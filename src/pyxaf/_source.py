"""Input sources: paths, bytes and binary streams, with transparent gzip/zip decompression."""

from __future__ import annotations

import gzip
import io
import os
import zipfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, BinaryIO, TypeAlias, cast

from .errors import (
    CorruptArchiveError,
    EncryptedAuditfileError,
    LimitExceededError,
    NotAnAuditfileError,
    PyxafError,
)

__all__ = ["CHUNK_SIZE", "Source", "SourceLike", "open_source"]

SourceLike: TypeAlias = "str | os.PathLike[str] | bytes | bytearray | memoryview | BinaryIO"

CHUNK_SIZE = 1 << 16

_GZIP_MAGIC = b"\x1f\x8b"
_ZIP_MAGIC = b"PK\x03\x04"
_CAB_MAGIC = b"MSCF"
_ENCRYPTED_SUFFIXES = {
    ".xac": "an encrypted auditfile for the Belastingdienst (.xac)",
    ".xsc": "an encrypted auditfile (.xsc / .pbl.xsc)",
    ".xfc": "an Exact encrypted XML auditfile (.XFC)",
    ".adc": "an Exact encrypted ASCII auditfile (.ADC)",
}


@dataclass(slots=True)
class Source:
    """A re-openable (where possible) stream of decompressed auditfile bytes."""

    name: str
    compression: str | None
    reopenable: bool
    _opener: Callable[[], IO[bytes]]
    size: int | None = None
    """Size of the (compressed) input in bytes, when known."""
    _consumed: bool = False
    _head: bytes | None = None
    _pending: IO[bytes] | None = None

    def head(self, size: int = CHUNK_SIZE) -> bytes:
        """Return (up to) the first ``size`` decompressed bytes without consuming the source."""
        if self._head is None or (len(self._head) < size and self.reopenable):
            if self.reopenable:
                with self._opener() as stream:
                    self._head = stream.read(size)
            else:
                if self._consumed:
                    raise PyxafError(f"{self.name}: the input stream can only be read once")
                self._pending = self._opener()
                self._head = self._pending.read(size)
        return self._head[:size]

    def open(self) -> IO[bytes]:
        """Open a fresh binary stream positioned at the start of the content."""
        if self.reopenable:
            return self._opener()
        if self._consumed:
            raise PyxafError(
                f"{self.name}: the input stream can only be read once; pass a path, bytes or a "
                "seekable stream to iterate more than once"
            )
        self._consumed = True
        if self._pending is not None:
            pending, self._pending = self._pending, None
            return io.BufferedReader(_Chain(self._head or b"", pending))
        return self._opener()

    def chunks(self, size: int = CHUNK_SIZE) -> Iterator[bytes]:
        """Yield the content in chunks."""
        stream = self.open()
        try:
            while chunk := stream.read(size):
                yield chunk
        finally:
            stream.close()


class _LimitedReader(io.RawIOBase):
    """Raise once more than ``limit`` bytes were produced (decompression-bomb guard)."""

    def __init__(self, inner: IO[bytes], limit: int | None, name: str) -> None:
        self._inner = inner
        self._limit = limit
        self._count = 0
        self._name = name

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        try:
            data = self._inner.read(len(b))
        except (OSError, EOFError, zlib.error, zipfile.BadZipFile) as exc:
            raise CorruptArchiveError(
                f"{self._name}: corrupt or truncated archive ({exc})"
            ) from None
        n = len(data)
        self._count += n
        if self._limit is not None and self._count > self._limit:
            raise LimitExceededError(
                f"{self._name}: decompressed size exceeds the limit of {self._limit:,} bytes "
                "(pass a larger max_decompressed_size if this file is legitimate)"
            )
        b[:n] = data
        return n

    def close(self) -> None:
        self._inner.close()
        super().close()


class _NonClosing(io.RawIOBase):
    """Wrap a caller-owned stream so pyxaf never closes it."""

    def __init__(self, inner: IO[bytes]) -> None:
        self._inner = inner

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self._inner.read(len(b))
        b[: len(data)] = data
        return len(data)


def _check_encrypted(name: str, head: bytes) -> None:
    lower = name.lower()
    for suffix, what in _ENCRYPTED_SUFFIXES.items():
        if lower.endswith(suffix):
            raise EncryptedAuditfileError(
                f"{name}: looks like {what}; only the intended recipient can decrypt it"
            )
    if head.startswith(_CAB_MAGIC):
        raise EncryptedAuditfileError(
            f"{name}: is a Microsoft CAB archive (Exact 'Verdichten'); extract it first"
        )


