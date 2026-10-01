from __future__ import annotations

import pytest
import xafgen

import pyxaf
from pyxaf import Version
from pyxaf.catalogue import get_catalogue


@pytest.mark.parametrize("version", ["CLAIR2", "3.0", "3.1", "3.2", "3.2.1", "4.0"])
def test_catalogue_integrity(version: str) -> None:
    cat = get_catalogue(version)
    assert "/auditfile" in cat.fields
    for path, spec in cat.fields.items():
        assert spec.parent == "" or spec.parent in cat.fields, path
        assert cat.fields[spec.parent].is_complex if spec.parent else True
    assert cat.derived == (version in ("3.0", "3.1"))


def test_catalogue_facts() -> None:
    c40 = get_catalogue("4.0")
    assert sum(not s.is_complex for s in c40.fields.values()) == 90
    amnt = c40.fields["/auditfile/company/transactions/journal/transaction/trLine/amnt"]
    assert (amnt.total_digits, amnt.fraction_digits, amnt.min_inclusive) == (20, 2, None)
    assert c40.fields["/auditfile/header/RGSVersion"].min_occurs == 0
    assert "custSupTp" not in get_catalogue("3.0").vocabulary
    assert "custSupTp" in get_catalogue("3.1").vocabulary
    assert len(get_catalogue("3.2").fields) == len(get_catalogue("3.2.1").fields) == 298
    with pytest.raises(ValueError):
        get_catalogue("ADF")


@pytest.mark.extras
@pytest.mark.parametrize("version", ["CLAIR2", "3.2", "3.2.1", "4.0"])
def test_generated_files_are_xsd_valid(version: str) -> None:
    pytest.importorskip("lxml")
    report = pyxaf.validate(xafgen.write(version), xsd=True)
    assert "L4x xsd" in report.checked
    assert not [f for f in report.findings if f.code == "XAF3040"]


@pytest.mark.extras
def test_xsd_errors_are_reported_and_agree_with_catalogue() -> None:
    pytest.importorskip("lxml")
    data = xafgen.write("4.0").replace(b"<amntTp>C</amntTp>", b"<amntTp>X</amntTp>", 1)
    report = pyxaf.validate(data, xsd=True)
    assert {"XAF3040", "XAF3015"} <= set(report.counts)


@pytest.mark.extras
def test_xsd_skipped_for_derived_versions_and_variant_namespaces() -> None:
    pytest.importorskip("lxml")
    report = pyxaf.validate(xafgen.write("3.1"), xsd=True)
    f = next(f for f in report.findings if f.code == "XAF3040")
    assert f.severity is pyxaf.Severity.INFO
    data = xafgen.write("4.0", namespace="http://www.odb.belastingdienst.nl/XAF/4.0")
    report = pyxaf.validate(data, xsd=True)
    assert any(f.code == "XAF3040" and f.severity is pyxaf.Severity.ERROR for f in report.findings)


def test_xsd_files_are_bundled() -> None:
    from importlib import resources

    from pyxaf._xsd import SCHEMA_FILES

    for name in SCHEMA_FILES.values():
        assert resources.files("pyxaf.schemas").joinpath(name).is_file()
    assert set(SCHEMA_FILES) == {Version.CLAIR2, Version.XAF32, Version.XAF321, Version.XAF40}
