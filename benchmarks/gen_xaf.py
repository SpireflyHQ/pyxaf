#!/usr/bin/env python3
"""Synthetic XAF 4.0 (XML Auditfile Financieel) generator.

Produces files that validate against the official Belastingdienst XSD
(XmlAuditfileFinancieel4.0.xsd, targetNamespace
http://www.odb.belastingdienst.nl/Belastingdienst/BCPP/1.1/structures/XmlauditfileXAF_4.0).

Design goals
- Streaming writer: constant memory, so it can produce multi-GB files.
- Deterministic (seeded) so benchmark inputs are reproducible.
- Every transaction is balanced (sum debit == sum credit), and the
  <transactions> linesCount/totalDebit/totalCredit control totals are correct.
  Because those totals precede the journals in the XSD sequence, amounts are
  generated in a cheap first pass (same seed) and the file in a second pass.
- Realistic-ish content: Dutch descriptions incl. non-ASCII and characters
  that need escaping (&, <), optional elements (settDate, custSupID, vat,
  currency, ...) present on a fraction of lines.

Usage
    python gen_xaf.py --lines 1000000 -o big.xaf [--seed 42] [--indent tab|none]
                      [--encoding UTF-8|ISO-8859-1]

Only uses the standard library.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
from xml.sax.saxutils import escape

NS = "http://www.odb.belastingdienst.nl/Belastingdienst/BCPP/1.1/structures/XmlauditfileXAF_4.0"

WORDS = [
    "Verkoop",
    "Inkoop",
    "Huur",
    "Kantoorartikelen",
    "Brandstof",
    "Reiskosten",
    "Loon",
    "Afschrijving",
    "Rente",
    "Bankkosten",
    "Consultancy",
    "Licentie",
    "Café-bezoek",
    "Représentatie",
    "Ëxport",
    "R&D",
    "Würth",
    "Overboeking",
    "Creditnota",
    "Factuur",
    "Correctie <handmatig>",
    "Kas",
    "Debiteur",
    "Crediteur",
]
CITIES = [
    "Amsterdam",
    "Rotterdam",
    "Utrecht",
    "Den Haag",
    "Eindhoven",
    "Groningen",
    "'s-Hertogenbosch",
]
JOURNALS = [
    ("MEMO", "Memoriaal", "M"),
    ("VERK", "Verkoopboek", "S"),
    ("INK", "Inkoopboek", "P"),
    ("BANK", "Bank ING", "B"),
    ("KAS", "Kasboek", "C"),
    ("BEGIN", "Beginbalans", "O"),
]
VAT_CODES = [("H21", "BTW hoog 21%", "21"), ("L9", "BTW laag 9%", "9"), ("N0", "BTW 0%", "0")]


def fmt_amount(cents: int) -> str:
    """TypeAmount2decimals: xsd:decimal, totalDigits 20, fractionDigits 2."""
    return f"{cents // 100}.{cents % 100:02d}"


def plan_amounts(rng: random.Random, n_lines: int):
    """Yield per-transaction lists of (cents, 'D'|'C'); balanced; total lines == n_lines."""
    remaining = n_lines
    while remaining > 0:
        k = min(remaining, rng.choice((2, 2, 2, 3, 3, 4, 5, 6)))
        if k == 1:  # can only happen at the very end; emit a zero-amount line
            yield [(0, "D")]
            remaining -= 1
            continue
        n_debit = 1 if k <= 3 else rng.randint(1, k - 1)
        n_credit = k - n_debit
        debits = [rng.randint(1, 2_500_000) for _ in range(n_debit)]  # up to 25k EUR
        total = sum(debits)
        # split total over credit lines
        if n_credit == 1:
            credits = [total]
        else:
            cuts = sorted(rng.randint(0, total) for _ in range(n_credit - 1))
            credits = [b - a for a, b in zip([0, *cuts], [*cuts, total])]
        lines = [(c, "D") for c in debits] + [(c, "C") for c in credits]
        yield lines
        remaining -= k


def generate(
    out,
    n_lines: int,
    seed: int = 42,
    indent: str = "tab",
    encoding: str = "UTF-8",
    n_accounts: int = 250,
    n_custsup: int = 2000,
    year: int = 2026,
) -> None:
    # ---- pass 1: control totals ------------------------------------------------
    total_d = total_c = 0
    n_tx = 0
    for tx in plan_amounts(random.Random(seed), n_lines):
        n_tx += 1
        for cents, tp in tx:
            if tp == "D":
                total_d += cents
            else:
                total_c += cents
    assert total_d == total_c

    rng_amt = random.Random(seed)  # replays the same amounts in pass 2
    rng = random.Random(seed + 1)  # everything else
    nl = "\n" if indent != "none" else ""

    def ind(level: int) -> str:
        return "\t" * level if indent == "tab" else ""

    w = out.write

    def el(level: int, tag: str, value: str) -> None:
        w(f"{ind(level)}<{tag}>{escape(value)}</{tag}>{nl}")

    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)
    w(f'<?xml version="1.0" encoding="{encoding}"?>\n')
    w(f'<auditfile xmlns="{NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{nl}')
    # header
    w(f"{ind(1)}<header>{nl}")
    el(2, "fiscalYear", str(year))
    el(2, "startDate", start.isoformat())
    el(2, "endDate", end.isoformat())
    el(2, "curCode", "EUR")
    el(2, "dateCreated", dt.date(year + 1, 3, 1).isoformat())
    el(2, "softwareDesc", "pyxaf gen_xaf.py")
    el(2, "softwareVersion", "1.0")
    el(2, "RGSVersion", "3.7")
    w(f"{ind(1)}</header>{nl}")
    # company
    w(f"{ind(1)}<company>{nl}")
    el(2, "Commercenr", "12345678")
    el(2, "companyName", "Synthetische Testonderneming B.V. & Zn.")
    el(2, "taxRegistrationCountry", "NL")
    el(2, "taxRegIdent", "NL001234567B01")
    w(f"{ind(2)}<streetAddress>{nl}")
    el(3, "streetname", "Keizersgracht")
    el(3, "number", "123")
    el(3, "city", "Amsterdam")
    el(3, "postalCode", "1015 CJ")
    el(3, "country", "NL")
    w(f"{ind(2)}</streetAddress>{nl}")
    # customersSuppliers
    w(f"{ind(2)}<customersSuppliers>{nl}")
    for i in range(1, n_custsup + 1):
        w(f"{ind(3)}<customerSupplier>{nl}")
        el(4, "custSupID", f"CS{i:06d}")
        el(4, "custSupName", f"Relatie {i} {rng.choice(WORDS)}"[:50])
        if rng.random() < 0.5:
            el(4, "eMail", f"info{i}@example.nl")
        el(4, "custSupTp", rng.choice("BCS"))
        if rng.random() < 0.3:
            el(4, "opBalDesc", fmt_amount(rng.randint(0, 10_000_000)))
            el(4, "opBalTp", rng.choice("DC"))
        w(f"{ind(4)}<streetAddress>{nl}")
        el(5, "city", rng.choice(CITIES))
        el(5, "country", "NL")
        w(f"{ind(4)}</streetAddress>{nl}")
        w(f"{ind(3)}</customerSupplier>{nl}")
    w(f"{ind(2)}</customersSuppliers>{nl}")
    # generalLedger
    accounts = [str(1000 + 10 * i) for i in range(n_accounts)]
    w(f"{ind(2)}<generalLedger>{nl}")
    for i, acc in enumerate(accounts):
        w(f"{ind(3)}<ledgerAccount>{nl}")
        el(4, "accID", acc)
        el(4, "accDesc", f"{rng.choice(WORDS)} {acc}")
        el(4, "accTp", "B" if i < n_accounts // 2 else "P")
        el(4, "RGScode", f"W{rng.choice(['Omz', 'Bed', 'Fin'])}{i:04d}")
        w(f"{ind(3)}</ledgerAccount>{nl}")
    w(f"{ind(2)}</generalLedger>{nl}")
    # vatCodes
    w(f"{ind(2)}<vatCodes>{nl}")
    for vid, vdesc, _ in VAT_CODES:
        w(f"{ind(3)}<vatCode>{nl}")
        el(4, "vatID", vid)
        el(4, "vatDesc", vdesc)
        el(4, "vatToPayAccID", "1500")
        el(4, "vatToClaimAccID", "1510")
        w(f"{ind(3)}</vatCode>{nl}")
    w(f"{ind(2)}</vatCodes>{nl}")
    # periods
    w(f"{ind(2)}<periods>{nl}")
    for m in range(1, 13):
        ps = dt.date(year, m, 1)
        pe = dt.date(year + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
        w(f"{ind(3)}<period>{nl}")
        el(4, "periodNumber", str(m))
        el(4, "startDatePeriod", ps.isoformat())
        el(4, "endDatePeriod", pe.isoformat())
        w(f"{ind(3)}</period>{nl}")
    w(f"{ind(2)}</periods>{nl}")
    # openingBalance (balanced: 2 lines)
    w(f"{ind(2)}<openingBalance>{nl}")
    el(3, "linesCount", "2")
    el(3, "totalDebit", "50000.00")
    el(3, "totalCredit", "50000.00")
    for nr, acc, tp in ((1, accounts[0], "D"), (2, accounts[1], "C")):
        w(f"{ind(3)}<obLine>{nl}")
        el(4, "nr", str(nr))
        el(4, "accID", acc)
        el(4, "amnt", "50000.00")
        el(4, "amntTp", tp)
        w(f"{ind(3)}</obLine>{nl}")
    w(f"{ind(2)}</openingBalance>{nl}")
    # transactions
    w(f"{ind(2)}<transactions>{nl}")
    el(3, "linesCount", str(n_lines))
    el(3, "totalDebit", fmt_amount(total_d))
    el(3, "totalCredit", fmt_amount(total_c))
    journals = JOURNALS[:5]
    txs = plan_amounts(rng_amt, n_lines)
    # distribute transactions over journals in contiguous blocks
    per_journal = [n_tx // len(journals)] * len(journals)
    per_journal[-1] += n_tx - sum(per_journal)
    tx_nr = 0
    days = (end - start).days
    for (jid, jdesc, jtp), n_j in zip(journals, per_journal):
        w(f"{ind(3)}<journal>{nl}")
        el(4, "jrnID", jid)
        el(4, "desc", jdesc)
        el(4, "jrnTp", jtp)
        for _ in range(n_j):
            tx = next(txs)
            tx_nr += 1
            d = start + dt.timedelta(days=rng.randint(0, days))
            ds = d.isoformat()
            w(f"{ind(4)}<transaction>{nl}")
            el(5, "nr", f"{year}{tx_nr:08d}")
            el(5, "desc", f"{rng.choice(WORDS)} {tx_nr}")
            el(5, "periodNumber", str(d.month))
            el(5, "trDt", ds)
            if rng.random() < 0.5:
                el(5, "Source", f"{jid} {tx_nr}")
            if rng.random() < 0.5:
                el(5, "User", rng.choice(("JANSEN01", "DEVRIES", "BOT")))
            cs = f"CS{rng.randint(1, n_custsup):06d}" if rng.random() < 0.4 else None
            doc = f"{jid}-{tx_nr}"
            for ln, (cents, tp) in enumerate(tx, 1):
                w(f"{ind(5)}<trLine>{nl}")
                el(6, "nr", str(ln))
                el(6, "accID", rng.choice(accounts))
                el(6, "docRef", doc)
                el(6, "effDate", ds)
                if rng.random() < 0.2:
                    el(6, "settDate", ds)
                el(6, "desc", f"{rng.choice(WORDS)} regel {ln}")
                el(6, "amnt", fmt_amount(cents))
                el(6, "amntTp", tp)
                if cs:
                    el(6, "custSupID", cs)
                    el(6, "invRef", doc)
                if rng.random() < 0.1:
                    el(6, "cost", f"KP{rng.randint(1, 50)}")
                if rng.random() < 0.05:
                    el(6, "project", f"PRJ{rng.randint(1, 20)}")
                if rng.random() < 0.3:
                    vid, _, perc = rng.choice(VAT_CODES)
                    w(f"{ind(6)}<vat>{nl}")
                    el(7, "vatID", vid)
                    el(7, "vatPerc", perc)
                    el(7, "vatAmnt", fmt_amount(cents * int(perc) // 121))
                    el(7, "vatAmntTp", tp)
                    w(f"{ind(6)}</vat>{nl}")
                if rng.random() < 0.03:
                    w(f"{ind(6)}<currency>{nl}")
                    el(7, "curCode", "USD")
                    el(7, "curAmnt", fmt_amount(cents * 108 // 100))
                    w(f"{ind(6)}</currency>{nl}")
                w(f"{ind(5)}</trLine>{nl}")
            w(f"{ind(4)}</transaction>{nl}")
        w(f"{ind(3)}</journal>{nl}")
    w(f"{ind(2)}</transactions>{nl}")
    w(f"{ind(1)}</company>{nl}")
    w("</auditfile>\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lines", type=int, default=10_000, help="number of trLine elements")
    ap.add_argument("-o", "--output", default="-", help="output path ('-' = stdout)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--indent", choices=("tab", "none"), default="tab")
    ap.add_argument("--encoding", default="UTF-8", help="UTF-8 (default) or ISO-8859-1")
    ap.add_argument("--accounts", type=int, default=250)
    ap.add_argument("--custsup", type=int, default=2000)
    a = ap.parse_args(argv)
    if a.output == "-":
        sys.stdout.reconfigure(encoding=a.encoding, newline="\n")
        generate(sys.stdout, a.lines, a.seed, a.indent, a.encoding, a.accounts, a.custsup)
    else:
        with open(a.output, "w", encoding=a.encoding, newline="\n", buffering=1 << 20) as f:
            generate(f, a.lines, a.seed, a.indent, a.encoding, a.accounts, a.custsup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
