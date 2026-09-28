# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

GCC Automation Tool: automates Grid Code Compliance (GCC) studies in DIgSILENT PowerFactory.
The pipeline is: load project + grid-code requirement configs → set up PF study cases/events →
run simulations → compare results against requirement curves → plot. See `README.md` for the
module roadmap and `docs/` for class/function documentation.

## Commands

- Run the example entry point: `uv run main.py` (loads `config/project_test_config.yaml`, prints applicable requirements)
- Install/sync dependencies: `uv sync` (Python 3.13, see `.python-version`)
- There is no test suite or linter configured yet.

## Layout

- `main.py` — entry point / usage example. Must be run from the repo root (paths are relative).
- `src/gcc_automation_tool/` — the tool package
  - `config/` — YAML config system: `ConfigLoader` detects the config type and builds a
    `BaseConfig` subclass (`ProjectConfig`, requirements config, backend config) via `_CONFIG_REGISTRY`.
    New config types: add a subclass + a template entry in `config_template` in `config_loader.py`.
  - `project.py` — runtime `Project` wrapping a `ProjectConfig`; auto-loads requirements.
  - `func_lib.py` — shared helpers, incl. getting a PowerFactory instance. Put functions used by
    more than one module here, and check here before writing a new helper.
  - `plotutils.py` — plotting (uses `styles/DNV.mplstyle` / `DNV_colors.py`).
  - `ARCHIVE/` — old code, kept for reference only. Do not edit or import from it.
- `config/` — YAML configs: per-country grid-code requirement files
  (`<country>_<code>_type<B|C|D>.yaml`), project configs, and templates in `config/master_templates/`.
- `FRT Scripts/` — standalone PowerFactory scripts (e.g. `NL_GCC_FRT_setup_rev3.py`), run from inside
  PowerFactory via a ComPython object. Not yet integrated into the package.
- `docs/` — Markdown documentation per module.

## PowerFactory constraints

- Anything that does `import powerfactory` only runs on Windows with PowerFactory installed.
  In the cloud/Linux sandbox this code can be edited but not executed — say so rather than
  claiming it was tested. Config loading, result comparison and plotting can be run anywhere.
- PF scripts use Windows paths (raw strings `r"C:\..."`) and PowerFactory's bundled Python;
  keep them compatible with that interpreter and avoid adding dependencies beyond PyYAML there.
- Model element names are project-specific; keep them in config/constants at the top of scripts
  (or `config/element_mapper.yaml`), never hard-coded deep in logic.

## Conventions

- Imports within the package use the `src.gcc_automation_tool...` prefix (see `main.py`, `project.py`).
- Use the `logging` module (`logger = logging.getLogger(__name__)`), not `print`, in library code.
  Logging is configured once via `logger.setup_logging()`.
- Add docstrings and comments to new functions, and update the matching page in `docs/` when
  changing a module's public behaviour.
- If a module needs new inputs/data, note it in `module_requirements.md` (referenced in README).
- Don't commit generated output: `output/`, `*.csv`, `*.xlsx`, `*.log` are gitignored.
