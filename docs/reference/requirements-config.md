---
title: "RequirementsConfig"
---

## 1. Short overview

Represents grid code requirements grouped by plant type and scope.

---

## 2. Class description

Initialised with raw YAML and the selected `plant_type`.

---

## 3. Requirement entry

Each requirement includes name, applicability, scope and evaluation method.

---

## 4. Usage example

```python
req_cfg = ConfigLoader('req.yaml', plant_type='type_1').load()
```