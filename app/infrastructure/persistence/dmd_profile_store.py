"""
Persistent DMD Profile & Server-Managed Playlist Store (`./data/dmd_profiles.json`).

Stores per-DMD hardware geometry, LAN UDP target (`udp_ip:udp_port`), operating mode
(`config_source`: `"client"` vs `"server"`, `supports_local_card`: `True` vs `False`),
content broadcast language (`lang`: `fr`, `en`, `es`, `de`, `it`, `pt`, `ja`), and
multi-domain weighted playlists with atomic `.tmp` + `os.replace` writes.
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
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.persistence.dmd_profiles")

SUPPORTED_CONTENT_LANGS = ("fr", "en", "es", "de", "it", "pt", "ja")
VALID_DOMAINS = ("games", "movies", "series", "anime")
VALID_CARD_LAYOUTS = (
    "classic_split",
    "poster_right",
    "badge_header",
    "hero_banner",
    "minimal_arcade",
)
VALID_CARD_THEMES = (
    "tinted",
    "pure_black",
    "neon_border",
    "poster_wash",
    "amber_dmd",
)
VALID_CARD_FONTS = ("arcade_pixel", "clean_sans")


def normalize_card_style(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize Mode 2B / Studio Info Card style settings with safe pixel-perfect defaults."""
    src = raw if isinstance(raw, dict) else {}
    layout = str(src.get("card_layout") or "classic_split").strip().lower()
    if layout not in VALID_CARD_LAYOUTS:
        layout = "classic_split"
    theme = str(src.get("card_theme") or "tinted").strip().lower()
    if theme not in VALID_CARD_THEMES:
        theme = "tinted"
    font = str(src.get("card_font") or "arcade_pixel").strip().lower()
    if font not in VALID_CARD_FONTS:
        font = "arcade_pixel"
    accent = str(src.get("card_accent_color") or "auto").strip() or "auto"
    hold_raw = src.get("card_hold_s")
    hold_s = max(0.2, min(5.0, float(hold_raw if hold_raw is not None else 0.9)))
    return {
        "include_card_intro": bool(src.get("include_card_intro", True)),
        "card_layout": layout,
        "card_theme": theme,
        "card_font": font,
        "card_accent_color": accent,
        "include_poster": bool(src.get("include_poster", True)),
        "show_badge": bool(src.get("show_badge", True)),
        "show_year": bool(src.get("show_year", True)),
        "show_rating": bool(src.get("show_rating", True)),
        "include_synopsis": bool(src.get("include_synopsis", True)),
        "text_scroll_speed": str(src.get("text_scroll_speed") or "normal").strip().lower() or "normal",
        "card_hold_s": hold_s,
    }


def default_playlist_slots(lang: str = "fr") -> List[Dict[str, Any]]:
    """Build a sensible default 4-domain playlist for newly discovered passive DMDs."""
    eff_lang = lang if lang in SUPPORTED_CONTENT_LANGS else "fr"
    return [
        {
            "enabled": True,
            "domain": "games",
            "count": 2,
            "lang": eff_lang,
            "systems": ["arcade", "mame", "neogeo", "cps2", "snes", "megadrive"],
            "genres": [],
            "decades": [],
            "min_rating": 6.0,
            "video_type": "any",
            "include_poster": True,
            "include_synopsis": True,
            "text_scroll_speed": "normal",
            "jacket_panorama": True,
            "marquee_panorama": False,
            "snapshot_panorama_count": 0,
            "include_video": True,
            "video_max_duration_s": 8.0,
            "video_fps": 15,
            "video_mode": str(settings.default_video_mode or "auto_action"),
            "color_boost": str(settings.default_color_boost or "auto"),
        },
        {
            "enabled": True,
            "domain": "movies",
            "count": 1,
            "lang": eff_lang,
            "systems": [],
            "genres": [],
            "decades": [],
            "min_rating": 6.5,
            "video_type": "trailer",
            "include_poster": True,
            "include_synopsis": True,
            "text_scroll_speed": "normal",
            "jacket_panorama": True,
            "marquee_panorama": False,
            "snapshot_panorama_count": 0,
            "include_video": True,
            "video_max_duration_s": 8.0,
            "video_fps": 15,
            "video_mode": str(settings.default_video_mode or "auto_action"),
            "color_boost": str(settings.default_color_boost or "auto"),
        },
        {
            "enabled": False,
            "domain": "series",
            "count": 1,
            "lang": eff_lang,
            "systems": [],
            "genres": [],
            "decades": [],
            "min_rating": 6.5,
            "video_type": "any",
            "include_poster": True,
            "include_synopsis": True,
            "text_scroll_speed": "normal",
            "jacket_panorama": True,
            "marquee_panorama": False,
            "snapshot_panorama_count": 0,
            "include_video": True,
            "video_max_duration_s": 8.0,
            "video_fps": 15,
            "video_mode": str(settings.default_video_mode or "auto_action"),
            "color_boost": str(settings.default_color_boost or "auto"),
        },
        {
            "enabled": False,
            "domain": "anime",
            "count": 1,
            "lang": eff_lang,
            "systems": [],
            "genres": [],
            "decades": [],
            "min_rating": 6.5,
            "video_type": "opening",
            "include_poster": True,
            "include_synopsis": True,
            "text_scroll_speed": "normal",
            "jacket_panorama": True,
            "marquee_panorama": False,
            "snapshot_panorama_count": 0,
            "include_video": True,
            "video_max_duration_s": 8.0,
            "video_fps": 15,
            "video_mode": str(settings.default_video_mode or "auto_action"),
            "color_boost": str(settings.default_color_boost or "auto"),
        },
    ]


