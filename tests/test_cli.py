from __future__ import annotations

import json
from pathlib import Path

import pytest
import xafgen

pytest.importorskip("typer")
from typer.testing import CliRunner

from pyxaf.cli import app

pytestmark = pytest.mark.extras
runner = CliRunner()


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    p = tmp_path / "clean.xaf"
    p.write_bytes(xafgen.write("4.0"))
    return p


@pytest.fixture
def broken(tmp_path: Path) -> Path:
    p = tmp_path / "broken.xaf"
    led = xafgen.unbalance_first_transaction(xafgen.make_ledger())
    p.write_bytes(xafgen.write("4.0", led))
    return p


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and "pyxaf" in result.stdout


def test_detect(clean: Path) -> None:
    result = runner.invoke(app, ["detect", str(clean)])
    assert result.exit_code == 0 and "4.0" in result.stdout
    result = runner.invoke(app, ["detect", "--output", "json", str(clean)])
    assert json.loads(result.stdout)["files"][0]["version"] == "4.0"


def test_info(clean: Path) -> None:
    result = runner.invoke(app, ["info", "--output", "json", str(clean)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["lines"] == 36 and data["total_debit"] == data["total_credit"]


def test_validate_exit_codes(clean: Path, broken: Path) -> None:
    assert runner.invoke(app, ["validate", str(clean)]).exit_code == 0
    result = runner.invoke(app, ["validate", str(broken)])
    assert result.exit_code == 2 and "XAF5010" in result.stdout
    result = runner.invoke(app, ["validate", "--output", "json", str(broken)])
    assert json.loads(result.stdout)["ok"] is False


def test_validate_strict_warnings(tmp_path: Path) -> None:
    p = tmp_path / "w.xaf"
    p.write_bytes(xafgen.write("4.0").replace(b"<trDt>2024-01", b"<trDt>2023-01", 1))
    assert runner.invoke(app, ["validate", str(p)]).exit_code == 0
    assert runner.invoke(app, ["validate", "--strict", str(p)]).exit_code == 1


def test_validate_set(tmp_path: Path) -> None:
    a, b = tmp_path / "a.xaf", tmp_path / "b.xaf"
    a.write_bytes(xafgen.write("4.0", xafgen.make_ledger(seed=1, n_tx=2)))
    b.write_bytes(xafgen.write("4.0", xafgen.make_ledger(seed=2, n_tx=2), ob_style="none"))
    result = runner.invoke(app, ["validate", "--set", "--output", "json", str(a), str(b)])
    assert json.loads(result.stdout)["stats"]["transactions"] == 4


def test_export(clean: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, ["export", str(clean), "-o", str(out), "-t", "lines"])
    assert result.exit_code == 0
    assert (out / "lines.csv").exists() and not (out / "accounts.csv").exists()


def test_tool_failure(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("not an auditfile")
    result = runner.invoke(app, ["validate", str(p)])
    assert result.exit_code == 3


def test_codes() -> None:
    result = runner.invoke(app, ["codes", "--output", "json"])
    assert any(c["code"] == "XAF5009" for c in json.loads(result.stdout))
