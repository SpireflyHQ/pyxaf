from __future__ import annotations

from collections.abc import Callable

import pytest
import xafgen

import pyxaf
from pyxaf import Severity

Mut = Callable[[bytes], bytes]


def sub(old: str, new: str, count: int = 1) -> Mut:
    def f(data: bytes) -> bytes:
        o, n = old.encode(), new.encode()
        assert o in data, old
        return data.replace(o, n, count)

    return f


def chain(*fs: Mut) -> Mut:
    def f(data: bytes) -> bytes:
        for g in fs:
            data = g(data)
        return data

    return f


def codes(report: pyxaf.ValidationReport) -> set[str]:
    return set(report.counts)


def test_clean_files_have_no_problems(any_version: str) -> None:
    report = pyxaf.validate(xafgen.write(any_version))
    bad = [f for f in report.findings if f.severity >= Severity.WARNING]
    assert not bad, "\n".join(map(str, bad))
    assert report.ok
    assert report.stats["lines"] > 0
    assert "L6 totals" in report.checked


DEFECTS: list[tuple[str, Mut, str]] = [
    # --- structure, layer 4
    ("missing-required", sub("<docRef>F0000</docRef>", ""), "XAF3010"),
    (
        "unknown-element",
        sub("<docRef>F0000</docRef>", "<docRef>F0000</docRef><foo>1</foo>"),
        "XAF3011",
    ),
    (
        "too-many",
        sub("<curCode>EUR</curCode>", "<curCode>EUR</curCode><curCode>EUR</curCode>"),
        "XAF3012",
    ),
    (
        "order",
        sub(
            "<accID>0100</accID>\n\t\t\t\t<accDesc>Inventaris</accDesc>",
            "<accDesc>Inventaris</accDesc><accID>0100</accID>",
        ),
        "XAF3013",
    ),
    ("length", sub("<accID>0100</accID>", "<accID>" + "9" * 40 + "</accID>"), "XAF3014"),
    ("enum", sub("<amntTp>C</amntTp>", "<amntTp>X</amntTp>"), "XAF3015"),
    ("pattern", sub("<fiscalYear>2024</fiscalYear>", "<fiscalYear>24</fiscalYear>"), "XAF3016"),
    ("date", sub("<trDt>", "<trDt>x"), "XAF3017"),
    ("decimal", sub("<amnt>10000.00</amnt>", "<amnt>10000.001</amnt>"), "XAF3018"),
    ("integer", sub("<linesCount>3</linesCount>", "<linesCount>three</linesCount>"), "XAF3019"),
    ("minimum", sub("<vatPerc>21.000</vatPerc>", "<vatPerc>-21.000</vatPerc>"), "XAF3021"),
    # --- references, layer 5
    ("account", sub("<accID>1300</accID>", "<accID>9999</accID>"), "XAF4001"),
    (
        "relation",
        sub(
            "<custSupID>D001</custSupID>\n\t\t\t\t\t\t<invRef>",
            "<custSupID>ZZZ</custSupID>\n\t\t\t\t\t\t<invRef>",
        ),
        "XAF4002",
    ),
    (
        "vat",
        sub("<vatID>H21</vatID>\n\t\t\t\t\t\t\t<vatPerc>", "<vatID>X21</vatID><vatPerc>"),
        "XAF4003",
    ),
    (
        "period",
        sub(
            "<periodNumber>1</periodNumber>\n\t\t\t\t\t<trDt>",
            "<periodNumber>77</periodNumber><trDt>",
        ),
        "XAF4004",
    ),
    (
        "offset",
        sub("<jrnTp>M</jrnTp>", "<jrnTp>M</jrnTp><offsetAccID>none</offsetAccID>"),
        "XAF4005",
    ),
    (
        "vat-account",
        sub("<vatToPayAccID>1800</vatToPayAccID>", "<vatToPayAccID>x</vatToPayAccID>"),
        "XAF4006",
    ),
    (
        "ob-account",
        sub("<accID>1100</accID>\n\t\t\t\t<amnt>", "<accID>7777</accID><amnt>"),
        "XAF4007",
    ),
    # --- totals, layer 6
    (
        "ob-debit",
        sub("<totalDebit>12500.50</totalDebit>", "<totalDebit>12500.51</totalDebit>"),
        "XAF5004",
    ),
    (
        "ob-credit",
        sub("<totalCredit>12500.50</totalCredit>", "<totalCredit>1.00</totalCredit>"),
        "XAF5005",
    ),
    ("ob-balance", sub("<amnt>2500.50</amnt>", "<amnt>2500.60</amnt>"), "XAF5013"),
    ("ob-count", sub("<linesCount>3</linesCount>", "<linesCount>4</linesCount>"), "XAF5011"),
    ("tx-count", sub("<linesCount>36</linesCount>", "<linesCount>35</linesCount>"), "XAF5012"),
    # --- uniqueness, layer 7
    ("dup-relation", sub("<custSupID>C001</custSupID>", "<custSupID>D001</custSupID>"), "XAF6001"),
    (
        "dup-account",
        sub(
            "<accID>1000</accID>\n\t\t\t\t<accDesc>Kas", "<accID>0100</accID>\n\t\t\t\t<accDesc>Kas"
        ),
        "XAF6002",
    ),
    ("dup-rgs", sub("<RGScode>BLimKas</RGScode>", "<RGScode>BMvaBei</RGScode>"), "XAF6003"),
    (
        "dup-tx",
        sub("<nr>4</nr>\n\t\t\t\t\t<desc>Boeking 4", "<nr>1</nr>\n\t\t\t\t\t<desc>Boeking 4"),
        "XAF6004",
    ),
    (
        "dup-line",
        sub("<nr>2</nr>\n\t\t\t\t\t\t<accID>8000", "<nr>1</nr>\n\t\t\t\t\t\t<accID>8000"),
        "XAF6005",
    ),
    (
        "dup-ob-line",
        sub("<nr>2</nr>\n\t\t\t\t<accID>0100", "<nr>1</nr>\n\t\t\t\t<accID>0100"),
        "XAF6006",
    ),
    ("dup-journal", sub("<jrnID>VRK</jrnID>", "<jrnID>MEM</jrnID>"), "XAF6007"),
    ("dup-vat", sub("<vatID>L9</vatID>", "<vatID>H21</vatID>"), "XAF6008"),
    (
        "dup-period",
        sub("<periodNumber>2</periodNumber>", "<periodNumber>1</periodNumber>"),
        "XAF6009",
    ),
    # --- data quality, layer 8
    ("tx-date-fy", sub("<trDt>2024-01", "<trDt>2023-01"), "XAF7002"),
    ("tx-date-period", sub("<trDt>2024-01", "<trDt>2024-11"), "XAF7003"),
    (
        "fy-mismatch",
        sub("<fiscalYear>2024</fiscalYear>", "<fiscalYear>2004</fiscalYear>"),
        "XAF7007",
    ),
    ("timezone", sub("<trDt>2024-01-", "<trDt>2024-01-0"), "XAF3017"),
    ("eff-date", sub("<effDate>2024-01", "<effDate>2023-01"), "XAF7010"),
    ("rgs-placeholder", sub("<RGScode>BLimKas</RGScode>", "<RGScode>0000</RGScode>"), "XAF8001"),
    ("rgs-garbage", sub("<RGScode>BLimKas</RGScode>", "<RGScode>1234</RGScode>"), "XAF8002"),
    ("relation-type", sub("<custSupTp>B</custSupTp>", "<custSupTp>Q</custSupTp>"), "XAF7012"),
]


