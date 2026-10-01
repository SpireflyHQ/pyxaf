"""Reader for the fixed-width ASCII auditfile (ADF, ``CLAIR1.00.00``, 1999).

Layout from the Belastingdienst *Handleiding Auditfile* (Dec 2001, erratum Feb 2003): one header
line followed by mutation lines, CR LF separated, fields left-justified and space-padded. ADF has no
master-data section: accounts, relations and journals are collected from the mutation lines.
"""

from __future__ import annotations

import codecs
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

from ._normalize import NegativePolicy
from ._source import Source
from .findings import FindingCollector
from .models import (
    AccountType,
    Address,
    Company,
    ForeignAmount,
    Header,
    Journal,
    JournalType,
    LedgerAccount,
    Line,
    Relation,
    RelationType,
    Side,
    Transaction,
    TransactionTotals,
    VatLine,
)
from .raw import RawRecord
from .values import parse_adf_amount, parse_adf_date, parse_int

if TYPE_CHECKING:
    from .reader import _Master

__all__ = ["ADF_HEADER", "ADF_LINE", "AdfField", "AdfReader"]


class AdfField(NamedTuple):
    """A fixed-width ADF field (``start`` is 1-based as in the manual)."""

    name: str
    start: int
    length: int
    required: bool = False
    kind: str = "C"  # C character, N numeric (decimals), D date
    decimals: int = 0

    def slice(self, line: str) -> str:
        return line[self.start - 1 : self.start - 1 + self.length]


ADF_HEADER: tuple[AdfField, ...] = (
    AdfField("versie", 1, 12, True),
    AdfField("boekhoudpakket", 13, 50),
    AdfField("administratiecode", 63, 20),
    AdfField("jaarPeriode", 83, 15, True),
    AdfField("fiscaalnummer", 98, 15),
    AdfField("naamOnderneming", 113, 50, True),
    AdfField("adres", 163, 30),
    AdfField("plaats", 193, 30),
    AdfField("aantalMutaties", 223, 10, False, "N", 0),
    AdfField("datumAanmaak", 233, 10, False, "D"),
    AdfField("tellingDebet", 243, 16, False, "N", 2),
    AdfField("tellingCredit", 259, 16, False, "N", 2),
)

ADF_LINE: tuple[AdfField, ...] = (
    AdfField("dagboekcode", 1, 20),
    AdfField("dagboekomschrijving", 21, 30),
    AdfField("periode", 51, 5),
    AdfField("volgnummer", 56, 10),
    AdfField("regelnummer", 66, 5, False, "N", 0),
    AdfField("identificatieJournaalpost", 71, 20),
    AdfField("verwerkingsdatum", 91, 10, False, "D"),
    AdfField("grootboekrekeningcode", 101, 15, True),
    AdfField("soortGrootboekrekening", 116, 5),
    AdfField("cluster", 121, 15),
    AdfField("grootboekrekeningnaam", 136, 30, True),
    AdfField("mutatiedatum", 166, 10, True, "D"),
    AdfField("boekstuknummer", 176, 15),
    AdfField("soortMutatie", 191, 5),
    AdfField("relatieAndereAdministraties", 196, 15),
    AdfField("kostenplaats", 211, 15),
    AdfField("kostensoort", 226, 15),
    AdfField("kostendrager", 241, 15),
    AdfField("omschrijving", 256, 30, True),
    AdfField("debet", 286, 16, True, "N", 2),
    AdfField("credit", 302, 16, True, "N", 2),
    AdfField("btwCode", 318, 5),
    AdfField("valuta", 323, 10),
    AdfField("koers", 333, 13, False, "N", 6),
    AdfField("debCredNummer", 346, 15),
    AdfField("debCredSoort", 361, 5),
    AdfField("debCredFiscaalNummer", 366, 16),
    AdfField("debCredNaam", 382, 30),
    AdfField("debCredAdres", 412, 30),
    AdfField("debCredPostcode", 442, 10),
    AdfField("debCredPlaats", 452, 30),
    AdfField("debCredLand", 482, 15),
)
ADF_LINE_LENGTH = 496
_ZERO = Decimal(0)


