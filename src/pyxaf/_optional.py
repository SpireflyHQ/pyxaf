"""Import optional dependencies lazily, with an install hint when missing."""

from __future__ import annotations

import importlib
from types import ModuleType

from .errors import MissingExtraError

__all__ = ["require"]


def require(module: str, extra: str) -> ModuleType:
    """Import ``module`` or raise :class:`~pyxaf.errors.MissingExtraError` naming the extra."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingExtraError(
            f"this feature needs {module.split('.', maxsplit=1)[0]!r}: pip install 'pyxaf[{extra}]'"
        ) from exc
