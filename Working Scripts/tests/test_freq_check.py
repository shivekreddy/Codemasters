"""
Tests for freq_check.py

Synthetic tests (always run): p_expected against the full park and HVC
tables (Freq_Calcs_clean.xlsx / DNV 25-1670 Rev. 2), step response with a
known pure delay and first-order lag on a non-uniform time base with
duplicate timestamps, threshold steps, FSM insensitivity, manifest handling
(missing columns, ramp cases skipped), bus1-only sign flip, PPC limit flag.

Acceptance test (sample "LFSMO_Step.csv"): runs when the file is in
tests/data/ or FREQ_SAMPLE_CSV points at it; otherwise skipped. The CSV is
project data and not committed (.gitignore excludes *.csv). The Ti / Ts
values are PROVISIONAL until confirmed against the PowerFactory cursors.

Run:  python -m pytest "Working Scripts/tests" -v
"""

import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import freq_check as fq                                  # noqa: E402

CFG = fq.CONFIG
P0 = 132.72          # 70 % of 189.6 MW
P0_HVC = 45.535


def pe(f, mode, p0=P0):
    return round(fq.p_expected(f, mode, p0)[0], 2)


# ==============================================================================
# p_expected
# ==============================================================================

@pytest.mark.parametrize("mode,f,p", [
    ("LFSM-O", 50.2, 132.72), ("LFSM-O", 50.5, 116.79), ("LFSM-O", 51.0, 90.25),
    ("LFSM-O", 51.5, 63.71),
    ("LFSM-U", 49.8, 132.72), ("LFSM-U", 49.5, 148.65),
    ("FSM", 50.1, 127.41), ("FSM", 50.2, 122.10), ("FSM", 50.3, 117.22),
    ("FSM", 49.9, 138.03), ("FSM", 49.8, 143.34), ("FSM", 49.7, 149.07),
])
def test_p_expected_full_park(mode, f, p):
    assert pe(f, mode) == p


@pytest.mark.parametrize("mode,f,p", [
    ("LFSM-O", 50.5, 40.07), ("LFSM-O", 51.0, 30.96), ("LFSM-O", 51.5, 21.86),
    ("LFSM-U", 49.5, 51.00),
    ("FSM", 50.1, 43.71), ("FSM", 50.2, 41.89), ("FSM", 50.3, 40.22),
    ("FSM", 49.9, 47.36), ("FSM", 49.8, 49.18), ("FSM", 49.7, 51.14),
])
def test_p_expected_hvc(mode, f, p):
    assert pe(f, mode, P0_HVC) == p


def test_ramp_rate_8_percent_per_min():
    # 0.2 Hz/min with 5 % droop: 8 % of Pref per minute
    d = fq.p_expected(50.0, "FSM", P0)[0] - fq.p_expected(50.2, "FSM", P0)[0]
    assert round(d, 2) == 10.62


def test_lfsm_cases_have_no_fsm_response():
    assert fq.p_expected(50.1, "LFSM-O", P0)[0] == P0
    assert fq.p_expected(49.9, "LFSM-U", P0)[0] == P0


def test_clipping_flagged():
    p, clipped = fq.p_expected(47.0, "LFSM-U", 180.0)
    assert clipped and p == CFG["P_avail_MW"]


def test_insensitivity_full_droop_values():
    assert pe(50.012, "FSM-INS") == 132.08
    assert pe(49.988, "FSM-INS") == 133.36


def test_ppc_limit_flag():
    ppc = {"fsm_limit_frac_pn": 0.05, "lfsm_limitF_frac_pn": 0.80}  # 9.48 MW
    assert fq.ppc_limit_flags(49.8, "FSM", P0, ppc)                 # 10.62 MW change
    ppc["fsm_limit_frac_pn"] = 0.08                                 # 15.17 MW
    assert fq.ppc_limit_flags(49.7, "FSM", P0, ppc) == []


# ==============================================================================
# Synthetic exports
# ==============================================================================

DELAY, TAU = 0.5, 1.0


