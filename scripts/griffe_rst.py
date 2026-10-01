"""Griffe extension for the docs build: render pyxaf's reST-flavoured docstrings as Markdown.

pyxaf's docstrings are Google style but use a few Sphinx conventions that Markdown does not know:
inline roles (:class:`~pyxaf.raw.RawRecord`, :func:`pyxaf.open`) and simple reST tables
(``====  ====`` borders). mkdocstrings renders docstrings as Markdown, so this extension rewrites
them at load time — roles become code spans, simple tables become Markdown tables. The source is
not changed. Loaded by ``zensical.toml`` (``handlers.python.options.extensions``).
"""

from __future__ import annotations

import re
from typing import Any

import griffe

_ROLE = re.compile(r":(?:py:)?(class|func|meth|attr|data|mod|exc|obj):`(~?)([^`]+)`")
_BORDER = re.compile(r"^(\s*)(=+)((?: +=+)+)\s*$")


def _role(m: re.Match[str]) -> str:
    kind, short, target = m.group(1), m.group(2), m.group(3).strip()
    if short:
        target = target.rsplit(".", 1)[-1]
    if kind in ("func", "meth") and not target.endswith(")"):
        target += "()"
    return f"`{target}`"


def _cells(line: str, starts: list[int]) -> list[str]:
    bounds = [*starts[1:], None]
    return [line[a:b].strip().replace("|", "\\|") for a, b in zip(starts, bounds, strict=True)]


def _tables(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _BORDER.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        border = lines[i]
        indent = len(m.group(1))
        starts = [s.start() for s in re.finditer(r"=+", border)]
        rows: list[list[str]] = []
        borders = 1
        j = i + 1
        while j < len(lines):
            if _BORDER.match(lines[j]):
                borders += 1
                j += 1
                # a simple table has two borders (no header) or three (header row)
                if borders == 3 or (j < len(lines) and not lines[j].strip()):
                    break
                continue
            line = lines[j]
            if rows and line[: starts[1]].strip() == "" and line.strip():
                # continuation of the last column
                rows[-1][-1] += " " + line.strip()
            elif line.strip():
                rows.append(_cells(line, starts))
            j += 1
        if not rows:
            out.append(border)
            i += 1
            continue
        pad = " " * indent
        if borders == 3:
            header, body = rows[0], rows[1:]
        else:
            header, body = [""] * len(starts), rows
        out.append(pad + "| " + " | ".join(header) + " |")
        out.append(pad + "|" + "|".join(["---"] * len(starts)) + "|")
        out.extend(pad + "| " + " | ".join(r) + " |" for r in body)
        i = j
    return "\n".join(out)


class RstToMarkdown(griffe.Extension):
    """Rewrite reST roles and simple tables in every docstring."""

    def on_instance(self, *, obj: griffe.Object, **kwargs: Any) -> None:
        del kwargs
        doc = obj.docstring
        if doc is None:
            return
        value = _ROLE.sub(_role, doc.value)
        if "==" in value:
            value = _tables(value)
        doc.value = value
