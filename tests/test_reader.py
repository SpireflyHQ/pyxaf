from __future__ import annotations

import io
from decimal import Decimal

import pytest
import xafgen

import pyxaf
from pyxaf import AccountKind, JournalKind, RelationKind, Side, Version


def _sum(af: pyxaf.AuditFile) -> tuple[Decimal, Decimal, int]:
    d = c = Decimal(0)
    n = 0
    for ln in af.lines():
        n += 1
        d += ln.debit or 0
        c += ln.credit or 0
    return d, c, n


def test_every_iteration_reads_the_same_ledger(any_version: str, ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write(any_version, ledger)) as af:
        assert af.version == any_version
        d, c, n = _sum(af)
        ob = af.opening_balance()
        exp_d, exp_c = ledger.totals()
        ob_d, ob_c = ledger.ob_totals()
        if ob.source == "transactions":  # CLAIR2/ADF: opening balance as period-0 lines
            exp_d, exp_c = exp_d + ob_d, exp_c + ob_c
            n -= len(ledger.ob_lines)
        assert (d, c) == (exp_d, exp_c)
        assert n == len(ledger.lines())
        assert ob.source in ("element", "transactions")
        assert (ob.total_debit, ob.total_credit) == (ob_d, ob_c)
        assert ob.balanced
        assert af.company.name == ledger.company
        assert af.header.fiscal_year == "2024"
        assert not [f for f in af.findings if f.severity >= pyxaf.Severity.WARNING]


def test_master_data_xaf40(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("4.0", ledger)) as af:
        assert af.header.currency == "EUR"
        assert af.header.rgs_version == "3.8"
        assert af.header.software_name == "pyxaf-testgen"
        assert af.company.commerce_number == "12345678"
        assert af.company.addresses[0].city == "Utrecht"
        acc = af.accounts["1100"]
        assert acc.account_type.kind is AccountKind.BALANCE
        assert acc.rgs is not None and acc.rgs.code == "BLimBan" and acc.rgs.source == "RGScode"
        assert af.accounts["8000"].account_type.kind is AccountKind.PROFIT_LOSS
        rel = af.relations["D001"]
        assert rel.relation_type.kind is RelationKind.CUSTOMER
        assert rel.name == "Klant Één"
        assert rel.opening_balance == Decimal("100.00")
        assert af.vat_codes["H21"].payable_account_id == "1800"
        assert af.periods["1"].number == 1
        assert af.journals["VRK"].journal_type.kind is JournalKind.SALES
        totals = af.transaction_totals
        assert totals is not None and totals.lines_count == len(ledger.lines())


def test_rgs_from_32_taxonomy_and_31_taxonomies(ledger: xafgen.Ledger) -> None:
    for version, source in (("3.2", "taxonomy"), ("3.1", "taxonomies"), ("3.0", "taxonomies")):
        with pyxaf.open(xafgen.write(version, ledger)) as af:
            ref = af.accounts["1000"].rgs
            assert ref is not None and ref.code == "BLimKas" and ref.source == source


def test_transaction_and_line_details(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("4.0", ledger)) as af:
        tx = next(af.transactions())
        assert tx.journal_id == "MEM" and tx.number == "1" and tx.period_key == "1"
        assert tx.source == "testgen" and tx.user == "tester"
        assert tx.balanced
        first, revenue, _vat = tx.lines
        assert first.side is Side.DEBIT and first.relation_id == "D001"
        assert first.invoice_ref == first.document_ref
        assert revenue.vat[0].code == "H21"
        assert revenue.vat[0].percentage == Decimal("21.000")
        assert revenue.vat[0].signed_amount == -revenue.vat[0].amount  # credit VAT
        assert first.transaction_seq == tx.seq and first.journal_id == "MEM"
        assert first.raw is not None and first.raw.fields["accID"] == "1300"
        lines = list(af.lines())
        assert [ln.seq for ln in lines] == list(range(len(lines)))
        foreign = [ln for ln in lines if ln.foreign]
        assert foreign and foreign[0].foreign.currency == "USD"  # type: ignore[union-attr]


