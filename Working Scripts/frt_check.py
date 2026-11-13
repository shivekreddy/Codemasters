"""
FRT / FFCI compliance checker for PowerFactory RMS exports
==========================================================
Type D PPM (NNWZ PV park, POC Westermeerdijk 110 kV), TenneT NL.

Requirements: NBNL PGMCV 2.0 §4.2.14 (FRT), §4.2.16 (post-fault active power
recovery), §4.2.18 (fast fault current injection, FFCI). Method as DNV report
25-1670 Rev. 2 (accepted by TenneT).

Input: one folder per case, e.g. "FRT 1PH 0.05 Q0/", holding POC.csv,
"GU Terminal 1.csv" (HVC) and "GU Terminal 2.csv" (Novar), as written by
gcc_export_raw_results.py: time_s + 10 signals per file.

Usage — from PowerFactory (ComPython):
    set PF_CASES_FOLDER (and optionally the other PF_ settings) below, point a
    ComPython object at this script and execute it. Output goes to the
    PowerFactory output window, results.xlsx into the cases folder.

Usage — command line (also used by the tests):
    python frt_check.py <folder with case folders> [--out results.xlsx]
                        [--plots] [--tf 10.0] [--tc 10.25]

Reference points are taken at exact samples (by index): the last sample
before the fault-inception jump and the last sample before the clearance
jump — the PowerFactory cursor readings. Short transients at the events are
never assessed. Every assumption that triggers is reported as a flag.
Nothing is rounded until the table sheets.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass, field

try:
    import numpy as np
    import pandas as pd
    import openpyxl  # noqa: F401  (needed to write results.xlsx)
except ImportError as _exc:
    raise ImportError(
        f"frt_check.py needs pandas, numpy and openpyxl ({_exc}). Install them "
        f"into the Python that runs this script:\n"
        f'  "{os.path.join(sys.prefix, "python.exe")}" -m pip install pandas '
        f"numpy openpyxl matplotlib pyyaml\n"
        f"(Python in use: {sys.prefix})") from _exc


# ==============================================================================
# SETTINGS FOR RUNNING FROM POWERFACTORY (ComPython) — edit before executing
# ==============================================================================
# Folder holding the case folders ("FRT 1PH 0.05 Q0", ...), as written by
# gcc_export_raw_results.py. A single case folder works too.
PF_CASES_FOLDER = r"C:\Users\SHIRED\OneDrive - DNV\Projects\GCC Script Development\Codemasters-1\FRT_2026-11-11_13-56-23 (PPC Enabled)"

# Excel output. None = results.xlsx inside PF_CASES_FOLDER
PF_OUT_FILE = None

# Ti,90 plots per case (PNG, in a "plots" folder next to the Excel file)
PF_PLOTS = True

# Event time overrides [s]. None = detect from the POC phase voltages
PF_TF = None
PF_TC = None


# ==============================================================================
# CONFIGURATION — all values editable
# ==============================================================================
CONFIG: dict = {
    "pmax_MW": 189.6,
    "poc": {
        "u_nom_kV": 110,
        "i_base_A": 995,             # Pmax / (sqrt(3) * 110 kV); report base
        "i_scale": 1.0,              # exported POC pu already on ~1000 A base
    },
    "gu": {
        "u_nom_V": 800,
        "i_report_base_A": 247,      # inverter Iq limit = 1.0 pu in the report
        "i_scale": 0.93458,          # = 1/1.07: exported GU pu (~231 A) -> 247 A base
        "iq_cap_export_pu": 1.07,    # Iq limit in exported pu
        "i_total_cap_export_pu": 1.08,   # phase current limit in exported pu
    },
    "gu_names": {"GU1": "HVC", "GU2": "Novar"},
    "k_set": 5.0,
    "k_tol": 0.10,                   # k within k_set +/- 10 % = "per setting"
    "ffci_min_residual_pu": 0.15,    # NBNL 4.2.18
    "ti90_limit_ms": 30,
    "ti90_limit_incl_seq_ms": 50,    # incl. <= 20 ms sequence calculation
    "ts_limit_ms": 60,
    "ts_limit_incl_seq_ms": 80,
    "p_recovery_frac": 0.90,
    "p_recovery_limit_s": 10.0,
    "p_recovery_hold_s": 1.0,        # P must stay above the threshold this long
    # Case durations, DNV 25-1670 Rev. 2 §5.3 (accepted by TenneT)
    "fault_duration_ms": {5: 250, 20: 900, 40: 1550, 60: 2190, 85: 3000},
    "fault_duration_tol_ms": 5,
    "retained_tol_pu": 0.02,
    "setpoints": {"P_MW": 176.33, "Q0_Mvar": 0.0, "Qmax_Mvar": 62.57,
                  "Qmin_Mvar": -62.57},
    "op_point_tol_frac_pmax": 0.01,
    # Inverter protection (GU terminal). Trip acts on L-L voltage; the exports
    # only hold L-G voltages, so the lowest/highest L-G voltage is the proxy.
    "ov1_pu": 1.25, "ov1_ms": 100,
    "uv1_pu": 0.80, "uv1_trip_ms": 2400,
    "uv2_pu": 0.30, "uv2_trip_ms": 800,
    "spike_ignore_ms": 2,            # excursions shorter than this are spikes
    # Event detection
    "event_jump_pu": 0.02,
    "clearance_search_delay_s": 0.020,
    # Settled checks (warn only)
    "prefault_window_s": 1.0,
    "prefault_flat_frac": 0.005,
    "fault_window_s": 0.020,
    "fault_settled_pu": 0.005,
    # Ti,90 / Ts
    "ti90_hold_ms": 10,
    "small_step_pu": 0.2,            # |dI| below this: absolute band
    "small_step_band_pu": 0.02,
    "no_step_pu": 0.02,              # |dI| below this: "n/a (no step)"
    # Post-fault
    "final_window_s": 1.0,           # runs end at 15 s: 3 s fault clears at 13 s
    "final_state_frac": 0.01,
    "damping_start_s": 0.5,
    "damping_window_s": 1.0,
    "damping_abs_tol_MW": 0.2,       # noise floor for "peak-to-peak increased"
    "p_loss_persist_s": 0.5,         # P below the recovery threshold this long after
                                     # recovering = active power lost
    "complete_after_tc_s": 1.5,      # run must reach t_c + 1.5 s (15 s stop, 3 s fault: t_c 13 s)
    "consistency_spread_pu": 0.005,
    # Grid code YAML compared against fault_duration_ms (warning only)
    "grid_code_yaml": "netherlands_NC-RfG-TypeD.yaml",
}

FILES = {"POC": "POC.csv", "GU1": "GU Terminal 1.csv", "GU2": "GU Terminal 2.csv"}

# Signal keys in export order, with the header keyword(s) that identify them
SIGNALS = ["Ua", "Ub", "Uc", "U1", "U2", "P", "Q", "Ip1", "Iq1", "Iq2"]
KEYWORDS = {
    "Ua":  r"voltage a\b",
    "Ub":  r"voltage b\b",
    "Uc":  r"voltage c\b",
    "U1":  r"positive-sequence voltage",
    "U2":  r"negative-sequence voltage",
    "P":   r"(?<!re)active power",
    "Q":   r"reactive power",
    "Ip1": r"positive-sequence active current",
    "Iq1": r"positive-sequence reactive current",
    "Iq2": r"negative-sequence reactive current",
}
# POC quantities are load oriented: flip to generator orientation
POC_FLIP = ("P", "Q", "Ip1", "Iq1", "Iq2")
CURRENTS = ("Ip1", "Iq1", "Iq2")

CASE_RE = re.compile(r"FRT[ _]+(\d)PH[ _]+([0-9.]+)[ _]+(Q0|Qmax|Qmin)\s*$",
                     re.IGNORECASE)


# ==============================================================================
# 1.  DATA
# ==============================================================================

@dataclass
class Location:
    """One export file, sign-corrected to generator orientation.

    exported : signals after the POC sign flip, before any base scaling
    scaled   : currents converted to the report base (GU: 247 A); voltages,
               P and Q identical to `exported`
    """
    key: str                       # POC / GU1 / GU2
    name: str                      # POC / HVC / Novar
    t: np.ndarray
    exported: dict
    scaled: dict
    flags: list = field(default_factory=list)


@dataclass
class Case:
    folder: str
    name: str                      # report name, e.g. 1P-5U-Q0
    fault: str                     # 3P / 2P / 1P
    retained_pct: int              # 5 / 20 / 40 / 60 / 85
    q_label: str                   # Q0 / Qmax / Qmin


def parse_case_name(folder_name: str) -> Case:
    """'FRT 1PH 0.05 Q0' -> Case(name='1P-5U-Q0', ...). Spaces or underscores."""
    m = CASE_RE.search(os.path.basename(folder_name.rstrip("/\\")))
    if not m:
        raise ValueError(f"Folder name '{folder_name}' is not 'FRT <n>PH <uret> <Q>'.")
    n_ph, uret, q = m.groups()
    pct = int(round(float(uret) * 100))
    q = {"q0": "Q0", "qmax": "Qmax", "qmin": "Qmin"}[q.lower()]
    return Case(folder=folder_name, name=f"{n_ph}P-{pct}U-{q}",
                fault=f"{n_ph}P", retained_pct=pct, q_label=q)


def map_columns(headers: list[str]) -> tuple[dict, list[str]]:
    """
    Map signal keys to column headers by keyword. Falls back to column
    position (export order) only if a keyword does not match exactly one
    column, and says so in the returned flags. Raises if neither works.
    """
    flags, mapping = [], {}
    signal_cols = [h for h in headers if h.strip().lower() != "time_s"]
    for i, key in enumerate(SIGNALS):
        hits = [h for h in signal_cols if re.search(KEYWORDS[key], h, re.I)]
        if len(hits) == 1:
            mapping[key] = hits[0]
        elif len(signal_cols) == len(SIGNALS):
            mapping[key] = signal_cols[i]
            flags.append(f"{key} mapped by column position ('{signal_cols[i]}')")
        else:
            raise ValueError(
                f"Signal '{key}' not found (keyword '{KEYWORDS[key]}', "
                f"{len(hits)} matches) and the file does not have the "
                f"{len(SIGNALS)} signal columns needed for a position fallback.")
    return mapping, flags


def load_location(path: str, key: str, cfg: dict = CONFIG) -> Location:
    """Read one export CSV, map columns, flip POC signs, scale currents."""
    df = pd.read_csv(path)
    if "time_s" not in df.columns:
        raise ValueError(f"{os.path.basename(path)}: no 'time_s' column.")
    mapping, flags = map_columns(list(df.columns))
    t = df["time_s"].to_numpy(dtype=float)

    exported = {}
    for sig, col in mapping.items():
        v = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
        if key == "POC" and sig in POC_FLIP:
            v = -v
        exported[sig] = v
        if np.all(np.isnan(v)) or np.nanmax(np.abs(v)) == 0.0:
            flags.append(f"{sig} is all zero/NaN")

    scale = cfg["poc"]["i_scale"] if key == "POC" else cfg["gu"]["i_scale"]
    scaled = {s: (v * scale if s in CURRENTS else v) for s, v in exported.items()}
    name = "POC" if key == "POC" else cfg["gu_names"][key]
    return Location(key, name, t, exported, scaled, flags)


# ==============================================================================
# 2.  EVENTS AND REFERENCE SAMPLES
# ==============================================================================

def detect_inception(t: np.ndarray, phases: list[np.ndarray],
                     jump: float = 0.02) -> int:
    """
    Index of the fault-inception reference sample: the LAST sample before the
    first sample where any phase L-G voltage changes by more than `jump` pu
    from the previous sample. (DNV 25-1670 Rev. 2: cursor on the inception line.)
    """
    d = np.max(np.abs(np.diff(np.vstack(phases), axis=1)), axis=0)
    hits = np.nonzero(d > jump)[0]
    if hits.size == 0:
        raise ValueError("No fault inception found (no POC phase voltage jump).")
    return int(hits[0])            # diff index i = jump from sample i to i+1


def detect_clearance(t: np.ndarray, phases: list[np.ndarray], i_f: int,
                     jump: float = 0.02, delay_s: float = 0.020) -> int:
    """
    Index of the clearance reference sample: after t_f + delay, the LAST
    sample before the first sample where the minimum phase L-G voltage rises
    by more than `jump` pu from the previous sample.
    """
    umin = np.min(np.vstack(phases), axis=0)
    start = int(np.searchsorted(t, t[i_f] + delay_s, side="left"))
    rise = np.diff(umin[start:])
    hits = np.nonzero(rise > jump)[0]
    if hits.size == 0:
        raise ValueError("No fault clearance found (no rise of the minimum "
                         "POC phase voltage).")
    return int(start + hits[0])


def index_at_time(t: np.ndarray, t_ref: float) -> int:
    """
    Sample index for an overridden event time: the FIRST row at exactly t_ref
    (with duplicate timestamps that is the pre-event state), else the last
    row before t_ref.
    """
    exact = np.nonzero(np.abs(t - t_ref) < 1e-9)[0]
    if exact.size:
        return int(exact[0])
    before = np.nonzero(t < t_ref)[0]
    if before.size == 0:
        raise ValueError(f"No sample at or before t = {t_ref} s.")
    return int(before[-1])


def window(t: np.ndarray, t0: float, t1: float) -> np.ndarray:
    """Boolean mask of samples with t0 <= t <= t1."""
    return (t >= t0 - 1e-12) & (t <= t1 + 1e-12)


# ==============================================================================
# 3.  CALCULATIONS (pure functions)
# ==============================================================================

def k_factor(i_pre: float, i_fault: float, u_pre: float, u_fault: float,
             negative: bool = False) -> float:
    """
    Reactive current gain (NBNL 4.2.18; method as DNV 25-1670 Rev. 2):
      positive sequence: k1 = (Iq1,f - Iq1,pre) / (U1,pre - U1,f)
      negative sequence: k2 = -(Iq2,f - Iq2,pre) / (U2,f - U2,pre)
    (a correct inductive negative-sequence response gives k2 > 0)
    """
    if negative:
        du = u_fault - u_pre
        return -(i_fault - i_pre) / du if du != 0 else float("nan")
    du = u_pre - u_fault
    return (i_fault - i_pre) / du if du != 0 else float("nan")


def worst_phase_current(ip1: float, iq1: float, iq2: float) -> float:
    """Upper-bound phase current estimate: sqrt(Ip1^2 + Iq1^2) + |Iq2|."""
    return math.hypot(ip1, iq1) + abs(iq2)


def k_verdict(k: float, limited: bool, k_set: float, k_tol: float) -> str:
    """'per setting' within k_set*(1 +/- k_tol); below: 'limited*' if the current
    limit is reached, else 'NON-COMPLIANT'; above: 'above setting' (review)."""
    if not np.isfinite(k):
        return "n/a"
    lo, hi = k_set * (1 - k_tol), k_set * (1 + k_tol)
    if lo <= k <= hi:
        return "per setting"
    if k > hi:
        return "above setting"
    return "limited*" if limited else "NON-COMPLIANT"


def u_ll_faulted_2p(u1: float, u2: float, ua_unfaulted: float) -> float:
    """
    Faulted line-to-line voltage for a 2P fault without phasors (no zero
    sequence, so |V1 + V2| = |Va| of the unfaulted phase):
      |V1 - V2| = sqrt(2 U1^2 + 2 U2^2 - Ua^2)    (line pu)
    """
    return math.sqrt(max(2 * u1 ** 2 + 2 * u2 ** 2 - ua_unfaulted ** 2, 0.0))


def _interp_crossing(t0, x0, t1, x1, level):
    """Time where x crosses `level` between (t0, x0) and (t1, x1)."""
    if t1 == t0 or x1 == x0:
        return t1
    return t0 + (level - x0) / (x1 - x0) * (t1 - t0)


def rise_and_settling(t: np.ndarray, i: np.ndarray, i_f: int, i_c: int,
                      cfg: dict = CONFIG) -> dict:
    """
    Rise time Ti,90 and settling time Ts of a current step during the fault
    (NBNL 4.2.18; method as DNV 25-1670 Rev. 2). Uses samples t_f < t <= t_c.

    x(t) = (I(t) - I,pre) / dI,  dI = I,fault - I,pre
    Ti,90: first time x >= 0.9 and stays >= 0.9 for >= ti90_hold_ms,
           interpolated, minus t_f.
    Ts:    time after t_f from which x stays inside the band until t_c,
           excursions shorter than spike_ignore_ms ignored. Band 0.9..1.1;
           for |dI| < small_step_pu an absolute +/- small_step_band_pu.
    """
    out = {"ti90_ms": None, "ts_ms": None, "notes": []}
    i_pre, i_flt = i[i_f], i[i_c]
    d_i = i_flt - i_pre
    if abs(d_i) < cfg["no_step_pu"]:
        out["notes"].append("n/a (no step)")
        return out

    tt, x = t[i_f:i_c + 1], (i[i_f:i_c + 1] - i_pre) / d_i   # tt[0] = t_f, x[0] = 0
    t_f = tt[0]
    if abs(d_i) < cfg["small_step_pu"]:
        half = cfg["small_step_band_pu"] / abs(d_i)
        out["notes"].append(f"small step |dI| = {abs(d_i):.3f} pu: absolute "
                            f"+/-{cfg['small_step_band_pu']} pu band used")
    else:
        half = 0.1
    lo, hi = 1.0 - half, 1.0 + half

    # --- Ti,90 ---------------------------------------------------------------
    hold = cfg["ti90_hold_ms"] / 1000.0
    above = x >= 0.9
    for j in range(1, len(tt)):
        if not above[j]:
            continue
        held = window(tt, tt[j], tt[j] + hold)
        if np.all(above[held]):
            t90 = _interp_crossing(tt[j - 1], x[j - 1], tt[j], x[j], 0.9) \
                if not above[j - 1] else tt[j]
            out["ti90_ms"] = (t90 - t_f) * 1000.0
            break

    # --- Ts --------------------------------------------------------------------
    inside = (x >= lo) & (x <= hi)
    inside[0] = False                        # x(t_f) = 0: the rise starts out of band
    spike = cfg["spike_ignore_ms"] / 1000.0

    # out-of-band runs: samples a..b-1 out, b back in (b = len -> never back in)
    runs, j = [], 0
    while j < len(tt):
        if inside[j]:
            j += 1
            continue
        k = j
        while k < len(tt) and not inside[k]:
            k += 1
        runs.append([j, k])
        j = k
    # in-band blips shorter than `spike` do not split an out-of-band run
    merged = [runs[0]]
    for a, b in runs[1:]:
        if tt[a] - tt[merged[-1][1]] < spike:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    # out-of-band excursions shorter than `spike` are ignored (the rise never is)
    significant = [r for n, r in enumerate(merged)
                   if n == 0 or r[1] >= len(tt) or tt[r[1]] - tt[r[0]] >= spike]
    last = significant[-1]
    if last[1] >= len(tt):
        out["notes"].append("does not settle in the band before clearance")
        return out
    b = last[1]                              # first sample back in band for good
    from_below = x[b - 1] < lo
    if last is merged[0] and from_below:
        # rose straight into the band and never left it: Ts = Ti,90
        out["ts_ms"] = out["ti90_ms"]
        out["notes"].append("within band after rise")
    else:
        # settles after an excursion, or comes down into the band after an
        # overshoot: Ts = interpolated entry into the band
        t_in = _interp_crossing(tt[b - 1], x[b - 1], tt[b], x[b],
                                lo if from_below else hi)
        out["ts_ms"] = (t_in - t_f) * 1000.0
    return out


def p_recovery_time(t: np.ndarray, p: np.ndarray, i_c: int, p_pre: float,
                    cfg: dict = CONFIG) -> float | None:
    """
    Post-fault active power recovery (NBNL 4.2.16): first time after t_c at
    which P >= frac * P,pre and stays above for at least p_recovery_hold_s.
    Returns seconds after t_c, or None if not recovered.
    """
    thr, hold, t_c = cfg["p_recovery_frac"] * p_pre, cfg["p_recovery_hold_s"], t[i_c]
    ok = p >= thr
    for j in range(i_c + 1, len(t)):
        if not ok[j]:
            continue
        if t[-1] < t[j] + hold:
            return None                      # cannot confirm the hold period
        if np.all(ok[window(t, t[j], t[j] + hold)]):
            return t[j] - t_c
    return None


def longest_run(t: np.ndarray, cond: np.ndarray, t_from: float) -> float:
    """Longest continuous time [s] for which cond holds, from t_from on.
    A run lasts from its first sample to the first sample after it."""
    best, j, n = 0.0, int(np.searchsorted(t, t_from, side="left")), len(t)
    while j < n:
        if not cond[j]:
            j += 1
            continue
        k = j
        while k < n and cond[k]:
            k += 1
        best = max(best, (t[k] if k < n else t[-1]) - t[j])
        j = k
    return best


def p_loss_after_recovery(t: np.ndarray, p: np.ndarray, t_from: float, p_pre: float,
                          cfg: dict = CONFIG) -> float | None:
    """
    Time [s] at which P, having recovered, falls below p_recovery_frac x P,pre
    again and stays below for at least p_loss_persist_s (a loss of active
    power after the fault, e.g. a trip or a P command to zero). Searched from
    t_from (end of the recovery hold). None if P stays recovered.
    """
    if not np.isfinite(p_pre) or p_pre <= 0:
        return None
    low = p < cfg["p_recovery_frac"] * p_pre
    j = int(np.searchsorted(t, t_from, side="left"))
    n = len(t)
    while j < n:
        if not low[j]:
            j += 1
            continue
        k = j
        while k < n and low[k]:
            k += 1
        end = t[k] if k < n else t[-1]
        if end - t[j] >= cfg["p_loss_persist_s"] or (k == n and end - t[j] > 0):
            # first sample that left the pre-loss level: walk back while P was falling
            m = j
            while m > 0 and p[m - 1] < p_pre * (1 - cfg["final_state_frac"]) and t[m - 1] >= t_from:
                m -= 1
            return float(t[m])
        j = k
    return None


def damping_increase(t: np.ndarray, y: np.ndarray, t_c: float,
                     cfg: dict = CONFIG, t_end: float | None = None) -> bool:
    """Peak-to-peak of y in consecutive 1 s windows from t_c + 0.5 s must not
    increase (beyond damping_abs_tol_MW). True = possible undamped oscillation.
    t_end: stop before this time (e.g. a loss of active power, which is a
    single step, not an oscillation)."""
    w, t0 = cfg["damping_window_s"], t_c + cfg["damping_start_s"]
    t_last = t[-1] if t_end is None else min(t[-1], t_end)
    pp = []
    while t0 + w <= t_last + 1e-9:
        m = window(t, t0, t0 + w)
        if m.any():
            pp.append(float(np.max(y[m]) - np.min(y[m])))
        t0 += w
    return any(b > a + cfg["damping_abs_tol_MW"] for a, b in zip(pp, pp[1:]))


# ==============================================================================
# 4.  PER-CASE EVALUATION
# ==============================================================================

def evaluate_location(loc: Location, case: Case, i_f: int, i_c: int,
                      cfg: dict = CONFIG) -> dict:
    """All values and flags for one location of one case."""
    e, s, t = loc.exported, loc.scaled, loc.t
    is_gu, asym = loc.key != "POC", case.fault in ("2P", "1P")
    flags = list(loc.flags)
    r = {"case": case.name, "location": loc.name, "key": loc.key,
         "t_f": t[i_f], "t_c": t[i_c], "sample_tf_index": i_f,
         "sample_tc_index": i_c}

    for sig in SIGNALS:
        r[f"{sig}_pre"], r[f"{sig}_fault"] = s[sig][i_f], s[sig][i_c]
    for sig in CURRENTS:
        r[f"{sig}_pre_export"], r[f"{sig}_fault_export"] = e[sig][i_f], e[sig][i_c]

    # --- settled checks (warn) ------------------------------------------------
    pre = window(t, t[i_f] - cfg["prefault_window_s"], t[i_f])
    p_ref = abs(s["P"][i_f]) or 1.0
    for sig, ref in (("P", p_ref), ("Q", p_ref), ("U1", None)):
        v = s[sig][pre]
        spread = float(np.max(v) - np.min(v))
        scale = ref if ref is not None else abs(s[sig][i_f])
        if spread > cfg["prefault_flat_frac"] * scale:
            flags.append(f"pre-fault not flat: {sig} spread {spread:.4g} over "
                         f"{cfg['prefault_window_s']} s")
    flt = window(t, t[i_c] - cfg["fault_window_s"], t[i_c])
    for sig in ("U1", "Iq1", "Iq2"):
        v = s[sig][flt]
        if float(np.max(v) - np.min(v)) > cfg["fault_settled_pu"]:
            r[f"{sig}_fault_mean20ms"] = float(np.mean(v))
            flags.append(f"fault value not settled: {sig} (20 ms mean "
                         f"{np.mean(v):.4f}, sample {s[sig][i_c]:.4f})")

    # --- k-factor ---------------------------------------------------------------
    r["dU1"] = s["U1"][i_f] - s["U1"][i_c]
    r["k1"] = k_factor(s["Iq1"][i_f], s["Iq1"][i_c], s["U1"][i_f], s["U1"][i_c])
    if abs(r["dU1"]) < 0.03:
        flags.append(f"k1 sensitive to rounding (dU1 = {r['dU1']:.4f} pu)")
    r["k2"] = None
    if asym:
        r["dU2"] = s["U2"][i_c] - s["U2"][i_f]
        r["k2"] = k_factor(s["Iq2"][i_f], s["Iq2"][i_c], s["U2"][i_f],
                           s["U2"][i_c], negative=True)
        if r["k2"] < 0:
            flags.append("negative-sequence current has wrong sign (k2 < 0)")
        if abs(r["dU2"]) < 0.03:
            flags.append(f"k2 sensitive to rounding (dU2 = {r['dU2']:.4f} pu)")

    phases_f = [s[p][i_c] for p in ("Ua", "Ub", "Uc")]
    phases_p = [s[p][i_f] for p in ("Ua", "Ub", "Uc")]
    r["U_LG_min_fault"] = min(phases_f)

    if not is_gu:
        r["k_verdict"] = "reported only (POC)"
        return {**r, "flags": flags}

    # --- current limit (exported pu) -----------------------------------------------
    gu = cfg["gu"]
    r["worst_phase_current_export"] = worst_phase_current(
        e["Ip1"][i_c], e["Iq1"][i_c], e["Iq2"][i_c] if asym else 0.0)
    r["limited"] = bool(r["worst_phase_current_export"] >= 0.99 * gu["i_total_cap_export_pu"]
                        or e["Iq1"][i_c] >= 0.99 * gu["iq_cap_export_pu"])

    # --- FFCI applicability and mode ---------------------------------------------
    r["u_residual"] = s["U1"][i_c] if case.fault == "3P" else min(phases_f)
    r["ffci_applies"] = bool(r["u_residual"] > cfg["ffci_min_residual_pu"])
    drop = max((p - f) / p for p, f in zip(phases_p, phases_f) if p)
    r["max_phase_drop"] = drop
    r["mode"] = "FRT" if drop > 0.10 else ("SRT" if drop >= 0.05 else "below SRT threshold")

    if r["ffci_applies"]:
        r["k1_verdict"] = k_verdict(r["k1"], r["limited"], cfg["k_set"], cfg["k_tol"])
        r["k2_verdict"] = (k_verdict(r["k2"], r["limited"], cfg["k_set"], cfg["k_tol"])
                           if asym else None)
    else:
        r["k1_verdict"] = r["k2_verdict"] = "n/a (residual <= 0.15 pu)"
        flags.append(f"FFCI criteria not applicable: residual voltage "
                     f"{r['u_residual']:.3f} pu <= {cfg['ffci_min_residual_pu']} pu")

    # optional consistency of the two sequences under the limit (2P/1P)
    if asym:
        r["ratio_pos"] = s["Iq1"][i_c] / (s["Iq1"][i_f] + cfg["k_set"] * r["dU1"])
        r["ratio_neg"] = (abs(s["Iq2"][i_c]) / (cfg["k_set"] * s["U2"][i_c])
                          if s["U2"][i_c] else float("nan"))
        if abs(r["ratio_pos"] - r["ratio_neg"]) > 0.02:
            flags.append(f"sequence scaling ratios differ: pos {r['ratio_pos']:.3f} "
                         f"vs neg {r['ratio_neg']:.3f}")

    # --- Ti,90 / Ts --------------------------------------------------------------
    for seq, sig in (("pos", "Iq1"), ("neg", "Iq2")):
        if seq == "neg" and not asym:
            continue
        rs = rise_and_settling(t, s[sig], i_f, i_c, cfg)
        r[f"ti90_{seq}_ms"], r[f"ts_{seq}_ms"] = rs["ti90_ms"], rs["ts_ms"]
        r[f"ti_ts_{seq}_notes"] = "; ".join(rs["notes"])
        flags += [f"{sig}: {n}" for n in rs["notes"]
                  if n not in ("within band after rise",)]

    # --- inverter protection proxies (L-G voltage) ---------------------------------
    t_f, t_c = t[i_f], t[i_c]
    lg = np.vstack([s["Ua"], s["Ub"], s["Uc"]])
    lg_min, lg_max = lg.min(axis=0), lg.max(axis=0)
    during = window(t, t_f, t_c) & (t > t_f)
    r["U_LG_min_during_fault"] = float(lg_min[during].min())
    for tag in ("uv1", "uv2"):
        dur = longest_run(t, lg_min < cfg[f"{tag}_pu"], t_f)
        r[f"{tag}_below_ms"] = dur * 1000.0
        if dur * 1000.0 >= cfg[f"{tag}_trip_ms"]:
            flags.append(f"{tag.upper()} (proxy: lowest L-G voltage) below "
                         f"{cfg[f'{tag}_pu']} pu for {dur * 1000:.0f} ms >= trip "
                         f"time {cfg[f'{tag}_trip_ms']} ms")
    ov = longest_run(t, lg_max > cfg["ov1_pu"], t_c + 1e-9)
    r["ov1_above_ms"] = ov * 1000.0
    r["U_LG_peak_post_clearance"] = float(lg_max[window(t, t_c, t_c + 0.5)].max())
    if ov * 1000.0 > cfg["ov1_ms"]:
        flags.append(f"OV1 (proxy: highest L-G voltage) above {cfg['ov1_pu']} pu "
                     f"for {ov * 1000:.0f} ms > {cfg['ov1_ms']} ms")
    return {**r, "flags": flags}


def evaluate_case(folder: str, cfg: dict = CONFIG, tf: float | None = None,
                  tc: float | None = None) -> dict:
    """Evaluate one case folder: events, validity, all locations, verdicts."""
    case = parse_case_name(folder)
    locs = {k: load_location(os.path.join(folder, f), k, cfg)
            for k, f in FILES.items() if os.path.isfile(os.path.join(folder, f))}
    if "POC" not in locs:
        raise FileNotFoundError(f"{folder}: POC.csv missing.")
    flags = []
    missing = [f for k, f in FILES.items() if k not in locs]
    if missing:
        flags.append("missing files: " + ", ".join(missing))

    # --- events (POC phase voltages) ------------------------------------------------
    poc = locs["POC"]
    phases = [poc.scaled[p] for p in ("Ua", "Ub", "Uc")]
    if tf is not None:
        i_f = index_at_time(poc.t, tf)
        flags.append(f"t_f override used ({tf} s)")
    else:
        i_f = detect_inception(poc.t, phases, cfg["event_jump_pu"])
    if tc is not None:
        i_c = index_at_time(poc.t, tc)
        flags.append(f"t_c override used ({tc} s)")
    else:
        i_c = detect_clearance(poc.t, phases, i_f, cfg["event_jump_pu"],
                               cfg["clearance_search_delay_s"])
    t_f, t_c = poc.t[i_f], poc.t[i_c]

    # all files share the POC time base; take the same samples by index
    for loc in locs.values():
        if len(loc.t) != len(poc.t) or not np.allclose(loc.t, poc.t):
            raise ValueError(f"{FILES[loc.key]}: time column differs from POC.csv.")

    # --- run validity -------------------------------------------------------------------
    v = {"t_f": t_f, "t_c": t_c, "fault_duration_ms": (t_c - t_f) * 1000.0}
    target_ms = cfg["fault_duration_ms"].get(case.retained_pct)
    if target_ms is None:
        flags.append(f"no target fault duration for {case.retained_pct} % in config")
    elif abs(v["fault_duration_ms"] - target_ms) > cfg["fault_duration_tol_ms"]:
        flags.append(f"fault duration {v['fault_duration_ms']:.0f} ms vs target "
                     f"{target_ms} ms")
    s = poc.scaled
    if case.fault == "3P":
        v["retained_measure"], v["retained_pu"] = "U1", s["U1"][i_c]
    elif case.fault == "1P":
        v["retained_measure"] = "min phase L-G"
        v["retained_pu"] = min(s[p][i_c] for p in ("Ua", "Ub", "Uc"))
    else:
        ua = max(s[p][i_c] for p in ("Ua", "Ub", "Uc"))       # unfaulted phase
        v["retained_measure"] = "U_LL,faulted"
        v["retained_pu"] = v["U_LL_faulted"] = u_ll_faulted_2p(s["U1"][i_c], s["U2"][i_c], ua)
    if abs(v["retained_pu"] - case.retained_pct / 100) > cfg["retained_tol_pu"]:
        flags.append(f"retained voltage {v['retained_pu']:.3f} pu "
                     f"({v['retained_measure']}) vs target {case.retained_pct / 100:.2f}")
    sp = cfg["setpoints"]
    tol = cfg["op_point_tol_frac_pmax"] * cfg["pmax_MW"]
    p_pre, q_pre = s["P"][i_f], s["Q"][i_f]
    if abs(p_pre - sp["P_MW"]) > tol or abs(q_pre - sp[f"{case.q_label}_Mvar"]) > tol:
        flags.append(f"pre-fault operating point P {p_pre:.2f} MW / Q {q_pre:.2f} "
                     f"Mvar vs setpoint {sp['P_MW']} / {sp[f'{case.q_label}_Mvar']}")
    v["complete"] = poc.t[-1] >= t_c + cfg["complete_after_tc_s"]
    if not v["complete"]:
        flags.append(f"simulation incomplete: ends at {poc.t[-1]:.2f} s "
                     f"< t_c + {cfg['complete_after_tc_s']} s")

    # --- locations ------------------------------------------------------------------------
    results = {k: evaluate_location(loc, case, i_f, i_c, cfg) for k, loc in locs.items()}

    # --- ride-through and recovery ------------------------------------------------------
    ride_ok = v["complete"]
    for k, loc in locs.items():
        r, ls = results[k], loc.scaled
        rec = p_recovery_time(loc.t, ls["P"], i_c, ls["P"][i_f], cfg)
        r["p_recovery_s"] = rec
        if rec is None or rec > cfg["p_recovery_limit_s"]:
            ride_ok = False
            r["flags"].append("P not recovered to "
                              f"{cfg['p_recovery_frac']:.0%} within "
                              f"{cfg['p_recovery_limit_s']} s")
        fin = window(loc.t, loc.t[-1] - cfg["final_window_s"], loc.t[-1])
        ref = abs(ls["P"][i_f]) or 1.0
        for sig, scale in (("P", ref), ("Q", ref), ("U1", abs(ls["U1"][i_f]))):
            dev = abs(float(np.mean(ls[sig][fin])) - ls[sig][i_f])
            if dev > cfg["final_state_frac"] * scale:
                ride_ok = False
                r["flags"].append(f"final state differs: {sig} mean over last "
                                  f"{cfg['final_window_s']} s off by {dev:.4g}")
    # --- loss of active power after recovery (trip / P command to zero) -------------
    t_loss = None
    if "POC" in locs:
        ls, rec = locs["POC"].scaled, results["POC"].get("p_recovery_s")
        if rec is not None:
            t_loss = p_loss_after_recovery(
                locs["POC"].t, ls["P"], t_c + rec + cfg["p_recovery_hold_s"],
                ls["P"][i_f], cfg)
        if t_loss is not None:
            ride_ok = False
            fin = window(locs["POC"].t, locs["POC"].t[-1] - cfg["final_window_s"],
                         locs["POC"].t[-1])
            v["t_p_loss"] = t_loss
            flags.append(f"active power lost at {t_loss:.3f} s ({t_loss - t_f:.2f} s after "
                         f"fault inception, {t_loss - t_c:.2f} s after clearance): POC P "
                         f"{ls['P'][i_f]:.1f} to {float(np.mean(ls['P'][fin])):.1f} MW")
    osc = [sig for sig in ("P", "Q")
           if damping_increase(poc.t, poc.scaled[sig], t_c, cfg, t_end=t_loss)]
    if osc:
        ride_ok = False
        flags.append("possible undamped oscillation (POC " + ", ".join(osc) + ")")

    return {"case": case, "validity": v, "locations": results, "flags": flags,
            "ride_through": "pass" if ride_ok else "fail"}


# ==============================================================================
# 5.  GRID CODE YAML vs CONFIG (warning only)
# ==============================================================================

def grid_code_duration_warnings(cfg: dict = CONFIG, script_dir: str | None = None) -> list[str]:
    """Compare frt_scenarios clearance durations in the grid code YAML with
    cfg['fault_duration_ms']; one warning per mismatch. YAML is not changed."""
    path = cfg.get("grid_code_yaml")
    if not path:
        return []
    if not os.path.isabs(path):
        path = os.path.join(script_dir or os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.isfile(path):
        return [f"grid code YAML not found for the duration check: {path}"]
    try:
        import yaml
    except ImportError:
        return ["PyYAML not installed: grid code YAML durations not checked"]
    with open(path, "r", encoding="utf-8") as f:
        gc = yaml.safe_load(f) or {}
    scen = (((gc.get("requirements_type_2") or {}).get("fault_ride_through") or {})
            .get("frt_scenarios") or [])
    out = []
    for sc in scen:
        pct = int(round(float(sc["uret_pu"]) * 100))
        yaml_ms = float(sc["clearance_duration_s"]) * 1000.0
        cfg_ms = cfg["fault_duration_ms"].get(pct)
        if cfg_ms is not None and abs(yaml_ms - cfg_ms) > 1e-6:
            out.append(f"grid code YAML clearance for {pct} % is {yaml_ms:.0f} ms, "
                       f"config (DNV 25-1670 Rev. 2) is {cfg_ms} ms")
    return out


# ==============================================================================
# 6.  OUTPUT
# ==============================================================================

def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), n)


def _k_text(k, limited):
    if k is None or not np.isfinite(k):
        return None
    return f"{k:.2f}*" if limited else f"{k:.2f}"


# ------------------------------------------------------------------------------
# Report-ready tables (DNV 25-1670 Rev. 2 chapter 5.4, Tables 5-2 to 5-11): one
# tab per fault type, tables in report order. Cells stay numbers; the number
# format gives the report text (1.00, 1.10*, 5), so a copy into Word matches.
# ------------------------------------------------------------------------------
REPORT_HEAD_POC = ["Case", "Upre-fault [p.u.]", "Ufault [p.u.]", "Iq,pre-fault [p.u.]",
                   "Iq,fault [p.u.]", "k-factor"]
REPORT_HEAD_GU = REPORT_HEAD_POC + ["Ti,90 [ms]", "Ts [ms]"]
_N_PHASE = {"3P": "3-phase", "2P": "2-phase", "1P": "1-phase"}


def _rh(x: float, n: int = 0) -> float:
    """Round half up as Excel shows it (12 significant digits first; no -0.00)."""
    from decimal import Decimal, ROUND_HALF_UP
    v = float(Decimal(f"{float(x):.12g}").quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP))
    return v + 0.0 if v != 0 else 0.0


def _ordered(results: list[dict]) -> list[dict]:
    """3P, 2P, 1P; by retained voltage; Q0, Qmax, Qmin (report order)."""
    return sorted(results, key=lambda r: (_F_ORDER.get(r["case"].fault, 9),
                                          r["case"].retained_pct,
                                          _Q_ORDER.get(r["case"].q_label, 9)))


def report_rows(results: list[dict], fault: str, key: str, seq: str) -> list[list]:
    """
    One report table: rows of (value, number format) for location `key`
    (POC / GU1 / GU2) and sequence pos / neg. GU tables add Ti,90 and Ts [ms]:
    "N/A" where FFCI does not apply (residual voltage <= 0.15 pu). Ti,90 and
    Ts are always shown as numbers, also when they are equal.
    """
    u, i, k = ("U1", "Iq1", "k1") if seq == "pos" else ("U2", "Iq2", "k2")
    rows = []
    for res in _ordered(results):
        if res["case"].fault != fault or key not in res["locations"]:
            continue
        r = res["locations"][key]
        num = lambda x: ("-", None) if x is None or not np.isfinite(x) else (_rh(x, 2), "0.00")
        kk = r.get(k)
        kc = (("-", None) if kk is None or not np.isfinite(kk) else
              (_rh(kk, 2), '0.00"*"' if r.get("limited") else "0.00"))
        row = [(res["case"].name, None), num(r[f"{u}_pre"]), num(r[f"{u}_fault"]),
               num(r[f"{i}_pre"]), num(r[f"{i}_fault"]), kc]
        if key != "POC":
            ti, ts = r.get(f"ti90_{seq}_ms"), r.get(f"ts_{seq}_ms")
            if not r.get("ffci_applies"):
                row += [("N/A", None), ("N/A", None)]
            else:
                # both always as numbers, also when Ts = Ti,90
                row.append(("-", None) if ti is None else (_rh(ti, 0), "0"))
                row.append(("-", None) if ts is None else (_rh(ts, 0), "0"))
        rows.append(row)
    return rows


def write_report_tables(wb, results: list[dict], cfg: dict = CONFIG) -> None:
    """Tabs '3P faults', '2P faults', '1P faults' with the chapter 5.4 tables."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    thin = Side(style="thin", color="000000")
    for fault in ("3P", "2P", "1P"):
        if not any(r["case"].fault == fault for r in results):
            continue
        ws = wb.create_sheet(f"{fault} faults")
        gus = [g for g in ("GU1", "GU2")
               if any(g in r["locations"] for r in results if r["case"].fault == fault)]
        row0 = 1
        for seq in (("pos",) if fault == "3P" else ("pos", "neg")):
            for key in ["POC"] + gus:
                body = report_rows(results, fault, key, seq)
                if not body:
                    continue
                where = "the POC" if key == "POC" else "the GU terminals"
                if key != "POC" and len(gus) > 1:
                    where += f" ({cfg['gu_names'][key]})"
                title = (f"{'Positive' if seq == 'pos' else 'Negative'}-sequence results "
                         f"for the {_N_PHASE[fault]} faults at {where}")
                head = REPORT_HEAD_POC if key == "POC" else REPORT_HEAD_GU
                ws.cell(row0, 1, title).font = Font(bold=True)
                for c, h in enumerate(head, 1):
                    cell = ws.cell(row0 + 1, c, h)
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
                    cell.alignment = Alignment(horizontal="center", vertical="center",
                                               wrap_text=True)
                    cell.border = Border(top=thin, bottom=thin)
                r = row0 + 1
                for row in body:
                    r += 1
                    for c, (v, fmt) in enumerate(row, 1):
                        cell = ws.cell(r, c, v)
                        if fmt:
                            cell.number_format = fmt
                        cell.alignment = Alignment(horizontal="center")
                for c in range(1, len(head) + 1):
                    ws.cell(r, c).border = Border(bottom=thin)
                row0 = r + 3
        for c, w in enumerate([14, 12, 10, 13, 11, 10, 10, 9], 1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.sheet_properties.pageSetUpPr.fitToPage = True   # print / PDF: one page wide
        ws.page_setup.fitToHeight = 0


def raw_sheet(results: list[dict]) -> pd.DataFrame:
    rows = []
    for res in results:
        for key, r in res["locations"].items():
            row = {k: v for k, v in r.items() if k != "flags"}
            row.update({f"case_{k}": v for k, v in res["validity"].items()})
            row["flags"] = "; ".join(res["flags"] + r["flags"])
            rows.append(row)
    return pd.DataFrame(rows)


def _limit_verdict(values, limit):
    vals = [v for v in values if v is not None]
    if not vals:
        return "n/a"
    return "pass" if all(v <= limit for v in vals) else "fail"


def criteria_sheet(results: list[dict], cfg: dict = CONFIG) -> pd.DataFrame:
    rows = []
    for res in results:
        gus = [r for k, r in res["locations"].items() if k != "POC"]
        kv = [r.get(f"k{n}_verdict") for r in gus for n in (1, 2)]
        kv = [x for x in kv if x and not x.startswith("n/a")]
        if not kv:
            k_res = "n/a"
        elif "NON-COMPLIANT" in kv:
            k_res = "fail"
        elif "limited*" in kv:
            k_res = "pass*"
        else:
            k_res = "pass"
        applies = [r for r in gus if r.get("ffci_applies")]
        ti = [r.get(f"ti90_{s}_ms") for r in applies for s in ("pos", "neg")]
        ts = [r.get(f"ts_{s}_ms") for r in applies for s in ("pos", "neg")]
        rec = [r.get("p_recovery_s") for r in res["locations"].values()]
        rec_res = ("fail" if any(x is None or x > cfg["p_recovery_limit_s"] for x in rec)
                   else "pass")
        flags = res["flags"] + [f"{r['location']}: {f}" for r in res["locations"].values()
                                for f in r["flags"]]
        rows.append({
            "Case": res["case"].name,
            "Ride-through": res["ride_through"],
            "k (GU)": k_res,
            f"Ti,90 <= {cfg['ti90_limit_ms']} ms": _limit_verdict(ti, cfg["ti90_limit_ms"]),
            f"Ti,90 <= {cfg['ti90_limit_incl_seq_ms']} ms": _limit_verdict(ti, cfg["ti90_limit_incl_seq_ms"]),
            f"Ts <= {cfg['ts_limit_ms']} ms": _limit_verdict(ts, cfg["ts_limit_ms"]),
            f"Ts <= {cfg['ts_limit_incl_seq_ms']} ms": _limit_verdict(ts, cfg["ts_limit_incl_seq_ms"]),
            "P recovery": rec_res,
            "Flags": "; ".join(flags) + ("; " if flags else "")
                     + "no inverter trip signal in the export",
        })
    return pd.DataFrame(rows)


def consistency_sheet(results: list[dict], cfg: dict = CONFIG) -> pd.DataFrame:
    rows = []
    for q in ("Q0", "Qmax", "Qmin"):
        for gu in ("GU1", "GU2"):
            vals = [(res["case"].name, res["locations"][gu]) for res in results
                    if res["case"].q_label == q and gu in res["locations"]]
            if not vals:
                continue
            for qty, key in (("Upre", "U1_pre"), ("Iq,pre", "Iq1_pre")):
                xs = [r[key] for _, r in vals]
                spread = max(xs) - min(xs)
                rows.append({"Q condition": q, "Location": cfg["gu_names"][gu],
                             "Quantity": qty, "n cases": len(xs), "min": min(xs),
                             "max": max(xs), "spread": spread,
                             "Flag": ("spread > " + str(cfg["consistency_spread_pu"]) + " pu")
                             if spread > cfg["consistency_spread_pu"] else ""})
    return pd.DataFrame(rows)


HEADER_FILL = "9BD7F0"                        # report table header colour
FAIL_FILL = "FF9999"                          # summary: failed / error cells
PASS_FILL = "C6EFCE"                          # summary: passed result cell
SUMMARY_COLS = ["Case", "PowerFactory case", "Result", "Ride-through", "k (GU)",
                "Ti,90 (GU)", "Ts (GU)", "P recovery", "Flags"]
_Q_ORDER = {"Q0": 0, "Qmax": 1, "Qmin": 2}
_F_ORDER = {"3P": 0, "2P": 1, "1P": 2}


def case_summary(res: dict, cfg: dict = CONFIG) -> dict:
    """
    One summary row per case: {column: (text, failed)}. Ti,90 / Ts: pass up
    to the simulation limit, pass* up to the limit incl. the sequence
    calculation, FAIL beyond. k: FAIL when NON-COMPLIANT. P recovery: FAIL
    when not recovered within the limit.
    """
    case = res["case"]
    gus = [r for k, r in res["locations"].items() if k != "POC"]
    row = {"Case": (case.name, False),
           "PowerFactory case": (os.path.basename(case.folder.rstrip("/\\")), False)}
    rt = res["ride_through"]
    t_loss = res["validity"].get("t_p_loss")
    txt = "pass" if rt == "pass" else (f"FAIL (P lost at {t_loss:.3f} s)" if t_loss else "FAIL")
    row["Ride-through"] = (txt, rt != "pass")

    kv = [r.get(f"k{n}_verdict") for r in gus for n in (1, 2)]
    kv = [x for x in kv if x and not x.startswith("n/a")]
    ks = [r.get("k1") for r in gus if r.get("k1") is not None and np.isfinite(r.get("k1"))]
    kt = f" ({min(ks):.2f}-{max(ks):.2f})" if ks else ""
    if not kv:
        row["k (GU)"] = ("n/a", False)
    elif "NON-COMPLIANT" in kv:
        row["k (GU)"] = ("FAIL" + kt, True)
    elif "limited*" in kv:
        row["k (GU)"] = ("pass*" + kt + ", current limited", False)
    else:
        row["k (GU)"] = ("pass" + kt, False)

    applies = [r for r in gus if r.get("ffci_applies")]

    def timing(key, lim, lim_seq):
        vals = [r.get(f"{key}_{q}_ms") for r in applies for q in ("pos", "neg")]
        vals = [v for v in vals if v is not None]
        if not vals:
            return ("n/a", False)
        m = max(vals)
        if m <= lim:
            return (f"pass (max {m:.1f} ms)", False)
        if m <= lim_seq:
            return (f"pass* (max {m:.1f} ms, <= {lim_seq:g} ms incl. sequence calc.)", False)
        return (f"FAIL (max {m:.1f} ms > {lim_seq:g} ms)", True)

    row["Ti,90 (GU)"] = timing("ti90", cfg["ti90_limit_ms"], cfg["ti90_limit_incl_seq_ms"])
    row["Ts (GU)"] = timing("ts", cfg["ts_limit_ms"], cfg["ts_limit_incl_seq_ms"])
    rec = [r.get("p_recovery_s") for r in res["locations"].values()]
    if any(x is None for x in rec):
        row["P recovery"] = ("FAIL (not recovered)", True)
    else:
        m = max(rec)
        ok = m <= cfg["p_recovery_limit_s"]
        row["P recovery"] = (f"{'pass' if ok else 'FAIL'} (max {m:.2f} s)", not ok)
    failed = any(f for _, f in row.values())
    star = any(str(t).startswith("pass*") for t, _ in row.values())
    row["Result"] = ("FAIL" if failed else ("PASS*" if star else "PASS"), failed)
    fl = res["flags"] + [f"{r['location']}: {f}" for r in res["locations"].values()
                         for f in r["flags"]]
    row["Flags"] = (f"{len(fl)}: " + " | ".join(fl) if fl else "none", False)
    return row


def summary_rows(results: list[dict], errors: list[tuple[str, str]],
                 cfg: dict = CONFIG) -> list[dict]:
    """Every case folder: evaluated cases (3P, 2P, 1P; by retained voltage; Q0, Qmax, Qmin), then errors."""
    rows = [case_summary(r, cfg) for r in _ordered(results)]
    for folder, msg in errors:
        row = {c: ("-", False) for c in SUMMARY_COLS}
        try:
            name = parse_case_name(folder).name
        except ValueError:
            name = folder
        row.update({"Case": (name, False), "PowerFactory case": (folder, False),
                    "Result": ("ERROR", True), "Flags": (msg, False)})
        rows.append(row)
    return rows


def write_summary(ws, rows: list[dict], title: str) -> None:
    """Summary tab: one row per case, failed cells red, passed results green."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    thin = Side(style="thin", color="999999")
    ws.cell(1, 1, title).font = Font(bold=True, size=13)
    n_pass = sum(1 for r in rows if r["Result"][0].startswith("PASS"))
    ws.cell(2, 1, f"{len(rows)} case(s): {n_pass} pass, {len(rows) - n_pass} fail / error. "
                  f"Failed cells are red. pass* = within the limit incl. the sequence "
                  f"calculation, or k limited by the current limit.")
    for c, h in enumerate(SUMMARY_COLS, 1):
        cell = ws.cell(4, c, h)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
    for r, row in enumerate(rows, 5):
        for c, col in enumerate(SUMMARY_COLS, 1):
            text, failed = row.get(col, ("-", False))
            cell = ws.cell(r, c, text)
            cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
            cell.alignment = Alignment(vertical="top", wrap_text=True,
                                       horizontal="left" if col in ("Case", "PowerFactory case", "Flags")
                                       else "center")
            if failed:
                cell.fill = PatternFill("solid", fgColor=FAIL_FILL)
                cell.font = Font(bold=True, color="9C0006")
            elif col == "Result" and str(text).startswith("PASS"):
                cell.fill = PatternFill("solid", fgColor=PASS_FILL)
                cell.font = Font(bold=True, color="006100")
    widths = {"Case": 13, "PowerFactory case": 20, "Result": 9, "Ride-through": 12,
              "k (GU)": 22, "Ti,90 (GU)": 26, "Ts (GU)": 26, "P recovery": 16, "Flags": 70}
    for c, col in enumerate(SUMMARY_COLS, 1):
        ws.column_dimensions[get_column_letter(c)].width = widths[col]
    ws.freeze_panes = "C5"


def write_excel(results: list[dict], path: str, warnings: list[str],
                cfg: dict = CONFIG, errors: list[tuple[str, str]] | None = None) -> None:
    """First tab: Summary (every case, pass / FAIL, failed cells red); then the details."""
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        pd.DataFrame().to_excel(xl, sheet_name="Summary", index=False)
        write_summary(xl.sheets["Summary"], summary_rows(results, errors or [], cfg),
                      "FRT / FFCI compliance summary")
        write_report_tables(xl.book, results, cfg)
        raw_sheet(results).to_excel(xl, sheet_name="raw", index=False)
        crit = criteria_sheet(results, cfg)
        crit.to_excel(xl, sheet_name="criteria", index=False)
        consistency_sheet(results, cfg).to_excel(xl, sheet_name="consistency", index=False)
        if warnings:
            pd.DataFrame({"Warning": warnings}).to_excel(xl, sheet_name="warnings", index=False)


def plot_case(res: dict, folder: str, out_dir: str, cfg: dict = CONFIG) -> None:
    """GU Iq1/Iq2 from t_f - 20 ms to t_f + 100 ms with 90/110 % lines and Ti,90."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    case = res["case"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=False)
    for ax, gu in zip(axes, ("GU1", "GU2")):
        if gu not in res["locations"]:
            continue
        loc = load_location(os.path.join(folder, FILES[gu]), gu, cfg)
        r = res["locations"][gu]
        t_f = r["t_f"]
        m = window(loc.t, t_f - 0.020, t_f + 0.100)
        tm = (loc.t[m] - t_f) * 1000
        for sig, seq in (("Iq1", "pos"), ("Iq2", "neg")):
            if seq == "neg" and case.fault == "3P":
                continue
            y = loc.scaled[sig]
            line, = ax.plot(tm, y[m], label=sig)
            pre, flt = r[f"{sig}_pre"], r[f"{sig}_fault"]
            for frac in (0.9, 1.1):
                ax.axhline(pre + frac * (flt - pre), ls=":", lw=0.8,
                           color=line.get_color(), alpha=0.6)
            ti = r.get(f"ti90_{seq}_ms")
            if ti is not None:
                ax.axvline(ti, ls="--", lw=0.8, color=line.get_color(),
                           label=f"Ti,90 {sig} = {ti:.1f} ms")
        ax.set_title(f"{case.name} — {r['location']}")
        ax.set_xlabel("time after t_f [ms]")
        ax.set_ylabel("current [pu, 247 A base]")
        ax.grid(True, alpha=0.3)
        ax.legend()
    fig.tight_layout()
    os.makedirs(out_dir, exist_ok=True)
    fig.savefig(os.path.join(out_dir, f"{case.name}.png"), dpi=150)
    plt.close(fig)


def console_summary(res: dict) -> str:
    lines = [f"{res['case'].name}  ride-through {res['ride_through']}  "
             f"t_f {res['validity']['t_f']:.4f} s  t_c {res['validity']['t_c']:.4f} s"]
    for key, r in res["locations"].items():
        k = _k_text(r["k1"], r.get("limited", False))
        k2 = _k_text(r["k2"], r.get("limited", False)) if r.get("k2") is not None else "-"
        ti, rec = r.get("ti90_pos_ms"), r.get("p_recovery_s")
        ti_txt = "-" if ti is None else f"{ti:.1f} ms"
        rec_txt = "-" if rec is None else f"{rec:.3f} s"
        lines.append(f"   {r['location']:6s} Upre {r['U1_pre']:.4f}  Ufault {r['U1_fault']:.4f}  "
                     f"k1 {k}  k2 {k2}  Ti90 {ti_txt}  Prec {rec_txt}")
    for f in res["flags"] + [f"{r['location']}: {f}" for r in res["locations"].values()
                             for f in r["flags"]]:
        lines.append(f"   ! {f}")
    return "\n".join(lines)


# ==============================================================================
# 7.  CLI
# ==============================================================================

# Output channel: print() on the command line, the PowerFactory output window
# when run as a ComPython script (set in run_in_powerfactory).
_say = print
_warn = print


def find_case_folders(root: str) -> list[str]:
    out = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p) and CASE_RE.search(name):
            out.append(p)
    return out


