"""Normalized, version-independent data model.

All classes are slotted, keyword-only dataclasses with English names. Master-data classes are
frozen; the per-line classes (:class:`Line`, :class:`Transaction`, :class:`VatLine`,
:class:`ForeignAmount`) are not, because frozen construction costs ~3 µs per object on the hot
path — treat them as read-only all the same. Every object keeps
the :class:`~pyxaf.raw.RawRecord` it was built from in ``raw`` (lossless access to the exact text),
and ``Line.extra``/``Transaction.extra`` expose fields of that record without a normalized
attribute. The
"Normalized field mapping" section of the version guide documents which element of each version
maps to which attribute.

Code lists (journal type, account type, relation type) are data, not closed enums: the written
``code`` is always kept next to the interpreted ``kind``.
"""

from __future__ import annotations

import datetime as dt
import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .raw import RawRecord

__all__ = [
    "AccountKind",
    "AccountType",
    "Address",
    "Company",
    "ForeignAmount",
    "Header",
    "Journal",
    "JournalKind",
    "JournalType",
    "LedgerAccount",
    "Line",
    "OpeningBalance",
    "Period",
    "Relation",
    "RelationKind",
    "RelationType",
    "RgsRef",
    "Side",
    "Transaction",
    "TransactionTotals",
    "VatCode",
    "VatLine",
]


class Side(enum.StrEnum):
    """Debit or credit."""

    DEBIT = "D"
    CREDIT = "C"


class JournalKind(enum.StrEnum):
    """Interpretation of a journal type code (``jrnTp``)."""

    BANK = "bank"
    CASH = "cash"
    GOODS = "goods"
    MEMO = "memo"
    OPENING = "opening"
    PURCHASES = "purchases"
    SALES = "sales"
    PRODUCTION = "production"
    PAYROLL = "payroll"
    OTHER = "other"
    UNKNOWN = "unknown"


_JOURNAL_CODES = {
    "B": JournalKind.BANK,
    "C": JournalKind.CASH,
    "G": JournalKind.GOODS,
    "M": JournalKind.MEMO,
    "O": JournalKind.OPENING,
    "P": JournalKind.PURCHASES,
    "S": JournalKind.SALES,
    "T": JournalKind.PRODUCTION,
    "Y": JournalKind.PAYROLL,
    "Z": JournalKind.OTHER,
}


@dataclass(frozen=True, slots=True)
class JournalType:
    """A journal type: the written ``code`` and its interpretation."""

    code: str | None
    kind: JournalKind

    @classmethod
    def from_code(cls, code: str | None) -> JournalType:
        """Interpret an XAF ``jrnTp`` (B C G M O P S T Y Z) or a CLAIR2 free-text type."""
        if code is None or not code.strip():
            return cls(code, JournalKind.UNKNOWN)
        c = code.strip()
        kind = _JOURNAL_CODES.get(c.upper()) if len(c) == 1 else None
        if kind is None:
            low = c.lower()
            for words, k in (
                (("bank",), JournalKind.BANK),
                (("kas", "cash"), JournalKind.CASH),
                (("memo", "memoriaal", "dagboek"), JournalKind.MEMO),
                (("begin", "opening", "openings"), JournalKind.OPENING),
                (("inkoop", "purchase"), JournalKind.PURCHASES),
                (("verkoop", "sales", "sale"), JournalKind.SALES),
                (("loon", "salaris", "payroll"), JournalKind.PAYROLL),
                (("productie", "production"), JournalKind.PRODUCTION),
                (("goederen", "goods", "voorraad"), JournalKind.GOODS),
            ):
                if any(low.startswith(w) for w in words):
                    kind = k
                    break
        return cls(code, kind or JournalKind.UNKNOWN)


class AccountKind(enum.StrEnum):
    """Interpretation of a ledger account type."""

    BALANCE = "balance"
    PROFIT_LOSS = "profit_loss"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AccountType:
    """A ledger account type: the written ``code`` and its interpretation."""

    code: str | None
    kind: AccountKind

    @classmethod
    def from_code(cls, code: str | None) -> AccountType:
        """Interpret ``accTp`` (B/M/P) or a CLAIR2/ADF free-text type (heuristic)."""
        if code is None or not code.strip():
            return cls(code, AccountKind.UNKNOWN)
        c = code.strip().lower()
        if c in ("b", "balans", "balance", "bal", "balans-rekening"):
            kind = AccountKind.BALANCE
        elif c in ("p", "w", "v", "wv", "w&v", "pl", "p&l", "resultaat", "winst- en verlies"):
            kind = AccountKind.PROFIT_LOSS
        elif c in ("m", "mixed", "gemengd"):
            kind = AccountKind.MIXED
        elif c.startswith(("balans", "balance", "activa", "passiva", "asset", "liabilit")):
            kind = AccountKind.BALANCE
        elif c.startswith(("winst", "verlies", "resultaat", "kosten", "opbrengst", "profit")):
            kind = AccountKind.PROFIT_LOSS
        else:
            kind = AccountKind.UNKNOWN
        return cls(code, kind)


