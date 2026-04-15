# Logging Setup

## Table of Contents
- [Overview](#overview)
- [Usage Examples](#usage-examples)
- [Function Description](#function-description)
- [Parameters](#parameters)
- [Behavior and Side Effects](#behavior-and-side-effects)
- [Related Documentation](#related-documentation)

---

## Overview

This module provides a **centralized logging setup function** for the application.

It configures:
- console logging (stdout)
- optional file logging
- a unified log format across all modules

Logging is configured **globally** using `logging.basicConfig`.

---

## Usage Examples

### Example 1 – Console logging only
```python
import logging
from logger import setup_logging

setup_logging(level=logging.INFO)
```

### Example 2 – Console and file logging
```python
setup_logging(
    level=logging.DEBUG,
    log_file="./logs/project.log"
)
```

If the directory of the log file does not exist, it is created automatically.

---

## Function Description

### setup_logging(level: int = logging.INFO, log_file: str | None = None) → None

Configure global logging handlers, format, and log level.

This function is intended to be called **once during application startup**.

### Technical description

- A `StreamHandler` is always created for console output.
- If `log_file` is provided:
  - parent directories are created if needed
  - a `FileHandler` is added
- Both handlers use the same formatter.
- `logging.basicConfig` is called with the constructed handler list.

---

## Parameters

| Parameter | Type | Description |
|----------|------|-------------|
| `level` | `int` | Logging level (e.g. `logging.INFO`, `logging.DEBUG`) |
| `log_file` | `str | None` | Optional path to a log file |

---

## Behavior and Side Effects

- Configures **global logging state**
- Overrides previously configured handlers
- Creates directories for `log_file` if they do not exist
- Affects all modules using the Python `logging` package

This function should therefore be called **once**, early in application startup.

---

## Related Documentation

- project.md
- config_loader.md
