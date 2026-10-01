"""Graded, coded findings.

Every problem pyxaf notices in a file is reported as a :class:`Finding` with a stable code
(``XAF<nnnn>``), a :class:`Severity` and the location where it occurred. Codes are grouped:

========  ==============================================
``1xxx``  input container and character encoding
``2xxx``  XML well-formedness and security
``3xxx``  version, namespace and structure
``4xxx``  references between records
``5xxx``  control totals and balance
``6xxx``  uniqueness
``7xxx``  data quality and conventions
``8xxx``  RGS (Referentie Grootboekschema)
========  ==============================================

The official XAF 4.0 consistency rules map to codes ending in their rule number
(rule [0009] → ``XAF5009``). :data:`CODES` documents every code.
"""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

__all__ = [
    "CODES",
    "CodeInfo",
    "Finding",
    "FindingCollector",
    "Severity",
]


class Severity(enum.IntEnum):
    """How serious a finding is. Ordered: ``INFO < WARNING < ERROR``."""

    INFO = 10
    """Notable, but neither wrong nor likely to affect analysis."""
    WARNING = 20
    """Likely a data problem, or something that affects analysis."""
    ERROR = 30
    """Violates the specification of the detected version."""

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class CodeInfo:
    """Documentation of a finding code."""

    code: str
    severity: Severity
    title: str
    rule_ref: str | None = None


def _c(code: str, severity: Severity, title: str, rule_ref: str | None = None) -> CodeInfo:
    return CodeInfo(code, severity, title, rule_ref)


_E, _W, _I = Severity.ERROR, Severity.WARNING, Severity.INFO

