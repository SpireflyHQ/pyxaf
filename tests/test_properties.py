"""Property tests: generated ledgers survive write → read → tables with exact totals."""

from __future__ import annotations

from decimal import Decimal

import xafgen
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pyxaf

versions = st.sampled_from(["4.0", "3.2.1", "3.2", "3.1", "3.0", "CLAIR2", "ADF"])


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 10_000), n_tx=st.integers(1, 25), version=versions)
def test_roundtrip_totals(seed: int, n_tx: int, version: str) -> None:
    led = xafgen.make_ledger(seed=seed, n_tx=n_tx)
    with pyxaf.open(xafgen.write(version, led)) as af:
        d = c = Decimal(0)
        n = 0
        for ln in af.lines():
            d += ln.debit or 0
            c += ln.credit or 0
            n += 1
        exp_d, exp_c = led.totals()
        if version in ("CLAIR2", "ADF"):
            ob_d, ob_c = led.ob_totals()
            exp_d, exp_c = exp_d + ob_d, exp_c + ob_c
            n -= len(led.ob_lines)
        assert (d, c) == (exp_d, exp_c)
        assert n == len(led.lines())
    report = pyxaf.validate(xafgen.write(version, led))
    assert report.ok, str(report)


@settings(max_examples=60, deadline=None)
@given(amount=st.decimals(min_value=-(10**6), max_value=10**6, places=2, allow_nan=False))
def test_sign_policy_flip(amount: Decimal) -> None:
    from pyxaf._normalize import Normalizer
    from pyxaf.findings import FindingCollector
    from pyxaf.raw import RawRecord

    norm = Normalizer(pyxaf.Version.XAF40, findings=FindingCollector())
    rec = RawRecord("trLine", {"amnt": str(amount), "amntTp": "C"}, None, 1)
    side, signed = norm.sided(amount, "C", rec, "/x")
    assert side is pyxaf.Side.CREDIT
    assert signed == -amount
    debit, credit = norm.split(signed)
    assert debit is not None and credit is not None
    assert debit >= 0 and credit >= 0 and debit - credit == signed
