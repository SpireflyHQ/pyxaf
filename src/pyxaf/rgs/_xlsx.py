"""Minimal, hardened, streaming ``.xlsx`` reader using only the standard library.

Just enough of SpreadsheetML to read tabular reference data (the RGS Excel): sheets resolved by
name through ``xl/workbook.xml`` and its relationships, shared strings (including rich-text runs),
inline strings, numbers, booleans, errors and formula results. Cell values are returned as the raw
text Excel stored — numbers are *not* converted, so nothing is coerced silently.

Security properties:

- Every XML part is parsed with ``pyexpat``; DOCTYPE, ENTITY and NOTATION declarations and external
  entity references raise :class:`~pyxaf.errors.ForbiddenConstructError`. Office never writes a
  DTD, so a declaration is either a malformed or a hostile file.
- Every ZIP member is decompressed as a stream and counted; a member that inflates beyond
  ``max_member_size`` raises :class:`~pyxaf.errors.LimitExceededError` (zip-bomb guard). The size
  declared in the ZIP directory is checked first but not trusted.
- Cell references beyond Excel's own grid (16,384 columns) are refused, so a crafted reference
  cannot allocate a huge row.

Rows are produced one at a time from a SAX-style parse; only the shared-string table is held in
memory.
"""

from __future__ import annotations

import os
import posixpath
import pyexpat
import zipfile
from contextlib import suppress
from typing import IO, TYPE_CHECKING, Any, Final, Self

from ..errors import ForbiddenConstructError, LimitExceededError, PyxafError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator

__all__ = [
    "DEFAULT_MAX_MEMBER_SIZE",
    "MAX_COLUMNS",
    "XlsxError",
    "XlsxWorkbook",
    "column_index",
    "iter_rows",
    "sheet_names",
]

#: Default cap on the uncompressed size of a single ZIP member (200 MB).
DEFAULT_MAX_MEMBER_SIZE: Final = 200 * 1024 * 1024
#: Number of columns in an Excel worksheet (``A`` … ``XFD``).
MAX_COLUMNS: Final = 16_384

_CHUNK: Final = 1 << 16
_NS_SEP: Final = "}"
_REL_NS: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SHARED_STRINGS_TYPE: Final = "/sharedStrings"

XlsxSource = str | os.PathLike[str] | IO[bytes]


class XlsxError(PyxafError):
    """The workbook is not a readable ``.xlsx`` file or lacks a required part."""


def column_index(ref: str) -> int:
    """Return the 0-based column index of a cell reference.

    Args:
        ref: A cell reference such as ``"AB12"`` or a bare column such as ``"C"``.

    Returns:
        ``A`` → 0, ``Z`` → 25, ``AA`` → 26, …

    Raises:
        XlsxError: If ``ref`` does not start with a column letter.
        LimitExceededError: If the column is beyond Excel's 16,384-column grid.
    """
    col = 0
    n = 0
    for ch in ref:
        o = ord(ch) | 0x20  # case-insensitive
        if 0x61 <= o <= 0x7A:
            col = col * 26 + (o - 0x60)
            n += 1
            if n > 3:
                break
        else:
            break
    if n == 0:
        raise XlsxError(f"invalid cell reference {ref!r}")
    if col > MAX_COLUMNS or n > 3:
        raise LimitExceededError(f"cell reference {ref[:20]!r} is beyond column XFD")
    return col - 1


def _row_number(ref: str) -> int | None:
    digits = ref.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    return int(digits) if digits.isdigit() else None


def _local(name: str) -> str:
    return name.rpartition(_NS_SEP)[2]


def _forbidden(*_args: object) -> Any:
    raise ForbiddenConstructError("DOCTYPE/ENTITY declarations are not allowed in xlsx parts")