def test_reiteration_is_repeatable(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("3.2", ledger)) as af:
        first = [(t.seq, t.number) for t in af.transactions()]
        second = [(t.seq, t.number) for t in af.transactions()]
        assert first == second


def test_one_shot_stream_reads_once(ledger: xafgen.Ledger) -> None:
    class OneShot(io.RawIOBase):
        def __init__(self, data: bytes) -> None:
            self._b = io.BytesIO(data)

        def readable(self) -> bool:
            return True

        def seekable(self) -> bool:
            return False

        def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
            return self._b.readinto(b)

    af = pyxaf.open(OneShot(xafgen.write("4.0", ledger)))
    assert sum(1 for _ in af.transactions()) == 12
    with pytest.raises(pyxaf.PyxafError, match="only be read once"):
        list(af.transactions())


def test_file_path_and_file_object(tmp_path, ledger: xafgen.Ledger) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "x.xaf"
    p.write_bytes(xafgen.write("4.0", ledger))
    with pyxaf.open(p) as af1, p.open("rb") as fh, pyxaf.open(fh) as af2:
        assert _sum(af1) == _sum(af2)
        assert af1.files == (str(p),)


@pytest.mark.parametrize("style", ["element", "period0", "journalO"])
def test_opening_balance_unification(style: str, ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("3.2", ledger, ob_style=style)) as af:
        ob = af.opening_balance()
        assert ob.source == ("element" if style == "element" else "transactions")
        assert ob.by_account() == {
            "1100": Decimal("10000.00"),
            "0100": Decimal("2500.50"),
            "0500": Decimal("-12500.50"),
        }
        if style == "element":
            assert ob.declared is not None and ob.declared.lines_count == 3
            assert ob.description == "Beginbalans"


def test_no_opening_balance(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("4.0", ledger, ob_style="none")) as af:
        assert af.opening_balance().source == "none"
        assert af.opening_balance("element").source == "none"


def test_negative_amount_policy(ledger: xafgen.Ledger) -> None:
    led = xafgen.negative_line(xafgen.make_ledger())
    data = xafgen.write("4.0", led)
    with pyxaf.open(data) as af:
        tx = next(af.transactions())
        neg = tx.lines[1]
        assert neg.amount is not None and neg.amount < 0 and neg.side is Side.DEBIT
        assert neg.signed_amount == neg.amount  # -x D counts as x C
        assert neg.credit == -neg.amount and neg.debit == 0
        assert tx.balanced
        assert any(f.code == "XAF7001" for f in af.findings)
    with pyxaf.open(data, negative_amounts="abs") as af:
        tx = next(af.transactions())
        assert tx.lines[1].signed_amount == -tx.lines[1].amount  # type: ignore[operator]
        assert not tx.balanced
    with pytest.raises(ValueError, match="negative_amounts"):
        pyxaf.open(data, negative_amounts="ignore")  # type: ignore[arg-type]


def test_amounts_without_two_decimals() -> None:
    data = xafgen.write("3.2").replace(b"<amnt>10000.00</amnt>", b"<amnt>10000</amnt>", 1)
    with pyxaf.open(data) as af:
        assert af.opening_balance().lines[0].amount == Decimal("10000")


def test_invalid_amount_is_none_with_finding() -> None:
    data = xafgen.write("4.0").replace(b"<amnt>10000.00</amnt>", b"<amnt>NaN</amnt>", 1)
    with pyxaf.open(data) as af:
        ln = af.opening_balance().lines[0]
        assert ln.amount is None and ln.signed_amount is None and ln.debit is None
        assert ln.raw is not None and ln.raw.fields["amnt"] == "NaN"
        assert any(f.code == "XAF3018" and f.value == "NaN" for f in af.findings)


def test_unknown_vendor_elements_are_kept_in_raw_and_extra() -> None:
    data = xafgen.write("4.0").replace(
        b"<docRef>F0000</docRef>", b"<docRef>F0000</docRef><vendorField>x</vendorField>", 1
    )
    with pyxaf.open(data) as af:
        ln = next(ln for ln in af.lines() if ln.raw is not None and "vendorField" in ln.raw.fields)
        assert ln.extra == {"vendorField": "x"}


