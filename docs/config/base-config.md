# BaseConfig

## Table of Contents
- [Overview](#overview)
- [Configuration Registry](#configuration-registry)
- [Usage Examples](#usage-examples)
- [Class Description](#class-description)
- [Properties](#properties)
- [Methods](#methods)
- [Related Documentation](#related-documentation)

---

## Overview

`BaseConfig` is the **common superclass for all configuration objects** in the system.
It defines the minimal structure shared by all configs and provides a default implementation for dictionary-based instantiation.

Concrete configuration classes (e.g. `ProjectConfig`, `RequirementsConfig`) are expected to subclass `BaseConfig` and override parsing behavior where needed.

---

## Configuration Registry

The configuration system maintains a **global registry** that maps a `config_type` string to the corresponding `BaseConfig` subclass.

This registry is populated using the `@register_config` decorator:

```python
@register_config("project_info")
class ProjectConfig(BaseConfig):
    ...
```

At runtime, this registry enables **dynamic selection of the correct config class** based solely on the `config_type` field contained in the configuration data.

The registry itself is implemented as an internal mapping:

```python
_CONFIG_REGISTRY: Dict[str, Type[BaseConfig]]
```

`BaseConfig` does not perform the lookup directly, but defines the common interface that all registered config classes adhere to.

---

## Usage Examples

### Example 1 – Default instantiation via subclass
```python
cfg = ProjectConfig.from_dict(config_data)
```

### Example 2 – Access raw configuration dictionary
```python
cfg.raw
```

---

## Class Description

### Short description

Base class for all configuration types.

### Technical description

`BaseConfig` is implemented as a `dataclass` and stores:
- the declared `config_type`
- the original, unmodified configuration dictionary (`raw`)
- any unknown or unsupported top-level keys (`extra`)

It provides a minimal `from_dict` factory method intended to be overridden by subclasses that require structured parsing, validation, or additional constructor arguments.

---

## Properties

| Property | Type | Description |
|---------|------|-------------|
| `config_type` | `str` | Configuration type identifier |
| `raw` | `Dict[str, Any]` | Original configuration dictionary |
| `extra` | `Dict[str, Any]` | Unknown or unsupported fields |

---

## Methods

### from_dict(d: Dict[str, Any]) → BaseConfig

Create a `BaseConfig` instance from a configuration dictionary.

This default implementation performs **no validation** and simply stores the provided data.
Subclasses are expected to override this method if structured parsing or additional context (e.g. plant type) is required.

**Parameters**

- `d: Dict[str, Any]`  
  Parsed configuration mapping

**Returns**

- `BaseConfig`  
  Base configuration instance

---

## Related Documentation

- [BaseConfig](base-config.md)
- [ProjectConfig](project-config.md)
- [RequirementsConfig](requirements-config.md)
- [ConfigLoader](config-loader.md)
- [TypeDefinitions](config-types.md)
- [BackendSpec](backend-config.md)