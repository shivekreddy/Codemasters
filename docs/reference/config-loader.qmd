---
title: "ConfigLoader"
---

## 1. Short overview

Loads YAML configuration files and dispatches them to the correct config class based on `config_type`.

---

## 2. Class description

Thin orchestration layer responsible for file I/O and registry-based dispatch.

---

## 3. Function descriptions

### `__init__(path, **kwargs)`

Initialises the loader for a YAML file.

### `load()`

Loads the YAML file, resolves `config_type` and instantiates the config class.

---

## 4. Usage examples

```python
cfg = ConfigLoader('project.yaml').load()
```

```python
cfg = ConfigLoader('req.yaml', plant_type='type_2').load()
```