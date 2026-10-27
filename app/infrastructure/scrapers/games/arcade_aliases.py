"""
Canonical Arcade & Retro Game Shorthand / Acronym Alias Map
(`app/infrastructure/scrapers/games/arcade_aliases.py`).

Resolves player shorthand queries (`sf2x`, `3s`, `kof98`, `windjammers`, `avsp`, `sotn`, etc.)
and canonical game titles directly to MAME ROM set names and full search titles.
"""

from __future__ import annotations

import re
from typing import Dict, List

_NORM_WS_RE = re.compile(r"[^a-z0-9]+")

# Player shorthand / acronym / title slug -> ordered MAME ROM set candidates (parent/iconic sets)
ARCADE_QUERY_ALIASES: Dict[str, List[str]] = {
    "sf2x": ["ssf2xj", "ssf2t"],
    "ssf2x": ["ssf2xj", "ssf2t"],
    "ssf2xj": ["ssf2xj", "ssf2t"],
    "ssf2t": ["ssf2t", "ssf2xj"],
    "st": ["ssf2t"],
    "super street fighter 2 turbo": ["ssf2t", "ssf2xj"],
    "super street fighter ii turbo": ["ssf2t", "ssf2xj"],
    "super street fighter 2 x": ["ssf2xj", "ssf2t"],
    "super street fighter ii x": ["ssf2xj", "ssf2t"],
    "ssf2": ["ssf2", "ssf2t"],
    "super street fighter 2": ["ssf2", "ssf2t"],
    "super street fighter ii": ["ssf2", "ssf2t"],
    "hyper street fighter 2": ["hsf2"],
    "hyper street fighter ii": ["hsf2"],
    "hsf2": ["hsf2"],
    "sf2": ["sf2", "sf2ce", "sf2hf"],
    "sf2ce": ["sf2ce", "sf2"],
    "sf2hf": ["sf2hf", "sf2"],
    "street fighter 2": ["sf2", "sf2ce", "sf2hf"],
    "street fighter ii": ["sf2", "sf2ce", "sf2hf"],
    "sfa": ["sfa"],
    "sfa2": ["sfa2", "sfz2al"],
    "sfz2": ["sfa2", "sfz2al"],
    "sfa3": ["sfa3"],
    "sfz3": ["sfa3"],
    "street fighter alpha 2": ["sfa2"],
    "street fighter alpha 3": ["sfa3"],
    "3s": ["sfiii3"],
    "sf3": ["sfiii3", "sfiii2", "sfiii"],
    "sfiii3": ["sfiii3"],
    "third strike": ["sfiii3"],
    "3rd strike": ["sfiii3"],
    "street fighter iii 3rd strike": ["sfiii3"],
    "street fighter 3 3rd strike": ["sfiii3"],
    "mvsc": ["mvsc"],
    "mvc": ["mvsc"],
    "marvel vs capcom": ["mvsc"],
    "mvsc2": ["mvsc2"],
    "mvc2": ["mvsc2"],
    "xmvsf": ["xmvsf"],
    "x men vs street fighter": ["xmvsf"],
    "mshvsf": ["mshvsf"],
    "marvel super heroes vs street fighter": ["mshvsf"],
    "msh": ["msh"],
    "marvel super heroes": ["msh"],
    "xmcota": ["xmcota"],
    "vsav": ["vsav"],
    "vampire savior": ["vsav"],
    "darkstalkers": ["dstlk", "nwarr", "vsav"],
    "avsp": ["avsp"],
    "alien vs predator": ["avsp"],
    "ddsom": ["ddsom"],
    "shadow over mystara": ["ddsom"],
    "ddtod": ["ddtod"],
    "tower of doom": ["ddtod"],
    "kof94": ["kof94"],
    "kof95": ["kof95"],
    "kof96": ["kof96"],
    "kof97": ["kof97"],
    "kof98": ["kof98"],
    "the king of fighters 98": ["kof98"],
    "king of fighters 98": ["kof98"],
    "kof99": ["kof99"],
    "kof2000": ["kof2000"],
    "kof2002": ["kof2002"],
    "the king of fighters 2002": ["kof2002"],
    "kof2003": ["kof2003"],
    "garou": ["garou"],
    "motw": ["garou"],
    "mark of the wolves": ["garou"],
    "garou mark of the wolves": ["garou"],
    "mslug": ["mslug", "mslugx", "mslug3"],
    "metal slug": ["mslug", "mslugx", "mslug3", "mslug2"],
    "metal slug x": ["mslugx"],
    "metal slug 2": ["mslug2"],
    "metal slug 3": ["mslug3"],
    "samsho": ["samsho2", "samsho", "samsho4", "samsho5sp"],
    "samurai shodown": ["samsho", "samsho2", "samsho4"],
    "samurai shodown ii": ["samsho2"],
    "samurai shodown 2": ["samsho2"],
    "last blade": ["lastblad", "lastbld2"],
    "last blade 2": ["lastbld2"],
    "windjammers": ["wjammers", "windjamm"],
    "wjammers": ["wjammers"],
    "flying power disc": ["wjammers"],
    "final fight": ["ffight"],
    "ffight": ["ffight"],
    "cadillacs": ["dino"],
    "cadillacs and dinosaurs": ["dino"],
    "punisher": ["punisher"],
    "the punisher": ["punisher"],
    "captain commando": ["captcomm"],
    "strider": ["strider"],
    "ghouls": ["ghouls"],
    "ghouls n ghosts": ["ghouls"],
    "1944": ["1944"],
    "19xx": ["19xx"],
    "gigawing": ["gigawing"],
    "giga wing": ["gigawing"],
    "mars matrix": ["marsmatr"],
    "progear": ["progear"],
    "dimahoo": ["dimahoo"],
    "spf2t": ["spf2t"],
    "puzzle fighter": ["spf2t"],
    "super puzzle fighter ii turbo": ["spf2t"],
    "pocket fighter": ["sgemf"],
    "super gem fighter": ["sgemf"],
    "ibara": ["ibara"],
    "dodonpachi": ["ddonpach", "ddp3"],
    "donpachi": ["donpachi"],
    "espgaluda": ["espgal"],
    "esprade": ["esprade"],
    "ikaruga": ["ikaruga"],
    "radiant silvergun": ["rsgun"],
    "blazing star": ["blazstar"],
    "pulstar": ["pulstar"],
    "shock troopers": ["shocktro", "shocktr2"],
    "neo turf masters": ["turfmast"],
    "puzzle bobble": ["pbobble", "pbobble2"],
    "bust a move": ["pbobble"],
    "outrun": ["outrun"],
    "daytona": ["daytona"],
    "daytona usa": ["daytona"],
    "sega rally": ["srally"],
    "virtua fighter 2": ["vf2"],
    "virtua cop 2": ["vcop2"],
    "house of the dead": ["hotd"],
    "jojo": ["jojoba", "jojo"],
    "red earth": ["redearth"],
    "knights of valour": ["kov"],
    "demon front": ["dmnfrnt"],
}

