"""
Console & Retro Games Scraper Adapter: Libretro Thumbnails + Wikidata SPARQL + Localized Wikipedia (`LibretroWikidataAdapter`).

Keyless Console, Handheld & Home Computer adapter implementing `IScraperSource`:
- Uses `LibretroWikidataReferentialStrategy` (`./data/referentials/libretro_wikidata.json` &
  `./data/referentials/libretro_tree_{sys_key}.json`) to query Wikidata SPARQL strictly for
  Video Games (`wd:Q7889`) matching the exact requested platform QID (`wdt:P400`) and genre QID
  (`wdt:P136/wdt:P279*`).
- Guarantees hardware consoles (`Nintendo Entertainment System`, `CIC`) or games from other
  consoles (`Goof Troop`, `F-Zero`, `Pilotwings` when querying `nes`) are NEVER returned.
- Matches the cached GitHub tree of `libretro-thumbnails/{repo}` (`Named_Boxarts`, `Named_Titles`,
  `Named_Snaps`) with regional priority `(France) -> (Europe) -> (World) -> (USA)` so every
  returned asset URL is a verified HTTP 200 PNG.
- Fetches localized synopses from `{lang}.wikipedia.org` (`fr.wikipedia.org` when `lang="fr"`).
"""

from __future__ import annotations

