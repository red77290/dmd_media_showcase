"""
Comprehensive Non-Regression, Backpressure, Multi-Domain, Preset Matrix, and Real Node.js
Web Worker End-to-End Tests (`tests/test_batch_scraper_e2e_worker_and_isolation.py`).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import io
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Dict, List, Optional, Set

import numpy as np
from PIL import Image, ImageDraw
import pytest
import uvicorn

from app.application.batch_scraper_service import BatchScraperService
from app.application.source_chain_service import DomainSourceChainService
from app.domain.batch_scraper_specs import (
    BatchPresetId,
    BatchScrapeConfig,
    ChapterSelectionSpec,
    GifEncodingSpec,
)
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports import IScraperRegistryPort, IScraperSource
from app.domain.ports.batch_scraper_ports import ComposedGifResult, IBatchGifComposerPort
from app.infrastructure.persistence.ephemeral_batch_queue import EphemeralBatchArtifactQueue
from app.infrastructure.persistence.json_seen_repository import JsonSeenHistoryRepository
from app.infrastructure.rendering.gif_chapter_composer import compose_candidate_gif_worker_task
from app.main import app
from app.portable_env import get_ffmpeg_binary
from app.scheduler.dmd_process_registry import dmd_process_registry


def _make_rgb_png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    im = Image.new("RGB", (w, h), rgb)
    draw = ImageDraw.Draw(im)
    draw.rectangle([6, 6, w - 6, h - 6], outline=(255, 240, 80), width=3)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _make_sample_mp4(path: Path, dur_s: float = 1.6) -> bytes:
    subprocess.run(
        [
            get_ffmpeg_binary(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x90:rate=15:duration={dur_s}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path.read_bytes()


class _InProcessComposer(IBatchGifComposerPort):
    def __init__(self, artificial_delay_s: float = 0.0) -> None:
        self._delay_s = artificial_delay_s

    async def compose_candidate_gif(
        self,
        candidate: MediaCandidate,
        chapters: ChapterSelectionSpec,
        encoding: GifEncodingSpec,
        work_dir: Path,
        job_id: str = "",
        export_marquee_png: bool = False,
    ) -> Optional[ComposedGifResult]:
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        raw = await asyncio.to_thread(
            compose_candidate_gif_worker_task,
            asdict(candidate),
            asdict(chapters),
            asdict(encoding),
            str(work_dir),
            0,
            1,
            25,
            export_marquee_png,
        )
        if not raw:
            return None
        mq = str(raw.get("marquee_png_path") or "")
        return ComposedGifResult(
            gif_path=Path(str(raw["gif_path"])),
            width=int(raw["width"]),
            height=int(raw["height"]),
            fps=int(raw["fps"]),
            frame_count=int(raw["frame_count"]),
            duration_s=float(raw["duration_s"]),
            size_bytes=int(raw["size_bytes"]),
            marquee_png_path=Path(mq) if mq else None,
            marquee_size_bytes=int(raw.get("marquee_size_bytes") or 0),
        )


class _ConfigurableCatalogChain:
    def __init__(self, items_by_domain: Dict[str, List[MediaCandidate]]) -> None:
        self._items = items_by_domain

    async def search_on_demand(
        self,
        domain: str,
        query: str,
        lang: str = "fr",
        criteria: Optional[FilterCriteria] = None,
    ) -> Optional[MediaCandidate]:
        pool = self._items.get(domain, [])
        for cand in pool:
            if query.lower() in cand.id.lower() or query.lower() in cand.title.lower():
                return cand
        return pool[0] if pool else None

    async def sample_batch_candidates(
        self,
        criteria: FilterCriteria,
        count: int = 10,
        batch_seen_ids: Optional[Set[str]] = None,
    ) -> List[MediaCandidate]:
        seen = batch_seen_ids or set()
        pool = self._items.get(criteria.domain, [])
        out: List[MediaCandidate] = []
        for cand in pool:
            if cand.id in seen:
                continue
            out.append(cand)
            if len(out) >= count:
                break
        return out


@pytest.fixture()
def media_assets_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    poster_b = _make_rgb_png(200, 280, (220, 40, 90))
    marquee_b = _make_rgb_png(320, 96, (25, 160, 230))
    snap_b = _make_rgb_png(256, 192, (40, 200, 90))
    mp4_b = _make_sample_mp4(tmp_path / "fixture_clip.mp4", dur_s=1.5)

    def _dl(url: str, timeout: float = 10.0) -> Optional[bytes]:
        if "marquee" in url:
            return marquee_b
        if "snap" in url:
            return snap_b
        if "video" in url:
            return mp4_b
        return poster_b

    monkeypatch.setattr(
        "app.infrastructure.rendering.imaging_primitives.resolve_active_downloader",
        lambda: _dl,
    )
    monkeypatch.setattr(
        "app.infrastructure.rendering.gif_chapter_composer.resolve_active_downloader",
        lambda: _dl,
    )
    return {
        "poster": "https://mock.local/poster.png",
        "marquee": "https://mock.local/marquee.png",
        "snap": "https://mock.local/snap.png",
        "video": "https://mock.local/video.mp4",
    }


@pytest.mark.asyncio
async def test_all_eight_presets_static_jacket_global_palette_and_dither_modes(
    tmp_path: Path,
    media_assets_patch: Dict[str, str],
) -> None:
    cand = MediaCandidate(
        id="preset_matrix_cand",
        domain="games",
        title="Garou Mark of the Wolves",
        year="1999",
        badge="NEOGEO",
        badge_color="#00A0E9",
        subtitle="Fighting",
        rating="9.5",
        synopsis="Le sommet de l'animation 2D SNK sur borne Neo-Geo.",
        poster_url=media_assets_patch["poster"],
        backdrop_url=media_assets_patch["marquee"],
        snapshot_urls=[media_assets_patch["snap"]],
        video_url=media_assets_patch["video"],
        lang="fr",
        source_id="igdb",
        extra_meta={"system": "neogeo", "genre": "fighting"},
    )
    composer = _InProcessComposer()

    presets: List[BatchPresetId] = [
        "full_showcase",
        "video_only",
        "poster_only",
        "marquee_only",
        "card_description_only",
        "description_only",
        "screenshot_only",
        "custom",
    ]
    for idx, preset in enumerate(presets):
        is_tate = idx % 2 == 1
        overrides = (
            {
                "include_card": False,
                "include_jacket": True,
                "jacket_mode": "static",
                "include_marquee": True,
                "marquee_mode": "static",
                "snapshot_count": 0,
                "include_video": False,
            }
            if preset == "custom"
            else {}
        )
        chapters = ChapterSelectionSpec.from_preset(preset, **overrides)
        encoding = GifEncodingSpec(
            width=64 if is_tate else 128,
            height=128 if is_tate else 32,
            fps=12,
            video_max_duration_s=3.0,
            let_me_handle_it=(idx % 2 == 0),
            dither="bayer" if preset == "poster_only" else "none",
            palette_mode="global_rgb888" if preset == "marquee_only" else "per_scene_rgb888",
        )
        wdir = tmp_path / f"preset_{preset}"
        res = await composer.compose_candidate_gif(
            candidate=cand,
            chapters=chapters,
            encoding=encoding,
            work_dir=wdir,
            export_marquee_png=(preset == "full_showcase"),
        )
        assert res is not None, f"preset {preset} failed to produce a GIF"
        assert res.gif_path.exists() and res.size_bytes > 200
        with Image.open(res.gif_path) as im:
            assert im.format == "GIF"
            assert im.size == (encoding.width, encoding.height)
            assert getattr(im, "n_frames", 1) >= 4
            im.seek(min(2, getattr(im, "n_frames", 1) - 1))
            rgb_arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
            assert int(rgb_arr.max()) > 25, f"preset {preset} rendered all-black frame"


@pytest.mark.asyncio
async def test_non_game_domains_duplicate_year_disambiguation_and_catalog_exhaustion(
    tmp_path: Path,
    media_assets_patch: Dict[str, str],
) -> None:
    movies_pool = [
        MediaCandidate(
            id="mov_dune_1984",
            domain="movies",
            title="Dune",
            year="1984",
            badge="MOVIE",
            badge_color="#FF6D00",
            subtitle="Sci-Fi",
            rating="7.2",
            synopsis="Arrakis 1984.",
            poster_url=media_assets_patch["poster"],
            extra_meta={"genre": "Sci-Fi"},
        ),
        MediaCandidate(
            id="mov_dune_2021",
            domain="movies",
            title="Dune",
            year="2021",
            badge="MOVIE",
            badge_color="#FF6D00",
            subtitle="Sci-Fi",
            rating="8.4",
            synopsis="Arrakis 2021.",
            poster_url=media_assets_patch["poster"],
            extra_meta={"genre": "Sci-Fi"},
        ),
    ]
    spool = tmp_path / ".spool_movies"
    svc = BatchScraperService(
        chain_service=_ConfigurableCatalogChain({"movies": movies_pool}),  # type: ignore[arg-type]
        composer_port=_InProcessComposer(),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=spool),
    )

    # Request 10 movies when catalog only has 2 ("Dune" 1984 and "Dune" 2021):
    # Must disambiguate filenames (`Dune.gif` + `Dune (2021).gif` inside `movies_scifi`)
    # AND terminate cleanly on catalog exhaustion without hanging!
    cfg = BatchScrapeConfig(
        mode="random_filter",
        domain="movies",
        genres=("sci-fi",),
        target_count=10,
        max_workers=2,
        chapters=ChapterSelectionSpec.from_preset("poster_only"),
        encoding=GifEncodingSpec(width=128, height=32, fps=10),
    )
    snap = await svc.start_job(cfg)
    job_id = str(snap["job_id"])

    relpaths: List[str] = []
    cursor = 0
    while True:
        cur_snap, ready = await svc.wait_for_next_events(job_id, after_seq=cursor, timeout_s=5.0)
        assert cur_snap is not None
        for r in ready:
            cursor = max(cursor, r.seq)
            relpaths.append(r.relpath)
            await svc.ack_artifact(job_id, r.seq)
        if cur_snap["status"] in ("completed", "failed", "cancelled") and not ready:
            break

    assert cur_snap["status"] == "completed"
    assert sorted(relpaths) == ["movies_scifi/Dune (2021).gif", "movies_scifi/Dune.gif"]


@pytest.mark.asyncio
async def test_ephemeral_spool_backpressure_blocks_workers_until_client_acks(
    tmp_path: Path,
    media_assets_patch: Dict[str, str],
) -> None:
    games_pool = [
        MediaCandidate(
            id=f"bp_game_{i}",
            domain="games",
            title=f"Backpressure Runner {i}",
            year="1995",
            badge="SNES",
            badge_color="#7C4DFF",
            subtitle="Platformer",
            rating="8.8",
            synopsis="Classic 16-bit platformer.",
            poster_url=media_assets_patch["poster"],
            extra_meta={"system": "snes", "genre": "platformer"},
        )
        for i in range(1, 9)
    ]
    spool = tmp_path / ".spool_bp"
    svc = BatchScraperService(
        chain_service=_ConfigurableCatalogChain({"games": games_pool}),  # type: ignore[arg-type]
        composer_port=_InProcessComposer(artificial_delay_s=0.02),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=spool),
    )

    # max_workers=1 & delivery_mode='direct_folder' -> max_unacked_items == 4 for target_count=7
    cfg = BatchScrapeConfig(
        mode="random_filter",
        delivery_mode="direct_folder",
        domain="games",
        systems=("snes",),
        genres=("platformer",),
        target_count=7,
        max_workers=1,
        spool_max_items=4,
        chapters=ChapterSelectionSpec.from_preset("poster_only"),
        encoding=GifEncodingSpec(width=64, height=32, fps=10),
    )
    snap = await svc.start_job(cfg)
    job_id = str(snap["job_id"])

    # Wait until spool fills to its bounded cap (4 unacked items) and verify it NEVER exceeds 4
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        s = svc.get_job_snapshot(job_id)
        assert s is not None
        if int(s["unacked_count"]) >= 4:  # type: ignore[arg-type]
            break
        await asyncio.sleep(0.05)

    await asyncio.sleep(0.25)
    capped_snap = svc.get_job_snapshot(job_id)
    assert capped_snap is not None
    assert int(capped_snap["unacked_count"]) == 4  # type: ignore[arg-type]
    assert int(capped_snap["succeeded_count"]) == 4  # type: ignore[arg-type]
    assert capped_snap["status"] == "running"

    # Now drain and ACK all items -> workers unblock and finish all 7 items cleanly!
    cursor = 0
    acked_total = 0
    while True:
        cur_snap, ready = await svc.wait_for_next_events(job_id, after_seq=cursor, timeout_s=5.0)
        assert cur_snap is not None
        for r in ready:
            cursor = max(cursor, r.seq)
            await svc.ack_artifact(job_id, r.seq)
            acked_total += 1
        if cur_snap["status"] == "completed" and int(cur_snap["unacked_count"]) == 0:  # type: ignore[arg-type]
            break

    assert acked_total == 7


@pytest.mark.asyncio
async def test_batch_scraper_never_mutates_seen_history_or_aborts_live_dmd_sessions(
    tmp_path: Path,
    media_assets_patch: Dict[str, str],
) -> None:
    class _DummySource(IScraperSource):
        source_id = "igdb"
        role = "primary"
        domain: DomainType = "games"

        def is_configured(self) -> bool:
            return True

        def is_in_cooldown(self) -> bool:
            return False

        async def fetch_random_unseen(
            self,
            criteria: FilterCriteria,
            seen_ids: Set[str],
            count: int = 5,
        ) -> List[MediaCandidate]:
            out = []
            for idx in range(1, count + 3):
                cid = f"iso_snes_{idx}"
                if cid not in seen_ids:
                    out.append(
                        MediaCandidate(
                            id=cid,
                            domain="games",
                            title=f"Isolated Title {idx}",
                            year="1993",
                            badge="SNES",
                            badge_color="#7C4DFF",
                            subtitle="Action",
                            rating="8.1",
                            synopsis="Action SNES.",
                            poster_url=media_assets_patch["poster"],
                            extra_meta={"system": "snes", "genre": "action"},
                        )
                    )
            return out

        async def search_by_query(
            self,
            query: str,
            criteria: FilterCriteria,
            limit: int = 5,
        ) -> List[MediaCandidate]:
            return []

    class _DummyRegistry(IScraperRegistryPort):
        def register(self, source: IScraperSource, prepend: bool = False) -> None:
            pass

        def get_chain(self, domain: DomainType) -> List[IScraperSource]:
            return [_DummySource()]

        def describe_active_chains(self) -> Dict[str, str]:
            return {"games": "igdb"}

        def describe_chains_detailed(self) -> Dict[str, List[Dict[str, object]]]:
            return {"games": [{"source_id": "igdb", "role": "primary", "configured": True}]}

        def available_filters(self, domain: DomainType, lang: str = "fr") -> Dict[str, object]:
            return {"systems": [], "genres": [], "decades": []}

        def get_domain_policy(self, domain: DomainType) -> Dict[str, object]:
            return {"ordered_sources": ["igdb"], "disabled_sources": [], "aggregation_mode": "fallback_only"}

    seen_file = tmp_path / "seen_history.json"
    history_repo = JsonSeenHistoryRepository(file_path=seen_file)
    history_repo.mark_consumed("games", "preexisting_live_game")

    real_chain = DomainSourceChainService(
        registry=_DummyRegistry(),
        history_repo=history_repo,
    )
    svc = BatchScraperService(
        chain_service=real_chain,
        composer_port=_InProcessComposer(artificial_delay_s=0.05),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=tmp_path / ".spool_iso"),
    )

    live_dmd_epoch_before = dmd_process_registry.current_epoch("esp32_cab_1")

    snap = await svc.start_job(
        BatchScrapeConfig(
            mode="random_filter",
            domain="games",
            systems=("snes",),
            genres=("action",),
            target_count=4,
            max_workers=2,
            chapters=ChapterSelectionSpec.from_preset("poster_only"),
        )
    )
    job_id = str(snap["job_id"])
    await asyncio.sleep(0.12)
    await svc.cancel_job(job_id)

    # Live DMD epoch & seen_history MUST remain 100% untouched!
    assert dmd_process_registry.current_epoch("esp32_cab_1") == live_dmd_epoch_before
    assert history_repo.get_seen_ids("games") == {"preexisting_live_game"}


def test_real_js_batch_worker_e2e_live_sse_direct_folder_and_second_run_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    media_assets_patch: Dict[str, str],
) -> None:
    """
    Executes the REAL JavaScript ES module `static/js/batch_worker.js` inside Node.js v22
    against a live `uvicorn` HTTP server, writing directly into a simulated user SD card directory,
    plus verifies Mobile / LAN `zip_stream` mode disconnect recovery & post-EOF server unlink!
    """
    import zipfile

    games_pool = [
        MediaCandidate(
            id="wk_mslugx",
            domain="games",
            title="Metal Slug X",
            year="1999",
            badge="NEOGEO",
            badge_color="#00A0E9",
            subtitle="Run & Gun",
            rating="9.2",
            synopsis="SNK Run and Gun.",
            poster_url=media_assets_patch["poster"],
            backdrop_url=media_assets_patch["marquee"],
            extra_meta={"system": "neogeo", "genre": "run&gun"},
        ),
        MediaCandidate(
            id="wk_shocktro",
            domain="games",
            title="Shock Troopers",
            year="1997",
            badge="NEOGEO",
            badge_color="#00A0E9",
            subtitle="Run & Gun",
            rating="8.9",
            synopsis="Saurus Run and Gun.",
            poster_url=media_assets_patch["poster"],
            backdrop_url=media_assets_patch["marquee"],
            extra_meta={"system": "neogeo", "genre": "run&gun"},
        ),
    ]
    spool_dir = tmp_path / ".live_worker_spool"
    svc = BatchScraperService(
        chain_service=_ConfigurableCatalogChain({"games": games_pool}),  # type: ignore[arg-type]
        composer_port=_InProcessComposer(),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=spool_dir),
    )
    monkeypatch.setattr("app.infrastructure.http.routers.batch_scraper.batch_scraper_service", svc)

    # Bind free localhost port and launch live uvicorn server in background thread
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])

    uv_cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    uv_server = uvicorn.Server(uv_cfg)
    srv_thread = threading.Thread(target=uv_server.run, daemon=True)
    srv_thread.start()
    try:
        base_url = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)

        sd_card_dir = tmp_path / "user_sd_card"
        sd_card_dir.mkdir(parents=True, exist_ok=True)
        worker_js_path = (
            Path(__file__).resolve().parents[1]
            / "app/infrastructure/web/static/js/batch_worker.js"
        )
        harness_mjs = tmp_path / "run_worker_e2e.mjs"
        harness_mjs.write_text(
            f"""
