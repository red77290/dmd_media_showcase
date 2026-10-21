"""
Showcase MQTT Gateway coordinating the Embedded MQTT Broker (`:1883`) or External Broker,
publishing server online/LWT status (`dmd_showcase/server/status`), and routing DMD client
retained configurations (`dmd_showcase/clients/+/config`) and real-time events
(`dmd_showcase/clients/+/events`).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse

from app import __version__
from app.infrastructure.mqtt.embedded_broker import (
    EmbeddedMqttBroker,
    SimpleAsyncMqttClient,
)
from app.infrastructure.persistence.server_config_store import server_config_store

logger = logging.getLogger("dmd_showcase.mqtt.gateway")

SERVER_STATUS_TOPIC = "dmd_showcase/server/status"
ConfigHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
EventHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]


class ShowcaseMqttGateway:
    """
    Unified MQTT Control Plane gateway for `dmd_media_showcase`.
    """

    def __init__(self) -> None:
        self.embedded_broker: Optional[EmbeddedMqttBroker] = None
        self.external_client: Optional[SimpleAsyncMqttClient] = None
        self._on_client_config: Optional[ConfigHandler] = None
        self._on_client_event: Optional[EventHandler] = None
        self._running = False

    def set_handlers(
        self,
        on_client_config: ConfigHandler,
        on_client_event: EventHandler,
    ) -> None:
        self._on_client_config = on_client_config
        self._on_client_event = on_client_event

    async def start(self, bind_port: Optional[int] = None) -> None:
        if self._running:
            return
        self._running = True
        cfg = server_config_store.get_all()
        mode = str(cfg.get("mqtt_mode") or "embedded").lower()
        port = bind_port if bind_port is not None else int(cfg.get("mqtt_port") or 1883)
        ext_url = str(cfg.get("mqtt_external_url") or "").strip()

        if mode == "external" and ext_url:
            parsed = urlparse(ext_url if "://" in ext_url else f"mqtt://{ext_url}")
            host = parsed.hostname or "127.0.0.1"
            ext_port = parsed.port or 1883
            will_bytes = json.dumps({"online": False, "version": __version__}).encode("utf-8")
            self.external_client = SimpleAsyncMqttClient(
                host=host,
                port=ext_port,
                client_id="dmd_media_showcase_server",
                will_topic=SERVER_STATUS_TOPIC,
                will_payload=will_bytes,
                will_retain=True,
            )
            try:
                await self.external_client.connect()
                await self.external_client.subscribe("dmd_showcase/clients/+/config", self._on_raw_msg)
                await self.external_client.subscribe("dmd_showcase/clients/+/events", self._on_raw_msg)
                await self.publish_json(
                    SERVER_STATUS_TOPIC,
                    {"online": True, "version": __version__, "mode": "external"},
                    retain=True,
                )
                logger.info("[MQTT GATEWAY] Connected to external broker %s:%d", host, ext_port)
                return
            except Exception as exc:
                logger.warning(
                    "[MQTT GATEWAY] External broker %s:%d unreachable (%s), falling back to embedded",
                    host,
                    ext_port,
                    exc,
                )
                self.external_client = None

        self.embedded_broker = EmbeddedMqttBroker(host="0.0.0.0", port=port)
        self.embedded_broker.subscribe_internal("dmd_showcase/clients/+/config", self._on_raw_msg)
        self.embedded_broker.subscribe_internal("dmd_showcase/clients/+/events", self._on_raw_msg)
        await self.embedded_broker.start()
        await self.publish_json(
            SERVER_STATUS_TOPIC,
            {"online": True, "version": __version__, "mode": "embedded", "port": self.embedded_broker.port},
            retain=True,
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            await self.publish_json(
                SERVER_STATUS_TOPIC,
                {"online": False, "version": __version__},
                retain=True,
            )
        except Exception:
            pass
        if self.external_client is not None:
            await self.external_client.disconnect(graceful=True)
            self.external_client = None
        if self.embedded_broker is not None:
            await self.embedded_broker.stop()
            self.embedded_broker = None

    async def publish_json(self, topic: str, data: Dict[str, Any], retain: bool = False) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if self.external_client is not None:
            await self.external_client.publish(topic, raw, retain=retain)
        elif self.embedded_broker is not None:
            await self.embedded_broker.publish(topic, raw, retain=retain)

    async def _on_raw_msg(self, topic: str, payload: bytes, _retain: bool) -> None:
        if not payload:
            return
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != "dmd_showcase" or parts[1] != "clients":
            return
        dmd_id = parts[2]
        leaf = parts[3]
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict):
                return
        except Exception:
            return

        if leaf == "config" and self._on_client_config is not None:
            await self._on_client_config(dmd_id, decoded)
        elif leaf == "events" and self._on_client_event is not None:
            await self._on_client_event(dmd_id, decoded)


showcase_mqtt_gateway = ShowcaseMqttGateway()
