"""
Common Taxonomy Normalization & Canonical Decades (`app/domain/taxonomy/common.py`).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict


def normalize_category_slug(raw: str) -> str:
    """Normalize an arbitrary scraper category or system name into a stable lowercase slug."""
    if not raw:
        return ""
    ascii_str = (
        unicodedata.normalize("NFKD", str(raw))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_str).strip("_")
    return slug


def localize_label(labels: Dict[str, str], lang: str = "fr", fallback: str = "") -> str:
    """Return the localized label for `lang` (`fr`, `en`, `es`, `de`, `it`)."""
    short = (lang or "fr").split("-")[0].split("_")[0].lower()
    if short in labels and labels[short]:
        return labels[short]
    if "en" in labels and labels["en"]:
        return labels["en"]
    if "fr" in labels and labels["fr"]:
        return labels["fr"]
    return next(iter(labels.values()), fallback)


# =============================================================================
# 1. Canonical Decades (All Domains)
# =============================================================================

CANONICAL_DECADES: Dict[str, Dict[str, Any]] = {
    "1970s": {
        "bounds": (1970, 1979),
        "labels": {
            "fr": "Années 1970",
            "en": "1970s",
            "es": "Años 1970",
            "de": "1970er",
            "it": "Anni 1970",
        },
    },
    "1980s": {
        "bounds": (1980, 1989),
        "labels": {
            "fr": "Années 1980",
            "en": "1980s",
            "es": "Años 1980",
            "de": "1980er",
            "it": "Anni 1980",
        },
    },
    "1990s": {
        "bounds": (1990, 1999),
        "labels": {
            "fr": "Années 1990",
            "en": "1990s",
            "es": "Años 1990",
            "de": "1990er",
            "it": "Anni 1990",
        },
    },
    "2000s": {
        "bounds": (2000, 2009),
        "labels": {
            "fr": "Années 2000",
            "en": "2000s",
            "es": "Años 2000",
            "de": "2000er",
            "it": "Anni 2000",
        },
    },
    "2010s": {
        "bounds": (2010, 2019),
        "labels": {
            "fr": "Années 2010",
            "en": "2010s",
            "es": "Años 2010",
            "de": "2010er",
            "it": "Anni 2010",
        },
    },
    "2020s": {
        "bounds": (2020, 2029),
        "labels": {
            "fr": "Années 2020",
            "en": "2020s",
            "es": "Años 2020",
            "de": "2020er",
            "it": "Anni 2020",
        },
    },
}