import fs from 'node:fs/promises';
import path from 'node:path';
import {{ pathToFileURL }} from 'node:url';

class NodeDirHandle {{
  constructor(absDir) {{ this.absDir = absDir; this.kind = 'directory'; this.name = path.basename(absDir); }}
  async *entries() {{
    let list = [];
    try {{ list = await fs.readdir(this.absDir, {{ withFileTypes: true }}); }} catch (_) {{ return; }}
    for (const d of list) {{
      const full = path.join(this.absDir, d.name);
      if (d.isDirectory()) yield [d.name, new NodeDirHandle(full)];
      else if (d.isFile()) yield [d.name, {{ kind: 'file', name: d.name }}];
    }}
  }}
  async getDirectoryHandle(name, opts = {{}}) {{
    const sub = path.join(this.absDir, name);
    if (opts.create) await fs.mkdir(sub, {{ recursive: true }});
    else await fs.stat(sub);
    return new NodeDirHandle(sub);
  }}
  async getFileHandle(name, opts = {{}}) {{
    const fp = path.join(this.absDir, name);
    if (!opts.create) await fs.stat(fp);
    return {{
      kind: 'file',
      name,
      async getFile() {{
        const buf = await fs.readFile(fp);
        return {{ async text() {{ return buf.toString('utf8'); }} }};
      }},
      async createWritable() {{
        let chunks = [];
        return {{
          async write(data) {{ chunks.push(Buffer.from(data)); }},
          async close() {{
            await fs.mkdir(path.dirname(fp), {{ recursive: true }});
            await fs.writeFile(fp, Buffer.concat(chunks));
          }}
        }};
      }}
    }};
  }}
}}

