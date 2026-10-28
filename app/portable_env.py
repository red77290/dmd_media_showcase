"""
Hermetic portable environment isolation for Windows PC, macOS, and Docker deployments.

When running as a portable bundle (PyInstaller frozen executable or `DMD_PORTABLE_MODE=1`):
- Overrides `os.environ["PATH"]` to point exclusively to bundled `_internal/bin` (`ffmpeg`, `ffprobe`)
  so any host-installed Python, FFmpeg, or OpenCV libraries are 100% ignored.
- Purges `PYTHONPATH` and `PYTHONHOME`.
- Forces all persistent and temporary runtime files into `./data/` next to the executable.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


def is_portable_bundle() -> bool:
    """Return True when running inside a frozen PyInstaller bundle or explicit portable mode."""
    return bool(getattr(sys, "frozen", False)) or os.environ.get("DMD_PORTABLE_MODE") == "1"


def get_bundle_root() -> Path:
    """Return the root directory of the portable executable or repository."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_macos_app_bundle() -> bool:
    """Return True when running inside a macOS `.app` bundle (`.../Contents/MacOS`)."""
    if sys.platform != "darwin":
        return False
    return ".app/Contents/" in f"{get_bundle_root().as_posix()}/"


def get_internal_bin_dir() -> Path:
    """Return the bundled `bin` directory containing static `ffmpeg` and `ffprobe`."""
    root = get_bundle_root()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "bin"
        if candidate.exists():
            return candidate
    for candidate in (
        root / "_internal" / "bin",
        root.parent / "Frameworks" / "bin",
        root.parent / "Resources" / "bin",
    ):
        if candidate.exists():
            return candidate
    return root / "bin"


def get_resource_path(rel_path: str) -> Path:
    """
    Resolve a bundled read-only resource path (`docs/asyncapi.yaml`, `app/infrastructure/web/static`, etc.)
    across normal source checkouts, Docker containers, macOS `.app` bundles, and PyInstaller builds.
    """
    clean_rel = Path(rel_path)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / clean_rel
        if cand.exists():
            return cand
    root = get_bundle_root()
    for base in (
        root,
        root / "_internal",
        root.parent / "Frameworks",
        root.parent / "Resources",
        Path(__file__).resolve().parent.parent,
        Path.cwd(),
    ):
        cand = base / clean_rel
        if cand.exists():
            return cand
    return Path(__file__).resolve().parent.parent / clean_rel


def get_data_dir() -> Path:
    """Return the persistent `data` directory used for `server_config.json` and `seen_history.json`."""
    env_data = os.environ.get("DMD_DATA_DIR")
    if env_data:
        path = Path(env_data).expanduser().resolve()
    elif is_macos_app_bundle():
        # Never write inside `.app/Contents/...` (breaks macOS codesign seal and fails on read-only .dmg)
        path = (Path.home() / "Library" / "Application Support" / "DMDMediaShowcase" / "data").resolve()
    else:
        path = get_bundle_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def enforce_hermetic_environment() -> Path:
    """
    Lock down process environment variables in portable mode so nothing installed on the
    host PC/Mac can interfere with bundled Python, FFmpeg, or OpenCV runtimes.
    """
    data_dir = get_data_dir()
    (data_dir / "stream_tmp").mkdir(parents=True, exist_ok=True)

    if is_portable_bundle():
        bin_dir = get_internal_bin_dir()
        if not bin_dir.exists():
            try:
                bin_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        # Strictly isolate PATH to the bundled bin directory + minimal OS kernel dirs
        if sys.platform == "win32":
            sys_root = os.environ.get("SystemRoot", r"C:\Windows")
            os.environ["PATH"] = f"{bin_dir};{sys_root}\\System32"
        else:
            os.environ["PATH"] = f"{bin_dir}:/usr/bin:/bin"

        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("PYTHONHOME", None)
        os.environ["PYTHONNOUSERSITE"] = "1"
        os.environ.setdefault("CACHE_DIR", str(data_dir / "stream_tmp"))
        try:
            import certifi

            ca_bundle = certifi.where()
            if ca_bundle and Path(ca_bundle).is_file():
                os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
                os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
        except Exception:
            pass

    return data_dir


def subprocess_windowless_kwargs() -> Dict[str, int]:
    """Return `creationflags=CREATE_NO_WINDOW` on Windows so helper CLI processes never flash consoles."""
    if sys.platform == "win32":
        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": int(flag)}
    return {}


def resolve_ytdlp_command() -> Optional[List[str]]:
    """
    Resolve the `yt-dlp` command prefix across Docker, virtualenvs, and hermetic
    PyInstaller bundles (`Windows_x64`, `macOS`, `Linux_x64`).
    """
    bin_dir = get_internal_bin_dir()
    exe_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
    bundled = bin_dir / exe_name
    if bundled.exists() and bundled.is_file():
        return [str(bundled)]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    if getattr(sys, "frozen", False) and sys.executable:
        return [sys.executable, "--internal-ytdlp"]
    if sys.executable and importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def get_ffmpeg_binary() -> str:
    """Resolve the `ffmpeg` binary (preferring bundled `_internal/bin/ffmpeg` when present)."""
    bin_dir = get_internal_bin_dir()
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    bundled = bin_dir / exe_name
    if bundled.exists() and bundled.is_file():
        return str(bundled)
    return shutil.which("ffmpeg") or "ffmpeg"


def get_ffprobe_binary() -> str:
    """Resolve the `ffprobe` binary (preferring bundled `_internal/bin/ffprobe` when present)."""
    bin_dir = get_internal_bin_dir()
    exe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    bundled = bin_dir / exe_name
    if bundled.exists() and bundled.is_file():
        return str(bundled)
    return shutil.which("ffprobe") or "ffprobe"
