# Architecture

This document describes the high-level architecture of pyxaf. Read it to find your way around
the code; the [user documentation](https://spireflyhq.github.io/pyxaf/) explains what the library
does, and [CONTRIBUTING.md](CONTRIBUTING.md) explains how to work on it.

## Bird's-eye view

pyxaf reads a Dutch *Auditfile Financieel*, the standard export of a general ledger, in any of its
seven iterations (ADF, CLAIR2, XAF 3.0, 3.1, 3.2, 3.2.1, 4.0), and maps it onto one
version-independent model. Files can be several gigabytes and come from untrusted sources and
from software that does not follow the specification. So the library:

1. reads bytes from a path, bytes object or stream, and unpacks gzip/zip;
2. works out the encoding and the iteration of the file;
3. parses the XML with a hardened, streaming event engine;
4. reads the master data (company, accounts, relations, VAT codes, periods) eagerly and then
   pauses at the first journal, so transactions and lines can be streamed lazily;
5. turns raw records into typed model objects, reporting every irregularity as a coded finding;
6. optionally validates the whole file in one streaming pass, and exports normalized tables.

```text
input ─► _source ─► _encoding ─► detect ─► _xml (events) ─► reader ─► _normalize ─► models
                                              │                │
                                              │                └─► tables / _arrow (exports)
                                              └─► validate (catalogue + semantics) ─► findings
ADF (fixed width) ─────────────────────────────► _adf ─────────┘
```

## Code map

All library code lives in `src/pyxaf/`. Modules starting with `_` are internal.

- `_source.py`: input sources (paths, bytes, streams), gzip/zip unpacking and decompression limits.
- `_encoding.py`: BOM and XML-declaration sniffing, transcoding of encodings Expat cannot decode
  itself (windows-1252 and others), and the opt-in byte-level repairs.
- `detect.py`: identifies the family and version by scoring several signals (namespace, root
  element, version fields, vocabulary), returning a `FormatInfo` with the reasons.
- `formats.py`: the `Version` and `Family` enums and what identifies each iteration.
- `_xml.py`: `XmlEngine`, the streaming event engine on top of `pyexpat`. It refuses
  DOCTYPE/ENTITY declarations, enforces depth and text-size limits and delivers events in batches.
- `reader.py`: `pyxaf.open()` and `AuditFile`. It reads master data at open time, pauses at the
  first journal and resumes when transactions are requested, and merges multi-file sets.
- `raw.py`: `RawRecord`, the text of an element and its children exactly as written.
- `_normalize.py`: builds model objects from raw records for CLAIR2 and XAF 3.0–4.0.
- `_adf.py`: the fixed-width ADF reader, which produces the same records.
- `models.py`: the normalized, English-named data model (`Header`, `LedgerAccount`,
  `Transaction`, `Line`, …).
- `values.py`: strict parsers for amounts, dates and numbers that keep the raw text.
- `findings.py`: `Finding`, `Severity`, the `CODES` registry and `FindingCollector`.
- `validate.py`: the layered validator: structure checks driven by the field catalogues, then
  references, totals, uniqueness and data quality.
- `catalogue/`: per-version field catalogues (every element path with cardinality, type and
  facets). `_generated_*.py` are generated from the XSDs by `scripts/gen_catalogues.py`.
- `schemas/`: the official XSDs, redistributed unchanged (sources in `schemas/NOTICE.md`).
- `_xsd.py`: optional validation against those XSDs with lxml.
- `tables.py`, `_arrow.py`: normalized tables and their exports (CSV, JSONL, Arrow, polars,
  pandas, Parquet).
- `rgs/`: loading the official RGS Excel release (with a stdlib `.xlsx` reader) and checking RGS
  codes.
- `cli.py`, `__main__.py`: the Typer command line.
- `_optional.py`: lazy imports of optional extras, with an install hint when one is missing.

Outside the package:

- `tests/`: the pytest suite. `tests/xafgen.py` renders one synthetic ledger as every iteration
  (XSD-valid), so most tests build their input instead of reading fixture files.
- `scripts/`: code generators for the catalogues and the generated documentation pages.
- `benchmarks/`: the throughput and memory benchmark.
- `examples/`: synthetic sample files for the README quickstart (generated).
- `docs/`: the documentation site (Zensical, configured in `zensical.toml`).
- `noxfile.py`: developer tasks (`uvx nox -l`).

## Invariants

These hold everywhere; a change that breaks one needs a very good reason.

- **No runtime dependencies in the core.** Only the standard library is imported at module import
  time. Optional libraries (lxml, nanoarrow, polars, pandas, pyarrow, Typer) are imported lazily
  through `_optional.py`, and CI tests the wheel without any of them.
- **Never refuse a file for data problems.** Data problems become findings with a stable
  `XAF<nnnn>` code and a severity. Only input that is not an auditfile at all, and security
  violations, raise exceptions.
- **No silent coercion.** Money is `decimal.Decimal`, never `float`. Parsed values keep their raw
  text, and anything pyxaf could not parse is reported.
- **Constant memory.** Transactions and lines are streamed; nothing may hold the whole file or
  all transactions in memory.
- **Hostile input is expected.** DOCTYPE and ENTITY declarations are refused, there is no parser
  "recover" mode, and nesting depth, text size and decompressed size are limited. lxml is only
  ever used with `resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False`.
- **Versions are detected, not assumed.** A namespace alone does not decide the version; real
  files carry wrong and unofficial namespaces.

## Cross-cutting concerns

- **Event batching.** The XML engine delivers events per input chunk, so the parser state may be
  ahead of the event being handled. Handlers must use the frame carried by the event, never the
  live parser stack.
- **Findings on repeated passes.** Iterating transactions again re-parses the file. Value and
  data-quality findings are reported on the first complete pass only.
- **Generated code.** The catalogues and `docs/reference/codes.md` / `tables.md` are generated;
  CI fails when they are stale. Regenerate with `uvx nox -s generate`.
- **Performance.** `Line` and `Transaction` are created once per line of a multi-gigabyte file,
  so they are slotted, non-frozen dataclasses. Measure with `uvx nox -s bench` before and after
  changing the hot path.
