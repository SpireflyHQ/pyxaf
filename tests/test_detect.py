from __future__ import annotations

import gzip
import io
import zipfile

import pytest
import xafgen

import pyxaf
from pyxaf import Family, NamespaceStatus, Version


def test_detects_every_generated_iteration(any_version: str) -> None:
    info = pyxaf.detect(xafgen.write(any_version))
    assert info.version == any_version
    assert info.family is Version(any_version).family


@pytest.mark.parametrize(
    ("namespace", "status", "confidence"),
    [
        (xafgen.NS["4.0"], NamespaceStatus.OFFICIAL, 1.0),
        ("http://www.odb.belastingdienst.nl/XAF/4.0", NamespaceStatus.DOCUMENTED_VARIANT, 0.9),
        ("http://www.auditfiles.nl/XAF/4.0", NamespaceStatus.KNOWN_BOGUS, 0.7),
        ("HTTP://WWW.AUDITFILES.NL/XAF/4.0/", NamespaceStatus.KNOWN_BOGUS, 0.7),
    ],
)
def test_namespace_variants_of_40(
    namespace: str, status: NamespaceStatus, confidence: float
) -> None:
    info = pyxaf.detect(xafgen.write("4.0", namespace=namespace))
    assert info.version is Version.XAF40
    assert info.namespace_status is status
    assert info.confidence == confidence


def test_unknown_or_missing_namespace_uses_vocabulary() -> None:
    for ns in ("urn:example:not-xaf", ""):
        info = pyxaf.detect(xafgen.write("4.0", namespace=ns))
        assert info.version is Version.XAF40  # RGScode, Commercenr, … only exist in 4.0
        assert info.confidence < 1
        assert any("vocabulary" in r for r in info.reasons)


def test_wrong_official_namespace_is_reported() -> None:
    # a 4.0 document wearing the 3.2 namespace
    info = pyxaf.detect(xafgen.write("4.0", namespace=xafgen.NS["3.2"]))
    assert info.version is Version.XAF32
    assert info.confidence < 1
    assert any("fits 4.0 better" in r for r in info.reasons)


def test_encoding_bom_and_declaration() -> None:
    info = pyxaf.detect(xafgen.write("3.2", bom=True))
    assert info.bom and info.encoding.effective == "utf-8"
    info = pyxaf.detect(xafgen.write("3.2", declaration=False))
    assert not info.encoding.has_declaration
    info = pyxaf.detect(xafgen.write("3.2", encoding="ISO-8859-1"))
    assert info.encoding.effective == "iso8859-1"
    info = pyxaf.detect(xafgen.write("3.2", encoding="windows-1252"))
    assert info.encoding.effective == "cp1252"


def test_utf16_document() -> None:
    data = xafgen.write("4.0").decode().replace('encoding="UTF-8"', 'encoding="UTF-16"')
    info = pyxaf.detect(data.encode("utf-16"))
    assert info.version is Version.XAF40
    assert info.encoding.effective.startswith("utf-16")


def test_continuation_comment() -> None:
    data = b"<!-- Vervolgbestand 2 van 3 -->\n<transaction><nr>1</nr></transaction>"
    info = pyxaf.detect(data)
    assert info.continuation == (2, 3)


def test_compressed_inputs() -> None:
    data = xafgen.write("4.0")
    assert pyxaf.detect(gzip.compress(data)).compression == "gzip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("2024.xaf", data)
    info = pyxaf.detect(buf.getvalue())
    assert info.compression == "zip" and info.version is Version.XAF40


def test_zip_with_several_members_is_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.xaf", b"<auditfile/>")
        zf.writestr("b.xaf", b"<auditfile/>")
    with pytest.raises(pyxaf.NotAnAuditfileError, match="exactly one"):
        pyxaf.detect(buf.getvalue())


@pytest.mark.parametrize(
    "data",
    [b"hello world", b"<html><body/></html>", b"", b"\x00\x01\x02binary"],
)
def test_not_an_auditfile(data: bytes) -> None:
    with pytest.raises(pyxaf.NotAnAuditfileError):
        pyxaf.detect(data)


def test_encrypted_suffix(tmp_path: pytest.TempPathFactory) -> None:
    p = tmp_path / "admin.xac"  # type: ignore[operator]
    p.write_bytes(b"\x8a\x11random")
    with pytest.raises(pyxaf.EncryptedAuditfileError):
        pyxaf.detect(p)


def test_cab_archive_is_explained() -> None:
    with pytest.raises(pyxaf.EncryptedAuditfileError, match="CAB"):
        pyxaf.detect(b"MSCF\x00\x00\x00\x00rest")


def test_adf_detection_with_bom() -> None:
    info = pyxaf.detect(b"\xef\xbb\xbf" + xafgen.write("ADF"))
    assert info.family is Family.ADF


def test_case_insensitive_root() -> None:
    data = (
        xafgen.write("4.0")
        .replace(b"<auditfile", b"<AuditFile", 1)
        .replace(b"</auditfile>", b"</AuditFile>")
    )
    info = pyxaf.detect(data)
    assert info.version is Version.XAF40
    assert any("root element written as" in r for r in info.reasons)