#: Registry of all finding codes with their default severity.
CODES: Mapping[str, CodeInfo] = {
    c.code: c
    for c in (
        # 1xxx input & encoding
        _c("XAF1001", _I, "Byte-order mark present"),
        _c("XAF1002", _W, "No XML declaration; encoding assumed to be UTF-8"),
        _c("XAF1003", _E, "Encoding not allowed by the specification"),
        _c("XAF1004", _I, "Encoding overridden by the caller"),
        _c("XAF1005", _W, "Declared ISO-8859-1 but contains windows-1252 characters"),
        _c("XAF1006", _W, "Undecodable bytes replaced"),
        _c("XAF1010", _W, "Repair: XML-illegal control characters removed"),
        _c("XAF1011", _W, "Repair: ISO-8859-1 decoded as windows-1252"),
        _c("XAF1012", _W, "Repair: bare ampersands escaped"),
        _c("XAF1013", _W, "Repair: trailing garbage after the document removed"),
        _c("XAF1020", _I, "Compressed container"),
        _c("XAF1021", _E, "Corrupt or truncated compressed container"),
        # 2xxx XML & security
        _c("XAF2001", _E, "XML is not well-formed"),
        _c("XAF2002", _E, "Forbidden construct (DOCTYPE/ENTITY)"),
        _c("XAF2003", _E, "Safety limit exceeded"),
        # 3xxx version, namespace, structure
        _c("XAF3001", _I, "Version detected"),
        _c("XAF3002", _W, "Namespace is a documented variant, not the XSD target namespace"),
        _c("XAF3003", _W, "Namespace does not officially exist"),
        _c("XAF3004", _W, "Unknown namespace"),
        _c("XAF3005", _W, "Version signals contradict each other"),
        _c("XAF3006", _I, "Continuation file of a split auditfile"),
        _c("XAF3007", _W, "Version could not be determined with certainty"),
        _c("XAF3010", _E, "Required element missing"),
        _c("XAF3011", _E, "Element not defined for this version"),
        _c("XAF3012", _E, "Element occurs more often than allowed"),
        _c("XAF3013", _E, "Element out of order"),
        _c("XAF3014", _E, "Value length outside the allowed range"),
        _c("XAF3015", _E, "Value not in the allowed code list"),
        _c("XAF3016", _E, "Value does not match the required pattern"),
        _c("XAF3017", _E, "Invalid date"),
        _c("XAF3018", _E, "Invalid decimal number"),
        _c("XAF3019", _E, "Invalid integer"),
        _c("XAF3020", _E, "Value differs from the fixed value"),
        _c("XAF3021", _E, "Value outside the allowed range"),
        _c("XAF3030", _W, "Master data after transactions"),
        _c("XAF3040", _E, "XSD validation error"),
        _c("XAF3050", _E, "Invalid fixed-width (ADF) record"),
        # 4xxx references
        _c("XAF4001", _E, "Account not defined"),
        _c("XAF4002", _E, "Customer/supplier not defined"),
        _c("XAF4003", _E, "VAT code not defined"),
        _c("XAF4004", _E, "Period not defined"),
        _c("XAF4005", _W, "Journal offset account not defined"),
        _c("XAF4006", _W, "VAT code account not defined"),
        _c("XAF4007", _E, "Opening balance account not defined"),
        # 5xxx totals & balance
        _c(
            "XAF5004",
            _E,
            "Opening balance totalDebit differs from the sum of debit lines",
            "[0004]",
        ),
        _c(
            "XAF5005",
            _E,
            "Opening balance totalCredit differs from the sum of credit lines",
            "[0005]",
        ),
        _c("XAF5006", _E, "Opening balance totalDebit differs from totalCredit", "[0006]"),
        _c("XAF5007", _E, "Transactions totalDebit differs from the sum of debit lines", "[0007]"),
        _c(
            "XAF5008", _E, "Transactions totalCredit differs from the sum of credit lines", "[0008]"
        ),
        _c("XAF5009", _E, "Transactions totalDebit differs from totalCredit", "[0009]"),
        _c("XAF5010", _E, "Transaction does not balance", "[0010]"),
        _c("XAF5011", _E, "Opening balance linesCount differs from the number of lines"),
        _c("XAF5012", _E, "Transactions linesCount differs from the number of lines"),
        _c("XAF5013", _W, "Opening balance does not balance"),
        # 6xxx uniqueness
        _c("XAF6001", _E, "Duplicate customer/supplier ID", "[0001]"),
        _c("XAF6002", _E, "Duplicate account ID", "[0002]"),
        _c("XAF6003", _W, "Duplicate RGS code", "[0003]"),
        _c("XAF6004", _E, "Duplicate transaction number within a journal"),
        _c("XAF6005", _E, "Duplicate line number within a transaction"),
        _c("XAF6006", _E, "Duplicate opening balance line number"),
        _c("XAF6007", _W, "Duplicate journal ID"),
        _c("XAF6008", _E, "Duplicate VAT code"),
        _c("XAF6009", _E, "Duplicate period number"),
        _c("XAF6010", _W, "Conflicting master data across files"),
        # 7xxx data quality
        _c("XAF7001", _I, "Negative amount"),
        _c("XAF7002", _W, "Transaction date outside the fiscal year"),
        _c("XAF7003", _W, "Transaction date outside its period"),
        _c("XAF7004", _W, "Opening balance present both as element and as transactions"),
        _c("XAF7005", _E, "Opening balance as transactions is not allowed in XAF 4.0"),
        _c("XAF7006", _W, "No opening balance"),
        _c("XAF7007", _W, "Fiscal year inconsistent with start and end date"),
        _c("XAF7008", _I, "Time-zone suffix removed from date"),
        _c("XAF7009", _W, "Identifier has leading or trailing whitespace"),
        _c("XAF7010", _I, "Effective date outside the fiscal year"),
        _c("XAF7011", _W, "Transaction without lines"),
        _c("XAF7012", _W, "Unusual code value"),
        _c("XAF7013", _W, "Line has both a debit and a credit amount"),
        _c("XAF7014", _W, "Period number not numeric"),
        # 8xxx RGS
        _c("XAF8001", _W, "Placeholder RGS code"),
        _c("XAF8002", _W, "RGS code does not look like an RGS reference code"),
        _c("XAF8003", _E, "RGS code not in the RGS version"),
        _c("XAF8004", _W, "RGS code was renamed in a later RGS version"),
        _c("XAF8005", _I, "RGS version not recognised"),
        _c("XAF8006", _I, "Account mapped to an RGS level other than 4 or 5"),
    )
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """One observation about an auditfile.

    Attributes:
        code: Stable code, e.g. ``"XAF5009"``; see :data:`CODES`.
        severity: Severity after rule-set adjustments and overrides.
        message: Human-readable English message.
        file: Index of the file within a multi-file set (0 for single files).
        line: 1-based line number of the element (``None`` if not applicable).
        column: 0-based column number.
        path: Element path (``/auditfile/company/...``) or ADF field name.
        value: The offending raw value, if any.
        rule_ref: Reference to the official rule, e.g. ``"[0009]"``.
    """

    code: str
    severity: Severity
    message: str
    file: int = 0
    line: int | None = None
    column: int | None = None
    path: str | None = None
    value: str | None = None
    rule_ref: str | None = None

    def __str__(self) -> str:
        where = (
            f"line {self.line}" + (f":{self.column}" if self.column is not None else "")
            if self.line is not None
            else "-"
        )
        prefix = f"#{self.file} " if self.file else ""
        val = f" [{self.value!r}]" if self.value is not None else ""
        return f"{prefix}{where} {self.severity.name} {self.code} {self.message}{val}"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dictionary."""
        return {
            "code": self.code,
            "severity": self.severity.name,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "path": self.path,
            "value": self.value,
            "rule_ref": self.rule_ref,
        }


@dataclass(slots=True)
class FindingCollector:
    """Collects findings with per-code and overall limits; counts what it suppresses.

    Args:
        max_per_code: Keep at most this many findings per code (``None`` = unlimited).
        max_total: Keep at most this many findings overall (``None`` = unlimited).
        overrides: Severity per code, replacing the default severity. Mapping a code to
            ``None`` silences it.
    """

    max_per_code: int | None = 100
    max_total: int | None = 10_000
    overrides: Mapping[str, Severity | None] = field(default_factory=dict)
    file: int = 0
    _items: list[Finding] = field(default_factory=list)
    _counts: Counter[str] = field(default_factory=Counter)
    _keys: set[tuple[object, ...]] = field(default_factory=set)

    def add(
        self,
        code: str,
        message: str,
        *,
        line: int | None = None,
        column: int | None = None,
        path: str | None = None,
        value: str | None = None,
        severity: Severity | None = None,
        file: int | None = None,
    ) -> None:
        """Record a finding (counted even when suppressed by a limit)."""
        info = CODES[code]
        key = (code, self.file if file is None else file, line, path, value, message)
        if key in self._keys:  # identical finding (e.g. from a repeated pass)
            return
        if code in self.overrides:
            sev = self.overrides[code]
            if sev is None:
                return
        else:
            sev = severity or info.severity
        self._counts[code] += 1
        if self.max_per_code is not None and self._counts[code] > self.max_per_code:
            return
        if self.max_total is not None and len(self._items) >= self.max_total:
            return
        self._keys.add(key)
        if value is not None and len(value) > 200:
            value = value[:200] + "…"
        self._items.append(
            Finding(
                code=code,
                severity=sev,
                message=message,
                file=self.file if file is None else file,
                line=line,
                column=column,
                path=path,
                value=value,
                rule_ref=info.rule_ref,
            )
        )

    def extend(self, findings: Iterable[Finding]) -> None:
        """Add already-built findings (respecting limits and overrides)."""
        for f in findings:
            self.add(
                f.code,
                f.message,
                line=f.line,
                column=f.column,
                path=f.path,
                value=f.value,
                severity=f.severity,
                file=f.file,
            )

    @property
    def counts(self) -> Mapping[str, int]:
        """Number of occurrences per code, including suppressed ones."""
        return dict(self._counts)

    @property
    def suppressed(self) -> int:
        """Number of findings dropped because of limits."""
        return sum(self._counts.values()) - len(self._items)

    def __iter__(self) -> Iterator[Finding]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def snapshot(self) -> tuple[Finding, ...]:
        """Return the findings collected so far."""
        return tuple(self._items)
