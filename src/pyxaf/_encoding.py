"""Character-encoding detection and opt-in byte-level repairs."""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

from .findings import FindingCollector

__all__ = [
    "REPAIRS",
    "EncodingInfo",
    "Repair",
    "expat_plan",
    "repair_chunks",
    "sniff_encoding",
    "transcode_chunks",
]

Repair = Literal["control-chars", "latin1-as-cp1252", "bare-ampersand"]
REPAIRS: frozenset[str] = frozenset({"control-chars", "latin1-as-cp1252", "bare-ampersand"})

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_DECL = re.compile(rb"^\s*<\?xml\b[^>]*?\bencoding\s*=\s*([\"'])([A-Za-z][A-Za-z0-9._-]*)\1")
_HAS_DECL = re.compile(rb"^\s*<\?xml\b")
_C1 = re.compile(rb"[\x80-\x9f]")
# XML 1.0 forbids C0 controls except TAB, LF, CR
_CONTROL = bytes([*range(9), 11, 12, *range(14, 32)])
_BARE_AMP = re.compile(rb"&(?!(?:[A-Za-z_:][A-Za-z0-9._:-]*|#[0-9]+|#x[0-9A-Fa-f]+);)")

#: Encodings the 3.2/3.2.1/4.0 specifications allow (Python codec names).
ALLOWED_ENCODINGS = frozenset({"utf-8", "iso8859-1"})


def canonical(name: str) -> str:
    """Return Python's canonical codec name (``"ISO-8859-1"`` → ``"iso8859-1"``)."""
    try:
        return codecs.lookup(name).name
    except LookupError:
        return name.lower()


@dataclass(frozen=True, slots=True)
class EncodingInfo:
    """How the bytes of a file are to be decoded.

    Attributes:
        bom: Encoding indicated by a byte-order mark, if any.
        declared: Encoding named in the XML declaration (as written), if any.
        has_declaration: Whether an XML declaration is present.
        effective: Canonical Python codec name used for decoding.
        override: Encoding forced by the caller, if any.
    """

    bom: str | None
    declared: str | None
    has_declaration: bool
    effective: str
    override: str | None = None


def sniff_encoding(head: bytes, override: str | None = None) -> EncodingInfo:
    """Determine the encoding from the BOM and XML declaration (XML 1.0 Appendix F)."""
    bom = next((enc for mark, enc in _BOMS if head.startswith(mark)), None)
    body = head
    if bom is not None:
        body = head[len(next(m for m, e in _BOMS if e == bom)) :]
    if (bom is not None and bom.startswith("utf-16")) or (
        bom is not None and bom.startswith("utf-32")
    ):
        try:
            text = body[:4096].decode(bom, errors="ignore").encode("ascii", errors="ignore")
        except LookupError:  # pragma: no cover - all codecs exist
            text = b""
        body = text
    elif head[:4] in (b"<\x00?\x00", b"\x00<\x00?"):
        enc = "utf-16-le" if head[0] == 0x3C else "utf-16-be"
        body = head[:4096].decode(enc, errors="ignore").encode("ascii", errors="ignore")
        bom = None
        m = _DECL.match(body)
        effective = canonical(override) if override else enc
        return EncodingInfo(None, m.group(2).decode() if m else None, True, effective, override)
    m = _DECL.match(body)
    declared = m.group(2).decode("ascii") if m else None
    has_decl = bool(_HAS_DECL.match(body))
    if override:
        effective = canonical(override)
    elif bom is not None:
        effective = bom
    elif declared:
        effective = canonical(declared)
    else:
        effective = "utf-8"
    return EncodingInfo(bom, declared, has_decl, effective, override)


