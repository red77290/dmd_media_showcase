"""
Pure Domain Request-Keyed Cache Key Builder (`app/domain/on_demand_cache_key.py`).

Computes deterministic cache keys strictly from the incoming On-Demand request
(`domain`, `systems`, `query`, `geometry`, and rendering `criteria`) BEFORE any
upstream scraper lookup or media conversion is executed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Any, Dict, Iterable, List, Tuple

from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
)
from app.domain.taxonomy.resolver import resolve_canonical_system

_ROM_EXT_RE = re.compile(
    r"\.(?:zip|7z|rar|sfc|smc|nes|fds|gb|gbc|gba|nds|md|gen|smd|bin|cue|chd|iso|pbp|pce|ngp|ngc|ws|wsc|lnx|a26|a78|col|rom|dsk|adf|prg)$",
    re.IGNORECASE,
)
_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class OnDemandRequestCacheKey:
    """Deterministic request-derived cache key and style signature."""

    cache_key: str
    render_signature: str
    normalized_system: str
    normalized_query: str


def compute_criteria_render_signature(
    geometry: PanelGeometry,
    criteria: FilterCriteria,
) -> str:
    """Compute a compact deterministic hash over geometry and sequence presentation options."""
    payload = {
        "w": int(geometry.width),
        "h": int(geometry.height),
        "lang": str(criteria.lang or "fr").lower(),
        "render_card": bool(criteria.render_card_in_stream),
        "card_intro": bool(criteria.include_card_intro),
        "layout": str(criteria.card_layout),
        "theme": str(criteria.card_theme),
        "font": str(criteria.card_font),
        "accent": str(criteria.card_accent_color),
        "badge": bool(criteria.show_badge),
        "year": bool(criteria.show_year),
        "rating": bool(criteria.show_rating),
        "hold_s": round(float(criteria.card_hold_s), 2),
        "poster": bool(criteria.include_poster),
        "synopsis": bool(criteria.include_synopsis),
        "scroll": str(criteria.text_scroll_speed),
        "jacket_pan": bool(criteria.jacket_panorama),
        "marquee_pan": bool(criteria.marquee_panorama),
        "snap_cnt": int(criteria.snapshot_panorama_count),
        "pan_dur": round(float(criteria.panorama_duration_s), 2),
        "video": bool(criteria.include_video),
        "vid_dur": round(float(criteria.video_max_duration_s), 2),
        "vid_fps": int(criteria.video_fps),
        "vid_mode": str(criteria.video_mode),
        "cboost": str(criteria.color_boost),
        "vtype": str(criteria.video_type),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def build_on_demand_request_cache_key(
    domain: DomainType,
    raw_systems: Iterable[str],
    raw_query: str,
    geometry: PanelGeometry,
    criteria: FilterCriteria,
) -> OnDemandRequestCacheKey:
    """
    Derive a collision-free cache key strictly from `(domain, systems, query, geometry, criteria)`
    without touching any scraper source.
    """
    canon_systems = sorted(
        {
            resolve_canonical_system(s)
            for s in (raw_systems or [])
            if s and str(s).strip().lower() not in ("", "all")
        }
    )
    norm_sys = "_".join(canon_systems) if canon_systems else "any"

    raw_q = str(raw_query or "").strip()
    base_q = os.path.basename(raw_q.replace("\\", "/")) if ("/" in raw_q or "\\" in raw_q) else raw_q
    norm_q = _ROM_EXT_RE.sub("", base_q).strip().lower()

    query_slug = _SAFE_TOKEN_RE.sub("_", norm_q).strip("_")[:56] or "item"
    sys_slug = _SAFE_TOKEN_RE.sub("_", norm_sys).strip("_")[:24] or "any"
    req_digest = hashlib.sha256(
        f"{domain}|{norm_sys}|{norm_q}".encode("utf-8")
    ).hexdigest()[:8]
    render_sig = compute_criteria_render_signature(geometry, criteria)

    cache_key = (
        f"{domain}__{sys_slug}__{query_slug}__{req_digest}"
        f"__{geometry.width}x{geometry.height}__{render_sig[:8]}"
    )
    return OnDemandRequestCacheKey(
        cache_key=cache_key,
        render_signature=render_sig,
        normalized_system=norm_sys,
        normalized_query=norm_q,
    )


def candidate_to_cache_dict(cand: MediaCandidate) -> Dict[str, Any]:
    """Serialize a `MediaCandidate` into a JSON-compatible dictionary."""
    r, g, b = cand.theme_palette
    return {
        "id": cand.id,
        "domain": cand.domain,
        "title": cand.title,
        "year": cand.year,
        "badge": cand.badge,
        "badge_color": cand.badge_color,
        "subtitle": cand.subtitle,
        "rating": cand.rating,
        "synopsis": cand.synopsis,
        "lang": cand.lang,
        "poster_url": cand.poster_url,
        "jacket_url": cand.jacket_url,
        "marquee_url": cand.marquee_url,
        "backdrop_url": cand.backdrop_url,
        "snapshot_urls": list(cand.snapshot_urls),
        "video_url": cand.video_url,
        "youtube_url": cand.youtube_url,
        "source_id": cand.source_id,
        "theme_palette": [int(r), int(g), int(b)],
        "extra_meta": dict(cand.extra_meta or {}),
    }


def candidate_from_cache_dict(data: Dict[str, Any]) -> MediaCandidate:
    """Rehydrate a `MediaCandidate` from its cached JSON dictionary."""
    raw_pal: List[Any] = list(data.get("theme_palette") or [230, 74, 25])
    palette: Tuple[int, int, int] = (
        int(raw_pal[0]) if len(raw_pal) > 0 else 230,
        int(raw_pal[1]) if len(raw_pal) > 1 else 74,
        int(raw_pal[2]) if len(raw_pal) > 2 else 25,
    )
    return MediaCandidate(
        id=str(data.get("id") or "cached"),
        domain=data.get("domain") or "games",  # type: ignore[arg-type]
        title=str(data.get("title") or "Cached Item"),
        year=str(data.get("year") or ""),
        badge=str(data.get("badge") or "GAMES"),
        badge_color=str(data.get("badge_color") or "#FF6D00"),
        subtitle=str(data.get("subtitle") or ""),
        rating=str(data.get("rating") or ""),
        synopsis=str(data.get("synopsis") or ""),
        lang=str(data.get("lang") or "fr"),
        poster_url=data.get("poster_url"),
        jacket_url=data.get("jacket_url"),
        marquee_url=data.get("marquee_url"),
        backdrop_url=data.get("backdrop_url"),
        snapshot_urls=[str(u) for u in (data.get("snapshot_urls") or [])],
        video_url=data.get("video_url"),
        youtube_url=data.get("youtube_url"),
        source_id=str(data.get("source_id") or "cache"),
        theme_palette=palette,
        extra_meta={str(k): str(v) for k, v in (data.get("extra_meta") or {}).items()},
    )
