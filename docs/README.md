# GCC Automation Tool – Documentation

## Table of Contents
- [Overview](#overview)
- [Architecture at a Glance](#architecture-at-a-glance)
- [Documentation Structure](#documentation-structure)
- [Configuration Layer](#configuration-layer)
- [Runtime Layer](#runtime-layer)
- [Utilities and Analysis](#utilities-and-analysis)
- [Typical Entry Points](#typical-entry-points)
- [Contribution Guidelines](#contribution-guidelines)

---

## Overview

This documentation describes the **architecture, configuration handling, runtime execution, and supporting utilities** of the GCC Automation Tool.

The codebase is structured to clearly separate:
- **Configuration** – what should be executed
- **Runtime orchestration** – when and how it is executed
- **Utilities and analysis** – supporting and post‑processing tasks

This separation improves maintainability, testability, and extensibility for future grid codes, backends, and workflows.

---

## Architecture at a Glance

```text
YAML configuration
  → ConfigLoader
      → BaseConfig / ProjectConfig / RequirementsConfig
          → Project (runtime)
              → execution logic
              → simulation results
              → compliance checks
                  → analysis & plotting utilities
```

Configuration is fully loaded and validated before entering the runtime layer. The runtime layer owns all mutable execution state.

---

## Documentation Structure

```text
docs/
├─ README.md              ← top‑level entry (this file)
│
├─ configs/               ← configuration system
│   ├─ README.md
│   ├─ base_config.md
│   ├─ project_config.md
│   ├─ requirements_config.md
│   ├─ config_loader.md
│   ├─ backend_spec.md
│   └─ type_definitions.md
│
├─ runtime/               ← runtime & orchestration
│   ├─ README.md
│   ├─ project.md
│   └─ logger.md
│
├─ utils/                 ← supporting utilities
│   ├─ func_lib.md
│   └─ plotutils.md
```

Each folder contains a README that acts as a local landing page.

---

## Configuration Layer

The configuration layer defines **what should be executed**.

Core concepts:
- YAML‑based configuration files
- Explicit `config_type`
- Registry‑based dispatch
- Lightweight, warning‑based validation

Key components:
- `ConfigLoader`
- `ProjectConfig`
- `RequirementsConfig`

Start here:
- `docs/configs/README.md`

---

## Runtime Layer

The runtime layer turns validated configuration into **executable state**.

Key principles:
- Configuration is immutable input
- Runtime owns all mutable state
- `Project` is the single runtime entry point

Start here:
- `docs/runtime/README.md`

---

## Utilities and Analysis

Utility modules provide optional but important supporting functionality.

Included utilities:
- PowerFactory helpers (installation detection, project selection)
- Plotting and signal‑processing helpers (RMS, steady‑state, deviation)

These modules are stateless and mainly used for interactive engineering and reporting.

References:
- `docs/utils/func_lib.md`
- `docs/utils/plotutils.md`

---

## Typical Entry Points

- Understand configuration → `docs/configs/README.md`
- Execute or extend runtime logic → `docs/runtime/project.md`
- Work with PowerFactory → `docs/utils/func_lib.md`
- Analyze and visualize results → `docs/utils/plotutils.md`

---

## Contribution Guidelines

When extending the system:

- Add new config types via `@register_config`
- Keep parsing and validation out of the runtime layer
- Extend execution logic via `Project`
- Keep utilities stateless and explicit about side effects
- Update documentation together with code changes

Documentation is treated as part of the system design, not as an afterthought.
