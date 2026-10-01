# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0 the public API may
change in minor releases. Finding codes are stable: a code is never renumbered or reused.

## [Unreleased]

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
