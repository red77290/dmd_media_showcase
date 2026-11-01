"""
Isolated Cancellable Worker Process Registry (`app/scheduler/dmd_process_registry.py`).

Tracks active `dmd_gif_converter` / `ffmpeg` / `yt-dlp` conversion process groups per `dmd_id`
(`Dict[str, Set[IsolatedWorkerHandle]]`, supporting 1..N concurrent processes per `dmd_id`):
- Runs cancellable conversions in dedicated OS process groups (`os.setsid()` on POSIX -> `os.killpg`,
  `taskkill /F /T` on Windows) completely separate from `SessionBatchManager`'s shared
  `ProcessPoolExecutor`, guaranteeing zero `BrokenProcessPool` impact on other DMDs.
- Maintains a pre-warmed idle worker (`dmd_gif_converter` + `cv2` + `PIL` already imported)
  and automatically warms a replacement in the background after any `SIGKILL` cancellation,
  preserving 0 ms startup latency on subsequent game launches.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
import logging
import multiprocessing
from multiprocessing.connection import Connection
import os
import signal
import subprocess
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

from app.config import settings
from app.domain.ports.media_transcoder import ConversionAbortedError

logger = logging.getLogger("dmd_showcase.pool.cancellable")


def _isolated_worker_entrypoint(child_conn: Connection) -> None:
    """
    Entrypoint for an isolated warm conversion worker process.
    Places itself in a dedicated OS process group so all child `ffmpeg` / `yt-dlp` subprocesses
    can be terminated atomically when the associated DMD cancels its queue.
    """
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except Exception:
        pass

    try:
        from app.portable_env import enforce_hermetic_environment

        enforce_hermetic_environment()
        import dmd_gif_converter as _dgc  # noqa: F401
        import app.scheduler.asset_builder as _ab  # noqa: F401
    except Exception:
        pass

    try:
        child_conn.send(("ready", os.getpid()))
        while True:
            msg = child_conn.recv()
            if not isinstance(msg, tuple) or not msg:
                break
            op = msg[0]
            if op == "shutdown":
                break
            if op == "call":
                _, fn, args, kwargs = msg
                try:
                    res = fn(*args, **kwargs)
                    child_conn.send(("ok", res))
                except BaseException as exc:
                    try:
                        child_conn.send(("err", type(exc).__name__, str(exc)))
                    except Exception:
                        break
    except (EOFError, OSError, BrokenPipeError):
        pass
    finally:
        try:
            child_conn.close()
        except Exception:
            pass


def _terminate_pid_tree(pid: Optional[int], proc: Any) -> None:
    """Force-terminate an isolated worker process and all of its child subprocesses."""
    if pid and pid > 0:
        if os.name == "nt":
            try:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                    check=False,
                    creationflags=creationflags,
                )
            except Exception:
                pass
        else:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except ProcessLookupError:
                    break
                except Exception:
                    try:
                        os.kill(pid, sig)
                    except Exception:
                        pass
    if proc is not None:
        try:
            if proc.is_alive():
                proc.kill()
        except Exception:
            pass


class IsolatedWorkerHandle:
    """Single warm `spawn` worker process owning a dedicated OS process group."""

    def __init__(self, proc: Any, parent_conn: Connection, pid: int) -> None:
        self.proc = proc
        self.parent_conn = parent_conn
        self.pid = pid
        self.aborted = False
        self._closed = False
        self._io_lock = threading.Lock()

    @classmethod
    def spawn_warm(cls, startup_timeout_s: float = 30.0) -> "IsolatedWorkerHandle":
        mp_ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = mp_ctx.Pipe(duplex=True)
        proc = mp_ctx.Process(
            target=_isolated_worker_entrypoint,
            args=(child_conn,),
            daemon=False,
        )
        proc.start()
        child_conn.close()
        try:
            if not parent_conn.poll(startup_timeout_s):
                raise TimeoutError("Timed out waiting for isolated converter worker startup")
            hello = parent_conn.recv()
            worker_pid = int(hello[1]) if isinstance(hello, tuple) and len(hello) >= 2 else int(proc.pid or 0)
        except BaseException:
            try:
                parent_conn.close()
            except Exception:
                pass
            _terminate_pid_tree(proc.pid, proc)
            try:
                proc.join(timeout=0.5)
            except Exception:
                pass
            raise
        return cls(proc=proc, parent_conn=parent_conn, pid=worker_pid)

    def is_healthy(self) -> bool:
        return (
            not self.aborted
            and not self._closed
            and self.proc is not None
            and bool(self.proc.is_alive())
        )

    def call_blocking(self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: Dict[str, Any]) -> Any:
        if self.aborted or self._closed:
            raise ConversionAbortedError("Worker handle already aborted")
        with self._io_lock:
            if self.aborted or self._closed:
                raise ConversionAbortedError("Worker handle already aborted")
            try:
                self.parent_conn.send(("call", fn, args, kwargs))
                reply = self.parent_conn.recv()
            except (EOFError, OSError, BrokenPipeError, ValueError) as exc:
                if self.aborted:
                    try:
                        self.parent_conn.close()
                    except Exception:
                        pass
                    raise ConversionAbortedError("Converter worker terminated on DMD cancel") from exc
                self.force_kill_async()
                raise RuntimeError(f"Isolated converter worker exited unexpectedly: {exc}") from exc
            finally:
                if self.aborted:
                    try:
                        self.parent_conn.close()
                    except Exception:
                        pass

        if self.aborted:
            raise ConversionAbortedError("Converter worker terminated on DMD cancel")
        if not isinstance(reply, tuple) or not reply:
            raise RuntimeError("Malformed reply from isolated converter worker")
        if reply[0] == "ok":
            return reply[1]
        err_type = str(reply[1]) if len(reply) > 1 else "RuntimeError"
        err_msg = str(reply[2]) if len(reply) > 2 else "Worker task failed"
        raise RuntimeError(f"{err_type}: {err_msg}")

    def force_kill_async(self) -> None:
        """Immediately kill the process group and reap the child process off the event loop."""
        self.aborted = True
        self._closed = True
        _terminate_pid_tree(self.pid, self.proc)
        if self._io_lock.acquire(blocking=False):
            try:
                self.parent_conn.close()
            except Exception:
                pass
            finally:
                self._io_lock.release()

        proc_ref = self.proc
        if proc_ref is not None:
            def _reap() -> None:
                try:
                    proc_ref.join(timeout=2.0)
                except Exception:
                    pass

            threading.Thread(target=_reap, name=f"reap-dmd-wkr-{self.pid}", daemon=True).start()

    def graceful_shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.parent_conn.send(("shutdown",))
        except Exception:
            pass
        try:
            self.parent_conn.close()
        except Exception:
            pass
        if self.proc is not None:
            try:
                self.proc.join(timeout=0.3)
            except Exception:
                pass
            if self.proc.is_alive():
                _terminate_pid_tree(self.pid, self.proc)
                try:
                    self.proc.join(timeout=0.5)
                except Exception:
                    pass


class BoundDmdWorkerSession:
    """Scoped conversion session binding one isolated worker handle to a `dmd_id`."""

    def __init__(
        self,
        registry: "DmdConversionProcessRegistry",
        dmd_id: str,
        epoch: int,
        handle: IsolatedWorkerHandle,
    ) -> None:
        self._registry = registry
        self.dmd_id = dmd_id
        self.epoch = epoch
        self.handle = handle

    async def run_step(
        self,
        loop: asyncio.AbstractEventLoop,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        self._registry.raise_if_aborted(self.dmd_id, self.epoch)
        if self.handle.aborted:
            raise ConversionAbortedError(f"Conversion aborted for '{self.dmd_id}'")
        res = await loop.run_in_executor(None, self.handle.call_blocking, fn, args, kwargs)
        self._registry.raise_if_aborted(self.dmd_id, self.epoch)
        return res


class DmdConversionProcessRegistry:
    """
    Thread-safe registry tracking `1..N` active isolated converter processes per `dmd_id`,
    plus a bounded warm idle worker cache (`max_idle=2`).
    """

    def __init__(self, max_idle: int = 2) -> None:
        self._lock = threading.RLock()
        self._max_idle = max(1, int(max_idle))
        self._active_by_dmd: Dict[str, Set[IsolatedWorkerHandle]] = {}
        self._abort_epochs: Dict[str, int] = {}
        self._idle_workers: List[IsolatedWorkerHandle] = []
        self._prewarming = False
        self._shutting_down = False
        self.force_isolated_in_tests: bool = False

    def current_epoch(self, dmd_id: str) -> int:
        clean = str(dmd_id or "").strip()
        if not clean:
            return 0
        with self._lock:
            return self._abort_epochs.get(clean, 0)

    def raise_if_aborted(self, dmd_id: str, start_epoch: int) -> None:
        clean = str(dmd_id or "").strip()
        if not clean:
            return
        with self._lock:
            cur = self._abort_epochs.get(clean, 0)
        if cur != start_epoch:
            raise ConversionAbortedError(
                f"Conversion for dmd_id='{clean}' aborted (epoch {start_epoch} -> {cur})"
            )

    def active_pids_for_dmd(self, dmd_id: str) -> List[int]:
        clean = str(dmd_id or "").strip()
        with self._lock:
            handles = self._active_by_dmd.get(clean, set())
            return sorted(h.pid for h in handles if h.pid > 0 and h.is_healthy())

    def should_use_isolated_process(self, batch_manager: Any) -> bool:
        if self.force_isolated_in_tests:
            return True
        if not bool(settings.use_process_pool):
            return False
        if batch_manager is None:
            return False
        return isinstance(batch_manager.shared_executor(), ProcessPoolExecutor)

    def _acquire_handle_blocking(self, dmd_id: str, start_epoch: int) -> IsolatedWorkerHandle:
        clean = str(dmd_id or "").strip()
        self.raise_if_aborted(clean, start_epoch)
        candidate: Optional[IsolatedWorkerHandle] = None
        with self._lock:
            self._shutting_down = False
            while self._idle_workers:
                h = self._idle_workers.pop()
                if h.is_healthy():
                    candidate = h
                    break
                h.graceful_shutdown()

        if candidate is None:
            candidate = IsolatedWorkerHandle.spawn_warm()

        with self._lock:
            if self._abort_epochs.get(clean, 0) != start_epoch or self._shutting_down:
                candidate.force_kill_async()
                raise ConversionAbortedError(f"Conversion for dmd_id='{clean}' aborted during worker acquire")
            self._active_by_dmd.setdefault(clean, set()).add(candidate)
        return candidate

    def _release_handle(self, dmd_id: str, handle: IsolatedWorkerHandle) -> None:
        clean = str(dmd_id or "").strip()
        return_to_idle = False
        with self._lock:
            bucket = self._active_by_dmd.get(clean)
            if bucket is not None:
                bucket.discard(handle)
                if not bucket:
                    self._active_by_dmd.pop(clean, None)
            if (
                not self._shutting_down
                and handle.is_healthy()
                and len(self._idle_workers) < self._max_idle
            ):
                self._idle_workers.append(handle)
                return_to_idle = True
        if not return_to_idle and not handle.aborted:
            handle.graceful_shutdown()

    @contextmanager
    def session_blocking_init(self, dmd_id: str, handle: IsolatedWorkerHandle, epoch: int) -> Iterator[BoundDmdWorkerSession]:
        try:
            yield BoundDmdWorkerSession(self, dmd_id, epoch, handle)
        finally:
            self._release_handle(dmd_id, handle)

    async def acquire_handle_async(
        self,
        loop: asyncio.AbstractEventLoop,
        dmd_id: str,
        start_epoch: int,
    ) -> IsolatedWorkerHandle:
        return await loop.run_in_executor(None, self._acquire_handle_blocking, dmd_id, start_epoch)

    def on_pool_started(self) -> None:
        """Reset shutdown latch on worker pool startup and prewarm one worker outside pytest."""
        with self._lock:
            self._shutting_down = False
        if "PYTEST_CURRENT_TEST" not in os.environ:
            self.prewarm_async()

    def prewarm_async(self) -> None:
        """Pre-warm one idle worker in a background thread so the next sync launch pays 0 ms import cost."""
        if not (bool(settings.use_process_pool) or self.force_isolated_in_tests):
            return
        with self._lock:
            if self._shutting_down or self._prewarming or len(self._idle_workers) >= 1:
                return
            self._prewarming = True

        def _warm() -> None:
            handle: Optional[IsolatedWorkerHandle] = None
            try:
                handle = IsolatedWorkerHandle.spawn_warm()
            except Exception as exc:
                logger.debug("[POOL] Background worker prewarm skipped: %s", exc)
            finally:
                keep = False
                with self._lock:
                    self._prewarming = False
                    if (
                        handle is not None
                        and not self._shutting_down
                        and handle.is_healthy()
                        and len(self._idle_workers) < self._max_idle
                    ):
                        self._idle_workers.append(handle)
                        keep = True
                if handle is not None and not keep:
                    handle.graceful_shutdown()

        threading.Thread(target=_warm, name="prewarm-dmd-converter", daemon=True).start()

    def abort_for_dmd(self, dmd_id: str, spawn_replacement: bool = True) -> int:
        """
        Atomically increment the abort epoch for `dmd_id` and terminate ALL active converter
        processes and child subprocesses (`1..N`) registered for `dmd_id`.
        """
        clean = str(dmd_id or "").strip()
        if not clean:
            return 0
        with self._lock:
            self._abort_epochs[clean] = self._abort_epochs.get(clean, 0) + 1
            handles = list(self._active_by_dmd.pop(clean, set()))

        killed = 0
        killed_pids: List[int] = []
        for h in handles:
            killed += 1
            if h.pid > 0:
                killed_pids.append(h.pid)
            h.force_kill_async()

        if killed > 0:
            logger.info(
                "[POOL] Terminated %d active converter process group(s) (pids=%s) for dmd_id='%s'",
                killed,
                killed_pids,
                clean,
            )
            if spawn_replacement and not self._shutting_down:
                self.prewarm_async()
        return killed

    def shutdown_all(self) -> int:
        """Terminate all active and idle isolated converter workers cleanly on server shutdown."""
        with self._lock:
            self._shutting_down = True
            active_handles = [
                h for bucket in self._active_by_dmd.values() for h in list(bucket)
            ]
            self._active_by_dmd.clear()
            idle_handles = list(self._idle_workers)
            self._idle_workers.clear()

        for h in active_handles:
            h.force_kill_async()
        for h in idle_handles:
            h.graceful_shutdown()
        return len(active_handles) + len(idle_handles)


dmd_process_registry = DmdConversionProcessRegistry()
