"""Layered validation in one streaming pass.

Layers (each listed in :attr:`ValidationReport.checked` when it ran):

========  =====================================================================================
L1        container and character encoding (``XAF1xxx``)
L2        XML well-formedness and security (``XAF2xxx``)
L3        version and namespace detection (``XAF3001``–``XAF3007``)
L4        structure against the version's field catalogue — required elements, unknown elements,
          cardinality, order, lengths, code lists, patterns, types (``XAF3010``–``XAF3021``);
          no dependencies, "XSD-like"
L4x       XSD validation with lxml (``XAF3040``; optional, ``pyxaf[xsd]``)
L5        references between records (``XAF4xxx``)
L6        control totals and balance, incl. official rules [0004]–[0010] (``XAF5xxx``)
L7        uniqueness, incl. rules [0001]–[0003] (``XAF6xxx``)
L8        data quality and conventions (``XAF7xxx``) and RGS references (``XAF8xxx``)
========  =====================================================================================
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from . import _xml
from ._encoding import sniff_encoding
from ._normalize import NegativePolicy
from ._source import SourceLike, open_source
from .catalogue import Catalogue, FieldSpec, get_catalogue
from .detect import _detect_source
from .errors import (
    CorruptArchiveError,
    ForbiddenConstructError,
    LimitExceededError,
    XmlSyntaxError,
)
from .findings import CODES, Finding, FindingCollector, Severity
from .formats import Family, FormatInfo, NamespaceStatus, Version
from .models import JournalKind, Line, Side, Transaction
from .raw import RawRecord
from .reader import AuditFile
from .values import parse_adf_amount, parse_amount, parse_date, parse_int

if TYPE_CHECKING:
    from .rgs import RgsSchema

__all__ = ["RuleSet", "ValidationReport", "validate"]

RuleSet = Literal["spec", "vts"]

_VTS_CODES = frozenset(
    {
        *(c for c in CODES if c.startswith(("XAF1", "XAF2"))),
        *(f"XAF30{n}" for n in range(10, 22)),
        "XAF3040",
        "XAF5004",
        "XAF5005",
        "XAF5006",
        "XAF5007",
        "XAF5008",
        "XAF5009",
        "XAF5010",
        "XAF6001",
        "XAF6002",
        "XAF6003",
    }
)
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?")
_TIME = re.compile(r"\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?")
_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Result of :func:`validate`.

    Attributes:
        format: Detection result.
        findings: Findings, most severe first, then in file order.
        counts: Occurrences per code, including findings suppressed by limits.
        suppressed: Number of findings dropped because of limits.
        checked: Validation layers that ran (see module documentation).
        stats: Counts gathered during the pass (lines, transactions, accounts…).
        rules: Rule set used.
    """

    format: FormatInfo
    findings: tuple[Finding, ...]
    counts: Mapping[str, int]
    suppressed: int
    checked: tuple[str, ...]
    stats: Mapping[str, int]
    rules: RuleSet = "spec"

    @property
    def errors(self) -> tuple[Finding, ...]:
        """Findings with severity ERROR."""
        return tuple(f for f in self.findings if f.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        """Findings with severity WARNING."""
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        """``True`` when there are no ERROR findings."""
        return not self.errors

    @property
    def max_severity(self) -> Severity | None:
        """Highest severity among the findings."""
        return max((f.severity for f in self.findings), default=None)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation (``schema_version`` 1)."""
        fmt = self.format
        return {
            "schema_version": 1,
            "format": {
                "family": fmt.family.value if fmt.family else None,
                "version": fmt.version.value if fmt.version else None,
                "namespace": fmt.namespace,
                "namespace_status": fmt.namespace_status.value,
                "encoding": fmt.encoding.effective,
                "bom": fmt.bom,
                "confidence": fmt.confidence,
                "reasons": list(fmt.reasons),
            },
            "ok": self.ok,
            "rules": self.rules,
            "checked": list(self.checked),
            "stats": dict(self.stats),
            "counts": dict(self.counts),
            "suppressed": self.suppressed,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, **kwargs: Any) -> str:
        """Serialise :meth:`to_dict` as JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    def __str__(self) -> str:
        head = (
            f"{self.format.version or 'unknown format'} — {len(self.errors)} error(s), "
            f"{len(self.warnings)} "
            f"warning(s), {len(self.findings) - len(self.errors) - len(self.warnings)} info"
        )
        lines = [head, *(str(f) for f in self.findings)]
        if self.suppressed:
            lines.append(f"({self.suppressed} more findings suppressed by limits)")
        return "\n".join(lines)


# ----------------------------------------------------------------------------- L4 structure
class _Structure:
    """Catalogue-driven structural checks fed by raw engine events."""

    def __init__(self, catalogue: Catalogue, findings: FindingCollector) -> None:
        self.cat = catalogue
        self.f = findings
        self.derived = catalogue.derived
        self.index: dict[str, dict[str, FieldSpec]] = {}
        for parent, specs in catalogue.children.items():
            self.index[parent] = {s.name: s for s in specs}
        self.path: list[str] = []
        self.seen: dict[int, list[str]] = defaultdict(list)
        self.value_cache: dict[tuple[str, str], tuple[str, str] | None] = {}

    def _unknown_sev(self) -> Severity | None:
        return Severity.WARNING if self.derived else None

    def event(self, ev: tuple[Any, ...]) -> None:
        kind = ev[0]
        if kind == _xml.START:
            local, depth, line = ev[1], ev[2], ev[3]
            del self.path[depth - 1 :]
            parent = "/" + "/".join(self.path) if self.path else ""
            self.path.append(local)
            seen = self.seen[depth - 1]
            if not seen or seen[-1] != local:  # collapse repeats: memory stays flat
                seen.append(local)
            self.seen[depth] = []
            if depth == 1:
                if local != "auditfile":
                    self.f.add(
                        "XAF3011", f"root element <{local}> should be <auditfile>", line=line
                    )
                return
            if local not in self.index.get(parent, {}):
                self.f.add(
                    "XAF3011",
                    f"<{local}> is not defined in {parent or '/'} for {self.cat.version}",
                    line=line,
                    path=f"{parent}/{local}",
                    severity=self._unknown_sev(),
                )
        elif kind == _xml.RECORD:
            rec: RawRecord = ev[1]
            depth = len(ev[2])
            del self.path[depth:]
            parent = "/" + "/".join(self.path)
            seen = self.seen[depth]
            if not seen or seen[-1] != rec.tag:
                seen.append(rec.tag)
            spec = self.index.get(parent, {}).get(rec.tag)
            if spec is None:
                self.f.add(
                    "XAF3011",
                    f"<{rec.tag}> is not defined in {parent} for {self.cat.version}",
                    line=rec.line,
                    path=f"{parent}/{rec.tag}",
                    severity=self._unknown_sev(),
                )
                return
            self.check(rec, spec.path, ())
        elif kind == _xml.END:
            rec = ev[1]
            depth = ev[2]
            path = "/" + "/".join(self.path[:depth])
            if self.path[:depth] and self.path[depth - 1] != rec.tag:  # pragma: no cover
                return
            self.check(rec, path, self.seen.get(depth, ()), container=True)
            self.seen.pop(depth, None)

    def check(
        self,
        rec: RawRecord,
        path: str,
        seen: Sequence[str],
        *,
        container: bool = False,
    ) -> None:
        specs = self.index.get(path)
        if specs is None:
            return
        line = rec.line
        add = self.f.add
        present: Counter[str] = Counter()
        last_order = -1
        out_of_order = False
        for name, value in rec.fields.items():
            spec = specs.get(name)
            if spec is None:
                if not container:  # containers report unknown leaves via START events
                    add(
                        "XAF3011",
                        f"<{name}> is not defined in {path} for {self.cat.version}",
                        line=line,
                        path=f"{path}/{name}",
                        severity=self._unknown_sev(),
                    )
                continue
            present[name] += 1
            if spec.order < last_order:
                out_of_order = True
            last_order = spec.order
            if spec.kind != "complex":
                self.value(spec, value, line)
            elif any(c.min_occurs for c in self.cat.children_of(spec.path)):
                add("XAF3010", f"<{name}> is empty", line=line, path=spec.path)
        last_order = -1
        for name, items in rec.children.items():
            spec = specs.get(name)
            if spec is None:
                if not container:
                    add(
                        "XAF3011",
                        f"<{name}> is not defined in {path} for {self.cat.version}",
                        line=items[0].line,
                        path=f"{path}/{name}",
                        severity=self._unknown_sev(),
                    )
                continue
            if items[0].text is not None:  # repeated leaf
                present[name] = len(items)
                for it in items[1:]:
                    self.value(spec, it.text or "", it.line)
                continue
            present[name] += len(items)
            if container:
                continue  # complex children of containers were checked at their own END event
            if spec.kind != "complex":
                inner = ", ".join(
                    sorted({f"<{c.tag}>" for it in items for c in it.iter_children()})
                )
                add(
                    "XAF3011",
                    f"<{name}> must contain text only, found element(s) {inner or '(none)'}",
                    line=items[0].line,
                    path=spec.path,
                )
                continue
            if spec.order < last_order:
                out_of_order = True
            last_order = spec.order
            for child in items:
                self.check(child, spec.path, ())
        if container:
            order = -1
            for name in seen:
                spec = specs.get(name)
                if spec is None:
                    continue
                if name not in rec.fields and name not in rec.children:
                    present[name] += 1
                elif name in rec.children and rec.children[name][0].text is None:
                    pass  # counted above
                if spec.order < order:
                    out_of_order = True
                order = spec.order
        if out_of_order and not self.derived:
            add("XAF3013", f"children of <{rec.tag}> are not in schema order", line=line, path=path)
        choices: dict[str, set[str]] = defaultdict(set)
        choice_required: dict[str, bool] = {}
        for spec in specs.values():
            n = present.get(spec.name, 0)
            if spec.choice is not None:
                group, _, branch = spec.choice.partition(".")
                choice_required[group] = choice_required.get(group, False) or bool(spec.min_occurs)
                if n:
                    choices[group].add(branch)
                continue
            if n < spec.min_occurs:
                add(
                    "XAF3010",
                    f"<{rec.tag}> lacks required <{spec.name}>",
                    line=line,
                    path=spec.path,
                    severity=Severity.WARNING if self.derived else None,
                )
            elif spec.max_occurs is not None and n > spec.max_occurs:
                add(
                    "XAF3012",
                    f"<{spec.name}> occurs {n} times in <{rec.tag}> (at most {spec.max_occurs})",
                    line=line,
                    path=spec.path,
                )
        for group, required in choice_required.items():
            branches = choices.get(group, set())
            names = [
                s.name for s in specs.values() if s.choice and s.choice.partition(".")[0] == group
            ]
            if not branches and required:
                add(
                    "XAF3010",
                    f"<{rec.tag}> needs one of {', '.join(f'<{x}>' for x in names)}",
                    line=line,
                    path=path,
                )
            elif len(branches) > 1:
                add(
                    "XAF3012",
                    f"<{rec.tag}> may contain only one of {', '.join(f'<{x}>' for x in names)}",
                    line=line,
                    path=path,
                )

    def value(self, spec: FieldSpec, value: str, line: int) -> None:
        key = (spec.path, value)
        cache = self.value_cache
        if key in cache:
            problem = cache[key]
        else:
            problem = _value_problem(spec, value)
            if len(cache) > 50_000:
                cache.clear()
            cache[key] = problem
        if problem is not None:
            self.f.add(problem[0], problem[1], line=line, path=spec.path, value=value)


def _value_problem(spec: FieldSpec, value: str) -> tuple[str, str] | None:
    kind = spec.kind
    name = spec.name
    if spec.fixed is not None and value.strip() != spec.fixed:
        return "XAF3020", f"<{name}> must be {spec.fixed!r}"
    if kind == "string":
        n = len(value)
        if spec.length is not None and n != spec.length:
            return "XAF3014", f"<{name}> must be exactly {spec.length} characters (has {n})"
        if spec.max_length is not None and n > spec.max_length:
            return "XAF3014", f"<{name}> is longer than {spec.max_length} characters ({n})"
        if spec.min_length is not None and n < spec.min_length:
            return "XAF3014", f"<{name}> is shorter than {spec.min_length} characters ({n})"
        if spec.enum is not None and value not in spec.enum:
            shown = ", ".join(spec.enum[:12]) + (", …" if len(spec.enum) > 12 else "")
            return "XAF3015", f"<{name}> must be one of {shown}"
        if spec.patterns and not any(p.fullmatch(value) for p in spec.patterns):
            return "XAF3016", f"<{name}> does not match {spec.patterns[0].pattern!r}"
        return None
    v = value.strip()
    if kind == "date":
        if parse_date(v)[0] is None:
            return "XAF3017", f"<{name}> is not a valid date (YYYY-MM-DD)"
        return None
    if kind == "decimal":
        d = parse_amount(v)
        if d is None:
            return "XAF3018", f"<{name}> is not a valid decimal number"
        # facets apply to the value: trailing fraction zeros do not count
        _sign, digits, exp = d.normalize().as_tuple() if d else (0, (0,), 0)
        exp = exp if isinstance(exp, int) else 0
        frac = -exp if exp < 0 else 0
        digits = (*digits, *([0] * exp)) if exp > 0 else digits
        if spec.fraction_digits is not None and frac > spec.fraction_digits:
            return "XAF3018", f"<{name}> has more than {spec.fraction_digits} decimals"
        if spec.total_digits is not None and max(len(digits), frac) > spec.total_digits:
            return "XAF3018", f"<{name}> has more than {spec.total_digits} digits"
        if spec.min_inclusive is not None and d < spec.min_inclusive:
            return "XAF3021", f"<{name}> must be at least {spec.min_inclusive}"
        if spec.enum is not None and v not in spec.enum:
            return "XAF3015", f"<{name}> must be one of {', '.join(spec.enum)}"
        return None
    if kind == "integer":
        i = parse_int(v)
        if i is None:
            return "XAF3019", f"<{name}> is not a valid integer"
        if spec.total_digits is not None and len(str(abs(i))) > spec.total_digits:
            return "XAF3019", f"<{name}> has more than {spec.total_digits} digits"
        if spec.min_inclusive is not None and i < spec.min_inclusive:
            return "XAF3021", f"<{name}> must be at least {spec.min_inclusive}"
        if spec.max_inclusive is not None and i > spec.max_inclusive:
            return "XAF3021", f"<{name}> must be at most {spec.max_inclusive}"
        return None
    if kind == "datetime" and not _DATETIME.fullmatch(v):
        return "XAF3017", f"<{name}> is not a valid date-time"
    if kind == "time" and not _TIME.fullmatch(v):
        return "XAF3017", f"<{name}> is not a valid time"
    if kind == "boolean" and v not in ("true", "false", "1", "0"):
        return "XAF3015", f"<{name}> must be true or false"
    return None


# ------------------------------------------------------------------------- L5–L8 semantics
@dataclass(slots=True)
class _Totals:
    lines: int = 0
    debit: Decimal = _ZERO
    credit: Decimal = _ZERO


@dataclass(slots=True)
class _Semantics:
    af: AuditFile
    f: FindingCollector
    rgs: RgsSchema | None = None
    per_file: dict[int, _Totals] = field(default_factory=lambda: defaultdict(_Totals))
    tx_numbers: dict[tuple[int, str | None], set[str]] = field(default_factory=dict)
    record_ids: set[str] = field(default_factory=set)
    tx_count: int = 0
    line_count: int = 0
    ob_tx_lines: int = 0
    eff_outside: int = 0

    def master(self) -> None:
        af = self.af
        f = self.f
        accounts = af.accounts
        # [0003] RGS codes unique; RGS quality
        rgs_seen: dict[str, str] = {}
        for acc in accounts.values():
            ref = acc.rgs
            if ref is None:
                continue
            line = acc.raw.line if acc.raw is not None else None
            if ref.placeholder:
                f.add(
                    "XAF8001",
                    f"account {acc.id!r} has placeholder RGS code",
                    line=line,
                    value=ref.raw,
                )
                continue
            if ref.code is None:
                f.add(
                    "XAF8002",
                    f"account {acc.id!r}: RGS code not recognisable",
                    line=line,
                    value=ref.raw,
                )
                continue
            key = ref.raw.strip()
            if key in rgs_seen and af.version is Version.XAF40:
                f.add(
                    "XAF6003",
                    f"RGS code {key!r} also used by account {rgs_seen[key]!r}",
                    line=line,
                    value=key,
                )
            rgs_seen.setdefault(key, acc.id)
            if self.rgs is not None:
                self.rgs.check_ref(ref, f, line=line, account_id=acc.id)
        if self.rgs is not None and af.header.rgs_version:
            self.rgs.check_version(af.header.rgs_version, f)
        # L5 master references
        for vat in af.vat_codes.values():
            line = vat.raw.line if vat.raw is not None else None
            for acc_id in (vat.payable_account_id, vat.receivable_account_id):
                if acc_id and accounts and acc_id not in accounts:
                    f.add(
                        "XAF4006",
                        f"VAT code {vat.id!r} refers to undefined account {acc_id!r}",
                        line=line,
                        value=acc_id,
                    )
        for rel in af.relations.values():
            code = rel.relation_type.code
            if code and rel.relation_type.kind.value == "unknown":
                f.add(
                    "XAF7012",
                    f"customer/supplier {rel.id!r} has type {code!r}",
                    line=rel.raw.line if rel.raw is not None else None,
                    value=code,
                )
        # header
        h = af.header
        if h.start_date and h.end_date and h.fiscal_year:
            fy = h.fiscal_year.strip()
            years = {int(y) for y in re.findall(r"\d{4}", fy)}
            if years and not (years & {h.start_date.year, h.end_date.year}):
                f.add(
                    "XAF7007",
                    f"fiscal year {fy!r} does not match {h.start_date}..{h.end_date}",
                    value=fy,
                )
        # opening balance element
        ob = af.opening_balance("element")
        if ob.source == "element":
            self.opening_balance_element()

    def opening_balance_element(self) -> None:
        af = self.af
        f = self.f
        ob = af.opening_balance("element")
        d = c = _ZERO
        nrs: set[str] = set()
        for ln in ob.lines:
            line = ln.raw.line if ln.raw is not None else None
            if ln.amount is not None:
                if ln.side is Side.DEBIT:
                    d += ln.amount
                elif ln.side is Side.CREDIT:
                    c += ln.amount
            if ln.account_id and af.accounts and ln.account_id not in af.accounts:
                f.add(
                    "XAF4007",
                    f"opening balance line refers to undefined account {ln.account_id!r}",
                    line=line,
                    value=ln.account_id,
                )
            if ln.number is not None:
                if ln.number in nrs:
                    f.add(
                        "XAF6006",
                        f"duplicate opening balance line number {ln.number!r}",
                        line=line,
                        value=ln.number,
                    )
                nrs.add(ln.number)
        decl = ob.declared
        if decl is not None:
            if decl.total_debit is not None and decl.total_debit != d:
                f.add(
                    "XAF5004",
                    f"openingBalance totalDebit {decl.total_debit} ≠ sum of debit lines {d}",
                    value=str(decl.total_debit),
                )
            if decl.total_credit is not None and decl.total_credit != c:
                f.add(
                    "XAF5005",
                    f"openingBalance totalCredit {decl.total_credit} ≠ sum of credit lines {c}",
                    value=str(decl.total_credit),
                )
            if (
                decl.total_debit is not None
                and decl.total_credit is not None
                and decl.total_debit != decl.total_credit
            ):
                f.add(
                    "XAF5006",
                    f"openingBalance totalDebit {decl.total_debit} ≠ totalCredit "
                    f"{decl.total_credit}",
                )
            if decl.lines_count is not None and decl.lines_count != len(ob.lines):
                f.add(
                    "XAF5011",
                    f"openingBalance linesCount {decl.lines_count} ≠ {len(ob.lines)} lines",
                    value=str(decl.lines_count),
                )
        if d != c:
            f.add("XAF5013", f"opening balance does not balance: debit {d}, credit {c}")

    def transaction(self, tx: Transaction) -> None:
        af = self.af
        f = self.f
        self.tx_count += 1
        line = tx.raw.line if tx.raw is not None else None
        totals = self.per_file[tx.file]
        clair2 = af.format.family is Family.CLAIR2
        # uniqueness of transaction numbers per journal (CLAIR2: per file, an XSD key)
        if tx.number is not None:
            key = (tx.file, None if clair2 else tx.journal_id)
            nrs = self.tx_numbers.setdefault(key, set())
            if tx.number in nrs:
                f.add(
                    "XAF6004",
                    f"transaction number {tx.number!r} occurs more than once in "
                    f"journal {tx.journal_id!r}",
                    line=line,
                    value=tx.number,
                )
            else:
                nrs.add(tx.number)
        if not tx.lines:
            f.add("XAF7011", f"transaction {tx.number!r} has no lines", line=line)
        d = c = _ZERO
        line_nrs: set[str] = set()
        accounts, relations, vat_codes, periods = (
            af.accounts,
            af.relations,
            af.vat_codes,
            af.periods,
        )
        for ln in tx.lines:
            self.line_count += 1
            totals.lines += 1
            ln_line = ln.raw.line if ln.raw is not None else line
            if ln.amount is not None:
                if af.format.family is Family.XAF:
                    if ln.side is Side.DEBIT:
                        d += ln.amount
                    elif ln.side is Side.CREDIT:
                        c += ln.amount
                else:  # CLAIR2 / ADF: the written debit and credit columns
                    wd, wc = _written_dc(ln)
                    d += wd
                    c += wc
            if ln.number is not None:
                scope = self.record_ids if clair2 else line_nrs
                if ln.number in scope:
                    where = "the file" if clair2 else f"transaction {tx.number!r}"
                    f.add(
                        "XAF6005",
                        f"line number {ln.number!r} occurs more than once in {where}",
                        line=ln_line,
                        value=ln.number,
                    )
                scope.add(ln.number)
            self._line_refs(ln, ln_line, accounts, relations, vat_codes)
        totals.debit += d
        totals.credit += c
        if d != c:
            f.add(
                "XAF5010",
                f"transaction {tx.number!r} in journal {tx.journal_id!r} does not "
                f"balance: debit {d}, credit {c}",
                line=line,
            )
        # periods and dates
        if (
            tx.period_key is not None
            and periods
            and tx.period_key not in periods
            and not (tx.period_number == 0 and af.version is not Version.XAF40)
        ):  # period 0 is the 3.x/CLAIR2/ADF opening-balance convention, not a defined period
            alt = str(tx.period_number) if tx.period_number is not None else None
            if alt is None or alt not in periods:
                f.add(
                    "XAF4004",
                    f"period {tx.period_key!r} is not defined",
                    line=line,
                    value=tx.period_key,
                )
        h = af.header
        if (
            tx.date is not None
            and h.start_date
            and h.end_date
            and not (h.start_date <= tx.date <= h.end_date)
        ):
            f.add(
                "XAF7002",
                f"transaction date {tx.date} outside the fiscal year {h.start_date}..{h.end_date}",
                line=line,
                value=str(tx.date),
            )
        per = periods.get(tx.period_key or "") if periods else None
        if (
            per is not None
            and tx.date is not None
            and per.start_date
            and per.end_date
            and not (per.start_date <= tx.date <= per.end_date)
        ):
            f.add(
                "XAF7003",
                f"transaction date {tx.date} outside period {per.key} "
                f"({per.start_date}..{per.end_date})",
                line=line,
                value=str(tx.date),
            )
        if h.start_date and h.end_date:
            for ln in tx.lines:
                ed = ln.effective_date
                if ed is not None and not (h.start_date <= ed <= h.end_date):
                    self.eff_outside += 1
        if self._is_ob(tx):
            self.ob_tx_lines += len(tx.lines)

    def _is_ob(self, tx: Transaction) -> bool:
        if tx.period_key in ("0", "00", "000"):
            return True
        jr = self.af._master.journals.get(tx.journal_id or "")
        return jr is not None and jr.journal_type.kind is JournalKind.OPENING

    def _line_refs(
        self,
        ln: Line,
        line: int | None,
        accounts: Mapping[str, Any],
        relations: Mapping[str, Any],
        vat_codes: Mapping[str, Any],
    ) -> None:
        f = self.f
        if ln.account_id is not None and accounts and ln.account_id not in accounts:
            f.add(
                "XAF4001",
                f"account {ln.account_id!r} is not defined",
                line=line,
                value=ln.account_id,
            )
        if ln.relation_id is not None and relations and ln.relation_id not in relations:
            f.add(
                "XAF4002",
                f"customer/supplier {ln.relation_id!r} is not defined",
                line=line,
                value=ln.relation_id,
            )
        for v in ln.vat:
            if (
                v.code is not None
                and self.af.format.family is Family.XAF
                and vat_codes
                and v.code not in vat_codes
            ):
                f.add("XAF4003", f"VAT code {v.code!r} is not defined", line=line, value=v.code)

    def finish(self) -> None:
        af = self.af
        f = self.f
        # control totals per file
        for part in af._parts:
            frame = part.transactions_frame
            if frame is None:
                continue
            from ._normalize import Normalizer  # noqa: PLC0415

            norm = Normalizer(af.version, findings=f, value_findings=False)
            decl = norm.totals(RawRecord("transactions", frame, None, 0))
            if decl is None:
                continue
            got = self.per_file[part.index]
            fi = part.index
            count_name = "numberEntries" if af.format.family is Family.CLAIR2 else "linesCount"
            if decl.total_debit is not None and decl.total_debit != got.debit:
                f.add(
                    "XAF5007",
                    f"transactions totalDebit {decl.total_debit} ≠ sum of debit lines {got.debit}",
                    value=str(decl.total_debit),
                    file=fi,
                )
            if decl.total_credit is not None and decl.total_credit != got.credit:
                f.add(
                    "XAF5008",
                    f"transactions totalCredit {decl.total_credit} ≠ sum of credit "
                    f"lines {got.credit}",
                    value=str(decl.total_credit),
                    file=fi,
                )
            if (
                decl.total_debit is not None
                and decl.total_credit is not None
                and decl.total_debit != decl.total_credit
            ):
                f.add(
                    "XAF5009",
                    f"transactions totalDebit {decl.total_debit} ≠ totalCredit {decl.total_credit}",
                    file=fi,
                )
            if decl.lines_count is not None and decl.lines_count != got.lines:
                hint = ""
                if af.format.family is Family.CLAIR2 and decl.lines_count == self.tx_count:
                    hint = " (it equals the number of transactions, as some exporters write)"
                f.add(
                    "XAF5012",
                    f"{count_name} {decl.lines_count} ≠ {got.lines} lines{hint}",
                    value=str(decl.lines_count),
                    file=fi,
                )
        if af.format.family is Family.ADF and af.transaction_totals is not None:
            decl = af.transaction_totals
            got = self.per_file[0]
            if decl.total_debit is not None and decl.total_debit != got.debit:
                f.add("XAF5007", f"header Telling debet {decl.total_debit} ≠ sum {got.debit}")
            if decl.total_credit is not None and decl.total_credit != got.credit:
                f.add("XAF5008", f"header Telling credit {decl.total_credit} ≠ sum {got.credit}")
            if decl.lines_count is not None and decl.lines_count != got.lines:
                f.add("XAF5012", f"header Aantal mutaties {decl.lines_count} ≠ {got.lines} lines")
        # opening balance conventions
        ob = af.opening_balance("element")
        has_element = ob.source == "element"
        if has_element and self.ob_tx_lines:
            f.add(
                "XAF7004",
                f"opening balance given as element and as {self.ob_tx_lines} "
                "line(s) in period 0 / opening journals: risk of double counting",
            )
        if af.version is Version.XAF40 and self.ob_tx_lines:
            f.add(
                "XAF7005",
                "XAF 4.0 requires the opening balance in <openingBalance>, not as "
                "period-0 or opening-journal transactions",
            )
        if not has_element and not self.ob_tx_lines:
            f.add(
                "XAF7006",
                "no opening balance found",
                severity=Severity.WARNING if af.version is Version.XAF40 else Severity.INFO,
            )
        if self.eff_outside:
            f.add(
                "XAF7010",
                f"{self.eff_outside} line(s) have an effective date outside the fiscal year",
            )
        # journals
        for jr in af.journals.values():
            if jr.offset_account_id and af.accounts and jr.offset_account_id not in af.accounts:
                f.add(
                    "XAF4005",
                    f"journal {jr.id!r} offset account {jr.offset_account_id!r} is not defined",
                    value=jr.offset_account_id,
                )


def _written_dc(ln: Line) -> tuple[Decimal, Decimal]:
    """Debit and credit as written in a CLAIR2/ADF line (before sign handling)."""
    raw = ln.raw
    if raw is None:
        return ln.debit or _ZERO, ln.credit or _ZERO
    f = raw.fields
    if raw.tag == "adfLine":
        d = parse_adf_amount(f.get("debet", "")) if f.get("debet", "").strip() else _ZERO
        c = parse_adf_amount(f.get("credit", "")) if f.get("credit", "").strip() else _ZERO
    else:
        d = parse_amount(f["debitAmount"]) if "debitAmount" in f else _ZERO
        c = parse_amount(f["creditAmount"]) if "creditAmount" in f else _ZERO
    return d or _ZERO, c or _ZERO


class _JournalIds:
    """Detect duplicate journal IDs from raw END events (the reader keeps the first only)."""

    def __init__(self, findings: FindingCollector, family: Family) -> None:
        self.f = findings
        self.key = "journalID" if family is Family.CLAIR2 else "jrnID"
        self.seen: set[tuple[int, str]] = set()

    def event(self, file: int, ev: tuple[Any, ...]) -> None:
        if ev[0] == _xml.END and ev[1].tag == "journal":
            jid = (ev[1].fields.get(self.key) or "").strip()
            if (file, jid) in self.seen:
                self.f.add(
                    "XAF6007",
                    f"journal ID {jid!r} occurs more than once",
                    line=ev[1].line,
                    value=jid,
                )
            self.seen.add((file, jid))


# ------------------------------------------------------------------------------- entry point
def validate(
    source: SourceLike | Sequence[SourceLike],
    *,
    xsd: bool = False,
    rules: RuleSet = "spec",
    rgs: RgsSchema | None = None,
    encoding: str | None = None,
    repair: Iterable[str] = (),
    negative_amounts: NegativePolicy = "flip",
    severity_overrides: Mapping[str, Severity | None] | None = None,
    max_findings_per_code: int | None = 100,
    max_findings: int | None = 10_000,
    max_depth: int = _xml.DEFAULT_MAX_DEPTH,
    max_text_size: int = _xml.DEFAULT_MAX_TEXT,
    max_decompressed_size: int | None = None,
) -> ValidationReport:
    """Validate an auditfile (or multi-file set) in one streaming pass.

    Args:
        source: Path, bytes, binary stream, or a sequence of them for a split auditfile.
        xsd: Also validate against the official XSD (needs ``pyxaf[xsd]``).
        rules: ``"spec"`` (everything, per the detected version) or ``"vts"`` (only what the
            Belastingdienst's validation service checks: encoding, XML, schema and the numbered
            4.0 consistency rules).
        rgs: An :class:`~pyxaf.rgs.RgsSchema` to check RGS codes against.
        encoding: Override the declared encoding.
        repair: Opt-in repairs (see :func:`pyxaf.open`).
        negative_amounts: Sign policy (see :func:`pyxaf.open`).
        severity_overrides: Change severities per code, or silence codes with ``None``.
        max_findings_per_code: Keep at most this many findings per code.
        max_findings: Keep at most this many findings in total.
        max_depth: Maximum XML nesting depth.
        max_text_size: Maximum text length of one element.
        max_decompressed_size: Maximum decompressed size of gzip/zip input.

    Returns:
        A :class:`ValidationReport`. Problems in the data never raise; only unreadable input
            (not an auditfile, unsupported source) does.
    """
    if rules not in ("spec", "vts"):
        raise ValueError("rules must be 'spec' or 'vts'")
    checked: list[str] = ["L1 encoding", "L2 xml", "L3 version"]
    single = (
        isinstance(source, (str, bytes, bytearray, memoryview))
        or hasattr(source, "read")
        or hasattr(source, "__fspath__")
    )
    items: list[Any] = [source] if single else list(source)  # type: ignore[arg-type]
    sources = [open_source(s, max_decompressed_size=max_decompressed_size) for s in items]
    overrides: dict[str, Severity | None] = dict(severity_overrides or {})
    if rules == "vts":  # drop non-VTS codes before limits apply
        overrides |= {c: None for c in CODES if c not in _VTS_CODES}
    try:
        info = _detect_source(sources[0], encoding)
    except (ForbiddenConstructError, CorruptArchiveError) as exc:
        fc = FindingCollector(overrides=overrides)
        _report_fatal(fc, exc)
        try:
            head = sources[0].head()
        except CorruptArchiveError:
            head = b""
        empty = FormatInfo(
            family=None,
            version=None,
            namespace=None,
            namespace_status=NamespaceStatus.UNKNOWN,
            encoding=sniff_encoding(head, encoding),
            confidence=0.0,
            reasons=("refused before detection",),
        )
        return _report(empty, fc, ("L1 encoding", "L2 xml"), {}, rules)
    family = info.family or Family.XAF
    fc = FindingCollector(
        max_per_code=max_findings_per_code,
        max_total=max_findings,
        overrides=overrides,
    )
    structures: dict[int, _Structure] = {}
    catalogue: Catalogue | None = None
    structure: _Structure | None = None
    journal_ids: _JournalIds | None = None
    if family is not Family.ADF and info.version is not None:
        catalogue = get_catalogue(info.version)
        structure = _Structure(catalogue, fc)
        journal_ids = _JournalIds(fc, family)
        checked.append("L4 structure" + (" (derived catalogue)" if catalogue.derived else ""))

    def observer(file: int, ev: tuple[Any, ...]) -> None:
        # one checker state per file: master data of all files is read before transactions
        st = structures.get(file)
        if st is None:
            assert catalogue is not None
            st = structures[file] = _Structure(catalogue, fc)
        fc.file = file
        st.event(ev)
        if journal_ids is not None:
            journal_ids.event(file, ev)

    try:
        af = AuditFile(
            sources,
            encoding=encoding,
            repair=repair,
            negative_amounts=negative_amounts,
            _observer=observer if structure is not None else None,
            _value_findings=family is Family.ADF,
            _findings=fc,
            max_depth=max_depth,
            max_text_size=max_text_size,
            max_decompressed_size=max_decompressed_size,
        )
    except (XmlSyntaxError, LimitExceededError, CorruptArchiveError) as exc:
        _report_fatal(fc, exc)
        return _report(info, fc, tuple(checked), {}, rules)
    sem = _Semantics(af, fc, rgs)
    fc.file = 0
    sem.master()
    failed = False
    try:
        for tx in af.transactions():
            sem.transaction(tx)
    except (XmlSyntaxError, LimitExceededError, CorruptArchiveError) as exc:
        failed = True
        _report_fatal(fc, exc)
    else:
        sem.finish()
        if rules == "spec":
            checked += ["L5 references", "L6 totals", "L7 uniqueness", "L8 data quality"]
        else:
            checked += ["L6 totals (4.0 rules)", "L7 uniqueness (4.0 rules)"]
        if rgs is not None and rules == "spec":
            checked.append("L8 RGS")
    if xsd and not failed:
        from ._xsd import validate_xsd  # noqa: PLC0415

        ran = validate_xsd(af, fc)
        if ran:
            checked.append("L4x xsd")
    af.close()
    stats = {
        "transactions": sem.tx_count,
        "lines": sem.line_count,
        "accounts": len(af.accounts),
        "relations": len(af.relations),
        "vat_codes": len(af.vat_codes),
        "periods": len(af.periods),
        "journals": len(af._master.journals if failed else af.journals),
        "opening_balance_lines": len(af.opening_balance("element").lines),
    }
    return _report(af.format, fc, tuple(checked), stats, rules)


def _report_fatal(fc: FindingCollector, exc: Exception | None) -> None:
    if exc is None:
        return
    if isinstance(exc, CorruptArchiveError):
        fc.add("XAF1021", str(exc))
    elif isinstance(exc, ForbiddenConstructError):
        fc.add("XAF2002", str(exc), line=exc.line, column=exc.column, file=exc.file)
    elif isinstance(exc, XmlSyntaxError):
        fc.add("XAF2001", str(exc), line=exc.line, column=exc.column, file=exc.file)
    else:
        fc.add("XAF2003", str(exc))


def _report(
    info: FormatInfo,
    fc: FindingCollector,
    checked: tuple[str, ...],
    stats: Mapping[str, int],
    rules: RuleSet,
) -> ValidationReport:
    items = list(fc)
    counts = dict(fc.counts)
    if rules == "vts":
        items = [x for x in items if x.code in _VTS_CODES]
        counts = {k: v for k, v in counts.items() if k in _VTS_CODES}
    items.sort(key=lambda x: (-x.severity, x.file, x.line or 0))
    return ValidationReport(
        format=info,
        findings=tuple(items),
        counts=counts,
        suppressed=fc.suppressed,
        checked=checked,
        stats=stats,
        rules=rules,
    )


def _date_or_none(value: object) -> dt.date | None:  # pragma: no cover - helper for typing
    return value if isinstance(value, dt.date) else None
