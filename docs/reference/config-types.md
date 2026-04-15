---
title: "Config Types"
---

## 1. Short overview

Defines shared domain literals used by configuration classes.

---

## 2. Defined literals

- `PlantType`: type_1, type_2
- `ScopeName`: common, type_1, type_2
- `EvalMethod`: rms, emt, loadflow, short-circuit
- `BackendName`: powerfactory, pscad, psse

---

## 3. Usage example

```python
plant_type: PlantType = 'type_1'
```