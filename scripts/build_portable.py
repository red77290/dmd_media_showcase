#!/usr/bin/env python3
"""
Hermetic Portable Bundle Builder for DMD Media Showcase (Windows x64, macOS arm64/x64, Linux x64).

Executed automatically by the GitHub Actions CI/CD pipeline (`.github/workflows/ci.yml`)
or locally via `python scripts/build_portable.py`.

Produces a self-contained directory and `.zip` archive in `dist/`:
  DMD_Media_Showcase_Portable_<OS>_<ARCH>/
    ├── dmd-media-showcase(.exe)
    ├── Start_Showcase_Windows.bat / Start_Showcase_Mac.command
    ├── data/
    └── _internal/
        ├── bin/
        │   ├── ffmpeg(.exe)
        │   └── ffprobe(.exe)
        └── (bundled Python runtime, dmd_gif_converter, OpenCV, FastAPI, yt-dlp, MQTT broker)
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def _detect_os_arch_tag() -> str:
    sys_name = platform.system().lower()
    machine = platform.machine().lower()
    if "windows" in sys_name or sys.platform == "win32":
        os_tag = "Windows"
    elif "darwin" in sys_name or sys.platform == "darwin":
        os_tag = "macOS"
    else:
        os_tag = "Linux"

    if machine in ("amd64", "x86_64", "x64"):
        arch_tag = "x64"
    elif machine in ("arm64", "aarch64"):
        arch_tag = "arm64"
    else:
        arch_tag = machine or "x64"
    return f"{os_tag}_{arch_tag}"


def _write_launchers(bundle_dir: Path) -> None:
    bat_path = bundle_dir / "Start_Showcase_Windows.bat"
    bat_path.write_text(
        "@echo off\r\n"
        "title DMD Media Showcase Server (Portable Hermetic)\r\n"
        "set DMD_PORTABLE_MODE=1\r\n"
        "echo Starting DMD Media Showcase Server on http://dmdmediashowcase.local:8890 (http://localhost:8890) and MQTT :1883 ...\r\n"
        "start \"\" http://localhost:8890/\r\n"
        "\"%~dp0dmd-media-showcase.exe\"\r\n",
        encoding="utf-8",
    )

    cmd_path = bundle_dir / "Start_Showcase_Mac.command"
    cmd_path.write_text(
        "#!/usr/bin/env bash\n"
        "DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
        "export DMD_PORTABLE_MODE=1\n"
        "echo \"Starting DMD Media Showcase Server on http://dmdmediashowcase.local:8890 (http://localhost:8890) and MQTT :1883 ...\"\n"
        "(sleep 2 && open http://localhost:8890/ >/dev/null 2>&1 || true) &\n"
        "exec \"$DIR/dmd-media-showcase\"\n",
        encoding="utf-8",
    )
    try:
        cmd_path.chmod(cmd_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass


def build_portable_package() -> Path:
    tag = _detect_os_arch_tag()
    bundle_name = f"DMD_Media_Showcase_Portable_{tag}"
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "dmd-media-showcase",
        "--collect-submodules",
        "app",
        "--collect-submodules",
        "dmd_gif_converter",
        "--collect-submodules",
        "src.engine",
        "--collect-submodules",
        "src.plugins",
        "--exclude-module",
        "src.ui",
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "customtkinter",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "yt_dlp",
        str(ROOT / "app" / "main.py"),
    ]
    print(f"[PORTABLE] Running PyInstaller for {bundle_name} ...")
    subprocess.run(pyinstaller_cmd, cwd=str(ROOT), check=True)

    raw_out = DIST_DIR / "dmd-media-showcase"
    target_dir = DIST_DIR / bundle_name
    if target_dir.exists():
        shutil.rmtree(target_dir)
    raw_out.rename(target_dir)

    internal_bin = target_dir / "_internal" / "bin"
    internal_bin.mkdir(parents=True, exist_ok=True)
    (target_dir / "data" / "stream_tmp").mkdir(parents=True, exist_ok=True)

    for tool in ("ffmpeg", "ffprobe"):
        exe_name = f"{tool}.exe" if sys.platform == "win32" else tool
        found = shutil.which(tool)
        if found:
            dst = internal_bin / exe_name
            shutil.copy2(found, dst)
            print(f"[PORTABLE] Bundled static binary: {found} -> {dst}")

    _write_launchers(target_dir)

    archive_base = DIST_DIR / bundle_name
    zip_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=DIST_DIR, base_dir=bundle_name))
    print(f"[PORTABLE] Created hermetic portable archive: {zip_path} ({zip_path.stat().st_size / (1024*1024):.1f} MB)")
    return zip_path


if __name__ == "__main__":
    build_portable_package()
