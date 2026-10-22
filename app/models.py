"""
Canonical Pydantic data models for the DMD Media Showcase API.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field

DomainType = Literal["games", "movies", "series", "anime"]
SupportedLang = Literal["fr", "en", "es", "de", "it", "pt", "ja"]


def compute_card_poster_dimensions(width: int, height: int) -> Tuple[int, int]:
    """
    Compute the optimal mini-jacket `(poster_w, poster_h)` dimensions on the Info Card
    for any DMD panel resolution or TATE orientation:
      - TATE / Vertical portrait (`height > width`, e.g. 64x128, 64x256, 32x128):
        top-centered square jacket (`40x40` when width >= 64, `24x24` when width < 64).
      - Square (`width == height`, e.g. 64x64):
        top-left `32x32` jacket.
      - Tall / HD Landscape (`width >= 128` and `height >= 64`, e.g. 128x64, 256x64):
        left `48x64` portrait cover (or `32x32` when requested by legacy clients).
      - Classic WPC Landscape (`128x32` or `height < 64`):
        left `32x32` jacket.
    """
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


class JobPriority(IntEnum):
    """Priority levels in the bounded FIFO worker queue."""

    HIGH_ON_DEMAND = 1   # Reserved for on-demand / voice assistant requests (Google Home, etc.)
    NORMAL_BATCH = 10    # Standard DMD CurrentBatch preparation
    LOW_PREFETCH = 20    # Background NextBatch pre-warming ("one step ahead")


class SessionRegisterRequest(BaseModel):
    """
    Payload sent proactively by a DMD Engine on boot or configuration change
    (`POST /api/v1/{domain}/session`) before its rotation turn arrives.
    """

    client_id: Optional[str] = Field(
        default=None,
        description="Optional stable identifier for the DMD engine instance (e.g. 'rpi_games').",
    )
    count: int = Field(
        default=3,
        ge=1,
        le=25,
        description="Exact number of random items to preload per rotation turn.",
    )
    lang: str = Field(
        default="fr",
        description="ISO language code for titles, synopses, and localized posters (fr, en, es, de, it).",
    )
    width: int = Field(default=128, ge=32, le=512, description="Target LED matrix width in pixels.")
    height: int = Field(default=32, ge=16, le=256, description="Target LED matrix height in pixels.")
    poster_width: Optional[int] = Field(
        default=None,
        ge=16,
        le=128,
        description="Optional explicit mini-jacket width on the Info Card (auto-derived from width/height if omitted).",
    )
    poster_height: Optional[int] = Field(
        default=None,
        ge=16,
        le=128,
        description="Optional explicit mini-jacket height on the Info Card (auto-derived from width/height if omitted).",
    )

    # Component toggles & granular panorama options chosen by the DMD user
    include_poster: bool = Field(
        default=True,
        description="Display the mini-jacket / poster on the Info Card alongside title and synopsis.",
    )
    include_synopsis: bool = Field(
        default=True,
        description="Scroll the full localized synopsis on the Info Card.",
    )
    text_scroll_speed: Literal["slow", "normal", "fast"] = Field(
        default="normal",
        description="Organic text scrolling speed on the DMD Info Card ('slow'=16px/s, 'normal'=24px/s, 'fast'=36px/s).",
    )
    jacket_panorama: bool = Field(
        default=True,
        description="Display full-screen top-to-bottom scrolling panorama of the Jacket / Poster right after the Info Card.",
    )
    marquee_panorama: bool = Field(
        default=False,
        description="Display full-screen scrolling panorama of the Marquee / Wheel / Banner / Logo.",
    )
    snapshot_panorama_count: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Number of in-game screenshots or scene stills (0..5) to scroll in panorama mode.",
    )
    include_snapshot: bool = Field(
        default=True,
        description="Enable panorama image streams after the Info Card.",
    )
    image_count: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Legacy alias for snapshot_panorama_count (0..5).",
    )
    include_video: bool = Field(
        default=True,
        description="Stream real video clip / trailer after panoramas (skipped if no video is available).",
    )

    # Scraper source selection & optional credentials forwarded from the DMD engine config
    primary_source: str = Field(
        default="auto",
        description=(
            "Preferred primary scraper source ('auto', 'screenscraper', 'arcadeitalia', 'libretro', "
            "'tmdb', 'imdb', 'tvmaze', 'animethemes', 'kitsu', 'jikan', 'wikipedia'). Other sources are used as fallback."
        ),
    )
    scraper_user: str = Field(
        default="",
        description="Optional scraper username (e.g. ScreenScraper account) configured on the DMD engine.",
    )
    scraper_pass: str = Field(
        default="",
        description="Optional scraper password configured on the DMD engine.",
    )
    scraper_api_key: str = Field(
        default="",
        description="Optional API key (TMDB, TheTVDB, or ScreenScraper devid:devpassword) configured on the DMD engine.",
    )

    # Video & Colorimetry parameters
    video_max_duration_s: float = Field(
        default=6.0,
        ge=1.5,
        le=30.0,
        description="Maximum video duration in seconds requested by the DMD.",
    )
    video_fps: int = Field(default=15, ge=6, le=30, description="Target video frame rate.")
    video_mode: Literal[
        "auto_action", "center_crop", "pixel_art", "anime", "cinema", "panorama"
    ] = "auto_action"
    color_boost: bool = True

    # Domain-specific filters
    systems: List[str] = Field(
        default_factory=lambda: ["mame", "neogeo", "cps1", "cps2", "snes", "megadrive", "psx"],
        description="Selected arcade/console systems (for 'games' domain).",
    )
    genres: List[str] = Field(
        default_factory=list,
        description="Selected genres/categories matching the live APIs.",
    )
    decades: List[str] = Field(
        default_factory=list,
        description="Selected decades (e.g. 1980s, 1990s, 2000s, 2010s, 2020s).",
    )
    decade: Optional[str] = Field(
        default=None,
        description="Optional single decade filter (e.g. '1990s').",
    )
    min_rating: float = Field(default=6.0, ge=0.0, le=9.9)
    video_type: Literal["opening", "ending", "pv", "trailer", "scene", "any"] = "any"

    def effective_snapshot_count(self) -> int:
        if self.snapshot_panorama_count > 0:
            return min(5, self.snapshot_panorama_count)
        if self.image_count > 0:
            return min(5, self.image_count)
        return 0

    def resolved_poster_dims(self) -> Tuple[int, int]:
        if self.poster_width and self.poster_height:
            return self.poster_width, self.poster_height
        return compute_card_poster_dimensions(self.width, self.height)


class MediaAssetsUrls(BaseModel):
    """Relative URLs to the pre-processed DMD assets for a showcase item."""

    poster_png: str
    poster_rgb565: str
    marquee_png: str
    marquee_rgb565: str
    snapshot_pan_png: str
    snapshot_pan_rgb565: str
    jacket_gif: str = ""
    jacket_stream_rgb565: str = ""
    marquee_gif: str = ""
    marquee_stream_rgb565: str = ""
    snapshot_gifs: List[str] = Field(default_factory=list)
    snapshot_streams_rgb565: List[str] = Field(default_factory=list)
    panorama_gifs: List[str] = Field(default_factory=list)
    panorama_streams_rgb565: List[str] = Field(default_factory=list)
    snapshots_pan_rgb565: List[str] = Field(default_factory=list)
    video_gif: str = ""
    video_stream_rgb565: str = ""


class MediaShowcaseItem(BaseModel):
    """Normalized media showcase card identical across Games, Movies, Series, and Anime."""

    id: str
    domain: DomainType
    title: str
    year: str
    badge: str
    badge_color: str = "#FF6D00"
    subtitle: str
    rating: str
    synopsis: str
    lang: str = "fr"
    source_used: str = "live_api"
    poster_width: int = 32
    poster_height: int = 32
    text_scroll_speed: str = "normal"
    jacket_panorama: bool = False
    marquee_panorama: bool = False
    snapshot_panorama_count: int = 0
    image_count: int = 0
    has_video: bool = False
    video_ready: bool = False
    video_duration_s: float = 0.0
    video_fps: int = 15
    quality_score: int = 85
    on_demand: bool = False
    assets: MediaAssetsUrls


class SessionRegisterResponse(BaseModel):
    """Response to `POST /api/v1/{domain}/session`."""

    session_id: str
    domain: DomainType
    status: Literal["warming", "ready"]
    batch_size: int
    ready_count: int
    queue_depth: int
    active_workers: int


class BatchStatusResponse(BaseModel):
    """
    Response to the fast LAN check `GET /api/v1/{domain}/batch?session_id=...`
    ("Are you ready?").
    """

    session_id: str
    domain: DomainType
    status: Literal["warming", "ready"]
    ready_count: int
    total_count: int
    queue_position: int
    active_workers: int
    items: List[MediaShowcaseItem]


class OnDemandRequest(BaseModel):
    """
    Payload for triggering an immediate on-demand item from a voice
    assistant (Google Home / Alexa / Home Assistant) or external webhook.
    """

    query: str = Field(..., min_length=1, description="Search query (e.g. 'Metal Slug', 'Blade Runner').")
    domain: DomainType = "movies"
    lang: str = "fr"
    width: int = 128
    height: int = 32
    jacket_panorama: bool = True
    marquee_panorama: bool = False
    snapshot_panorama_count: int = 0
    image_count: int = 0
    video_max_duration_s: float = 8.0
    video_fps: int = 15
    primary_source: str = "auto"
    scraper_user: str = ""
    scraper_pass: str = ""
    scraper_api_key: str = ""
    target_session_id: Optional[str] = Field(
        default=None,
        description="If provided, injects the prepared item at the front of this DMD session's priority queue.",
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    converter_version: str
    active_workers: int
    max_workers: int
    queue_depth: int
    active_sessions: int
    completed_jobs_total: int
    avg_job_duration_s: float
    providers_configured: Dict[str, str]
    mqtt_mode: str = "embedded"
    mqtt_port: int = 1883
    seen_history_counts: Dict[str, int] = Field(default_factory=dict)


class AdminServerConfigPayload(BaseModel):
    tmdb_api_key: Optional[str] = None
    screenscraper_user: Optional[str] = None
    screenscraper_pass: Optional[str] = None
    screenscraper_dev_id: Optional[str] = None
    screenscraper_dev_pass: Optional[str] = None
    mqtt_mode: Optional[Literal["embedded", "external"]] = None
    mqtt_port: Optional[int] = Field(default=None, ge=1, le=65535)
    mqtt_external_url: Optional[str] = None
    default_lang: Optional[str] = None
    showcase_api_key: Optional[str] = None

