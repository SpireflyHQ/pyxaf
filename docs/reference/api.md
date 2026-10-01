# API reference

Generated from the docstrings with [mkdocstrings](https://mkdocstrings.github.io/). Everything
listed here under `pyxaf.<module>` is also importable from the top-level package where noted:
`pyxaf.open`, `pyxaf.detect`, `pyxaf.validate`, `pyxaf.AuditFile`, `pyxaf.ValidationReport`, the
model classes, `pyxaf.Finding`, `pyxaf.Severity`, `pyxaf.CODES`, `pyxaf.FormatInfo`,
`pyxaf.Version`, `pyxaf.Family`, `pyxaf.NamespaceStatus`, `pyxaf.RawRecord` and the exceptions.
Import `pyxaf.rgs` and `pyxaf.tables` explicitly (`import pyxaf.rgs`).

| Page section | Contents |
|---|---|
| [Reading](#pyxaf.reader.open) | `open()`, `AuditFile`, `RawView` (`af.raw`) |
| [Detection](#pyxaf.detect.detect) | `detect()` |
| [Validation](#pyxaf.validate.validate) | `validate()`, `ValidationReport` |
| [Findings](#pyxaf.findings) | `Finding`, `Severity`, `CODES`, `CodeInfo`, `FindingCollector` |
| [Formats](#pyxaf.formats) | `FormatInfo`, `Version`, `Family`, `NamespaceStatus` |
| [Data model](#pyxaf.models) | `Header`, `Company`, `LedgerAccount`, `Transaction`, `Line`, … |
| [Raw records](#pyxaf.raw.RawRecord) | `RawRecord` |
| [Tables](#pyxaf.tables) | `Tables`, `Table`, `Column`, `TABLES` |
| [RGS](#pyxaf.rgs) | `load_excel()`, `RgsSchema`, `RgsCode`, `validate_refs()` |
| [Exceptions](#pyxaf.errors) | `PyxafError` and subclasses |

::: pyxaf.reader.open
    options:
      heading: "pyxaf.open"

::: pyxaf.reader.AuditFile
    options:
      heading: "pyxaf.AuditFile"

::: pyxaf.reader.RawView
    options:
      heading: "pyxaf.reader.RawView"

::: pyxaf.detect.detect
    options:
      heading: "pyxaf.detect"

::: pyxaf.validate.validate
    options:
      heading: "pyxaf.validate"

::: pyxaf.validate.ValidationReport
    options:
      heading: "pyxaf.ValidationReport"

::: pyxaf.findings
    options:
      show_root_full_path: true

::: pyxaf.formats
    options:
      show_root_full_path: true
      members: [Family, Version, NamespaceStatus, FormatInfo, KNOWN_NAMESPACES]

::: pyxaf.models
    options:
      show_root_full_path: true

::: pyxaf.raw.RawRecord
    options:
      heading: "pyxaf.RawRecord"

::: pyxaf.tables
    options:
      show_root_full_path: true
      members: [TABLES, Column, Table, Tables]

::: pyxaf.rgs
    options:
      show_root_full_path: true

::: pyxaf.errors
    options:
      show_root_full_path: true