def synth(events, mode, p0=P0, t_end=None, response=None, hold=30.0):
    """
    POC export for a step sequence. events = [(t, f), ...] incl. the initial
    (0, 50.0). P responds to each step with a pure delay DELAY and a
    first-order lag TAU towards p_expected (superposition). Time base:
    10 ms, 0.5 ms for 0.1 s around each event, with a duplicate row at the
    event time (pre-event value first).
    """
    t_end = t_end or events[-1][0] + hold
    base = list(np.round(np.arange(-2.0, t_end + 1e-9, 0.01), 6))
    fine = []
    for te, _ in events[1:]:
        fine += list(np.round(np.arange(te - 0.05, te + 0.05, 0.0005), 6))
    t = np.array(sorted(set(base) | set(fine)))
    ev_t = [te for te, _ in events[1:]]
    t = np.sort(np.concatenate([t, np.array(ev_t)]))   # duplicates at events

    f = np.full(t.size, events[0][1])
    p = np.full(t.size, p0)
    seen = {}
    for i, ti in enumerate(t):
        for te, fe in events[1:]:
            post = ti > te or (abs(ti - te) < 1e-12 and seen.get(te, 0) >= 1)
            if abs(ti - te) < 1e-12:
                seen[te] = seen.get(te, 0) + 1
            if post:
                f[i] = fe
    levels = [events[0][1]] + [fe for _, fe in events[1:]]
    for k, (te, fe) in enumerate(events[1:]):
        dp = (fq.p_expected(fe, mode, p0)[0] - fq.p_expected(levels[k], mode, p0)[0])
        if response is not None:
            dp = response(k, dp)
        x = np.clip(t - te - DELAY, 0, None)
        p = p + dp * (1 - np.exp(-x / TAU))
    return t, f, p


def write_case(folder, name, ctype, events, t, f, p, bus1_only=False, extra=None):
    cols = {"time_s": t, "POC Frequency [Hz]": f,
            "POC Pos-Seq Voltage [pu]": np.ones_like(t),
            "POC Active Power [MW]": -p, "POC Reactive Power [Mvar]": np.zeros_like(t)}
    if not bus1_only:
        cols["POC Active Power, export positive [MW]"] = p
        cols["POC Reactive Power, export positive [Mvar]"] = np.zeros_like(t)
    fname = name.replace(" ", "_") + ".csv"
    pd.DataFrame(cols).to_csv(os.path.join(folder, fname), index=False)
    entry = {"name": name, "type": ctype, "variant": "Step", "csv_file": fname,
             "step_events": [[a, b] for a, b in events], "psetp": P0,
             "t_stop": float(t[-1]), "missing_columns": [], "export_status": "ok",
             "complete": True}
    entry.update(extra or {})
    return entry


def manifest(folder, entries):
    m = {"run_tag": "FREQ_synthetic",
         "ppc": {"P_mode": 2, "relative_FSM": 2,
                 "fsm_limit_frac_pn": 0.08, "lfsm_limitF_frac_pn": 0.80},
         "cases": entries}
    with open(os.path.join(folder, "export_manifest.json"), "w") as fh:
        json.dump(m, fh)
    return m


LFSMO_EV = [(0, 50.0), (30, 50.2), (60, 50.5), (90, 50.2), (120, 50.0)]


def eval_synth(tmp_path, events, ctype, mode, **kw):
    t, f, p = synth(events, mode, **{k: v for k, v in kw.items() if k in ("response",)})
    e = write_case(str(tmp_path), f"{ctype} Step", ctype, events, t, f, p,
                   bus1_only=kw.get("bus1_only", False))
    m = manifest(str(tmp_path), [e])
    return fq.evaluate_case(os.path.join(str(tmp_path), e["csv_file"]), e, m)


def test_step_delay_and_lag(tmp_path):
    res = eval_synth(tmp_path, LFSMO_EV, "LFSMO", "LFSM-O")
    s = res["steps"][1]                                 # 50.2 -> 50.5
    assert round(s["P_calc"], 2) == 116.79
    assert abs(s["error_pct"]) < 1e-6
    # Ti: delay + tau * -ln(1 - 0.02) ; Ts: delay + tau * ln(1 / 0.05)
    assert s["Ti_s"] == pytest.approx(DELAY - TAU * math.log(0.98), abs=2e-3)
    assert s["Ts_s"] == pytest.approx(DELAY + TAU * math.log(20), abs=2e-3)
    assert round(s["Ti_s"], 1) == 0.5 and round(s["Ts_s"], 1) == 3.5
    assert s["eff_droop_pct"] == pytest.approx(5.0, rel=1e-6)
    assert s["overshoot_pct"] == pytest.approx(0.0, abs=1e-9)
    assert not s["osc_growing"]
    assert s["flags"] == []
    assert res["P0"] == pytest.approx(P0)


