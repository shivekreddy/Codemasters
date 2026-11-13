# ==============================================================================
# GCC Export Plots (FRT and Frequency)
# ==============================================================================
# Writes the plot pages of the LATEST FRT_* and/or FREQ_* run folder in Study
# Cases (or paths.frt_run / paths.freq_run from the project config) as image
# files:
#
#   <paths.root>\PowerFactory Plots\
#       FRT\<FRT run folder>\<study case>\POC.png
#                                         \GU Terminal 1.png ...
#       Frequency\<FREQ run folder>\<study case> - Plot 1 - Main Results.png ...
#
#   e.g. ...\PowerFactory Plots\FRT\FRT_2026-11-11_13-56-23 (PPC Enabled)\
#            FRT 3PH 0.05 Q0\POC.png
#        ...\PowerFactory Plots\Frequency\FREQ_2026-11-12_16-50-24\
#            LFSMO Step - Plot 2 - Active power with labels.png
#
# Folders are created when missing; existing files are overwritten.
# Cases without simulation results are skipped (listed at the end).
# The folder comes from the project config next to this script (paths.root).
#
# Usage: set FRT / FREQUENCY below, point a ComPython object at this script
# and run it from PowerFactory after the simulations have finished.
# Needs gcc_common.py in the same folder.
# ==============================================================================

import os
import re
import sys
import fnmatch

import powerfactory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.modules.pop("gcc_common", None)      # always load gcc_common.py fresh from this folder
import gcc_common as gcc                 # noqa: E402


# ------------------------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------------------------
FRT       = 1    # 1 = export the FRT plots, 0 = skip
FREQUENCY = 0    # 1 = export the frequency plots, 0 = skip

# Image format of PowerFactory's "Write graphic" command (ComWr):
#   "png" (default), "pdf", "svg", "emf", "wmf", "bmp"
FORMAT = "png"


# ==============================================================================

# Per study: run-folder prefix, output subfolder, plot pages to export (as
# created by gcc_frt_script.py / gcc_freq_script.py; * = wildcard), and
# whether each study case gets its own folder.
STUDIES = {
    "FRT": {
        "prefix": "FRT", "kind": "frt_plots",
        "pages": ["POC", "GU Terminal*"],
        "folder_per_case": True,
    },
    "Frequency": {
        "prefix": "FREQ", "kind": "freq_plots",
        "pages": ["* Plot 1 - *", "* Plot 2 - *", "* Plot 3 - *"],
        "folder_per_case": False,
    },
}

_BAD_CHARS = re.compile(r'[<>:"/\\|?*]')                     # not allowed in Windows names


def run_folder_for(app, cfg, prefix):
    """paths.frt_run / paths.freq_run from the config, else the latest '<prefix>_*' run folder."""
    study = app.GetProjectFolder("study")
    if study is None:
        raise RuntimeError("No Study Cases folder: activate the project first.")
    name = gcc.run_name_setting(cfg, prefix)
    if name:
        found = study.GetContents(f"{name}.IntFolder")
        if not found:
            raise RuntimeError(f"Run folder '{name}' (paths.{prefix.lower()}_run) not in Study Cases.")
        return found[0]
    return gcc.find_latest_run_folder(study, prefix)


def safe(name):
    """A PowerFactory name as a valid Windows file or folder name."""
    return _BAD_CHARS.sub("_", name).strip().rstrip(".")


def has_results(app):
    """True if the active case's result file holds simulation data."""
    inc = app.GetFromStudyCase("ComInc")
    elmRes = inc.p_resvar if inc is not None else None
    if elmRes is None:
        return False
    elmRes.Load()
    try:
        return elmRes.GetNumberOfRows() > 0
    finally:
        elmRes.Release()


def plot_pages(app, patterns):
    """The active case's plot pages matching the patterns, in pattern order."""
    board = app.GetGraphicsBoard()
    if board is None:
        return []
    pages = board.GetContents("*.GrpPage") or []
    out = []
    for pat in patterns:
        out += sorted((p for p in pages if fnmatch.fnmatch(p.loc_name, pat)
                       and p not in out), key=lambda p: p.loc_name)
    return out


def export_page(app, page, path):
    """Show the page and write it to path with ComWr (overwrites). Error or None."""
    app.GetGraphicsBoard().Show(page)
    if os.path.isfile(path):
        os.remove(path)                       # overwrite, and a stale file can't fake success
    comWr = app.GetFromStudyCase("ComWr")
    comWr.iopt_rd    = FORMAT
    comWr.iopt_savas = 0                      # write to f without a dialog
    comWr.f          = path
    if comWr.Execute():
        return "export command failed"
    if not os.path.isfile(path):
        return "no file written (try another FORMAT, e.g. 'pdf' or 'wmf')"
    return None


