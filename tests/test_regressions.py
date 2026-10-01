"""Regression tests for defects found in code review (2026-10-01)."""

from __future__ import annotations

import decimal
import gzip
import io
import re
from decimal import Decimal
from pathlib import Path

import pytest
import xafgen

import pyxaf
from pyxaf.values import parse_adf_amount, parse_amount, parse_date, parse_double, parse_int


def _codes(report: pyxaf.ValidationReport) -> set[str]:
    return set(report.counts)


def test_whitespace_between_records_does_not_accumulate() -> None:
    tx = b"<transaction><nr>{n}</nr><periodNumber>1</periodNumber><trDt>2024-01-01</trDt></transaction>"
    body = b"".join(b"\n        " + tx.replace(b"{n}", str(i).encode()) for i in range(5000))
    data = xafgen.write("4.0").replace(b"<jrnTp>M</jrnTp>", b"<jrnTp>M</jrnTp>" + body, 1)
    with pyxaf.open(data, max_text_size=10_000) as af:
        assert sum(1 for _ in af.transactions()) == 5012


@pytest.mark.parametrize("cut", [5, 40])
def test_corrupt_gzip_is_a_pyxaf_error(cut: int) -> None:
    blob = gzip.compress(xafgen.write("4.0"))
    with pytest.raises(pyxaf.PyxafError):
        list(pyxaf.open(blob[:-cut]).transactions())
    report = pyxaf.validate(blob[: len(blob) // 2])
    assert not report.ok


@pytest.mark.parametrize("encoding", ["UTF-32", "UTF-7", "cp1250"])
def test_encodings_expat_does_not_support(encoding: str) -> None:
    text = xafgen.write("4.0").decode().replace('encoding="UTF-8"', f'encoding="{encoding}"')
    with pyxaf.open(text.encode(encoding)) as af:
        assert af.relations["D001"].name == "Klant Één"
        assert sum(1 for _ in af.lines()) == 36


def test_unknown_encodings() -> None:
    data = xafgen.write("4.0").replace(b'encoding="UTF-8"', b'encoding="yTF-8"', 1)
    with pyxaf.open(data) as af:
        list(af.transactions())
        assert any(f.code == "XAF1003" for f in af.findings)
    with pytest.raises(ValueError, match="unknown encoding"):
        pyxaf.open(xafgen.write("4.0"), encoding="foo")


def test_cp1252_undefined_bytes_are_lenient() -> None:
    data = xafgen.write("3.2", encoding="ISO-8859-1").replace(b"Verkoop", b"\x81\x93x\x94", 1)
    with pyxaf.open(data, repair={"latin1-as-cp1252"}) as af:
        assert "\x81“x”" in {ln.description for ln in af.lines()}
        assert any(f.code == "XAF1006" for f in af.findings)


def test_continuation_starting_with_end_tag() -> None:
    data = xafgen.write("4.0")
    cut = data.index(b"</journal>")
    first, second = data[:cut], b"<!-- Vervolgbestand 2 van 2 -->\n" + data[cut:]
    with pyxaf.open([first, second]) as af, pyxaf.open(data) as whole:
        assert sum(1 for _ in af.lines()) == sum(1 for _ in whole.lines())


@pytest.mark.parametrize("version", ["CLAIR2", "ADF"])
def test_written_totals_for_negative_amounts(version: str) -> None:
    led = xafgen.negative_line(xafgen.make_ledger())
    report = pyxaf.validate(xafgen.write(version, led))
    assert not ({"XAF5007", "XAF5008"} & _codes(report)), str(report)


def test_adf_with_bom() -> None:
    plain = pyxaf.open(xafgen.write("ADF"))
    bom = pyxaf.open(b"\xef\xbb\xbf" + xafgen.write("ADF"))
    assert bom.transaction_totals == plain.transaction_totals
    assert bom.header.declared_version == "CLAIR1.00.00"


def test_findings_not_duplicated_on_repeated_passes() -> None:
    led = xafgen.negative_line(xafgen.make_ledger())
    af = pyxaf.open(xafgen.write("4.0", led))
    for _ in range(3):
        list(af.transactions())
    assert sum(f.code == "XAF7001" for f in af.findings) == 1


def test_structure_gaps_found_in_review() -> None:
    empty_line = xafgen.write("4.0").replace(b"</transaction>", b"<trLine/></transaction>", 1)
    assert "XAF3010" in _codes(pyxaf.validate(empty_line))
    both = xafgen.write("CLAIR2").replace(
        b"<creditAmount>", b"<debitAmount>0.00</debitAmount><creditAmount>", 1
    )
    assert "XAF3012" in _codes(pyxaf.validate(both))
    nested = xafgen.write("4.0").replace(b"<accID>1300</accID>", b"<accID>1300<b/></accID>", 1)
    assert "XAF3011" in _codes(pyxaf.validate(nested))


def test_decimal_facets_apply_to_values() -> None:
    data = xafgen.write("4.0").replace(b"<amnt>10000.00</amnt>", b"<amnt>10000.000</amnt>", 1)
    assert "XAF3018" not in _codes(pyxaf.validate(data))


def test_non_ascii_digits_are_rejected() -> None:
    assert parse_amount("١٠٠") is None
    assert parse_date("٢٠٢٤-01-01")[0] is None


def test_adf_from_one_shot_stream() -> None:
    class OneShot(io.RawIOBase):
        def __init__(self, data: bytes) -> None:
            self._b = io.BytesIO(data)

        def readable(self) -> bool:
            return True

        def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
            return self._b.readinto(b)

    with pyxaf.open(OneShot(xafgen.write("ADF"))) as af:
        assert sum(1 for _ in af.transactions()) == 13


# --------------------------------------------------------------------------------------------
# Second review (2026-10-01): one test per finding (R01–R17).

_CLEAN = xafgen.write("4.0")
_BROKEN = _CLEAN.replace(b"<amnt>3612.16</amnt>", b"<amnt>3621.16</amnt>", 1)


@pytest.mark.parametrize(
    ("data", "kwargs"),
    [
        (_BROKEN, {"max_findings_per_code": 0}),
        (_BROKEN.split(b"\n", 1)[1], {"max_findings": 1}),  # a warning takes the only slot
        (_BROKEN, {"max_findings": 0}),
    ],
    ids=["per-code-0", "warning-first", "total-0"],
)
def test_r01_limits_never_change_the_verdict(data: bytes, kwargs: dict[str, int]) -> None:
    report = pyxaf.validate(data, **kwargs)
    assert not report.ok
    assert report.max_severity is pyxaf.Severity.ERROR
    assert report.severity_counts[pyxaf.Severity.ERROR] == 2
    assert report.to_dict()["severity_counts"]["ERROR"] == 2
    assert "2 error(s)" in str(report)


def test_r01_silenced_codes_do_not_count() -> None:
    report = pyxaf.validate(
        _BROKEN, severity_overrides={"XAF5010": None, "XAF5007": pyxaf.Severity.INFO}
    )
    assert report.ok
    assert report.max_severity is pyxaf.Severity.INFO


@pytest.mark.parametrize("rounding", [decimal.ROUND_UP, decimal.ROUND_FLOOR, decimal.ROUND_DOWN])
def test_r02_amounts_do_not_depend_on_the_decimal_context(rounding: str) -> None:
    with decimal.localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = rounding
        ctx.traps[decimal.Inexact] = True
        with pyxaf.open(_CLEAN) as af:
            tx = next(af.transactions())
            assert str(tx.lines[1].signed_amount) == "-2985.26"
            assert str(tx.lines[1].credit) == "2985.26"
            assert str(tx.total_credit) == "3612.16"
            assert tx.balanced
        assert pyxaf.validate(_CLEAN).ok


def test_r02_adf_amounts_are_exact_in_any_context() -> None:
    with decimal.localcontext() as ctx:
        ctx.prec = 2
        assert parse_adf_amount("10.239,87") == Decimal("10239.87")
        assert parse_adf_amount("-1023987") == Decimal("-10239.87")


def test_r02_excess_digits_are_not_rounded_away() -> None:
    data = _CLEAN.replace(b"<amnt>3612.16</amnt>", b"<amnt>3612.16" + b"0" * 30 + b"1</amnt>", 1)
    codes = _codes(pyxaf.validate(data))
    assert {"XAF3018", "XAF5010"} <= codes  # too many decimals, and no longer balanced


@pytest.mark.parametrize(
    ("value", "round_", "expected"),
    [
        ("3612.16", False, 361216),
        ("3612.161", False, None),
        ("3612.165", True, 361216),  # half to even
        ("3612.175", True, 361218),
        ("3612.1651", True, 361217),
        ("-3612.165", True, -361216),
        ("3612.16" + "0" * 30 + "1", False, None),
        ("999999999999999999.995", True, None),  # rounding would need a 21st digit
        ("007.50", False, 750),
    ],
)
def test_r02_arrow_scaling_is_exact(value: str, round_: bool, expected: int | None) -> None:
    from pyxaf._arrow import scaled_integer

    with decimal.localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = decimal.ROUND_UP
        assert scaled_integer(Decimal(value), 20, 2, round_=round_) == expected


@pytest.mark.extras
def test_r02_export_raises_on_inexact_values_in_the_default_context() -> None:
    pytest.importorskip("polars")
    data = _CLEAN.replace(b"<amnt>3612.16</amnt>", b"<amnt>3612.16" + b"0" * 30 + b"1</amnt>", 1)
    with pyxaf.open(data) as af, pytest.raises(ValueError, match="exactly"):
        af.to_polars(["lines"])


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (None, "duplicate-header", "XAF3012"),
        (None, "vat-before-nr", "XAF3013"),
        (b"<curCode>EUR</curCode>", b'<curCode xmlns="urn:other">EUR</curCode>', "XAF3011"),
        (b"<curCode>EUR</curCode>", b"<x:curCode>EUR</x:curCode>", "XAF3011"),  # unbound prefix
    ],
)
def test_r03_structure_matches_the_schema(old: bytes | None, new: str | bytes, code: str) -> None:
    data = _CLEAN
    if new == "duplicate-header":
        hdr = re.search(rb"<header>.*?</header>", data, re.S)
        assert hdr is not None
        data = data.replace(hdr.group(), hdr.group() * 2, 1)
    elif new == "vat-before-nr":
        vat = re.search(rb"<vat>.*?</vat>", data, re.S)
        assert vat is not None
        data = data.replace(vat.group(), b"", 1).replace(b"<trLine>", b"<trLine>" + vat.group(), 1)
    else:
        assert old is not None and isinstance(new, bytes)
        data = xafgen.replace_once(data, old, new)
    assert code in _codes(pyxaf.validate(data))


