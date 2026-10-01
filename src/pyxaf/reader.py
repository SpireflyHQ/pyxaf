"""The streaming reader: :func:`open` and :class:`AuditFile`."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Any, Literal

from . import _xml
from ._encoding import REPAIRS, expat_plan, repair_chunks, transcode_chunks
from ._normalize import NegativePolicy, Normalizer, journal_id_from
from ._source import Source, SourceLike, open_source
from .detect import _detect_source, report_format
from .errors import NotAnAuditfileError, PyxafError
from .findings import Finding, FindingCollector, Severity
from .formats import Family, FormatInfo, Version
from .models import (
    Company,
    Header,
    Journal,
    JournalKind,
    LedgerAccount,
    Line,
    OpeningBalance,
    Period,
    Relation,
    RgsRef,
    Transaction,
    TransactionTotals,
    VatCode,
)
from .raw import RawRecord
from .values import exact_sum

if TYPE_CHECKING:
    from .tables import Tables

__all__ = ["AuditFile", "open"]

RECORD_TAGS: Mapping[Family, frozenset[str]] = {
    Family.XAF: frozenset(
        {
            "header",
            "customerSupplier",
            "ledgerAccount",
            "basic",
            "vatCode",
            "period",
            "obLine",
            "obSbLine",
            "transaction",
            "sbLine",
        }
    ),
    Family.CLAIR2: frozenset({"header", "ledgerAccount", "customerSupplier", "transaction"}),
}
_MASTER_TAGS = frozenset(
    {"header", "customerSupplier", "ledgerAccount", "vatCode", "period", "obLine", "basic"}
)
_OB_PERIOD_KEYS = frozenset({"0", "00", "000"})
# containers whose live frame is kept for context (company fields, control totals)
_FRAME_TAGS = frozenset({"company", "transactions"})

Observer = Callable[[int, tuple[Any, ...]], None]
"""Receives ``(file_index, engine_event)`` for every raw event (used by the validator)."""


@dataclass(slots=True)
class _Part:
    """One XML document (possibly concatenated from continuation files)."""

    index: int
    sources: list[Source]
    info: FormatInfo
    continuation_infos: list[FormatInfo] = field(default_factory=list)
    paused: Iterator[tuple[Any, ...]] | None = None
    pending: tuple[Any, ...] | None = None
    engine: _xml.XmlEngine | None = None
    complete: bool = False  # a full *normalized* pass has finished (value findings reported)
    frames: dict[str, Any] = field(default_factory=dict)
    totals_fields: dict[str, str] | None = None
    findings: FindingCollector | None = None
    company_raw: RawRecord | None = None  # this part's administration (set checks)
    header_raw: RawRecord | None = None

    @property
    def transactions_frame(self) -> dict[str, str] | None:
        """Leaf values of ``transactions`` (linesCount, totals) — final or live."""
        if self.totals_fields is not None:
            return self.totals_fields
        frame = self.frames.get("transactions")
        if frame is None:
            return None
        return frame[_xml.FIELDS] or {}


@dataclass(slots=True)
class _Master:
    header: Header | None = None
    header_raw: RawRecord | None = None
    company_raw: RawRecord | None = None
    accounts: dict[str, LedgerAccount] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    vat_codes: dict[str, VatCode] = field(default_factory=dict)
    periods: dict[str, Period] = field(default_factory=dict)
    ob_lines: list[Line] = field(default_factory=list)
    ob_raw: RawRecord | None = None
    ob_frame: dict[str, str] | None = None
    journals: dict[str, Journal] = field(default_factory=dict)
    journals_complete: bool = False
    taxonomies: list[RawRecord] = field(default_factory=list)


def _strip_prolog(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Drop a continuation file's BOM, XML declaration and leading comments."""
    buf = b""
    it = iter(chunks)
    for chunk in it:
        buf += chunk
        if buf.startswith(b"\xef\xbb\xbf"):
            buf = buf[3:]
        while True:
            s = buf.lstrip()
            if s.startswith(b"<?"):
                end = s.find(b"?>")
                if end == -1:
                    break
                buf = s[end + 2 :]
            elif s.startswith(b"<!--"):
                end = s.find(b"-->")
                if end == -1:
                    break
                buf = s[end + 3 :]
            elif s:
                yield s
                yield from it
                return
            else:
                buf = s
                break
    if buf.strip():
        yield buf


