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
