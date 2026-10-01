# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0 the public API may
change in minor releases. Finding codes are stable: a code is never renumbered or reused.

## [Unreleased]

### Added

- `ValidationReport.severity_counts`: findings per severity, including those suppressed by limits
  (also in `to_dict()` / the JSON report).
- Checks that the files of a multi-file set belong together: another administration (`XAF6011`),
  another version (`XAF3008`), and inconsistent "Vervolgbestand x van y" numbering (`XAF3009`).
- `XAF4008`: references that could not be checked because an XAF file has no master data section
  for them (for example no `customersSuppliers`), instead of silently skipping them.
- The structure check (L4) now reports elements in a foreign namespace or with an undeclared
  prefix (`XAF3011`).
- `pyxaf.values.parse_double()` for `xs:double` values such as `2.1E1`.
- The Apache-2.0 licence of the AnalyticsLibrary path table, from which the XAF 3.0/3.1
  catalogues are derived, now ships with the package.

### Fixed

- **The verdict no longer depends on the finding limits**: `ok`, `max_severity` and the exit code
  of `pyxaf validate` are based on all findings, so `max_findings`/`--max-findings` can no longer
  turn an invalid file into a valid one.
- **Amounts are exact in any decimal context**: sign changes, sums and totals, ADF amounts, decimal
  facets and Arrow/polars/pandas/Parquet exports no longer depend on (or round to) the caller's
  `decimal` context. Values with more digits than the precision were rounded away before the facet
  check; `on_inexact="round"` now always rounds half to even.
- **Structure check**: duplicated containers (`XAF3012`), leaf and complex children out of
  order (`XAF3013`) and an incomplete `xs:choice` branch (`XAF3010`) were not reported.
- Reading the journals of a caller's seekable stream (for example `io.BytesIO`) while
  transactions were pending corrupted the paused parser on larger files.
- The decompression-bomb guard now also applies to gzip input from a file object; it only
  applied to paths and bytes.
- An invalid ADF debit or credit amount became `0`; the line's amounts are now `None`, as in the
  XML formats.
- Iterating `af.raw.transactions()` first suppressed the value and data-quality findings of a
  later `af.transactions()`.
- `validate()` raised on a very long integer (`ValueError`) and on a size limit reached while
  detecting the version; both are findings now.
- XSD validation ignored an `encoding=` override, transcoding and the `latin1-as-cp1252` repair;
  an override also did not apply to UTF-16 files without a byte-order mark.
- Validation memory: transaction numbers checked for uniqueness move to a temporary SQLite file
  beyond 500,000, and streaming XSD validation releases validated elements.
- A batch size of zero or less is rejected (`ValueError`) instead of dropping rows or never
  finishing.
- `xs:double` values with an exponent (CLAIR2 `vatPercentage`) were reported as invalid, and dates
  with an out-of-range time zone (`+99:99`) were accepted.
- A small RGS workbook could allocate millions of empty rows: row numbers are checked against
  Excel's grid (1–1,048,576) and must not decrease, and gaps are expanded lazily.
- gzip and zip inputs given as a path no longer leave the file open.
- `CITATION.cff` version, the `--output json` option in the issue template, and the documented
  exit code of command-line usage errors (3).

### Changed

- Releases are published to PyPI/TestPyPI only after the full CI has passed on the tagged commit.
- Coverage no longer excludes every function whose signature contains `...`.
- `RawRecord` and the documentation no longer call the raw layer lossless: it keeps the data
  exactly, not the XML markup.

## [0.1.2] - 2026-10-01

### Fixed

- The README no longer opens with an example that reads a file the reader does not have yet;
  the quickstart downloads the sample files first.

## [0.1.1] - 2026-10-01

### Added

- Synthetic sample auditfiles in `examples/` (`2024.xaf` and `2024-broken.xaf`) to try pyxaf
  without an auditfile of your own.

### Fixed

- The README quickstart referred to a `2024.xaf` that was not available; it now starts by
  downloading the sample files.

## [0.1.0] - 2026-10-01

Initial release.

### Added

