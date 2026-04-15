---
title: "Config Registry"
---

## 1. Short overview

The config registry maps `config_type` strings to config classes.

---

## 2. Decorator description

### register_config

Registers a `BaseConfig` subclass under a given config type string.

---

## 3. Function description

```python
@register_config('project_info')
class ProjectConfig(BaseConfig):
    ...
```

---
## 4. Usage note

The registry is populated by importing config modules.