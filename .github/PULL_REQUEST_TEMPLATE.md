## What and why

<!-- What does this change, and why? Link the issue: "Fixes #123". -->

## How was it tested?

<!-- New/changed tests, manual checks, which XAF versions or exporting software it concerns. -->

## Checklist

- [ ] **No real client or company data** is included anywhere: no real auditfiles, names, IBANs,
      VAT/KvK numbers or amounts in code, tests, fixtures, docs or this description.
- [ ] New sample files are hand-made or anonymised, state their origin (exporting software), and
      may be published under the MIT license.
- [ ] Tests added or updated; tests that need an optional extra are marked `@pytest.mark.extras`.
- [ ] No new runtime dependency in the core; optional libraries are imported lazily.
- [ ] Money stays `decimal.Decimal`; raw text is kept; nothing is coerced silently.
- [ ] New or changed finding codes are registered in `pyxaf.findings.CODES` and documented.
- [ ] `CHANGELOG.md` updated under `[Unreleased]`.
- [ ] `uvx prek run --all-files`, `uv run mypy` and `uv run pytest` pass locally.
