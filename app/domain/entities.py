"""
Pure Domain Entities & Value Objects for DMD Media Showcase (zero I/O, zero framework dependencies).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

DomainType = Literal["games", "movies", "series", "anime"]


@dataclass(frozen=True)
class PanelGeometry:
    """Target HUB75 DMD panel geometry and adaptive mini-jacket dimensions."""

    width: int = 128
    height: int = 32
    poster_width: Optional[int] = None
    poster_height: Optional[int] = None

    @property
    def is_tate(self) -> bool:
        return self.height > self.width

    def resolved_poster_dims(self) -> Tuple[int, int]:
        if self.poster_width and self.poster_height:
            return self.poster_width, self.poster_height
        if self.height > self.width:
            side = 40 if self.width >= 64 else 24
            return side, side
        if self.width == self.height:
            side = min(32, self.width // 2)
            return side, side
        if self.width >= 128 and self.height >= 64:
            return 48, 64
        side = min(32, self.height, self.width // 3)
        return side, side


@dataclass
class FilterCriteria:
    """User-selected catalog filter criteria and presentation toggles for a showcase engine."""

    domain: DomainType = "games"
    lang: str = "fr"
    systems: List[str] = field(
        default_factory=lambda: ["arcade", "mame", "neogeo", "cps1", "cps2", "snes", "megadrive"]
    )
    genres: List[str] = field(default_factory=list)
    decades: List[str] = field(default_factory=list)
    min_rating: float = 6.0
    video_type: str = "any"
    include_poster: bool = True
    include_synopsis: bool = True
    text_scroll_speed: Literal["slow", "normal", "fast"] = "normal"
    jacket_panorama: bool = True
    marquee_panorama: bool = False
    snapshot_panorama_count: int = 0
    include_video: bool = True
    video_max_duration_s: float = 8.0
    video_fps: int = 15
    video_mode: str = "auto_action"
    color_boost: bool = True


@dataclass
class MediaCandidate:
    """
    Domain entity representing a unique media item discovered from a live scraper source
    before DMD transcoding.
    """

    id: str
    domain: DomainType
    title: str
    year: str
    badge: str
    badge_color: str
    subtitle: str
    rating: str
    synopsis: str
    lang: str = "fr"
    poster_url: Optional[str] = None
    jacket_url: Optional[str] = None
    marquee_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    snapshot_urls: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    youtube_url: Optional[str] = None
    source_id: str = "live"
    theme_palette: Tuple[int, int, int] = (230, 74, 25)
    extra_meta: Dict[str, str] = field(default_factory=dict)

    def add_unique_snapshot(self, url: Optional[str]) -> None:
        if not url or not url.startswith(("http://", "https://")):
            return
        if url not in self.snapshot_urls and url != self.poster_url and url != self.marquee_url:
            self.snapshot_urls.append(url)
        if not self.backdrop_url:
            self.backdrop_url = url

    def resolved_jacket_url(self) -> Optional[str]:
        return self.jacket_url or self.poster_url


@dataclass
class ShowcaseCard:
    """
    Phase 1 Card payload delivered in JSON over MQTT (`dmd_showcase/clients/{dmd_id}/{domain}/ready`)
    or HTTP (`/api/v1/{domain}/next`) so the DMD renders the mini-jacket and scrolls the synopsis
    organically at `text_scroll_speed`.
    """

    item_id: str
    domain: DomainType
    title: str
    year: str
    badge: str
    badge_color: str
    subtitle: str
    rating: str
    synopsis: str
    lang: str
    source_used: str
    poster_width: int
    poster_height: int
    poster_rgb565_b64: str
    text_scroll_speed: str = "normal"
    has_panoramas: bool = False
    has_video: bool = False
    stream_frames: int = 0
    stream_fps: int = 15
    stream_duration_s: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        poster_url = f"/api/v1/{self.domain}/poster/{self.item_id}.rgb565"
        stream_url = (
            f"/api/v1/{self.domain}/stream/{self.item_id}.rgb565.stream"
            if self.stream_frames > 0
            else ""
        )
        return {
            "id": self.item_id,
            "item_id": self.item_id,
            "domain": self.domain,
            "title": self.title,
            "year": self.year,
            "badge": self.badge,
            "badge_color": self.badge_color,
            "subtitle": self.subtitle,
            "rating": self.rating,
            "synopsis": self.synopsis,
            "lang": self.lang,
            "source_used": self.source_used,
            "poster_width": self.poster_width,
            "poster_height": self.poster_height,
            "poster_rgb565_b64": self.poster_rgb565_b64,
            "poster_rgb565_url": poster_url,
            "text_scroll_speed": self.text_scroll_speed,
            "has_panoramas": self.has_panoramas,
            "has_video": self.has_video,
            "stream_frames": self.stream_frames,
            "stream_fps": self.stream_fps,
            "fps": self.stream_fps,
            "stream_duration_s": self.stream_duration_s,
            "total_stream_duration_s": self.stream_duration_s,
            "assets": {
                "poster_rgb565": poster_url,
                "unified_stream_rgb565": stream_url,
            },
        }


@dataclass
class PreparedShowcasePackage:
    """
    Prepared consume-once media package on disk for a specific `(dmd_id, domain)` slot.
    Purged immediately after streaming completes (`stream_eof` or HTTP stream close).
    """

    item_id: str
    domain: DomainType
    dmd_id: str
    candidate: MediaCandidate
    card: ShowcaseCard
    geometry: PanelGeometry
    item_dir: Path
    unified_stream_path: Optional[Path] = None
    files_map: Dict[str, object] = field(default_factory=dict)
    consumed: bool = False
    created_at: float = field(default_factory=time.monotonic)