def test_r03_same_namespace_with_a_prefix_is_fine() -> None:
    ns = re.search(rb'<auditfile xmlns="([^"]+)"', _CLEAN)
    assert ns is not None
    data = _CLEAN.replace(
        b"<curCode>EUR</curCode>", b'<p:curCode xmlns:p="' + ns.group(1) + b'">EUR</p:curCode>', 1
    )
    assert pyxaf.validate(data).ok


def test_r03_selected_choice_branch_must_be_complete() -> None:
    clair = xafgen.write("CLAIR2")
    data = re.sub(rb"<address>[^<]+</address>", b"<streetname>Main</streetname>", clair, count=1)
    report = pyxaf.validate(data)
    assert any(f.code == "XAF3010" and "number" in f.message for f in report.findings)


def test_r04_journals_and_transactions_share_a_seekable_stream() -> None:
    data = xafgen.write("4.0", xafgen.make_ledger(n_tx=400))  # many parser chunks
    with pyxaf.open(io.BytesIO(data)) as af:
        assert len(af.journals) == 3
        it = af.transactions()
        next(it)
        assert len(af.journals) == 3
        assert 1 + sum(1 for _ in it) == 400


class _OneShot(io.RawIOBase):
    def __init__(self, data: bytes) -> None:
        self._b = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self._b.read(len(b))
        b[: len(data)] = data
        return len(data)


