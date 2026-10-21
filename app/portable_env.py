"""
Hermetic portable environment isolation for Windows PC, macOS, and Docker deployments.

When running as a portable bundle (PyInstaller frozen executable or `DMD_PORTABLE_MODE=1`):
- Overrides `os.environ["PATH"]` to point exclusively to bundled `_internal/bin` (`ffmpeg`, `ffprobe`)
  so any host-installed Python, FFmpeg, or OpenCV libraries are 100% ignored.
- Purges `PYTHONPATH` and `PYTHONHOME`.
- Forces all persistent and temporary runtime files into `./data/` next to the executable.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_portable_bundle() -> bool:
    """Return True when running inside a frozen PyInstaller bundle or explicit portable mode."""
    return bool(getattr(sys, "frozen", False)) or os.environ.get("DMD_PORTABLE_MODE") == "1"


def get_bundle_root() -> Path:
    """Return the root directory of the portable executable or repository."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_internal_bin_dir() -> Path:
    """Return the bundled `_internal/bin` directory containing static `ffmpeg` and `ffprobe`."""
    root = get_bundle_root()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "bin"
        if candidate.exists():
            return candidate
    internal_bin = root / "_internal" / "bin"
    if internal_bin.exists():
        return internal_bin
    return root / "bin"


def get_data_dir() -> Path:
    """Return the persistent `./data` directory used for `server_config.json` and `seen_history.json`."""
    env_data = os.environ.get("DMD_DATA_DIR")
    if env_data:
        path = Path(env_data).resolve()
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
        bin_dir.mkdir(parents=True, exist_ok=True)
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

    return data_dir


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
