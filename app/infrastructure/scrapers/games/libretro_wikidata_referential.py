"""
Live Referential Strategy for Console & Retro Games (`LibretroWikidataAdapter`).

Queries Wikidata SPARQL (`wd:Q7889` = Video Game ONLY) or MediaWiki CirrusSearch fallback,
and delegates file-tree asset resolution to `LibretroTreeResolver`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import FilterCriteria
from app.domain.ports.referential_strategy import IScraperReferentialStrategy
from app.domain.taxonomy import (
    GAME_GENRE_CATALOG,
    RETRO_SYSTEM_CATALOG,
    is_disallowed_non_fighting_title,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.scrapers.games.libretro_tree_resolver import LibretroTreeResolver

logger = logging.getLogger("dmd_showcase.scrapers.referential.wikidata")


class LibretroWikidataReferentialStrategy(IScraperReferentialStrategy):
    """Harvests verified Wikidata Video Game entities and resolves Libretro/Archive.org URLs."""

    source_id = "libretro_wikidata"
    SCHEMA_VERSION = 3

    def __init__(self, store_cls: Optional[Any] = None) -> None:
        if store_cls is None:
            from app.infrastructure.scrapers.referential_store import LiveReferentialStore

            store_cls = LiveReferentialStore
        self.store = store_cls(self.source_id, ttl_seconds=86400)
        self._tree_resolver = LibretroTreeResolver(
            store_factory=lambda sid, ttl: store_cls(sid, ttl_seconds=ttl)
        )

    def _get_tree_store(self, sys_key: str) -> Any:
        return self._tree_resolver.get_tree_store(sys_key)

    async def sync_system_libretro_tree(
        self, client: Any, sys_key: str
    ) -> Dict[str, List[str]]:
        return await self._tree_resolver.sync_system_libretro_tree(client, sys_key)

    async def resolve_verified_libretro_urls(
        self,
        client: Any,
        sys_key: str,
        game_title: str,
        lang: str = "fr",
    ) -> Dict[str, Optional[str]]:
        return await self._tree_resolver.resolve_verified_libretro_urls(
            client, sys_key, game_title, lang=lang
        )

    async def harvest_wikidata_games(
        self,
        client: Any,
        sys_key: str,
        genre_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query Wikidata SPARQL (or CirrusSearch fallback) for verified Video Games (`wd:Q7889`)."""
        canon_sys = resolve_canonical_system(sys_key)
        canon_genre = resolve_canonical_genre("games", genre_key) if genre_key else ""
        cache_bucket = f"{canon_sys}:{canon_genre or 'all'}"

        data = self.store.load()
        meta = data.get("metadata") or {}
        is_fresh = not self.store.is_expired()
        by_bucket = (
            dict(meta.get("by_system_genre") or {})
            if meta.get("schema_version") == self.SCHEMA_VERSION
            else {}
        )
        cached_list = by_bucket.get(cache_bucket) if is_fresh else None
        if isinstance(cached_list, list) and len(cached_list) >= 5:
            return cached_list

        smeta = RETRO_SYSTEM_CATALOG.get(canon_sys) or RETRO_SYSTEM_CATALOG["nes"]
        platform_qid = smeta.get("wikidata_qid") or "Q172742"
        gmeta = GAME_GENRE_CATALOG.get(canon_genre) if canon_genre else None
        genre_qid = gmeta.get("wikidata_qid") if gmeta else None
        genre_qids: List[str] = (
            list(gmeta.get("wikidata_qids") or ([genre_qid] if genre_qid else []))
            if gmeta
            else []
        )
        excluded_genre_qids: List[str] = (
            list(gmeta.get("wikidata_excluded_qids") or []) if gmeta else []
        )

        if genre_qids:
            values_str = " ".join(f"wd:{q}" for q in genre_qids if q)
            genre_clause = (
                f"VALUES ?targetGenre {{ {values_str} }} ?game wdt:P136/wdt:P279* ?targetGenre . "
            )
        else:
            genre_clause = ""

        if excluded_genre_qids:
            excl_str = " ".join(f"wd:{q}" for q in excluded_genre_qids if q)
            exclude_clause = (
                f"FILTER NOT EXISTS {{ VALUES ?exclGenre {{ {excl_str} }} "
                "?game wdt:P136/wdt:P279* ?exclGenre . } "
            )
        else:
            exclude_clause = ""

        sparql = (
            "SELECT DISTINCT ?game ?titleFr ?titleEn ?year ?frTitle ?enTitle WHERE { "
            f"?game wdt:P31/wdt:P279* wd:Q7889 ; wdt:P400 wd:{platform_qid} . "
            f"{genre_clause}"
            f"{exclude_clause}"
            "OPTIONAL { ?game wdt:P577 ?pubDate . BIND(YEAR(?pubDate) AS ?year) } "
            "OPTIONAL { ?game rdfs:label ?titleFr . FILTER(LANG(?titleFr) = 'fr') } "
            "OPTIONAL { ?game rdfs:label ?titleEn . FILTER(LANG(?titleEn) = 'en') } "
            "OPTIONAL { ?frArticle schema:about ?game ; schema:isPartOf <https://fr.wikipedia.org/> ; schema:name ?frTitle . } "
            "OPTIONAL { ?enArticle schema:about ?game ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?enTitle . } "
            "} LIMIT 250"
        )

        games_by_qid: Dict[str, Dict[str, Any]] = {}
        try:
            resp = await client.get(
                "https://query.wikidata.org/sparql",
                params={"format": "json", "query": sparql},
                headers={"Accept": "application/sparql-results+json"},
                timeout=7.0,
            )
            if resp.status_code == 200:
                bindings = (resp.json().get("results") or {}).get("bindings") or []
                for b in bindings:
                    uri = str(((b.get("game") or {}).get("value")) or "")
                    qid = uri.rsplit("/", 1)[-1] if "/" in uri else uri
                    if not qid.startswith("Q"):
                        continue
                    title_en = str(((b.get("titleEn") or {}).get("value")) or "").strip()
                    title_fr = str(((b.get("titleFr") or {}).get("value")) or "").strip()
                    if not title_en and not title_fr:
                        continue
                    if re.fullmatch(r"Q\d+", title_en or title_fr):
                        continue
                    if canon_genre == "fighting" and (
                        is_disallowed_non_fighting_title(title_en)
                        or is_disallowed_non_fighting_title(title_fr)
                    ):
                        continue
                    yr_raw = str(((b.get("year") or {}).get("value")) or "").strip()
                    yr = yr_raw[:4] if len(yr_raw) >= 4 and yr_raw[:4].isdigit() else ""
                    fr_wiki = str(((b.get("frTitle") or {}).get("value")) or "").strip()
                    en_wiki = str(((b.get("enTitle") or {}).get("value")) or "").strip()

                    existing = games_by_qid.get(qid)
                    if existing is None:
                        games_by_qid[qid] = {
                            "qid": qid,
                            "system": canon_sys,
                            "genre": canon_genre or "action",
                            "title_en": title_en or title_fr,
                            "title_fr": title_fr or title_en,
                            "year": yr,
                            "fr_wiki": fr_wiki,
                            "en_wiki": en_wiki,
                        }
                    else:
                        if yr and (not existing["year"] or yr < existing["year"]):
                            existing["year"] = yr
                        if fr_wiki and not existing["fr_wiki"]:
                            existing["fr_wiki"] = fr_wiki
                        if en_wiki and not existing["en_wiki"]:
                            existing["en_wiki"] = en_wiki
        except Exception as exc:
            logger.debug(
                "[REFERENTIAL:wikidata] SPARQL query skipped for %s/%s: %s",
                canon_sys,
                canon_genre,
                exc,
            )

        if not games_by_qid:
            target_qids = genre_qids if genre_qids else [None]
            for g_qid in target_qids:
                await self._harvest_via_wikidata_cirrus_api(
                    client,
                    canon_sys=canon_sys,
                    canon_genre=canon_genre,
                    platform_qid=platform_qid,
                    genre_qid=g_qid,
                    excluded_genre_qids=excluded_genre_qids,
                    games_by_qid=games_by_qid,
                )
                if len(games_by_qid) >= 60:
                    break

        harvested = list(games_by_qid.values())
        if harvested:
            by_bucket[cache_bucket] = harvested
            tokens = [f"{canon_sys}:{g['qid']}" for g in harvested]
            self.store.save(
                tokens=tokens,
                metadata={
                    "schema_version": self.SCHEMA_VERSION,
                    "by_system_genre": by_bucket,
                },
            )
            logger.info(
                "[REFERENTIAL:wikidata] Harvested %d verified '%s' games for genre '%s'",
                len(harvested),
                canon_sys,
                canon_genre or "all",
            )
        return harvested

    async def _harvest_via_wikidata_cirrus_api(
        self,
        client: Any,
        canon_sys: str,
        canon_genre: str,
        platform_qid: str,
        genre_qid: Optional[str],
        excluded_genre_qids: List[str],
        games_by_qid: Dict[str, Dict[str, Any]],
    ) -> None:
        sr_parts = [
            "haswbstatement:P31=Q7889",
            f"haswbstatement:P400={platform_qid}",
        ]
        if genre_qid:
            sr_parts.append(f"haswbstatement:P136={genre_qid}")
        for excl_qid in excluded_genre_qids:
            if excl_qid:
                sr_parts.append(f"-haswbstatement:P136={excl_qid}")
        srsearch = " ".join(sr_parts)
        try:
            s_resp = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": srsearch,
                    "srlimit": "50",
                    "format": "json",
                },
                timeout=6.5,
            )
            if s_resp.status_code != 200:
                return
            search_hits = ((s_resp.json().get("query") or {}).get("search")) or []
            qids = [
                str(hit.get("title") or "").strip()
                for hit in search_hits
                if str(hit.get("title") or "").strip().startswith("Q")
            ][:50]
            if not qids:
                return

            e_resp = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(qids),
                    "props": "labels|claims|sitelinks",
                    "languages": "fr|en",
                    "sitefilter": "frwiki|enwiki",
                    "format": "json",
                },
                timeout=6.5,
            )
            if e_resp.status_code != 200:
                return
            entities = (e_resp.json().get("entities")) or {}
            excluded_set = set(excluded_genre_qids)
            for qid, ent in entities.items():
                if not isinstance(ent, dict):
                    continue
                claims = ent.get("claims") or {}
                p400 = claims.get("P400") or []
                if p400:
                    claimed_platforms = {
                        str(
                            (
                                ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                                or {}
                            ).get("id")
                            or ""
                        )
                        for c in p400
                        if isinstance(c, dict)
                    }
                    if claimed_platforms and platform_qid not in claimed_platforms:
                        continue
                if excluded_set:
                    p136 = claims.get("P136") or []
                    claimed_genres = {
                        str(
                            (
                                ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                                or {}
                            ).get("id")
                            or ""
                        )
                        for c in p136
                        if isinstance(c, dict)
                    }
                    if claimed_genres & excluded_set:
                        continue

                labels = ent.get("labels") or {}
                title_fr = str(((labels.get("fr") or {}).get("value")) or "").strip()
                title_en = str(((labels.get("en") or {}).get("value")) or "").strip()
                if not title_en and not title_fr:
                    continue
                if re.fullmatch(r"Q\d+", title_en or title_fr):
                    continue
                if canon_genre == "fighting" and (
                    is_disallowed_non_fighting_title(title_en)
                    or is_disallowed_non_fighting_title(title_fr)
                ):
                    continue
                sitelinks = ent.get("sitelinks") or {}
                fr_wiki = str(((sitelinks.get("frwiki") or {}).get("title")) or "").strip()
                en_wiki = str(((sitelinks.get("enwiki") or {}).get("title")) or "").strip()

                yr = ""
                p577 = claims.get("P577") or []
                for claim in p577:
                    tval = (
                        (
                            ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                            or {}
                        ).get("time")
                        if isinstance(claim, dict)
                        else None
                    )
                    if isinstance(tval, str):
                        m = re.search(r"(\d{4})", tval)
                        if m:
                            cand_yr = m.group(1)
                            if not yr or cand_yr < yr:
                                yr = cand_yr

                games_by_qid[qid] = {
                    "qid": qid,
                    "system": canon_sys,
                    "genre": canon_genre or "action",
                    "title_en": title_en or title_fr,
                    "title_fr": title_fr or title_en,
                    "year": yr,
                    "fr_wiki": fr_wiki,
                    "en_wiki": en_wiki,
                }
        except Exception as exc:
            logger.debug(
                "[REFERENTIAL:wikidata] Cirrus API fallback skipped for %s/%s: %s",
                canon_sys,
                canon_genre,
                exc,
            )

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()
        await self.harvest_wikidata_games(client, "nes", "platform")
        return self.store.load()

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref = await self.sync_referential(client, force=False)
        return list(ref.get("tokens") or [])[:count]