# Shorthand -> Canonical full titles for IGDB / ScreenScraper / Libretro text search expansion
RETRO_QUERY_TITLE_ALIASES: Dict[str, List[str]] = {
    "sf2x": ["Super Street Fighter II Turbo", "Super Street Fighter II"],
    "ssf2x": ["Super Street Fighter II Turbo", "Super Street Fighter II"],
    "ssf2xj": ["Super Street Fighter II Turbo", "Super Street Fighter II"],
    "ssf2t": ["Super Street Fighter II Turbo", "Super Street Fighter II"],
    "st": ["Super Street Fighter II Turbo"],
    "hsf2": ["Hyper Street Fighter II"],
    "sf2": ["Street Fighter II"],
    "sf2ce": ["Street Fighter II': Champion Edition", "Street Fighter II"],
    "sf2hf": ["Street Fighter II': Hyper Fighting", "Street Fighter II"],
    "sfa2": ["Street Fighter Alpha 2"],
    "sfa3": ["Street Fighter Alpha 3"],
    "sfz3": ["Street Fighter Alpha 3"],
    "3s": ["Street Fighter III: 3rd Strike", "Street Fighter III"],
    "sf3": ["Street Fighter III: 3rd Strike", "Street Fighter III"],
    "sfiii3": ["Street Fighter III: 3rd Strike"],
    "third strike": ["Street Fighter III: 3rd Strike"],
    "mvsc": ["Marvel vs. Capcom: Clash of Super Heroes", "Marvel vs. Capcom"],
    "mvc": ["Marvel vs. Capcom: Clash of Super Heroes", "Marvel vs. Capcom"],
    "mvsc2": ["Marvel vs. Capcom 2: New Age of Heroes"],
    "mvc2": ["Marvel vs. Capcom 2: New Age of Heroes"],
    "xmvsf": ["X-Men vs. Street Fighter"],
    "mshvsf": ["Marvel Super Heroes vs. Street Fighter"],
    "vsav": ["Vampire Savior", "Darkstalkers 3"],
    "avsp": ["Alien vs. Predator"],
    "ddsom": ["Dungeons & Dragons: Shadow over Mystara"],
    "ddtod": ["Dungeons & Dragons: Tower of Doom"],
    "kof98": ["The King of Fighters '98"],
    "kof2002": ["The King of Fighters 2002"],
    "motw": ["Garou: Mark of the Wolves"],
    "garou": ["Garou: Mark of the Wolves"],
    "mslug": ["Metal Slug"],
    "mslugx": ["Metal Slug X"],
    "mslug3": ["Metal Slug 3"],
    "samsho": ["Samurai Shodown II", "Samurai Shodown"],
    "wjammers": ["Windjammers"],
    "spf2t": ["Super Puzzle Fighter II Turbo"],
    "sotn": ["Castlevania: Symphony of the Night"],
    "alttp": ["The Legend of Zelda: A Link to the Past"],
    "smw": ["Super Mario World"],
    "smb3": ["Super Mario Bros. 3"],
    "sor2": ["Streets of Rage 2"],
    "ff6": ["Final Fantasy VI", "Final Fantasy III"],
    "ff7": ["Final Fantasy VII"],
    "ct": ["Chrono Trigger"],
}


