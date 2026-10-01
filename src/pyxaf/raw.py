"""Lossless raw records: exactly what the file contains, per element.

The raw layer keeps every element — including unknown and vendor-specific ones — with its exact
text. The normalized model (:mod:`pyxaf.models`) is built from it, and every normalized object
keeps a reference to its :class:`RawRecord` in ``.raw``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import ClassVar

__all__ = ["RawRecord"]


class RawRecord:
    """One XML element with its leaf children as ``fields`` and complex children as ``children``.

    Attributes:
        tag: Local element name (namespace prefix removed).
        fields: Text of leaf child elements by local name, exactly as written (entities resolved,
            whitespace preserved; empty elements give ``""``). If a leaf element occurs more than
            once, the first occurrence is in ``fields`` and every occurrence is also available as a
            text-only child record in ``children``.
        children: Complex child elements (and repeated leaves) by local name, in document order.
        line: 1-based line of the start tag.
        text: Text content for a leaf element stored as a child, else ``None``.
    """

    __slots__ = ("children", "fields", "line", "tag", "text")

    def __init__(
        self,
        tag: str,
        fields: dict[str, str],
        children: dict[str, list[RawRecord]] | None,
        line: int,
        text: str | None = None,
    ) -> None:
        self.tag: str = tag
        self.fields: Mapping[str, str] = fields
        self.children: Mapping[str, Sequence[RawRecord]] = children or {}
        self.line: int = line
        self.text: str | None = text

    def get(self, name: str, default: str | None = None) -> str | None:
        """Return the text of leaf child ``name``."""
        return self.fields.get(name, default)

    def child(self, name: str) -> RawRecord | None:
        """Return the first complex child named ``name``."""
        items = self.children.get(name)
        return items[0] if items else None

    def all(self, name: str) -> Sequence[RawRecord]:
        """Return all complex children named ``name``."""
        return self.children.get(name, ())

    def iter_children(self) -> Iterator[RawRecord]:
        """Iterate over all complex children."""
        for items in self.children.values():
            yield from items

    def to_dict(self) -> dict[str, object]:
        """Return a plain ``dict`` (children become lists of dicts)."""
        out: dict[str, object] = dict(self.fields)
        for name, items in self.children.items():
            if all(i.text is not None for i in items):
                out[name] = [i.text for i in items]
            else:
                out[name] = [i.to_dict() for i in items]
        return out

    def __repr__(self) -> str:
        return f"RawRecord({self.tag!r}, line={self.line}, fields={dict(self.fields)!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RawRecord):
            return NotImplemented
        return (
            self.tag == other.tag
            and dict(self.fields) == dict(other.fields)
            and {k: list(v) for k, v in self.children.items()}
            == {k: list(v) for k, v in other.children.items()}
            and self.text == other.text
        )

    __hash__: ClassVar[None]  # type: ignore[assignment]  # mutable mappings: unhashable
