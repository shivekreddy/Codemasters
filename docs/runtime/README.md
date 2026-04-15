# Runtime Layer

## Table of Contents
- [Overview](#overview)
- [Runtime Architecture](#runtime-architecture)
- [Core Components](#core-components)
- [Typical Runtime Flow](#typical-runtime-flow)
- [Utilities and Helpers](#utilities-and-helpers)
- [Separation of Concerns](#separation-of-concerns)
- [Where to Go Next](#where-to-go-next)

---

## Overview

This section documents the **runtime layer** of the application.

The runtime layer is responsible for:
- turning validated configuration into executable objects
- orchestrating execution workflows
- holding runtime state such as results and intermediate data

It deliberately **does not perform configuration parsing or validation**. All configuration objects entering the runtime layer are assumed to be validated.

---

## Runtime Architecture

At a high level, the runtime layer sits **above the configuration system** and **below domain‑specific execution logic**:

```text
YAML configuration
  → ConfigLoader
      → BaseConfig / ProjectConfig / RequirementsConfig
          → Project (runtime)
              → execution logic
              → results & post‑processing
```

The `Project` class acts as the main runtime entry point.

---

## Core Components

### Project

`Project` is the **central runtime object**.

It wraps a `ProjectConfig` and:
- loads the associated grid‑code requirements
- exposes derived helpers (paths, backends, metadata)
- owns runtime containers for simulation and check results

All execution logic should depend on `Project`, not directly on configuration classes.

See: project.md

---

### Logging

Logging is configured centrally via `setup_logging`.

The runtime layer assumes logging has been initialized **once at application startup**.

Logging responsibilities:
- consistent log formatting
- optional file logging
- global availability across modules

See: logger.md

---

## Typical Runtime Flow

A minimal runtime setup usually follows this pattern:

```python
from config.config_loader import ConfigLoader
from runtime.project import Project
from runtime.logger import setup_logging

setup_logging()

proj_cfg = ConfigLoader("./project_info.yaml").load()
project = Project(proj_cfg)

# execute simulations / checks
# store results in project.simulation_results / project.check_results
```

This keeps responsibilities clearly separated:
- configuration → ConfigLoader
- execution context → Project
- side effects → runtime logic

---

## Utilities and Helpers

### PowerFactory helpers (`func_lib`)

Utility functions for:
- locating PowerFactory installations
- instantiating PowerFactory Python APIs
- selecting PowerFactory projects interactively

These helpers are **Windows‑only** and intended for interactive engineering workflows.

See: func_lib.md

---

### Plot utilities (`plotutils`)

Stateless helper functions for:
- DNV‑style plotting
- signal post‑processing (RMS, steady‑state, deviation)
- small Matplotlib layout adjustments

Used primarily during analysis and reporting, not execution.

See: plotutils.md

---

## Separation of Concerns

The runtime layer follows these design rules:

- Configuration objects are **immutable inputs**
- Runtime objects may hold mutable state
- Utilities are stateless and reusable
- Plotting and analysis are kept out of core execution

This separation keeps testing, extension, and refactoring manageable.

---

## Where to Go Next

Depending on your task:

- Start with project.md to understand the runtime entry point
- Read logger.md to understand logging side effects
- Consult func_lib.md when working with PowerFactory
- Use plotutils.md for result visualization and analysis

For configuration details, refer back to the configuration documentation section.
