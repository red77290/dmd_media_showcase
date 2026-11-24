"""
Pure Domain Recalbox / Batocera `gamelist.xml` Parser (`app/domain/gamelist_catalog.py`).

Extracts ROM stems, localized descriptions, release years, ratings, and system identifiers
from single or multi-system `gamelist.xml` files with zero external I/O or third-party XML deps.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Set, Tuple
import xml.etree.ElementTree as ET

from app.domain.batch_scraper_specs import FolderNamingPolicy
from app.domain.taxonomy.resolver import resolve_canonical_system

_ROM_KNOWN_EXTENSIONS: Set[str] = {
    ".zip",
    ".7z",
    ".rar",
    ".chd",
    ".cue",
    ".bin",
    ".iso",
    ".cso",
    ".pbp",
    ".sfc",
    ".smc",
    ".nes",
    ".fds",
    ".unf",
    ".md",
    ".gen",
    ".smd",
    ".sms",
    ".gg",
    ".32x",
    ".pce",
    ".sgx",
    ".gb",
    ".gbc",
    ".gba",
    ".nds",
    ".n64",
    ".z64",
    ".v64",
    ".ndd",
    ".cdi",
    ".gdi",
    ".m3u",
    ".dsk",
    ".adf",
    ".atr",
    ".a26",
    ".a52",
    ".a78",
    ".lnx",
    ".j64",
    ".ngp",
    ".ngc",
    ".ws",
    ".wsc",
    ".vec",
    ".col",
    ".int",
    ".rom",
    ".prg",
    ".tap",
    ".tzx",
}

_KNOWN_BIOS_STEMS: Set[str] = {
    "neogeo",
    "pgm",
    "skns",
    "naomi",
    "naomi2",
    "awbios",
    "hod2bios",
    "f355bios",
    "airlbios",
    "crysbios",
    "cpzn1",
    "cpzn2",
    "qsound",
    "qsound_hle",
    "ym2608",
    "cvs",
    "decocass",
    "playch10",
    "nss",
    "megatech",
    "megaplay",
    "stvbios",
}

_DOCTYPE_ENTITY_GUARD_RE = re.compile(r"<!ENTITY\b", re.IGNORECASE)


@dataclass(frozen=True)
class GamelistGameEntry:
    """Normalized game entry parsed from a Recalbox / Batocera / EmulationStation `gamelist.xml`."""

    system: str
    rom_path: str
    rom_stem: str
    title: str
    synopsis: str = ""
    year: str = ""
    rating: str = ""
    genre: str = ""
    players: str = ""
    developer: str = ""
    publisher: str = ""
    hidden: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "system": self.system,
            "rom_path": self.rom_path,
            "rom_stem": self.rom_stem,
            "title": self.title,
            "synopsis": self.synopsis,
            "year": self.year,
            "rating": self.rating,
            "genre": self.genre,
            "players": self.players,
            "developer": self.developer,
            "publisher": self.publisher,
            "hidden": self.hidden,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "GamelistGameEntry":
        return cls(
            system=str(data.get("system") or ""),
            rom_path=str(data.get("rom_path") or ""),
            rom_stem=str(data.get("rom_stem") or ""),
            title=str(data.get("title") or ""),
            synopsis=str(data.get("synopsis") or ""),
            year=str(data.get("year") or ""),
            rating=str(data.get("rating") or ""),
            genre=str(data.get("genre") or ""),
            players=str(data.get("players") or ""),
            developer=str(data.get("developer") or ""),
            publisher=str(data.get("publisher") or ""),
            hidden=bool(data.get("hidden", False)),
        )


def extract_rom_stem(raw_path: str) -> str:
    """
    Extract the exact ROM filename stem from `<path>./Super Mario World (USA).zip</path>`
    or `<path>./cps2/ssf2t.zip</path>`, matching `ArcadeMatrix` `FrontendSyncEngine` resolution.
    """
    norm = (raw_path or "").replace("\\", "/").strip().rstrip("/")
    if not norm:
        return ""
    leaf = norm.split("/")[-1].strip()
    if not leaf or leaf in (".", ".."):
        return ""
    dot_idx = leaf.rfind(".")
    if dot_idx > 0:
        ext = leaf[dot_idx:].lower()
        if ext in _ROM_KNOWN_EXTENSIONS or (2 <= len(ext) <= 5 and ext[1:].isalnum()):
            leaf = leaf[:dot_idx]
    return FolderNamingPolicy.sanitize_filename_stem(leaf, max_len=92)


def infer_system_from_gamelist_context(
    system_hint: str = "",
    source_relpath: str = "",
    provider_system_text: str = "",
    first_rom_path: str = "",
) -> str:
    """Resolve canonical system slug from explicit hint, file path (`roms/snes/gamelist.xml`), or XML header."""
    for raw_cand in (system_hint, provider_system_text):
        cleaned = (raw_cand or "").strip()
        if cleaned:
            resolved = resolve_canonical_system(cleaned)
            if resolved:
                return resolved

    for path_like in (source_relpath, first_rom_path):
        norm = (path_like or "").replace("\\", "/").strip("/")
        if not norm:
            continue
        parts = [p for p in norm.split("/") if p and p.lower() not in ("roms", "share", "gamelist.xml", ".")]
        for seg in reversed(parts):
            if seg.lower().endswith(".xml"):
                stem_guess = seg[:-4].replace("gamelist_", "").replace("gamelist-", "").strip()
                if stem_guess and stem_guess.lower() != "gamelist":
                    res = resolve_canonical_system(stem_guess)
                    if res:
                        return res
                continue
            res = resolve_canonical_system(seg)
            if res:
                return res

    fallback = (system_hint or provider_system_text or "mame").strip().lower()
    return resolve_canonical_system(fallback) or fallback or "mame"


def _parse_es_release_year(raw_date: str) -> str:
    txt = (raw_date or "").strip()
    if len(txt) >= 4 and txt[:4].isdigit():
        yr = int(txt[:4])
        if 1970 <= yr <= 2035:
            return str(yr)
    m = re.search(r"\b(19[7-9]\d|20[0-3]\d)\b", txt)
    return m.group(1) if m else ""


def _parse_es_rating(raw_rating: str) -> str:
    txt = (raw_rating or "").strip()
    if not txt:
        return ""
    try:
        val = float(txt)
    except ValueError:
        return ""
    if val <= 0.0:
        return ""
    # EmulationStation / Recalbox stores ratings on `[0.0 .. 1.0]`; convert to `[0.0 .. 10.0]`
    scaled = val * 10.0 if val <= 1.0 else min(10.0, val)
    return f"{scaled:.1f}"


def _child_text(elem: ET.Element, tag: str) -> str:
    node = elem.find(tag)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def is_bios_entry(rom_stem: str, title: str) -> bool:
    stem_low = (rom_stem or "").strip().lower()
    title_low = (title or "").strip().lower()
    if stem_low in _KNOWN_BIOS_STEMS:
        return True
    if "[bios]" in title_low or "(bios)" in title_low or title_low.startswith("bios "):
        return True
    return False


def parse_recalbox_gamelist_xml(
    xml_content: str,
    system_hint: str = "",
    source_relpath: str = "",
    skip_hidden: bool = True,
    skip_bios: bool = True,
) -> List[GamelistGameEntry]:
    """
    Parse a Recalbox / Batocera `gamelist.xml` string into deduplicated `GamelistGameEntry` items.
    Rejects XML entity expansion (`<!ENTITY`) for security.
    """
    if not xml_content or not xml_content.strip():
        return []
    if _DOCTYPE_ENTITY_GUARD_RE.search(xml_content):
        raise ValueError("Disallowed XML entity declaration in gamelist.xml")

    cleaned_xml = xml_content.lstrip("\ufeff").strip()
    root = ET.fromstring(cleaned_xml)

    provider_sys = ""
    prov_node = root.find("provider")
    if prov_node is not None:
        provider_sys = _child_text(prov_node, "System") or _child_text(prov_node, "system")

    game_nodes = list(root.findall("game"))
    first_path = _child_text(game_nodes[0], "path") if game_nodes else ""
    resolved_sys = infer_system_from_gamelist_context(
        system_hint=system_hint,
        source_relpath=source_relpath,
        provider_system_text=provider_sys,
        first_rom_path=first_path,
    )

    entries: List[GamelistGameEntry] = []
    seen_keys: Set[Tuple[str, str]] = set()

    for gnode in game_nodes:
        raw_path = _child_text(gnode, "path")
        rom_stem = extract_rom_stem(raw_path)
        if not rom_stem:
            continue

        hidden_txt = _child_text(gnode, "hidden").lower()
        is_hidden = hidden_txt in ("true", "1", "yes")
        if skip_hidden and is_hidden:
            continue

        raw_title = _child_text(gnode, "name") or rom_stem
        if skip_bios and is_bios_entry(rom_stem, raw_title):
            continue

        # Support multi-system collections where `<path>` embeds `/roms/<sys>/<rom>.zip`
        item_sys = infer_system_from_gamelist_context(
            system_hint=system_hint or resolved_sys,
            source_relpath="",
            provider_system_text="",
            first_rom_path=raw_path,
        )
        if not system_hint and "/" in raw_path.replace("\\", "/").lstrip("./"):
            sub_sys = infer_system_from_gamelist_context(first_rom_path=raw_path)
            if sub_sys and sub_sys != "mame":
                item_sys = sub_sys

        dedup_key = (item_sys.lower(), rom_stem.lower())
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        entries.append(
            GamelistGameEntry(
                system=item_sys,
                rom_path=raw_path,
                rom_stem=rom_stem,
                title=raw_title,
                synopsis=_child_text(gnode, "desc"),
                year=_parse_es_release_year(_child_text(gnode, "releasedate")),
                rating=_parse_es_rating(_child_text(gnode, "rating")),
                genre=_child_text(gnode, "genre"),
                players=_child_text(gnode, "players"),
                developer=_child_text(gnode, "developer"),
                publisher=_child_text(gnode, "publisher"),
                hidden=is_hidden,
            )
        )

    return entries
