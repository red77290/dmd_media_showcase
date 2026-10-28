"""
HTTP & text cleaning utilities shared by infrastructure scraper adapters.
"""

from __future__ import annotations

import html
import logging
import re
import socket
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.security.log_redactor import (
    SecretRedactingLogFilter,
    install_secret_log_redaction,
)

logger = logging.getLogger("dmd_showcase.scrapers.http")
install_secret_log_redaction()

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ORIG_GETADDRINFO = socket.getaddrinfo
_IPV4_INSTALLED = False


def install_ipv4_preference() -> None:
    """Prefer IPv4 (AF_INET) DNS records before IPv6 to prevent Docker AAAA SYN timeouts."""
    global _IPV4_INSTALLED
    if _IPV4_INSTALLED:
        return

    def _ipv4_first_getaddrinfo(
        host: Any,
        port: Any,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> List[Any]:
        res = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 if v4 else res

    socket.getaddrinfo = _ipv4_first_getaddrinfo  # type: ignore[assignment]
    _IPV4_INSTALLED = True


install_ipv4_preference()


def format_exception_detail(exc: BaseException) -> str:
    """Format an exception with its class name so empty `TimeoutError()`/`ReadTimeout("")` never log blank."""
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def create_async_http_client(
    timeout: float = 6.0,
    headers: Optional[Dict[str, str]] = None,
) -> httpx.AsyncClient:
    """Create an IPv4-first AsyncClient with a Wikimedia-compliant User-Agent."""
    default_headers = {
        "User-Agent": (
            "DMDMediaShowcase/3.0 "
            "(https://github.com/red77290/dmd_media_showcase; contact@arcadematrix.org) "
            "python-httpx/0.27"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        default_headers.update(headers)
    try:
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", verify=False, retries=1)
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False,
            headers=default_headers,
            transport=transport,
        )
    except Exception:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False,
            headers=default_headers,
        )


def clean_synopsis_text(raw: Optional[str], max_chars: int = 320) -> str:
    """Strip HTML tags, normalize whitespace, and bound length for organic DMD scrolling."""
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    no_tags = _TAG_RE.sub(" ", unescaped)
    compact = _WS_RE.sub(" ", no_tags).strip()
    if len(compact) <= max_chars:
        return compact
    cut = compact[: max_chars - 1].rsplit(" ", 1)[0]
    return f"{cut}…"


def build_tmdb_auth(raw_key: Optional[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Support both TMDB v3 API keys (32-char hex passed as `?api_key=`) and
    TMDB v4 Read Access Tokens (JWT starting with `eyJ` passed as `Authorization: Bearer`).
    Returns `(query_params_dict, headers_dict)`.
    """
    clean = (raw_key or "").strip()
    if not clean:
        return {}, {}
    if clean.lower().startswith("bearer "):
        clean = clean[7:].strip()
    if clean.startswith("eyJ") or len(clean) > 45:
        return {}, {"Authorization": f"Bearer {clean}"}
    return {"api_key": clean}, {}


MEDIA_TOPIC_KEYWORDS = {
    "fr": ("film", "série", "anime", "manga", "animation", "télévision", "feuilleton", "cinéma", "vidéo", "manga"),
    "es": ("película", "serie", "anime", "manga", "animación", "televisión", "cine", "videojuego"),
    "de": ("film", "serie", "anime", "manga", "fernsehserie", "animation", "kino", "videospiel"),
    "it": ("film", "serie", "anime", "manga", "animazione", "televisione", "cinema", "videogioco"),
    "en": ("film", "movie", "series", "anime", "manga", "animation", "television", "tv show", "video game"),
}

_GAME_DOMAIN_HINTS = {
    "game",
    "games",
    "video game",
    "jeu vidéo",
    "jeu video",
    "videojuego",
    "videospiel",
    "videogioco",
}

_GAME_EXTRACT_MARKERS = (
    "jeu vidéo",
    "jeux vidéo",
    "jeu d'",
    "jeu de ",
    "série de jeux",
    "video game",
    "arcade",
    "borne d'arcade",
    "videojuego",
    "videospiel",
    "videogioco",
    "gameplay",
    "super nintendo",
    "mega drive",
    "game boy",
    "famicom",
    "playstation",
)

_NON_GAME_LEAD_MARKERS = (
    "est un film",
    "is a film",
    "film américain",
    "film d'animation",
    "film réalisé",
    "directed by",
    "long métrage",
    "est une série télévisée",
    "est une série d'animation",
    "television series",
    "animated series",
    "série de jouets",
    "toy line",
    "bande dessinée",
    "roman de ",
)

_WIKI_TITLE_STOPWORDS = {
    "the", "of", "and", "in", "to", "for", "on", "at", "by", "le", "la", "les",
    "de", "du", "des", "un", "une", "et", "el", "los", "las", "del", "jeu", "video",
    "vidéo", "game", "games", "film", "serie", "série", "anime",
}


def _wiki_title_tokens(raw: str) -> List[str]:
    no_parens = re.sub(r"\s*\([^)]*\)", " ", raw or "")
    norm = (
        no_parens.lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("ô", "o")
        .replace("û", "u")
        .replace("ï", "i")
    )
    return [
        t
        for t in re.findall(r"[a-z0-9]+", norm)
        if (len(t) >= 2 or t.isdigit()) and t not in _WIKI_TITLE_STOPWORDS
    ]


def is_relevant_media_extract(
    data: Dict[str, Any],
    lang: str,
    is_game_domain: bool = False,
    query_title: str = "",
    trusted_titles: Optional[List[str]] = None,
) -> bool:
    """Validate that a Wikipedia summary matches the queried entity and media domain."""
    desc = str(data.get("description") or "").lower()
    extract = str(data.get("extract") or "").strip()
    extract_low = extract.lower()
    combined = f"{desc} {extract_low}"

    page_title = str(data.get("title") or "").strip()
    if query_title and page_title:
        trusted_norm = {
            re.sub(r"\s*\([^)]*\)", "", t).strip().lower()
            for t in (trusted_titles or [])
            if t and t.strip()
        }
        page_clean_low = re.sub(r"\s*\([^)]*\)", "", page_title).strip().lower()
        if page_clean_low not in trusted_norm:
            p_toks = set(_wiki_title_tokens(page_title))
            q_toks = set(_wiki_title_tokens(query_title))
            for tt in trusted_titles or []:
                q_toks.update(_wiki_title_tokens(tt))
            if q_toks and (not p_toks or not (p_toks & q_toks)):
                return False
            # Guard franchise subtitle collisions (e.g. "Zelda: Ocarina of Time" vs "Zelda: Majora's Mask")
            if ":" in query_title and ":" in page_title:
                q_sub = set(_wiki_title_tokens(query_title.split(":", 1)[1]))
                p_sub = set(_wiki_title_tokens(page_title.split(":", 1)[1]))
                if q_sub and p_sub and not (q_sub & p_sub):
                    return False

    if is_game_domain:
        lead_sentence = extract_low.split(".")[0] if extract_low else ""
        lead_combined = f"{desc} {lead_sentence}"
        has_lead_game_marker = any(
            m in lead_combined
            for m in ("jeu vidéo", "jeux vidéo", "jeu d'", "jeu de ", "video game", "arcade", "videojuego", "videospiel", "videogioco")
        )
        if not has_lead_game_marker and any(bad in lead_combined for bad in _NON_GAME_LEAD_MARKERS):
            return False
        return any(gm in combined for gm in _GAME_EXTRACT_MARKERS)

    keywords = MEDIA_TOPIC_KEYWORDS.get(lang, MEDIA_TOPIC_KEYWORDS["en"])
    return any(kw in combined for kw in keywords)


async def fetch_wikipedia_localized_synopsis(
    client: httpx.AsyncClient,
    title: str,
    lang: str = "fr",
    domain_hint: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch localized synopsis and high-res image from Wikipedia REST API adhering
    strictly to the requested `lang` (zero English fallback when `lang != "en"`):
    1. If `lang != "en"`, queries English Wikipedia interwiki `langlinks` (`enwiki -> {lang}wiki`)
       to translate the canonical English title into the exact official localized title.
    2. Queries `{lang}.wikipedia.org/api/rest_v1/page/summary/{localized_title}`.
    3. Falls back to localized MediaWiki search on `{lang}.wikipedia.org`.
    4. Validates entity title overlap and domain markers (prevents film/toy/cartoon misfires).
    """
    short_lang = (lang or "fr").split("-")[0].lower()
    clean_t = re.sub(r"[^\w\s\-\:\'\,\.]", " ", title).strip()
    if not clean_t:
        return None, None

    hint_low = (domain_hint or "").strip().lower()
    is_game_domain = hint_low in _GAME_DOMAIN_HINTS
    en_disambig = (
        "video game"
        if is_game_domain
        else {
            "film": "film",
            "movie": "film",
            "movies": "film",
            "series": "TV series",
            "tv": "TV series",
            "série télévisée": "TV series",
            "anime": "anime",
        }.get(hint_low, domain_hint)
    )

    # Step 1: Interwiki resolution via en.wikipedia langlinks if target is not English
    localized_titles: List[str] = []
    if short_lang != "en":
        search_titles = [f"{clean_t} ({en_disambig})", clean_t] if en_disambig else [clean_t]
        for st in search_titles:
            try:
                ll_url = "https://en.wikipedia.org/w/api.php"
                ll_params = {
                    "action": "query",
                    "titles": st,
                    "prop": "langlinks",
                    "lllang": short_lang,
                    "redirects": "1",
                    "format": "json",
                }
                resp = await client.get(ll_url, params=ll_params, timeout=2.5)
                if resp.status_code == 200:
                    pages = (resp.json().get("query") or {}).get("pages") or {}
                    for page in pages.values():
                        for llink in page.get("langlinks") or []:
                            resolved = str(llink.get("*") or "").strip()
                            if resolved and resolved not in localized_titles:
                                localized_titles.append(resolved)
            except Exception:
                pass
            if localized_titles:
                break

    # Suffixes localized to the target language
    suffix_map = {
        "fr": {
            "film": "film",
            "anime": "anime",
            "series": "série télévisée",
            "tv": "série télévisée",
            "game": "jeu vidéo",
        },
        "es": {
            "film": "película",
            "anime": "anime",
            "series": "serie de televisión",
            "tv": "serie de televisión",
            "game": "videojuego",
        },
        "de": {
            "film": "Film",
            "anime": "Anime",
            "series": "Fernsehserie",
            "tv": "Fernsehserie",
            "game": "Videospiel",
        },
        "it": {
            "film": "film",
            "anime": "anime",
            "series": "serie televisiva",
            "tv": "serie televisiva",
            "game": "videogioco",
        },
        "en": {
            "film": "film",
            "anime": "anime",
            "series": "TV series",
            "tv": "TV series",
            "game": "video game",
        },
    }
    lookup_domain_key = "game" if is_game_domain else hint_low
    loc_suffix = suffix_map.get(short_lang, suffix_map["en"]).get(lookup_domain_key, domain_hint)

    candidates: List[str] = []
    if loc_suffix:
        candidates.append(f"{clean_t} ({loc_suffix})")
    for lt in localized_titles:
        if lt not in candidates:
            candidates.append(lt)
    if clean_t not in candidates:
        candidates.append(clean_t)

    # Step 2: Query summary API on `{short_lang}.wikipedia.org`
    for cand in candidates:
        slug = urllib.parse.quote(cand.replace(" ", "_"))
        url = f"https://{short_lang}.wikipedia.org/api/rest_v1/page/summary/{slug}"
        try:
            resp = await client.get(url, timeout=3.0)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if data.get("type") == "disambiguation":
                continue
            if not is_relevant_media_extract(
                data,
                short_lang,
                is_game_domain=is_game_domain,
                query_title=clean_t,
                trusted_titles=localized_titles,
            ):
                continue
            extract = clean_synopsis_text(data.get("extract"), max_chars=320)
            thumb = (
                (data.get("originalimage") or {}).get("source")
                or (data.get("thumbnail") or {}).get("source")
            )
            if extract and len(extract) > 35:
                return extract, thumb
        except Exception:
            continue

    # Step 3: Localized MediaWiki search if direct slug lookups missed
    try:
        search_query = f"{clean_t} {loc_suffix}".strip()
        sr_url = f"https://{short_lang}.wikipedia.org/w/api.php"
        sr_params = {
            "action": "query",
            "list": "search",
            "srsearch": search_query,
            "srlimit": "2",
            "format": "json",
        }
        sr_resp = await client.get(sr_url, params=sr_params, timeout=2.5)
        if sr_resp.status_code == 200:
            hits = (sr_resp.json().get("query") or {}).get("search") or []
            for hit in hits:
                hit_title = str(hit.get("title") or "").strip()
                if not hit_title:
                    continue
                slug = urllib.parse.quote(hit_title.replace(" ", "_"))
                url = f"https://{short_lang}.wikipedia.org/api/rest_v1/page/summary/{slug}"
                resp = await client.get(url, timeout=2.5)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("type") == "disambiguation":
                        continue
                    if is_relevant_media_extract(
                        data,
                        short_lang,
                        is_game_domain=is_game_domain,
                        query_title=clean_t,
                        trusted_titles=localized_titles,
                    ):
                        extract = clean_synopsis_text(data.get("extract"), max_chars=320)
                        thumb = (
                            (data.get("originalimage") or {}).get("source")
                            or (data.get("thumbnail") or {}).get("source")
                        )
                        if extract and len(extract) > 35:
                            return extract, thumb
    except Exception:
        pass

    return None, None


async def resolve_multilingual_media_details(
    client: httpx.AsyncClient,
    title: str,
    lang: str = "fr",
    domain_hint: str = "anime",
    year: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Unified 3-tier localized resolver respecting the DMD's dynamic `target_lang`:
    1. TMDB search in `target_lang` (supports both v3 API Key and v4 Bearer Token) + trailer lookup.
    2. TheTVDB v4 translation + trailer lookup if configured.
    3. Wikipedia interwiki langlinks bridge + localized summary.
    Returns `(localized_title, localized_synopsis, optional_trailer_url)`.
    """
    short_lang = (lang or "fr").split("-")[0].lower()
    resolved_title: Optional[str] = None
    resolved_synopsis: Optional[str] = None
    resolved_trailer: Optional[str] = None

    # Tier 1: TMDB Search in target_lang
    try:
        from app.infrastructure.persistence.server_config_store import server_config_store
        from app.infrastructure.scrapers.catalog_metadata import TMDB_LANG_MAP

        cfg = server_config_store.get_all()
        tmdb_key = str(cfg.get("tmdb_api_key") or "").strip()
        if tmdb_key:
            auth_params, auth_headers = build_tmdb_auth(tmdb_key)
            tmdb_lang = TMDB_LANG_MAP.get(short_lang, f"{short_lang}-{short_lang.upper()}")
            endpoint = "movie" if domain_hint in ("film", "movies", "movie") else "tv"
            year_key = "first_air_date_year" if endpoint == "tv" else "primary_release_year"

            t_params: Dict[str, str] = {
                **auth_params,
                "query": title,
                "language": tmdb_lang,
            }
            if year and len(year) == 4 and year.isdigit():
                t_params[year_key] = year

            t_resp = await client.get(
                f"https://api.themoviedb.org/3/search/{endpoint}",
                params=t_params,
                headers=auth_headers,
                timeout=2.5,
            )
            t_items = (t_resp.json().get("results") or []) if t_resp.status_code == 200 else []
            if not t_items and year_key in t_params:
                t_params_no_yr = {k: v for k, v in t_params.items() if k != year_key}
                t_resp2 = await client.get(
                    f"https://api.themoviedb.org/3/search/{endpoint}",
                    params=t_params_no_yr,
                    headers=auth_headers,
                    timeout=2.5,
                )
                t_items = (t_resp2.json().get("results") or []) if t_resp2.status_code == 200 else []

            if t_items:
                best = t_items[0]
                t_overview = clean_synopsis_text(best.get("overview"), max_chars=320)
                t_title = best.get("title") or best.get("name")
                if t_title:
                    resolved_title = str(t_title)
                if t_overview and len(t_overview) > 30:
                    resolved_synopsis = t_overview

                best_id = best.get("id")
                if best_id:
                    v_resp = await client.get(
                        f"https://api.themoviedb.org/3/{endpoint}/{best_id}/videos",
                        params={
                            **auth_params,
                            "language": tmdb_lang,
                            "include_video_language": f"{short_lang},en,ja,null",
                        },
                        headers=auth_headers,
                        timeout=2.0,
                    )
                    if v_resp.status_code == 200:
                        for v in v_resp.json().get("results") or []:
                            if v.get("site") == "YouTube" and v.get("key"):
                                resolved_trailer = f"https://www.youtube.com/watch?v={v['key']}"
                                if str(v.get("type", "")).lower() == "trailer":
                                    break

                if resolved_synopsis and resolved_trailer:
                    return resolved_title, resolved_synopsis, resolved_trailer
    except Exception:
        pass

    # Tier 2: TheTVDB v4 in target_lang
    try:
        from app.infrastructure.scrapers.tvdb_adapter import fetch_tvdb_localized_overview

        t_title, t_overview, t_trailer = await fetch_tvdb_localized_overview(
            title=title,
            media_type="movies" if domain_hint in ("film", "movies", "movie") else "series",
            lang=short_lang,
            year=year,
        )
        if t_title and not resolved_title:
            resolved_title = t_title
        if t_overview and len(t_overview) > 30 and not resolved_synopsis:
            resolved_synopsis = t_overview
        if t_trailer and not resolved_trailer:
            resolved_trailer = t_trailer
        if resolved_synopsis:
            return resolved_title, resolved_synopsis, resolved_trailer
    except Exception:
        pass

    # Tier 3: Wikipedia interwiki langlinks bridge
    if not resolved_synopsis and short_lang != "en":
        wiki_syn, _ = await fetch_wikipedia_localized_synopsis(
            client, title, lang=short_lang, domain_hint=domain_hint
        )
        if wiki_syn and len(wiki_syn) > 30:
            resolved_synopsis = wiki_syn

    return resolved_title, resolved_synopsis, resolved_trailer


async def fetch_imdb_suggestion_items(
    client: httpx.AsyncClient,
    query: str,
    qid_filter: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Query IMDb's public keyless CDN suggestion endpoint (`v3.sg.media-imdb.com`).
    Returns a list of raw IMDb suggestion dicts containing `id`, `l` (title),
    `y` (year), `q`/`qid` (type), `s` (stars), and `i.imageUrl` (Amazon CloudFront poster).
    """
    clean_q = re.sub(r"[^a-zA-Z0-9 ]+", " ", query).strip().lower()
    if not clean_q:
        return []
    first_char = clean_q[0] if clean_q[0].isalnum() else "x"
    encoded = urllib.parse.quote(clean_q[:40])
    url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{encoded}.json"
    try:
        resp = await client.get(url, timeout=3.5)
        if resp.status_code != 200:
            return []
        entries = resp.json().get("d") or []
        results: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            tid = str(entry.get("id") or "")
            if not tid.startswith("tt"):
                continue
            qid = str(entry.get("qid") or entry.get("q") or "").lower()
            if qid_filter and not any(f.lower() in qid for f in qid_filter):
                continue
            img_obj = entry.get("i")
            if not isinstance(img_obj, dict) or not img_obj.get("imageUrl"):
                continue
            results.append(entry)
        return results
    except Exception as exc:
        logger.debug("IMDb suggestion lookup failed for %s: %s", query, exc)
        return []


async def fetch_dailymotion_trailer_and_stills(
    client: httpx.AsyncClient,
    query: str,
    max_snapshots: int = 4,
) -> Tuple[Optional[str], List[str]]:
    """
    Query Dailymotion's public keyless REST API (`https://api.dailymotion.com/videos`)
    to retrieve a clean HD trailer page URL (`url`, supported by `yt-dlp`) and up to
    `max_snapshots` distinct 720p widescreen video stills (`thumbnail_720_url`).
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return None, []
    try:
        resp = await client.get(
            "https://api.dailymotion.com/videos",
            params={
                "search": clean_q,
                "fields": "id,title,url,duration,thumbnail_720_url",
                "limit": str(max(4, max_snapshots + 2)),
                "longer_than": "1",
                "shorter_than": "6",
            },
            timeout=3.8,
        )
        if resp.status_code != 200:
            return None, []
        items = resp.json().get("list") or []
        trailer_url: Optional[str] = None
        stills: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            v_url = str(item.get("url") or "").strip()
            if v_url.startswith("http") and trailer_url is None:
                trailer_url = v_url
            thumb = str(item.get("thumbnail_720_url") or "").strip()
            if thumb.startswith("http") and thumb not in stills and len(stills) < max_snapshots:
                stills.append(thumb)
        return trailer_url, stills
    except Exception as exc:
        logger.debug("Dailymotion trailer lookup failed for %s: %s", clean_q, exc)
        return None, []

