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
VALID_VIDEO_MODES = ("auto_action", "panorama", "center_crop", "pixel_art", "anime", "cinema")
VALID_VIDEO_TYPES = ("any", "opening", "ending", "pv", "trailer")

DOMAIN_STUDIO_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "games": {
        "card_layout": "classic_split",
        "card_theme": "tinted",
        "card_font": "arcade_pixel",
        "marquee_panorama": True,
        "video_type": "any",
        "video_max_duration_s": 10.0,
        "color_boost": "pixel_art",
    },
    "movies": {
        "card_layout": "poster_right",
        "card_theme": "poster_wash",
        "card_font": "clean_sans",
        "marquee_panorama": False,
        "video_type": "trailer",
        "video_max_duration_s": 12.0,
        "color_boost": "cinema",
    },
    "series": {
        "card_layout": "classic_split",
        "card_theme": "tinted",
        "card_font": "clean_sans",
        "marquee_panorama": False,
        "video_type": "any",
        "video_max_duration_s": 10.0,
        "color_boost": "cinema",
    },
    "anime": {
        "card_layout": "badge_header",
        "card_theme": "neon_border",
        "card_font": "arcade_pixel",
        "marquee_panorama": False,
        "video_type": "opening",
        "video_max_duration_s": 12.0,
        "color_boost": "anime",
    },
}

_RENDER_STYLE_KEYS = (
    "include_card_intro",
    "card_layout",
    "card_theme",
    "card_font",
    "card_accent_color",
    "include_poster",
    "show_badge",
    "show_year",
    "show_rating",
    "include_synopsis",
    "text_scroll_speed",
    "card_hold_s",
    "jacket_panorama",
    "marquee_panorama",
    "snapshot_panorama_count",
    "panorama_duration_s",
    "include_video",
    "video_type",
    "video_max_duration_s",
    "video_fps",
    "video_mode",
    "color_boost",
)


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


def default_domain_studio_style(domain: str) -> Dict[str, Any]:
    """Return canonical Studio Card + Sequence defaults tailored for `domain`."""
    dom = str(domain or "games").strip().lower()
    d_def = DOMAIN_STUDIO_DEFAULTS.get(dom, DOMAIN_STUDIO_DEFAULTS["games"])
    return {
        "include_card_intro": True,
        "card_layout": d_def["card_layout"],
        "card_theme": d_def["card_theme"],
        "card_font": d_def["card_font"],
        "card_accent_color": "auto",
        "include_poster": True,
        "show_badge": True,
        "show_year": True,
        "show_rating": True,
        "include_synopsis": True,
        "text_scroll_speed": "5",
        "card_hold_s": 0.9,
        "jacket_panorama": True,
        "marquee_panorama": bool(d_def["marquee_panorama"]),
        "snapshot_panorama_count": 0,
        "panorama_duration_s": float(settings.panorama_duration_s or 4.0),
        "include_video": True,
        "video_type": str(d_def["video_type"]),
        "video_max_duration_s": float(d_def["video_max_duration_s"]),
        "video_fps": 15,
        "video_mode": str(settings.default_video_mode or "auto_action"),
        "color_boost": str(d_def["color_boost"]),
    }


