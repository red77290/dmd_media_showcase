"""
Unit tests for pure Batch Scraper Domain Specifications, `FolderNamingPolicy`,
Recalbox `gamelist.xml` parser, and generic `BatchScrapeJob` aggregate.
"""

from __future__ import annotations

import pytest

from app.domain.aggregates.batch_job import BatchScrapeJob
from app.domain.batch_scraper_specs import (
    BatchScrapeConfig,
    ChapterSelectionSpec,
    FolderNamingPolicy,
    GifEncodingSpec,
    clamp_batch_video_duration,
)
from app.domain.gamelist_catalog import extract_rom_stem, parse_recalbox_gamelist_xml


def test_folder_naming_policy_flat_clock_playlists():
    assert (
        FolderNamingPolicy.resolve_clock_folder_name("games", "Super Nintendo", "Plateforme")
        == "snes_platformer"
    )
    assert (
        FolderNamingPolicy.resolve_clock_folder_name("games", "Mega Drive", "Combat / Versus")
        == "megadrive_fighting"
    )
    assert (
        FolderNamingPolicy.resolve_clock_folder_name("games", "Neo-Geo", "Run & Gun")
        == "neogeo_run&gun"
    )
    assert (
        FolderNamingPolicy.resolve_clock_folder_name(
            "games", "Neo-Geo", "Run & Gun", safe_ascii=True
        )
        == "neogeo_runandgun"
    )
    assert (
        FolderNamingPolicy.resolve_clock_folder_name("movies", "", "Science-Fiction")
        == "movies_scifi"
    )
    # Zero nested folders unless wrap_orientation_root=True
    assert "/" not in FolderNamingPolicy.resolve_clock_folder_name("games", "snes", "rpg")
    assert (
        FolderNamingPolicy.resolve_clock_folder_name(
            "games", "mame", "shootemup", is_tate=True, wrap_orientation_root=True
        )
        == "gifs_tate/mame_shootemup"
    )


def test_video_duration_clamps_up_to_120_seconds():
    assert clamp_batch_video_duration(0.5) == 2.0
    assert clamp_batch_video_duration(8.0) == 8.0
    assert clamp_batch_video_duration(120.0) == 120.0
    assert clamp_batch_video_duration(300.0) == 120.0

    enc = GifEncodingSpec(width=64, height=128, fps=20, video_max_duration_s=95.0).normalized()
    assert enc.is_tate is True
    assert enc.video_max_duration_s == 95.0


def test_chapter_selection_presets_a_la_carte():
    full = ChapterSelectionSpec.from_preset("full_showcase")
    assert full.include_card and full.include_description and full.include_jacket and full.include_video

    vid_only = ChapterSelectionSpec.from_preset("video_only")
    assert vid_only.include_video is True
    assert not vid_only.include_card and not vid_only.include_jacket and vid_only.snapshot_count == 0

    poster_only = ChapterSelectionSpec.from_preset("poster_only")
    assert poster_only.include_jacket is True
    assert not poster_only.include_card and not poster_only.include_video

    desc_only = ChapterSelectionSpec.from_preset("description_only")
    assert desc_only.include_card is True
    assert desc_only.include_poster_in_card is False
    assert desc_only.include_description is True
    assert desc_only.include_video is False


def test_parse_recalbox_gamelist_xml_and_offline_paths():
    sample_xml = """<?xml version="1.0"?>
    <gameList>
        <provider><System>Super Nintendo</System></provider>
        <game>
            <path>./Super Mario World (USA).zip</path>
            <name>Super Mario World</name>
            <desc>Mario et Yoshi sur Dinosaur Land.</desc>
            <releasedate>19901121T000000</releasedate>
            <rating>0.95</rating>
            <genre>Plateforme</genre>
        </game>
        <game>
            <path>./neogeo.zip</path>
            <name>[BIOS] Neo-Geo</name>
        </game>
        <game>
            <path>./Hidden Proto.sfc</path>
            <name>Hidden Proto</name>
            <hidden>true</hidden>
        </game>
    </gameList>
    """
    entries = parse_recalbox_gamelist_xml(sample_xml, source_relpath="roms/snes/gamelist.xml")
    assert len(entries) == 1
    e = entries[0]
    assert e.system == "snes"
    assert e.rom_stem == "Super Mario World (USA)"
    assert e.title == "Super Mario World"
    assert e.year == "1990"
    assert e.rating == "9.5"
    assert e.genre == "Plateforme"

    sc_dir, gif_name, px_dir = FolderNamingPolicy.resolve_offline_showcase_paths(
        e.system, e.rom_stem, is_tate=False
    )
    assert sc_dir == "showcase/snes"
    assert gif_name == "Super Mario World (USA).gif"
    assert px_dir == "pixelcade/snes"
    assert extract_rom_stem("./cps2/ssf2t.zip") == "ssf2t"


