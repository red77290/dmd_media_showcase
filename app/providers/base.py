"""
Base provider contract supporting live random discovery across exhaustive API
categories, language-agnostic trailer/still enrichment, and on-demand queries.
"""

from __future__ import annotations

import html
import logging
import re
import socket
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from app.models import DomainType, SessionRegisterRequest

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_ORIG_GETADDRINFO = socket.getaddrinfo
_IPV4_INSTALLED = False


def install_ipv4_preference() -> None:
    """
    Prefer IPv4 (AF_INET) DNS records before IPv6 (AF_INET6) so Docker bridge
    containers without global IPv6 routing never hang on AAAA SYN timeouts.
    """
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
    """Create an IPv4-first AsyncClient with standard User-Agent headers."""
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
            "DMDMediaShowcase/2.1"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    if headers:
        default_headers.update(headers)
    try:
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=default_headers,
            transport=transport,
        )
    except Exception:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=default_headers,
        )


def clean_synopsis_text(raw: Optional[str], max_chars: int = 280) -> str:
    """
    Strip HTML tags, normalize whitespace, and bound length so scrolling text
    on a DMD remains crisp, complete, and readable.
    """
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    no_tags = _TAG_RE.sub(" ", unescaped)
    compact = _WS_RE.sub(" ", no_tags).strip()
    if len(compact) <= max_chars:
        return compact
    cut = compact[: max_chars - 1].rsplit(" ", 1)[0]
    return f"{cut}…"


@dataclass
class RawMediaCandidate:
    """
    Raw media item returned by a scraper provider before DMD image/video processing.
    """

    id: str
    domain: DomainType
    title: str
    year: str
    badge: str
    badge_color: str
    subtitle: str
    rating: str
    synopsis: str
    lang: str
    poster_url: Optional[str] = None
    jacket_url: Optional[str] = None
    marquee_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    snapshot_urls: List[str] = field(default_factory=list)
    image_urls: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    youtube_url: Optional[str] = None
    source_used: str = "live"
    theme_palette: Tuple[int, int, int] = (230, 74, 25)
    extra_meta: Dict[str, Any] = field(default_factory=dict)

    def add_unique_image(self, url: Optional[str]) -> None:
        """Append an image URL to `snapshot_urls` and `image_urls` if valid and unique."""
        if not url or not url.startswith(("http://", "https://")):
            return
        if url not in self.snapshot_urls and url != self.poster_url and url != self.marquee_url:
            self.snapshot_urls.append(url)
        if url not in self.image_urls:
            self.image_urls.append(url)
        if not self.backdrop_url:
            self.backdrop_url = url

    def resolved_jacket_url(self) -> Optional[str]:
        """Return the high-resolution jacket/poster URL for `jacket_panorama`."""
        return self.jacket_url or self.poster_url


async def fetch_dailymotion_trailer_and_stills(
    client: httpx.AsyncClient,
    query: str,
    max_snapshots: int = 3,
    min_duration: int = 12,
    max_duration: int = 360,
) -> Tuple[Optional[str], List[str]]:
    """
    Search Dailymotion's public keyless API for a trailer/gameplay/opening clip
    (language-agnostic since the DMD is mute) and return `(video_page_url, thumbnail_urls)`.
    """
    if not query.strip():
        return None, []
    try:
        resp = await client.get(
            "https://api.dailymotion.com/videos",
            params={
                "search": query,
                "limit": 5,
                "fields": "id,title,url,duration,thumbnail_720_url,thumbnail_480_url",
            },
            timeout=3.5,
        )
        if resp.status_code != 200:
            return None, []
        items = resp.json().get("list") or []
        chosen_url: Optional[str] = None
        stills: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            dur = int(item.get("duration") or 0)
            url = item.get("url")
            thumb = item.get("thumbnail_720_url") or item.get("thumbnail_480_url")
            if thumb and str(thumb).startswith("http") and thumb not in stills:
                stills.append(str(thumb))
            if not chosen_url and url and (min_duration <= dur <= max_duration or dur == 0):
                chosen_url = str(url)
        if not chosen_url and items:
            first_url = items[0].get("url")
            if first_url:
                chosen_url = str(first_url)
        return chosen_url, stills[:max_snapshots]
    except Exception as exc:
        logger.debug("Dailymotion lookup failed for %s: %s", query, exc)
        return None, []


async def fetch_imdb_suggestion_items(
    client: httpx.AsyncClient,
    query: str,
    qid_filter: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Query IMDb's public keyless CDN suggestion endpoint (`v3.sg.media-imdb.com`).
    `qid_filter` can filter e.g. `{"movie"}` or `{"tvSeries", "tvMiniSeries"}`.
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


async def fetch_wikipedia_summary(
    client: httpx.AsyncClient,
    title: str,
    lang: str = "fr",
    domain_hint: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetch localized synopsis and high-res thumbnail from Wikipedia REST API
    (`https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}`).
    Falls back to English Wikipedia if localized summary is missing.
    """
    langs = [lang] if lang == "en" else [lang, "en"]
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
                extract = clean_synopsis_text(data.get("extract"), max_chars=260)
                thumb = (
                    (data.get("originalimage") or {}).get("source")
                    or (data.get("thumbnail") or {}).get("source")
                )
                if extract and len(extract) > 35:
                    return extract, thumb
            except Exception:
                continue
    return None, None


class BaseMediaProvider(ABC):
    """
    Abstract provider contract for a media domain (`games`, `movies`, `series`, `anime`).
    """

    domain: DomainType

    @abstractmethod
    async def fetch_random_candidates(
        self,
        req: SessionRegisterRequest,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[RawMediaCandidate]:
        """Return `req.count` random candidates matching the requested filters and language."""

    @abstractmethod
    async def search_on_demand(
        self,
        query: str,
        lang: str = "fr",
        criteria: Optional[Any] = None,
    ) -> Optional[RawMediaCandidate]:
        """Search and return the best matching item for an on-demand / voice request."""

    @abstractmethod
    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        """Return the available filter categories for UI auto-discovery."""