def normalize_domain_studio_style(domain: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normalize a per-domain Studio rendering profile (`games`, `movies`, `series`, `anime`)
    owning both Phase 1 Info Card appearance and Phases 2 & 3 Panorama / Video parameters.
    """
    base = default_domain_studio_style(domain)
    src = {**base, **(raw if isinstance(raw, dict) else {})}

    layout = str(src.get("card_layout") or base["card_layout"]).strip().lower()
    if layout not in VALID_CARD_LAYOUTS:
        layout = base["card_layout"]
    theme = str(src.get("card_theme") or base["card_theme"]).strip().lower()
    if theme not in VALID_CARD_THEMES:
        theme = base["card_theme"]
    font = str(src.get("card_font") or base["card_font"]).strip().lower()
    if font not in VALID_CARD_FONTS:
        font = base["card_font"]
    accent = str(src.get("card_accent_color") or "auto").strip() or "auto"
    hold_raw = src.get("card_hold_s")
    hold_s = max(0.2, min(5.0, float(hold_raw if hold_raw is not None else 0.9)))
    pano_dur_raw = src.get("panorama_duration_s")
    pano_dur_s = max(
        1.5,
        min(15.0, float(pano_dur_raw if pano_dur_raw is not None else base["panorama_duration_s"])),
    )
    vdur_raw = src.get("video_max_duration_s")
    vdur_s = max(
        1.5,
        min(120.0, float(vdur_raw if vdur_raw is not None else base["video_max_duration_s"])),
    )
    vfps_raw = src.get("video_fps")
    vfps = max(6, min(60, int(vfps_raw if vfps_raw is not None else 15)))
    vmode = str(src.get("video_mode") or base["video_mode"]).strip().lower()
    if vmode not in VALID_VIDEO_MODES:
        vmode = "auto_action"
    vtype = str(src.get("video_type") or base["video_type"]).strip().lower()
    if vtype not in VALID_VIDEO_TYPES:
        vtype = base["video_type"]
    raw_cb = src.get("color_boost")
    cboost: Any = (
        raw_cb
        if isinstance(raw_cb, bool)
        else (str(raw_cb or base["color_boost"]).strip().lower() or base["color_boost"])
    )

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
        "text_scroll_speed": str(src.get("text_scroll_speed") or "5").strip().lower() or "5",
        "card_hold_s": hold_s,
        "jacket_panorama": bool(src.get("jacket_panorama", True)),
        "marquee_panorama": bool(src.get("marquee_panorama", base["marquee_panorama"])),
        "snapshot_panorama_count": max(0, min(5, int(src.get("snapshot_panorama_count") or 0))),
        "panorama_duration_s": pano_dur_s,
        "include_video": bool(src.get("include_video", True)),
        "video_type": vtype,
        "video_max_duration_s": vdur_s,
        "video_fps": vfps,
        "video_mode": vmode,
        "color_boost": cboost,
    }


def normalize_domain_styles_map(
    raw_domain_styles: Optional[Dict[str, Any]],
    legacy_card_style: Optional[Dict[str, Any]] = None,
    raw_playlist: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Build normalized `domain_styles` for all 4 domains (`games`, `movies`, `series`, `anime`),
    seamlessly migrating legacy `card_style` or slot-level rendering fields when `domain_styles`
    was not explicitly provided.
    """
    has_explicit_map = isinstance(raw_domain_styles, dict) and len(raw_domain_styles) > 0
    slot_by_dom: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_playlist, list):
        for s in raw_playlist:
            if isinstance(s, dict) and s.get("domain") in VALID_DOMAINS:
                slot_by_dom[str(s["domain"])] = s

    legacy_cs = legacy_card_style if isinstance(legacy_card_style, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for dom in VALID_DOMAINS:
        merged: Dict[str, Any] = {}
        if not has_explicit_map:
            slot_src = slot_by_dom.get(dom) or {}
            for k in _RENDER_STYLE_KEYS:
                if k in slot_src and slot_src[k] is not None:
                    merged[k] = slot_src[k]
            for k, v in legacy_cs.items():
                if v is not None:
                    merged[k] = v
        else:
            for k, v in legacy_cs.items():
                if v is not None:
                    merged[k] = v
            dom_entry = raw_domain_styles.get(dom) if isinstance(raw_domain_styles, dict) else None
            if isinstance(dom_entry, dict):
                for k, v in dom_entry.items():
                    if v is not None:
                        merged[k] = v
        out[dom] = normalize_domain_studio_style(dom, merged)
    return out


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

    domain_styles = normalize_domain_styles_map(
        raw.get("domain_styles") if isinstance(raw.get("domain_styles"), dict) else None,
        legacy_card_style=raw.get("card_style") if isinstance(raw.get("card_style"), dict) else None,
        raw_playlist=raw_playlist if isinstance(raw_playlist, list) else None,
    )
    # Keep slot-level rendering fields aligned with the authoritative per-domain Studio profile
    for slot in playlist:
        dom_key = slot.get("domain")
        if dom_key in domain_styles:
            dstyle = domain_styles[dom_key]
            for rk in _RENDER_STYLE_KEYS:
                slot[rk] = dstyle[rk]

    raw_cs = raw.get("card_style")
    effective_cs = normalize_card_style(
        raw_cs if isinstance(raw_cs, dict) and len(raw_cs) > 0 else domain_styles["games"]
    )

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
        "card_style": effective_cs,
        "domain_styles": domain_styles,
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
            incoming_ds = data.get("domain_styles")
            existing_ds = existing.get("domain_styles")
            if isinstance(incoming_ds, dict) and len(incoming_ds) > 0:
                combined_ds: Dict[str, Any] = dict(existing_ds) if isinstance(existing_ds, dict) else {}
                for dom_k, dom_v in incoming_ds.items():
                    if dom_k in VALID_DOMAINS and isinstance(dom_v, dict):
                        prev_dom = combined_ds.get(dom_k) if isinstance(combined_ds.get(dom_k), dict) else {}
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
                # Caller omitted domain_styles: preserve existing domain_styles if present,
                # unless legacy playlist slot overrides were explicitly supplied on a fresh profile.
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
