"""
Pure Domain Style Specifications, Geometry Rules & Profile Normalizers (`app/domain/style_specs.py`).

Zero infrastructure or framework imports: shared cleanly across Domain, Application, and Persistence.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from app.domain.entities import DomainType

SUPPORTED_CONTENT_LANGS: Tuple[str, ...] = ("fr", "en", "es", "de", "it", "pt", "ja")
VALID_DOMAINS: Tuple[DomainType, ...] = ("games", "movies", "series", "anime")
VALID_CARD_LAYOUTS: Tuple[str, ...] = (
    "classic_split",
    "poster_right",
    "badge_header",
    "hero_banner",
    "minimal_arcade",
)
VALID_CARD_THEMES: Tuple[str, ...] = (
    "tinted",
    "pure_black",
    "neon_border",
    "poster_wash",
    "amber_dmd",
)
VALID_CARD_FONTS: Tuple[str, ...] = ("arcade_pixel", "clean_sans")
VALID_VIDEO_MODES: Tuple[str, ...] = (
    "auto_action",
    "panorama",
    "center_crop",
    "pixel_art",
    "anime",
    "cinema",
)
VALID_VIDEO_TYPES: Tuple[str, ...] = ("any", "opening", "ending", "pv", "trailer")
VALID_SCROLL_SPEEDS = frozenset(
    {"slow", "normal", "fast", "turbo", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}
)

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

RENDER_STYLE_KEYS: Tuple[str, ...] = (
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


def parse_hex_rgb(
    hex_color: str, fallback: Tuple[int, int, int] = (230, 74, 25)
) -> Tuple[int, int, int]:
    """Parse a `#RRGGBB` hex string into an RGB tuple with safe fallback."""
    raw = str(hex_color or "").strip().lstrip("#")
    if len(raw) == 6:
        try:
            return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        except ValueError:
            pass
    return fallback


def compute_card_poster_dimensions(width: int, height: int) -> Tuple[int, int]:
    """Compute optimal mini-jacket `(poster_w, poster_h)` dimensions for any YOKO/TATE DMD resolution."""
    if height > width:
        side = 40 if width >= 64 else 24
        return side, side
    if width == height:
        side = min(32, width // 2)
        return side, side
    if width >= 128 and height >= 64:
        return 48, 64
    side = min(32, height, width // 3)
    return side, side


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


def default_domain_studio_style(
    domain: str,
    *,
    default_panorama_duration_s: float = 4.0,
    default_video_mode: str = "auto_action",
) -> Dict[str, Any]:
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
        "panorama_duration_s": float(default_panorama_duration_s or 4.0),
        "include_video": True,
        "video_type": str(d_def["video_type"]),
        "video_max_duration_s": float(d_def["video_max_duration_s"]),
        "video_fps": 15,
        "video_mode": str(default_video_mode or "auto_action"),
        "color_boost": str(d_def["color_boost"]),
    }


def normalize_domain_studio_style(
    domain: str,
    raw: Optional[Dict[str, Any]],
    *,
    default_panorama_duration_s: float = 4.0,
    default_video_mode: str = "auto_action",
) -> Dict[str, Any]:
    """Normalize a per-domain Studio rendering profile (`games`, `movies`, `series`, `anime`)."""
    base = default_domain_studio_style(
        domain,
        default_panorama_duration_s=default_panorama_duration_s,
        default_video_mode=default_video_mode,
    )
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
    *,
    default_panorama_duration_s: float = 4.0,
    default_video_mode: str = "auto_action",
) -> Dict[str, Dict[str, Any]]:
    """Build normalized `domain_styles` for all 4 domains (`games`, `movies`, `series`, `anime`)."""
    has_explicit_map = isinstance(raw_domain_styles, dict) and len(raw_domain_styles) > 0
    slot_by_dom: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_playlist, list):
        for slot in raw_playlist:
            if isinstance(slot, dict) and slot.get("domain") in VALID_DOMAINS:
                slot_by_dom[str(slot["domain"])] = slot

    legacy_cs = legacy_card_style if isinstance(legacy_card_style, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for dom in VALID_DOMAINS:
        merged: Dict[str, Any] = {}
        if not has_explicit_map:
            slot_src = slot_by_dom.get(dom) or {}
            for key in RENDER_STYLE_KEYS:
                if key in slot_src and slot_src[key] is not None:
                    merged[key] = slot_src[key]
            for key, val in legacy_cs.items():
                if val is not None:
                    merged[key] = val
        else:
            for key, val in legacy_cs.items():
                if val is not None:
                    merged[key] = val
            dom_entry = raw_domain_styles.get(dom) if isinstance(raw_domain_styles, dict) else None
            if isinstance(dom_entry, dict):
                for key, val in dom_entry.items():
                    if val is not None:
                        merged[key] = val
        out[dom] = normalize_domain_studio_style(
            dom,
            merged,
            default_panorama_duration_s=default_panorama_duration_s,
            default_video_mode=default_video_mode,
        )
    return out


def default_playlist_slots(
    lang: str = "fr",
    *,
    default_video_mode: str = "auto_action",
    default_color_boost: str = "auto",
) -> List[Dict[str, Any]]:
    """Build a sensible default 4-domain playlist for newly discovered passive DMDs."""
    eff_lang = lang if lang in SUPPORTED_CONTENT_LANGS else "fr"
    vmode = str(default_video_mode or "auto_action")
    cboost = str(default_color_boost or "auto")
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
            "video_mode": vmode,
            "color_boost": cboost,
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
            "video_mode": vmode,
            "color_boost": cboost,
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
            "video_mode": vmode,
            "color_boost": cboost,
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
            "video_mode": vmode,
            "color_boost": cboost,
        },
    ]


def _normalize_str_list(val: Any) -> List[str]:
    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip() and s.strip().lower() != "all"]
    if isinstance(val, list):
        return [str(s).strip() for s in val if str(s).strip() and str(s).strip().lower() != "all"]
    return []


def normalize_playlist_slot(
    raw: Dict[str, Any],
    fallback_lang: str = "fr",
    *,
    default_video_mode: str = "auto_action",
    default_color_boost: str = "auto",
) -> Dict[str, Any]:
    """Normalize a single playlist rotation slot dictionary."""
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
        "min_rating": max(
            0.0, min(9.9, float(raw.get("min_rating") if raw.get("min_rating") is not None else 6.0))
        ),
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
        "video_mode": str(raw.get("video_mode") or default_video_mode or "auto_action").strip().lower(),
        "color_boost": (
            raw.get("color_boost")
            if isinstance(raw.get("color_boost"), bool)
            else str(raw.get("color_boost") or default_color_boost or "auto").strip().lower()
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


def normalize_dmd_profile_spec(
    dmd_id: str,
    raw: Dict[str, Any],
    *,
    default_lang: str = "fr",
    default_panorama_duration_s: float = 4.0,
    default_video_mode: str = "auto_action",
    default_color_boost: str = "auto",
) -> Dict[str, Any]:
    """Normalize a full per-DMD profile specification dictionary with pure domain defaults."""
    clean_id = str(dmd_id or raw.get("dmd_id") or "").strip()
    if not clean_id:
        raise ValueError("dmd_id cannot be empty")
    lang = str(raw.get("lang") or default_lang or "fr").strip().lower()
    if lang not in SUPPORTED_CONTENT_LANGS:
        lang = "fr"
    cfg_src = str(raw.get("config_source") or "server").strip().lower()
    if cfg_src not in ("client", "server"):
        cfg_src = "server"
    raw_playlist = raw.get("playlist")
    if isinstance(raw_playlist, list) and len(raw_playlist) > 0:
        playlist = [
            normalize_playlist_slot(
                s,
                fallback_lang=lang,
                default_video_mode=default_video_mode,
                default_color_boost=default_color_boost,
            )
            for s in raw_playlist
            if isinstance(s, dict)
        ]
    else:
        playlist = default_playlist_slots(
            lang=lang,
            default_video_mode=default_video_mode,
            default_color_boost=default_color_boost,
        )
    if not playlist:
        playlist = default_playlist_slots(
            lang=lang,
            default_video_mode=default_video_mode,
            default_color_boost=default_color_boost,
        )

    domain_styles = normalize_domain_styles_map(
        raw.get("domain_styles") if isinstance(raw.get("domain_styles"), dict) else None,
        legacy_card_style=raw.get("card_style") if isinstance(raw.get("card_style"), dict) else None,
        raw_playlist=raw_playlist if isinstance(raw_playlist, list) else None,
        default_panorama_duration_s=default_panorama_duration_s,
        default_video_mode=default_video_mode,
    )
    for slot in playlist:
        dom_key = slot.get("domain")
        if dom_key in domain_styles:
            dstyle = domain_styles[dom_key]
            for rk in RENDER_STYLE_KEYS:
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
        "inter_item_gap_s": max(
            0.0,
            min(10.0, float(raw.get("inter_item_gap_s") if raw.get("inter_item_gap_s") is not None else 1.0)),
        ),
        "card_style": effective_cs,
        "domain_styles": domain_styles,
        "playlist": playlist,
        "updated_at": float(raw.get("updated_at") or time.time()),
    }
