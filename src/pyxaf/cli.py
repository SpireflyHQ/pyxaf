"""Command-line interface (``pyxaf[cli]``).

Exit codes: 0 ok · 1 warnings (only with ``--strict``) · 2 errors · 3 tool failure.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer

from . import __version__
from .detect import detect as _detect
from .errors import PyxafError
from .findings import CODES, Severity
from .reader import open as _open
from .validate import validate as _validate

app: typer.Typer = typer.Typer(
    name="pyxaf",
    help="Read and validate Dutch auditfiles (XAF 4.0, 3.x, CLAIR2, ADF).",
    no_args_is_help=True,
    add_completion=False,
)

FilesArg = Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, readable=True)]
FileArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]
EncodingOpt = Annotated[
    str | None, typer.Option("--encoding", help="Override the declared encoding.")
]
RepairOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--repair",
        help="Opt-in repair: control-chars, latin1-as-cp1252, bare-ampersand (repeatable).",
    ),
]


class OutputFormat(str, Enum):
    """Output format for reports."""

    text = "text"
    json = "json"


class ExportFormat(str, Enum):
    """Export file format."""

    csv = "csv"
    jsonl = "jsonl"
    parquet = "parquet"


class Rules(str, Enum):
    """Validation rule set."""

    spec = "spec"
    vts = "vts"


def _fail(message: str) -> typer.Exit:
    typer.echo(f"pyxaf: {message}", err=True)
    return typer.Exit(3)


def _fail_exc(path: Path, exc: Exception) -> typer.Exit:
    msg = str(exc)
    return _fail(msg if msg.startswith(str(path)) else f"{path}: {msg}")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"pyxaf {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    """Read and validate Dutch auditfiles."""


@app.command()
def detect(files: FilesArg, output: OutputFormat = OutputFormat.text) -> None:
    """Detect version, namespace and encoding of FILES (inspects the first 64 KiB)."""
    results: list[dict[str, Any]] = []
    for f in files:
        try:
            info = _detect(f)
        except PyxafError as exc:
            raise _fail_exc(f, exc) from None
        results.append(
            {
                "file": str(f),
                "family": info.family.value if info.family else None,
                "version": info.version.value if info.version else None,
                "namespace": info.namespace,
                "namespace_status": info.namespace_status.value,
                "encoding": info.encoding.effective,
                "bom": info.bom,
                "confidence": info.confidence,
                "continuation": info.continuation,
                "reasons": list(info.reasons),
            }
        )
    if output is OutputFormat.json:
        typer.echo(
            json.dumps({"schema_version": 1, "files": results}, indent=2, ensure_ascii=False)
        )
        return
    for r in results:
        typer.echo(
            f"{r['file']}: {r['version']} ({r['family']}, confidence {r['confidence']:.1f}, "
            f"{r['encoding']}{', BOM' if r['bom'] else ''})"
        )
        for reason in r["reasons"]:
            typer.echo(f"  - {reason}")


@app.command()
def info(
    file: FileArg,
    output: OutputFormat = OutputFormat.text,
    encoding: EncodingOpt = None,
    repair: RepairOpt = None,
) -> None:
    """Summarise FILE: version, company, counts, totals and periods (streams the file once)."""
    try:
        with _open(file, encoding=encoding, repair=repair or ()) as af:
            n_tx = n_lines = 0
            debit = credit = Decimal(0)
            for tx in af.transactions():
                n_tx += 1
                for ln in tx.lines:
                    n_lines += 1
                    debit += ln.debit or 0
                    credit += ln.credit or 0
            ob = af.opening_balance()
            h = af.header
            data: dict[str, Any] = {
                "schema_version": 1,
                "file": str(file),
                "version": af.version.value,
                "encoding": af.format.encoding.effective,
                "company": af.company.name,
                "fiscal_year": h.fiscal_year,
                "start_date": str(h.start_date) if h.start_date else None,
                "end_date": str(h.end_date) if h.end_date else None,
                "currency": h.currency,
                "software": " ".join(x for x in (h.software_name, h.software_version) if x),
                "accounts": len(af.accounts),
                "relations": len(af.relations),
                "vat_codes": len(af.vat_codes),
                "periods": len(af.periods),
                "journals": len(af.journals),
                "transactions": n_tx,
                "lines": n_lines,
                "total_debit": str(debit),
                "total_credit": str(credit),
                "opening_balance": {
                    "source": ob.source,
                    "lines": len(ob.lines),
                    "debit": str(ob.total_debit),
                    "credit": str(ob.total_credit),
                },
                "findings": len(af.findings),
            }
    except PyxafError as exc:
        raise _fail_exc(file, exc) from None
    if output is OutputFormat.json:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return
    width = max(len(k) for k in data)
    for k, v in data.items():
        shown = ", ".join(f"{a}={b}" for a, b in v.items()) if isinstance(v, dict) else v
        typer.echo(f"{k.replace('_', ' '):<{width}}  {shown}")


@app.command()
def validate(
    files: FilesArg,
    xsd: Annotated[bool, typer.Option("--xsd", help="Also validate against the XSD.")] = False,
    rules: Rules = Rules.spec,
    output: OutputFormat = OutputFormat.text,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit with 1 when there are warnings.")
    ] = False,
    max_findings: Annotated[
        int, typer.Option("--max-findings", help="Keep at most N findings per code.")
    ] = 100,
    encoding: EncodingOpt = None,
    repair: RepairOpt = None,
    as_set: Annotated[
        bool,
        typer.Option("--set", help="Treat FILES as one split auditfile (in the given order)."),
    ] = False,
) -> None:
    """Validate FILES (each separately, or as one split auditfile with --set)."""
    groups: list[Any] = [files] if as_set else [[f] for f in files]
    worst = 0
    reports = []
    for group in groups:
        src = group if len(group) > 1 else group[0]
        try:
            report = _validate(
                src,
                xsd=xsd,
                rules=rules.value,
                encoding=encoding,
                repair=repair or (),
                max_findings_per_code=max_findings,
            )
        except PyxafError as exc:
            raise _fail_exc(group[0], exc) from None
        reports.append((group, report))
        sev = report.max_severity
        if sev is Severity.ERROR:
            worst = max(worst, 2)
        elif sev is Severity.WARNING and strict:
            worst = max(worst, 1)
    if output is OutputFormat.json:
        payload = [{"files": [str(f) for f in g], **r.to_dict()} for g, r in reports]
        typer.echo(
            json.dumps(payload if len(payload) > 1 else payload[0], indent=2, ensure_ascii=False)
        )
    else:
        for group, report in reports:
            typer.echo(f"== {', '.join(str(f) for f in group)}")
            typer.echo(str(report))
    raise typer.Exit(worst)


@app.command()
def export(
    file: FileArg,
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")],
    fmt: Annotated[ExportFormat, typer.Option("--format", "-f")] = ExportFormat.csv,
    tables: Annotated[
        list[str] | None, typer.Option("--table", "-t", help="Table to export (repeatable).")
    ] = None,
    encoding: EncodingOpt = None,
    repair: RepairOpt = None,
) -> None:
    """Export the normalized tables of FILE to a directory."""
    try:
        with _open(file, encoding=encoding, repair=repair or ()) as af:
            paths = af.export(out, format=fmt.value, tables=tables)
    except (PyxafError, KeyError, ImportError) as exc:
        raise _fail_exc(file, exc) from None
    for p in paths:
        typer.echo(p)


@app.command()
def codes(output: OutputFormat = OutputFormat.text) -> None:
    """List all finding codes with their default severity."""
    if output is OutputFormat.json:
        typer.echo(
            json.dumps(
                [
                    {
                        "code": c.code,
                        "severity": c.severity.name,
                        "title": c.title,
                        "rule_ref": c.rule_ref,
                    }
                    for c in CODES.values()
                ],
                indent=2,
            )
        )
        return
    for c in CODES.values():
        ref = f" {c.rule_ref}" if c.rule_ref else ""
        typer.echo(f"{c.code}  {c.severity.name:<7}  {c.title}{ref}")


def run() -> None:  # pragma: no cover - console entry
    """Entry point used by ``python -m pyxaf``."""
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
