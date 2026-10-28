"""
Live Referential Strategy for ArcadeItalia (`https://adb.arcadeitalia.net`).

Strictly dedicated to Arcade / MAME / NeoGeo / CPS systems, harvesting MAME `game_name`
ROM identifiers from `lista_mame.php` (HTTPS) and Wikidata `wdt:P2451`.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List, Optional, Set

from app.domain.entities import FilterCriteria
from app.domain.ports.referential_strategy import IScraperReferentialStrategy
from app.domain.taxonomy import resolve_canonical_genre, resolve_canonical_system
from app.infrastructure.scrapers.referential_utils import (
    _ADB_ROM_LINK_RE,
    _IGNORED_MAME_PREFIXES,
    random_search_syllable,
)

logger = logging.getLogger("dmd_showcase.scrapers.referential.arcadeitalia")

_SYS_RNG = random.SystemRandom()


class ArcadeItaliaReferentialStrategy(IScraperReferentialStrategy):
    """Harvests and samples verified MAME ROM tokens per arcade hardware & genre."""

    source_id = "arcadeitalia"

    _AUTHORITATIVE_SYSTEM_ROMS: Dict[str, List[str]] = {
        "cps1": [
            "sf2", "sf2ce", "sf2hf", "ffight", "dino", "punisher", "captcomm",
            "knights", "wof", "kod", "strider", "ghouls", "1941", "varth",
            "cawing", "mercs", "forgottn", "unsquad", "willow", "mtwins",
            "nemo", "megaman", "slammast", "mbombrd", "pnickj", "qad",
            "qtono2", "pang3", "3wonders",
        ],
        "cps2": [
            "ssf2", "ssf2t", "ssf2xj", "hsf2", "sfa", "sfa2", "sfa3",
            "msh", "vsav", "vhunt2", "vsav2", "nwarr", "dstlk", "xmcota",
            "xmvsf", "mshvsf", "mvsc", "cybots", "ringdest", "sgemf",
            "spf2t", "pzloop2", "mpang", "1944", "19xx", "gigawing",
            "progear", "marsmatr", "dimahoo", "ecofghtr", "avsp", "ddtod",
            "ddsom", "batcir", "armwar", "csclub", "megaman2", "qndream", "choko",
        ],
        "cps3": [
            "sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth",
        ],
        "neogeo": [
            "kof94", "kof95", "kof96", "kof97", "kof98", "kof99", "kof2000",
            "kof2001", "kof2002", "kof2003", "samsho", "samsho2", "samsho3",
            "samsho4", "samsho5", "samsho5sp", "mslug", "mslug2", "mslug3",
            "mslug4", "mslug5", "mslugx", "garou", "fatfury1", "fatfury2",
            "fatfury3", "rbff1", "rbff2", "rbffspec", "aof", "aof2", "aof3",
            "lastblad", "lastbld2", "wh1", "wh2", "wh2jet", "whp", "svc",
            "rotd", "matrim", "wakuwak7", "kabukikl", "breakers", "breakrev",
            "ninjamas", "kizuna", "savagere", "karnovr", "shocktro", "shocktr2",
            "sengoku", "sengoku2", "sengoku3", "pulstar", "blazstar", "viewpoin",
            "lastreso", "twinspri", "aerofig2", "aerofig3", "strik1945p", "preisle2",
            "zedblade", "androdu", "alpham2", "wjammers", "windjamm", "ssideki", "ssideki2",
            "ssideki3", "ssideki4", "turfmast", "neocup98", "2020bb", "strhoop",
            "tophuntr", "spinmast", "maglord", "bjourney", "cyberlip", "nam1975",
            "mutnat", "roboarmy", "burningf", "eightman", "pbobble", "pbobble2",
            "magicald", "magdrop2", "magdrop3", "puzzledp", "joyjoy", "moneyid",
            "gururin", "panicbom", "overtop", "neodrift", "thrashr",
        ],
        "naomi": [
            "mvsc2", "cvs2", "ikaruga", "ggxx", "vf4", "totd", "hotd2",
            "crazytax", "doa2", "pstone", "pstone2", "capvs", "bdrdown",
        ],
        "atomiswave": [
            "mslug6", "kofxi", "ngbc", "hokuto", "ggisuka", "dolphin", "demofist",
        ],
        "model2": [
            "daytona", "vf2", "vcop", "vcop2", "hotd", "srallyc", "fvipers", "doa", "zerogun",
        ],
        "model3": [
            "scud", "daytona2", "vf3", "srally2", "la_machin", "swtrilgy", "spikeout",
        ],
        "zn": [
            "sfex", "sfex2", "sfex2p", "rvschola", "strider2", "techromn", "plsmaswd", "ts2",
        ],
        "pgm": [
            "kov", "kov2", "kovsh", "dmnfrnt", "martmast", "orlegend", "ddp2", "svg", "ket",
        ],
    }

    _FORBIDDEN_ROMS_BY_SYSTEM: Dict[str, Set[str]] = {
        "cps1": {
            "mvsc", "mvsc2", "sfa", "sfa2", "sfa3", "ssf2", "ssf2t", "ssf2xj", "hsf2",
            "msh", "vsav", "vhunt2", "vsav2", "nwarr", "dstlk", "xmcota", "xmvsf", "mshvsf",
            "sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth", "cvs", "cvs2",
        },
        "cps2": {
            "mvsc2", "mvsc2u", "mvsc2j", "cvs", "cvs2", "cvsgd", "capcom_vs_snk",
            "sf2", "sf2ce", "sf2hf", "ffight", "dino", "punisher", "captcomm",
            "sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth",
            "sfex", "sfex2", "sfex2p", "sftm", "pstone", "pstone2", "techromn",
            "rvschola", "pjfghtr", "kof94", "kof95", "kof98", "mslug", "samsho",
        },
        "cps3": {
            "mvsc", "mvsc2", "sfa", "sfa2", "sfa3", "ssf2", "ssf2t", "sf2", "sf2ce",
            "cvs", "cvs2", "sfex", "sfex2",
        },
        "neogeo": {
            "sf2", "ssf2", "ssf2t", "sfa", "sfa2", "sfa3", "mvsc", "mvsc2", "sfiii",
        },
    }

    _SYSTEM_SEARCH_STEMS: Dict[str, List[str]] = {
        "neogeo": _AUTHORITATIVE_SYSTEM_ROMS["neogeo"],
        "cps1": _AUTHORITATIVE_SYSTEM_ROMS["cps1"],
        "cps2": _AUTHORITATIVE_SYSTEM_ROMS["cps2"],
        "cps3": _AUTHORITATIVE_SYSTEM_ROMS["cps3"],
        "naomi": _AUTHORITATIVE_SYSTEM_ROMS["naomi"],
        "atomiswave": _AUTHORITATIVE_SYSTEM_ROMS["atomiswave"],
        "model2": _AUTHORITATIVE_SYSTEM_ROMS["model2"],
        "model3": _AUTHORITATIVE_SYSTEM_ROMS["model3"],
        "zn": _AUTHORITATIVE_SYSTEM_ROMS["zn"],
        "pgm": _AUTHORITATIVE_SYSTEM_ROMS["pgm"],
    }

    _GENRE_SEARCH_STEMS: Dict[str, Dict[str, List[str]]] = {
        "fighting": {
            "neogeo": [
                "kof94", "kof95", "kof96", "kof97", "kof98", "kof99", "kof2000", "kof2001",
                "kof2002", "kof2003", "samsho", "samsho2", "samsho3", "samsho4", "samsho5",
                "samsho5sp", "garou", "fatfury1", "fatfury2", "fatfury3", "rbff1", "rbff2",
                "rbffspec", "aof", "aof2", "aof3", "lastblad", "lastbld2", "wh1", "wh2",
                "wh2jet", "whp", "svc", "rotd", "matrim", "wakuwak7", "kabukikl", "breakers",
                "breakrev", "ninjamas", "kizuna", "savagere", "karnovr",
            ],
            "cps1": ["sf2", "sf2ce", "sf2hf", "slammast", "mbombrd"],
            "cps2": [
                "ssf2", "ssf2t", "ssf2xj", "hsf2", "sfa", "sfa2", "sfa3",
                "msh", "vsav", "vhunt2", "vsav2", "nwarr", "dstlk",
                "xmcota", "xmvsf", "mshvsf", "mvsc", "cybots", "ringdest", "sgemf",
            ],
            "cps3": ["sfiii", "sfiii2", "sfiii3", "jojo", "jojoba", "redearth"],
            "all": [
                "sf2", "ssf2t", "sfa2", "sfa3", "kof98", "kof2002", "samsho2", "mk",
                "tekken3", "vsav", "garou", "rbffspec", "soulclbr", "mvsc", "sfiii3",
            ],
        },
        "beatemup": {
            "neogeo": ["sengoku", "sengoku2", "sengoku3", "mutnat", "burningf", "roboarmy", "eightman"],
            "cps1": ["ffight", "dino", "punisher", "captcomm", "knights", "wof", "kod"],
            "cps2": ["avsp", "ddtod", "ddsom", "batcir", "armwar"],
            "all": ["ffight", "dino", "punisher", "captcomm", "tmnt", "simpsons", "xmen", "avsp", "ddsom", "sengoku3"],
        },
        "runandgun": {
            "neogeo": ["mslug", "mslug2", "mslug3", "mslug4", "mslug5", "mslugx", "shocktro", "shocktr2", "cyberlip", "nam1975"],
            "cps1": ["mercs", "cawing", "forgottn", "varth"],
            "cps2": ["progear", "ecofghtr"],
            "all": ["mslug", "mslugx", "mslug3", "contra", "ssriders", "shocktro", "gunforce", "mercs", "gberet"],
        },
        "shootemup": {
            "neogeo": [
                "pulstar", "blazstar", "viewpoin", "lastreso", "twinspri",
                "aerofig2", "aerofig3", "strik1945p", "preisle2", "zedblade", "androdu", "alpham2",
            ],
            "cps1": ["1941", "varth", "cawing", "forgottn", "unsquad"],
            "cps2": ["1944", "19xx", "gigawing", "progear", "marsmatr", "dimahoo", "ecofghtr"],
            "all": ["1942", "1943", "19xx", "raiden", "rtype", "gradius", "ddonpach", "pulstar", "blazstar", "gigawing", "marsmatr"],
        },
        "platform": {
            "neogeo": ["tophuntr", "spinmast", "maglord", "bjourney"],
            "cps1": ["ghouls", "strider", "megaman", "nemo", "mtwins", "willow", "3wonders"],
            "cps2": ["megaman2"],
            "all": ["snowbros", "bublbobl", "rainbow", "newzeal", "ghouls", "strider", "toki", "rastan", "shinobi", "willow"],
        },
        "puzzle": {
            "neogeo": ["pbobble", "pbobble2", "magicald", "magdrop2", "magdrop3", "puzzledp", "joyjoy", "moneyid", "gururin", "panicbom"],
            "cps1": ["pnickj", "qad", "qtono2", "pang3"],
            "cps2": ["spf2t", "pzloop2", "mpang", "qndream", "choko"],
            "all": ["pbobble", "atetris", "columns", "klax", "spf2t", "puyopuy2", "pang", "mpang", "magdrop3"],
        },
        "sports": {
            "neogeo": ["wjammers", "windjamm", "ssideki", "ssideki2", "ssideki3", "ssideki4", "turfmast", "neocup98", "2020bb", "strhoop"],
            "cps2": ["csclub"],
            "all": ["wjammers", "windjamm", "nbajam", "ssideki2", "turfmast", "trackfld", "archrivl", "csclub"],
        },
        "racing": {
            "neogeo": ["overtop", "neodrift", "thrashr"],
            "all": ["outrun", "daytona", "srally", "chasehq", "hangon", "overtop", "neodrift"],
        },
        "maze": {
            "all": ["pacman", "mspacman", "galaga", "digdug", "pengo", "bbmanw", "frogger"],
        },
    }

    def __init__(self, store_cls: Optional[Any] = None) -> None:
        if store_cls is None:
            from app.infrastructure.scrapers.referential_store import LiveReferentialStore

            store_cls = LiveReferentialStore
        self.store = store_cls(self.source_id, ttl_seconds=43200)

    @classmethod
    def is_rom_allowed_for_system(cls, rom: str, sys_key: Optional[str]) -> bool:
        """Return True if `rom` is permitted on `sys_key` (rejects Naomi `mvsc2` on `cps2`, etc.)."""
        r = (rom or "").strip().lower()
        if not r or r.startswith(_IGNORED_MAME_PREFIXES):
            return False
        if not sys_key:
            return True
        canon_sys = resolve_canonical_system(sys_key)
        forbidden = cls._FORBIDDEN_ROMS_BY_SYSTEM.get(canon_sys)
        if forbidden and r in forbidden:
            return False
        return True

    @classmethod
    def is_rom_authoritative_for_system(cls, rom: str, sys_key: Optional[str]) -> bool:
        """Return True when `rom` is explicitly listed in the authoritative board set for `sys_key`."""
        r = (rom or "").strip().lower()
        if not r or not sys_key:
            return False
        canon_sys = resolve_canonical_system(sys_key)
        auth_roms = cls._AUTHORITATIVE_SYSTEM_ROMS.get(canon_sys) or []
        return r in auth_roms

    @classmethod
    def resolve_authoritative_system_for_rom(cls, rom: str) -> Optional[str]:
        """Return the canonical arcade sub-board (`cps1`, `cps2`, `cps3`, `neogeo`, etc.) owning `rom`."""
        r = (rom or "").strip().lower()
        if not r:
            return None
        for sys_k, auth_roms in cls._AUTHORITATIVE_SYSTEM_ROMS.items():
            if r in auth_roms:
                return sys_k
        return None

    async def _harvest_from_lista_mame(
        self, client: Any, token: str, sys_key: Optional[str] = None
    ) -> List[str]:
        discovered: List[str] = []
        try:
            resp = await client.get(
                "https://adb.arcadeitalia.net/lista_mame.php",
                params={"ricerca": token, "arcade_only": "1", "cloni": "0", "lang": "en"},
                timeout=4.5,
            )
            if resp.status_code == 200 and resp.text:
                for match in _ADB_ROM_LINK_RE.finditer(resp.text):
                    rom = match.group(1).lower().strip()
                    if (
                        rom
                        and self.is_rom_allowed_for_system(rom, sys_key)
                        and rom not in discovered
                    ):
                        discovered.append(rom)
            elif resp.status_code != 200:
                logger.warning(
                    "[REFERENTIAL:arcadeitalia] lista_mame '%s' returned HTTP %s",
                    token,
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning(
                "[REFERENTIAL:arcadeitalia] lista_mame harvest for '%s' failed: %s", token, exc
            )
        return discovered

    async def _harvest_from_wikidata_p2451(self, client: Any) -> List[str]:
        sparql = (
            "SELECT DISTINCT ?rom WHERE { "
            "?game wdt:P31/wdt:P279* wd:Q7889 ; wdt:P2451 ?rom . "
            "} LIMIT 400"
        )
        discovered: List[str] = []
        try:
            resp = await client.get(
                "https://query.wikidata.org/sparql",
                params={"format": "json", "query": sparql},
                headers={"Accept": "application/sparql-results+json"},
                timeout=5.5,
            )
            if resp.status_code == 200:
                bindings = (resp.json().get("results") or {}).get("bindings") or []
                for b in bindings:
                    rom = str(((b.get("rom") or {}).get("value")) or "").strip().lower()
                    if (
                        rom
                        and re.fullmatch(r"[a-z0-9_]{2,16}", rom)
                        and not rom.startswith(_IGNORED_MAME_PREFIXES)
                    ):
                        discovered.append(rom)
        except Exception as exc:
            logger.debug("[REFERENTIAL:arcadeitalia] Wikidata P2451 sync skipped: %s", exc)
        return discovered

    async def sync_referential(self, client: Any, force: bool = False) -> Dict[str, Any]:
        if not force and not self.store.is_expired():
            return self.store.load()

        tokens: List[str] = []
        for _ in range(2):
            syllable = random_search_syllable()
            batch = await self._harvest_from_lista_mame(client, syllable)
            tokens.extend(batch)
            if len(tokens) >= 40:
                break

        if len(tokens) < 25:
            wd_roms = await self._harvest_from_wikidata_p2451(client)
            tokens.extend(wd_roms)

        saved = self.store.save(
            tokens=tokens,
            metadata={
                "last_sync_count": len(tokens),
                "supported_systems": ["arcade", "mame", "neogeo", "cps1", "cps2", "cps3"],
            },
        )
        logger.info(
            "[REFERENTIAL:arcadeitalia] Live referential synced: %d MAME ROM identifiers cached",
            len(saved.get("tokens") or []),
        )
        return saved

    async def sample_unseen_tokens(
        self,
        client: Any,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 4,
    ) -> List[str]:
        ref_data = await self.sync_referential(client, force=False)
        req_sys = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.lower() != "all"
        ]
        req_genres = [
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.lower() != "all"
        ]
        specific_sys = next((s for s in req_sys if s in self._SYSTEM_SEARCH_STEMS), None)
        chosen_genre = _SYS_RNG.choice(req_genres) if req_genres else None

        if chosen_genre and chosen_genre in self._GENRE_SEARCH_STEMS:
            g_map = self._GENRE_SEARCH_STEMS[chosen_genre]
            stems_pool = [
                s.strip().lower()
                for s in (g_map.get(specific_sys or "all") or g_map.get("all") or [])
                if s and self.is_rom_allowed_for_system(s, specific_sys)
            ]
            if stems_pool:
                bucket_key = f"{specific_sys or 'arcade'}:{chosen_genre}"
                by_sg = dict(((ref_data.get("metadata") or {}).get("by_system_genre")) or {})
                sg_tokens: List[str] = [
                    r
                    for r in (by_sg.get(bucket_key) or [])
                    if self.is_rom_allowed_for_system(r, specific_sys)
                ]
                unseen_sg = [r for r in sg_tokens if f"games_adb_{r}" not in seen_ids]
                if len(unseen_sg) < max(6, count):
                    unseen_stems = [
                        s
                        for s in stems_pool
                        if s not in sg_tokens and f"games_adb_{s}" not in seen_ids
                    ] or list(stems_pool)
                    _SYS_RNG.shuffle(unseen_stems)
                    for stem in unseen_stems[:4]:
                        fresh = await self._harvest_from_lista_mame(
                            client, stem, sys_key=specific_sys
                        )
                        for rom in fresh:
                            if rom not in sg_tokens and self.is_rom_allowed_for_system(
                                rom, specific_sys
                            ):
                                sg_tokens.append(rom)
                        if stem not in sg_tokens and self.is_rom_allowed_for_system(
                            stem, specific_sys
                        ):
                            sg_tokens.append(stem)
                        if len(
                            [r for r in sg_tokens if f"games_adb_{r}" not in seen_ids]
                        ) >= max(8, count * 2):
                            break
                    by_sg[bucket_key] = sg_tokens
                    self.store.save(tokens=sg_tokens, metadata={"by_system_genre": by_sg})
                    unseen_sg = [r for r in sg_tokens if f"games_adb_{r}" not in seen_ids]
                pool = unseen_sg if unseen_sg else sg_tokens
                if pool:
                    return _SYS_RNG.sample(pool, k=min(len(pool), max(6, count)))

        if specific_sys:
            by_system = dict(((ref_data.get("metadata") or {}).get("by_system")) or {})
            sys_tokens: List[str] = [
                r
                for r in (by_system.get(specific_sys) or [])
                if self.is_rom_allowed_for_system(r, specific_sys)
            ]
            unseen_sys = [r for r in sys_tokens if f"games_adb_{r}" not in seen_ids]
            if len(unseen_sys) < count:
                stems = [
                    s
                    for s in self._SYSTEM_SEARCH_STEMS[specific_sys]
                    if s not in sys_tokens and f"games_adb_{s}" not in seen_ids
                ] or list(self._SYSTEM_SEARCH_STEMS[specific_sys])
                _SYS_RNG.shuffle(stems)
                for stem in stems[:4]:
                    fresh = await self._harvest_from_lista_mame(
                        client, stem, sys_key=specific_sys
                    )
                    for rom in fresh:
                        if rom not in sys_tokens and self.is_rom_allowed_for_system(
                            rom, specific_sys
                        ):
                            sys_tokens.append(rom)
                    if len(
                        [r for r in sys_tokens if f"games_adb_{r}" not in seen_ids]
                    ) >= max(6, count):
                        break
                by_system[specific_sys] = sys_tokens
                self.store.save(tokens=sys_tokens, metadata={"by_system": by_system})
                unseen_sys = [r for r in sys_tokens if f"games_adb_{r}" not in seen_ids]
            pool = unseen_sys if unseen_sys else sys_tokens
            if pool:
                return _SYS_RNG.sample(pool, k=min(len(pool), max(1, count)))

        all_tokens: List[str] = list(ref_data.get("tokens") or [])
        unseen = [r for r in all_tokens if f"games_adb_{r}" not in seen_ids]

        if len(unseen) < count:
            fresh = await self._harvest_from_lista_mame(client, random_search_syllable())
            if fresh:
                ref_data = self.store.save(tokens=fresh)
                all_tokens = list(ref_data.get("tokens") or [])
                unseen = [r for r in all_tokens if f"games_adb_{r}" not in seen_ids]

        if not unseen:
            unseen = all_tokens
        if not unseen:
            return []
        return _SYS_RNG.sample(unseen, k=min(len(unseen), max(1, count)))
