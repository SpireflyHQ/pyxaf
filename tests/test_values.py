from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from pyxaf.values import parse_adf_amount, parse_adf_date, parse_amount, parse_date, parse_int


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("121", Decimal("121")),
        ("609.9", Decimal("609.9")),
        ("-12.50", Decimal("-12.50")),
        (" 1.00 ", Decimal("1.00")),
        ("+3", Decimal(3)),
        (".5", Decimal("0.5")),
        ("1.", Decimal("1")),
    ],
)
def test_parse_amount_valid(text: str, expected: Decimal) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["NaN", "1,50", "1e3", "1.000,00", "", "abc", "Infinity", "--1"])
def test_parse_amount_invalid(text: str) -> None:
    assert parse_amount(text) is None


def test_parse_amount_keeps_exact_float_artefacts() -> None:
    assert parse_amount("40.800000000000004") == Decimal("40.800000000000004")


def test_parse_date() -> None:
    assert parse_date("2024-02-29") == (dt.date(2024, 2, 29), False)
    assert parse_date("2024-02-30") == (None, False)
    assert parse_date("2024-02-03+01:00") == (dt.date(2024, 2, 3), True)
    assert parse_date("2024-02-03Z") == (dt.date(2024, 2, 3), True)
    assert parse_date("03-02-2024") == (None, False)
    assert parse_date(None) == (None, False)


def test_parse_int() -> None:
    assert parse_int("007") == 7
    assert parse_int("-1") == -1
    assert parse_int("1.0") is None


@pytest.mark.parametrize("text", ["10.239,87", "10239,87", "10239.87", "1023987", "  1023987  "])
def test_adf_amount_notations(text: str) -> None:
    # Handleiding Auditfile: all of these mean 10239.87
    assert parse_adf_amount(text) == Decimal("10239.87")


def test_adf_amount_sign_and_invalid() -> None:
    assert parse_adf_amount("-100,00") == Decimal("-100.00")
    assert parse_adf_amount("100,00-") == Decimal("-100.00")
    assert parse_adf_amount("1.000") is None  # three digits after the last separator
    assert parse_adf_amount("") is None
    assert parse_adf_amount("12a") is None
    assert parse_adf_amount("5", 0) == Decimal(5)


def test_adf_dates() -> None:
    assert parse_adf_date("31-12-1998") == dt.date(1998, 12, 31)
    assert parse_adf_date("31121998") == dt.date(1998, 12, 31)
    assert parse_adf_date("32-12-1998") is None
