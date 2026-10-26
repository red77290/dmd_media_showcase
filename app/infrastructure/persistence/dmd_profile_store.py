"""
Persistent DMD Profile & Server-Managed Playlist Store (`./data/dmd_profiles.json`).

Implements `IDmdProfileRepository` with atomic `.tmp` + `os.replace` writes while delegating
all pure profile, style, and playlist normalization rules to `app.domain.style_specs`.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.domain.ports.profile_repository import IDmdProfileRepository
from app.domain.style_specs import (
    DOMAIN_STUDIO_DEFAULTS,
    RENDER_STYLE_KEYS as _RENDER_STYLE_KEYS,
    SUPPORTED_CONTENT_LANGS,
    VALID_CARD_FONTS,
    VALID_CARD_LAYOUTS,
    VALID_CARD_THEMES,
    VALID_DOMAINS,
    VALID_VIDEO_MODES,
    VALID_VIDEO_TYPES,
    default_domain_studio_style as _pure_default_domain_studio_style,
    default_playlist_slots as _pure_default_playlist_slots,
    normalize_card_style,
    normalize_dmd_profile_spec,
    normalize_domain_studio_style as _pure_normalize_domain_studio_style,
    normalize_domain_styles_map as _pure_normalize_domain_styles_map,
    normalize_playlist_slot as _pure_normalize_playlist_slot,
)
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.persistence.dmd_profiles")


def default_domain_studio_style(domain: str) -> Dict[str, Any]:
    """Return canonical Studio Card + Sequence defaults using active server defaults."""
    return _pure_default_domain_studio_style(
        domain,
        default_panorama_duration_s=float(settings.panorama_duration_s or 4.0),
        default_video_mode=str(settings.default_video_mode or "auto_action"),
    )


def normalize_domain_studio_style(domain: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize a per-domain Studio rendering profile using active server defaults."""
    return _pure_normalize_domain_studio_style(
        domain,
        raw,
        default_panorama_duration_s=float(settings.panorama_duration_s or 4.0),
        default_video_mode=str(settings.default_video_mode or "auto_action"),
    )


