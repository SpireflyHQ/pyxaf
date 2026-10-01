# Versioning and compatibility

pyxaf follows [Semantic Versioning 2.0](https://semver.org/spec/v2.0.0.html). Every release is
listed in the [changelog](https://github.com/SpireflyHQ/pyxaf/blob/main/CHANGELOG.md).

## Before 1.0

Until version 1.0 the API can still change. pyxaf keeps changes predictable:

| Release | May contain |
|---|---|
| patch (`0.y.Z`) | bug fixes and documentation only |
| minor (`0.Y.0`) | new features, and breaking changes to the public API, each listed in the changelog with a migration note |

From 1.0 on, breaking changes need a new major version.

## What the public API is

These are covered by the compatibility promise:

- the names exported by the `pyxaf` package (`pyxaf.__all__`), the public modules `pyxaf.rgs`
  and `pyxaf.tables`, and everything documented in the [API reference](reference/api.md);
- the [finding codes](reference/codes.md): a code is never renumbered, reused or given a new
  meaning. Changing a code's default severity is a listed change;
- the [table schemas](reference/tables.md): column names and types. New columns may be added;
- the command line: commands, options, exit codes and the JSON report layout, which is versioned
  by `schema_version`.

These are **not** covered and may change in any release: modules and names starting with an
underscore (`pyxaf._xml`, `pyxaf.catalogue._generated_xaf40`, …), the exact wording of finding
messages and log output, and the bundled schema files.

A newer pyxaf may report findings that an older one did not, because it checks more. Do not treat
"no new findings" as part of the API; pin the version when you need identical reports.

## Deprecations

A feature that is going away first raises a `DeprecationWarning` that names the replacement and
the version that removes it. It is removed no earlier than the next minor release, and the
changelog lists it under **Deprecated** and later under **Removed**. To see deprecation warnings
in your own code, run Python with `-W default::DeprecationWarning` or let pytest show them.

## Supported Python versions

pyxaf supports CPython 3.11 and newer and is tested on Linux, macOS and Windows. Support for a
Python version ends in a minor release after that version reaches its upstream end of life.
