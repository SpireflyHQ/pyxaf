# Versions of the auditfile

The *Auditfile Financieel* (auditfile for financial records) is a Dutch standard for exporting a
general ledger: master data, opening balance and all journal entries of one fiscal year. Tax
inspectors and auditors request it from the company's accounting software. Over 25 years it went
through seven iterations; pyxaf reads all of them into the same model.

## History

| Iteration | Year | Structure | Specification |
|---|---|---|---|
| **ADF** ("Auditfile 1.0", `CLAIR1.00.00`) | 1999 | fixed-width ASCII, CR LF lines: one header line, then one line per mutation (32 fields); no master-data section | Belastingdienst *Handleiding Auditfile* (2001, erratum 2003) |
| **CLAIR2** (`CLAIR2.00.00`; also called "XML Auditfile 2.0" or "XAF 1.0") | 2003 | first XML version, **no namespace**; company data in `header`; separate `debitAmount`/`creditAmount`; one `vat` per line | official XSD (2003) and manual; English OECD translation fed into SAF-T |
| **XAF 3.0** | ~2008 | `auditfile/company/…` structure with `trLine`, `amnt` + `amntTp`, subadministrations and an `openingBalance` | XSD not publicly available |
| **XAF 3.1** | ~2010 | adds `custSupTp`; RGS links in `generalLedger/taxonomies` | XSD not publicly available |
| **XAF 3.2** | 2014, addendum 2017 | adds master-data history, `lead*` fields, RGS via `taxonomy/entryPoint/conceptRef`, `matchKeyID` and more; the 2017 addendum adds file-splitting rules | official XSD, functional description, explanation, test file |
| **XAF 3.2.1** | 2024 | same structure as 3.2 with a new namespace, an updated ISO 4217 currency list and minimum values for counts and quantities | official XSD and test file (package "XAF-3.2.2") |
| **XAF 4.0** (current release 4.0.3) | 2025 | streamlined: 111 elements instead of 298; adds `Commercenr`, `RGSVersion`/`RGScode`, customer/supplier balances, `settDate`, `Source`/`User`; drops postal addresses, history and subledgers | public package from the ODB: XSD, functional description, explanation, test file |

!!! warning "Phase-out of older versions"
    The Belastingdienst (ODB notice of 22 April 2026) accepts **only XAF 4.0 from 1 January
    2027**. Files in older iterations remain in archives for years, so pyxaf keeps reading and
    validating all of them. RGS is not a blocker for 4.0: `RGScode` must be delivered only when the
    software has RGS data.

## Identification and namespaces

| Signal | Meaning |
|---|---|
| first 12 bytes `CLAIR1.00.00` (after an optional BOM) | ADF |
| no namespace and `header/auditfileVersion` = `CLAIR2.00.00` | CLAIR2 |
| `http://www.auditfiles.nl/XAF/3.0` | XAF 3.0 (plausible, but no official source could be found) |
| `http://www.auditfiles.nl/XAF/3.1`, `…/XAF/3.2` | XAF 3.1, 3.2 |
| `http://www.odb.belastingdienst.nl/Belastingdienst/BCPP/1.0/structures/XmlAuditfileFinancieel3.2.1` | XAF 3.2.1 |
| `http://www.odb.belastingdienst.nl/Belastingdienst/BCPP/1.1/structures/XmlauditfileXAF_4.0` | XAF 4.0: the `targetNamespace` of the official XSD |
| `http://www.odb.belastingdienst.nl/XAF/4.0` | XAF 4.0 as written in the 4.0 *Toelichting* (§3.7), which **contradicts the XSD** |
| `http://www.auditfiles.nl/XAF/4.0` | **does not exist**, but is written by several exporters |
| `http://www.auditfiles.nl/XAF/2.0` | does not exist; real CLAIR2 files have no namespace |
| comment `<!-- Vervolgbestand x van y -->` at the start | continuation file *x* of *y* of a split auditfile |

Namespaces alone are therefore unreliable: the two official 4.0 documents disagree, 3.2.1 has a
namespace of its own, some exporters invent one, and CLAIR2 and some 3.x exporters write none.
pyxaf compares namespaces case-insensitively and ignores a trailing slash.

## How detection works

`pyxaf.detect()` inspects only the first 64 KiB (after decompression) and scores several signals:

1. the ADF signature;
2. the root element (`auditfile`, any case) and its namespace, looked up with a trust status;
3. the element vocabulary of the first 64 KiB compared with each version's catalogue
   (`trLine`/`amnt` vs `line`/`debitAmount`, 4.0-only `RGScode`/`Commercenr`/`settDate`,
   3.2-only `companyIdent`/`opBalDate`, 3.1-only `taxonomies`, …);
