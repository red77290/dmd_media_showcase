"""
End-to-end integration tests for the Streaming RGB888 Batch Scraper & Offline Pack Builder:
- Recalbox / Batocera `gamelist.xml` parsing & Offline In-Game Showcase pack generation
  (`showcase/<system>/<rom>.gif` + non-interlaced `pixelcade/<system>/<rom>.png`).
- Random Filter Batch mode (`<system>_<genre>/*.gif` flat Clock folders + `index.txt` + `playlists.json`).
- À-la-carte presets (`full_showcase`, `description_only`, `poster_only`), configurable video cap up to `120s`.
- Event-driven SSE wakeup, immediate disk `unlink()` on artifact ACK, `existing_relpaths` resume skip,
  live worker scaling / pause / resume / cancel, and zero-RAM `ZIP_STORED` `stream.zip` fallback.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import subprocess
from typing import List, Optional
import zipfile

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from dataclasses import asdict
from app.application.batch_scraper_service import BatchScraperService
from app.domain.batch_scraper_specs import ChapterSelectionSpec, GifEncodingSpec
from app.domain.entities import FilterCriteria, MediaCandidate
from app.domain.ports.batch_scraper_ports import ComposedGifResult, IBatchGifComposerPort
from app.infrastructure.persistence.ephemeral_batch_queue import EphemeralBatchArtifactQueue
from app.infrastructure.rendering.gif_chapter_composer import (
    GifChapterComposerAdapter,
    compose_candidate_gif_worker_task,
)
from app.main import app
from app.portable_env import get_ffmpeg_binary


def _make_test_png_bytes(width: int = 240, height: int = 320, color: tuple[int, int, int] = (220, 60, 30)) -> bytes:
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([12, 12, width - 12, height - 12], outline=(255, 220, 40), width=4)
    draw.rectangle([width // 4, height // 3, (width * 3) // 4, (height * 2) // 3], fill=(30, 160, 250))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_tiny_mp4(path: Path, duration_s: float = 2.0) -> Path:
    ffmpeg_bin = get_ffmpeg_binary()
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x90:rate=15:duration={duration_s}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


class _FakeChainService:
    def __init__(self, poster_url: str, marquee_url: str, video_url: str) -> None:
        self._poster_url = poster_url
        self._marquee_url = marquee_url
        self._video_url = video_url

    async def search_on_demand(
        self,
        domain: str,
        query: str,
        lang: str = "fr",
        criteria: Optional[FilterCriteria] = None,
    ) -> Optional[MediaCandidate]:
        q_low = (query or "").lower()
        sys_code = (criteria.systems[0] if criteria and criteria.systems else "snes")
        return MediaCandidate(
            id=f"cand_{sys_code}_{q_low}",
            domain="games",
            title=f"Title {query}",
            year="1994",
            badge=sys_code.upper(),
            badge_color="#E60012",
            subtitle="Platformer",
            rating="9.1",
            synopsis="Un classique arcade riche en couleurs RGB888 pour HUB75.",
            poster_url=self._poster_url,
            backdrop_url=self._marquee_url,
            snapshot_urls=[self._poster_url],
            video_url=self._video_url,
            lang=lang,
            source_id="fake_catalog",
            extra_meta={"system": sys_code, "rom_stem": q_low, "genre": "Platformer"},
        )

    async def sample_batch_candidates(
        self,
        criteria: FilterCriteria,
        count: int = 24,
        batch_seen_ids: Optional[set[str]] = None,
    ) -> List[MediaCandidate]:
        excluded = batch_seen_ids or set()
        sys_code = criteria.systems[0] if criteria.systems else "snes"
        gen_code = criteria.genres[0] if criteria.genres else "run&gun"
        out: List[MediaCandidate] = []
        for idx in range(1, count + 4):
            cid = f"rnd_{sys_code}_{gen_code}_{idx}"
            if cid in excluded:
                continue
            out.append(
                MediaCandidate(
                    id=cid,
                    domain=criteria.domain,
                    title=f"{sys_code.upper()} Hero {idx}",
                    year="1996",
                    badge=sys_code.upper(),
                    badge_color="#00A0E9",
                    subtitle=gen_code,
                    rating="8.8",
                    synopsis=f"Action intense {idx} en pur RGB888.",
                    poster_url=self._poster_url,
                    backdrop_url=self._marquee_url,
                    snapshot_urls=[self._poster_url],
                    video_url=self._video_url,
                    lang=criteria.lang,
                    source_id="fake_catalog",
                    extra_meta={"system": sys_code, "genre": gen_code},
                )
            )
            if len(out) >= count:
                break
        return out


@pytest.fixture()
def isolated_batch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    poster_bytes = _make_test_png_bytes(200, 280, (210, 45, 70))
    marquee_bytes = _make_test_png_bytes(320, 100, (20, 120, 220))
    mp4_path = _make_tiny_mp4(tmp_path / "sample.mp4", duration_s=1.8)
    mp4_bytes = mp4_path.read_bytes()

    def _fake_download(url: str, timeout: float = 12.0) -> Optional[bytes]:
        if "poster" in url:
            return poster_bytes
        if "marquee" in url:
            return marquee_bytes
        if "video" in url:
            return mp4_bytes
        return poster_bytes

    monkeypatch.setattr(
        "app.infrastructure.rendering.imaging_primitives.resolve_active_downloader",
        lambda: _fake_download,
    )
    monkeypatch.setattr(
        "app.infrastructure.rendering.gif_chapter_composer.resolve_active_downloader",
        lambda: _fake_download,
    )

    class _InThreadTestComposer(IBatchGifComposerPort):
        async def compose_candidate_gif(
            self,
            candidate: MediaCandidate,
            chapters: ChapterSelectionSpec,
            encoding: GifEncodingSpec,
            work_dir: Path,
            job_id: str = "",
            export_marquee_png: bool = False,
        ) -> Optional[ComposedGifResult]:
            assert isinstance(GifChapterComposerAdapter(), IBatchGifComposerPort)
            raw = await asyncio.to_thread(
                compose_candidate_gif_worker_task,
                asdict(candidate),
                asdict(chapters),
                asdict(encoding),
                str(work_dir),
                0,
                1,
                30,
                export_marquee_png,
            )
            if not raw:
                return None
            mq_str = str(raw.get("marquee_png_path") or "")
            return ComposedGifResult(
                gif_path=Path(str(raw["gif_path"])),
                width=int(raw["width"]),
                height=int(raw["height"]),
                fps=int(raw["fps"]),
                frame_count=int(raw["frame_count"]),
                duration_s=float(raw["duration_s"]),
                size_bytes=int(raw["size_bytes"]),
                marquee_png_path=Path(mq_str) if mq_str else None,
                marquee_size_bytes=int(raw.get("marquee_size_bytes") or 0),
            )

    spool_dir = tmp_path / ".batch_spool"
    queue = EphemeralBatchArtifactQueue(spool_root=spool_dir)
    composer = _InThreadTestComposer()
    fake_chain = _FakeChainService(
        poster_url="https://mock.local/poster.png",
        marquee_url="https://mock.local/marquee.png",
        video_url="https://mock.local/video.mp4",
    )
    svc = BatchScraperService(
        chain_service=fake_chain,  # type: ignore[arg-type]
        composer_port=composer,
        artifact_queue=queue,
    )
    monkeypatch.setattr("app.infrastructure.http.routers.batch_scraper.batch_scraper_service", svc)
    return svc, spool_dir


def test_recalbox_gamelist_parse_and_streaming_rgb888_offline_pack_with_immediate_unlink(
    isolated_batch_env,
) -> None:
    svc, spool_dir = isolated_batch_env
    snes_xml = """<?xml version="1.0"?>
    <gameList>
      <game>
        <path>./smw.sfc</path>
        <name>Super Mario World</name>
        <desc>Explore Dinosaur Land in vivid RGB888 colors!</desc>
        <releasedate>19901121T000000</releasedate>
        <genre>Platformer</genre>
      </game>
      <game>
        <path>./fzero.zip</path>
        <name>F-Zero</name>
        <desc>Futuristic high-speed racing.</desc>
      </game>
      <game>
        <path>./neogeo_bios.zip</path>
        <name>[BIOS] SNES Test</name>
        <hidden>true</hidden>
      </game>
    </gameList>
    """

    with TestClient(app) as client:
        parse_res = client.post(
            "/api/v1/batch-scraper/gamelist/parse",
            json={
                "files": [{"relpath": "roms/snes/gamelist.xml", "system": "", "xml_content": snes_xml}],
                "is_tate": False,
                "include_marquee_png_companion": True,
            },
        )
        assert parse_res.status_code == 200
        parsed_body = parse_res.json()
        assert parsed_body["total_games"] == 2
        assert parsed_body["systems"] == ["snes"]
        assert "showcase/snes" in parsed_body["planned_directories"]
        assert "pixelcade/snes" in parsed_body["planned_directories"]

        # Start job with `smw.gif` already present on the user's SD card (`existing_relpaths`)
        # and `video_max_duration_s` set to the maximum allowed `120.0` seconds.
        start_res = client.post(
            "/api/v1/batch-scraper/jobs",
            json={
                "mode": "recalbox_gamelist",
                "lang": "fr",
                "max_workers": 2,
                "preset": "full_showcase",
                "include_marquee_png_companion": True,
                "existing_relpaths": ["showcase/snes/smw.gif"],
                "encoding": {
                    "width": 128,
                    "height": 32,
                    "fps": 12,
                    "video_max_duration_s": 120.0,
                    "let_me_handle_it": True,
                    "palette_mode": "per_scene_rgb888",
                },
                "gamelist_files": [
                    {"relpath": "roms/snes/gamelist.xml", "system": "snes", "xml_content": snes_xml}
                ],
            },
        )
        assert start_res.status_code == 200
        job_id = start_res.json()["job"]["job_id"]

        # Consume SSE events until the single non-skipped game (`fzero.gif`) is ready
        ready_items = []
        with client.stream("GET", f"/api/v1/batch-scraper/jobs/{job_id}/events?after_seq=0") as sse_resp:
            assert sse_resp.status_code == 200
            for raw_line in sse_resp.iter_lines():
                if not raw_line.startswith("data:"):
                    continue
                payload = json.loads(raw_line[5:].strip())
                if payload["ready"]:
                    ready_items.extend(payload["ready"])
                    break

        assert len(ready_items) == 1
        item = ready_items[0]
        assert item["relpath"] == "showcase/snes/fzero.gif"
        assert item["companion_relpath"] == "pixelcade/snes/fzero.png"

        # Pull the staged RGB888 GIF
        gif_res = client.get(f"/api/v1/batch-scraper/jobs/{job_id}/artifacts/{item['seq']}?kind=gif")
        assert gif_res.status_code == 200
        assert gif_res.headers["x-dmd-relpath"] == "showcase/snes/fzero.gif"
        gif_bytes = gif_res.content
        assert gif_bytes[:6] in (b"GIF89a", b"GIF87a")
        with Image.open(io.BytesIO(gif_bytes)) as gif_im:
            assert gif_im.size == (128, 32)
            assert getattr(gif_im, "n_frames", 1) > 10

        # Pull the companion non-interlaced Marquee PNG
        png_res = client.get(f"/api/v1/batch-scraper/jobs/{job_id}/artifacts/{item['seq']}?kind=companion")
        assert png_res.status_code == 200
        assert png_res.headers["x-dmd-relpath"] == "pixelcade/snes/fzero.png"
        with Image.open(io.BytesIO(png_res.content)) as png_im:
            assert png_im.format == "PNG"
            assert png_im.size[0] <= 512 and png_im.size[1] <= 512
            assert int(png_im.info.get("interlace", 0)) == 0

        # Acknowledge disk write -> server MUST immediately unlink staged files
        ack_res = client.delete(f"/api/v1/batch-scraper/jobs/{job_id}/artifacts/{item['seq']}")
        assert ack_res.status_code == 200
        assert ack_res.json()["unacked_count"] == 0

        # Verify 404 after ACK and 0 leftover files in `.batch_spool/<job_id>/ready`
        gone_res = client.get(f"/api/v1/batch-scraper/jobs/{job_id}/artifacts/{item['seq']}")
        assert gone_res.status_code == 404
        ready_dir = spool_dir / job_id / "ready"
        leftover = list(ready_dir.glob("*")) if ready_dir.exists() else []
        assert leftover == []

        snap_res = client.get(f"/api/v1/batch-scraper/jobs/{job_id}")
        snap = snap_res.json()
        assert snap["skipped_existing_count"] == 1
        assert snap["succeeded_count"] == 1


def test_random_filter_clock_pack_flat_folders_and_streaming_zip_archive(
    isolated_batch_env,
) -> None:
    svc, spool_dir = isolated_batch_env
    with TestClient(app) as client:
        start_res = client.post(
            "/api/v1/batch-scraper/jobs",
            json={
                "mode": "random_filter",
                "domain": "games",
                "lang": "fr",
                "systems": ["neogeo", "snes"],
                "genres": ["run&gun"],
                "target_count": 2,
                "max_workers": 2,
                "folder_template": "system_genre",
                "safe_ascii_folders": False,
                "preset": "description_only",
                "encoding": {
                    "width": 128,
                    "height": 32,
                    "fps": 12,
                    "video_max_duration_s": 6.0,
                },
            },
        )
        assert start_res.status_code == 200
        job_id = start_res.json()["job"]["job_id"]

        # Exercise live worker resize control endpoint
        ctrl_res = client.post(
            f"/api/v1/batch-scraper/jobs/{job_id}/control",
            json={"action": "set_workers", "max_workers": 3},
        )
        assert ctrl_res.status_code == 200
        assert ctrl_res.json()["job"]["max_workers"] == 3

        # Stream universal `ZIP_STORED` pack (`stream.zip`)
        zip_res = client.get(f"/api/v1/batch-scraper/jobs/{job_id}/stream.zip")
        assert zip_res.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zip_res.content), "r") as zf:
            names = zf.namelist()
            gif_entries = [n for n in names if n.endswith(".gif")]
            assert len(gif_entries) == 2
            # Verify flat `<system>_<genre>/<slug>.gif` layout (no nested subfolders)
            for g_name in gif_entries:
                parts = g_name.split("/")
                assert len(parts) == 2
                assert parts[0] in ("neogeo_run&gun", "snes_run&gun")
                raw_gif = zf.read(g_name)
                assert raw_gif[:6] in (b"GIF89a", b"GIF87a")

            # Verify auto-generated `index.txt` inside generated folder(s) and `playlists.json` at root
            assert "playlists.json" in names
            playlists_doc = json.loads(zf.read("playlists.json").decode("utf-8"))
            assert isinstance(playlists_doc, dict) and len(playlists_doc) >= 1
            for folder_key, pl_meta in playlists_doc.items():
                assert folder_key in ("neogeo_run&gun", "snes_run&gun")
                assert pl_meta["path"] == f"/gifs/{folder_key}"
                assert int(pl_meta["count"]) >= 1
                idx_entry = f"{folder_key}/index.txt"
                assert idx_entry in names
                idx_lines = [
                    ln.strip()
                    for ln in zf.read(idx_entry).decode("utf-8").splitlines()
                    if ln.strip()
                ]
                assert len(idx_lines) == int(pl_meta["count"])

        # Confirm zero files remain staged on server after `stream.zip` finishes
        ready_dir = spool_dir / job_id / "ready"
        leftover = list(ready_dir.glob("*")) if ready_dir.exists() else []
        assert leftover == []


@pytest.mark.asyncio
async def test_pause_resume_and_cancel_batch_job_lifecycle(isolated_batch_env) -> None:
    svc, spool_dir = isolated_batch_env
    from app.domain.batch_scraper_specs import BatchScrapeConfig, ChapterSelectionSpec

    cfg = BatchScrapeConfig(
        mode="random_filter",
        domain="games",
        systems=("snes",),
        genres=("platformer",),
        target_count=20,
        max_workers=1,
        chapters=ChapterSelectionSpec.from_preset("poster_only"),
    )
    snap = await svc.start_job(cfg)
    job_id = str(snap["job_id"])

    paused = await svc.pause_job(job_id)
    assert paused is not None and paused["status"] == "paused"

    resumed = await svc.resume_job(job_id)
    assert resumed is not None and resumed["status"] == "running"

    cancelled = await svc.cancel_job(job_id)
    assert cancelled is not None and cancelled["status"] == "cancelled"
    await asyncio.sleep(0.05)
    assert not (spool_dir / job_id).exists()
