# Benchmarks

`run.py` measures throughput (lines per second) and peak memory (RSS) on a generated XAF 4.0
file. `gen_xaf.py` writes that file; it contains only synthetic data.

```bash
uvx nox -s bench                          # 200,000 lines
uvx nox -s bench -- --lines 1000000       # a bigger file
uv run python benchmarks/run.py --task validate --lines 1000000
```

Tasks: `read` (normalized transactions and lines), `validate`, `csv` and `polars` export. Each
task runs in a fresh process, so peak memory is measured per task. Peak memory should stay
roughly constant as `--lines` grows; if it grows with the file, something holds data that should
be streamed.

CI runs the benchmark on every push as a non-blocking job. Compare against `main` on the same
machine before and after a change to the parsing hot path; absolute numbers depend on the
machine.
