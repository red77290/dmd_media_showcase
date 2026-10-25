"""
Canonical Pydantic data models for the DMD Media Showcase API.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, List, Literal, Optional, Tuple, Union
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
    text_scroll_speed: str = Field(
        default="normal",
        description="Organic text scrolling speed on the DMD Info Card ('1'..'10' or 'slow'|'normal'|'fast'|'turbo').",
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
        default=8.0,
        ge=1.5,
        le=120.0,
        description="Maximum video duration in seconds requested by the DMD.",
    )
    video_fps: int = Field(default=15, ge=6, le=60, description="Target video frame rate.")
    video_mode: Literal[
        "auto_action", "panorama", "center_crop", "pixel_art", "anime", "cinema"
    ] = Field(
        default="auto_action",
        description="Video framing strategy: 'auto_action' (Let Me Handle It AI), 'panorama' (top-bottom scan), or 'center_crop' (static center crop).",
    )
    color_boost: Union[
        Literal["auto", "pixel_art", "anime", "cinema"], bool, str
    ] = Field(
        default="auto",
        description="LED colorimetry profile: 'auto' (Let Me Handle It AI Smart Color Boost), 'pixel_art', 'anime', or 'cinema'.",
    )

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
    include_video: bool = True
    video_mode: Literal[
        "auto_action", "panorama", "center_crop", "pixel_art", "anime", "cinema"
    ] = "auto_action"
    color_boost: Union[
        Literal["auto", "pixel_art", "anime", "cinema"], bool, str
    ] = "auto"
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
    providers_detailed: Dict[str, List[Dict[str, Union[str, bool]]]] = Field(default_factory=dict)
    mqtt_mode: str = "embedded"
    mqtt_port: int = 1883
    mqtt_connected_clients: int = 0
    mqtt_tcp_sessions: int = 0
    registered_dmd_slots: int = 0
    ready_slots: int = 0
    standby_slots: int = 0
    active_udp_streams: int = 0
    active_broadcasts: int = 0
    video_max_duration_limit_s: float = 30.0
    video_max_fps_limit: int = 30
    use_process_pool: bool = True
    seen_history_counts: Dict[str, int] = Field(default_factory=dict)


class AdminServerConfigPayload(BaseModel):
    tmdb_api_key: Optional[str] = None
    tvdb_api_key: Optional[str] = None
    screenscraper_user: Optional[str] = None
    screenscraper_pass: Optional[str] = None
    screenscraper_dev_id: Optional[str] = None
    screenscraper_dev_pass: Optional[str] = None
    mqtt_mode: Optional[Literal["embedded", "external"]] = None
    mqtt_port: Optional[int] = Field(default=None, ge=1, le=65535)
    mqtt_external_url: Optional[str] = None
    default_lang: Optional[str] = None
    showcase_api_key: Optional[str] = None
    # Real-time worker pool & CPU tuning
    max_concurrent_jobs: Optional[int] = Field(default=None, ge=0, le=16)
    use_process_pool: Optional[bool] = None
    ffmpeg_threads_per_job: Optional[int] = Field(default=None, ge=1, le=8)
    worker_nice_increment: Optional[int] = Field(default=None, ge=0, le=19)
    max_queue_size: Optional[int] = Field(default=None, ge=10, le=1000)
    # Real-time video & media limits
    video_max_duration_limit_s: Optional[float] = Field(default=None, ge=1.5, le=120.0)
    server_master_clip_max_s: Optional[float] = Field(default=None, ge=1.5, le=120.0)
    video_max_fps_limit: Optional[int] = Field(default=None, ge=6, le=60)
    panorama_duration_s: Optional[float] = Field(default=None, ge=1.5, le=15.0)
    ytdlp_max_filesize_mb: Optional[int] = Field(default=None, ge=10, le=250)
    default_video_mode: Optional[Literal["auto_action", "panorama", "center_crop"]] = None
    default_color_boost: Optional[Literal["auto", "pixel_art", "anime", "cinema"]] = None
    # Cache, session TTLs, mDNS & logging
    session_ttl_seconds: Optional[int] = Field(default=None, ge=60, le=86400)
    batch_ttl_seconds: Optional[int] = Field(default=None, ge=30, le=7200)
    cache_dir: Optional[str] = None
    log_level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR"]] = None
    mdns_enabled: Optional[bool] = None
    mdns_hostname: Optional[str] = None
    trust_proxy_headers: Optional[bool] = None


class DmdPlaylistSlotPayload(BaseModel):
    """Single domain rotation slot inside a server-managed DMD profile (`dmd_profiles.json`)."""

    enabled: bool = True
    domain: DomainType = "games"
    count: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of consecutive items played from this domain per playlist cycle.",
    )
    lang: Optional[str] = Field(
        default=None,
        description="Optional per-slot content broadcast language override ('fr', 'en', 'es', 'de', 'it', 'pt', 'ja').",
    )
    systems: List[str] = Field(default_factory=list)
    genres: List[str] = Field(default_factory=list)
    decades: List[str] = Field(default_factory=list)
    min_rating: float = Field(default=6.0, ge=0.0, le=9.9)
    video_type: str = "any"
    include_poster: bool = True
    include_synopsis: bool = True
    text_scroll_speed: str = "normal"
    jacket_panorama: bool = True
    marquee_panorama: bool = False
    snapshot_panorama_count: int = Field(default=0, ge=0, le=5)
    include_video: bool = True
    video_max_duration_s: float = Field(default=8.0, ge=1.5, le=120.0)
    video_fps: int = Field(default=15, ge=6, le=60)
    video_mode: str = "auto_action"
    color_boost: Union[str, bool] = "auto"


class DmdProfilePayload(BaseModel):
    """
    Persistent per-DMD profile & server-managed playlist stored in `./data/dmd_profiles.json`.
    Supports Mode 1 (`config_source='client'`), Mode 2A (`config_source='server', supports_local_card=True`),
    and Mode 2B (`config_source='server', supports_local_card=False`).
    """

    dmd_id: Optional[str] = None
    display_name: str = ""
    config_source: Literal["client", "server"] = "server"
    supports_local_card: bool = Field(
        default=True,
        description="True (Mode 2A): DMD renders 60 FPS text Info Card locally + streams UDP video. False (Mode 2B): Server renders Info Card directly into RGB565 UDP stream.",
    )
    always_stream_udp: bool = Field(
        default=False,
        description="When True in Mode 2B, streams UDP DMDF frames continuously to udp_ip:udp_port even without MQTT presence.",
    )
    width: int = Field(default=128, ge=32, le=512)
    height: int = Field(default=32, ge=16, le=256)
    udp_ip: str = ""
    udp_port: int = Field(default=4210, ge=1, le=65535)
    lang: str = Field(
        default="fr",
        description="Content broadcast language ('fr', 'en', 'es', 'de', 'it', 'pt', 'ja') for titles, synopses, and posters.",
    )
    paused: bool = False
    inter_item_gap_s: float = Field(default=1.0, ge=0.0, le=10.0)
    playlist: List[DmdPlaylistSlotPayload] = Field(default_factory=list)


class ClientHelloPayload(BaseModel):
    """Lightweight hardware announcement payload for passive ESP32/RPi receivers (MQTT or HTTP)."""

    dmd_id: str = Field(..., min_length=1)
    display_name: str = ""
    config_source: Literal["client", "server"] = "server"
    supports_local_card: bool = True
    width: int = Field(default=128, ge=32, le=512)
    height: int = Field(default=32, ge=16, le=256)
    udp_ip: str = ""
    client_ip: str = ""
    udp_port: int = Field(default=4210, ge=1, le=65535)
    lang: Optional[str] = None


class SearchRequestPayload(BaseModel):
    """Multi-candidate localized catalog search request (`POST /api/v1/{domain}/search`)."""

    q: str = Field(
        default="",
        description="Optional text query (title, ROM name, franchise). Empty returns filtered browse items.",
    )
    query: Optional[str] = Field(default=None, description="Optional alias for `q`.")
    lang: str = Field(
        default="fr",
        description="Content broadcast language ('fr', 'en', 'es', 'de', 'it', 'pt', 'ja').",
    )
    systems: List[str] = Field(default_factory=list)
    genres: List[str] = Field(default_factory=list)
    decades: List[str] = Field(default_factory=list)
    video_type: str = "any"
    limit: int = Field(default=8, ge=1, le=20)


class SearchCandidateModel(BaseModel):
    """Single media item candidate returned by `/api/v1/{domain}/search`."""

    candidate_id: str
    id: str
    domain: DomainType
    title: str
    year: str
    badge: str
    badge_color: str
    subtitle: str
    rating: str
    synopsis: str
    lang: str
    source_id: str
    poster_url: Optional[str] = None
    has_video: bool = False


class SearchCandidatesResponse(BaseModel):
    """Response returned by `GET/POST /api/v1/{domain}/search`."""

    domain: DomainType
    query: str
    lang: str
    count: int
    items: List[SearchCandidateModel]


class QueueAddRequest(BaseModel):
    """
    Enqueue or immediately play a specific media title on a target DMD (`POST /api/v1/clients/{dmd_id}/queue`).
    Accepts either `candidate_id` (from `/search` UI selection) or `{domain, query, systems, lang}` (1-shot FrontendSync / webhook).
    """

    candidate_id: Optional[str] = Field(
        default=None,
        description="Exact staged candidate_id returned by `/api/v1/{domain}/search`.",
    )
    domain: DomainType = "games"
    query: str = Field(
        default="",
        description="Direct search query when `candidate_id` is omitted (e.g. FrontendSync game launch or voice command).",
    )
    systems: List[str] = Field(default_factory=list)
    genres: List[str] = Field(default_factory=list)
    decades: List[str] = Field(default_factory=list)
    video_type: str = "any"
    lang: Optional[str] = Field(
        default=None,
        description="Content broadcast language ('fr', 'en', 'es', 'de', 'it', 'pt', 'ja'). Defaults to target DMD profile language.",
    )
    mode: Literal["enqueue_next", "play_now"] = "enqueue_next"
    mark_seen: bool = Field(
        default=True,
        description="When False (e.g. FrontendSync), does not record the item in seen_history.json so it can be displayed again.",
    )
    width: Optional[int] = Field(default=None, ge=32, le=512)
    height: Optional[int] = Field(default=None, ge=16, le=256)
    include_poster: Optional[bool] = None
    include_synopsis: Optional[bool] = None
    text_scroll_speed: Optional[str] = None
    jacket_panorama: Optional[bool] = None
    marquee_panorama: Optional[bool] = None
    snapshot_panorama_count: Optional[int] = Field(default=None, ge=0, le=5)
    include_video: Optional[bool] = None
    video_max_duration_s: Optional[float] = Field(default=None, ge=1.5, le=120.0)
    video_fps: Optional[int] = Field(default=None, ge=6, le=60)
    video_mode: Optional[str] = None
    color_boost: Optional[Union[str, bool]] = None


class QueueItemModel(BaseModel):
    """Single item in a DMD client's On-Demand priority queue."""

    queue_id: str
    dmd_id: str
    domain: DomainType
    candidate_id: str
    item_id: str = ""
    title: str
    year: str = ""
    badge: str = ""
    lang: str = "fr"
    mode: Literal["enqueue_next", "play_now"] = "enqueue_next"
    mark_seen: bool = True
    status: Literal["queued", "preparing", "ready", "playing"] = "queued"
    created_at: float


class ClientControlRequest(BaseModel):
    """Live playback control command for a DMD client (`POST /api/v1/clients/{dmd_id}/control`)."""

    action: Literal["skip", "pause", "resume", "stop"]