def _normalize_str_list(val: Any) -> List[str]:
    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip() and s.strip().lower() != "all"]
    if isinstance(val, list):
        return [str(s).strip() for s in val if str(s).strip() and str(s).strip().lower() != "all"]
    return []


def normalize_playlist_slot(raw: Dict[str, Any], fallback_lang: str = "fr") -> Dict[str, Any]:
    dom = str(raw.get("domain") or "games").strip().lower()
    if dom not in VALID_DOMAINS:
        dom = "games"
    slot_lang_raw = raw.get("lang")
    slot_lang = str(slot_lang_raw).strip().lower() if slot_lang_raw else ""
    if slot_lang and slot_lang not in SUPPORTED_CONTENT_LANGS:
        slot_lang = fallback_lang
    out: Dict[str, Any] = {
        "enabled": bool(raw.get("enabled", True)),
        "domain": dom,
        "count": max(1, min(10, int(raw.get("count") or 1))),
        "lang": slot_lang or None,
        "systems": _normalize_str_list(raw.get("systems")),
        "genres": _normalize_str_list(raw.get("genres")),
        "decades": _normalize_str_list(raw.get("decades")),
        "min_rating": max(0.0, min(9.9, float(raw.get("min_rating") if raw.get("min_rating") is not None else 6.0))),
        "video_type": str(raw.get("video_type") or "any").strip().lower() or "any",
        "include_poster": bool(raw.get("include_poster", True)),
        "include_synopsis": bool(raw.get("include_synopsis", True)),
        "text_scroll_speed": str(raw.get("text_scroll_speed") or "normal").strip().lower() or "normal",
        "jacket_panorama": bool(raw.get("jacket_panorama", True)),
        "marquee_panorama": bool(raw.get("marquee_panorama", False)),
        "snapshot_panorama_count": max(0, min(5, int(raw.get("snapshot_panorama_count") or 0))),
        "include_video": bool(raw.get("include_video", True)),
        "video_max_duration_s": max(1.5, min(120.0, float(raw.get("video_max_duration_s") or 8.0))),
        "video_fps": max(6, min(60, int(raw.get("video_fps") or 15))),
        "video_mode": str(raw.get("video_mode") or settings.default_video_mode or "auto_action").strip().lower(),
        "color_boost": (
            raw.get("color_boost")
            if isinstance(raw.get("color_boost"), bool)
            else str(raw.get("color_boost") or settings.default_color_boost or "auto").strip().lower()
        ),
    }
    if raw.get("include_card_intro") is not None:
        out["include_card_intro"] = bool(raw["include_card_intro"])
    if raw.get("card_layout") in VALID_CARD_LAYOUTS:
        out["card_layout"] = str(raw["card_layout"])
    if raw.get("card_theme") in VALID_CARD_THEMES:
        out["card_theme"] = str(raw["card_theme"])
    if raw.get("card_font") in VALID_CARD_FONTS:
        out["card_font"] = str(raw["card_font"])
    if raw.get("card_accent_color"):
        out["card_accent_color"] = str(raw["card_accent_color"]).strip()
    for bkey in ("show_badge", "show_year", "show_rating"):
        if raw.get(bkey) is not None:
            out[bkey] = bool(raw[bkey])
    if raw.get("card_hold_s") is not None:
        out["card_hold_s"] = max(0.2, min(5.0, float(raw["card_hold_s"])))
    if raw.get("panorama_duration_s") is not None:
        out["panorama_duration_s"] = max(1.5, min(15.0, float(raw["panorama_duration_s"])))
    return out


