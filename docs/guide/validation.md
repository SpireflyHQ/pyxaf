# Validation

```python
import pyxaf

report = pyxaf.validate("unbalanced.xaf")
print(report)
```

```text
4.0 — 2 error(s), 0 warning(s), 0 info
- ERROR XAF5009 transactions totalDebit 38260.42 ≠ totalCredit 38261.42
line 223 ERROR XAF5010 transaction '1' in journal 'MEM' does not balance: debit 3612.16, credit 3613.16
```

`pyxaf.validate()` checks an auditfile, or a [multi-file set](reading.md#multi-file-sets), in
**one streaming pass**, so it works on files of any size in constant memory. It needs no
dependencies; only the optional XSD layer uses lxml. The official validation service of the
Belastingdienst (VTS) is available to subscribers only and accepts files up to 5 MB; pyxaf runs
locally on files of several GB and never sends data anywhere.

## Layers

| Layer | Checks | Codes |
|---|---|---|
| L1 encoding | container (gzip/zip), BOM, XML declaration, allowed encodings, windows-1252 bytes in ISO-8859-1, applied repairs | `XAF1xxx` |
| L2 xml | well-formedness, refused DOCTYPE/ENTITY, safety limits | `XAF2xxx` |
| L3 version | version and namespace detection | `XAF3001`–`XAF3007` |
| L4 structure | the version's field catalogue: required and unknown elements, cardinality, order, lengths, code lists, patterns, dates, decimals, integers, ranges; XSD-like, without dependencies | `XAF3010`–`XAF3021`, `XAF3030`, `XAF3050` (ADF) |
| L4x xsd | validation against the bundled official XSD with lxml (optional) | `XAF3040` |
| L5 references | accounts, customers/suppliers, VAT codes and periods referenced by lines and master data exist | `XAF4xxx` |
| L6 totals | control totals and balance, incl. official rules [0004]–[0010]; line counts | `XAF5xxx` |
| L7 uniqueness | IDs and numbers, incl. official rules [0001]–[0003] | `XAF6xxx` |
| L8 data quality | dates outside the fiscal year or period, negative amounts, opening-balance conventions, unusual codes; RGS references | `XAF7xxx`, `XAF8xxx` |

The [code reference](../reference/codes.md) lists every code with its default severity.
`report.checked` names the layers that actually ran, for example
`('L1 encoding', 'L2 xml', 'L3 version', 'L4 structure', 'L5 references', 'L6 totals',
'L7 uniqueness', 'L8 data quality')`. ADF has no element structure, so L4 does not run for it;
its fixed-width field checks are reported as `XAF3050`. For XAF 3.0 and 3.1 the label reads
`L4 structure (derived catalogue)`, see [below](#xaf-30-and-31).

If the XML turns out not to be well-formed, or a limit is exceeded, the problem becomes a finding
(`XAF2001`, `XAF2002`, `XAF2003`) and the report contains what was checked up to that point; the
later layers are not listed in `checked`. `validate()` raises only when the input is not an
auditfile at all (`NotAnAuditfileError`) or cannot be opened.

### Official rules

The XAF 4.0 functional description defines ten numbered consistency rules. pyxaf implements each
of them as a code ending in the rule number:

| Rule | Content | Code |
|---|---|---|
| [0001] | `custSupID` unique | `XAF6001` |
| [0002] | `accID` unique | `XAF6002` |
| [0003] | `RGScode` unique (4.0 only; WARNING, because accounts sharing a code are common) | `XAF6003` |
| [0004] / [0005] | opening balance `totalDebit` / `totalCredit` = sum of `obLine` amounts with D / C | `XAF5004` / `XAF5005` |
| [0006] | opening balance `totalDebit` = `totalCredit` | `XAF5006` |
| [0007] / [0008] | transactions `totalDebit` / `totalCredit` = sum of `trLine` amounts with D / C | `XAF5007` / `XAF5008` |
| [0009] | transactions `totalDebit` = `totalCredit` | `XAF5009` |
| [0010] | each transaction: sum of D = sum of C | `XAF5010` |

pyxaf applies the same checks to the equivalent elements of the other versions (CLAIR2
`numberEntries`/`totalDebit`/`totalCredit`, the ADF header totals). Beyond the numbered rules it
checks the textual rules of the specifications: transaction numbers unique within a journal
(`XAF6004`), line numbers unique within a transaction (`XAF6005`), `linesCount` equal to the number
of lines (`XAF5011`, `XAF5012`), and references to defined master data (`XAF4xxx`). Amounts are
summed exactly as written (amount per written side), as the rules are worded, independent of the
[negative-amount policy](reading.md#negative-amounts).

## The report

`validate()` returns a `ValidationReport`:

| Member | Meaning |
|---|---|
| `ok` | `True` when there is no ERROR finding |
| `findings` | all kept findings, most severe first, then in file order |
| `errors`, `warnings` | the ERROR and WARNING findings |
| `max_severity` | the highest severity, or `None` without findings |
| `counts` | occurrences per code, **including** findings suppressed by limits |
| `suppressed` | number of findings dropped because of limits |
| `checked` | the layers that ran |
| `stats` | counts gathered during the pass: `transactions`, `lines`, `accounts`, `relations`, `vat_codes`, `periods`, `journals`, `opening_balance_lines` |
| `format` | the [`FormatInfo`](versions.md#how-detection-works) of the (first) file |
| `rules` | the rule set used |
| `to_dict()`, `to_json(**kwargs)` | JSON-serialisable form; `to_json` passes `kwargs` such as `indent` to `json.dumps` |
| `str(report)` | the text summary shown above |

Each `Finding` has `code`, `severity` (`Severity.INFO < WARNING < ERROR`), `message` (English),
`file` (index in a multi-file set), `line` (1-based) and `column` where known, `path` (element
path, or the ADF field), `value` (the offending raw value, truncated to 200 characters) and
`rule_ref` (e.g. `"[0009]"`).

```python
report = pyxaf.validate("unbalanced.xaf")
if not report.ok:
    for f in report.errors:
        print(f.code, f.line, f.message, f.value)
print(report.counts)  # {'XAF5010': 1, 'XAF5009': 1}
print(report.stats["lines"])  # 36
```

`to_dict()` has a stable layout with `schema_version` 1:

```json
{
  "schema_version": 1,
  "format": {
    "family": "xaf", "version": "4.0",
    "namespace": "http://www.odb.belastingdienst.nl/Belastingdienst/BCPP/1.1/structures/XmlauditfileXAF_4.0",
    "namespace_status": "official", "encoding": "utf-8", "bom": false,
    "confidence": 1.0, "reasons": ["official namespace of XAF 4.0"]
  },
  "ok": false,
  "rules": "spec",
  "checked": ["L1 encoding", "L2 xml", "L3 version", "L4 structure", "L5 references",
              "L6 totals", "L7 uniqueness", "L8 data quality"],
  "stats": {"transactions": 12, "lines": 36, "accounts": 11, "relations": 3, "vat_codes": 2,
            "periods": 12, "journals": 3, "opening_balance_lines": 3},
  "counts": {"XAF5010": 1, "XAF5009": 1},
  "suppressed": 0,
  "findings": [
    {"code": "XAF5009", "severity": "ERROR",
     "message": "transactions totalDebit 38260.42 ≠ totalCredit 38261.42",
     "file": 0, "line": null, "column": null, "path": null, "value": null, "rule_ref": "[0009]"}
  ]
}
```

## Options

```python
pyxaf.validate(
    source,  # path, bytes, binary stream, or a list for a multi-file set
    xsd=False,  # also validate against the official XSD (pyxaf[xsd])
    rules="spec",  # or "vts"
    rgs=None,  # an RgsSchema to check RGS codes against
    encoding=None,  # override the declared encoding
    repair=(),  # opt-in repairs, see the reading guide
    negative_amounts="flip",  # sign policy, see the reading guide
    severity_overrides=None,  # {"XAF7002": Severity.INFO, "XAF7001": None}
    max_findings_per_code=100,
    max_findings=10_000,
)
```

### Rule sets

| `rules=` | Reports |
|---|---|
| `"spec"` (default) | everything pyxaf checks, per the detected version |
| `"vts"` | only what the Belastingdienst's validation service checks: encoding and XML (`XAF1xxx`, `XAF2xxx`), schema structure (`XAF3010`–`XAF3021`, `XAF3040`) and the numbered 4.0 rules (`XAF5004`–`XAF5010`, `XAF6001`–`XAF6003`) |

The `vts` rule set approximates VTS so that you can predict its verdict before uploading; the
consistency rules VTS applies are not published in full, so it cannot be an exact replica. The
same checks run in both rule sets; `vts` filters the report.

### Severities and limits

Each code has a default severity ([code reference](../reference/codes.md)). The validator adjusts
some of them to the detected version: for XAF 3.0/3.1, unknown and missing elements are WARNING
instead of ERROR; a missing opening balance (`XAF7006`) is a WARNING for 4.0 and INFO otherwise;
an unverified namespace (`XAF3004`) is INFO. Your overrides win over both:

```python
from pyxaf import Severity

report = pyxaf.validate(
    "2024.xaf",
    severity_overrides={
        "XAF5010": Severity.WARNING,  # downgrade
        "XAF7010": None,  # silence
    },
)
```

`max_findings_per_code` (default 100) and `max_findings` (default 10,000) keep reports of files
with millions of problems small. Suppressed findings are still counted: `report.counts` has the
real number per code and `report.suppressed` the number dropped.

### XSD validation

With `pyxaf[xsd]` installed, `xsd=True` adds layer L4x: validation against the bundled official
XSD of CLAIR2, 3.2, 3.2.1 or 4.0 with lxml, using hardened parser options (see
[Security](../security.md#lxml-hardening)). Inputs up to 64 MiB are validated as a whole and
every schema error is reported; larger inputs are validated streaming, which stops at the first
schema error. No XSD exists for ADF, 3.0 and 3.1 (an INFO `XAF3040` says so). A file whose
namespace is not the XSD's target namespace, such as the 4.0 Toelichting variant or a bogus
namespace, cannot validate and gets one ERROR `XAF3040` instead of hundreds of follow-up errors.

The dependency-free L4 layer covers the same structural rules (required elements, cardinality,
order, lengths, enumerations, patterns, types) from catalogues generated from the same XSDs, so
the XSD layer is mostly a second opinion with the official tool chain.

### XAF 3.0 and 3.1

No official XSD of 3.0 or 3.1 is publicly available; pyxaf's catalogues for them are derived (see
[Versions](versions.md#xaf-30-and-31)). Unknown elements (`XAF3011`) and missing required
elements (`XAF3010`) are reported as WARNING, and element order (`XAF3013`) is not checked.

### RGS

Pass an [`RgsSchema`](rgs.md) to check every account's RGS code against an official RGS release
(`XAF8003`–`XAF8006`); `report.checked` then includes `L8 RGS`. Placeholder and unrecognisable RGS
codes (`XAF8001`, `XAF8002`) are reported without a schema.

## Interpreting results

A report full of findings does not necessarily mean the file is wrong. Experience with real files:

- **Official test files violate the official rules.** The 3.2, 3.2.1 and 4.0 test files published
  with the specifications only exercise the XSD: they contain wrong totals, unbalanced
  transactions and references to undefined accounts, VAT codes and customers/suppliers.
- **Control totals are usually right; structure varies wildly.** Real files are internally
  consistent on totals far more often than they agree on opening-balance conventions, period
  numbering or encodings.
- **Opening balances often do not balance** (`XAF5013`), are missing (`XAF7006`), or are given
  both as element and as period-0 transactions (`XAF7004`). Check before adding them to balances.
- **Dates**: effective dates outside the fiscal year (`XAF7010`) are common and often legitimate
  (invoice dates of the previous year). A `fiscalYear` that matches neither the start nor the end
  date's year is reported as `XAF7007`; broken fiscal years (February to January) are often
  written as a single year, which is accepted.
- Known vendor quirks (from public exporter code and sample files):
    - Uniconta (4.0): opening-balance `linesCount` one too high (`XAF5011`), `RGSVersion` `RGS-1`
      (`XAF8005` with a schema), `RGScode` `0000` fallback (`XAF8001`), offset account `9999`
      (`XAF4005` when it is not defined), `-` for an empty commerce number.
    - Odoo (Enterprise and OCA): customer/supplier types C and S swapped (not detectable), period
      numbers such as `01` or `501`, `accTp` `M` for equity, no XML declaration (`XAF1002`).
    - Microsoft Business Central: windows-1252 encoding (`XAF1003`), no `jrnTp`.
    - Exact Online: negative quantities that fail the XSD, UTF-8 BOM (`XAF1001`).
    - Banana (CLAIR2): `numberEntries` counts transactions instead of lines (`XAF5012`, with a hint),
      negative debit/credit amounts.
    - osFinancials: no XML declaration, header elements out of order, `custSupTp` `D`.
    - AFAS: amounts with fewer than two decimals (`121`, `609.9`; valid), period numbers without a
      `periods` element.
- **Encoding problems** are the most common reason a file cannot be read at all; see
  [Encodings](reading.md#encodings) for the opt-in repairs.

A practical approach: start with `rules="vts"` to see what the Belastingdienst would object to,
then read the `spec` report for what affects your analysis, and silence codes you have decided to
accept with `severity_overrides`.