def open_source(
    source: SourceLike | Source,
    *,
    max_decompressed_size: int | None = None,
    max_ratio: float | None = 100.0,
) -> Source:
    """Wrap ``source`` in a :class:`Source`, detecting gzip/zip compression by magic bytes.

    Args:
        source: Path, raw bytes or a binary file object.
        max_decompressed_size: Absolute cap on the decompressed size of archives; when given it
            replaces the ratio guard.
        max_ratio: Cap on the decompressed/compressed size ratio (zip-bomb guard), used when no
            ``max_decompressed_size`` is given: ``size × max_ratio + 1 MiB``.
    """
    if isinstance(source, Source):
        return source
    peek = b""
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        name = "<bytes>"

        def base() -> IO[bytes]:
            return io.BytesIO(data)

        size: int | None = len(data)
        reopenable = True
    elif isinstance(source, (str, os.PathLike)):
        path = Path(source)
        name = str(path)
        if not path.is_file():
            raise FileNotFoundError(name)
        size = path.stat().st_size

        def base() -> IO[bytes]:
            return path.open("rb")

        reopenable = True
    elif hasattr(source, "read"):
        stream: IO[bytes] = source
        name = str(getattr(stream, "name", "<stream>"))
        reopenable = bool(getattr(stream, "seekable", lambda: False)())
        size = None
        if reopenable:
            start = stream.tell()

            def base() -> IO[bytes]:
                stream.seek(start)
                return io.BufferedReader(_NonClosing(stream))

        else:
            peek = stream.read(8)
            used = [False]

            def base() -> IO[bytes]:
                if used[0]:
                    raise PyxafError(f"{name}: the input stream can only be read once")
                used[0] = True
                return io.BufferedReader(_Chain(peek, stream))

    else:
        raise TypeError(f"unsupported source type {type(source).__name__}")

    if reopenable:
        with base() as probe:
            head = probe.read(8)
    else:
        head = peek
    if not isinstance(cast("object", head), bytes):
        raise TypeError("pyxaf needs a binary stream; open the file in 'rb' mode")

    _check_encrypted(name, head)
    # an explicit max_decompressed_size replaces the ratio guard (so it can also raise the cap)
    limit = max_decompressed_size
    if limit is None and size is not None and max_ratio is not None:
        limit = int(size * max_ratio) + (1 << 20)

    if head.startswith(_GZIP_MAGIC):

        def gz() -> IO[bytes]:
            return io.BufferedReader(
                _LimitedReader(
                    cast("IO[bytes]", gzip.GzipFile(fileobj=base(), mode="rb")), limit, name
                )
            )

        return Source(name, "gzip", reopenable, gz, size)

    if head.startswith(_ZIP_MAGIC):
        if not reopenable:
            raise PyxafError(
                f"{name}: zip archives must be given as a path, bytes or seekable stream"
            )

        def zp() -> IO[bytes]:
            try:
                zf = zipfile.ZipFile(base())
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                raise CorruptArchiveError(f"{name}: corrupt zip archive ({exc})") from None
            members = [
                i
                for i in zf.infolist()
                if not i.is_dir() and not i.filename.startswith("__MACOSX/")
            ]
            if len(members) != 1:
                zf.close()
                raise NotAnAuditfileError(
                    f"{name}: zip archive must contain exactly one auditfile, found {len(members)}"
                )
            info = members[0]
            if limit is not None and info.file_size > limit:
                zf.close()
                raise LimitExceededError(
                    f"{name}: member {info.filename} is {info.file_size:,} bytes uncompressed, "
                    f"over the limit of {limit:,}"
                )
            _check_encrypted(info.filename, b"")
            return io.BufferedReader(_LimitedReader(zf.open(info), limit, name))

        return Source(name, "zip", True, zp, size)

    return Source(name, None, reopenable, base, size)


class _Chain(io.RawIOBase):
    """Replay already-consumed ``head`` bytes, then continue with ``rest``."""

    def __init__(self, head: bytes, rest: IO[bytes]) -> None:
        self._head = head
        self._rest = rest

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        if self._head:
            n = min(len(b), len(self._head))
            b[:n] = self._head[:n]
            self._head = self._head[n:]
            return n
        data = self._rest.read(len(b))
        b[: len(data)] = data
        return len(data)
