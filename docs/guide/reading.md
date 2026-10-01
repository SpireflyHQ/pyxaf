# Reading auditfiles

```python
import pyxaf

with pyxaf.open("2024.xaf") as af:
    print(af)  # <AuditFile '2024.xaf' version=4.0 company='…'>
    print(af.header.start_date, af.header.end_date, af.header.currency)
    for line in af.lines():
        print(line.account_id, line.signed_amount, line.description)
```

`pyxaf.open()` works the same for every iteration: ADF, CLAIR2 and XAF 3.0–4.0. The result is an
[`AuditFile`](../reference/api.md#pyxaf.reader.AuditFile) with a normalized, English-named model; the
[field mapping](versions.md#normalized-field-mapping) shows which element of each version ends up
in which attribute.

## Sources

`pyxaf.open(source, ...)` accepts:

| Source | Notes |
|---|---|
| `str` or `os.PathLike` | a path; opened in binary mode, re-opened for every pass |
| `bytes`, `bytearray`, `memoryview` | the whole file in memory |
| binary file object | anything with `read()` returning `bytes`, e.g. `open(p, "rb")`, `io.BytesIO`, an upload stream; seekable streams can be read repeatedly, non-seekable ones once (see [below](#re-iteration-and-one-shot-streams)); pyxaf never closes a stream you pass |
| a sequence of the above | a [multi-file set](#multi-file-sets) |

Compression is recognised by its magic bytes, not by the file name: **gzip** files and **zip**
archives with exactly one member are decompressed transparently while streaming (finding
`XAF1020`, INFO). A zip archive must be given as a path, bytes or seekable stream.

Encrypted or vendor-compressed auditfiles (`.xac`, `.xsc`/`.pbl.xsc`, Exact `.XFC`/`.ADC`, and
Microsoft CAB archives from Exact "Verdichten") cannot be read by anyone but the intended
recipient; pyxaf recognises them and raises `EncryptedAuditfileError` with an explanation.

Use the `AuditFile` as a context manager, or call `close()`. pyxaf opens files for each pass and
closes them afterwards; `close()` releases a parser paused after reading the master data.

### Errors

pyxaf raises exceptions only for input it cannot read at all. Everything else becomes a
[finding](#findings-while-reading).

| Exception | When |
|---|---|
| `NotAnAuditfileError` | neither XML with an `<auditfile>` root nor ADF; also a continuation file passed without its first file |
| `EncryptedAuditfileError` | an encrypted or vendor-compressed auditfile (subclass of `NotAnAuditfileError`) |
| `XmlSyntaxError` | the XML is not well-formed; raised when the parser reaches the damage, with `line`, `column` and `file` |
| `ForbiddenConstructError` | a DOCTYPE or ENTITY declaration (subclass of `XmlSyntaxError`); see [Security](../security.md) |
| `LimitExceededError` | a [safety limit](#safety-limits) was exceeded |
| `MissingExtraError` | an optional feature needs an extra; the message names it |
| `FileNotFoundError`, `TypeError` | a missing path; a text-mode stream or unsupported source type |

All pyxaf exceptions derive from `pyxaf.PyxafError`. Because transactions are parsed lazily,
an `XmlSyntaxError` in the middle of a file is raised while you iterate, after the transactions
before the damage have been delivered.

## Master data and streamed transactions

When a file is opened, pyxaf parses it up to the first journal or transaction and keeps the
master data in memory:

| Attribute | Content |
|---|---|
| `af.format` | [`FormatInfo`](versions.md#how-detection-works): family, version, namespace status, encoding, confidence, reasons |
| `af.version` | the detected `Version`; compares equal to its string (`af.version == "4.0"`) |
| `af.header` | `Header`: fiscal year (a string: `"2024"` or `"2023-2024"`), start/end date, currency, creation date, software, RGS version |
| `af.company` | `Company`: name, identifier, commerce number, tax registration, addresses |
| `af.accounts` | ledger accounts by ID (`LedgerAccount`, with `rgs`) |
| `af.relations` | customers/suppliers by ID (`Relation`) |
| `af.vat_codes` | VAT codes by ID (`VatCode`) |
| `af.periods` | periods by period key (`Period`) |
| `af.journals` | journals by ID (`Journal`); journals enclose their transactions in XML, so the first access may scan the file (cheaply, skipping transaction content) |
| `af.transaction_totals` | declared control totals (`linesCount`/`numberEntries`, `totalDebit`, `totalCredit`), summed over a multi-file set |
| `af.files` | names of the underlying files |
| `af.findings` | findings collected so far |

The mappings are read-only. For duplicate IDs the first definition wins and a finding
(`XAF6001`, `XAF6002`, …) is recorded. ADF has no master-data section: its accounts, relations
and journals are collected from the mutation lines.

Code lists are kept as data, not closed enums: `account.account_type` is an `AccountType` with
the written `code` and an interpreted `kind` (`BALANCE`, `PROFIT_LOSS`, `MIXED`, `UNKNOWN`);
`journal.journal_type` and `relation.relation_type` work the same way (`JournalKind`,
`RelationKind`). Free-text types of CLAIR2 and ADF are interpreted heuristically; unknown values
map to `UNKNOWN` and the written code stays available.

## Transactions and lines

```python
with pyxaf.open("2024.xaf") as af:
    for tx in af.transactions():
        print(tx.journal_id, tx.number, tx.period_key, tx.date, tx.description)
        print(tx.total_debit, tx.total_credit, tx.balanced)
        for line in tx.lines:
            print(line.number, line.account_id, line.amount, line.side, line.signed_amount)

    for line in af.lines():  # flattened; each line repeats its transaction context
        print(line.journal_id, line.transaction_number, line.period_key, line.account_id)
```

`transactions()` yields `Transaction` objects in file order; `lines()` yields their `Line`
objects. A line carries its transaction context (`journal_id`, `transaction_number`,
`transaction_seq`, `period_key`, `transaction_date`) so you can process lines without keeping
transactions around.

Amounts on a line:

| Attribute | Meaning |
|---|---|
| `amount` | the value as written, may be negative (`Decimal`) |
| `side` | the written debit/credit indicator, `Side.DEBIT` or `Side.CREDIT` (`"D"`/`"C"`) |
| `signed_amount` | debit-positive, after the [negative-amount policy](#negative-amounts) |
| `debit`, `credit` | non-negative presentation of `signed_amount`; one of them is zero |

All four are `None` when the written amount is invalid (`NaN`, a decimal comma, …); a finding
records the raw value. Amounts are `decimal.Decimal` and never rounded: files write `121` and
`609.9` as well as `121.00`. Other line attributes include `description`, `document_ref`,
`effective_date`, `relation_id`, `invoice_ref`, `cost_center`, `product`, `project`, `quantity`,
`vat` (a tuple of `VatLine`), and `foreign` (`ForeignAmount`). Every transaction and line also has
a synthetic `seq` (its position in the file or set) and `file` (index in a multi-file set),
because real files contain duplicate transaction and line numbers.

Dates are `datetime.date`. A time-zone suffix (`2024-01-15+01:00`) is removed with an INFO
finding (`XAF7008`); an invalid date becomes `None` with an ERROR finding.

### Re-iteration and one-shot streams

Every call of `transactions()` or `lines()` starts a new pass that re-reads the source from the
start; nothing is cached, which is what keeps memory constant. The first pass continues the parser
that read the master data. Stopping a loop early is fine, and so is iterating several times:

```python
with pyxaf.open("2024.xaf") as af:
    first = next(af.transactions())  # stop after one transaction
    n_lines = sum(1 for _ in af.lines())  # a new, complete pass
    accounts_used = {ln.account_id for ln in af.lines()}  # and another one
```

A non-seekable stream (a pipe, a socket, a one-shot upload) can be read **once**: opening plus a
single pass over the transactions works, a second pass raises `PyxafError` ("the input stream can
only be read once"). Pass a path, bytes or a seekable stream when you need several passes;
`opening_balance(strategy="transactions")`, `af.tables` and `af.journals` may also need a pass of
their own.

## Opening balance

The opening balance lives in different places depending on the version and the software:

- the `openingBalance` element with `obLine`s (XAF 3.x and 4.0);
- transactions in period `0`, or in journals of type `O` (opening balance), in XAF 3.x;
- period-0 mutation lines in ADF and period `"0"` transactions in CLAIR2.

XAF 4.0 allows only the element. `opening_balance()` returns one unified view:

```python
with pyxaf.open("2024.xaf") as af:
    ob = af.opening_balance()  # strategy="auto"
    print(ob.source)  # 'element', 'transactions' or 'none'
    print(ob.total_debit, ob.total_credit, ob.balanced)
    print(ob.by_account())  # {'1100': Decimal('10000.00'), ...}
    print(ob.declared)  # declared totals of the element, if any
```

| `strategy=` | Behaviour |
|---|---|
| `"auto"` (default) | the element when the file has one, otherwise the transactions |
| `"element"` | only the `openingBalance` element (`source="none"` if absent) |
| `"transactions"` | only lines of transactions in period `0`/`00`/`000` or in journals of type `O`; scans the file |

`OpeningBalance.lines` are ordinary `Line` objects, signed according to the
[negative-amount policy](#negative-amounts); `by_account()` sums them per account
(debit-positive). The element's `opBalDate`/`opBalDesc` (3.x) are available as `date` and
`description`. The validator reports a missing opening balance (`XAF7006`), one given both ways
(`XAF7004`, double-counting risk), opening-balance transactions in a 4.0 file (`XAF7005`) and an
unbalanced opening balance (`XAF5013`, common in real files).

## Negative amounts

XAF writes an amount plus a debit/credit indicator. Some software writes negative amounts, and
readers disagree on what `-100 C` means. The `negative_amounts=` option decides:

| Policy | `-100` with `C` | Rationale |
|---|---|---|
| `"flip"` (default) | counts as 100 **debit**: the sign is applied to the side | the 4.0 XSD allows signed amounts, and the documentation shows signed values |
| `"abs"` | counts as 100 **credit**: the sign is ignored | what some importers do |

Each negative amount is reported as an INFO finding (`XAF7001`) either way. `amount` always keeps
the written value; only `signed_amount`, `debit` and `credit` depend on the policy.

Worked example: a sales booking whose revenue line was written as `-2985.26 D` instead of
`2985.26 C`.

```xml
<trLine><nr>1</nr><accID>1300</accID>…<amnt>3612.16</amnt><amntTp>D</amntTp></trLine>
<trLine><nr>2</nr><accID>8000</accID>…<amnt>-2985.26</amnt><amntTp>D</amntTp></trLine>
<trLine><nr>3</nr><accID>1800</accID>…<amnt>626.90</amnt><amntTp>C</amntTp></trLine>
```

```python
for policy in ("flip", "abs"):
    with pyxaf.open("negative.xaf", negative_amounts=policy) as af:
        tx = next(af.transactions())
        for ln in tx.lines:
            print(policy, ln.account_id, ln.amount, ln.side, ln.signed_amount, ln.debit, ln.credit)
        print(policy, tx.balanced)
```

| Policy | Line | `amount` | `side` | `signed_amount` | `debit` | `credit` |
|---|---|---:|---|---:|---:|---:|
| `flip` | 1300 | 3612.16 | D | 3612.16 | 3612.16 | 0 |
| `flip` | 8000 | -2985.26 | D | -2985.26 | 0 | 2985.26 |
| `flip` | 1800 | 626.90 | C | -626.90 | 0 | 626.90 |
| `abs` | 1300 | 3612.16 | D | 3612.16 | 3612.16 | 0 |
| `abs` | 8000 | -2985.26 | D | 2985.26 | 2985.26 | 0 |
| `abs` | 1800 | 626.90 | C | -626.90 | 0 | 626.90 |

With `flip` the transaction balances (debit 3612.16 = credit 2985.26 + 626.90, so
`tx.balanced` is `True`); with `abs` it does not (debit 6597.42, credit 626.90). For CLAIR2 and
ADF, which have separate debit and credit amounts, `signed_amount` is debit minus credit under
`flip`.

!!! note
    The policy shapes the values you read. The validator's control-total and balance rules
    ([0004]–[0010]) sum the written amounts per written side, as the rules are worded, so they
    give the same result under both policies (here: balanced).

## The raw layer

Normalization never loses information. Every normalized object keeps the
[`RawRecord`](../reference/api.md#pyxaf.raw.RawRecord) it was built from in `.raw`: the element with its leaf
children as `fields` (exact text, whitespace preserved, empty elements as `""`) and complex
children as `children`, plus its line number. Unknown and vendor-specific elements are kept too.

```python
with pyxaf.open("vendor.xaf") as af:
    line = next(af.lines())
    line.raw.tag  # 'trLine'
    line.raw.line  # 290 (1-based line of the start tag)
    line.raw.fields["amnt"]  # the amount exactly as written
    line.raw.get("matchKeyID")  # 'M-17'
    line.extra  # {'matchKeyID': 'M-17', 'vendorFlag': 'x'}

    tx = next(af.transactions())
    tx.raw.all("trLine")  # all <trLine> children (RawRecord objects)
    tx.raw.child("trLine")  # the first one
    af.accounts["1100"].raw.to_dict()  # plain dict, children as lists of dicts
```

To stream raw records without normalization at all, which is also the fastest way through a
file, use `af.raw`:

```python
with pyxaf.open("vendor.xaf") as af:
    af.raw.header.fields["fiscalYear"]  # '2024'
    af.raw.company.fields["companyName"]  # CLAIR2: the header record; ADF: the header line
    for journal_id, tx in af.raw.transactions():  # ('MEM', RawRecord('transaction', ...))
        for line in tx.all("trLine"):
            ...
```

For ADF, `af.raw.transactions()` yields one record per mutation line (tag `adfLine`, fields named
after the ADF fields, such as `dagboekcode` and `grootboekrekeningcode`).

`Line.extra` and `Transaction.extra` return the raw fields that have no normalized attribute:
version-specific (`matchKeyID` in 3.2) or vendor-specific elements. For other objects use
`.raw.fields` directly. If a leaf element occurs more than once, `fields` holds the first
occurrence and `children[name]` all of them.

## Periods are opaque keys

Period numbers are strings, and real files use many schemes: `1`–`12`, `01`–`12`, `0` for the
opening balance, `55`, `501` (year digit + month), week numbers. pyxaf never assumes 1–12:

- `Transaction.period_key` and `Period.key` are the period number **as written**;
- `Transaction.period_number` and `Period.number` are its integer value when numeric, else `None`;
- `af.periods` is keyed by `Period.key`.

The validator looks a transaction's period up in the defined periods by key and, failing that,
by its numeric value (a transaction in period `"01"` matches a defined period `"1"`). Some files
have no `periods` element at all; then nothing is checked.

## Multi-file sets

Large auditfiles are split in one of two ways, and `pyxaf.open()` accepts both as a list (in
order):

```python
# 1. one document split into continuation files ("Vervolgbestand x van y")
with pyxaf.open(["big.xaf", "big-2.xaf", "big-3.xaf"]) as af:
    ...

# 2. complete auditfiles for shorter periods (XAF 4.0 allows this)
with pyxaf.open(["2024-h1.xaf", "2024-h2.xaf"]) as af:
    ...
```

**Continuation files** (3.2 addendum 2017, XAF 4.0 §1.4): the first file holds the header, the
master data and the start of the transactions; each following file starts with the comment
`<!-- Vervolgbestand x van y -->` and holds only transactions. pyxaf strips the continuation
files' BOM, XML declaration and leading comments and parses the set as one document. Passing a
continuation file without the first file raises `NotAnAuditfileError`. Such fragments cannot be
XSD-validated individually; validate the whole set.

**Per-period files**: each is a complete auditfile that repeats the master data. pyxaf reads the
master data of every file: the first definition of an ID wins, and a different definition in a
later file is reported as `XAF6010`. Transactions are concatenated in the order of the list;
`Transaction.file`/`Line.file` give the file index and `seq` continues across files. Control
totals are summed (`af.transaction_totals`); the opening-balance element is taken from the first
file.

ADF files cannot be combined into a set.

## Encodings

pyxaf determines the encoding the way XML parsers must: byte-order mark, then the XML declaration,
then UTF-8. ADF files have no declaration and are decoded as windows-1252 (DOS-era files).

| Situation | Finding |
|---|---|
| UTF-8 BOM (common: Exact, Twinfield, Asperion) | `XAF1001` INFO |
| no XML declaration (osFinancials, Odoo) | `XAF1002` WARNING; read as UTF-8 |
| encoding other than UTF-8 or ISO-8859-1 in 3.2/3.2.1/4.0 (e.g. windows-1252) | `XAF1003` ERROR; the file is still read |
| declared ISO-8859-1 but contains bytes 0x80–0x9F (really windows-1252) | `XAF1005` WARNING, with a hint |

Bytes 0x80–0x9F are invisible control characters in ISO-8859-1 but typographic characters (`€`,
`‘`, `–`) in windows-1252, so a mis-declared file decodes "successfully" into garbage. pyxaf
detects this and suggests a fix, but does not change the text unless you ask:

```python
af = pyxaf.open("file.xaf", encoding="cp1252")  # override (XAF1004 INFO)
af = pyxaf.open("file.xaf", repair={"latin1-as-cp1252"})  # only for declared ISO-8859-1
```

### Opt-in repairs

Some files are not well-formed XML. pyxaf refuses them with an `XmlSyntaxError` whose message
points at the usual causes, and offers narrow, opt-in repairs. Each repair that changes something
is reported as a WARNING finding.

| `repair=` | Fixes | Finding |
|---|---|---|
| `"control-chars"` | removes XML-illegal C0 control characters (all below 0x20 except tab, LF, CR) | `XAF1010` |
| `"latin1-as-cp1252"` | decodes a declared ISO-8859-1 file as windows-1252 | `XAF1011` |
| `"bare-ampersand"` | escapes `&` that starts no entity or character reference (`R&D` → `R&amp;D`) | `XAF1012` |

```python
af = pyxaf.open("file.xaf", repair={"control-chars", "bare-ampersand"})
```

Repairs apply to ASCII-compatible encodings (not UTF-16/32). Data after the end of the root
element (such as a DOS end-of-file character) is ignored automatically with `XAF1013`. pyxaf never
uses a parser "recover" mode, which silently truncates data.

## Findings while reading

Reading already reports what it notices: format problems, invalid values, negative amounts,
duplicate IDs. `af.findings` returns the findings collected so far; it grows while you iterate,
because transactions are parsed lazily. For a complete check, use
[`pyxaf.validate()`](validation.md), which runs all layers in one pass.

```python
with pyxaf.open("2024.xaf", severity_overrides={"XAF7001": None}) as af:  # silence a code
    for line in af.lines():
        ...
    for finding in af.findings:
        print(finding)  # e.g. 'line 235 ERROR XAF3018 <amnt> is not a valid decimal number'
```

`max_findings_per_code` (default 100) and `max_findings` (default 10,000) bound the memory used
by findings on files with millions of problems; `severity_overrides` changes a code's severity or
silences it with `None`.

## Safety limits

| Option | Default | Protects against |
|---|---|---|
| `max_depth` | 32 | deeply nested elements (XAF needs at most 8 levels) |
| `max_text_size` | 10 MiB (10,485,760 characters) | huge text nodes |
| `max_decompressed_size` | none | gzip/zip archives that inflate too far |

Unless `max_decompressed_size` is given (it replaces this guard), a gzip or zip input may not
inflate to more than 100 times its compressed size plus 1 MiB (a decompression-bomb guard). Exceeding a limit raises `LimitExceededError`. See
[Security](../security.md).
