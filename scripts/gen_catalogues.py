"""Generate pyxaf's per-version field catalogues from the bundled XSDs.

Run from the repository root (needs the ``gen`` dependency group)::

    uv run --group gen python scripts/gen_catalogues.py

Inputs:
- ``src/pyxaf/schemas/*.xsd`` — official schemas (CLAIR2 2003, XAF 3.2, 3.2.1, 4.0).
- ``scripts/data/xpaths-3.0-3.1.json`` — XAF 3.0/3.1 leaf paths derived from the AnalyticsLibrary
  "XAF Mapping en Namen" table (Apache-2.0). No 3.0/3.1 XSD is publicly available; facets for paths
  shared with 3.2 are copied from the 3.2 schema, the others are typed as unrestricted strings.

Output: ``src/pyxaf/catalogue/_generated_<version>.py`` (committed; do not edit by hand).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import xmlschema
from xmlschema.validators import XsdElement, XsdGroup

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "src" / "pyxaf" / "schemas"
OUT = ROOT / "src" / "pyxaf" / "catalogue"
XSD_NS = "{http://www.w3.org/2001/XMLSchema}"

SOURCES = {
    "clair2": ("clair2-auditfile.xsd", "CLAIR2.00.00 (SRA 2003)"),
    "xaf32": ("XmlAuditfileFinancieel3.2.xsd", "XAF 3.2"),
    "xaf321": ("XmlAuditfileFinancieel3.2.1.xsd", "XAF 3.2.1"),
    "xaf40": ("XmlAuditfileFinancieel4.0.xsd", "XAF 4.0"),
}

# kind names used by pyxaf.catalogue
KINDS = {
    "string": "string",
    "normalizedString": "string",
    "token": "string",
    "date": "date",
    "dateTime": "datetime",
    "time": "time",
    "decimal": "decimal",
    "double": "decimal",
    "float": "decimal",
    "integer": "integer",
    "nonNegativeInteger": "integer",
    "positiveInteger": "integer",
    "int": "integer",
    "long": "integer",
    "short": "integer",
    "boolean": "boolean",
}


def primitive(t: Any) -> str:
    while t is not None:
        if t.name is not None and t.name.startswith(XSD_NS):
            return str(t.local_name)
        base = getattr(t, "base_type", None)
        if base is None:
            break
        t = base
    return "string"


def facets(t: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("max_length", "min_length", "length"):
        v = getattr(t, k, None)
        if v is not None:
            out[k] = int(v)
    enum = getattr(t, "enumeration", None)
    if enum:
        out["enum"] = tuple(str(e) for e in enum)
    # walk the derivation chain so inherited facets are kept
    chain = t
    while chain is not None and not (chain.name or "").startswith(XSD_NS):
        for f in getattr(chain, "facets", {}).values():
            n = type(f).__name__
            if n == "XsdTotalDigitsFacet":
                out.setdefault("total_digits", int(f.value))
            elif n == "XsdFractionDigitsFacet":
                out.setdefault("fraction_digits", int(f.value))
            elif n == "XsdMinInclusiveFacet":
                out.setdefault("min_inclusive", str(f.value))
            elif n == "XsdMaxInclusiveFacet":
                out.setdefault("max_inclusive", str(f.value))
            elif n == "XsdPatternFacets":
                out.setdefault("patterns", tuple(str(r) for r in f.regexps))
        chain = getattr(chain, "base_type", None)
    return out


def walk_group(group: XsdGroup, parent: str, rows: list[dict[str, Any]], choice: list[int]) -> None:
    for particle in group:
        if isinstance(particle, XsdGroup):
            if particle.model == "choice":
                choice[0] += 1
                walk_choice(particle, parent, rows, choice, choice[0])
            else:
                walk_group(particle, parent, rows, choice)
        elif isinstance(particle, XsdElement):
            walk_element(particle, parent, rows, choice, None)


def walk_choice(
    group: XsdGroup,
    parent: str,
    rows: list[dict[str, Any]],
    choice: list[int],
    cid: int,
    branch: int | None = None,
) -> None:
    """Elements of a choice get ``"<cid>.<branch>"``; a nested sequence is one branch."""
    for i, particle in enumerate(group):
        b = i if branch is None else branch
        if isinstance(particle, XsdElement):
            walk_element(particle, parent, rows, choice, f"{cid}.{b}")
        else:
            walk_choice(particle, parent, rows, choice, cid, b)


def walk_element(
    e: XsdElement, parent: str, rows: list[dict[str, Any]], choice: list[int], cid: str | None
) -> None:
    path = f"{parent}/{e.local_name}"
    t = e.type
    row: dict[str, Any] = {
        "path": path,
        "min": int(e.min_occurs),
        "max": None if e.max_occurs is None else int(e.max_occurs),
        "choice": cid,
    }
    if t.is_complex() and t.has_complex_content():
        row["kind"] = "complex"
        rows.append(row)
        walk_group(t.content, path, rows, choice)
        return
    st = t if t.is_simple() else t.content
    prim = primitive(st)
    row["kind"] = KINDS.get(prim, "string")
    row.update(facets(st))
    if prim in ("nonNegativeInteger", "positiveInteger"):
        row.setdefault("min_inclusive", "0" if prim == "nonNegativeInteger" else "1")
    if e.fixed is not None:
        row["fixed"] = str(e.fixed)
    rows.append(row)


def dump_xsd(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    schema = xmlschema.XMLSchema(str(path))
    rows: list[dict[str, Any]] = []
    for e in schema.elements.values():
        walk_element(e, "", rows, [0], None)
    return schema.target_namespace or None, rows


def derive(paths: list[str], base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a 3.0/3.1 catalogue from leaf paths, borrowing facets from 3.2 where paths match."""
    by_path = {r["path"]: r for r in base}
    wanted: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i in range(2, len(parts) + 1):
            wanted.add("/".join(parts[:i]))
    rows: list[dict[str, Any]] = []
    # keep 3.2 ordering for known paths; append unknown ones after their parent's known children
    known = [r for r in base if r["path"] in wanted]
    unknown = sorted(wanted - {r["path"] for r in known}, key=lambda p: (p.count("/"), p))
    rows = [dict(r) for r in known]
    for p in unknown:
        is_leaf = p in paths
        row: dict[str, Any] = {
            "path": p,
            "min": 0,
            "max": 1 if is_leaf else None,
            "choice": None,
            "kind": "string" if is_leaf else "complex",
        }
        parent = p.rsplit("/", 1)[0]
        # insert after the last row that lives under the parent
        idx = max(
            i
            for i, r in enumerate(rows)
            if r["path"] == parent or r["path"].startswith(parent + "/")
        )
        rows.insert(idx + 1, row)
    # complex elements that only exist as parents in the path list must be complex
    leaves = set(paths)
    for r in rows:
        if r["path"] not in leaves and r["kind"] != "complex":
            r.update(kind="complex")
        if (
            r["path"] in by_path
            and r["path"] not in leaves
            and by_path[r["path"]]["kind"] != "complex"
        ):
            r["kind"] = "complex"
    return rows


