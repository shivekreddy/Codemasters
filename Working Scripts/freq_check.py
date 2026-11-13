"""
LFSM-O / LFSM-U / FSM compliance checker for PowerFactory RMS exports
=====================================================================
Type D PPM (NNWZ PV park, POC Westermeerdijk 110 kV), TenneT NL.

Requirements: NBNL PGMCV 2.0 §4.2.1 (LFSM-O), §4.2.2 (LFSM-U), §4.2.3 (FSM);
Netcode elektriciteit Art. 3.13 sub 4, Art. 3.24 sub 2 and 3. Method as DNV
report 25-1670 Rev. 2, Tables 4-2 to 4-11 (accepted by TenneT).

Input: the flat "Freq Raw Data" folder written by gcc_export_freq.py:
    LFSMO_Step.csv, LFSMU_Step.csv, FSM_Step.csv, FSMINS_Step.csv, ...
    export_manifest.json   (event times, psetp, missing_columns)
Step event times come from the manifest; they are not detected in the data.
Ramp cases are not evaluated yet (skipped with a message).

Usage, from PowerFactory (ComPython):
    set PF_DATA_FOLDER (and optionally PF_OUT_FILE / PF_PLOTS) below, point a
    ComPython object at this script and execute it.

Usage, command line (also used by the tests):
    python freq_check.py <Freq Raw Data folder> [--out freq_results.xlsx] [--plots]

Reference samples are taken by index (duplicate timestamps at the events):
the last sample before an event and the last sample before the next event,
i.e. the PowerFactory cursor readings. Every assumption that triggers is
reported as a flag. Nothing is rounded until the table sheets.

Self-contained on purpose: importing frt_check.py from inside PowerFactory
would start the FRT check (it runs itself when PowerFactory is present).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

try:
    import numpy as np
    import pandas as pd
    import openpyxl  # noqa: F401  (needed to write freq_results.xlsx)
except ImportError as _exc:
    raise ImportError(
        f"freq_check.py needs pandas, numpy and openpyxl ({_exc}). Install them "
        f"into the Python that runs this script:\n"
        f'  "{os.path.join(sys.prefix, "python.exe")}" -m pip install pandas '
        f"numpy openpyxl matplotlib\n"
        f"(Python in use: {sys.prefix})") from _exc


# ==============================================================================
# SETTINGS FOR RUNNING FROM POWERFACTORY (ComPython): edit before executing
# ==============================================================================
# The "Freq Raw Data" folder written by gcc_export_freq.py (CSV files plus
# export_manifest.json).
PF_DATA_FOLDER = r"C:\Users\SHIRED\Downloads\Results Export\Freq Raw Data"

# Output workbook; None -> freq_results.xlsx in PF_DATA_FOLDER
PF_OUT_FILE = None

# True -> also write PNG plots (needs matplotlib) to a "plots" folder next to
# the workbook
PF_PLOTS = True


# ==============================================================================
# CONFIGURATION (all editable)
# ==============================================================================
CONFIG: dict = {
    "fn_Hz": 50.0,
    "Pmax_MW": 189.6,
    "droop": 0.05,
    "lfsm_o_threshold_Hz": 50.2,
    "lfsm_u_threshold_Hz": 49.8,
    "fsm_deadband_mHz": 0.0,
    "P_min_reg_MW": 0.0,             # minimum regulating level (never binds here)
    "P_avail_MW": 189.6,             # plant limit (limit_power)
    "pref_basis": "relative",        # NBNL 4.2.1-4.2.3: PPM Pref = actual power
    "error_limit_pct": 5.0,
    "ti_justify_s": 2.0,             # NBNL: initial delay > 2 s needs justification
    "ts_limit_s": {"LFSM-O": 20.0, "LFSM-U": 30.0, "FSM": 30.0},
    "ts_limit_all_s": 20.0,          # also reported against 20 s for all modes
    # Ti and Ts are not defined in 25-1670: project proposal, keep editable
    "ti_threshold_frac": 0.02,       # Ti: |P - P-before| >= 2 % of |dP expected| ...
    "ti_threshold_min_MW": 0.05,     # ... but at least 0.05 MW ...
    "persist_s": 0.2,                # ... and staying there for 0.2 s
    "ts_band_frac": 0.05,            # Ts: within P-simulated +/- 5 % of |dP expected|
    "ts_ignore_s": 0.05,             # excursions shorter than this are ignored
    "hold_min_s": 10.0,
    "settle_window_s": 2.0,          # P-simulated settled check before the next event
    "settle_frac": 0.005,            # max-min below 0.5 % of Pref
    "threshold_resp_frac": 0.005,    # threshold step: |dP| below 0.5 % of Pref
    "droop_tol_rel": 0.05,           # effective droop within 5 % +/- 5 % (assumption)
    "osc_window_s": 1.0,
    "osc_abs_tol_MW": 0.02,          # noise floor for "peak-to-peak grows"
    # pre-event checks (NBNL operating point, project values)
    "pre_window_s": 5.0,
    "pre_psetp_frac": 0.005,         # measured P0 vs psetp
    "pre_Q_frac_pmax": 0.01,
    "pre_U_tol_pu": 0.01,
    "pre_flat_frac": 0.002,          # max-min P below 0.2 % of P0
    "setpoints": {"U_POC_pu": 1.0, "Q_POC_Mvar": 0.0},
    # frequency signal checks against the manifest sequence
    "f_level_tol_Hz": 0.002,
    "f_flat_tol_Hz": 0.001,
    "step_search_s": 0.1,
    "bus_check_tol_MW": 0.01,        # bus1 vs bus2 power cross-check
    # FSM insensitivity (NBNL 4.2.3: <= 10 mHz; FSM deadband 0 mHz)
    "insensitivity_limit_mHz": 10.0,
    "insens_no_resp_frac": 0.10,     # "no response": |dP| < 10 % of the droop value
    "insens_present_frac": 0.50,     # "present": >= 50 % of the droop value, right sign
    # PPC settings: project specific, set them by hand for each project.
    # Not read from the model or the manifest. None = not configured: the
    # value is reported as such and that limit is not checked.
    # Example (NNWZ PPC): P_mode 2, relative_FSM 2, FSM limit 0.08 Pn,
    # LFSM limitF 0.80 Pn.
    "ppc": {"P_mode": None,                # reported in the output header
            "relative_FSM": None,          # reported in the output header
            "fsm_limit_frac_pn": None,     # FSM 'limit' as a fraction of Pn
            "lfsm_limitF_frac_pn": None},  # LFSM 'limitF' as a fraction of Pn
}

MODES = {"LFSMO": "LFSM-O", "LFSMU": "LFSM-U", "FSM": "FSM", "FSMINS": "FSM-INS"}
MODE_ORDER = ("LFSM-O", "LFSM-U", "FSM")
SOURCE = "NBNL PGMCV 2.0 §4.2.1-4.2.3; method as DNV 25-1670 Rev. 2"

# Column keywords (gcc_common.RECORD_VARS_FREQ labels)
KEYWORDS = {
    "f":     r"frequency",
    "U":     r"pos(itive)?-seq(uence)? voltage",
    "P_exp": r"(?<!re)active power, export positive",
    "Q_exp": r"reactive power, export positive",
    "P_load": r"(?<!re)active power(?!, export)",
    "Q_load": r"reactive power(?!, export)",
}


# ==============================================================================
# 1.  EXPECTED ACTIVE POWER
# ==============================================================================

def pref_values(P0: float, mode: str, cfg: dict = CONFIG) -> dict:
    """
    Pref per frequency region (NBNL 4.2.1-4.2.3, PPM: Pref = actual power when
    the threshold is reached). FSM band: P0. Beyond 50.2 / 49.8 Hz: the power
    at the threshold (P0 in the LFSM cases; 0.92 / 1.08 P0 in the FSM case).
    """
    k = cfg["fn_Hz"] * cfg["droop"]
    fo, fu, fn = cfg["lfsm_o_threshold_Hz"], cfg["lfsm_u_threshold_Hz"], cfg["fn_Hz"]
    if fsm_active(mode):
        return {"band": P0, "O": P0 * (1 - _fsm_df(fo, cfg) / k),
                "U": P0 * (1 - _fsm_df(fu, cfg) / k)}
    return {"band": P0, "O": P0, "U": P0}


def fsm_active(mode: str) -> bool:
    """LFSM-O/U are always active; FSM only in the FSM (and FSM-INS) cases."""
    return mode in ("FSM", "FSM-INS")


def _fsm_df(f: float, cfg: dict) -> float:
    """f - fn reduced by the FSM deadband (0 mHz for this project)."""
    db = cfg["fsm_deadband_mHz"] / 1000.0
    d = f - cfg["fn_Hz"]
    return math.copysign(max(abs(d) - db, 0.0), d)


def p_expected(f: float, mode: str, P0: float, cfg: dict = CONFIG) -> tuple[float, bool]:
    """
    P-calculated [MW] at frequency f and whether it was clipped to
    [P_min_reg, P_avail]. Droop dP/Pref = -(df/fn)/droop
    (NBNL 4.2.1-4.2.3; DNV 25-1670 Rev. 2 Tables 4-3, 4-6, 4-9).
    """
    k = cfg["fn_Hz"] * cfg["droop"]
    fo, fu = cfg["lfsm_o_threshold_Hz"], cfg["lfsm_u_threshold_Hz"]
    pr = pref_values(P0, mode, cfg)
    if f > fo:
        p = pr["O"] * (1 - (f - fo) / k)
    elif f < fu:
        p = pr["U"] * (1 + (fu - f) / k)
    elif fsm_active(mode):
        p = P0 * (1 - _fsm_df(f, cfg) / k)
    else:
        p = P0
    lo, hi = cfg["P_min_reg_MW"], cfg["P_avail_MW"]
    clipped = p < lo or p > hi
    return min(max(p, lo), hi), clipped


def ppc_limit_flags(f: float, mode: str, P0: float, ppc: dict,
                    cfg: dict = CONFIG) -> list[str]:
    """
    Flags when the PPC limits would clip the expected change: FSM 'limit'
    (fsm_limit_frac_pn x Pn) on the FSM part, LFSM 'limitF' on the LFSM part.
    """
    fo, fu = cfg["lfsm_o_threshold_Hz"], cfg["lfsm_u_threshold_Hz"]
    p_tot, _ = p_expected(f, mode, P0, cfg)
    p_band, _ = p_expected(min(max(f, fu), fo), mode, P0, cfg)
    out, pn = [], cfg["Pmax_MW"]
    fl, ll = ppc.get("fsm_limit_frac_pn"), ppc.get("lfsm_limitF_frac_pn")
    if fl is not None and abs(p_band - P0) > fl * pn + 1e-9:
        out.append(f"PPC FSM limit ({fl:g} Pn = {fl * pn:.2f} MW) clips the "
                   f"expected FSM change at {f:g} Hz ({p_band - P0:+.2f} MW)")
    if ll is not None and abs(p_tot - p_band) > ll * pn + 1e-9:
        out.append(f"PPC LFSM limitF ({ll:g} Pn = {ll * pn:.2f} MW) clips the "
                   f"expected LFSM change at {f:g} Hz ({p_tot - p_band:+.2f} MW)")
    return out


def step_pref(f_from: float, f_to: float, mode: str, P0: float,
              cfg: dict = CONFIG) -> float | None:
    """
    Pref for the effective droop of one step: the Pref of the region both
    frequencies lie in. None if the step crosses a threshold or lies in the
    band of an LFSM case (no droop response there).
    """
    fo, fu = cfg["lfsm_o_threshold_Hz"], cfg["lfsm_u_threshold_Hz"]
    pr = pref_values(P0, mode, cfg)
    lo, hi = min(f_from, f_to), max(f_from, f_to)
    if lo >= fo - 1e-9 and hi > fo:
        return pr["O"]
    if hi <= fu + 1e-9 and lo < fu:
        return pr["U"]
    if fu - 1e-9 <= lo and hi <= fo + 1e-9 and fsm_active(mode):
        return pr["band"]
    return None


# ==============================================================================
# 2.  DATA
# ==============================================================================

@dataclass
class CaseData:
    """One export CSV, P and Q export positive."""
    name: str
    mode: str
    t: np.ndarray
    f: np.ndarray
    U: np.ndarray
    P: np.ndarray
    Q: np.ndarray
    flags: list = field(default_factory=list)


def _find(headers: list[str], key: str) -> str | None:
    hits = [h for h in headers if re.search(KEYWORDS[key], h, re.I)]
    if len(hits) > 1:
        raise ValueError(f"Column keyword '{KEYWORDS[key]}' matches {hits}.")
    return hits[0] if hits else None


def load_case(path: str, name: str, mode: str, cfg: dict = CONFIG) -> CaseData:
    """
    Read one export CSV. P and Q from the export-positive (bus2) columns; the
    load-oriented bus1 columns are a cross-check, or flipped (with a flag)
    when bus2 is missing.
    """
    df = pd.read_csv(path)
    hdr = list(df.columns)
    if "time_s" not in hdr:
        raise ValueError(f"{os.path.basename(path)}: no 'time_s' column.")
    flags = []
    col = {k: _find(hdr, k) for k in KEYWORDS}
    num = lambda c: pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
    for k in ("f", "U"):
        if col[k] is None:
            raise ValueError(f"No column matching '{KEYWORDS[k]}' in {os.path.basename(path)}.")
    sig = {}
    for q in ("P", "Q"):
        ce, cl = col[f"{q}_exp"], col[f"{q}_load"]
        if ce is not None:
            sig[q] = num(ce)
            if cl is not None:
                d = np.nanmax(np.abs(sig[q] + num(cl)))
                if d > cfg["bus_check_tol_MW"]:
                    flags.append(f"{q}: bus2 and -bus1 differ by up to {d:.3f} "
                                 f"(export-positive column used)")
        elif cl is not None:
            sig[q] = -num(cl)
            flags.append(f"{q}: no export-positive column; load-oriented '{cl}' "
                         f"multiplied by -1")
        else:
            raise ValueError(f"No {q} column in {os.path.basename(path)}.")
    return CaseData(name, mode, num("time_s"), num(col["f"]), num(col["U"]),
                    sig["P"], sig["Q"], flags)


def index_at_time(t: np.ndarray, t_ref: float) -> int:
    """
    Reference sample for an event at t_ref: the FIRST row at exactly t_ref
    (with duplicate timestamps that is the pre-event state), else the last
    row before t_ref. Same rule as frt_check.py.
    """
    exact = np.nonzero(np.abs(t - t_ref) < 1e-9)[0]
    if exact.size:
        return int(exact[0])
    before = np.nonzero(t < t_ref)[0]
    if before.size == 0:
        raise ValueError(f"No sample at or before t = {t_ref} s.")
    return int(before[-1])


def window(t: np.ndarray, t0: float, t1: float) -> np.ndarray:
    """Boolean mask of samples with t0 <= t < t1."""
    return (t >= t0 - 1e-12) & (t < t1 - 1e-12)


# ==============================================================================
# 3.  TIMING (pure functions)
# ==============================================================================

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) index pairs (inclusive) of True runs."""
    if mask.size == 0:
        return []
    d = np.diff(np.concatenate(([0], mask.astype(int), [0])))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1))


