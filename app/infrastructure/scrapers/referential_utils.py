"""
Shared Referential Normalization & Regional Filename Scoring (`app/infrastructure/scrapers/referential_utils.py`).
"""

from __future__ import annotations

import random
import re
from typing import Set

_SYS_RNG = random.SystemRandom()

_VOWELS = "aeiou"
_CONSONANTS = "bcdfghjklmnpqrstvwxz"
_ADB_ROM_LINK_RE = re.compile(
    r"dettaglio_mame\.php\?[^>]*?\bgame_name=([a-z0-9_]+)", re.IGNORECASE
)
_IGNORED_MAME_PREFIXES = (
    "bios",
    "neogeo",
    "cvs",
    "playch10",
    "nss",
    "megatech",
    "stvbios",
    "cpzn",
)
_REGION_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SUBTITLE_SPLIT_RE = re.compile(r"\s*(?::|\s[-–~]\s)\s*")
_EDITION_SUBTITLE_SLUGS: Set[str] = {
    "the_new_challengers",
    "special_champion_edition",
    "champion_edition",
    "hyper_fighting",
    "the_world_warrior",
    "turbo",
    "turbo_hyper_fighting",
    "special",
    "dash",
    "plus",
    "plus_champion_edition",
}


def random_search_syllable() -> str:
    """Generate a natural 2-letter syllable (`ca`, `ma`, `st`, `ra`, `so`, ...) for live index harvesting."""
    if _SYS_RNG.random() < 0.75:
        return _SYS_RNG.choice(_CONSONANTS) + _SYS_RNG.choice(_VOWELS)
    return _SYS_RNG.choice(_VOWELS) + _SYS_RNG.choice(_CONSONANTS)


def is_excluded_nointro_filename(filename: str) -> bool:
    """Return True if a Libretro / Archive.org filename is a hack, bootleg, unlicensed, demo, beta, or VC dump."""
    low = (filename or "").lower()
    return any(
        bad in low
        for bad in (
            "virtual console",
            "[hack",
            "(hack",
            "arcade hack",
            "(unl)",
            "(pirate",
            "(bootleg",
            "[b]",
            "[t+",
            "(proto",
            "(beta",
            "(sample",
            "(demo",
            "hyper champion edition",
            "remastered edition",
        )
    )


def normalize_game_lookup_key(title: str) -> str:
    """
    Normalize a game title or No-Intro filename into a canonical lookup key:
    - Strip `.png`, `.ia.mp4`, `.mp4` and all parenthetical tags `(Europe)`, `(France)`, `(USA)`, `(Rev A)`
    - Handle trailing `, The` or leading `The `
    """
    s = (title or "").strip()
    low_ext = s.lower()
    if low_ext.endswith(".ia.mp4"):
        s = s[:-7]
    elif low_ext.endswith((".png", ".mp4")):
        s = s[:-4]
    s = _REGION_TAG_RE.sub("", s).strip()
    lower = s.lower()
    if lower.endswith(", the"):
        s = s[:-5].strip()
    elif lower.startswith("the "):
        s = s[4:].strip()
    return _NON_ALNUM_RE.sub("", s.lower())


def score_libretro_filename_for_lang(filename: str, lang: str = "fr") -> int:
    """
    Score a No-Intro Libretro filename by regional preference so French DMDs receive
    `(France)` or `(Europe)` boxarts/titles before `(World)` or `(USA)`.
    """
    low = filename.lower()
    short_lang = (lang or "fr").split("-")[0].split("_")[0].lower()
    score = 10
    if any(tok in low for tok in ("proto", "beta", "sample", "demo", "pirate", "unl")):
        score -= 50

    if short_lang == "fr":
        if "(france" in low or ", fr" in low or "(fr)" in low:
            score += 100
        elif "(europe" in low:
            score += 85
        elif "(world" in low:
            score += 70
        elif "(usa" in low:
            score += 50
        elif "(japan" in low:
            score += 30
    elif short_lang in ("es", "de", "it"):
        country_map = {"es": "(spain", "de": "(germany", "it": "(italy"}
        if country_map[short_lang] in low:
            score += 100
        elif "(europe" in low:
            score += 85
        elif "(world" in low:
            score += 70
        elif "(usa" in low:
            score += 55
    else:
        if "(world" in low or "(usa" in low:
            score += 90
        elif "(europe" in low:
            score += 80
        elif "(japan" in low:
            score += 40
    return score
