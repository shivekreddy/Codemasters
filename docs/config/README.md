# Configuration Handling

## Table of Contents
- [Overview](#overview)
- [Configuration Flow](#configuration-flow)
- [Configuration Types](#configuration-types)
- [Core Components](#core-components)
- [Validation Strategy](#validation-strategy)
- [Where to Go Next](#where-to-go-next)

---

## Overview

This section documents how **configuration files are structured, loaded, validated, and accessed** within the project.

The configuration system is built around:
- YAML configuration files
- A registry-based dispatch mechanism (`config_type` → config class)
- Explicit config classes with structured accessors
- Lightweight, template-based validation

All configuration handling is **repo-local** and does not rely on external services.

---

## Configuration Flow

The high-level configuration flow is:

1. A YAML configuration file is provided (e.g. `project_info.yaml`).
2. The file declares a `config_type`.
3. `ConfigLoader` parses the YAML and validates its structure.
4. The correct configuration class is selected via the registry.
5. The configuration is instantiated via `from_dict(...)`.
6. Consumers interact with a strongly-typed config object.

```text
YAML file
  → ConfigLoader
      → structural validation (template)
      → config registry lookup
      → BaseConfig.from_dict(...)
          → concrete config instance
```

---

## Configuration Types

The following configuration types are currently supported:

| Config Type | Description | Documentation |
|------------|-------------|---------------|
| `project_info` | Project-level metadata, backends, paths | project_config.md |
| `grid_code_requirements` | Grid code requirements, plant-type scoped | requirements_config.md |

Each configuration file must declare its type explicitly via the `config_type` field.

---

## Core Components

### ConfigLoader

Responsible for:
- Reading YAML files
- Structural validation against templates
- Selecting the correct config class

See: config_loader.md

---

### BaseConfig

Common superclass for all configuration objects.
Defines:
- shared attributes (`config_type`, `raw`, `extra`)
- the contract for `from_dict(...)`
- integration with the configuration registry

See: base_config.md

---

### Configuration Registry

A global registry maps `config_type` strings to concrete config classes.

Registration is done via the `@register_config` decorator:

```python
@register_config("project_info")
class ProjectConfig(BaseConfig):
    ...
```

This enables dynamic dispatch without hard-coded conditionals.

---

### Supporting Models and Types

- BackendSpec → backend definitions (backend_spec.md)
- Requirement / RequirementsConfig → grid code requirements (requirements_config.md)
- Shared literal types (`PlantType`, `ScopeName`, ...) → type_definitions.md

---

## Validation Strategy

Validation is intentionally **lightweight and non-blocking**:

- Structural validation is performed using YAML templates.
- Missing keys and type mismatches produce warnings.
- Extra keys are logged but allowed.
- Config-type-specific validation may append additional warnings.

Warnings do **not** prevent successful loading.
This allows forward compatibility and project-specific extensions.

---

## Where to Go Next

If you are new to the configuration system:

1. Read project_config.md to understand the main entry point.
2. Read config_loader.md to see how configs are instantiated and validated.
3. Read requirements_config.md if working with grid code compliance.

For reference:
- base_config.md
- backend_spec.md
- type_definitions.md
