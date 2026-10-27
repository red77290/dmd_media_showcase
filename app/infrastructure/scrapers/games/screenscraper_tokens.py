"""
ScreenScraper Discovery Tokens, Hardware Stems & High-Entropy Sampler
(`app/infrastructure/scrapers/games/screenscraper_tokens.py`).
"""

from __future__ import annotations

from collections import deque
import random
from typing import Deque, Dict, List, Set, Tuple

from app.infrastructure.scrapers.catalog_metadata import SYSTEM_METADATA

_SYS_RNG = random.SystemRandom()

SEARCH_SYLLABLES: List[str] = [
    "al", "am", "an", "ar", "as", "at", "au", "ax", "az",
    "ba", "be", "bi", "bl", "bo", "br", "bu",
    "ca", "ce", "ch", "cl", "co", "cr", "cy",
    "da", "de", "di", "do", "dr", "du", "dy",
    "ea", "ec", "el", "em", "en", "er", "es", "et", "ex",
    "fa", "fe", "fi", "fl", "fo", "fr", "fu",
    "ga", "ge", "gh", "gi", "gl", "go", "gr", "gu",
    "ha", "he", "hi", "ho", "hu", "hy",
    "ic", "ik", "il", "in", "ir", "is",
    "ja", "je", "jo", "ju",
    "ka", "ke", "ki", "kl", "kn", "ko", "kr", "ku",
    "la", "le", "li", "lo", "lu",
    "ma", "me", "mi", "mo", "mu", "my",
    "na", "ne", "ni", "no", "nu",
    "ob", "od", "om", "on", "op", "or", "ou", "ov",
    "pa", "pe", "ph", "pi", "pl", "po", "pr", "ps", "pu",
    "qu", "ra", "re", "ri", "ro", "ru",
    "sa", "sc", "se", "sf", "sh", "si", "sk", "sl", "sm", "sn", "so", "sp", "sq", "st", "su", "sw", "sy",
    "ta", "te", "th", "ti", "to", "tr", "tu", "tw",
    "ul", "un", "ur", "va", "ve", "vi", "vo", "vr",
    "wa", "we", "wh", "wi", "wo", "wr",
    "xe", "ya", "yo", "yu", "za", "ze", "zo",
]

