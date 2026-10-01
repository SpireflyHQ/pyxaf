# Interoperability

pyxaf turns every auditfile into the same set of **normalized tables** with fixed schemas, whatever
the iteration. They are produced lazily and can be written to CSV or JSONL with the standard
library, or handed to Arrow-based tools: pyarrow, polars, DuckDB, pandas, Parquet.

```python
with pyxaf.open("2024.xaf") as af:
    af.export("out/")  # one CSV file per table, no dependencies
```

## The tables

| Table | Rows | Source |
|---|---|---|
| `header` | one row: file header and detected version | memory |
| `company` | one row | memory |
| `addresses` | company and customer/supplier addresses | memory |
| `accounts` | ledger accounts, with RGS reference | memory |
| `relations` | customers/suppliers | memory |
| `vat_codes` | VAT codes | memory |
| `periods` | periods | memory |
| `journals` | journals | may scan the file once |
| `transactions` | journal entries, with line count and debit/credit totals | streamed |
| `lines` | transaction lines with their transaction context | streamed |
| `line_vat` | VAT details of lines (`line_seq` → `lines.seq`) | streamed |
| `opening_balance` | opening-balance lines, from the element or from period-0/opening transactions | memory or one scan |

Column names and types are listed in the [table schemas](../reference/tables.md). Every row has a
synthetic `seq` key, because real files contain duplicate transaction and line numbers; join
`lines.transaction_seq` to `transactions.seq` and `line_vat.line_seq` to `lines.seq`. Columns a
version does not have are null.

```python
with pyxaf.open("2024.xaf") as af:
    tables = af.tables  # a Tables object; tables.names lists the names
    lines = tables["lines"]  # a Table
    lines.column_names  # ('seq', 'transaction_seq', 'file', 'journal_id', ...)
    lines.columns[9]  # Column(name='amount', type='decimal(20,2)')
    for row in lines.rows():  # tuples of Python values (Decimal, date, str, int)
        ...
    for row in lines.dicts():  # dicts
        ...
```

Each call of `rows()` or `dicts()` on a streamed table is a new pass over the file, in constant
memory.

## CSV and JSONL (no dependencies)

```python
with pyxaf.open("2024.xaf") as af:
    paths = af.export("out/", format="csv")  # all tables
    paths = af.export("out/", format="jsonl", tables=["accounts", "lines"])
```

`export()` writes one file per table (`out/lines.csv`, …) and returns the paths. Decimals are
written as exact strings (`3612.16`, never `3612.1600000001`), dates as ISO 8601, missing values as
empty CSV fields or JSON `null`, booleans as `true`/`false`. The streamed tables are written
together in **one** pass over the file. Files are UTF-8; the CSV dialect is Python's default
(comma-separated, minimal quoting).

## Arrow

