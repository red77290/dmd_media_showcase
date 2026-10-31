"""
Reusable Imaging, Typography, Palette & Framing Primitives (`app/infrastructure/rendering/imaging_primitives.py`).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import ssl
import threading
from typing import Callable, Literal, Optional, Set, Tuple, Union
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from app.domain.style_specs import parse_hex_rgb
from app.infrastructure.scrapers.http_utils import install_ipv4_preference
from src.engine.conversion.core import DEFAULT_PARAMS

DEFAULT_PARAMS["font_name"] = None
DEFAULT_PARAMS["text_overlay_enabled"] = False
DEFAULT_PARAMS["text_font_file"] = ""
DEFAULT_PARAMS["quiet_terminal_log"] = True
install_ipv4_preference()

logger = logging.getLogger("dmd_showcase.converter")

_UNVERIFIED_TLS_HOSTS: Set[str] = set()
_UNVERIFIED_TLS_LOCK = threading.Lock()


def _build_verified_ssl_context() -> ssl.SSLContext:
    """Create a verified TLS context backed by `certifi` when available."""
    try:
        import certifi

        ca_file = certifi.where()
        if ca_file and os.path.isfile(ca_file):
            return ssl.create_default_context(cafile=ca_file)
    except Exception:
        pass
    return ssl.create_default_context()


def _is_ssl_verification_error(exc: BaseException) -> bool:
    """Return True when `exc` (or `URLError.reason`) is a TLS certificate verification error."""
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, URLError) and isinstance(getattr(exc, "reason", None), ssl.SSLError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(exc)


_MAIN_PROCESS_PID = os.getpid()


def apply_worker_nice(nice_inc: int = 10, ffmpeg_threads: int = 1) -> None:
    """Lower OS scheduling priority and cap OpenMP/OpenCV thread pools inside child transcoding workers."""
    install_ipv4_preference()
    eff_threads = max(1, min(8, int(ffmpeg_threads)))
    os.environ["OMP_NUM_THREADS"] = str(eff_threads)
    if nice_inc > 0 and hasattr(os, "nice") and os.getpid() != _MAIN_PROCESS_PID:
        try:
            current = os.nice(0)
            if current < nice_inc:
                os.nice(nice_inc - current)
        except Exception:
            pass
    try:
        cv2.setNumThreads(eff_threads)
    except Exception:
        pass


def default_http_download_bytes(url: Optional[str], timeout: float = 6.0) -> Optional[bytes]:
    """
    Download up to 80 MB over HTTP/HTTPS with a standard browser User-Agent.
    Automatically retries with an unverified TLS context when an upstream retro archive
    (such as `adb.arcadeitalia.net`) presents an expired certificate.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    is_https = url.startswith("https://")
    host = (urlparse(url).hostname or "").lower() if is_https else ""
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 dmd-media-showcase/2.1"
            )
        },
    )

    def _read_with_context(ctx: Optional[ssl.SSLContext]) -> Optional[bytes]:
        open_kwargs = {"context": ctx} if (is_https and ctx is not None) else {}
        with urlopen(req, timeout=timeout, **open_kwargs) as resp:
            if resp.status == 200:
                data = resp.read(80 * 1024 * 1024)
                if len(data) < 128:
                    logger.info(
                        "[CONVERTER] Ignored empty/truncated payload (%d bytes) from %s",
                        len(data),
                        url,
                    )
                    return None
                logger.info(
                    "[CONVERTER] Downloaded %.1f KB from %s",
                    len(data) / 1024.0,
                    url,
                )
                return data
        return None

    use_unverified = False
    if is_https and host:
        with _UNVERIFIED_TLS_LOCK:
            use_unverified = host in _UNVERIFIED_TLS_HOSTS

    try:
        ctx = (
            ssl._create_unverified_context()
            if (is_https and use_unverified)
            else (_build_verified_ssl_context() if is_https else None)
        )
        return _read_with_context(ctx)
    except Exception as exc:
        if is_https and not use_unverified and _is_ssl_verification_error(exc):
            first_for_host = False
            if host:
                with _UNVERIFIED_TLS_LOCK:
                    if host not in _UNVERIFIED_TLS_HOSTS:
                        _UNVERIFIED_TLS_HOSTS.add(host)
                        first_for_host = True
            if first_for_host:
                logger.info(
                    "[CONVERTER] TLS certificate verification failed for '%s' (%s); "
                    "retrying with unverified TLS context",
                    host or url,
                    exc,
                )
            try:
                return _read_with_context(ssl._create_unverified_context())
            except Exception as retry_exc:
                logger.info("[CONVERTER] Download failed for %s: %s", url, retry_exc)
                return None
        logger.info("[CONVERTER] Download failed for %s: %s", url, exc)
    return None


