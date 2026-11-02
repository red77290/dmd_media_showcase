"""
Offline deterministic `IScraperSource` adapter backed by a local `catalog.json` directory.

Enabled exclusively when `DMD_E2E_FIXTURES_DIR` points to a valid local fixture folder so
containerized E2E test suites execute the complete `dmd_media_showcase` pipeline (`ProcessPoolExecutor`,
`dmd_gif_converter`, `DMDZ` v2 lossless codec, MQTT Retain control plane, and HTTP `Content-Length`
streaming) with zero external internet access or upstream API keys.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource

logger = logging.getLogger("dmd_showcase.scrapers.e2e_fixtures")

_EXT_RE = re.compile(r"\.(zip|7z|sfc|smc|md|gen|bin|cue|iso|chd|nes|gba|gb|pce)$", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_token(raw: str) -> str:
    cleaned = _EXT_RE.sub("", (raw or "").strip().lower())
    return _NON_ALNUM_RE.sub("", cleaned)


class LocalFixtureCatalogAdapter(IScraperSource):
    """
    Deterministic local catalog source implementing `IScraperSource` for offline E2E testing.
    Serves media URLs pointing to the server's loopback `/_e2e_fixtures/*` static endpoint.
    """

    def __init__(
        self,
        fixtures_dir: Path,
        base_url_resolver: Callable[[], str],
        domain: DomainType = "games",
    ) -> None:
        self.fixtures_dir = Path(fixtures_dir).resolve()
        self._base_url_resolver = base_url_resolver
        self.domain = domain
        self.source_id = f"e2e_local_fixtures_{domain}" if domain != "games" else "e2e_local_fixtures"
        self.display_name = "Local E2E Deterministic Fixtures"
        self.role = "primary"

    def is_configured(self) -> bool:
        return (self.fixtures_dir / "catalog.json").is_file()

    def _load_entries(self) -> List[Dict[str, Any]]:
        catalog_path = self.fixtures_dir / "catalog.json"
        if not catalog_path.is_file():
            return []
        try:
            payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[E2E-FIXTURES] Failed to parse %s: %s", catalog_path, exc)
            return []
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return [
            entry
            for entry in items
            if isinstance(entry, dict) and str(entry.get("domain") or "games") == self.domain
        ]

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        systems_seen: List[Dict[str, str]] = []
        seen_ids: Set[str] = set()
        for entry in self._load_entries():
            for sys_id in entry.get("systems") or []:
                s = str(sys_id).strip().lower()
                if s and s not in seen_ids:
                    seen_ids.add(s)
                    systems_seen.append({"id": s, "label": s.upper()})
        if not systems_seen:
            systems_seen = [
                {"id": "neogeo", "label": "NEOGEO"},
                {"id": "snes", "label": "SNES"},
                {"id": "megadrive", "label": "MEGADRIVE"},
            ]
        return {
            "systems": systems_seen,
            "genres": [{"id": "action", "label": "Action"}, {"id": "platform", "label": "Platform"}],
            "decades": [{"id": "1990s", "label": "1990s"}],
        }

    def _resolve_asset_url(self, rel_path: Optional[str]) -> Optional[str]:
        if not rel_path:
            return None
        clean_rel = str(rel_path).lstrip("/")
        base = self._base_url_resolver().rstrip("/")
        return f"{base}/{clean_rel}"

    def _entry_to_candidate(self, entry: Dict[str, Any], lang: str = "fr") -> MediaCandidate:
        raw_syn = entry.get("synopsis") or "Deterministic local E2E fixture."
        if isinstance(raw_syn, dict):
            synopsis = str(raw_syn.get(lang) or raw_syn.get("en") or next(iter(raw_syn.values()), ""))
        else:
            synopsis = str(raw_syn)

        raw_pal = entry.get("theme_palette") or [230, 74, 25]
        palette: Tuple[int, int, int] = (
            int(raw_pal[0]),
            int(raw_pal[1]),
            int(raw_pal[2]),
        )

        poster_url = self._resolve_asset_url(entry.get("poster_rel"))
        jacket_url = self._resolve_asset_url(entry.get("jacket_rel")) or poster_url
        marquee_url = self._resolve_asset_url(entry.get("marquee_rel"))
        video_url = self._resolve_asset_url(entry.get("video_rel"))

        sys_list = [str(s) for s in (entry.get("systems") or []) if s]
        primary_sys = str(entry.get("system") or (sys_list[0] if sys_list else ""))
        cand = MediaCandidate(
            id=str(entry.get("id") or "e2e_item"),
            domain=self.domain,
            title=str(entry.get("title") or "E2E Game"),
            year=str(entry.get("year") or entry.get("release_year") or "1996"),
            badge=str(entry.get("badge") or primary_sys.upper() or "ARCADE"),
            badge_color=str(entry.get("badge_color") or "#FF5722"),
            subtitle=str(entry.get("subtitle") or "Action • SNK"),
            rating=str(entry.get("rating") or "9.4/10"),
            synopsis=synopsis,
            lang=lang or "fr",
            poster_url=poster_url,
            jacket_url=jacket_url,
            marquee_url=marquee_url,
            video_url=video_url,
            source_id=self.source_id,
            theme_palette=palette,
            extra_meta={"systems": sys_list, "system": primary_sys},
        )
        for snap_rel in entry.get("snapshots_rel") or []:
            cand.add_unique_snapshot(self._resolve_asset_url(str(snap_rel)))
        return cand

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        entries = self._load_entries()
        if not entries:
            return []

        wanted_systems = {s.strip().lower() for s in (criteria.systems or []) if s.strip()}
        filtered = entries
        if wanted_systems:
            sys_matches = [
                e
                for e in entries
                if wanted_systems.intersection({str(x).lower() for x in (e.get("systems") or [])})
            ]
            if sys_matches:
                filtered = sys_matches

        unseen = [e for e in filtered if str(e.get("id")) not in seen_ids]
        chosen = unseen if unseen else filtered
        return [self._entry_to_candidate(e, lang=criteria.lang) for e in chosen[: max(1, count)]]

    def _score_entry(
        self,
        entry: Dict[str, Any],
        q_norm: str,
        wanted_systems: Set[str],
    ) -> int:
        if not q_norm:
            return 0
        tokens: List[str] = [
            _normalize_token(str(entry.get("id") or "")),
            _normalize_token(str(entry.get("rom") or "")),
            _normalize_token(str(entry.get("title") or "")),
        ]
        for alias in entry.get("aliases") or []:
            tokens.append(_normalize_token(str(alias)))
        tokens = [t for t in tokens if t]

        score = 0
        if q_norm in tokens:
            score = 100
        elif any(q_norm in t or t in q_norm for t in tokens):
            score = 70

        if score > 0 and wanted_systems:
            entry_sys = {str(x).strip().lower() for x in (entry.get("systems") or [])}
            if wanted_systems.intersection(entry_sys):
                score += 25
        return score

    async def search_candidates(
        self,
        query: str,
        criteria: FilterCriteria,
        limit: int = 8,
    ) -> List[MediaCandidate]:
        entries = self._load_entries()
        if not entries:
            return []

        q_norm = _normalize_token(query)
        wanted_systems = {s.strip().lower() for s in (criteria.systems or []) if s.strip()}
        scored: List[Tuple[int, int, Dict[str, Any]]] = []
        for idx, entry in enumerate(entries):
            sc = self._score_entry(entry, q_norm, wanted_systems)
            if sc > 0:
                scored.append((sc, -idx, entry))

        scored.sort(reverse=True)
        return [
            self._entry_to_candidate(entry, lang=criteria.lang)
            for _, _, entry in scored[: max(1, limit)]
        ]

    async def search_by_query(
        self,
        query: str,
        lang: str = "fr",
    ) -> Optional[MediaCandidate]:
        crit = FilterCriteria(domain=self.domain, lang=lang, systems=[])
        matches = await self.search_candidates(query=query, criteria=crit, limit=1)
        return matches[0] if matches else None