def test_repeated_leaf_is_kept() -> None:
    data = xafgen.write("4.0").replace(b"<desc>btw</desc>", b"<desc>btw</desc><desc>dup</desc>", 1)
    with pyxaf.open(data) as af:
        ln = next(ln for ln in af.lines() if ln.raw is not None and "desc" in ln.raw.children)
        assert [r.text for r in ln.raw.children["desc"]] == ["btw", "dup"]  # type: ignore[union-attr]


def test_encodings(ledger: xafgen.Ledger) -> None:
    for enc in ("UTF-8", "ISO-8859-1", "windows-1252"):
        with pyxaf.open(xafgen.write("3.2", ledger, encoding=enc)) as af:
            assert af.relations["D001"].name == "Klant Één"
    with pyxaf.open(xafgen.write("3.2", ledger, bom=True, declaration=False)) as af:
        assert af.relations["D001"].name == "Klant Één"


def test_cp1252_bytes_declared_latin1() -> None:
    data = xafgen.write("3.2", encoding="ISO-8859-1").replace(b"Huur januari", b"\x93quoted\x94")
    data = data.replace(b"Verkoop", b"\x93quoted\x94")
    with pyxaf.open(data) as af:
        list(af.transactions())
        assert any(f.code == "XAF1005" for f in af.findings)
    with pyxaf.open(data, repair={"latin1-as-cp1252"}) as af:
        descs = {t.description for t in af.transactions()} | {ln.description for ln in af.lines()}
        assert "“quoted”" in descs
        assert any(f.code == "XAF1011" for f in af.findings)
    with pyxaf.open(data, encoding="cp1252") as af:
        assert "“quoted”" in {ln.description for ln in af.lines()}


def test_control_characters_need_repair() -> None:
    data = xafgen.write("4.0").replace(b"<desc>btw</desc>", b"<desc>b\x02tw</desc>", 1)
    with pytest.raises(pyxaf.XmlSyntaxError, match="control character"):
        list(pyxaf.open(data).transactions())
    with pyxaf.open(data, repair={"control-chars"}) as af:
        assert "btw" in {ln.description for ln in af.lines()}
        assert any(f.code == "XAF1010" for f in af.findings)


def test_bare_ampersand_repair() -> None:
    data = xafgen.write("4.0").replace(b"<desc>btw</desc>", b"<desc>A & B &amp; C</desc>", 1)
    with pytest.raises(pyxaf.XmlSyntaxError):
        list(pyxaf.open(data).transactions())
    with pyxaf.open(data, repair={"bare-ampersand"}) as af:
        assert "A & B & C" in {ln.description for ln in af.lines()}
        assert any(f.code == "XAF1012" for f in af.findings)
    with pytest.raises(ValueError, match="unknown repair"):
        pyxaf.open(data, repair={"everything"})


def test_trailing_garbage_is_ignored_with_finding() -> None:
    with pyxaf.open(xafgen.write("4.0") + b"\x1a\x00junk") as af:
        list(af.transactions())
        assert any(f.code == "XAF1013" for f in af.findings)