class RelationKind(enum.StrEnum):
    """Interpretation of a customer/supplier type (``custSupTp``)."""

    BOTH = "both"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RelationType:
    """A relation type: the written ``code`` and its interpretation."""

    code: str | None
    kind: RelationKind

    @classmethod
    def from_code(cls, code: str | None) -> RelationType:
        """Interpret ``custSupTp`` (B/C/S; also O, and D/F as written by some exporters/ADF)."""
        if code is None or not code.strip():
            return cls(code, RelationKind.UNKNOWN)
        c = code.strip().upper()
        kind = {
            "B": RelationKind.BOTH,
            "C": RelationKind.CUSTOMER,
            "S": RelationKind.SUPPLIER,
            "O": RelationKind.OTHER,
            "D": RelationKind.CUSTOMER,  # ADF "D(ebiteur)"; osFinancials
            "F": RelationKind.SUPPLIER,  # ADF "F" (leverancier)
        }.get(c)
        if kind is None:  # CLAIR2/ADF free text
            low = c.lower()
            cust = any(w in low for w in ("debiteur", "klant", "customer", "afnemer"))
            supp = any(w in low for w in ("crediteur", "leverancier", "supplier", "vendor"))
            if (cust and supp) or "both" in low or "beide" in low:
                kind = RelationKind.BOTH
            elif cust:
                kind = RelationKind.CUSTOMER
            elif supp:
                kind = RelationKind.SUPPLIER
            else:
                kind = RelationKind.UNKNOWN
        return cls(code, kind)


_RGS_CODE = re.compile(r"([BW](?:[A-Z][A-Za-z0-9]{2})*)(.*)", re.DOTALL)
#: Values exporters write when no real RGS code is available.
RGS_PLACEHOLDERS = frozenset({"", "0", "00", "000", "0000", "0001", "-", "rgs-1", "nvt", "n.v.t."})


@dataclass(frozen=True, slots=True, kw_only=True)
class RgsRef:
    """A reference from a ledger account to an RGS code.

    Attributes:
        raw: The value as written.
        code: The RGS reference code (``BIvaKouVvp``) or ``None`` if not recognisable.
        extension: Anything appended after the code (sub-codes, ``.01``…), stripped.
        source: Where the value came from: ``RGScode`` (4.0), ``taxonomy`` (3.2 conceptRef),
            ``taxonomies`` (3.1), ``leadReference``/``leadCode``/``leadCrossRef`` (vendor
            heuristics).
        placeholder: Whether the value is a known placeholder (``0000``, ``RGS-1``…).
    """

    raw: str
    code: str | None
    extension: str | None
    source: str
    placeholder: bool = False

    @classmethod
    def parse(cls, raw: str, source: str) -> RgsRef:
        """Split ``raw`` into code and extension and recognise placeholders."""
        value = raw.strip()
        if value.lower() in RGS_PLACEHOLDERS:
            return cls(raw=raw, code=None, extension=None, source=source, placeholder=True)
        m = _RGS_CODE.fullmatch(value)
        if not m:
            return cls(raw=raw, code=None, extension=None, source=source)
        ext = m.group(2).strip(" .-_") or None
        return cls(raw=raw, code=m.group(1), extension=ext, source=source)