def write(
    name: str, title: str, namespace: str | None, rows: list[dict[str, Any]], derived: bool
) -> None:
    keys = (
        "path",
        "min",
        "max",
        "kind",
        "choice",
        "min_length",
        "max_length",
        "length",
        "enum",
        "patterns",
        "total_digits",
        "fraction_digits",
        "min_inclusive",
        "max_inclusive",
        "fixed",
    )
    tuples = [tuple(r.get(k) for k in keys) for r in rows]
    body = "(\n" + "".join(f"    {t!r},\n" for t in tuples) + ")"
    text = (
        f'"""Field catalogue for {title}.\n\n'
        'Generated by scripts/gen_catalogues.py; do not edit.\n"""\n\n'
        f"NAMESPACE = {namespace!r}\n"
        f"DERIVED = {derived!r}\n"
        f"COLUMNS = {keys!r}\n"
        f"FIELDS = {body}\n"
    )
    (OUT / f"_generated_{name}.py").write_text(text, encoding="utf-8")
    leaves = sum(1 for r in rows if r["kind"] != "complex")
    print(f"{name}: {len(rows)} elements, {leaves} leaves, ns={namespace}")


def main() -> None:
    dumped: dict[str, list[dict[str, Any]]] = {}
    for name, (xsd, title) in SOURCES.items():
        ns, rows = dump_xsd(SCHEMAS / xsd)
        dumped[name] = rows
        write(name, title, ns, rows, derived=False)
    xpaths = json.loads((ROOT / "scripts" / "data" / "xpaths-3.0-3.1.json").read_text())
    write("xaf30", "XAF 3.0 (derived)", None, derive(xpaths["3.0"], dumped["xaf32"]), True)
    write(
        "xaf31",
        "XAF 3.1 (derived)",
        "http://www.auditfiles.nl/XAF/3.1",
        derive(xpaths["3.1"], dumped["xaf32"]),
        True,
    )


if __name__ == "__main__":
    main()