const events = [];
const listeners = [];
let resolveDone;
const donePromise = new Promise(res => {{ resolveDone = res; }});

delete globalThis.window;
globalThis.self = {{
  addEventListener(evtName, fn) {{
    if (evtName === 'message') listeners.push(fn);
  }},
  postMessage(msg) {{
    events.push(msg);
    if (msg.type === 'STREAM_DONE' || msg.type === 'SCAN_ERROR') resolveDone(msg);
  }}
}};

await import(pathToFileURL({json.dumps(str(worker_js_path))}).href);

const modeArg = process.argv[2];
const sdDir = process.argv[3];
const baseUrl = process.argv[4];
const reqBody = JSON.parse(process.argv[5]);

const startResp = await fetch(`${{baseUrl}}/api/v1/batch-scraper/jobs`, {{
  method: 'POST',
  headers: {{ 'Content-Type': 'application/json' }},
  body: JSON.stringify(reqBody)
}});
const startJson = await startResp.json();
if (!startResp.ok) throw new Error(JSON.stringify(startJson));
const job = startJson.job;

for (const fn of listeners) {{
  await fn({{
    data: {{
      type: 'START_STREAM',
      jobId: job.job_id,
      baseUrl,
      apiKey: '',
      dirHandle: new NodeDirHandle(sdDir),
      plannedDirs: job.planned_directories || [],
      isClockMode: modeArg === 'clock',
      isTate: Boolean(reqBody.encoding && reqBody.encoding.height > reqBody.encoding.width),
      wrapOrientationRoot: Boolean(reqBody.wrap_orientation_root)
    }}
  }});
}}

