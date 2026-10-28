"""
Zero-Key Free Synopsis Translation & Card Metadata Localization Service
(`app/infrastructure/scrapers/translation_service.py`).

Guarantees localized DMD Info Cards across all 4 domains (`games`, `movies`, `series`, `anime`):
1. Preserves native human-written localized synopses (`synopsis_localized = "1"`) from
   `ScreenScraper`, `TMDB`, `TheTVDB`, or localized `Wikipedia`.
2. Detects untranslated English synopses (including silent upstream English fallbacks) and
   translates them into `target_lang` via a bounded zero-API-key cascade
   (`Google GTX -> MyMemory -> Lingva`, cached in a 512-entry LRU) tagged as `"auto"`, so
   `CandidateAggregator` still upgrades `"auto"` -> `"1"` whenever a secondary source holds
   an official human translation.
3. Localizes genre badges & subtitles across `movies`, `series`, `anime`, and `games`.
"""

from __future__ import annotations

from collections import OrderedDict
import logging
import re
import threading
from typing import Any, Dict, Optional, Set, Tuple
import urllib.parse

from app.domain.entities import MediaCandidate
from app.domain.taxonomy import (
    ANIME_GENRE_CATALOG,
    GAME_GENRE_CATALOG,
    MOVIE_GENRE_CATALOG,
    SERIES_GENRE_CATALOG,
    localize_label,
    resolve_canonical_genre,
)
from app.infrastructure.scrapers.http_utils import clean_synopsis_text

logger = logging.getLogger("dmd_showcase.scrapers.translation")

_WORD_RE = re.compile(r"[a-zàâäáãåçéèêëíìîïñóòôöõúùûüýÿœæß]+", re.I)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")

_EN_STOPWORDS: Set[str] = {
    "the", "and", "with", "from", "into", "that", "this", "their", "his", "her",
    "they", "them", "when", "while", "after", "before", "against", "through",
    "between", "which", "where", "must", "world", "story", "follows", "young",
    "player", "players", "character", "characters", "game", "series", "movie",
    "film", "released", "developed", "published", "arcade", "fight", "battle",
    "discover", "discovers", "becomes", "about", "during", "only", "first",
}

_TARGET_STOPWORDS: Dict[str, Set[str]] = {
    "fr": {
        "dans", "avec", "pour", "une", "des", "les", "sur", "est", "sont", "mais",
        "cette", "leur", "aux", "qui", "plus", "entre", "son", "ses", "comme",
        "après", "contre", "monde", "histoire", "jeu", "vidéo", "série", "sorti",
        "développé", "édité", "joueur", "personnage", "alors", "tout", "être",
    },
    "es": {
        "con", "una", "los", "las", "del", "para", "por", "como", "pero", "sus",
        "entre", "sobre", "juego", "mundo", "historia", "serie", "película", "contra",
        "cuando", "donde", "esta", "este", "son", "fue", "jugador",
    },
    "de": {
        "und", "der", "die", "das", "mit", "ein", "eine", "auf", "für", "sich",
        "nicht", "auch", "dem", "den", "von", "ist", "sind", "spiel", "welt",
        "gegen", "wird", "nach", "über",
    },
    "it": {
        "con", "una", "degli", "delle", "nella", "sono", "suo", "loro", "dopo",
        "tra", "gioco", "storia", "mondo", "contro", "della", "dello", "questo",
    },
    "pt": {
        "com", "uma", "dos", "das", "para", "pelo", "pela", "seus", "entre",
        "jogo", "mundo", "história", "contra", "quando", "onde", "está",
    },
}

_CATALOG_BY_DOMAIN: Dict[str, Dict[str, Dict[str, Any]]] = {
    "games": GAME_GENRE_CATALOG,
    "movies": MOVIE_GENRE_CATALOG,
    "series": SERIES_GENRE_CATALOG,
    "anime": ANIME_GENRE_CATALOG,
}

_TRANSLATION_CACHE: "OrderedDict[Tuple[str, str], str]" = OrderedDict()
_CACHE_MAX_SIZE = 512
_CACHE_LOCK = threading.Lock()


def normalize_short_lang(lang: str) -> str:
    return (lang or "fr").split("-")[0].split("_")[0].strip().lower() or "fr"


