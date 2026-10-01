"""Format, version and encoding detection by scoring several signals.

Namespaces alone are unreliable (the 4.0 XSD and 4.0 documentation disagree, some exporters
write non-existent namespaces, CLAIR2 has none), so detection combines:

1. the ADF signature ``CLAIR1.00.00``;
2. the root element and its namespace (looked up in :data:`~pyxaf.formats.KNOWN_NAMESPACES`);
3. the element vocabulary of the first 64 KiB compared with each version's catalogue;
4. ``header/auditfileVersion`` (CLAIR2) and the "Vervolgbestand x van y" comment (split files).
"""

from __future__ import annotations

import contextlib
import pyexpat
import re
from dataclasses import dataclass, field

from ._encoding import (
    EncodingInfo,
    check_encoding_name,
    expat_plan,
    sniff_encoding,
    transcode_chunks,
)
from ._source import Source, SourceLike, open_source
from .catalogue import get_catalogue
from .errors import ForbiddenConstructError, NotAnAuditfileError
from .findings import FindingCollector, Severity
from .formats import Family, FormatInfo, NamespaceStatus, Version, lookup_namespace

__all__ = ["detect"]

SNIFF_SIZE = 1 << 16
ADF_SIGNATURE = b"CLAIR1.00.00"
_CONTINUATION = re.compile(rb"<!--\s*Vervolgbestand\s+(\d+)\s+van\s+(\d+)\s*-->", re.IGNORECASE)
_XML_VERSIONS = (
    Version.XAF40,
    Version.XAF32,
    Version.XAF321,
    Version.XAF31,
    Version.XAF30,
    Version.CLAIR2,
)
# order used to break ties between versions with identical vocabulary
_TIE_ORDER = {
    Version.XAF32: 0,
    Version.XAF40: 1,
    Version.XAF31: 2,
    Version.XAF321: 3,
    Version.XAF30: 4,
    Version.CLAIR2: 5,
}


@dataclass(slots=True)
class _Sniff:
    root: str | None = None
    prefix: str | None = None
    namespace: str | None = None
    names: set[str] = field(default_factory=set)
    auditfile_version: str | None = None
    first_children: list[str] = field(default_factory=list)


def _sniff_xml(head: bytes, effective: str) -> _Sniff:
    info = _Sniff()
    expat_enc, codec, _unknown = expat_plan(effective)
    if codec is not None:
        head = b"".join(transcode_chunks([head], codec))
    parser = pyexpat.ParserCreate(expat_enc)
    parser.buffer_text = True
    path: list[str] = []
    text: list[str] = []

    def forbidden(*_: object) -> None:
        raise ForbiddenConstructError(
            "DOCTYPE/ENTITY declarations are not allowed in an auditfile (refused for security)",
            line=parser.CurrentLineNumber,
            column=parser.CurrentColumnNumber,
        )

    def start(name: str, attrs: dict[str, str]) -> None:
        local = name.rpartition(":")[2]
        if not path and info.root is None:
            info.root = local
            info.prefix = name.partition(":")[0] if ":" in name else None
            key = f"xmlns:{info.prefix}" if info.prefix else "xmlns"
            info.namespace = attrs.get(key)
        elif len(path) == 1:
            info.first_children.append(local)
        info.names.add(local)
        path.append(local)
        text.clear()

    def end(_name: str) -> None:
        if path and path[-1] == "auditfileVersion":
            info.auditfile_version = "".join(text).strip()
        if path:
            path.pop()

    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text.append
    # a truncated head, junk after a fragment or malformed data: use what was seen
    with contextlib.suppress(pyexpat.ExpatError):
        parser.Parse(head, False)
    return info


