# pyxaf

[![CI](https://github.com/SpireflyHQ/pyxaf/actions/workflows/ci.yml/badge.svg)](https://github.com/SpireflyHQ/pyxaf/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pyxaf)](https://pypi.org/project/pyxaf/)
[![Python](https://img.shields.io/pypi/pyversions/pyxaf)](https://pypi.org/project/pyxaf/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/SpireflyHQ/pyxaf/blob/main/LICENSE)

Read and validate **every iteration of the Dutch Auditfile Financieel** — XAF 4.0, 3.2.1, 3.2,
3.1, 3.0, the XML `CLAIR2` format and the fixed-width ASCII auditfile (ADF) — in plain Python,
with **no runtime dependencies**.

```python
import pyxaf

with pyxaf.open("2024.xaf") as af:  # any iteration; .gz/.zip work too
    print(af.version, af.company.name)  # 4.0 Voorbeeld B.V.
    for line in af.lines():  # streamed: constant memory, even for GBs
        print(line.account_id, line.signed_amount)

report = pyxaf.validate("2024.xaf")  # official rules [0001]–[0010] and much more
print(report.ok, [str(f) for f in report.errors])
```

## Why

- **All versions, one model.** Files from 1999 to today map onto the same typed model
  (`Header`, `LedgerAccount`, `Transaction`, `Line`, …). The lossless raw layer keeps every
  element exactly as written, including vendor extensions.
- **Lenient reading, honest reporting.** Real auditfiles break the specification in many ways
  (unbalanced opening balances, wrong namespaces, negative amounts, Windows-1252 in "ISO-8859-1"
  files, …). pyxaf reads them anyway and reports every problem as a graded, coded finding
  (`XAF5009 ERROR …`) instead of refusing the file or silently guessing.
- **A local validator.** The official validation service is subscriber-only and limited to 5 MB;
  pyxaf validates files of any size locally: structure (dependency-free, XSD-like), references,
  control totals, balance, uniqueness, data quality and RGS codes — plus the official XSD with
  `pyxaf[xsd]`.
- **Production friendly.** Standard library only (hardened `pyexpat`), streaming, exact
  `Decimal` money, fully typed. Heavy libraries are optional extras, never imported unless used.
- **Secure by default.** DOCTYPE/ENTITY declarations are refused (no billion laughs, no XXE),
  nesting, text and decompression sizes are limited, and there is no "recover" mode that could
  silently drop data.

| Iteration | Year | Identified by |
|---|---|---|
| XAF 4.0 (4.0.3) | 2025 | namespace (incl. known variants), 4.0-only elements |
| XAF 3.2.1 | 2024 | ODB namespace |
| XAF 3.2 | 2014/2017 | `http://www.auditfiles.nl/XAF/3.2` |
| XAF 3.1 / 3.0 | ~2010 / ~2008 | namespace and vocabulary (structure derived; no public XSD) |
| CLAIR2 | 2003 | `header/auditfileVersion = CLAIR2.00.00`, no namespace |
| ADF | 1999 | fixed-width ASCII starting with `CLAIR1.00.00` |

From 1 January 2027 the Belastingdienst only accepts XAF 4.0, but older files remain in use for
years (seven-year retention), which is why pyxaf reads them all.

## Install

```console
pip install pyxaf                 # core: no dependencies
pip install "pyxaf[cli]"          # command line (Typer)
pip install "pyxaf[xsd]"          # XSD validation (lxml)
pip install "pyxaf[polars]"       # polars DataFrames (no pyarrow needed)
pip install "pyxaf[pandas]"       # pandas DataFrames (pandas + pyarrow)
pip install "pyxaf[arrow]"        # Arrow PyCapsule streams (nanoarrow) for DuckDB, pyarrow, …
pip install "pyxaf[parquet]"      # Parquet export
pip install "pyxaf[all]"
```

Python 3.11 or newer.

## More examples

```python
import pyxaf

with pyxaf.open("2024.xaf") as af:
    af.format  # FormatInfo: version, namespace status, encoding, reasons
    af.accounts["1000"].rgs  # RgsRef(code='BLimKas', …) from RGScode / taxonomy / …
    ob = af.opening_balance()  # element, period-0 or journal-O transactions: one view
    ob.by_account()  # {'1100': Decimal('10000.00'), …}

    af.export("out/", format="csv")  # header, accounts, …, lines, line_vat (stdlib)
    frames = af.to_polars()  # dict of polars DataFrames, Decimal(20, 2)

import pyarrow as pa

pa.table(af.tables["lines"])  # Arrow PyCapsule interface

# split auditfiles ("Vervolgbestand 2 van 3") and per-period files
with pyxaf.open(["big.xaf", "big-2.xaf", "big-3.xaf"]) as af:
    ...

# problem files
pyxaf.open("old.xaf", encoding="cp1252", repair={"control-chars", "bare-ampersand"})
```

```console
$ pyxaf validate --xsd 2024.xaf
$ pyxaf info 2024.xaf
$ pyxaf export 2024.xaf --format parquet --out out/
```

Exit codes: 0 ok, 1 warnings (with `--strict`), 2 errors, 3 tool failure.

## Documentation

<https://spireflyhq.github.io/pyxaf/>: reading guide, version guide with the field mapping across
all iterations, validation and finding-code reference, interop, RGS, CLI and security notes.
Release notes are in the [changelog](https://github.com/SpireflyHQ/pyxaf/blob/main/CHANGELOG.md); the compatibility promise is in
[Versioning](https://spireflyhq.github.io/pyxaf/versioning/). For help, see
[SUPPORT.md](https://github.com/SpireflyHQ/pyxaf/blob/main/SUPPORT.md).

## Contributing

Contributions are welcome: see [CONTRIBUTING.md](https://github.com/SpireflyHQ/pyxaf/blob/main/CONTRIBUTING.md) for the development
setup and [ARCHITECTURE.md](https://github.com/SpireflyHQ/pyxaf/blob/main/ARCHITECTURE.md) for a map of the code. **Never attach real client
auditfiles to issues or pull requests**: they contain personal and financial data. Anonymised
snippets and finding codes are enough.

## Licence and attribution

pyxaf is MIT-licensed. The bundled XML schemas are published by the Belastingdienst (XAF 3.2.1,
4.0; ODB site text under CC0), the former auditfiles.nl platform (XAF 3.2) and SRA (CLAIR2, 2003).
The XAF 3.0/3.1 element lists are derived from the AnalyticsLibrary "XAF Mapping en Namen" table
(Apache-2.0). RGS data is not bundled; load the official Excel yourself with
`pyxaf.rgs.load_excel`.

pyxaf is an independent open-source project and is not affiliated with or endorsed by the
Belastingdienst, the Taakgroep RGS or any software vendor.
