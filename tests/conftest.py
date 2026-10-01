"""Shared fixtures."""

from __future__ import annotations

import pytest
import xafgen

ALL_VERSIONS = ("4.0", "3.2.1", "3.2", "3.1", "3.0", "CLAIR2", "ADF")


@pytest.fixture(scope="session")
def ledger() -> xafgen.Ledger:
    return xafgen.make_ledger()


@pytest.fixture(params=ALL_VERSIONS)
def any_version(request: pytest.FixtureRequest) -> str:
    return str(request.param)