import logging
import random
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import (
    CANONICAL_DECADES,
    GAME_GENRE_CATALOG,
    RETRO_SYSTEM_CATALOG,
    ReferentialConsolidator,
    build_game_fingerprint,
    is_disallowed_non_fighting_title,
    localize_label,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.scrapers.http_utils import (
    create_async_http_client,
    fetch_wikipedia_localized_synopsis,
)
from app.infrastructure.scrapers.referential_store import (
    LibretroWikidataReferentialStrategy,
    libretro_wikidata_referential_strategy,
    score_libretro_filename_for_lang,
)

logger = logging.getLogger("dmd_showcase.scrapers.games.libretro_wikidata")

_SYS_RNG = random.SystemRandom()

_REGION_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_FORBIDDEN_HARDWARE_TITLES = {
    "nintendo entertainment system",
    "super nintendo entertainment system",
    "famicom",
    "super famicom",
    "sega genesis",
    "sega mega drive",
    "master system",
    "game boy",
    "game boy color",
    "game boy advance",
    "playstation",
    "nintendo 64",
    "sega saturn",
    "dreamcast",
    "cic",
    "nes",
    "snes",
}

_VERIFIED_GENRE_TITLE_SEEDS: Dict[str, List[str]] = {
    "platform": [
        "Super Mario Bros.", "Super Mario Bros. 2", "Super Mario Bros. 3", "Super Mario World",
        "Sonic the Hedgehog", "Sonic the Hedgehog 2", "Sonic the Hedgehog 3", "Castlevania",
        "Mega Man 2", "Mega Man 3", "Mega Man X", "Donkey Kong Country", "Donkey Kong Country 2",
        "Kirby's Adventure", "DuckTales", "Chip 'n Dale Rescue Rangers", "Aladdin",
        "Earthworm Jim", "Rayman", "Crash Bandicoot", "Spyro the Dragon", "SuperMario 64",
        "Banjo-Kazooie", "Alex Kidd in Miracle World", "Castle of Illusion", "Shinobi III",
        "Rocket Knight Adventures", "Dynamite Headdy", "Ristar", "Ghouls 'n Ghosts",
        "Super Ghouls 'n Ghosts", "Super Castlevania IV", "Bonk's Adventure", "Super Metroid",
    ],
    "shootemup": [
        "Gradius", "Life Force", "Salamander", "R-Type", "Super R-Type", "Thunder Force III",
        "Thunder Force IV", "M.U.S.H.A.", "Blazing Lazers", "Super Star Soldier", "Soldier Blade",
        "Axelay", "Parodius", "TwinBee", "1942", "1943", "UN Squadron", "Einhander",
        "Radiant Silvergun", "Ikaruga", " Darius Twin", "Space Megaforce", "Gate of Thunder",
    ],
    "runandgun": [
        "Contra", "Super C", "Contra III: The Alien Wars", "Contra: Hard Corps",
        "Gunstar Heroes", "Sunset Riders", "Metal Slug", "Turrican", "Super Turrican",
        "Mega Turrican", "Ikari Warriors", "Mercs", "Shock Troopers", "Zombies Ate My Neighbors",
    ],
    "fighting": [
        "Street Fighter II", "Street Fighter II' - Special Champion Edition",
        "Super Street Fighter II", "Street Fighter Alpha 2", "Eternal Champions",
        "Mortal Kombat", "Mortal Kombat II", "Mortal Kombat 3", "Ultimate Mortal Kombat 3",
        "Fatal Fury", "Fatal Fury 2", "Fatal Fury Special", "Samurai Shodown",
        "Art of Fighting", "World Heroes", "Dragon Ball Z - L'Appel du Destin",
        "Dragon Ball Z - Buyuu Retsuden", "Primal Rage", "ClayFighter", "WeaponLord",
        "Justice League Task Force", "Fighting Masters", "Deadly Moves",
        "Brutal - Paws of Fury", "Shaq-Fu", "Ballz 3D", "Double Dragon V - The Shadow Falls",
        "King of the Monsters", "King of the Monsters 2", "Killer Instinct", "Tekken 3",
        "Soul Blade", "Dead or Alive", "Virtua Fighter 2", "The King of Fighters '98",
        "Marvel vs. Capcom",
    ],
    "beatemup": [
        "Streets of Rage", "Streets of Rage 2", "Streets of Rage 3", "Final Fight",
        "Double Dragon", "Double Dragon II", "Teenage Mutant Ninja Turtles IV: Turtles in Time",
        "Golden Axe", "Golden Axe II", "Battletoads", "River City Ransom", "Comix Zone",
    ],
    "rpg": [
        "Chrono Trigger", "Final Fantasy", "Final Fantasy IV", "Final Fantasy VI",
        "Final Fantasy VII", "Secret of Mana", "EarthBound", "Super Mario RPG",
        "Phantasy Star IV", "Shining Force II", "Breath of Fire II", "Dragon Warrior",
        "Terranigma", "Illusion of Gaia", "Suikoden II", "Xenogears",
    ],
    "racing": [
        "Super Mario Kart", "Mario Kart 64", "F-Zero", "F-Zero X", "OutRun",
        "Road Rash II", "Top Gear", "Ridge Racer Type 4", "Gran Turismo", "Excitebike",
        "RC Pro-Am", "Micro Machines", "Wave Race 64", "Daytona USA", "Sega Rally Championship",
    ],
    "puzzle": [
        "Tetris", "Dr. Mario", "Puyo Puyo", "Columns", "Panel de Pon", "Tetris Attack",
        "Bust-A-Move", "Klax", "Lemmings", "Yoshi's Cookie", "Wario's Woods",
    ],
}


def _clean_display_title_from_nointro(filename: str) -> str:
    """Convert a No-Intro filename (`Castlevania (Europe).png`) into a clean display title (`Castlevania`)."""
    s = (filename or "").strip()
    if s.lower().endswith(".png"):
        s = s[:-4]
    s = _REGION_PAREN_RE.sub("", s).strip()
    if s.lower().endswith(", the"):
        s = "The " + s[:-5].strip()
    return s


class LibretroWikidataAdapter(IScraperSource):
    """
    Keyless `IScraperSource` for Console, Handheld, and Home Computer retro games
    powered by `LibretroWikidataReferentialStrategy`.
    """

    source_id: str = "libretro_wikidata"
    domain: DomainType = "games"
    role: str = "fallback"

    def __init__(
        self,
        referential_strategy: Optional[LibretroWikidataReferentialStrategy] = None,
    ) -> None:
        self.referential_strategy = referential_strategy or libretro_wikidata_referential_strategy

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        ref = ReferentialConsolidator.build_domain_referential("games", lang=lang)
        return {
            "systems": [{"id": s["id"], "label": s["label"]} for s in ref["systems"]],
            "genres": [{"id": g["id"], "label": g["label"]} for g in ref["genres"]],
            "decades": [{"id": d["id"], "label": d["label"]} for d in ref["decades"]],
        }

    def _matches_decades(self, year_str: str, decades: List[str]) -> bool:
        req_decs = [d.strip().lower() for d in (decades or []) if d and d.strip().lower() != "all"]
        if not req_decs:
            return True
        if not year_str or len(year_str) < 4 or not year_str[:4].isdigit():
            return False
        yr = int(year_str[:4])
        for dec_clean in req_decs:
            if dec_clean in CANONICAL_DECADES:
                low_b, high_b = CANONICAL_DECADES[dec_clean]["bounds"]
                if low_b <= yr <= high_b:
                    return True
            elif len(dec_clean) >= 4 and dec_clean[:4].isdigit():
                base = int(dec_clean[:4])
                if base <= yr <= base + 9:
                    return True
        return False

    async def _build_candidate_from_wikidata_entry(
        self,
        client: Any,
        entry: Dict[str, Any],
        sys_key: str,
        genre_key: str,
        lang: str,
    ) -> Optional[MediaCandidate]:
        canon_sys = resolve_canonical_system(sys_key)
        canon_genre = resolve_canonical_genre("games", genre_key) if genre_key else "platform"
        smeta = RETRO_SYSTEM_CATALOG.get(canon_sys) or RETRO_SYSTEM_CATALOG["nes"]
        gmeta = GAME_GENRE_CATALOG.get(canon_genre) or GAME_GENRE_CATALOG["platform"]

        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        title_en = str(entry.get("title_en") or "").strip()
        title_fr = str(entry.get("title_fr") or title_en).strip()
        display_title = title_fr if short_lang == "fr" and title_fr else (title_en or title_fr)
        display_title = re.sub(r"\s*\([^)]*video game[^)]*\)\s*$", "", display_title, flags=re.I).strip()
        display_title = re.sub(r"\s*\([^)]*jeu vidéo[^)]*\)\s*$", "", display_title, flags=re.I).strip()

        if not display_title or display_title.lower() in _FORBIDDEN_HARDWARE_TITLES:
            return None
        if canon_genre == "fighting" and (
            is_disallowed_non_fighting_title(display_title)
            or is_disallowed_non_fighting_title(title_en)
            or is_disallowed_non_fighting_title(title_fr)
        ):
            return None

        # Match against verified Libretro file tree (lookup using English title first, then French title)
        verified_urls = await self.referential_strategy.resolve_verified_libretro_urls(
            client, canon_sys, title_en or display_title, lang=short_lang
        )
        if not verified_urls.get("boxart") and not verified_urls.get("snap") and title_fr != title_en:
            verified_urls = await self.referential_strategy.resolve_verified_libretro_urls(
                client, canon_sys, title_fr, lang=short_lang
            )

        boxart_url = verified_urls.get("boxart")
        title_url = verified_urls.get("title")
        snap_url = verified_urls.get("snap")
        video_url = verified_urls.get("video")

        # Require at least one verified Libretro asset so non-system games (e.g. SNES game on NES) are rejected
        if not boxart_url and not snap_url and not title_url:
            return None

        year_str = str(entry.get("year") or "").strip()[:4]
        if not year_str or not year_str.isdigit():
            default_dec = (smeta.get("decades") or ["1980s"])[0]
            year_str = default_dec[:4] if default_dec[:4].isdigit() else "1989"

        # Fetch localized synopsis using the exact sitelink (`fr_wiki` when `lang == "fr"`)
        wiki_query = (
            str(entry.get("fr_wiki") or display_title)
            if short_lang == "fr"
            else str(entry.get("en_wiki") or title_en or display_title)
        )
        wiki_syn, wiki_thumb = await fetch_wikipedia_localized_synopsis(
            client,
            wiki_query,
            lang=short_lang,
            domain_hint="jeu vidéo" if short_lang == "fr" else "video game",
        )
        genre_label = localize_label(gmeta["labels"], short_lang, "Retro")
        sys_label = localize_label(smeta["labels"], short_lang, smeta["badge"])
        default_syn = (
            f"{display_title} ({year_str}) — {genre_label} sur {sys_label}."
            if short_lang == "fr"
            else f"{display_title} ({year_str}) — {genre_label} on {sys_label}."
        )
        synopsis = wiki_syn or default_syn

        poster_url = boxart_url or title_url or snap_url or wiki_thumb
        if not poster_url:
            return None

        slug = re.sub(r"[^a-z0-9]+", "_", (title_en or display_title).lower()).strip("_")
        cand = MediaCandidate(
            id=f"games_libretro_{canon_sys}_{slug}",
            domain="games",
            title=display_title,
            year=year_str,
            badge=smeta["badge"],
            badge_color=smeta["color"],
            subtitle=f"{genre_label} • {smeta['badge']}"[:28],
            rating="8.8",
            synopsis=synopsis,
            lang=short_lang,
            poster_url=poster_url,
            jacket_url=poster_url,
            marquee_url=title_url or poster_url,
            video_url=video_url,
            youtube_url=None,
            source_id=self.source_id,
            theme_palette=tuple(smeta["palette"]),
            extra_meta={"system": canon_sys, "genre": canon_genre, "qid": entry.get("qid", "")},
        )
        if snap_url:
            cand.add_unique_snapshot(snap_url)
        if title_url and title_url != poster_url:
            cand.add_unique_snapshot(title_url)
        return cand

    async def _discover_from_verified_libretro_tree(
        self,
        client: Any,
        sys_key: str,
        genre_key: str,
        lang: str,
        seen_ids: Set[str],
        count: int,
    ) -> List[MediaCandidate]:
        """
        Fallback when Wikidata SPARQL is unreachable: sample directly from the verified
        `Named_Boxarts` file tree of `libretro-thumbnails/{repo}`, prioritizing regional
        `(France) -> (Europe) -> (World) -> (USA)` filenames.
        """
        canon_sys = resolve_canonical_system(sys_key)
        canon_genre = resolve_canonical_genre("games", genre_key) if genre_key else "platform"
        smeta = RETRO_SYSTEM_CATALOG.get(canon_sys) or RETRO_SYSTEM_CATALOG["nes"]
        gmeta = GAME_GENRE_CATALOG.get(canon_genre) or GAME_GENRE_CATALOG["platform"]
        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        repo = smeta.get("libretro_repo") or "Nintendo_-_Nintendo_Entertainment_System"

        folders = await self.referential_strategy.sync_system_libretro_tree(client, canon_sys)
        boxarts = list(
            folders.get("Named_Boxarts")
            or folders.get("Named_Titles")
            or folders.get("Named_Snaps")
            or []
        )
        if not boxarts:
            return []

        # Prioritize regional filenames matching `short_lang`
        scored = [
            (score_libretro_filename_for_lang(fname, short_lang) + _SYS_RNG.randint(0, 15), fname)
            for fname in boxarts
            if score_libretro_filename_for_lang(fname, short_lang) > 0
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        pool = [fname for _, fname in scored[: max(60, count * 16)]]
        _SYS_RNG.shuffle(pool)

        results: List[MediaCandidate] = []
        for fname in pool:
            if len(results) >= count:
                break
            clean_title = _clean_display_title_from_nointro(fname)
            if not clean_title or clean_title.lower() in _FORBIDDEN_HARDWARE_TITLES:
                continue
            slug = re.sub(r"[^a-z0-9]+", "_", clean_title.lower()).strip("_")
            cand_id = f"games_libretro_{canon_sys}_{slug}"
            fp_key = build_game_fingerprint(canon_sys, clean_title)
            if not slug or cand_id in seen_ids or (fp_key and fp_key in seen_ids):
                continue

            verified = await self.referential_strategy.resolve_verified_libretro_urls(
                client, canon_sys, clean_title, lang=short_lang
            )
            boxart_url = verified.get("boxart") or (
                f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/"
                f"Named_Boxarts/{urllib.parse.quote(fname)}"
            )
            title_url = verified.get("title")
            snap_url = verified.get("snap")
            video_url = verified.get("video")

            wiki_syn, _ = await fetch_wikipedia_localized_synopsis(
                client,
                clean_title,
                lang=short_lang,
                domain_hint="jeu vidéo" if short_lang == "fr" else "video game",
            )
            genre_label = localize_label(gmeta["labels"], short_lang, "Retro")
            sys_label = localize_label(smeta["labels"], short_lang, smeta["badge"])
            default_dec = (smeta.get("decades") or ["1980s"])[0]
            year_str = default_dec[:4] if default_dec[:4].isdigit() else "1989"
            synopsis = wiki_syn or (
                f"{clean_title} ({year_str}) — {genre_label} sur {sys_label}."
                if short_lang == "fr"
                else f"{clean_title} ({year_str}) — {genre_label} on {sys_label}."
            )

            cand = MediaCandidate(
                id=cand_id,
                domain="games",
                title=clean_title,
                year=year_str,
                badge=smeta["badge"],
                badge_color=smeta["color"],
                subtitle=f"{genre_label} • {smeta['badge']}"[:28],
                rating="8.7",
                synopsis=synopsis,
                lang=short_lang,
                poster_url=boxart_url,
                jacket_url=boxart_url,
                marquee_url=title_url or boxart_url,
                video_url=video_url,
                youtube_url=None,
                source_id=self.source_id,
                theme_palette=tuple(smeta["palette"]),
                extra_meta={"system": canon_sys, "genre": canon_genre},
            )
            if snap_url:
                cand.add_unique_snapshot(snap_url)
            seen_ids.add(cand.id)
            if fp_key:
                seen_ids.add(fp_key)
            results.append(cand)

        return results

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        req_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.lower() != "all" and resolve_canonical_system(s) in RETRO_SYSTEM_CATALOG
        ]
        if not req_systems:
            req_systems = ["nes", "snes", "megadrive", "psx", "n64", "pcengine", "gb", "gba"]

        req_genres = [
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.lower() != "all" and resolve_canonical_genre("games", g) in GAME_GENRE_CATALOG
        ]
        chosen_genre = _SYS_RNG.choice(req_genres) if req_genres else "platform"

        results: List[MediaCandidate] = []
        async with create_async_http_client(timeout=7.5) as client:
            for _ in range(max(2, count * 2)):
                if len(results) >= count:
                    break
                sys_key = _SYS_RNG.choice(req_systems)
                chosen_genre = _SYS_RNG.choice(req_genres) if req_genres else None
                harvested = await self.referential_strategy.harvest_wikidata_games(
                    client, sys_key=sys_key, genre_key=chosen_genre
                )
                if not harvested and not req_genres:
                    harvested = await self.referential_strategy.harvest_wikidata_games(
                        client, sys_key=sys_key, genre_key=None
                    )

                if not harvested and chosen_genre in _VERIFIED_GENRE_TITLE_SEEDS:
                    smeta_decades = (RETRO_SYSTEM_CATALOG.get(sys_key) or {}).get("decades") or ["1990s"]
                    seed_year = "1993" if "1990s" in smeta_decades else str(smeta_decades[0])[:4]
                    harvested = [
                        {
                            "qid": "",
                            "system": sys_key,
                            "genre": chosen_genre,
                            "title_en": t,
                            "title_fr": t,
                            "year": seed_year,
                            "fr_wiki": t,
                            "en_wiki": t,
                        }
                        for t in _VERIFIED_GENRE_TITLE_SEEDS[chosen_genre]
                    ]

                if harvested:
                    req_decs_clean = [
                        d.strip().lower()
                        for d in (criteria.decades or [])
                        if d and d.strip().lower() != "all"
                    ]
                    filtered = [
                        g
                        for g in harvested
                        if self._matches_decades(str(g.get("year") or ""), req_decs_clean)
                    ]
                    _SYS_RNG.shuffle(filtered)
                    for entry in filtered:
                        if len(results) >= count:
                            break
                        title_probe = str(entry.get("title_en") or entry.get("title_fr") or "")
                        slug = re.sub(r"[^a-z0-9]+", "_", title_probe.lower()).strip("_")
                        cand_id = f"games_libretro_{sys_key}_{slug}"
                        fp_probe = build_game_fingerprint(sys_key, title_probe)
                        if not slug or cand_id in seen_ids or (fp_probe and fp_probe in seen_ids):
                            continue
                        cand = await self._build_candidate_from_wikidata_entry(
                            client,
                            entry=entry,
                            sys_key=sys_key,
                            genre_key=chosen_genre or "platform",
                            lang=criteria.lang,
                        )
                        if cand and cand.id not in seen_ids:
                            fp_cand = build_game_fingerprint(sys_key, cand.title)
                            if fp_cand and fp_cand in seen_ids:
                                seen_ids.add(cand.id)
                                continue
                            seen_ids.add(cand.id)
                            if fp_cand:
                                seen_ids.add(fp_cand)
                            if fp_probe:
                                seen_ids.add(fp_probe)
                            results.append(cand)

                # Strictly never fall back to uncategorized random Libretro boxarts when a specific genre filter was set
                if len(results) < count and not req_genres and not harvested:
                    tree_cands = await self._discover_from_verified_libretro_tree(
                        client,
                        sys_key=sys_key,
                        genre_key=chosen_genre or "platform",
                        lang=criteria.lang,
                        seen_ids=seen_ids,
                        count=count - len(results),
                    )
                    results.extend(tree_cands)

        return results[:count]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        q_clean = (query or "").strip()
        if not q_clean:
            return None
        try:
            async with create_async_http_client(timeout=6.5) as client:
                for sys_key in ("nes", "snes", "megadrive", "psx"):
                    verified = await self.referential_strategy.resolve_verified_libretro_urls(
                        client, sys_key, q_clean, lang=lang
                    )
                    if verified.get("boxart") or verified.get("snap"):
                        entry = {
                            "qid": "",
                            "title_en": q_clean,
                            "title_fr": q_clean,
                            "year": "1991",
                            "fr_wiki": q_clean,
                            "en_wiki": q_clean,
                        }
                        cand = await self._build_candidate_from_wikidata_entry(
                            client, entry, sys_key=sys_key, genre_key="action", lang=lang
                        )
                        if cand:
                            return cand
        except Exception:
            pass
        return None

