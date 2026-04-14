---
title: "BaseConfig"
---

## 1. Short overview

`BaseConfig` is the common superclass for all configuration objects. It stores the raw YAML data and unknown fields.

---

## 2. Class description

### BaseConfig

Holds generic configuration data shared by all configs.

---

## 3. Field descriptions

- `config_type`: Declares which config subclass should handle the data
- `raw`: Original YAML mapping
- `extra`: Unknown or unparsed fields

---

## 4. Usage example

```python
cfg = BaseConfig(config_type='example', raw=data)
```