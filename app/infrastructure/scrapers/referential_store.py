"""
Real-Time Scraper Referential Persistence Store (`./data/referentials/{source_id}.json`).

Provides the reusable `LiveReferentialStore` JSON persistence engine and re-exports the
canonical `IScraperReferentialStrategy` port alongside per-domain strategy implementations.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional

from app.domain.ports.referential_strategy import IScraperReferentialStrategy
from app.infrastructure.scrapers.anime.anilist_referential import AniListReferentialStrategy
from app.infrastructure.scrapers.anime.jikan_referential import JikanReferentialStrategy
from app.infrastructure.scrapers.games.arcadeitalia_referential import (
    ArcadeItaliaReferentialStrategy,
)
from app.infrastructure.scrapers.games.libretro_wikidata_referential import (
    LibretroWikidataReferentialStrategy,
)
from app.infrastructure.scrapers.referential_utils import (
    is_excluded_nointro_filename,
    normalize_game_lookup_key,
    random_search_syllable,
    score_libretro_filename_for_lang,
)
from app.infrastructure.scrapers.series.tvmaze_referential import TvMazeReferentialStrategy
from app.portable_env import get_data_dir

logger = logging.getLogger("dmd_showcase.scrapers.referential")

_is_excluded_nointro_filename = is_excluded_nointro_filename


class LiveReferentialStore:
    """
    Thread-safe JSON persistence store for live scraper referentials in
    `./data/referentials/{source_id}.json`.
    """

    def __init__(self, source_id: str, ttl_seconds: int = 86400) -> None:
        self.source_id = source_id
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, Any]] = None

    @property
    def file_path(self) -> Path:
        return get_data_dir() / "referentials" / f"{self.source_id}.json"

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
            path = self.file_path
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        self._cache = raw
                        return dict(raw)
                except Exception as exc:
                    logger.debug(
                        "[REFERENTIAL:%s] Could not read %s: %s", self.source_id, path, exc
                    )
            self._cache = {"updated_at": 0.0, "tokens": [], "metadata": {}}
            return dict(self._cache)

    def is_expired(self) -> bool:
        data = self.load()
        updated_at = float(data.get("updated_at") or 0.0)
        tokens = data.get("tokens") or []
        return (time.time() - updated_at) > self.ttl_seconds or len(tokens) == 0

    def save(
        self, tokens: List[str], metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        with self._lock:
            current = self.load()
            merged_tokens = list(
                dict.fromkeys([t for t in (current.get("tokens") or []) + tokens if t])
            )
            merged_meta = dict(current.get("metadata") or {})
            if metadata:
                merged_meta.update(metadata)
            payload = {
                "source_id": self.source_id,
                "updated_at": time.time(),
                "tokens": merged_tokens,
                "metadata": merged_meta,
            }
            self._cache = payload
            try:
                path = self.file_path
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(tmp, path)
            except Exception as exc:
                logger.debug(
                    "[REFERENTIAL:%s] Could not write referential: %s", self.source_id, exc
                )
            return dict(payload)


libretro_wikidata_referential_strategy = LibretroWikidataReferentialStrategy(LiveReferentialStore)
arcadeitalia_referential_strategy = ArcadeItaliaReferentialStrategy(LiveReferentialStore)
jikan_referential_strategy = JikanReferentialStrategy(LiveReferentialStore)
anilist_referential_strategy = AniListReferentialStrategy(LiveReferentialStore)
tvmaze_referential_strategy = TvMazeReferentialStrategy(LiveReferentialStore)

__all__ = [
    "AniListReferentialStrategy",
    "ArcadeItaliaReferentialStrategy",
    "IScraperReferentialStrategy",
    "JikanReferentialStrategy",
    "LibretroWikidataReferentialStrategy",
    "LiveReferentialStore",
    "TvMazeReferentialStrategy",
    "anilist_referential_strategy",
    "arcadeitalia_referential_strategy",
    "jikan_referential_strategy",
    "libretro_wikidata_referential_strategy",
    "normalize_game_lookup_key",
    "random_search_syllable",
    "score_libretro_filename_for_lang",
    "tvmaze_referential_strategy",
]
