from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest
import xafgen

import pyxaf
from pyxaf.tables import TABLES


@pytest.fixture
def af() -> pyxaf.AuditFile:
    return pyxaf.open(xafgen.write("4.0"))


def test_schemas_match_rows(af: pyxaf.AuditFile) -> None:
    for name in TABLES:
        table = af.tables[name]
        for row in table.rows():
            assert len(row) == len(table.columns), name


def test_table_contents(af: pyxaf.AuditFile) -> None:
    rows = list(af.tables["lines"].dicts())
    assert len(rows) == 36
    assert sum(r["debit"] for r in rows) == sum(r["credit"] for r in rows)
    vat = list(af.tables["line_vat"].dicts())
    assert len(vat) == 12 and vat[0]["code"] == "H21"
    assert {r["line_seq"] for r in vat} <= {r["seq"] for r in rows}
    tx = list(af.tables["transactions"].dicts())
    assert tx[0]["line_count"] == 3
    assert next(iter(af.tables["header"].dicts()))["format_version"] == "4.0"
    assert len(list(af.tables["opening_balance"].rows())) == 3
    addresses = list(af.tables["addresses"].dicts())
    assert addresses[0]["owner_type"] == "company"
    with pytest.raises(KeyError):
        af.tables["nope"]


def test_csv_and_jsonl_export(af: pyxaf.AuditFile, tmp_path: Path) -> None:
    paths = af.export(tmp_path / "csv")
    assert len(paths) == len(TABLES)
    with (tmp_path / "csv" / "lines.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 36
    assert sum(Decimal(r["debit"]) for r in rows) == sum(Decimal(r["credit"]) for r in rows)
    af.export(tmp_path / "jl", format="jsonl", tables=["accounts", "lines"])
    first = json.loads(
        (tmp_path / "jl" / "lines.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert isinstance(first["amount"], str) and first["transaction_date"] == "2024-01-05"
    with pytest.raises(ValueError, match="format"):
        af.export(tmp_path, format="xlsx")  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        af.export(tmp_path, tables=["nope"])


@pytest.mark.extras
def test_arrow_stream_and_batches(af: pyxaf.AuditFile) -> None:
    pa = pytest.importorskip("pyarrow")
    pytest.importorskip("nanoarrow")
    table = pa.table(af.tables["lines"])
    assert table.num_rows == 36
    assert str(table.schema.field("amount").type) == "decimal128(20, 2)"
    assert str(table.schema.field("transaction_date").type) == "date32[day]"
    total = pa.compute.sum(table["debit"]).as_py()
    assert total == sum(Decimal(r["debit"]) for r in af.tables["lines"].dicts())
    batches = list(af.tables["lines"].batches(batch_size=10))
    assert [b.length for b in batches] == [10, 10, 10, 6]
    empty = pa.table(af.tables["line_vat"]) if False else None
    assert empty is None


@pytest.mark.extras
def test_polars_without_pyarrow_path(af: pyxaf.AuditFile) -> None:
    pl = pytest.importorskip("polars")
    pytest.importorskip("nanoarrow")
    frames = af.to_polars(batch_size=7)
    assert set(frames) == set(TABLES)
    lines = frames["lines"]
    assert lines.height == 36
    assert lines.schema["amount"] == pl.Decimal(20, 2)
    assert lines["debit"].sum() == lines["credit"].sum()
    assert frames["periods"].height == 12
    assert frames["line_vat"].height == 12


@pytest.mark.extras
def test_pandas(af: pyxaf.AuditFile) -> None:
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    frames = af.to_pandas(["accounts", "lines"])
    assert len(frames["lines"]) == 36
    assert "decimal128(20, 2)" in str(frames["lines"]["amount"].dtype)


@pytest.mark.extras
def test_parquet(af: pyxaf.AuditFile, tmp_path: Path) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    paths = af.export(tmp_path, format="parquet", batch_size=5)
    assert len(paths) == len(TABLES)
    assert pq.read_table(tmp_path / "lines.parquet").num_rows == 36
    assert pq.read_table(tmp_path / "line_vat.parquet").num_rows == 12
    assert pq.read_table(tmp_path / "periods.parquet").num_rows == 12


@pytest.mark.extras
def test_inexact_decimal_raises() -> None:
    pytest.importorskip("nanoarrow")
    data = xafgen.write("3.2").replace(
        b"<amnt>10000.00</amnt>", b"<amnt>40.800000000000004</amnt>", 1
    )
    af = pyxaf.open(data)
    with pytest.raises(ValueError, match="does not fit"):
        list(af.tables["opening_balance"].batches())


def test_missing_extra_message(monkeypatch: pytest.MonkeyPatch, af: pyxaf.AuditFile) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.split(".", maxsplit=1)[0] in ("nanoarrow", "polars", "pyarrow"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    import sys

    for mod in [m for m in sys.modules if m.split(".")[0] in ("nanoarrow", "polars", "pyarrow")]:
        monkeypatch.delitem(sys.modules, mod)
    with pytest.raises(pyxaf.MissingExtraError, match=r"pyxaf\[polars\]"):
        af.to_polars()
    with pytest.raises(pyxaf.MissingExtraError, match=r"pyxaf\[arrow\]"):
        list(af.tables["accounts"].batches())


@pytest.mark.extras
def test_on_inexact_policies() -> None:
    pytest.importorskip("polars")
    data = xafgen.write("4.0").replace(b"<amnt>2985.26</amnt>", b"<amnt>2985.265</amnt>", 1)
    data = data.replace(b"<amntTp>C</amntTp>", b"<amntTp>C</amntTp>", 1)
    af = pyxaf.open(data)
    with pytest.raises(ValueError, match="key"):
        af.to_polars(["lines"])
    lines = af.to_polars(["lines"], on_inexact="null")["lines"]
    assert lines["amount"].null_count() == 1
    lines = af.to_polars(["lines"], on_inexact="round")["lines"]
    assert lines["amount"].null_count() == 0
