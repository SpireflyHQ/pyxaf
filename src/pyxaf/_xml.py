"""Hardened streaming XML engine on top of the standard library's ``pyexpat``.

Security properties (see docs/security.md):

- DOCTYPE and ENTITY declarations raise :class:`~pyxaf.errors.ForbiddenConstructError`; no version
  of the auditfile uses a DTD. This blocks entity-expansion and external-entity attacks before any
  expansion happens. Expat itself never opens files or network connections.
- Element depth and per-element text size are limited.
- No "recover" mode: malformed XML raises :class:`~pyxaf.errors.XmlSyntaxError` with a position.

The engine turns the document into a stream of events. Elements whose local name is in
``record_tags`` are materialised completely as :class:`~pyxaf.raw.RawRecord` and emitted when they
end ("records": ``transaction``, ``ledgerAccount``, …). Everything outside records is a "container";
the engine keeps the leaf values of open containers (``jrnID`` of the current journal, the totals of
``transactions`` …) so records can be put in context, but never retains records themselves.
Memory therefore does not grow with file size.
"""

from __future__ import annotations

import pyexpat
from collections.abc import Iterable, Iterator
from typing import Any, Final, TypeAlias

from .errors import ForbiddenConstructError, LimitExceededError, XmlSyntaxError
from .findings import FindingCollector
from .raw import RawRecord

__all__ = [
    "CHILDREN",
    "END",
    "FIELDS",
    "LINE",
    "RECORD",
    "SEQ",
    "START",
    "TAG",
    "Frame",
    "XmlEngine",
]

# event kinds
START: Final = 0
"""``(START, local_name, depth, line, frame)`` — a container element started; ``frame`` is its
live frame (its ``FIELDS`` fill in as parsing proceeds)."""
RECORD: Final = 1
"""``(RECORD, RawRecord, parents)`` — a record ended; ``parents`` are the open container frames."""
END: Final = 2
"""``(END, RawRecord, depth)`` — a complex container element ended."""

# frame layout (lists are markedly faster than objects in the hot path)
TAG, FIELDS, CHILDREN, TEXT, LINE, SEQ = range(6)
Frame: TypeAlias = list[Any]  # [tag, fields | None, children | None, text, line, sequence | None]

CHUNK: Final = 1 << 16
DEFAULT_MAX_DEPTH: Final = 32
DEFAULT_MAX_TEXT: Final = 10 * 1024 * 1024
MAX_RUNS: Final = 256
"""Strict mode keeps at most this many runs of child names per element (a valid document has
at most one run per declared child, far fewer)."""


