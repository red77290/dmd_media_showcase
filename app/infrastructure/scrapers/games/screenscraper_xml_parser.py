"""
ScreenScraper XML/JSON Response Parser & Regional `<jeu>` Node Mapper
(`app/infrastructure/scrapers/games/screenscraper_xml_parser.py`).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from app.domain.entities import FilterCriteria, MediaCandidate
from app.domain.taxonomy import (
    CANONICAL_DECADES,
    GAME_GENRE_CATALOG,
    is_disallowed_non_fighting_title,
    localize_label,
    normalize_category_slug,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.scrapers.catalog_metadata import GAME_GENRES, SYSTEM_METADATA
from app.infrastructure.scrapers.games.screenscraper_tokens import (
    NON_CPS_TITLE_TOKENS,
    SS_ID_TO_SYS_KEY,
    STRICT_HARDWARE_SS_IDS,
)
from app.infrastructure.scrapers.http_utils import clean_synopsis_text

logger = logging.getLogger("dmd_showcase.scrapers.games.screenscraper.parser")


def extract_node_text(node: Any) -> str:
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
        return extract_node_text(node[0])
    return ""


def region_priority_order(short_lang: str) -> List[str]:
    """Return ordered region preference list for `short_lang` (`fr`, `en`, `es`, `de`, `it`, `pt`, `ja`)."""
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


def score_region(region: str, priority: List[str]) -> int:
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
    into a list of normalized `<jeu>` dictionaries.
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


def extract_jeux_from_response(resp: Any) -> List[Dict[str, Any]]:
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


def extract_genre_entries(genres_raw: Any) -> List[Dict[str, str]]:
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


def matches_decades(year_str: str, decades: List[str]) -> bool:
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


def parse_screenscraper_game_node(
    jeu: Dict[str, Any],
    sys_key: str,
    lang: str,
    criteria: Optional[FilterCriteria] = None,
    source_id: str = "screenscraper",
) -> Optional[MediaCandidate]:
    """Validate and map a ScreenScraper `<jeu>` dict into a `MediaCandidate`."""
    if not isinstance(jeu, dict):
        return None
    if str(jeu.get("notgame") or "").strip().lower() in ("true", "1"):
        return None

    game_id = str(jeu.get("id") or "").strip()
    if not game_id:
        return None

    short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
    reg_priority = region_priority_order(short_lang)

    sys_node = jeu.get("systeme")
    if isinstance(sys_node, dict):
        sys_id_resp = str(sys_node.get("id") or "").strip()
        sys_text_resp = str(sys_node.get("text") or sys_node.get("nom") or "").strip().lower()
    else:
        sys_id_resp = ""
        sys_text_resp = str(sys_node or "").strip().lower()

    resp_sys = ""
    if sys_id_resp:
        if sys_id_resp in SS_ID_TO_SYS_KEY:
            resp_sys = SS_ID_TO_SYS_KEY[sys_id_resp]
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
    if requested_sys in STRICT_HARDWARE_SS_IDS:
        expected_tokens = STRICT_HARDWARE_SS_IDS[requested_sys]
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
        if resp_sys and resp_sys not in (
            "arcade",
            "mame",
            "fbneo",
            "cps1",
            "cps2",
            "cps3",
            "neogeo",
        ):
            return None
        canon_sys = (
            resp_sys if resp_sys in ("cps1", "cps2", "cps3", "neogeo") else requested_sys
        )
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

    noms_raw = jeu.get("noms")
    if isinstance(noms_raw, dict):
        noms_raw = noms_raw.get("nom") or noms_raw.get("noms") or [noms_raw]
    scored_titles: List[Tuple[int, str]] = []
    for nom in noms_raw or []:
        if isinstance(nom, dict) and nom.get("text"):
            txt = str(nom["text"]).strip()
            reg = str(nom.get("region") or "").strip().lower()
            if txt:
                scored_titles.append((score_region(reg, reg_priority), txt))
    if not scored_titles:
        return None
    scored_titles.sort(key=lambda item: item[0], reverse=True)
    title = scored_titles[0][1]

    title_low = title.lower()
    if canon_sys in ("cps1", "cps2", "cps3"):
        if any(bad_tok in title_low for bad_tok in NON_CPS_TITLE_TOKENS):
            return None

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
                scored_years.append((score_region(reg, reg_priority), m.group(1)))
    scored_years.sort(key=lambda item: item[0], reverse=True)
    year = scored_years[0][1] if scored_years else "1992"

    if criteria and criteria.decades:
        if not matches_decades(year, criteria.decades):
            return None

    genre_entries = extract_genre_entries(jeu.get("genres"))
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
            GAME_GENRE_CATALOG[primary_genre]["labels"],
            short_lang,
            GAME_GENRES.get(primary_genre, ""),
        )

    publisher = extract_node_text(jeu.get("editeur")) or extract_node_text(
        jeu.get("developpeur")
    )
    if localized_genre_label and publisher:
        subtitle = f"{localized_genre_label} • {publisher}"[:28]
    elif publisher:
        subtitle = f"{sys_meta['badge']} • {publisher}"[:28]
    elif localized_genre_label:
        subtitle = f"{localized_genre_label} • {year}"[:28]
    else:
        subtitle = f"{sys_meta['badge']} • {year}"[:28]

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

    note_raw = extract_node_text(jeu.get("note"))
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
        source_id=source_id,
        theme_palette=sys_meta["palette"],
        extra_meta={
            "system": canon_sys,
            "genre": primary_genre,
            "genres": canon_genres,
            "publisher": publisher,
        },
    )

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
            continue
        mtype = str(media.get("type") or "").strip()
        url = str(media.get("url") or media.get("text") or "").strip()
        if not url.startswith("http"):
            continue
        reg = str(media.get("region") or "").strip().lower()
        reg_score = score_region(reg, reg_priority)

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
