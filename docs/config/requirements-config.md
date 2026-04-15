# RequirementsConfig

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Class Description](#class-description)
- [Properties](#properties)
- [Methods](#methods)
- [Requirement Data Model](#requirement-data-model)
- [Related Documentation](#related-documentation)

---

## Overview

`RequirementsConfig` represents a **grid code requirements configuration**, bound to a specific plant type.
It provides structured and convenience-based access to individual grid code requirements, including applicability flags, evaluation methods, and requirement data.

The configuration is registered under the config type **`grid_code_requirements`** and is typically loaded after `ProjectConfig`, using the plant type defined there.

---

## Usage Examples

### Example 1 – Create requirements config from dictionary
```python
req_cfg = RequirementsConfig.from_dict(data, plant_type="type_2")
```

### Example 2 – Access a requirement by name (convenience access)
```python
req = req_cfg.frequency_support
req.applicable
req.data
```

### Example 3 – Iterate over applicable requirements
```python
for r in req_cfg.iter_applicable():
    evaluate(r)
```

---

## Class Description

### Short description

Plant‑type‑aware container for grid code requirements.

### Technical description

`RequirementsConfig` is a subclass of `BaseConfig` that splits requirements into logical scopes:
- `common`
- `type_1`
- `type_2`

At runtime, the active plant type determines which scoped requirements are considered.

The class provides:
- explicit lookup via `get_requirement`
- dynamic attribute access via `__getattr__`
- iterators for planning and execution workflows

---

## Properties

| Property | Type | Description |
|---------|------|-------------|
| `raw` | `Dict[str, Any]` | Original configuration mapping |
| `plant_type` | `PlantType` | Active plant type (`type_1` or `type_2`) |
| `meta` | `Dict[str, Any]` | Optional metadata section |
| `assumptions` | `Dict[str, Any]` | Grid code assumptions |

Internal structures:

| Property | Type | Description |
|---------|------|-------------|
| `_sections` | `Dict[ScopeName, Dict[str, Any]]` | Scoped requirement blocks |

---

## Methods

### from_dict(d: Dict[str, Any], plant_type: PlantType) → RequirementsConfig

Create a `RequirementsConfig` instance from a configuration dictionary and plant type.

**Parameters**

- `d: Dict[str, Any]`  
  Parsed configuration mapping
- `plant_type: PlantType`  
  Active plant type

**Returns**

- `RequirementsConfig`

**Errors**
- `ValueError` if `config_type` is invalid

---

### get_requirement(name: str, scope: ScopeName) → Requirement

Return a single requirement object for the given scope.

**Errors**
- `KeyError` if the requirement does not exist
- `TypeError` if the requirement block is malformed

---

### iter_requirements() → Iterator[Requirement]

Iterate over requirements relevant for the current plant type:
- all `common` requirements
- all requirements of the active plant type scope

---

### iter_applicable() → Iterator[Requirement]

Iterate only over requirements with `applicable == True`.

---

## Requirement Data Model

### Requirement

Represents a **single grid code requirement** with applicability and evaluation context.

| Property | Type | Description |
|---------|------|-------------|
| `name` | `str` | Requirement identifier |
| `scope` | `ScopeName` | Scope (`common`, `type_1`, `type_2`) |
| `applicable` | `Optional[bool]` | Applicability flag |
| `evaluation_method` | `Optional[EvalMethod]` | Evaluation method |
| `data` | `Dict[str, Any]` | Requirement content |

---

## Related Documentation

- [BaseConfig](base-config.md)
- [ProjectConfig](project-config.md)
- [ConfigLoader](config-loader.md)
- [TypeDefinitions](config-types.md)
- [BackendSpec](backend-config.md)