@pytest.mark.parametrize(("name", "mutate", "code"), DEFECTS, ids=[d[0] for d in DEFECTS])
def test_injected_defect_is_reported(name: str, mutate: Mut, code: str) -> None:
    report = pyxaf.validate(mutate(xafgen.write("4.0")))
    assert code in codes(report), str(report)


def test_unbalanced_transaction_and_totals() -> None:
    led = xafgen.unbalance_first_transaction(xafgen.make_ledger())
    report = pyxaf.validate(xafgen.write("4.0", led))
    assert {"XAF5010", "XAF5009"} <= codes(report)
    f = next(f for f in report.findings if f.code == "XAF5010")
    assert f.rule_ref == "[0010]" and f.line is not None


def test_declared_totals_differ_from_lines() -> None:
    data = sub("<totalDebit>", "<totalDebit>1")(xafgen.write("4.0").split(b"<transactions>")[1])
    full = xafgen.write("4.0")
    head = full.split(b"<transactions>")[0]
    report = pyxaf.validate(head + b"<transactions>" + data)
    assert {"XAF5007", "XAF5009"} <= codes(report)


def test_opening_balance_conventions() -> None:
    led = xafgen.make_ledger()
    both = xafgen.write("3.2", led).replace(b"<jrnID>MEM</jrnID>", b"<jrnID>MEM</jrnID>", 1)
    period0 = xafgen.write("4.0", led, ob_style="period0")
    assert "XAF7005" in codes(pyxaf.validate(period0))
    assert "XAF7005" not in codes(pyxaf.validate(xafgen.write("3.2", led, ob_style="period0")))
    report = pyxaf.validate(xafgen.write("4.0", led, ob_style="none"))
    f = next(f for f in report.findings if f.code == "XAF7006")
    assert f.severity is Severity.WARNING
    report = pyxaf.validate(xafgen.write("3.2", led, ob_style="none"))
    assert next(f for f in report.findings if f.code == "XAF7006").severity is Severity.INFO
    # element and period-0 transactions at the same time
    data = xafgen.write("3.2", led, ob_style="period0")
    element = xafgen.write("3.2", led).split(b"<openingBalance>")[1].split(b"</openingBalance>")[0]
    data = data.replace(
        b"<transactions>", b"<openingBalance>" + element + b"</openingBalance>\n<transactions>", 1
    )
    assert "XAF7004" in codes(pyxaf.validate(data))
    assert both


