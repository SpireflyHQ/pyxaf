"""Arrow interoperability via nanoarrow (``pyxaf[arrow]``), and polars/pandas/Parquet on top.

Columns are built directly from buffers: decimals as 16-byte little-endian integers (exact; no
float anywhere), dates as int32 days since the epoch, with validity bitmaps. nanoarrow cannot
build decimal128 or date32 arrays from Python objects, and has no lazy stream constructor, so
``stream()`` materialises the batches of one table in Arrow memory; ``batches()`` is lazy.
"""

from __future__ import annotations

import datetime as dt
import struct
from collections.abc import Iterable, Iterator, Sequence
from decimal import Decimal
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._optional import require
from .tables import TABLES, Column, Row, check_batch_size

if TYPE_CHECKING:
    from .tables import Tables

__all__ = ["batches", "schema", "stream", "to_pandas", "to_polars", "write_parquet"]

_EPOCH = dt.date(1970, 1, 1).toordinal()


def _na() -> Any:
    return require("nanoarrow", "arrow")


def _type(na: Any, col: Column) -> Any:
    dec = col.decimal
    if dec is not None:
        return na.decimal128(dec[0], dec[1])
    return {
        "string": na.string(),
        "int64": na.int64(),
        "date": na.date32(),
        "bool": na.bool_(),
    }[col.type]


def schema(columns: Sequence[Column]) -> Any:
    """Nanoarrow struct schema for ``columns``."""
    na = _na()
    return na.struct({c.name: _type(na, c) for c in columns})