def run(folder: str, out: str, plots: bool = False, tf: float | None = None,
        tc: float | None = None, cfg: dict = CONFIG) -> int:
    """Evaluate every case folder in `folder`, write `out`. Returns 0 / 1."""
    folders = find_case_folders(folder)
    if not folders and CASE_RE.search(os.path.basename(folder.rstrip("/\\"))):
        folders = [folder]                       # a single case folder given
    if not folders:
        _warn(f"No case folders ('FRT <n>PH <uret> <Q>') found in {folder}")
        return 1

    warnings = grid_code_duration_warnings(cfg)
    for w in warnings:
        _warn(f"WARNING: {w}")

    results, errors = [], []
    for case_folder in folders:
        try:
            res = evaluate_case(case_folder, cfg, tf, tc)
        except Exception as exc:                 # one case never stops the others
            _warn(f"ERROR {os.path.basename(case_folder)}: {exc}")
            errors.append((os.path.basename(case_folder.rstrip("/\\")), f"ERROR {exc}"))
            continue
        results.append(res)
        _say(console_summary(res))
        if plots:
            try:
                plot_case(res, case_folder,
                          os.path.join(os.path.dirname(os.path.abspath(out)), "plots"), cfg)
            except Exception as exc:
                _warn(f"   plot failed: {exc}")

    if results or errors:
        write_excel(results, out, warnings, cfg, errors)
        _say("\nSUMMARY")
        for row in summary_rows(results, errors, cfg):
            _say(f"   {row['Case'][0]:14s} {row['Result'][0]}")
        _say(f"\nWritten: {out}  ({len(results)} case(s) evaluated)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    ap = argparse.ArgumentParser(description="FRT / FFCI compliance checker")
    ap.add_argument("folder", help="folder containing the case folders")
    ap.add_argument("--out", default="results.xlsx")
    ap.add_argument("--plots", action="store_true")
    ap.add_argument("--tf", type=float, default=None, help="override t_f [s]")
    ap.add_argument("--tc", type=float, default=None, help="override t_c [s]")
    a = ap.parse_args(argv)
    return run(a.folder, a.out, a.plots, a.tf, a.tc)


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
    out = PF_OUT_FILE or os.path.join(PF_CASES_FOLDER, "results.xlsx")
    app.PrintPlain("=" * 72)
    app.PrintPlain("  FRT / FFCI compliance check")
    app.PrintPlain("=" * 72)
    app.PrintPlain(f"  Cases  : {PF_CASES_FOLDER}")
    app.PrintPlain(f"  Output : {out}" + ("   (+ plots)" if PF_PLOTS else ""))
    app.PrintPlain(f"  Python : {sys.prefix}\n")
    if not os.path.isdir(PF_CASES_FOLDER):
        app.PrintError(f"Cases folder not found:\n  {PF_CASES_FOLDER}\n"
                       "Set PF_CASES_FOLDER at the top of frt_check.py.")
        return
    try:
        run(PF_CASES_FOLDER, out, PF_PLOTS, PF_TF, PF_TC)
    except PermissionError as exc:
        app.PrintError(f"Could not write the output — is {os.path.basename(out)} "
                       f"open in Excel? ({exc})")
    except Exception as exc:
        app.PrintError(f"FRT compliance check aborted: {exc}")
        raise


# Run from PowerFactory (ComPython) with the PF_ settings, or from the command
# line with arguments. Importing the module (e.g. from the tests) runs nothing.
_PF_APP = powerfactory_app()
if _PF_APP is not None:
    run_in_powerfactory(_PF_APP)
elif __name__ == "__main__":
    sys.exit(main())
