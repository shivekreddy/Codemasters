---
title: "BackendSpec"
---

## 1. Short overview

`BackendSpec` describes one simulation backend entry from the project config.

---

## 2. Class description

Stores backend metadata such as name, version, executable paths and settings.

---

## 3. Field descriptions

- `id`: backend identifier
- `name`: backend name
- `enabled`: activation flag
- `settings`: backend-specific parameters

---

## 4. Usage example

```python
backend = BackendSpec(id='pf', name='powerfactory', enabled=True)
```