def _cross(t0, x0, t1, x1, level):
    """Time where x reaches `level` between (t0, x0) and (t1, x1)."""
    if x1 == x0 or t1 == t0:
        return t1
    return t0 + (level - x0) / (x1 - x0) * (t1 - t0)


def initial_delay(t: np.ndarray, p: np.ndarray, i_e: int, i_end: int,
                  thr: float, persist_s: float) -> float | None:
    """
    Ti [s]: from t_e = t[i_e] to the first time |P - P-before| reaches `thr`
    and stays there for persist_s (interpolated). Project definition; not
    stated in DNV 25-1670 (Table 4-3 reports Ti).
    """
    tt, dp = t[i_e:i_end + 1], np.abs(p[i_e:i_end + 1] - p[i_e])
    for a, b in _runs(dp >= thr):
        if tt[b] - tt[a] >= persist_s or b == len(tt) - 1:
            tc = tt[a] if a == 0 else _cross(tt[a - 1], dp[a - 1], tt[a], dp[a], thr)
            return float(tc - tt[0])
    return None


def settling_time(t: np.ndarray, p: np.ndarray, i_e: int, i_end: int,
                  p_final: float, band: float, ignore_s: float) -> float | None:
    """
    Ts [s]: from t_e until P stays within p_final +/- band up to the next
    event. Excursions shorter than ignore_s are ignored. None if P is still
    outside the band at the end of the hold. Project definition.
    """
    tt, dev = t[i_e:i_end + 1], np.abs(p[i_e:i_end + 1] - p_final)
    long_runs = [(a, b) for a, b in _runs(dev > band)
                 if tt[b] - tt[a] >= ignore_s or b == len(tt) - 1]
    if not long_runs:
        return 0.0
    a, b = long_runs[-1]
    if b == len(tt) - 1:
        return None
    tc = _cross(tt[b], dev[b], tt[b + 1], dev[b + 1], band)
    return float(tc - tt[0])