def is_likely_english_synopsis(text: str, target_lang: str) -> bool:
    """Return True when `text` clearly reads as untranslated English instead of `target_lang`."""
    short_lang = normalize_short_lang(target_lang)
    if short_lang == "en" or not text or len(text.strip()) < 12:
        return False
    if short_lang == "ja":
        return len(_CJK_RE.findall(text)) < 2

    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < 4:
        return False

    en_hits = sum(1 for w in words if w in _EN_STOPWORDS)
    tgt_vocab = _TARGET_STOPWORDS.get(short_lang, set())
    tgt_hits = sum(1 for w in words if w in tgt_vocab)

    if en_hits >= 2 and tgt_hits == 0:
        return True
    return en_hits >= (tgt_hits * 2 + 3)


def localize_domain_genre_label(domain: str, raw_genre: str, lang: str) -> str:
    """Resolve localized genre label (`fr`, `en`, `es`, `de`, `it`) for any of the 4 domains."""
    short_lang = normalize_short_lang(lang)
    clean_raw = (raw_genre or "").strip()
    if not clean_raw:
        return ""
    catalog = _CATALOG_BY_DOMAIN.get(domain) or {}
    canon = resolve_canonical_genre(domain, clean_raw)
    if canon and canon in catalog:
        return localize_label(catalog[canon].get("labels") or {}, short_lang, clean_raw)
    low = clean_raw.lower()
    if low in catalog:
        return localize_label(catalog[low].get("labels") or {}, short_lang, clean_raw)
    return clean_raw


def _cache_get(key: Tuple[str, str]) -> Optional[str]:
    with _CACHE_LOCK:
        val = _TRANSLATION_CACHE.get(key)
        if val is not None:
            _TRANSLATION_CACHE.move_to_end(key)
        return val


def _cache_put(key: Tuple[str, str], value: str) -> None:
    with _CACHE_LOCK:
        _TRANSLATION_CACHE[key] = value
        _TRANSLATION_CACHE.move_to_end(key)
        while len(_TRANSLATION_CACHE) > _CACHE_MAX_SIZE:
            _TRANSLATION_CACHE.popitem(last=False)


async def translate_synopsis_free(
    client: Optional[Any],
    text: str,
    target_lang: str,
    source_lang: str = "auto",
) -> Optional[str]:
    """
    Translate `text` into `target_lang` using zero-API-key public endpoints
    (`Google GTX -> MyMemory -> Lingva`) with a bounded 512-entry in-memory LRU cache.
    """
    short_lang = normalize_short_lang(target_lang)
    clean_src = clean_synopsis_text(text, max_chars=320)
    if not clean_src or len(clean_src) < 10 or short_lang == "en":
        return None
    if source_lang == short_lang:
        return clean_src
    if client is None or not hasattr(client, "get"):
        return None

    cache_key = (short_lang, clean_src)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    sl_gtx = source_lang if source_lang and source_lang != "auto" else "auto"

    # Tier 1: Google Translate GTX JSON endpoint (fast, neural, zero API key)
    try:
        resp = await client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": sl_gtx, "tl": short_lang, "dt": "t", "q": clean_src},
            timeout=2.2,
        )
        if getattr(resp, "status_code", 0) == 200:
            payload = resp.json()
            if isinstance(payload, list) and payload and isinstance(payload[0], list):
                joined = "".join(
                    str(seg[0])
                    for seg in payload[0]
                    if isinstance(seg, list) and len(seg) >= 1 and seg[0]
                ).strip()
                cleaned = clean_synopsis_text(joined, max_chars=320)
                if cleaned and not is_likely_english_synopsis(cleaned, short_lang):
                    _cache_put(cache_key, cleaned)
                    return cleaned
    except Exception as exc:
        logger.debug("[TRANSLATE] GTX lookup skipped for '%s': %s", short_lang, exc)

    # Tier 2: MyMemory Translated.net REST JSON endpoint (zero API key)
    sl_mm = source_lang if source_lang and source_lang != "auto" else "en"
    try:
        resp2 = await client.get(
            "https://api.mymemory.translated.net/get",
            params={"q": clean_src[:480], "langpair": f"{sl_mm}|{short_lang}"},
            timeout=2.2,
        )
        if getattr(resp2, "status_code", 0) == 200:
            payload2 = resp2.json()
            if isinstance(payload2, dict) and int(payload2.get("responseStatus") or 0) == 200:
                rdata = payload2.get("responseData")
                raw_tr = str(rdata.get("translatedText") or "").strip() if isinstance(rdata, dict) else ""
                if raw_tr and "MYMEMORY WARNING" not in raw_tr.upper():
                    cleaned2 = clean_synopsis_text(raw_tr, max_chars=320)
                    if cleaned2 and not is_likely_english_synopsis(cleaned2, short_lang):
                        _cache_put(cache_key, cleaned2)
                        return cleaned2
    except Exception as exc:
        logger.debug("[TRANSLATE] MyMemory lookup skipped for '%s': %s", short_lang, exc)

    # Tier 3: Lingva public JSON proxy
    try:
        quoted = urllib.parse.quote(clean_src, safe="")
        resp3 = await client.get(
            f"https://lingva.ml/api/v1/{sl_gtx}/{short_lang}/{quoted}",
            timeout=2.2,
        )
        if getattr(resp3, "status_code", 0) == 200:
            payload3 = resp3.json()
            if isinstance(payload3, dict) and isinstance(payload3.get("translation"), str):
                cleaned3 = clean_synopsis_text(payload3["translation"], max_chars=320)
                if cleaned3 and not is_likely_english_synopsis(cleaned3, short_lang):
                    _cache_put(cache_key, cleaned3)
                    return cleaned3
    except Exception as exc:
        logger.debug("[TRANSLATE] Lingva lookup skipped for '%s': %s", short_lang, exc)

    return None


