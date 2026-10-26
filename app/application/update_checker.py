"""
Lightweight, non-intrusive GitHub Release update checker with runtime OS detection.

Checks `https://api.github.com/repos/red77290/dmd_media_showcase/releases/latest`
with in-memory caching, strict URL prefix validation, and graceful offline fallback
so isolated LAN arcade cabinets never experience errors or startup delays.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import app.container  # noqa: F401
from app import __version__
from app.domain.ports.default_ports import default_port_registry

logger = logging.getLogger("dmd_showcase.update_checker")

RuntimeOsType = Literal["macos", "windows", "linux", "docker"]
UpdateStatusType = Literal["update_available", "up_to_date", "no_release_yet", "unreachable"]


@dataclass(frozen=True)
class UpdateCheckResult:
    """Pure Application-layer value object describing release update availability."""

    status: UpdateStatusType
    current_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    runtime_os: RuntimeOsType = "linux"
    runtime_arch: str = "x64"
    os_label: str = "Linux (x64)"
    release_url: str = "https://github.com/red77290/dmd_media_showcase/releases"
    asset_name: Optional[str] = None
    asset_download_url: Optional[str] = None
    published_at: Optional[str] = None
    checked_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

GITHUB_REPO = "red77290/dmd_media_showcase"
GITHUB_RELEASES_HTML_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_LATEST_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_ALLOWED_PREFIX = f"https://github.com/{GITHUB_REPO}/"

_SEMVER_RE = re.compile(
    r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]?(dev|snapshot|alpha|beta|rc)[0-9a-zA-Z.-]*)?$",
    re.IGNORECASE,
)


def detect_runtime_platform(
    *,
    sys_platform: Optional[str] = None,
    machine: Optional[str] = None,
    is_docker: Optional[bool] = None,
) -> Tuple[RuntimeOsType, str, str]:
    """
    Detect the server's active operating system, CPU architecture, and human-readable label.
    Returns `(runtime_os, runtime_arch, os_label)`.
    """
    raw_machine = (machine if machine is not None else (platform.machine() or "x86_64")).lower()
    arch = "arm64" if any(tok in raw_machine for tok in ("arm64", "aarch64", "armv8")) else "x64"

    if is_docker is None:
        env_docker = os.environ.get("DOCKER_CONTAINER", "").strip().lower() in ("1", "true", "yes")
        is_docker = env_docker or Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()

    if is_docker:
        return "docker", arch, f"Docker ({arch})"

    plat = (sys_platform if sys_platform is not None else sys.platform).lower()
    if plat.startswith("darwin"):
        chip_lbl = "Apple Silicon" if arch == "arm64" else "Intel x64"
        return "macos", arch, f"macOS ({chip_lbl})"
    if plat.startswith("win"):
        return "windows", arch, f"Windows ({arch})"
    return "linux", arch, f"Linux ({arch})"


def parse_semver_key(raw_version: str) -> Optional[Tuple[int, int, int, int]]:
    """
    Parse a version string (`1.0.0`, `v1.2.3`, `1.0.0.dev0`, `v1.0.0-SNAPSHOT`) into
    a comparable 4-tuple `(major, minor, patch, is_stable)` where `is_stable` is `1`
    for official stable releases and `0` for dev/snapshot/pre-release builds.
    """
    cleaned = (raw_version or "").strip()
    if not cleaned:
        return None
    match = _SEMVER_RE.match(cleaned)
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or 0)
    is_stable = 0 if match.group(4) else 1
    return (major, minor, patch, is_stable)


def is_newer_release(current_version: str, candidate_version: str) -> bool:
    """
    Return `True` strictly when `candidate_version` is a valid stable SemVer release
    newer than `current_version`. Pre-release / SNAPSHOT tags never trigger notifications.
    """
    cur = parse_semver_key(current_version)
    cand = parse_semver_key(candidate_version)
    if cur is None or cand is None:
        return False
    if cand[3] == 0:
        return False
    return cand > cur


def _sanitize_github_url(raw_url: Any, fallback: str = GITHUB_RELEASES_HTML_URL) -> str:
    url = str(raw_url or "").strip()
    if url.startswith(GITHUB_ALLOWED_PREFIX):
        return url
    return fallback


def select_os_release_asset(
    assets: List[Dict[str, Any]],
    runtime_os: RuntimeOsType,
    runtime_arch: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Match the best binary installer/archive from GitHub Release `assets` for `(runtime_os, runtime_arch)`.
    Returns `(asset_name, browser_download_url)` or `(None, None)` (e.g. for Docker).
    """
    if runtime_os == "docker" or not assets:
        return None, None

    valid_assets: List[Tuple[str, str]] = []
    download_prefix = f"{GITHUB_ALLOWED_PREFIX}releases/download/"
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        dl_url = str(item.get("browser_download_url") or "").strip()
        if name and dl_url.startswith(download_prefix):
            valid_assets.append((name, dl_url))

    if not valid_assets:
        return None, None

    def _matches_arch(low_name: str) -> bool:
        if runtime_arch == "arm64":
            return any(t in low_name for t in ("arm64", "aarch64", "universal"))
        return any(t in low_name for t in ("x64", "x86_64", "amd64", "win64", "universal"))

    os_candidates: List[Tuple[str, str]] = []
    if runtime_os == "macos":
        os_candidates = [
            (n, u)
            for (n, u) in valid_assets
            if n.lower().endswith(".dmg") or "macos" in n.lower() or "darwin" in n.lower()
        ]
        dmg_arch = [
            (n, u) for (n, u) in os_candidates if n.lower().endswith(".dmg") and _matches_arch(n.lower())
        ]
        if dmg_arch:
            return dmg_arch[0]
        dmg_any = [(n, u) for (n, u) in os_candidates if n.lower().endswith(".dmg")]
        if dmg_any:
            return dmg_any[0]
    elif runtime_os == "windows":
        os_candidates = [
            (n, u)
            for (n, u) in valid_assets
            if ("windows" in n.lower() or "win" in n.lower())
            and n.lower().endswith((".zip", ".exe", ".msi"))
        ]
    elif runtime_os == "linux":
        os_candidates = [
            (n, u)
            for (n, u) in valid_assets
            if "linux" in n.lower()
            and n.lower().endswith((".tar.gz", ".tgz", ".appimage", ".deb"))
        ]

    if not os_candidates:
        return None, None

    arch_preferred = [(n, u) for (n, u) in os_candidates if _matches_arch(n.lower())]
    return arch_preferred[0] if arch_preferred else os_candidates[0]


