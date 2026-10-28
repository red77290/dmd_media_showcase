"""
Canonical Video Game Genre Taxonomy & Strict 1v1 Fighting Disambiguation (`app/domain/taxonomy/game_genres.py`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Set, Tuple

from app.domain.taxonomy.common import normalize_category_slug


GAME_GENRE_CATALOG: Dict[str, Dict[str, Any]] = {
    "platform": {
        "wikidata_qid": "Q828322",
        "wikidata_qids": ["Q828322"],
        "adb_keywords": [
            "platform / run jump",
            "platform / run jump scrolling",
            "plateforme / run jump",
            "plateforme / run jump scrolling",
            "platform",
        ],
        "screenscraper_keywords": ["plateforme", "platform", "plattform", "piattaforma", "plataforma"],
        "synonyms": [
            "platform",
            "platformer",
            "plateforme",
            "plattform",
            "piattaforma",
            "plataforma",
            "platform_game",
            "platform_run_jump",
            "platform_run_jump_scrolling",
            "plateforme_run_jump",
            "plateforme_run_jump_scrolling",
            "metroidvania",
        ],
        "labels": {
            "fr": "Plateforme",
            "en": "Platformer",
            "es": "Plataformas",
            "de": "Jump ’n’ Run",
            "it": "Platform",
        },
    },
    "action": {
        "wikidata_qid": "Q270948",
        "wikidata_qids": ["Q270948", "Q343568"],
        "adb_keywords": [
            "action",
            "climbing",
            "platform / fighter scrolling",
            "plateforme / combat scrolling",
            "plateforme / fighter scrolling",
        ],
        "screenscraper_keywords": ["action"],
        "synonyms": [
            "action",
            "action_adventure",
            "action_game",
            "platform_fighter_scrolling",
            "plateforme_combat_scrolling",
            "plateforme_fighter_scrolling",
        ],
        "labels": {
            "fr": "Action / Aventure",
            "en": "Action / Adventure",
            "es": "Acción / Aventura",
            "de": "Action / Abenteuer",
            "it": "Azione / Avventura",
        },
    },
    "fighting": {
        "wikidata_qid": "Q846224",
        "wikidata_qids": ["Q846224"],
        "wikidata_excluded_qids": ["Q401831", "Q1400669", "Q828322"],
        "adb_keywords": [
            "fighter / versus",
            "fighter / versus co-op",
            "fighter / 2d",
            "fighter / 3d",
            "combat / versus",
            "combat / versus co-op",
            "combat / 2d",
            "combat / 3d",
            "sports / wrestling",
            "wrestling",
        ],
        "screenscraper_keywords": [
            "combat",
            "fighting",
            "fighter",
            "combat / versus",
            "combat / 2d",
            "combat / 3d",
            "lucha",
            "luta",
            "kampfspiel",
            "picchiaduro a incontri",
        ],
        "synonyms": [
            "fighting",
            "fighter",
            "fighter_versus",
            "fighter_versus_co_op",
            "fighter_2d",
            "fighter_3d",
            "combat",
            "combat_versus",
            "combat_versus_co_op",
            "combat_2d",
            "combat_3d",
            "versus",
            "vs_fighting",
            "versus_fighting",
            "1v1",
            "fighting_game",
            "lucha",
            "luta",
            "kampfspiel",
        ],
        "labels": {
            "fr": "Combat (1v1 / Versus)",
            "en": "Fighting (1v1)",
            "es": "Lucha (1v1)",
            "de": "Fighting (1v1)",
            "it": "Picchiaduro (1v1)",
        },
    },
    "beatemup": {
        "wikidata_qid": "Q401831",
        "wikidata_qids": ["Q401831", "Q1400669"],
        "adb_keywords": [
            "fighter / 2.5d",
            "fighter / field",
            "fighter / misc",
            "fighter / vertical",
            "fighter / scrolling",
            "beat-'em-up",
            "combat / 2.5d",
            "combat / vertical",
            "combat / defilement",
            "combat / défilement",
            "combat / coop",
            "combat / co-op",
            "combat / champ",
            "combat / rue",
            "combat / misc",
        ],
        "screenscraper_keywords": [
            "beat'em all",
            "beat em all",
            "beat'em up",
            "beat em up",
            "brawler",
            "combat / 2.5d",
            "combat / défilement",
            "combat / vertical",
        ],
        "synonyms": [
            "beatemup",
            "beat_em_up",
            "beat_em_all",
            "brawler",
            "hack_and_slash",
            "combat_2_5d",
            "combat_vertical",
            "combat_defilement",
            "combat_coop",
            "combat_co_op",
            "combat_champ",
            "combat_rue",
            "combat_misc",
            "yo_contra_el_barrio",
            "picchiaduro_a_scorrimento",
            "briga_de_rua",
        ],
        "labels": {
            "fr": "Beat 'em up / Brawler",
            "en": "Beat 'em up / Brawler",
            "es": "Beat 'em up / Yo contra el barrio",
            "de": "Beat ’em up",
            "it": "Picchiaduro a scorrimento",
        },
    },
    "shootemup": {
        "wikidata_qid": "Q1044478",
        "wikidata_qids": ["Q1044478"],
        "adb_keywords": [
            "shoot-'em-up",
            "shooter / flying",
            "shooter / flying vertical",
            "shooter / flying horizontal",
            "shooter / flying 1st person",
            "shooter / gallery",
            "shooter / field",
        ],
        "screenscraper_keywords": ["shoot'em up", "shoot em up", "shmup"],
        "synonyms": ["shootemup", "shoot_em_up", "shmup", "scrolling_shooter", "bullet_hell"],
        "labels": {
            "fr": "Shoot 'em up (Shmup)",
            "en": "Shoot 'em up (Shmup)",
            "es": "Shoot 'em up (Matamarcianos)",
            "de": "Shoot ’em up",
            "it": "Sparatutto (Shmup)",
        },
    },
    "runandgun": {
        "wikidata_qid": "Q60480500",
        "wikidata_qids": ["Q60480500", "Q1044478"],
        "adb_keywords": [
            "shooter / walking",
            "run and gun",
            "platform / shooter",
            "platform / shooter scrolling",
            "platform / shooterscrolling",
        ],
        "screenscraper_keywords": ["run & gun", "run and gun", "tir"],
        "synonyms": [
            "runandgun",
            "run_and_gun",
            "run_gun",
            "shooter_walking",
            "platform_shooter",
            "platform_shooter_scrolling",
            "platform_shooterscrolling",
        ],
        "labels": {
            "fr": "Run & Gun",
            "en": "Run & Gun",
            "es": "Run & Gun",
            "de": "Run & Gun",
            "it": "Run & Gun",
        },
    },
    "rpg": {
        "wikidata_qid": "Q744038",
        "wikidata_qids": ["Q744038", "Q1422746", "Q1529437"],
        "adb_keywords": ["role-playing"],
        "screenscraper_keywords": ["jeu de rôles", "rpg", "role playing"],
        "synonyms": ["rpg", "jrpg", "action_rpg", "role_playing", "role_playing_video_game", "jeu_de_roles"],
        "labels": {
            "fr": "Jeu de rôle (RPG / JRPG)",
            "en": "Role-Playing (RPG / JRPG)",
            "es": "Juego de rol (RPG)",
            "de": "Rollenspiel (RPG)",
            "it": "Gioco di ruolo (RPG)",
        },
    },
    "racing": {
        "wikidata_qid": "Q860750",
        "wikidata_qids": ["Q860750"],
        "adb_keywords": ["driving", "race", "motorcycle"],
        "screenscraper_keywords": ["course", "conduite", "racing", "driving"],
        "synonyms": ["racing", "driving", "course", "racing_video_game", "kart_racing"],
        "labels": {
            "fr": "Course / Conduite",
            "en": "Racing / Driving",
            "es": "Carreras / Conducción",
            "de": "Rennspiel",
            "it": "Corse / Guida",
        },
    },
    "puzzle": {
        "wikidata_qid": "Q54767",
        "wikidata_qids": ["Q54767"],
        "adb_keywords": ["puzzle", "puzzle / drop", "puzzle / match", "puzzle / toss"],
        "screenscraper_keywords": ["puzzle", "réflexion"],
        "synonyms": ["puzzle", "reflexion", "puzzle_video_game", "falling_block"],
        "labels": {
            "fr": "Puzzle / Réflexion",
            "en": "Puzzle",
            "es": "Puzle / Lógica",
            "de": "Puzzle / Geschicklichkeit",
            "it": "Rompicapo / Puzzle",
        },
    },
    "sports": {
        "wikidata_qid": "Q868217",
        "wikidata_qids": ["Q868217"],
        "adb_keywords": [
            "sports",
            "sports / soccer",
            "sports / basketball",
            "sports / baseball",
            "sports / tennis",
            "sports / golf",
            "sports / dodgeball",
        ],
        "screenscraper_keywords": ["sport", "sports"],
        "synonyms": ["sports", "sport", "sports_video_game", "soccer", "football", "basketball", "tennis", "golf"],
        "labels": {
            "fr": "Sport",
            "en": "Sports",
            "es": "Deportes",
            "de": "Sport",
            "it": "Sport",
        },
    },
    "strategy": {
        "wikidata_qid": "Q472055",
        "wikidata_qids": ["Q472055", "Q1529437"],
        "adb_keywords": ["strategy", "tabletop"],
        "screenscraper_keywords": ["stratégie", "strategy", "tactic"],
        "synonyms": ["strategy", "tactical", "rts", "turn_based_strategy", "strategie"],
        "labels": {
            "fr": "Stratégie / Tactique",
            "en": "Strategy / Tactics",
            "es": "Estrategia / Táctica",
            "de": "Strategie / Taktik",
            "it": "Strategia / Tattica",
        },
    },
    "simulation": {
        "wikidata_qid": "Q1610017",
        "wikidata_qids": ["Q1610017"],
        "adb_keywords": ["simulation"],
        "screenscraper_keywords": ["simulation", "vol"],
        "synonyms": ["simulation", "flight_simulator", "space_combat"],
        "labels": {
            "fr": "Simulation",
            "en": "Simulation",
            "es": "Simulación",
            "de": "Simulation",
            "it": "Simulazione",
        },
    },
    "maze": {
        "wikidata_qid": "Q13717398",
        "wikidata_qids": ["Q13717398"],
        "adb_keywords": ["maze"],
        "screenscraper_keywords": ["labyrinthe", "maze"],
        "synonyms": ["maze", "labyrinthe", "pac_man_clone"],
        "labels": {
            "fr": "Arcade Labyrinthe (Maze)",
            "en": "Maze Arcade",
            "es": "Laberinto (Maze)",
            "de": "Labyrinth (Maze)",
            "it": "Labirinto (Maze)",
        },
    },
    "adventure": {
        "wikidata_qid": "Q23916",
        "wikidata_qids": ["Q23916", "Q343568"],
        "adb_keywords": ["adventure"],
        "screenscraper_keywords": ["aventure", "adventure"],
        "synonyms": ["adventure", "aventure", "point_and_click"],
        "labels": {
            "fr": "Aventure",
            "en": "Adventure",
            "es": "Aventura",
            "de": "Abenteuer",
            "it": "Avventura",
        },
    },
    "pinball": {
        "wikidata_qid": "Q3177954",
        "wikidata_qids": ["Q3177954", "Q653928"],
        "adb_keywords": ["pinball"],
        "screenscraper_keywords": ["flipper", "pinball"],
        "synonyms": ["pinball", "flipper"],
        "labels": {
            "fr": "Flipper / Pinball",
            "en": "Pinball",
            "es": "Pinball",
            "de": "Flipper / Pinball",
            "it": "Flipper / Pinball",
        },
    },
    "lightgun": {
        "wikidata_qid": "Q3965952",
        "wikidata_qids": ["Q3965952"],
        "adb_keywords": ["shooter / gun", "lightgun"],
        "screenscraper_keywords": ["tir au pistolet", "lightgun", "gun"],
        "synonyms": ["lightgun", "light_gun", "rail_shooter", "gun_shooter", "shooter_gun"],
        "labels": {
            "fr": "Tir au pistolet (Lightgun)",
            "en": "Light Gun Shooter",
            "es": "Disparos con pistola (Lightgun)",
            "de": "Lightgun-Shooter",
            "it": "Sparatutto con pistola",
        },
    },
}

_IGDB_GENRE_IDS_BY_CANONICAL: Dict[str, Tuple[int, ...]] = {
    "platform": (8,),
    "action": (31,),
    "fighting": (4,),
    "beatemup": (25,),
    "shootemup": (5,),
    "runandgun": (5, 8),
    "rpg": (12,),
    "racing": (10,),
    "puzzle": (9,),
    "sports": (14,),
    "strategy": (15, 16, 24),
    "simulation": (13,),
    "maze": (33,),
    "adventure": (31, 2),
    "pinball": (30,),
    "lightgun": (5,),
}

for _gid, _igdb_ids in _IGDB_GENRE_IDS_BY_CANONICAL.items():
    if _gid in GAME_GENRE_CATALOG:
        GAME_GENRE_CATALOG[_gid]["igdb_genre_ids"] = list(_igdb_ids)

# Specificity ranking when an upstream item carries multiple genre IDs (e.g. IGDB [9, 12, 31] on Zelda)
CANONICAL_GAME_GENRE_PRIORITY: Tuple[str, ...] = (
    "fighting",
    "beatemup",
    "shootemup",
    "runandgun",
    "rpg",
    "platform",
    "racing",
    "sports",
    "strategy",
    "action",
    "adventure",
    "pinball",
    "lightgun",
    "simulation",
    "puzzle",
    "maze",
)

_UNOFFICIAL_TITLE_MARKERS: Tuple[str, ...] = (
    "bug fix",
    "unreal engine",
    "unity remake",
    "randomizer",
    "decompilation",
)


_UNOFFICIAL_SYNOPSIS_MARKERS: Tuple[str, ...] = (
    " is a sub-game ",
    " is a minigame ",
    " is a mini-game ",
)


def is_unofficial_game_release(
    title: str,
    synopsis: str = "",
    publisher: str = "",
) -> bool:
    """
    Return True when metadata indicates a non-retro PC engine tech demo (`Unreal Engine` /
    `Unity Remake`), randomizer seed, bug-fix patch, or embedded sub-game/minigame entry
    rather than a standalone playable retro release (legitimate console/Neo-Geo homebrews
    and ports on real hardware remain allowed).
    """
    _ = publisher
    t_low = (title or "").strip().lower()
    if any(marker in t_low for marker in _UNOFFICIAL_TITLE_MARKERS):
        return True
    s_low = f" {(synopsis or '').strip().lower()} "
    return any(marker in s_low for marker in _UNOFFICIAL_SYNOPSIS_MARKERS)


_GAME_TITLE_PARENS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")

_EXACT_NON_FIGHTING_GAME_SLUGS: Set[str] = {
    "x_men",
    "the_uncanny_x_men",
    "uncanny_x_men",
    "spider_man",
    "the_amazing_spider_man",
    "spider_man_the_video_game",
    "marvel_land",
    "marvelland",
    "wolverine",
    "the_incredible_hulk",
    "incredible_hulk",
    "captain_america_and_the_avengers",
    "the_punisher",
    "punisher",
    "batman",
    "batman_returns",
    "silver_surfer",
    "fantastic_four",
    "comix_zone",
    "final_fight",
    "final_fight_2",
    "final_fight_3",
    "final_fight_cd",
    "mighty_final_fight",
    "golden_axe",
    "golden_axe_ii",
    "golden_axe_2",
    "golden_axe_iii",
    "golden_axe_3",
    "double_dragon_ii",
    "double_dragon_ii_the_revenge",
    "double_dragon_2",
    "double_dragon_3",
    "double_dragon_3_the_arcade_game",
    "double_dragon_iii",
    "super_double_dragon",
}

_NON_FIGHTING_GAME_SUBSTRING_SLUGS: Tuple[str, ...] = (
    "marvel_land",
    "marvelland",
    "spider_man",
    "maximum_carnage",
    "separation_anxiety",
    "arcade_s_revenge",
    "arcades_revenge",
    "clone_wars",
    "adamantium_rage",
    "captain_america_and_the_avengers",
    "incredible_hulk",
    "x_o_manowar",
    "streets_of_rage",
    "bare_knuckle",
    "comix_zone",
    "sonic_the_hedgehog",
    "sonic_and_knuckles",
    "sonic_knuckles",
    "sonic_spinball",
    "sonic_3d",
    "shinobi",
    "shadow_dancer",
    "ghouls_n_ghosts",
    "ghosts_n_goblins",
    "castlevania",
    "contra_hard_corps",
    "probotector",
    "gunstar_heroes",
    "metal_slug",
    "sunset_riders",
    "ristar",
    "kid_chameleon",
    "vectorman",
    "earthworm_jim",
    "castle_of_illusion",
    "world_of_illusion",
    "quackshot",
    "rocket_knight",
    "sparkster",
    "dynamite_headdy",
    "alex_kidd",
    "wonder_boy",
    "toejam",
    "ecco_the_dolphin",
    "robocop_versus_the_terminator",
    "robocop_vs_the_terminator",
    "batman_beyond",
    "batman_revenge_of_the_joker",
    "return_of_the_joker",
    "adventures_of_batman",
    "hyperstone_heist",
    "turtles_in_time",
    "return_of_the_shredder",
    "battletoads",
    "alien_storm",
    "altered_beast",
    "eswat",
    "rolling_thunder",
    "moonwalker",
    "splatterhouse",
    "gauntlet",
    "river_city_ransom",
)


def is_disallowed_non_fighting_title(title: str) -> bool:
    """
    Return True when `title` is an unambiguous Platformer, Beat 'em up, or Action/Run&Gun game
    that must never pass a strict 1v1 VS Fighting (`fighting`) filter even if an upstream catalog
    tags it with a generic `Combat` / `Fighting` umbrella.
    """
    cleaned = _GAME_TITLE_PARENS_RE.sub("", str(title or "")).strip()
    slug = normalize_category_slug(cleaned)
    if not slug:
        return False
    if slug in _EXACT_NON_FIGHTING_GAME_SLUGS:
        return True
    if "golden_axe" in slug and "the_duel" not in slug and "duel" not in slug:
        return True
    if "final_fight" in slug and "revenge" not in slug:
        return True
    for bad in _NON_FIGHTING_GAME_SUBSTRING_SLUGS:
        if bad in slug:
            return True
    return False
