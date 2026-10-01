# pyxaf

[![CI](https://github.com/SpireflyHQ/pyxaf/actions/workflows/ci.yml/badge.svg)](https://github.com/SpireflyHQ/pyxaf/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pyxaf)](https://pypi.org/project/pyxaf/)
[![Python](https://img.shields.io/pypi/pyversions/pyxaf)](https://pypi.org/project/pyxaf/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/SpireflyHQ/pyxaf/blob/main/LICENSE)

**Read and validate every version of the Dutch Auditfile Financieel in plain Python.** 🇳🇱

The *Auditfile Financieel* is the standard export of a general ledger that Dutch accounting
software produces for tax inspectors and auditors. pyxaf opens all seven versions of it, from the
fixed-width files of 1999 to XAF 4.0, gives you one clean, typed model to work with, and tells
you exactly what is wrong with a file instead of choking on it. No runtime dependencies, no
upload to anyone's server, no gigabytes of RAM.

```python
import pyxaf

with pyxaf.open("2024.xaf") as af:
    print(af.version, af.company.name)  # 4.0 Voorbeeld & Zonen B.V.
```

**Contents:** [Why pyxaf](#why-pyxaf) · [Installation](#installation) ·
[Quickstart](#quickstart) · [More examples](#more-examples) ·
[Supported versions](#supported-versions) · [Documentation](#documentation) ·
[Contributing](#contributing) · [License](#license-and-attribution)

## Why pyxaf

Real auditfiles are messy. Official test files break the official rules, vendors invent their own
namespaces, and an "ISO-8859-1" file turns out to be Windows-1252. pyxaf was built for those files.

- **All versions, one model.** XAF 4.0, 3.2.1, 3.2, 3.1, 3.0, CLAIR2 and the fixed-width ADF all
  map onto the same classes: `Header`, `LedgerAccount`, `Transaction`, `Line` and friends. The
  raw layer keeps every element exactly as written, vendor extensions included.
- **Lenient reading, honest reporting.** pyxaf never refuses a file because of bad data. Every
  problem becomes a graded finding with a stable code, such as `XAF5010 ERROR`, so nothing is
  silently guessed or dropped.
- **A validator on your own machine.** The official validation service is for subscribers and
  stops at 5 MB. pyxaf checks files of any size: structure, references, control totals,
  balance, uniqueness, data quality and RGS codes, including the official XAF 4.0 rules
  [0001]–[0010]. 🔍
- **Exact money.** Amounts are `decimal.Decimal`, never `float`, with the original text kept
  next to every parsed value.
- **Streaming.** Master data is read when the file opens; transactions and lines are streamed,
  so memory stays flat even for files of several gigabytes.
- **Lightweight.** The core uses only the standard library and is fully typed. pandas, polars,
  Arrow, lxml and the command line are optional extras that are only imported when you use them.
- **Safe with untrusted files.** DOCTYPE and ENTITY declarations are refused (no "billion
  laughs", no XXE), nesting, text and decompression sizes are capped, and there is no "recover"
  mode that could quietly lose data.

## Installation

pyxaf needs Python 3.11 or newer.

```console
pip install pyxaf
```

The core has no dependencies. Add an extra for each optional feature you need:

| Extra | Adds | Install |
|---|---|---|
| `cli` | the `pyxaf` command line | `pip install "pyxaf[cli]"` |
| `xsd` | validation against the official XSDs (lxml) | `pip install "pyxaf[xsd]"` |
| `polars` | `to_polars()`, without pyarrow | `pip install "pyxaf[polars]"` |
| `pandas` | `to_pandas()` with Arrow-backed types | `pip install "pyxaf[pandas]"` |
| `arrow` | Arrow streams for DuckDB, pyarrow and friends (nanoarrow) | `pip install "pyxaf[arrow]"` |
| `parquet` | Parquet export | `pip install "pyxaf[parquet]"` |
| `all` | everything above | `pip install "pyxaf[all]"` |

Using uv? `uv add pyxaf` works the same way, for example `uv add "pyxaf[cli,polars]"`.

## Quickstart

Grab an auditfile exported from your accounting software. The examples use `2024.xaf`, but any
version works, and so do `.gz` and `.zip` files.

### 1. Open the file

```python
import pyxaf

af = pyxaf.open("2024.xaf")
print(af.version, af.company.name, af.header.fiscal_year)
print(len(af.accounts), "ledger accounts")
```

```text
4.0 Voorbeeld & Zonen B.V. 2024
11 ledger accounts
```

pyxaf detected the version and encoding, and read the master data: company, ledger accounts,
customers and suppliers, VAT codes and periods.

### 2. Walk through the transactions

Transactions and their lines are streamed straight from the file, one at a time:

```python
for tx in af.transactions():
    print(tx.journal_id, tx.number, tx.date, tx.description, tx.balanced)
    for line in tx.lines:
        print("   ", line.account_id, line.side, line.amount)
```

```text
MEM 1 2024-01-05 Boeking 1 True
    1300 D 3612.16
    8000 C 2985.26
    1800 C 626.90
...
```

The opening balance gets the same treatment, whichever way the file stores it:

```python
print(af.opening_balance().by_account())  # signed: debit positive, credit negative
af.close()
```

```text
{'1100': Decimal('10000.00'), '0100': Decimal('2500.50'), '0500': Decimal('-12500.50')}
```

### 3. Validate it

```python
report = pyxaf.validate("2024.xaf")
print(report.ok)
for finding in report.errors:
    print(finding.code, finding.line, finding.message)
```

A clean file prints `True`. A file in which someone typed `3621.16` instead of `3612.16` prints:

```text
False
XAF5007 None transactions totalDebit 38260.42 ≠ sum of debit lines 38269.42
XAF5010 223 transaction '1' in journal 'MEM' does not balance: debit 3621.16, credit 3612.16
```

Every finding has a stable code, a severity (`ERROR`, `WARNING` or `INFO`) and, where possible,
a line number. The [finding-code reference](https://spireflyhq.github.io/pyxaf/reference/codes/)
explains each one.

### 4. Or use the command line

With `pyxaf[cli]` installed:

```console
$ pyxaf validate 2024.xaf
== 2024.xaf
4.0 — 0 error(s), 0 warning(s), 0 info

$ pyxaf info 2024.xaf
version          4.0
company          Voorbeeld & Zonen B.V.
fiscal year      2024
transactions     12
lines            36
total debit      38260.42
total credit     38260.42
...
```

`pyxaf validate` exits with 0 when the file is fine, 1 for warnings (with `--strict`), 2 for
errors and 3 when pyxaf itself could not do its job, so it slots straight into scripts and CI. 🎉

## More examples

### Export to tables

pyxaf turns a file into twelve normalized tables, such as `accounts`, `transactions` and `lines`,
with the same columns for every version:

```python
with pyxaf.open("2024.xaf") as af:
    af.export("out/", format="csv")  # standard library only; also "jsonl"
    af.export("out/", format="parquet")  # pyxaf[parquet]
    frames = af.to_polars()  # pyxaf[polars]: a dict of DataFrames
    frames["lines"]  # amounts as Decimal(20, 2), not float
```

### Query with DuckDB or pyarrow

Tables speak the Arrow PyCapsule interface, so Arrow-aware tools read them directly
(needs `pyxaf[arrow]`):

```python
import duckdb

with pyxaf.open("2024.xaf") as af:
    lines = af.tables["lines"]
    duckdb.sql("select account_id, sum(signed_amount) from lines group by account_id").show()
```

### Check RGS codes

pyxaf reads each account's RGS (Referentie Grootboekschema) code. Load the official RGS Excel
release you downloaded, and the validator checks every code against it:

```python
import pyxaf.rgs

schema = pyxaf.rgs.load_excel("RGS 3.8-def.xlsx")
report = pyxaf.validate("2024.xaf", rgs=schema)
```

### Split files and problem files

```python
# a split auditfile ("Vervolgbestand 2 van 3") or one file per period, read as one
with pyxaf.open(["big.xaf", "big-2.xaf", "big-3.xaf"]) as af:
    ...

# a file that lies about its encoding, with stray control characters and bare "&"
af = pyxaf.open("old.xaf", encoding="cp1252", repair={"control-chars", "bare-ampersand"})
```

Repairs are opt-in and every repair shows up as a finding, so you always know what was changed.

<details>
<summary><b>Go deeper: raw records, detection details and the official XSDs</b></summary>

```python
with pyxaf.open("2024.xaf") as af:
    af.format  # FormatInfo: version, namespace status, encoding, reasons
    af.accounts["1000"].rgs  # RgsRef(code='BLimKas', source='RGScode', ...)
    for journal_id, record in af.raw.transactions():
        ...  # lossless raw records, about twice as fast

report = pyxaf.validate("2024.xaf", xsd=True)  # also check the official XSD (pyxaf[xsd])
```

</details>

## Supported versions

| Version | Year | How pyxaf recognises it |
|---|---|---|
| XAF 4.0 (4.0.3) | 2025 | namespace (including known variants) and 4.0-only elements |
| XAF 3.2.1 | 2024 | its own ODB namespace |
| XAF 3.2 | 2014, 2017 | `http://www.auditfiles.nl/XAF/3.2` |
| XAF 3.1 and 3.0 | ~2010, ~2008 | namespace and vocabulary (no public XSD exists) |
| CLAIR2 | 2003 | `header/auditfileVersion` is `CLAIR2.00.00`, no namespace |
| ADF | 1999 | fixed-width ASCII starting with `CLAIR1.00.00` |

From 1 January 2027 the Belastingdienst only accepts XAF 4.0. Older files stay around for years
because of the seven-year retention period, which is why pyxaf reads them all. 📚

## Documentation

The full documentation lives at **<https://spireflyhq.github.io/pyxaf/>**:

- [Reading auditfiles](https://spireflyhq.github.io/pyxaf/guide/reading/): the data model,
  streaming, opening balances, encodings and repairs
- [Versions](https://spireflyhq.github.io/pyxaf/guide/versions/): how the seven versions differ
  and the field mapping across all of them
- [Validation](https://spireflyhq.github.io/pyxaf/guide/validation/) and the
  [finding codes](https://spireflyhq.github.io/pyxaf/reference/codes/)
- [Interoperability](https://spireflyhq.github.io/pyxaf/guide/interop/),
  [RGS](https://spireflyhq.github.io/pyxaf/guide/rgs/) and the
  [command line](https://spireflyhq.github.io/pyxaf/guide/cli/)
- [Security](https://spireflyhq.github.io/pyxaf/security/) and
  [versioning](https://spireflyhq.github.io/pyxaf/versioning/)

Release notes are in the [changelog](https://github.com/SpireflyHQ/pyxaf/blob/main/CHANGELOG.md).
For questions, see [SUPPORT.md](https://github.com/SpireflyHQ/pyxaf/blob/main/SUPPORT.md).

## Contributing

Contributions are very welcome, especially reports of files from software that pyxaf does not
handle well yet. 🙌 Start with [CONTRIBUTING.md](https://github.com/SpireflyHQ/pyxaf/blob/main/CONTRIBUTING.md)
for the development setup, and [ARCHITECTURE.md](https://github.com/SpireflyHQ/pyxaf/blob/main/ARCHITECTURE.md)
for a map of the code. Issues labelled
[good first issue](https://github.com/SpireflyHQ/pyxaf/labels/good%20first%20issue) are a good
place to begin.

> **Never attach a real auditfile** to an issue or pull request. Auditfiles contain personal data
> and confidential financial records. The version, the exporting software, the finding codes and
> a small hand-made snippet are all we need.

Security problems are reported privately, as described in
[SECURITY.md](https://github.com/SpireflyHQ/pyxaf/blob/main/SECURITY.md).

## License and attribution

pyxaf is released under the [MIT license](https://github.com/SpireflyHQ/pyxaf/blob/main/LICENSE).

The bundled XML schemas are published by the Belastingdienst (XAF 3.2.1 and 4.0), the former
auditfiles.nl platform (XAF 3.2) and SRA (CLAIR2). The XAF 3.0 and 3.1 element lists are derived
from the AnalyticsLibrary "XAF Mapping en Namen" table (Apache-2.0). RGS data is not bundled;
load the official Excel release yourself with `pyxaf.rgs.load_excel`.

pyxaf is an independent open-source project. It is not affiliated with or endorsed by the
Belastingdienst, the Taakgroep RGS or any software vendor, and a clean pyxaf report does not
guarantee that the Belastingdienst will accept a file.
