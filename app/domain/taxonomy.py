"""
Domain Taxonomy & Multi-Scraper Referential Consolidator (`app/domain/taxonomy.py`).

Responsibilities:
1. Provide a source-agnostic Canonical Taxonomy and Synonym Normalization engine across all
   4 domains (`games`, `movies`, `series`, `anime`) in 5 languages (`fr`, `en`, `es`, `de`, `it`).
2. Translate canonical or dynamically discovered category IDs to each scraper's native
   identifier (`ScreenScraper`, `ArcadeItalia`, `Libretro+Wikidata`, `TMDB`, `iTunes`,
   `TVMaze`, `Jikan`, `AniList`).
3. Consolidate live-harvested scraper referentials (`./data/referentials/{source_id}.json`)
   into a unified, relational catalog (`System -> Supported Genres & Decades`) exposed via
   `GET /api/v1/{domain}/filters?lang={lang}` and MQTT `dmd_showcase/catalog/{domain}/referential`
   so the DMD WebUI never hardcodes scraper categories.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria


def normalize_category_slug(raw: str) -> str:
    """Normalize an arbitrary scraper category or system name into a stable lowercase slug."""
    if not raw:
        return ""
    ascii_str = (
        unicodedata.normalize("NFKD", str(raw))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_str).strip("_")
    return slug


def localize_label(labels: Dict[str, str], lang: str = "fr", fallback: str = "") -> str:
    """Return the localized label for `lang` (`fr`, `en`, `es`, `de`, `it`)."""
    short = (lang or "fr").split("-")[0].split("_")[0].lower()
    if short in labels and labels[short]:
        return labels[short]
    if "en" in labels and labels["en"]:
        return labels["en"]
    if "fr" in labels and labels["fr"]:
        return labels["fr"]
    return next(iter(labels.values()), fallback)


# =============================================================================
# 1. Canonical Decades (All Domains)
# =============================================================================

CANONICAL_DECADES: Dict[str, Dict[str, Any]] = {
    "1970s": {
        "bounds": (1970, 1979),
        "labels": {
            "fr": "Années 1970",
            "en": "1970s",
            "es": "Años 1970",
            "de": "1970er",
            "it": "Anni 1970",
        },
    },
    "1980s": {
        "bounds": (1980, 1989),
        "labels": {
            "fr": "Années 1980",
            "en": "1980s",
            "es": "Años 1980",
            "de": "1980er",
            "it": "Anni 1980",
        },
    },
    "1990s": {
        "bounds": (1990, 1999),
        "labels": {
            "fr": "Années 1990",
            "en": "1990s",
            "es": "Años 1990",
            "de": "1990er",
            "it": "Anni 1990",
        },
    },
    "2000s": {
        "bounds": (2000, 2009),
        "labels": {
            "fr": "Années 2000",
            "en": "2000s",
            "es": "Años 2000",
            "de": "2000er",
            "it": "Anni 2000",
        },
    },
    "2010s": {
        "bounds": (2010, 2019),
        "labels": {
            "fr": "Années 2010",
            "en": "2010s",
            "es": "Años 2010",
            "de": "2010er",
            "it": "Anni 2010",
        },
    },
    "2020s": {
        "bounds": (2020, 2029),
        "labels": {
            "fr": "Années 2020",
            "en": "2020s",
            "es": "Años 2020",
            "de": "2020er",
            "it": "Anni 2020",
        },
    },
}


# =============================================================================
# 2. Games Domain — Canonical Systems & Multi-Scraper Mapping
# =============================================================================

_ALL_CONSOLE_GENRES = [
    "platform",
    "action",
    "rpg",
    "shootemup",
    "runandgun",
    "fighting",
    "beatemup",
    "racing",
    "puzzle",
    "sports",
    "strategy",
    "simulation",
]

_ALL_ARCADE_GENRES = [
    "fighting",
    "beatemup",
    "shootemup",
    "runandgun",
    "platform",
    "action",
    "racing",
    "puzzle",
    "sports",
    "maze",
]

RETRO_SYSTEM_CATALOG: Dict[str, Dict[str, Any]] = {
    "arcade": {
        "is_arcade": True,
        "screenscraper_id": "75",
        "arcadeitalia_driver": "",
        "wikidata_qid": "Q192851",
        "libretro_repo": "MAME",
        "badge": "ARCADE",
        "color": "#FF6D00",
        "palette": (255, 109, 0),
        "decades": ["1970s", "1980s", "1990s", "2000s"],
        "genres": _ALL_ARCADE_GENRES,
        "synonyms": ["arcade", "coin_op", "borne_arcade"],
        "labels": {
            "fr": "Arcade (Classiques)",
            "en": "Arcade (Classics)",
            "es": "Arcade (Clásicos)",
            "de": "Arcade (Klassiker)",
            "it": "Arcade (Classici)",
        },
    },
    "mame": {
        "is_arcade": True,
        "screenscraper_id": "75",
        "arcadeitalia_driver": "",
        "wikidata_qid": "Q192851",
        "libretro_repo": "MAME",
        "badge": "MAME",
        "color": "#FF9100",
        "palette": (255, 145, 0),
        "decades": ["1970s", "1980s", "1990s", "2000s"],
        "genres": _ALL_ARCADE_GENRES,
        "synonyms": ["mame", "fbneo", "finalburn", "finalburn_neo"],
        "labels": {
            "fr": "MAME / FBNeo",
            "en": "MAME / FBNeo",
            "es": "MAME / FBNeo",
            "de": "MAME / FBNeo",
            "it": "MAME / FBNeo",
        },
    },
    "neogeo": {
        "is_arcade": True,
        "screenscraper_id": "142",
        "arcadeitalia_driver": "neogeo",
        "wikidata_qid": "Q1054350",
        "libretro_repo": "SNK_-_Neo_Geo",
        "archive_video_repo": "neogeosnaps_202302",
        "badge": "NEO-GEO",
        "color": "#E53935",
        "palette": (229, 57, 53),
        "decades": ["1990s", "2000s"],
        "genres": ["fighting", "runandgun", "shootemup", "beatemup", "sports", "puzzle", "platform", "action"],
        "synonyms": ["neogeo", "neo_geo", "snk_neo_geo", "aes", "mvs"],
        "labels": {
            "fr": "SNK Neo-Geo (MVS / AES)",
            "en": "SNK Neo-Geo (MVS / AES)",
            "es": "SNK Neo-Geo (MVS / AES)",
            "de": "SNK Neo-Geo (MVS / AES)",
            "it": "SNK Neo-Geo (MVS / AES)",
        },
    },
    "cps1": {
        "is_arcade": True,
        "screenscraper_id": "105",
        "arcadeitalia_driver": "cps1",
        "wikidata_qid": "Q1034233",
        "libretro_repo": "MAME",
        "archive_video_repo": "CapcomCPS-1VideoSnaps",
        "badge": "CPS-1",
        "color": "#FFB300",
        "palette": (255, 179, 0),
        "decades": ["1980s", "1990s"],
        "genres": ["fighting", "beatemup", "shootemup", "runandgun", "platform", "action", "puzzle"],
        "synonyms": ["cps1", "cps_1", "capcom_play_system", "cps"],
        "labels": {
            "fr": "Capcom CPS-1",
            "en": "Capcom CPS-1",
            "es": "Capcom CPS-1",
            "de": "Capcom CPS-1",
            "it": "Capcom CPS-1",
        },
    },
    "cps2": {
        "is_arcade": True,
        "screenscraper_id": "106",
        "arcadeitalia_driver": "cps2",
        "wikidata_qid": "Q2981666",
        "libretro_repo": "MAME",
        "archive_video_repo": "CapcomCPS-2VideoSnaps_201804",
        "badge": "CPS-2",
        "color": "#FB8C00",
        "palette": (251, 140, 0),
        "decades": ["1990s", "2000s"],
        "genres": ["fighting", "beatemup", "shootemup", "runandgun", "puzzle", "action"],
        "synonyms": ["cps2", "cps_2", "capcom_play_system_2"],
        "labels": {
            "fr": "Capcom CPS-2",
            "en": "Capcom CPS-2",
            "es": "Capcom CPS-2",
            "de": "Capcom CPS-2",
            "it": "Capcom CPS-2",
        },
    },
    "cps3": {
        "is_arcade": True,
        "screenscraper_id": "107",
        "arcadeitalia_driver": "cps3",
        "wikidata_qid": "Q2634041",
        "libretro_repo": "MAME",
        "archive_video_repo": "CPSsnaps",
        "badge": "CPS-3",
        "color": "#F4511E",
        "palette": (244, 81, 30),
        "decades": ["1990s"],
        "genres": ["fighting", "action"],
        "synonyms": ["cps3", "cps_3", "capcom_play_system_3"],
        "labels": {
            "fr": "Capcom CPS-3",
            "en": "Capcom CPS-3",
            "es": "Capcom CPS-3",
            "de": "Capcom CPS-3",
            "it": "Capcom CPS-3",
        },
    },
    "nes": {
        "is_arcade": False,
        "screenscraper_id": "3",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q172742",
        "libretro_repo": "Nintendo_-_Nintendo_Entertainment_System",
        "archive_video_repo": "nintendo-entertainment-system-video-snaps",
        "badge": "NES",
        "color": "#D32F2F",
        "palette": (211, 47, 47),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": [
            "nes",
            "famicom",
            "fc",
            "nintendo_entertainment_system",
            "nintendo_nintendo_entertainment_system",
            "family_computer",
        ],
        "labels": {
            "fr": "Nintendo NES / Famicom",
            "en": "Nintendo NES / Famicom",
            "es": "Nintendo NES / Famicom",
            "de": "Nintendo NES / Famicom",
            "it": "Nintendo NES / Famicom",
        },
    },
    "snes": {
        "is_arcade": False,
        "screenscraper_id": "4",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q183259",
        "libretro_repo": "Nintendo_-_Super_Nintendo_Entertainment_System",
        "archive_video_repo": "super-nintendo-entertainment-system-video-snaps",
        "badge": "SNES",
        "color": "#7B1FA2",
        "palette": (123, 31, 162),
        "decades": ["1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": [
            "snes",
            "sfc",
            "super_nintendo",
            "super_famicom",
            "super_nintendo_entertainment_system",
            "nintendo_super_nintendo_entertainment_system",
        ],
        "labels": {
            "fr": "Super Nintendo (SNES / Super Famicom)",
            "en": "Super Nintendo (SNES / Super Famicom)",
            "es": "Super Nintendo (SNES / Super Famicom)",
            "de": "Super Nintendo (SNES / Super Famicom)",
            "it": "Super Nintendo (SNES / Super Famicom)",
        },
    },
    "megadrive": {
        "is_arcade": False,
        "screenscraper_id": "1",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q10677",
        "libretro_repo": "Sega_-_Mega_Drive_-_Genesis",
        "archive_video_repo": "sega-genesis-video-snaps",
        "badge": "MD",
        "color": "#1976D2",
        "palette": (25, 118, 210),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": [
            "megadrive",
            "mega_drive",
            "genesis",
            "sega_genesis",
            "sega_mega_drive",
            "sega_mega_drive_genesis",
        ],
        "labels": {
            "fr": "Sega Mega Drive / Genesis",
            "en": "Sega Mega Drive / Genesis",
            "es": "Sega Mega Drive / Genesis",
            "de": "Sega Mega Drive / Genesis",
            "it": "Sega Mega Drive / Genesis",
        },
    },
    "mastersystem": {
        "is_arcade": False,
        "screenscraper_id": "2",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q209868",
        "libretro_repo": "Sega_-_Master_System_-_Mark_III",
        "archive_video_repo": "SegaMasterSystemVideoSnaps",
        "badge": "SMS",
        "color": "#1565C0",
        "palette": (21, 101, 192),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["mastersystem", "master_system", "sms", "sega_master_system", "mark_iii"],
        "labels": {
            "fr": "Sega Master System",
            "en": "Sega Master System",
            "es": "Sega Master System",
            "de": "Sega Master System",
            "it": "Sega Master System",
        },
    },
    "pcengine": {
        "is_arcade": False,
        "screenscraper_id": "31",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q1057377",
        "libretro_repo": "NEC_-_PC_Engine_-_TurboGrafx_16",
        "archive_video_repo": "NECTurboGrafx16VideoSnaps",
        "badge": "PCE",
        "color": "#E64A19",
        "palette": (230, 74, 25),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["pcengine", "pc_engine", "turbografx", "turbografx_16", "tg16", "pce"],
        "labels": {
            "fr": "PC Engine / TurboGrafx-16",
            "en": "PC Engine / TurboGrafx-16",
            "es": "PC Engine / TurboGrafx-16",
            "de": "PC Engine / TurboGrafx-16",
            "it": "PC Engine / TurboGrafx-16",
        },
    },
    "psx": {
        "is_arcade": False,
        "screenscraper_id": "57",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q10680",
        "libretro_repo": "Sony_-_PlayStation",
        "archive_video_repo": "SonyPlaystationVideoSnaps",
        "badge": "PS1",
        "color": "#0288D1",
        "palette": (2, 136, 209),
        "decades": ["1990s", "2000s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["psx", "ps1", "playstation", "sony_playstation", "psone"],
        "labels": {
            "fr": "Sony PlayStation (PS1)",
            "en": "Sony PlayStation (PS1)",
            "es": "Sony PlayStation (PS1)",
            "de": "Sony PlayStation (PS1)",
            "it": "Sony PlayStation (PS1)",
        },
    },
    "n64": {
        "is_arcade": False,
        "screenscraper_id": "14",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q184839",
        "libretro_repo": "Nintendo_-_Nintendo_64",
        "archive_video_repo": "Nintendo64VideoSnaps",
        "badge": "N64",
        "color": "#388E3C",
        "palette": (56, 142, 60),
        "decades": ["1990s", "2000s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["n64", "nintendo_64", "nintendo_nintendo_64"],
        "labels": {
            "fr": "Nintendo 64",
            "en": "Nintendo 64",
            "es": "Nintendo 64",
            "de": "Nintendo 64",
            "it": "Nintendo 64",
        },
    },
    "saturn": {
        "is_arcade": False,
        "screenscraper_id": "22",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q200912",
        "libretro_repo": "Sega_-_Saturn",
        "archive_video_repo": "sega-saturn_202111",
        "badge": "SATURN",
        "color": "#303F9F",
        "palette": (48, 63, 159),
        "decades": ["1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["saturn", "sega_saturn"],
        "labels": {
            "fr": "Sega Saturn",
            "en": "Sega Saturn",
            "es": "Sega Saturn",
            "de": "Sega Saturn",
            "it": "Sega Saturn",
        },
    },
    "dreamcast": {
        "is_arcade": False,
        "screenscraper_id": "23",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q184198",
        "libretro_repo": "Sega_-_Dreamcast",
        "archive_video_repo": "sega-dreamcast-video-snaps",
        "badge": "DC",
        "color": "#F57C00",
        "palette": (245, 124, 0),
        "decades": ["1990s", "2000s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["dreamcast", "dc", "sega_dreamcast"],
        "labels": {
            "fr": "Sega Dreamcast",
            "en": "Sega Dreamcast",
            "es": "Sega Dreamcast",
            "de": "Sega Dreamcast",
            "it": "Sega Dreamcast",
        },
    },
    "gb": {
        "is_arcade": False,
        "screenscraper_id": "9",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q186437",
        "libretro_repo": "Nintendo_-_Game_Boy",
        "archive_video_repo": "nintendo-game-boy-video-snaps",
        "badge": "GB",
        "color": "#689F38",
        "palette": (104, 159, 56),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["gb", "gameboy", "game_boy", "nintendo_game_boy"],
        "labels": {
            "fr": "Nintendo Game Boy",
            "en": "Nintendo Game Boy",
            "es": "Nintendo Game Boy",
            "de": "Nintendo Game Boy",
            "it": "Nintendo Game Boy",
        },
    },
    "gbc": {
        "is_arcade": False,
        "screenscraper_id": "10",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q203992",
        "libretro_repo": "Nintendo_-_Game_Boy_Color",
        "archive_video_repo": "tonka-raceway-usa",
        "badge": "GBC",
        "color": "#00ACC1",
        "palette": (0, 172, 193),
        "decades": ["1990s", "2000s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["gbc", "game_boy_color", "nintendo_game_boy_color"],
        "labels": {
            "fr": "Nintendo Game Boy Color",
            "en": "Nintendo Game Boy Color",
            "es": "Nintendo Game Boy Color",
            "de": "Nintendo Game Boy Color",
            "it": "Nintendo Game Boy Color",
        },
    },
    "gba": {
        "is_arcade": False,
        "screenscraper_id": "12",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q188642",
        "libretro_repo": "Nintendo_-_Game_Boy_Advance",
        "archive_video_repo": "nintendo-game-boy-advance-video-sanps-sq",
        "badge": "GBA",
        "color": "#512DA8",
        "palette": (81, 45, 168),
        "decades": ["2000s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["gba", "game_boy_advance", "nintendo_game_boy_advance"],
        "labels": {
            "fr": "Nintendo Game Boy Advance",
            "en": "Nintendo Game Boy Advance",
            "es": "Nintendo Game Boy Advance",
            "de": "Nintendo Game Boy Advance",
            "it": "Nintendo Game Boy Advance",
        },
    },
    "fbneo": {
        "is_arcade": True,
        "screenscraper_id": "75",
        "arcadeitalia_driver": "",
        "wikidata_qid": "Q192851",
        "libretro_repo": "FBNeo_-_Arcade_Games",
        "badge": "FBNEO",
        "color": "#F4511E",
        "palette": (244, 81, 30),
        "decades": ["1980s", "1990s", "2000s"],
        "genres": _ALL_ARCADE_GENRES,
        "synonyms": ["fbneo", "finalburn_neo", "fba"],
        "labels": {
            "fr": "FinalBurn Neo (Arcade)",
            "en": "FinalBurn Neo (Arcade)",
            "es": "FinalBurn Neo (Arcade)",
            "de": "FinalBurn Neo (Arcade)",
            "it": "FinalBurn Neo (Arcade)",
        },
    },
    "gamegear": {
        "is_arcade": False,
        "screenscraper_id": "21",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q751719",
        "libretro_repo": "Sega_-_Game_Gear",
        "archive_video_repo": "SegaGameGearVideoSnaps",
        "badge": "GG",
        "color": "#039BE5",
        "palette": (3, 155, 229),
        "decades": ["1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["gamegear", "game_gear", "sega_game_gear", "gg"],
        "labels": {
            "fr": "Sega Game Gear",
            "en": "Sega Game Gear",
            "es": "Sega Game Gear",
            "de": "Sega Game Gear",
            "it": "Sega Game Gear",
        },
    },
    "lynx": {
        "is_arcade": False,
        "screenscraper_id": "28",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q753657",
        "libretro_repo": "Atari_-_Lynx",
        "archive_video_repo": "atarilynx-lossless-video-snaps",
        "badge": "LYNX",
        "color": "#FBC02D",
        "palette": (251, 192, 45),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["lynx", "atari_lynx"],
        "labels": {
            "fr": "Atari Lynx",
            "en": "Atari Lynx",
            "es": "Atari Lynx",
            "de": "Atari Lynx",
            "it": "Atari Lynx",
        },
    },
    "ngp": {
        "is_arcade": False,
        "screenscraper_id": "82",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q1977455",
        "libretro_repo": "SNK_-_Neo_Geo_Pocket_Color",
        "archive_video_repo": "SNKNeoGeoPocketColorVideoSnaps",
        "badge": "NGPC",
        "color": "#E53935",
        "palette": (229, 57, 53),
        "decades": ["1990s", "2000s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["ngp", "ngpc", "neo_geo_pocket", "neo_geo_pocket_color"],
        "labels": {
            "fr": "Neo Geo Pocket Color",
            "en": "Neo Geo Pocket Color",
            "es": "Neo Geo Pocket Color",
            "de": "Neo Geo Pocket Color",
            "it": "Neo Geo Pocket Color",
        },
    },
    "atari2600": {
        "is_arcade": False,
        "screenscraper_id": "26",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q206261",
        "libretro_repo": "Atari_-_2600",
        "archive_video_repo": "Atari2600VideoSnaps",
        "badge": "A2600",
        "color": "#D84315",
        "palette": (216, 67, 21),
        "decades": ["1970s", "1980s"],
        "genres": ["action", "shootemup", "platform", "racing", "sports", "maze", "puzzle"],
        "synonyms": ["atari2600", "atari_2600", "vcs"],
        "labels": {
            "fr": "Atari 2600",
            "en": "Atari 2600",
            "es": "Atari 2600",
            "de": "Atari 2600",
            "it": "Atari 2600",
        },
    },
    "atari7800": {
        "is_arcade": False,
        "screenscraper_id": "43",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q753600",
        "libretro_repo": "Atari_-_7800",
        "archive_video_repo": "Atari7800VideoSnaps",
        "badge": "A7800",
        "color": "#C62828",
        "palette": (198, 40, 40),
        "decades": ["1980s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["atari7800", "atari_7800"],
        "labels": {
            "fr": "Atari 7800",
            "en": "Atari 7800",
            "es": "Atari 7800",
            "de": "Atari 7800",
            "it": "Atari 7800",
        },
    },
    "c64": {
        "is_arcade": False,
        "screenscraper_id": "66",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q99775",
        "libretro_repo": "Commodore_-_64",
        "badge": "C64",
        "color": "#6D4C41",
        "palette": (109, 76, 65),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["c64", "commodore_64", "commodore64"],
        "labels": {
            "fr": "Commodore 64",
            "en": "Commodore 64",
            "es": "Commodore 64",
            "de": "Commodore 64",
            "it": "Commodore 64",
        },
    },
    "amiga": {
        "is_arcade": False,
        "screenscraper_id": "64",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q100047",
        "libretro_repo": "Commodore_-_Amiga",
        "badge": "AMIGA",
        "color": "#AD1457",
        "palette": (173, 20, 87),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["amiga", "commodore_amiga", "amiga_500"],
        "labels": {
            "fr": "Commodore Amiga",
            "en": "Commodore Amiga",
            "es": "Commodore Amiga",
            "de": "Commodore Amiga",
            "it": "Commodore Amiga",
        },
    },
    "msx": {
        "is_arcade": False,
        "screenscraper_id": "113",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q853547",
        "libretro_repo": "Microsoft_-_MSX",
        "archive_video_repo": "MicrosoftMSX2CartsVideoSnaps_201804",
        "badge": "MSX",
        "color": "#283593",
        "palette": (40, 53, 147),
        "decades": ["1980s", "1990s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["msx", "msx2", "microsoft_msx"],
        "labels": {
            "fr": "MSX / MSX2",
            "en": "MSX / MSX2",
            "es": "MSX / MSX2",
            "de": "MSX / MSX2",
            "it": "MSX / MSX2",
        },
    },
    "colecovision": {
        "is_arcade": False,
        "screenscraper_id": "48",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q1046862",
        "libretro_repo": "Coleco_-_ColecoVision",
        "archive_video_repo": "coleco-lossless-video-snaps",
        "badge": "COLECO",
        "color": "#00838F",
        "palette": (0, 131, 143),
        "decades": ["1980s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["colecovision", "coleco"],
        "labels": {
            "fr": "ColecoVision",
            "en": "ColecoVision",
            "es": "ColecoVision",
            "de": "ColecoVision",
            "it": "ColecoVision",
        },
    },
    "intellivision": {
        "is_arcade": False,
        "screenscraper_id": "115",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q1061441",
        "libretro_repo": "Mattel_-_Intellivision",
        "badge": "INTV",
        "color": "#9E9D24",
        "palette": (158, 157, 36),
        "decades": ["1970s", "1980s"],
        "genres": _ALL_CONSOLE_GENRES,
        "synonyms": ["intellivision", "mattel_intellivision"],
        "labels": {
            "fr": "Mattel Intellivision",
            "en": "Mattel Intellivision",
            "es": "Mattel Intellivision",
            "de": "Mattel Intellivision",
            "it": "Mattel Intellivision",
        },
    },
    "vectrex": {
        "is_arcade": False,
        "screenscraper_id": "102",
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q767631",
        "libretro_repo": "GCE_-_Vectrex",
        "archive_video_repo": "GCEVectrexVideoSnaps",
        "badge": "VECTREX",
        "color": "#00E676",
        "palette": (0, 230, 118),
        "decades": ["1980s"],
        "genres": ["action", "shootemup", "maze", "sports", "platform"],
        "synonyms": ["vectrex", "gce_vectrex"],
        "labels": {
            "fr": "GCE Vectrex",
            "en": "GCE Vectrex",
            "es": "GCE Vectrex",
            "de": "GCE Vectrex",
            "it": "GCE Vectrex",
        },
    },
}


# =============================================================================
# 3. Domain Genre Catalogs & Multi-Scraper Synonym Mapping
# =============================================================================

GAME_GENRE_CATALOG: Dict[str, Dict[str, Any]] = {
    "platform": {
        "wikidata_qid": "Q828322",
        "wikidata_qids": ["Q828322"],
        "adb_keywords": ["platform / run jump", "platform / run jump scrolling", "platform"],
        "screenscraper_keywords": ["plateforme", "platform"],
        "synonyms": [
            "platform",
            "platformer",
            "plateforme",
            "platform_game",
            "platform_run_jump",
            "metroidvania",
        ],
        "labels": {
            "fr": "Plateforme",
            "en": "Platformer",
            "es": "Plataformas",
            "de": "Jump ’n’ Run",
            "it": "Platform",
        },
    },
    "action": {
        "wikidata_qid": "Q270948",
        "wikidata_qids": ["Q270948", "Q343568"],
        "adb_keywords": ["action", "climbing", "platform / fighter scrolling"],
        "screenscraper_keywords": ["action"],
        "synonyms": ["action", "action_adventure", "action_game"],
        "labels": {
            "fr": "Action / Aventure",
            "en": "Action / Adventure",
            "es": "Acción / Aventura",
            "de": "Action / Abenteuer",
            "it": "Azione / Avventura",
        },
    },
    "fighting": {
        "wikidata_qid": "Q846224",
        "wikidata_qids": ["Q846224"],
        "adb_keywords": [
            "fighter / versus",
            "fighter / versus co-op",
            "fighter / 2d",
            "fighter / 3d",
            "sports / wrestling",
            "wrestling",
        ],
        "screenscraper_keywords": ["combat", "fighting"],
        "synonyms": ["fighting", "fighter_versus", "combat", "versus", "vs_fighting", "fighting_game"],
        "labels": {
            "fr": "Combat (1v1 / Versus)",
            "en": "Fighting (1v1)",
            "es": "Lucha (1v1)",
            "de": "Fighting (1v1)",
            "it": "Picchiaduro (1v1)",
        },
    },
    "beatemup": {
        "wikidata_qid": "Q401831",
        "wikidata_qids": ["Q401831"],
        "adb_keywords": [
            "fighter / 2.5d",
            "fighter / field",
            "fighter / misc",
            "fighter / vertical",
            "beat-'em-up",
        ],
        "screenscraper_keywords": ["beat'em all", "beat em up"],
        "synonyms": ["beatemup", "beat_em_up", "beat_em_all", "brawler", "hack_and_slash"],
        "labels": {
            "fr": "Beat 'em up / Brawler",
            "en": "Beat 'em up / Brawler",
            "es": "Beat 'em up / Yo contra el barrio",
            "de": "Beat ’em up",
            "it": "Picchiaduro a scorrimento",
        },
    },
    "shootemup": {
        "wikidata_qid": "Q1044478",
        "wikidata_qids": ["Q1044478"],
        "adb_keywords": [
            "shoot-'em-up",
            "shooter / flying",
            "shooter / flying vertical",
            "shooter / flying horizontal",
            "shooter / flying 1st person",
            "shooter / gallery",
            "shooter / field",
        ],
        "screenscraper_keywords": ["shoot'em up", "shoot em up", "shmup"],
        "synonyms": ["shootemup", "shoot_em_up", "shmup", "scrolling_shooter", "bullet_hell"],
        "labels": {
            "fr": "Shoot 'em up (Shmup)",
            "en": "Shoot 'em up (Shmup)",
            "es": "Shoot 'em up (Matamarcianos)",
            "de": "Shoot ’em up",
            "it": "Sparatutto (Shmup)",
        },
    },
    "runandgun": {
        "wikidata_qid": "Q60480500",
        "wikidata_qids": ["Q60480500", "Q1044478"],
        "adb_keywords": [
            "shooter / walking",
            "run and gun",
            "platform / shooter",
            "platform / shooter scrolling",
        ],
        "screenscraper_keywords": ["run & gun", "run and gun", "tir"],
        "synonyms": ["runandgun", "run_and_gun", "run_gun", "shooter_walking", "platform_shooter"],
        "labels": {
            "fr": "Run & Gun",
            "en": "Run & Gun",
            "es": "Run & Gun",
            "de": "Run & Gun",
            "it": "Run & Gun",
        },
    },
    "rpg": {
        "wikidata_qid": "Q744038",
        "wikidata_qids": ["Q744038", "Q1422746", "Q1529437"],
        "adb_keywords": ["role-playing"],
        "screenscraper_keywords": ["jeu de rôles", "rpg", "role playing"],
        "synonyms": ["rpg", "jrpg", "action_rpg", "role_playing", "role_playing_video_game", "jeu_de_roles"],
        "labels": {
            "fr": "Jeu de rôle (RPG / JRPG)",
            "en": "Role-Playing (RPG / JRPG)",
            "es": "Juego de rol (RPG)",
            "de": "Rollenspiel (RPG)",
            "it": "Gioco di ruolo (RPG)",
        },
    },
    "racing": {
        "wikidata_qid": "Q860750",
        "wikidata_qids": ["Q860750"],
        "adb_keywords": ["driving", "race", "motorcycle"],
        "screenscraper_keywords": ["course", "conduite", "racing", "driving"],
        "synonyms": ["racing", "driving", "course", "racing_video_game", "kart_racing"],
        "labels": {
            "fr": "Course / Conduite",
            "en": "Racing / Driving",
            "es": "Carreras / Conducción",
            "de": "Rennspiel",
            "it": "Corse / Guida",
        },
    },
    "puzzle": {
        "wikidata_qid": "Q54767",
        "wikidata_qids": ["Q54767"],
        "adb_keywords": ["puzzle", "puzzle / drop", "puzzle / match", "puzzle / toss"],
        "screenscraper_keywords": ["puzzle", "réflexion"],
        "synonyms": ["puzzle", "reflexion", "puzzle_video_game", "falling_block"],
        "labels": {
            "fr": "Puzzle / Réflexion",
            "en": "Puzzle",
            "es": "Puzle / Lógica",
            "de": "Puzzle / Geschicklichkeit",
            "it": "Rompicapo / Puzzle",
        },
    },
    "sports": {
        "wikidata_qid": "Q868217",
        "wikidata_qids": ["Q868217"],
        "adb_keywords": [
            "sports",
            "sports / soccer",
            "sports / basketball",
            "sports / baseball",
            "sports / tennis",
            "sports / golf",
            "sports / dodgeball",
        ],
        "screenscraper_keywords": ["sport", "sports"],
        "synonyms": ["sports", "sport", "sports_video_game", "soccer", "football", "basketball", "tennis", "golf"],
        "labels": {
            "fr": "Sport",
            "en": "Sports",
            "es": "Deportes",
            "de": "Sport",
            "it": "Sport",
        },
    },
    "strategy": {
        "wikidata_qid": "Q472055",
        "wikidata_qids": ["Q472055", "Q1529437"],
        "adb_keywords": ["strategy", "tabletop"],
        "screenscraper_keywords": ["stratégie", "strategy", "tactic"],
        "synonyms": ["strategy", "tactical", "rts", "turn_based_strategy", "strategie"],
        "labels": {
            "fr": "Stratégie / Tactique",
            "en": "Strategy / Tactics",
            "es": "Estrategia / Táctica",
            "de": "Strategie / Taktik",
            "it": "Strategia / Tattica",
        },
    },
    "simulation": {
        "wikidata_qid": "Q1610017",
        "wikidata_qids": ["Q1610017"],
        "adb_keywords": ["simulation"],
        "screenscraper_keywords": ["simulation", "vol"],
        "synonyms": ["simulation", "flight_simulator", "space_combat"],
        "labels": {
            "fr": "Simulation",
            "en": "Simulation",
            "es": "Simulación",
            "de": "Simulation",
            "it": "Simulazione",
        },
    },
    "maze": {
        "wikidata_qid": "Q13717398",
        "wikidata_qids": ["Q13717398"],
        "adb_keywords": ["maze"],
        "screenscraper_keywords": ["labyrinthe", "maze"],
        "synonyms": ["maze", "labyrinthe", "pac_man_clone"],
        "labels": {
            "fr": "Arcade Labyrinthe (Maze)",
            "en": "Maze Arcade",
            "es": "Laberinto (Maze)",
            "de": "Labyrinth (Maze)",
            "it": "Labirinto (Maze)",
        },
    },
    "adventure": {
        "wikidata_qid": "Q23916",
        "wikidata_qids": ["Q23916", "Q343568"],
        "adb_keywords": ["adventure"],
        "screenscraper_keywords": ["aventure", "adventure"],
        "synonyms": ["adventure", "aventure", "point_and_click"],
        "labels": {
            "fr": "Aventure",
            "en": "Adventure",
            "es": "Aventura",
            "de": "Abenteuer",
            "it": "Avventura",
        },
    },
    "pinball": {
        "wikidata_qid": "Q3177954",
        "wikidata_qids": ["Q3177954", "Q653928"],
        "adb_keywords": ["pinball"],
        "screenscraper_keywords": ["flipper", "pinball"],
        "synonyms": ["pinball", "flipper"],
        "labels": {
            "fr": "Flipper / Pinball",
            "en": "Pinball",
            "es": "Pinball",
            "de": "Flipper / Pinball",
            "it": "Flipper / Pinball",
        },
    },
    "lightgun": {
        "wikidata_qid": "Q3965952",
        "wikidata_qids": ["Q3965952"],
        "adb_keywords": ["shooter / gun", "lightgun"],
        "screenscraper_keywords": ["tir au pistolet", "lightgun", "gun"],
        "synonyms": ["lightgun", "light_gun", "rail_shooter", "gun_shooter", "shooter_gun"],
        "labels": {
            "fr": "Tir au pistolet (Lightgun)",
            "en": "Light Gun Shooter",
            "es": "Disparos con pistola (Lightgun)",
            "de": "Lightgun-Shooter",
            "it": "Sparatutto con pistola",
        },
    },
}

MOVIE_GENRE_CATALOG: Dict[str, Dict[str, Any]] = {
    "scifi": {
        "tmdb_id": "878",
        "itunes_term": "sci-fi",
        "badge": "SCI-FI",
        "color": "#00E5FF",
        "palette": (0, 229, 255),
        "synonyms": ["scifi", "sci_fi", "science_fiction"],
        "labels": {
            "fr": "Science-Fiction",
            "en": "Sci-Fi",
            "es": "Ciencia Ficción",
            "de": "Science-Fiction",
            "it": "Fantascienza",
        },
    },
    "action": {
        "tmdb_id": "28",
        "itunes_term": "action",
        "badge": "ACTION",
        "color": "#FF3D00",
        "palette": (255, 61, 0),
        "synonyms": ["action"],
        "labels": {
            "fr": "Action",
            "en": "Action",
            "es": "Acción",
            "de": "Action",
            "it": "Azione",
        },
    },
    "adventure": {
        "tmdb_id": "12",
        "itunes_term": "adventure",
        "badge": "ADVENTURE",
        "color": "#FF9100",
        "palette": (255, 145, 0),
        "synonyms": ["adventure", "aventure"],
        "labels": {
            "fr": "Aventure",
            "en": "Adventure",
            "es": "Aventura",
            "de": "Abenteuer",
            "it": "Avventura",
        },
    },
    "animation": {
        "tmdb_id": "16",
        "itunes_term": "animation",
        "badge": "ANIMATION",
        "color": "#FFEA00",
        "palette": (255, 234, 0),
        "synonyms": ["animation", "animated"],
        "labels": {
            "fr": "Animation",
            "en": "Animation",
            "es": "Animación",
            "de": "Animation",
            "it": "Animazione",
        },
    },
    "thriller": {
        "tmdb_id": "53",
        "itunes_term": "thriller",
        "badge": "THRILLER",
        "color": "#D500F9",
        "palette": (213, 0, 249),
        "synonyms": ["thriller", "suspense"],
        "labels": {
            "fr": "Thriller / Suspense",
            "en": "Thriller",
            "es": "Suspense / Thriller",
            "de": "Thriller",
            "it": "Thriller",
        },
    },
    "crime": {
        "tmdb_id": "80",
        "itunes_term": "crime",
        "badge": "CRIME",
        "color": "#FF1744",
        "palette": (255, 23, 68),
        "synonyms": ["crime", "policier", "gangster"],
        "labels": {
            "fr": "Policier / Crime",
            "en": "Crime",
            "es": "Crimen / Policiaco",
            "de": "Krimi",
            "it": "Crime / Poliziesco",
        },
    },
    "fantasy": {
        "tmdb_id": "14",
        "itunes_term": "fantasy",
        "badge": "FANTASY",
        "color": "#7C4DFF",
        "palette": (124, 77, 255),
        "synonyms": ["fantasy", "fantastique"],
        "labels": {
            "fr": "Fantastique / Fantasy",
            "en": "Fantasy",
            "es": "Fantasía",
            "de": "Fantasy",
            "it": "Fantasy",
        },
    },
    "horror": {
        "tmdb_id": "27",
        "itunes_term": "horror",
        "badge": "HORROR",
        "color": "#D50000",
        "palette": (213, 0, 0),
        "synonyms": ["horror", "horreur", "epouvante"],
        "labels": {
            "fr": "Horreur / Épouvante",
            "en": "Horror",
            "es": "Terror",
            "de": "Horror",
            "it": "Horror",
        },
    },
    "comedy": {
        "tmdb_id": "35",
        "itunes_term": "comedy",
        "badge": "COMEDY",
        "color": "#00E676",
        "palette": (0, 230, 118),
        "synonyms": ["comedy", "comedie"],
        "labels": {
            "fr": "Comédie",
            "en": "Comedy",
            "es": "Comedia",
            "de": "Komödie",
            "it": "Commedia",
        },
    },
    "drama": {
        "tmdb_id": "18",
        "itunes_term": "drama",
        "badge": "DRAMA",
        "color": "#2979FF",
        "palette": (41, 121, 255),
        "synonyms": ["drama", "drame"],
        "labels": {
            "fr": "Drame",
            "en": "Drama",
            "es": "Drama",
            "de": "Drama",
            "it": "Drammatico",
        },
    },
}

SERIES_GENRE_CATALOG: Dict[str, Dict[str, Any]] = {
    "scifi": {
        "tmdb_id": "10765",
        "tvmaze": "Science-Fiction",
        "badge": "SCI-FI",
        "color": "#00B0FF",
        "palette": (0, 176, 255),
        "synonyms": ["scifi", "sci_fi", "science_fiction", "sci_fi_fantasy"],
        "labels": {
            "fr": "Sci-Fi & Fantastique",
            "en": "Sci-Fi & Fantasy",
            "es": "Ciencia Ficción y Fantasía",
            "de": "Sci-Fi & Fantasy",
            "it": "Sci-Fi & Fantasy",
        },
    },
    "drama": {
        "tmdb_id": "18",
        "tvmaze": "Drama",
        "badge": "DRAMA",
        "color": "#FF1744",
        "palette": (255, 23, 68),
        "synonyms": ["drama", "drame"],
        "labels": {
            "fr": "Drame",
            "en": "Drama",
            "es": "Drama",
            "de": "Drama",
            "it": "Drammatico",
        },
    },
    "crime": {
        "tmdb_id": "80",
        "tvmaze": "Crime",
        "badge": "CRIME",
        "color": "#FF9100",
        "palette": (255, 145, 0),
        "synonyms": ["crime", "policier"],
        "labels": {
            "fr": "Policier / Crime",
            "en": "Crime",
            "es": "Crimen",
            "de": "Krimi",
            "it": "Crime",
        },
    },
    "action": {
        "tmdb_id": "10759",
        "tvmaze": "Action",
        "badge": "ACTION",
        "color": "#FF3D00",
        "palette": (255, 61, 0),
        "synonyms": ["action", "adventure", "action_adventure"],
        "labels": {
            "fr": "Action & Aventure",
            "en": "Action & Adventure",
            "es": "Acción y Aventura",
            "de": "Action & Abenteuer",
            "it": "Azione & Avventura",
        },
    },
    "mystery": {
        "tmdb_id": "9648",
        "tvmaze": "Mystery",
        "badge": "MYSTERY",
        "color": "#AA00FF",
        "palette": (170, 0, 255),
        "synonyms": ["mystery", "mystere", "thriller", "supernatural"],
        "labels": {
            "fr": "Mystère / Surnaturel",
            "en": "Mystery / Supernatural",
            "es": "Misterio / Sobrenatural",
            "de": "Mystery",
            "it": "Mistero",
        },
    },
    "comedy": {
        "tmdb_id": "35",
        "tvmaze": "Comedy",
        "badge": "COMEDY",
        "color": "#00E676",
        "palette": (0, 230, 118),
        "synonyms": ["comedy", "comedie", "sitcom"],
        "labels": {
            "fr": "Comédie",
            "en": "Comedy",
            "es": "Comedia",
            "de": "Komödie",
            "it": "Commedia",
        },
    },
    "animation": {
        "tmdb_id": "16",
        "tvmaze": "Anime",
        "badge": "ANIMATED",
        "color": "#FFD600",
        "palette": (255, 214, 0),
        "synonyms": ["animation", "animated"],
        "labels": {
            "fr": "Série d'Animation",
            "en": "Animation",
            "es": "Animación",
            "de": "Animation",
            "it": "Animazione",
        },
    },
}

ANIME_GENRE_CATALOG: Dict[str, Dict[str, Any]] = {
    "shonen": {
        "jikan_genre": "27",
        "anilist_genre": "Action",
        "badge": "SHONEN",
        "color": "#FF6D00",
        "palette": (255, 109, 0),
        "synonyms": ["shonen", "shounen"],
        "labels": {
            "fr": "Shōnen",
            "en": "Shonen",
            "es": "Shōnen",
            "de": "Shōnen",
            "it": "Shōnen",
        },
    },
    "action": {
        "jikan_genre": "1",
        "anilist_genre": "Action",
        "badge": "ACTION",
        "color": "#FF1744",
        "palette": (255, 23, 68),
        "synonyms": ["action"],
        "labels": {
            "fr": "Action",
            "en": "Action",
            "es": "Acción",
            "de": "Action",
            "it": "Azione",
        },
    },
    "scifi": {
        "jikan_genre": "24",
        "anilist_genre": "Sci-Fi",
        "badge": "SCI-FI",
        "color": "#00E5FF",
        "palette": (0, 229, 255),
        "synonyms": ["scifi", "sci_fi", "science_fiction", "cyberpunk"],
        "labels": {
            "fr": "Sci-Fi / Cyberpunk",
            "en": "Sci-Fi / Cyberpunk",
            "es": "Ciencia Ficción / Cyberpunk",
            "de": "Sci-Fi / Cyberpunk",
            "it": "Sci-Fi / Cyberpunk",
        },
    },
    "mecha": {
        "jikan_genre": "18",
        "anilist_genre": "Mecha",
        "badge": "MECHA",
        "color": "#2979FF",
        "palette": (41, 121, 255),
        "synonyms": ["mecha", "robot"],
        "labels": {
            "fr": "Mecha",
            "en": "Mecha",
            "es": "Mecha",
            "de": "Mecha",
            "it": "Mecha",
        },
    },
    "fantasy": {
        "jikan_genre": "10",
        "anilist_genre": "Fantasy",
        "badge": "FANTASY",
        "color": "#D500F9",
        "palette": (213, 0, 249),
        "synonyms": ["fantasy", "isekai", "magic"],
        "labels": {
            "fr": "Fantasy / Isekai",
            "en": "Fantasy / Isekai",
            "es": "Fantasía / Isekai",
            "de": "Fantasy / Isekai",
            "it": "Fantasy / Isekai",
        },
    },
    "seinen": {
        "jikan_genre": "42",
        "anilist_genre": "Thriller",
        "badge": "SEINEN",
        "color": "#AA00FF",
        "palette": (170, 0, 255),
        "synonyms": ["seinen", "psychological", "thriller"],
        "labels": {
            "fr": "Seinen / Thriller",
            "en": "Seinen / Thriller",
            "es": "Seinen / Suspense",
            "de": "Seinen / Thriller",
            "it": "Seinen / Thriller",
        },
    },
    "adventure": {
        "jikan_genre": "2",
        "anilist_genre": "Adventure",
        "badge": "ADVENTURE",
        "color": "#00E676",
        "palette": (0, 230, 118),
        "synonyms": ["adventure", "aventure"],
        "labels": {
            "fr": "Aventure",
            "en": "Adventure",
            "es": "Aventura",
            "de": "Abenteuer",
            "it": "Avventura",
        },
    },
    "comedy": {
        "jikan_genre": "4",
        "anilist_genre": "Comedy",
        "badge": "COMEDY",
        "color": "#FFD600",
        "palette": (255, 214, 0),
        "synonyms": ["comedy", "comedie", "slice_of_life"],
        "labels": {
            "fr": "Comédie / Tranche de vie",
            "en": "Comedy / Slice of Life",
            "es": "Comedia / Recuentos de la vida",
            "de": "Komödie / Slice of Life",
            "it": "Commedia / Slice of Life",
        },
    },
}


# =============================================================================
# 4. Synonym Resolution & Multi-Source Referential Consolidation
# =============================================================================

_EXTRA_MOVIE_GENRES: Dict[str, Dict[str, str]] = {
    "mystery": {"fr": "Mystère", "en": "Mystery", "es": "Misterio", "de": "Mystery", "it": "Mistero"},
    "romance": {"fr": "Romance", "en": "Romance", "es": "Romance", "de": "Romantik", "it": "Romantico"},
    "western": {"fr": "Western", "en": "Western", "es": "Western", "de": "Western", "it": "Western"},
    "war": {"fr": "Guerre", "en": "War", "es": "Bélico", "de": "Kriegsfilm", "it": "Guerra"},
    "history": {"fr": "Histoire", "en": "History", "es": "Historia", "de": "Historie", "it": "Storico"},
    "documentary": {"fr": "Documentaire", "en": "Documentary", "es": "Documental", "de": "Dokumentarfilm", "it": "Documentario"},
    "music": {"fr": "Musique / Musical", "en": "Music", "es": "Música", "de": "Musik", "it": "Musica"},
    "family": {"fr": "Famille", "en": "Family", "es": "Familia", "de": "Familie", "it": "Famiglia"},
    "cyberpunk": {"fr": "Cyberpunk", "en": "Cyberpunk", "es": "Cyberpunk", "de": "Cyberpunk", "it": "Cyberpunk"},
    "martial_arts": {"fr": "Arts Martiaux", "en": "Martial Arts", "es": "Artes Marciales", "de": "Martial Arts", "it": "Arti Marziali"},
    "noir": {"fr": "Film Noir", "en": "Film Noir", "es": "Cine Negro", "de": "Film Noir", "it": "Film Noir"},
    "superhero": {"fr": "Super-Héros", "en": "Superhero", "es": "Superhéroes", "de": "Superhelden", "it": "Supereroi"},
    "kaiju": {"fr": "Kaiju / Monstres", "en": "Kaiju / Monster", "es": "Kaiju / Monstruos", "de": "Kaiju / Monster", "it": "Kaiju / Mostri"},
}
for _mg_id, _mg_labels in _EXTRA_MOVIE_GENRES.items():
    if _mg_id not in MOVIE_GENRE_CATALOG:
        MOVIE_GENRE_CATALOG[_mg_id] = {
            "tmdb_id": "28",
            "itunes_term": _mg_id.replace("_", " "),
            "badge": _mg_id.upper()[:8],
            "color": "#00ACC1",
            "palette": (0, 172, 193),
            "synonyms": [_mg_id],
            "labels": _mg_labels,
        }

_EXTRA_SERIES_GENRES: Dict[str, Dict[str, str]] = {
    "adventure": {"fr": "Aventure", "en": "Adventure", "es": "Aventura", "de": "Abenteuer", "it": "Avventura"},
    "anime": {"fr": "Animation / Anime", "en": "Animation / Anime", "es": "Animación / Anime", "de": "Animation / Anime", "it": "Animazione / Anime"},
    "cyberpunk": {"fr": "Cyberpunk", "en": "Cyberpunk", "es": "Cyberpunk", "de": "Cyberpunk", "it": "Cyberpunk"},
    "espionage": {"fr": "Espionnage", "en": "Espionage", "es": "Espionaje", "de": "Spionage", "it": "Spionaggio"},
    "family": {"fr": "Famille", "en": "Family", "es": "Familia", "de": "Familie", "it": "Famiglia"},
    "fantasy": {"fr": "Fantastique / Fantasy", "en": "Fantasy", "es": "Fantasía", "de": "Fantasy", "it": "Fantasy"},
    "history": {"fr": "Histoire", "en": "History", "es": "Historia", "de": "Historie", "it": "Storico"},
    "horror": {"fr": "Horreur", "en": "Horror", "es": "Terror", "de": "Horror", "it": "Horror"},
    "legal": {"fr": "Judiciaire", "en": "Legal", "es": "Legal / Judicial", "de": "Justiz", "it": "Giudiziario"},
    "medical": {"fr": "Médical", "en": "Medical", "es": "Médico", "de": "Medizin", "it": "Medico"},
    "music": {"fr": "Musique", "en": "Music", "es": "Música", "de": "Musik", "it": "Musica"},
    "nature": {"fr": "Nature / Doc", "en": "Nature / Doc", "es": "Naturaleza / Doc", "de": "Natur / Doku", "it": "Natura / Doc"},
    "romance": {"fr": "Romance", "en": "Romance", "es": "Romance", "de": "Romantik", "it": "Romantico"},
    "science-fiction": {"fr": "Science-Fiction", "en": "Science-Fiction", "es": "Ciencia Ficción", "de": "Science-Fiction", "it": "Fantascienza"},
    "sports": {"fr": "Sport", "en": "Sports", "es": "Deportes", "de": "Sport", "it": "Sport"},
    "supernatural": {"fr": "Surnaturel", "en": "Supernatural", "es": "Sobrenatural", "de": "Übernatürlich", "it": "Soprannaturale"},
    "thriller": {"fr": "Thriller / Suspense", "en": "Thriller", "es": "Suspense / Thriller", "de": "Thriller", "it": "Thriller"},
    "war": {"fr": "Guerre", "en": "War", "es": "Bélico", "de": "Krieg", "it": "Guerra"},
    "western": {"fr": "Western", "en": "Western", "es": "Western", "de": "Western", "it": "Western"},
}
for _sg_id, _sg_labels in _EXTRA_SERIES_GENRES.items():
    if _sg_id not in SERIES_GENRE_CATALOG:
        SERIES_GENRE_CATALOG[_sg_id] = {
            "tmdb_id": "18",
            "tvmaze": _sg_labels["en"].split("/")[0].strip(),
            "badge": _sg_id.upper()[:8],
            "color": "#00ACC1",
            "palette": (0, 172, 193),
            "synonyms": [_sg_id],
            "labels": _sg_labels,
        }

_EXTRA_ANIME_GENRES: Dict[str, Dict[str, str]] = {
    "drama": {"fr": "Drame", "en": "Drama", "es": "Drama", "de": "Drama", "it": "Drammatico"},
    "cyberpunk": {"fr": "Cyberpunk", "en": "Cyberpunk", "es": "Cyberpunk", "de": "Cyberpunk", "it": "Cyberpunk"},
    "shojo": {"fr": "Shōjo", "en": "Shojo", "es": "Shōjo", "de": "Shōjo", "it": "Shōjo"},
    "mystery": {"fr": "Mystère", "en": "Mystery", "es": "Misterio", "de": "Mystery", "it": "Mistero"},
    "psychological": {"fr": "Psychologique", "en": "Psychological", "es": "Psicológico", "de": "Psychologisch", "it": "Psicologico"},
    "horror": {"fr": "Horreur", "en": "Horror", "es": "Terror", "de": "Horror", "it": "Horror"},
    "supernatural": {"fr": "Surnaturel", "en": "Supernatural", "es": "Sobrenatural", "de": "Übernatürlich", "it": "Soprannaturale"},
    "romance": {"fr": "Romance", "en": "Romance", "es": "Romance", "de": "Romantik", "it": "Romantico"},
    "slice_of_life": {"fr": "Tranche de vie", "en": "Slice of Life", "es": "Recuentos de la vida", "de": "Slice of Life", "it": "Slice of Life"},
    "sports": {"fr": "Sport", "en": "Sports", "es": "Deportes", "de": "Sport", "it": "Sport"},
    "music": {"fr": "Musique", "en": "Music", "es": "Música", "de": "Musik", "it": "Musica"},
    "historical": {"fr": "Historique", "en": "Historical", "es": "Histórico", "de": "Historisch", "it": "Storico"},
    "military": {"fr": "Militaire", "en": "Military", "es": "Militar", "de": "Militär", "it": "Militare"},
    "martial_arts": {"fr": "Arts Martiaux", "en": "Martial Arts", "es": "Artes Marciales", "de": "Martial Arts", "it": "Arti Marziali"},
    "samurai": {"fr": "Samouraï", "en": "Samurai", "es": "Samurái", "de": "Samurai", "it": "Samurai"},
    "space": {"fr": "Space Opera", "en": "Space Opera", "es": "Space Opera", "de": "Space Opera", "it": "Space Opera"},
    "vampire": {"fr": "Vampire", "en": "Vampire", "es": "Vampiros", "de": "Vampir", "it": "Vampiri"},
    "isekai": {"fr": "Isekai", "en": "Isekai", "es": "Isekai", "de": "Isekai", "it": "Isekai"},
    "mahou_shoujo": {"fr": "Magical Girl", "en": "Magical Girl", "es": "Magical Girl", "de": "Magical Girl", "it": "Magical Girl"},
    "post_apocalyptic": {"fr": "Post-Apocalyptique", "en": "Post-Apocalyptic", "es": "Postapocalíptico", "de": "Postapokalyptisch", "it": "Post-apocalittico"},
    "racing": {"fr": "Course / Racing", "en": "Racing", "es": "Carreras", "de": "Rennen", "it": "Corse"},
    "parody": {"fr": "Parodie", "en": "Parody", "es": "Parodia", "de": "Parodie", "it": "Parodia"},
    "thriller": {"fr": "Suspense / Thriller", "en": "Suspense / Thriller", "es": "Suspense / Thriller", "de": "Thriller", "it": "Thriller"},
}
for _ag_id, _ag_labels in _EXTRA_ANIME_GENRES.items():
    if _ag_id not in ANIME_GENRE_CATALOG:
        ANIME_GENRE_CATALOG[_ag_id] = {
            "jikan_genre": "1",
            "anilist_genre": _ag_labels["en"].split("/")[0].strip(),
            "badge": _ag_id.upper()[:8],
            "color": "#00ACC1",
            "palette": (0, 172, 193),
            "synonyms": [_ag_id],
            "labels": _ag_labels,
        }

_SYSTEM_SYNONYM_INDEX: Dict[str, str] = {}
for _sys_id, _sys_meta in RETRO_SYSTEM_CATALOG.items():
    _SYSTEM_SYNONYM_INDEX[_sys_id] = _sys_id
    for _syn in _sys_meta.get("synonyms", []):
        _SYSTEM_SYNONYM_INDEX[normalize_category_slug(_syn)] = _sys_id


def _build_genre_synonym_index(catalog: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    idx: Dict[str, str] = {}
    for gid, meta in catalog.items():
        idx[gid] = gid
        idx[normalize_category_slug(gid)] = gid
        for syn in meta.get("synonyms", []):
            idx[normalize_category_slug(syn)] = gid
        for kw in meta.get("adb_keywords", []):
            idx[normalize_category_slug(kw)] = gid
        for skw in meta.get("screenscraper_keywords", []):
            idx[normalize_category_slug(skw)] = gid
        for lbl in (meta.get("labels") or {}).values():
            idx[normalize_category_slug(lbl)] = gid
    return idx


_GENRE_SYNONYM_BY_DOMAIN: Dict[DomainType, Dict[str, str]] = {
    "games": _build_genre_synonym_index(GAME_GENRE_CATALOG),
    "movies": _build_genre_synonym_index(MOVIE_GENRE_CATALOG),
    "series": _build_genre_synonym_index(SERIES_GENRE_CATALOG),
    "anime": _build_genre_synonym_index(ANIME_GENRE_CATALOG),
}


def resolve_canonical_system(raw_system: str) -> str:
    """Resolve any scraper or user system token (`NES`, `Famicom`, `Nintendo Entertainment System`) to its canonical ID."""
    slug = normalize_category_slug(raw_system)
    return _SYSTEM_SYNONYM_INDEX.get(slug, slug)


def resolve_canonical_genre(domain: DomainType, raw_genre: str) -> str:
    """Resolve any scraper or user genre token (`Platformer`, `Plateforme`, `Fighter / Versus`, `Shooter / Walking`) to its canonical ID."""
    slug = normalize_category_slug(raw_genre)
    domain_idx = _GENRE_SYNONYM_BY_DOMAIN.get(domain, {})
    if slug in domain_idx:
        return domain_idx[slug]
    for syn_slug, canonical_id in sorted(domain_idx.items(), key=lambda kv: len(kv[0]), reverse=True):
        if syn_slug and len(syn_slug) >= 4 and syn_slug in slug:
            return canonical_id
    return slug


def is_arcade_system(sys_key: str) -> bool:
    """Return True if `sys_key` is an Arcade/MAME system supported by ArcadeItalia."""
    canon = resolve_canonical_system(sys_key)
    meta = RETRO_SYSTEM_CATALOG.get(canon)
    if meta is not None:
        return bool(meta.get("is_arcade", False))
    return canon in ("arcade", "mame", "neogeo", "cps1", "cps2", "cps3", "fbneo")


class ReferentialConsolidator:
    """
    Consolidates live referentials from all registered `IScraperSource` adapters in a domain
    into a single source-agnostic, localized specification for the DMD WebUI (`GET /api/v1/{domain}/filters`).
    """

    @staticmethod
    def build_domain_referential(
        domain: DomainType,
        lang: str = "fr",
        sources_info: Optional[List[Dict[str, str]]] = None,
        extra_discovered_genres: Optional[List[Tuple[str, str]]] = None,
        extra_discovered_systems: Optional[List[Tuple[str, str]]] = None,
    ) -> Dict[str, Any]:
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        decades_list = [
            {"id": did, "label": localize_label(dmeta["labels"], short_lang, did)}
            for did, dmeta in CANONICAL_DECADES.items()
        ]

        if domain == "games":
            systems_out: List[Dict[str, Any]] = []
            seen_sys: Set[str] = set()
            for sys_id, smeta in RETRO_SYSTEM_CATALOG.items():
                seen_sys.add(sys_id)
                supported_sources = (
                    ["screenscraper", "arcadeitalia", "libretro_wikidata"]
                    if smeta.get("is_arcade")
                    else ["screenscraper", "libretro_wikidata"]
                )
                systems_out.append(
                    {
                        "id": sys_id,
                        "label": localize_label(smeta["labels"], short_lang, sys_id),
                        "badge": smeta["badge"],
                        "is_arcade": bool(smeta.get("is_arcade")),
                        "genres": list(smeta.get("genres", _ALL_CONSOLE_GENRES)),
                        "decades": list(smeta.get("decades", ["1980s", "1990s"])),
                        "sources": supported_sources,
                    }
                )
            for raw_id, raw_label in (extra_discovered_systems or []):
                canon_sys = resolve_canonical_system(raw_id)
                if canon_sys and canon_sys not in seen_sys:
                    seen_sys.add(canon_sys)
                    systems_out.append(
                        {
                            "id": canon_sys,
                            "label": raw_label or canon_sys.upper(),
                            "badge": canon_sys[:6].upper(),
                            "is_arcade": False,
                            "genres": list(_ALL_CONSOLE_GENRES),
                            "decades": ["1980s", "1990s", "2000s"],
                            "sources": ["screenscraper", "libretro_wikidata"],
                        }
                    )

            genres_out: List[Dict[str, Any]] = []
            seen_genres: Set[str] = set()
            for gid, gmeta in GAME_GENRE_CATALOG.items():
                seen_genres.add(gid)
                genres_out.append(
                    {
                        "id": gid,
                        "label": localize_label(gmeta["labels"], short_lang, gid),
                    }
                )
            for raw_gid, raw_glabel in (extra_discovered_genres or []):
                canon_g = resolve_canonical_genre("games", raw_gid)
                if canon_g and canon_g not in seen_genres:
                    seen_genres.add(canon_g)
                    genres_out.append({"id": canon_g, "label": raw_glabel or canon_g.title()})

            return {
                "domain": "games",
                "lang": short_lang,
                "sources": sources_info or [],
                "systems": systems_out,
                "genres": genres_out,
                "decades": decades_list[:4],  # 1970s..2000s for retro games
            }

        genre_cat = (
            MOVIE_GENRE_CATALOG
            if domain == "movies"
            else (SERIES_GENRE_CATALOG if domain == "series" else ANIME_GENRE_CATALOG)
        )
        genres_list: List[Dict[str, Any]] = []
        seen_dom_genres: Set[str] = set()
        for gid, gmeta in genre_cat.items():
            seen_dom_genres.add(gid)
            genres_list.append(
                {
                    "id": gid,
                    "label": localize_label(gmeta["labels"], short_lang, gid),
                }
            )
        for raw_gid, raw_glabel in (extra_discovered_genres or []):
            canon_g = resolve_canonical_genre(domain, raw_gid)
            if canon_g and canon_g not in seen_dom_genres:
                seen_dom_genres.add(canon_g)
                genres_list.append({"id": canon_g, "label": raw_glabel or canon_g.title()})

        result: Dict[str, Any] = {
            "domain": domain,
            "lang": short_lang,
            "sources": sources_info or [],
            "genres": genres_list,
            "decades": decades_list,
        }
        if domain == "anime":
            video_type_labels = {
                "any": {"fr": "Opening, Ending ou Trailer", "en": "Opening, Ending or Trailer", "es": "Opening, Ending o Tráiler"},
                "opening": {"fr": "Opening (OP sans crédits)", "en": "Opening (Creditless OP)", "es": "Opening (OP sin créditos)"},
                "ending": {"fr": "Ending (ED sans crédits)", "en": "Ending (Creditless ED)", "es": "Ending (ED sin créditos)"},
                "trailer": {"fr": "Bande-annonce (PV / Trailer)", "en": "Official PV / Trailer", "es": "Tráiler Oficial (PV)"},
            }
            result["video_types"] = [
                {"id": vt_id, "label": localize_label(vt_lbls, short_lang, vt_id)}
                for vt_id, vt_lbls in video_type_labels.items()
            ]
        return result

    @staticmethod
    def source_supports_criteria(source_id: str, criteria: FilterCriteria) -> bool:
        """
        Determine whether `source_id` supports the requested `criteria` (Capability-Aware Routing).
        Specifically prevents calling `arcadeitalia` (100% MAME/Arcade) when the user only requested
        home consoles like `nes`, `snes`, `megadrive`, `psx`, etc.
        """
        if criteria.domain == "games" and source_id == "arcadeitalia":
            req_systems = [
                resolve_canonical_system(s)
                for s in (criteria.systems or [])
                if s and s.lower() != "all"
            ]
            if req_systems and not any(is_arcade_system(s) for s in req_systems):
                return False
        return True
