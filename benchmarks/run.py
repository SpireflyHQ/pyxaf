"""Throughput and memory benchmark on a generated XAF 4.0 file.

    uv run python benchmarks/run.py --lines 1000000 [--task read|validate|csv|polars]

Each task runs in a fresh process so peak RSS is measured per task.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = ("read", "validate", "csv", "polars")


def run_task(task: str, path: str) -> dict[str, float]:
    import pyxaf

    start = time.perf_counter()
    lines = 0
    if task == "read":
        with pyxaf.open(path) as af:
            for tx in af.transactions():
                lines += len(tx.lines)
    elif task == "validate":
        report = pyxaf.validate(path)
        lines = report.stats["lines"]
    elif task == "csv":
        with tempfile.TemporaryDirectory() as out, pyxaf.open(path) as af:
            af.export(out, tables=["transactions", "lines", "line_vat"])
            lines = sum(1 for _ in open(Path(out) / "lines.csv", encoding="utf-8")) - 1
    elif task == "polars":
        with pyxaf.open(path) as af:
            lines = af.to_polars(["lines"])["lines"].height
    seconds = time.perf_counter() - start
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return {
        "lines": lines,
        "seconds": round(seconds, 2),
        "lines_per_s": round(lines / seconds),
        "peak_rss_mb": round(rss_mb, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=200_000)
    ap.add_argument("--task", choices=TASKS, action="append")
    ap.add_argument("--file", help="use an existing file instead of generating one")
    ap.add_argument("--_child", nargs=2, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args._child:
        print(json.dumps(run_task(*args._child)))
        return
    tasks = args.task or ["read", "validate", "csv"]
    with tempfile.TemporaryDirectory() as tmp:
        path = args.file or str(Path(tmp) / "bench.xaf")
        if not args.file:
            subprocess.run(
                [sys.executable, str(HERE / "gen_xaf.py"), "--lines", str(args.lines), "-o", path],
                check=True,
                capture_output=True,
            )
        size = Path(path).stat().st_size / 1e6
        print(f"file: {size:.0f} MB, python {sys.version.split()[0]}")
        for task in tasks:
            out = subprocess.run(
                [sys.executable, __file__, "--_child", task, path],
                check=True,
                capture_output=True,
                text=True,
            )
            print(f"{task:9} {json.loads(out.stdout.strip().splitlines()[-1])}")


if __name__ == "__main__":
    main()
