"""
Canonical Pydantic data models for the DMD Media Showcase API.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

DomainType = Literal["games", "movies", "series", "anime"]
SupportedLang = Literal["fr", "en", "es", "de", "it", "pt", "ja"]


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

    # Component toggles & image count chosen by the DMD user
    include_poster: bool = True
    include_synopsis: bool = True
    include_snapshot: bool = True
    include_video: bool = True
    image_count: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Number of top-to-bottom scrolling panorama images to generate per item (0..5).",
    )

    # Scraper source selection & optional credentials forwarded from the DMD engine config
    primary_source: str = Field(
        default="auto",
        description=(
            "Preferred primary scraper source ('auto', 'screenscraper', 'arcadeitalia', 'libretro', "
            "'tmdb', 'tvdb', 'tvmaze', 'animethemes', 'jikan', 'wikipedia'). Other sources are used as fallback."
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
        default_factory=lambda: ["mame", "neogeo", "snes", "megadrive", "psx"],
        description="Selected arcade/console systems (for 'games' domain).",
    )
    genres: List[str] = Field(
        default_factory=list,
        description="Selected genres (e.g. scifi, action, horror, shonen, mecha).",
    )
    decades: List[str] = Field(
        default_factory=list,
        description="Selected decades (e.g. 1980s, 1990s, 2000s).",
    )
    min_rating: float = Field(default=6.5, ge=0.0, le=9.9)
    video_type: Literal["opening", "ending", "pv", "trailer", "any"] = "any"


class MediaAssetsUrls(BaseModel):
    """Relative URLs to the pre-processed DMD assets for a showcase item."""

    poster_png: str
    poster_rgb565: str
    marquee_png: str
    marquee_rgb565: str
    snapshot_pan_png: str
    snapshot_pan_rgb565: str
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
    source_used: str = "catalog"
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
    Future-ready payload for triggering an immediate on-demand item from a voice
    assistant (Google Home / Alexa / Home Assistant) or external webhook.
    """

    query: str = Field(..., min_length=1, description="Search query (e.g. 'Metal Slug', 'Blade Runner').")
    domain: DomainType = "movies"
    lang: str = "fr"
    width: int = 128
    height: int = 32
    image_count: int = 2
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
