# Contributing to pyxaf

Thanks for helping. pyxaf reads and validates every iteration of the Dutch Auditfile Financieel
(ADF, CLAIR2, XAF 3.0–3.2.1 and 4.0). Bug reports, file-compatibility reports, anonymised samples,
documentation and code are all welcome.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report security problems
privately as described in [SECURITY.md](SECURITY.md), not in public issues. For questions about
using pyxaf, see [SUPPORT.md](SUPPORT.md).

**Looking for something to work on?** Issues labelled
[`good first issue`](https://github.com/SpireflyHQ/pyxaf/labels/good%20first%20issue) are small and
self-contained; [`help wanted`](https://github.com/SpireflyHQ/pyxaf/labels/help%20wanted) marks
larger ones where help is welcome. Comment on the issue before you start, so work is not done
twice. [ARCHITECTURE.md](ARCHITECTURE.md) is a map of the code.

## Never share real audit files

> [!CAUTION]
> **Never attach, upload, paste or commit a real auditfile from a client or company.** This
> applies to issues, pull requests, discussions, gists and test fixtures. Auditfiles contain
> personal data (names, addresses, IBANs, VAT and Chamber of Commerce numbers) and complete
> financial records. Sharing them can break the GDPR (AVG), professional confidentiality and
> your contract with the client.

Instead, share:

- the **pyxaf version**, **Python version**, detected **XAF version** (`pyxaf.detect(path)`)
  and the **software that exported the file**;
- the **finding codes** (`XAF<nnnn>`) and messages that pyxaf reported;
- a **minimal, hand-made or anonymised snippet** that reproduces the problem (see
  [Contributing anonymised samples](#contributing-anonymised-samples)).

## Development setup

You need [uv](https://docs.astral.sh/uv/). uv installs the right Python for you.

```bash
git clone https://github.com/SpireflyHQ/pyxaf.git
cd pyxaf
uv sync --all-extras          # creates .venv with the package, all extras and the dev groups
uvx prek install              # git hooks (or: uvx pre-commit install)
uvx nox                       # lint, type checks and tests: what CI runs
```

The core library has **no runtime dependencies**. Everything outside the standard library
(lxml, nanoarrow, polars, pandas, pyarrow, typer) is an optional extra and must only be imported
lazily, inside the function that needs it, via `pyxaf._optional`. CI has a job that installs the
wheel with no third-party packages at all and runs the core test suite.

## Everyday commands

The common tasks are [nox](https://nox.thea.codes/) sessions; `uvx nox -l` lists them:

| Session | Does |
|---|---|
| `uvx nox -s lint` | all pre-commit hooks (ruff, typos, zizmor, uv-lock, validate-pyproject, …) |
| `uvx nox -s typing` | mypy (strict) and pyright's public-API completeness check |
| `uvx nox -s tests -- -x` | the tests with all extras; options after `--` go to pytest |
| `uvx nox -s tests-core` | the tests that need no extra, without any extra installed |
| `uvx nox -s generate` | regenerate the field catalogues and the generated docs pages |
| `uvx nox -s docs` | build the documentation (`-- serve` for a live preview) |
| `uvx nox -s bench` | the throughput and memory benchmark |

The same work with plain `uv run`, in the development environment:

```bash
uv run pytest                          # tests (all extras installed by `uv sync --all-extras`)
uv run pytest --cov                    # with coverage
uv run pytest -m "not extras"          # only the tests that need no optional extras
uv run ruff check --fix                # lint
uv run ruff format                     # format
uv run mypy                            # type check (strict; blocking in CI)
uv run pyright --verifytypes pyxaf --ignoreexternal   # public API type completeness
uvx prek run --all-files               # all hooks: ruff, typos, zizmor, uv-lock, validate-pyproject, …
```

Test markers (see `[tool.pytest.ini_options]` in `pyproject.toml`):

- `extras` — the test needs an optional extra (`xsd`, `arrow`, `polars`, `pandas`, `parquet`,
  `cli`). Mark every such test, or the no-extras CI job fails.
- `slow` — long-running (large generated files, benchmarks).

Prefer **generated** test input (`tests/xafgen.py`) over fixture files. A test for a quirk
should build the smallest document that shows it.

## Regenerating the field catalogues

`src/pyxaf/catalogue/_generated_*.py` are generated from the bundled XSDs in
`src/pyxaf/schemas/` and `scripts/data/`. Do not edit them by hand. After changing the
generator or its inputs, run:

```bash
uvx nox -s generate        # or: uv run --group gen python scripts/gen_catalogues.py
```

Commit the regenerated files together with the change that caused them. CI regenerates them and
fails if the result differs. `docs/reference/codes.md` and `docs/reference/tables.md` are
generated the same way (by `scripts/gen_docs.py`); see [scripts/README.md](scripts/README.md).

## Adding a finding code

Every problem pyxaf reports is a `Finding` with a stable code `XAF<nnnn>`. Codes are part of
the public API: users filter on them and write them into audit working papers.

1. Pick the next free number in the right range: `1xxx` input/encoding, `2xxx` XML/security,
   `3xxx` version/namespace/structure, `4xxx` references, `5xxx` totals/balance,
   `6xxx` uniqueness, `7xxx` data quality, `8xxx` RGS. An official numbered consistency rule maps
   to the code ending in its number (rule `[0009]` → `XAF5009`).
2. Register it in `CODES` in `src/pyxaf/findings.py` with its default `Severity` (`ERROR` = the
   file violates the specification of its version; `WARNING` = likely a data problem;
   `INFO` = notable), a short English title, and `rule_ref` if it implements an official rule.
3. Emit it with `FindingCollector.add("XAF….", message, line=…, …)`. Messages say what was found
   and where, and include the offending value when that helps.
4. Add a test that triggers the finding and checks its code and severity, and a test that a
   valid document does **not** produce it.
5. Add an entry to `CHANGELOG.md`.

Never renumber a code, reuse a retired code or change its meaning. Changing a default severity
is a user-visible change and belongs in the changelog.

Data problems must never stop a file from loading: report a finding and keep going. Only
"this is not an auditfile at all" and security violations raise.

## Contributing anonymised samples

Real-world quirks (a vendor's odd namespace, a wrong encoding, period 0 opening balances, …)
are the most valuable contributions, but they must arrive **without any real data**:

- **Best:** describe the quirk and share a small **hand-written** snippet, or a generator
  change in `tests/xafgen.py`, that reproduces it.
- **If you must start from a real file:** replace every name, address, e-mail, phone number,
  IBAN/BIC, VAT number, Chamber of Commerce (KvK) number, description and reference with
  fictitious values. Change amounts as well, keeping only what the quirk needs (e.g. the number
  of decimals, or that a transaction does not balance). Remove everything not needed to show
  the problem. Keep the encoding, BOM, line endings and namespace exactly as they were, because
  those are often the bug.
- State the exporting software and version, and confirm in the pull request that the sample
  contains **no real personal or financial data** and that you may publish it under the
  project's MIT license.

Samples that might contain real data are deleted without review.

## Pull requests

- Open an issue first for larger changes, so we can agree on the approach.
- Keep pull requests focused. Include tests and update the documentation and `CHANGELOG.md`
  (under `[Unreleased]`, in the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
  categories: Added, Changed, Deprecated, Removed, Fixed, Security).
- Changes to the public API follow the
  [versioning policy](https://spireflyhq.github.io/pyxaf/versioning/): deprecate first with a
  `DeprecationWarning`, remove in a later minor release.
- CI must pass: ruff, mypy, tests on Linux/macOS/Windows, the no-extras job, the generated-files
  check and zizmor.
- Hard rules: money is `decimal.Decimal`, never `float`;
  keep raw text next to parsed values; never silently coerce; stream transactions and lines so
  memory does not grow with file size; refuse DOCTYPE/ENTITY declarations and never use a
  parser "recover" mode.
- Do not copy or translate code, tests or documentation from GPL/AGPL projects (e.g. rxaf).

## Releasing (maintainers)

1. `uv version --bump patch|minor|major` (or `--bump rc` etc. for a pre-release).
2. Move the `[Unreleased]` entries in `CHANGELOG.md` under the new version and date.
3. Commit, then `git tag -a vX.Y.Z -m "pyxaf X.Y.Z"` and `git push --follow-tags`.
4. The `Release` workflow checks that the tag matches the version, builds and checks the
   distributions, waits for approval in the `pypi` environment, publishes with Trusted
   Publishing (with attestations) and creates the GitHub release. Pre-release tags
   (`a`, `b`, `rc`, `.dev`) go to TestPyPI only.