4. `header/auditfileVersion` (CLAIR2) and the continuation comment.

```python
>>> info = pyxaf.detect("2024.xaf")
>>> info.version, info.namespace_status, info.confidence
(<Version.XAF40: '4.0'>, <NamespaceStatus.OFFICIAL: 'official'>, 1.0)
>>> info.reasons
('official namespace of XAF 4.0',)
>>> pyxaf.detect("export.xaf").reasons
("namespace 'http://www.auditfiles.nl/XAF/4.0' is known-bogus for 4.0",)
```

The result is a `FormatInfo`:

| Attribute | Meaning |
|---|---|
| `family` | `Family.ADF`, `Family.CLAIR2` or `Family.XAF` |
| `version` | `Version.ADF`, `CLAIR2`, `XAF30`, `XAF31`, `XAF32`, `XAF321` or `XAF40`; compares equal to `"ADF"`, `"CLAIR2"`, `"3.0"` … `"4.0"` |
| `namespace` | the root element's namespace URI, as written |
| `namespace_status` | `official`, `documented-variant` (the 4.0 Toelichting URI), `unverified` (3.0), `known-bogus`, `unknown` or `none` |
| `encoding` | `EncodingInfo`: `bom`, `declared`, `has_declaration`, `effective` codec, `override` |
| `bom` | whether the file starts with a byte-order mark |
| `confidence` | 0–1; 1 when the official markers and the vocabulary agree |
| `reasons` | human-readable explanation of the decision |
| `continuation` | `(x, y)` for a continuation file, else `None` |
| `compression` | `"gzip"`, `"zip"` or `None` |
| `root` | local name of the root element (`None` for ADF) |

How the confidence comes about:

| Situation | Version used | Confidence |
|---|---|---|
| ADF signature | ADF | 1.0 |
| `auditfileVersion` `CLAIR2…` without namespace (with a namespace: 0.8) | CLAIR2 | 1.0 |
| official namespace, vocabulary fits | that version | 1.0 |
| official namespace, but elements that do not exist in that version and fit another version better | the namespace's version | 0.6 |
| documented variant (4.0 Toelichting URI) | 4.0 | 0.9 |
| unverified (3.0) or known-bogus namespace | that version | 0.7 |
| non-official namespace whose vocabulary fits a sibling version better | the best-fitting version | 0.6 |
| no or unknown namespace: best vocabulary match | best match | 0.5 (0.3 if even the best match has unknown elements) |

Detection never fails because of contradictory signals; it chooses a version and says why. When
reading or validating, the decision becomes findings: `XAF3001` (INFO) or `XAF3007` (WARNING,
confidence below 0.7) with the reasons, `XAF3002` for the documented variant (XSD validation will
fail), `XAF3003` for a namespace that does not exist, `XAF3004` for an unknown one. Only input that
is not an auditfile at all raises `NotAnAuditfileError`.

## Normalized field mapping

The normalized model uses English names that are the same for every iteration. The table shows
where the core fields come from. "ADF n" is field number *n* of the ADF mutation line; "hdr" the
ADF header line. Paths are relative to `auditfile/company` for XAF and to `auditfile` for CLAIR2.

