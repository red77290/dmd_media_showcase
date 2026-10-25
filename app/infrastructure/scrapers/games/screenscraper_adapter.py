"""
Primary Games Scraper Adapter: ScreenScraper API v2 (`api.screenscraper.fr`).
Implements `IScraperSource` with uniform random discovery across all retro systems,
strictly following ScreenScraper's `jeuInfos.xml` / `jeuRecherche.php` schema.
"""

from __future__ import annotations

from collections import deque
import logging
import random
import re
import xml.etree.ElementTree as ET
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import (
    CANONICAL_DECADES,
    GAME_GENRE_CATALOG,
    build_game_fingerprint,
    is_disallowed_non_fighting_title,
    localize_label,
    normalize_category_slug,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.persistence.server_config_store import server_config_store
from app.infrastructure.scrapers.catalog_metadata import GAME_GENRES, SYSTEM_METADATA
from app.infrastructure.scrapers.http_utils import clean_synopsis_text, create_async_http_client

logger = logging.getLogger("dmd_showcase.scrapers.games.screenscraper")

_SYS_RNG = random.SystemRandom()

_SEARCH_SYLLABLES = [
    "al", "am", "an", "ar", "as", "at", "au", "ax", "az",
    "ba", "be", "bi", "bl", "bo", "br", "bu",
    "ca", "ce", "ch", "cl", "co", "cr", "cy",
    "da", "de", "di", "do", "dr", "du", "dy",
    "ea", "ec", "el", "em", "en", "er", "es", "et", "ex",
    "fa", "fe", "fi", "fl", "fo", "fr", "fu",
    "ga", "ge", "gh", "gi", "gl", "go", "gr", "gu",
    "ha", "he", "hi", "ho", "hu", "hy",
    "ic", "ik", "il", "in", "ir", "is",
    "ja", "je", "jo", "ju",
    "ka", "ke", "ki", "kl", "kn", "ko", "kr", "ku",
    "la", "le", "li", "lo", "lu",
    "ma", "me", "mi", "mo", "mu", "my",
    "na", "ne", "ni", "no", "nu",
    "ob", "od", "om", "on", "op", "or", "ou", "ov",
    "pa", "pe", "ph", "pi", "pl", "po", "pr", "ps", "pu",
    "qu", "ra", "re", "ri", "ro", "ru",
    "sa", "sc", "se", "sf", "sh", "si", "sk", "sl", "sm", "sn", "so", "sp", "sq", "st", "su", "sw", "sy",
    "ta", "te", "th", "ti", "to", "tr", "tu", "tw",
    "ul", "un", "ur", "va", "ve", "vi", "vo", "vr",
    "wa", "we", "wh", "wi", "wo", "wr",
    "xe", "ya", "yo", "yu", "za", "ze", "zo",
]

_GENRE_DISCOVERY_TOKENS: Dict[str, List[str]] = {
    "fighting": [
        "street fighter", "super street", "mortal kombat", "ultimate mortal", "eternal",
        "fatal fury", "samurai", "art of fighting", "world heroes", "dragon ball",
        "appel du destin", "buyuu", "hakusho", "yu yu", "primal rage", "clayfighter",
        "weaponlord", "virtua fighter", "tournament", "brutal", "justice league",
        "power instinct", "fighting masters", "deadly moves", "shadow falls", "ballz",
        "shaq", "king of the monsters", "king of fighters", "garou", "last blade",
        "darkstalkers", "vampire", "tekken", "soul", "dead or alive", "killer instinct",
        "guilty gear", "rival schools", "pocket fighter", "cyberbots", "jojo",
        "red earth", "toshinden", "bloody roar", "bushido blade", "tobal", "breakers",
        "kizuna", "waku waku", "kaiser", "ranma", "gundam",
    ],
    "beatemup": [
        "streets of rage", "bare knuckle", "golden axe", "final fight", "double dragon",
        "comix zone", "maximum carnage", "separation anxiety", "captain america",
        "punisher", "turtles", "hyperstone", "battletoads", "alien storm", "altered beast",
        "cadillacs", "knights of the round", "warriors of fate", "king of dragons",
        "sengoku", "mutation nation", "burning fight", "alien vs", "dungeons",
        "river city", "splatterhouse", "vendetta", "crime fighters",
    ],
    "platform": [
        "sonic", "mario", "castlevania", "mega man", "donkey kong", "kirby", "aladdin",
        "lion king", "castle of illusion", "world of illusion", "quackshot", "ristar",
        "dynamite headdy", "rocket knight", "sparkster", "shinobi", "kid chameleon",
        "earthworm jim", "rayman", "crash", "spyro", "alex kidd", "wonder boy",
        "ghouls", "strider", "bonk", "klonoa", "gex", "bubsy", "cool spot", "vectorman",
    ],
    "shootemup": [
        "thunder force", "gradius", "r-type", "darius", "parodius", "twinbee", "musha",
        "aleste", "raiden", "1942", "1943", "19xx", "giga wing", "mars matrix", "pulstar",
        "blazing star", "gleylancer", "gaiares", "bio-hazard", "truxton", "fire shark",
        "aero fighters", "strikers", "ikaruga", "radiant", "einhander", "axelay", "soldier",
    ],
    "runandgun": [
        "contra", "probotector", "gunstar", "metal slug", "sunset riders", "turrican",
        "mercs", "shock troopers", "midnight resistance", "rolling thunder", "eswat",
        "vectorman", "doom troopers", "skeleton krew", "zombies", "smash tv", "ikari",
    ],
    "rpg": [
        "phantasy star", "shining force", "chrono", "final fantasy", "dragon quest",
        "dragon warrior", "mana", "breath of fire", "suikoden", "wild arms", "xenogears",
        "landstalker", "beyond oasis", "story of thor", "soleil", "crusader of centy",
        "lunar", "grandia", "star ocean", "valkyrie", "earthbound", "terranigma", "ys",
    ],
    "racing": [
        "outrun", "road rash", "mario kart", "f-zero", "top gear", "virtua racing",
        "ridge racer", "gran turismo", "wipeout", "micro machines", "rock n roll racing",
        "super hang-on", "super monaco", "daytona", "sega rally", "lotus", "cruis",
    ],
    "puzzle": [
        "columns", "puyo", "tetris", "dr. mario", "mean bean", "puzzle bobble",
        "bust-a-move", "klax", "lemmings", "magical drop", "panel de pon", "lumines",
        "boulder dash", "sokoban", "picross", "kula world", "intelligent qube",
    ],
    "sports": [
        "nba jam", "fifa", "sensible soccer", "iss", "nhl", "madden", "windjammers",
        "speedball", "mutant league", "virtua tennis", "tony hawk", "punch-out",
        "super sidekicks", "turf masters", "track & field", "olympic", "tecmo super",
    ],
    "strategy": [
        "dune", "herzog", "shining force", "warsong", "langrisser", "ogre battle",
        "tactics", "advance wars", "populous", "syndicate", "theme park", "civilization",
        "warcraft", "command & conquer", "romance of the three", "nobunaga", "worms",
    ],
    "action": [
        "strider", "shinobi", "zelda", "metroid", "castlevania", "resident evil",
        "metal gear", "tomb raider", "legacy of kain", "bomberman", "desert strike",
        "jungle strike", "urban strike", "flashback", "another world", "ecc", "chaos engine",
    ],
}

_SYSTEM_SPECIFIC_STEMS: Dict[str, List[str]] = {
    "cps1": [
        "street fighter", "final fight", "cadillacs", "punisher", "captain commando",
        "knights of the round", "warriors of fate", "king of dragons", "strider",
        "ghouls", "1941", "varth", "carrier air wing", "mercs", "forgotten worlds",
        "un squadron", "willow", "mega twins", "nemo", "mega man", "slam masters",
        "muscle bomber", "puzz loop", "pang", "three wonders",
    ],
    "cps2": [
        "super street fighter", "street fighter alpha", "street fighter zero",
        "marvel super heroes", "vampire savior", "vampire hunter", "night warriors",
        "darkstalkers", "x-men", "marvel vs. capcom", "cyberbots", "ring of destruction",
        "super gem fighter", "pocket fighter", "super puzzle fighter", "puzz loop 2",
        "mighty pang", "1944", "19xx", "giga wing", "progear", "mars matrix",
        "dimahoo", "eco fighters", "alien vs. predator", "dungeons & dragons",
        "battle circuit", "armored warriors", "capcom sports club", "mega man 2",
    ],
    "cps3": [
        "street fighter iii", "jojo", "red earth", "war-zard",
    ],
    "neogeo": [
        "king of fighters", "samurai shodown", "metal slug", "garou", "fatal fury",
        "art of fighting", "last blade", "world heroes", "snk vs. capcom", "shock troopers",
        "sengoku", "pulstar", "blazing star", "viewpoint", "last resort", "twinkle star",
        "aero fighters", "strikers 1945", "prehistoric isle", "windjammers", "super sidekicks",
        "neo turf masters", "baseball stars", "street hoop", "top hunter", "spinmaster",
        "magician lord", "blue's journey", "cyber-lip", "nam-1975", "mutation nation",
        "robo army", "burning fight", "eight man", "puzzle bobble", "magical drop",
    ],
}

_STRICT_HARDWARE_SS_IDS: Dict[str, Tuple[str, ...]] = {
    "cps1": ("105", "cps1", "cps-1", "capcom play system", "cp system"),
    "cps2": ("106", "cps2", "cps-2", "capcom play system 2", "cp system ii"),
    "cps3": ("107", "cps3", "cps-3", "capcom play system 3", "cp system iii"),
    "neogeo": ("142", "neogeo", "neo-geo", "neo geo", "snk neo-geo"),
}

_NON_CPS_TITLE_TOKENS: Tuple[str, ...] = (
    "marvel vs. capcom 2",
    "marvel vs capcom 2",
    "mvsc2",
    "capcom vs. snk",
    "capcom vs snk",
    "cvs2",
    "guilty gear",
    "ikaruga",
    "virtua fighter",
    "dead or alive",
    "soulcalibur",
    "tekken",
    "mortal kombat",
)

_SS_ID_TO_SYS_KEY: Dict[str, str] = {
    str(meta["ss_id"]): k for k, meta in SYSTEM_METADATA.items() if meta.get("ss_id") != 75
}

_WARNED_MISSING_DEVID = False


def _extract_node_text(node: Any) -> str:
    """Extract stripped text from a ScreenScraper JSON/XML node (str, dict with 'text', or list)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float)):
        return str(node).strip()
    if isinstance(node, dict):
        return str(node.get("text") or node.get("nom") or "").strip()
    if isinstance(node, list) and node:
        return _extract_node_text(node[0])
    return ""


def _region_priority_order(short_lang: str) -> List[str]:
    """Return ordered region preference list for a target language code (`fr`, `en`, `es`, `de`, `it`, `pt`, `ja`)."""
    lang_code = (short_lang or "fr").strip().lower()
    if lang_code == "ja":
        base = ["jp", "ja", "wor", "ss", "us", "eu", "kr", "br", "au"]
    elif lang_code == "pt":
        base = ["pt", "br", "eu", "wor", "ss", "us", "jp", "au", "kr"]
    elif lang_code == "es":
        base = ["es", "sp", "eu", "wor", "ss", "us", "jp", "br", "au", "kr"]
    elif lang_code == "en":
        base = ["en", "us", "uk", "wor", "eu", "ss", "au", "jp", "br", "kr"]
    else:
        base = [lang_code, "eu", "wor", "ss", "us", "jp", "br", "au", "kr"]
    return list(dict.fromkeys(r for r in base if r))


def _score_region(region: str, priority: List[str]) -> int:
    """Return a descending score for `region` according to `priority`."""
    reg = (region or "").strip().lower()
    if reg in priority:
        return 100 - priority.index(reg) * 10
    if not reg:
        return 25
    return 10


def parse_jeu_infos_xml(xml_text: str) -> List[Dict[str, Any]]:
    """
    Parse a ScreenScraper XML payload (`jeuInfos.xml` or `jeuRecherche.php?output=xml`)
    into a list of normalized `<jeu>` dictionaries compatible with `ScreenScraperAdapter._parse_game_node`.
    """
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        logger.debug("[ScreenScraper] XML parse error: %s", exc)
        return []

    jeu_elements = root.findall(".//jeu") if root.tag != "jeu" else [root]
    parsed_games: List[Dict[str, Any]] = []

    for jeu_el in jeu_elements:
        jeu_dict: Dict[str, Any] = {
            "id": str(jeu_el.attrib.get("id") or "").strip(),
            "romid": str(jeu_el.attrib.get("romid") or "").strip(),
            "notgame": str(jeu_el.attrib.get("notgame") or "false").strip(),
        }

        cloneof_el = jeu_el.find("cloneof")
        if cloneof_el is not None:
            jeu_dict["cloneof"] = (cloneof_el.text or "0").strip()

        sys_el = jeu_el.find("systeme")
        if sys_el is not None:
            jeu_dict["systeme"] = {
                "id": str(sys_el.attrib.get("id") or "").strip(),
                "parentid": str(sys_el.attrib.get("parentid") or "").strip(),
                "text": (sys_el.text or "").strip(),
            }

        for company_tag in ("editeur", "developpeur"):
            comp_el = jeu_el.find(company_tag)
            if comp_el is not None:
                jeu_dict[company_tag] = {
                    "id": str(comp_el.attrib.get("id") or "").strip(),
                    "text": (comp_el.text or "").strip(),
                }

        joueurs_el = jeu_el.find("joueurs")
        if joueurs_el is not None:
            jeu_dict["joueurs"] = {"text": (joueurs_el.text or "").strip()}

        note_el = jeu_el.find("note")
        if note_el is not None:
            jeu_dict["note"] = {"text": (note_el.text or "").strip()}

        noms_list: List[Dict[str, str]] = []
        noms_el = jeu_el.find("noms")
        if noms_el is not None:
            for nom_el in noms_el.findall("nom"):
                txt = (nom_el.text or "").strip()
                if txt:
                    noms_list.append(
                        {
                            "region": str(nom_el.attrib.get("region") or "").strip().lower(),
                            "text": txt,
                        }
                    )
        jeu_dict["noms"] = noms_list

        syn_list: List[Dict[str, str]] = []
        syn_parent = jeu_el.find("synopsis")
        if syn_parent is not None:
            sub_syns = syn_parent.findall("synopsis")
            if sub_syns:
                for s_el in sub_syns:
                    txt = (s_el.text or "").strip()
                    if txt:
                        syn_list.append(
                            {
                                "langue": str(
                                    s_el.attrib.get("langue") or s_el.attrib.get("lang") or ""
                                ).strip().lower(),
                                "text": txt,
                            }
                        )
            elif (syn_parent.text or "").strip():
                syn_list.append(
                    {
                        "langue": str(
                            syn_parent.attrib.get("langue")
                            or syn_parent.attrib.get("lang")
                            or "en"
                        ).strip().lower(),
                        "text": (syn_parent.text or "").strip(),
                    }
                )
        jeu_dict["synopsis"] = syn_list

        dates_list: List[Dict[str, str]] = []
        dates_el = jeu_el.find("dates")
        if dates_el is not None:
            for d_el in dates_el.findall("date"):
                txt = (d_el.text or "").strip()
                if txt:
                    dates_list.append(
                        {
                            "region": str(d_el.attrib.get("region") or "").strip().lower(),
                            "text": txt,
                        }
                    )
        jeu_dict["dates"] = dates_list

        genres_list: List[Dict[str, Any]] = []
        genres_el = jeu_el.find("genres")
        if genres_el is not None:
            for g_el in genres_el.findall("genre"):
                txt = (g_el.text or "").strip()
                if txt:
                    genres_list.append(
                        {
                            "id": str(g_el.attrib.get("id") or "").strip(),
                            "nomcourt": str(g_el.attrib.get("nomcourt") or "").strip(),
                            "principale": str(g_el.attrib.get("principale") or "0").strip(),
                            "parentid": str(g_el.attrib.get("parentid") or "0").strip(),
                            "langue": str(
                                g_el.attrib.get("langue") or g_el.attrib.get("lang") or ""
                            ).strip().lower(),
                            "text": txt,
                        }
                    )
        jeu_dict["genres"] = genres_list

        medias_list: List[Dict[str, str]] = []
        medias_el = jeu_el.find("medias")
        if medias_el is not None:
            for m_el in medias_el.findall("media"):
                url = (m_el.text or m_el.attrib.get("url") or "").strip()
                if not url:
                    continue
                m_entry: Dict[str, str] = {k: str(v).strip() for k, v in m_el.attrib.items()}
                m_entry["url"] = url
                m_entry["text"] = url
                medias_list.append(m_entry)
        jeu_dict["medias"] = medias_list

        parsed_games.append(jeu_dict)

    return parsed_games


class ScreenScraperAdapter(IScraperSource):
    """
    Primary `IScraperSource` for `games` using ScreenScraper API v2 (`jeuRecherche.php` & `jeuInfos.php`).
    """

    source_id: str = "screenscraper"
    domain: DomainType = "games"
    role: str = "primary"

    parse_jeu_infos_xml = staticmethod(parse_jeu_infos_xml)

    def __init__(self) -> None:
        self._recent_tokens: Dict[Tuple[str, str], Deque[str]] = {}

    def _credentials(self) -> Dict[str, str]:
        cfg = server_config_store.get_all()
        return {
            "user": str(cfg.get("screenscraper_user") or "").strip(),
            "pass": str(cfg.get("screenscraper_pass") or "").strip(),
            "devid": str(cfg.get("screenscraper_dev_id") or "").strip(),
            "devpass": str(cfg.get("screenscraper_dev_pass") or "").strip(),
        }

    def is_configured(self) -> bool:
        global _WARNED_MISSING_DEVID
        creds = self._credentials()
        if creds["devid"] and creds["devpass"]:
            return True
        if creds["user"] and not _WARNED_MISSING_DEVID:
            _WARNED_MISSING_DEVID = True
            logger.warning(
                "[ScreenScraper] SCREENSCRAPER_USER is set, but SCREENSCRAPER_DEV_ID / SCREENSCRAPER_DEV_PASS "
                "are empty (required by ScreenScraper API v2) -> using fallback game scrapers."
            )
        return False

    def supported_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        return {
            "systems": [
                {"id": sys_id, "label": f"{meta['badge']} ({meta['label']})"}
                for sys_id, meta in SYSTEM_METADATA.items()
            ],
            "genres": [{"id": gid, "label": label} for gid, label in GAME_GENRES.items()],
            "decades": [
                {"id": "1970s", "label": "1970s"},
                {"id": "1980s", "label": "1980s"},
                {"id": "1990s", "label": "1990s"},
                {"id": "2000s", "label": "2000s"},
            ],
        }

    @staticmethod
    def _extract_genre_entries(genres_raw: Any) -> List[Dict[str, str]]:
        """Normalize ScreenScraper JSON/XML `<genres>` into a flat list with hierarchy metadata."""
        if isinstance(genres_raw, dict):
            genres_raw = genres_raw.get("genre") or genres_raw.get("genres") or [genres_raw]
        if not isinstance(genres_raw, list):
            return []
        entries: List[Dict[str, str]] = []
        for g in genres_raw:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("id") or "").strip()
            parentid = str(g.get("parentid") or "0").strip()
            principale = str(g.get("principale") or "1").strip()
            if isinstance(g.get("noms"), list):
                for n in g["noms"]:
                    if isinstance(n, dict) and n.get("text"):
                        entries.append(
                            {
                                "id": gid,
                                "parentid": parentid,
                                "langue": str(n.get("langue") or n.get("lang") or "").strip().lower(),
                                "text": str(n["text"]).strip(),
                                "principale": principale,
                            }
                        )
            elif g.get("text"):
                entries.append(
                    {
                        "id": gid,
                        "parentid": parentid,
                        "langue": str(g.get("langue") or g.get("lang") or "").strip().lower(),
                        "text": str(g["text"]).strip(),
                        "principale": principale,
                    }
                )
        return entries

    @staticmethod
    def _matches_decades(year_str: str, decades: List[str]) -> bool:
        req_decs = [d.strip().lower() for d in (decades or []) if d and d.strip().lower() != "all"]
        if not req_decs:
            return True
        if not year_str or len(year_str) < 4 or not year_str[:4].isdigit():
            return False
        yr = int(year_str[:4])
        for dec in req_decs:
            if dec in CANONICAL_DECADES:
                low_b, high_b = CANONICAL_DECADES[dec]["bounds"]
                if low_b <= yr <= high_b:
                    return True
            elif len(dec) >= 4 and dec[:4].isdigit():
                base = int(dec[:4])
                if base <= yr <= base + 9:
                    return True
        return False

    def _parse_game_node(
        self,
        jeu: Dict[str, Any],
        sys_key: str,
        lang: str,
        criteria: Optional[FilterCriteria] = None,
    ) -> Optional[MediaCandidate]:
        if not isinstance(jeu, dict):
            return None
        if str(jeu.get("notgame") or "").strip().lower() in ("true", "1"):
            return None

        game_id = str(jeu.get("id") or "").strip()
        if not game_id:
            return None

        short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
        reg_priority = _region_priority_order(short_lang)

        # 1. Strictly validate <systeme id="..."> and <systeme> text for ALL systems
        sys_node = jeu.get("systeme")
        if isinstance(sys_node, dict):
            sys_id_resp = str(sys_node.get("id") or "").strip()
            sys_text_resp = str(sys_node.get("text") or sys_node.get("nom") or "").strip().lower()
        else:
            sys_id_resp = ""
            sys_text_resp = str(sys_node or "").strip().lower()

        resp_sys = ""
        if sys_id_resp:
            if sys_id_resp in _SS_ID_TO_SYS_KEY:
                resp_sys = _SS_ID_TO_SYS_KEY[sys_id_resp]
            elif sys_id_resp == "75":
                resp_sys = "arcade"
            else:
                resp_sys = f"ss_other_{sys_id_resp}"
        elif sys_text_resp:
            resolved_from_text = resolve_canonical_system(sys_text_resp)
            if resolved_from_text in SYSTEM_METADATA:
                resp_sys = resolved_from_text
            else:
                resp_sys = f"ss_other_{resolved_from_text}"

        requested_sys = resolve_canonical_system(sys_key) if sys_key else ""
        if requested_sys in _STRICT_HARDWARE_SS_IDS:
            expected_tokens = _STRICT_HARDWARE_SS_IDS[requested_sys]
            expected_id = expected_tokens[0]
            if sys_id_resp or sys_text_resp:
                id_ok = sys_id_resp == expected_id
                text_ok = sys_id_resp in ("", "75") and any(
                    tok in sys_text_resp for tok in expected_tokens[1:]
                )
                if not id_ok and not text_ok:
                    return None
            canon_sys = requested_sys
        elif requested_sys in ("arcade", "mame", "fbneo"):
            if resp_sys and resp_sys not in ("arcade", "mame", "fbneo", "cps1", "cps2", "cps3", "neogeo"):
                return None
            canon_sys = resp_sys if resp_sys in ("cps1", "cps2", "cps3", "neogeo") else requested_sys
        elif requested_sys and requested_sys in SYSTEM_METADATA:
            expected_ss_id = str(SYSTEM_METADATA[requested_sys]["ss_id"])
            if sys_id_resp and sys_id_resp != expected_ss_id:
                return None
            if not sys_id_resp and resp_sys and resp_sys != requested_sys:
                return None
            canon_sys = requested_sys
        else:
            if resp_sys and resp_sys not in SYSTEM_METADATA:
                return None
            canon_sys = resp_sys if resp_sys in SYSTEM_METADATA else "arcade"

        if criteria and criteria.systems:
            req_systems = {
                resolve_canonical_system(s)
                for s in criteria.systems
                if s and s.strip().lower() != "all"
            }
            if req_systems and canon_sys not in req_systems:
                if not (
                    canon_sys in ("cps1", "cps2", "cps3", "neogeo")
                    and (req_systems & {"arcade", "mame", "fbneo"})
                ):
                    return None

        sys_meta = SYSTEM_METADATA.get(canon_sys, SYSTEM_METADATA["arcade"])

        # 2. Parse <noms> with regional priority: short_lang -> eu -> wor -> ss -> us -> jp
        noms_raw = jeu.get("noms")
        if isinstance(noms_raw, dict):
            noms_raw = noms_raw.get("nom") or noms_raw.get("noms") or [noms_raw]
        scored_titles: List[Tuple[int, str]] = []
        for nom in noms_raw or []:
            if isinstance(nom, dict) and nom.get("text"):
                txt = str(nom["text"]).strip()
                reg = str(nom.get("region") or "").strip().lower()
                if txt:
                    scored_titles.append((_score_region(reg, reg_priority), txt))
        if not scored_titles:
            return None
        scored_titles.sort(key=lambda item: item[0], reverse=True)
        title = scored_titles[0][1]

        title_low = title.lower()
        if canon_sys in ("cps1", "cps2", "cps3"):
            if any(bad_tok in title_low for bad_tok in _NON_CPS_TITLE_TOKENS):
                return None

        # 3. Parse <dates> with regional priority and verify decade filter
        dates_raw = jeu.get("dates")
        if isinstance(dates_raw, dict):
            dates_raw = dates_raw.get("date") or dates_raw.get("dates") or [dates_raw]
        scored_years: List[Tuple[int, str]] = []
        for d_node in dates_raw or []:
            if isinstance(d_node, dict) and d_node.get("text"):
                raw_d = str(d_node["text"]).strip()
                m = re.search(r"\b(19\d{2}|20\d{2})\b", raw_d)
                if m:
                    reg = str(d_node.get("region") or "").strip().lower()
                    scored_years.append((_score_region(reg, reg_priority), m.group(1)))
        scored_years.sort(key=lambda item: item[0], reverse=True)
        year = scored_years[0][1] if scored_years else "1992"

        if criteria and criteria.decades:
            if not self._matches_decades(year, criteria.decades):
                return None

        # 4. Parse <genres> with ScreenScraper umbrella disambiguation & strict genre enforcement
        genre_entries = self._extract_genre_entries(jeu.get("genres"))
        raw_canon_genres: List[str] = []
        entry_canon_pairs: List[Tuple[Dict[str, str], str]] = []
        has_explicit_versus_fighting = False

        for g_entry in genre_entries:
            g_text = g_entry.get("text") or ""
            if not g_text:
                continue
            g_slug = normalize_category_slug(g_text)
            cg = resolve_canonical_genre("games", g_text)
            if cg:
                entry_canon_pairs.append((g_entry, cg))
                if cg not in raw_canon_genres:
                    raw_canon_genres.append(cg)
            if cg == "fighting" and any(
                tok in g_slug
                for tok in (
                    "versus",
                    "1v1",
                    "combat_2d",
                    "combat_3d",
                    "fighter_2d",
                    "fighter_3d",
                    "wrestling",
                    "incontri",
                )
            ):
                has_explicit_versus_fighting = True

        # ScreenScraper uses top-level <genre id="5" parentid="0">Combat / Fighter</genre>
        # as the umbrella parent of <genre parentid="5">Beat'em all</genre> and on melee
        # action-platformers. Strip generic umbrella "fighting" whenever a concrete non-1v1
        # genre ("beatemup", "platform", "runandgun", "shootemup") is present without an
        # explicit 1v1 versus subgenre.
        canon_genres = list(raw_canon_genres)
        if (
            "fighting" in canon_genres
            and not has_explicit_versus_fighting
            and (set(canon_genres) & {"beatemup", "platform", "runandgun", "shootemup"})
        ):
            canon_genres = [g for g in canon_genres if g != "fighting"]

        if criteria and criteria.genres:
            req_genres = {
                resolve_canonical_genre("games", g)
                for g in criteria.genres
                if g and g.strip().lower() != "all"
            }
            if req_genres:
                if not canon_genres or not (set(canon_genres) & req_genres):
                    return None
                if "fighting" in req_genres:
                    if "platform" not in req_genres and "platform" in raw_canon_genres:
                        return None
                    if "beatemup" not in req_genres and "beatemup" in raw_canon_genres:
                        return None
                    if (
                        "platform" not in req_genres
                        and "beatemup" not in req_genres
                        and is_disallowed_non_fighting_title(title)
                    ):
                        return None

        primary_genre = canon_genres[0] if canon_genres else ""
        localized_genre_label = ""
        for g_entry, cg in entry_canon_pairs:
            if g_entry.get("langue") == short_lang and cg == primary_genre:
                localized_genre_label = g_entry.get("text") or ""
                break
        if not localized_genre_label:
            for g_entry, cg in entry_canon_pairs:
                if g_entry.get("langue") == short_lang and cg in canon_genres:
                    localized_genre_label = g_entry.get("text") or ""
                    break
        if not localized_genre_label and primary_genre in GAME_GENRE_CATALOG:
            localized_genre_label = localize_label(
                GAME_GENRE_CATALOG[primary_genre]["labels"], short_lang, GAME_GENRES.get(primary_genre, "")
            )

        # 5. Parse <editeur> / <developpeur>
        publisher = _extract_node_text(jeu.get("editeur")) or _extract_node_text(jeu.get("developpeur"))
        if localized_genre_label and publisher:
            subtitle = f"{localized_genre_label} • {publisher}"[:28]
        elif publisher:
            subtitle = f"{sys_meta['badge']} • {publisher}"[:28]
        elif localized_genre_label:
            subtitle = f"{localized_genre_label} • {year}"[:28]
        else:
            subtitle = f"{sys_meta['badge']} • {year}"[:28]

        # 6. Parse <synopsis> with language priority: short_lang -> en -> fr -> first available
        syn_raw = jeu.get("synopsis")
        if isinstance(syn_raw, dict):
            syn_raw = syn_raw.get("synopsis") or [syn_raw]
        synopsis_by_lang: Dict[str, str] = {}
        first_synopsis = ""
        for syn in syn_raw or []:
            if isinstance(syn, dict) and syn.get("text"):
                s_lang = str(syn.get("langue") or "").strip().lower()
                cleaned = clean_synopsis_text(str(syn["text"]))
                if cleaned:
                    if s_lang and s_lang not in synopsis_by_lang:
                        synopsis_by_lang[s_lang] = cleaned
                    if not first_synopsis:
                        first_synopsis = cleaned
        alt_lang = "jp" if short_lang == "ja" else ("sp" if short_lang == "es" else "")
        synopsis = (
            synopsis_by_lang.get(short_lang)
            or (synopsis_by_lang.get(alt_lang) if alt_lang else None)
            or synopsis_by_lang.get("en")
            or synopsis_by_lang.get("fr")
            or first_synopsis
        )

        # 7. Parse <note> (ScreenScraper scale is /20 -> normalize to /10)
        note_raw = _extract_node_text(jeu.get("note"))
        try:
            note_val = float(note_raw)
            rating = f"{note_val / 2.0:.1f}" if note_val > 10.0 else f"{note_val:.1f}"
        except (TypeError, ValueError):
            rating = "8.5"

        cand = MediaCandidate(
            id=f"games_ss_{game_id}",
            domain="games",
            title=title,
            year=year,
            badge=sys_meta["badge"],
            badge_color=sys_meta["color"],
            subtitle=subtitle,
            rating=rating,
            synopsis=synopsis or f"{title} ({year}) on {sys_meta['label']}.",
            lang=short_lang,
            source_id=self.source_id,
            theme_palette=sys_meta["palette"],
            extra_meta={
                "system": canon_sys,
                "genre": primary_genre,
                "genres": canon_genres,
                "publisher": publisher,
            },
        )

        # 8. Parse <medias> strictly filtering parent in (None, "", "jeu")
        medias_raw = jeu.get("medias")
        if isinstance(medias_raw, dict):
            medias_raw = medias_raw.get("media") or medias_raw.get("medias") or [medias_raw]

        poster_type_rank = {"box-2D": 400, "flyer": 300, "box-3D": 200, "steamgrid": 100}
        marquee_type_rank = {
            "screenmarquee": 600,
            "screenmarqueesmall": 500,
            "wheel-hd": 400,
            "wheel": 300,
            "wheel-carbon": 200,
            "wheel-steel": 100,
        }
        snap_type_rank = {"ss": 300, "sstitle": 200, "fanart": 100}
        video_type_rank = {"video-normalized": 200, "video": 100}

        best_posters: List[Tuple[int, str]] = []
        best_marquees: List[Tuple[int, str]] = []
        best_snaps: List[Tuple[int, str]] = []
        best_videos: List[Tuple[int, str]] = []

        for media in medias_raw or []:
            if not isinstance(media, dict):
                continue
            parent_attr = str(media.get("parent") or "jeu").strip().lower()
            if parent_attr not in ("", "jeu"):
                # Strictly ignore publisher/developer/family/genre/region/rating logos and figurines
                continue
            mtype = str(media.get("type") or "").strip()
            url = str(media.get("url") or media.get("text") or "").strip()
            if not url.startswith("http"):
                continue
            reg = str(media.get("region") or "").strip().lower()
            reg_score = _score_region(reg, reg_priority)

            if mtype in poster_type_rank:
                best_posters.append((poster_type_rank[mtype] + reg_score, url))
            elif mtype in marquee_type_rank:
                best_marquees.append((marquee_type_rank[mtype] + reg_score, url))
            elif mtype in snap_type_rank:
                best_snaps.append((snap_type_rank[mtype] + reg_score, url))
            elif mtype in video_type_rank:
                best_videos.append((video_type_rank[mtype] + reg_score, url))

        if best_posters:
            best_posters.sort(key=lambda item: item[0], reverse=True)
            cand.poster_url = best_posters[0][1]
            cand.jacket_url = best_posters[0][1]

        if best_marquees:
            best_marquees.sort(key=lambda item: item[0], reverse=True)
            cand.marquee_url = best_marquees[0][1]

        if best_snaps:
            best_snaps.sort(key=lambda item: item[0], reverse=True)
            for _, snap_url in best_snaps:
                cand.add_unique_snapshot(snap_url)

        if best_videos:
            best_videos.sort(key=lambda item: item[0], reverse=True)
            cand.video_url = best_videos[0][1]

        return cand if (cand.poster_url or cand.snapshot_urls) else None

    @staticmethod
    def _extract_jeux_from_response(resp: Any) -> List[Dict[str, Any]]:
        """Extract list of `<jeu>` dicts from either JSON or XML ScreenScraper HTTP response."""
        text = (getattr(resp, "text", "") or "").lstrip()
        if text.startswith("<?xml") or text.startswith("<Data") or text.startswith("<jeu"):
            return parse_jeu_infos_xml(text)
        try:
            payload = resp.json()
        except Exception:
            return parse_jeu_infos_xml(text) if text else []
        resp_obj = (payload.get("response") or {}) if isinstance(payload, dict) else {}
        jeux = resp_obj.get("jeux")
        if isinstance(jeux, list):
            return [j for j in jeux if isinstance(j, dict)]
        jeu_single = resp_obj.get("jeu")
        if isinstance(jeu_single, dict):
            return [jeu_single]
        return []

    def _pick_discovery_token(
        self,
        sys_key: str,
        req_genres: List[str],
        used_tokens: Set[Tuple[str, str]],
    ) -> str:
        """
        Select a high-entropy search token for `jeuRecherche.php`, interleaving genre-affinitive
        stems, hardware stems, and broad 2-letter prefixes while avoiding recently used tokens
        across consecutive discovery calls.
        """
        genre_bucket = ",".join(sorted(req_genres)) if req_genres else "all"
        recent = self._recent_tokens.get((sys_key, genre_bucket))
        if recent is None:
            recent = deque(maxlen=28)
            self._recent_tokens[(sys_key, genre_bucket)] = recent

        genre_pool: List[str] = []
        for g in req_genres:
            for tok in _GENRE_DISCOVERY_TOKENS.get(g, []):
                if tok not in genre_pool:
                    genre_pool.append(tok)
        sys_stems = _SYSTEM_SPECIFIC_STEMS.get(sys_key) or []

        roll = _SYS_RNG.random()
        if genre_pool and sys_stems:
            if roll < 0.45:
                primary_pool = genre_pool
            elif roll < 0.75:
                primary_pool = sys_stems
            else:
                primary_pool = _SEARCH_SYLLABLES
        elif genre_pool:
            primary_pool = genre_pool if roll < 0.58 else _SEARCH_SYLLABLES
        elif sys_stems:
            primary_pool = sys_stems if roll < 0.65 else _SEARCH_SYLLABLES
        else:
            primary_pool = _SEARCH_SYLLABLES

        recent_set = set(recent)
        fresh = [
            t
            for t in primary_pool
            if (sys_key, t) not in used_tokens and t not in recent_set
        ]
        if not fresh:
            combined = list(dict.fromkeys(genre_pool + sys_stems + _SEARCH_SYLLABLES))
            fresh = [
                t
                for t in combined
                if (sys_key, t) not in used_tokens and t not in recent_set
            ]
        if not fresh:
            fresh = [t for t in primary_pool if (sys_key, t) not in used_tokens] or list(primary_pool)

        token = _SYS_RNG.choice(fresh)
        used_tokens.add((sys_key, token))
        recent.append(token)
        return token

    async def fetch_random_unseen(
        self,
        criteria: FilterCriteria,
        seen_ids: Set[str],
        count: int = 1,
    ) -> List[MediaCandidate]:
        if not self.is_configured():
            return []

        creds = self._credentials()
        valid_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.strip().lower() != "all" and resolve_canonical_system(s) in SYSTEM_METADATA
        ] or list(SYSTEM_METADATA.keys())
        req_genres = [
            resolve_canonical_genre("games", g)
            for g in (criteria.genres or [])
            if g and g.strip().lower() != "all"
        ]

        results: List[MediaCandidate] = []
        overflow_candidates: List[Tuple[MediaCandidate, Set[str]]] = []
        staged_keys: Set[str] = set()
        used_tokens: Set[Tuple[str, str]] = set()
        max_attempts = max(10 if req_genres else 6, count * 5)

        async with create_async_http_client(timeout=6.0) as client:
            for _ in range(max_attempts):
                if len(results) >= count:
                    break
                sys_key = _SYS_RNG.choice(valid_systems)
                sys_meta = SYSTEM_METADATA[sys_key]
                token = self._pick_discovery_token(sys_key, req_genres, used_tokens)

                params: Dict[str, str] = {
                    "softname": "dmd-media-showcase",
                    "output": "json",
                    "systemeid": str(sys_meta["ss_id"]),
                    "recherche": token,
                }
                if creds["devid"]:
                    params["devid"] = creds["devid"]
                    params["devpassword"] = creds["devpass"]
                if creds["user"]:
                    params["ssid"] = creds["user"]
                    params["sspassword"] = creds["pass"]

                try:
                    resp = await client.get(
                        "https://api.screenscraper.fr/api2/jeuRecherche.php",
                        params=params,
                    )
                    if resp.status_code != 200:
                        continue
                    jeux = self._extract_jeux_from_response(resp)
                    _SYS_RNG.shuffle(jeux)
                    # Prioritize parent games (cloneof == 0) over clones while preserving random order within each tier
                    jeux.sort(
                        key=lambda j: 0
                        if _extract_node_text(j.get("cloneof")) in ("", "0")
                        else 1
                    )
                    token_primary_added = False
                    for jeu in jeux:
                        raw_gid = str(jeu.get("id") or "").strip()
                        if raw_gid and (
                            f"games_ss_{raw_gid}" in seen_ids
                            or f"games_ss_{raw_gid}" in staged_keys
                        ):
                            continue
                        clone_parent = _extract_node_text(jeu.get("cloneof"))
                        if clone_parent not in ("", "0") and (
                            f"games_ss_{clone_parent}" in seen_ids
                            or f"games_ss_{clone_parent}" in staged_keys
                        ):
                            continue

                        cand = self._parse_game_node(jeu, sys_key, criteria.lang, criteria=criteria)
                        if not cand:
                            if raw_gid:
                                seen_ids.add(f"games_ss_{raw_gid}")
                            continue

                        cand_sys = str((cand.extra_meta or {}).get("system") or sys_key)
                        title_slug = re.sub(r"[^a-z0-9]+", "_", cand.title.lower()).strip("_")
                        title_key = f"games_ss_title_{title_slug}"
                        fp_key = build_game_fingerprint(cand_sys, cand.title)

                        dedup_keys = {cand.id, title_key}
                        if fp_key:
                            dedup_keys.add(fp_key)
                        if clone_parent not in ("", "0"):
                            dedup_keys.add(f"games_ss_{clone_parent}")

                        if dedup_keys & seen_ids or dedup_keys & staged_keys:
                            seen_ids.add(cand.id)
                            continue

                        # When count > 1, take at most 1 primary candidate per query token first
                        # to guarantee cross-prefix diversity and prevent alphabetical clustering.
                        if not token_primary_added:
                            seen_ids.update(dedup_keys)
                            results.append(cand)
                            token_primary_added = True
                            if len(results) >= count:
                                break
                        else:
                            staged_keys.update(dedup_keys)
                            overflow_candidates.append((cand, dedup_keys))
                except Exception as exc:
                    logger.debug("[ScreenScraper] Discovery error: %s", exc)

        if len(results) < count and overflow_candidates:
            _SYS_RNG.shuffle(overflow_candidates)
            for cand, dedup_keys in overflow_candidates:
                if len(results) >= count:
                    break
                if dedup_keys & seen_ids:
                    continue
                seen_ids.update(dedup_keys)
                results.append(cand)

        return results[:count]

    async def search_candidates(
        self,
        query: str,
        criteria: FilterCriteria,
        limit: int = 8,
    ) -> List[MediaCandidate]:
        q_clean = (query or "").strip()
        if not self.is_configured() or not q_clean or limit <= 0:
            return []

        target_limit = max(1, int(limit))
        creds = self._credentials()
        valid_systems = [
            resolve_canonical_system(s)
            for s in (criteria.systems or [])
            if s and s.strip().lower() != "all" and resolve_canonical_system(s) in SYSTEM_METADATA
        ]

        results: List[MediaCandidate] = []
        seen_keys: Set[str] = set()

        def _base_params(system_id: Optional[str] = None) -> Dict[str, str]:
            p: Dict[str, str] = {
                "softname": "dmd-media-showcase",
                "output": "json",
                "recherche": q_clean,
            }
            if system_id:
                p["systemeid"] = system_id
            if creds["devid"]:
                p["devid"] = creds["devid"]
                p["devpassword"] = creds["devpass"]
            if creds["user"]:
                p["ssid"] = creds["user"]
                p["sspassword"] = creds["pass"]
            return p

        def _resolve_hint_sys(jeu: Dict[str, Any], default_sys: str) -> str:
            if default_sys:
                return default_sys
            if not valid_systems:
                return ""
            sys_node = jeu.get("systeme")
            if isinstance(sys_node, dict):
                sid = str(sys_node.get("id") or "").strip()
                stxt = str(sys_node.get("text") or sys_node.get("nom") or "").strip().lower()
            else:
                sid = ""
                stxt = str(sys_node or "").strip().lower()
            for strict_sys in ("cps1", "cps2", "cps3", "neogeo"):
                if strict_sys in valid_systems:
                    tokens = _STRICT_HARDWARE_SS_IDS[strict_sys]
                    if sid == tokens[0] or (sid in ("", "75") and any(t in stxt for t in tokens[1:])):
                        return strict_sys
            return ""

        def _ingest_jeux(jeux: List[Dict[str, Any]], default_sys: str) -> None:
            # Stable sort: keep ScreenScraper relevance order while prioritizing parent ROMs (cloneof == 0)
            ordered = sorted(
                jeux,
                key=lambda j: 0 if _extract_node_text(j.get("cloneof")) in ("", "0") else 1,
            )
            for jeu in ordered:
                if len(results) >= target_limit:
                    break
                raw_gid = str(jeu.get("id") or "").strip()
                if raw_gid and f"games_ss_{raw_gid}" in seen_keys:
                    continue
                clone_parent = _extract_node_text(jeu.get("cloneof"))
                if clone_parent not in ("", "0") and f"games_ss_{clone_parent}" in seen_keys:
                    continue

                hint_sys = _resolve_hint_sys(jeu, default_sys)
                cand = self._parse_game_node(jeu, hint_sys, criteria.lang, criteria=criteria)
                if not cand:
                    continue

                cand_sys = str((cand.extra_meta or {}).get("system") or hint_sys or "arcade")
                fp_key = build_game_fingerprint(cand_sys, cand.title)
                dedup_keys = {cand.id}
                if fp_key:
                    dedup_keys.add(fp_key)
                if clone_parent not in ("", "0"):
                    dedup_keys.add(f"games_ss_{clone_parent}")

                if dedup_keys & seen_keys:
                    continue
                seen_keys.update(dedup_keys)
                results.append(cand)

        try:
            async with create_async_http_client(timeout=6.0) as client:
                if len(valid_systems) == 1:
                    only_sys = valid_systems[0]
                    ss_id = str(SYSTEM_METADATA[only_sys]["ss_id"])
                    resp = await client.get(
                        "https://api.screenscraper.fr/api2/jeuRecherche.php",
                        params=_base_params(system_id=ss_id),
                    )
                    if resp.status_code == 200:
                        _ingest_jeux(self._extract_jeux_from_response(resp), only_sys)
                else:
                    resp = await client.get(
                        "https://api.screenscraper.fr/api2/jeuRecherche.php",
                        params=_base_params(),
                    )
                    if resp.status_code == 200:
                        _ingest_jeux(self._extract_jeux_from_response(resp), "")

                    if valid_systems and len(results) < target_limit:
                        queried_ss_ids: Set[str] = set()
                        for sys_key in valid_systems:
                            if len(results) >= target_limit or len(queried_ss_ids) >= 3:
                                break
                            ss_id = str(SYSTEM_METADATA[sys_key]["ss_id"])
                            if ss_id in queried_ss_ids:
                                continue
                            queried_ss_ids.add(ss_id)
                            sub_resp = await client.get(
                                "https://api.screenscraper.fr/api2/jeuRecherche.php",
                                params=_base_params(system_id=ss_id),
                            )
                            if sub_resp.status_code == 200:
                                _ingest_jeux(self._extract_jeux_from_response(sub_resp), sys_key)
        except Exception as exc:
            logger.debug("[ScreenScraper] Search error for '%s': %s", q_clean, exc)

        return results[:target_limit]

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        cands = await self.search_candidates(
            query=query,
            criteria=FilterCriteria(domain="games", systems=[], genres=[], decades=[], lang=lang),
            limit=1,
        )
        return cands[0] if cands else None
