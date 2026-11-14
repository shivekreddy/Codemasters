# GCC Working Scripts: user and developer guide

PowerFactory scripts that set up and run the grid code compliance (GCC)
simulations for a Type D power park module (NBNL PGMCV 2.0, Dutch RfG), export
the results, and check them against the requirements. Two studies are covered:

- **FRT / FFCI**: fault ride-through and fast fault current injection
  (NBNL 4.2.14, 4.2.16, 4.2.18)
- **Frequency**: LFSM-O, LFSM-U, FSM and FSM insensitivity (NBNL 4.2.1 to 4.2.3)

Method and report tables follow DNV report 25-1670 Rev. 2.

---

## 1. Folder layout

```
Working Scripts\
    project_config_update.yaml        THE project config: the only file you edit per project
    netherlands_NC-RfG-TypeD.yaml     grid code requirements and test sequences
    gcc_common.py                     shared code library (never run directly)
    FRT\
        frt_1_create_and_run_cases.py      create the FRT study cases, optionally run them
        frt_2_set_fault_impedance.py       tune the fault impedances (retained voltages)
        frt_3_export_raw_data.py           export the results to CSV
        frt_3b_export_selected_cases.py    export only a few cases, into a folder you choose
        frt_4_export_plots.py              export the PowerFactory plot pages as PNG
        frt_5_compliance_check.py          evaluate compliance, write the Excel workbook
        frt_6_reset_plot_scaling.py        put the plot y-axes back to their fixed ranges
        frt_7_fix_poc_power_sign.py        older runs only: flip the sign of the POC power curves
    Frequency\
        freq_1_create_and_run_cases.py     create the frequency study cases, optionally run them
        freq_2_export_raw_data.py          export the results to CSV
        freq_3_export_plots.py             export the PowerFactory plot pages as PNG
        freq_4_compliance_check.py         evaluate compliance, write the Excel workbook
    tests\                                 pytest tests (no PowerFactory needed)
```

The number in each name is the run order. The config, the grid code file and
`gcc_common.py` must stay in `Working Scripts`: the scripts in `FRT\` and
`Frequency\` look for them one folder up.

---

## 2. One-time setup

1. **Python packages** in the Python that PowerFactory uses:
   `"<PF Python>\python.exe" -m pip install pyyaml pandas numpy openpyxl matplotlib`
   (pyyaml for all scripts; pandas, numpy, openpyxl for the checks; matplotlib
   only for the optional check plots).
2. **ComPython objects**: one per script, each pointing at the script file
   (e.g. `...\Working Scripts\FRT\frt_1_create_and_run_cases.py`). Run them
   from PowerFactory with the project active.
3. **Base case**: a study case named as `elements.base_case` (default
   `GCC_BaseCase`) with a working load flow and RMS initialisation. Every
   study case is a copy of it, made as is.

---

## 3. The project config (`project_config_update.yaml`)

| Section | What it holds |
|---|---|
| `project` | Name, number, client, author (written to the manifests) |
| `plant` | `pmax_mw`, `poc_voltage_kv`, `q_factor` (Qmax = q_factor x Pmax) |
| `grid_code` | Grid code YAML file name (relative to the config) |
| `elements` | PowerFactory `loc_name` of every element used (see below) |
| `plot_labels` | Legend names per element; also become the CSV column headers and the plant names in the FRT workbook |
| `paths` | **The only place a folder is set** (see section 4) |
| `simulation` | Run control, timing, P setpoints for FRT and frequency |
| `grid_requirements_project_data` | Project-specific grid code choices (k-factor, response times, ...) |

**Elements:** `poc_busbar` (ElmTerm), `poc_breaker` (ElmCoup),
`external_grid` (ElmXnet), `station_ctrl` (ElmStactrl), `pfc_ctrl`
(ElmSecctrl), `freq_voltage_source` (ElmVac), `base_case`, and per plant
`gu_busbar1` / `inverter1` and optionally `gu_busbar2` / `inverter2`.
**One or two plants is decided here**: set `gu_busbar2` and `inverter2` to
`null` for a one-plant model. The FRT scripts follow it automatically; the
frequency study uses the POC only and does not need the plants.

**Simulation run control:**
- `setup_only: true` creates the cases only; `false` also runs them.
- `run_sim_only: true` runs existing cases without recreating them (e.g.
  after `frt_2_set_fault_impedance.py`).
- `t_stop_s` (FRT stop time, also the end of the FRT plot time axis),
  `fault_start_s`, `frt_reduced_p` / `frt_reduced_p_factor` (FRT at 93 % Pmax),
  `freq_p_factor` (frequency cases at 70 % Pmax).

---

## 4. Folders and outputs

Set once in the config:

```yaml
paths:
  root: 'C:\Users\<you>\Downloads\Results Export'
  frt_run:  null    # null = latest FRT_* run, or a run folder name
  freq_run: null    # null = latest FREQ_* run