| Normalized | ADF | CLAIR2 | XAF 3.0–3.2.1 | XAF 4.0 |
|---|---|---|---|---|
| `header.fiscal_year` | hdr Jaar/periode | `header/fiscalYear` | `header/fiscalYear` | `header/fiscalYear` |
| `header.start_date` / `end_date` | — | `header/startDate`, `endDate` | `header/startDate`, `endDate` | same |
| `header.currency` | — | `header/currencyCode` | `header/curCode` | `header/curCode` |
| `header.created` | hdr Datum aanmaak | `header/dateCreated` | `header/dateCreated` | `header/dateCreated` |
| `header.software_name` / `software_version` | hdr Boekhoudpakket + versie (one field) | `header/productID`, `productVersion` | `header/softwareDesc`, `softwareVersion` | same (required) |
| `header.rgs_version` | — | — | — | `header/RGSVersion` |
| `header.declared_version` | hdr Versie | `header/auditfileVersion` | — | — |
| `company.name` | hdr Naam onderneming | `header/companyName` | `companyName` | `companyName` |
| `company.identifier` | hdr Administratiecode | `header/companyID` | `companyIdent` (3.2) | — |
| `company.commerce_number` | — | — | — | `Commercenr` |
| `company.tax_registration_id` | hdr Fiscaalnummer | `header/taxRegistrationNr` | `taxRegIdent` | `taxRegIdent` |
| `account.id` / `description` | 8 / 11 | `ledgerAccount/accountID`, `accountDesc` | `ledgerAccount/accID`, `accDesc` | same |
| `account.account_type` | 9 (free text) | `accountType` (free text) | `accTp` (B/M/P) | `accTp` (B/P) |
| `account.lead_code` | 10 Cluster | `leadCode` | `leadCode` (3.2) | — |
| `account.rgs` | — | — | 3.2: `taxonomy/entryPoint/conceptRef`, else `leadReference`/`leadCode`/`leadCrossRef` when they hold an RGS code; 3.1: `generalLedger/taxonomies` | `RGScode` |
| `relation.id` / `name` | 25 / 28 | `custSupID` / `companyName` | `custSupID` / `custSupName` | same |
| `relation.relation_type` | 26 | `type` | `custSupTp` (3.1+) | `custSupTp` |
| `relation.opening_balance` / `closing_balance` | — | — | — | `opBalDesc` + `opBalTp`, `clBalDesc` + `clBalTp` (amounts, despite the names) |
| `journal.id` / `description` / `journal_type` | 1 / 2 / — | `journalID` / `description` / `type` | `jrnID` / `desc` / `jrnTp` | same |
| `transaction.number` | 4 Volgnummer (else 6) | `transactionID` | `nr` | `nr` |
| `transaction.period_key` | 3 | `period` | `periodNumber` | `periodNumber` |
| `transaction.date` | 7 Verwerkingsdatum | `transactionDate` | `trDt` | `trDt` |
| `transaction.source` / `user` | — | `sourceID` / — | `sourceID` / `userID` (3.2) | `Source` / `User` |
| `line.number` | 5 | `recordID` | `trLine/nr` | `trLine/nr` |
| `line.account_id` | 8 | `accountID` | `accID` | `accID` |
| `line.document_ref` | 13 | `documentID` | `docRef` | `docRef` |
| `line.effective_date` | 12 Mutatiedatum | `effectiveDate` | `effDate` (date of the event) | `effDate` (invoice date) |
| `line.settlement_date` | — | — | — | `settDate` |
| `line.amount` / `side` | 20 Debet, 21 Credit | `debitAmount`, `creditAmount` | `amnt`, `amntTp` | `amnt`, `amntTp` |
| `line.relation_id` | 25 | `custSupID` | `custSupID` | `custSupID` |
| `line.invoice_ref` | — | — | `invRef` | `invRef` |
| `line.cost_center` / `product` / `project` | 16 Kostenplaats / — / — | `costDesc` / `productDesc` / `projectDesc` | `costID` / `prodID` / `projID` | `cost` / `product` / `project` |
| `line.cost_unit` | 18 Kostendrager | — | — | — |
| `line.vat` | 22 (code only) | `vat` (`vatCode`, `vatPercentage`, `vatAmount`; one) | `vat` (`vatID`, `vatPerc`, `vatAmnt`, `vatAmntTp`) | same, 0–99 |
| `line.foreign` | 23 Valuta, 24 Koers | `currency` (`currencyCode`, `currencyDebitAmount`, `currencyCreditAmount`) | `currency` (`curCode`, `curAmnt`) | same |
| `line.bank_account` / `offset_bank_account` | — | — | `bankAccNr` (3.2) / — | `bankAccNr` / `offsetBankAccNr` |
| `af.transaction_totals` | hdr Aantal mutaties, Telling debet/credit | `transactions/numberEntries`, `totalDebit`, `totalCredit` | `transactions/linesCount`, `totalDebit`, `totalCredit` | same |
| `af.opening_balance()` | period-0 lines | period-`0` transactions | `openingBalance/obLine`, or period-0 / journal type O transactions | `openingBalance/obLine` only |

