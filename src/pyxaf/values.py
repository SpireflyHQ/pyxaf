"""Strict, non-coercing value parsers.

Parsers return ``None`` for values they cannot interpret exactly; the caller reports a finding
and keeps the raw text. Nothing is ever rounded or guessed silently.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Context, Decimal, Inexact, InvalidOperation

__all__ = [
    "parse_adf_amount",
    "parse_adf_date",
    "parse_amount",
    "parse_date",
    "parse_double",
    "parse_int",
]

# Money arithmetic must not depend on the caller's decimal context (precision, rounding, traps).
# Sign changes use the context-free copy_negate()/copy_abs(); additions run in this private
# context, which is exact for every amount the parsers accept (no exponents, bounded text).
_EXACT = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN, traps=[InvalidOperation, Inexact])
_ZERO = Decimal(0)


def exact_sum(values: Iterable[Decimal], start: Decimal = _ZERO) -> Decimal:
    """Sum decimals exactly, independent of the active decimal context."""
    add = _EXACT.add
    total = start
    for v in values:
        total = add(total, v)
    return total


def exact_add(a: Decimal, b: Decimal) -> Decimal:
    """``a + b`` exactly, independent of the active decimal context."""
    return _EXACT.add(a, b)


def exact_sub(a: Decimal, b: Decimal) -> Decimal:
    """``a - b`` exactly, independent of the active decimal context."""
    return _EXACT.subtract(a, b)


_AMOUNT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(Z|[+-]([0-9]{2}):([0-9]{2}))?")
_DOUBLE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?")
_DOUBLE_SPECIAL = frozenset({"INF", "-INF", "NaN"})
_MAX_EXPONENT = 400  # far beyond any amount or percentage; bounds the work on hostile input
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
    try:
        return int(s)
    except ValueError:  # beyond the interpreter's integer-string conversion limit
        return None


def is_double(text: str) -> bool:
    """Whether ``text`` is in the lexical space of ``xs:double`` (incl. ``INF``, ``NaN``)."""
    s = text.strip()
    return s in _DOUBLE_SPECIAL or _DOUBLE.fullmatch(s) is not None


def parse_double(text: str | None) -> Decimal | None:
    """Parse an ``xs:double`` (``21``, ``2.1E1``) exactly as written, as a ``Decimal``.

    ``None`` if the text is not a valid double, or is ``INF``/``NaN`` or an exponent beyond
    ±400, which no amount or percentage has.
    """
    if text is None:
        return None
    s = text.strip()
    if not _DOUBLE.fullmatch(s):
        return None
    exponent = s.lower().partition("e")[2].lstrip("+-").lstrip("0")
    if len(exponent) > 3 or (exponent and int(exponent) > _MAX_EXPONENT):
        return None
    return Decimal(s)


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
    if m.group(5) is not None:  # time zone: at most ±14:00 (XML Schema)
        hours, minutes = int(m.group(5)), int(m.group(6))
        if minutes > 59 or hours > 14 or (hours == 14 and minutes):
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
    # built from its parts: exact in any decimal context (scaleb would round to the context)
    return Decimal((int(neg), tuple(int(c) for c in digits), -decimals))


def parse_adf_date(text: str) -> dt.date | None:
    """Parse an ADF date (``dd-mm-jjjj`` or ``ddmmjjjj``)."""
    m = _ADF_DATE.fullmatch(text.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