@pytest.mark.parametrize("kind", ["bytes", "seekable", "one-shot"])
def test_r06_decompression_guard_for_every_source_kind(kind: str) -> None:
    bomb = gzip.compress(
        _CLEAN.replace(b"<desc>btw</desc>", b"<desc>" + b" " * 3_000_000 + b"</desc>", 1)
    )
    src: object = {"bytes": bomb, "seekable": io.BytesIO(bomb), "one-shot": _OneShot(bomb)}[kind]
    with pytest.raises(pyxaf.LimitExceededError), pyxaf.open(src) as af:  # type: ignore[arg-type]
        list(af.lines())
    ok = gzip.compress(_CLEAN)
    src = {"bytes": ok, "seekable": io.BytesIO(ok), "one-shot": _OneShot(ok)}[kind]
    with pyxaf.open(src) as af:  # type: ignore[arg-type]
        assert sum(1 for _ in af.lines()) == 36


def _adf_with(debit: bytes, credit: bytes | None = None) -> bytes:
    lines = xafgen.write("ADF").split(b"\r\n")
    row = lines[1].ljust(317)
    row = row[:285] + debit.ljust(16) + row[301:]
    if credit is not None:
        row = row[:301] + credit.ljust(16) + row[317:]
    lines[1] = row
    return b"\r\n".join(lines)