async def ensure_candidate_localized(
    cand: Optional[MediaCandidate],
    target_lang: str,
    client: Optional[Any] = None,
    raw_source_lang: str = "",
) -> Optional[MediaCandidate]:
    """
    Ensure `cand` has localized genre badge/subtitle and a localized `synopsis` in `target_lang`.
    Tags human-localized synopses as `"1"`, free-translated synopses as `"auto"`, and synthetic
    placeholders as `"0"` so `CandidateAggregator` enforces strict `"1" > "auto" > "0"` precedence.
    """
    if cand is None:
        return None

    short_lang = normalize_short_lang(target_lang)
    cand.lang = short_lang
    meta = cand.extra_meta

    # Localize subtitle & badge genre prefix on movies / series / anime
    if cand.domain in ("movies", "series", "anime") and cand.subtitle:
        parts = [p.strip() for p in cand.subtitle.split("•", 1)]
        if parts and parts[0]:
            loc_genre = localize_domain_genre_label(cand.domain, parts[0], short_lang)
            if loc_genre:
                cand.subtitle = (
                    f"{loc_genre} • {parts[1]}"[:28] if len(parts) == 2 else loc_genre[:28]
                )
                cand.badge = loc_genre[:8].upper()

    if short_lang == "en":
        if cand.synopsis:
            meta["synopsis_localized"] = "1"
        return cand

    loc_state = str(meta.get("synopsis_localized") or "")
    src_lang = (raw_source_lang or str(meta.get("synopsis_source_lang") or "")).strip().lower()
    has_foreign_text = (
        bool(src_lang and src_lang != short_lang)
        or is_likely_english_synopsis(cand.synopsis, short_lang)
    )

    if loc_state == "1" and not has_foreign_text:
        return cand
    if loc_state == "1" and has_foreign_text:
        loc_state = "0"
        meta["synopsis_localized"] = "0"

    if cand.synopsis and has_foreign_text:
        translated = await translate_synopsis_free(
            client,
            cand.synopsis,
            target_lang=short_lang,
            source_lang=src_lang or "en",
        )
        if translated:
            cand.synopsis = translated
            meta["synopsis_localized"] = "auto"
            meta.pop("synopsis_source_lang", None)
            return cand

    if cand.synopsis and not has_foreign_text and loc_state == "":
        meta["synopsis_localized"] = "1"
        return cand

    meta.setdefault("synopsis_localized", "0")
    return cand