def test_transaction_without_lines() -> None:
    data = xafgen.write("4.0")
    start = data.index(b"<trLine>")
    end = data.index(b"</transaction>", start)
    report = pyxaf.validate(data[:start] + data[end:])
    assert "XAF7011" in codes(report)


def test_negative_amounts_are_info_and_balance_under_flip() -> None:
    report = pyxaf.validate(xafgen.write("4.0", xafgen.negative_line(xafgen.make_ledger())))
    assert "XAF7001" in codes(report)
    assert "XAF5010" not in codes(report)


def test_clair2_rules() -> None:
    data = xafgen.write("CLAIR2")
    report = pyxaf.validate(sub("<recordID>2</recordID>", "<recordID>1</recordID>")(data))
    assert "XAF6005" in codes(report)
    report = pyxaf.validate(
        sub("<numberEntries>39</numberEntries>", "<numberEntries>13</numberEntries>")(data)
    )
    f = next(f for f in report.findings if f.code == "XAF5012")
    assert "number of transactions" in f.message
    report = pyxaf.validate(sub("CLAIR2.00.00", "CLAIR2.01.00")(data))
    assert "XAF3020" in codes(report)


def test_adf_rules() -> None:
    data = xafgen.write("ADF")
    lines = data.split(b"\r\n")
    hdr = lines[0]
    lines[0] = hdr[:222] + b"99".ljust(10) + hdr[232:]
    report = pyxaf.validate(b"\r\n".join(lines))
    assert "XAF5012" in codes(report)
    broken = data.replace(b"Inventaris", b"          ", 1)  # required account name
    assert "XAF3010" in codes(pyxaf.validate(broken))


