---
title: ProjectConfig
---

## 1. Short overview

Represents the `project_info` configuration.

---

## 2. Class description

`ProjectConfig` stores project metadata, plant type, config paths and backend definitions.

---

## 3. Key attributes

- `meta`
- `plant`
- `configs`
- `backends`
- `results`

---

## 4. Usage example

```python
project_cfg = ConfigLoader('project.yaml').load()
```