"""Per-version field catalogues: every element path with cardinality, type and facets.

Catalogues are generated from the official XSDs (CLAIR2, 3.2, 3.2.1, 4.0) by
``scripts/gen_catalogues.py``. XAF 3.0 and 3.1 have no public XSD; their catalogues are *derived*
(paths from the AnalyticsLibrary mapping, facets borrowed from 3.2) and flagged ``derived=True``.

They drive dependency-free structural validation, version detection and documentation.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from types import MappingProxyType

from ..formats import Version

__all__ = ["Catalogue", "FieldSpec", "get_catalogue"]

_MODULES = {
    Version.CLAIR2: "_generated_clair2",
    Version.XAF30: "_generated_xaf30",
    Version.XAF31: "_generated_xaf31",
    Version.XAF32: "_generated_xaf32",
    Version.XAF321: "_generated_xaf321",
    Version.XAF40: "_generated_xaf40",
}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Specification of one element path.

    Attributes:
        path: Absolute path of local names, e.g. ``/auditfile/header/fiscalYear``.
        name: Local element name.
        parent: Path of the parent element (``""`` for the root).
        order: Position among the parent's children in the schema sequence.
        min_occurs: Minimum occurrences within one parent.
        max_occurs: Maximum occurrences (``None`` = unbounded).
        kind: ``complex``, ``string``, ``date``, ``datetime``, ``time``, ``decimal``, ``integer``
            or ``boolean``.
        choice: ``"<group>.<branch>"`` when the element belongs to an ``xs:choice`` (members of a
            nested sequence share a branch), else ``None``.
    """

    path: str
    name: str
    parent: str
    order: int
    min_occurs: int
    max_occurs: int | None
    kind: str
    choice: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    length: int | None = None
    enum: tuple[str, ...] | None = None
    patterns: tuple[re.Pattern[str], ...] = ()
    total_digits: int | None = None
    fraction_digits: int | None = None
    min_inclusive: Decimal | None = None
    max_inclusive: Decimal | None = None
    fixed: str | None = None

    @property
    def is_complex(self) -> bool:
        """Whether the element has child elements."""
        return self.kind == "complex"


@dataclass(frozen=True, slots=True)
class Catalogue:
    """All element specifications of one version."""

    version: Version
    namespace: str | None
    derived: bool
    fields: Mapping[str, FieldSpec]
    children: Mapping[str, tuple[FieldSpec, ...]] = field(repr=False)
    vocabulary: frozenset[str] = field(repr=False)

    def spec(self, path: str) -> FieldSpec | None:
        """Return the specification of ``path``, if the version defines it."""
        return self.fields.get(path)

    def children_of(self, path: str) -> tuple[FieldSpec, ...]:
        """Return the child element specifications of ``path`` in schema order."""
        return self.children.get(path, ())


@cache
def get_catalogue(version: Version | str) -> Catalogue:
    """Return the catalogue of an XML version (not available for ADF)."""
    v = Version(version)
    if v not in _MODULES:
        raise ValueError(f"no field catalogue for {v.value}")
    mod = importlib.import_module(f"{__name__}.{_MODULES[v]}")
    cols: tuple[str, ...] = mod.COLUMNS
    specs: dict[str, FieldSpec] = {}
    children: dict[str, list[FieldSpec]] = {}
    for row in mod.FIELDS:
        r = dict(zip(cols, row, strict=True))
        path: str = r["path"]
        parent, _, name = path.rpartition("/")
        siblings = children.setdefault(parent, [])
        spec = FieldSpec(
            path=path,
            name=name,
            parent=parent,
            order=len(siblings),
            min_occurs=r["min"],
            max_occurs=r["max"],
            kind=r["kind"],
            choice=r["choice"],
            min_length=r["min_length"],
            max_length=r["max_length"],
            length=r["length"],
            enum=r["enum"],
            patterns=tuple(re.compile(p) for p in (r["patterns"] or ())),
            total_digits=r["total_digits"],
            fraction_digits=r["fraction_digits"],
            min_inclusive=None if r["min_inclusive"] is None else Decimal(r["min_inclusive"]),
            max_inclusive=None if r["max_inclusive"] is None else Decimal(r["max_inclusive"]),
            fixed=r["fixed"],
        )
        specs[path] = spec
        siblings.append(spec)
    return Catalogue(
        version=v,
        namespace=mod.NAMESPACE,
        derived=mod.DERIVED,
        fields=MappingProxyType(specs),
        children=MappingProxyType({k: tuple(v) for k, v in children.items()}),
        vocabulary=frozenset(s.name for s in specs.values()),
    )