def _new_parser() -> pyexpat.XMLParserType:
    parser = pyexpat.ParserCreate(namespace_separator=_NS_SEP)
    parser.buffer_text = True
    parser.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _forbidden
    parser.EntityDeclHandler = _forbidden
    parser.UnparsedEntityDeclHandler = _forbidden
    parser.NotationDeclHandler = _forbidden
    parser.ExternalEntityRefHandler = _forbidden
    return parser


class XlsxWorkbook:
    """An open ``.xlsx`` workbook.

    Use as a context manager. Shared strings are loaded once, on first use, and reused for every
    sheet read through the same instance.

    Args:
        source: Path or binary seekable stream of the workbook.
        max_member_size: Maximum uncompressed size of any single part, in bytes.

    Raises:
        XlsxError: If the source is not a ZIP file or has no workbook part.
    """

    def __init__(
        self, source: XlsxSource, *, max_member_size: int = DEFAULT_MAX_MEMBER_SIZE
    ) -> None:
        try:
            self._zip = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError) as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise XlsxError(f"not an xlsx (ZIP) file: {exc}") from exc
        self._max = max_member_size
        self._names = {i.filename for i in self._zip.infolist()}
        self._shared: list[str] | None = None
        try:
            self._sheets, self._shared_part = self._read_workbook()
        except BaseException:
            self._zip.close()
            raise

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        """Close the underlying ZIP file."""
        self._zip.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ public
    @property
    def sheet_names(self) -> list[str]:
        """Names of the worksheets in workbook order."""
        return list(self._sheets)

    def dimension(self, sheet: str) -> str | None:
        """Return the sheet's declared used range (``"A1:AC4982"``) without reading its rows.

        Args:
            sheet: Worksheet name.

        Returns:
            The ``<dimension ref>`` value, or ``None`` when the sheet does not declare one.
        """
        found: list[str] = []

        class _StopParsingError(Exception):
            pass

        def start(name: str, attrs: dict[str, str]) -> None:
            local = _local(name)
            if local == "dimension":
                found.append(attrs.get("ref", ""))
                raise _StopParsingError
            if local == "sheetData":
                raise _StopParsingError

        with suppress(_StopParsingError):
            self._run(self._part(sheet), start=start)
        return found[0] or None if found else None

    def iter_rows(self, sheet: str) -> Iterator[list[str | None]]:
        """Stream the rows of a worksheet.

        Rows are yielded in sheet order, one list per row starting at row 1; rows absent from the
        file (completely empty) are yielded as ``[]`` so that the n-th item is Excel row n. Missing
        cells inside a row are ``None``. Values are the stored text: shared/inline strings as
        text, numbers as written (``"4980"``, ``"1.5E-3"``), booleans as ``"TRUE"``/``"FALSE"``,
        errors as written (``"#N/A"``). Formula cells yield their cached result.

        Args:
            sheet: Worksheet name.

        Yields:
            One list of cell values per row.

        Raises:
            KeyError: If there is no sheet with that name.
            ForbiddenConstructError: If a part contains a DOCTYPE/ENTITY declaration.
            LimitExceededError: If a part exceeds ``max_member_size``.
        """
        part = self._part(sheet)
        shared = self._shared_strings()
        pending: list[list[str | None]] = []
        state: dict[str, Any] = {
            "row": None,  # dict[int, str | None] while inside <row>
            "rownum": 0,  # last yielded row number
            "r": None,  # current row number
            "col": -1,  # last cell column
            "ctype": None,
            "cref": None,
            "v": None,  # text of <v>
            "inl": None,  # list[str] collecting inline string text
            "intext": None,  # list[str] collecting current text
            "in_rph": False,
        }

        def start(name: str, attrs: dict[str, str]) -> None:
            local = _local(name)
            if local == "c":
                ref = attrs.get("r")
                state["col"] = column_index(ref) if ref else state["col"] + 1
                state["ctype"] = attrs.get("t", "n")
                state["v"] = None
                state["inl"] = None
            elif local == "v" or (local == "t" and state["inl"] is not None):
                state["intext"] = []
            elif local == "is":
                state["inl"] = []
            elif local == "rPh":
                state["in_rph"] = True
            elif local == "row":
                rn = attrs.get("r")
                num = int(rn) if rn and rn.isdigit() else state["rownum"] + 1
                while state["rownum"] + 1 < num:
                    pending.append([])
                    state["rownum"] += 1
                state["r"] = num
                state["row"] = {}
                state["col"] = -1

        def chars(data: str) -> None:
            buf = state["intext"]
            if buf is not None and not state["in_rph"]:
                buf.append(data)

        def end(name: str) -> None:
            local = _local(name)
            if local == "v":
                state["v"] = "".join(state["intext"] or ())
                state["intext"] = None
            elif local == "t" and state["inl"] is not None:
                if not state["in_rph"]:
                    state["inl"].append("".join(state["intext"] or ()))
                state["intext"] = None
            elif local == "rPh":
                state["in_rph"] = False
            elif local == "c":
                row = state["row"]
                if row is not None:
                    row[state["col"]] = _cell_value(
                        state["ctype"], state["v"], state["inl"], shared
                    )
                state["inl"] = None
            elif local == "row":
                row = state["row"]
                if row:
                    width = max(row) + 1
                    out: list[str | None] = [None] * width
                    for i, val in row.items():
                        out[i] = val
                else:
                    out = []
                pending.append(out)
                state["rownum"] = state["r"]
                state["row"] = None

        yield from self._parse(part, start=start, end=end, chars=chars, pending=pending)

    # ------------------------------------------------------------------ internals
    def _member(self, name: str) -> Generator[bytes, None, None]:
        try:
            info = self._zip.getinfo(name)
        except KeyError:
            raise XlsxError(f"xlsx part {name!r} is missing") from None
        if info.file_size > self._max:
            raise LimitExceededError(
                f"xlsx part {name!r} declares {info.file_size} bytes "
                f"(limit {self._max}); refusing to decompress"
            )
        total = 0
        with self._zip.open(info) as fh:
            while chunk := fh.read(_CHUNK):
                total += len(chunk)
                if total > self._max:
                    raise LimitExceededError(
                        f"xlsx part {name!r} decompresses beyond the limit of {self._max} bytes"
                    )
                yield chunk

    def _parse(
        self,
        name: str,
        *,
        start: Callable[[str, dict[str, str]], None] | None = None,
        end: Callable[[str], None] | None = None,
        chars: Callable[[str], None] | None = None,
        pending: list[list[str | None]] | None = None,
    ) -> Iterator[list[str | None]]:
        """Parse a part; when ``pending`` is given, yield its items after every chunk."""
        parser = _new_parser()
        if start is not None:
            parser.StartElementHandler = start
        if end is not None:
            parser.EndElementHandler = end
        if chars is not None:
            parser.CharacterDataHandler = chars
        parse = parser.Parse
        chunks = self._member(name)
        try:
            for chunk in chunks:
                parse(chunk, False)
                if pending:
                    yield from pending
                    pending.clear()
            parse(b"", True)
        except pyexpat.ExpatError as exc:
            raise XlsxError(f"xlsx part {name!r} is not well-formed XML: {exc}") from exc
        finally:
            chunks.close()
        if pending:
            yield from pending
            pending.clear()

    def _run(self, name: str, **handlers: Any) -> None:
        for _ in self._parse(name, **handlers):
            pass  # pragma: no cover - nothing is pending

    def _read_rels(self, name: str) -> dict[str, tuple[str, str]]:
        rels: dict[str, tuple[str, str]] = {}
        if name not in self._names:
            return rels

        def start(tag: str, attrs: dict[str, str]) -> None:
            if _local(tag) == "Relationship" and "Id" in attrs:
                rels[attrs["Id"]] = (attrs.get("Target", ""), attrs.get("Type", ""))

        self._run(name, start=start)
        return rels

    def _read_workbook(self) -> tuple[dict[str, str], str | None]:
        wb = "xl/workbook.xml"
        if wb not in self._names:
            raise XlsxError("not an xlsx file: xl/workbook.xml is missing")
        rels = self._read_rels("xl/_rels/workbook.xml.rels")
        base = posixpath.dirname(wb)
        sheets: dict[str, str] = {}

        def start(tag: str, attrs: dict[str, str]) -> None:
            if _local(tag) != "sheet":
                return
            rid = attrs.get(_REL_NS + _NS_SEP + "id") or next(
                (v for k, v in attrs.items() if _local(k) == "id"), None
            )
            sheet_name = attrs.get("name")
            if sheet_name is None or rid not in rels:
                return
            sheets[sheet_name] = _resolve(base, rels[rid][0])

        self._run(wb, start=start)
        shared = next(
            (_resolve(base, t) for t, typ in rels.values() if typ.endswith(_SHARED_STRINGS_TYPE)),
            None,
        )
        if shared is None and "xl/sharedStrings.xml" in self._names:
            shared = "xl/sharedStrings.xml"
        return sheets, shared

    def _part(self, sheet: str) -> str:
        try:
            return self._sheets[sheet]
        except KeyError:
            raise KeyError(f"no sheet named {sheet!r}; sheets: {self.sheet_names}") from None

    def _shared_strings(self) -> list[str]:
        if self._shared is not None:
            return self._shared
        strings: list[str] = []
        self._shared = strings
        if self._shared_part is None or self._shared_part not in self._names:
            return strings
        state: dict[str, Any] = {"si": None, "t": None, "rph": False}

        def start(tag: str, _attrs: dict[str, str]) -> None:
            local = _local(tag)
            if local == "si":
                state["si"] = []
            elif local == "t" and state["si"] is not None and not state["rph"]:
                state["t"] = []
            elif local == "rPh":
                state["rph"] = True

        def chars(data: str) -> None:
            if state["t"] is not None:
                state["t"].append(data)

        def end(tag: str) -> None:
            local = _local(tag)
            if local == "t" and state["t"] is not None:
                state["si"].append("".join(state["t"]))
                state["t"] = None
            elif local == "rPh":
                state["rph"] = False
            elif local == "si":
                strings.append("".join(state["si"] or ()))
                state["si"] = None

        self._run(self._shared_part, start=start, end=end, chars=chars)
        return strings