def overshoot_pct(p: np.ndarray, p_final: float, dp_exp: float) -> float:
    """Max excursion beyond P-simulated in the direction of the change, % |dP expected|."""
    ex = np.max((p - p_final) * math.copysign(1.0, dp_exp))
    return float(max(ex, 0.0) / abs(dp_exp) * 100.0)


def oscillation_growing(t: np.ndarray, p: np.ndarray, t0: float, t1: float,
                        win_s: float, tol: float) -> tuple[bool, list[float]]:
    """
    Peak-to-peak of P in consecutive windows from t0 to t1: True if a later
    window exceeds the first by more than tol (possible undamped oscillation).
    """
    pp = []
    w = t0
    while w + win_s <= t1 + 1e-9:
        m = window(t, w, w + win_s)
        if m.sum() > 1:
            pp.append(float(np.ptp(p[m])))
        w += win_s
    grow = len(pp) >= 2 and max(pp[1:]) > pp[0] + tol
    return grow, pp


# ==============================================================================
# 4.  EVALUATION
# ==============================================================================

def manifest_events(entry: dict) -> list[tuple[float, float]]:
    """[(time_s, freq_hz), ...] from the manifest step_events, sorted."""
    ev = entry.get("step_events")
    if not ev:
        raise ValueError("No step_events in the manifest for this case.")
    out = []
    for e in ev:
        if isinstance(e, dict):
            out.append((float(e["time_s"]), float(e["freq_hz"])))
        else:
            out.append((float(e[0]), float(e[1])))
    return sorted(out)


