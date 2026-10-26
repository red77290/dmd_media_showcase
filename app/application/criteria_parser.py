"""
Application Criteria Normalization & Package Assembly (`app/application/criteria_parser.py`).

Pure Domain/Application translation helpers converting raw MQTT/HTTP/Profile dictionaries
into canonical `FilterCriteria` Value Objects and assembling `PreparedShowcasePackage` objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from app.config import settings
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)
from app.domain.style_specs import (
    VALID_CARD_FONTS,
    VALID_CARD_LAYOUTS,
    VALID_CARD_THEMES,
    VALID_SCROLL_SPEEDS,
)
from app.domain.taxonomy import resolve_canonical_genre, resolve_canonical_system


def parse_domain_criteria(
    domain: DomainType,
    raw_cfg: Dict[str, Any],
    top_lang: str = "fr",
) -> FilterCriteria:
    """Normalize a raw domain configuration dictionary into an immutable `FilterCriteria`."""
    lang = str(raw_cfg.get("lang") or top_lang or "fr").strip().lower()
    if lang in ("", "system", "auto"):
        lang = (top_lang if top_lang not in ("", "system", "auto") else "fr").lower()

    raw_systems = raw_cfg.get("systems")
    if isinstance(raw_systems, str):
        sys_tokens = [s.strip() for s in raw_systems.split(",") if s.strip()]
    elif isinstance(raw_systems, list):
        sys_tokens = [str(s).strip() for s in raw_systems if str(s).strip()]
    else:
        sys_tokens = ["arcade", "mame", "neogeo", "cps1", "cps2", "snes", "megadrive"]

    systems: List[str] = []
    for s in sys_tokens:
        if s.lower() == "all":
            continue
        canon_s = resolve_canonical_system(s)
        if canon_s and canon_s not in systems:
            systems.append(canon_s)

    raw_genres = raw_cfg.get("genres")
    if isinstance(raw_genres, str):
        genre_tokens = [g.strip() for g in raw_genres.split(",") if g.strip()]
    elif isinstance(raw_genres, list):
        genre_tokens = [str(g).strip() for g in raw_genres if str(g).strip()]
    else:
        genre_tokens = []

    genres: List[str] = []
    for g in genre_tokens:
        if g.lower() == "all":
            continue
        canon_g = resolve_canonical_genre(domain, g)
        if canon_g and canon_g not in genres:
            genres.append(canon_g)

    raw_decades = raw_cfg.get("decades")
    if isinstance(raw_decades, str):
        decades = [
            d.strip()
            for d in raw_decades.split(",")
            if d.strip() and d.strip().lower() != "all"
        ]
    elif isinstance(raw_decades, list):
        decades = [
            str(d).strip()
            for d in raw_decades
            if str(d).strip() and str(d).strip().lower() != "all"
        ]
    else:
        decades = []

    speed = str(raw_cfg.get("text_scroll_speed") or "normal").strip().lower()
    if speed not in VALID_SCROLL_SPEEDS:
        speed = "normal"

    default_cb = str(settings.default_color_boost or "auto").strip().lower()
    if default_cb not in ("auto", "pixel_art", "anime", "cinema"):
        default_cb = "auto"
    raw_cb = raw_cfg.get("color_boost")
    if raw_cb is None:
        color_boost_val = default_cb
    elif isinstance(raw_cb, bool):
        color_boost_val = "auto" if raw_cb else "cinema"
    else:
        cb_str = str(raw_cb).strip().lower()
        if cb_str == "":
            color_boost_val = default_cb
        elif cb_str in ("true", "1", "yes", "on", "auto"):
            color_boost_val = "auto"
        elif cb_str in ("false", "0", "no", "off", "none"):
            color_boost_val = "cinema"
        elif cb_str in ("pixel_art", "anime", "cinema"):
            color_boost_val = cb_str
        else:
            color_boost_val = default_cb

    default_vm = str(settings.default_video_mode or "auto_action").strip().lower()
    if default_vm not in ("auto_action", "panorama", "center_crop"):
        default_vm = "auto_action"
    vm_raw = str(raw_cfg.get("video_mode") or "").strip().lower()
    video_mode_val = (
        vm_raw if vm_raw in ("auto_action", "panorama", "center_crop") else default_vm
    )

    max_dur_ceiling = max(1.5, float(settings.video_max_duration_limit_s))
    req_dur = float(raw_cfg.get("video_max_duration_s") or 8.0)
    eff_video_dur = max(1.5, min(max_dur_ceiling, req_dur))

    max_fps_ceiling = max(6, int(settings.video_max_fps_limit))
    req_fps = int(raw_cfg.get("video_fps") or 15)
    eff_video_fps = max(6, min(max_fps_ceiling, req_fps))

    card_layout_raw = str(raw_cfg.get("card_layout") or "classic_split").strip().lower()
    card_layout_val = card_layout_raw if card_layout_raw in VALID_CARD_LAYOUTS else "classic_split"
    card_theme_raw = str(raw_cfg.get("card_theme") or "tinted").strip().lower()
    card_theme_val = card_theme_raw if card_theme_raw in VALID_CARD_THEMES else "tinted"
    card_font_raw = str(raw_cfg.get("card_font") or "arcade_pixel").strip().lower()
    card_font_val = card_font_raw if card_font_raw in VALID_CARD_FONTS else "arcade_pixel"
    card_accent_val = str(raw_cfg.get("card_accent_color") or "auto").strip() or "auto"
    hold_raw = raw_cfg.get("card_hold_s")
    card_hold_val = max(0.2, min(5.0, float(hold_raw if hold_raw is not None else 0.9)))
    pan_dur_raw = raw_cfg.get("panorama_duration_s")
    pan_dur_val = max(
        1.5,
        min(
            15.0,
            float(pan_dur_raw if pan_dur_raw is not None else settings.panorama_duration_s),
        ),
    )

    return FilterCriteria(
        domain=domain,
        lang=lang,
        systems=systems,
        genres=genres,
        decades=decades,
        min_rating=float(raw_cfg.get("min_rating") or 6.0),
        video_type=str(raw_cfg.get("video_type") or "any"),
        include_poster=bool(raw_cfg.get("show_poster", raw_cfg.get("include_poster", True))),
        include_synopsis=bool(raw_cfg.get("show_synopsis", raw_cfg.get("include_synopsis", True))),
        text_scroll_speed=speed,  # type: ignore[arg-type]
        include_card_intro=bool(raw_cfg.get("include_card_intro", True)),
        card_layout=card_layout_val,
        card_theme=card_theme_val,
        card_font=card_font_val,
        card_accent_color=card_accent_val,
        show_badge=bool(raw_cfg.get("show_badge", True)),
        show_year=bool(raw_cfg.get("show_year", True)),
        show_rating=bool(raw_cfg.get("show_rating", True)),
        card_hold_s=card_hold_val,
        jacket_panorama=bool(raw_cfg.get("jacket_panorama", True)),
        marquee_panorama=bool(raw_cfg.get("marquee_panorama", False)),
        snapshot_panorama_count=int(raw_cfg.get("snapshot_panorama_count", 0)),
        panorama_duration_s=pan_dur_val,
        include_video=bool(raw_cfg.get("show_video", raw_cfg.get("include_video", True))),
        video_max_duration_s=eff_video_dur,
        video_fps=eff_video_fps,
        video_mode=video_mode_val,
        color_boost=color_boost_val,
        render_card_in_stream=bool(raw_cfg.get("render_card_in_stream", False)),
    )


def assemble_prepared_package(
    *,
    item_id: str,
    domain: DomainType,
    dmd_id: str,
    cand: MediaCandidate,
    geometry: PanelGeometry,
    criteria: FilterCriteria,
    item_dir: Path,
    files_map: Dict[str, Any],
) -> PreparedShowcasePackage:
    """Build a `ShowcaseCard` + `PreparedShowcasePackage` from transcoded `files_map` artifacts."""
    poster_w, poster_h = geometry.resolved_poster_dims()
    card = ShowcaseCard(
        item_id=item_id,
        domain=domain,
        title=cand.title,
        year=cand.year,
        badge=cand.badge,
        badge_color=cand.badge_color,
        subtitle=cand.subtitle,
        rating=cand.rating,
        synopsis=cand.synopsis if criteria.include_synopsis else "",
        lang=criteria.lang,
        source_used=cand.source_id,
        poster_width=poster_w,
        poster_height=poster_h,
        poster_rgb565_b64=str(files_map.get("poster_rgb565_b64") or ""),
        text_scroll_speed=criteria.text_scroll_speed,
        has_panoramas=bool(files_map.get("has_panoramas")),
        has_video=bool(files_map.get("has_video")),
        stream_frames=int(files_map.get("stream_frames") or 0),
        stream_fps=int(files_map.get("stream_fps") or criteria.video_fps),
        stream_duration_s=float(files_map.get("stream_duration_s") or 0.0),
    )
    unified_path_str = str(files_map.get("unified_stream_path") or "")
    unified_path = Path(unified_path_str) if unified_path_str else None
    if unified_path is not None and not unified_path.exists():
        unified_path = None

    return PreparedShowcasePackage(
        item_id=item_id,
        domain=domain,
        dmd_id=dmd_id,
        candidate=cand,
        card=card,
        geometry=geometry,
        item_dir=item_dir,
        unified_stream_path=unified_path,
        files_map=files_map,
    )
