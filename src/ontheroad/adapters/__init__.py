"""Agent adapters: one ABC, typed events, and a name registry.

Importing this package registers the built-in adapters ("echo", "hermes").
"""

from .base import (
    AgentAdapter,
    AgentEvent,
    available_adapters,
    get_adapter_class,
    register_adapter,
)
from .echo import EchoAdapter
from .hermes_acp import HermesACPAdapter

__all__ = [
    "AgentAdapter",
    "AgentEvent",
    "EchoAdapter",
    "HermesACPAdapter",
    "available_adapters",
    "get_adapter_class",
    "register_adapter",
]