def test_truncated_file_raises_on_iteration() -> None:
    data = xafgen.write("4.0")
    af = pyxaf.open(data[: len(data) // 2])  # master data is complete; the damage comes later
    assert af.company.name is not None
    with pytest.raises(pyxaf.XmlSyntaxError, match=r"truncated|not well-formed"):
        list(af.transactions())


def test_duplicate_master_data_first_wins() -> None:
    data = xafgen.write("4.0").replace(b"<accDesc>Kas</accDesc>", b"<accDesc>Kas</accDesc>", 1)
    dup = b"<ledgerAccount><accID>1000</accID><accDesc>Other</accDesc><accTp>B</accTp></ledgerAccount>"
    data = data.replace(b"</generalLedger>", dup + b"</generalLedger>", 1)
    with pyxaf.open(data) as af:
        assert af.accounts["1000"].description == "Kas"
        assert any(f.code == "XAF6002" for f in af.findings)


def test_continuation_files(ledger: xafgen.Ledger) -> None:
    data = xafgen.write("4.0", ledger)
    cut1 = data.index(b"<transaction>", data.index(b"<journal>") + 300)
    cut2 = data.index(b"<transaction>", cut1 + 500)
    # cut just before a transaction start tag (on its own line, keep indentation outside)
    cut1 = data.rindex(b"\n", 0, cut1) + 1
    cut2 = data.rindex(b"\n", 0, cut2) + 1
    first = data[:cut1]
    second = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<!-- Vervolgbestand 2 van 3 -->\n'
        + data[cut1:cut2]
    )
    third = b"<!-- Vervolgbestand 3 van 3 -->\n" + data[cut2:]
    with pyxaf.open([first, second, third]) as af:
        assert _sum(af) == _sum(pyxaf.open(data))
        assert len(af.files) == 3
    with pytest.raises(pyxaf.NotAnAuditfileError, match="continuation"):
        pyxaf.open([second, third])


def test_per_period_files(ledger: xafgen.Ledger) -> None:
    a = xafgen.make_ledger(seed=1, n_tx=6)
    b = xafgen.make_ledger(seed=2, n_tx=4)
    with pyxaf.open([xafgen.write("4.0", a), xafgen.write("4.0", b, ob_style="none")]) as af:
        txs = list(af.transactions())
        assert len(txs) == 10
        assert [t.file for t in txs] == [0] * 6 + [1] * 4
        assert [t.seq for t in txs] == list(range(10))
        assert len(af.accounts) == len(a.accounts)
        totals = af.transaction_totals
        assert totals is not None and totals.lines_count == 30


def test_journals_without_iterating(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("4.0", ledger)) as af:
        assert set(af.journals) == {"MEM", "VRK", "BNK"}


def test_adf_specifics(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("ADF", ledger)) as af:
        assert af.version is Version.ADF
        assert af.header.declared_version == "CLAIR1.00.00"
        assert af.company.name == ledger.company
        assert af.transaction_totals is not None
        assert af.transaction_totals.lines_count == len(ledger.lines()) + len(ledger.ob_lines)
        assert af.relations["D001"].relation_type.kind is RelationKind.CUSTOMER
        tx = next(af.transactions())
        assert tx.period_key == "0" and tx.journal_id == "BEGIN"


def test_clair2_specifics(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("CLAIR2", ledger)) as af:
        assert af.header.declared_version == "CLAIR2.00.00"
        assert af.company.identifier == ledger.company_ident
        assert af.relations["B001"].relation_type.kind is RelationKind.BOTH
        assert af.accounts["8000"].account_type.kind is AccountKind.PROFIT_LOSS
        vat_lines = [v for ln in af.lines() for v in ln.vat]
        assert vat_lines and vat_lines[0].code == "H21"


def test_repr(ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write("4.0", ledger)) as af:
        assert "4.0" in repr(af) and ledger.company in repr(af)


def test_raw_view(any_version: str, ledger: xafgen.Ledger) -> None:
    with pyxaf.open(xafgen.write(any_version, ledger)) as af:
        assert af.raw.header is not None
        pairs = list(af.raw.transactions())
        assert pairs
        journal_id, rec = pairs[-1]
        assert journal_id is not None and rec.line > 0
        if any_version not in ("ADF", "CLAIR2"):
            assert rec.tag == "transaction" and "trLine" in rec.children
            assert af.raw.company is not None and af.raw.company.fields["companyName"]


def test_adf_undecodable_bytes_are_reported() -> None:
    data = xafgen.write("ADF").replace(b"Inventaris", b"Inventari\x81", 1)
    with pyxaf.open(data) as af:
        list(af.transactions())
        assert any(f.code == "XAF1006" for f in af.findings)


def test_decompressed_limit_can_be_raised() -> None:
    import gzip

    big = gzip.compress(
        xafgen.write("4.0").replace(
            b"<desc>btw</desc>", b"<desc>" + b" " * 3_000_000 + b"</desc>", 1
        )
    )
    with pytest.raises(pyxaf.LimitExceededError):
        list(pyxaf.open(big).lines())
    with pyxaf.open(big, max_decompressed_size=50_000_000) as af:
        assert sum(1 for _ in af.lines()) == 36
