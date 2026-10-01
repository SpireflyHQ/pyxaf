"""RGS (Referentie Grootboekschema) reference data and checks.

pyxaf does not bundle RGS data: the official RGS Excel carries no licence or reuse statement.
Download the Excel release yourself (e.g. ``RGS 3.8-def.xlsx``) and load it with
:func:`load_excel`; the workbook is read with a small standard-library ``.xlsx`` reader, so no
extra is needed.

Example:
    >>> import pyxaf, pyxaf.rgs
    >>> schema = pyxaf.rgs.load_excel("RGS-3.8-def.xlsx")       # doctest: +SKIP
    >>> schema.version, len(schema)                              # doctest: +SKIP
    ('3.8', 4979)
    >>> schema.parent("BIvaKouVvp").code                         # doctest: +SKIP
    'BIvaKou'
    >>> report = pyxaf.validate("2024.xaf", rgs=schema)          # doctest: +SKIP

RGS codes form a strict prefix hierarchy: every code consists of ``B`` (balance) or ``W``
(profit and loss) followed by three-character segments, and the parent of a code is its longest
proper prefix that is itself a code. Levels run from 1 (``B``, ``W``) to 5 (mutations such as
``…Beg``, ``…Inv``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import IO, TYPE_CHECKING, Final

from ..findings import Finding, FindingCollector
from ..models import RgsRef
from ._xlsx import DEFAULT_MAX_MEMBER_SIZE, XlsxError, XlsxWorkbook

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from ..reader import AuditFile

__all__ = [
    "RgsCode",
    "RgsSchema",
    "XlsxError",
    "load_excel",
    "parse_ref",
    "parse_version",
    "validate_refs",
]

#: Levels an account should normally be mapped to (4 = account, 5 = mutation).
ACCOUNT_LEVELS: Final = frozenset({4, 5})

_HEADER_SCAN_ROWS: Final = 10
_FALSY: Final = frozenset({"", "0", "false", "nee", "n", "no"})
_VERSION_IN_NAME = re.compile(r"rgs\s*[-_ ]?\s*v?([0-9]+(?:[.,][0-9]+)+)", re.IGNORECASE)
_VERSION_LENIENT = re.compile(r"(?<![0-9.])([0-9]+(?:[.,][0-9]+)*)")

# normalised header text → field name
_FIELDS: Final[Mapping[str, str]] = {
    "referentiecode": "code",
    "rgscode": "code",
    "referentieomslagcode": "opposite_code",
    "omslagcode": "opposite_code",
    "sortering": "sort_key",
    "referentienummer": "reference_number",
    "omschrijvingverkort": "short_description",
    "verkorteomschrijving": "short_description",
    "omschrijving": "description",
    "dc": "debit_credit",
    "debetcredit": "debit_credit",
    "debitcredit": "debit_credit",
    "nivo": "level",
    "niveau": "level",
    "level": "level",
}


def _norm(text: str | None) -> str:
    """Normalise a header: lower case, letters and digits only."""
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True, slots=True, kw_only=True)
class RgsCode:
    """One RGS reference code.

    Attributes:
        code: The reference code, e.g. ``"BIvaKouVvp"``.
        level: Hierarchy level 1–5 (``Nivo``), ``None`` if absent or not numeric.
        description: Full description (``Omschrijving``).
        short_description: Short description (``Omschrijving (verkort)``).
        debit_credit: ``"D"`` or ``"C"`` (``D/C``) — the normal balance side.
        sort_key: Presentation order key (``Sortering``, e.g. ``"A.A.A010"``).
        reference_number: ``Referentienummer`` as stored. Formats are irregular (``"0101010.01"``,
            ``"01"``) and some values were stored as numbers in Excel, losing a leading zero
            (``"101015"``); the value is kept exactly as written.
        opposite_code: ``ReferentieOmslagcode``, the code on the other balance side used when the
            balance flips (e.g. a bank balance that becomes an overdraft).
        entities: Names of the entity-filter columns set for the code (``Basis``, ``Uitgebr``,
            ``EZ/VOF``, ``ZZP``, ``WoCo``, ``BV``, …).
        elimination_filters: Names of the columns in the "Filters - te vervallen c.q. te
            elimineren" group that are set: the code can be dropped when the entity does not use
            that feature. Empty when the workbook has no such group.
    """

    code: str
    level: int | None
    description: str | None
    short_description: str | None = None
    debit_credit: str | None = None
    sort_key: str | None = None
    reference_number: str | None = None
    opposite_code: str | None = None
    entities: frozenset[str] = frozenset()
    elimination_filters: frozenset[str] = frozenset()


def parse_version(text: str | None) -> str | None:
    """Extract an RGS version number from free text, leniently.

    Accepts ``"3.8"``, ``"RGS 3.8"``, ``"RGS3.8-def"``, ``"rgs-3,7"``, ``"Versie 3.5.0"``; a
    trailing ``.0`` is dropped. Returns ``None`` when no number is found.

    Args:
        text: Any text that may contain a version (a header value, sheet or file name).

    Returns:
        The normalised version (``"3.8"``) or ``None``.
    """
    if not text:
        return None
    m = _VERSION_IN_NAME.search(text) or _VERSION_LENIENT.search(text)
    if m is None:
        return None
    parts = [p.lstrip("0") or "0" for p in re.split(r"[.,]", m.group(1))]
    while len(parts) > 2 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def parse_ref(raw: str, source: str = "RGScode") -> RgsRef:
    """Split a raw RGS reference into code and extension (see :meth:`pyxaf.models.RgsRef.parse`).

    Args:
        raw: The value as written in the auditfile.
        source: Where the value came from (``RGScode``, ``taxonomy``, …).
    """
    return RgsRef.parse(raw, source)


class RgsSchema:
    """One RGS release: codes, hierarchy and renames.

    Usually created by :func:`load_excel`.

    Args:
        codes: The codes of this release (iteration order is kept as the sheet order).
        version: The RGS version, e.g. ``"3.8"``.
        rename_map: Old code → code in this version, for codes renamed in earlier releases.
        duplicate_codes: Codes that occurred more than once in the source (the first wins).

    Attributes:
        version: The RGS version (``"3.8"``) or ``None`` if it could not be determined.
        codes: Codes by reference code, in sheet order.
        rename_map: Old code → code in this version. Only codes that no longer exist in this
            version are included.
        duplicate_codes: Codes that occurred more than once in the source sheet.
    """

    __slots__ = ("_children", "codes", "duplicate_codes", "rename_map", "version")

    def __init__(
        self,
        codes: Iterable[RgsCode],
        *,
        version: str | None = None,
        rename_map: Mapping[str, str] | None = None,
        duplicate_codes: Iterable[str] = (),
    ) -> None:
        table: dict[str, RgsCode] = {}
        for c in codes:
            table.setdefault(c.code, c)
        self.version: str | None = version
        self.codes: Mapping[str, RgsCode] = MappingProxyType(table)
        self.rename_map: Mapping[str, str] = MappingProxyType(
            {old: new for old, new in (rename_map or {}).items() if old not in table}
        )
        self.duplicate_codes: frozenset[str] = frozenset(duplicate_codes)
        self._children: dict[str, tuple[RgsCode, ...]] | None = None

    def __repr__(self) -> str:
        return f"RgsSchema(version={self.version!r}, codes={len(self.codes)})"

    def __contains__(self, code: object) -> bool:
        return code in self.codes

    def __len__(self) -> int:
        return len(self.codes)

    def __iter__(self) -> Iterator[RgsCode]:
        return iter(self.codes.values())

    # ------------------------------------------------------------------ lookup
    def resolve(self, code: str) -> RgsCode | None:
        """Return the code, following :attr:`rename_map` for codes renamed since.

        Args:
            code: A reference code (surrounding whitespace is ignored).

        Returns:
            The :class:`RgsCode` in this version, or ``None`` if unknown.
        """
        code = code.strip()
        found = self.codes.get(code)
        if found is None and code in self.rename_map:
            found = self.codes.get(self.rename_map[code])
        return found

    def parent(self, code: str) -> RgsCode | None:
        """Return the parent: the longest proper prefix of ``code`` that is a code.

        Works for codes that are not in the schema too (the nearest existing ancestor).
        """
        code = code.strip()
        for k in range(len(code) - 1, 0, -1):
            found = self.codes.get(code[:k])
            if found is not None:
                return found
        return None

    def children(self, code: str) -> tuple[RgsCode, ...]:
        """Return the direct children of ``code`` in sheet order."""
        if self._children is None:
            index: dict[str, list[RgsCode]] = {}
            for c in self.codes.values():
                p = self.parent(c.code)
                if p is not None:
                    index.setdefault(p.code, []).append(c)
            self._children = {k: tuple(v) for k, v in index.items()}
        return self._children.get(code.strip(), ())

    # ------------------------------------------------------------------ checks
    def check_ref(
        self, ref: RgsRef, findings: FindingCollector, *, line: int | None, account_id: str
    ) -> None:
        """Check one account's RGS reference against this release.

        Placeholders and unrecognisable values are skipped (the validator reports them as
        ``XAF8001``/``XAF8002``). Reports ``XAF8003`` for a code that does not exist in this
        version, ``XAF8004`` for a code that was renamed (naming the new code) and ``XAF8006``
        for a code at a level other than 4 or 5.

        Args:
            ref: The parsed reference.
            findings: Where to report.
            line: Line of the ledger account in the file.
            account_id: The ledger account's ID (for the message).
        """
        code = ref.code
        if code is None or ref.placeholder:
            return
        found = self.codes.get(code)
        version = f"RGS {self.version}" if self.version else "the loaded RGS version"
        if found is None:
            new = self.rename_map.get(code)
            if new is not None:
                findings.add(
                    "XAF8004",
                    f"account {account_id!r}: RGS code {code!r} was renamed; it is {new!r} "
                    f"in {version}",
                    line=line,
                    value=ref.raw,
                )
                return
            nearest = self.parent(code)
            hint = f" (nearest existing ancestor: {nearest.code!r})" if nearest else ""
            findings.add(
                "XAF8003",
                f"account {account_id!r}: RGS code {code!r} does not exist in {version}{hint}",
                line=line,
                value=ref.raw,
            )
            return
        if found.level is not None and found.level not in ACCOUNT_LEVELS:
            findings.add(
                "XAF8006",
                f"account {account_id!r} is mapped to RGS code {code!r} at level {found.level}",
                line=line,
                value=ref.raw,
            )

    def check_version(self, version_text: str, findings: FindingCollector) -> None:
        """Check the auditfile's declared RGS version (XAF 4.0 ``header/RGSVersion``).

        The element has no specified format (``"3.7"``, ``"RGS 3.8"``, ``"RGS-1"`` occur), so it
        is parsed leniently with :func:`parse_version`. Reports ``XAF8005`` when no version can be
        recognised or when it differs from this schema's version.

        Args:
            version_text: The declared version as written.
            findings: Where to report.
        """
        parsed = parse_version(version_text)
        if parsed is None:
            findings.add("XAF8005", "RGS version could not be recognised", value=version_text)
        elif self.version is not None and parsed != self.version:
            findings.add(
                "XAF8005",
                f"declared RGS version {parsed} differs from the loaded RGS {self.version}",
                value=version_text,
            )


def validate_refs(af: AuditFile, schema: RgsSchema) -> list[Finding]:
    """Check every ledger account's RGS reference and the declared RGS version.

    Reports placeholders (``XAF8001``), unrecognisable values (``XAF8002``) and everything
    :meth:`RgsSchema.check_ref` and :meth:`RgsSchema.check_version` report.

    Args:
        af: An open auditfile.
        schema: The RGS release to check against.

    Returns:
        The findings, in account order.
    """
    f = FindingCollector(max_per_code=None, max_total=None)
    for acc in af.accounts.values():
        ref = acc.rgs
        if ref is None:
            continue
        line = acc.raw.line if acc.raw is not None else None
        if ref.placeholder:
            f.add(
                "XAF8001", f"account {acc.id!r} has placeholder RGS code", line=line, value=ref.raw
            )
        elif ref.code is None:
            f.add(
                "XAF8002",
                f"account {acc.id!r}: RGS code not recognisable",
                line=line,
                value=ref.raw,
            )
        else:
            schema.check_ref(ref, f, line=line, account_id=acc.id)
    if af.header.rgs_version:
        schema.check_version(af.header.rgs_version, f)
    return list(f)


# ------------------------------------------------------------------------------- Excel loading
@dataclass(slots=True)
class _Layout:
    """Where the header is in a sheet and what it contains."""

    sheet: str
    header_index: int  # 0-based row index
    header: list[str | None]
    above: list[str | None]  # the row above the header (group titles / version labels)
    code_columns: list[int]


def _find_layout(wb: XlsxWorkbook, sheet: str) -> _Layout | None:
    prev: list[str | None] = []
    for i, row in enumerate(wb.iter_rows(sheet)):
        if i >= _HEADER_SCAN_ROWS:
            break
        cols = [j for j, v in enumerate(row) if _FIELDS.get(_norm(v)) == "code"]
        if cols:
            return _Layout(sheet, i, row, prev, cols)
        prev = row
    return None


def _row_count(wb: XlsxWorkbook, sheet: str) -> int:
    dim = wb.dimension(sheet)
    if dim:
        last = dim.rpartition(":")[2].lstrip("$ABCDEFGHIJKLMNOPQRSTUVWXYZ").replace("$", "")
        if last.isascii() and last.isdigit() and len(last) <= 7:  # Excel: 1,048,576 rows
            return int(last)
    return sum(1 for _ in wb.iter_rows(sheet))


def _code_rows(wb: XlsxWorkbook, layout: _Layout) -> Iterator[list[str | None]]:
    for i, row in enumerate(wb.iter_rows(layout.sheet)):
        if i > layout.header_index:
            yield row


def _cell(row: Sequence[str | None], col: int | None) -> str | None:
    if col is None or col >= len(row):
        return None
    return _clean(row[col])


def _read_codes(wb: XlsxWorkbook, layout: _Layout) -> tuple[list[RgsCode], list[str]]:
    columns: dict[str, int] = {}
    filters: list[tuple[int, str, bool]] = []  # (column, name, is_elimination_group)
    elimination_from: int | None = None
    for j, title in enumerate(layout.above):
        t = _norm(title)
        if "vervallen" in t or "elimin" in t:
            elimination_from = j
            break
    for j, title in enumerate(layout.header):
        key = _norm(title)
        if not key:
            continue
        field = _FIELDS.get(key)
        if field is not None:
            columns.setdefault(field, j)
        else:
            name = (title or "").strip()
            filters.append((j, name, elimination_from is not None and j >= elimination_from))
    code_col = layout.code_columns[0]
    out: list[RgsCode] = []
    seen: set[str] = set()
    dups: list[str] = []
    for row in _code_rows(wb, layout):
        code = _cell(row, code_col)
        if code is None:
            continue
        if code in seen:
            dups.append(code)
            continue
        seen.add(code)
        level_text = _cell(row, columns.get("level"))
        entities: set[str] = set()
        eliminate: set[str] = set()
        for j, name, elim in filters:
            v = _cell(row, j)
            if v is not None and v.casefold() not in _FALSY:
                (eliminate if elim else entities).add(name)
        out.append(
            RgsCode(
                code=code,
                level=int(level_text)
                if level_text
                and level_text.isascii()
                and level_text.isdigit()
                and len(level_text) < 4
                else None,
                description=_cell(row, columns.get("description")),
                short_description=_cell(row, columns.get("short_description")),
                debit_credit=_cell(row, columns.get("debit_credit")),
                sort_key=_cell(row, columns.get("sort_key")),
                reference_number=_cell(row, columns.get("reference_number")),
                opposite_code=_cell(row, columns.get("opposite_code")),
                entities=frozenset(entities),
                elimination_filters=frozenset(eliminate),
            )
        )
    return out, dups


def _read_renames(wb: XlsxWorkbook, layout: _Layout, version: str | None) -> dict[str, str]:
    cols = layout.code_columns
    target = cols[-1]
    if version is not None:
        for j in cols:
            label = layout.above[j] if j < len(layout.above) else None
            if parse_version(label) == version:
                target = j
                break
    renames: dict[str, str] = {}
    for row in _code_rows(wb, layout):
        new = _cell(row, target)
        if new is None:
            continue
        for j in cols:
            if j == target:
                continue
            old = _cell(row, j)
            if old is not None and old != new:
                renames.setdefault(old, new)
    return renames


def _detect_version(
    wb: XlsxWorkbook, layout: _Layout, source: str | os.PathLike[str] | IO[bytes]
) -> str | None:
    candidates: list[str | None] = list(layout.above[:1])
    candidates.append(layout.sheet)
    if isinstance(source, (str, os.PathLike)):
        candidates.append(Path(source).name)
    for text in candidates:
        if text and _VERSION_IN_NAME.search(text):
            return parse_version(text)
    for name in wb.sheet_names:
        if _norm(name).startswith("recap"):
            for i, row in enumerate(wb.iter_rows(name)):
                if i >= _HEADER_SCAN_ROWS:
                    break
                for v in row:
                    if v and _VERSION_IN_NAME.search(v):
                        return parse_version(v)
    return None


def load_excel(
    path: str | os.PathLike[str] | IO[bytes],
    sheet: str | None = None,
    *,
    rename_sheet: str | None = None,
    max_member_size: int = DEFAULT_MAX_MEMBER_SIZE,
) -> RgsSchema:
    """Load an official RGS Excel release.

    The main sheet is detected automatically when ``sheet`` is not given: among the sheets with
    a ``Referentiecode`` column header in their first rows (and only one such column), names
    starting with ``Totaal`` are preferred, then the sheet with the most rows. A sheet with
    several ``Referentiecode`` columns (one per release, e.g. ``RGS3.8-versus-RGS3.7``) provides
    :attr:`RgsSchema.rename_map`. Header matching ignores case, whitespace and punctuation.

    The version is taken from the title cell above the header (``RGS3.8``), the sheet name, the
    file name or the ``Recap`` sheet, in that order.

    Args:
        path: Path or binary seekable stream of the ``.xlsx`` file.
        sheet: Name of the sheet with the codes (auto-detected when ``None``).
        rename_sheet: Name of the version-comparison sheet (auto-detected when ``None``).
        max_member_size: Maximum uncompressed size of a single workbook part, in bytes.

    Returns:
        The loaded :class:`RgsSchema`.

    Raises:
        XlsxError: If the file is not an ``.xlsx`` workbook or no sheet with RGS codes is found.
        KeyError: If ``sheet`` or ``rename_sheet`` does not exist.
        ForbiddenConstructError: If a workbook part contains a DOCTYPE/ENTITY declaration.
        LimitExceededError: If a workbook part exceeds ``max_member_size``.
    """
    with XlsxWorkbook(path, max_member_size=max_member_size) as wb:
        layouts: dict[str, _Layout] = {}
        for name in wb.sheet_names:
            if name in (sheet, rename_sheet) or sheet is None or rename_sheet is None:
                found = _find_layout(wb, name)
                if found is not None:
                    layouts[name] = found
        if sheet is not None:
            main = layouts.get(sheet)
            if main is None:
                if sheet not in wb.sheet_names:
                    raise KeyError(f"no sheet named {sheet!r}; sheets: {wb.sheet_names}")
                raise XlsxError(f"sheet {sheet!r} has no 'Referentiecode' column header")
        else:
            singles = [lay for lay in layouts.values() if len(lay.code_columns) == 1]
            if not singles:
                raise XlsxError("no sheet with a 'Referentiecode' column header found")
            main = max(
                singles,
                key=lambda lay: (_norm(lay.sheet).startswith("totaal"), _row_count(wb, lay.sheet)),
            )
        version = _detect_version(wb, main, path)
        codes, dups = _read_codes(wb, main)

        compare: _Layout | None
        if rename_sheet is not None:
            compare = layouts.get(rename_sheet)
            if compare is None:
                if rename_sheet not in wb.sheet_names:
                    raise KeyError(f"no sheet named {rename_sheet!r}; sheets: {wb.sheet_names}")
                raise XlsxError(f"sheet {rename_sheet!r} has no 'Referentiecode' column header")
        else:
            multi = [lay for lay in layouts.values() if len(lay.code_columns) > 1]
            compare = max(multi, key=lambda lay: len(lay.code_columns)) if multi else None
        renames = _read_renames(wb, compare, version) if compare is not None else {}
    return RgsSchema(codes, version=version, rename_map=renames, duplicate_codes=dups)