class XmlEngine:
    """Pull-style event stream over a byte-chunk iterable.

    Args:
        chunks: Raw document bytes.
        record_tags: Local names of elements to materialise as records.
        skip_tags: Local names of elements whose whole subtree is ignored (outside records).
        encoding: Override the document encoding (passed to Expat).
        max_depth: Maximum element nesting depth.
        max_text: Maximum text length of a single element.
        findings: Collector for non-fatal problems (trailing data).
        file_index: Index of the file in a multi-file set (for error positions).
        strict: Validation mode: record the order of every element's children (``RawRecord.
            sequence``) and report elements outside the document's namespace (``XAF3011``).
    """

    def __init__(
        self,
        chunks: Iterable[bytes],
        *,
        record_tags: frozenset[str],
        skip_tags: frozenset[str] = frozenset(),
        encoding: str | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_text: int = DEFAULT_MAX_TEXT,
        findings: FindingCollector | None = None,
        file_index: int = 0,
        strict: bool = False,
    ) -> None:
        self._chunks = chunks
        self._strict = strict
        self._record_tags = record_tags
        self._skip_tags = skip_tags
        self._encoding = encoding
        self._max_depth = max_depth
        self._max_text = max_text
        self._findings = findings
        self._file = file_index
        self.root_tag: str | None = None
        self.root_prefix: str | None = None
        self.root_attributes: dict[str, str] = {}
        self.root_closed = False
        self.namespace: str | None = None
        self.stack: list[Frame] = []
        """Currently open element frames (inspect only between events)."""

    # ------------------------------------------------------------------ public
    def events(self) -> Iterator[tuple[Any, ...]]:
        """Parse the document lazily, yielding events (see module docstring)."""
        parser = pyexpat.ParserCreate(self._encoding)
        parser.buffer_text = True
        parser.buffer_size = CHUNK
        parser.ordered_attributes = False
        parser.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_NEVER)

        pending: list[tuple[Any, ...]] = []
        stack: list[Frame] = []
        self.stack = stack
        record_tags = self._record_tags
        skip_tags = self._skip_tags
        max_depth = self._max_depth
        max_text = self._max_text
        # mutable state captured by the closures
        state = [0, 0, 0]  # depth, record_depth, skip_depth
        DEPTH, REC, SKIP = 0, 1, 2  # noqa: N806
        strict = self._strict
        sink = self._findings
        # strict mode: namespace declarations in scope, as (depth, {prefix: uri}); "" = default
        ns_scopes: list[tuple[int, dict[str, str]]] = []
        doc_ns: list[str | None] = [None]
        root_prefix: list[str | None] = [None]  # None until the root element is seen

        def check_namespace(name: str, attrs: dict[str, str], depth: int) -> None:
            if attrs:
                decl = {
                    (k[6:] if k != "xmlns" else ""): v
                    for k, v in attrs.items()
                    if k == "xmlns" or k.startswith("xmlns:")
                }
                if decl:
                    ns_scopes.append((depth, decl))
            i = name.find(":")
            prefix = name[:i] if i >= 0 else ""
            uri: str | None = None
            bound = not prefix
            for _depth, decl in reversed(ns_scopes):
                if prefix in decl:
                    uri, bound = decl[prefix] or None, True
                    break
            if depth == 1:
                doc_ns[0] = uri
                root_prefix[0] = prefix
                return
            if sink is None or (bound and uri == doc_ns[0]):
                return
            local = name[i + 1 :]
            where = (
                f"uses the undeclared prefix {prefix!r}"
                if not bound
                else f"is in namespace {uri!r}, not in the document's {doc_ns[0]!r}"
            )
            sink.add(
                "XAF3011",
                f"<{local}> {where}",
                line=parser.CurrentLineNumber,
                value=name,
            )

        def forbidden(*_: object) -> None:
            raise ForbiddenConstructError(
                "DOCTYPE/ENTITY declarations are not allowed in an auditfile "
                "(refused for security)",
                line=parser.CurrentLineNumber,
                column=parser.CurrentColumnNumber,
                file=self._file,
            )

        def start(name: str, attrs: dict[str, str]) -> None:
            depth = state[DEPTH] + 1
            state[DEPTH] = depth
            if depth > max_depth:
                raise LimitExceededError(
                    f"element nesting deeper than {max_depth} at line {parser.CurrentLineNumber}"
                )
            if state[SKIP]:
                return
            i = name.find(":")
            local = name[i + 1 :] if i >= 0 else name
            # fast path: no declarations below the root and the root's prefix → same namespace
            if strict and (
                attrs or len(ns_scopes) != 1 or (name[:i] if i >= 0 else "") != root_prefix[0]
            ):
                check_namespace(name, attrs, depth)
            if depth == 1:
                self._root(name, local, attrs)
            if stack:
                stack[-1][TEXT] = ""  # whitespace between children is not content
            if not state[REC]:
                if local in skip_tags:
                    state[SKIP] = depth
                    parser.CharacterDataHandler = None
                    if stack and stack[-1][CHILDREN] is None:
                        stack[-1][CHILDREN] = {}
                    return
                if local in record_tags:
                    state[REC] = depth
                    parser.StartElementHandler = start_in_record
                    stack.append([local, None, None, "", parser.CurrentLineNumber, None])
                    return
                frame: Frame = [local, None, None, "", parser.CurrentLineNumber, None]
                pending.append((START, local, depth, frame[LINE], frame))
                stack.append(frame)
                return
            stack.append([local, None, None, "", parser.CurrentLineNumber, None])

        def start_in_record(name: str, attrs: dict[str, str]) -> None:
            depth = state[DEPTH] + 1
            state[DEPTH] = depth
            if depth > max_depth:
                raise LimitExceededError(
                    f"element nesting deeper than {max_depth} at line {parser.CurrentLineNumber}"
                )
            i = name.find(":")
            if strict and (
                attrs or len(ns_scopes) != 1 or (name[:i] if i >= 0 else "") != root_prefix[0]
            ):
                check_namespace(name, attrs, depth)
            stack.append(
                [name[i + 1 :] if i >= 0 else name, None, None, "", parser.CurrentLineNumber, None]
            )

        def chars(data: str) -> None:
            # a single call delivers at most one input chunk (64 KiB), far below max_text
            if stack:
                f = stack[-1]
                text = f[TEXT]
                if text:
                    text += data
                    if len(text) > max_text:
                        raise LimitExceededError(
                            f"text of <{f[TAG]}> longer than {max_text:,} characters "
                            f"(line {f[LINE]})"
                        )
                    f[TEXT] = text
                else:
                    f[TEXT] = data

        def chars_checked(data: str) -> None:
            # variant for small max_text values: also check the first piece
            if stack:
                f = stack[-1]
                text = f[TEXT] + data if f[TEXT] else data
                if len(text) > max_text:
                    raise LimitExceededError(
                        f"text of <{f[TAG]}> longer than {max_text:,} characters (line {f[LINE]})"
                    )
                f[TEXT] = text

        if max_text < 4 * CHUNK:
            chars = chars_checked

        def end(_name: str) -> None:
            depth = state[DEPTH]
            state[DEPTH] = depth - 1
            if ns_scopes and ns_scopes[-1][0] == depth:
                ns_scopes.pop()
            if state[SKIP]:
                if depth == state[SKIP]:
                    state[SKIP] = 0
                    parser.CharacterDataHandler = chars
                return
            f = stack.pop()
            if depth == 1:
                self.root_closed = True
            if strict and stack:  # the parent's child sequence, as runs of equal names
                runs = stack[-1][SEQ]
                tag = f[TAG]
                if runs is None:
                    stack[-1][SEQ] = [[tag, 1]]
                elif runs[-1][0] == tag:
                    runs[-1][1] += 1
                elif len(runs) < MAX_RUNS:
                    runs.append([tag, 1])
            fields = f[FIELDS]
            children = f[CHILDREN]
            rec_depth = state[REC]
            if depth == rec_depth:
                state[REC] = 0
                parser.StartElementHandler = start
                if stack and stack[-1][CHILDREN] is None:
                    stack[-1][CHILDREN] = {}  # mark the container as having element children
                pending.append(
                    (
                        RECORD,
                        RawRecord(f[TAG], fields or {}, children, f[LINE], None, f[SEQ]),
                        tuple(stack),
                    )
                )
                return
            if not stack:
                if fields is None and children is None:
                    pending.append((END, RawRecord(f[TAG], {}, None, f[LINE], None, f[SEQ]), depth))
                    return
            elif fields is None and children is None:
                # leaf element (no element children): a field of its parent
                parent = stack[-1]
                pf = parent[FIELDS]
                tag = f[TAG]
                if pf is None:
                    parent[FIELDS] = {tag: f[TEXT]}
                elif tag not in pf:
                    pf[tag] = f[TEXT]
                else:  # repeated leaf: keep every occurrence as a text-only child
                    ch = parent[CHILDREN]
                    if ch is None:
                        ch = parent[CHILDREN] = {}
                    lst = ch.setdefault(tag, [])
                    if not lst:
                        lst.append(RawRecord(tag, {}, None, parent[LINE], pf[tag]))
                    lst.append(RawRecord(tag, {}, None, f[LINE], f[TEXT]))
                return
            rec = RawRecord(f[TAG], fields or {}, children, f[LINE], None, f[SEQ])
            if stack:
                parent = stack[-1]
                ch = parent[CHILDREN]
                if ch is None:
                    parent[CHILDREN] = {rec.tag: [rec]}
                else:
                    lst = ch.get(rec.tag)
                    if lst is None:
                        ch[rec.tag] = [rec]
                    else:
                        lst.append(rec)
            if not rec_depth:
                pending.append((END, rec, depth))

        parser.StartDoctypeDeclHandler = forbidden
        parser.EntityDeclHandler = forbidden
        parser.UnparsedEntityDeclHandler = forbidden
        parser.NotationDeclHandler = forbidden
        parser.ExternalEntityRefHandler = forbidden  # type: ignore[assignment]
        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = chars

        parse = parser.Parse
        error: Exception | None = None
        for chunk in self._chunks:
            try:
                parse(chunk, False)
            except pyexpat.ExpatError as exc:
                if not self._trailing(exc):
                    error = self._error(exc)
                break
            except (LimitExceededError, XmlSyntaxError) as exc:
                error = exc
                break
            if pending:
                yield from pending
                pending.clear()
        else:
            try:
                parse(b"", True)
            except pyexpat.ExpatError as exc:
                if not self._trailing(exc):
                    error = self._error(exc)
        # events completed before an error are still delivered, then the error is raised
        if pending:
            yield from pending
            pending.clear()
        if error is not None:
            raise error
        if self.root_tag is None:
            raise XmlSyntaxError("no root element found", line=1, column=0, file=self._file)

    # ----------------------------------------------------------------- helpers
    def _root(self, name: str, local: str, attrs: dict[str, str]) -> None:
        self.root_tag = local
        prefix = name[: name.find(":")] if ":" in name else None
        self.root_prefix = prefix
        self.root_attributes = dict(attrs)
        key = f"xmlns:{prefix}" if prefix else "xmlns"
        self.namespace = attrs.get(key)

    def _trailing(self, exc: pyexpat.ExpatError) -> bool:
        """Non-whitespace data after the root element: report and ignore (data is complete)."""
        if not self.root_closed:
            return False
        if self._findings is not None:
            self._findings.add(
                "XAF1013",
                f"ignored data after the end of the document ({pyexpat.ErrorString(exc.code)})",
                line=exc.lineno,
                column=exc.offset,
            )
        return True

    def _error(self, exc: pyexpat.ExpatError) -> XmlSyntaxError:
        msg = pyexpat.ErrorString(exc.code)
        hint = ""
        if exc.code == pyexpat.errors.codes[pyexpat.errors.XML_ERROR_INVALID_TOKEN]:
            hint = (
                " (an XML-illegal control character, a bare '&' or a wrong encoding is the usual "
                "cause; see the repair= and encoding= options)"
            )
        elif exc.code == pyexpat.errors.codes[pyexpat.errors.XML_ERROR_UNDEFINED_ENTITY]:
            hint = " (unescaped '&'? see repair={'bare-ampersand'})"
        elif exc.code == pyexpat.errors.codes[pyexpat.errors.XML_ERROR_NO_ELEMENTS]:
            hint = " (empty or truncated file?)"
        elif exc.code == pyexpat.errors.codes[pyexpat.errors.XML_ERROR_UNCLOSED_TOKEN] or (
            exc.code == pyexpat.errors.codes[pyexpat.errors.XML_ERROR_TAG_MISMATCH]
        ):
            hint = " (truncated or incorrectly split file?)"
        return XmlSyntaxError(
            f"XML not well-formed: {msg} at line {exc.lineno}, column {exc.offset}{hint}",
            line=exc.lineno,
            column=exc.offset,
            file=self._file,
        )
