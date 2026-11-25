"""
FastAPI Router: Batch Scraper & Offline Pack Streaming (`app/infrastructure/http/routers/batch_scraper.py`).

Exposes event-driven endpoints for:
1. Random Filter Batch Scraping (`<system>_<genre>/*.gif` + `index.txt` + `playlists.json`)
2. Recalbox / Batocera `gamelist.xml` Offline Sync Packs (`showcase[_tate]/<system>/<rom>.gif`)
3. Background-tab-safe `SSE` event stream + binary artifact pull/ACK (`unlink` on disk write)
   + universal `O(1)`-RAM `ZIP_STORED` live stream fallback (`stream.zip`).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Tuple, Union

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.application.batch_scraper_service import batch_scraper_service
from app.config import settings
from app.domain.batch_scraper_specs import (
    BATCH_VIDEO_DEFAULT_DURATION_S,
    BatchPresetId,
    BatchScrapeConfig,
    BatchScrapeMode,
    ChapterSelectionSpec,
    FolderNamingPolicy,
    GifEncodingSpec,
)
from app.domain.entities import DomainType
from app.infrastructure.persistence.zip_stream_writer import StreamingZipArchiveBuilder

router = APIRouter(prefix="/api/v1/batch-scraper", tags=["Batch Scraper & Offline Packs"])


class GamelistXmlFilePayload(BaseModel):
    relpath: str = Field(default="gamelist.xml", max_length=260)
    system: str = Field(default="", max_length=64)
    xml_content: str = Field(min_length=10)


class GamelistParseRequest(BaseModel):
    files: List[GamelistXmlFilePayload] = Field(min_length=1, max_length=128)
    skip_hidden: bool = True
    skip_bios: bool = True
    is_tate: bool = False
    include_marquee_png_companion: bool = True


class ChapterOverridesPayload(BaseModel):
    include_card: Optional[bool] = None
    include_poster_in_card: Optional[bool] = None
    include_description: Optional[bool] = None
    card_layout: Optional[str] = None
    card_theme: Optional[str] = None
    card_font: Optional[str] = None
    card_accent_color: Optional[str] = None
    show_badge: Optional[bool] = None
    show_year: Optional[bool] = None
    show_rating: Optional[bool] = None
    text_scroll_speed: Optional[str] = None
    card_hold_s: Optional[float] = None
    include_jacket: Optional[bool] = None
    jacket_mode: Optional[Literal["panorama", "static"]] = None
    include_marquee: Optional[bool] = None
    marquee_mode: Optional[Literal["panorama", "static"]] = None
    snapshot_count: Optional[int] = Field(default=None, ge=0, le=5)
    panorama_duration_s: Optional[float] = Field(default=None, ge=1.5, le=15.0)
    include_video: Optional[bool] = None


class GifEncodingPayload(BaseModel):
    width: int = Field(default=128, ge=16, le=512)
    height: int = Field(default=32, ge=16, le=512)
    fps: int = Field(default=15, ge=6, le=30)
    video_max_duration_s: float = Field(
        default=BATCH_VIDEO_DEFAULT_DURATION_S,
        ge=2.0,
        le=120.0,
        description="Maximum video clip duration in seconds (configurable from 2s up to 120s max).",
    )
    let_me_handle_it: bool = True
    video_mode: Literal["auto_action", "panorama", "center_crop"] = "auto_action"
    content_mode: Literal["auto", "pixel_art", "anime", "cinema", "custom"] = "auto"
    color_boost: Union[str, bool] = "auto"
    dither: Literal["none", "bayer", "floyd_steinberg"] = "none"
    palette_mode: Literal["per_scene_rgb888", "global_rgb888"] = "per_scene_rgb888"
    reject_threshold: int = Field(default=0, ge=0, le=95)
    brightness: float = -0.02
    contrast: float = 1.45
    saturation: float = 1.85
    gamma: float = 0.90


class StartBatchJobRequest(BaseModel):
    mode: BatchScrapeMode = "random_filter"
    delivery_mode: Literal["direct_folder", "zip_stream"] = "direct_folder"
    domain: DomainType = "games"
    lang: str = Field(default="fr", max_length=8)
    systems: List[str] = Field(default_factory=lambda: ["snes", "megadrive", "neogeo"])
    genres: List[str] = Field(default_factory=list)
    decades: List[str] = Field(default_factory=list)
    min_rating: float = Field(default=0.0, ge=0.0, le=10.0)
    video_type: Literal["any", "opening", "ending", "pv", "trailer"] = "any"
    target_count: int = Field(default=100, ge=1, le=10000)
    max_workers: int = Field(default=0, ge=0, le=8)
    ffmpeg_threads_per_worker: int = Field(default=1, ge=1, le=4)
    folder_template: Literal["system_genre", "system", "genre"] = "system_genre"
    safe_ascii_folders: bool = False
    wrap_orientation_root: bool = False
    include_marquee_png_companion: bool = True
    preset: BatchPresetId = "full_showcase"
    chapters: Optional[ChapterOverridesPayload] = None
    encoding: GifEncodingPayload = Field(default_factory=GifEncodingPayload)
    existing_relpaths: List[str] = Field(default_factory=list, max_length=50000)
    gamelist_files: List[GamelistXmlFilePayload] = Field(default_factory=list, max_length=128)
    skip_hidden: bool = True
    skip_bios: bool = True


class BatchJobControlRequest(BaseModel):
    action: Literal["pause", "resume", "cancel", "set_workers"]
    max_workers: Optional[int] = Field(default=None, ge=1, le=8)


@router.post("/gamelist/parse", summary="Parse Recalbox or Batocera gamelist.xml files")
async def parse_gamelist_preview(req: GamelistParseRequest) -> Dict[str, Any]:
    tuples: List[Tuple[str, str, str]] = [
        (f.relpath, f.system, f.xml_content) for f in req.files
    ]
    try:
        entries, systems = batch_scraper_service.parse_gamelists_preview(
            tuples,
            skip_hidden=req.skip_hidden,
            skip_bios=req.skip_bios,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dummy_cfg = BatchScrapeConfig(
        mode="recalbox_gamelist",
        systems=tuple(systems),
        include_marquee_png_companion=req.include_marquee_png_companion,
        encoding=GifEncodingSpec(width=64 if req.is_tate else 128, height=128 if req.is_tate else 32),
    )
    planned_dirs = FolderNamingPolicy.plan_initial_directories(dummy_cfg, gamelist_systems=systems)
    return {
        "total_games": len(entries),
        "systems": systems,
        "planned_directories": planned_dirs,
        "entries_preview": [
            {
                "system": e.system,
                "rom_stem": e.rom_stem,
                "title": e.title,
                "year": e.year,
                "genre": e.genre,
                "has_synopsis": bool(e.synopsis),
            }
            for e in entries[:40]
        ],
    }


@router.post("/jobs", summary="Start a streaming Batch Scraper job")
async def create_batch_scraper_job(req: StartBatchJobRequest) -> Dict[str, Any]:
    ch_overrides = req.chapters.model_dump(exclude_none=True) if req.chapters else {}
    chapters_spec = ChapterSelectionSpec.from_preset(req.preset, **ch_overrides)
    enc_spec = GifEncodingSpec(**req.encoding.model_dump()).normalized()
    eff_workers = (
        req.max_workers
        if req.max_workers > 0
        else max(1, min(8, settings.effective_max_workers))
    )

    gamelist_entries = []
    if req.mode == "recalbox_gamelist":
        if not req.gamelist_files:
            raise HTTPException(
                status_code=400,
                detail="At least one gamelist.xml file is required in recalbox_gamelist mode.",
            )
        tuples = [(f.relpath, f.system, f.xml_content) for f in req.gamelist_files]
        try:
            gamelist_entries, _ = batch_scraper_service.parse_gamelists_preview(
                tuples,
                skip_hidden=req.skip_hidden,
                skip_bios=req.skip_bios,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not gamelist_entries:
            raise HTTPException(status_code=400, detail="No valid game entries found in gamelist.xml.")

    cfg = BatchScrapeConfig(
        mode=req.mode,
        delivery_mode=req.delivery_mode,
        domain=req.domain,
        lang=req.lang,
        systems=tuple(req.systems),
        genres=tuple(req.genres),
        decades=tuple(req.decades),
        min_rating=req.min_rating,
        video_type=req.video_type,
        target_count=req.target_count,
        max_workers=eff_workers,
        ffmpeg_threads_per_worker=req.ffmpeg_threads_per_worker,
        folder_template=req.folder_template,
        safe_ascii_folders=req.safe_ascii_folders,
        wrap_orientation_root=req.wrap_orientation_root,
        include_marquee_png_companion=req.include_marquee_png_companion,
        chapters=chapters_spec,
        encoding=enc_spec,
        existing_relpaths=frozenset(req.existing_relpaths),
    )
    snap = await batch_scraper_service.start_job(cfg, gamelist_entries=gamelist_entries)
    return {"ok": True, "job": snap}


@router.get("/jobs", summary="List active and recent Batch Scraper jobs")
async def list_batch_scraper_jobs() -> Dict[str, Any]:
    return {
        "default_workers": settings.effective_max_workers,
        "detected_cpu_cores": settings.detected_cpu_cores,
        "video_max_duration_limit_s": settings.batch_video_max_duration_limit_s,
        "jobs": batch_scraper_service.list_active_jobs(),
    }


@router.get("/jobs/{job_id}", summary="Get live status of a Batch Scraper job")
async def get_batch_scraper_job(job_id: str) -> Dict[str, Any]:
    snap = batch_scraper_service.get_job_snapshot(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return snap


@router.post("/jobs/{job_id}/control", summary="Pause, resume, cancel, or resize workers on a Batch job")
async def control_batch_scraper_job(job_id: str, req: BatchJobControlRequest) -> Dict[str, Any]:
    if req.action == "pause":
        snap = await batch_scraper_service.pause_job(job_id)
    elif req.action == "resume":
        snap = await batch_scraper_service.resume_job(job_id)
    elif req.action == "cancel":
        snap = await batch_scraper_service.cancel_job(job_id)
    else:
        snap = await batch_scraper_service.update_workers(job_id, req.max_workers or 2)
    if snap is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return {"ok": True, "job": snap}


@router.delete("/jobs/{job_id}", summary="Dismiss and purge a Batch Scraper job and its staged files")
async def dismiss_batch_scraper_job(job_id: str) -> Dict[str, Any]:
    removed = await batch_scraper_service.dismiss_job(job_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return {"ok": True, "dismissed": job_id}


@router.get("/jobs/{job_id}/events", summary="Event-driven SSE stream for background Web Worker or UI recovery")
async def stream_batch_job_events(
    job_id: str,
    after_seq: int = Query(default=0, ge=0),
    status_only: bool = Query(default=False),
) -> StreamingResponse:
    if batch_scraper_service.get_job_snapshot(job_id) is None:
        raise HTTPException(status_code=404, detail="Batch job not found")

    async def _sse_generator() -> AsyncIterator[str]:
        cursor = int(after_seq)
        while True:
            snap, ready = await batch_scraper_service.wait_for_next_events(
                job_id=job_id,
                after_seq=cursor,
                timeout_s=12.0,
            )
            if snap is None:
                break
            if ready and not status_only:
                cursor = max(cursor, max(r.seq for r in ready))
            elif ready and status_only:
                cursor = max(cursor, max(r.seq for r in ready))
            payload = {
                "snapshot": snap,
                "ready": [] if status_only else [r.to_dict() for r in ready],
            }
            yield f"event: batch_update\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if status_only:
                if snap["status"] in ("completed", "cancelled", "failed"):
                    break
            elif (
                snap["status"] in ("completed", "cancelled", "failed")
                and int(snap["unacked_count"]) == 0  # type: ignore[arg-type]
                and not ready
            ):
                break
            if not ready:
                await asyncio.sleep(0.25)

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs/{job_id}/artifacts/{seq}", summary="Download a staged RGB888 GIF or companion marquee PNG")
async def download_staged_batch_artifact(
    job_id: str,
    seq: int,
    background_tasks: BackgroundTasks,
    kind: Literal["gif", "companion"] = Query(default="gif"),
    ack: bool = Query(default=False),
) -> FileResponse:
    staged = batch_scraper_service.get_staged_artifact(job_id, seq)
    if staged is None:
        raise HTTPException(status_code=404, detail="Staged artifact not found or already acknowledged")

    if kind == "companion":
        if staged.companion_path is None or not staged.companion_path.exists():
            raise HTTPException(status_code=404, detail="No companion marquee PNG for this item")
        target_path = staged.companion_path
        media_type = "image/png"
        rel_hdr = staged.companion_relpath
    else:
        target_path = staged.gif_path
        media_type = "image/gif"
        rel_hdr = f"{staged.rel_dir}/{staged.filename}".strip("/")

    if ack:
        background_tasks.add_task(batch_scraper_service.ack_artifact, job_id, seq)

    return FileResponse(
        path=str(target_path),
        media_type=media_type,
        headers={"X-DMD-Relpath": rel_hdr},
    )


@router.delete("/jobs/{job_id}/artifacts/{seq}", summary="Acknowledge disk write and immediately unlink server file")
async def ack_staged_batch_artifact(job_id: str, seq: int) -> Dict[str, Any]:
    snap = await batch_scraper_service.ack_artifact(job_id, seq)
    if snap is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    return {"ok": True, "acked_seq": seq, "unacked_count": snap["unacked_count"]}


_ZIP_README_TEXT = (
    "=== DMD Media Showcase — Offline RGB888 Pack ===\n"
    "FR : Décompressez ce fichier .zip à la racine de votre carte microSD ArcadeMatrix (ESP32/S3) ou dossier médias ArcadeMatrix_RPi :\n"
    "  - Mode Horloge / Playlists : copiez les dossiers (<systeme>_<genre> + playlists.json) dans /gifs/ (YOKO) ou /gifs_tate/ (TATE).\n"
    "  - Mode Recalbox / Batocera : copiez showcase/ (ou showcase_tate/) et pixelcade/ directement à la racine.\n"
    "EN : Extract this .zip to the root of your ArcadeMatrix SD card (/gifs or /gifs_tate for Clock playlists; /showcase & /pixelcade for Recalbox sync).\n"
    "ES : Extraiga este .zip en la raíz de su tarjeta SD ArcadeMatrix (/gifs o /gifs_tate para reloj; /showcase y /pixelcade para Recalbox).\n"
)


@router.get("/jobs/{job_id}/stream.zip", summary="Stream live ZIP_STORED pack with mobile disconnect resilience")
async def stream_batch_job_zip(job_id: str) -> StreamingResponse:
    init_snap = batch_scraper_service.get_job_snapshot(job_id)
    if init_snap is None:
        raise HTTPException(status_code=404, detail="Batch job not found")

    async def _zip_byte_stream() -> AsyncIterator[bytes]:
        builder = StreamingZipArchiveBuilder()
        # Emit README_PACK.txt immediately at t=0ms so mobile browsers (iOS Safari / Android Chrome)
        # receive PKZIP headers in < 2ms (zero initial TTFB stall while waiting for FFmpeg #1!)
        yield builder.emit_bytes_entry("README_PACK.txt", _ZIP_README_TEXT.encode("utf-8"))
        for p_dir in init_snap.get("planned_directories") or []:  # type: ignore[union-attr]
            hdr = builder.emit_directory(str(p_dir))
            if hdr:
                yield hdr

        cursor = 0
        emitted_seqs: List[int] = []
        is_clock_mode = True
        is_tate = False
        while True:
            snap, ready = await batch_scraper_service.wait_for_next_events(
                job_id=job_id,
                after_seq=cursor,
                timeout_s=10.0,
            )
            if snap is None:
                break
            for desc in ready:
                cursor = max(cursor, desc.seq)
                staged = batch_scraper_service.get_staged_artifact(job_id, desc.seq)
                if staged is None:
                    continue
                if desc.rel_dir.startswith(("showcase/", "showcase_tate/")):
                    is_clock_mode = False
                if desc.rel_dir.startswith(("gifs_tate/", "showcase_tate/")):
                    is_tate = True
                if staged.gif_path.exists():
                    for chunk in builder.iter_stored_file_chunks(
                        desc.relpath,
                        staged.gif_path,
                        record_for_clock_index=is_clock_mode,
                    ):
                        yield chunk
                if staged.companion_relpath and staged.companion_path and staged.companion_path.exists():
                    for chunk in builder.iter_stored_file_chunks(
                        staged.companion_relpath,
                        staged.companion_path,
                        record_for_clock_index=False,
                    ):
                        yield chunk
                emitted_seqs.append(desc.seq)
                # Sliding window unlink ONLY if spool exceeds 192 MB on ultra-large jobs;
                # otherwise keep staged until Central Directory EOF so mobile Wi-Fi drops lose 0 files!
                cur_snap = batch_scraper_service.get_job_snapshot(job_id)
                if cur_snap and int(cur_snap.get("unacked_bytes") or 0) >= 192 * 1024 * 1024 and emitted_seqs:
                    await batch_scraper_service.ack_artifact(job_id, emitted_seqs.pop(0))

            if snap["status"] in ("completed", "cancelled", "failed") and not ready:
                break

        if is_clock_mode:
            manifests = builder.emit_clock_manifests_if_any(is_tate=is_tate)
            if manifests:
                yield manifests
        yield builder.finalize_central_directory()
        # Clean EOF reached: atomically unlink all streamed files so 0 bytes remain on server!
        if cursor > 0:
            await batch_scraper_service.ack_all_up_to(job_id, cursor)

    filename = f"dmd_pack_{job_id}.zip"
    return StreamingResponse(
        _zip_byte_stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
