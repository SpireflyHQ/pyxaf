# Maintenance scripts

These scripts are for maintainers and contributors; they are not part of the package. Run them
through nox (`uvx nox -s generate`) or directly with `uv run`.

| Script | Purpose |
|---|---|
| `gen_catalogues.py` | Generates `src/pyxaf/catalogue/_generated_*.py` from the XSDs in `src/pyxaf/schemas/` and the XAF 3.0/3.1 path lists in `data/`. Needs the `gen` dependency group. |
| `gen_docs.py` | Generates `docs/reference/codes.md` and `docs/reference/tables.md` from the code. `--check` fails when they are out of date. |
| `griffe_rst.py` | A griffe extension used by the docs build to render the reST roles in docstrings as Markdown. |

The generated files are committed. CI regenerates them and fails if the result differs.

`data/` holds the inputs that are not XSDs; see `data/README.md` for their origin and license.