Every other element is still available through [`.raw`](reading.md#the-raw-layer) and, for lines
and transactions, `.extra`.

## Notes per version

### ADF

- Fields are left-justified and space-padded at fixed positions; pyxaf reads CR LF and LF line
  ends. Field problems are reported as `XAF3050`.
- Numbers: the decimal symbol may be a comma, a point **or absent**, but the two decimal digits
  are always present, and grouping symbols are allowed: `10.239,87`, `10239,87`, `10239.87` and
  `1023987` all mean 10,239.87. A leading minus is allowed.
- Dates are `dd-mm-jjjj` or `ddmmjjjj`.
- The opening balance is period 0 (2003 erratum). All amounts are in one currency; field 24 holds
  an exchange rate, which pyxaf exposes as `ForeignAmount.exchange_rate`.
- The encoding is ASCII per the specification; pyxaf decodes windows-1252 by default (DOS-era
  files), overridable with `encoding=`.
- ADF has no transaction records: pyxaf groups consecutive mutation lines with the same journal
  (field 1), period (3), Volgnummer (4) and Identificatie journaalpost (6) into one transaction.
- Accounts, relations and journals are collected from the mutation lines; the header's counts and
  totals become `af.transaction_totals`.

### CLAIR2

- No namespace; recognised by `header/auditfileVersion` = `CLAIR2.00.00`.
- Company data sits in `header`; accounts have free-text types (`Balans`, `W`, `Assets`, …),
  interpreted heuristically.
- Each line has `debitAmount` and `creditAmount`; `signed_amount` is debit minus credit.
- `transactions/numberEntries` should count lines; some exporters count transactions.
- Unique IDs and references are rules of the manual (keyrefs), not numbered rules.

### XAF 3.0 and 3.1

No XSD of either version is publicly available. pyxaf's catalogues for them are **derived**: the
element paths come from a cross-version XPath mapping that matches the 3.2 XSD exactly, and the
value rules are borrowed from 3.2. Because of that uncertainty, the validator reports unknown
elements and missing required elements in 3.0/3.1 files as WARNING instead of ERROR, and skips the
element-order check. 3.0 → 3.1 only adds `customerSupplier/custSupTp`. In 3.1, RGS links live in
`generalLedger/taxonomies/taxonomy/taxoElement` (`glAccID` → `txCd`). The 3.0 namespace is
unverified (`XAF3004`, INFO).

### XAF 3.2 and 3.2.1

- 298 elements, 250 leaf fields. 3.2.1 has the same structure under a new namespace, an updated
  ISO 4217 currency list (3.2 still lists obsolete codes such as EEK and LTL), `linesCount` ≥ 1,
  `qntity` ≥ 1 and `periodNumber` ≥ 0.
- The opening balance must be given exactly once: either as `openingBalance` or as transactions
  with period number 0.
- Allowed encodings: UTF-8 and ISO-8859-1.
- Journal types `jrnTp`: B bank, C cash, G goods, M memo, O opening balance, P purchases,
  S sales, T production, Y payroll, Z other. Account types `accTp`: B balance, M mixed,
  P profit and loss.

### XAF 4.0

- 111 elements, 90 leaf fields; amounts `decimal` with 20 digits and 2 decimals and **no minimum**
  (negative amounts are allowed); `vatPerc` `decimal(8,3)`; `fiscalYear` is `YYYY` or
  `YYYY-YYYY` for broken fiscal years.
- The opening balance may only be given in `openingBalance` (`XAF7005` otherwise); per the 4.0
  explanation a file always contains one.
- `effDate` now means the **invoice date** (3.2: date of the event); `settDate` is new (delivery or
  prepayment date).
- Removed: `companyIdent`, postal addresses, history and change information, `lead*`, taxonomy,
  `opBalDate`/`opBalDesc` of the opening balance, subledgers, `qntity`, `matchKeyID`, among others.
  Renamed in effect: `sourceID` → `Source`, `userID` → `User`, `costID`/`prodID`/`projID` →
  `cost`/`product`/`project`.
- Casing quirks are real element names: `Commercenr` (company) vs `commerceNr` (relation), `Source`,
  `User`, `RGScode`, `RGSVersion`. `opBalDesc`/`clBalDesc` of a customer/supplier hold amounts.
- `RGSVersion` has no specified format (`3.7`, `RGS 3.8` and `RGS-1` occur); pyxaf parses it
  leniently.
- The functional description defines consistency rules [0001]–[0010], which pyxaf implements as
  `XAF6001`–`XAF6003` and `XAF5004`–`XAF5010` (see [Validation](validation.md#official-rules)).
- Account types are B and P only; the 4.0 functional description no longer lists journal type O,
  but the XSD still allows it.

## Sources

- [XML Auditfile Financieel (XAF) 4.0.3](https://odb.belastingdienst.nl/documentatie/xml-auditfile-financieel-xaf-4-0-3/)
  and [background information on XAF 4.0](https://odb.belastingdienst.nl/auditfiles/achtergrond-informatie-xaf4-0/),
  published by the [ODB](https://odb.belastingdienst.nl/) (Ontwikkelaars Documentatie
  Belastingdienst).
- [Phase-out of the older auditfiles from 1 January 2027](https://odb.belastingdienst.nl/belangrijke-update-uitfasering-oude-auditfiles-financieel-xaf-per-01-01-2027/).
- The bundled schemas and where they come from: `src/pyxaf/schemas/NOTICE.md`.
- The XAF 3.0/3.1 element lists: the "XAF Mapping en Namen" table of
  [AnalyticsLibrary](https://github.com/AnalyticsLibrary/Analytics) (Apache-2.0).
