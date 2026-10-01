"""Tests for pyxaf.rgs and its stdlib xlsx reader (synthetic workbooks; invented codes)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

import pyxaf
from pyxaf.errors import ForbiddenConstructError, LimitExceededError
from pyxaf.findings import FindingCollector
from pyxaf.rgs import RgsSchema, load_excel, parse_ref, parse_version, validate_refs
from pyxaf.rgs._xlsx import XlsxError, XlsxWorkbook, column_index, iter_rows, sheet_names

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


# ----------------------------------------------------------------------------- xlsx builder
@dataclass(frozen=True)
class Inline:
    text: str


@dataclass(frozen=True)
class Rich:
    runs: tuple[str, ...]
    phonetic: str = "PHONETIC"


@dataclass(frozen=True)
class At:
    """A cell at an explicit column, leaving a gap before it."""

    col: str
    value: object


Cell = object  # str | int | float | bool | Inline | Rich | At | None
Rows = list[list[Cell]]


def _col_letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def build_xlsx(
    path: Path,
    sheets: dict[str, Rows],
    *,
    raw_parts: dict[str, str] | None = None,
    omit_refs: bool = False,
) -> Path:
    """Write a minimal xlsx. An empty row list item means "row absent from the XML"."""
    shared: list[str] = []  # serialised <si> bodies
    index: dict[str, int] = {}

    def sst(body: str) -> int:
        if body not in index:
            index[body] = len(shared)
            shared.append(body)
        return index[body]

    def cell_xml(ref: str, value: object) -> str:
        r = "" if omit_refs else f' r="{ref}"'
        if isinstance(value, bool):
            return f'<c{r} t="b"><v>{int(value)}</v></c>'
        if isinstance(value, (int, float)):
            return f"<c{r}><v>{value}</v></c>"
        if isinstance(value, Inline):
            return f'<c{r} t="inlineStr"><is><t>{escape(value.text)}</t></is></c>'
        if isinstance(value, Rich):
            runs = "".join(f"<r><rPr><b/></rPr><t>{escape(t)}</t></r>" for t in value.runs)
            body = f"{runs}<rPh sb='0' eb='1'><t>{value.phonetic}</t></rPh>"
            return f'<c{r} t="s"><v>{sst(body)}</v></c>'
        assert isinstance(value, str)
        body = '<t xml:space="preserve">' + escape(value) + "</t>"
        return f'<c{r} t="s"><v>{sst(body)}</v></c>'

    sheet_xml: list[str] = []
    for rows in sheets.values():
        out = []
        for rn, row in enumerate(rows, 1):
            if not row:
                continue
            cells = []
            col = 0
            for item in row:
                value = item
                if isinstance(item, At):
                    col = column_index(item.col)
                    value = item.value
                if value is not None:
                    cells.append(cell_xml(f"{_col_letters(col)}{rn}", value))
                col += 1
            out.append(f'<row r="{rn}">{"".join(cells)}</row>')
        sheet_xml.append(
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{MAIN_NS}"><dimension ref="A1:Z{len(rows)}"/>'
            f"<sheetData>{''.join(out)}</sheetData></worksheet>"
        )

    names = list(sheets)
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
        "<sheets>"
        + "".join(
            f'<sheet name="{escape(n)}" sheetId="{i + 10}" r:id="rId{i + 1}"/>'
            for i, n in enumerate(names)
        )
        + "</sheets></workbook>"
    )
    rels = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG_REL_NS}">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" Type="{REL_NS}/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(names))
        )
        + f'<Relationship Id="rId99" Type="{REL_NS}/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    sst_xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{MAIN_NS}" count="{len(shared)}" '
        f'uniqueCount="{len(shared)}">' + "".join(f"<si>{b}</si>" for b in shared) + "</sst>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/></Types>'
    )
    parts = {
        "[Content_Types].xml": content_types,
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": rels,
        "xl/sharedStrings.xml": sst_xml,
    }
    for i, xml in enumerate(sheet_xml):
        parts[f"xl/worksheets/sheet{i + 1}.xml"] = xml
    parts.update(raw_parts or {})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return path


# ----------------------------------------------------------------------------- RGS workbook
HEADER: list[Cell] = [
    "REFERENTIECODE",
    "Referentie-Omslagcode",
    "sortering",
    "Referentienummer",
    "Omschrijving  (verkort)",
    "Omschrijving",
    "D / C",
    "Nivo",
    "Basis",
    "ZZP",
    "BV",
    "Agro",
]
GROUPS: list[Cell] = [
    "RGS9.9",
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    "Filter - te kiezen",
    None,
    "Filters - te vervallen c.q. te elimineren",
]


def code_row(code: str, level: int, dc: str | None, *filters: Cell, **kw: Cell) -> list[Cell]:
    return [
        code,
        kw.get("omslag"),
        kw.get("sort", "X"),
        kw.get("nr", "01"),
        Inline(f"short {code}"),
        f"long {code} & more",
        dc,
        str(level),
        *filters,
    ]


MAIN_ROWS: Rows = [
    GROUPS,
    HEADER,
    code_row("B", 1, None, "1", "1"),
    code_row("BIva", 2, "D", "1", "1"),
    code_row("BIvaKou", 3, "D", "1", None, "1", nr=101015),
    code_row("BIvaKouVvp", 4, "D", "1", " ", "1", omslag="WOmzNopNod"),
    code_row("BIvaKouVvpBeg", 5, "D", "1"),
    code_row("BIvaKouVvpInv", 5, "D", None, "1", None, "1"),
    code_row("W", 1, None, "1"),
    code_row("WOmz", 2, "C", "1"),
    code_row("WOmzNop", 3, "C", "1"),
    code_row("WOmzNopNod", 4, "C", "1"),
    code_row("WOmzNopNod", 3, "C", "1"),  # duplicate (the official 3.8 file has one too)
    [],  # empty row
    [At("I", 12), 4],  # a totals row without code
]

COMPARE_ROWS: Rows = [
    ["RGS9.7", "RGS9.8", "RGS9.9"],
    ["Referentiecode", "Referentie code", "referentiecode", "Omschrijving"],
    ["B", "B", "B"],
    ["BIva", "BIva", "BIva"],
    ["BIva", "BIvaKou", "BIvaKou"],  # old code still exists → not a rename
    ["BMvaOld", "BMvaOld", "BIvaKouVvp"],
    ["WOmzNopOud", " ", "WOmzNopNod"],
]


@pytest.fixture
def rgs_xlsx(tmp_path: Path) -> Path:
    return build_xlsx(
        tmp_path / "rgs-test.xlsx",
        {
            "Recap": [[None, "Release notes RGS 9.9"]],
            "Small-RGS9.9": [GROUPS, HEADER, code_row("B", 1, None)],
            "Totaal-RGS9.9-def": MAIN_ROWS,
            "RGS9.9-versus-RGS9.8": COMPARE_ROWS,
        },
    )


@pytest.fixture
def schema(rgs_xlsx: Path) -> RgsSchema:
    return load_excel(rgs_xlsx)


# ----------------------------------------------------------------------------- xlsx reader
def test_column_index() -> None:
    assert column_index("A1") == 0
    assert column_index("z") == 25
    assert column_index("AA7") == 26
    assert column_index("AB12") == 27
    assert column_index("XFD1048576") == 16_383
    with pytest.raises(LimitExceededError):
        column_index("XFE1")
    with pytest.raises(LimitExceededError):
        column_index("ABCDEFG1")
    with pytest.raises(XlsxError):
        column_index("12")


def test_iter_rows_value_kinds_and_gaps(tmp_path: Path) -> None:
    path = build_xlsx(
        tmp_path / "kinds.xlsx",
        {
            "First": [["x"]],
            "Data": [
                ["shared", Inline("inline"), 42, 1.5, True, False],
                [],
                [None, None, "c"],
                [At("D", "d"), At("AB", Rich(("Immat", "eriële")))],
                ["dup", "dup"],
            ],
        },
    )
    assert sheet_names(path) == ["First", "Data"]
    rows = list(iter_rows(path, "Data"))
    assert rows[0] == ["shared", "inline", "42", "1.5", "TRUE", "FALSE"]
    assert rows[1] == []
    assert rows[2] == [None, None, "c"]
    assert rows[3][3] == "d"
    assert rows[3][27] == "Immateriële"  # rich-text runs joined, phonetic run ignored
    assert rows[3][:3] == [None, None, None]
    assert rows[4] == ["dup", "dup"]
    with pytest.raises(KeyError):
        list(iter_rows(path, "Nope"))


def test_cells_without_reference(tmp_path: Path) -> None:
    path = build_xlsx(tmp_path / "noref.xlsx", {"S": [["a", "b", 3]]}, omit_refs=True)
    assert list(iter_rows(path, "S")) == [["a", "b", "3"]]


def test_workbook_reuses_shared_strings_and_dimension(tmp_path: Path) -> None:
    path = build_xlsx(tmp_path / "d.xlsx", {"A": [["x"], ["y"]], "B": [["y"]]})
    with XlsxWorkbook(path) as wb:
        assert wb.dimension("A") == "A1:Z2"
        assert list(wb.iter_rows("B")) == [["y"]]
        assert list(wb.iter_rows("A")) == [["x"], ["y"]]


def test_doctype_refused_in_sheet(tmp_path: Path) -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE worksheet [<!ENTITY lol "lol">]>'
        f'<worksheet xmlns="{MAIN_NS}"><sheetData><row r="1"><c r="A1" t="inlineStr">'
        "<is><t>&lol;</t></is></c></row></sheetData></worksheet>"
    )
    path = build_xlsx(
        tmp_path / "evil.xlsx",
        {"S": [["x"]]},
        raw_parts={"xl/worksheets/sheet1.xml": evil},
    )
    with pytest.raises(ForbiddenConstructError):
        list(iter_rows(path, "S"))


def test_doctype_refused_in_workbook(tmp_path: Path) -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE workbook SYSTEM "http://example.invalid/x.dtd">'
        f'<workbook xmlns="{MAIN_NS}"><sheets/></workbook>'
    )
    path = build_xlsx(tmp_path / "evil.xlsx", {"S": [["x"]]}, raw_parts={"xl/workbook.xml": evil})
    with pytest.raises(ForbiddenConstructError):
        sheet_names(path)


def test_zip_bomb_limit(tmp_path: Path) -> None:
    rows: Rows = [[Inline("0" * 1000)] for _ in range(200)]  # ~250 KB, compresses to little
    path = build_xlsx(tmp_path / "bomb.xlsx", {"S": rows})
    assert len(list(iter_rows(path, "S"))) == 200
    with pytest.raises(LimitExceededError):
        list(iter_rows(path, "S", max_member_size=50_000))
    with pytest.raises(LimitExceededError):
        load_excel(path, max_member_size=50_000)


def test_not_a_workbook(tmp_path: Path) -> None:
    junk = tmp_path / "junk.xlsx"
    junk.write_bytes(b"not a zip")
    with pytest.raises(XlsxError):
        sheet_names(junk)
    empty = tmp_path / "empty.xlsx"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(XlsxError):
        sheet_names(empty)


def test_rows_stream_lazily(tmp_path: Path) -> None:
    path = build_xlsx(tmp_path / "s.xlsx", {"S": [[str(i)] for i in range(5000)]})
    it = iter_rows(path, "S")
    assert next(it) == ["0"]
    it.close()  # abandoning the generator closes the workbook


# ----------------------------------------------------------------------------- loading
def test_load_detects_main_sheet_version_and_fields(schema: RgsSchema) -> None:
    assert schema.version == "9.9"
    assert len(schema) == 10
    assert "BIvaKouVvp" in schema
    assert "BIvaKouVvpXyz" not in schema
    assert schema.duplicate_codes == {"WOmzNopNod"}
    nod = schema.codes["WOmzNopNod"]
    assert nod.level == 4  # first occurrence wins
    vvp = schema.codes["BIvaKouVvp"]
    assert vvp.short_description == "short BIvaKouVvp"
    assert vvp.description == "long BIvaKouVvp & more"
    assert vvp.debit_credit == "D"
    assert vvp.sort_key == "X"
    assert vvp.reference_number == "01"
    assert vvp.opposite_code == "WOmzNopNod"
    assert vvp.entities == {"Basis"}  # " " is not set
    assert vvp.elimination_filters == {"BV"}
    inv = schema.codes["BIvaKouVvpInv"]
    assert inv.entities == {"ZZP"}
    assert inv.elimination_filters == {"Agro"}
    assert schema.codes["BIvaKou"].reference_number == "101015"  # kept as stored
    assert schema.codes["B"].debit_credit is None
    assert [c.code for c in schema][:3] == ["B", "BIva", "BIvaKou"]


def test_explicit_sheet_and_errors(rgs_xlsx: Path) -> None:
    small = load_excel(rgs_xlsx, "Small-RGS9.9")
    assert len(small) == 1
    assert small.version == "9.9"
    assert small.rename_map  # comparison sheet still auto-detected
    with pytest.raises(KeyError):
        load_excel(rgs_xlsx, "Missing")
    with pytest.raises(XlsxError):
        load_excel(rgs_xlsx, "Recap")
    no_renames = load_excel(rgs_xlsx, rename_sheet="Small-RGS9.9")
    assert dict(no_renames.rename_map) == {}


def test_load_without_any_rgs_sheet(tmp_path: Path) -> None:
    path = build_xlsx(tmp_path / "x.xlsx", {"S": [["a", "b"]]})
    with pytest.raises(XlsxError):
        load_excel(path)


def test_version_from_file_name(tmp_path: Path) -> None:
    rows: Rows = [HEADER, code_row("B", 1, None)]
    path = build_xlsx(tmp_path / "RGS 3.6 def.xlsx", {"Codes": rows})
    assert load_excel(path).version == "3.6"
    path2 = build_xlsx(tmp_path / "codes.xlsx", {"Codes": rows})
    assert load_excel(path2).version is None
    with path.open("rb") as fh:  # streams work too
        assert len(load_excel(fh)) == 1


def test_hierarchy(schema: RgsSchema) -> None:
    parent = schema.parent("BIvaKouVvpBeg")
    assert parent is not None
    assert parent.code == "BIvaKouVvp"
    assert schema.parent("B") is None
    unknown_parent = schema.parent("BIvaKouXyzAbc")  # nearest existing ancestor
    assert unknown_parent is not None
    assert unknown_parent.code == "BIvaKou"
    assert [c.code for c in schema.children("BIvaKouVvp")] == ["BIvaKouVvpBeg", "BIvaKouVvpInv"]
    assert [c.code for c in schema.children("W")] == ["WOmz"]
    assert schema.children("BIvaKouVvpBeg") == ()
    for c in schema:
        p = schema.parent(c.code)
        if p is not None:
            assert c.code.startswith(p.code)
            assert len(p.code) < len(c.code)


def test_rename_map_and_resolve(schema: RgsSchema) -> None:
    assert dict(schema.rename_map) == {"BMvaOld": "BIvaKouVvp", "WOmzNopOud": "WOmzNopNod"}
    resolved = schema.resolve("BMvaOld")
    assert resolved is not None
    assert resolved.code == "BIvaKouVvp"
    assert schema.resolve(" WOmz ") is schema.codes["WOmz"]
    assert schema.resolve("BNope") is None


# ----------------------------------------------------------------------------- checks
def _check(schema: RgsSchema, raw: str) -> list[tuple[str, str]]:
    fc = FindingCollector()
    schema.check_ref(parse_ref(raw), fc, line=7, account_id="1000")
    return [(f.code, f.message) for f in fc]


def test_check_ref(schema: RgsSchema) -> None:
    assert _check(schema, "BIvaKouVvp") == []
    assert _check(schema, "BIvaKouVvpBeg") == []
    assert _check(schema, "0000") == []  # placeholders are the validator's business
    assert _check(schema, "not a code") == []

    [(code, msg)] = _check(schema, "BIvaKouXyz")
    assert code == "XAF8003"
    assert "RGS 9.9" in msg
    assert "'BIvaKou'" in msg

    [(code, msg)] = _check(schema, "BMvaOld")
    assert code == "XAF8004"
    assert "'BIvaKouVvp'" in msg

    [(code, msg)] = _check(schema, "BIvaKou")
    assert code == "XAF8006"
    assert "level 3" in msg

    assert [c for c, _ in _check(schema, "W")] == ["XAF8006"]
    assert _check(schema, "BIvaKouVvp.01") == []  # extension is ignored


def test_check_ref_finding_details(schema: RgsSchema) -> None:
    fc = FindingCollector()
    schema.check_ref(parse_ref("BMvaOld"), fc, line=12, account_id="A")
    [f] = fc
    assert f.line == 12
    assert f.value == "BMvaOld"
    assert f.severity is pyxaf.Severity.WARNING


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("RGS 9.9", []),
        ("9.9", []),
        ("rgs9.9-def", []),
        ("9.9.0", []),
        ("9,9", []),
        ("RGS 9.8", ["XAF8005"]),
        ("RGS-1", ["XAF8005"]),
        ("unknown", ["XAF8005"]),
        ("", ["XAF8005"]),
    ],
)
def test_check_version(schema: RgsSchema, text: str, expected: list[str]) -> None:
    fc = FindingCollector()
    schema.check_version(text, fc)
    assert [f.code for f in fc] == expected
    assert all(f.severity is pyxaf.Severity.INFO for f in fc)


def test_check_version_without_schema_version() -> None:
    bare = RgsSchema([])
    fc = FindingCollector()
    bare.check_version("3.7", fc)
    assert list(fc) == []
    bare.check_version("whatever", fc)
    assert [f.code for f in fc] == ["XAF8005"]


def test_parse_version() -> None:
    assert parse_version("RGS3.8-beta") == "3.8"
    assert parse_version("Totaal-RGS3.10-def") == "3.10"
    assert parse_version("RGS-1") == "1"
    assert parse_version(None) is None
    assert parse_version("n.v.t.") is None


def test_parse_ref() -> None:
    ref = parse_ref(" BIvaKouVvp 01 ")
    assert ref.code == "BIvaKouVvp"
    assert ref.extension == "01"
    assert ref.source == "RGScode"
    assert parse_ref("0000", "taxonomy").placeholder


XAF40 = """<?xml version="1.0" encoding="UTF-8"?>
<auditfile xmlns="http://www.odb.belastingdienst.nl/xaf/4.0">
<header><fiscalYear>2024</fiscalYear><startDate>2024-01-01</startDate>
<endDate>2024-12-31</endDate><curCode>EUR</curCode><dateCreated>2025-01-01</dateCreated>
<softwareDesc>x</softwareDesc><softwareVersion>1</softwareVersion>
<RGSVersion>RGS 9.8</RGSVersion></header>
<company><companyName>A</companyName><taxRegistrationCountry>NL</taxRegistrationCountry>
<taxRegIdent>1</taxRegIdent><generalLedger>
{accounts}
</generalLedger></company></auditfile>"""


def test_validate_refs(schema: RgsSchema) -> None:
    accounts = "".join(
        f"<ledgerAccount><accID>{i}</accID><accDesc>a</accDesc><accTp>B</accTp>"
        f"<RGScode>{code}</RGScode></ledgerAccount>"
        for i, code in enumerate(["BIvaKouVvp", "0000", "???", "BIvaKouXyz", "BMvaOld", "WOmz"], 1)
    )
    with pyxaf.open(XAF40.format(accounts=accounts).encode()) as af:
        findings = validate_refs(af, schema)
    assert [f.code for f in findings] == [
        "XAF8001",
        "XAF8002",
        "XAF8003",
        "XAF8004",
        "XAF8006",
        "XAF8005",
    ]
    assert all(f.line is not None for f in findings[:-1])