def normalize_domain_styles_map(
    raw_domain_styles: Optional[Dict[str, Any]],
    legacy_card_style: Optional[Dict[str, Any]] = None,
    raw_playlist: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build normalized `domain_styles` for all 4 domains."""
    return _pure_normalize_domain_styles_map(
        raw_domain_styles,
        legacy_card_style=legacy_card_style,
        raw_playlist=raw_playlist,
        default_panorama_duration_s=float(settings.panorama_duration_s or 4.0),
        default_video_mode=str(settings.default_video_mode or "auto_action"),
    )


def default_playlist_slots(lang: str = "fr") -> List[Dict[str, Any]]:
    """Build default 4-domain playlist slots for newly discovered passive DMDs."""
    return _pure_default_playlist_slots(
        lang=lang,
        default_video_mode=str(settings.default_video_mode or "auto_action"),
        default_color_boost=str(settings.default_color_boost or "auto"),
    )


def normalize_playlist_slot(raw: Dict[str, Any], fallback_lang: str = "fr") -> Dict[str, Any]:
    """Normalize a single playlist slot using active server defaults."""
    return _pure_normalize_playlist_slot(
        raw,
        fallback_lang=fallback_lang,
        default_video_mode=str(settings.default_video_mode or "auto_action"),
        default_color_boost=str(settings.default_color_boost or "auto"),
    )


def normalize_dmd_profile(dmd_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a full per-DMD profile dictionary using active server defaults."""
    return normalize_dmd_profile_spec(
        dmd_id,
        raw,
        default_lang=str(settings.default_lang or "fr"),
        default_panorama_duration_s=float(settings.panorama_duration_s or 4.0),
        default_video_mode=str(settings.default_video_mode or "auto_action"),
        default_color_boost=str(settings.default_color_boost or "auto"),
    )


class DmdProfileStore(IDmdProfileRepository):
    """
    Thread-safe JSON repository for `./data/dmd_profiles.json` implementing `IDmdProfileRepository`.
    Automatically follows `get_data_dir()` so isolated pytest fixtures and Docker volumes work seamlessly.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._custom_path = file_path
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._loaded_from: Optional[Path] = None

    @property
    def file_path(self) -> Path:
        if self._custom_path is not None:
            return self._custom_path
        return get_data_dir() / "dmd_profiles.json"

    def _ensure_loaded(self) -> None:
        path = self.file_path
        if self._loaded_from == path:
            return
        loaded: Dict[str, Dict[str, Any]] = {}
        if path.exists():
            try:
                raw_json = json.loads(path.read_text(encoding="utf-8"))
                raw_map = (
                    raw_json.get("profiles")
                    if isinstance(raw_json, dict) and "profiles" in raw_json
                    else raw_json
                )
                if isinstance(raw_map, dict):
                    for d_id, p_dict in raw_map.items():
                        if isinstance(p_dict, dict):
                            try:
                                norm = normalize_dmd_profile(str(d_id), p_dict)
                                loaded[norm["dmd_id"]] = norm
                            except Exception as item_exc:
                                logger.warning(
                                    "[PROFILES] Skipping invalid profile '%s': %s", d_id, item_exc
                                )
            except Exception as exc:
                logger.warning("[PROFILES] Could not read %s: %s", path, exc)
        self._profiles = loaded
        self._loaded_from = path

    def _save_locked(self) -> None:
        path = self.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        payload = {"version": 1, "profiles": self._profiles}
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, path)
        self._loaded_from = path

    def list_profiles(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [copy.deepcopy(p) for _, p in sorted(self._profiles.items())]

    def get_profile(self, dmd_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            prof = self._profiles.get(str(dmd_id or "").strip())
            return copy.deepcopy(prof) if prof is not None else None

    def upsert_profile(self, dmd_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            clean_id = str(dmd_id or data.get("dmd_id") or "").strip()
            existing = self._profiles.get(clean_id, {})
            merged = {**existing, **data, "dmd_id": clean_id, "updated_at": time.time()}
            incoming_ds = data.get("domain_styles")
            existing_ds = existing.get("domain_styles")
            if isinstance(incoming_ds, dict) and len(incoming_ds) > 0:
                combined_ds: Dict[str, Any] = (
                    dict(existing_ds) if isinstance(existing_ds, dict) else {}
                )
                for dom_k, dom_v in incoming_ds.items():
                    if dom_k in VALID_DOMAINS and isinstance(dom_v, dict):
                        prev_dom = (
                            combined_ds.get(dom_k)
                            if isinstance(combined_ds.get(dom_k), dict)
                            else {}
                        )
                        combined_ds[dom_k] = {**prev_dom, **dom_v}
                merged["domain_styles"] = combined_ds
                if "card_style" not in data and "games" in combined_ds:
                    merged["card_style"] = normalize_card_style(combined_ds["games"])
            elif isinstance(data.get("card_style"), dict) and len(data["card_style"]) > 0:
                if isinstance(existing_ds, dict) and len(existing_ds) > 0:
                    merged["domain_styles"] = {
                        dom_k: {**(existing_ds.get(dom_k) or {}), **data["card_style"]}
                        for dom_k in VALID_DOMAINS
                    }
                else:
                    merged.pop("domain_styles", None)
            elif "domain_styles" in data and data["domain_styles"] is None:
                if isinstance(existing_ds, dict) and len(existing_ds) > 0:
                    merged["domain_styles"] = copy.deepcopy(existing_ds)
                else:
                    merged.pop("domain_styles", None)
            norm = normalize_dmd_profile(clean_id, merged)
            self._profiles[clean_id] = norm
            self._save_locked()
            logger.info(
                "[PROFILES] Saved DMD profile '%s' (mode=%s, local_card=%s, lang=%s, %dx%d)",
                clean_id,
                norm["config_source"],
                norm["supports_local_card"],
                norm["lang"],
                norm["width"],
                norm["height"],
            )
            return copy.deepcopy(norm)

    def ensure_from_hello(
        self, dmd_id: str, payload: Dict[str, Any], detected_ip: str = ""
    ) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            clean_id = str(dmd_id or payload.get("dmd_id") or "").strip()
            existing = self._profiles.get(clean_id)
            best_ip = str(
                payload.get("udp_ip") or payload.get("client_ip") or detected_ip or ""
            ).strip()
            if best_ip in ("0.0.0.0", "auto", "unknown", "testclient"):
                best_ip = ""

            if existing is not None:
                updated = copy.deepcopy(existing)
                if payload.get("width"):
                    updated["width"] = max(32, min(512, int(payload["width"])))
                if payload.get("height"):
                    updated["height"] = max(16, min(256, int(payload["height"])))
                if best_ip:
                    updated["udp_ip"] = best_ip
                if payload.get("udp_port"):
                    updated["udp_port"] = max(1, min(65535, int(payload["udp_port"])))
                if "supports_local_card" in payload and payload["supports_local_card"] is not None:
                    updated["supports_local_card"] = bool(payload["supports_local_card"])
                if payload.get("display_name") and updated.get("display_name") == clean_id:
                    updated["display_name"] = str(payload["display_name"]).strip()
                updated["updated_at"] = time.time()
                norm = normalize_dmd_profile(clean_id, updated)
                if norm != existing:
                    self._profiles[clean_id] = norm
                    self._save_locked()
                return copy.deepcopy(norm)

            init_lang = str(payload.get("lang") or settings.default_lang or "fr").strip().lower()
            if init_lang not in SUPPORTED_CONTENT_LANGS:
                init_lang = "fr"
            created = normalize_dmd_profile(
                clean_id,
                {
                    "dmd_id": clean_id,
                    "display_name": str(payload.get("display_name") or clean_id).strip(),
                    "config_source": str(payload.get("config_source") or "server"),
                    "supports_local_card": bool(payload.get("supports_local_card", True)),
                    "always_stream_udp": bool(payload.get("always_stream_udp", False)),
                    "width": int(payload.get("width") or 128),
                    "height": int(payload.get("height") or 32),
                    "udp_ip": best_ip,
                    "udp_port": int(payload.get("udp_port") or 4210),
                    "lang": init_lang,
                    "paused": False,
                    "inter_item_gap_s": 1.0,
                    "playlist": default_playlist_slots(lang=init_lang),
                    "updated_at": time.time(),
                },
            )
            self._profiles[clean_id] = created
            self._save_locked()
            logger.info(
                "[PROFILES] Auto-created persisted DMD profile '%s' from hello announcement",
                clean_id,
            )
            return copy.deepcopy(created)

    def delete_profile(self, dmd_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            removed = self._profiles.pop(str(dmd_id or "").strip(), None)
            if removed is not None:
                self._save_locked()
                return True
            return False


dmd_profile_store = DmdProfileStore()

__all__ = [
    "DOMAIN_STUDIO_DEFAULTS",
    "DmdProfileStore",
    "SUPPORTED_CONTENT_LANGS",
    "VALID_CARD_FONTS",
    "VALID_CARD_LAYOUTS",
    "VALID_CARD_THEMES",
    "VALID_DOMAINS",
    "VALID_VIDEO_MODES",
    "VALID_VIDEO_TYPES",
    "_RENDER_STYLE_KEYS",
    "default_domain_studio_style",
    "default_playlist_slots",
    "dmd_profile_store",
    "normalize_card_style",
    "normalize_dmd_profile",
    "normalize_domain_studio_style",
    "normalize_domain_styles_map",
    "normalize_playlist_slot",
]