def test_r07_invalid_adf_amount_is_none_not_zero() -> None:
    with pyxaf.open(_adf_with(b"INVALID")) as af:
        ln = next(af.lines())
        assert ln.amount is None
        assert ln.signed_amount is None
        assert ln.debit is None
        assert ln.credit is None
        assert ln.raw is not None and ln.raw.fields["debet"].strip() == "INVALID"
        assert "XAF3018" in {f.code for f in af.findings}


def test_r07_blank_adf_side_is_zero() -> None:
    with pyxaf.open(_adf_with(b"", b"12,50")) as af:
        ln = next(af.lines())
        assert ln.amount == Decimal("12.50")
        assert ln.signed_amount == Decimal("-12.50")


def test_r08_different_administrations_are_reported() -> None:
    other = _CLEAN.replace(b"Voorbeeld &amp; Zonen B.V.", b"Other B.V.").replace(
        b"NL001234567B01", b"NL999999999B01"
    )
    assert "XAF6011" in _codes(pyxaf.validate([_CLEAN, other]))
    same = _CLEAN.replace(b"Voorbeeld &amp; Zonen B.V.", b"Voorbeeld  &amp; zonen b.v.")
    assert "XAF6011" not in _codes(pyxaf.validate([_CLEAN, same]))


def test_r08_mixed_versions_use_their_own_catalogue() -> None:
    report = pyxaf.validate([_CLEAN, xafgen.write("3.2")])
    assert "XAF3008" in report.counts
    assert "XAF3011" not in report.counts  # the 3.2 file is not judged by the 4.0 catalogue


def test_r08_continuation_numbering() -> None:
    cut = _CLEAN.index(b"<transaction>", _CLEAN.index(b"<journal>") + 300)
    cut = _CLEAN.rindex(b"\n", 0, cut) + 1
    bad = [_CLEAN[:cut], b"<!-- Vervolgbestand 9 van 2 -->\n" + _CLEAN[cut:]]
    assert "XAF3009" in _codes(pyxaf.validate(bad))
    good = [_CLEAN[:cut], b"<!-- Vervolgbestand 2 van 2 -->\n" + _CLEAN[cut:]]
    assert "XAF3009" not in _codes(pyxaf.validate(good))


def test_r09_raw_pass_first_keeps_normalized_findings() -> None:
    data = xafgen.write("4.0", xafgen.negative_line(xafgen.make_ledger()))
    with pyxaf.open(data) as af:
        list(af.raw.transactions())
        list(af.transactions())
        assert "XAF7001" in {f.code for f in af.findings}


def test_r10_huge_integer_is_a_finding() -> None:
    data = re.sub(
        rb"<linesCount>[0-9]+</linesCount>",
        b"<linesCount>" + b"9" * 5000 + b"</linesCount>",
        _CLEAN,
        count=1,
    )
    assert "XAF3019" in _codes(pyxaf.validate(data))  # was an uncaught ValueError
    assert parse_int("9" * 5000) is None


def test_r10_limit_during_detection_is_a_finding() -> None:
    report = pyxaf.validate(gzip.compress(_CLEAN), max_decompressed_size=1000)
    assert not report.ok
    assert "XAF2003" in report.counts


