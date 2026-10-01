# Command line

```bash
pip install "pyxaf[cli]"
```

The `pyxaf` command is always installed, but needs the `cli` extra (Typer). Without it, it prints
the install hint and exits with code 3. `python -m pyxaf` works the same way.

| Command | Purpose |
|---|---|
| [`pyxaf detect FILE…`](#pyxaf-detect) | detect version, namespace and encoding (inspects the first 64 KiB) |
| [`pyxaf info FILE`](#pyxaf-info) | summarise a file: version, company, counts, totals (streams the file once) |
| [`pyxaf validate FILE…`](#pyxaf-validate) | validate files, each separately or as one split auditfile |
| [`pyxaf export FILE`](#pyxaf-export) | export the normalized tables to a directory |
| [`pyxaf codes`](#pyxaf-codes) | list all finding codes with their default severity |

`pyxaf --version` prints the version; every command has `--help`.

## `pyxaf detect`

```console
$ pyxaf detect 2024.xaf export.xaf
2024.xaf: 4.0 (xaf, confidence 1.0, utf-8)
  - official namespace of XAF 4.0
export.xaf: 4.0 (xaf, confidence 0.7, utf-8)
  - namespace 'http://www.auditfiles.nl/XAF/4.0' is known-bogus for 4.0
```

Inspects only the first 64 KiB of each file (see [detection](versions.md#how-detection-works)).

| Option | Meaning |
|---|---|
| `--output text\|json` | output format (default `text`); JSON is a list with `file`, `family`, `version`, `namespace`, `namespace_status`, `encoding`, `bom`, `confidence`, `continuation`, `reasons` per file |

## `pyxaf info`

```console
$ pyxaf info 2024.xaf
file             2024.xaf
version          4.0
encoding         utf-8
company          Voorbeeld & Zonen B.V.
fiscal year      2024
start date       2024-01-01
end date         2024-12-31
currency         EUR
software         pyxaf-testgen 1.0
accounts         11
relations        3
vat codes        2
periods          12
journals         3
transactions     12
lines            36
total debit      38260.42
total credit     38260.42
opening balance  source=element, lines=3, debit=12500.50, credit=12500.50
findings         0
```

Streams the file once and summarises it. `findings` counts what reading noticed; use `validate`
for a full check.

| Option | Meaning |
|---|---|
| `--output text\|json` | output format (default `text`) |
| `--encoding NAME` | override the declared encoding |
| `--repair NAME` | opt-in repair: `control-chars`, `latin1-as-cp1252`, `bare-ampersand`; repeatable |

## `pyxaf validate`

```console
$ pyxaf validate unbalanced.xaf
== unbalanced.xaf
4.0 — 2 error(s), 0 warning(s), 0 info
- ERROR XAF5009 transactions totalDebit 38260.42 ≠ totalCredit 38261.42
line 223 ERROR XAF5010 transaction '1' in journal 'MEM' does not balance: debit 3612.16, credit 3613.16
$ echo $?
2
```

Validates each file separately, or several files as one [multi-file set](reading.md#multi-file-sets)
with `--set`. See [Validation](validation.md) for the layers and rule sets.

| Option | Meaning |
|---|---|
| `--xsd` | also validate against the official XSD (needs `pyxaf[xsd]`) |
| `--rules spec\|vts` | rule set (default `spec`) |
| `--output text\|json` | output format (default `text`) |
| `--strict` | exit with 1 when there are warnings (and no errors) |
| `--max-findings N` | keep at most N findings **per code** (default 100) |
| `--encoding NAME` | override the declared encoding |
| `--repair NAME` | opt-in repair; repeatable |
| `--set` | treat all FILES as one split auditfile, in the given order |

With `--output json` each report is the [`to_dict()`](validation.md#the-report) form (with
`schema_version` 1) plus a `files` list; several reports are printed as a JSON array, a single
report as an object:

```console
$ pyxaf validate 2024.xaf --output json
{
  "files": [
    "2024.xaf"
  ],
  "schema_version": 1,
  "format": {
    "family": "xaf",
    "version": "4.0",
    ...
  },
  "ok": true,
  ...
}
```

The JSON layout is versioned by `schema_version` so that scripts can rely on it; the `detect` and
`info` JSON outputs carry no version number yet.

## `pyxaf export`

```console
$ pyxaf export 2024.xaf --out out/ --format jsonl --table lines --table accounts
out/lines.jsonl
out/accounts.jsonl
```

Writes the [normalized tables](interop.md) of FILE, one file per table, and prints the paths.

| Option | Meaning |
|---|---|
| `--out`, `-o DIR` | output directory (required; created if missing) |
| `--format`, `-f csv\|jsonl\|parquet` | file format (default `csv`; `parquet` needs `pyxaf[parquet]`) |
| `--table`, `-t NAME` | table to export; repeatable (default: all tables) |
| `--encoding NAME`, `--repair NAME` | as for `info` |

## `pyxaf codes`

```console
$ pyxaf codes
XAF1001  INFO     Byte-order mark present
XAF1002  WARNING  No XML declaration; encoding assumed to be UTF-8
XAF1003  ERROR    Encoding not allowed by the specification
...
XAF5009  ERROR    Transactions totalDebit differs from totalCredit [0009]
...
```

Lists every finding code with its default severity, title and official rule; `--output json` gives
a list of objects with `code`, `severity`, `title`, `rule_ref`. The same list is on the
[finding codes](../reference/codes.md) page.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success; for `validate`: no errors (and, with `--strict`, no warnings) |
| 1 | `validate --strict`: warnings, but no errors |
| 2 | `validate`: at least one ERROR finding in at least one file |
| 3 | tool failure: a usage error (unknown command or option, missing argument, a file that does not exist), the input could not be read (not an auditfile, encrypted, unreadable), a missing extra, or the `cli` extra is not installed |

!!! note
    Command-line usage errors (an unknown command or option, a missing argument, a file that does
    not exist) exit with code 3, for both `pyxaf` and `python -m pyxaf`, so they can never be
    mistaken for code 2 (ERROR findings). The error output says what was wrong.