@dataclass(frozen=True, slots=True, kw_only=True)
class Address:
    """A street or postal address."""

    kind: Literal["street", "postal"]
    street: str | None = None
    number: str | None = None
    number_extension: str | None = None
    property: str | None = None
    city: str | None = None
    postal_code: str | None = None
    region: str | None = None
    country: str | None = None
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Header:
    """File header.

    ``fiscal_year`` is the raw string (``"2024"`` or broken years ``"2023-2024"``).
    ``declared_version`` is CLAIR2's ``auditfileVersion`` or ADF's version field.
    """

    fiscal_year: str | None
    start_date: dt.date | None
    end_date: dt.date | None
    currency: str | None
    created: dt.date | None
    software_name: str | None
    software_version: str | None
    rgs_version: str | None = None
    declared_version: str | None = None
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Company:
    """The administration (company) the auditfile is about."""

    name: str | None
    identifier: str | None = None
    commerce_number: str | None = None
    tax_registration_country: str | None = None
    tax_registration_id: str | None = None
    addresses: tuple[Address, ...] = ()
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerAccount:
    """A general-ledger account."""

    id: str
    description: str | None
    account_type: AccountType
    lead_code: str | None = None
    lead_description: str | None = None
    rgs: RgsRef | None = None
    seq: int = 0
    file: int = 0
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Relation:
    """A customer and/or supplier.

    ``opening_balance``/``closing_balance`` (4.0 ``opBalDesc``/``clBalDesc``, which despite their
    names are amounts) are signed, debit-positive.
    """

    id: str
    name: str | None
    relation_type: RelationType
    contact: str | None = None
    tax_registration_country: str | None = None
    tax_registration_id: str | None = None
    commerce_number: str | None = None
    email: str | None = None
    telephone: str | None = None
    website: str | None = None
    addresses: tuple[Address, ...] = ()
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    seq: int = 0
    file: int = 0
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class VatCode:
    """A VAT code definition."""

    id: str
    description: str | None
    payable_account_id: str | None = None
    receivable_account_id: str | None = None
    seq: int = 0
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Period:
    """An accounting period.

    ``key`` is the period number as written (opaque, e.g. ``"01"``, ``"501"``); ``number`` is
    its integer value when numeric.
    """

    key: str
    number: int | None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    description: str | None = None
    seq: int = 0
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Journal:
    """A journal (dagboek)."""

    id: str
    description: str | None
    journal_type: JournalType
    offset_account_id: str | None = None
    bank_account: str | None = None
    seq: int = 0
    file: int = 0
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True, kw_only=True)
class VatLine:
    """VAT information on a transaction line. ``signed_amount`` is debit-positive."""

    code: str | None
    percentage: Decimal | None
    amount: Decimal | None
    side: Side | None
    signed_amount: Decimal | None
    raw: RawRecord | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True, kw_only=True)
class ForeignAmount:
    """Amount in a foreign currency. ``exchange_rate`` is only given by ADF."""

    currency: str | None
    amount: Decimal | None
    signed_amount: Decimal | None = None
    exchange_rate: Decimal | None = None


@dataclass(slots=True, kw_only=True)
class Line:
    """A transaction line (or opening-balance line), flattened with its transaction context.

    Amounts:
        ``amount`` is the value as written (may be negative) and ``side`` the written debit/credit
        indicator. ``signed_amount`` is debit-positive after applying the negative-amount policy;
        ``debit``/``credit`` are its non-negative presentation (one of them is zero).
        All are ``None`` when the written amount is invalid (a finding is reported).

    Context:
        ``journal_id``, ``transaction_number``, ``transaction_seq``, ``period_key`` and
        ``transaction_date`` repeat the enclosing transaction's values.
    """

    seq: int
    number: str | None = None
    account_id: str | None = None
    amount: Decimal | None
    side: Side | None
    signed_amount: Decimal | None
    debit: Decimal | None
    credit: Decimal | None
    description: str | None = None
    document_ref: str | None = None
    effective_date: dt.date | None = None
    settlement_date: dt.date | None = None
    relation_id: str | None = None
    invoice_ref: str | None = None
    order_ref: str | None = None
    receiving_doc_ref: str | None = None
    shipping_doc_ref: str | None = None
    cost_center: str | None = None
    cost_unit: str | None = None
    product: str | None = None
    project: str | None = None
    work_cost_arrangement: str | None = None
    bank_account: str | None = None
    offset_bank_account: str | None = None
    quantity: Decimal | None = None
    vat: tuple[VatLine, ...] = ()
    foreign: ForeignAmount | None = None
    journal_id: str | None = None
    transaction_number: str | None = None
    transaction_seq: int | None = None
    period_key: str | None = None
    transaction_date: dt.date | None = None
    file: int = 0
    raw: RawRecord | None = field(default=None, repr=False, compare=False)

    @property
    def extra(self) -> Mapping[str, str]:
        """Fields of the raw record without a normalized attribute (version/vendor specific)."""
        return _extra(self.raw)