def repair_chunks(
    chunks: Iterable[bytes],
    repairs: frozenset[str],
    findings: FindingCollector | None,
    *,
    check_c1: bool = False,
) -> Iterator[bytes]:
    """Apply opt-in byte-level repairs (ASCII-compatible encodings only) and report them.

    Args:
        chunks: Raw byte chunks.
        repairs: Subset of :data:`REPAIRS` (``latin1-as-cp1252`` is handled by the parser).
        findings: Where to report applied repairs.
        check_c1: Report (once) C1 bytes in a file declared ISO-8859-1 (likely windows-1252).
    """
    strip_ctrl = "control-chars" in repairs
    fix_amp = "bare-ampersand" in repairs
    removed = 0
    escaped = 0
    carry = b""
    c1_reported = not check_c1
    for chunk in chunks:
        if not c1_reported and _C1.search(chunk):
            c1_reported = True
            if findings is not None:
                findings.add(
                    "XAF1005",
                    "file is declared ISO-8859-1 but contains bytes 0x80-0x9F, which are control "
                    "characters in ISO-8859-1 and typographic characters in windows-1252; "
                    "pass repair={'latin1-as-cp1252'} or encoding='cp1252' to decode them as such",
                )
        if not (strip_ctrl or fix_amp):
            yield chunk
            continue
        data = carry + chunk
        carry = b""
        if strip_ctrl:
            cleaned = data.translate(None, _CONTROL)
            removed += len(data) - len(cleaned)
            data = cleaned
        if fix_amp:
            # keep a possibly incomplete entity reference for the next chunk
            amp = data.rfind(b"&", max(0, len(data) - 64))
            if amp != -1 and b";" not in data[amp:]:
                data, carry = data[:amp], data[amp:]
            data, n = _BARE_AMP.subn(b"&amp;", data)
            escaped += n
        yield data
    if carry:
        data, n = _BARE_AMP.subn(b"&amp;", carry)
        escaped += n
        yield data
    if findings is not None:
        if removed:
            findings.add("XAF1010", f"removed {removed:,} XML-illegal control character(s)")
        if escaped:
            findings.add("XAF1012", f"escaped {escaped:,} bare '&' character(s) as '&amp;'")


#: Encodings Expat decodes itself (Python codec names).
EXPAT_NATIVE = frozenset({"utf-8", "utf-16", "utf-16-le", "utf-16-be", "iso8859-1", "ascii"})
_SURROGATE = re.compile("[\udc80-\udcff]")


def check_encoding_name(name: str) -> str:
    """Return the canonical codec name or raise ``ValueError`` for unknown encodings."""
    try:
        return codecs.lookup(name).name
    except LookupError:
        raise ValueError(f"unknown encoding {name!r}") from None


def expat_plan(effective: str) -> tuple[str | None, str | None, bool]:
    """Decide how Expat gets the text: ``(expat_encoding, transcode_codec, unknown)``.

    Encodings Expat supports natively are passed through. Everything else Python knows
    (windows-1252, UTF-32, Shift-JIS, …) is transcoded to UTF-8 in Python; unknown names are read
    as UTF-8 (``unknown`` is then ``True``).
    """
    if effective in EXPAT_NATIVE:
        return None, None, False
    try:
        codec = codecs.lookup(effective).name
    except LookupError:
        return "utf-8", None, True
    if codec in EXPAT_NATIVE:
        return codec, None, False
    return "utf-8", codec, False


def transcode_chunks(
    chunks: Iterable[bytes], codec: str, counter: list[int] | None = None
) -> Iterator[bytes]:
    """Decode with ``codec`` and re-encode as UTF-8.

    Bytes the codec does not define (e.g. 0x81 in windows-1252) are kept as the code point of the
    same value, as ISO-8859-1 would, and counted in ``counter[0]``.
    """
    decoder = codecs.getincrementaldecoder(codec)(errors="surrogateescape")

    def fix(text: str) -> str:
        if _SURROGATE.search(text):
            if counter is not None:
                counter[0] += len(_SURROGATE.findall(text))
            text = _SURROGATE.sub(lambda m: chr(ord(m.group()) - 0xDC00), text)
        return text

    for chunk in chunks:
        yield fix(decoder.decode(chunk)).encode("utf-8")
    tail = decoder.decode(b"", final=True)
    if tail:
        yield fix(tail).encode("utf-8")