def _normalize_lookup_key(raw: str) -> str:
    return _NORM_WS_RE.sub(" ", (raw or "").strip().lower()).strip()


def resolve_arcade_query_roms(query: str) -> List[str]:
    """Return ordered MAME ROM candidates matching a shorthand, ROM name, or canonical title."""
    norm = _normalize_lookup_key(query)
    if not norm:
        return []
    compact = norm.replace(" ", "")
    for candidate_key in (norm, compact):
        if candidate_key in ARCADE_QUERY_ALIASES:
            return list(ARCADE_QUERY_ALIASES[candidate_key])
    # Prefix / subtitle stripping (e.g. "Super Street Fighter II Turbo: Grand Master Challenge")
    head = norm.split(":")[0].strip()
    if head and head in ARCADE_QUERY_ALIASES:
        return list(ARCADE_QUERY_ALIASES[head])
    for alias_key, roms in ARCADE_QUERY_ALIASES.items():
        if len(alias_key) >= 6 and (norm.startswith(alias_key) or alias_key in norm):
            return list(roms)
    return []


def expand_retro_query_titles(query: str) -> List[str]:
    """Expand player shorthand (`sf2x`, `3s`, `sotn`, `kof98`) into full canonical search titles."""
    norm = _normalize_lookup_key(query)
    if not norm:
        return []
    compact = norm.replace(" ", "")
    for candidate_key in (norm, compact):
        if candidate_key in RETRO_QUERY_TITLE_ALIASES:
            return list(RETRO_QUERY_TITLE_ALIASES[candidate_key])
    return []
