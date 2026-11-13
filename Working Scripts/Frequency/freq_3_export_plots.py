# ==============================================================================
# GCC Frequency: Export Plots
# ==============================================================================
# Step 3 of the Frequency workflow. Writes the plot pages of the latest FREQ_*
# run folder (or paths.freq_run from the project config) as image files:
#
#   <paths.root>\PowerFactory Plots\Frequency\<run folder>\
#       <study case> - Plot 1 - Main Results.png, ... Plot 2, Plot 3
#
# Folders are created when missing; existing files are overwritten. Cases
# without simulation results are skipped (listed at the end). paths.root is
# set in the project config in Working Scripts; gcc_common.py must be there
# too. Point a ComPython object at this script and run it from PowerFactory
# after the simulations have finished.
# ==============================================================================

import os
import sys

import powerfactory

# gcc_common.py and the project config live one folder up (Working Scripts)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.pop("gcc_common", None)      # always load gcc_common.py fresh
import gcc_common as gcc                 # noqa: E402


# Image format of PowerFactory's "Write graphic" command (ComWr):
#   "png" (default), "pdf", "svg", "emf", "wmf", "bmp"
FORMAT = "png"


gcc.export_plots(powerfactory.GetApplication(), "Frequency", __file__, FORMAT)
