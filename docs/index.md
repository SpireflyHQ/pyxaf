# pyxaf

**Read and validate every iteration of the Dutch Auditfile Financieel in Python.**

pyxaf is an open-source library for the *Auditfile Financieel*, the Dutch standard export of a
general ledger that accounting software produces for tax inspectors and auditors. It reads all
seven iterations of the format into one version-independent model, streams files of any size in
constant memory, and validates them locally, without uploading anything, against the rules of the
detected version.

| Iteration | Year | Identified by | Structure source in pyxaf | XSD check (`pyxaf[xsd]`) |
|---|---|---|---|---|
| ADF (`CLAIR1.00.00`) | 1999 | fixed-width ASCII, first 12 bytes `CLAIR1.00.00` | official manual (field positions) | no XSD (fixed width) |
| CLAIR2 (`CLAIR2.00.00`) | 2003 | no namespace, `header/auditfileVersion` | official 2003 XSD, bundled | yes |
| XAF 3.0 | ~2008 | `http://www.auditfiles.nl/XAF/3.0` (unverified) | derived catalogue (no public XSD) | no |
| XAF 3.1 | ~2010 | `http://www.auditfiles.nl/XAF/3.1` | derived catalogue (no public XSD) | no |
| XAF 3.2 | 2014 (+ 2017 addendum) | `http://www.auditfiles.nl/XAF/3.2` | official XSD, bundled | yes |
| XAF 3.2.1 | 2024 | ODB namespace `…/BCPP/1.0/…/XmlAuditfileFinancieel3.2.1` | official XSD, bundled | yes |
| XAF 4.0 (4.0.3) | 2025 | ODB namespace `…/BCPP/1.1/…/XmlauditfileXAF_4.0` | official XSD, bundled | yes |

See [Versions](guide/versions.md) for how pyxaf tells them apart.

!!! warning "From 1 January 2027 only XAF 4.0 is accepted"
    The Belastingdienst announced that from 1 January 2027 it accepts only XAF 4.0 auditfiles.
    Files in older iterations stay relevant for years (seven-year retention), and pyxaf keeps
    reading them.

## Features

- **Zero runtime dependencies.** The core uses only the standard library (`pyexpat`, `decimal`,
  `zipfile`, `gzip`, `csv`, `json`) and is pure Python, so it fits small environments such as
  AWS Lambda. Heavier libraries are opt-in extras and are only imported when you use them.
- **Streaming in constant memory.** Master data (company, accounts, relations, VAT codes,
  periods, opening balance) is read when the file is opened; transactions and lines are parsed
  lazily as you iterate. Memory does not grow with file size, and real files reach several GB.
- **Lenient reading with graded findings.** pyxaf never refuses a file because of data problems.
  Every irregularity becomes a [finding](reference/codes.md) with a stable code (`XAF5009`), a
  severity (`ERROR`, `WARNING`, `INFO`) and a position. Values are `decimal.Decimal`, never
  `float`, and the raw text is always kept next to the parsed value.
- **A local validator** that checks encoding, XML, version, structure (an XSD-like check
  without dependencies), references, control totals and balance, uniqueness and data quality,
  including the official XAF 4.0 consistency rules [0001]–[0010]. It runs in one streaming pass
  on files of any size. See [Validation](guide/validation.md).
- **Normalized tables** with fixed schemas for every iteration, exported to CSV and JSONL with
  the standard library, or to Arrow, polars, pandas and Parquet through extras. See
  [Interoperability](guide/interop.md).
- **RGS support**: load the official RGS Excel release you downloaded and check every account's
  RGS code against it. See [RGS](guide/rgs.md).
- **Hardened against hostile files**: DOCTYPE/ENTITY declarations are refused, no "recover" parser
  mode, limits on nesting depth, text size and decompression. See [Security](security.md).

## Installation

```bash
pip install pyxaf
```

pyxaf needs Python 3.11 or newer. Optional features are installed as extras:

| Extra | Installs | Enables |
|---|---|---|
| `xsd` | lxml ≥ 6.1.3 | validation against the bundled official XSDs (`validate(..., xsd=True)`) |
| `arrow` | nanoarrow | Arrow PyCapsule export (`pyarrow.table(...)`, DuckDB, polars) and record batches |
| `polars` | polars | `to_polars()` (no pyarrow needed) |
| `pandas` | pandas, pyarrow | `to_pandas()` with Arrow-backed dtypes |
| `parquet` | pyarrow | `export(..., format="parquet")` |
| `cli` | Typer | the `pyxaf` command line |
| `all` | everything above | |

```bash
pip install "pyxaf[cli]"            # command line
pip install "pyxaf[polars]"         # polars DataFrames (no pyarrow)
pip install "pyxaf[all]"            # everything
```

!!! note "Arrow data is built with nanoarrow"
    `to_polars()`, `to_pandas()` and Parquet export build their Arrow data with nanoarrow, which
    the `polars`, `pandas` and `parquet` extras install. A missing package raises
    `MissingExtraError` naming the extra to install.

## Quick start

No auditfile at hand? Download a synthetic sample (`2024-broken.xaf` is the same ledger with one
mistyped amount):

```console
curl -L -O https://raw.githubusercontent.com/SpireflyHQ/pyxaf/main/examples/2024.xaf \
        -O https://raw.githubusercontent.com/SpireflyHQ/pyxaf/main/examples/2024-broken.xaf
```

```python
import pyxaf

with pyxaf.open("2024.xaf") as af:  # any iteration; .gz/.zip too
    print(af.version, af.company.name, af.header.fiscal_year)
    for tx in af.transactions():  # streamed, constant memory
        if not tx.balanced:
            print("unbalanced:", tx.journal_id, tx.number)
    print(af.opening_balance().by_account())  # signed, debit-positive

report = pyxaf.validate("2024.xaf")  # one streaming pass
print(report.ok, len(report.errors), len(report.warnings))
```

Or from the shell (with `pyxaf[cli]`):

```console
$ pyxaf validate 2024.xaf
== 2024.xaf
4.0 — 0 error(s), 0 warning(s), 0 info
```

Continue with [Reading auditfiles](guide/reading.md).

## Disclaimer

pyxaf is an independent open-source project. It is **not affiliated with, endorsed by or
supported by the Belastingdienst** (Dutch Tax and Customs Administration), its Ontwikkelaars
Documentatie Belastingdienst (ODB) or validation service (VTS), **or the Taakgroep RGS**. A clean
pyxaf report does not mean that the Belastingdienst will accept a file; use the official
validation service for that. pyxaf does not bundle RGS data; see [RGS](guide/rgs.md).

pyxaf is MIT-licensed. Please never attach real client auditfiles to issues: they contain
confidential financial and personal data (see [Security](security.md#privacy)).