def pre_event_checks(cd: CaseData, i_first: int, psetp: float | None,
                     cfg: dict = CONFIG) -> tuple[float, dict, list[str]]:
    """
    P0 = mean P over the pre_window_s before the first event (Pref is the
    actual power at the POC), plus the operating-point checks.
    """
    t, flags = cd.t, []
    t1 = t[i_first]
    m = window(t, t1 - cfg["pre_window_s"], t1)
    m[i_first] = True
    if t[0] > t1 - cfg["pre_window_s"] + 1e-9:
        flags.append(f"pre-event window shorter than {cfg['pre_window_s']:g} s "
                     f"(data starts at {t[0]:g} s)")
    P0 = float(np.mean(cd.P[m]))
    info = {"P0_MW": P0, "psetp_MW": psetp, "pre_t0": float(t[m][0]), "pre_t1": float(t1),
            "pre_P_ptp_MW": float(np.ptp(cd.P[m])),
            "pre_Q_max_dev_Mvar": float(np.max(np.abs(cd.Q[m] - cfg["setpoints"]["Q_POC_Mvar"]))),
            "pre_U_max_dev_pu": float(np.max(np.abs(cd.U[m] - cfg["setpoints"]["U_POC_pu"])))}
    if psetp:
        d = (P0 - psetp) / psetp
        info["P0_vs_psetp_pct"] = d * 100
        if abs(d) > cfg["pre_psetp_frac"]:
            flags.append(f"pre-event operating point: P0 {P0:.2f} MW differs from "
                         f"psetp {psetp:.2f} MW by {d * 100:+.2f} %")
    else:
        flags.append("no psetp in the manifest: P0 not cross-checked")
    if info["pre_Q_max_dev_Mvar"] > cfg["pre_Q_frac_pmax"] * cfg["Pmax_MW"]:
        flags.append(f"pre-event operating point: Q deviates up to "
                     f"{info['pre_Q_max_dev_Mvar']:.2f} Mvar from the setpoint")
    if info["pre_U_max_dev_pu"] > cfg["pre_U_tol_pu"]:
        flags.append(f"pre-event operating point: U1 deviates up to "
                     f"{info['pre_U_max_dev_pu']:.4f} pu from "
                     f"{cfg['setpoints']['U_POC_pu']} pu")
    if info["pre_P_ptp_MW"] > cfg["pre_flat_frac"] * P0:
        flags.append(f"pre-event not flat: P max-min {info['pre_P_ptp_MW']:.3f} MW "
                     f"(> {cfg['pre_flat_frac'] * 100:g} % of P0)")
    return P0, info, flags


def frequency_checks(cd: CaseData, t_ev: float, t_next: float, i_e: int,
                     f_from: float, f_to: float, cfg: dict = CONFIG) -> tuple[float, list[str]]:
    """Measured POC frequency against the manifest sequence (level, step, hold)."""
    t, f, flags = cd.t, cd.f, []
    if abs(f[i_e] - f_from) > cfg["f_level_tol_Hz"]:
        flags.append(f"frequency before the event {f[i_e]:.4f} Hz, expected {f_from:g} Hz")
    m = window(t, t_next - cfg["settle_window_s"], t_next)
    f_set = float(np.median(f[m])) if m.any() else float(f[i_e])
    if abs(f_set - f_to) > cfg["f_level_tol_Hz"]:
        flags.append(f"settled frequency {f_set:.4f} Hz, expected {f_to:g} Hz")
    if m.any() and np.ptp(f[m]) > cfg["f_flat_tol_Hz"]:
        flags.append(f"frequency not flat at the end of the hold "
                     f"(max-min {np.ptp(f[m]) * 1000:.2f} mHz)")
    ms = window(t, t_ev, t_ev + cfg["step_search_s"])
    if not ms.any() or np.max(np.abs(f[ms] - f[i_e])) < 0.5 * abs(f_to - f_from):
        flags.append(f"no frequency step found within {cfg['step_search_s']:g} s "
                     f"after t = {t_ev:g} s")
    if t_next - t_ev < cfg["hold_min_s"]:
        flags.append(f"hold {t_next - t_ev:.1f} s shorter than {cfg['hold_min_s']:g} s "
                     f"(P may not have settled)")
    return f_set, flags


def evaluate_step(cd: CaseData, k: int, t_ev: float, t_next: float | None,
                  f_from: float, f_to: float, P0: float, ppc: dict,
                  psetp: float | None, cfg: dict = CONFIG) -> dict:
    """
    One frequency step (NBNL 4.2.1-4.2.3; DNV 25-1670 Rev. 2 Tables 4-3, 4-6,
    4-9): P-calculated, P-simulated, error, effective droop, Ti, Ts,
    overshoot, oscillation; threshold steps checked for no response.
    """
    t, p, mode = cd.t, cd.P, cd.mode
    i_e = index_at_time(t, t_ev)
    i_n = index_at_time(t, t_next) if t_next is not None else len(t) - 1
    t_end = t_next if t_next is not None else float(t[-1])
    f_set, flags = frequency_checks(cd, t_ev, t_end, i_e, f_from, f_to, cfg)

    pc_from, c1 = p_expected(f_from, mode, P0, cfg)
    pc_to, c2 = p_expected(f_to, mode, P0, cfg)
    if c1 or c2:
        flags.append("P-calculated clipped to [P_min_reg, P_avail]")
    flags += ppc_limit_flags(f_to, mode, P0, ppc, cfg)
    pb, ps = float(p[i_e]), float(p[i_n])
    dpe, dps = pc_to - pc_from, ps - pb
    err = (ps - pc_to) / pc_to * 100.0 if pc_to else None
    if err is not None and abs(err) > cfg["error_limit_pct"]:
        flags.append(f"error {err:+.3f} % beyond +/-{cfg['error_limit_pct']:g} %")

    m2 = window(t, t_end - cfg["settle_window_s"], t_end)
    m2[i_n] = True
    ptp2, mean2 = float(np.ptp(p[m2])), float(np.mean(p[m2]))
    if ptp2 > cfg["settle_frac"] * P0:
        flags.append(f"not settled: P max-min {ptp2:.3f} MW over the last "
                     f"{cfg['settle_window_s']:g} s (2 s mean {mean2:.3f} MW)")

    r = {"case": cd.name, "mode": mode, "event": k, "t_event_s": t_ev,
         "t_next_s": t_next, "i_e": i_e, "i_next": i_n,
         "t_e_sample_s": float(t[i_e]), "t_sim_sample_s": float(t[i_n]),
         "f_from": f_from, "f_to": f_to, "df_Hz": f_to - f_from, "f_settled": f_set,
         "P_calc_from": pc_from, "P_calc": pc_to, "dP_expected": dpe,
         "P_calc_psetp": p_expected(f_to, mode, psetp, cfg)[0] if psetp else None,
         "P_before": pb, "P_sim": ps, "dP_sim": dps, "error_pct": err,
         "P_2s_ptp": ptp2, "P_2s_mean": mean2,
         "threshold_step": abs(dpe) < 1e-9, "eff_droop_pct": None,
         "Ti_s": None, "Ts_s": None, "overshoot_pct": None,
         "osc_pp": [], "osc_growing": False}

    if r["threshold_step"]:
        lim = cfg["threshold_resp_frac"] * P0
        if abs(dps) >= lim:
            flags.append(f"response inside the deadband / threshold not respected "
                         f"({dps:+.3f} MW, limit {lim:.3f} MW)")
            if not fsm_active(mode):
                flags.append("FSM appears active")
        r["threshold_ok"] = abs(dps) < lim
    else:
        pref = step_pref(f_from, f_to, mode, P0, cfg)
        r["Pref_MW"] = pref
        if pref is not None and dps != 0:
            r["eff_droop_pct"] = -((f_to - f_from) / cfg["fn_Hz"]) / (dps / pref) * 100.0
        else:
            flags.append("effective droop not evaluated (step crosses a threshold)")
        thr = max(cfg["ti_threshold_frac"] * abs(dpe), cfg["ti_threshold_min_MW"])
        r["Ti_s"] = initial_delay(t, p, i_e, i_n, thr, cfg["persist_s"])
        if r["Ti_s"] is None:
            flags.append("no response found for Ti")
        elif r["Ti_s"] > cfg["ti_justify_s"]:
            flags.append(f"Ti {r['Ti_s']:.2f} s > {cfg['ti_justify_s']:g} s: justification needed")
        band = cfg["ts_band_frac"] * abs(dpe)
        r["Ts_s"] = settling_time(t, p, i_e, i_n, ps, band, cfg["ts_ignore_s"])
        if r["Ts_s"] is None:
            flags.append("Ts: P not within the band at the end of the hold")
        r["overshoot_pct"] = overshoot_pct(p[i_e:i_n + 1], ps, dpe)
        t_osc = t[i_e] + (r["Ts_s"] if r["Ts_s"] is not None else 0.0)
        r["osc_growing"], r["osc_pp"] = oscillation_growing(
            t, p, t_osc, t_end, cfg["osc_window_s"], cfg["osc_abs_tol_MW"])
        if r["osc_growing"]:
            flags.append("possible undamped oscillation")
    r["flags"] = flags
    return r