def resolve_active_downloader() -> Callable[[Optional[str], float], Optional[bytes]]:
    """
    Resolve `_download_bytes` through `app.scheduler.asset_builder` when loaded so test
    monkeypatches on `app.scheduler.asset_builder._download_bytes` take effect everywhere.
    """
    import sys

    ab_mod = sys.modules.get("app.scheduler.asset_builder")
    if ab_mod is not None and hasattr(ab_mod, "_download_bytes"):
        return getattr(ab_mod, "_download_bytes")
    return default_http_download_bytes


def generate_fallback_artwork(
    path: Path,
    width: int,
    height: int,
    title: str,
    badge: str,
    palette: Tuple[int, int, int],
) -> Path:
    """Generate clean LED-friendly badge artwork when a remote poster image is offline."""
    r0, g0, b0 = palette
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        factor = 0.30 + 0.70 * (1.0 - y / max(1, height - 1))
        arr[y, :, 0] = min(255, int(r0 * factor))
        arr[y, :, 1] = min(255, int(g0 * factor))
        arr[y, :, 2] = min(255, int(b0 * factor))

    img = Image.fromarray(arr, mode="RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(255, 255, 255), width=1)
    short_label = (badge[:6] if width <= 40 else f"{badge}: {title[:16]}").upper()
    draw.text((3, max(2, height // 2 - 5)), short_label, fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")
    return path


def fit_poster_preserving_cover(
    img: Image.Image,
    width: int,
    height: int,
    palette: Tuple[int, int, int],
    color_boost: bool = True,
) -> Image.Image:
    """Resize a boxart/flyer/cover to `(width, height)` without cropping away rectangular covers."""
    rgb = img.convert("RGB")
    src_w, src_h = rgb.size
    ratio = src_w / max(1, src_h)
    target_ratio = width / max(1, height)

    if abs(ratio - target_ratio) <= 0.35:
        canvas = ImageOps.fit(
            rgb,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.4),
        )
    else:
        bg_r, bg_g, bg_b = (palette[0] // 6, palette[1] // 6, palette[2] // 6)
        canvas = Image.new("RGB", (width, height), (bg_r, bg_g, bg_b))
        contained = ImageOps.contain(rgb, (width, height), method=Image.Resampling.LANCZOS)
        ox = (width - contained.width) // 2
        oy = (height - contained.height) // 2
        canvas.paste(contained, (ox, oy))

    if color_boost:
        canvas = ImageEnhance.Contrast(canvas).enhance(1.22)
        canvas = ImageEnhance.Color(canvas).enhance(1.30)
        canvas = ImageEnhance.Sharpness(canvas).enhance(1.15)
    return canvas


def resolve_framing_and_color_params(
    video_mode: str = "auto_action",
    color_boost: Union[str, bool] = "auto",
    domain: str = "",
) -> Tuple[bool, bool, bool, Literal["pixel_art", "anime", "cinema"]]:
    """Resolve orthogonal framing (`video_mode`) and LED colorimetry (`color_boost`) settings."""
    vm = str(video_mode or "auto_action").strip().lower()
    dom = str(domain or "").strip().lower()
    scroll_on_video = vm == "panorama"
    use_auto_action = vm not in ("panorama", "center_crop")

    if isinstance(color_boost, bool):
        cb = "auto" if color_boost else "cinema"
    else:
        cb = str(color_boost or "auto").strip().lower()

    if cb in ("pixel_art", "anime", "cinema"):
        preset_mode: Literal["pixel_art", "anime", "cinema"] = cb  # type: ignore[assignment]
        use_smart_color_boost = False
    elif cb in ("false", "0", "no", "off", "none"):
        preset_mode = "cinema"
        use_smart_color_boost = False
    else:
        if vm in ("pixel_art", "anime", "cinema"):
            preset_mode = vm  # type: ignore[assignment]
        elif dom in ("movies", "series"):
            preset_mode = "cinema"
        elif dom == "anime":
            preset_mode = "anime"
        else:
            preset_mode = "pixel_art"
        use_smart_color_boost = True

    return use_auto_action, scroll_on_video, use_smart_color_boost, preset_mode


def sanitize_card_text(text: str) -> str:
    """Normalize typographic punctuation into crisp single-line DMD text."""
    if not text:
        return ""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u0153": "oe",
        "\u0152": "OE",
        "\u00a0": " ",
        "\n": " ",
        "\r": " ",
    }
    cleaned = str(text)
    for k, v in replacements.items():
        cleaned = cleaned.replace(k, v)
    return " ".join(cleaned.split())


def load_card_font(
    text_sample: str,
    size: int = 10,
    font_style: str = "arcade_pixel",
) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Load an arcade bitmap font or Unicode/CJK TrueType font matching `font_style`."""
    needs_unicode = any(ord(ch) > 255 for ch in text_sample)
    style = str(font_style or "arcade_pixel").strip().lower()
    win_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if needs_unicode:
        candidates: Tuple[str, ...] = (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            os.path.join(win_fonts, "msyh.ttc"),
            os.path.join(win_fonts, "msgothic.ttc"),
            os.path.join(win_fonts, "malgun.ttf"),
            os.path.join(win_fonts, "segoeui.ttf"),
        )
    elif style == "clean_sans":
        candidates = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            os.path.join(win_fonts, "segoeuib.ttf"),
            os.path.join(win_fonts, "arialbd.ttf"),
            os.path.join(win_fonts, "segoeui.ttf"),
            os.path.join(win_fonts, "arial.ttf"),
        )
    else:
        return ImageFont.load_default()

    for fp in candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def resolve_scroll_px_per_sec(speed: str) -> float:
    """Convert named or numeric (`1..10`) scroll speeds into pixels per second."""
    raw = str(speed or "normal").strip().lower()
    named = {"slow": 22.0, "normal": 34.0, "fast": 48.0, "turbo": 64.0}
    if raw in named:
        return named[raw]
    try:
        lvl = max(1, min(10, int(raw)))
        return 16.0 + lvl * 5.0
    except ValueError:
        return 34.0


def apply_amber_dmd_palette(arr: np.ndarray) -> np.ndarray:
    """Map an RGB888 frame to classic Williams/Bally/Stern #FF7300 plasma dot-matrix amber."""
    f = arr.astype(np.float32)
    lum = 0.299 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.114 * f[:, :, 2]
    out = np.empty_like(arr, dtype=np.uint8)
    out[:, :, 0] = np.clip(lum * 1.08, 0, 255).astype(np.uint8)
    out[:, :, 1] = np.clip(lum * 0.46, 0, 255).astype(np.uint8)
    out[:, :, 2] = np.clip(lum * 0.04, 0, 255).astype(np.uint8)
    return out


__all__ = [
    "apply_amber_dmd_palette",
    "apply_worker_nice",
    "default_http_download_bytes",
    "fit_poster_preserving_cover",
    "generate_fallback_artwork",
    "load_card_font",
    "parse_hex_rgb",
    "resolve_active_downloader",
    "resolve_framing_and_color_params",
    "resolve_scroll_px_per_sec",
    "sanitize_card_text",
]