class ReleaseUpdateChecker:
    """Cached asynchronous GitHub Release update checker."""

    def __init__(
        self,
        *,
        current_version: str = __version__,
        ttl_ok_s: float = 3600.0,
        ttl_error_s: float = 300.0,
        client_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.current_version = current_version
        self.ttl_ok_s = ttl_ok_s
        self.ttl_error_s = ttl_error_s
        self._client_factory = client_factory
        self._lock = asyncio.Lock()
        self._cached: Optional[UpdateCheckResult] = None
        self._expires_monotonic: float = 0.0

    def _resolve_client_factory(self) -> Callable[..., Any]:
        if self._client_factory is not None:
            return self._client_factory
        return default_port_registry.resolve("http_client_factory")

    def invalidate_cache(self) -> None:
        """Clear cached GitHub release metadata."""
        self._cached = None
        self._expires_monotonic = 0.0

    async def check_for_update(self, *, force: bool = False) -> UpdateCheckResult:
        """
        Query GitHub `/releases/latest` (or return cached result when fresh) and build
        an OS-aware `UpdateCheckResult`. Never raises on network or API errors.
        """
        now_mono = time.monotonic()
        if not force and self._cached is not None and now_mono < self._expires_monotonic:
            return self._cached

        async with self._lock:
            now_mono = time.monotonic()
            if not force and self._cached is not None and now_mono < self._expires_monotonic:
                return self._cached

            runtime_os, runtime_arch, os_label = detect_runtime_platform()
            checked_ts = time.time()
            factory = self._resolve_client_factory()

            try:
                async with factory(
                    timeout=4.0,
                    headers={"Accept": "application/vnd.github+json"},
                ) as client:
                    resp = await client.get(GITHUB_LATEST_API_URL)

                if resp.status_code == 404:
                    result = UpdateCheckResult(
                        status="no_release_yet",
                        current_version=self.current_version,
                        latest_version=None,
                        update_available=False,
                        runtime_os=runtime_os,
                        runtime_arch=runtime_arch,
                        os_label=os_label,
                        release_url=GITHUB_RELEASES_HTML_URL,
                        checked_at=checked_ts,
                    )
                    self._cached = result
                    self._expires_monotonic = time.monotonic() + self.ttl_ok_s
                    return result

                if resp.status_code != 200:
                    logger.debug("GitHub release check returned HTTP %s", resp.status_code)
                    result = UpdateCheckResult(
                        status="unreachable",
                        current_version=self.current_version,
                        latest_version=None,
                        update_available=False,
                        runtime_os=runtime_os,
                        runtime_arch=runtime_arch,
                        os_label=os_label,
                        release_url=GITHUB_RELEASES_HTML_URL,
                        checked_at=checked_ts,
                    )
                    self._cached = result
                    self._expires_monotonic = time.monotonic() + self.ttl_error_s
                    return result

                payload = resp.json()
                if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
                    result = UpdateCheckResult(
                        status="no_release_yet",
                        current_version=self.current_version,
                        latest_version=None,
                        update_available=False,
                        runtime_os=runtime_os,
                        runtime_arch=runtime_arch,
                        os_label=os_label,
                        release_url=GITHUB_RELEASES_HTML_URL,
                        checked_at=checked_ts,
                    )
                    self._cached = result
                    self._expires_monotonic = time.monotonic() + self.ttl_ok_s
                    return result

                raw_tag = str(payload.get("tag_name") or "").strip()
                clean_ver = raw_tag.lstrip("vV") or None
                release_url = _sanitize_github_url(payload.get("html_url"))
                published_at = str(payload.get("published_at") or "").strip() or None
                assets_raw = payload.get("assets") if isinstance(payload.get("assets"), list) else []
                asset_name, asset_dl_url = select_os_release_asset(assets_raw, runtime_os, runtime_arch)
                has_update = bool(raw_tag and is_newer_release(self.current_version, raw_tag))
                status_val: Literal["update_available", "up_to_date"] = (
                    "update_available" if has_update else "up_to_date"
                )

                result = UpdateCheckResult(
                    status=status_val,
                    current_version=self.current_version,
                    latest_version=clean_ver,
                    update_available=has_update,
                    runtime_os=runtime_os,
                    runtime_arch=runtime_arch,
                    os_label=os_label,
                    release_url=release_url,
                    asset_name=asset_name,
                    asset_download_url=asset_dl_url,
                    published_at=published_at,
                    checked_at=checked_ts,
                )
                self._cached = result
                self._expires_monotonic = time.monotonic() + self.ttl_ok_s
                return result

            except Exception as exc:
                logger.debug("GitHub release check skipped (offline or unreachable): %s", exc)
                result = UpdateCheckResult(
                    status="unreachable",
                    current_version=self.current_version,
                    latest_version=None,
                    update_available=False,
                    runtime_os=runtime_os,
                    runtime_arch=runtime_arch,
                    os_label=os_label,
                    release_url=GITHUB_RELEASES_HTML_URL,
                    checked_at=checked_ts,
                )
                self._cached = result
                self._expires_monotonic = time.monotonic() + self.ttl_error_s
                return result


release_update_checker = ReleaseUpdateChecker()
