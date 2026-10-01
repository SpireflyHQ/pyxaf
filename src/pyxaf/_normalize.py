"""Build normalized model objects from raw records (CLAIR2 and XAF 3.0–4.0)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from .findings import FindingCollector
from .formats import Family, Version
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
    Period,
    Relation,
    RelationType,
    RgsRef,
    Side,
    Transaction,
    TransactionTotals,
    VatCode,
    VatLine,
)
from .raw import RawRecord
from .values import parse_amount, parse_date, parse_int

__all__ = ["NegativePolicy", "Normalizer"]

NegativePolicy = Literal["flip", "abs"]
_ZERO = Decimal(0)
_SIDES = {"D": Side.DEBIT, "C": Side.CREDIT, "d": Side.DEBIT, "c": Side.CREDIT}


def _s(value: str | None) -> str | None:
    """Strip; empty → None."""
    if value is None:
        return None
    v = value.strip()
    return v or None


# trLine text fields → Line attribute (4.0 names first: they win over 3.x names)
_XAF_LINE_TEXT = {
    "nr": "number",
    "accID": "account_id",
    "desc": "description",
    "docRef": "document_ref",
    "custSupID": "relation_id",
    "invRef": "invoice_ref",
    "orderRef": "order_ref",
    "receivingDocRef": "receiving_doc_ref",
    "shipDocRef": "shipping_doc_ref",
    "cost": "cost_center",
    "costID": "cost_center",
    "product": "product",
    "prodID": "product",
    "project": "project",
    "projID": "project",
    "workCostArrRef": "work_cost_arrangement",
    "bankAccNr": "bank_account",
    "offsetBankAccNr": "offset_bank_account",
}


class Normalizer:
    """Turns raw records into model objects and reports value-level findings."""

    def __init__(
        self,
        version: Version,
        *,
        negative_amounts: NegativePolicy = "flip",
        findings: FindingCollector,
        value_findings: bool = True,
        quality_findings: bool = True,
        file: int = 0,
    ) -> None:
        if negative_amounts not in ("flip", "abs"):
            raise ValueError("negative_amounts must be 'flip' or 'abs'")
        self.version = version
        self.family = version.family
        self.policy = negative_amounts
        self.findings = findings
        self.value_findings = value_findings
        self.quality_findings = quality_findings
        self.file = file
        self.line_seq = 0
        self.tx_seq = 0

    # ------------------------------------------------------------------ values
    def amount(self, rec: RawRecord, name: str, path: str) -> Decimal | None:
        text = rec.fields.get(name)
        if text is None:
            return None
        value = parse_amount(text)
        if value is None and self.value_findings:
            self.findings.add(
                "XAF3018",
                f"<{name}> is not a valid decimal number",
                line=rec.line,
                path=path,
                value=text,
            )
        return value

    def date(self, rec: RawRecord, name: str, path: str) -> dt.date | None:
        text = rec.fields.get(name)
        if text is None:
            return None
        value, tz = parse_date(text)
        if value is None:
            if self.value_findings:
                self.findings.add(
                    "XAF3017", f"<{name}> is not a valid date", line=rec.line, path=path, value=text
                )
        elif tz and self.quality_findings:
            self.findings.add(
                "XAF7008",
                f"<{name}> has a time-zone suffix; it was ignored",
                line=rec.line,
                path=path,
                value=text,
            )
        return value

    def sided(
        self, amount: Decimal | None, side_text: str | None, rec: RawRecord, path: str
    ) -> tuple[Side | None, Decimal | None]:
        """Return (written side, signed debit-positive amount) under the sign policy."""
        side = _SIDES.get(side_text.strip()) if side_text is not None else None
        if side is None and side_text is not None and self.value_findings:
            self.findings.add(
                "XAF3015",
                "debit/credit indicator must be D or C",
                line=rec.line,
                path=path,
                value=side_text,
            )
        if amount is None or side is None:
            return side, None
        if amount < 0:
            if self.quality_findings:
                self.findings.add(
                    "XAF7001",
                    "negative amount; "
                    + (
                        "counted on the opposite side"
                        if self.policy == "flip"
                        else "sign ignored (negative_amounts='abs')"
                    ),
                    line=rec.line,
                    path=path,
                    value=rec.fields.get("amnt"),
                )
            if self.policy == "abs":
                amount = -amount
        if not amount:
            return side, abs(amount)  # avoid -0.00
        return side, amount if side is Side.DEBIT else -amount

    @staticmethod
    def split(signed: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
        if signed is None:
            return None, None
        if signed >= 0:
            return signed, _ZERO
        return _ZERO, -signed

    # ------------------------------------------------------------------ header
    def header(self, rec: RawRecord) -> Header:
        f = rec.fields
        p = "/auditfile/header"
        if self.family is Family.CLAIR2:
            return Header(
                fiscal_year=_s(f.get("fiscalYear")),
                start_date=self.date(rec, "startDate", p),
                end_date=self.date(rec, "endDate", p),
                currency=_s(f.get("currencyCode")),
                created=self.date(rec, "dateCreated", p),
                software_name=_s(f.get("productID")),
                software_version=_s(f.get("productVersion")),
                declared_version=_s(f.get("auditfileVersion")),
                raw=rec,
            )
        return Header(
            fiscal_year=_s(f.get("fiscalYear")),
            start_date=self.date(rec, "startDate", p),
            end_date=self.date(rec, "endDate", p),
            currency=_s(f.get("curCode")),
            created=self.date(rec, "dateCreated", p),
            software_name=_s(f.get("softwareDesc")),
            software_version=_s(f.get("softwareVersion")),
            rgs_version=_s(f.get("RGSVersion")),
            raw=rec,
        )

    def address(self, rec: RawRecord, kind: Literal["street", "postal"]) -> Address:
        f = rec.fields
        return Address(
            kind=kind,
            street=_s(f.get("streetname")) or _s(f.get("address")),
            number=_s(f.get("number")),
            number_extension=_s(f.get("numberExtension")),
            property=_s(f.get("property")),
            city=_s(f.get("city")),
            postal_code=_s(f.get("postalCode")),
            region=_s(f.get("region")),
            country=_s(f.get("country")),
            raw=rec,
        )

    def addresses(self, rec: RawRecord) -> tuple[Address, ...]:
        return tuple(self.address(a, "street") for a in rec.all("streetAddress")) + tuple(
            self.address(a, "postal") for a in rec.all("postalAddress")
        )

    def company(self, rec: RawRecord | None, header: RawRecord | None) -> Company:
        if self.family is Family.CLAIR2:
            f = header.fields if header is not None else {}
            addr = Address(
                kind="street",
                street=_s(f.get("companyAddress")),
                city=_s(f.get("companyCity")),
                postal_code=_s(f.get("companyPostalCode")),
            )
            has_addr = any((addr.street, addr.city, addr.postal_code))
            return Company(
                name=_s(f.get("companyName")),
                identifier=_s(f.get("companyID")),
                tax_registration_id=_s(f.get("taxRegistrationNr")),
                addresses=(addr,) if has_addr else (),
                raw=header,
            )
        if rec is None:
            return Company(name=None)
        f = rec.fields
        return Company(
            name=_s(f.get("companyName")),
            identifier=_s(f.get("companyIdent")),
            commerce_number=_s(f.get("Commercenr")) or _s(f.get("commerceNr")),
            tax_registration_country=_s(f.get("taxRegistrationCountry")),
            tax_registration_id=_s(f.get("taxRegIdent")),
            addresses=self.addresses(rec),
            raw=rec,
        )

    # ------------------------------------------------------------- master data
    def account(self, rec: RawRecord, seq: int) -> LedgerAccount:
        f = rec.fields
        if self.family is Family.CLAIR2:
            return LedgerAccount(
                id=(f.get("accountID") or "").strip(),
                description=_s(f.get("accountDesc")),
                account_type=AccountType.from_code(f.get("accountType")),
                lead_code=_s(f.get("leadCode")),
                lead_description=_s(f.get("leadDescription")),
                seq=seq,
                file=self.file,
                raw=rec,
            )
        return LedgerAccount(
            id=(f.get("accID") or "").strip(),
            description=_s(f.get("accDesc")),
            account_type=AccountType.from_code(f.get("accTp")),
            lead_code=_s(f.get("leadCode")),
            lead_description=_s(f.get("leadDescription")),
            rgs=self.rgs_ref(rec),
            seq=seq,
            file=self.file,
            raw=rec,
        )

    def rgs_ref(self, rec: RawRecord) -> RgsRef | None:
        f = rec.fields
        if "RGScode" in f:
            return RgsRef.parse(f["RGScode"], "RGScode")
        for tax in rec.all("taxonomy"):
            for ep in tax.all("entryPoint"):
                concept = ep.fields.get("conceptRef")
                if concept:
                    # conceptRef may be a QName or URI fragment: keep the local part
                    local = concept.strip().rsplit("#", 1)[-1].rsplit(":", 1)[-1]
                    ref = RgsRef.parse(local, "taxonomy")
                    return RgsRef(
                        raw=concept,
                        code=ref.code,
                        extension=ref.extension,
                        source="taxonomy",
                        placeholder=ref.placeholder,
                    )
        for name in ("leadReference", "leadCode", "leadCrossRef"):
            value = f.get(name)
            if value and value.strip()[:1] in ("B", "W"):
                ref = RgsRef.parse(value, name)
                if ref.code is not None and len(ref.code) >= 4:
                    return ref
        return None

    def relation(self, rec: RawRecord, seq: int) -> Relation:
        f = rec.fields
        p = "/auditfile/company/customersSuppliers/customerSupplier"
        if self.family is Family.CLAIR2:
            return Relation(
                id=(f.get("custSupID") or "").strip(),
                name=_s(f.get("companyName")),
                relation_type=RelationType.from_code(f.get("type")),
                contact=_s(f.get("contact")),
                tax_registration_id=_s(f.get("taxRegistrationNr")),
                email=_s(f.get("eMail")),
                telephone=_s(f.get("telephone")),
                website=_s(f.get("website")),
                addresses=self.addresses(rec),
                seq=seq,
                file=self.file,
                raw=rec,
            )
        opening = closing = None
        if "opBalDesc" in f and "opBalTp" in f:
            amt = self.amount(rec, "opBalDesc", p)
            opening = self.sided(amt, f.get("opBalTp"), rec, p)[1]
        if "clBalDesc" in f and "clBalTp" in f:
            amt = self.amount(rec, "clBalDesc", p)
            closing = self.sided(amt, f.get("clBalTp"), rec, p)[1]
        return Relation(
            id=(f.get("custSupID") or "").strip(),
            name=_s(f.get("custSupName")),
            relation_type=RelationType.from_code(f.get("custSupTp")),
            contact=_s(f.get("contact")),
            tax_registration_country=_s(f.get("taxRegistrationCountry")),
            tax_registration_id=_s(f.get("taxRegIdent")),
            commerce_number=_s(f.get("commerceNr")),
            email=_s(f.get("eMail")),
            telephone=_s(f.get("telephone")),
            website=_s(f.get("website")),
            addresses=self.addresses(rec),
            opening_balance=opening,
            closing_balance=closing,
            seq=seq,
            file=self.file,
            raw=rec,
        )

    def vat_code(self, rec: RawRecord, seq: int) -> VatCode:
        f = rec.fields
        return VatCode(
            id=(f.get("vatID") or "").strip(),
            description=_s(f.get("vatDesc")),
            payable_account_id=_s(f.get("vatToPayAccID")),
            receivable_account_id=_s(f.get("vatToClaimAccID")),
            seq=seq,
            raw=rec,
        )

    def period(self, rec: RawRecord, seq: int) -> Period:
        f = rec.fields
        p = "/auditfile/company/periods/period"
        key = (f.get("periodNumber") or "").strip()
        return Period(
            key=key,
            number=parse_int(key),
            start_date=self.date(rec, "startDatePeriod", p),
            end_date=self.date(rec, "endDatePeriod", p),
            description=_s(f.get("periodDesc")),
            seq=seq,
            raw=rec,
        )

    def journal(self, rec: RawRecord, seq: int) -> Journal:
        f = rec.fields
        if self.family is Family.CLAIR2:
            return Journal(
                id=(f.get("journalID") or "").strip(),
                description=_s(f.get("description")),
                journal_type=JournalType.from_code(f.get("type")),
                seq=seq,
                file=self.file,
                raw=rec,
            )
        return Journal(
            id=(f.get("jrnID") or "").strip(),
            description=_s(f.get("desc")),
            journal_type=JournalType.from_code(f.get("jrnTp")),
            offset_account_id=_s(f.get("offsetAccID")),
            bank_account=_s(f.get("bankAccNr")),
            seq=seq,
            file=self.file,
            raw=rec,
        )

    def totals(self, rec: RawRecord | None) -> TransactionTotals | None:
        if rec is None:
            return None
        f = rec.fields
        count_text = f.get("linesCount", f.get("numberEntries"))
        return TransactionTotals(
            lines_count=parse_int(count_text),
            total_debit=parse_amount(f.get("totalDebit")),
            total_credit=parse_amount(f.get("totalCredit")),
        )

    # ------------------------------------------------------------ transactions
    def transaction(self, rec: RawRecord, journal_id: str | None) -> Transaction:
        if self.family is Family.CLAIR2:
            return self._clair2_transaction(rec, journal_id)
        f = rec.fields
        p = "/auditfile/company/transactions/journal/transaction"
        seq = self.tx_seq
        self.tx_seq += 1
        period_key = f.get("periodNumber")
        period_key = period_key.strip() if period_key is not None else None
        date = self.date(rec, "trDt", p)
        number = _s(f.get("nr"))
        lines = tuple(
            self._xaf_line(ln, p + "/trLine", journal_id, number, seq, period_key, date)
            for ln in rec.all("trLine")
        )
        return Transaction(
            seq=seq,
            journal_id=journal_id,
            number=number,
            description=_s(f.get("desc")),
            period_key=period_key,
            period_number=parse_int(period_key),
            date=date,
            source=_s(f.get("Source")) or _s(f.get("sourceID")),
            user=_s(f.get("User")) or _s(f.get("userID")),
            lines=lines,
            file=self.file,
            raw=rec,
        )

    def _vat(self, rec: RawRecord, path: str) -> VatLine:
        f = rec.fields
        if self.family is Family.CLAIR2:
            perc = self.amount(rec, "vatPercentage", path)
            amt = self.amount(rec, "vatAmount", path)
            return VatLine(
                code=_s(f.get("vatCode")),
                percentage=perc,
                amount=amt,
                side=None,
                signed_amount=None,
                raw=rec,
            )
        perc = self.amount(rec, "vatPerc", path)
        amt = self.amount(rec, "vatAmnt", path)
        side, signed = self.sided(amt, f.get("vatAmntTp"), rec, path)
        return VatLine(
            code=_s(f.get("vatID")),
            percentage=perc,
            amount=amt,
            side=side,
            signed_amount=signed,
            raw=rec,
        )

    def _xaf_line(
        self,
        rec: RawRecord,
        path: str,
        journal_id: str | None,
        tx_number: str | None,
        tx_seq: int | None,
        period_key: str | None,
        tx_date: dt.date | None,
    ) -> Line:
        f = rec.fields
        kw: dict[str, object] = {}
        for name, text in f.items():
            attr = _XAF_LINE_TEXT.get(name)
            if attr is not None:
                v = text.strip()
                if v and attr not in kw:
                    kw[attr] = v
        amount = self.amount(rec, "amnt", path)
        side, signed = self.sided(amount, f.get("amntTp"), rec, path)
        if signed is None:
            debit = credit = None
        elif signed >= 0:
            debit, credit = signed, _ZERO
        else:
            debit, credit = _ZERO, -signed
        if "effDate" in f:
            kw["effective_date"] = self.date(rec, "effDate", path)
        if "settDate" in f:
            kw["settlement_date"] = self.date(rec, "settDate", path)
        if "qntity" in f:
            kw["quantity"] = parse_amount(f["qntity"])
        children = rec.children
        if children:
            cur = children.get("currency")
            if cur:
                c0 = cur[0]
                kw["foreign"] = ForeignAmount(
                    currency=_s(c0.fields.get("curCode")),
                    amount=self.amount(c0, "curAmnt", path + "/currency"),
                )
            vat = children.get("vat")
            if vat:
                kw["vat"] = tuple(self._vat(v, path + "/vat") for v in vat)
        seq = self.line_seq
        self.line_seq = seq + 1
        return Line(
            seq=seq,
            amount=amount,
            side=side,
            signed_amount=signed,
            debit=debit,
            credit=credit,
            journal_id=journal_id,
            transaction_number=tx_number,
            transaction_seq=tx_seq,
            period_key=period_key,
            transaction_date=tx_date,
            file=self.file,
            raw=rec,
            **kw,  # type: ignore[arg-type]
        )

    def ob_line(self, rec: RawRecord) -> Line:
        f = rec.fields
        path = "/auditfile/company/openingBalance/obLine"
        amount = self.amount(rec, "amnt", path)
        side, signed = self.sided(amount, f.get("amntTp"), rec, path)
        debit, credit = self.split(signed)
        seq = self.line_seq
        self.line_seq += 1
        return Line(
            seq=seq,
            number=_s(f.get("nr")),
            account_id=_s(f.get("accID")),
            amount=amount,
            side=side,
            signed_amount=signed,
            debit=debit,
            credit=credit,
            file=self.file,
            raw=rec,
        )

    # ------------------------------------------------------------------ CLAIR2
    def _clair2_transaction(self, rec: RawRecord, journal_id: str | None) -> Transaction:
        f = rec.fields
        p = "/auditfile/transactions/journal/transaction"
        seq = self.tx_seq
        self.tx_seq += 1
        period_key = f.get("period")
        period_key = period_key.strip() if period_key is not None else None
        date = self.date(rec, "transactionDate", p)
        number = _s(f.get("transactionID"))
        lines = tuple(
            self._clair2_line(ln, p + "/line", journal_id, number, seq, period_key, date)
            for ln in rec.all("line")
        )
        return Transaction(
            seq=seq,
            journal_id=journal_id,
            number=number,
            description=_s(f.get("description")),
            period_key=period_key,
            period_number=parse_int(period_key),
            date=date,
            source=_s(f.get("sourceID")),
            lines=lines,
            file=self.file,
            raw=rec,
        )

    def _clair2_line(
        self,
        rec: RawRecord,
        path: str,
        journal_id: str | None,
        tx_number: str | None,
        tx_seq: int,
        period_key: str | None,
        tx_date: dt.date | None,
    ) -> Line:
        f = rec.fields
        get = f.get
        d = self.amount(rec, "debitAmount", path) if "debitAmount" in f else _ZERO
        c = self.amount(rec, "creditAmount", path) if "creditAmount" in f else _ZERO
        amount: Decimal | None
        side: Side | None
        signed: Decimal | None
        if d is None or c is None:
            amount = side = signed = None
        else:
            if (d < 0 or c < 0) and self.quality_findings:
                self.findings.add(
                    "XAF7001",
                    "negative debit/credit amount; "
                    + ("counted on the opposite side" if self.policy == "flip" else "sign ignored"),
                    line=rec.line,
                    path=path,
                    value=get("debitAmount") if d < 0 else get("creditAmount"),
                )
            if self.policy == "abs":
                d, c = abs(d), abs(c)
            if d and c and self.quality_findings:
                self.findings.add(
                    "XAF7013",
                    "line has both a debit and a credit amount; the difference is used",
                    line=rec.line,
                    path=path,
                )
            signed = abs(d - c) if d == c else d - c  # no -0.00
            if c and not d:
                amount, side = c, Side.CREDIT
            elif d and not c:
                amount, side = d, Side.DEBIT
            else:
                amount, side = abs(signed), Side.DEBIT if signed >= 0 else Side.CREDIT
        debit, credit = self.split(signed)
        vat = rec.children.get("vat")
        cur = rec.child("currency")
        foreign = None
        if cur is not None:
            cp = path + "/currency"
            cd = (
                self.amount(cur, "currencyDebitAmount", cp)
                if "currencyDebitAmount" in cur.fields
                else _ZERO
            )
            cc = (
                self.amount(cur, "currencyCreditAmount", cp)
                if "currencyCreditAmount" in cur.fields
                else _ZERO
            )
            fsigned = None if cd is None or cc is None else cd - cc
            foreign = ForeignAmount(
                currency=_s(cur.fields.get("currencyCode")),
                amount=None if fsigned is None else abs(fsigned),
                signed_amount=fsigned,
            )
        seq = self.line_seq
        self.line_seq += 1
        return Line(
            seq=seq,
            number=_s(get("recordID")),
            account_id=_s(get("accountID")),
            amount=amount,
            side=side,
            signed_amount=signed,
            debit=debit,
            credit=credit,
            description=_s(get("description")),
            document_ref=_s(get("documentID")),
            effective_date=self.date(rec, "effectiveDate", path) if "effectiveDate" in f else None,
            relation_id=_s(get("custSupID")),
            cost_center=_s(get("costDesc")),
            product=_s(get("productDesc")),
            project=_s(get("projectDesc")),
            vat=tuple(self._vat(v, path + "/vat") for v in vat) if vat else (),
            foreign=foreign,
            journal_id=journal_id,
            transaction_number=tx_number,
            transaction_seq=tx_seq,
            period_key=period_key,
            transaction_date=tx_date,
            file=self.file,
            raw=rec,
        )


def journal_id_from(fields: Mapping[str, str], family: Family) -> str | None:
    """The journal ID from a journal container's fields."""
    return _s(fields.get("journalID" if family is Family.CLAIR2 else "jrnID"))
