"""
Application Service: `CandidateAggregator` (`app/application/candidate_aggregator.py`).

Composite Enricher (GoF) implementing cross-source metadata & asset aggregation:
when a primary `IScraperSource` returns a valid `MediaCandidate` that is missing key DMD
assets (e.g. `marquee_url`, direct `video_url`, `backdrop_url` / `snapshot_urls`, or
localized `synopsis`), queries compatible secondary sources in the domain chain with a
bounded per-source timeout (`3.0s`) and merges only missing fields while tracking
composite provenance (`source_id = "igdb+arcadeitalia"`, `extra_meta["sources_aggregated"]`).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Set, Tuple

from app.domain.entities import FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import ReferentialConsolidator, build_game_fingerprint

logger = logging.getLogger("dmd_showcase.application.aggregator")

_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SEQUEL_NUM_MAP = {
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
}
_STOP_TOKENS = {"the", "of", "and", "le", "la", "les", "de", "du", "des", "el", "los", "las", "a"}


def _core_tokens_and_numbers(title: str) -> Tuple[Set[str], Set[str]]:
    cleaned = _PAREN_RE.sub(" ", (title or "").lower())
    raw_toks = _TOKEN_RE.findall(cleaned)
    norm_toks: Set[str] = set()
    nums: Set[str] = set()
    for tok in raw_toks:
        mapped = _SEQUEL_NUM_MAP.get(tok, tok)
        if mapped.isdigit():
            nums.add(mapped)
            norm_toks.add(mapped)
        elif len(mapped) >= 2 and mapped not in _STOP_TOKENS:
            norm_toks.add(mapped)
    return norm_toks, nums


class CandidateAggregator:
    """Cross-source Composite Enricher for `MediaCandidate` instances."""

    SECONDARY_TIMEOUT_S: float = 3.0

    @staticmethod
    def needs_enrichment(cand: MediaCandidate, criteria: FilterCriteria) -> bool:
        """Return True if `cand` lacks one or more key DMD visual/localized fields."""
        short_lang = (criteria.lang or cand.lang or "fr").split("-")[0].split("_")[0].lower()
        meta = cand.extra_meta or {}

        missing_synopsis = (not cand.synopsis) or (
            short_lang != "en" and meta.get("synopsis_localized") == "0"
        )
        missing_marquee = not cand.marquee_url
        if cand.domain in ("games", "movies", "anime"):
            missing_video = not cand.video_url
        else:
            missing_video = not cand.video_url and not cand.youtube_url
        missing_stills = not cand.backdrop_url and len(cand.snapshot_urls) == 0

        return bool(missing_marquee or missing_video or missing_stills or missing_synopsis)

    @staticmethod
    def _is_compatible_donor(primary: MediaCandidate, donor: MediaCandidate) -> bool:
        """Verify `donor` represents the same game/film/series/anime before merging fields."""
        if primary.domain != donor.domain:
            return False

        p_meta = primary.extra_meta or {}
        d_meta = donor.extra_meta or {}

        if primary.domain == "games":
            p_rom = str(p_meta.get("mame_rom") or "").strip().lower()
            d_rom = str(d_meta.get("mame_rom") or "").strip().lower()
            if p_rom and d_rom:
                if p_rom == d_rom or (len(p_rom) >= 4 and p_rom[:4] == d_rom[:4]):
                    return True
            p_sys = str(p_meta.get("system") or "").strip()
            d_sys = str(d_meta.get("system") or p_sys).strip()
            fp_p = build_game_fingerprint(p_sys, primary.title)
            fp_d = build_game_fingerprint(p_sys or d_sys, donor.title)
            if fp_p and fp_d and fp_p == fp_d:
                return True

        # Year sanity check for cinema/series/anime (prevent sequel cross-contamination)
        if primary.domain != "games":
            y1 = (primary.year or "")[:4]
            y2 = (donor.year or "")[:4]
            if y1.isdigit() and y2.isdigit() and abs(int(y1) - int(y2)) > 2:
                return False

        t1_toks, n1 = _core_tokens_and_numbers(primary.title)
        t2_toks, n2 = _core_tokens_and_numbers(donor.title)
        if not t1_toks or not t2_toks:
            return False

        # Reject mismatched sequel numbers (e.g. Street Fighter 2 vs Street Fighter 3)
        if (n1 or n2) and n1 != n2:
            # Exception: Japanese "Super Street Fighter II X" vs "Super Street Fighter II Turbo" both have {"2"}
            return False

        overlap = len(t1_toks & t2_toks)
        smaller = min(len(t1_toks), len(t2_toks))
        return overlap >= max(1, (smaller + 1) // 2)

    @staticmethod
    def _build_secondary_lookup(
        cand: MediaCandidate,
        secondary_source_id: str,
        base_criteria: FilterCriteria,
    ) -> Tuple[str, FilterCriteria]:
        meta = cand.extra_meta or {}
        if cand.domain == "games":
            cand_sys = str(meta.get("system") or "").strip()
            systems = [cand_sys] if cand_sys else list(base_criteria.systems or [])
            sec_criteria = FilterCriteria(
                domain="games",
                lang=base_criteria.lang,
                systems=systems,
                genres=[],
                decades=[],
                video_type=base_criteria.video_type,
            )
            mame_rom = str(meta.get("mame_rom") or "").strip()
            if secondary_source_id == "arcadeitalia" and mame_rom:
                return mame_rom, sec_criteria
            return cand.title, sec_criteria

        return (
            cand.title,
            FilterCriteria(
                domain=cand.domain,
                lang=base_criteria.lang,
                genres=[],
                decades=[],
                video_type=base_criteria.video_type,
            ),
        )

    @staticmethod
    def _merge_donor_into_primary(
        primary: MediaCandidate,
        donor: MediaCandidate,
        short_lang: str,
    ) -> bool:
        contributed = False
        if not primary.poster_url and donor.poster_url:
            primary.poster_url = donor.poster_url
            contributed = True
        if not primary.jacket_url and (donor.jacket_url or donor.poster_url):
            primary.jacket_url = donor.jacket_url or donor.poster_url
            contributed = True
        if not primary.marquee_url and donor.marquee_url:
            primary.marquee_url = donor.marquee_url
            contributed = True
        if not primary.backdrop_url and donor.backdrop_url:
            primary.backdrop_url = donor.backdrop_url
            contributed = True
        if not primary.video_url and donor.video_url:
            primary.video_url = donor.video_url
            contributed = True
        if not primary.youtube_url and donor.youtube_url:
            primary.youtube_url = donor.youtube_url
            contributed = True

        if len(primary.snapshot_urls) < 4 and donor.snapshot_urls:
            before_count = len(primary.snapshot_urls)
            for snap in donor.snapshot_urls:
                primary.add_unique_snapshot(snap)
                if len(primary.snapshot_urls) >= 4:
                    break
            if len(primary.snapshot_urls) > before_count:
                contributed = True

        p_meta = primary.extra_meta
        d_meta = donor.extra_meta or {}
        p_loc = p_meta.get("synopsis_localized") == "1"
        d_loc = d_meta.get("synopsis_localized") == "1"

        if (not primary.synopsis and donor.synopsis) or (
            short_lang != "en"
            and not p_loc
            and donor.synopsis
            and (d_loc or len(donor.synopsis) > len(primary.synopsis) + 25)
        ):
            primary.synopsis = donor.synopsis
            if d_loc:
                p_meta["synopsis_localized"] = "1"
            contributed = True

        if d_meta.get("mame_rom") and not p_meta.get("mame_rom"):
            p_meta["mame_rom"] = d_meta["mame_rom"]

        return contributed

    async def enrich_candidate(
        self,
        cand: MediaCandidate,
        chain: List[IScraperSource],
        criteria: FilterCriteria,
    ) -> MediaCandidate:
        """
        Enrich missing DMD assets on `cand` using remaining configured secondary sources in `chain`.
        Never overwrites non-empty primary fields and bounds each secondary lookup to `3.0s`.
        """
        if cand is None or not self.needs_enrichment(cand, criteria):
            return cand

        origin_sources: List[str] = list(
            (cand.extra_meta or {}).get("sources_aggregated")
            or ([cand.source_id] if cand.source_id and "+" not in cand.source_id else cand.source_id.split("+"))
        )
        origin_sources = [s for s in origin_sources if s]
        short_lang = (criteria.lang or cand.lang or "fr").split("-")[0].split("_")[0].lower()

        for sec in chain:
            if not self.needs_enrichment(cand, criteria):
                break
            if sec.source_id in origin_sources:
                continue
            if not sec.is_configured() or sec.is_in_cooldown():
                continue

            enrich_query, sec_criteria = self._build_secondary_lookup(
                cand, sec.source_id, criteria
            )
            if not enrich_query or not ReferentialConsolidator.source_supports_criteria(
                sec.source_id, sec_criteria
            ):
                continue

            try:
                sec_hits = await asyncio.wait_for(
                    sec.search_candidates(
                        query=enrich_query,
                        criteria=sec_criteria,
                        limit=3,
                    ),
                    timeout=self.SECONDARY_TIMEOUT_S,
                )
            except Exception as exc:
                err_txt = str(exc).strip()
                err_summary = f"{type(exc).__name__}: {err_txt}" if err_txt else type(exc).__name__
                logger.warning(
                    "[AGGREGATOR] Secondary enrichment on '%s' for '%s' timed out/failed: %s",
                    sec.source_id,
                    cand.title,
                    err_summary,
                )
                continue

            for donor in sec_hits or []:
                if donor is None or not self._is_compatible_donor(cand, donor):
                    continue
                if self._merge_donor_into_primary(cand, donor, short_lang):
                    if sec.source_id not in origin_sources:
                        origin_sources.append(sec.source_id)
                    break

        if len(origin_sources) >= 2:
            cand.extra_meta["sources_aggregated"] = list(origin_sources)
            cand.source_id = "+".join(origin_sources)

        return cand
