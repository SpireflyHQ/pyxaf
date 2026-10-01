# RGS (Referentie Grootboekschema)

The *Referentie Grootboekschema* (RGS, reference chart of accounts) is a Dutch standard list of
general-ledger codes such as `BLimBan` (balance sheet › liquid assets › bank). Accounting software
maps its own accounts to RGS codes so that reports can be compared and generated automatically.
Auditfiles carry this mapping: XAF 4.0 in `ledgerAccount/RGScode` (plus `header/RGSVersion`),
XAF 3.2 and 3.1 through taxonomy elements.

pyxaf reads the mapping from every version into `LedgerAccount.rgs` and, given an official RGS
release, checks that the codes exist.

## Why pyxaf does not bundle RGS

RGS is maintained by the Taakgroep RGS and published as an Excel workbook (for example
`RGS 3.8-def.xlsx`) on [referentiegrootboekschema.nl](https://www.referentiegrootboekschema.nl/).
No licence or reuse statement exists for the RGS data, so pyxaf does not redistribute it. Download
the release you need yourself and load it with `pyxaf.rgs.load_excel()`. pyxaf is not affiliated
with the Taakgroep RGS.

## Loading the Excel release

```python
import pyxaf.rgs

schema = pyxaf.rgs.load_excel("RGS 3.8-def.xlsx")
schema.version  # '3.8'
len(schema)  # number of reference codes
"BLimBan" in schema  # True
```

The workbook is read with a small, hardened `.xlsx` reader from the standard library, so no extra
is needed. `load_excel()` finds its way around the workbook automatically:

- **Main sheet**: among the sheets with a `Referentiecode` column header in their first ten rows
  (and only one such column), sheet names starting with `Totaal` are preferred, then the sheet
  with the most rows. Override with `sheet="..."`.
- **Renames**: a sheet with several `Referentiecode` columns, one per release (such as
  `RGS3.8-versus-RGS3.7`), provides `schema.rename_map` (old code → current code). Override with
  `rename_sheet="..."`.
- **Version**: from the title cell above the header (`RGS3.8`), the sheet name, the file name or
  the `Recap` sheet, in that order.
- Header matching ignores case, spaces and punctuation; cell values are kept exactly as Excel
  stored them (nothing is converted to numbers).

`load_excel()` accepts a path or a seekable binary stream. It raises `XlsxError` when the file is
not an `.xlsx` workbook or has no sheet with RGS codes, `KeyError` for a named sheet that does not
exist, and refuses DOCTYPE/ENTITY declarations and parts larger than `max_member_size` (200 MB by
default) like the auditfile reader does.

## The `RgsSchema` API

| Member | Meaning |
|---|---|
| `version` | the RGS version (`"3.8"`) or `None` |
| `codes` | `RgsCode` objects by reference code, in sheet order |
| `rename_map` | old code → code in this version (only codes that no longer exist) |
| `duplicate_codes` | codes that occurred more than once in the sheet (the first wins) |
| `code in schema`, `len(schema)`, `iter(schema)` | membership, size, iteration over `RgsCode` |
| `resolve(code)` | the `RgsCode`, following `rename_map` for renamed codes, or `None` |
| `parent(code)` | the longest proper prefix of `code` that is a code; works for unknown codes too (nearest existing ancestor) |
| `children(code)` | the direct children, in sheet order |

Each `RgsCode` has `code`, `level` (1–5), `description`, `short_description`, `debit_credit`
(`"D"`/`"C"`), `sort_key`, `reference_number` (as stored; formats are irregular),
`opposite_code` (the code used when the balance flips sides, *ReferentieOmslagcode*), `entities`
(the entity-filter columns set for the code, such as `Basis`, `ZZP`, `BV`) and
`elimination_filters`.

RGS codes form a strict prefix hierarchy: `B` (balance sheet) or `W` (profit and loss) followed by
three-character segments. Level 4 codes are accounts, level 5 codes are mutations (`…Beg`, `…Inv`).

You can also build a schema yourself, for example from your own reference table:

```python
from pyxaf.rgs import RgsCode, RgsSchema

schema = RgsSchema(
    [
        RgsCode(code="B", level=1, description="Balans"),
        RgsCode(code="BLim", level=2, description="Liquide middelen"),
        RgsCode(code="BLimBan", level=3, description="Banken"),
        RgsCode(code="BLimBanRba", level=4, description="Rekening-courant bank"),
    ],
    version="3.8",
    rename_map={"BLimBnk": "BLimBan"},
)
schema.parent("BLimBanRba").code  # 'BLimBan'
[c.code for c in schema.children("BLim")]  # ['BLimBan']
schema.resolve("BLimBnk").code  # 'BLimBan'
```

## Validating RGS references

```python
report = pyxaf.validate("2024.xaf", rgs=schema)
print(report.checked[-1])  # 'L8 RGS'
```

or, on an open file, without the other layers:

```python
from pyxaf.rgs import validate_refs

with pyxaf.open("2024.xaf") as af:
    for finding in validate_refs(af, schema):
        print(finding)
```

| Code | Severity | Meaning |
|---|---|---|
| `XAF8001` | WARNING | placeholder value instead of an RGS code (reported without a schema too) |
| `XAF8002` | WARNING | value does not look like an RGS reference code (reported without a schema too) |
| `XAF8003` | ERROR | code does not exist in the loaded RGS version; the message names the nearest existing ancestor |
| `XAF8004` | WARNING | code was renamed in a later version; the message names the new code |
| `XAF8005` | INFO | `header/RGSVersion` cannot be recognised, or differs from the loaded version |
| `XAF8006` | INFO | account mapped to a level other than 4 or 5 |
| `XAF6003` | WARNING | (4.0, rule [0003]) the same RGS code is used by more than one account |

Rule [0003] makes RGS codes unique per account; since several accounts often map to one RGS code,
the RGS guidance lets software append an **extension** after the reference code. pyxaf splits it
off:

```python
from pyxaf.rgs import parse_ref, parse_version

parse_ref(
    " BLimBanRba.01 "
)  # RgsRef(raw=' BLimBanRba.01 ', code='BLimBanRba', extension='01', ...)
parse_ref("0000").placeholder  # True
parse_version("RGS 3.8-def")  # '3.8'
```

`RGSVersion` has no specified format; `3.7`, `RGS 3.8` and `RGS-1` all occur. pyxaf parses it
leniently with `parse_version()`.

## Placeholders

Exporters write a dummy value when no RGS code is available. These values (compared
case-insensitively, after trimming) are recognised as placeholders: an empty value, `0`, `00`,
`000`, `0000`, `0001`, `-`, `RGS-1`, `nvt`, `n.v.t.`. They get `RgsRef.placeholder = True` and
`code = None`, and are reported as `XAF8001` rather than as unknown codes.

## RGS in XAF 3.x

XAF 3.x has no `RGScode` element. pyxaf takes the code from, in this order:

1. **3.2**: `ledgerAccount/taxonomy/entryPoint/conceptRef` — the official way since the 2017
   addendum (`taxoRef` holds the RGS taxonomy namespace, `conceptRef` the code; pyxaf keeps the
   local part of a QName or URI). `RgsRef.source` is `"taxonomy"`.
2. **3.2, vendor conventions**: `leadReference`, `leadCode` or `leadCrossRef`, but only when the
   value parses as an RGS code (starts with `B` or `W`, at least four characters). The `lead*`
   fields are officially "not for RGS", but some exporters use them. `source` names the element.
3. **3.1**: `generalLedger/taxonomies/taxonomy/taxoElement` (`glAccID` → `txCd`);
   `source` is `"taxonomies"`.

Whitespace around the value is ignored; the written value stays in `RgsRef.raw`. CLAIR2 and ADF
carry no RGS references.