def normalize_dmd_profile(dmd_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    clean_id = str(dmd_id or raw.get("dmd_id") or "").strip()
    if not clean_id:
        raise ValueError("dmd_id cannot be empty")
    lang = str(raw.get("lang") or settings.default_lang or "fr").strip().lower()
    if lang not in SUPPORTED_CONTENT_LANGS:
        lang = "fr"
    cfg_src = str(raw.get("config_source") or "server").strip().lower()
    if cfg_src not in ("client", "server"):
        cfg_src = "server"
    raw_playlist = raw.get("playlist")
    if isinstance(raw_playlist, list) and len(raw_playlist) > 0:
        playlist = [normalize_playlist_slot(s, fallback_lang=lang) for s in raw_playlist if isinstance(s, dict)]
    else:
        playlist = default_playlist_slots(lang=lang)
    if not playlist:
        playlist = default_playlist_slots(lang=lang)

    return {
        "dmd_id": clean_id,
        "display_name": str(raw.get("display_name") or clean_id).strip(),
        "config_source": cfg_src,
        "supports_local_card": bool(raw.get("supports_local_card", True)),
        "always_stream_udp": bool(raw.get("always_stream_udp", False)),
        "width": max(32, min(512, int(raw.get("width") or 128))),
        "height": max(16, min(256, int(raw.get("height") or 32))),
        "udp_ip": str(raw.get("udp_ip") or raw.get("client_ip") or "").strip(),
        "udp_port": max(1, min(65535, int(raw.get("udp_port") or 4210))),
        "lang": lang,
        "paused": bool(raw.get("paused", False)),
        "inter_item_gap_s": max(0.0, min(10.0, float(raw.get("inter_item_gap_s") if raw.get("inter_item_gap_s") is not None else 1.0))),
        "card_style": normalize_card_style(raw.get("card_style")),
        "playlist": playlist,
        "updated_at": float(raw.get("updated_at") or time.time()),
    }


class DmdProfileStore:
    """
    Thread-safe JSON store for `./data/dmd_profiles.json`.
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
                raw_map = raw_json.get("profiles") if isinstance(raw_json, dict) and "profiles" in raw_json else raw_json
                if isinstance(raw_map, dict):
                    for d_id, p_dict in raw_map.items():
                        if isinstance(p_dict, dict):
                            try:
                                norm = normalize_dmd_profile(str(d_id), p_dict)
                                loaded[norm["dmd_id"]] = norm
                            except Exception as item_exc:
                                logger.warning("[PROFILES] Skipping invalid profile '%s': %s", d_id, item_exc)
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

    def ensure_from_hello(self, dmd_id: str, payload: Dict[str, Any], detected_ip: str = "") -> Dict[str, Any]:
        """
        Create or refresh hardware coordinates (`width`, `height`, `udp_ip`, `udp_port`, `supports_local_card`)
        when a passive DMD announces itself via MQTT `hello` or HTTP `POST /api/v1/clients/hello`,
        WITHOUT overwriting user-curated `playlist`, `lang`, or `paused` settings if the profile already exists!
        """
        with self._lock:
            self._ensure_loaded()
            clean_id = str(dmd_id or payload.get("dmd_id") or "").strip()
            existing = self._profiles.get(clean_id)
            best_ip = (
                str(payload.get("udp_ip") or payload.get("client_ip") or detected_ip or "").strip()
            )
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
            logger.info("[PROFILES] Auto-created persisted DMD profile '%s' from hello announcement", clean_id)
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
