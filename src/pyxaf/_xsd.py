"""Optional XSD validation with lxml (``pyxaf[xsd]``).

The lxml parser is always created with ``resolve_entities=False, no_network=True,
load_dtd=False, huge_tree=False`` (lxml < 6.1 resolved external entities in ``iterparse`` by
default, CVE-2026-41066). Small inputs are validated as a whole (all errors are reported); large
inputs are validated streaming, which stops at the first schema error.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Iterator
from importlib import resources
from typing import TYPE_CHECKING, Any

from ._encoding import transcode_chunks
from ._optional import require
from .findings import FindingCollector, Severity
from .formats import NamespaceStatus, Version

if TYPE_CHECKING:
    from .reader import AuditFile

__all__ = ["SCHEMA_FILES", "load_schema", "validate_xsd"]

SCHEMA_FILES = {
    Version.CLAIR2: "clair2-auditfile.xsd",
    Version.XAF32: "XmlAuditfileFinancieel3.2.xsd",
    Version.XAF321: "XmlAuditfileFinancieel3.2.1.xsd",
    Version.XAF40: "XmlAuditfileFinancieel4.0.xsd",
}
#: Inputs up to this size are validated as a whole tree (reporting every error).
FULL_TREE_LIMIT = 64 * 1024 * 1024
_SAFE = {"resolve_entities": False, "no_network": True, "load_dtd": False, "huge_tree": False}


def load_schema(version: Version) -> Any:
    """Return an ``lxml.etree.XMLSchema`` for a bundled official XSD."""
    etree = require("lxml.etree", "xsd")
    name = SCHEMA_FILES[version]
    data = resources.files("pyxaf.schemas").joinpath(name).read_bytes()
    parser = etree.XMLParser(**_SAFE)
    return etree.XMLSchema(etree.fromstring(data, parser))


class _ChunkStream(io.RawIOBase):
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._it: Iterator[bytes] = iter(chunks)
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        while not self._buf:
            try:
                self._buf = next(self._it)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def validate_xsd(af: AuditFile, findings: FindingCollector) -> bool:
    """Validate every part of ``af`` against its official XSD; return whether it ran."""
    etree = require("lxml.etree", "xsd")
    ran = False
    for part in af._parts:
        info = part.info
        version = info.version
        if version is None or version not in SCHEMA_FILES:
            findings.add(
                "XAF3040",
                f"no official XSD exists for {version}; XSD validation skipped",
                severity=Severity.INFO,
                file=part.index,
            )
            continue
        if info.namespace_status not in (NamespaceStatus.OFFICIAL, NamespaceStatus.NONE):
            findings.add(
                "XAF3040",
                f"root namespace {info.namespace!r} is not the XSD target namespace; "
                "the file cannot validate against the official XSD",
                file=part.index,
            )
            continue
        schema = load_schema(version)
        ran = True
        # the bytes the core parsed: same continuation handling, transcoding, override, repairs
        stream, forced = af._prepared(part, None)
        encoding = None
        if forced is not None:  # the XML declaration is overridden: hand lxml UTF-8
            if forced != "utf-8":
                stream = transcode_chunks(stream, forced)
            stream = _strip_bom(stream)  # lxml does not skip a BOM when the encoding is forced
            encoding = "utf-8"
        sizes = [s.size for s in part.sources]
        compressed = any(s.compression for s in part.sources)
        size = (
            sum(x for x in sizes if x is not None)
            if None not in sizes and not compressed
            else FULL_TREE_LIMIT + 1
        )
        reader = io.BufferedReader(_ChunkStream(stream))
        if size <= FULL_TREE_LIMIT:
            parser = etree.XMLParser(encoding=encoding, **_SAFE)
            try:
                doc = etree.parse(reader, parser)
            except etree.XMLSyntaxError as exc:
                findings.add("XAF3040", f"XSD validation: {exc}", file=part.index)
                continue
            if not schema.validate(doc):
                for err in schema.error_log:
                    findings.add(
                        "XAF3040",
                        _clean(err.message),
                        line=err.line or None,
                        file=part.index,
                    )
            continue
        try:
            for _ev, el in etree.iterparse(
                reader, events=("end",), schema=schema, encoding=encoding, **_SAFE
            ):
                el.clear(keep_tail=True)
                # clear() empties the element but leaves it attached: drop finished siblings so
                # memory stays flat (the schema validator does not need them)
                while el.getprevious() is not None:
                    del el.getparent()[0]
        except etree.XMLSyntaxError as exc:
            last = exc.error_log.last_error if exc.error_log else None
            findings.add(
                "XAF3040",
                _clean(last.message if last is not None else str(exc))
                + " (streaming XSD validation stops at the first error)",
                line=(last.line or None) if last is not None else None,
                file=part.index,
            )
    return ran


def _strip_bom(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Drop a leading UTF-8 byte-order mark."""
    it = iter(chunks)
    head = b""
    for chunk in it:
        head += chunk
        if len(head) >= 3:
            break
    yield head.removeprefix(b"\xef\xbb\xbf")
    yield from it


def _clean(message: str) -> str:
    """Shorten ``{namespace}local`` names in lxml messages to ``local``."""
    import re  # noqa: PLC0415

    return re.sub(r"\{[^}]*\}", "", message)