- **Reading every iteration** of the Dutch Auditfile Financieel: XML Auditfile Financieel 4.0,
  3.2.1, 3.2, 3.1 and 3.0, CLAIR2 and the fixed-width ADF (CLAIR1) format, through one
  `pyxaf.open()` API with a normalized, typed data model (company, ledger accounts with RGS
  references, customers/suppliers, VAT codes, periods, journals, transactions, lines, opening
  balance). The original raw records stay available next to the parsed values.
- **Streaming**: transactions and lines are streamed, so memory does not grow with file size.
  Multi-file sets are supported: continuation files ("Vervolgbestand x van y") are read as one
  document, per-period files are merged (master data deduplicated, conflicts reported).
- **Robust input**: corrupt archives raise `CorruptArchiveError`; encodings Expat lacks
  (windows-125x, UTF-32, …) are transcoded; Arrow-based exports take `on_inexact=`.
- **Raw access**: `af.raw.header`, `af.raw.company` and `af.raw.transactions()` stream the
  lossless raw records without normalization (about twice as fast).
- **Exact values**: amounts are `decimal.Decimal` with the raw text kept; amounts with 0–2
  decimals, negative amounts and opaque period numbers are handled. The opening balance is unified
  from the `openingBalance` element or period-0/opening-journal transactions.
- **Input handling**: paths, bytes and binary streams; transparent gzip and single-member zip;
  UTF-8 (with or without BOM), ISO-8859-1 and windows-1252, `encoding=` override and opt-in,
  reported repairs (`control-chars`, `latin1-as-cp1252`, `bare-ampersand`). Encrypted
  tax-authority containers are recognised and rejected with an explanatory error.
- **Version detection** (`pyxaf.detect()`) by scoring signals (namespace, vocabulary, ADF
  signature, continuation comment) rather than namespace alone, with confidence, reasons and the
  namespace status (official, documented variant, known bogus, none).
- **Validation** (`pyxaf.validate()`) with graded, coded findings (`XAF<nnnn>`, severities
  ERROR/WARNING/INFO, documented in `pyxaf.CODES`): encoding, XML well-formedness, structure per
  version (required elements, cardinality, lengths, code lists, patterns), references, control
  totals and balance, uniqueness and data quality. The official XAF 4.0 consistency rules
  [0001]–[0010] map to codes; rule sets `spec` and `vts`; per-code and overall finding limits.
  Data problems never stop a file from loading.
- **Field catalogues** for every XML iteration, generated from the official XSDs
  (`scripts/gen_catalogues.py`).
- **XSD validation** against the bundled official schemas (CLAIR2, 3.2, 3.2.1, 4.0) with the
  `xsd` extra (lxml >= 6.1.3, hardened parser options).
- **Normalized tables and exports**: fixed-schema tables (`header`, `company`, `addresses`,
  `accounts`, `relations`, `vat_codes`, `periods`, `journals`, `transactions`, `lines`,
  `line_vat`, `opening_balance`); CSV and JSON Lines with the standard library; Arrow streams
  with exact `decimal128` columns (`arrow` extra, nanoarrow); `to_polars()` (`polars` extra),
  `to_pandas()` (`pandas` extra) and Parquet (`parquet` extra).
- **RGS** (`pyxaf.rgs`): load the official RGS Excel release with a small standard-library
  `.xlsx` reader, resolve codes and their hierarchy, and check the RGS references of an auditfile.
  No RGS data is bundled.
- **Command line** (`cli` extra): `pyxaf detect`, `info`, `validate` (text or JSON output,
  `--strict`), `export` and `codes`, with documented exit codes. Without the extra, `pyxaf` prints
  an install hint.
- **Security hardening** for untrusted input: DOCTYPE/ENTITY declarations are refused, no
  network or entity resolution, limits on nesting depth, text-node size and decompressed size.
- Zero runtime dependencies in the core; fully typed (`py.typed`); Python 3.11–3.14.

[Unreleased]: https://github.com/SpireflyHQ/pyxaf/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/SpireflyHQ/pyxaf/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/SpireflyHQ/pyxaf/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SpireflyHQ/pyxaf/releases/tag/v0.1.0
