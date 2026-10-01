"""Regression tests for defects found in code review (2026-10-01)."""

from __future__ import annotations

import gzip
import io

import pytest
import xafgen

import pyxaf
from pyxaf.values import parse_amount, parse_date


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
