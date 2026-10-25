"""
Showcase MQTT Gateway coordinating the Embedded MQTT Broker (`:1883`) or External Broker,
publishing server online/LWT status (`dmd_showcase/server/status`) and consolidated catalog
referentials (`dmd_showcase/catalog/{domain}/referential`), and routing DMD client retained
configurations (`dmd_showcase/clients/+/config`) and real-time events
(`dmd_showcase/clients/+/events`).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse

from app import __version__
from app.domain.taxonomy import ReferentialConsolidator
from app.infrastructure.mqtt.embedded_broker import (
    EmbeddedMqttBroker,
    SimpleAsyncMqttClient,
)
from app.infrastructure.persistence.server_config_store import server_config_store

logger = logging.getLogger("dmd_showcase.mqtt.gateway")

SERVER_STATUS_TOPIC = "dmd_showcase/server/status"
ConfigHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
EventHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]


def _summarize_payload_for_log(data: Dict[str, Any]) -> str:
    """Format an MQTT JSON payload for `[INFO]` logs, compacting base64 poster bytes."""
    copy_dict: Dict[str, Any] = {}
    for k, v in data.items():
        if k == "poster_rgb565_b64" and isinstance(v, str) and len(v) > 32:
            copy_dict[k] = f"<b64:{len(v)} chars>"
        elif k == "card" and isinstance(v, dict):
            card_copy = dict(v)
            b64 = card_copy.get("poster_rgb565_b64")
            if isinstance(b64, str) and len(b64) > 32:
                card_copy["poster_rgb565_b64"] = f"<b64:{len(b64)} chars>"
            synopsis = card_copy.get("synopsis")
            if isinstance(synopsis, str) and len(synopsis) > 80:
                card_copy["synopsis"] = synopsis[:77] + "..."
            copy_dict[k] = card_copy
        elif k in ("systems", "genres", "decades") and isinstance(v, list) and len(v) > 8:
            copy_dict[k] = f"<{len(v)} items>"
        else:
            copy_dict[k] = v
    return json.dumps(copy_dict, ensure_ascii=False)


class ShowcaseMqttGateway:
    """
    Unified MQTT Control Plane gateway for `dmd_media_showcase`.
    """

    def __init__(self) -> None:
        self.embedded_broker: Optional[EmbeddedMqttBroker] = None
        self.external_client: Optional[SimpleAsyncMqttClient] = None
        self._on_client_config: Optional[ConfigHandler] = None
        self._on_client_event: Optional[EventHandler] = None
        self._on_client_hello: Optional[ConfigHandler] = None
        self._running = False

    def set_handlers(
        self,
        on_client_config: ConfigHandler,
        on_client_event: EventHandler,
        on_client_hello: Optional[ConfigHandler] = None,
    ) -> None:
        self._on_client_config = on_client_config
        self._on_client_event = on_client_event
        self._on_client_hello = on_client_hello

    async def publish_catalog_referentials(self, lang: str = "fr") -> None:
        """Publish consolidated scraper referentials as retained MQTT topics for all 4 domains."""
        for dom in ("games", "movies", "series", "anime"):
            ref = ReferentialConsolidator.build_domain_referential(dom, lang=lang)  # type: ignore[arg-type]
            await self.publish_json(f"dmd_showcase/catalog/{dom}/referential", ref, retain=True)

    async def start(self, bind_port: Optional[int] = None) -> None:
        if self._running:
            return
        self._running = True
        cfg = server_config_store.get_all()
        mode = str(cfg.get("mqtt_mode") or "embedded").lower()
        port = bind_port if bind_port is not None else int(cfg.get("mqtt_port") or 1883)
        ext_url = str(cfg.get("mqtt_external_url") or "").strip()
        default_lang = str(cfg.get("default_lang") or "fr")

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
                await self.external_client.subscribe("dmd_showcase/clients/+/hello", self._on_raw_msg)
                await self.external_client.subscribe("dmd_showcase/clients/+/status", self._on_raw_msg)
                await self.external_client.subscribe("dmd_showcase/clients/+/events", self._on_raw_msg)
                await self.external_client.subscribe("dmd_showcase/clients/+/+/events", self._on_raw_msg)
                await self.publish_json(
                    SERVER_STATUS_TOPIC,
                    {"online": True, "version": __version__, "mode": "external"},
                    retain=True,
                )
                await self.publish_catalog_referentials(lang=default_lang)
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
        self.embedded_broker.subscribe_internal("dmd_showcase/clients/+/hello", self._on_raw_msg)
        self.embedded_broker.subscribe_internal("dmd_showcase/clients/+/status", self._on_raw_msg)
        self.embedded_broker.subscribe_internal("dmd_showcase/clients/+/events", self._on_raw_msg)
        self.embedded_broker.subscribe_internal("dmd_showcase/clients/+/+/events", self._on_raw_msg)
        await self.embedded_broker.start()
        await self.publish_json(
            SERVER_STATUS_TOPIC,
            {"online": True, "version": __version__, "mode": "embedded", "port": self.embedded_broker.port},
            retain=True,
        )
        await self.publish_catalog_referentials(lang=default_lang)

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
        logger.info(
            "[MQTT OUT] --> topic='%s' (retain=%s) | payload=%s",
            topic,
            retain,
            _summarize_payload_for_log(data),
        )
        if self.external_client is not None:
            await self.external_client.publish(topic, raw, retain=retain)
        elif self.embedded_broker is not None:
            await self.embedded_broker.publish(topic, raw, retain=retain)

    async def _on_raw_msg(self, topic: str, payload: bytes, retain: bool) -> None:
        if not payload:
            return
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != "dmd_showcase" or parts[1] != "clients":
            return
        dmd_id = parts[2]
        leaf = parts[-1]
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict):
                return
        except Exception as exc:
            logger.warning("[MQTT IN] Invalid JSON on topic='%s' (retain=%s): %s", topic, retain, exc)
            return

        peer_ip = ""
        if self.embedded_broker is not None:
            peer_ip = self.embedded_broker.get_client_peer_ip(dmd_id) or ""
            if peer_ip:
                decoded.setdefault("_peer_ip", peer_ip)

        if len(parts) >= 5 and leaf == "events":
            decoded.setdefault("domain", parts[3])

        declared_ip = str(decoded.get("client_ip") or decoded.get("udp_ip") or "").strip()
        logger.info(
            "[MQTT IN] <-- topic='%s' (retain=%s, dmd_id='%s', client_ip='%s', peer_ip='%s') | payload=%s",
            topic,
            retain,
            dmd_id,
            declared_ip or "auto",
            peer_ip or "unknown",
            _summarize_payload_for_log(decoded),
        )

        if leaf == "hello":
            if self._on_client_hello is not None:
                await self._on_client_hello(dmd_id, decoded)
            elif self._on_client_config is not None:
                await self._on_client_config(dmd_id, {**decoded, "_is_hello": True})
        elif leaf == "config" and self._on_client_config is not None:
            await self._on_client_config(dmd_id, decoded)
        elif leaf == "events" and self._on_client_event is not None:
            await self._on_client_event(dmd_id, decoded)
        elif leaf == "status" and self._on_client_event is not None:
            if not decoded.get("online", True):
                await self._on_client_event(dmd_id, {"action": "client_offline"})
            else:
                await self._on_client_event(dmd_id, {**decoded, "action": "client_online"})


showcase_mqtt_gateway = ShowcaseMqttGateway()