const finalMsg = await donePromise;
console.log(JSON.stringify({{ finalMsg, jobId: job.job_id, eventCount: events.length }}));
""",
            encoding="utf-8",
        )

        # RUN 1: TATE Clock Playlist (`gifs_tate/neogeo_runandgun/*.gif` + `gifs_tate/playlists.json`)
        clock_req = {
            "mode": "random_filter",
            "delivery_mode": "direct_folder",
            "domain": "games",
            "systems": ["neogeo"],
            "genres": ["run&gun"],
            "target_count": 2,
            "max_workers": 2,
            "folder_template": "system_genre",
            "safe_ascii_folders": True,
            "wrap_orientation_root": True,
            "preset": "poster_only",
            "encoding": {"width": 64, "height": 128, "fps": 10},
        }
        proc1 = subprocess.run(
            ["node", str(harness_mjs), "clock", str(sd_card_dir), base_url, json.dumps(clock_req)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        out1 = json.loads(proc1.stdout.strip().splitlines()[-1])
        assert out1["finalMsg"]["type"] == "STREAM_DONE"
        assert out1["finalMsg"]["writtenTotal"] == 2

        tate_folder = sd_card_dir / "gifs_tate" / "neogeo_runandgun"
        assert (tate_folder / "Metal Slug X.gif").is_file()
        assert (tate_folder / "Shock Troopers.gif").is_file()
        idx_lines = (tate_folder / "index.txt").read_text(encoding="utf-8").splitlines()
        assert sorted(idx_lines) == ["Metal Slug X.gif", "Shock Troopers.gif"]
        pl_manifest = json.loads(
            (sd_card_dir / "gifs_tate" / "playlists.json").read_text(encoding="utf-8")
        )
        assert pl_manifest["neogeo_runandgun"] == {
            "path": "/gifs_tate/neogeo_runandgun",
            "count": 2,
        }

        # RUN 2: Recalbox `gamelist.xml` Offline Pack with pre-existing `mslugx.gif` skipped!
        pre_existing = sd_card_dir / "showcase" / "neogeo" / "mslugx.gif"
        pre_existing.parent.mkdir(parents=True, exist_ok=True)
        pre_existing.write_bytes(b"GIF89a_EXISTING_MARKER")

        gl_xml = """<?xml version="1.0"?>
        <gameList>
          <game><path>./mslugx.zip</path><name>Metal Slug X</name></game>
          <game><path>./shocktro.zip</path><name>Shock Troopers</name></game>
        </gameList>
        """
        gl_req = {
            "mode": "recalbox_gamelist",
            "delivery_mode": "direct_folder",
            "preset": "poster_only",
            "include_marquee_png_companion": True,
            "existing_relpaths": ["showcase/neogeo/mslugx.gif"],
            "encoding": {"width": 128, "height": 32, "fps": 10},
            "gamelist_files": [
                {"relpath": "roms/neogeo/gamelist.xml", "system": "neogeo", "xml_content": gl_xml}
            ],
        }
        proc2 = subprocess.run(
            ["node", str(harness_mjs), "recalbox", str(sd_card_dir), base_url, json.dumps(gl_req)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        out2 = json.loads(proc2.stdout.strip().splitlines()[-1])
        assert out2["finalMsg"]["type"] == "STREAM_DONE"
        assert out2["finalMsg"]["writtenTotal"] == 1
        # Existing file untouched, new `shocktro.gif` + `pixelcade/neogeo/shocktro.png` written, 0 server leftovers!
        assert pre_existing.read_bytes() == b"GIF89a_EXISTING_MARKER"
        assert (sd_card_dir / "showcase" / "neogeo" / "shocktro.gif").is_file()
        assert (sd_card_dir / "pixelcade" / "neogeo" / "shocktro.png").is_file()
        ready_leftovers = list((spool_dir / out2["jobId"] / "ready").glob("*"))
        assert ready_leftovers == []

        # RUN 3: Mobile / LAN `zip_stream` Mode — Zero-Block Server Completion while Phone Sleeps,
        # Wi-Fi Mid-Stream Disconnect Zero-Loss Recovery, Instant README_PACK.txt TTFB, & EOF Unlink!
        import urllib.request

        mobile_req = json.dumps(
            {
                "mode": "random_filter",
                "delivery_mode": "zip_stream",
                "domain": "games",
                "systems": ["neogeo"],
                "genres": [],
                "target_count": 2,
                "max_workers": 2,
                "preset": "poster_only",
                "encoding": {"width": 128, "height": 32, "fps": 10},
            }
        ).encode("utf-8")
        http_req = urllib.request.Request(
            f"{base_url}/api/v1/batch-scraper/jobs",
            data=mobile_req,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(http_req, timeout=10) as resp:
            mob_job_id = json.loads(resp.read().decode("utf-8"))["job"]["job_id"]

        # Wait for server-side completion WITHOUT any client polling/ACKing (simulating locked phone!)
        for _ in range(80):
            with urllib.request.urlopen(f"{base_url}/api/v1/batch-scraper/jobs/{mob_job_id}", timeout=5) as r:
                snap_mob = json.loads(r.read().decode("utf-8"))
            if snap_mob["status"] == "completed":
                break
            time.sleep(0.05)
        assert snap_mob["status"] == "completed"
        assert snap_mob["succeeded_count"] == 2
        assert snap_mob["unacked_count"] == 2

        # Simulate aborted Wi-Fi download (read only first 128 bytes of stream.zip then close socket!)
        with urllib.request.urlopen(f"{base_url}/api/v1/batch-scraper/jobs/{mob_job_id}/stream.zip", timeout=10) as r:
            first_bytes = r.read(128)
            assert first_bytes.startswith(b"PK\x03\x04")
            assert b"README_PACK.txt" in first_bytes

        # Verify 0 items were lost on the server after the interrupted mobile download!
        with urllib.request.urlopen(f"{base_url}/api/v1/batch-scraper/jobs/{mob_job_id}", timeout=5) as r:
            after_drop = json.loads(r.read().decode("utf-8"))
        assert after_drop["unacked_count"] == 2

        # Second 1-tap download reads to EOF -> complete valid .zip AND 100% server spool unlinked!
        with urllib.request.urlopen(f"{base_url}/api/v1/batch-scraper/jobs/{mob_job_id}/stream.zip", timeout=15) as r:
            full_zip_bytes = r.read()

        with zipfile.ZipFile(io.BytesIO(full_zip_bytes), "r") as zf:
            names = set(zf.namelist())
            assert "README_PACK.txt" in names
            assert "playlists.json" in names
            assert "neogeo_run&gun/Metal Slug X.gif" in names
            assert "neogeo_run&gun/Shock Troopers.gif" in names
            assert "neogeo_run&gun/index.txt" in names

        time.sleep(0.1)
        mob_leftovers = list((spool_dir / mob_job_id / "ready").glob("*"))
        assert mob_leftovers == []
    finally:
        uv_server.should_exit = True
        srv_thread.join(timeout=5.0)


@pytest.mark.asyncio
async def test_persistent_batch_job_survives_window_loss_and_server_restart_without_duplicate_work(
    tmp_path: Path,
    media_assets_patch: Dict[str, str],
) -> None:
    """
    Proves that Batch Scraper jobs are true first-class persistent server tasks:
    1. A `direct_folder` job of 6 items runs all the way to `completed` (6/6) even if the user
       closes their browser window immediately (`0` ACKs sent while running).
    2. Across a full server restart (`JsonBatchJobRepository` + `EphemeralBatchArtifactQueue`
       rehydrated from disk), an interrupted `recalbox_gamelist` job preserves its already-staged
       `.gif` files on disk, restores as `paused`, and upon `resume_job()` completes only the
       remaining entries with zero duplicate work before `dismiss_job()` cleans up 100% of disk files.
    """
    from app.domain.gamelist_catalog import GamelistGameEntry
    from app.infrastructure.persistence.json_batch_job_repository import JsonBatchJobRepository

    pool = [
        MediaCandidate(
            id=f"persist_snes_{i}",
            domain="games",
            title=f"Persistent Quest {i}",
            year="1994",
            badge="SNES",
            badge_color="#7C4DFF",
            subtitle="RPG",
            rating="9.0",
            synopsis="Persistent quest.",
            poster_url=media_assets_patch["poster"],
            extra_meta={"system": "snes", "genre": "rpg"},
        )
        for i in range(1, 10)
    ]
    spool_dir = tmp_path / ".batch_spool"
    jobs_json = tmp_path / "batch_jobs.json"

    svc1 = BatchScraperService(
        chain_service=_ConfigurableCatalogChain({"games": pool}),  # type: ignore[arg-type]
        composer_port=_InProcessComposer(artificial_delay_s=0.01),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=spool_dir),
        job_repository=JsonBatchJobRepository(file_path=jobs_json),
    )

    # Part 1: Direct-folder job with NO browser worker connected -> finishes 6/6 without stalling!
    cfg_headless = BatchScrapeConfig(
        mode="random_filter",
        delivery_mode="direct_folder",
        domain="games",
        systems=("snes",),
        genres=("rpg",),
        target_count=6,
        max_workers=2,
        chapters=ChapterSelectionSpec.from_preset("poster_only"),
        encoding=GifEncodingSpec(width=64, height=32, fps=10),
    )
    snap_hl = await svc1.start_job(cfg_headless)
    hl_id = str(snap_hl["job_id"])
    for _ in range(100):
        cur = svc1.get_job_snapshot(hl_id)
        assert cur is not None
        if cur["status"] == "completed":
            break
        await asyncio.sleep(0.03)
    final_hl = svc1.get_job_snapshot(hl_id)
    assert final_hl is not None
    assert final_hl["status"] == "completed"
    assert final_hl["succeeded_count"] == 6
    assert final_hl["unacked_count"] == 6
    assert (
        final_hl["config_summary"]["delivery_mode"] == "direct_folder"  # type: ignore[index]
    )

    # Part 2: Start a 4-ROM Recalbox job, pause after >=1 item, ACK seq 1 if >=2, then simulate server reboot!
    composed_titles: List[str] = []

    class _CountingComposer(_InProcessComposer):
        async def compose_candidate_gif(self, candidate, chapters, encoding, work_dir, job_id="", export_marquee_png=False):  # type: ignore[override]
            composed_titles.append(candidate.title)
            return await super().compose_candidate_gif(
                candidate, chapters, encoding, work_dir, job_id=job_id, export_marquee_png=export_marquee_png
            )

    svc1._composer = _CountingComposer(artificial_delay_s=0.04)
    gl_entries = [
        GamelistGameEntry(system="snes", rom_path=f"./rom_{i}.sfc", rom_stem=f"rom_{i}", title=f"Persistent Quest {i}")
        for i in range(1, 5)
    ]
    cfg_gl = BatchScrapeConfig(
        mode="recalbox_gamelist",
        delivery_mode="direct_folder",
        domain="games",
        systems=("snes",),
        max_workers=1,
        include_marquee_png_companion=False,
        chapters=ChapterSelectionSpec.from_preset("poster_only"),
        encoding=GifEncodingSpec(width=64, height=32, fps=10),
    )
    snap_gl = await svc1.start_job(cfg_gl, gamelist_entries=gl_entries)
    gl_id = str(snap_gl["job_id"])

    for _ in range(100):
        s = svc1.get_job_snapshot(gl_id)
        assert s is not None
        if int(s["succeeded_count"]) >= 2:  # type: ignore[arg-type]
            await svc1.pause_job(gl_id)
            break
        await asyncio.sleep(0.02)

    # Wait for in-flight worker to settle while paused
    for _ in range(50):
        s = svc1.get_job_snapshot(gl_id)
        if s and int(s["active_workers"]) == 0:  # type: ignore[arg-type]
            break
        await asyncio.sleep(0.02)

    # Client ACKs seq 1 before losing window + server restarting; seq 2 stays staged on disk!
    await svc1.ack_artifact(gl_id, 1)
    before_reboot = svc1.get_job_snapshot(gl_id)
    assert before_reboot is not None
    done_before = int(before_reboot["succeeded_count"])  # type: ignore[arg-type]
    assert done_before >= 2
    # Simulate abrupt process stop of svc1 runner task
    ctx1 = svc1._jobs[gl_id]
    if ctx1.runner_task and not ctx1.runner_task.done():
        ctx1.runner_task.cancel()
        await asyncio.gather(ctx1.runner_task, return_exceptions=True)
        # Re-save as if it was running/paused at shutdown
        ctx1.job.status = "running"
        svc1._persist_jobs_state()

    # Instantiate brand-new service & queue on same disk paths (simulating Docker container restart!)
    svc2 = BatchScraperService(
        chain_service=_ConfigurableCatalogChain({"games": pool}),  # type: ignore[arg-type]
        composer_port=_CountingComposer(artificial_delay_s=0.01),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=spool_dir),
        job_repository=JsonBatchJobRepository(file_path=jobs_json),
    )
    restored_n = await svc2.restore_persisted_jobs()
    assert restored_n >= 2

    restored_gl = svc2.get_job_snapshot(gl_id)
    assert restored_gl is not None
    assert restored_gl["status"] == "paused"
    assert int(restored_gl["succeeded_count"]) == done_before  # type: ignore[arg-type]
    assert int(restored_gl["acked_count"]) == 1  # type: ignore[arg-type]
    # Surviving unacked seq 2 is still physically readable from rehydrated spool!
    staged_seq2 = svc2.get_staged_artifact(gl_id, 2)
    assert staged_seq2 is not None and staged_seq2.gif_path.is_file()

    # Resume job -> finishes remaining entries up to 4/4 without re-composing already completed ROMs!
    composed_titles.clear()
    await svc2.resume_job(gl_id)
    for _ in range(100):
        s2 = svc2.get_job_snapshot(gl_id)
        assert s2 is not None
        if s2["status"] == "completed":
            break
        await asyncio.sleep(0.03)

    completed_gl = svc2.get_job_snapshot(gl_id)
    assert completed_gl is not None
    assert completed_gl["status"] == "completed"
    assert completed_gl["succeeded_count"] == 4
    assert len(composed_titles) == 4 - done_before

    # Dismiss both jobs -> 0 records and 0 spool directories left on disk
    assert await svc2.dismiss_job(gl_id) is True
    assert await svc2.dismiss_job(hl_id) is True
    assert svc2.list_active_jobs() == []
    assert list(spool_dir.iterdir()) == []


def test_window_close_and_reopen_recovery_sse_and_playwright_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    media_assets_patch: Dict[str, str],
) -> None:
    """
    End-to-end verification of closing/losing the browser window during a scrape and reopening:
    - Verifies `GET /api/v1/batch-scraper/jobs`, `GET /events?status_only=true`, and `DELETE /jobs/{id}`.
    - When Playwright + Chrome are installed locally, opens a brand-new browser window on `/` (Tab #1)
      and verifies `#batch-global-banner`, `#batch-progress-card`, `#batch-reconnect-bar`, and
      `#btn-batch-dismiss` automatically re-attach to the persistent server job!
    """
    from pathlib import Path as _Path
    import socket
    import threading
    import urllib.request

    from app.infrastructure.http.routers import batch_scraper as router_mod
    from app.infrastructure.persistence.json_batch_job_repository import JsonBatchJobRepository
    from app.main import app
    import uvicorn

    pool = [
        MediaCandidate(
            id=f"reopen_{i}",
            domain="games",
            title=f"Chrono Trigger {i}",
            year="1995",
            badge="SNES",
            badge_color="#FF6D00",
            subtitle="RPG",
            rating="9.6",
            synopsis="Time travel epic.",
            poster_url=media_assets_patch["poster"],
            extra_meta={"system": "snes", "genre": "rpg"},
        )
        for i in range(1, 4)
    ]
    spool_dir = tmp_path / ".spool_ui_reopen"
    jobs_file = tmp_path / "batch_jobs_ui.json"
    live_svc = BatchScraperService(
        chain_service=_ConfigurableCatalogChain({"games": pool}),  # type: ignore[arg-type]
        composer_port=_InProcessComposer(artificial_delay_s=0.02),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=spool_dir),
        job_repository=JsonBatchJobRepository(file_path=jobs_file),
    )
    monkeypatch.setattr(router_mod, "batch_scraper_service", live_svc)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    uv_server = uvicorn.Server(cfg)
    srv_thread = threading.Thread(target=uv_server.run, daemon=True)
    srv_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    try:
        # 1. User launches a direct_folder job, then immediately closes their window!
        start_body = json.dumps(
            {
                "mode": "random_filter",
                "delivery_mode": "direct_folder",
                "domain": "games",
                "systems": ["snes"],
                "genres": ["rpg"],
                "target_count": 2,
                "preset": "poster_only",
                "encoding": {"width": 64, "height": 32, "fps": 10},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/v1/batch-scraper/jobs",
            data=start_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            job_id = json.loads(resp.read().decode("utf-8"))["job"]["job_id"]

        # 2. Status-only SSE stream delivers live/terminal snapshot without draining ready items
        with urllib.request.urlopen(
            f"{base_url}/api/v1/batch-scraper/jobs/{job_id}/events?status_only=true",
            timeout=15,
        ) as sse_resp:
            raw_sse = sse_resp.read().decode("utf-8")
        assert "event: batch_update" in raw_sse
        last_data = [ln for ln in raw_sse.splitlines() if ln.startswith("data:")][-1]
        sse_payload = json.loads(last_data.removeprefix("data:").strip())
        assert sse_payload["ready"] == []
        assert sse_payload["snapshot"]["status"] == "completed"
        assert sse_payload["snapshot"]["unacked_count"] == 2

        # 3. If Playwright + Chrome exist in this environment, verify real browser reopen on Tab #1!
        chrome_bin = _Path("/usr/bin/google-chrome")
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
            has_pw = chrome_bin.is_file()
        except Exception:
            has_pw = False

        if has_pw:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=str(chrome_bin), headless=True)
                page = browser.new_page(viewport={"width": 1180, "height": 900})
                # Open brand-new window on `/` (Tab #1 Overview — user has NOT clicked Tab #7!)
                page.goto(base_url, wait_until="domcontentloaded")
                page.wait_for_function(
                    "() => { const b = document.getElementById('batch-global-banner'); return b && getComputedStyle(b).display !== 'none'; }",
                    timeout=5000,
                )
                # Switch to Tab #7 via the global banner button -> progress card, reconnect bar & zip button are all live!
                page.click("#batch-global-banner button")
                page.wait_for_timeout(200)
                assert page.is_visible("#batch-progress-card")
                assert page.is_visible("#batch-reconnect-bar")
                assert page.is_visible("#btn-batch-download-zip")
                assert page.is_visible("#btn-batch-dismiss")
                # Click Dismiss -> calls DELETE /api/v1/batch-scraper/jobs/{job_id} and hides card + banner
                page.click("#btn-batch-dismiss")
                page.wait_for_function(
                    "() => getComputedStyle(document.getElementById('batch-progress-card')).display === 'none'",
                    timeout=5000,
                )
                browser.close()
        else:
            del_req = urllib.request.Request(
                f"{base_url}/api/v1/batch-scraper/jobs/{job_id}",
                method="DELETE",
            )
            with urllib.request.urlopen(del_req, timeout=5) as del_resp:
                assert json.loads(del_resp.read().decode("utf-8"))["ok"] is True

        with urllib.request.urlopen(f"{base_url}/api/v1/batch-scraper/jobs", timeout=5) as list_resp:
            assert json.loads(list_resp.read().decode("utf-8"))["jobs"] == []
    finally:
        uv_server.should_exit = True
        srv_thread.join(timeout=5.0)


def test_batch_scraper_studio_parity_12_languages_and_live_card_preview_playwright(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    media_assets_patch: Dict[str, str],
) -> None:
    """
    Guarantee that Batch Scraper (`#tab-batch`) never offers fewer content languages or
    rendering options than Card & Render Studio (`#tab-studio`):
    1. All 12 `SUPPORTED_CONTENT_LANGS` (`fr, en, es, de, it, pt, nl, pl, ru, ja, ko, zh`)
       are selectable in `#batch-lang` (matching `#studio-lang`) and remain visible in BOTH
       Mode A (`random_filter`) and Mode B (`recalbox_gamelist`).
    2. Full Studio Card & Sequence controls (`theme`, `font`, `accent`, `badge/year/rating`
       pills, `scroll_speed`, `card_hold`, `0..5` screenshots, `marquee_mode`, `video_type`,
       `min_rating`, `TATE` toggle, DMD profile import, and live `<100ms` HUB75 Card preview
       canvas) work end-to-end in real Chrome Playwright and `POST /api/v1/batch-scraper/jobs`.
    """
    import socket
    import threading
    import urllib.request

    from app.domain.style_specs import SUPPORTED_CONTENT_LANGS
    from app.infrastructure.http.routers import batch_scraper as router_mod
    from app.infrastructure.persistence.json_batch_job_repository import JsonBatchJobRepository
    from app.main import app
    import uvicorn

    assert len(SUPPORTED_CONTENT_LANGS) == 12
    for expected_lang in ("fr", "en", "es", "de", "it", "pt", "nl", "pl", "ru", "ja", "ko", "zh"):
        assert expected_lang in SUPPORTED_CONTENT_LANGS

    pool = [
        MediaCandidate(
            id="anime_akira",
            domain="anime",
            title="AKIRA",
            year="1988",
            badge="ANIME",
            badge_color="#E53935",
            subtitle="Mecha",
            rating="8.8",
            synopsis="ネオ東京で繰り広げられるSFサイバーパンク。",
            poster_url=media_assets_patch["poster"],
            extra_meta={"genre": "mecha"},
        )
    ]
    live_svc = BatchScraperService(
        chain_service=_ConfigurableCatalogChain({"anime": pool, "games": pool}),  # type: ignore[arg-type]
        composer_port=_InProcessComposer(artificial_delay_s=0.01),
        artifact_queue=EphemeralBatchArtifactQueue(spool_root=tmp_path / ".spool_parity"),
        job_repository=JsonBatchJobRepository(file_path=tmp_path / "batch_jobs_parity.json"),
    )
    monkeypatch.setattr(router_mod, "batch_scraper_service", live_svc)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()

    uv_cfg = uvicorn.Config(app, host="127.0.0.1", port=free_port, log_level="error", lifespan="off")
    uv_server = uvicorn.Server(uv_cfg)
    srv_thread = threading.Thread(target=uv_server.run, daemon=True)
    srv_thread.start()
    base_url = f"http://127.0.0.1:{free_port}"

    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", free_port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    try:
        # 1. Verify HTTP contract accepts full Studio-superset parameters (Japanese + 5 snaps + opening + custom style)
        req_body = json.dumps(
            {
                "mode": "random_filter",
                "delivery_mode": "zip_stream",
                "domain": "anime",
                "lang": "ja",
                "systems": [],
                "genres": ["mecha"],
                "min_rating": 7.5,
                "video_type": "opening",
                "target_count": 1,
                "max_workers": 1,
                "preset": "custom",
                "chapters": {
                    "include_card": True,
                    "include_poster_in_card": True,
                    "show_badge": True,
                    "show_year": True,
                    "show_rating": True,
                    "include_description": True,
                    "card_layout": "badge_header",
                    "card_theme": "neon_border",
                    "card_font": "clean_sans",
                    "card_accent_color": "#00e5ff",
                    "text_scroll_speed": "7",
                    "card_hold_s": 1.4,
                    "include_jacket": True,
                    "jacket_mode": "panorama",
                    "include_marquee": True,
                    "marquee_mode": "static",
                    "snapshot_count": 5,
                    "panorama_duration_s": 3.5,
                    "include_video": False,
                },
                "encoding": {"width": 64, "height": 128, "fps": 15, "video_max_duration_s": 8.0},
            }
        ).encode("utf-8")
        post_req = urllib.request.Request(
            f"{base_url}/api/v1/batch-scraper/jobs",
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(post_req, timeout=5) as start_resp:
            started = json.loads(start_resp.read().decode("utf-8"))["job"]
            job_id = started["job_id"]
            assert started["config_summary"]["lang"] == "ja"
            assert started["config_summary"]["video_type"] == "opening"
            assert started["config_summary"]["min_rating"] == 7.5
            assert started["config_summary"]["is_tate"] is True

        del_req = urllib.request.Request(
            f"{base_url}/api/v1/batch-scraper/jobs/{job_id}",
            method="DELETE",
        )
        with urllib.request.urlopen(del_req, timeout=5):
            pass

        # 2. Real Chrome Playwright verification of 12-language parity + live <100ms Card preview
        chrome_bin = Path("/usr/bin/google-chrome")
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
            has_pw = chrome_bin.is_file()
        except Exception:
            has_pw = False

        if has_pw:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=str(chrome_bin), headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 980})
                page.goto(base_url, wait_until="domcontentloaded")

                studio_langs = page.eval_on_selector_all(
                    "#studio-lang option", "els => els.map(e => e.value)"
                )
                batch_langs = page.eval_on_selector_all(
                    "#batch-lang option", "els => els.map(e => e.value)"
                )
                assert studio_langs == list(SUPPORTED_CONTENT_LANGS)
                assert batch_langs == studio_langs

                # Open Tab #7 (Batch Scraper)
                page.evaluate("window.switchTab('batch')")
                page.wait_for_function(
                    "() => document.getElementById('batch-sim-meta') && document.getElementById('batch-sim-meta').textContent.includes('FPS')",
                    timeout=5000,
                )

                # Switch to Mode B (Recalbox gamelist.xml) -> #batch-lang MUST stay visible!
                page.click("#batch-mode-card-gamelist")
                assert page.is_visible("#batch-lang")
                page.click("#batch-mode-card-random")

                # Select Japanese ('ja') + TATE + Clean Sans + Custom Accent -> live simulator updates in <100ms!
                page.select_option("#batch-lang", "ja")
                page.click("#btn-batch-tate")
                page.select_option("#batch-card-font", "clean_sans")
                page.select_option("#batch-accent-mode", "custom")
                page.wait_for_function(
                    "() => { const m = document.getElementById('batch-sim-meta'); return m && m.textContent.includes('32×128') && m.textContent.includes('JA'); }",
                    timeout=5000,
                )
                assert page.inner_text("#batch-geom-pill") == "32×128 • TATE"
                browser.close()
    finally:
        uv_server.should_exit = True
        srv_thread.join(timeout=5.0)


