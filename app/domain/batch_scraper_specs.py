"""
Pure Domain Specifications & Folder Naming Policies for the Batch Scraper (`app/domain/batch_scraper_specs.py`).

100% pure Python (zero FastAPI, HTTP, Pillow, OpenCV, or filesystem I/O imports).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Dict, List, Literal, Optional, Sequence, Set, Tuple, Union

from app.domain.entities import DomainType, FilterCriteria, PanelGeometry
from app.domain.taxonomy.resolver import resolve_canonical_genre, resolve_canonical_system

BatchScrapeMode = Literal["random_filter", "recalbox_gamelist"]
BatchDeliveryMode = Literal["direct_folder", "zip_stream"]

BatchPresetId = Literal[
    "full_showcase",
    "video_only",
    "poster_only",
    "marquee_only",
    "card_description_only",
    "description_only",
    "screenshot_only",
    "custom",
]

BATCH_VIDEO_MIN_DURATION_S: float = 2.0
BATCH_VIDEO_MAX_DURATION_S: float = 120.0
BATCH_VIDEO_DEFAULT_DURATION_S: float = 8.0

# Friendly folder suffixes matching ArcadeMatrix Clock GIF playlist naming conventions
_GENRE_FOLDER_DISPLAY_MAP: Dict[str, str] = {
    "platform": "platformer",
    "runandgun": "run&gun",
    "shootemup": "shootemup",
    "beatemup": "beatemup",
    "fighting": "fighting",
    "martial_arts": "martial_arts",
    "slice_of_life": "slice_of_life",
    "mahou_shoujo": "mahou_shoujo",
    "post_apocalyptic": "post_apocalyptic",
    "science-fiction": "scifi",
}

# Mapping from canonical Showcase system IDs to ArcadeMatrix SD folder names (`/showcase/<folder>/`)
_ARCADEMATRIX_SHOWCASE_FOLDER_MAP: Dict[str, str] = {
    "megadrive": "megadrive",
    "genesis": "megadrive",
    "snes": "snes",
    "nes": "nes",
    "neogeo": "neogeo",
    "neogeocd": "neogeocd",
    "mame": "mame",
    "arcade": "mame",
    "fbneo": "fbneo",
    "cps1": "cps1",
    "cps2": "cps2",
    "cps3": "cps3",
    "naomi": "naomi",
    "atomiswave": "atomiswave",
    "psx": "psx",
    "ps2": "ps2",
    "psp": "psp",
    "n64": "n64",
    "nds": "nds",
    "gb": "gb",
    "gbc": "gbc",
    "gba": "gba",
    "Dreamcast": "dreamcast",
    "dreamcast": "dreamcast",
    "saturn": "saturn",
    "mastersystem": "mastersystem",
    "gamegear": "gamegear",
    "pcengine": "pcengine",
    "pcenginecd": "pcenginecd",
}


def clamp_batch_video_duration(raw_seconds: float) -> float:
    """Clamp batch video clip duration strictly to `[2.0 .. 120.0]` seconds."""
    try:
        val = float(raw_seconds)
    except (TypeError, ValueError):
        val = BATCH_VIDEO_DEFAULT_DURATION_S
    return round(max(BATCH_VIDEO_MIN_DURATION_S, min(BATCH_VIDEO_MAX_DURATION_S, val)), 2)


@dataclass(frozen=True)
class ChapterSelectionSpec:
    """Modular 'à la carte' chapter composition toggles for unified RGB888 GIF generation."""

    preset: BatchPresetId = "full_showcase"
    include_card: bool = True
    include_poster_in_card: bool = True
    include_description: bool = True
    card_layout: str = "classic_split"
    card_theme: str = "tinted"
    card_font: str = "arcade_pixel"
    card_accent_color: str = "auto"
    show_badge: bool = True
    show_year: bool = True
    show_rating: bool = True
    text_scroll_speed: str = "normal"
    card_hold_s: float = 0.9
    include_jacket: bool = True
    jacket_mode: Literal["panorama", "static"] = "panorama"
    include_marquee: bool = False
    marquee_mode: Literal["panorama", "static"] = "panorama"
    snapshot_count: int = 1
    panorama_duration_s: float = 4.0
    include_video: bool = True

    @classmethod
    def from_preset(cls, preset_id: str, **overrides: object) -> "ChapterSelectionSpec":
        norm = str(preset_id or "full_showcase").strip().lower()
        base_map: Dict[str, ChapterSelectionSpec] = {
            "full_showcase": cls(
                preset="full_showcase",
                include_card=True,
                include_poster_in_card=True,
                include_description=True,
                include_jacket=True,
                include_marquee=False,
                snapshot_count=1,
                include_video=True,
            ),
            "video_only": cls(
                preset="video_only",
                include_card=False,
                include_poster_in_card=False,
                include_description=False,
                include_jacket=False,
                include_marquee=False,
                snapshot_count=0,
                include_video=True,
            ),
            "poster_only": cls(
                preset="poster_only",
                include_card=False,
                include_poster_in_card=False,
                include_description=False,
                include_jacket=True,
                include_marquee=False,
                snapshot_count=0,
                include_video=False,
            ),
            "marquee_only": cls(
                preset="marquee_only",
                include_card=False,
                include_poster_in_card=False,
                include_description=False,
                include_jacket=False,
                include_marquee=True,
                snapshot_count=0,
                include_video=False,
            ),
            "card_description_only": cls(
                preset="card_description_only",
                include_card=True,
                include_poster_in_card=True,
                include_description=True,
                include_jacket=False,
                include_marquee=False,
                snapshot_count=0,
                include_video=False,
            ),
            "description_only": cls(
                preset="description_only",
                include_card=True,
                include_poster_in_card=False,
                include_description=True,
                include_jacket=False,
                include_marquee=False,
                snapshot_count=0,
                include_video=False,
            ),
            "screenshot_only": cls(
                preset="screenshot_only",
                include_card=False,
                include_poster_in_card=False,
                include_description=False,
                include_jacket=False,
                include_marquee=False,
                snapshot_count=1,
                include_video=False,
            ),
            "custom": cls(preset="custom"),
        }
        selected = base_map.get(norm, base_map["full_showcase"])
        valid_fields = set(cls.__dataclass_fields__.keys())
        clean_overrides = {k: v for k, v in overrides.items() if k in valid_fields and v is not None}
        if clean_overrides:
            selected = replace(selected, **clean_overrides)  # type: ignore[arg-type]
        if not selected.has_any_chapter():
            selected = replace(selected, include_card=True, include_description=True)
        return selected

    def has_any_chapter(self) -> bool:
        return bool(
            self.include_card
            or self.include_jacket
            or self.include_marquee
            or self.snapshot_count > 0
            or self.include_video
        )


@dataclass(frozen=True)
class GifEncodingSpec:
    """End-to-end 24-bit RGB888 GIF encoding parameters shared across ESP32/S3 & Raspberry Pi."""

    width: int = 128
    height: int = 32
    fps: int = 15
    video_max_duration_s: float = BATCH_VIDEO_DEFAULT_DURATION_S
    let_me_handle_it: bool = True
    video_mode: Literal["auto_action", "panorama", "center_crop"] = "auto_action"
    content_mode: Literal["auto", "pixel_art", "anime", "cinema", "custom"] = "auto"
    color_boost: Union[str, bool] = "auto"
    dither: Literal["none", "bayer", "floyd_steinberg"] = "none"
    palette_mode: Literal["per_scene_rgb888", "global_rgb888"] = "per_scene_rgb888"
    reject_threshold: int = 0
    brightness: float = -0.02
    contrast: float = 1.45
    saturation: float = 1.85
    gamma: float = 0.90

    def normalized(self) -> "GifEncodingSpec":
        w = max(16, min(512, int(self.width or 128)))
        h = max(16, min(512, int(self.height or 32)))
        f = max(6, min(30, int(self.fps or 15)))
        dur = clamp_batch_video_duration(self.video_max_duration_s)
        rej = max(0, min(95, int(self.reject_threshold or 0)))
        vmode = self.video_mode if self.video_mode in ("auto_action", "panorama", "center_crop") else "auto_action"
        if self.let_me_handle_it:
            vmode = "auto_action"
        dith = self.dither if self.dither in ("none", "bayer", "floyd_steinberg") else "none"
        pmode = (
            self.palette_mode
            if self.palette_mode in ("per_scene_rgb888", "global_rgb888")
            else "per_scene_rgb888"
        )
        cmode = (
            self.content_mode
            if self.content_mode in ("auto", "pixel_art", "anime", "cinema", "custom")
            else "auto"
        )
        return replace(
            self,
            width=w,
            height=h,
            fps=f,
            video_max_duration_s=dur,
            video_mode=vmode,  # type: ignore[arg-type]
            content_mode=cmode,  # type: ignore[arg-type]
            dither=dith,  # type: ignore[arg-type]
            palette_mode=pmode,  # type: ignore[arg-type]
            reject_threshold=rej,
        )

    @property
    def is_tate(self) -> bool:
        return self.height > self.width

    def to_panel_geometry(self) -> PanelGeometry:
        return PanelGeometry(width=self.width, height=self.height)


@dataclass(frozen=True)
class BatchScrapeConfig:
    """Complete immutable configuration for a Batch Scraper job."""

    mode: BatchScrapeMode = "random_filter"
    delivery_mode: BatchDeliveryMode = "direct_folder"
    domain: DomainType = "games"
    lang: str = "fr"
    systems: Tuple[str, ...] = ("snes", "megadrive", "neogeo")
    genres: Tuple[str, ...] = ()
    decades: Tuple[str, ...] = ()
    min_rating: float = 0.0
    target_count: int = 100
    max_workers: int = 2
    ffmpeg_threads_per_worker: int = 1
    folder_template: Literal["system_genre", "system", "genre"] = "system_genre"
    safe_ascii_folders: bool = False
    wrap_orientation_root: bool = False
    include_marquee_png_companion: bool = True
    chapters: ChapterSelectionSpec = field(default_factory=ChapterSelectionSpec)
    encoding: GifEncodingSpec = field(default_factory=GifEncodingSpec)
    existing_relpaths: FrozenSetType = field(default_factory=frozenset)  # type: ignore[assignment]

    def to_filter_criteria(self) -> FilterCriteria:
        enc = self.encoding.normalized()
        ch = self.chapters
        clean_sys = [s for s in self.systems if s and s.strip().lower() != "all"]
        eff_systems = clean_sys if self.systems else ["snes", "megadrive", "neogeo"]
        return FilterCriteria(
            domain=self.domain,
            lang=self.lang or "fr",
            systems=eff_systems,
            genres=list(self.genres),
            decades=list(self.decades),
            min_rating=max(0.0, min(10.0, float(self.min_rating or 0.0))),
            include_poster=ch.include_poster_in_card,
            include_synopsis=ch.include_description,
            text_scroll_speed=ch.text_scroll_speed,
            include_card_intro=ch.include_card,
            card_layout=ch.card_layout,
            card_theme=ch.card_theme,
            card_font=ch.card_font,
            card_accent_color=ch.card_accent_color,
            show_badge=ch.show_badge,
            show_year=ch.show_year,
            show_rating=ch.show_rating,
            card_hold_s=ch.card_hold_s,
            jacket_panorama=ch.include_jacket,
            marquee_panorama=ch.include_marquee,
            snapshot_panorama_count=max(0, min(4, int(ch.snapshot_count))),
            panorama_duration_s=max(1.5, min(15.0, float(ch.panorama_duration_s))),
            include_video=ch.include_video,
            video_max_duration_s=enc.video_max_duration_s,
            video_fps=enc.fps,
            video_mode=enc.video_mode,
            color_boost="auto" if enc.let_me_handle_it else enc.color_boost,
            render_card_in_stream=ch.include_card,
        )


FrozenSetType = frozenset[str]


class FolderNamingPolicy:
    """
    Deterministic folder & filename resolver for Clock GIF playlists (`snes_platformer`,
    `megadrive_fighting`, `neogeo_run&gun`) and Recalbox Offline Sync (`/showcase/<sys>/<rom>.gif`).
    """

    _UNSAFE_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
    _MULTI_UNDERSCORE = re.compile(r"_+")

    @classmethod
    def _clean_token(cls, raw: str, allow_ampersand: bool = True) -> str:
        txt = cls._UNSAFE_FS_CHARS.sub("_", (raw or "").strip().lower())
        txt = re.sub(r"\s+", "_", txt)
        if not allow_ampersand:
            txt = txt.replace("&", "and")
        allowed = r"[^a-z0-9_&\-]" if allow_ampersand else r"[^a-z0-9_\-]"
        txt = re.sub(allowed, "_", txt)
        txt = cls._MULTI_UNDERSCORE.sub("_", txt).strip("._- ")
        return txt or "misc"

    @classmethod
    def format_genre_slug(
        cls,
        domain: DomainType,
        raw_genre: str,
        safe_ascii: bool = False,
    ) -> str:
        raw_first = (raw_genre or "").split("•")[0].split(",")[0].split("/")[0].strip()
        if raw_genre and "run" in raw_genre.lower() and "gun" in raw_genre.lower():
            canon = "runandgun"
        else:
            canon = resolve_canonical_genre(domain, raw_genre) or resolve_canonical_genre(domain, raw_first)
        mapped = _GENRE_FOLDER_DISPLAY_MAP.get(canon, canon or raw_first or "misc")
        if safe_ascii and mapped == "run&gun":
            mapped = "runandgun"
        return cls._clean_token(mapped, allow_ampersand=not safe_ascii)

    @classmethod
    def format_system_slug(cls, raw_system: str) -> str:
        canon = resolve_canonical_system(raw_system) if raw_system else ""
        return cls._clean_token(canon or raw_system or "arcade", allow_ampersand=False)

    @classmethod
    def resolve_clock_folder_name(
        cls,
        domain: DomainType,
        raw_system: str = "",
        raw_genre: str = "",
        template: str = "system_genre",
        safe_ascii: bool = False,
        is_tate: bool = False,
        wrap_orientation_root: bool = False,
    ) -> str:
        """
        Return a flat playlist folder name (zero nested subfolders inside the playlist leaf),
        e.g. `snes_platformer`, `megadrive_fighting`, `neogeo_run&gun`, or `movies_scifi`.
        """
        prefix = cls.format_system_slug(raw_system) if domain == "games" else cls._clean_token(domain, False)
        g_slug = cls.format_genre_slug(domain, raw_genre, safe_ascii=safe_ascii)

        if template == "system":
            leaf = prefix
        elif template == "genre":
            leaf = g_slug
        else:
            leaf = f"{prefix}_{g_slug}"

        if wrap_orientation_root:
            orient_root = "gifs_tate" if is_tate else "gifs"
            return f"{orient_root}/{leaf}"
        return leaf

    @classmethod
    def sanitize_filename_stem(cls, raw_name: str, max_len: int = 96) -> str:
        """Sanitize a ROM or title stem for cross-platform FAT32/exFAT/NTFS/POSIX safety."""
        cleaned = cls._UNSAFE_FS_CHARS.sub("_", (raw_name or "").strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        if not cleaned:
            cleaned = "untitled"
        return cleaned[:max_len].strip(" .") or "untitled"

    @classmethod
    def resolve_showcase_system_folder(cls, raw_system: str) -> str:
        canon = resolve_canonical_system(raw_system) if raw_system else ""
        key = (canon or raw_system or "mame").strip().lower()
        return _ARCADEMATRIX_SHOWCASE_FOLDER_MAP.get(key, cls._clean_token(key, allow_ampersand=False))

    @classmethod
    def resolve_offline_showcase_paths(
        cls,
        raw_system: str,
        rom_stem: str,
        is_tate: bool = False,
    ) -> Tuple[str, str, str]:
        """
        Return `(showcase_rel_dir, gif_filename, pixelcade_rel_dir)` matching `ArcadeMatrix`
        and `ArcadeMatrix_RPI` local offline resolution:
        - YOKO: `("showcase/<sys>", "<rom_stem>.gif", "pixelcade/<sys>")`
        - TATE: `("showcase_tate/<sys>", "<rom_stem>.gif", "pixelcade/<sys>")`
        """
        sys_folder = cls.resolve_showcase_system_folder(raw_system)
        clean_stem = cls.sanitize_filename_stem(rom_stem, max_len=92)
        root_name = "showcase_tate" if is_tate else "showcase"
        return f"{root_name}/{sys_folder}", f"{clean_stem}.gif", f"pixelcade/{sys_folder}"

    @classmethod
    def plan_initial_directories(
        cls,
        config: BatchScrapeConfig,
        gamelist_systems: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Compute initial directory paths to pre-create in the user's selected folder."""
        is_tate = config.encoding.normalized().is_tate
        dirs: Set[str] = set()
        if config.mode == "recalbox_gamelist":
            systems_src = list(gamelist_systems or config.systems)
            for sys_id in systems_src:
                sc_dir, _, px_dir = cls.resolve_offline_showcase_paths(sys_id, "probe", is_tate=is_tate)
                dirs.add(sc_dir)
                if config.include_marquee_png_companion:
                    dirs.add(px_dir)
            return sorted(dirs)

        if config.wrap_orientation_root:
            dirs.add("gifs_tate" if is_tate else "gifs")

        if config.domain == "games":
            sys_list = [s for s in config.systems if s and s != "all"]
            gen_list = [g for g in config.genres if g and g != "all"]
            if config.folder_template == "system":
                for s in sys_list:
                    dirs.add(
                        cls.resolve_clock_folder_name(
                            "games", s, "", "system", config.safe_ascii_folders, is_tate, config.wrap_orientation_root
                        )
                    )
            elif sys_list and gen_list:
                for s in sys_list:
                    for g in gen_list:
                        dirs.add(
                            cls.resolve_clock_folder_name(
                                "games",
                                s,
                                g,
                                config.folder_template,
                                config.safe_ascii_folders,
                                is_tate,
                                config.wrap_orientation_root,
                            )
                        )
        else:
            for g in (g for g in config.genres if g and g != "all"):
                dirs.add(
                    cls.resolve_clock_folder_name(
                        config.domain,
                        "",
                        g,
                        config.folder_template,
                        config.safe_ascii_folders,
                        is_tate,
                        config.wrap_orientation_root,
                    )
                )
        return sorted(dirs)
