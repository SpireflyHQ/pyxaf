"""Normalized tables for analysis and export.

Every table has a fixed schema (column names and logical types) that is the same for all
iterations. Tables are produced lazily: master-data tables from memory, ``transactions``,
``lines`` and ``line_vat`` by streaming the file. All rows carry synthetic ``seq`` keys because
real files contain duplicate numbers.

=================  ===========================================================================
Table              Rows
=================  ===========================================================================
``header``         one row: file header and detected version
``company``        one row
``addresses``      company and customer/supplier addresses
``accounts``       ledger accounts (with RGS reference)
``relations``      customers/suppliers
``vat_codes``      VAT codes
``periods``        periods
``journals``       journals
``transactions``   journal entries (with line counts and debit/credit totals)
``lines``          transaction lines with their transaction context
``line_vat``       VAT details of transaction lines (``line_seq`` → ``lines.seq``)
``opening_balance`` opening-balance lines (from the element or period-0/opening transactions)
=================  ===========================================================================

Logical types: ``string``, ``int64``, ``date``, ``bool`` and ``decimal(p,s)``. Amounts are
``decimal(20,2)`` as in the XSDs, VAT percentages ``decimal(8,3)``.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import operator
import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .models import Line, Transaction
    from .reader import AuditFile

__all__ = ["TABLES", "Column", "Table", "Tables"]

Row = tuple[Any, ...]
ExportFormat = Literal["csv", "jsonl", "parquet"]
OnInexact = Literal["raise", "null", "round"]
"""What Arrow-based exports do with values that do not fit the column type exactly (an amount
with more than 2 decimals, an integer beyond int64): raise ``ValueError`` (default), write null,
or round half-even (decimals only)."""
AMOUNT = "decimal(20,2)"


@dataclass(frozen=True, slots=True)
class Column:
    """A table column: name and logical type."""

    name: str
    type: str

    @property
    def decimal(self) -> tuple[int, int] | None:
        """``(precision, scale)`` for decimal columns."""
        if not self.type.startswith("decimal("):
            return None
        p, s = self.type[8:-1].split(",")
        return int(p), int(s)


def _cols(spec: str) -> tuple[Column, ...]:
    out = []
    for item in spec.split():
        name, _, typ = item.partition(":")
        out.append(Column(name, typ or "string"))
    return tuple(out)


#: Schema of every table.
TABLES: dict[str, tuple[Column, ...]] = {
    "header": _cols(
        "format_version family fiscal_year start_date:date end_date:date currency created:date "
        "software_name software_version rgs_version declared_version"
    ),
    "company": _cols(
        "name identifier commerce_number tax_registration_country tax_registration_id"
    ),
    "addresses": _cols(
        "owner_type owner_id seq:int64 kind street number number_extension property city "
        "postal_code region country"
    ),
    "accounts": _cols(
        "seq:int64 file:int64 id description account_type account_kind lead_code "
        "lead_description rgs_raw rgs_code rgs_extension rgs_source rgs_placeholder:bool"
    ),
    "relations": _cols(
        "seq:int64 file:int64 id name relation_type relation_kind contact "
        "tax_registration_country tax_registration_id commerce_number email telephone website "
        f"opening_balance:{AMOUNT} closing_balance:{AMOUNT}"
    ),
    "vat_codes": _cols("seq:int64 id description payable_account_id receivable_account_id"),
    "periods": _cols("seq:int64 key number:int64 start_date:date end_date:date description"),
    "journals": _cols(
        "seq:int64 file:int64 id description journal_type journal_kind offset_account_id "
        "bank_account"
    ),
    "transactions": _cols(
        "seq:int64 file:int64 journal_id number description period_key period_number:int64 "
        f"date:date source user line_count:int64 total_debit:{AMOUNT} total_credit:{AMOUNT}"
    ),
    "lines": _cols(
        "seq:int64 transaction_seq:int64 file:int64 journal_id transaction_number period_key "
        "transaction_date:date number account_id "
        f"amount:{AMOUNT} side signed_amount:{AMOUNT} debit:{AMOUNT} credit:{AMOUNT} "
        "description document_ref effective_date:date settlement_date:date relation_id "
        "invoice_ref order_ref receiving_doc_ref shipping_doc_ref cost_center cost_unit product "
        "project work_cost_arrangement bank_account offset_bank_account quantity:decimal(24,6) "
        f"foreign_currency foreign_amount:{AMOUNT} foreign_signed_amount:{AMOUNT} "
        "exchange_rate:decimal(24,6) vat_count:int64"
    ),
    "line_vat": _cols(
        "line_seq:int64 transaction_seq:int64 index:int64 code percentage:decimal(8,3) "
        f"amount:{AMOUNT} side signed_amount:{AMOUNT}"
    ),
    "opening_balance": _cols(
        "seq:int64 source number account_id "
        f"amount:{AMOUNT} side signed_amount:{AMOUNT} debit:{AMOUNT} credit:{AMOUNT} "
        "transaction_seq:int64 journal_id"
    ),
}
STREAMED = frozenset({"transactions", "lines", "line_vat"})


def _line_row(ln: Line) -> Row:
    fa = ln.foreign
    return (
        ln.seq, ln.transaction_seq, ln.file, ln.journal_id, ln.transaction_number, ln.period_key,
        ln.transaction_date, ln.number, ln.account_id, ln.amount,
        ln.side.value if ln.side is not None else None, ln.signed_amount, ln.debit, ln.credit,
        ln.description, ln.document_ref, ln.effective_date, ln.settlement_date, ln.relation_id,
        ln.invoice_ref, ln.order_ref, ln.receiving_doc_ref, ln.shipping_doc_ref, ln.cost_center,
        ln.cost_unit, ln.product, ln.project, ln.work_cost_arrangement, ln.bank_account,
        ln.offset_bank_account, ln.quantity,
        fa.currency if fa else None, fa.amount if fa else None, fa.signed_amount if fa else None,
        fa.exchange_rate if fa else None, len(ln.vat),
    )  # fmt: skip


def _tx_row(tx: Transaction) -> Row:
    return (
        tx.seq, tx.file, tx.journal_id, tx.number, tx.description, tx.period_key,
        tx.period_number, tx.date, tx.source, tx.user, len(tx.lines), tx.total_debit,
        tx.total_credit,
    )  # fmt: skip


def _vat_rows(ln: Line) -> Iterator[Row]:
    for i, v in enumerate(ln.vat):
        yield (
            ln.seq, ln.transaction_seq, i, v.code, v.percentage, v.amount,
            v.side.value if v.side is not None else None, v.signed_amount,
        )  # fmt: skip


def check_batch_size(batch_size: int) -> int:
    """Return ``batch_size`` if it is a positive integer, else raise ``ValueError``."""
    try:
        n = operator.index(batch_size)
    except TypeError:
        n = 0
    if n < 1:
        raise ValueError(f"batch_size must be a positive integer, not {batch_size!r}")
    return n


class Table:
    """One normalized table: schema plus lazily produced rows.

    With ``pyxaf[arrow]`` installed a table implements the Arrow PyCapsule stream interface
    (``__arrow_c_stream__``), so ``polars.DataFrame(table)``, ``pyarrow.table(table)`` and
    DuckDB can consume it directly.
    """

    def __init__(self, tables: Tables, name: str) -> None:
        if name not in TABLES:
            raise KeyError(f"unknown table {name!r}; choose from {', '.join(TABLES)}")
        self._tables = tables
        self.name = name
        self.columns: tuple[Column, ...] = TABLES[name]

    @property
    def column_names(self) -> tuple[str, ...]:
        """Names of the columns."""
        return tuple(c.name for c in self.columns)

    def rows(self) -> Iterator[Row]:
        """Yield rows as tuples of Python values (``Decimal``, ``date``, ``str``, ``int``)."""
        return self._tables._rows(self.name)

    def dicts(self) -> Iterator[dict[str, Any]]:
        """Yield rows as dictionaries."""
        names = self.column_names
        for row in self.rows():
            yield dict(zip(names, row, strict=True))

    def batches(self, batch_size: int = 65_536, on_inexact: OnInexact = "raise") -> Iterator[Any]:
        """Yield Arrow record batches lazily (needs ``pyxaf[arrow]``).

        Each batch implements ``__arrow_c_array__`` (a nanoarrow struct array).
        """
        from ._arrow import batches  # noqa: PLC0415

        check_batch_size(batch_size)
        return batches(self.columns, self.rows(), batch_size, on_inexact)

    def __arrow_c_schema__(self) -> object:
        from ._arrow import schema  # noqa: PLC0415

        return schema(self.columns).__arrow_c_schema__()

    def __arrow_c_stream__(self, requested_schema: object | None = None) -> object:
        """Arrow PyCapsule stream (materialises this table's batches in Arrow memory)."""
        from ._arrow import stream  # noqa: PLC0415

        return stream(self.columns, self.rows()).__arrow_c_stream__(requested_schema)

    def to_polars(self, on_inexact: OnInexact = "raise") -> Any:
        """Return a polars DataFrame (needs ``pyxaf[polars]``)."""
        from ._arrow import to_polars  # noqa: PLC0415

        return to_polars(self.columns, self.rows(), on_inexact)

    def to_pandas(self, on_inexact: OnInexact = "raise") -> Any:
        """Return a pandas DataFrame with Arrow-backed dtypes (needs ``pyxaf[pandas]``)."""
        from ._arrow import to_pandas  # noqa: PLC0415

        return to_pandas(self.columns, self.rows(), on_inexact)

    def __repr__(self) -> str:
        return f"<Table {self.name!r} columns={len(self.columns)}>"


class Tables:
    """All normalized tables of an :class:`~pyxaf.AuditFile` (see module documentation)."""

    def __init__(self, af: AuditFile) -> None:
        self._af = af

    @property
    def names(self) -> tuple[str, ...]:
        """Table names."""
        return tuple(TABLES)

    def __getitem__(self, name: str) -> Table:
        return Table(self, name)

    def __iter__(self) -> Iterator[str]:
        return iter(TABLES)

    def __len__(self) -> int:
        return len(TABLES)

    # ------------------------------------------------------------------ rows
    def _rows(self, name: str) -> Iterator[Row]:
        af = self._af
        if name == "header":
            h = af.header
            yield (
                af.version.value, af.format.family.value if af.format.family else None,
                h.fiscal_year, h.start_date, h.end_date, h.currency, h.created, h.software_name,
                h.software_version, h.rgs_version, h.declared_version,
            )  # fmt: skip
        elif name == "company":
            c = af.company
            yield (
                c.name, c.identifier, c.commerce_number, c.tax_registration_country,
                c.tax_registration_id,
            )  # fmt: skip
        elif name == "addresses":
            owners = [("company", None, af.company.addresses)] + [
                ("relation", r.id, r.addresses) for r in af.relations.values()
            ]
            seq = 0
            for owner_type, owner_id, addresses in owners:
                for a in addresses:
                    yield (
                        owner_type, owner_id, seq, a.kind, a.street, a.number,
                        a.number_extension, a.property, a.city, a.postal_code, a.region,
                        a.country,
                    )  # fmt: skip
                    seq += 1
        elif name == "accounts":
            for acc in af.accounts.values():
                r = acc.rgs
                yield (
                    acc.seq, acc.file, acc.id, acc.description, acc.account_type.code,
                    acc.account_type.kind.value, acc.lead_code, acc.lead_description,
                    r.raw if r else None, r.code if r else None, r.extension if r else None,
                    r.source if r else None, r.placeholder if r else None,
                )  # fmt: skip
        elif name == "relations":
            for x in af.relations.values():
                yield (
                    x.seq, x.file, x.id, x.name, x.relation_type.code,
                    x.relation_type.kind.value, x.contact, x.tax_registration_country,
                    x.tax_registration_id, x.commerce_number, x.email, x.telephone, x.website,
                    x.opening_balance, x.closing_balance,
                )  # fmt: skip
        elif name == "vat_codes":
            for v in af.vat_codes.values():
                yield (v.seq, v.id, v.description, v.payable_account_id, v.receivable_account_id)
        elif name == "periods":
            for p in af.periods.values():
                yield (p.seq, p.key, p.number, p.start_date, p.end_date, p.description)
        elif name == "journals":
            for j in af.journals.values():
                yield (
                    j.seq, j.file, j.id, j.description, j.journal_type.code,
                    j.journal_type.kind.value, j.offset_account_id, j.bank_account,
                )  # fmt: skip
        elif name == "transactions":
            for tx in af.transactions():
                yield _tx_row(tx)
        elif name == "lines":
            for tx in af.transactions():
                for ln in tx.lines:
                    yield _line_row(ln)
        elif name == "line_vat":
            for tx in af.transactions():
                for ln in tx.lines:
                    yield from _vat_rows(ln)
        elif name == "opening_balance":
            ob = af.opening_balance()
            for i, ln in enumerate(ob.lines):
                yield (
                    i, ob.source, ln.number, ln.account_id, ln.amount,
                    ln.side.value if ln.side is not None else None, ln.signed_amount, ln.debit,
                    ln.credit, ln.transaction_seq, ln.journal_id,
                )  # fmt: skip
        else:  # pragma: no cover - guarded by Table
            raise KeyError(name)

    def _streamed_rows(self, names: Sequence[str]) -> Iterator[tuple[str, Row]]:
        """One pass over the transactions producing rows for several streamed tables."""
        want_tx = "transactions" in names
        want_lines = "lines" in names
        want_vat = "line_vat" in names
        for tx in self._af.transactions():
            if want_tx:
                yield "transactions", _tx_row(tx)
            if want_lines or want_vat:
                for ln in tx.lines:
                    if want_lines:
                        yield "lines", _line_row(ln)
                    if want_vat and ln.vat:
                        for r in _vat_rows(ln):
                            yield "line_vat", r

    def _chunks(self, names: Sequence[str], batch_size: int) -> Iterator[tuple[str, list[Row]]]:
        """Row chunks per table.

        Every table yields at least one (possibly empty) chunk; the streamed tables are produced
        together in a single pass over the file.
        """
        check_batch_size(batch_size)
        for n in names:
            if n not in STREAMED:
                it = iter(self._rows(n))
                first = True
                while True:
                    chunk = list(islice(it, batch_size))
                    if chunk or first:
                        yield n, chunk
                    first = False
                    if len(chunk) < batch_size:
                        break
        streamed = [n for n in names if n in STREAMED]
        if not streamed:
            return
        buffers: dict[str, list[Row]] = {n: [] for n in streamed}
        emitted: set[str] = set()
        for n, row in self._streamed_rows(streamed):
            buf = buffers[n]
            buf.append(row)
            if len(buf) >= batch_size:
                yield n, buf
                emitted.add(n)
                buffers[n] = []
        for n in streamed:
            if buffers[n] or n not in emitted:
                yield n, buffers[n]

    # ---------------------------------------------------------------- export
    def export(
        self,
        directory: str | os.PathLike[str],
        *,
        format: ExportFormat = "csv",
        tables: Iterable[str] | None = None,
        batch_size: int = 65_536,
        on_inexact: OnInexact = "raise",
    ) -> list[str]:
        """Write tables to ``directory`` (one file per table); return the written paths.

        ``csv`` and ``jsonl`` need no dependencies (decimals are written as exact strings, dates
        as ISO 8601); ``parquet`` needs ``pyxaf[parquet]``. Streamed tables are produced in a
        single pass over the file.
        """
        names = list(TABLES) if tables is None else list(tables)
        for n in names:
            if n not in TABLES:
                raise KeyError(f"unknown table {n!r}")
        check_batch_size(batch_size)
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        if format == "parquet":
            from ._arrow import write_parquet  # noqa: PLC0415

            return write_parquet(self, names, out, batch_size, on_inexact)
        if format not in ("csv", "jsonl"):
            raise ValueError("format must be 'csv', 'jsonl' or 'parquet'")
        paths: dict[str, Path] = {n: out / f"{n}.{format}" for n in names}
        handles = {n: paths[n].open("w", encoding="utf-8", newline="") for n in names}
        try:
            writers: dict[str, Any] = {}
            for n in names:
                cols = [c.name for c in TABLES[n]]
                if format == "csv":
                    w = csv.writer(handles[n])
                    w.writerow(cols)
                    writers[n] = w
                else:
                    writers[n] = cols

            def emit(n: str, row: Row) -> None:
                if format == "csv":
                    writers[n].writerow([_csv_value(v) for v in row])
                else:
                    record = {k: _json_value(v) for k, v in zip(writers[n], row, strict=True)}
                    handles[n].write(json.dumps(record, ensure_ascii=False) + "\n")

            for n in names:
                if n not in STREAMED:
                    for row in self._rows(n):
                        emit(n, row)
            streamed = [n for n in names if n in STREAMED]
            if streamed:
                for n, row in self._streamed_rows(streamed):
                    emit(n, row)
        finally:
            for h in handles.values():
                h.close()
        return [str(paths[n]) for n in names]

    def to_polars(
        self,
        tables: Iterable[str] | None = None,
        *,
        batch_size: int = 65_536,
        on_inexact: OnInexact = "raise",
    ) -> dict[str, Any]:
        """Return polars DataFrames by table name (needs ``pyxaf[polars]``; no pyarrow).

        Streamed tables are built in a single pass over the file.
        """
        from ._arrow import polars_frames  # noqa: PLC0415

        check_batch_size(batch_size)
        return polars_frames(self, list(tables or TABLES), batch_size, on_inexact)

    def to_pandas(
        self, tables: Iterable[str] | None = None, *, on_inexact: OnInexact = "raise"
    ) -> dict[str, Any]:
        """Return pandas DataFrames with Arrow-backed dtypes (needs ``pyxaf[pandas]``)."""
        from ._arrow import to_pandas  # noqa: PLC0415

        return {n: to_pandas(TABLES[n], self._rows(n), on_inexact) for n in (tables or TABLES)}


def _csv_value(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (Decimal, dt.date)):
        return str(v)
    return v


def _json_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    return v