def _s(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


class AdfReader:
    """Streams an ADF file (re-reading the source per pass)."""

    def __init__(
        self,
        source: Source,
        *,
        encoding: str,
        policy: NegativePolicy,
        findings: FindingCollector,
        value_findings: bool = True,
    ) -> None:
        self._src = source
        self._encoding = encoding
        self._policy = policy
        self._findings = findings
        self._vf = value_findings
        self.company: Company = Company(name=None)
        self.totals: TransactionTotals | None = None
        self.header_raw: RawRecord | None = None
        self._reported_replacements = False

    # ------------------------------------------------------------------ lines
    def _lines(self) -> Iterator[tuple[int, str]]:
        decoder = codecs.getincrementaldecoder(self._encoding)(errors="replace")
        buf = ""
        n = 0
        replaced = 0
        for chunk in self._src.chunks():
            text = decoder.decode(chunk)
            replaced += text.count("\ufffd")
            buf += text
            *lines, buf = buf.split("\n")
            for ln in lines:
                n += 1
                yield n, ln.rstrip("\r\x1a")
        buf += decoder.decode(b"", final=True)
        if buf.rstrip("\r\x1a").strip():
            yield n + 1, buf.rstrip("\r\x1a")
        if replaced and self._vf and not self._reported_replacements:
            self._reported_replacements = True
            self._findings.add(
                "XAF1006",
                f"{replaced} byte(s) not valid in {self._encoding} were replaced by U+FFFD "
                "(pass encoding= to choose another code page)",
            )

    def _record(self, tag: str, spec: tuple[AdfField, ...], text: str, line_no: int) -> RawRecord:
        return RawRecord(tag, {f.name: f.slice(text) for f in spec}, None, line_no)

    def _num(self, rec: RawRecord, f: AdfField) -> Decimal | None:
        text = rec.fields[f.name]
        if not text.strip():
            return None
        value = parse_adf_amount(text, f.decimals)
        if value is None and self._vf:
            self._findings.add(
                "XAF3018",
                f"ADF field {f.name} is not a valid number",
                line=rec.line,
                path=f.name,
                value=text,
            )
        return value

    def _date(self, rec: RawRecord, name: str) -> object:
        text = rec.fields[name]
        if not text.strip():
            return None
        value = parse_adf_date(text)
        if value is None and self._vf:
            self._findings.add(
                "XAF3017",
                f"ADF field {name} is not a valid date",
                line=rec.line,
                path=name,
                value=text,
            )
        return value

    def _check(self, rec: RawRecord, spec: tuple[AdfField, ...], text: str) -> None:
        if not self._vf:
            return
        for f in spec:
            if f.required and not rec.fields[f.name].strip():
                self._findings.add(
                    "XAF3010", f"required ADF field {f.name} is empty", line=rec.line, path=f.name
                )
        if len(text) > (ADF_LINE_LENGTH if spec is ADF_LINE else 274) + 2:
            self._findings.add(
                "XAF3050",
                f"line is {len(text)} characters long; data beyond the last field is ignored",
                line=rec.line,
            )

    # ---------------------------------------------------------------- master
    def load_master(self, master: _Master) -> None:
        """Read the header and collect accounts, relations and journals from all lines."""
        hdr_fields = {f.name: f for f in ADF_HEADER}
        acc_seq = rel_seq = 0
        it = self._lines()
        for n, text in it:
            if n == 1:
                rec = self._record("adfHeader", ADF_HEADER, text, n)
                self.header_raw = rec
                self._check(rec, ADF_HEADER, text)
                f = rec.fields
                created = self._date(rec, "datumAanmaak")
                master.header = Header(
                    fiscal_year=_s(f["jaarPeriode"]),
                    start_date=None,
                    end_date=None,
                    currency=None,
                    created=created,  # type: ignore[arg-type]
                    software_name=_s(f["boekhoudpakket"]),
                    software_version=None,
                    declared_version=_s(f["versie"]),
                    raw=rec,
                )
                addr = Address(kind="street", street=_s(f["adres"]), city=_s(f["plaats"]))
                self.company = Company(
                    name=_s(f["naamOnderneming"]),
                    identifier=_s(f["administratiecode"]),
                    tax_registration_id=_s(f["fiscaalnummer"]),
                    addresses=(addr,) if addr.street or addr.city else (),
                    raw=rec,
                )
                count = f["aantalMutaties"].strip()
                self.totals = TransactionTotals(
                    lines_count=parse_int(count) if count else None,
                    total_debit=self._num(rec, hdr_fields["tellingDebet"]),
                    total_credit=self._num(rec, hdr_fields["tellingCredit"]),
                )
                continue
            if not text.strip():
                continue
            rec = self._record("adfLine", ADF_LINE, text, n)
            f = rec.fields
            acc_id = f["grootboekrekeningcode"].strip()
            if acc_id and acc_id not in master.accounts:
                master.accounts[acc_id] = LedgerAccount(
                    id=acc_id,
                    description=_s(f["grootboekrekeningnaam"]),
                    account_type=AccountType.from_code(f["soortGrootboekrekening"]),
                    lead_code=_s(f["cluster"]),
                    seq=acc_seq,
                    raw=rec,
                )
                acc_seq += 1
            rel_id = f["debCredNummer"].strip()
            if rel_id and rel_id not in master.relations:
                addr = Address(
                    kind="street",
                    street=_s(f["debCredAdres"]),
                    postal_code=_s(f["debCredPostcode"]),
                    city=_s(f["debCredPlaats"]),
                    country=_s(f["debCredLand"]),
                )
                master.relations[rel_id] = Relation(
                    id=rel_id,
                    name=_s(f["debCredNaam"]),
                    relation_type=RelationType.from_code(f["debCredSoort"]),
                    tax_registration_id=_s(f["debCredFiscaalNummer"]),
                    addresses=(addr,) if any((addr.street, addr.city, addr.country)) else (),
                    seq=rel_seq,
                    raw=rec,
                )
                rel_seq += 1
            jr_id = f["dagboekcode"].strip()
            if jr_id not in master.journals:
                master.journals[jr_id] = Journal(
                    id=jr_id,
                    description=_s(f["dagboekomschrijving"]),
                    journal_type=JournalType.from_code(f["dagboekomschrijving"]),
                    seq=len(master.journals),
                    raw=rec,
                )
        master.journals_complete = True

    def load_journals(self, journals: dict[str, Journal]) -> None:
        """Journals are collected by :meth:`load_master` already."""

    # ------------------------------------------------------------ transactions
    def raw_lines(self) -> Iterator[tuple[str | None, RawRecord]]:
        """Yield ``(journal_id, line_record)`` for every mutation line."""
        for n, text in self._lines():
            if n == 1 or not text.strip():
                continue
            rec = self._record("adfLine", ADF_LINE, text, n)
            yield rec.fields["dagboekcode"].strip() or None, rec

    def transactions(self) -> Iterator[Transaction]:
        """Group consecutive lines with the same journal, period and entry number."""
        spec = {f.name: f for f in ADF_LINE}
        tx_seq = 0
        line_seq = 0
        current_key: tuple[str, ...] | None = None
        pending: list[Line] = []
        first_rec: RawRecord | None = None
        for n, text in self._lines():
            if n == 1 or not text.strip():
                continue
            rec = self._record("adfLine", ADF_LINE, text, n)
            self._check(rec, ADF_LINE, text)
            f = rec.fields
            key = (
                f["dagboekcode"].strip(),
                f["periode"].strip(),
                f["volgnummer"].strip(),
                f["identificatieJournaalpost"].strip(),
            )
            if key != current_key and pending:
                yield self._tx(first_rec, pending, tx_seq)
                tx_seq += 1
                pending = []
            if not pending:
                current_key = key
                first_rec = rec
            pending.append(self._line(rec, spec, line_seq, tx_seq))
            line_seq += 1
        if pending:
            yield self._tx(first_rec, pending, tx_seq)

    def _tx(self, rec: RawRecord | None, lines: list[Line], seq: int) -> Transaction:
        assert rec is not None
        f = rec.fields
        period_key = f["periode"].strip() or None
        date = lines[0].transaction_date
        return Transaction(
            seq=seq,
            journal_id=f["dagboekcode"].strip(),
            number=_s(f["volgnummer"]) or _s(f["identificatieJournaalpost"]),
            description=None,
            period_key=period_key,
            period_number=parse_int(period_key),
            date=date,
            lines=tuple(lines),
            raw=rec,
        )

    def _line(self, rec: RawRecord, spec: dict[str, AdfField], seq: int, tx_seq: int) -> Line:
        f = rec.fields
        d = self._num(rec, spec["debet"]) or _ZERO
        c = self._num(rec, spec["credit"]) or _ZERO
        if (d < 0 or c < 0) and self._vf:
            self._findings.add("XAF7001", "negative debit/credit amount", line=rec.line)
        if self._policy == "abs":
            d, c = abs(d), abs(c)
        if d and c and self._vf:
            self._findings.add(
                "XAF7013", "line has both a debit and a credit amount", line=rec.line
            )
        signed = abs(d - c) if d == c else d - c  # no -0.00
        if c and not d:
            amount, side = c, Side.CREDIT
        elif d and not c:
            amount, side = d, Side.DEBIT
        else:
            amount, side = abs(signed), Side.DEBIT if signed >= 0 else Side.CREDIT
        debit, credit = (signed, _ZERO) if signed >= 0 else (_ZERO, -signed)
        currency = _s(f["valuta"])
        rate = self._num(rec, spec["koers"])
        foreign = (
            ForeignAmount(currency=currency, amount=None, exchange_rate=rate)
            if currency or rate is not None
            else None
        )
        vat_code = _s(f["btwCode"])
        period_key = f["periode"].strip() or None
        return Line(
            seq=seq,
            number=_s(f["regelnummer"]),
            account_id=_s(f["grootboekrekeningcode"]),
            amount=amount,
            side=side,
            signed_amount=signed,
            debit=debit,
            credit=credit,
            description=_s(f["omschrijving"]),
            document_ref=_s(f["boekstuknummer"]),
            effective_date=self._date(rec, "mutatiedatum"),  # type: ignore[arg-type]
            relation_id=_s(f["debCredNummer"]),
            cost_center=_s(f["kostenplaats"]),
            cost_unit=_s(f["kostendrager"]),
            vat=(
                VatLine(code=vat_code, percentage=None, amount=None, side=None, signed_amount=None),
            )
            if vat_code
            else (),
            foreign=foreign,
            journal_id=f["dagboekcode"].strip(),
            transaction_number=_s(f["volgnummer"]) or _s(f["identificatieJournaalpost"]),
            transaction_seq=tx_seq,
            period_key=period_key,
            transaction_date=self._date(rec, "verwerkingsdatum"),  # type: ignore[arg-type]
            raw=rec,
        )