def test_parse_recalbox_gamelist_rejects_xml_entities():
    malicious_xml = """<?xml version="1.0"?>
    <!DOCTYPE lolz [<!ENTITY lol "lol">]>
    <gameList><game><path>./a.zip</path></game></gameList>
    """
    with pytest.raises(ValueError):
        parse_recalbox_gamelist_xml(malicious_xml)


def test_batch_scrape_job_aggregate_backpressure_and_resume_cursor():
    cfg = BatchScrapeConfig(target_count=10, max_workers=2)
    job: BatchScrapeJob[BatchScrapeConfig] = BatchScrapeJob(
        job_id="job_test",
        meta=cfg,
        total_target=4,
        max_workers=2,
        max_unacked_items=2,
        max_unacked_bytes=1024,
        planned_directories=FolderNamingPolicy.plan_initial_directories(cfg),
    )
    job.start(now=100.0)
    assert job.can_dispatch_worker() is True

    d1 = job.record_artifact_ready("snes_platformer", "smw.gif", 400, title="SMW")
    d2 = job.record_artifact_ready("megadrive_fighting", "sf2.gif", 400, title="SF2")
    assert d1.seq == 1 and d2.seq == 2
    # Spool reached max_unacked_items=2 -> backpressure halts new worker dispatch
    assert job.has_spool_capacity() is False
    assert job.can_dispatch_worker() is False

    # Browser Worker resumes with after_seq=1 -> gets seq 2 only
    missed = job.unacked_after(1)
    assert [m.seq for m in missed] == [2]

    # Browser ACKs seq 1 -> frees capacity immediately
    acked = job.ack_artifact(1)
    assert acked is not None and acked.filename == "smw.gif"
    assert job.has_spool_capacity() is True
    assert job.can_dispatch_worker() is True

    snap = job.snapshot(now=110.0)
    assert snap["succeeded_count"] == 2
    assert snap["acked_count"] == 1
    assert snap["unacked_count"] == 1
    assert snap["progress_pct"] == 50.0


def test_bubble_all_systems_sentinel_and_default_systems_filter_criteria():
    default_cfg = BatchScrapeConfig(domain="games", systems=())
    assert default_cfg.to_filter_criteria().systems == ["snes", "megadrive", "neogeo"]

    all_cfg = BatchScrapeConfig(domain="games", systems=("all",), genres=("platform",))
    fc = all_cfg.to_filter_criteria()
    assert fc.systems == []
    assert fc.genres == ["platform"]


def test_batch_webui_bubbles_and_studio_card_rendering_icons_parity():
    from pathlib import Path
    import re

    static_dir = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "infrastructure"
        / "web"
        / "static"
    )
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    # Extract Tab #7 section
    m = re.search(r'<div id="tab-batch"[\s\S]*?</div>\s*</div>\s*<!-- /main -->', html)
    tab_batch_html = m.group(0) if m else html[html.index('id="tab-batch"') :]

    # 1. Zero Ctrl+Click <select multiple> anywhere in Tab #7
    assert not re.search(r"<select\b[^>]*\bmultiple\b", tab_batch_html, re.IGNORECASE)

    # 2. ArcadeMatrix Showcase-style selectable bubble containers exist for all 3 filter dimensions
    for group in ("systems", "genres", "decades"):
        assert f'id="batch-bubbles-{group}"' in tab_batch_html
        assert f"clearBatchBubbles('{group}')" in tab_batch_html
    for searchable in ("systems", "genres"):
        assert f'id="batch-bubble-search-{searchable}"' in tab_batch_html

    # 3. All 8 'A la carte' presets feature Studio .layout-wire visual DMD rendering icons
    preset_btns = re.findall(
        r'<button[^>]*class="[^"]*\bbatch-preset-btn\b[^"]*"[^>]*>[\s\S]*?</button>',
        tab_batch_html,
    )
    assert len(preset_btns) == 8
    for btn in preset_btns:
        assert 'class="layout-wire"' in btn

    # 4. Card Layout selector uses the exact 5 Studio .layout-card-btn visual wireframes (not a text <select>)
    assert '<select id="batch-card-layout"' not in tab_batch_html
    assert 'id="batch-card-layout-box"' in tab_batch_html
    assert 'id="batch-layout-grid"' in tab_batch_html
    for layout_id in (
        "classic_split",
        "poster_right",
        "badge_header",
        "hero_banner",
        "minimal_arcade",
    ):
        assert f'data-layout="{layout_id}"' in tab_batch_html
        assert f"selectBatchCardLayout('{layout_id}')" in tab_batch_html

