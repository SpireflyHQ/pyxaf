"""Exceptions raised by pyxaf.

pyxaf never refuses a file because of *data* problems — those become findings
(see :mod:`pyxaf.findings`). Exceptions are reserved for inputs that cannot be read at all:
not an auditfile, malformed XML, forbidden constructs, exceeded limits, missing extras.
"""

from __future__ import annotations

__all__ = [
    "CorruptArchiveError",
    "EncryptedAuditfileError",
    "ForbiddenConstructError",
    "LimitExceededError",
    "MissingExtraError",
    "NotAnAuditfileError",
    "PyxafError",
    "XmlSyntaxError",
]


class PyxafError(Exception):
    """Base class for all pyxaf errors."""


class NotAnAuditfileError(PyxafError):
    """The input is not recognisable as any iteration of the Auditfile Financieel."""


class EncryptedAuditfileError(NotAnAuditfileError):
    """The input is an encrypted or vendor-compressed auditfile (``.xac``, ``.xsc``, ``.XFC``, …).

    Only the intended recipient (usually the Belastingdienst) can decrypt these.
    """


class XmlSyntaxError(PyxafError):
    """The XML is not well-formed.

    Attributes:
        line: 1-based line number of the error (``None`` if unknown).
        column: 0-based column of the error (``None`` if unknown).
        file: index of the file in a multi-file set (0 for single files).
    """

    def __init__(
        self, message: str, *, line: int | None = None, column: int | None = None, file: int = 0
    ) -> None:
        super().__init__(message)
        self.line = line
        self.column = column
        self.file = file


class ForbiddenConstructError(XmlSyntaxError):
    """The XML contains a construct pyxaf refuses for security reasons (DOCTYPE, ENTITY).

    No version of the Auditfile Financieel uses a DTD; refusing them blocks entity-expansion
    ("billion laughs") and external-entity (XXE) attacks.
    """


class CorruptArchiveError(NotAnAuditfileError):
    """A gzip or zip container is corrupt or truncated."""


class LimitExceededError(PyxafError):
    """A configured safety limit (nesting depth, text size, decompressed size) was exceeded."""


class MissingExtraError(PyxafError, ImportError):
    """An optional dependency is needed; the message says which extra to install."""