@pytest.mark.extras
def test_r11_xsd_uses_the_encoding_override() -> None:
    pytest.importorskip("lxml")
    data = xafgen.write("4.0", encoding="ISO-8859-1").replace(
        b'encoding="ISO-8859-1"', b'encoding="UTF-8"'
    )
    report = pyxaf.validate(data, encoding="iso8859-1", xsd=True)
    assert "XAF3040" not in report.counts
    bom = pyxaf.validate(b"\xef\xbb\xbf" + _CLEAN, encoding="utf-8", xsd=True)
    assert "XAF3040" not in bom.counts


def test_r11_override_beats_utf16_signature() -> None:
    text = _CLEAN.decode().replace('encoding="UTF-8"', 'encoding="UTF-16"')
    assert pyxaf.detect(text.encode("utf-16-le"), encoding="utf-8").encoding.effective == "utf-8"


def test_r12_uniqueness_state_spills_to_disk() -> None:
    from pyxaf.validate import _SeenKeys

    seen = _SeenKeys(max_memory=10)
    assert all(seen.add(("j", i % 3), str(i)) for i in range(50))
    assert seen._db is not None
    assert not seen.add(("j", 1), "4")
    assert seen.add(("j", 2), "4")
    seen.close()


@pytest.mark.extras
def test_r12_streaming_xsd_prunes_finished_elements(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("lxml")
    from pyxaf import _xsd

    monkeypatch.setattr(_xsd, "FULL_TREE_LIMIT", 0)  # force the streaming path
    data = xafgen.write("4.0", xafgen.make_ledger(n_tx=300))
    assert "XAF3040" not in pyxaf.validate(data, xsd=True).counts
    broken = data.replace(b"<amntTp>C</amntTp>", b"<amntTp>X</amntTp>", 1)
    assert "XAF3040" in pyxaf.validate(broken, xsd=True).counts


@pytest.mark.parametrize("size", [0, -1, 2.5, "8"])
def test_r13_batch_size_must_be_positive(size: object) -> None:
    from pyxaf._arrow import row_batches

    with pyxaf.open(_CLEAN) as af:
        with pytest.raises(ValueError, match="batch_size"):
            list(af.tables._chunks(["accounts"], size))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="batch_size"):
            af.export("/nonexistent-never-created", batch_size=size)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="batch_size"):
        row_batches([(1,)], size)  # type: ignore[arg-type]


def test_r14_double_and_timezone_lexical_rules() -> None:
    clair = xafgen.write("CLAIR2")
    exp = re.sub(
        rb"<vatAmount>[^<]+</vatAmount>", b"<vatPercentage>2.1E1</vatPercentage>", clair, count=1
    )
    assert "XAF3018" not in _codes(pyxaf.validate(exp))
    assert parse_double("2.1E1") == Decimal(21)
    assert parse_double("INF") is None
    assert parse_double("1E+999999") is None
    for tz, valid in [
        ("+14:00", True),
        ("-13:59", True),
        ("+14:01", False),
        ("+00:60", False),
        ("+99:99", False),
    ]:
        assert (parse_date("2024-01-05" + tz)[0] is not None) is valid, tz


def test_r16_missing_master_section_is_reported() -> None:
    absent = re.sub(
        rb"<customersSuppliers>.*?</customersSuppliers>", b"", _CLEAN, count=1, flags=re.S
    )
    report = pyxaf.validate(absent)
    assert report.counts.get("XAF4008") == 1
    empty = re.sub(
        rb"<customersSuppliers>.*?</customersSuppliers>",
        b"<customersSuppliers/>",
        _CLEAN,
        count=1,
        flags=re.S,
    )
    assert "XAF4002" in _codes(pyxaf.validate(empty))
    clair = xafgen.write("CLAIR2")
    clair_absent = re.sub(
        rb"<customersSuppliers>.*?</customersSuppliers>", b"", clair, count=1, flags=re.S
    )
    assert "XAF4002" in _codes(pyxaf.validate(clair_absent))  # CLAIR2's XSD keyrefs


def test_gzip_and_zip_paths_close_their_files(tmp_path: Path) -> None:
    import gc
    import warnings
    import zipfile

    base = tmp_path
    gz = base / "a.xaf.gz"
    gz.write_bytes(gzip.compress(_CLEAN))
    zp = base / "a.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("a.xaf", _CLEAN)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        for p in (gz, zp):
            with pyxaf.open(p) as af:
                assert sum(1 for _ in af.lines()) == 36
            gc.collect()
