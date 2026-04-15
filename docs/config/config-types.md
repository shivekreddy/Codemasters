# Type Definitions

## Table of Contents
- [Overview](#overview)
- [Type Aliases](#type-aliases)
- [Usage Examples](#usage-examples)
- [Related Documentation](#related-documentation)

---

## Overview

This module defines **shared literal type aliases** used across configuration and execution logic.

These types constrain allowed string values at type‑checking time and improve readability, consistency, and validation guarantees throughout the codebase.

All types in this module are implemented using `typing.Literal`.

---

## Type Aliases

### PlantType

```python
PlantType = Literal["type_1", "type_2"]
```

Defines the supported **plant classifications**.
Used to bind configuration and grid code requirements to a specific plant type.

---

### ScopeName

```python
ScopeName = Literal["common", "type_1", "type_2"]
```

Defines the **scope** of a grid code requirement:
- `common`: applicable to all plant types
- `type_1`: applicable to plant type 1
- `type_2`: applicable to plant type 2

---

### EvalMethod

```python
EvalMethod = Literal["rms", "emt", "loadflow", "short-circuit"]
```

Defines supported **evaluation or simulation methods** for grid code compliance checks.
Typical usage includes selecting the appropriate simulation backend or workflow.

---

### BackendName

```python
BackendName = Literal["powerfactory", "pscad", "psse"]
```

Defines the supported **simulation backend identifiers**.
Used to reference tool‑specific configuration and execution logic.

---

## Usage Examples

### Example 1 – Plant‑type dependent logic
```python
if cfg.plant_type == "type_1":
    handle_type_1()
```

### Example 2 – Backend selection
```python
if backend.name == "powerfactory":
    run_powerfactory(backend)
```

---

## Related Documentation

- [BaseConfig](base-config.md)
- [ProjectConfig](project-config.md)
- [RequirementsConfig](requirements-config.md)
- [ConfigLoader](config-loader.md)
- [BackendSpec](backend-config.md)