def evaluate_insensitivity(steps: list[dict], P0: float, cfg: dict = CONFIG) -> list[dict]:
    """
    FSM insensitivity (NBNL 4.2.3: insensitivity <= 10 mHz, deadband 0 mHz).
    |f - fn| <= limit: no response allowed. |f - fn| > limit: response present
    (>= insens_present_frac of the droop value) and in the right direction.
    f = fn: back at P0. Not assessed with the +/-5 % error criterion.
    """
    lim = cfg["insensitivity_limit_mHz"] / 1000.0
    out = []
    for s in steps:
        d = s["f_to"] - cfg["fn_Hz"]
        p_full, _ = p_expected(s["f_to"], "FSM", P0, cfg)
        dp_full, dp = p_full - P0, s["P_sim"] - P0
        tol = max(cfg["ti_threshold_min_MW"], cfg["insens_no_resp_frac"] * abs(dp_full))
        if abs(d) < 1e-9:
            test, ok = "back at P0", abs(dp) < max(tol, cfg["ti_threshold_min_MW"])
        elif abs(d) <= lim + 1e-9:
            test, ok = "no response allowed", abs(dp) < tol
        else:
            test = "response present, right direction"
            ok = (math.copysign(1, dp) == math.copysign(1, dp_full)
                  and abs(dp) >= cfg["insens_present_frac"] * abs(dp_full))
        out.append({"f_from": s["f_from"], "f_to": s["f_to"], "df_from_fn_mHz": d * 1000,
                    "test": test, "P_full_droop": p_full, "P_sim": s["P_sim"],
                    "dP_sim_vs_P0": dp, "pct_of_full_droop": (dp / dp_full * 100) if dp_full else None,
                    "tolerance_MW": tol, "pass": bool(ok)})
    return out


def evaluate_case(path: str, entry: dict, manifest: dict, cfg: dict = CONFIG) -> dict:
    """Evaluate one Step case: pre-event checks and every step from the manifest."""
    name = entry["name"]
    mode = MODES.get(str(entry.get("type", "")).upper())
    if mode is None:
        raise ValueError(f"Unknown case type '{entry.get('type')}'.")
    if entry.get("missing_columns"):
        raise ValueError(f"export has missing columns {entry['missing_columns']} "
                         f"(written as zeros): re-export before evaluating.")
    cd = load_case(path, name, mode, cfg)
    flags = list(cd.flags)
    if entry.get("complete") is False:
        flags.append(f"run incomplete: ends at {entry.get('t_end_s')} s, "
                     f"t_stop {entry.get('t_stop')} s")
    ppc = dict(cfg["ppc"])

    ev = manifest_events(entry)
    f_level, events = ev[0][1], []
    for j, (t_ev, f_ev) in enumerate(ev):
        if j == 0 or t_ev <= cd.t[0] + 1e-9 or abs(f_ev - f_level) < 1e-9:
            if j > 0 and t_ev > cd.t[0] + 1e-9:
                flags.append(f"manifest event at {t_ev:g} s does not change the frequency")
            f_level = f_ev
            continue
        events.append((t_ev, f_level, f_ev))
        f_level = f_ev
    if not events:
        raise ValueError("no frequency change in the manifest step_events.")

    i_first = index_at_time(cd.t, events[0][0])
    psetp = entry.get("psetp")
    P0, pre, pflags = pre_event_checks(cd, i_first, psetp, cfg)
    flags += pflags

    steps = []
    for k, (t_ev, f_from, f_to) in enumerate(events):
        t_next = events[k + 1][0] if k + 1 < len(events) else None
        steps.append(evaluate_step(cd, k + 1, t_ev, t_next, f_from, f_to,
                                   P0, ppc, psetp, cfg))
    res = {"name": name, "mode": mode, "csv": os.path.basename(path), "P0": P0,
           "pre": pre, "steps": steps, "flags": flags, "ppc": ppc}
    if mode == "FSM-INS":
        res["insens"] = evaluate_insensitivity(steps, P0, cfg)
    return res


# ==============================================================================
# 5.  OUTPUT
# ==============================================================================

def _r(x, n):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


def overview_sheet(res: dict, cfg: dict = CONFIG) -> pd.DataFrame:
    """DNV 25-1670 Tables 4-2 / 4-5 / 4-8: no blank cells; From != To where a change is expected."""
    rows = []
    for s in res["steps"]:
        pref = s.get("Pref_MW") or res["P0"]
        rows.append({"From freq. [Hz]": _r(s["f_from"], 3), "To freq. [Hz]": _r(s["f_to"], 3),
                     "Delta freq. [mHz]": _r(s["df_Hz"] * 1000, 1),
                     "From power [MW]": _r(s["P_calc_from"], 2), "To power [MW]": _r(s["P_calc"], 2),
                     "Delta power [% Pref]": _r(s["dP_expected"] / pref * 100, 2)})
    return pd.DataFrame(rows)


