"""Iterations of the Auditfile Financieel and how they are identified."""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ._encoding import EncodingInfo

__all__ = [
    "KNOWN_NAMESPACES",
    "Family",
    "FormatInfo",
    "NamespaceStatus",
    "Version",
]


class Family(enum.StrEnum):
    """Structural family of an auditfile."""

    ADF = "adf"
    """Fixed-width ASCII auditfile (``CLAIR1.00.00``, 1999)."""
    CLAIR2 = "clair2"
    """First XML auditfile (``CLAIR2.00.00``, 2003), no namespace."""
    XAF = "xaf"
    """XML Auditfile Financieel 3.0 – 4.0."""


class Version(enum.StrEnum):
    """Iteration of the Auditfile Financieel. Compares equal to its string value (``"4.0"``)."""

    ADF = "ADF"
    CLAIR2 = "CLAIR2"
    XAF30 = "3.0"
    XAF31 = "3.1"
    XAF32 = "3.2"
    XAF321 = "3.2.1"
    XAF40 = "4.0"

    @property
    def family(self) -> Family:
        """Structural family of this version."""
        if self is Version.ADF:
            return Family.ADF
        if self is Version.CLAIR2:
            return Family.CLAIR2
        return Family.XAF


class NamespaceStatus(enum.StrEnum):
    """How trustworthy the root element's namespace is."""

    OFFICIAL = "official"
    """The target namespace of the official XSD."""
    DOCUMENTED_VARIANT = "documented-variant"
    """Stated in official documentation but not the XSD target namespace."""
    UNVERIFIED = "unverified"
    """Plausible, but no official source could be found (XAF 3.0)."""
    KNOWN_BOGUS = "known-bogus"
    """Does not officially exist, but occurs in files from some exporters."""
    UNKNOWN = "unknown"
    """Not in pyxaf's table."""
    NONE = "none"
    """No namespace (CLAIR2, ADF, some exporters)."""


#: Known namespace URIs (compared case-insensitively, trailing slash ignored).
KNOWN_NAMESPACES: Mapping[str, tuple[Version, NamespaceStatus]] = MappingProxyType(
    {
        "http://www.auditfiles.nl/xaf/3.0": (Version.XAF30, NamespaceStatus.UNVERIFIED),
        "http://www.auditfiles.nl/xaf/3.1": (Version.XAF31, NamespaceStatus.OFFICIAL),
        "http://www.auditfiles.nl/xaf/3.2": (Version.XAF32, NamespaceStatus.OFFICIAL),
        "http://www.odb.belastingdienst.nl/belastingdienst/bcpp/1.0/structures/"
        "xmlauditfilefinancieel3.2.1": (Version.XAF321, NamespaceStatus.OFFICIAL),
        "http://www.odb.belastingdienst.nl/belastingdienst/bcpp/1.1/structures/"
        "xmlauditfilexaf_4.0": (Version.XAF40, NamespaceStatus.OFFICIAL),
        "http://www.odb.belastingdienst.nl/xaf/4.0": (
            Version.XAF40,
            NamespaceStatus.DOCUMENTED_VARIANT,
        ),
        "http://www.auditfiles.nl/xaf/4.0": (Version.XAF40, NamespaceStatus.KNOWN_BOGUS),
        "http://www.auditfiles.nl/xaf/2.0": (Version.CLAIR2, NamespaceStatus.KNOWN_BOGUS),
    }
)


def lookup_namespace(uri: str | None) -> tuple[Version | None, NamespaceStatus]:
    """Look ``uri`` up in :data:`KNOWN_NAMESPACES`."""
    if not uri:
        return None, NamespaceStatus.NONE
    key = uri.strip().rstrip("/").lower()
    hit = KNOWN_NAMESPACES.get(key)
    if hit is None:
        return None, NamespaceStatus.UNKNOWN
    return hit


@dataclass(frozen=True, slots=True, kw_only=True)
class FormatInfo:
    """Result of format detection.

    Attributes:
        family: Structural family (``None`` only if undeterminable).
        version: Detected iteration (best guess when ``confidence < 1``).
        namespace: Namespace URI of the root element, as written.
        namespace_status: Trust level of the namespace.
        encoding: Encoding information (BOM, declaration, effective codec).
        confidence: 0–1; 1 means the official markers and the vocabulary agree.
        reasons: Human-readable explanation of how the version was decided.
        continuation: ``(x, y)`` for a continuation file ("Vervolgbestand x van y"), else ``None``.
        compression: ``"gzip"``, ``"zip"`` or ``None``.
        root: Local name of the root element (``None`` for ADF).
    """

    family: Family | None
    version: Version | None
    namespace: str | None
    namespace_status: NamespaceStatus
    encoding: EncodingInfo
    confidence: float
    reasons: tuple[str, ...]
    continuation: tuple[int, int] | None = None
    compression: str | None = None
    root: str | None = None

    @property
    def bom(self) -> bool:
        """Whether the file starts with a byte-order mark."""
        return self.encoding.bom is not None
