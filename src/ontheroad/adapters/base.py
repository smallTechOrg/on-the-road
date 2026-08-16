"""AgentAdapter ABC, AgentEvent, and the adapter registry.

Adapters normalize any coding agent into one interface emitting typed
``AgentEvent``s. Adapters NEVER assign ``seq`` numbers — the event store does,
at persist time.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

# Lowercase snake event type literals (see spec/architecture.md).
EVENT_TYPES = frozenset(
    {
        "user_message",
        "agent_text",
        "agent_thought",
        "tool_start",
        "tool_update",
        "tool_end",
        "usage",
        "git_status",
        "permission_request",
        "permission_response",
        "status",
        "turn_end",
        "error",
    }
)


@dataclass(frozen=True)
class AgentEvent:
    """One normalized agent event. ``seq`` is assigned by the store, never here."""

    type: str
    payload: dict
    ts: float = field(default_factory=time.time)


class AgentAdapter(ABC):
    """The core interface every agent integration implements."""

    name: str  # registry key, e.g. "hermes", "echo"

    @abstractmethod
    async def start(self, workdir: Path, resume_ref: str | None) -> str:
        """Spawn the subprocess and handshake.

        Returns the adapter-side session ref (persisted for resume).
        """

    @abstractmethod
    async def send_user_message(self, text: str) -> None:
        """Forward one user message; the turn streams via :meth:`events`."""

    @abstractmethod
    def events(self) -> AsyncIterator[AgentEvent]:
        """Normalized event stream for this adapter instance."""

    @abstractmethod
    async def respond_permission(self, request_id: str, option_id: str) -> None:
        """Answer a pending permission request."""

    @abstractmethod
    async def cancel(self) -> None:
        """Interrupt the current turn."""

    @abstractmethod
    async def stop(self) -> None:
        """Terminate the subprocess."""


_REGISTRY: dict[str, type[AgentAdapter]] = {}


def register_adapter(cls: type[AgentAdapter]) -> type[AgentAdapter]:
    """Class decorator: register an adapter under its ``name``."""
    if not getattr(cls, "name", None):
        raise ValueError(f"adapter class {cls.__name__} has no name")
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter_class(name: str) -> type[AgentAdapter]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown adapter: {name!r} (available: {available_adapters()})")


def available_adapters() -> list[str]:
    return sorted(_REGISTRY)
