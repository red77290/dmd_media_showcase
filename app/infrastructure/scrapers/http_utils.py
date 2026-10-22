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

logger = logging.getLogger("dmd_showcase.scrapers.http")

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


async def fetch_wikipedia_localized_synopsis(
    client: httpx.AsyncClient,
    title: str,
    lang: str = "fr",
    domain_hint: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch localized synopsis and high-res original image from Wikipedia REST API
    when a keyless English API returns an untranslated summary.
    """
    short_lang = (lang or "fr").split("-")[0].lower()
    langs = [short_lang] if short_lang == "en" else [short_lang, "en"]
    for try_lang in langs:
        candidates = [f"{title} ({domain_hint})".strip(), title] if domain_hint else [title]
        for cand in candidates:
            slug = urllib.parse.quote(cand.replace(" ", "_"))
            url = f"https://{try_lang}.wikipedia.org/api/rest_v1/page/summary/{slug}"
            try:
                resp = await client.get(url, timeout=3.0)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if data.get("type") == "disambiguation":
                    continue
                extract = clean_synopsis_text(data.get("extract"), max_chars=300)
                thumb = (
                    (data.get("originalimage") or {}).get("source")
                    or (data.get("thumbnail") or {}).get("source")
                )
                if extract and len(extract) > 35:
                    return extract, thumb
            except Exception:
                continue
    return None, None


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

