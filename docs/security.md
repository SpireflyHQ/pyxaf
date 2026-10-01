# Security

Auditfiles are **untrusted input**. They arrive from clients, from third-party accounting software
and by e-mail, and pyxaf is often run on servers that process files from many sources. pyxaf is
designed to read a hostile file safely: it must not read local files, open network connections,
or exhaust memory or CPU because of what a file contains.

To report a vulnerability, follow the [security policy](https://github.com/SpireflyHQ/pyxaf/blob/main/SECURITY.md):
privately, through GitHub's private vulnerability reporting or by e-mail, never in a public issue.

## Threat model

| Threat | Mitigation |
|---|---|
| Entity expansion ("billion laughs", quadratic blow-up) | DOCTYPE and ENTITY declarations are refused before any expansion happens |
| External entities (XXE): reading local files, server-side requests | refused with the DOCTYPE; Expat never opens files or network connections; parameter-entity parsing is disabled |
| Deeply nested elements | `max_depth` (default 32; XAF needs at most 8 levels) |
| Huge text nodes | `max_text_size` (default 10 MiB per element) |
| Decompression bombs in gzip/zip input | decompressed size counted while streaming; ratio limit and optional absolute cap |
| Memory exhaustion by file size | transactions and lines are streamed; memory does not grow with the file |
| Millions of findings | `max_findings_per_code` and `max_findings` |
| Silent data corruption | no parser "recover" mode; malformed XML is an error with a position |
| Hostile RGS workbook (`.xlsx`) | the same DOCTYPE/ENTITY refusal for every XML part, a per-part size cap, cell references and row numbers beyond Excel's grid (16,384 columns, 1,048,576 rows) refused, row numbers must not decrease, and a gap of absent rows is expanded only as rows are consumed |

pyxaf only reads the paths and streams you pass in, and never writes anything except the export
files you request.

## DOCTYPE and ENTITY declarations are refused

No iteration of the Auditfile Financieel uses a DTD. pyxaf's XML engine, built on the standard
library's `pyexpat`, therefore raises `ForbiddenConstructError` as soon as it meets a DOCTYPE,
ENTITY, unparsed-entity or notation declaration, or an external entity reference:

```python
>>> pyxaf.open(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><auditfile/>')
Traceback (most recent call last):
  ...
pyxaf.errors.ForbiddenConstructError: DOCTYPE/ENTITY declarations are not allowed in an auditfile (refused for security)
```

`pyxaf.validate()` reports the same as an ERROR finding `XAF2002` instead of raising. Refusing the
declarations rejects billion-laughs and XXE files in well under a millisecond, independent of the
Expat version. The check runs during detection too, so `pyxaf.detect()` refuses such files.

## No recover mode

Some XML libraries offer a "recover" mode that skips over errors. On malformed auditfiles it
silently truncates text after a bare `&`, drops the rest of a truncated file, or accepts truncated
amounts, so totals come out wrong without any warning. pyxaf never uses it. Malformed XML raises
`XmlSyntaxError` with the line and column (finding `XAF2001` in `validate()`), after delivering the
transactions before the damage. The [opt-in repairs](guide/reading.md#opt-in-repairs) are narrow,
byte-level fixes for specific, common defects, and each one is reported as a finding.

## Limits

| Option of `pyxaf.open()` | Default | Error |
|---|---|---|
| `max_depth` | 32 levels | `LimitExceededError` |
| `max_text_size` | 10 MiB (10,485,760 characters) per element | `LimitExceededError` |
| `max_decompressed_size` | none (bytes, for gzip/zip input) | `LimitExceededError` |
| `max_findings_per_code`, `max_findings` | 100, 10,000 | further findings are counted, not kept |

Unless `max_decompressed_size` is given (which replaces it, so it can also raise the cap), gzip
and zip input may not inflate to more than **100 times** its compressed size plus 1 MiB; the
decompressed bytes are counted while streaming, so a lying size
in a zip directory does not help an attacker. The guard is the same for paths, bytes and streams:
for a non-seekable stream, whose size is unknown, the ratio applies to the compressed bytes read so
far. A zip archive must contain exactly one file. The RGS
workbook reader caps every part at `max_member_size` (200 MB by default).

`pyxaf.validate()` reports an exceeded limit as finding `XAF2003` (also when it is hit while
detecting the version) and returns the report checked so far. Integers longer than Python's
integer-string conversion limit (4,300 digits by default) are reported as invalid values, not
raised; pyxaf never changes that interpreter-wide limit.

## lxml hardening

The core never uses lxml. The optional XSD validation (`pyxaf[xsd]`) does, and requires
**lxml 6.1.3 or newer**: before 6.1.0, lxml's `iterparse()` and `ETCompatXMLParser` resolved
external entities by default (CVE-2026-41066), and 6.1.3 also fixed the handling of parameter
entities. pyxaf additionally creates every lxml parser, for the bundled schemas and for the file,
with

```text
resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
```

and lxml only sees a file after pyxaf's own engine has accepted it (the XSD layer runs after the
streaming pass). If you use lxml on auditfiles in your own code, use the same options.

## Expat versions

Python's `pyexpat` may be linked against the system's Expat library. Expat versions before 2.7.2
have known denial-of-service weaknesses (see the
[Python XML security notes](https://docs.python.org/3/library/xml.html#xml-security)). Keep
Python and Expat up to date; check with `python -c "import pyexpat; print(pyexpat.EXPAT_VERSION)"`.
pyxaf's refusal of DTDs does not depend on the Expat version.

## Privacy

Auditfiles contain **personal data and confidential financial records**: names and addresses of
customers and suppliers, IBANs, VAT and Chamber of Commerce numbers, salaries in payroll journals,
and the complete bookkeeping of a company. Sharing them can breach the GDPR (AVG), professional
confidentiality and contracts.

- **Never attach a real auditfile** to a GitHub issue, discussion or pull request, not even
  "anonymised" by hand, and do not send one in a security report.
- Report the pyxaf and Python versions, the detected version (`pyxaf detect FILE`), the exporting
  software, the finding codes and messages, and a **minimal, hand-made snippet** that reproduces
  the problem. `CONTRIBUTING.md` explains how to build one.
- Finding messages and `value` fields quote data from the file (account IDs, amounts, names).
  Review a report before you share it.

pyxaf itself runs locally and sends nothing anywhere.