GENRE_DISCOVERY_TOKENS: Dict[str, List[str]] = {
    "fighting": [
        "street fighter", "super street", "mortal kombat", "ultimate mortal", "eternal",
        "fatal fury", "samurai", "art of fighting", "world heroes", "dragon ball",
        "appel du destin", "buyuu", "hakusho", "yu yu", "primal rage", "clayfighter",
        "weaponlord", "virtua fighter", "tournament", "brutal", "justice league",
        "power instinct", "fighting masters", "deadly moves", "shadow falls", "ballz",
        "shaq", "king of the monsters", "king of fighters", "garou", "last blade",
        "darkstalkers", "vampire", "tekken", "soul", "dead or alive", "killer instinct",
        "guilty gear", "rival schools", "pocket fighter", "cyberbots", "jojo",
        "red earth", "toshinden", "bloody roar", "bushido blade", "tobal", "breakers",
        "kizuna", "waku waku", "kaiser", "ranma", "gundam",
    ],
    "beatemup": [
        "streets of rage", "bare knuckle", "golden axe", "final fight", "double dragon",
        "comix zone", "maximum carnage", "separation anxiety", "captain america",
        "punisher", "turtles", "hyperstone", "battletoads", "alien storm", "altered beast",
        "cadillacs", "knights of the round", "warriors of fate", "king of dragons",
        "sengoku", "mutation nation", "burning fight", "alien vs", "dungeons",
        "river city", "splatterhouse", "vendetta", "crime fighters",
    ],
    "platform": [
        "sonic", "mario", "castlevania", "mega man", "donkey kong", "kirby", "aladdin",
        "lion king", "castle of illusion", "world of illusion", "quackshot", "ristar",
        "dynamite headdy", "rocket knight", "sparkster", "shinobi", "kid chameleon",
        "earthworm jim", "rayman", "crash", "spyro", "alex kidd", "wonder boy",
        "ghouls", "strider", "bonk", "klonoa", "gex", "bubsy", "cool spot", "vectorman",
    ],
    "shootemup": [
        "thunder force", "gradius", "r-type", "darius", "parodius", "twinbee", "musha",
        "aleste", "raiden", "1942", "1943", "19xx", "giga wing", "mars matrix", "pulstar",
        "blazing star", "gleylancer", "gaiares", "bio-hazard", "truxton", "fire shark",
        "aero fighters", "strikers", "ikaruga", "radiant", "einhander", "axelay", "soldier",
    ],
    "runandgun": [
        "contra", "probotector", "gunstar", "metal slug", "sunset riders", "turrican",
        "mercs", "shock troopers", "midnight resistance", "rolling thunder", "eswat",
        "vectorman", "doom troopers", "skeleton krew", "zombies", "smash tv", "ikari",
    ],
    "rpg": [
        "phantasy star", "shining force", "chrono", "final fantasy", "dragon quest",
        "dragon warrior", "mana", "breath of fire", "suikoden", "wild arms", "xenogears",
        "landstalker", "beyond oasis", "story of thor", "soleil", "crusader of centy",
        "lunar", "grandia", "star ocean", "valkyrie", "earthbound", "terranigma", "ys",
    ],
    "racing": [
        "outrun", "road rash", "mario kart", "f-zero", "top gear", "virtua racing",
        "ridge racer", "gran turismo", "wipeout", "micro machines", "rock n roll racing",
        "super hang-on", "super monaco", "daytona", "sega rally", "lotus", "cruis",
    ],
    "puzzle": [
        "columns", "puyo", "tetris", "dr. mario", "mean bean", "puzzle bobble",
        "bust-a-move", "klax", "lemmings", "magical drop", "panel de pon", "lumines",
        "boulder dash", "sokoban", "picross", "kula world", "intelligent qube",
    ],
    "sports": [
        "nba jam", "fifa", "sensible soccer", "iss", "nhl", "madden", "windjammers",
        "speedball", "mutant league", "virtua tennis", "tony hawk", "punch-out",
        "super sidekicks", "turf masters", "track & field", "olympic", "tecmo super",
    ],
    "strategy": [
        "dune", "herzog", "shining force", "warsong", "langrisser", "ogre battle",
        "tactics", "advance wars", "populous", "syndicate", "theme park", "civilization",
        "warcraft", "command & conquer", "romance of the three", "nobunaga", "worms",
    ],
    "action": [
        "strider", "shinobi", "zelda", "metroid", "castlevania", "resident evil",
        "metal gear", "tomb raider", "legacy of kain", "bomberman", "desert strike",
        "jungle strike", "urban strike", "flashback", "another world", "ecc", "chaos engine",
    ],
}

SYSTEM_SPECIFIC_STEMS: Dict[str, List[str]] = {
    "cps1": [
        "street fighter", "final fight", "cadillacs", "punisher", "captain commando",
        "knights of the round", "warriors of fate", "king of dragons", "strider",
        "ghouls", "1941", "varth", "carrier air wing", "mercs", "forgotten worlds",
        "un squadron", "willow", "mega twins", "nemo", "mega man", "slam masters",
        "muscle bomber", "puzz loop", "pang", "three wonders",
    ],
    "cps2": [
        "super street fighter", "street fighter alpha", "street fighter zero",
        "marvel super heroes", "vampire savior", "vampire hunter", "night warriors",
        "darkstalkers", "x-men", "marvel vs. capcom", "cyberbots", "ring of destruction",
        "super gem fighter", "pocket fighter", "super puzzle fighter", "puzz loop 2",
        "mighty pang", "1944", "19xx", "giga wing", "progear", "mars matrix",
        "dimahoo", "eco fighters", "alien vs. predator", "dungeons & dragons",
        "battle circuit", "armored warriors", "capcom sports club", "mega man 2",
    ],
    "cps3": [
        "street fighter iii", "jojo", "red earth", "war-zard",
    ],
    "neogeo": [
        "king of fighters", "samurai shodown", "metal slug", "garou", "fatal fury",
        "art of fighting", "last blade", "world heroes", "snk vs. capcom", "shock troopers",
        "sengoku", "pulstar", "blazing star", "viewpoint", "last resort", "twinkle star",
        "aero fighters", "strikers 1945", "prehistoric isle", "windjammers", "super sidekicks",
        "neo turf masters", "baseball stars", "street hoop", "top hunter", "spinmaster",
        "magician lord", "blue's journey", "cyber-lip", "nam-1975", "mutation nation",
        "robo army", "burning fight", "eight man", "puzzle bobble", "magical drop",
    ],
}