With `pyxaf[arrow]` (nanoarrow, 2.9 MB) every `Table` implements the
[Arrow PyCapsule interface](https://arrow.apache.org/docs/format/CDataInterface/PyCapsuleInterface.html)
(`__arrow_c_stream__`, `__arrow_c_schema__`), so Arrow-aware libraries consume it directly,
without pyarrow:

```python
import duckdb
import polars as pl
import pyarrow as pa

with pyxaf.open("2024.xaf") as af:
    lines = af.tables["lines"]

    tbl = pa.table(lines)  # pyarrow.Table
    df = pl.DataFrame(lines)  # polars, no pyarrow needed
    balances = duckdb.sql(  # DuckDB finds the Python variable
        "select account_id, sum(signed_amount) as balance from lines group by account_id"
    ).fetchall()  # [('1300', Decimal('38260.42')), ...]
```

DuckDB's replacement scan resolves the table name in the SQL to a Python variable of that name
(`lines` above), so any name works as long as a variable holds the `Table`.

### Memory behaviour

| Method | Behaviour |
|---|---|
| `rows()`, `dicts()` | lazy; constant memory |
| `batches(batch_size=65_536)` | lazy: yields Arrow record batches (nanoarrow arrays implementing `__arrow_c_array__`) one at a time |
| `__arrow_c_stream__` (`pa.table(t)`, `pl.DataFrame(t)`, DuckDB) | **materialises the whole table** in Arrow memory, batch by batch, before handing it over |
| `export(format="parquet")` | batch-wise; constant memory |
| `export(format="csv" / "jsonl")` | streaming; constant memory |

Arrow memory is far more compact than Python objects, but for files with tens of millions of
lines prefer `batches()` or a Parquet export, and query the Parquet files with DuckDB or polars:

```python
with pyxaf.open("big.xaf") as af:
    for batch in af.tables["lines"].batches(batch_size=100_000):
        part = pa.record_batch(batch)  # or polars.DataFrame(batch)
        ...
```

## polars, pandas and Parquet

```python
with pyxaf.open("2024.xaf") as af:
    frames = af.to_polars()  # {'header': DataFrame, ..., 'lines': DataFrame}
    lines = af.to_polars(["lines"])["lines"]
    pdf = af.to_pandas(["accounts"])["accounts"]  # pandas with ArrowDtype columns
    af.export("out/", format="parquet")  # one .parquet file per table
```

| Method | Needs | Notes |
|---|---|---|
| `af.to_polars(tables=None, batch_size=65_536)` | `pyxaf[polars]` | built batch-wise from Arrow buffers, **no pyarrow**; streamed tables share one pass |
| `af.to_pandas(tables=None)` | `pyxaf[pandas]` | via pyarrow, with `pd.ArrowDtype` columns, so decimals stay exact (`decimal128(20, 2)[pyarrow]`) |
| `af.export(dir, format="parquet")` | `pyxaf[parquet]` | `pyarrow.parquet.ParquetWriter`, batch-wise |
| `af.tables[name].to_polars()` / `.to_pandas()` | as above | a single table |

## Exact decimals

Amounts are `decimal.Decimal` in Python and **`decimal128(20, 2)`** in Arrow, the precision and
scale of the XSD's amount type (20 digits, 2 decimals). Quantities and exchange rates are
`decimal128(24, 6)`, VAT percentages `decimal128(8, 3)`, dates `date32`. No float is involved at
any step: decimals are written into Arrow buffers as 16-byte integers.

A value that does not fit its column exactly, such as an amount with three decimals (`3612.165`,
invalid per the XSD but readable), is **not rounded**: Arrow export raises `ValueError` naming the
column and value. CSV and JSONL export write such values unchanged. Find them first with
[validation](validation.md) (`XAF3018`).

!!! tip "Keep the scale when computing"
    Sums stay exact in pyarrow, polars and DuckDB (polars widens a sum to `decimal[38,2]`). Be
    careful with casts: polars rounds silently when casting a decimal to a smaller scale, and
    `str.to_decimal` turns unparseable strings into nulls.

## Why pandas is optional

pandas is the most common choice for tabular data in Python, and the most expensive one to
install. Measured on Python 3.14, Linux x86_64:

| Package | Installed size | Import time | Max RSS |
|---|---:|---:|---:|
| standard library only (pyxaf core) | 0 | 22 ms | 17 MB |
| nanoarrow (`pyxaf[arrow]`) | 2.9 MB | 16 ms | 18 MB |
| polars | 176.5 MB | 97 ms | 52 MB |
| pyarrow | 151 MB | 28 ms | 52 MB |
| pandas (+ numpy, dateutil) | 97 MB | 303 ms | 72 MB |
| pandas + pyarrow | 248.5 MB | 315 ms | 115 MB |

pandas with pyarrow alone fills the 250 MB unzipped limit of an AWS Lambda function. pyxaf
therefore keeps its core free of dependencies and builds Arrow data with the 2.9 MB nanoarrow, which
already lets polars and DuckDB read the tables without pyarrow. Install pandas when you want it.
