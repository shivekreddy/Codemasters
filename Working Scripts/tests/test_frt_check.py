"""
Tests for frt_check.py

Synthetic tests (always run): event detection with duplicate timestamps and a
non-uniform time step, k-factor, current limit, 2P retained voltage, Ti,90 /
Ts with spikes and small steps, P recovery, run lengths, damping, column
mapping, case names.

Acceptance tests (sample case "FRT 1PH 0.05 Q0", DNV 25-1670 Rev. 2 values):
run when the sample folder is in tests/data/ or FRT_SAMPLE_DIR points at it;
otherwise they are skipped. The CSVs are project data and not committed
(.gitignore excludes *.csv).

Run:  python -m pytest "Working Scripts/tests" -v
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import frt_check as fc                                   # noqa: E402


# ==============================================================================
# Synthetic signals
# ==============================================================================

def fault_signal(dup_at_events=True):
    """
    POC-like phase voltages: 1 pu, fault at 10.000 s to 0.3 pu on phase A,
    clearance at 10.250 s with an intermediate recovery row (like the
    10.42025 s row in the sample). Steps: 1 ms steady, 0.25-0.7 ms after
    events; duplicate rows at the events.
    """
    t = list(np.round(np.arange(9.0, 10.0, 0.001), 6)) + [10.0]
    if dup_at_events:
        t += [10.0]                                   # pre-event duplicate
    t += list(np.round(10.0005 + np.cumsum([0.00025, 0.0007] * 40), 6))
    t += list(np.round(np.arange(t[-1] + 0.001, 10.25, 0.001), 6)) + [10.25]
    t += [10.25025]                                   # intermediate recovery row
    t += list(np.round(np.arange(10.2507, 11.5, 0.001), 6))
    t = np.array(t)
    ua = np.where((t > 10.0) & (t <= 10.25), 0.3, 1.0)
    if dup_at_events:
        ua[np.nonzero(t == 10.0)[0][-1]] = 1.0       # still pre-event
    ua[t == 10.25025] = 0.65                          # recovering, not a fault value
    ub = uc = np.ones_like(t)
    return t, ua, ub, uc


def test_event_detection_with_duplicates_and_nonuniform_steps():
    t, ua, ub, uc = fault_signal()
    i_f = fc.detect_inception(t, [ua, ub, uc])
    i_c = fc.detect_clearance(t, [ua, ub, uc], i_f)
    assert t[i_f] == 10.0 and ua[i_f] == 1.0          # last pre-event row
    assert i_f == np.nonzero(t == 10.0)[0][-1]
    assert t[i_c] == 10.25 and ua[i_c] == 0.3         # last pre-clearance row
    assert ua[i_c + 1] == 0.65                        # intermediate row is NOT used


def test_index_at_time_override_takes_first_duplicate():
    t = np.array([9.999, 10.0, 10.0, 10.0005])
    assert fc.index_at_time(t, 10.0) == 1
    assert fc.index_at_time(t, 10.0002) == 2          # last row before


def test_k_factor_definitions():
    # k1 = dIq1 / dU1
    assert fc.k_factor(0.1, 0.6, 1.0, 0.9) == pytest.approx(5.0)
    # k2 = -(Iq2f - Iq2pre) / (U2f - U2pre): inductive (negative) Iq2 -> k2 > 0
    assert fc.k_factor(0.0, -0.5, 0.0, 0.1, negative=True) == pytest.approx(5.0)
    assert fc.k_factor(0.0, 0.5, 0.0, 0.1, negative=True) < 0


def test_worst_phase_current_and_verdicts():
    assert fc.worst_phase_current(0.3203, 0.5632, -0.4321) == pytest.approx(1.0800, abs=5e-4)
    assert fc.k_verdict(5.2, False, 5.0, 0.1) == "per setting"
    assert fc.k_verdict(1.67, True, 5.0, 0.1) == "limited*"
    assert fc.k_verdict(1.67, False, 5.0, 0.1) == "NON-COMPLIANT"
    assert fc.k_verdict(6.0, False, 5.0, 0.1) == "above setting"


def test_u_ll_faulted_2p_matches_phasors():
    a = complex(math.cos(2 * math.pi / 3), math.sin(2 * math.pi / 3))
    v1, v2 = 0.6 + 0j, 0.4 * complex(math.cos(0.3), math.sin(0.3))
    va, vb, vc = v1 + v2, a * a * v1 + a * v2, a * v1 + a * a * v2
    # line pu: |Vb - Vc| / sqrt(3) equals |V1 - V2|
    expected = abs(vb - vc) / math.sqrt(3)
    assert fc.u_ll_faulted_2p(abs(v1), abs(v2), abs(va)) == pytest.approx(abs(v1 - v2))
    assert expected == pytest.approx(abs(v1 - v2))


def step_response(t, t_f, t_c, i_pre, i_flt, tau=0.002, spike=None):
    i = np.where(t <= t_f, i_pre,
                 i_pre + (i_flt - i_pre) * (1 - np.exp(-(t - t_f) / tau)))
    i = np.where(t > t_c, i_pre, i)
    if spike:
        for ts, val in spike:
            i[np.argmin(np.abs(t - ts))] = val
    return i


def test_ti90_ignores_inception_spike_and_reports_within_band():
    t = np.round(np.concatenate([np.arange(9.9, 10.0, 0.001), [10.0],
                                 np.arange(10.0002, 10.25, 0.0005)]), 6)
    i_f, i_c = np.nonzero(t == 10.0)[0][0], len(t) - 1
    i = step_response(t, 10.0, t[-1], 0.0, 1.0, tau=0.002,
                      spike=[(10.0002, 0.95)])        # one-sample spike above 0.9
    i[i_c] = 1.0
    rs = fc.rise_and_settling(t, i, i_f, i_c)
    # exp rise reaches 0.9 at 2 ms * ln(10) = 4.6 ms; the spike must not count
    assert rs["ti90_ms"] == pytest.approx(4.6, abs=0.3)
    assert rs["ts_ms"] == pytest.approx(rs["ti90_ms"])
    assert "within band after rise" in rs["notes"]


def test_ts_with_overshoot_and_small_and_no_step():
    t = np.round(np.concatenate([[10.0], np.arange(10.0005, 10.3, 0.0005)]), 6)
    dt = (t - 10.0) * 1000                           # ms after t_f
    # x: ramp to 1.0 at 2 ms, overshoot 1.3 until 8 ms, then 1.0
    x = np.where(dt <= 2, dt / 2, np.where(dt <= 8, 1.3, 1.0))
    i = 0.2 + 0.5 * x
    rs = fc.rise_and_settling(t, i, 0, len(t) - 1)
    # samples at 1.5 ms (x 0.75) and 2.0 ms (x 1.3): 0.9 crossed at 1.64 ms
    assert rs["ti90_ms"] == pytest.approx(1.636, abs=0.01)
    assert rs["ts_ms"] == pytest.approx(8.0, abs=0.6)      # back below 1.1 after 8 ms
    assert "within band after rise" not in rs["notes"]
    # a one-sample out-of-band spike after the rise is ignored
    spiky = i.copy()
    spiky[np.argmin(np.abs(dt - 50))] = 0.2 + 0.5 * 1.5
    assert fc.rise_and_settling(t, spiky, 0, len(t) - 1)["ts_ms"] == pytest.approx(rs["ts_ms"])
    # small step: absolute band, flagged
    small = 0.2 + 0.1 * np.where(dt <= 2, dt / 2, 1.0)
    rs = fc.rise_and_settling(t, small, 0, len(t) - 1)
    assert any("absolute" in n for n in rs["notes"])
    # no step
    flat = np.full_like(t, 0.2)
    assert fc.rise_and_settling(t, flat, 0, len(t) - 1)["notes"] == ["n/a (no step)"]


def test_p_recovery_needs_hold():
    t = np.round(np.arange(0.0, 15.0, 0.01), 6)
    i_c = int(np.searchsorted(t, 5.0))
    p = np.where(t < 5.0, 100.0, 95.0)
    p[(t > 5.5) & (t < 5.6)] = 50.0                    # brief dip after first recovery
    rec = fc.p_recovery_time(t, p, i_c, 100.0)
    assert rec == pytest.approx(0.6, abs=0.011)        # first sustained recovery
    assert fc.p_recovery_time(t, np.where(t < 5.0, 100.0, 50.0), i_c, 100.0) is None


def test_longest_run_and_damping():
    t = np.round(np.arange(0.0, 10.0, 0.001), 6)
    cond = (t > 2.0) & (t < 2.5)
    assert fc.longest_run(t, cond, 0.0) == pytest.approx(0.5, abs=0.002)
    decaying = np.exp(-(t - 1.0)) * np.sin(2 * math.pi * 2 * t) * 10
    growing = np.exp(0.5 * (t - 1.0)) * np.sin(2 * math.pi * 2 * t) * 10
    assert not fc.damping_increase(t, decaying, 1.0)
    assert fc.damping_increase(t, growing, 1.0)
    assert not fc.damping_increase(t, np.full_like(t, 176.33) + 1e-6 * t, 1.0)


def test_map_columns_keywords_position_fallback_and_failure():
    good = ["time_s", "X Line-Ground Voltage A (p.u.)", "X Line-Ground Voltage B (p.u.)",
            "X Line-Ground Voltage C (p.u.)", "X Positive-Sequence Voltage (p.u.)",
            "X Negative-Sequence Voltage (p.u.)", "X Total Active Power (MW)",
            "X Total Reactive Power (Mvar)", "X Positive-Sequence Active Current (p.u.)",
            "X Positive-Sequence Reactive Current (p.u.)",
            "X Negative-Sequence Reactive Current (p.u.)"]
    mapping, flags = fc.map_columns(good)
    assert mapping["P"] == good[6] and mapping["Q"] == good[7] and not flags
    odd = ["time_s"] + [f"col{i}" for i in range(10)]
    mapping, flags = fc.map_columns(odd)
    assert mapping["Iq2"] == "col9" and len(flags) == 10
    with pytest.raises(ValueError):
        fc.map_columns(["time_s", "col0", "col1"])


def test_parse_case_name():
    assert fc.parse_case_name("FRT 1PH 0.05 Q0").name == "1P-5U-Q0"
    assert fc.parse_case_name("FRT_2PH_0.2_Qmin").name == "2P-20U-Qmin"
    assert fc.parse_case_name("x/FRT 3PH 0.85 Qmax/").name == "3P-85U-Qmax"


# ==============================================================================
# Acceptance: sample case FRT 1PH 0.05 Q0
# ==============================================================================

SAMPLE = os.environ.get("FRT_SAMPLE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "FRT 1PH 0.05 Q0")
needs_sample = pytest.mark.skipif(not os.path.isfile(os.path.join(SAMPLE, "POC.csv")),
                                  reason=f"sample case not found: {SAMPLE}")


@pytest.fixture(scope="module")
def sample():
    return fc.evaluate_case(SAMPLE)


EXPECTED = {   # exported pu (POC sign-flipped, GU as in the CSV)
    "POC": dict(U1_pre=1.0000, U1_fault=0.6858, U2_fault=0.3150, Iq1_pre_export=0.0000,
                Iq1_fault_export=0.5206, Iq2_fault_export=-0.4343,
                Ip1_pre_export=0.9255, Ip1_fault_export=0.3553, k1=1.66, k2=1.38),
    "GU1": dict(U1_pre=1.0078, U1_fault=0.7921, U2_fault=0.2039, Iq1_pre_export=0.1776,
                Iq1_fault_export=0.5632, Iq2_fault_export=-0.4321,
                Ip1_pre_export=0.9166, Ip1_fault_export=0.3203, k1=1.67, k2=1.98),
    "GU2": dict(U1_pre=1.0205, U1_fault=0.8064, U2_fault=0.2079, Iq1_pre_export=0.1767,
                Iq1_fault_export=0.5520, Iq2_fault_export=-0.4433,
                Ip1_pre_export=0.9120, Ip1_fault_export=0.3173, k1=1.64, k2=1.99),
}


@needs_sample
def test_sample_events(sample):
    assert sample["validity"]["t_f"] == pytest.approx(10.000, abs=1e-9)
    assert sample["validity"]["t_c"] == pytest.approx(10.420, abs=1e-9)


@needs_sample
@pytest.mark.parametrize("key", ["POC", "GU1", "GU2"])
def test_sample_values(sample, key):
    r = sample["locations"][key]
    for name, value in EXPECTED[key].items():
        tol = 0.005 if name.startswith("k") else 0.00005
        assert r[name] == pytest.approx(value, abs=tol), name


@needs_sample
def test_sample_current_limit_and_ti90(sample):
    for key in ("GU1", "GU2"):
        r = sample["locations"][key]
        assert r["worst_phase_current_export"] == pytest.approx(1.080, abs=0.0005)
        assert r["limited"] is True                       # k carries the asterisk
        assert r["k1_verdict"] == "limited*" and r["k2_verdict"] == "limited*"
        assert 5.0 <= r["ti90_pos_ms"] <= 7.0             # about 6 ms
    assert sample["locations"]["POC"]["k_verdict"] == "reported only (POC)"


@needs_sample
def test_sample_flags(sample):
    assert any("fault duration 420 ms vs target 250 ms" in f for f in sample["flags"])


@needs_sample
def test_pitfall_intermediate_recovery_row_not_used(sample):
    """Taking the 10.42025 s row instead of the last pre-clearance row would
    give POC U1,fault = 0.848 and k1 ~ 3.45. The tool must not do that."""
    poc = fc.load_location(os.path.join(SAMPLE, "POC.csv"), "POC")
    i_f = sample["locations"]["POC"]["sample_tf_index"]
    i_c = sample["locations"]["POC"]["sample_tc_index"]
    i_bad = int(np.nonzero(np.abs(poc.t - 10.42025) < 1e-9)[0][0])
    assert poc.scaled["U1"][i_bad] == pytest.approx(0.848, abs=0.001)
    k_bad = fc.k_factor(poc.scaled["Iq1"][i_f], poc.scaled["Iq1"][i_bad],
                        poc.scaled["U1"][i_f], poc.scaled["U1"][i_bad])
    assert k_bad == pytest.approx(3.45, abs=0.05)
    assert i_c != i_bad and poc.t[i_c] == pytest.approx(10.420, abs=1e-9)
    assert sample["locations"]["POC"]["U1_fault"] == pytest.approx(0.6858, abs=5e-5)
    assert sample["locations"]["POC"]["k1"] == pytest.approx(1.66, abs=0.005)
