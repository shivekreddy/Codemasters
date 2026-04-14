
from typing import Dict, Type, Any
from dataclasses import dataclass, field

_CONFIG_REGISTRY: Dict[str, Type['BaseConfig']] = {}

def register_config(config_type: str):
    """Add config type to config registry - Use as @register_config on baseconfig subclass
    """
    def deco(cls):
        _CONFIG_REGISTRY[config_type] = cls
        return cls
    return deco

# --------------------------------------------------------------------------------------------------------------
#                                           Base-Config (Superclass)
# --------------------------------------------------------------------------------------------------------------


@dataclass
class BaseConfig:
    config_type: str
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)   # Original dict
    extra: Dict[str, Any] = field(default_factory=dict)             # unknown fields


    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BaseConfig":
        # default: store everything, subclasses will override
        return cls(config_type=d.get("config_type"), raw=d)
    