def _detect_source(src: Source, encoding: str | None = None) -> FormatInfo:
    if encoding:
        check_encoding_name(encoding)
    head = src.head(SNIFF_SIZE)
    enc = sniff_encoding(head, encoding)
    body = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    if body.startswith(ADF_SIGNATURE):
        default = "utf-8-sig" if enc.bom == "utf-8" else "cp1252"  # ADF is DOS text
        adf_enc = EncodingInfo(enc.bom, None, False, encoding or default, encoding)
        return FormatInfo(
            family=Family.ADF,
            version=Version.ADF,
            namespace=None,
            namespace_status=NamespaceStatus.NONE,
            encoding=adf_enc,
            confidence=1.0,
            reasons=("starts with the ADF signature CLAIR1.00.00",),
            compression=src.compression,
        )
    continuation = None
    m = _CONTINUATION.search(body[:2048])
    if m:
        continuation = (int(m.group(1)), int(m.group(2)))
    stripped = body.lstrip()
    if enc.effective.startswith("utf-16") or enc.effective.startswith("utf-32"):
        stripped = b"<"  # cannot inspect bytes directly; let Expat decide
    if not stripped.startswith(b"<"):
        raise NotAnAuditfileError(
            f"{src.name}: neither XML nor an ADF auditfile (starts with {body[:16]!r})"
        )
    sniff = _sniff_xml(head, enc.effective)
    if sniff.root is None and continuation is not None:
        # a continuation fragment may start with an end tag (split between journals)
        return FormatInfo(
            family=None,
            version=None,
            namespace=None,
            namespace_status=NamespaceStatus.NONE,
            encoding=enc,
            confidence=1.0,
            reasons=(f"continuation fragment {continuation[0]} of {continuation[1]}",),
            continuation=continuation,
            compression=src.compression,
        )
    if sniff.root is None:
        raise NotAnAuditfileError(f"{src.name}: no XML root element found in the first 64 KiB")
    if sniff.root.lower() != "auditfile" and continuation is None:
        raise NotAnAuditfileError(
            f"{src.name}: root element is <{sniff.root}>, not <auditfile>"
            + (" (a different auditfile type?)" if "audit" in sniff.root.lower() else "")
        )
    return _decide(sniff, enc, continuation, src.compression)


def _decide(
    sniff: _Sniff,
    enc: EncodingInfo,
    continuation: tuple[int, int] | None,
    compression: str | None,
) -> FormatInfo:
    reasons: list[str] = []
    ns_version, ns_status = lookup_namespace(sniff.namespace)
    names = sniff.names
    unknown = {v: names - get_catalogue(v).vocabulary for v in _XML_VERSIONS}
    best = min(_XML_VERSIONS, key=lambda v: (len(unknown[v]), _TIE_ORDER[v]))
    clair2_marker = (
        sniff.auditfile_version is not None and sniff.auditfile_version.upper().startswith("CLAIR2")
    )
    if sniff.root != "auditfile" and sniff.root and sniff.root.lower() == "auditfile":
        reasons.append(f"root element written as <{sniff.root}>")

    version: Version
    confidence: float
    if clair2_marker:
        version = Version.CLAIR2
        confidence = 1.0 if ns_status is NamespaceStatus.NONE else 0.8
        reasons.append(f"header/auditfileVersion is {sniff.auditfile_version!r}")
    elif ns_version is not None and ns_status is NamespaceStatus.OFFICIAL:
        version = ns_version
        confidence = 1.0
        reasons.append(f"official namespace of XAF {ns_version.value}")
        extra = unknown[version]
        if extra and len(unknown[best]) < len(extra):
            confidence = 0.6
            reasons.append(
                f"but elements {sorted(extra)[:5]} do not exist in {version.value}; "
                f"the vocabulary fits {best.value} better"
            )
    elif ns_version is not None:
        version = ns_version
        candidates = [v for v in _XML_VERSIONS if v.family is ns_version.family]
        vocab_best = min(candidates, key=lambda v: (len(unknown[v]), _TIE_ORDER[v]))
        confidence = 0.9 if ns_status is NamespaceStatus.DOCUMENTED_VARIANT else 0.7
        reasons.append(f"namespace {sniff.namespace!r} is {ns_status.value} for {version.value}")
        if vocab_best is not version and len(unknown[vocab_best]) < len(unknown[version]):
            reasons.append(f"vocabulary fits {vocab_best.value} better; using {vocab_best.value}")
            version = vocab_best
            confidence = 0.6
    else:
        version = best
        confidence = 0.5 if not unknown[best] else 0.3
        if sniff.namespace:
            reasons.append(f"unknown namespace {sniff.namespace!r}")
        else:
            reasons.append("no namespace")
        reasons.append(f"element vocabulary fits {best.value} best")
        ties = [v for v in _XML_VERSIONS if len(unknown[v]) == len(unknown[best]) and v is not best]
        if ties:
            reasons.append(
                "indistinguishable by vocabulary from " + ", ".join(v.value for v in ties)
            )
    if unknown[version]:
        reasons.append(f"elements not defined for {version.value}: {sorted(unknown[version])[:8]}")
    if continuation:
        reasons.append(f"continuation file {continuation[0]} of {continuation[1]}")
    return FormatInfo(
        family=version.family,
        version=version,
        namespace=sniff.namespace,
        namespace_status=ns_status,
        encoding=enc,
        confidence=confidence,
        reasons=tuple(reasons),
        continuation=continuation,
        compression=compression,
        root=sniff.root,
    )


