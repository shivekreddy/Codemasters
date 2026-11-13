# ==============================================================================
# GCC FRT: Export Plots
# ==============================================================================
# Step 4 of the FRT workflow. Writes the plot pages of the latest FRT_*
# run folder (or paths.frt_run from the project config) as image files:
#
#   <paths.root>\PowerFactory Plots\FRT\<run folder>\
#       <study case>\POC.png, GU Terminal 1.png, GU Terminal 2.png
#       (one folder per study case)
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


gcc.export_plots(powerfactory.GetApplication(), "FRT", __file__, FORMAT)
