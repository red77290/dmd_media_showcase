"""
Focused regression & entropy suite for Games Discovery (`ScreenScraperAdapter`,
`LibretroWikidataAdapter`, `ArcadeItaliaAdapter`, `DomainSourceChainService`,
and `JsonSeenHistoryRepository`).

Covers:
1. Strict rejection of platformers & beat 'em ups (including Marvel titles such as
   *Marvel Land*, *X-Men*, *X-Men 2: Clone Wars*, *Spider-Man vs. The Kingpin*,
   *Spider-Man & Venom: Maximum Carnage*, *Captain America and the Avengers*,
   *Streets of Rage 2*, *Sonic The Hedgehog 2*) when `systems=['megadrive']` and
   `genres=['fighting']` (`vs_fighting`) are requested, while accepting authentic
   Mega Drive 1v1 VS Fighting titles.
2. Strict system ID & Wikidata QID enforcement (`megadrive` = `ss_id=1` / `Q10676`,
   `psx` = `ss_id=57` / `Q10677`), schema v3 cache invalidation of poisoned `Q10677`
   entries, and exact Libretro No-Intro filename matching (preventing PS1/Arcade
   subtitles like *X-Men: Children of the Atom* or *X-Men: Mutant Academy* from
   matching Mega Drive's *X-Men (Europe).png* platformer).
3. High-entropy discovery across repeated calls, token rotation without immediate
   repeats, and anti-clustering when `count > 1`.
4. Strict `extra_exclude_ids` + `seen_history.json` ID & canonical title-fingerprint
   persistence across restarts (blocking regional clones and cross-source duplicates),
   plus protection against premature `seen_history.json` resets on transient errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pytest

from app.application.source_chain_service import DomainSourceChainService
from app.domain.entities import FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.domain.taxonomy import (
    GAME_GENRE_CATALOG,
    RETRO_SYSTEM_CATALOG,
    build_game_fingerprint,
    resolve_canonical_genre,
    resolve_canonical_system,
)
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.scrapers.games.libretro_wikidata_adapter import LibretroWikidataAdapter
from app.infrastructure.scrapers.games.screenscraper_adapter import (
    ScreenScraperAdapter,
    parse_jeu_infos_xml,
)
from app.infrastructure.scrapers.referential_store import LibretroWikidataReferentialStrategy
from app.infrastructure.scrapers.registry import ScraperRegistry
from app.models import SessionRegisterRequest
from app.providers.games import request_to_criteria


def _make_ss_jeu_dict(
    game_id: str,
    title: str,
    sys_id: str = "1",
    sys_text: str = "Megadrive",
    genres: Optional[List[Dict[str, str]]] = None,
    year: str = "1993",
    cloneof: str = "0",
) -> Dict[str, Any]:
    return {
        "id": game_id,
        "notgame": "false",
        "cloneof": cloneof,
        "systeme": {"id": sys_id, "text": sys_text},
        "noms": [{"region": "eu", "text": title}, {"region": "wor", "text": title}],
        "dates": [{"region": "eu", "text": f"{year}-06-15"}],
        "editeur": {"id": "1", "text": "SEGA"},
        "note": {"text": "17"},
        "genres": genres
        or [
            {"id": "5", "parentid": "0", "principale": "1", "langue": "fr", "text": "Combat"},
            {"id": "5", "parentid": "0", "principale": "1", "langue": "en", "text": "Fighter"},
        ],
        "synopsis": [{"langue": "fr", "text": f"{title} sur {sys_text}."}],
        "medias": [
            {
                "parent": "jeu",
                "type": "box-2D",
                "region": "eu",
                "url": f"https://neoclone.screenscraper.fr/media/{sys_id}/{game_id}/box-2D(eu).png",
            },
            {
                "parent": "jeu",
                "type": "video-normalized",
                "region": "",
                "url": f"https://neoclone.screenscraper.fr/media/{sys_id}/{game_id}/video.mp4",
            },
        ],
    }


def test_taxonomy_megadrive_psx_wikidata_qids_and_multilingual_genre_mapping():
    """Verify exact Wikidata platform QIDs and multilingual ScreenScraper/ADB genre normalization."""
    assert RETRO_SYSTEM_CATALOG["megadrive"]["wikidata_qid"] == "Q10676"
    assert RETRO_SYSTEM_CATALOG["psx"]["wikidata_qid"] == "Q10677"

    assert resolve_canonical_system("Mega Drive") == "megadrive"
    assert resolve_canonical_system("Sega Genesis") == "megadrive"
    assert resolve_canonical_system("Mega-CD") != "megadrive"
    assert resolve_canonical_system("Sega 32X") != "megadrive"
    assert resolve_canonical_system("PlayStation 2") != "psx"

    # True 1v1 fighting synonyms across FR/EN/ES/DE
    assert resolve_canonical_genre("games", "fighting") == "fighting"
    assert resolve_canonical_genre("games", "vs_fighting") == "fighting"
    assert resolve_canonical_genre("games", "Fighter") == "fighting"
    assert resolve_canonical_genre("games", "Combat") == "fighting"
    assert resolve_canonical_genre("games", "Combat / Versus") == "fighting"
    assert resolve_canonical_genre("games", "Fighter / Versus Co-op") == "fighting"
    assert resolve_canonical_genre("games", "Combat / 2D") == "fighting"

    # Beat 'em up & melee-platformer compounds must NEVER map to "fighting"
    assert resolve_canonical_genre("games", "Beat'em all") == "beatemup"
    assert resolve_canonical_genre("games", "Beat'em up") == "beatemup"
    assert resolve_canonical_genre("games", "Combat / 2.5D") == "beatemup"
    assert resolve_canonical_genre("games", "Combat / Défilement") == "beatemup"
    assert resolve_canonical_genre("games", "Combat / Coop") == "beatemup"
    assert resolve_canonical_genre("games", "Fighter / Field") == "beatemup"
    assert resolve_canonical_genre("games", "Plateforme / Combat Scrolling") == "action"
    assert resolve_canonical_genre("games", "Platform / Fighter Scrolling") == "action"
    assert resolve_canonical_genre("games", "Plateforme") == "platform"

    excluded = set(GAME_GENRE_CATALOG["fighting"].get("wikidata_excluded_qids") or [])
    assert {"Q401831", "Q1400669", "Q828322"}.issubset(excluded)

    req = SessionRegisterRequest(systems=["Genesis"], genres=["vs_fighting"])
    crit = request_to_criteria("games", req)
    assert crit.systems == ["megadrive"]
    assert crit.genres == ["fighting"]


def test_screenscraper_megadrive_vs_fighting_rejects_marvel_platformers_and_beatemups():
    """
    Ensure `systems=['megadrive']` + `genres=['vs_fighting']` strictly rejects Marvel/SEGA
    platformers and beat 'em ups (even when tagged with ScreenScraper's umbrella `Combat`/`Fighter`
    parent genre) while accepting authentic Mega Drive 1v1 VS Fighting games.
    """
    adapter = ScreenScraperAdapter()
    criteria = FilterCriteria(
        domain="games",
        systems=["megadrive"],
        genres=[resolve_canonical_genre("games", "vs_fighting")],
        lang="fr",
    )

    forbidden_nodes = [
        _make_ss_jeu_dict(
            "201",
            "Marvel Land",
            genres=[{"id": "7", "parentid": "0", "langue": "fr", "text": "Plateforme"}],
        ),
        _make_ss_jeu_dict(
            "202",
            "X-Men",
            genres=[
                {"id": "1", "parentid": "0", "langue": "fr", "text": "Action"},
                {"id": "7", "parentid": "0", "langue": "fr", "text": "Plateforme"},
                {"id": "5", "parentid": "0", "langue": "fr", "text": "Combat"},
            ],
        ),
        _make_ss_jeu_dict(
            "203",
            "X-Men 2 : Clone Wars",
            genres=[
                {"id": "7", "parentid": "0", "langue": "fr", "text": "Plateforme"},
                {"id": "99", "parentid": "7", "langue": "fr", "text": "Plateforme / Combat Scrolling"},
            ],
        ),
        _make_ss_jeu_dict(
            "204",
            "The Amazing Spider-Man vs. The Kingpin",
            genres=[
                {"id": "7", "parentid": "0", "langue": "en", "text": "Platform"},
                {"id": "5", "parentid": "0", "langue": "en", "text": "Fighter"},
            ],
        ),
        _make_ss_jeu_dict(
            "205",
            "Spider-Man & Venom : Maximum Carnage",
            genres=[
                {"id": "5", "parentid": "0", "langue": "fr", "text": "Combat"},
                {"id": "19", "parentid": "5", "langue": "fr", "text": "Beat'em all"},
            ],
        ),
        _make_ss_jeu_dict(
            "206",
            "Captain America and the Avengers",
            genres=[
                {"id": "5", "parentid": "0", "langue": "fr", "text": "Combat"},
                {"id": "20", "parentid": "5", "langue": "fr", "text": "Combat / 2.5D"},
            ],
        ),
        _make_ss_jeu_dict(
            "207",
            "Streets of Rage 2",
            genres=[
                {"id": "5", "parentid": "0", "langue": "fr", "text": "Combat"},
                {"id": "19", "parentid": "5", "langue": "fr", "text": "Beat'em all"},
            ],
        ),
        _make_ss_jeu_dict(
            "208",
            "Sonic The Hedgehog 2",
            genres=[{"id": "7", "parentid": "0", "langue": "fr", "text": "Plateforme"}],
        ),
    ]
    for bad_jeu in forbidden_nodes:
        parsed = adapter._parse_game_node(bad_jeu, "megadrive", "fr", criteria=criteria)
        assert parsed is None, f"Expected non-VS-fighting game '{bad_jeu['noms'][0]['text']}' to be rejected!"

    authentic_md_fighters = [
        _make_ss_jeu_dict(
            "301",
            "Street Fighter II' : Special Champion Edition",
            genres=[
                {"id": "5", "parentid": "0", "langue": "fr", "text": "Combat"},
                {"id": "58", "parentid": "5", "langue": "fr", "text": "Combat / Versus"},
            ],
        ),
        _make_ss_jeu_dict("302", "Eternal Champions"),
        _make_ss_jeu_dict(
            "303",
            "Mortal Kombat II",
            genres=[{"id": "58", "parentid": "5", "langue": "en", "text": "Fighter / Versus"}],
        ),
        _make_ss_jeu_dict("304", "Fatal Fury 2"),
        _make_ss_jeu_dict(
            "305",
            "Dragon Ball Z : L'Appel du Destin",
            genres=[{"id": "59", "parentid": "5", "langue": "fr", "text": "Combat / 2D"}],
        ),
        _make_ss_jeu_dict(
            "306",
            "Yu Yu Hakusho : Makyo Toitsusen",
            year="1994",
            genres=[{"id": "58", "parentid": "5", "langue": "fr", "text": "Combat / Versus"}],
        ),
    ]
    for good_jeu in authentic_md_fighters:
        cand = adapter._parse_game_node(good_jeu, "megadrive", "fr", criteria=criteria)
        assert cand is not None, f"Expected true Mega Drive fighter '{good_jeu['noms'][0]['text']}' to pass!"
        assert cand.badge == "MD"
        assert cand.extra_meta["system"] == "megadrive"
        assert "fighting" in cand.extra_meta["genres"]

    # Meanwhile on CPS-2, true Marvel 1v1 fighting games MUST be accepted!
    cps2_criteria = FilterCriteria(domain="games", systems=["cps2"], genres=["fighting"], lang="fr")
    xmcota_cps2 = _make_ss_jeu_dict(
        "401",
        "X-Men : Children of the Atom",
        sys_id="106",
        sys_text="CPS-2",
        year="1994",
        genres=[{"id": "58", "parentid": "5", "langue": "en", "text": "Fighter / Versus"}],
    )
    cand_cps2 = adapter._parse_game_node(xmcota_cps2, "cps2", "fr", criteria=cps2_criteria)
    assert cand_cps2 is not None
    assert cand_cps2.badge == "CPS-2"

    # Also verify XML `lang="fr"` attribute compatibility in `parse_jeu_infos_xml`
    xml_payload = """<?xml version="1.0" encoding="UTF-8" ?>
    <Data><jeu id="302">
        <systeme id="1">Megadrive</systeme>
        <noms><nom region="eu">Eternal Champions</nom></noms>
        <dates><date region="eu">1993</date></dates>
        <genres><genre id="5" principale="1" parentid="0" lang="fr">Combat</genre></genres>
        <medias><media parent="jeu" type="box-2D" region="eu">https://example.com/ec.png</media></medias>
    </jeu></Data>"""
    xml_nodes = parse_jeu_infos_xml(xml_payload)
    assert len(xml_nodes) == 1
    assert adapter._parse_game_node(xml_nodes[0], "megadrive", "fr", criteria=criteria) is not None


@pytest.mark.asyncio
async def test_strict_system_enforcement_and_libretro_wikidata_anti_pollution(tmp_path: Path, monkeypatch):
    """
    Verify:
    1. `ScreenScraperAdapter` strictly rejects Arcade (`75`), CPS-2 (`106`), SNES (`4`),
       PS1 (`57`), Mega-CD (`20`), and Sega 32X (`19`) nodes when `systems=['megadrive']`.
    2. `LibretroWikidataReferentialStrategy.resolve_verified_libretro_urls` never collapses
       `X-Men: Children of the Atom`, `X-Men: Mutant Academy`, `Batman Beyond: Return of the Joker`,
       or `Mortal Kombat 4` onto Mega Drive's `X-Men (Europe).png`, `Batman (Europe).png`, or
       `Mortal Kombat (Europe).png`, and ignores `[Hack` / `(Beta)` / `(Virtual Console)` dumps.
    3. Legacy poisoned `schema_version == 2` Wikidata cache (`Q10677` PS1 titles under `megadrive:fighting`)
       is invalidated automatically and re-harvested with `wd:Q10676` + `schema_version == 3`.
    """
    monkeypatch.setenv("DMD_DATA_DIR", str(tmp_path / "md_strict_data"))

    ss_adapter = ScreenScraperAdapter()
    md_criteria = FilterCriteria(domain="games", systems=["megadrive"], genres=["fighting"], lang="fr")

    wrong_system_nodes = [
        _make_ss_jeu_dict("501", "Street Fighter Alpha 2", sys_id="106", sys_text="CPS-2"),
        _make_ss_jeu_dict("502", "Tekken 3", sys_id="57", sys_text="PlayStation", year="1998"),
        _make_ss_jeu_dict("503", "Killer Instinct", sys_id="4", sys_text="Super Nintendo", year="1995"),
        _make_ss_jeu_dict("504", "Mortal Kombat", sys_id="75", sys_text="MAME"),
        _make_ss_jeu_dict("505", "Eternal Champions : Challenge from the Dark Side", sys_id="20", sys_text="Mega-CD"),
        _make_ss_jeu_dict("506", "Virtua Fighter", sys_id="19", sys_text="Sega 32X"),
    ]
    for node in wrong_system_nodes:
        assert ss_adapter._parse_game_node(node, "megadrive", "fr", criteria=md_criteria) is None

    # Seed a poisoned schema_version=2 cache mimicking the real forensic bug (PS1 Q10677 games in megadrive:fighting)
    strat = LibretroWikidataReferentialStrategy()
    strat.store.save(
        tokens=["megadrive:Q1320987"],
        metadata={
            "schema_version": 2,
            "by_system_genre": {
                "megadrive:fighting": [
                    {
                        "qid": "Q1320987",
                        "system": "megadrive",
                        "genre": "fighting",
                        "title_en": "X-Men: Mutant Academy",
                        "title_fr": "X-Men: Mutant Academy",
                        "year": "2000",
                    }
                    for _ in range(8)
                ]
            },
        },
    )

    # Seed Mega Drive Libretro file tree with real No-Intro filenames (including platformers & hacks)
    tstore = strat._get_tree_store("megadrive")
    tstore.save(
        tokens=["Eternal Champions (Europe).png"],
        metadata={
            "repo": "Sega_-_Mega_Drive_-_Genesis",
            "archive_video_repo": "sega-genesis-video-snaps",
            "folders": {
                "Named_Boxarts": [
                    "X-Men (Europe).png",
                    "Spider-Man (World) (Sega).png",
                    "Batman (Europe).png",
                    "Mortal Kombat (Europe).png",
                    "Mortal Kombat II (Europe).png",
                    "Eternal Champions (Europe).png",
                    "Dragon Ball Z - L'Appel du Destin (France).png",
                    "Street Fighter II' - Special Champion Edition (Europe) (Virtual Console).png",
                    "Street Fighter II' - Special Champion Edition (Europe).png",
                    "Street Fighter II' - Hyper Champion Edition.png",
                    "Super Street Fighter II (Europe).png",
                    "Ultimate Mortal Kombat 3 - Arcade Hack [Hack by Nemesis_c].png",
                ],
                "Named_Titles": ["Eternal Champions (Europe).png"],
                "Named_Snaps": ["Eternal Champions (Europe).png"],
                "Video_Snaps": [
                    "X-Men (USA).ia.mp4",
                    "Spider-Man (World) (Sega).ia.mp4",
                    "Eternal Champions (USA).ia.mp4",
                    "Street Fighter II' - Special Champion Edition (USA).ia.mp4",
                ],
            },
        },
    )

    captured_sparql: List[str] = []

    class _MockResp:
        def __init__(self, status_code: int, json_data: Any = None):
            self.status_code = status_code
            self._json = json_data or {}

        def json(self) -> Any:
            return self._json

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url: str, params: Any = None, headers: Any = None, timeout: Any = None):
            if "query.wikidata.org/sparql" in url:
                q = str((params or {}).get("query") or "")
                captured_sparql.append(q)
                return _MockResp(
                    200,
                    {
                        "results": {
                            "bindings": [
                                {
                                    "game": {"value": "http://www.wikidata.org/entity/Q2632379"},
                                    "titleEn": {"value": "Eternal Champions"},
                                    "titleFr": {"value": "Eternal Champions"},
                                    "year": {"value": "1993"},
                                },
                                {
                                    "game": {"value": "http://www.wikidata.org/entity/Q1132568"},
                                    "titleEn": {"value": "Dragon Ball Z: Buyuu Retsuden"},
                                    "titleFr": {"value": "Dragon Ball Z : L'Appel du Destin"},
                                    "year": {"value": "1994"},
                                },
                                # Noisy upstream Marvel platformer must be stripped by harvest filter
                                {
                                    "game": {"value": "http://www.wikidata.org/entity/Q234567"},
                                    "titleEn": {"value": "Spider-Man"},
                                    "titleFr": {"value": "Spider-Man"},
                                    "year": {"value": "1991"},
                                },
                            ]
                        }
                    },
                )
            if "wikipedia.org" in url:
                return _MockResp(200, {"extract": "Jeu de combat 1v1 sur Sega Mega Drive."})
            return _MockResp(404)

    client = _MockClient()

    # PS1 / CPS-2 / N64 subtitled games must NEVER match Mega Drive's X-Men / Batman / MK1 assets
    for alien_title in (
        "X-Men: Children of the Atom",
        "X-Men: Mutant Academy",
        "Batman Beyond: Return of the Joker",
        "Mortal Kombat 4",
        "Mortal Kombat Mythologies: Sub-Zero",
        "Fatal Fury: Wild Ambition",
    ):
        urls = await strat.resolve_verified_libretro_urls(client, "megadrive", alien_title, lang="fr")
        assert urls["boxart"] is None, f"Alien game '{alien_title}' falsely matched boxart {urls['boxart']}"
        assert urls["video"] is None, f"Alien game '{alien_title}' falsely matched video {urls['video']}"

    # Authentic Mega Drive fighting titles (including edition subtitles) MUST resolve cleanly without VC/Hack dumps
    sf2_urls = await strat.resolve_verified_libretro_urls(client, "megadrive", "Street Fighter II", lang="fr")
    assert sf2_urls["boxart"] is not None
    assert "Special%20Champion%20Edition%20%28Europe%29.png" in sf2_urls["boxart"]
    assert "Virtual%20Console" not in sf2_urls["boxart"]
    assert "Hyper%20Champion" not in sf2_urls["boxart"]

    ssf2_urls = await strat.resolve_verified_libretro_urls(
        client, "megadrive", "Super Street Fighter II: The New Challengers", lang="fr"
    )
    assert ssf2_urls["boxart"] is not None
    assert "Super%20Street%20Fighter%20II%20%28Europe%29.png" in ssf2_urls["boxart"]

    dbz_urls = await strat.resolve_verified_libretro_urls(
        client, "megadrive", "Dragon Ball Z : L'Appel du Destin", lang="fr"
    )
    assert dbz_urls["boxart"] is not None
    assert "L%27Appel%20du%20Destin%20%28France%29.png" in dbz_urls["boxart"]

    # Harvest must ignore poisoned schema_version=2, query Q10676 + FILTER NOT EXISTS, and reject Spider-Man
    harvested = await strat.harvest_wikidata_games(client, "megadrive", "fighting")
    assert len(captured_sparql) == 1
    assert "wd:Q10676" in captured_sparql[0]
    assert "wd:Q10677" not in captured_sparql[0]
    assert "FILTER NOT EXISTS" in captured_sparql[0]
    assert "wd:Q401831" in captured_sparql[0]
    titles = {g["title_en"] for g in harvested}
    assert titles == {"Eternal Champions", "Dragon Ball Z: Buyuu Retsuden"}
    assert strat.store.load()["metadata"]["schema_version"] == 3

    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.libretro_wikidata_adapter.create_async_http_client",
        lambda timeout=7.5, headers=None: client,
    )
    lw_adapter = LibretroWikidataAdapter(referential_strategy=strat)
    cands = await lw_adapter.fetch_random_unseen(md_criteria, seen_ids=set(), count=5)
    assert {c.title for c in cands} == {"Eternal Champions", "Dragon Ball Z : L'Appel du Destin"}

    # Verify ArcadeItaliaAdapter also delegates 100% to console_adapter for megadrive and never leaks MAME ROMs
    from app.infrastructure.scrapers.games.arcadeitalia_adapter import ArcadeItaliaAdapter

    adb_adapter = ArcadeItaliaAdapter(console_adapter=lw_adapter)
    adb_cands = await adb_adapter.fetch_random_unseen(md_criteria, seen_ids=set(), count=5)
    assert {c.title for c in adb_cands} == {"Eternal Champions", "Dragon Ball Z : L'Appel du Destin"}
    assert all(not c.id.startswith("games_adb_") and c.extra_meta["system"] == "megadrive" for c in adb_cands)


@pytest.mark.asyncio
async def test_high_entropy_random_discovery_anti_clustering_and_token_rotation(monkeypatch):
    """
    Verify:
    1. Consecutive `fetch_random_unseen(count=1)` calls rotate across diverse query tokens
       without repeating recent `recherche` stems and yield 18 unique Mega Drive fighters
       across 18 draws with zero platformers/beat 'em ups.
    2. `fetch_random_unseen(count=5)` spreads across distinct query tokens (1 primary candidate
       per token before touching overflow) so results never cluster on a single prefix.
    """
    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.screenscraper_adapter.server_config_store.get_all",
        lambda: {
            "screenscraper_dev_id": "dev_test",
            "screenscraper_dev_pass": "pass_test",
            "screenscraper_user": "",
            "screenscraper_pass": "",
        },
    )

    md_fighters_catalog = [
        ("1001", "Street Fighter II' : Special Champion Edition"),
        ("1002", "Super Street Fighter II"),
        ("1003", "Mortal Kombat"),
        ("1004", "Mortal Kombat II"),
        ("1005", "Mortal Kombat 3"),
        ("1006", "Ultimate Mortal Kombat 3"),
        ("1007", "Eternal Champions"),
        ("1008", "Fatal Fury"),
        ("1009", "Fatal Fury 2"),
        ("1010", "Samurai Shodown"),
        ("1011", "Art of Fighting"),
        ("1012", "World Heroes"),
        ("1013", "Dragon Ball Z : L'Appel du Destin"),
        ("1014", "Yu Yu Hakusho : Makyo Toitsusen"),
        ("1015", "Primal Rage"),
        ("1016", "ClayFighter"),
        ("1017", "WeaponLord"),
        ("1018", "Virtua Fighter 2"),
        ("1019", "Justice League Task Force"),
        ("1020", "Brutal : Paws of Fury"),
        ("1021", "Fighting Masters"),
        ("1022", "Deadly Moves"),
        ("1023", "Ballz 3D"),
        ("1024", "Double Dragon V : The Shadow Falls"),
    ]
    distractors = [
        _make_ss_jeu_dict(
            "9001",
            "Marvel Land",
            genres=[{"id": "7", "parentid": "0", "langue": "fr", "text": "Plateforme"}],
        ),
        _make_ss_jeu_dict(
            "9002",
            "X-Men",
            genres=[
                {"id": "7", "parentid": "0", "langue": "fr", "text": "Plateforme"},
                {"id": "5", "parentid": "0", "langue": "fr", "text": "Combat"},
            ],
        ),
        _make_ss_jeu_dict(
            "9003",
            "Streets of Rage 2",
            genres=[
                {"id": "5", "parentid": "0", "langue": "fr", "text": "Combat"},
                {"id": "19", "parentid": "5", "langue": "fr", "text": "Beat'em all"},
            ],
        ),
        _make_ss_jeu_dict("9004", "Tekken 3", sys_id="57", sys_text="PlayStation"),
    ]
    fighter_nodes = [_make_ss_jeu_dict(gid, title) for gid, title in md_fighters_catalog]

    queried_tokens: List[str] = []

    class _MockResp:
        def __init__(self, jeux: List[Dict[str, Any]]):
            self.status_code = 200
            self.text = ""
            self._jeux = jeux

        def json(self) -> Dict[str, Any]:
            return {"response": {"jeux": list(self._jeux)}}

    class _CatalogHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url: str, params: Any = None, **kwargs: Any):
            tok = str((params or {}).get("recherche") or "").lower()
            queried_tokens.append(tok)
            matched = [
                n for n in fighter_nodes if tok in n["noms"][0]["text"].lower()
            ]
            if not matched:
                # Simulate deterministic substring window from catalog so any 2-letter syllable can hit
                idx = sum(ord(c) for c in tok) % len(fighter_nodes)
                matched = [fighter_nodes[idx], fighter_nodes[(idx + 7) % len(fighter_nodes)]]
            return _MockResp(distractors + matched)

    monkeypatch.setattr(
        "app.infrastructure.scrapers.games.screenscraper_adapter.create_async_http_client",
        lambda timeout=6.0, headers=None: _CatalogHttpClient(),
    )

    adapter = ScreenScraperAdapter()
    criteria = FilterCriteria(domain="games", systems=["megadrive"], genres=["fighting"], lang="fr")
    seen: Set[str] = set()
    drawn_ids: List[str] = []

    for _ in range(18):
        batch = await adapter.fetch_random_unseen(criteria=criteria, seen_ids=seen, count=1)
        assert len(batch) == 1
        cand = batch[0]
        assert cand.id not in drawn_ids
        assert cand.extra_meta["system"] == "megadrive"
        assert cand.title not in {"Marvel Land", "X-Men", "Streets of Rage 2", "Tekken 3"}
        drawn_ids.append(cand.id)

    assert len(set(drawn_ids)) == 18
    # Consecutive calls must rotate through distinct discovery tokens rather than repeating a few stems
    assert len(set(queried_tokens[:18])) >= 15

    # Verify anti-clustering when count=5: even if every token returns 4 items, 5 distinct tokens are queried!
    queried_tokens.clear()
    multi_batch = await adapter.fetch_random_unseen(criteria=criteria, seen_ids=set(), count=5)
    assert len(multi_batch) == 5
    assert len({c.id for c in multi_batch}) == 5
    assert len(set(queried_tokens)) >= 5


@pytest.mark.asyncio
async def test_seen_history_persistence_clone_and_cross_source_dedup_and_transient_error_safety(
    tmp_path: Path,
):
    """
    Verify:
    1. `extra_exclude_ids` is strictly enforced even if an adapter ignores `seen_ids`.
    2. Consumed game IDs and canonical `(system, title)` fingerprints persist in `seen_history.json`
       across repository reloads/restarts without inflating `stats()` or `get_seen_ids()`, blocking
       both regional clones and cross-source (`screenscraper` -> `libretro_wikidata`) duplicates.
    3. Transient scraper failures (`[]` or exceptions) NEVER wipe `seen_history.json`, whereas
       genuine catalog exhaustion cleanly resets and starts a new cycle.
    """
    seen_file = tmp_path / "seen_history.json"
    repo = JsonSeenHistoryRepository(file_path=seen_file)
    criteria = FilterCriteria(domain="games", systems=["megadrive"], genres=["fighting"], lang="fr")

    sf2_ss = MediaCandidate(
        id="games_ss_1420",
        domain="games",
        title="Street Fighter II' : Special Champion Edition",
        year="1993",
        badge="MD",
        badge_color="#1976D2",
        subtitle="Combat • Capcom",
        rating="9.2",
        synopsis="SF2 sur Mega Drive.",
        poster_url="https://example.com/sf2_ss.png",
        source_id="screenscraper",
        extra_meta={"system": "megadrive", "genre": "fighting", "genres": ["fighting"]},
    )
    sf2_clone_ss = MediaCandidate(
        id="games_ss_9999",
        domain="games",
        title="Street Fighter II': Special Champion Edition (USA)",
        year="1993",
        badge="MD",
        badge_color="#1976D2",
        subtitle="Combat • Capcom",
        rating="9.2",
        synopsis="SF2 US clone.",
        poster_url="https://example.com/sf2_us.png",
        source_id="screenscraper",
        extra_meta={"system": "megadrive", "genre": "fighting", "genres": ["fighting"]},
    )
    sf2_libretro = MediaCandidate(
        id="games_libretro_megadrive_street_fighter_ii_special_champion_edition",
        domain="games",
        title="Street Fighter II' - Special Champion Edition",
        year="1993",
        badge="MD",
        badge_color="#1976D2",
        subtitle="Combat • MD",
        rating="8.8",
        synopsis="SF2 Libretro fallback.",
        poster_url="https://example.com/sf2_lr.png",
        source_id="libretro_wikidata",
        extra_meta={"system": "megadrive", "genre": "fighting"},
    )
    eternal_libretro = MediaCandidate(
        id="games_libretro_megadrive_eternal_champions",
        domain="games",
        title="Eternal Champions",
        year="1993",
        badge="MD",
        badge_color="#1976D2",
        subtitle="Combat • MD",
        rating="8.8",
        synopsis="Eternal Champions sur Mega Drive.",
        poster_url="https://example.com/ec_lr.png",
        source_id="libretro_wikidata",
        extra_meta={"system": "megadrive", "genre": "fighting"},
    )
    rogue_platformer = MediaCandidate(
        id="games_ss_7777",
        domain="games",
        title="Marvel Land",
        year="1991",
        badge="MD",
        badge_color="#1976D2",
        subtitle="Plateforme • Namco",
        rating="7.5",
        synopsis="Platformer.",
        poster_url="https://example.com/ml.png",
        source_id="screenscraper",
        extra_meta={"system": "megadrive", "genre": "platform", "genres": ["platform"]},
    )

    class _ConfigurableSource(IScraperSource):
        def __init__(self, source_id: str, role: str, items: List[MediaCandidate]):
            self.source_id = source_id
            self.domain = "games"
            self.role = role
            self.items = list(items)
            self.fail_transiently = False

        def is_configured(self) -> bool:
            return True

        def supported_filters(self, lang: str = "fr"):
            return {"systems": [], "genres": [], "decades": []}

        async def fetch_random_unseen(self, criteria: FilterCriteria, seen_ids: Set[str], count: int = 1):
            if self.fail_transiently:
                raise RuntimeError("Simulated ScreenScraper HTTP 429 / network timeout")
            # Intentionally return items without pre-filtering `extra_exclude_ids` to test service guard
            return [c for c in self.items if c.id not in seen_ids][: max(count, 4)]

        async def search_by_query(self, query: str, lang: str = "fr"):
            return None

    primary = _ConfigurableSource("screenscraper", "primary", [rogue_platformer, sf2_ss, sf2_clone_ss])
    fallback = _ConfigurableSource("libretro_wikidata", "fallback", [sf2_libretro, eternal_libretro])

    reg = ScraperRegistry()
    reg._chains["games"] = [primary, fallback]
    svc = DomainSourceChainService(registry=reg, history_repo=repo)

    # 1. `extra_exclude_ids` blocks `games_ss_1420` (and its staged fingerprint blocks clones),
    # while defense-in-depth blocks `rogue_platformer` ("Marvel Land") -> picks `Eternal Champions`!
    first_with_excl = await svc.acquire_unseen_candidates(
        criteria, count=1, extra_exclude_ids={"games_ss_1420"}
    )
    assert len(first_with_excl) == 1
    assert first_with_excl[0].id not in {"games_ss_1420", "games_ss_7777"}

    draw_1 = await svc.acquire_unseen_candidates(criteria, count=1)
    assert len(draw_1) == 1
    assert draw_1[0].id == "games_ss_1420"
    repo.mark_consumed("games", draw_1[0].id)

    assert repo.stats()["games"] == 1
    assert repo.get_seen_ids("games") == {"games_ss_1420"}

    # 2. Simulate full service restart with a brand-new repository instance reading `seen_history.json`
    repo_restarted = JsonSeenHistoryRepository(file_path=seen_file)
    assert repo_restarted.stats()["games"] == 1
    assert repo_restarted.get_seen_ids("games") == {"games_ss_1420"}
    expected_fp = build_game_fingerprint("megadrive", "Street Fighter II' : Special Champion Edition")
    assert expected_fp in repo_restarted.get_seen_fingerprints("games")

    svc_restarted = DomainSourceChainService(registry=reg, history_repo=repo_restarted)
    draw_2 = await svc_restarted.acquire_unseen_candidates(criteria, count=1)
    assert len(draw_2) == 1
    # Both `sf2_clone_ss` AND `sf2_libretro` share the same canonical SF2:SCE fingerprint and are skipped!
    assert draw_2[0].id == "games_libretro_megadrive_eternal_champions"
    repo_restarted.mark_consumed("games", draw_2[0].id)
    assert repo_restarted.stats()["games"] == 2

    # 3. Simulate a transient outage on both sources when all items are in seen_history:
    # `seen_history.json` MUST NOT be wiped!
    primary.fail_transiently = True
    fallback.fail_transiently = True
    outage_draw = await svc_restarted.acquire_unseen_candidates(criteria, count=1)
    assert outage_draw == []
    assert repo_restarted.get_seen_ids("games") == {
        "games_ss_1420",
        "games_libretro_megadrive_eternal_champions",
    }
    raw_disk = json.loads(seen_file.read_text(encoding="utf-8"))
    assert set(raw_disk["games"]) == {
        "games_ss_1420",
        "games_libretro_megadrive_eternal_champions",
    }

    # 4. Once sources recover, true catalog exhaustion is proven by the probe pass -> resets cleanly!
    primary.fail_transiently = False
    fallback.fail_transiently = False
    fresh_cycle_draw = await svc_restarted.acquire_unseen_candidates(criteria, count=1)
    assert len(fresh_cycle_draw) == 1
    assert fresh_cycle_draw[0].id == "games_ss_1420"
    assert repo_restarted.stats()["games"] == 0
