"""
Generic Synonym Catalog Resolver & Canonical System/Genre Resolution (`app/domain/taxonomy/resolver.py`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable

from app.domain.entities import DomainType
from app.domain.taxonomy.cinema_genres import (
    ANIME_GENRE_CATALOG,
    MOVIE_GENRE_CATALOG,
    SERIES_GENRE_CATALOG,
)
from app.domain.taxonomy.common import normalize_category_slug
from app.domain.taxonomy.game_genres import GAME_GENRE_CATALOG
from app.domain.taxonomy.systems import RETRO_SYSTEM_CATALOG

_GAME_TITLE_PARENS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


class SynonymCatalogResolver:
    """
    Generic, reusable slug-indexed synonym resolver for any canonical taxonomy catalog.
    Supports exact slug lookups and longest-substring fallback matching.
    """

    def __init__(
        self,
        catalog: Dict[str, Dict[str, Any]],
        synonym_fields: Iterable[str] = ("synonyms",),
        include_labels: bool = False,
    ) -> None:
        self.index: Dict[str, str] = {}
        for canonical_id, meta in catalog.items():
            self.index[canonical_id] = canonical_id
            self.index[normalize_category_slug(canonical_id)] = canonical_id
            for field_name in synonym_fields:
                for token in meta.get(field_name) or []:
                    slug = normalize_category_slug(token)
                    if slug:
                        self.index[slug] = canonical_id
            if include_labels:
                for lbl in (meta.get("labels") or {}).values():
                    slug = normalize_category_slug(lbl)
                    if slug:
                        self.index[slug] = canonical_id
        self._sorted_pairs = sorted(
            self.index.items(), key=lambda kv: len(kv[0]), reverse=True
        )

    def resolve_exact(self, raw_token: str) -> str:
        slug = normalize_category_slug(raw_token)
        return self.index.get(slug, "")

    def resolve_with_substring_fallback(self, raw_token: str, min_sub_len: int = 4) -> str:
        slug = normalize_category_slug(raw_token)
        if slug in self.index:
            return self.index[slug]
        for syn_slug, canonical_id in self._sorted_pairs:
            if syn_slug and len(syn_slug) >= min_sub_len and syn_slug in slug:
                return canonical_id
        return slug


SYSTEM_RESOLVER = SynonymCatalogResolver(
    RETRO_SYSTEM_CATALOG, synonym_fields=("synonyms",), include_labels=False
)

GENRE_RESOLVERS_BY_DOMAIN: Dict[DomainType, SynonymCatalogResolver] = {
    "games": SynonymCatalogResolver(
        GAME_GENRE_CATALOG,
        synonym_fields=("synonyms", "adb_keywords", "screenscraper_keywords"),
        include_labels=True,
    ),
    "movies": SynonymCatalogResolver(
        MOVIE_GENRE_CATALOG, synonym_fields=("synonyms",), include_labels=True
    ),
    "series": SynonymCatalogResolver(
        SERIES_GENRE_CATALOG, synonym_fields=("synonyms",), include_labels=True
    ),
    "anime": SynonymCatalogResolver(
        ANIME_GENRE_CATALOG, synonym_fields=("synonyms",), include_labels=True
    ),
}

_PASSTHROUGH_UNLISTED_SYSTEM_SLUGS = frozenset(
    {
        "mega_cd",
        "sega_cd",
        "sega_mega_cd",
        "sega_32x",
        "32x",
        "mega_drive_32x",
        "genesis_32x",
        "playstation_2",
        "sony_playstation_2",
        "ps2",
    }
)


def resolve_canonical_system(raw_system: str) -> str:
    """Resolve any scraper or user system token (`NES`, `Famicom`, `Genesis`) to its canonical ID."""
    slug = normalize_category_slug(raw_system)
    if slug in _PASSTHROUGH_UNLISTED_SYSTEM_SLUGS:
        return slug
    exact = SYSTEM_RESOLVER.resolve_exact(slug)
    return exact if exact else slug


def resolve_canonical_genre(domain: DomainType, raw_genre: str) -> str:
    """Resolve any scraper or user genre token across languages to its canonical domain genre ID."""
    slug = normalize_category_slug(raw_genre)
    resolver = GENRE_RESOLVERS_BY_DOMAIN.get(domain)
    if resolver is not None:
        exact = resolver.resolve_exact(slug)
        if exact:
            return exact

    if domain == "games" and slug:
        if (
            "lightgun" in slug
            or "shooter_gun" in slug
            or "rail_shooter" in slug
            or "gun_shooter" in slug
            or "tir_au_pistolet" in slug
        ):
            return "lightgun"
        if ("shoot" in slug or "shmup" in slug or "flying" in slug or "tir" in slug) and (
            "platform" in slug or "plateforme" in slug or "plattform" in slug or "walking" in slug
        ):
            return "runandgun"
        if "shoot" in slug or "shmup" in slug or "flying" in slug or "gallery" in slug:
            return "shootemup"
        if ("fighter" in slug or "combat" in slug or "fighting" in slug) and (
            "platform" in slug
            or "plateforme" in slug
            or "plattform" in slug
            or "piattaforma" in slug
            or "plataforma" in slug
        ):
            return "action"
        if (
            "beat_em" in slug
            or "beatem" in slug
            or "brawler" in slug
            or "hack_and_slash" in slug
            or "scorrimento" in slug
            or "yo_contra_el_barrio" in slug
        ):
            return "beatemup"
        if (
            ("fighter" in slug or "combat" in slug or "fighting" in slug)
            and "versus" not in slug
            and any(
                tok in slug
                for tok in (
                    "2_5d",
                    "field",
                    "vertical",
                    "misc",
                    "scrolling",
                    "defilement",
                    "coop",
                    "co_op",
                    "champ",
                    "rue",
                )
            )
        ):
            return "beatemup"
        if ("fighter" in slug or "combat" in slug or "fighting" in slug or "lucha" in slug) and any(
            tok in slug for tok in ("versus", "1v1", "2d", "3d")
        ):
            return "fighting"

    if resolver is not None:
        return resolver.resolve_with_substring_fallback(slug, min_sub_len=4)
    return slug


def is_arcade_system(sys_key: str) -> bool:
    """Return True if `sys_key` is an Arcade/MAME system supported by ArcadeItalia."""
    canon = resolve_canonical_system(sys_key)
    meta = RETRO_SYSTEM_CATALOG.get(canon)
    if meta is not None:
        return bool(meta.get("is_arcade", False))
    return canon in ("arcade", "mame", "neogeo", "cps1", "cps2", "cps3", "fbneo")


def build_game_fingerprint(system: str, title: str) -> str:
    """
    Build a cross-source canonical game fingerprint (`games_fp_{system}:{title_slug}`)
    stripping regional/revision parentheticals while preserving sequel numbers and subtitles.
    """
    canon_sys = resolve_canonical_system(system) if system else "games"
    cleaned = _GAME_TITLE_PARENS_RE.sub("", str(title or "")).strip()
    lower = cleaned.lower()
    if lower.endswith(", the"):
        cleaned = cleaned[:-5].strip()
    elif lower.startswith("the "):
        cleaned = cleaned[4:].strip()
    slug = normalize_category_slug(cleaned)
    if not slug:
        return ""
    return f"games_fp_{canon_sys}:{slug}"
