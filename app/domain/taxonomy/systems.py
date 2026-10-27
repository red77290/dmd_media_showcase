"""
Canonical Retro & Arcade Gaming Systems Catalog (`app/domain/taxonomy/systems.py`).

Combines `ARCADE_AND_MAIN_CONSOLE_SYSTEMS` and `CLASSIC_HANDHELD_COMPUTER_SYSTEMS`
into the unified 64-system `RETRO_SYSTEM_CATALOG` while keeping each module concise.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.domain.taxonomy.systems_arcade_console import (
    ALL_ARCADE_GENRES,
    ALL_CONSOLE_GENRES,
    ARCADE_AND_MAIN_CONSOLE_SYSTEMS,
)
from app.domain.taxonomy.systems_handheld_computer import (
    CLASSIC_HANDHELD_COMPUTER_SYSTEMS,
)

_ALL_CONSOLE_GENRES: List[str] = ALL_CONSOLE_GENRES
_ALL_ARCADE_GENRES: List[str] = ALL_ARCADE_GENRES

_EXTRA_COMPUTER_ENGINE_SYSTEMS: Dict[str, Dict[str, Any]] = {
    "msx2": {
        "is_arcade": False,
        "screenscraper_id": "116",
        "igdb_id": 53,
        "igdb_ids": [53],
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q1142848",
        "libretro_repo": "Microsoft_-_MSX2",
        "archive_video_repo": "MicrosoftMSX2CartsVideoSnaps_201804",
        "badge": "MSX2",
        "color": "#1A237E",
        "palette": (26, 35, 126),
        "decades": ["1980s", "1990s"],
        "genres": ALL_CONSOLE_GENRES,
        "synonyms": ["msx2", "msx_2", "microsoft_msx2", "msx2+"],
        "labels": {
            "fr": "MSX2 / MSX2+",
            "en": "MSX2 / MSX2+",
            "es": "MSX2 / MSX2+",
            "de": "MSX2 / MSX2+",
            "it": "MSX2 / MSX2+",
        },
    },
    "scummvm": {
        "is_arcade": False,
        "screenscraper_id": "123",
        "igdb_id": 13,
        "igdb_ids": [13, 6, 16],
        "arcadeitalia_driver": None,
        "wikidata_qid": "Q843239",
        "libretro_repo": "ScummVM",
        "badge": "SCUMM",
        "color": "#8E24AA",
        "palette": (142, 36, 170),
        "decades": ["1980s", "1990s", "2000s"],
        "genres": ["adventure", "puzzle", "rpg", "action"],
        "synonyms": ["scummvm", "scumm_vm", "scumm", "lucasarts"],
        "labels": {
            "fr": "ScummVM (Point & Click)",
            "en": "ScummVM (Point & Click)",
            "es": "ScummVM (Point & Click)",
            "de": "ScummVM (Point & Click)",
            "it": "ScummVM (Point & Click)",
        },
    },
}

RETRO_SYSTEM_CATALOG: Dict[str, Dict[str, Any]] = {
    **ARCADE_AND_MAIN_CONSOLE_SYSTEMS,
    **CLASSIC_HANDHELD_COMPUTER_SYSTEMS,
    **_EXTRA_COMPUTER_ENGINE_SYSTEMS,
}
