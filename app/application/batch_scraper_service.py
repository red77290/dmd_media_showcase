"""
Application Service: `BatchScraperService` (`app/application/batch_scraper_service.py`).

Pure Hexagonal Application orchestrator (depends exclusively on `app/domain/` & `app/domain/ports/`):
- Mode A (`random_filter`): Balanced multi-bucket random scraping (`<system>_<genre>/*.gif`)
  across the 4 domains (`games`, `movies`, `series`, `anime`) up to `5,000+` unique items.
- Mode B (`recalbox_gamelist`): Deterministic enrichment of Recalbox / Batocera `gamelist.xml`
  entries into `showcase[_tate]/<system>/<rom_stem>.gif` (+ optional `pixelcade/<system>/<rom>.png`).
- Real-time event wakeup (`asyncio.Condition`) for zero-timer-throttling background browser streaming,
  hot worker count scaling (`1..8`), pause/resume/cancel, and strict bounded spool backpressure.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import shutil
import uuid
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.application.source_chain_service import DomainSourceChainService, source_chain_service
from app.domain.aggregates.batch_job import BatchArtifactDescriptor, BatchScrapeJob
from app.domain.batch_scraper_specs import (
    BatchScrapeConfig,
    FolderNamingPolicy,
)
from app.domain.entities import FilterCriteria, MediaCandidate
from app.domain.gamelist_catalog import GamelistGameEntry, parse_recalbox_gamelist_xml
from app.domain.ports import (
    ConversionAbortedError,
    IBatchGifComposerPort,
    IEphemeralArtifactQueuePort,
    IMediaTranscoderPort,
    StagedBatchFiles,
    default_port_registry,
)
from app.domain.style_specs import parse_hex_rgb
from app.domain.taxonomy import build_game_fingerprint

logger = logging.getLogger("dmd_showcase.application.batch_scraper")


def _normalize_relpath_key(relpath: str) -> str:
    return (relpath or "").replace("\\", "/").strip("/").lower()


class _ActiveJobContext:
    def __init__(
        self,
        job: BatchScrapeJob[BatchScrapeConfig],
        gamelist_entries: List[GamelistGameEntry],
    ) -> None:
        self.job = job
        self.gamelist_entries = gamelist_entries
        self.cond = asyncio.Condition()
        self.runner_task: Optional[asyncio.Task[None]] = None


class BatchScraperService:
    """Orchestrates concurrent Batch Scraper jobs with bounded ephemeral streaming."""

    def __init__(
        self,
        chain_service: Optional[DomainSourceChainService] = None,
        composer_port: Optional[IBatchGifComposerPort] = None,
        artifact_queue: Optional[IEphemeralArtifactQueuePort] = None,
        transcoder_port: Optional[IMediaTranscoderPort] = None,
    ) -> None:
        self._chain = chain_service
        self._composer = composer_port
        self._queue = artifact_queue
        self._transcoder = transcoder_port
        self._jobs: Dict[str, _ActiveJobContext] = {}

    @property
    def chain_service(self) -> DomainSourceChainService:
        return self._chain or source_chain_service

    @property
    def composer(self) -> IBatchGifComposerPort:
        return self._composer or default_port_registry.resolve("batch_gif_composer")

    @property
    def artifact_queue(self) -> IEphemeralArtifactQueuePort:
        return self._queue or default_port_registry.resolve("batch_artifact_queue")

    @property
    def transcoder(self) -> IMediaTranscoderPort:
        return self._transcoder or default_port_registry.resolve("media_transcoder")

    def parse_gamelists_preview(
        self,
        files_payload: Sequence[Tuple[str, str, str]],
        skip_hidden: bool = True,
        skip_bios: bool = True,
    ) -> Tuple[List[GamelistGameEntry], List[str]]:
        """Parse one or multiple `(filename_or_relpath, system_hint, xml_text)` tuples."""
        merged: List[GamelistGameEntry] = []
        seen_pairs: Set[Tuple[str, str]] = set()
        systems_found: Set[str] = set()
        for relpath, sys_hint, xml_text in files_payload:
            parsed = parse_recalbox_gamelist_xml(
                xml_content=xml_text,
                system_hint=sys_hint,
                source_relpath=relpath,
                skip_hidden=skip_hidden,
                skip_bios=skip_bios,
            )
            for item in parsed:
                key = (item.system.lower(), item.rom_stem.lower())
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                systems_found.add(item.system)
                merged.append(item)
        return merged, sorted(systems_found)

    async def start_job(
        self,
        config: BatchScrapeConfig,
        gamelist_entries: Optional[Sequence[GamelistGameEntry]] = None,
    ) -> Dict[str, object]:
        g_entries = list(gamelist_entries or [])
        norm_enc = config.encoding.normalized()
        max_w = max(1, min(8, int(config.max_workers or 2)))
        norm_cfg = replace(
            config,
            encoding=norm_enc,
            max_workers=max_w,
            target_count=(
                len(g_entries)
                if config.mode == "recalbox_gamelist"
                else max(1, min(10000, int(config.target_count or 100)))
            ),
        )

        g_systems = sorted({e.system for e in g_entries}) if g_entries else None
        planned_dirs = FolderNamingPolicy.plan_initial_directories(norm_cfg, gamelist_systems=g_systems)

        is_zip_mode = norm_cfg.delivery_mode == "zip_stream"
        job_id = f"batch_{uuid.uuid4().hex[:12]}"
        job: BatchScrapeJob[BatchScrapeConfig] = BatchScrapeJob(
            job_id=job_id,
            meta=norm_cfg,
            total_target=norm_cfg.target_count,
            max_workers=max_w,
            max_unacked_items=(
                max(128, min(1500, norm_cfg.target_count))
                if is_zip_mode
                else max(4, min(16, max_w * 3))
            ),
            max_unacked_bytes=(256 * 1024 * 1024) if is_zip_mode else (64 * 1024 * 1024),
            planned_directories=planned_dirs,
        )
        ctx = _ActiveJobContext(job=job, gamelist_entries=g_entries)
        self._jobs[job_id] = ctx
        self._prune_finished_jobs()

        job.start()
        ctx.runner_task = asyncio.create_task(self._run_job_supervisor(ctx))
        return job.snapshot()

    def get_job_snapshot(self, job_id: str) -> Optional[Dict[str, object]]:
        ctx = self._jobs.get(job_id)
        return ctx.job.snapshot() if ctx is not None else None

    def list_active_jobs(self) -> List[Dict[str, object]]:
        self._prune_finished_jobs()
        return [ctx.job.snapshot() for ctx in self._jobs.values()]

    async def pause_job(self, job_id: str) -> Optional[Dict[str, object]]:
        ctx = self._jobs.get(job_id)
        if ctx is None:
            return None
        async with ctx.cond:
            ctx.job.pause()
            ctx.cond.notify_all()
        return ctx.job.snapshot()

    async def resume_job(self, job_id: str) -> Optional[Dict[str, object]]:
        ctx = self._jobs.get(job_id)
        if ctx is None:
            return None
        async with ctx.cond:
            ctx.job.resume()
            ctx.cond.notify_all()
        return ctx.job.snapshot()

    async def update_workers(self, job_id: str, max_workers: int) -> Optional[Dict[str, object]]:
        ctx = self._jobs.get(job_id)
        if ctx is None:
            return None
        async with ctx.cond:
            ctx.job.update_max_workers(max_workers)
            ctx.cond.notify_all()
        return ctx.job.snapshot()

    async def cancel_job(self, job_id: str) -> Optional[Dict[str, object]]:
        ctx = self._jobs.get(job_id)
        if ctx is None:
            return None
        async with ctx.cond:
            ctx.job.cancel()
            ctx.cond.notify_all()
        try:
            self.transcoder.abort_conversions_for_dmd(f"batch_{job_id}_sync")
        except Exception:
            pass
        if ctx.runner_task and not ctx.runner_task.done():
            ctx.runner_task.cancel()
        self.artifact_queue.purge_job(job_id)
        return ctx.job.snapshot()

    def get_staged_artifact(self, job_id: str, seq: int) -> Optional[StagedBatchFiles]:
        return self.artifact_queue.get_staged(job_id, seq)

    async def ack_artifact(self, job_id: str, seq: int) -> Optional[Dict[str, object]]:
        ctx = self._jobs.get(job_id)
        if ctx is None:
            return None
        self.artifact_queue.ack_and_unlink(job_id, seq)
        async with ctx.cond:
            ctx.job.ack_artifact(seq)
            ctx.cond.notify_all()
        return ctx.job.snapshot()

    async def ack_all_up_to(self, job_id: str, max_seq: int) -> Optional[Dict[str, object]]:
        """Atomically unlink and acknowledge all staged artifacts up to `max_seq` (after clean ZIP EOF)."""
        ctx = self._jobs.get(job_id)
        if ctx is None:
            return None
        async with ctx.cond:
            seqs = [r.seq for r in ctx.job.unacked_after(0) if r.seq <= max_seq]
        for s in seqs:
            self.artifact_queue.ack_and_unlink(job_id, s)
        async with ctx.cond:
            for s in seqs:
                ctx.job.ack_artifact(s)
            ctx.cond.notify_all()
        return ctx.job.snapshot()

    async def wait_for_next_events(
        self,
        job_id: str,
        after_seq: int = 0,
        timeout_s: float = 15.0,
    ) -> Tuple[Optional[Dict[str, object]], List[BatchArtifactDescriptor]]:
        """
        Event-driven long-poll / SSE wakeup: blocks with `0` busy-spin until new artifacts
        with `seq > after_seq` are ready or job status transitions.
        """
        ctx = self._jobs.get(job_id)
        if ctx is None:
            return None, []
        async with ctx.cond:
            ready = list(ctx.job.unacked_after(after_seq))
            if not ready and ctx.job.status in ("queued", "running", "paused"):
                try:
                    await asyncio.wait_for(ctx.cond.wait(), timeout=max(0.5, float(timeout_s)))
                except asyncio.TimeoutError:
                    pass
                ready = list(ctx.job.unacked_after(after_seq))
            return ctx.job.snapshot(), ready

    def _prune_finished_jobs(self, max_kept: int = 6, max_idle_s: float = 1800.0) -> None:
        import time as _time

        now_mono = _time.monotonic()
        finished = [
            jid
            for jid, c in self._jobs.items()
            if c.job.status in ("completed", "cancelled", "failed")
        ]
        for jid in list(finished):
            c = self._jobs.get(jid)
            if c and c.job.finished_at and (now_mono - c.job.finished_at) > max_idle_s:
                self.artifact_queue.purge_job(jid)
                self._jobs.pop(jid, None)
                finished.remove(jid)
        while len(self._jobs) > max_kept and finished:
            old_id = finished.pop(0)
            self.artifact_queue.purge_job(old_id)
            self._jobs.pop(old_id, None)

    def _build_random_buckets(self, cfg: BatchScrapeConfig) -> List[FilterCriteria]:
        base = cfg.to_filter_criteria()
        if cfg.domain != "games":
            genres = [g for g in cfg.genres if g and g != "all"]
            if not genres:
                return [base]
            return [replace(base, genres=[g]) for g in genres]

        systems = [s for s in cfg.systems if s and s != "all"] or list(base.systems)
        genres = [g for g in cfg.genres if g and g != "all"]
        buckets: List[FilterCriteria] = []
        if genres and systems:
            for s in systems:
                for g in genres:
                    buckets.append(replace(base, systems=[s], genres=[g]))
        elif genres:
            for g in genres:
                buckets.append(replace(base, systems=[], genres=[g]))
        else:
            for s in systems:
                buckets.append(replace(base, systems=[s]))
        return buckets or [base]

    async def _resolve_gamelist_candidate(
        self,
        entry: GamelistGameEntry,
        cfg: BatchScrapeConfig,
    ) -> MediaCandidate:
        crit = replace(cfg.to_filter_criteria(), domain="games", systems=[entry.system], genres=[])
        cand = await self.chain_service.search_on_demand(
            domain="games",
            query=entry.rom_stem,
            lang=cfg.lang,
            criteria=crit,
        )
        if cand is None and entry.title and entry.title.lower() != entry.rom_stem.lower():
            cand = await self.chain_service.search_on_demand(
                domain="games",
                query=entry.title,
                lang=cfg.lang,
                criteria=crit,
            )

        if cand is not None:
            # Preserve curated localized Recalbox `<desc>` / `<releasedate>` / `<rating>` when present
            if entry.synopsis and len(entry.synopsis.strip()) >= 12:
                cand.synopsis = entry.synopsis.strip()
            if entry.year and not cand.year:
                cand.year = entry.year
            if entry.rating and not cand.rating:
                cand.rating = entry.rating
            if entry.title:
                cand.title = entry.title
            cand.extra_meta = {
                **(cand.extra_meta or {}),
                "system": entry.system,
                "rom_stem": entry.rom_stem,
                "genre": entry.genre or (cand.extra_meta or {}).get("genre") or "",
            }
            return cand

        badge_hex = "#FF6D00"
        return MediaCandidate(
            id=f"gamelist_{entry.system}_{entry.rom_stem}",
            domain="games",
            title=entry.title or entry.rom_stem,
            year=entry.year or "",
            badge=entry.system.upper()[:12],
            badge_color=badge_hex,
            subtitle=entry.genre or entry.publisher or entry.developer,
            rating=entry.rating or "",
            synopsis=entry.synopsis or "",
            lang=cfg.lang or "fr",
            source_id="gamelist_local",
            theme_palette=parse_hex_rgb(badge_hex),
            extra_meta={
                "system": entry.system,
                "rom_stem": entry.rom_stem,
                "genre": entry.genre,
            },
        )

    async def _process_one_item(
        self,
        ctx: _ActiveJobContext,
        cand: MediaCandidate,
        rel_dir: str,
        filename: str,
        companion_relpath: str,
        export_marquee_png: bool,
    ) -> None:
        cfg = ctx.job.meta
        token = uuid.uuid4().hex[:10]
        scratch_dir = self.artifact_queue.create_worker_scratch_dir(ctx.job.job_id, token)
        try:
            composed = await self.composer.compose_candidate_gif(
                candidate=cand,
                chapters=cfg.chapters,
                encoding=cfg.encoding,
                work_dir=scratch_dir,
                job_id=ctx.job.job_id,
                export_marquee_png=export_marquee_png,
            )
            if ctx.job.status == "cancelled":
                return
            if composed is None or not composed.gif_path.exists():
                async with ctx.cond:
                    ctx.job.record_failure(f"No valid GIF produced for '{cand.title}'")
                    ctx.cond.notify_all()
                return

            raw_sys = str((cand.extra_meta or {}).get("system") or cand.badge or "")
            raw_gen = str(
                (cand.extra_meta or {}).get("canonical_genre")
                or (cand.extra_meta or {}).get("genre")
                or cand.subtitle
                or ""
            )
            async with ctx.cond:
                if ctx.job.status == "cancelled":
                    return
                eff_comp_rel = companion_relpath if composed.marquee_png_path else ""
                desc = ctx.job.record_artifact_ready(
                    rel_dir=rel_dir,
                    filename=filename,
                    size_bytes=composed.size_bytes,
                    media_type="image/gif",
                    title=cand.title,
                    system=raw_sys,
                    genre=raw_gen,
                    duration_s=composed.duration_s,
                    companion_relpath=eff_comp_rel,
                    companion_size_bytes=composed.marquee_size_bytes if eff_comp_rel else 0,
                )
                self.artifact_queue.stage_completed_artifact(
                    job_id=ctx.job.job_id,
                    seq=desc.seq,
                    rel_dir=rel_dir,
                    filename=filename,
                    src_gif_path=composed.gif_path,
                    companion_relpath=eff_comp_rel,
                    src_companion_path=composed.marquee_png_path,
                )
                ctx.cond.notify_all()
        except ConversionAbortedError:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[BATCH] Worker error on '%s': %s", cand.title, exc)
            async with ctx.cond:
                ctx.job.record_failure(f"{cand.title}: {exc}")
                ctx.cond.notify_all()
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)
            async with ctx.cond:
                ctx.job.on_worker_exit()
                ctx.cond.notify_all()

    async def _run_job_supervisor(self, ctx: _ActiveJobContext) -> None:
        cfg = ctx.job.meta
        is_tate = cfg.encoding.is_tate
        existing_set = {_normalize_relpath_key(p) for p in (cfg.existing_relpaths or frozenset())}
        in_flight_tasks: Set[asyncio.Task[None]] = set()
        used_relpaths: Set[str] = set(existing_set)

        try:
            if cfg.mode == "recalbox_gamelist":
                queue_entries = list(ctx.gamelist_entries)
                idx = 0
                while idx < len(queue_entries):
                    async with ctx.cond:
                        while (
                            ctx.job.status == "paused"
                            or (
                                ctx.job.status == "running"
                                and (
                                    ctx.job.active_workers >= ctx.job.max_workers
                                    or not ctx.job.has_spool_capacity()
                                )
                            )
                        ):
                            await ctx.cond.wait()
                        if ctx.job.status in ("cancelled", "failed"):
                            break

                        entry = queue_entries[idx]
                        idx += 1
                        sc_dir, gif_name, px_dir = FolderNamingPolicy.resolve_offline_showcase_paths(
                            entry.system, entry.rom_stem, is_tate=is_tate
                        )
                        target_rel = _normalize_relpath_key(f"{sc_dir}/{gif_name}")
                        if target_rel in existing_set:
                            ctx.job.record_skipped_existing(1)
                            ctx.cond.notify_all()
                            continue

                        used_relpaths.add(target_rel)
                        ctx.job.on_worker_enter()
                        ctx.cond.notify_all()

                    async def _gamelist_worker(
                        ge: GamelistGameEntry,
                        r_dir: str,
                        f_name: str,
                        p_dir: str,
                    ) -> None:
                        cand = await self._resolve_gamelist_candidate(ge, cfg)
                        comp_rel = (
                            f"{p_dir}/{FolderNamingPolicy.sanitize_filename_stem(ge.rom_stem, 92)}.png"
                            if cfg.include_marquee_png_companion
                            else ""
                        )
                        await self._process_one_item(
                            ctx=ctx,
                            cand=cand,
                            rel_dir=r_dir,
                            filename=f_name,
                            companion_relpath=comp_rel,
                            export_marquee_png=cfg.include_marquee_png_companion,
                        )

                    t = asyncio.create_task(_gamelist_worker(entry, sc_dir, gif_name, px_dir))
                    in_flight_tasks.add(t)
                    t.add_done_callback(in_flight_tasks.discard)

            else:
                # Random Filter Batch Mode (`<system>_<genre>/*.gif`)
                buckets = self._build_random_buckets(cfg)
                bucket_idx = 0
                batch_seen_ids: Set[str] = set()
                staged_candidates: List[Tuple[MediaCandidate, FilterCriteria]] = []
                empty_rounds = 0

                while True:
                    async with ctx.cond:
                        while (
                            ctx.job.status == "paused"
                            or (
                                ctx.job.status == "running"
                                and (
                                    ctx.job.active_workers >= ctx.job.max_workers
                                    or not ctx.job.has_spool_capacity()
                                )
                            )
                        ):
                            await ctx.cond.wait()
                        if ctx.job.status in ("cancelled", "failed"):
                            break
                        if ctx.job.processed_count + ctx.job.active_workers >= ctx.job.total_target:
                            break

                    if not staged_candidates:
                        b_crit = buckets[bucket_idx % len(buckets)]
                        bucket_idx += 1
                        needed_now = max(
                            1,
                            min(
                                8,
                                ctx.job.total_target - (ctx.job.processed_count + ctx.job.active_workers),
                            ),
                        )
                        fetched = await self.chain_service.sample_batch_candidates(
                            criteria=b_crit,
                            count=needed_now,
                            batch_seen_ids=batch_seen_ids,
                        )
                        if fetched:
                            empty_rounds = 0
                            for fc in fetched:
                                batch_seen_ids.add(fc.id)
                                if cfg.domain == "games":
                                    fp = build_game_fingerprint(
                                        str((fc.extra_meta or {}).get("system") or ""),
                                        fc.title,
                                    )
                                    if fp:
                                        batch_seen_ids.add(fp)
                                staged_candidates.append((fc, b_crit))
                        else:
                            empty_rounds += 1
                            if empty_rounds >= max(3, len(buckets) * 2):
                                logger.info("[BATCH] All filter buckets exhausted for job %s", ctx.job.job_id)
                                async with ctx.cond:
                                    ctx.job.total_target = max(
                                        ctx.job.processed_count + ctx.job.active_workers,
                                        ctx.job.succeeded_count,
                                    )
                                    ctx.cond.notify_all()
                                break
                            continue

                    cand, used_crit = staged_candidates.pop(0)
                    cand_sys = str(
                        (cand.extra_meta or {}).get("system")
                        or (used_crit.systems[0] if used_crit.systems else "")
                    )
                    raw_genres = (cand.extra_meta or {}).get("genres")
                    cand_genre = str(
                        (cand.extra_meta or {}).get("canonical_genre")
                        or (cand.extra_meta or {}).get("genre")
                        or (raw_genres[0] if isinstance(raw_genres, list) and raw_genres else "")
                        or (used_crit.genres[0] if used_crit.genres else "")
                        or cand.subtitle
                        or ""
                    )
                    rel_dir = FolderNamingPolicy.resolve_clock_folder_name(
                        domain=cfg.domain,
                        raw_system=cand_sys,
                        raw_genre=cand_genre,
                        template=cfg.folder_template,
                        safe_ascii=cfg.safe_ascii_folders,
                        is_tate=is_tate,
                        wrap_orientation_root=cfg.wrap_orientation_root,
                    )
                    base_stem = FolderNamingPolicy.sanitize_filename_stem(cand.title, max_len=80)
                    filename = f"{base_stem}.gif"
                    norm_target = _normalize_relpath_key(f"{rel_dir}/{filename}")

                    if norm_target in existing_set:
                        async with ctx.cond:
                            ctx.job.record_skipped_existing(1)
                            ctx.cond.notify_all()
                        continue

                    if norm_target in used_relpaths and cand.year:
                        filename = f"{base_stem} ({cand.year[:4]}).gif"
                        norm_target = _normalize_relpath_key(f"{rel_dir}/{filename}")
                    used_relpaths.add(norm_target)

                    async with ctx.cond:
                        if ctx.job.status in ("cancelled", "failed"):
                            break
                        ctx.job.on_worker_enter()
                        ctx.cond.notify_all()

                    t = asyncio.create_task(
                        self._process_one_item(
                            ctx=ctx,
                            cand=cand,
                            rel_dir=rel_dir,
                            filename=filename,
                            companion_relpath="",
                            export_marquee_png=False,
                        )
                    )
                    in_flight_tasks.add(t)
                    t.add_done_callback(in_flight_tasks.discard)

            if in_flight_tasks:
                await asyncio.gather(*list(in_flight_tasks), return_exceptions=True)

            async with ctx.cond:
                if ctx.job.status not in ("cancelled", "failed"):
                    ctx.job.mark_completed()
                ctx.cond.notify_all()
        except asyncio.CancelledError:
            for t in list(in_flight_tasks):
                t.cancel()
            async with ctx.cond:
                ctx.job.cancel()
                ctx.cond.notify_all()
        except Exception as exc:
            logger.exception("[BATCH] Supervisor crashed for %s: %s", ctx.job.job_id, exc)
            async with ctx.cond:
                ctx.job.mark_failed(str(exc))
                ctx.cond.notify_all()


batch_scraper_service = BatchScraperService()
