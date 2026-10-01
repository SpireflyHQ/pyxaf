"""Release metadata that must stay in sync with the package version."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_citation_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    cff = ROOT / "CITATION.cff"
    if not cff.is_file():  # the sdist ships the tests but not CITATION.cff
        pytest.skip("CITATION.cff is not part of this source tree")
    citation = cff.read_text(encoding="utf-8")
    version = re.search(r'^version:\s*"?([^"\s]+)"?\s*$', citation, re.MULTILINE)
    assert version is not None, "CITATION.cff has no version"
    assert version.group(1) == project["version"], "bump CITATION.cff together with the release"
    released = re.search(r'^date-released:\s*"?(\d{4}-\d{2}-\d{2})"?\s*$', citation, re.MULTILINE)
    assert released is not None, "CITATION.cff has no date-released"