def _validity(values: Sequence[Any]) -> tuple[bytes | None, int]:
    nulls = 0
    bits = bytearray((len(values) + 7) // 8)
    for i, v in enumerate(values):
        if v is None:
            nulls += 1
        else:
            bits[i >> 3] |= 1 << (i & 7)
    return (bytes(bits) if nulls else None), nulls


def scaled_integer(value: Decimal, precision: int, scale: int, *, round_: bool) -> int | None:
    """``value × 10**scale`` as an integer with at most ``precision`` digits.

    Returns ``None`` when the value does not fit exactly — or, with ``round_``, when it does not
    fit even after rounding half to even. Pure integer arithmetic on ``as_tuple()``: the result
    never depends on the active decimal context (precision, rounding mode, traps).
    """
    sign, digits, exp = value.as_tuple()
    if not isinstance(exp, int):  # NaN / infinity
        return None
    shift = exp + scale
    if shift >= 0:
        if any(digits) and len(digits) + shift > precision:
            return None
        n: int = int("".join(map(str, digits))) * 10**shift
    else:
        kept, dropped = digits[:shift], digits[shift:]
        while kept and kept[0] == 0:  # leading zeros do not count against the precision
            kept = kept[1:]
        if len(kept) > precision:
            return None
        n = int("".join(map(str, kept))) if kept else 0
        if any(dropped):
            if not round_:
                return None
            first, rest = dropped[0], any(dropped[1:])
            if first > 5 or (first == 5 and (rest or n % 2)):  # round half to even
                n += 1
            if n >= 10**precision:  # rounding added a digit
                return None
    return -n if sign else n


def _decimal_array(
    na: Any, values: Sequence[Decimal | None], col: Column, keys: Sequence[Any], policy: str
) -> Any:
    precision, scale = col.decimal or (20, 2)
    out = bytearray(16 * len(values))
    nulled: list[int] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        n = scaled_integer(v, precision, scale, round_=False)
        if n is None and policy == "round":
            n = scaled_integer(v, precision, scale, round_=True)
        if n is None:
            if policy == "null":
                nulled.append(i)
                continue
            raise ValueError(
                f"column {col.name!r}: value {v} does not fit {col.type} exactly (row with "
                f"key {keys[i]!r}); pass on_inexact='null' or 'round' to export anyway"
            )
        out[16 * i : 16 * i + 16] = n.to_bytes(16, "little", signed=True)
    if nulled:
        values = list(values)
        for i in nulled:
            values[i] = None
    validity, nulls = _validity(values)
    return na.c_array_from_buffers(
        _type(na, col), len(values), [validity, bytes(out)], null_count=nulls
    )


def _int_array(
    na: Any, values: Sequence[int | None], col: Column, keys: Sequence[Any], policy: str
) -> Any:
    lo, hi = -(2**63), 2**63 - 1
    bad = [i for i, v in enumerate(values) if v is not None and not lo <= v <= hi]
    if bad:
        if policy == "raise":
            raise ValueError(
                f"column {col.name!r}: value {values[bad[0]]} does not fit int64 (row with key "
                f"{keys[bad[0]]!r}); pass on_inexact='null' to export anyway"
            )
        nulled = set(bad)
        values = [None if i in nulled else v for i, v in enumerate(values)]
    return na.c_array(list(values), na.int64())


def _date_array(na: Any, values: Sequence[dt.date | None]) -> Any:
    days = [0 if v is None else v.toordinal() - _EPOCH for v in values]
    validity, nulls = _validity(values)
    return na.c_array_from_buffers(
        na.date32(),
        len(values),
        [validity, struct.pack(f"<{len(days)}i", *days)],
        null_count=nulls,
    )


def _column(na: Any, col: Column, values: Sequence[Any], keys: Sequence[Any], policy: str) -> Any:
    if col.decimal is not None:
        return _decimal_array(na, values, col, keys, policy)
    if col.type == "date":
        return _date_array(na, values)
    if col.type == "int64":
        return _int_array(na, values, col, keys, policy)
    return na.c_array(list(values), _type(na, col))


def _batch(
    na: Any, columns: Sequence[Column], rows: Sequence[Row], sch: Any, policy: str = "raise"
) -> Any:
    if policy not in ("raise", "null", "round"):
        raise ValueError("on_inexact must be 'raise', 'null' or 'round'")
    cols = list(zip(*rows, strict=True)) if rows else [() for _ in columns]
    keys = cols[0] if rows else ()
    children = [_column(na, c, v, keys, policy) for c, v in zip(columns, cols, strict=True)]
    return na.c_array_from_buffers(sch, len(rows), [None], children=children)


def batches(
    columns: Sequence[Column],
    rows: Iterable[Row],
    batch_size: int = 65_536,
    on_inexact: str = "raise",
) -> Iterator[Any]:
    """Yield nanoarrow struct arrays of at most ``batch_size`` rows (lazily)."""
    check_batch_size(batch_size)
    na = _na()
    sch = schema(columns)
    return (_batch(na, columns, chunk, sch, on_inexact) for chunk in row_batches(rows, batch_size))


def row_batches(rows: Iterable[Row], batch_size: int) -> Iterator[list[Row]]:
    """Split rows into lists of at most ``batch_size`` (always at least one, maybe empty)."""
    check_batch_size(batch_size)
    return _row_batches(rows, batch_size)


def _row_batches(rows: Iterable[Row], batch_size: int) -> Iterator[list[Row]]:
    it = iter(rows)
    first = True
    while True:
        chunk = list(islice(it, batch_size))
        if not chunk and not first:
            return
        first = False
        yield chunk
        if len(chunk) < batch_size:
            return


def polars_frames(
    tables: Tables, names: Sequence[str], batch_size: int = 65_536, on_inexact: str = "raise"
) -> dict[str, Any]:
    """Build polars DataFrames for ``names``; streamed tables share one pass over the file."""
    pl = require("polars", "polars")
    na = _na()
    parts: dict[str, list[Any]] = {n: [] for n in names}
    for name, chunk in tables._chunks(names, batch_size):
        cols = TABLES[name]
        batch = _batch(na, cols, chunk, schema(cols), on_inexact)
        parts[name].append(pl.DataFrame(na.c_array_stream(batch)))
    return {
        n: (frames[0] if len(frames) == 1 else pl.concat(frames, how="vertical", rechunk=False))
        for n, frames in parts.items()
    }


def stream(
    columns: Sequence[Column],
    rows: Iterable[Row],
    batch_size: int = 65_536,
    on_inexact: str = "raise",
) -> Any:
    """A nanoarrow ``CArrayStream`` over all batches (materialised in Arrow memory)."""
    na = _na()
    from nanoarrow.c_array_stream import CArrayStream  # noqa: PLC0415

    arrays = list(batches(columns, rows, batch_size, on_inexact))
    return CArrayStream.from_c_arrays(arrays, na.c_schema(schema(columns)), validate=False)


def to_polars(columns: Sequence[Column], rows: Iterable[Row], on_inexact: str = "raise") -> Any:
    """Build a polars DataFrame batch-wise (no pyarrow needed)."""
    pl = require("polars", "polars")
    na = _na()
    frames = [
        pl.DataFrame(na.c_array_stream(b)) for b in batches(columns, rows, 65_536, on_inexact)
    ]
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="vertical", rechunk=False)


def to_pandas(columns: Sequence[Column], rows: Iterable[Row], on_inexact: str = "raise") -> Any:
    """Build a pandas DataFrame with ``ArrowDtype`` columns (needs pandas + pyarrow)."""
    pd = require("pandas", "pandas")
    pa = require("pyarrow", "pandas")
    table = pa.table(stream(columns, rows, 65_536, on_inexact))
    return table.to_pandas(types_mapper=pd.ArrowDtype)


def write_parquet(
    tables: Tables, names: Sequence[str], out: Path, batch_size: int, on_inexact: str = "raise"
) -> list[str]:
    """Write tables as Parquet files batch-wise (needs ``pyxaf[parquet]``)."""
    pa = require("pyarrow", "parquet")
    pq = require("pyarrow.parquet", "parquet")
    na = _na()
    paths = {n: out / f"{n}.parquet" for n in names}
    writers = {n: pq.ParquetWriter(str(paths[n]), pa.schema(schema(TABLES[n]))) for n in names}
    try:
        for name, chunk in tables._chunks(names, batch_size):
            cols = TABLES[name]
            batch = _batch(na, cols, chunk, schema(cols), on_inexact)
            writers[name].write_batch(pa.record_batch(batch))
    finally:
        for w in writers.values():
            w.close()
    return [str(paths[n]) for n in names]
