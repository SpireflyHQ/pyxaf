`xpaths-3.0-3.1.json` lists the leaf element paths of XAF 3.0 and 3.1. It is derived from the
"XAF Mapping en Namen" table of the AnalyticsLibrary project
(https://github.com/AnalyticsLibrary/Analytics, `A_GENERAL/Naamgeving`, Apache License 2.0),
whose 3.2 paths match the official 3.2 XSD exactly. No official 3.0/3.1 XSD is publicly available.

Provenance: upstream commit `1cec6a90338d1d30b74bc518d89c43e9443073dd` (2019-04-05, the latest).
The upstream licence is `A_GENERAL/license.txt` (Apache-2.0; the project has no NOTICE file); a
copy of the licence is in `LICENSES/Apache-2.0.txt` and ships with the package, because the
generated catalogues `_generated_xaf30.py` and `_generated_xaf31.py` are built from this table.
pyxaf's change: only the element paths were kept; cardinality and facets come from the official
3.2 XSD.
