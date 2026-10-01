"""Generate the synthetic sample auditfiles in ``examples/`` used by the README quickstart.

    uv run python scripts/gen_examples.py

The files are rendered by the test-suite generator (``tests/xafgen.py``) and contain only made-up
data. ``2024-broken.xaf`` is ``2024.xaf`` with one mistyped amount, so validation has something to
report.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import xafgen  # noqa: E402

OUT = ROOT / "examples"
TYPO = (b"<amnt>3612.16</amnt>", b"<amnt>3621.16</amnt>")


def main() -> None:
    clean = xafgen.write("4.0")
    if clean.count(TYPO[0]) != 1:
        raise SystemExit(f"expected exactly one {TYPO[0]!r} in the generated file")
    OUT.mkdir(exist_ok=True)
    (OUT / "2024.xaf").write_bytes(clean)
    (OUT / "2024-broken.xaf").write_bytes(clean.replace(*TYPO))
    print(f"wrote {OUT / '2024.xaf'} and {OUT / '2024-broken.xaf'}")


if __name__ == "__main__":
    main()