def test_threshold_step_no_response(tmp_path):
    res = eval_synth(tmp_path, LFSMO_EV, "LFSMO", "LFSM-O")
    for s in (res["steps"][0], res["steps"][3]):        # 50.0->50.2, 50.2->50.0
        assert s["threshold_step"]
        assert s["Ti_s"] is None and s["Ts_s"] is None
        assert s["flags"] == []
    assert res["flags"] == []
    tab = fq.steps_sheet(res)
    assert tab.loc[0, "Ti [s]"] == "-" and tab.loc[0, "Ts [s]"] == "-"


def test_fsm_active_in_lfsm_case_flagged(tmp_path):
    ev = [(0, 50.0), (30, 50.2), (60, 50.0)]
    # the plant responds with FSM droop although the case is LFSM-O
    resp = lambda k, dp: -0.08 * P0 if k == 0 else 0.08 * P0
    res = eval_synth(tmp_path, ev, "LFSMO", "LFSM-O", response=resp)
    fl = res["steps"][0]["flags"]
    assert any("threshold not respected" in x for x in fl)
    assert any("FSM appears active" in x for x in fl)


def test_overview_has_no_blank_and_changes(tmp_path):
    ev = [(0, 50.0), (30, 50.1), (60, 50.2), (90, 50.3), (120, 50.2), (150, 50.0)]
    res = eval_synth(tmp_path, ev, "FSM", "FSM")
    ov = fq.overview_sheet(res)
    assert not ov.isna().any().any()
    assert (ov["From power [MW]"] != ov["To power [MW]"]).all()
    assert ov.loc[2, "Delta power [% Pref]"] == -4.0    # 50.2->50.3, Pref = 0.92 P0
    s = res["steps"][2]
    assert s["eff_droop_pct"] == pytest.approx(5.0, rel=1e-6)


def test_bus1_only_flipped_and_flagged(tmp_path):
    res = eval_synth(tmp_path, LFSMO_EV, "LFSMO", "LFSM-O", bus1_only=True)
    assert res["P0"] == pytest.approx(P0)
    assert any("multiplied by -1" in x for x in res["flags"])


def test_psetp_mismatch_flagged(tmp_path):
    t, f, p = synth(LFSMO_EV, "LFSM-O", p0=130.0)
    e = write_case(str(tmp_path), "LFSMO Step", "LFSMO", LFSMO_EV, t, f, p)
    m = manifest(str(tmp_path), [e])
    res = fq.evaluate_case(os.path.join(str(tmp_path), e["csv_file"]), e, m)
    assert res["P0"] == pytest.approx(130.0)
    assert any("differs from psetp" in x for x in res["flags"])


# ==============================================================================
# FSM insensitivity
# ==============================================================================

INS_EV = [(0, 50.000), (30, 50.010), (60, 50.012), (90, 50.000),
          (120, 49.990), (150, 49.988), (180, 50.000)]


def band_response(db_hz=0.01):
    """Discontinuous band: no response within +/-db, full droop outside."""
    levels = [f for _, f in INS_EV]

    def p_of(f):
        return P0 if abs(f - 50.0) <= db_hz + 1e-12 else fq.p_expected(f, "FSM", P0)[0]

    return lambda k, dp: p_of(levels[k + 1]) - p_of(levels[k])


def test_insensitivity_pass(tmp_path):
    res = eval_synth(tmp_path, INS_EV, "FSMINS", "FSM-INS", response=band_response())
    tests = [(s["f_to"], s["test"], s["pass"]) for s in res["insens"]]
    assert all(ok for _, _, ok in tests), tests
    assert [x[1] for x in tests] == ["no response allowed", "response present, right direction",
                                     "back at P0", "no response allowed",
                                     "response present, right direction", "back at P0"]
    assert round(res["steps"][1]["P_sim"], 2) == 132.08
    assert round(res["steps"][4]["P_sim"], 2) == 133.36
    crit = fq.criteria_sheet([res])
    assert crit.loc[crit["No."] == 7, "Verdict"].item() == "pass"