def _resolve(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base, target))


def _cell_value(
    ctype: str | None, v: str | None, inline: list[str] | None, shared: list[str]
) -> str | None:
    if ctype == "inlineStr":
        return "".join(inline) if inline is not None else v
    if v is None:
        return None
    if ctype == "s":
        try:
            return shared[int(v)]
        except (ValueError, IndexError):
            raise XlsxError(f"invalid shared-string index {v!r}") from None
    if ctype == "b":
        return "TRUE" if v.strip() in ("1", "true") else "FALSE"
    return v


def sheet_names(path: XlsxSource, *, max_member_size: int = DEFAULT_MAX_MEMBER_SIZE) -> list[str]:
    """Return the worksheet names of an ``.xlsx`` workbook in workbook order.

    Args:
        path: Path or binary seekable stream.
        max_member_size: Maximum uncompressed size of any single part, in bytes.
    """
    with XlsxWorkbook(path, max_member_size=max_member_size) as wb:
        return wb.sheet_names


def iter_rows(
    path: XlsxSource, sheet: str, *, max_member_size: int = DEFAULT_MAX_MEMBER_SIZE
) -> Iterator[list[str | None]]:
    """Stream the rows of one worksheet (see :meth:`XlsxWorkbook.iter_rows`).

    Args:
        path: Path or binary seekable stream.
        sheet: Worksheet name.
        max_member_size: Maximum uncompressed size of any single part, in bytes.

    Yields:
        One list of cell values per row.
    """
    with XlsxWorkbook(path, max_member_size=max_member_size) as wb:
        yield from wb.iter_rows(sheet)