def export_study(app, label, spec, cfg):
    """Export every case of the run folder of one study. Returns a summary."""
    app.PrintPlain(f"\n--- {label} " + "-" * (66 - len(label)))
    run_folder = run_folder_for(app, cfg, spec["prefix"])
    if run_folder is None:
        app.PrintWarn(f"  No {spec['prefix']}_* run folder in Study Cases: nothing exported.")
        return {"label": label, "files": 0, "out": None, "no_results": [], "failed": []}

    out_dir = gcc.results_dir(cfg, spec["kind"], safe(run_folder.loc_name))
    cases = sorted(run_folder.GetContents("*.IntCase"), key=lambda c: c.loc_name)
    app.PrintPlain(f"  Run folder : {run_folder.loc_name}   ({len(cases)} cases)")
    app.PrintPlain(f"  Output     : {out_dir}")

    n_files, no_results, failed = 0, [], []
    for case in cases:
        name = case.loc_name
        case.Activate()
        if not has_results(app):
            no_results.append(name)
            continue
        pages = plot_pages(app, spec["pages"])
        if not pages:
            failed.append((name, "no plot pages found ("
                           + ", ".join(spec["pages"]) + ")"))
            continue

        written = []
        for page in pages:
            if spec["folder_per_case"]:
                case_dir = os.path.join(out_dir, safe(name))
                os.makedirs(case_dir, exist_ok=True)
                path = os.path.join(case_dir, f"{safe(page.loc_name)}.{FORMAT}")
            else:
                # 'LFSMO Plot 1 - Main Results' -> 'LFSMO Step - Plot 1 - Main Results'
                short = re.sub(r"^.*?(?=Plot \d)", "", page.loc_name) or page.loc_name
                path = os.path.join(out_dir, f"{safe(name)} - {safe(short)}.{FORMAT}")
            error = export_page(app, page, path)
            if error:
                failed.append((f"{name} / {page.loc_name}", error))
            else:
                written.append(page.loc_name)
                n_files += 1
        if written:
            app.PrintPlain(f"  {name:28s} -> {', '.join(written)}")

    active = app.GetActiveStudyCase()
    if active is not None:
        active.Deactivate()
    return {"label": label, "files": n_files, "out": out_dir,
            "no_results": no_results, "failed": failed}


def main():
    app = powerfactory.GetApplication()
    app.ClearOutputWindow()

    app.PrintPlain("=" * 72)
    app.PrintPlain("  GCC Export Plots")
    app.PrintPlain("=" * 72)
    try:
        config_file = gcc.resolve_config_path(__file__)
        cfg = gcc.load_yaml(config_file)
        root = gcc.results_root(cfg)
    except Exception as exc:
        app.PrintError(f"Project config / paths.root: {exc}")
        return
    app.PrintPlain(f"  Config    : {config_file}")
    app.PrintPlain(f"  Directory : {os.path.join(root, 'PowerFactory Plots')}")
    app.PrintPlain(f"  FRT       : {FRT}")
    app.PrintPlain(f"  Frequency : {FREQUENCY}")
    app.PrintPlain(f"  Format    : {FORMAT}")

    selected = [(label, spec) for label, spec, on in
                (("FRT", STUDIES["FRT"], FRT), ("Frequency", STUDIES["Frequency"], FREQUENCY))
                if on]
    if not selected:
        app.PrintWarn("FRT and Frequency are both 0: nothing to export.")
        return

    summaries = []
    for label, spec in selected:
        try:
            summaries.append(export_study(app, label, spec, cfg))
        except Exception as exc:            # one study never stops the other
            app.PrintError(f"  {label} export aborted: {exc}")

    app.PrintPlain("")
    app.PrintPlain("=" * 72)
    app.PrintPlain("  SUMMARY")
    for s in summaries:
        if s["out"] is None:
            app.PrintPlain(f"  {s['label']:9s}: no run folder found")
            continue
        app.PrintPlain(f"  {s['label']:9s}: {s['files']} plot(s) -> {s['out']}")
        if s["no_results"]:
            app.PrintPlain(f"             skipped, no simulation results ({len(s['no_results'])}): "
                           + ", ".join(s["no_results"]))
        for what, error in s["failed"]:
            app.PrintWarn(f"             FAILED  {what}: {error}")
    app.PrintPlain("=" * 72)


main()
