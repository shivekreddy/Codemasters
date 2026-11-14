# ==============================================================================
# GCC — Raw Results Export per Plot (FRT)
# ==============================================================================
# Exports the simulation results of every study case in ONE study case folder,
# split the same way as the FRT plot pages: one folder per study case, and in
# it one CSV per plot page holding exactly that page's variables.
#
# Output:  <paths.root>\FRT Raw Data\<FRT run folder>\
#            FRT 1PH 0.05 Q0\
#              POC.csv               POC busbar voltages + POC breaker P/Q/I
#              GU Terminal.csv       one plant:  GU busbar + inverter
#              (GU Terminal 1.csv    two plants: plant 1
#               GU Terminal 2.csv                plant 2)
#            FRT 1PH 0.05 Qmax\
#              ...
#            export_manifest.json    cases, files, columns, missing variables
#
# The pages and their elements come from the PROJECT CONFIG (elements +
# plot_labels): plant 2 configured (gu_busbar2 / inverter2) -> two GU pages.
# Column headers match the plot legends: "<name> <variable> (<unit>)",
# e.g. "Inverter 1 Total Active Power (MW)", plus time_s first.
# NOTE: with adaptive step size the time step is not uniform — interpolate on
# time_s, never assume a fixed row interval.
#
# Cases without simulation results are skipped and listed at the end. A
# variable that is not in a case's result file is left blank and listed.
#
# Which run and where to: from the project config in Working Scripts
# (paths.root; the latest FRT_* run, or paths.frt_run if set). Nothing to set
# in this script. To export only a few cases into another folder, use
# frt_3b_export_selected_cases.py.
#
# Usage: point a ComPython object at this script and run it from PowerFactory.
# Dependencies: PyYAML, gcc_common.py in Working Scripts (one folder up).
# ==============================================================================

import os
import sys

import powerfactory

# gcc_common.py and the project config live one folder up (Working Scripts)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.pop("gcc_common", None)      # always load gcc_common.py fresh
import gcc_common as gcc                 # noqa: E402


gcc.export_frt_raw(powerfactory.GetApplication(), __file__)
