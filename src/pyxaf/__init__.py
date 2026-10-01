"""pyxaf — read and validate every iteration of the Dutch Auditfile Financieel.

Supported: ADF (``CLAIR1.00.00``), CLAIR2, XAF 3.0, 3.1, 3.2, 3.2.1 and 4.0. The core has no
dependencies outside the standard library.

Example:
    >>> import pyxaf
    >>> with pyxaf.open("2024.xaf") as af:                      # doctest: +SKIP
    ...     print(af.version, af.company.name)
    ...     for line in af.lines():
    ...         ...
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from .detect import detect
from .errors import (
    EncryptedAuditfileError,
    ForbiddenConstructError,
    LimitExceededError,
    MissingExtraError,
    NotAnAuditfileError,
    PyxafError,
    XmlSyntaxError,
)
from .findings import CODES, Finding, Severity
from .formats import Family, FormatInfo, NamespaceStatus, Version
from .models import (
    AccountKind,
    AccountType,
    Address,
    Company,
    ForeignAmount,
    Header,
    Journal,
    JournalKind,
    JournalType,
    LedgerAccount,
    Line,
    OpeningBalance,
    Period,
    Relation,
    RelationKind,
    RelationType,
    RgsRef,
    Side,
    Transaction,
    TransactionTotals,
    VatCode,
    VatLine,
)
from .raw import RawRecord
from .reader import AuditFile, open
from .validate import ValidationReport, validate

try:
    __version__ = _version("pyxaf")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0"

__all__ = [
    "CODES",
    "AccountKind",
    "AccountType",
    "Address",
    "AuditFile",
    "Company",
    "EncryptedAuditfileError",
    "Family",
    "Finding",
    "ForbiddenConstructError",
    "ForeignAmount",
    "FormatInfo",
    "Header",
    "Journal",
    "JournalKind",
    "JournalType",
    "LedgerAccount",
    "LimitExceededError",
    "Line",
    "MissingExtraError",
    "NamespaceStatus",
    "NotAnAuditfileError",
    "OpeningBalance",
    "Period",
    "PyxafError",
    "RawRecord",
    "Relation",
    "RelationKind",
    "RelationType",
    "RgsRef",
    "Severity",
    "Side",
    "Transaction",
    "TransactionTotals",
    "ValidationReport",
    "VatCode",
    "VatLine",
    "Version",
    "XmlSyntaxError",
    "__version__",
    "detect",
    "open",
    "validate",
]
