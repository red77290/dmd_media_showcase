"""
Base provider contract supporting both random rotation batches and future
on-demand / voice assistant queries.
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from app.models import DomainType, SessionRegisterRequest

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_synopsis_text(raw: Optional[str], max_chars: int = 260) -> str:
    """
    Strip HTML tags, normalize whitespace, and bound length so scrolling text
    on a 128x32 DMD remains crisp and readable.
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
    marquee_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    video_url: Optional[str] = None
    theme_palette: Tuple[int, int, int] = (230, 74, 25)
    extra_meta: Dict[str, str] = field(default_factory=dict)


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
    ) -> Optional[RawMediaCandidate]:
        """Search and return the best matching item for an on-demand / voice request."""

    @abstractmethod
    def available_filters(self, lang: str = "fr") -> Dict[str, List[Dict[str, str]]]:
        """Return the available filter categories for UI auto-discovery."""