def test_derived_catalogue_downgrades_unknown_elements() -> None:
    data = sub("<docRef>F0000</docRef>", "<docRef>F0000</docRef><foo>1</foo>")(xafgen.write("3.1"))
    report = pyxaf.validate(data)
    f = next(f for f in report.findings if f.code == "XAF3011")
    assert f.severity is Severity.WARNING
    assert "derived" in " ".join(report.checked)


def test_rules_vts_filters_codes() -> None:
    data = sub("<RGScode>BLimKas</RGScode>", "<RGScode>0000</RGScode>")(xafgen.write("4.0"))
    data = sub("<trDt>2024-01", "<trDt>2023-01")(data)
    spec = pyxaf.validate(data)
    vts = pyxaf.validate(data, rules="vts")
    assert {"XAF8001", "XAF7002"} <= codes(spec)
    assert not ({"XAF8001", "XAF7002"} & codes(vts))
    with pytest.raises(ValueError, match="rules"):
        pyxaf.validate(data, rules="all")  # type: ignore[arg-type]


def test_severity_overrides_and_limits() -> None:
    data = sub("<trDt>2024-0", "<trDt>2023-0", 20)(xafgen.write("4.0"))
    report = pyxaf.validate(data, severity_overrides={"XAF7002": Severity.ERROR})
    assert all(f.severity is Severity.ERROR for f in report.findings if f.code == "XAF7002")
    report = pyxaf.validate(data, severity_overrides={"XAF7002": None})
    assert "XAF7002" not in codes(report)
    report = pyxaf.validate(data, max_findings_per_code=2)
    assert sum(f.code == "XAF7002" for f in report.findings) == 2
    assert report.counts["XAF7002"] > 2 and report.suppressed > 0


def test_malformed_xml_becomes_a_finding() -> None:
    data = xafgen.write("4.0")
    report = pyxaf.validate(data[: len(data) - 200])
    assert "XAF2001" in codes(report)
    assert not report.ok


def test_report_serialisation() -> None:
    report = pyxaf.validate(sub("<trDt>2024-01", "<trDt>2023-01")(xafgen.write("4.0")))
    d = report.to_dict()
    assert d["schema_version"] == 1 and d["format"]["version"] == "4.0"
    assert report.to_json().startswith("{")
    assert "XAF7002" in str(report)
    assert report.max_severity is Severity.WARNING
    assert report.warnings and not report.errors


def test_multi_file_validation() -> None:
    a = xafgen.write("4.0", xafgen.make_ledger(seed=1, n_tx=3))
    b = xafgen.write("4.0", xafgen.make_ledger(seed=2, n_tx=3), ob_style="none")
    b = sub("<accDesc>Kas</accDesc>", "<accDesc>Kasgeld</accDesc>")(b)
    report = pyxaf.validate([a, b])
    assert report.stats["transactions"] == 6
    assert "XAF6010" in codes(report)


def test_codes_registry_is_consistent() -> None:
    for code, info in pyxaf.CODES.items():
        assert code == info.code and code.startswith("XAF") and len(code) == 7


def test_period_zero_opening_balance_is_not_an_undefined_period() -> None:
    report = pyxaf.validate(xafgen.write("3.2", ob_style="period0"))
    assert "XAF4004" not in codes(report)


def test_vts_drops_other_codes_before_limits() -> None:
    data = sub("<trDt>2024-0", "<trDt>2023-0", 20)(xafgen.write("4.0"))
    report = pyxaf.validate(data, rules="vts", max_findings=1)
    assert report.suppressed == 0 and not report.findings
    assert "L5 references" not in report.checked


def test_continuation_finding_in_sets() -> None:
    data = xafgen.write("4.0")
    cut = data.rindex(b"\n", 0, data.index(b"<transaction>", data.index(b"<journal>") + 300)) + 1
    second = b"<!-- Vervolgbestand 2 van 2 -->\n" + data[cut:]
    with pyxaf.open([data[:cut], second]) as af:
        assert any(f.code == "XAF3006" for f in af.findings)