@dataclass(slots=True, kw_only=True)
class Transaction:
    """A journal entry with its lines.

    ``period_key`` is the period number as written (opaque); ``period_number`` its integer value.
    """

    seq: int
    journal_id: str | None
    number: str | None
    description: str | None
    period_key: str | None
    period_number: int | None
    date: dt.date | None
    source: str | None = None
    user: str | None = None
    lines: tuple[Line, ...] = ()
    file: int = 0
    raw: RawRecord | None = field(default=None, repr=False, compare=False)

    @property
    def total_debit(self) -> Decimal:
        """Sum of the lines' ``debit`` values (invalid amounts count as zero)."""
        return sum((ln.debit for ln in self.lines if ln.debit is not None), Decimal(0))

    @property
    def total_credit(self) -> Decimal:
        """Sum of the lines' ``credit`` values (invalid amounts count as zero)."""
        return sum((ln.credit for ln in self.lines if ln.credit is not None), Decimal(0))

    @property
    def balanced(self) -> bool:
        """Whether the lines balance (debit = credit)."""
        return self.total_debit == self.total_credit

    @property
    def extra(self) -> Mapping[str, str]:
        """Fields of the raw record without a normalized attribute."""
        return _extra(self.raw)


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionTotals:
    """Control totals declared in the file.

    ``linesCount``/``numberEntries``, ``totalDebit`` and ``totalCredit`` as parsed values;
    ``None`` where absent or invalid.
    """

    lines_count: int | None
    total_debit: Decimal | None
    total_credit: Decimal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class OpeningBalance:
    """Unified opening balance, wherever the file stores it.

    Attributes:
        source: ``"element"`` (``openingBalance``), ``"transactions"`` (period-0 transactions or
            journal type O), or ``"none"``.
        lines: The opening-balance lines (signed amounts follow the negative-amount policy).
        date: ``opBalDate`` (3.x) if given.
        description: ``opBalDesc`` (3.x) if given.
        declared: Control totals as written in the file (element only).
    """

    source: Literal["element", "transactions", "none"]
    lines: tuple[Line, ...]
    date: dt.date | None = None
    description: str | None = None
    declared: TransactionTotals | None = None

    @property
    def total_debit(self) -> Decimal:
        """Sum of debit values."""
        return sum((ln.debit for ln in self.lines if ln.debit is not None), Decimal(0))

    @property
    def total_credit(self) -> Decimal:
        """Sum of credit values."""
        return sum((ln.credit for ln in self.lines if ln.credit is not None), Decimal(0))

    @property
    def balanced(self) -> bool:
        """Whether debit equals credit."""
        return self.total_debit == self.total_credit

    def by_account(self) -> dict[str, Decimal]:
        """Signed (debit-positive) opening balance per account ID."""
        out: dict[str, Decimal] = {}
        for ln in self.lines:
            if ln.account_id is not None and ln.signed_amount is not None:
                out[ln.account_id] = out.get(ln.account_id, Decimal(0)) + ln.signed_amount
        return out


# fields with a normalized attribute, per raw tag (for ``extra``)
_KNOWN: dict[str, frozenset[str]] = {
    "trLine": frozenset(
        {
            "nr",
            "accID",
            "docRef",
            "effDate",
            "settDate",
            "desc",
            "amnt",
            "amntTp",
            "custSupID",
            "invRef",
            "orderRef",
            "receivingDocRef",
            "shipDocRef",
            "costID",
            "cost",
            "prodID",
            "product",
            "projID",
            "project",
            "workCostArrRef",
            "bankAccNr",
            "offsetBankAccNr",
            "qntity",
        }
    ),
    "obLine": frozenset({"nr", "accID", "amnt", "amntTp"}),
    "line": frozenset(
        {
            "recordID",
            "accountID",
            "custSupID",
            "documentID",
            "effectiveDate",
            "description",
            "debitAmount",
            "creditAmount",
            "costDesc",
            "productDesc",
            "projectDesc",
        }
    ),
    "transaction": frozenset(
        {
            "nr",
            "desc",
            "periodNumber",
            "trDt",
            "sourceID",
            "Source",
            "userID",
            "User",
            "transactionID",
            "description",
            "period",
            "transactionDate",
        }
    ),
}


def _extra(raw: RawRecord | None) -> Mapping[str, str]:
    if raw is None:
        return {}
    known = _KNOWN.get(raw.tag, frozenset())
    return {k: v for k, v in raw.fields.items() if k not in known}
