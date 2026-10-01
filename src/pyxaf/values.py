"""Strict, non-coercing value parsers.

Parsers return ``None`` for values they cannot interpret exactly; the caller reports a finding
and keeps the raw text. Nothing is ever rounded or guessed silently.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

__all__ = ["parse_adf_amount", "parse_adf_date", "parse_amount", "parse_date", "parse_int"]

_AMOUNT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(Z|[+-][0-9]{2}:[0-9]{2})?")
_INT = re.compile(r"[+-]?[0-9]+")


def parse_amount(text: str | None) -> Decimal | None:
    """Parse an ``xs:decimal`` (``-12.5``, ``121``, ``609.90``); ``None`` if not exactly valid.

    Surrounding whitespace is allowed (XML Schema collapses it). Exponents, ``NaN``, decimal
    commas and grouping separators are rejected.
    """
    if text is None:
        return None
    s = text.strip()
    if not _AMOUNT.fullmatch(s):
        return None
    return Decimal(s)


def parse_int(text: str | None) -> int | None:
    """Parse an ``xs:integer``; ``None`` if not exactly valid."""
    if text is None:
        return None
    s = text.strip()
    if not _INT.fullmatch(s):
        return None
    return int(s)


_DATE_CACHE: dict[str, dt.date] = {}


def parse_date(text: str | None) -> tuple[dt.date | None, bool]:
    """Parse an ``xs:date`` (``YYYY-MM-DD`` with optional time zone).

    Returns:
        ``(date, had_timezone)``; ``date`` is ``None`` when invalid.
    """
    if text is None:
        return None, False
    cached = _DATE_CACHE.get(text)
    if cached is not None:
        return cached, False
    if len(text) == 10 and text[4] == "-" and text[7] == "-" and text.isascii():
        try:
            value = dt.date.fromisoformat(text)
        except ValueError:
            return None, False
        if len(_DATE_CACHE) > 4096:
            _DATE_CACHE.clear()
        _DATE_CACHE[text] = value
        return value, False
    m = _DATE.fullmatch(text.strip())
    if not m:
        return None, False
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(4) is not None
    except ValueError:
        return None, False


# ---------------------------------------------------------------- ADF (fixed width)
_ADF_DATE = re.compile(r"([0-9]{2})-?([0-9]{2})-?([0-9]{4})")
_ADF_NUM = re.compile(r"-?[0-9.,]*[0-9]")


def parse_adf_amount(text: str, decimals: int = 2) -> Decimal | None:
    """Parse an ADF number (``N16,2``); ``None`` if not valid.

    The ADF manual allows a comma, a point or no decimal symbol, grouping symbols and a leading
    minus — but the decimal digits are always present. So ``10.239,87``, ``10239,87``,
    ``10239.87`` and ``1023987`` all mean 10239.87. When a separator is present, exactly
    ``decimals`` digits must follow the last one.
    """
    s = text.strip()
    if not s:
        return None
    neg = s.startswith("-") or s.endswith("-")
    s = s.strip("+-").strip()
    if not _ADF_NUM.fullmatch(s):
        return None
    last_sep = max(s.rfind("."), s.rfind(","))
    if last_sep != -1 and decimals and len(s) - last_sep - 1 != decimals:
        return None
    digits = s.replace(".", "").replace(",", "")
    value = Decimal(digits).scaleb(-decimals)
    return -value if neg else value


def parse_adf_date(text: str) -> dt.date | None:
    """Parse an ADF date (``dd-mm-jjjj`` or ``ddmmjjjj``)."""
    m = _ADF_DATE.fullmatch(text.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