def steps_sheet(res: dict) -> pd.DataFrame:
    """DNV 25-1670 Tables 4-3 / 4-6 / 4-9."""
    rows = []
    for s in res["steps"]:
        rows.append({"From freq. [Hz]": _r(s["f_from"], 3), "To freq. [Hz]": _r(s["f_to"], 3),
                     "P-Calculated [MW]": _r(s["P_calc"], 2), "P-Simulated [MW]": _r(s["P_sim"], 2),
                     "Error [%]": _r(s["error_pct"], 3),
                     "Ti [s]": _r(s["Ti_s"], 1), "Ts [s]": _r(s["Ts_s"], 1)})
    return pd.DataFrame(rows)


def insens_sheet(res: dict) -> pd.DataFrame:
    rows = []
    for s in res["insens"]:
        rows.append({"From freq. [Hz]": _r(s["f_from"], 3), "To freq. [Hz]": _r(s["f_to"], 3),
                     "f - fn [mHz]": _r(s["df_from_fn_mHz"], 1), "Test": s["test"],
                     "P full droop [MW]": _r(s["P_full_droop"], 2),
                     "P-Simulated [MW]": _r(s["P_sim"], 2),
                     "dP vs P0 [MW]": _r(s["dP_sim_vs_P0"], 3),
                     "% of full droop": _r(s["pct_of_full_droop"], 1),
                     "Result": "pass" if s["pass"] else "FAIL"})
    return pd.DataFrame(rows)


def _max(vals):
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def criteria_sheet(results: list[dict], cfg: dict = CONFIG) -> pd.DataFrame:
    """DNV 25-1670 Rev. 2 Table 4-11; maxima taken from the result tables."""
    by = {m: [s for r in results if r["mode"] == m for s in r["steps"]] for m in MODE_ORDER}
    resp = {m: [s for s in v if not s["threshold_step"]] for m, v in by.items()}
    ne = "not evaluated (no Step case)"
    rows = []

    def row(no, crit, req, vals, verdict, note=""):
        d = {"No.": no, "Criterion": crit, "Requirement": req}
        for m in MODE_ORDER:
            d[m] = vals.get(m, ne) if by[m] else ne
        d["Verdict"], d["Note"] = verdict, note
        rows.append(d)

    def verdict(oks):
        oks = [o for o in oks if o is not None]
        return "-" if not oks else ("pass" if all(oks) else "FAIL")

    # 1 thresholds
    v, oks = {}, []
    for m in ("LFSM-O", "LFSM-U"):
        th = [s for s in by[m] if s["threshold_step"]]
        bad = [s for s in th if not s.get("threshold_ok", True)]
        oks += [not bad] if by[m] else []
        v[m] = (f"{len(th)} threshold step(s), max |dP| "
                f"{_max([abs(s['dP_sim']) for s in th]) or 0:.3f} MW") if th else "no threshold step"
    v["FSM"] = "n/a (FSM active)"
    row(1, "Thresholds respected", "no response between 49.8 and 50.2 Hz in the LFSM cases",
        v, verdict(oks), f"limit {cfg['threshold_resp_frac'] * 100:g} % of Pref")
    # 2 droop
    v, oks = {}, []
    lo, hi = (cfg["droop"] * 100 * (1 - cfg["droop_tol_rel"]), cfg["droop"] * 100 * (1 + cfg["droop_tol_rel"]))
    for m in MODE_ORDER:
        d = [s["eff_droop_pct"] for s in resp[m] if s["eff_droop_pct"] is not None]
        if d:
            v[m] = f"{min(d):.3f} to {max(d):.3f} %"
            oks.append(lo <= min(d) and max(d) <= hi)
    row(2, "Response according to 5 % droop", "effective droop range",
        v, verdict(oks), f"accepted {lo:.2f}-{hi:.2f} % (tolerance is an assumption)")
    # 3 error
    v, oks = {}, []
    for m in MODE_ORDER:
        e = _max([abs(s["error_pct"]) for s in by[m] if s["error_pct"] is not None])
        if e is not None:
            v[m] = f"max |error| {e:.3f} %"
            oks.append(e <= cfg["error_limit_pct"])
    row(3, "Error", f"<= +/-{cfg['error_limit_pct']:g} %", v, verdict(oks))
    # 4 Ti
    v, oks = {}, []
    for m in MODE_ORDER:
        ti = _max([s["Ti_s"] for s in resp[m]])
        if ti is not None:
            v[m] = f"max Ti {ti:.1f} s (steps)"
            oks.append(ti <= cfg["ti_justify_s"])
    vt = verdict(oks)
    row(4, "Initial delay", f"as short as possible; > {cfg['ti_justify_s']:g} s needs justification",
        v, "justification needed" if vt == "FAIL" else vt,
        "Ti measured from the POC frequency (no PPC-measured frequency exported)")
    # 5 Ts
    v, oks = {}, []
    for m in MODE_ORDER:
        tss = [s["Ts_s"] for s in resp[m]]
        if resp[m]:
            if any(x is None for x in tss):
                v[m], ok = "not settled in at least one hold", False
            else:
                ts = max(tss)
                lim = cfg["ts_limit_s"][m]
                v[m] = (f"max Ts {ts:.1f} s (limit {lim:g} s; "
                        f"{'<=' if ts <= cfg['ts_limit_all_s'] else '>'} {cfg['ts_limit_all_s']:g} s)")
                ok = ts <= lim
            oks.append(ok)
    row(5, "Settling time", "LFSM-O 20 s, LFSM-U 30 s, FSM 30 s (also against 20 s)", v, verdict(oks))
    # 6 oscillation
    v, oks = {}, []
    for m in MODE_ORDER:
        if resp[m]:
            n = sum(s["osc_growing"] for s in resp[m])
            v[m] = "none" if n == 0 else f"{n} step(s) flagged"
            oks.append(n == 0)
    row(6, "No undamped oscillations", "P peak-to-peak does not grow after Ts", v, verdict(oks))
    # 7 insensitivity
    ins = [r for r in results if r["mode"] == "FSM-INS"]
    if ins:
        allp = all(s["pass"] for r in ins for s in r["insens"])
        txt = ("; ".join(f"{s['df_from_fn_mHz']:+.0f} mHz: {s['test']} "
                         f"({'pass' if s['pass'] else 'FAIL'})"
                         for s in ins[0]["insens"] if abs(s["df_from_fn_mHz"]) > 1e-6))
        rows.append({"No.": 7, "Criterion": "FSM insensitivity",
                     "Requirement": f"<= {cfg['insensitivity_limit_mHz']:g} mHz, FSM deadband 0 mHz",
                     "LFSM-O": "-", "LFSM-U": "-", "FSM": txt,
                     "Verdict": "pass" if allp else "FAIL",
                     "Note": "10 mHz is the controller insensitivity; FSM deadband 0 mHz. Test sequence is the "
                             "project's proposal (NBNL 4.2.3 sets none)."})
    else:
        rows.append({"No.": 7, "Criterion": "FSM insensitivity",
                     "Requirement": f"<= {cfg['insensitivity_limit_mHz']:g} mHz",
                     "LFSM-O": "-", "LFSM-U": "-", "FSM": "not demonstrated (no 10 mHz case)",
                     "Verdict": "not demonstrated", "Note": ""})
    rows.append({"No.": 8, "Criterion": "Model validation against compliance tests",
                 "Requirement": "simulation matches the on-site tests", "LFSM-O": "-",
                 "LFSM-U": "-", "FSM": "-", "Verdict": "pending", "Note": "text only"})
    return pd.DataFrame(rows)