STRICT_HARDWARE_SS_IDS: Dict[str, Tuple[str, ...]] = {
    "cps1": ("105", "cps1", "cps-1", "capcom play system", "cp system"),
    "cps2": ("106", "cps2", "cps-2", "capcom play system 2", "cp system ii"),
    "cps3": ("107", "cps3", "cps-3", "capcom play system 3", "cp system iii"),
    "neogeo": ("142", "neogeo", "neo-geo", "neo geo", "snk neo-geo"),
}

NON_CPS_TITLE_TOKENS: Tuple[str, ...] = (
    "marvel vs. capcom 2",
    "marvel vs capcom 2",
    "mvsc2",
    "capcom vs. snk",
    "capcom vs snk",
    "cvs2",
    "guilty gear",
    "ikaruga",
    "virtua fighter",
    "dead or alive",
    "soulcalibur",
    "tekken",
    "mortal kombat",
)

SS_ID_TO_SYS_KEY: Dict[str, str] = {}
for _k, _meta in SYSTEM_METADATA.items():
    if _meta.get("ss_id") != 75:
        SS_ID_TO_SYS_KEY.setdefault(str(_meta["ss_id"]), _k)


def pick_discovery_token(
    sys_key: str,
    req_genres: List[str],
    used_tokens: Set[Tuple[str, str]],
    recent_tokens: Dict[Tuple[str, str], Deque[str]],
) -> str:
    """Select a high-entropy search token avoiding recently used tokens across calls."""
    genre_bucket = ",".join(sorted(req_genres)) if req_genres else "all"
    recent = recent_tokens.get((sys_key, genre_bucket))
    if recent is None:
        recent = deque(maxlen=28)
        recent_tokens[(sys_key, genre_bucket)] = recent

    genre_pool: List[str] = []
    for g in req_genres:
        for tok in GENRE_DISCOVERY_TOKENS.get(g, []):
            if tok not in genre_pool:
                genre_pool.append(tok)
    sys_stems = SYSTEM_SPECIFIC_STEMS.get(sys_key) or []

    roll = _SYS_RNG.random()
    if genre_pool and sys_stems:
        if roll < 0.45:
            primary_pool = genre_pool
        elif roll < 0.75:
            primary_pool = sys_stems
        else:
            primary_pool = SEARCH_SYLLABLES
    elif genre_pool:
        primary_pool = genre_pool if roll < 0.58 else SEARCH_SYLLABLES
    elif sys_stems:
        primary_pool = sys_stems if roll < 0.65 else SEARCH_SYLLABLES
    else:
        primary_pool = SEARCH_SYLLABLES

    recent_set = set(recent)
    fresh = [
        t
        for t in primary_pool
        if (sys_key, t) not in used_tokens and t not in recent_set
    ]
    if not fresh:
        combined = list(dict.fromkeys(genre_pool + sys_stems + SEARCH_SYLLABLES))
        fresh = [
            t
            for t in combined
            if (sys_key, t) not in used_tokens and t not in recent_set
        ]
    if not fresh:
        fresh = [t for t in primary_pool if (sys_key, t) not in used_tokens] or list(
            primary_pool
        )

    token = _SYS_RNG.choice(fresh)
    used_tokens.add((sys_key, token))
    recent.append(token)
    return token
