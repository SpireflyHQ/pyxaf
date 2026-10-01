"""Hostile inputs must be refused quickly and without side effects."""

from __future__ import annotations

import gzip
import time

import pytest
import xafgen

import pyxaf

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
 <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
 <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
 <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">
 <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">
 <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">
]>
<auditfile><header><fiscalYear>&lol9;</fiscalYear></header></auditfile>"""

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE auditfile [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<auditfile><header><fiscalYear>&xxe;</fiscalYear></header><company/></auditfile>"""

EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE auditfile SYSTEM "http://example.invalid/evil.dtd">
<auditfile><header/><company/></auditfile>"""


@pytest.mark.parametrize("data", [BILLION_LAUGHS, XXE, EXTERNAL_DTD], ids=["laughs", "xxe", "dtd"])
def test_doctype_is_refused(data: bytes) -> None:
    start = time.perf_counter()
    with pytest.raises(pyxaf.ForbiddenConstructError):
        pyxaf.open(data)
    assert time.perf_counter() - start < 1


@pytest.mark.parametrize("data", [BILLION_LAUGHS, XXE], ids=["laughs", "xxe"])
def test_validate_reports_forbidden_construct(data: bytes) -> None:
    report = pyxaf.validate(data)
    assert not report.ok
    assert [f.code for f in report.findings] == ["XAF2002"]


def test_doctype_in_detection_is_refused() -> None:
    with pytest.raises(pyxaf.ForbiddenConstructError):
        pyxaf.detect(XXE)


def test_deep_nesting_limit() -> None:
    deep = (
        b"<auditfile><header/><company>" + b"<x>" * 100 + b"</x>" * 100 + b"</company></auditfile>"
    )
    with pytest.raises(pyxaf.LimitExceededError, match="nesting"):
        pyxaf.open(deep)


def test_text_size_limit() -> None:
    data = xafgen.write("4.0").replace(
        b"<desc>Memoriaal</desc>", b"<desc>" + b"x" * 5000 + b"</desc>"
    )

    def read() -> None:
        list(pyxaf.open(data, max_text_size=1000).transactions())

    with pytest.raises(pyxaf.LimitExceededError, match="longer than"):
        read()


def test_gzip_bomb_ratio_limit() -> None:
    bomb = gzip.compress(b"<auditfile>" + b" " * 50_000_000 + b"</auditfile>")
    with pytest.raises(pyxaf.LimitExceededError, match="decompressed"):
        pyxaf.open(bomb)


def test_absolute_decompressed_limit() -> None:
    data = gzip.compress(xafgen.write("4.0"))
    with pytest.raises(pyxaf.LimitExceededError):
        pyxaf.open(data, max_decompressed_size=1000)
