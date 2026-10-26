"""
Domain Port: `IMqttControlGatewayPort` for MQTT control-plane publication and presence inspection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, Optional

ConfigHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
EventHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]


class IMqttControlGatewayPort(ABC):
    """Hexagonal port decoupling Application Services from the underlying MQTT broker/client."""

    @abstractmethod
    def set_handlers(
        self,
        on_client_config: ConfigHandler,
        on_client_event: EventHandler,
        on_client_hello: Optional[ConfigHandler] = None,
    ) -> None:
        """Register inbound MQTT handlers for client `config`, `events`, and `hello` topics."""

    @abstractmethod
    async def publish_json(
        self, topic: str, data: Dict[str, Any], retain: bool = False
    ) -> None:
        """Publish a JSON control message (`ready`, `command`, `referential`, `status`)."""