def report_format(info: FormatInfo, findings: FindingCollector) -> None:
    """Turn detection results into findings (``XAF1xxx``/``XAF3xxx``)."""
    enc = info.encoding
    if enc.bom:
        findings.add("XAF1001", f"file starts with a {enc.bom} byte-order mark")
    if info.family is not Family.ADF:
        if not enc.has_declaration and not info.continuation:
            findings.add("XAF1002", "no XML declaration; the file is read as UTF-8")
        if enc.override:
            findings.add(
                "XAF1004",
                f"encoding overridden to {enc.override!r}"
                + (f" (declared {enc.declared!r})" if enc.declared else ""),
            )
        if (
            info.version in (Version.XAF32, Version.XAF321, Version.XAF40)
            and enc.effective not in ("utf-8", "iso8859-1")
            and not enc.override
        ):
            findings.add(
                "XAF1003",
                f"encoding {enc.declared or enc.effective!r} is not allowed "
                "(the specification allows UTF-8 and ISO-8859-1)",
                value=enc.declared,
            )
    if info.compression:
        findings.add("XAF1020", f"read from a {info.compression} archive")
    status = info.namespace_status
    if status is NamespaceStatus.DOCUMENTED_VARIANT:
        findings.add(
            "XAF3002",
            "namespace is stated in the 4.0 Toelichting but differs from the 4.0 XSD target "
            "namespace; XSD validation will fail",
            value=info.namespace,
        )
    elif status is NamespaceStatus.KNOWN_BOGUS:
        findings.add("XAF3003", "namespace does not officially exist", value=info.namespace)
    elif status is NamespaceStatus.UNKNOWN:
        findings.add("XAF3004", "namespace not recognised", value=info.namespace)
    elif status is NamespaceStatus.UNVERIFIED:
        findings.add(
            "XAF3004",
            "namespace could not be verified officially",
            value=info.namespace,
            severity=Severity.INFO,
        )
    if info.confidence < 1:
        findings.add(
            "XAF3007" if info.confidence < 0.7 else "XAF3001",
            f"detected {info.version} with confidence {info.confidence:.1f}: "
            + "; ".join(info.reasons),
        )
    if info.continuation:
        findings.add(
            "XAF3006", f"continuation file {info.continuation[0]} of {info.continuation[1]}"
        )


def detect(source: SourceLike, *, encoding: str | None = None) -> FormatInfo:
    """Detect the iteration, namespace status and encoding of an auditfile cheaply.

    Only the first 64 KiB (after decompression) are inspected.

    Args:
        source: Path, bytes or binary file object (gzip/zip are decompressed transparently).
        encoding: Override the declared encoding.

    Raises:
        NotAnAuditfileError: If the input is not an auditfile at all.
        EncryptedAuditfileError: For encrypted/vendor-compressed auditfiles.
        ForbiddenConstructError: If the prolog contains a DOCTYPE.
    """
    return _detect_source(open_source(source), encoding)
