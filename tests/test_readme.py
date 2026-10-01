"""The README's table of contents matches its headings, and every in-page link resolves."""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
TOC_HEADING = "Table of contents"


def _headings(text: str) -> list[tuple[int, str]]:
    """(level, title) of every heading outside fenced code blocks."""
    found: list[tuple[int, str]] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and (m := re.match(r"(#{1,6}) (.+)", line)):
            found.append((len(m[1]), m[2].strip()))
    return found


def _label(title: str) -> str:
    """The heading text without its leading emoji (the TOC lists plain titles)."""
    return re.sub(r"^[^\w]+ ", "", title)


def _slug(title: str) -> str:
    """GitHub's heading anchor: lower case, punctuation and emoji removed, spaces to hyphens.

    A leading emoji leaves a leading hyphen: ``## 🚀 Quickstart`` becomes ``#-quickstart``.
    """
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def _toc(text: str) -> list[tuple[int, str, str]]:
    """(level, label, anchor) of the entries in the table of contents."""
    start = next(
        m.end()
        for m in re.finditer(r"^## .+\n", text, re.MULTILINE)
        if _label(m[0][3:-1]) == TOC_HEADING
    )
    section = text[start:].split("\n## ", 1)[0]
    return [
        (2 + len(m[1]) // 2, m[2], m[3])
        for m in re.finditer(r"^( *)- \[(.+)\]\(#(.+)\)$", section, re.MULTILINE)
    ]


def test_toc_lists_every_section_in_order() -> None:
    text = README.read_text(encoding="utf-8")
    expected = [
        (level, _label(title), _slug(title))
        for level, title in _headings(text)
        if level in (2, 3) and _label(title) != TOC_HEADING
    ]
    assert _toc(text) == expected


def test_in_page_links_resolve() -> None:
    text = README.read_text(encoding="utf-8")
    anchors = {_slug(title) for _, title in _headings(text)}
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, f"README link #{target} has no matching heading"