def test_insensitivity_response_at_10mHz_fails(tmp_path):
    res = eval_synth(tmp_path, INS_EV, "FSMINS", "FSM-INS", response=band_response(0.0))
    assert not res["insens"][0]["pass"]                 # +10 mHz responded


def test_insensitivity_blocked_response_fails(tmp_path):
    res = eval_synth(tmp_path, INS_EV, "FSMINS", "FSM-INS", response=band_response(0.02))
    assert not res["insens"][1]["pass"]                 # +12 mHz: nothing


# ==============================================================================
# Runner: manifest handling
# ==============================================================================

def test_run_missing_columns_and_ramp(tmp_path):
    d = str(tmp_path)
    t, f, p = synth(LFSMO_EV, "LFSM-O")
    good = write_case(d, "LFSMO Step", "LFSMO", LFSMO_EV, t, f, p)
    bad = write_case(d, "LFSMU Step", "LFSMU", [(0, 50.0), (30, 49.8)], t, f, p,
                     extra={"missing_columns": ["POC Frequency [Hz]"]})
    ramp = {"name": "LFSMO Ramp", "type": "LFSMO", "variant": "Ramp",
            "export_status": "ok", "csv_file": "LFSMO_Ramp.csv"}
    nores = dict(good, name="FSM Step", type="FSM", export_status="no_result_data")
    manifest(d, [good, bad, ramp, nores])
    out = os.path.join(d, "freq_results.xlsx")
    assert fq.run(d, out) == 0
    x = pd.read_excel(out, sheet_name=None)
    assert {"info", "overview_LFSM-O", "steps_LFSM-O", "criteria", "raw", "flags"} <= set(x)
    skipped = " ".join(map(str, x["info"]["Value"]))
    assert "missing columns" in skipped and "ramp evaluation not implemented" in skipped
    assert "no_result_data" in skipped
    crit = x["criteria"]
    assert crit.loc[crit["No."] == 7, "Verdict"].item() == "not demonstrated"
    assert crit.loc[crit["No."] == 8, "Verdict"].item() == "pending"


def test_run_without_manifest(tmp_path):
    assert fq.run(str(tmp_path)) == 1


# ==============================================================================
# Acceptance: sample LFSMO_Step.csv (PROVISIONAL Ti / Ts)
# ==============================================================================

SAMPLE = os.environ.get("FREQ_SAMPLE_CSV") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "LFSMO_Step.csv")

LFSMO_FULL = [(0, 50.0), (30, 50.2), (60, 50.5), (90, 51.0), (120, 51.5),
              (150, 51.0), (180, 50.5), (210, 50.2), (240, 50.0)]


@pytest.mark.skipif(not os.path.isfile(SAMPLE), reason="sample LFSMO_Step.csv not present")
def test_sample_lfsmo_step():
    entry = {"name": "LFSMO Step", "type": "LFSMO", "variant": "Step",
             "step_events": LFSMO_FULL, "psetp": P0, "missing_columns": [],
             "export_status": "ok", "complete": True}
    res = fq.evaluate_case(SAMPLE, entry, {"ppc": {"P_mode": 2, "relative_FSM": 2,
                                                   "fsm_limit_frac_pn": 0.08,
                                                   "lfsm_limitF_frac_pn": 0.80}})
    assert round(res["P0"], 2) == 132.72
    tab = fq.steps_sheet(res)
    assert list(tab["P-Calculated [MW]"]) == [132.72, 116.79, 90.25, 63.71,
                                              90.25, 116.79, 132.72, 132.72]
    assert list(tab["P-Simulated [MW]"]) == [132.72, 116.79, 90.25, 63.70,
                                             90.25, 116.79, 132.72, 132.72]
    assert all(abs(e) < 0.01 for e in tab["Error [%]"])
    # PROVISIONAL: confirm against the PowerFactory cursors
    assert list(tab["Ti [s]"]) == ["-", 0.9, 0.4, 0.4, 0.4, 0.4, 0.4, "-"]
    assert list(tab["Ts [s]"]) == ["-", 4.1, 3.6, 3.5, 3.6, 3.6, 3.7, "-"]
    assert all(s["flags"] == [] for s in res["steps"])
    assert res["flags"] == []