```

Everything is written under `root` (folders are created when missing):

```
<root>\
    Run Manifests\<run>_manifest.json                    frt_1 / freq_1
    FRT Raw Data\<FRT run>\<case>\POC.csv, GU Terminal*.csv  + export_manifest.json   frt_3
    FRT Selected Cases\<FRT run>\<case>\...   (default of frt_3b; any folder set in the script)
    PowerFactory Plots\FRT\<FRT run>\<case>\POC.png, ...  frt_4
    FRT Results\<FRT run>\frt_results.xlsx (+ plots\)      frt_5
    Freq Raw Data\<FREQ run>\LFSMO_Step.csv, ...  + export_manifest.json            freq_2
    PowerFactory Plots\Frequency\<FREQ run>\<case> - Plot 1 - Main Results.png, ...  freq_3
    Freq Results\<FREQ run>\freq_results.xlsx (+ plots\)   freq_4
```

`<run>` is the PowerFactory run folder in Study Cases, named
`FRT_YYYY-MM-DD_HH-MM-SS` / `FREQ_YYYY-MM-DD_HH-MM-SS`. It may be renamed with
text after the timestamp (e.g. `FRT_2026-11-11_13-56-23 (PPC Enabled)`).
Every export and check uses the **latest** run by that timestamp unless
`frt_run` / `freq_run` names another one. Existing files are overwritten.

---

## 5. FRT workflow

| Step | Script | Does | Settings in the script |
|---|---|---|---|
| 1 | `frt_1_create_and_run_cases.py` | Builds every FRT case from the grid code (fault types x retained voltages x Q0/Qmax/Qmin) in a new `FRT_<timestamp>` folder: copies the base case, adds fault and clearance events, recorded variables, plot pages (POC and one GU page per plant, fixed y-axis ranges), P and Q setpoints, stop time. Runs them if `setup_only: false`. Writes the run manifest. | `QSETP_SIGN`, y-axis ranges (`Y_RANGE_*`, `PQ_*`) |
| 2 | `frt_2_set_fault_impedance.py` | Optional. Writes R_f / X_f per retained voltage into the fault events of the latest run, so the retained voltage at the POC hits its target. Then rerun with `run_sim_only: true`. | `FAULT_IMPEDANCE` table, `DRY_RUN`, `RUN_TAG` |
| 3 | `frt_3_export_raw_data.py` | One folder per case, one CSV per plot page (`POC.csv`, `GU Terminal.csv` or `GU Terminal 1.csv` + `GU Terminal 2.csv`), plus `export_manifest.json`. | none |
| 3b | `frt_3b_export_selected_cases.py` | Optional. Same export as step 3 (identical files), but only for the cases in `CASES` (PowerFactory or report names, wildcards allowed, e.g. `"1P-60U-Qmax"`, `"FRT 2PH * Qmax"`) and into `OUTPUT_DIR` (default `<root>\FRT Selected Cases`). Exporting again into the same folder adds or replaces cases. Check them with `frt_5_compliance_check.py <folder>\<run folder>` from the command line. | `CASES`, `OUTPUT_DIR`, `RUN_FOLDER` |
| 4 | `frt_4_export_plots.py` | PNG of every plot page, one folder per case. | `FORMAT` |
| 5 | `frt_5_compliance_check.py` | Evaluates every case and writes `frt_results.xlsx`. | `PF_PLOTS`, `PF_TF` / `PF_TC` (event time overrides) |
| 6 | `frt_6_reset_plot_scaling.py` | Utility, any time. Resets the y-axes on the POC and GU Terminal pages of every case (or `CASES`) of the latest run: voltages 0 to 1.4 pu, currents -1 to 1.4 pu, and the P / Q graph per page (POC, GU1, GU2 if the config has plant 2) to the min / max you enter (None = unchanged). The time axis is not touched. Use it after rescaling graphs by hand, before `frt_4_export_plots.py`. | `Y_RANGE_VOLTAGE`, `Y_RANGE_CURRENT`, `Y_RANGE_POWER`, `CASES`, `RUN_FOLDER` |
| 7 | `frt_7_fix_poc_power_sign.py` | Older runs only (made before frt_1 v5.6, POC power plotted on bus1, export negative). In the POC page's power graph it creates two Calculated Results (POC breaker `m:Psum:bus1` x -1 and `m:Qsum:bus1` x -1), adds them as curves with the original colours and labels, and hides the original curves: no rerun needed. Skips cases already fixed, on bus2, or with a hand-made Calculated Result. `UNDO = True` removes what it added; `DRY_RUN = True` reports only; test on one case first. | `DRY_RUN`, `UNDO`, `CASES`, `RUN_FOLDER` |

**FRT workbook (`frt_results.xlsx`):**
- **Summary** (first tab): one row per case with Result and a verdict per
  criterion: ride-through, k-factor, Ti,90, Ts, P recovery, and flags. Failed
  cells are red, passed results green. `pass*` = within the limit including
  the sequence calculation (Ti,90 50 ms, Ts 80 ms), or k below the setting
  because the current limit was reached. A loss of active power after recovery
  shows as `FAIL (P lost at <t> s)`.
- **3P faults / 2P faults / 1P faults**: the report tables (DNV 25-1670
  Tables 5-2 to 5-11) in report order. Cells are numbers with report
  formatting (`1.00`, `1.10*`, whole ms), ready to paste into Word.
- **raw**, **criteria**, **consistency**, **warnings**: full-precision values,
  per-case criteria, pre-fault consistency across cases, grid code warnings.

---

## 6. Frequency workflow

| Step | Script | Does | Settings in the script |
|---|---|---|---|
| 1 | `freq_1_create_and_run_cases.py` | Builds the frequency cases from the grid code (`LFSMO`, `LFSMU`, `FSM`: Step and Ramp; `FSMINS`: Step) in a new `FREQ_<timestamp>` folder: base case copy, POC fed by the frequency voltage source, P = `freq_p_factor` x Pmax, Q = 0, frequency step events (`EvtParam` on F0Hz), plot pages, stop time. Runs them if `setup_only: false`. Writes the run manifest. | `FREQ_Y_RANGE` |
| 2 | `freq_2_export_raw_data.py` | One CSV per case plus `export_manifest.json` (with step times and psetp from the run manifest). Cases without results are written as zeros and marked. | none |
| 3 | `freq_3_export_plots.py` | PNG of Plot 1 to 3 of every case. | `FORMAT` |
| 4 | `freq_4_compliance_check.py` | Evaluates every Step case and writes `freq_results.xlsx`. | `PF_PLOTS` |

**Frequency workbook (`freq_results.xlsx`):**
- **Summary** (first tab): every case in the manifest with Result and verdicts
  for thresholds, droop, error, Ti, Ts, oscillation, insensitivity, and flags.
  Failed or error cells red. Ramp cases show "not evaluated".
- **LFSM-O / LFSM-U / FSM**: the report tables (DNV 25-1670 Tables 4-2 to 4-10):
  list of frequency steps and ramps, and the simulation results at the POC.
  Units are in the cell format (`50.0 Hz`, `43.71 MW (Pref, FSM)`, `-4% Pref`,
  `0.4 s`), so a paste into Word gives the report text.
- **FSM insensitivity**: +/-10 mHz must give no response, +/-12 mHz a response
  in the right direction.
- **info**, **flags**, **raw**: run header, every flag, full-precision values.

---

## 7. Running the checks outside PowerFactory

Both checks run in any Python with pandas, numpy and openpyxl:

```
python "Working Scripts\FRT\frt_5_compliance_check.py"                 (folders from the config)
python "Working Scripts\FRT\frt_5_compliance_check.py" <run folder> --out x.xlsx --plots
python "Working Scripts\Frequency\freq_4_compliance_check.py" [<run folder>] [--out ...] [--plots]
```

Tests (no PowerFactory needed):

```
python -m pytest "Working Scripts/tests" -v
```

Sample-based acceptance tests run when the samples are in `tests/data/` or
`FRT_SAMPLE_DIR` / `FREQ_SAMPLE_DIR` point at them (see `tests/data/README.md`);
otherwise they are skipped. CSVs are never committed (`.gitignore`).

---

## 8. For developers: data contracts

**Run manifest** (`Run Manifests\<run tag>_manifest.json`, written by
`gcc_common.write_run_manifest`): `run_tag`, `created`, `project`, `plant`
(resolved Pmax, U_POC, Qmax/Qmin), `grid_code_file`, `grid_code_meta`,
`n_cases`, `n_failed`, `cases` and script extras (`script_version`,
`simulation_timing`, `p_factor`, run mode flags). Per case: `name`, `type` /
`variant` (frequency) or `fault_type`, `uret`, `t_fault`, `t_clear`,
`q_label` (FRT), plus `t_stop`, `psetp`, `qsetp`, `usetp`, `step_events`
(frequency), `status`, `reason`. It is named after the run tag the folder had
when it was created; renamed folders are matched by their timestamp.

**FRT export manifest** (`FRT Raw Data\<run>\export_manifest.json`):
`study_case_folder`, `exported`, `project_config`, `pages`, `cases` (per case:
`folder`, `n_rows`, `t_end_s`, `files` with their column headers,
`missing_variables`), `skipped_no_results`, `failed`.

**Frequency export manifest** (`Freq Raw Data\<run>\export_manifest.json`):
`study`, `run_tag`, `exported`, `columns`, `project`, `plant`, `p_factor`,
`simulation_timing`, `cases` (the run manifest entries plus `csv_file`,
`n_rows`, `t_end_s`, `missing_columns`, `export_status` = `ok` /
`no_result_data`, `complete`).

**CSV format:**
- First column `time_s`. The time step is **non-uniform** and there are
  **duplicate timestamps at events** (pre- and post-event values): select
  samples by index, never by `time == t`.
- FRT headers: `<plot label> <description> (<unit>)`, e.g.
  `HVC GU Terminal Line-Ground Voltage A (p.u.)`. 10 signals per file:
  U_A, U_B, U_C, U1, U2, P, Q, Ip1, Iq1, Iq2.
- Frequency headers: `POC Frequency [Hz]`, `POC Pos-Seq Voltage [pu]`,
  P and Q on both sides of the POC breaker.

**Sign conventions:**
- POC breaker `bus1` is load oriented (export negative); `bus2` is export
  positive. The FRT raw export uses bus1 (`frt_5` flips the sign); the FRT
  POC plot page and the frequency plots use bus2.
- Q setpoints in the manifests follow the plant convention: Qmax =
  +q_factor x Pmax. `QSETP_SIGN` in `frt_1` converts to the station
  controller's convention.

**Case naming:** FRT study cases `FRT <n>PH <Uret> <Q>` (e.g.
`FRT 1PH 0.05 Q0`), report name `<n>P-<Uret %>U-<Q>` (`1P-5U-Q0`). Frequency
cases `<TYPE> <Step|Ramp>`, CSV `<TYPE>_<Step|Ramp>.csv`.

**Execution model:** the PowerFactory scripts run **inside** PowerFactory as
ComPython scripts (`import powerfactory`). They cannot receive Python objects
from an outside process; their inputs are the project config, the grid code
YAML and the manifests on disk. Running them from an external `main` needs
PowerFactory in engine mode. The two checks have no PowerFactory dependency
and run from the command line.

**Where the logic lives (for the core migration):**
- Manifest writing: `gcc_common.write_run_manifest`, the export manifests in
  `gcc_common.export_raw_results` (frequency) and `gcc_common.export_frt_raw`
  (FRT, used by `frt_3` and `frt_3b`; the FRT export manifest also has
  `selected_cases`).
- Folder resolution: `gcc_common` section 0 (`results_dir`, `run_name_setting`,
  `latest_run_dir`, `find_latest_run_folder`).
- Recorded variables: `gcc_common.RECORD_VARS_FRT` / `RECORD_VARS_FREQ`.
- Requirement values used by the checks: the `CONFIG` dict at the top of each
  check (limits, tolerances, Ti/Ts definitions). These are the natural
  targets for an `EvaluationRequest`.

---

## 9. Known limitations and open points

- **Project-specific values in the checks**: `CONFIG` in
  `frt_5_compliance_check.py` (Pmax 189.6 MW, FRT setpoints, 247 A current
  base, target fault durations) and `freq_4_compliance_check.py` (Pmax 189.6 MW)
  still hold NNWZ values. They should come from the project config / effective
  requirements.
- **Grid code vs project config**: some values differ between the two (e.g.
  reactive current response times; FRT clearance time 2.20 s in the YAML vs
  2.19 s target in the check). Precedence is not resolved in these scripts.
- **FRT stop time 15 s**: a loss of active power after 15 s is not seen (case
  1P-60U-Qmax lost P at 15.004 s in a 20 s run). The NBNL 10 s P recovery
  limit cannot be demonstrated in full within 15 s after a 3 s fault.
- **FSM on/off** is taken from the base case for all frequency cases: the LFSM
  cases need FSM off, the FSM and FSMINS cases need it on.
- **Ramp cases** get no frequency events from `freq_1` and are not evaluated
  by `freq_4` yet.
- **Ti / Ts definitions** in the checks are project proposals (25-1670 does not
  define them); the sample-based acceptance values are provisional until
  confirmed against the PowerFactory cursors.
- **Not yet verified in real PowerFactory** (only against a test double): the
  fixed y-axis ranges on the FRT plots, the PNG export (`ComWr`), and the new
  folder layout. Check the output window for warnings on the first run.

---

## 10. Troubleshooting

| Message | Fix |
|---|---|
| `No 'paths: root:' in the project config` | Add the `paths` section (section 4). |
| `Project config not found` | The config must be in `Working Scripts`, next to `gcc_common.py`. |
| `No FRT/FREQ raw data found ... Run frt_3 / freq_2 first` | Run the export step for the latest run, or set `frt_run` / `freq_run`. |
| `Element not found: ...` | `elements` in the config must match the PowerFactory `loc_name` exactly. |
| `Could not set y-axis range` | PowerFactory version differs in the plot axis object; ranges stay on auto. |
| `no file written (try another FORMAT ...)` | Set `FORMAT = "pdf"` in the plot export script. |
| Workbook not written, PermissionError | Close the workbook in Excel and rerun. |
