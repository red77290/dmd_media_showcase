"""
Hexagonal Default Port Provider Registry (`app/domain/ports/default_ports.py`).

Allows the Composition Root (`app/container.py`) to bind concrete Infrastructure adapters
to Domain Ports, keeping `app/domain/` and `app/application/` 100% free of `app.infrastructure.*`
imports while preserving ergonomic zero-argument construction in tests regardless of import order.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict


class PortProviderRegistry:
    """Factory registry resolving default Hexagonal Port implementations."""

    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._bootstrapped = False

    def register(self, port_key: str, factory: Callable[[], Any]) -> None:
        """Bind a zero-arg factory returning a concrete adapter for `port_key`."""
        self._factories[port_key] = factory

    def resolve(self, port_key: str) -> Any:
        """Resolve `port_key`, bootstrapping the Composition Root once if needed."""
        if port_key not in self._factories and not self._bootstrapped:
            self._bootstrapped = True
            container_mod = importlib.import_module("app.container")
            register_fn = getattr(container_mod, "register_default_ports", None)
            if callable(register_fn):
                register_fn()

        factory = self._factories.get(port_key)
        if factory is None:
            raise LookupError(
                f"Hexagonal port '{port_key}' has not been registered in default_port_registry."
            )
        return factory()

    def is_registered(self, port_key: str) -> bool:
        return port_key in self._factories


default_port_registry = PortProviderRegistry()