def raw_sheet(results: list[dict]) -> pd.DataFrame:
    rows = []
    for r in results:
        for s in r["steps"]:
            d = {k: v for k, v in s.items() if k not in ("flags", "osc_pp")}
            d["osc_pp_MW"] = ", ".join(f"{x:.4f}" for x in s["osc_pp"])
            d["flags"] = " | ".join(s["flags"])
            rows.append(d)
    return pd.DataFrame(rows)


def info_sheet(results: list[dict], manifest: dict, skipped: list[str],
               cfg: dict = CONFIG) -> pd.DataFrame:
    rows = [("Tool", "freq_check.py"), ("Basis", SOURCE),
            ("Run", manifest.get("run_tag")), ("Exported", manifest.get("exported"))]
    if results:
        ppc = results[0]["ppc"]
        nc = "not configured (set CONFIG['ppc'])"
        txt = lambda k, unit="": nc if ppc.get(k) is None else f"{ppc[k]}{unit}"
        rows += [("PPC P_mode", txt("P_mode")),
                 ("PPC relative_FSM", txt("relative_FSM")),
                 ("PPC FSM limit", txt("fsm_limit_frac_pn", " x Pn")
                  + ("" if ppc.get("fsm_limit_frac_pn") is None else "; clipping flagged")),
                 ("PPC LFSM limitF", txt("lfsm_limitF_frac_pn", " x Pn")
                  + ("" if ppc.get("lfsm_limitF_frac_pn") is None else "; clipping flagged"))]
    for r in results:
        pre = r["pre"]
        rows.append((f"P0 {r['name']}", f"{r['P0']:.3f} MW measured "
                     f"({pre['pre_t0']:.2f}-{pre['pre_t1']:.2f} s); psetp {pre['psetp_MW']}"))
    rows += [("Pmax", f"{cfg['Pmax_MW']} MW"), ("Droop", f"{cfg['droop'] * 100:g} %"),
             ("Pref basis", "actual power at the threshold (PPM, NBNL 4.2.1-4.2.3)"),
             ("Ti definition", f"|P - P-before| >= max({cfg['ti_threshold_frac'] * 100:g} % |dP exp|, "
                               f"{cfg['ti_threshold_min_MW']} MW) for {cfg['persist_s']} s (project proposal)"),
             ("Ts definition", f"P within P-sim +/- {cfg['ts_band_frac'] * 100:g} % |dP exp|, "
                               f"excursions < {cfg['ts_ignore_s'] * 1000:g} ms ignored (project proposal)")]
    for s in skipped:
        rows.append(("Skipped", s))
    return pd.DataFrame(rows, columns=["Item", "Value"])


def flags_sheet(results: list[dict]) -> pd.DataFrame:
    rows = [(r["name"], "case", f) for r in results for f in r["flags"]]
    rows += [(r["name"], f"{s['f_from']:g} -> {s['f_to']:g} Hz", f)
             for r in results for s in r["steps"] for f in s["flags"]]
    return pd.DataFrame(rows, columns=["Case", "Event", "Flag"])


def write_excel(results: list[dict], path: str, manifest: dict, skipped: list[str],
                cfg: dict = CONFIG) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        info_sheet(results, manifest, skipped, cfg).to_excel(xl, sheet_name="info", index=False)
        for r in results:
            if r["mode"] == "FSM-INS":
                insens_sheet(r).to_excel(xl, sheet_name="fsm_insensitivity", index=False)
                continue
            overview_sheet(r, cfg).to_excel(xl, sheet_name=f"overview_{r['mode']}", index=False)
            steps_sheet(r).to_excel(xl, sheet_name=f"steps_{r['mode']}", index=False)
        criteria_sheet(results, cfg).to_excel(xl, sheet_name="criteria", index=False)
        raw_sheet(results).to_excel(xl, sheet_name="raw", index=False)
        flags_sheet(results).to_excel(xl, sheet_name="flags", index=False)