class AuditFile:
    """An opened auditfile (or multi-file set): master data eagerly, transactions streamed.

    Use :func:`pyxaf.open` to create one. Master data (header, company, accounts, relations, VAT
    codes, periods, opening balance) is read when the file is opened; transactions are parsed
    lazily each time :meth:`transactions` or :meth:`lines` is iterated (re-reading the source),
    so memory use does not depend on file size.
    """

    header: Header
    """File header."""
    company: Company
    """The administration (company)."""

    def __init__(
        self,
        source: SourceLike | Source | Sequence[SourceLike | Source],
        *,
        encoding: str | None = None,
        repair: Iterable[str] = (),
        negative_amounts: NegativePolicy = "flip",
        max_findings_per_code: int | None = 100,
        max_findings: int | None = 10_000,
        severity_overrides: Mapping[str, Severity | None] | None = None,
        max_depth: int = _xml.DEFAULT_MAX_DEPTH,
        max_text_size: int = _xml.DEFAULT_MAX_TEXT,
        max_decompressed_size: int | None = None,
        _observer: Observer | None = None,
        _value_findings: bool = True,
        _findings: FindingCollector | None = None,
        _on_parts: Callable[[Sequence[FormatInfo]], None] | None = None,
    ) -> None:
        repairs = frozenset(repair)
        unknown = repairs - REPAIRS
        if unknown:
            raise ValueError(f"unknown repair(s) {sorted(unknown)}; choose from {sorted(REPAIRS)}")
        if negative_amounts not in ("flip", "abs"):
            raise ValueError("negative_amounts must be 'flip' or 'abs'")
        self._encoding = encoding
        self._repairs = repairs
        self._policy: NegativePolicy = negative_amounts
        self._max_depth = max_depth
        self._max_text = max_text_size
        self._observer = _observer
        self._value_findings = _value_findings
        self._findings = (
            _findings
            if _findings is not None
            else FindingCollector(
                max_per_code=max_findings_per_code,
                max_total=max_findings,
                overrides=dict(severity_overrides or {}),
            )
        )
        if isinstance(source, (str, bytes, bytearray, memoryview, os.PathLike, Source)) or hasattr(
            source, "read"
        ):
            sources: list[SourceLike | Source] = [source]  # type: ignore[list-item]
        else:
            sources = list(source)
            if not sources:
                raise ValueError("no source given")
        opened = [open_source(s, max_decompressed_size=max_decompressed_size) for s in sources]
        self._names = tuple(s.name for s in opened)
        infos = [_detect_source(s, encoding) for s in opened]
        self._parts = self._group(opened, infos)
        if _on_parts is not None:
            _on_parts([p.info for p in self._parts])
        self.format: FormatInfo = self._parts[0].info
        """Detection result of the (first) file."""
        self.version: Version = self.format.version or Version.XAF32
        self._master = _Master()
        self._adf: Any = None
        for part in self._parts:
            for info in (part.info, *part.continuation_infos):
                report_format(info, self._finding_sink(part.index))
        if self.format.family is Family.ADF:
            from ._adf import AdfReader  # noqa: PLC0415 - keep import cheap for XML users

            if len(self._parts) > 1:
                raise PyxafError("multi-file sets are not supported for ADF")
            adf_source = opened[0]
            if not adf_source.reopenable:
                # ADF needs two passes (master data is collected from the lines): spool a
                # one-shot stream to a temporary file
                self._spool = tempfile.TemporaryFile()  # noqa: SIM115 - closed in close()
                for chunk in adf_source.chunks():
                    self._spool.write(chunk)
                self._spool.seek(0)
                adf_source = open_source(self._spool)
            self._adf = AdfReader(
                adf_source,
                encoding=self.format.encoding.effective,
                policy=self._policy,
                findings=self._findings,
                value_findings=_value_findings,
            )
            self._adf.load_master(self._master)
        else:
            for part in self._parts:
                self._load_master(part)
            self._check_set()
        self._finish_master()

    # ---------------------------------------------------------------- set-up
    def _finding_sink(self, index: int) -> FindingCollector:
        self._findings.file = index
        return self._findings

    def _group(self, opened: list[Source], infos: list[FormatInfo]) -> list[_Part]:
        parts: list[_Part] = []
        for i, (src, info) in enumerate(zip(opened, infos, strict=True)):
            is_fragment = info.continuation is not None or (
                info.root is not None and info.root.lower() != "auditfile"
            )
            if i > 0 and is_fragment and parts:
                parts[-1].sources.append(src)
                parts[-1].continuation_infos.append(info)
                continue
            if is_fragment and i == 0 and info.root and info.root.lower() != "auditfile":
                raise NotAnAuditfileError(
                    f"{src.name}: is a continuation file; pass the first file of the set as well"
                )
            parts.append(_Part(len(parts), [src], info))
        return parts

    def _check_set(self) -> None:
        """Check that the files of a multi-file set belong together (``XAF3008/3009/6011``)."""
        parts = self._parts
        for part in parts:
            self._check_continuations(part)
        if len(parts) < 2:
            return
        first = parts[0]
        companies = [
            self._normalizer(p, value_findings=False).company(p.company_raw, p.header_raw)
            for p in parts
        ]
        for part, company in zip(parts[1:], companies[1:], strict=True):
            sink = self._finding_sink(part.index)
            if part.info.version != first.info.version:
                sink.add(
                    "XAF3008",
                    f"file is {part.info.version}, the first file of the set is "
                    f"{first.info.version}; each file is read by the rules of its own version",
                    value=str(part.info.version),
                )
            ref = companies[0]
            ids = ("identifier", "tax_registration_id", "commerce_number")
            differing = [
                k
                for k in ids
                if getattr(ref, k)
                and getattr(company, k)
                and getattr(ref, k) != getattr(company, k)
            ]
            if differing:
                sink.add(
                    "XAF6011",
                    "file belongs to another administration than the first file of the set ("
                    + ", ".join(
                        f"{k} {getattr(company, k)!r} ≠ {getattr(ref, k)!r}" for k in differing
                    )
                    + ")",
                    value=getattr(company, differing[0]),
                )
            elif (ref.name and company.name) and _fold(ref.name) != _fold(company.name):
                sink.add(
                    "XAF6011",
                    f"company name {company.name!r} differs from the first file's {ref.name!r}",
                    value=company.name,
                    severity=Severity.WARNING,
                )
        self._findings.file = 0

    def _check_continuations(self, part: _Part) -> None:
        """'Vervolgbestand x van y' must count 2, 3, … y without gaps (Toelichting 4.0 §1.4)."""
        if not part.continuation_infos:
            return
        expected_total = len(part.continuation_infos) + 1
        problems = []
        for n, pos in enumerate(i.continuation for i in part.continuation_infos):
            if pos is None:
                continue
            x, y = pos
            if x != n + 2:
                problems.append(f"file {n + 2} of the set says 'Vervolgbestand {x} van {y}'")
            elif y != expected_total:
                problems.append(
                    f"'Vervolgbestand {x} van {y}', but the set has {expected_total} files"
                )
        if problems:
            self._finding_sink(part.index).add(
                "XAF3009",
                "continuation files are numbered inconsistently: " + "; ".join(problems),
            )

    def _engine(self, part: _Part, *, skip: frozenset[str] = frozenset()) -> _xml.XmlEngine:
        first_pass = part.engine is None
        sink = self._findings if first_pass else None
        stream, expat_enc = self._prepared(part, sink, first_pass=first_pass)
        return _xml.XmlEngine(
            stream,
            record_tags=RECORD_TAGS[part.info.family or Family.XAF],
            skip_tags=skip,
            encoding=expat_enc,
            max_depth=self._max_depth,
            max_text=self._max_text,
            findings=sink,
            file_index=part.index,
            strict=self._observer is not None and first_pass,
        )

    def _prepared(
        self, part: _Part, sink: FindingCollector | None, *, first_pass: bool = False
    ) -> tuple[Iterable[bytes], str | None]:
        """The document bytes as the parser must see them, and the encoding to force.

        Concatenates continuation files, transcodes encodings Expat cannot decode and applies the
        opt-in repairs. The second value is the encoding that overrides the XML declaration
        (``None``: trust the declaration). The XSD pass uses the same bytes.
        """
        info = part.info
        effective = info.encoding.effective
        if (
            self._encoding is None
            and "latin1-as-cp1252" in self._repairs
            and effective == "iso8859-1"
        ):
            effective = "cp1252"
            if sink is not None:
                sink.add("XAF1011", "ISO-8859-1 content decoded as windows-1252")
        expat_enc, codec, unknown = expat_plan(effective)
        if self._encoding and expat_enc is None:
            expat_enc = effective  # an explicit override must beat the XML declaration
        if unknown and sink is not None:
            sink.add(
                "XAF1003",
                f"unknown encoding {info.encoding.declared or effective!r}; read as UTF-8",
                value=info.encoding.declared,
            )
        check_c1 = effective == "iso8859-1" and not self._encoding and first_pass

        def chunks() -> Iterator[bytes]:
            for n, src in enumerate(part.sources):
                raw: Iterable[bytes] = src.chunks()
                if n > 0:
                    raw = _strip_prolog(raw)
                yield from raw

        stream: Iterable[bytes] = chunks()
        if codec is not None:
            stream = self._transcoded(stream, codec, sink)
        # byte-level repairs need an ASCII-compatible stream (transcoded text is UTF-8)
        utf16 = codec is None and effective.startswith(("utf-16", "utf-32"))
        repairs = frozenset() if utf16 else self._repairs
        if repairs or check_c1:
            stream = repair_chunks(stream, repairs, sink, check_c1=check_c1)
        return stream, expat_enc

    @staticmethod
    def _transcoded(
        stream: Iterable[bytes], codec: str, sink: FindingCollector | None
    ) -> Iterator[bytes]:
        counter = [0]
        yield from transcode_chunks(stream, codec, counter)
        if counter[0] and sink is not None:
            sink.add(
                "XAF1006",
                f"{counter[0]:,} byte(s) are not defined in {codec}; kept as the ISO-8859-1 "
                "character of the same value",
            )

    def _normalizer(
        self, part: _Part, *, value_findings: bool | None = None, quality_findings: bool = True
    ) -> Normalizer:
        vf = self._value_findings if value_findings is None else value_findings
        return Normalizer(
            part.info.version or Version.XAF32,
            negative_amounts=self._policy,
            findings=self._findings,
            value_findings=vf,
            quality_findings=quality_findings,
            file=part.index,
        )

    def _load_master(self, part: _Part) -> None:
        engine = self._engine(part)
        part.engine = engine
        events = engine.events()
        norm = self._normalizer(part)
        m = self._master
        first = part.index == 0
        observer = self._observer
        self._findings.file = part.index
        seqs = {"account": len(m.accounts), "relation": len(m.relations)}
        for ev in events:
            if observer is not None:
                observer(part.index, ev)
            kind = ev[0]
            if kind == _xml.START:
                local = ev[1]
                if local in _FRAME_TAGS and local not in part.frames:
                    part.frames[local] = ev[4]
                elif local == "journal":
                    part.paused = events
                    part.pending = ev
                    self._capture_company(part)
                    return
                continue
            if kind == _xml.RECORD:
                rec: RawRecord = ev[1]
                tag = rec.tag
                if tag == "header" and part.header_raw is None:
                    part.header_raw = rec
                if tag == "transaction":
                    part.paused = events
                    part.pending = ev
                    self._capture_company(part)
                    return
                self._master_record(rec, ev[2], norm, first, seqs)
            elif kind == _xml.END:
                if ev[1].tag == "company" and part.company_raw is None:
                    part.company_raw = ev[1]
                self._container_end(ev[1], part, norm, first)
        part.paused = None
        part.pending = None

    def _capture_company(self, part: _Part) -> None:
        frame = part.frames.get("company")
        if frame is None:
            return
        rec = RawRecord("company", frame[_xml.FIELDS] or {}, frame[_xml.CHILDREN], frame[_xml.LINE])
        if part.company_raw is None:
            part.company_raw = rec
        if part.index == 0 and self._master.company_raw is None:
            self._master.company_raw = rec

    def _master_record(
        self,
        rec: RawRecord,
        parents: tuple[Any, ...],
        norm: Normalizer,
        first: bool,
        seqs: dict[str, int],
    ) -> None:
        m = self._master
        tag = rec.tag
        if tag == "header":
            if m.header is None:
                m.header = norm.header(rec)
                m.header_raw = rec
            return
        if tag == "ledgerAccount":
            acc = norm.account(rec, seqs["account"])
            seqs["account"] += 1
            self._put(m.accounts, acc.id, acc, "XAF6002", "account", rec, first)
        elif tag == "customerSupplier":
            rel = norm.relation(rec, seqs["relation"])
            seqs["relation"] += 1
            self._put(m.relations, rel.id, rel, "XAF6001", "customer/supplier", rec, first)
        elif tag == "vatCode":
            vat = norm.vat_code(rec, len(m.vat_codes))
            self._put(m.vat_codes, vat.id, vat, "XAF6008", "VAT code", rec, first)
        elif tag == "period":
            per = norm.period(rec, len(m.periods))
            self._put(m.periods, per.key, per, "XAF6009", "period", rec, first)
        elif tag == "obLine":
            if first:
                m.ob_lines.append(norm.ob_line(rec))
                for frame in parents:
                    if frame[_xml.TAG] == "openingBalance":
                        m.ob_frame = frame[_xml.FIELDS] or {}

    def _put(
        self,
        target: dict[str, Any],
        key: str,
        value: Any,
        code: str,
        what: str,
        rec: RawRecord,
        first: bool,
    ) -> None:
        if key not in target:
            target[key] = value
            return
        if first:
            self._findings.add(
                code, f"duplicate {what} ID {key!r}; the first is used", line=rec.line, value=key
            )
        elif target[key].raw is not None and target[key].raw != rec:
            self._findings.add(
                "XAF6010",
                f"{what} {key!r} differs between files; the first file's definition is used",
                line=rec.line,
                value=key,
            )

    def _container_end(self, rec: RawRecord, part: _Part, norm: Normalizer, first: bool) -> None:
        tag = rec.tag
        m = self._master
        if tag == "company" and first:
            m.company_raw = rec
        elif tag == "openingBalance" and first:
            m.ob_raw = rec
            m.ob_frame = dict(rec.fields)
        elif tag == "transactions":
            part.totals_fields = dict(rec.fields)
        elif tag == "taxonomies":
            m.taxonomies.append(rec)
        elif tag == "journal":
            jr = norm.journal(rec, len(m.journals))
            m.journals.setdefault(jr.id, jr)

    def _finish_master(self) -> None:
        m = self._master
        if m.header is None:
            if self._adf is None and self._observer is None:  # the validator reports it itself
                self._findings.add("XAF3010", "no <header> element found", path="/auditfile/header")
            m.header = Header(
                fiscal_year=None,
                start_date=None,
                end_date=None,
                currency=None,
                created=None,
                software_name=None,
                software_version=None,
            )
        if self._adf is None:
            norm = self._normalizer(self._parts[0], value_findings=False)
            if self.format.family is Family.CLAIR2:
                self._company = norm.company(None, m.header_raw)
            else:
                self._company = norm.company(m.company_raw, None)
            # XAF 3.1: RGS links live in generalLedger/taxonomies/taxonomy/taxoElement
            for tax in m.taxonomies:
                for taxonomy in tax.all("taxonomy"):
                    for el in taxonomy.all("taxoElement"):
                        acc_id = (el.fields.get("glAccID") or "").strip()
                        code = el.fields.get("txCd")
                        acc = m.accounts.get(acc_id)
                        if acc is not None and code and acc.rgs is None:
                            m.accounts[acc_id] = _replace_rgs(acc, RgsRef.parse(code, "taxonomies"))
        else:
            self._company = self._adf.company
        self.header = m.header
        self.company = self._company
        self.accounts: Mapping[str, LedgerAccount] = MappingProxyType(m.accounts)
        """Ledger accounts by ID (the first definition wins for duplicates)."""
        self.relations: Mapping[str, Relation] = MappingProxyType(m.relations)
        """Customers/suppliers by ID."""
        self.vat_codes: Mapping[str, VatCode] = MappingProxyType(m.vat_codes)
        """VAT codes by ID."""
        self.periods: Mapping[str, Period] = MappingProxyType(m.periods)
        """Periods by period key (the period number as written)."""

    # ------------------------------------------------------------- properties
    @property
    def findings(self) -> tuple[Finding, ...]:
        """Findings collected while reading so far (grows as transactions are iterated)."""
        return self._findings.snapshot()

    @property
    def files(self) -> tuple[str, ...]:
        """Names of the underlying files."""
        return self._names

    @property
    def journals(self) -> Mapping[str, Journal]:
        """Journals by ID.

        In XML files journals enclose their transactions, so the first access may scan the whole
        file (cheaply, skipping transaction content).
        """
        m = self._master
        if not m.journals_complete:
            if self._adf is not None:
                self._adf.load_journals(m.journals)
            else:
                for part in self._parts:
                    engine = self._engine(part, skip=frozenset({"transaction"}))
                    norm = self._normalizer(part, value_findings=False)
                    for ev in engine.events():
                        if ev[0] == _xml.END and ev[1].tag == "journal":
                            jr = norm.journal(ev[1], len(m.journals))
                            m.journals.setdefault(jr.id, jr)
            m.journals_complete = True
        return MappingProxyType(m.journals)

    @property
    def transaction_totals(self) -> TransactionTotals | None:
        """Control totals declared for the transactions (summed over a multi-file set)."""
        if self._adf is not None:
            return self._adf.totals  # type: ignore[no-any-return]
        totals = [
            t
            for p in self._parts
            if p.transactions_frame is not None
            and (
                t := self._normalizer(p, value_findings=False).totals(
                    RawRecord("transactions", p.transactions_frame, None, 0)
                )
            )
            is not None
        ]
        if not totals:
            return None
        if len(totals) == 1:
            return totals[0]

        def total(values: list[Any]) -> Any:
            return None if any(v is None for v in values) else exact_sum(values)

        return TransactionTotals(
            lines_count=total([t.lines_count for t in totals]),
            total_debit=total([t.total_debit for t in totals]),
            total_credit=total([t.total_credit for t in totals]),
        )

    # -------------------------------------------------------------- streaming
    def transactions(self) -> Iterator[Transaction]:
        """Stream all transactions in file order (each call re-reads the source)."""
        if self._adf is not None:
            yield from self._adf.transactions()
            return
        tx_offset = 0
        line_offset = 0
        for part in self._parts:
            first = not part.complete  # report value/quality findings once, not on every pass
            norm = self._normalizer(
                part,
                value_findings=self._value_findings and first,
                quality_findings=first,
            )
            norm.tx_seq = tx_offset
            norm.line_seq = line_offset
            yield from self._part_transactions(part, norm)
            tx_offset = norm.tx_seq
            line_offset = norm.line_seq

    def _part_transactions(
        self, part: _Part, norm: Normalizer, *, raw: bool = False
    ) -> Iterator[Any]:
        observer = self._observer
        if part.paused is not None:
            events: Iterator[tuple[Any, ...]] = part.paused
            pending = part.pending
            part.paused = None
            part.pending = None
            first_pass = True
        else:
            engine = self._engine(part)
            events = engine.events()
            pending = None
            first_pass = False
            # without a paused first pass, events of master data must be skipped
        m = self._master
        family = part.info.family or Family.XAF
        journal_frame: Any = None
        journal_id: str | None = None
        in_transactions = first_pass
        stream: Iterable[tuple[Any, ...]] = events
        if pending is not None:
            stream = _prepend(pending, events)
        self._findings.file = part.index
        for ev in stream:
            if first_pass and observer is not None and ev is not pending:
                observer(part.index, ev)
            kind = ev[0]
            if kind == _xml.RECORD:
                rec: RawRecord = ev[1]
                if rec.tag == "transaction":
                    in_transactions = True
                    parents = ev[2]
                    jf = parents[-1] if parents else None
                    if jf is not None and jf is not journal_frame:
                        journal_frame = jf
                        jfields = jf[_xml.FIELDS] or {}
                        journal_id = journal_id_from(jfields, family)
                        if journal_id is not None and journal_id not in m.journals:
                            jrec = RawRecord("journal", jfields, None, jf[_xml.LINE])
                            m.journals[journal_id] = norm.journal(jrec, len(m.journals))
                    yield (journal_id, rec) if raw else norm.transaction(rec, journal_id)
                elif in_transactions and first_pass and rec.tag in _MASTER_TAGS:
                    self._findings.add(
                        "XAF3030",
                        f"<{rec.tag}> appears after the transactions",
                        line=rec.line,
                    )
                    self._master_record(
                        rec, ev[2], norm, part.index == 0, {"account": 0, "relation": 0}
                    )
            elif kind == _xml.END:
                tag = ev[1].tag
                if tag == "journal":
                    jr = norm.journal(ev[1], len(m.journals))
                    m.journals.setdefault(jr.id, jr)
                elif tag == "transactions":
                    part.totals_fields = dict(ev[1].fields)
                elif first_pass and tag in ("company", "openingBalance", "taxonomies"):
                    if tag == "company" and part.index == 0:
                        m.company_raw = ev[1]
        if first_pass:
            m.journals_complete = True
        if not raw:  # a raw pass produces no value/quality findings
            part.complete = True

    @property
    def raw(self) -> RawView:
        """The raw records: exact text, no normalization (the fastest way to stream)."""
        return RawView(self)

    def lines(self) -> Iterator[Line]:
        """Stream all transaction lines (each carries its journal/transaction context)."""
        for tx in self.transactions():
            yield from tx.lines

    def opening_balance(
        self, strategy: Literal["auto", "element", "transactions"] = "auto"
    ) -> OpeningBalance:
        """Return the opening balance, wherever the file stores it.

        Args:
            strategy: ``"element"`` uses ``openingBalance``; ``"transactions"`` collects the
                lines of transactions in period 0 or in journals of type O (scans the file);
                ``"auto"`` (default) uses the element when present, else the transactions.
        """
        m = self._master
        if strategy in ("auto", "element") and (m.ob_lines or m.ob_frame is not None):
            norm = self._normalizer(self._parts[0], value_findings=False) if self._parts else None
            ob_rec = RawRecord("openingBalance", m.ob_frame or {}, None, 0)
            declared = norm.totals(ob_rec) if norm else None
            date = None
            if norm is not None and m.ob_frame and "opBalDate" in m.ob_frame:
                date = norm.date(ob_rec, "opBalDate", "/auditfile/company/openingBalance")
            desc = (m.ob_frame or {}).get("opBalDesc")
            return OpeningBalance(
                source="element",
                lines=tuple(m.ob_lines),
                date=date,
                description=desc.strip() if desc else None,
                declared=declared,
            )
        if strategy == "element":
            return OpeningBalance(source="none", lines=())
        lines = [ln for tx in self.transactions() if self._is_ob_tx(tx) for ln in tx.lines]
        if not lines:
            return OpeningBalance(source="none", lines=())
        return OpeningBalance(source="transactions", lines=tuple(lines))

    def _is_ob_tx(self, tx: Transaction) -> bool:
        if tx.period_key in _OB_PERIOD_KEYS:
            return True
        jr = self._master.journals.get(tx.journal_id or "")
        return jr is not None and jr.journal_type.kind is JournalKind.OPENING

    @property
    def tables(self) -> Tables:
        """Normalized tables for export (see :mod:`pyxaf.tables`)."""
        from .tables import Tables  # noqa: PLC0415

        return Tables(self)

    def to_polars(self, tables: Iterable[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        """Return polars DataFrames by table name (needs ``pyxaf[polars]``)."""
        return self.tables.to_polars(tables, **kwargs)

    def to_pandas(self, tables: Iterable[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        """Return pandas DataFrames by table name (needs ``pyxaf[pandas]``)."""
        return self.tables.to_pandas(tables, **kwargs)

    def export(self, directory: str | os.PathLike[str], **kwargs: Any) -> list[str]:
        """Write the normalized tables to ``directory`` (see :meth:`pyxaf.tables.Tables.export`)."""
        return self.tables.export(directory, **kwargs)

    # ---------------------------------------------------------- context mgmt
    def close(self) -> None:
        """Release paused parsers (sources are opened per pass and closed after each)."""
        spool = getattr(self, "_spool", None)
        if spool is not None:
            spool.close()
        for part in self._parts:
            if part.paused is not None:
                close = getattr(part.paused, "close", None)
                if close is not None:
                    close()
                part.paused = None

    def __enter__(self) -> AuditFile:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        name = self._names[0] if len(self._names) == 1 else f"{len(self._names)} files"
        return f"<AuditFile {name!r} version={self.version} company={self.company.name!r}>"


class RawView:
    """Raw records of an :class:`AuditFile`, exactly as written."""

    def __init__(self, af: AuditFile) -> None:
        self._af = af

    @property
    def header(self) -> RawRecord | None:
        """The ``header`` element (ADF: the header line)."""
        return self._af.header.raw

    @property
    def company(self) -> RawRecord | None:
        """The ``company`` element without its sections (CLAIR2/ADF: the header)."""
        return self._af.company.raw

    def transactions(self) -> Iterator[tuple[str | None, RawRecord]]:
        """Stream ``(journal_id, transaction_record)`` pairs (ADF: one record per line)."""
        af = self._af
        if af._adf is not None:
            yield from af._adf.raw_lines()
            return
        for part in af._parts:
            yield from af._part_transactions(part, af._normalizer(part), raw=True)


def _prepend(first: tuple[Any, ...], rest: Iterator[tuple[Any, ...]]) -> Iterator[tuple[Any, ...]]:
    yield first
    yield from rest


def _fold(name: str) -> str:
    return " ".join(name.casefold().split())


def _replace_rgs(acc: LedgerAccount, ref: RgsRef) -> LedgerAccount:
    from dataclasses import replace  # noqa: PLC0415

    return replace(acc, rgs=ref)


def open(
    source: SourceLike | Sequence[SourceLike],
    *,
    encoding: str | None = None,
    repair: Iterable[str] = (),
    negative_amounts: NegativePolicy = "flip",
    max_findings_per_code: int | None = 100,
    max_findings: int | None = 10_000,
    severity_overrides: Mapping[str, Severity | None] | None = None,
    max_depth: int = _xml.DEFAULT_MAX_DEPTH,
    max_text_size: int = _xml.DEFAULT_MAX_TEXT,
    max_decompressed_size: int | None = None,
) -> AuditFile:
    """Open an auditfile of any iteration (ADF, CLAIR2, XAF 3.0–4.0).

    Args:
        source: Path, bytes or binary file object — or a sequence of them for a split auditfile
            (continuation files "Vervolgbestand x van y", or complete files per period).
            gzip and single-member zip archives are decompressed transparently.
        encoding: Override the declared encoding (e.g. ``"cp1252"`` for mis-declared files).
        repair: Opt-in fix-ups, each reported as a finding: ``"control-chars"`` (remove
            XML-illegal control characters), ``"latin1-as-cp1252"`` (decode declared ISO-8859-1
            as windows-1252), ``"bare-ampersand"`` (escape ``&`` that starts no entity).
        negative_amounts: ``"flip"`` (default): a negative amount counts on the opposite side
            (−100 C = 100 D); ``"abs"``: the sign is ignored.
        max_findings_per_code: Keep at most this many findings per code.
        max_findings: Keep at most this many findings in total.
        severity_overrides: Change the severity of codes, or silence them with ``None``.
        max_depth: Maximum XML nesting depth.
        max_text_size: Maximum text length of one element.
        max_decompressed_size: Maximum decompressed size of gzip/zip input.

    Returns:
        An :class:`AuditFile`; use it as a context manager.

    Raises:
        NotAnAuditfileError: The input is not an auditfile.
        XmlSyntaxError: The XML is not well-formed (raised when the damage is reached).
        ForbiddenConstructError: The XML contains a DOCTYPE/ENTITY declaration.
    """
    return AuditFile(
        source,
        encoding=encoding,
        repair=repair,
        negative_amounts=negative_amounts,
        max_findings_per_code=max_findings_per_code,
        max_findings=max_findings,
        severity_overrides=severity_overrides,
        max_depth=max_depth,
        max_text_size=max_text_size,
        max_decompressed_size=max_decompressed_size,
    )
