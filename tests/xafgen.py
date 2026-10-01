"""Deterministic synthetic auditfiles for every iteration (test fixtures).

One small, balanced :class:`Ledger` is rendered as XAF 4.0, 3.2.1, 3.2, 3.1, 3.0, CLAIR2 or ADF.
The XML renderings for 4.0, 3.2.1, 3.2 and CLAIR2 validate against the official XSDs (checked in
``tests/test_generators.py``). Quirk mutators reproduce problems seen in real files.

All data is invented. Never add real client data to the test suite.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from xml.sax.saxutils import escape

NS = {
    "3.0": "http://www.auditfiles.nl/XAF/3.0",  # unverified; no 3.0 file or XSD is public
    "3.1": "http://www.auditfiles.nl/XAF/3.1",
    "3.2": "http://www.auditfiles.nl/XAF/3.2",
    "3.2.1": "http://www.odb.belastingdienst.nl/Belastingdienst/BCPP/1.0/structures/"
    "XmlAuditfileFinancieel3.2.1",
    "4.0": "http://www.odb.belastingdienst.nl/Belastingdienst/BCPP/1.1/structures/"
    "XmlauditfileXAF_4.0",
}
XML_VERSIONS = ("4.0", "3.2.1", "3.2", "3.1", "3.0")


@dataclass
class TLine:
    nr: str
    acc: str
    amount: Decimal
    side: str  # "D" / "C"
    desc: str = ""
    doc: str = "DOC1"
    eff: dt.date = dt.date(2024, 1, 15)
    custsup: str | None = None
    vat: tuple[str, Decimal, Decimal, str] | None = None  # id, perc, amount, side
    currency: tuple[str, Decimal] | None = None


@dataclass
class Tx:
    nr: str
    desc: str
    period: str
    date: dt.date
    lines: list[TLine]


@dataclass
class Journal:
    id: str
    desc: str
    tp: str
    txs: list[Tx]


@dataclass
class Ledger:
    year: int = 2024
    company: str = "Voorbeeld & Zonen B.V."
    company_ident: str = "ADM01"
    tax_id: str = "NL001234567B01"
    accounts: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    relations: list[tuple[str, str, str]] = field(default_factory=list)
    vat_codes: list[tuple[str, str, str, str]] = field(default_factory=list)
    ob_lines: list[tuple[str, str, Decimal, str]] = field(default_factory=list)
    journals: list[Journal] = field(default_factory=list)

    @property
    def start(self) -> dt.date:
        return dt.date(self.year, 1, 1)

    @property
    def end(self) -> dt.date:
        return dt.date(self.year, 12, 31)

    def lines(self) -> list[TLine]:
        return [ln for j in self.journals for t in j.txs for ln in t.lines]

    def totals(self) -> tuple[Decimal, Decimal]:
        d = sum((ln.amount for ln in self.lines() if ln.side == "D"), Decimal(0))
        c = sum((ln.amount for ln in self.lines() if ln.side == "C"), Decimal(0))
        return d, c

    def ob_totals(self) -> tuple[Decimal, Decimal]:
        d = sum((a for _, _, a, s in self.ob_lines if s == "D"), Decimal(0))
        c = sum((a for _, _, a, s in self.ob_lines if s == "C"), Decimal(0))
        return d, c


ACCOUNTS = [
    ("0100", "Inventaris", "B", "BMvaBei"),
    ("1000", "Kas", "B", "BLimKas"),
    ("1100", "Bank", "B", "BLimBan"),
    ("1300", "Debiteuren", "B", "BVorDeb"),
    ("1600", "Crediteuren", "B", "BSchCre"),
    ("1800", "Te betalen btw", "B", "BSchBtw"),
    ("1810", "Te vorderen btw", "B", "BVorBtw"),
    ("0500", "Eigen vermogen", "B", "BEivOkc"),
    ("8000", "Omzet", "P", "WOmzNop"),
    ("4000", "Kantoorkosten", "P", "WBedKan"),
    ("4100", "Huur", "P", "WBedHui"),
]
RELATIONS = [("D001", "Klant Één", "C"), ("C001", "Leverancier Twee", "S"), ("B001", "Beide", "B")]
VAT_CODES = [("H21", "BTW hoog 21%", "1800", "1810"), ("L9", "BTW laag 9%", "1800", "1810")]
DESCS = ["Verkoop", "Huur januari", "Café-bezoek", "R&D <test>", "Kantoorartikelen", "Würth"]


def make_ledger(seed: int = 1, n_tx: int = 12, *, year: int = 2024) -> Ledger:
    """A small balanced ledger with an opening balance, VAT, relations and foreign currency."""
    rng = random.Random(seed)
    led = Ledger(
        year=year, accounts=list(ACCOUNTS), relations=list(RELATIONS), vat_codes=list(VAT_CODES)
    )
    led.ob_lines = [
        ("1", "1100", Decimal("10000.00"), "D"),
        ("2", "0100", Decimal("2500.50"), "D"),
        ("3", "0500", Decimal("12500.50"), "C"),
    ]
    journals = [
        Journal("MEM", "Memoriaal", "M", []),
        Journal("VRK", "Verkoopboek", "S", []),
        Journal("BNK", "Bank", "B", []),
    ]
    for i in range(n_tx):
        j = journals[i % len(journals)]
        month = 1 + (i % 12)
        date = dt.date(year, month, 1 + rng.randrange(28))
        net = Decimal(rng.randrange(100, 500_000)) / 100
        vat = (net * Decimal("0.21")).quantize(Decimal("0.01"))
        desc = rng.choice(DESCS)
        lines = [
            TLine("1", "1300", net + vat, "D", desc, f"F{i:04d}", date, custsup="D001"),
            TLine(
                "2",
                "8000",
                net,
                "C",
                desc,
                f"F{i:04d}",
                date,
                vat=("H21", Decimal("21.000"), vat, "C"),
            ),
            TLine("3", "1800", vat, "C", "btw", f"F{i:04d}", date),
        ]
        if i % 5 == 4:
            lines[0].currency = ("USD", (net + vat) * Decimal("1.10"))
        j.txs.append(Tx(f"{i + 1}", f"Boeking {i + 1}", str(month), date, lines))
    led.journals = journals
    return led


def _amt(v: Decimal) -> str:
    return f"{v:.2f}"


class _W:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.depth = 0

    def open(self, tag: str, attrs: str = "") -> None:
        self.parts.append("\t" * self.depth + f"<{tag}{attrs}>\n")
        self.depth += 1

    def close(self, tag: str) -> None:
        self.depth -= 1
        self.parts.append("\t" * self.depth + f"</{tag}>\n")

    def leaf(self, tag: str, value: object | None) -> None:
        if value is None:
            return
        text = _amt(value) if isinstance(value, Decimal) else str(value)
        self.parts.append("\t" * self.depth + f"<{tag}>{escape(text)}</{tag}>\n")

    def text(self) -> str:
        return "".join(self.parts)


def write_xaf(
    led: Ledger,
    version: str = "4.0",
    *,
    ob_style: str = "element",  # element | period0 | journalO | none
    namespace: str | None = None,
    encoding: str = "UTF-8",
    declaration: bool = True,
    bom: bool = False,
) -> bytes:
    """Render ``led`` as an XAF 3.0–4.0 document."""
    v40 = version == "4.0"
    v32 = version in ("3.2", "3.2.1")
    ns = NS.get(version) if namespace is None else namespace
    w = _W()
    w.open("auditfile", f' xmlns="{ns}"' if ns else "")
    w.open("header")
    w.leaf("fiscalYear", led.year)
    w.leaf("startDate", led.start)
    w.leaf("endDate", led.end)
    w.leaf("curCode", "EUR")
    w.leaf("dateCreated", dt.date(led.year + 1, 2, 1))
    w.leaf("softwareDesc", "pyxaf-testgen")
    w.leaf("softwareVersion", "1.0")
    if v40:
        w.leaf("RGSVersion", "3.8")
    w.close("header")
    w.open("company")
    if v40:
        w.leaf("Commercenr", "12345678")
    else:
        w.leaf("companyIdent", led.company_ident)
    w.leaf("companyName", led.company)
    w.leaf("taxRegistrationCountry", "NL")
    w.leaf("taxRegIdent", led.tax_id)
    w.open("streetAddress")
    w.leaf("streetname", "Hoofdstraat")
    w.leaf("number", "1")
    w.leaf("city", "Utrecht")
    w.leaf("postalCode", "3511 AA")
    w.leaf("country", "NL")
    w.close("streetAddress")
    w.open("customersSuppliers")
    for rid, name, tp in led.relations:
        w.open("customerSupplier")
        w.leaf("custSupID", rid)
        w.leaf("custSupName", name)
        if version != "3.0":
            w.leaf("custSupTp", tp)
        if v40:
            w.leaf("opBalDesc", Decimal("100.00"))
            w.leaf("opBalTp", "D")
        w.close("customerSupplier")
    w.close("customersSuppliers")
    w.open("generalLedger")
    for acc, desc, tp, rgs in led.accounts:
        w.open("ledgerAccount")
        w.leaf("accID", acc)
        w.leaf("accDesc", desc)
        w.leaf("accTp", tp)
        if v40 and rgs:
            w.leaf("RGScode", rgs)
        if v32 and rgs:
            w.open("taxonomy")
            w.leaf("taxoRef", "http://www.nltaxonomie.nl/rgs/3.8")
            w.open("entryPoint")
            w.leaf("entryPointRef", "rgs-basis")
            w.leaf("conceptRef", rgs)
            w.close("entryPoint")
            w.close("taxonomy")
        w.close("ledgerAccount")
    if version in ("3.0", "3.1"):
        w.open("taxonomies")
        w.open("taxonomy")
        for acc, _desc, _tp, rgs in led.accounts:
            if rgs:
                w.open("taxoElement")
                w.leaf("glAccID", acc)
                w.leaf("txCd", rgs)
                w.close("taxoElement")
        w.leaf("taxoRef", "rgs")
        w.close("taxonomy")
        w.close("taxonomies")
    w.close("generalLedger")
    w.open("vatCodes")
    for vid, desc, pay, claim in led.vat_codes:
        w.open("vatCode")
        w.leaf("vatID", vid)
        w.leaf("vatDesc", desc)
        w.leaf("vatToPayAccID", pay)
        w.leaf("vatToClaimAccID", claim)
        w.close("vatCode")
    w.close("vatCodes")
    w.open("periods")
    for m in range(1, 13):
        start = dt.date(led.year, m, 1)
        end = (dt.date(led.year + (m == 12), m % 12 + 1, 1)) - dt.timedelta(days=1)
        w.open("period")
        w.leaf("periodNumber", m)
        w.leaf("startDatePeriod", start)
        w.leaf("endDatePeriod", end)
        w.close("period")
    w.close("periods")
    if ob_style == "element":
        d, c = led.ob_totals()
        w.open("openingBalance")
        if not v40:
            w.leaf("opBalDate", led.start)
            w.leaf("opBalDesc", "Beginbalans")
        w.leaf("linesCount", len(led.ob_lines))
        w.leaf("totalDebit", d)
        w.leaf("totalCredit", c)
        for nr, acc, amount, side in led.ob_lines:
            w.open("obLine")
            w.leaf("nr", nr)
            w.leaf("accID", acc)
            w.leaf("amnt", amount)
            w.leaf("amntTp", side)
            w.close("obLine")
        w.close("openingBalance")
    journals = list(led.journals)
    if ob_style in ("period0", "journalO"):
        ob_lines = [
            TLine(nr, acc, amount, side, "Beginbalans", "BB", led.start)
            for nr, acc, amount, side in led.ob_lines
        ]
        tx = Tx("BB1", "Beginbalans", "0" if ob_style == "period0" else "1", led.start, ob_lines)
        jtp = "O" if ob_style == "journalO" else "M"
        journals = [Journal("BEGIN", "Beginbalans", jtp, [tx]), *journals]
    all_lines = [ln for j in journals for t in j.txs for ln in t.lines]
    d = sum((ln.amount for ln in all_lines if ln.side == "D"), Decimal(0))
    c = sum((ln.amount for ln in all_lines if ln.side == "C"), Decimal(0))
    w.open("transactions")
    w.leaf("linesCount", len(all_lines))
    w.leaf("totalDebit", d)
    w.leaf("totalCredit", c)
    for j in journals:
        w.open("journal")
        w.leaf("jrnID", j.id)
        w.leaf("desc", j.desc)
        w.leaf("jrnTp", j.tp)
        for t in j.txs:
            w.open("transaction")
            w.leaf("nr", t.nr)
            w.leaf("desc", t.desc)
            w.leaf("periodNumber", t.period)
            w.leaf("trDt", t.date)
            if v40:
                w.leaf("Source", "testgen")
                w.leaf("User", "tester")
            else:
                w.leaf("sourceID", "testgen")
            for ln in t.lines:
                w.open("trLine")
                w.leaf("nr", ln.nr)
                w.leaf("accID", ln.acc)
                w.leaf("docRef", ln.doc)
                w.leaf("effDate", ln.eff)
                w.leaf("desc", ln.desc or None)
                w.leaf("amnt", ln.amount)
                w.leaf("amntTp", ln.side)
                w.leaf("custSupID", ln.custsup)
                if v40 and ln.custsup:
                    w.leaf("invRef", ln.doc)
                if ln.vat:
                    vid, perc, vamt, vside = ln.vat
                    w.open("vat")
                    w.leaf("vatID", vid)
                    w.leaf("vatPerc", f"{perc:.3f}")
                    w.leaf("vatAmnt", vamt)
                    w.leaf("vatAmntTp", vside)
                    w.close("vat")
                if ln.currency:
                    w.open("currency")
                    w.leaf("curCode", ln.currency[0])
                    w.leaf("curAmnt", ln.currency[1].quantize(Decimal("0.01")))
                    w.close("currency")
                w.close("trLine")
            w.close("transaction")
        w.close("journal")
    w.close("transactions")
    w.close("company")
    w.close("auditfile")
    body = w.text()
    decl = f'<?xml version="1.0" encoding="{encoding}"?>\n' if declaration else ""
    data = (decl + body).encode(encoding.replace("windows-", "cp"))
    return (b"\xef\xbb\xbf" + data) if bom else data


def write_clair2(led: Ledger, *, ob_style: str = "period0") -> bytes:
    """Render ``led`` as a CLAIR2.00.00 document (no namespace)."""
    w = _W()
    w.open("auditfile")
    w.open("header")
    w.leaf("auditfileVersion", "CLAIR2.00.00")
    w.leaf("companyID", led.company_ident)
    w.leaf("taxRegistrationNr", "001234567B01")
    w.leaf("companyName", led.company)
    w.leaf("companyAddress", "Hoofdstraat 1")
    w.leaf("companyCity", "Utrecht")
    w.leaf("companyPostalCode", "3511 AA")
    w.leaf("fiscalYear", led.year)
    w.leaf("startDate", led.start)
    w.leaf("endDate", led.end)
    w.leaf("currencyCode", "EUR")
    w.leaf("dateCreated", dt.date(led.year + 1, 2, 1))
    w.leaf("productID", "pyxaf-testgen")
    w.leaf("productVersion", "1.0")
    w.close("header")
    w.open("generalLedger")
    w.leaf("taxonomy", "")
    for acc, desc, tp, _rgs in led.accounts:
        w.open("ledgerAccount")
        w.leaf("accountID", acc)
        w.leaf("accountDesc", desc)
        w.leaf("accountType", "Balans" if tp == "B" else "Winst en verlies")
        w.leaf("leadCode", acc[:2])
        w.close("ledgerAccount")
    w.close("generalLedger")
    w.open("customersSuppliers")
    for rid, name, tp in led.relations:
        w.open("customerSupplier")
        w.leaf("custSupID", rid)
        w.leaf("type", {"C": "Debiteur", "S": "Crediteur", "B": "Debiteur/Crediteur"}[tp])
        w.leaf("companyName", name)
        w.open("streetAddress")
        w.leaf("address", "Kerkstraat 2")
        w.leaf("city", "Utrecht")
        w.leaf("postalCode", "3511 AB")
        w.leaf("country", "NL")
        w.close("streetAddress")
        w.close("customerSupplier")
    w.close("customersSuppliers")
    journals = list(led.journals)
    if ob_style == "period0":
        ob = [
            TLine(nr, acc, amount, side, "Beginbalans", "BB", led.start)
            for nr, acc, amount, side in led.ob_lines
        ]
        journals = [
            Journal(
                "BEGIN", "Beginbalans", "Memoriaal", [Tx("BB1", "Beginbalans", "0", led.start, ob)]
            ),
            *journals,
        ]
    all_lines = [ln for j in journals for t in j.txs for ln in t.lines]
    d = sum((ln.amount for ln in all_lines if ln.side == "D"), Decimal(0))
    c = sum((ln.amount for ln in all_lines if ln.side == "C"), Decimal(0))
    record_id = 0
    w.open("transactions")
    w.leaf("numberEntries", len(all_lines))
    w.leaf("totalDebit", d)
    w.leaf("totalCredit", c)
    for j in journals:
        w.open("journal")
        w.leaf("journalID", j.id)
        w.leaf("description", j.desc)
        w.leaf("type", {"M": "Memoriaal", "S": "Verkoop", "B": "Bank"}.get(j.tp, j.tp))
        for t in j.txs:
            w.open("transaction")
            w.leaf("transactionID", t.nr)
            w.leaf("description", t.desc)
            w.leaf("period", t.period)
            w.leaf("transactionDate", t.date)
            for ln in t.lines:
                record_id += 1  # recordID is a key: unique in the whole file
                w.open("line")
                w.leaf("recordID", record_id)
                w.leaf("accountID", ln.acc)
                w.leaf("custSupID", ln.custsup)
                w.leaf("documentID", ln.doc)
                w.leaf("effectiveDate", ln.eff)
                w.leaf("description", ln.desc or None)
                w.leaf("debitAmount" if ln.side == "D" else "creditAmount", ln.amount)
                if ln.vat:
                    w.open("vat")
                    w.leaf("vatCode", ln.vat[0])
                    w.leaf("vatAmount", ln.vat[2])
                    w.close("vat")
                w.close("line")
            w.close("transaction")
        w.close("journal")
    w.close("transactions")
    w.close("auditfile")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + w.text()).encode()


# ADF field layout (start is 1-based), see pyxaf._adf
_ADF_HEADER = [
    ("versie", 1, 12),
    ("pakket", 13, 50),
    ("adm", 63, 20),
    ("jaar", 83, 15),
    ("fiscaal", 98, 15),
    ("naam", 113, 50),
    ("adres", 163, 30),
    ("plaats", 193, 30),
    ("aantal", 223, 10),
    ("datum", 233, 10),
    ("debet", 243, 16),
    ("credit", 259, 16),
]
_ADF_LINE = {
    "dagboek": (1, 20),
    "dagboekoms": (21, 30),
    "periode": (51, 5),
    "volgnr": (56, 10),
    "regel": (66, 5),
    "datum": (91, 10),
    "rek": (101, 15),
    "soort": (116, 5),
    "reknaam": (136, 30),
    "mutdatum": (166, 10),
    "stuk": (176, 15),
    "oms": (256, 30),
    "debet": (286, 16),
    "credit": (302, 16),
    "btw": (318, 5),
    "valuta": (323, 10),
    "relnr": (346, 15),
    "relsoort": (361, 5),
    "relnaam": (382, 30),
}


def _fixed(fields: dict[str, str], layout: dict[str, tuple[int, int]], width: int) -> str:
    buf = [" "] * width
    for name, value in fields.items():
        start, length = layout[name]
        text = value[:length].ljust(length)
        buf[start - 1 : start - 1 + length] = list(text)
    return "".join(buf).rstrip()


def _adf_num(v: Decimal, style: int) -> str:
    """Write an ADF amount in one of the allowed notations."""
    cents = int((v * 100).to_integral_value())
    if style == 0:
        return f"{cents}"  # no separator, decimals implied
    if style == 1:
        return f"{v:.2f}".replace(".", ",")
    if style == 2:
        whole, frac = f"{v:.2f}".split(".")
        groups = f"{int(whole):,}".replace(",", ".")
        return f"{groups},{frac}"
    return f"{v:.2f}"


def write_adf(led: Ledger) -> bytes:
    """Render ``led`` as a fixed-width ADF file (opening balance as period 0)."""
    acc_names = {a: (n, t) for a, n, t, _ in led.accounts}
    rel_names = {r: (n, t) for r, n, t in led.relations}
    rows: list[str] = []
    journals = [
        Journal(
            "BEGIN",
            "Beginbalans",
            "M",
            [
                Tx(
                    "0",
                    "Beginbalans",
                    "0",
                    led.start,
                    [
                        TLine(nr, acc, amount, side, "Beginbalans", "BB", led.start)
                        for nr, acc, amount, side in led.ob_lines
                    ],
                )
            ],
        ),
        *led.journals,
    ]
    n = 0
    total_d = total_c = Decimal(0)
    for j in journals:
        for t in j.txs:
            for ln in t.lines:
                n += 1
                d = ln.amount if ln.side == "D" else Decimal(0)
                c = ln.amount if ln.side == "C" else Decimal(0)
                total_d += d
                total_c += c
                fields = {
                    "dagboek": j.id,
                    "dagboekoms": j.desc,
                    "periode": t.period,
                    "volgnr": t.nr,
                    "regel": ln.nr,
                    "datum": t.date.strftime("%d-%m-%Y"),
                    "rek": ln.acc,
                    "soort": acc_names[ln.acc][1],
                    "reknaam": acc_names[ln.acc][0],
                    "mutdatum": ln.eff.strftime("%d%m%Y"),
                    "stuk": ln.doc,
                    "oms": ln.desc or "-",
                    "debet": _adf_num(d, n % 4),
                    "credit": _adf_num(c, n % 4),
                }
                if ln.vat:
                    fields["btw"] = ln.vat[0]
                if ln.custsup:
                    name, tp = rel_names[ln.custsup]
                    fields |= {
                        "relnr": ln.custsup,
                        "relsoort": "D" if tp == "C" else "C",
                        "relnaam": name,
                    }
                rows.append(_fixed(fields, _ADF_LINE, 496))
    hdr_layout = {name: (s, ln) for name, s, ln in _ADF_HEADER}
    header = _fixed(
        {
            "versie": "CLAIR1.00.00",
            "pakket": "pyxaf-testgen 1.0",
            "adm": led.company_ident,
            "jaar": str(led.year),
            "fiscaal": "001234567",
            "naam": led.company,
            "adres": "Hoofdstraat 1",
            "plaats": "Utrecht",
            "aantal": str(n),
            "datum": dt.date(led.year + 1, 2, 1).strftime("%d-%m-%Y"),
            "debet": _adf_num(total_d, 3),
            "credit": _adf_num(total_c, 3),
        },
        hdr_layout,
        274,
    )
    return ("\r\n".join([header, *rows]) + "\r\n").encode("cp1252")


def write(version: str, led: Ledger | None = None, **kwargs: object) -> bytes:
    """Render a ledger for any iteration (``"ADF"``, ``"CLAIR2"``, ``"3.0"`` … ``"4.0"``)."""
    led = led or make_ledger()
    if version == "ADF":
        return write_adf(led)
    if version == "CLAIR2":
        return write_clair2(led, **kwargs)  # type: ignore[arg-type]
    return write_xaf(led, version, **kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- quirk mutators
def replace_once(data: bytes, old: bytes, new: bytes) -> bytes:
    assert old in data, old
    return data.replace(old, new, 1)


def unbalance_first_transaction(led: Ledger) -> Ledger:
    led = replace(led, journals=[replace(j, txs=list(j.txs)) for j in led.journals])
    tx = led.journals[0].txs[0]
    tx.lines = [*tx.lines[:-1], replace(tx.lines[-1], amount=tx.lines[-1].amount + 1)]
    return led


def negative_line(led: Ledger) -> Ledger:
    """Turn the first credit line into a negative debit line (−x D == x C under 'flip')."""
    tx = led.journals[0].txs[0]
    ln = tx.lines[1]
    tx.lines[1] = replace(ln, amount=-ln.amount, side="D")
    return led


Mutator = Callable[[bytes], bytes]
