"""
Primary Games Scraper Adapter: ScreenScraper API v2 (`api.screenscraper.fr`).
Implements `IScraperSource` with uniform random discovery across all retro systems,
strictly following ScreenScraper's `jeuInfos.xml` / `jeuRecherche.php` schema.
"""

from __future__ import annotations

import logging
import random
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import (
    CANONICAL_DECADES,
    GAME_GENRE_CATALOG,
    localize_label,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.persistence.server_config_store import server_config_store
from app.infrastructure.scrapers.catalog_metadata import GAME_GENRES, SYSTEM_METADATA
from app.infrastructure.scrapers.http_utils import clean_synopsis_text, create_async_http_client

logger = logging.getLogger("dmd_showcase.scrapers.games.screenscraper")

_SEARCH_SYLLABLES = [
    "ma", "st", "ca", "so", "dr", "fi", "ki", "me", "su", "ba",
    "to", "ra", "da", "ko", "sh", "ga", "le", "pa", "ch", "bo",
    "al", "ar", "ta", "mo", "ro", "ni", "sa", "te", "ka", "cr",
    "sf", "vs", "mu", "pu", "sp", "wi", "bl", "gr", "tr", "de",
    "fa", "go", "ha", "jo", "la", "lu", "mi", "na", "ne", "no",
    "pe", "pl", "po", "pr", "qu", "re", "ri", "ru", "se", "si",
    "sk", "sl", "sm", "sn", "sq", "sw", "th", "ti", "tu", "un",
    "va", "ve", "vi", "wa", "wo", "xe", "ya", "yo", "za", "ze",
]

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
    """Return ordered region preference list for a target language code (`fr`, `en`, `es`, `de`, `it`)."""
    base = [short_lang, "eu", "wor", "ss", "us", "jp", "br", "au", "kr"]
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
                                "langue": str(s_el.attrib.get("langue") or "").strip().lower(),
                                "text": txt,
                            }
                        )
            elif (syn_parent.text or "").strip():
                syn_list.append(
                    {
                        "langue": str(syn_parent.attrib.get("langue") or "en").strip().lower(),
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
                            "langue": str(g_el.attrib.get("langue") or "").strip().lower(),
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
        """Normalize ScreenScraper JSON/XML `<genres>` into a flat list of `{'langue', 'text', 'principale'}`."""
        if isinstance(genres_raw, dict):
            genres_raw = genres_raw.get("genre") or genres_raw.get("genres") or [genres_raw]
        if not isinstance(genres_raw, list):
            return []
        entries: List[Dict[str, str]] = []
        for g in genres_raw:
            if not isinstance(g, dict):
                continue
            principale = str(g.get("principale") or "1").strip()
            if isinstance(g.get("noms"), list):
                for n in g["noms"]:
                    if isinstance(n, dict) and n.get("text"):
                        entries.append(
                            {
                                "langue": str(n.get("langue") or "").strip().lower(),
                                "text": str(n["text"]).strip(),
                                "principale": principale,
                            }
                        )
            elif g.get("text"):
                entries.append(
                    {
                        "langue": str(g.get("langue") or "").strip().lower(),
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

        # 1. Validate <systeme id="...">
        sys_node = jeu.get("systeme")
        if isinstance(sys_node, dict):
            sys_id_resp = str(sys_node.get("id") or "").strip()
            sys_text_resp = str(sys_node.get("text") or "").strip().lower()
        else:
            sys_id_resp = ""
            sys_text_resp = str(sys_node or "").strip().lower()

        canon_sys = resolve_canonical_system(sys_key) if sys_key else ""
        if (not canon_sys or canon_sys in ("arcade", "mame")) and sys_id_resp in _SS_ID_TO_SYS_KEY:
            canon_sys = _SS_ID_TO_SYS_KEY[sys_id_resp]
        elif not canon_sys and sys_text_resp:
            resolved_from_text = resolve_canonical_system(sys_text_resp)
            if resolved_from_text in SYSTEM_METADATA:
                canon_sys = resolved_from_text
        if not canon_sys or canon_sys not in SYSTEM_METADATA:
            canon_sys = "arcade"

        if sys_key and resolve_canonical_system(sys_key) in _STRICT_HARDWARE_SS_IDS:
            strict_sys = resolve_canonical_system(sys_key)
            expected_tokens = _STRICT_HARDWARE_SS_IDS[strict_sys]
            expected_id = expected_tokens[0]
            if sys_id_resp or sys_text_resp:
                id_ok = sys_id_resp == expected_id
                text_ok = any(tok in sys_text_resp for tok in expected_tokens[1:])
                if not id_ok and not text_ok:
                    return None
            canon_sys = strict_sys

        if criteria and criteria.systems:
            req_systems = {
                resolve_canonical_system(s)
                for s in criteria.systems
                if s and s.strip().lower() != "all"
            }
            if req_systems and canon_sys not in req_systems:
                if not (canon_sys in ("cps1", "cps2", "cps3", "neogeo") and req_systems & {"arcade", "mame"}):
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

        # 4. Parse <genres> and enforce genre filter if requested
        genre_entries = self._extract_genre_entries(jeu.get("genres"))
        canon_genres: List[str] = []
        localized_genre_label = ""
        for g_entry in genre_entries:
            g_text = g_entry.get("text") or ""
            if not g_text:
                continue
            cg = resolve_canonical_genre("games", g_text)
            if cg and cg not in canon_genres:
                canon_genres.append(cg)
            if g_entry.get("langue") == short_lang and not localized_genre_label:
                localized_genre_label = g_text

        if criteria and criteria.genres:
            req_genres = {
                resolve_canonical_genre("games", g)
                for g in criteria.genres
                if g and g.strip().lower() != "all"
            }
            if req_genres:
                if not canon_genres or not (set(canon_genres) & req_genres):
                    return None

        primary_genre = canon_genres[0] if canon_genres else ""
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
        synopsis = (
            synopsis_by_lang.get(short_lang)
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

        results: List[MediaCandidate] = []
        used_tokens: Set[Tuple[str, str]] = set()
        max_attempts = max(6, count * 4)

        async with create_async_http_client(timeout=6.0) as client:
            for _ in range(max_attempts):
                if len(results) >= count:
                    break
                sys_key = random.choice(valid_systems)
                sys_meta = SYSTEM_METADATA[sys_key]
                sys_stems = _SYSTEM_SPECIFIC_STEMS.get(sys_key)
                if sys_stems and random.random() < 0.7:
                    token_pool = sys_stems
                else:
                    token_pool = _SEARCH_SYLLABLES

                avail_tokens = [t for t in token_pool if (sys_key, t) not in used_tokens] or token_pool
                token = random.choice(avail_tokens)
                used_tokens.add((sys_key, token))

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
                    random.shuffle(jeux)
                    # Prioritize parent games (cloneof == 0) over clones
                    jeux.sort(
                        key=lambda j: 0
                        if _extract_node_text(j.get("cloneof")) in ("", "0")
                        else 1
                    )
                    for jeu in jeux:
                        raw_gid = str(jeu.get("id") or "").strip()
                        if raw_gid and f"games_ss_{raw_gid}" in seen_ids:
                            continue
                        cand = self._parse_game_node(jeu, sys_key, criteria.lang, criteria=criteria)
                        if not cand:
                            if raw_gid:
                                seen_ids.add(f"games_ss_{raw_gid}")
                            continue
                        title_slug = re.sub(r"[^a-z0-9]+", "_", cand.title.lower()).strip("_")
                        title_key = f"games_ss_title_{title_slug}"
                        if cand.id in seen_ids or title_key in seen_ids:
                            seen_ids.add(cand.id)
                            continue
                        seen_ids.add(cand.id)
                        seen_ids.add(title_key)
                        results.append(cand)
                        if len(results) >= count:
                            break
                except Exception as exc:
                    logger.debug("[ScreenScraper] Discovery error: %s", exc)

        return results

    async def search_by_query(self, query: str, lang: str = "fr") -> Optional[MediaCandidate]:
        if not self.is_configured() or not query.strip():
            return None
        creds = self._credentials()
        params: Dict[str, str] = {
            "softname": "dmd-media-showcase",
            "output": "json",
            "recherche": query.strip(),
        }
        if creds["devid"]:
            params["devid"] = creds["devid"]
            params["devpassword"] = creds["devpass"]
        if creds["user"]:
            params["ssid"] = creds["user"]
            params["sspassword"] = creds["pass"]
        try:
            async with create_async_http_client(timeout=6.0) as client:
                resp = await client.get(
                    "https://api.screenscraper.fr/api2/jeuRecherche.php",
                    params=params,
                )
                if resp.status_code == 200:
                    jeux = self._extract_jeux_from_response(resp)
                    for jeu in jeux:
                        cand = self._parse_game_node(jeu, "arcade", lang)
                        if cand:
                            return cand
        except Exception:
            pass
        return None