def plot_case(res: dict, path: str, out_dir: str, cfg: dict = CONFIG) -> None:
    """Overview (f and P against P-calculated) and one zoom per step (Ti, Ts)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cd = load_case(path, res["name"], res["mode"], cfg)
    os.makedirs(out_dir, exist_ok=True)
    tag = res["name"].replace(" ", "_")
    # staircase of P-calculated
    tc, pc = [cd.t[0]], [res["P0"]]
    for s in res["steps"]:
        tc += [s["t_event_s"], s["t_event_s"]]
        pc += [s["P_calc_from"], s["P_calc"]]
    tc.append(cd.t[-1]); pc.append(res["steps"][-1]["P_calc"])
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(cd.t, cd.P, label="P simulated (POC)")
    ax.plot(tc, pc, "--", label="P calculated")
    ax.set_xlabel("time [s]"); ax.set_ylabel("P [MW]"); ax.grid(True, alpha=0.3)
    ax2 = ax.twinx(); ax2.plot(cd.t, cd.f, color="grey", lw=0.8, label="f (POC)")
    ax2.set_ylabel("f [Hz]")
    ax.set_title(f"{res['name']}: P and f"); ax.legend(loc="lower left")
    fig.tight_layout(); fig.savefig(os.path.join(out_dir, f"{tag}_overview.png"), dpi=150)
    plt.close(fig)
    for s in res["steps"]:
        if s["threshold_step"]:
            continue
        te = s["t_e_sample_s"]
        m = window(cd.t, te - 1.0, te + 10.0)
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(cd.t[m] - te, cd.P[m], label="P simulated")
        ax.axhline(s["P_calc"], ls="--", color="k", lw=0.8, label="P calculated")
        band = cfg["ts_band_frac"] * abs(s["dP_expected"])
        for b in (-band, band):
            ax.axhline(s["P_sim"] + b, ls=":", color="grey", lw=0.8)
        if s["Ti_s"] is not None:
            ax.axvline(s["Ti_s"], ls="--", color="tab:red", label=f"Ti {s['Ti_s']:.1f} s")
        if s["Ts_s"] is not None:
            ax.axvline(s["Ts_s"], ls="--", color="tab:green", label=f"Ts {s['Ts_s']:.1f} s")
        ax.set_xlabel("time after t_e [s]"); ax.set_ylabel("P [MW]"); ax.grid(True, alpha=0.3)
        ax.set_title(f"{res['name']}: {s['f_from']:g} -> {s['f_to']:g} Hz"); ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"{tag}_step{s['event']}.png"), dpi=150)
        plt.close(fig)


def console_summary(res: dict) -> str:
    lines = [f"{res['name']}  ({res['mode']})  P0 {res['P0']:.2f} MW"]
    if res["mode"] == "FSM-INS":
        for s in res["insens"]:
            lines.append(f"   {s['f_from']:.3f} -> {s['f_to']:.3f} Hz  P-sim {s['P_sim']:.3f}  "
                         f"{s['test']}: {'pass' if s['pass'] else 'FAIL'}")
    else:
        for s in res["steps"]:
            ti = "-" if s["Ti_s"] is None else f"{s['Ti_s']:.1f}"
            ts = "-" if s["Ts_s"] is None else f"{s['Ts_s']:.1f}"
            e = "-" if s["error_pct"] is None else f"{s['error_pct']:+.3f}"
            lines.append(f"   {s['f_from']:.3f} -> {s['f_to']:.3f} Hz  P-calc {s['P_calc']:.2f}  "
                         f"P-sim {s['P_sim']:.2f}  err {e} %  Ti {ti} s  Ts {ts} s"
                         + (f"   [{len(s['flags'])} flag(s)]" if s["flags"] else ""))
    for f in res["flags"]:
        lines.append(f"   FLAG: {f}")
    for s in res["steps"]:
        for f in s["flags"]:
            lines.append(f"   FLAG {s['f_from']:g}->{s['f_to']:g} Hz: {f}")
    return "\n".join(lines)


# ==============================================================================
# 6.  RUNNERS
# ==============================================================================

_say = print
_warn = print


def run(folder: str, out: str | None = None, plots: bool = False,
        cfg: dict = CONFIG) -> int:
    """Evaluate every Step case in the manifest of `folder`, write `out`. Returns 0 / 1."""
    mpath = os.path.join(folder, "export_manifest.json")
    if not os.path.isfile(mpath):
        _warn(f"No export_manifest.json in {folder} (run gcc_export_freq.py first).")
        return 1
    with open(mpath, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    out = out or os.path.join(folder, "freq_results.xlsx")
    results, skipped = [], []
    for entry in manifest.get("cases", []):
        name = entry.get("name", "?")
        if entry.get("variant") == "Ramp" or "ramp" in name.lower():
            skipped.append(f"{name}: ramp evaluation not implemented yet")
            _say(f"{name}: skipped (ramp evaluation not implemented yet)")
            continue
        if entry.get("export_status") != "ok":
            skipped.append(f"{name}: export status '{entry.get('export_status')}'")
            _warn(f"{name}: skipped (export status '{entry.get('export_status')}')")
            continue
        path = os.path.join(folder, entry.get("csv_file") or name.replace(" ", "_") + ".csv")
        try:
            res = evaluate_case(path, entry, manifest, cfg)
        except Exception as exc:                 # one case never stops the others
            skipped.append(f"{name}: ERROR {exc}")
            _warn(f"ERROR {name}: {exc}")
            continue
        results.append(res)
        _say(console_summary(res))
        if plots:
            try:
                plot_case(res, path, os.path.join(os.path.dirname(os.path.abspath(out)), "plots"), cfg)
            except Exception as exc:
                _warn(f"   plot failed: {exc}")
    if not results:
        _warn("No case evaluated.")
        return 1
    write_excel(results, out, manifest, skipped, cfg)
    _say(f"\nWritten: {out}  ({len(results)} case(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    ap = argparse.ArgumentParser(description="LFSM-O / LFSM-U / FSM compliance checker")
    ap.add_argument("folder", help="'Freq Raw Data' folder (CSV files + export_manifest.json)")
    ap.add_argument("--out", default=None, help="default: freq_results.xlsx in the folder")
    ap.add_argument("--plots", action="store_true")
    a = ap.parse_args(argv)
    return run(a.folder, a.out, a.plots)


def powerfactory_app():
    """The PowerFactory application when running inside PowerFactory, else None."""
    try:
        import powerfactory
        return powerfactory.GetApplication()
    except Exception:
        return None


def run_in_powerfactory(app) -> None:
    """ComPython entry point: PF_ settings, output to the PF output window."""
    global _say, _warn
    _say, _warn = app.PrintPlain, app.PrintWarn
    app.ClearOutputWindow()
    out = PF_OUT_FILE or os.path.join(PF_DATA_FOLDER, "freq_results.xlsx")
    app.PrintPlain("=" * 72)
    app.PrintPlain("  LFSM-O / LFSM-U / FSM compliance check")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Data   : {PF_DATA_FOLDER}")
    app.PrintPlain(f"  Output : {out}" + ("   (+ plots)" if PF_PLOTS else ""))
    app.PrintPlain(f"  Python : {sys.prefix}\n")
    if not os.path.isdir(PF_DATA_FOLDER):
        app.PrintError(f"Data folder not found:\n  {PF_DATA_FOLDER}\n"
                       "Set PF_DATA_FOLDER at the top of freq_check.py.")
        return
    try:
        run(PF_DATA_FOLDER, out, PF_PLOTS)
    except PermissionError as exc:
        app.PrintError(f"Could not write the output: is {os.path.basename(out)} "
                       f"open in Excel? ({exc})")
    except Exception as exc:
        app.PrintError(f"Frequency compliance check aborted: {exc}")
        raise


# Run from PowerFactory (ComPython) with the PF_ settings, or from the command
# line with arguments. Importing the module (e.g. from the tests) runs nothing.
_PF_APP = powerfactory_app()
if _PF_APP is not None:
    run_in_powerfactory(_PF_APP)
elif __name__ == "__main__":
    sys.exit(main())
