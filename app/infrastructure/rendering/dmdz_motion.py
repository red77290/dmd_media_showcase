"""
2D Motion-Vector Estimation & CIE-1931 Temporal Dithering Stabilizer for DMDZ Streams.

Provides:
- Exact 1D (`dy`) and 2D (`dx, dy`) in-place `memmove` scroll reference synthesis matching the
  ESP32 `DmdzStreamDecoder` state machine byte-for-byte.
- Two-stage coarse-to-fine 2D integer motion search (`dx in [-16..16]`, `dy in [-8..8]`) supporting
  both lossless exact-match scoring (for `DMDZ` v2 `FRAME_SCROLL_2D`) and sub-LSB tolerance scoring
  (for inter-frame H.264/Lanczos jitter stabilization).
- Exact 8-bit HUB75 CIE-1931 dark green shadow canonicalization (`0` physical photon difference on
  LED panels) + motion-compensated temporal anchoring.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

DMDS_MAGIC = b"DMDS"
HEADER_FMT = "<4sBBHHHHH"
MAX_SCROLL_ROWS = 8
MAX_SCROLL_COLS = 16

# Exact HUB75 8-bit CIE-1931 LUT (`Hub75BulkEncoder.cpp`): dark green G6 pairs `(0,1)`, `(2,3)`,
# `(4,5)`, `(6,7)`, `(8,9)` map to identical physical PWM photon output (`0, 1, 2, 3, 4`).
_CIE1931_8BIT_BOTTOM40 = (
    0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2,
    2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 4,
    4, 4, 4, 4, 4, 5, 5, 5,
)
_CIE8_CANON_G6 = np.arange(64, dtype=np.uint16)
_seen_cie_g: Dict[int, int] = {}
for _g in range(10):
    _c = _CIE1931_8BIT_BOTTOM40[(_g << 2) | (_g >> 4)]
    if _c in _seen_cie_g:
        _CIE8_CANON_G6[_g] = np.uint16(_seen_cie_g[_c])
    else:
        _seen_cie_g[_c] = _g


def scrolled_reference(prev: np.ndarray, dy: int) -> np.ndarray:
    """Reference frame after the client's in-place `memmove` vertical scroll."""
    ref = prev.copy()
    h = prev.shape[0]
    if dy > 0:
        ref[: h - dy] = prev[dy:]
    elif dy < 0:
        ref[-dy:] = prev[: h + dy]
    return ref


def scrolled_reference_2d(prev: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Reference frame after the client's in-place vertical (`dy`) then per-row horizontal (`dx`) `memmove`."""
    ref = prev.copy()
    h, w = int(prev.shape[0]), int(prev.shape[1])
    if dy > 0:
        ref[: h - dy] = ref[dy:]
    elif dy < 0:
        ref[-dy:] = ref[: h + dy]
    if dx > 0:
        ref[:, : w - dx] = ref[:, dx:]
    elif dx < 0:
        ref[:, -dx:] = ref[:, : w + dx]
    return ref


def detect_scroll_1d(cur: np.ndarray, prev: np.ndarray) -> Optional[int]:
    """Detects vertical `dy` scroll (`[-8..8]`) when shifted pixel matches strictly beat `dy=0`."""
    h, w = int(cur.shape[0]), int(cur.shape[1])
    if h < 4 or w <= 0:
        return None
    total_px = h * w
    zero_matches = int(np.count_nonzero(cur == prev))
    if zero_matches == total_px:
        return None
    best_dy: Optional[int] = None
    best_matches = zero_matches + max(16, w // 4)
    for mag in range(1, min(MAX_SCROLL_ROWS, h - 1) + 1):
        shifted_px = (h - mag) * w
        tail_px = mag * w

        m_pos = int(np.count_nonzero(cur[: h - mag] == prev[mag:]))
        if m_pos == shifted_px:
            return mag
        if m_pos + tail_px > best_matches:
            m_pos_total = m_pos + int(np.count_nonzero(cur[h - mag :] == prev[h - mag :]))
            if m_pos_total > best_matches:
                best_matches = m_pos_total
                best_dy = mag

        m_neg = int(np.count_nonzero(cur[mag:] == prev[: h - mag]))
        if m_neg == shifted_px:
            return -mag
        if m_neg + tail_px > best_matches:
            m_neg_total = m_neg + int(np.count_nonzero(cur[:mag] == prev[:mag]))
            if m_neg_total > best_matches:
                best_matches = m_neg_total
                best_dy = -mag
    return best_dy


def detect_scroll_2d(
    cur: np.ndarray,
    prev: np.ndarray,
    tol_mode: bool = False,
    r_tol: int = 1,
    g_tol: int = 2,
    b_tol: int = 1,
) -> Tuple[int, int]:
    """
    Fast two-stage 2D `(dx, dy)` integer camera-pan detector for side-scrolling games & action crops.

    - `tol_mode=False`: counts exact `uint16` pixel matches after the exact in-place 2D `memmove`
      (including unshifted edge pixels), guaranteeing `FRAME_SCROLL_2D` only fires when it strictly
      increases `SKIP` matches.
    - `tol_mode=True`: counts pixels within `(r_tol, g_tol, b_tol)` on the overlapping interior so
      subpixel Lanczos / H.264 camera pans lock cleanly onto the true integer motion vector.
    """
    h, w = int(cur.shape[0]), int(cur.shape[1])
    if h < 4 or w < 4:
        return (0, 0)

    max_dx = min(MAX_SCROLL_COLS, w - 2)
    max_dy = min(MAX_SCROLL_ROWS, h - 2)

    if not tol_mode:
        base = int(np.count_nonzero(cur == prev))
        if base == h * w:
            return (0, 0)
        cr = cg = cb = pr = pg = pb = None
    else:
        cr = ((cur >> 11) & 0x1F).astype(np.int16)
        cg = ((cur >> 5) & 0x3F).astype(np.int16)
        cb = (cur & 0x1F).astype(np.int16)
        pr = ((prev >> 11) & 0x1F).astype(np.int16)
        pg = ((prev >> 5) & 0x3F).astype(np.int16)
        pb = (prev & 0x1F).astype(np.int16)
        base = int(
            np.count_nonzero(
                (np.abs(cr - pr) <= r_tol)
                & (np.abs(cg - pg) <= g_tol)
                & (np.abs(cb - pb) <= b_tol)
            )
        )
        if base == h * w:
            return (0, 0)

    def _score_interior(dx: int, dy: int) -> int:
        if dx == 0 and dy == 0:
            return base
        y0_c = 0 if dy >= 0 else -dy
        y1_c = (h - dy) if dy >= 0 else h
        y0_p = dy if dy >= 0 else 0
        y1_p = h if dy >= 0 else (h + dy)
        x0_c = 0 if dx >= 0 else -dx
        x1_c = (w - dx) if dx >= 0 else w
        x0_p = dx if dx >= 0 else 0
        x1_p = w if dx >= 0 else (w + dx)
        if not tol_mode:
            return int(np.count_nonzero(cur[y0_c:y1_c, x0_c:x1_c] == prev[y0_p:y1_p, x0_p:x1_p]))
        assert cr is not None and cg is not None and cb is not None
        assert pr is not None and pg is not None and pb is not None
        return int(
            np.count_nonzero(
                (np.abs(cr[y0_c:y1_c, x0_c:x1_c] - pr[y0_p:y1_p, x0_p:x1_p]) <= r_tol)
                & (np.abs(cg[y0_c:y1_c, x0_c:x1_c] - pg[y0_p:y1_p, x0_p:x1_p]) <= g_tol)
                & (np.abs(cb[y0_c:y1_c, x0_c:x1_c] - pb[y0_p:y1_p, x0_p:x1_p]) <= b_tol)
            )
        )

    # Stage 1: full 1D axes (`dx` with `dy=0`, `dy` with `dx=0`) + coarse 2D grid.
    # In `tol_mode` on >= 24x48 frames, score strided interior in Stage 1 before full-resolution Stage 2 refinement.
    use_stride = bool(tol_mode and h >= 24 and w >= 48)
    sx = 4 if (use_stride and w >= 64) else 2

    def _score_coarse(dx: int, dy: int) -> int:
        if not use_stride:
            return _score_interior(dx, dy)
        if dx == 0 and dy == 0:
            assert cr is not None and cg is not None and cb is not None
            assert pr is not None and pg is not None and pb is not None
            return int(
                np.count_nonzero(
                    (np.abs(cr[::2, ::sx] - pr[::2, ::sx]) <= r_tol)
                    & (np.abs(cg[::2, ::sx] - pg[::2, ::sx]) <= g_tol)
                    & (np.abs(cb[::2, ::sx] - pb[::2, ::sx]) <= b_tol)
                )
            )
        y0_c = 0 if dy >= 0 else -dy
        y1_c = (h - dy) if dy >= 0 else h
        y0_p = dy if dy >= 0 else 0
        y1_p = h if dy >= 0 else (h + dy)
        x0_c = 0 if dx >= 0 else -dx
        x1_c = (w - dx) if dx >= 0 else w
        x0_p = dx if dx >= 0 else 0
        x1_p = w if dx >= 0 else (w + dx)
        assert cr is not None and cg is not None and cb is not None
        assert pr is not None and pg is not None and pb is not None
        return int(
            np.count_nonzero(
                (np.abs(cr[y0_c:y1_c:2, x0_c:x1_c:sx] - pr[y0_p:y1_p:2, x0_p:x1_p:sx]) <= r_tol)
                & (np.abs(cg[y0_c:y1_c:2, x0_c:x1_c:sx] - pg[y0_p:y1_p:2, x0_p:x1_p:sx]) <= g_tol)
                & (np.abs(cb[y0_c:y1_c:2, x0_c:x1_c:sx] - pb[y0_p:y1_p:2, x0_p:x1_p:sx]) <= b_tol)
            )
        )

    coarse_best = (0, 0)
    coarse_score = _score_coarse(0, 0)
    tested = {(0, 0)}

    for dx in range(-max_dx, max_dx + 1):
        if dx == 0:
            continue
        tested.add((dx, 0))
        sc = _score_coarse(dx, 0)
        if sc > coarse_score:
            coarse_score = sc
            coarse_best = (dx, 0)

    for dy in range(-max_dy, max_dy + 1):
        if dy == 0:
            continue
        tested.add((0, dy))
        sc = _score_coarse(0, dy)
        if sc > coarse_score:
            coarse_score = sc
            coarse_best = (0, dy)

    for dx in range(-min(8, max_dx), min(8, max_dx) + 1, 2):
        if dx == 0:
            continue
        for dy in range(-min(6, max_dy), min(6, max_dy) + 1, 2):
            if dy == 0 or (dx, dy) in tested:
                continue
            tested.add((dx, dy))
            sc = _score_coarse(dx, dy)
            if sc > coarse_score:
                coarse_score = sc
                coarse_best = (dx, dy)

    # Stage 2: full-resolution +/-1 refinement around the coarse winner
    cx, cy = coarse_best
    best_full = (cx, cy)
    best_full_score = _score_interior(cx, cy) if (cx != 0 or cy != 0) else base
    refined_tested = {(0, 0), (cx, cy)}
    for dx in range(max(-max_dx, cx - 1), min(max_dx, cx + 1) + 1):
        for dy in range(max(-max_dy, cy - 1), min(max_dy, cy + 1) + 1):
            if (dx, dy) in refined_tested:
                continue
            refined_tested.add((dx, dy))
            sc = _score_interior(dx, dy)
            if sc > best_full_score:
                best_full_score = sc
                best_full = (dx, dy)

    bx, by = best_full
    if bx == 0 and by == 0:
        return (0, 0)

    min_gain = max(16, w // 4)
    if not tol_mode:
        full_matches = int(np.count_nonzero(cur == scrolled_reference_2d(prev, bx, by)))
        return (bx, by) if full_matches > (base + min_gain) else (0, 0)

    return (bx, by) if best_full_score > (base + min_gain) else (0, 0)


def stabilize_rgb565_stream_temporal_dither(
    raw_stream_path: Path,
    r_tol: int = 1,
    g_tol: int = 2,
    b_tol: int = 1,
) -> bool:
    """
    Suppresses sub-CIE dark shadow noise and 1-LSB H.264/Lanczos inter-frame boil across both
    static and 2D-panning video clips.

    1. Canonicalizes dark G6 shadow pairs `(1->0, 3->2, 5->4, 7->6, 9->8)` that map to the exact
       same 8-bit HUB75 CIE-1931 PWM value (`0` physical photon difference).
    2. Anchors `cur` against `scrolled_reference_2d(prev, dx, dy)` with a ~1 s periodic refresh
       (`anchor_interval = max(12, fps)`), keeping foreground sprites razor-sharp while shrinking
       side-scrolling & action video delta payloads cleanly.
    """
    try:
        raw = raw_stream_path.read_bytes()
    except OSError:
        return False
    if len(raw) < 16 or raw[:4] != DMDS_MAGIC:
        return False
    _, _ver, fps, width, height, total, _dur, _res = struct.unpack(HEADER_FMT, raw[:16])
    frame_bytes = int(width) * int(height) * 2
    if frame_bytes <= 0:
        return False
    avail = (len(raw) - 16) // frame_bytes
    count = min(int(total), avail) if total else avail
    if count < 2:
        return False

    raw_frames = np.frombuffer(raw[16 : 16 + count * frame_bytes], dtype="<u2").reshape(count, height, width)
    canon_g6 = _CIE8_CANON_G6[(raw_frames >> 5) & 0x3F]
    frames = ((raw_frames & np.uint16(0xF81F)) | (canon_g6 << np.uint16(5))).astype("<u2")
    changed = not np.array_equal(frames, raw_frames)

    anchor_interval = max(12, int(fps or 15))
    prev = frames[0].copy()
    out_frames: List[np.ndarray] = [prev]

    for k in range(1, count):
        cur = frames[k]
        if (k % anchor_interval) == 0:
            prev = cur.copy()
            out_frames.append(prev)
            continue

        # Preserve clean exact-scrolling artwork panoramas untouched while stabilizing noisy video pans
        dy_exact = detect_scroll_1d(cur, prev)
        if dy_exact is not None:
            ref_v = scrolled_reference(prev, dy_exact)
            if int(np.count_nonzero(cur == ref_v)) * 10 >= (height * width * 8):
                prev = cur.copy()
                out_frames.append(prev)
                continue

        dx, dy = detect_scroll_2d(cur, prev, tol_mode=True, r_tol=r_tol, g_tol=g_tol, b_tol=b_tol)
        ref = scrolled_reference_2d(prev, dx, dy) if (dx != 0 or dy != 0) else prev

        dr = ((cur >> 11) & 0x1F).astype(np.int16) - ((ref >> 11) & 0x1F).astype(np.int16)
        dg = ((cur >> 5) & 0x3F).astype(np.int16) - ((ref >> 5) & 0x3F).astype(np.int16)
        db = (cur & 0x1F).astype(np.int16) - (ref & 0x1F).astype(np.int16)
        jitter = (np.abs(dr) <= r_tol) & (np.abs(dg) <= g_tol) & (np.abs(db) <= b_tol) & (cur != ref)
        if np.any(jitter):
            recon = np.where(jitter, ref, cur).astype("<u2")
            changed = True
        else:
            recon = cur.copy()
        prev = recon
        out_frames.append(recon)

    if not changed:
        return True

    payload = raw[:16] + np.stack(out_frames).astype("<u2").tobytes() + raw[16 + count * frame_bytes :]
    tmp = raw_stream_path.with_name(raw_stream_path.name + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(raw_stream_path)
    return True


def stabilize_rgb565_frames_sram(
    frames: np.ndarray,
    fps: int = 20,
    r_tol: int = 2,
    g_tol: int = 3,
    b_tol: int = 2,
) -> np.ndarray:
    """
    In-memory H.264 inter-frame mosquito-noise stabilizer for `profile="sram"` (`esp32dev` 0 PSRAM).

    Leaves `.rgb565.stream` and `.dmdz2.stream` untouched on disk for `psram` / `psram_hd` / `RPi`,
    preserves clean exact-scrolling `1D` / `2D` panoramas bit-for-bit, and anchors sub-threshold
    H.264 luma/chroma boil (`|dr| <= r_tol`, `|dg| <= g_tol`, `|db| <= b_tol`, weighted L1 `<= 7`)
    against the motion-compensated previous frame with periodic refresh (`max(18, fps)`).
    """
    arr = np.ascontiguousarray(frames, dtype="<u2")
    if arr.ndim != 3 or arr.shape[0] < 2:
        return arr
    count, height, width = int(arr.shape[0]), int(arr.shape[1]), int(arr.shape[2])
    if height <= 0 or width <= 0:
        return arr

    # Detect whether Frame 0->1 is already a clean exact panorama or static hold so Frame 0 stays bit-exact.
    f0, f1 = arr[0], arr[1]
    dx01, dy01 = detect_scroll_2d(f1, f0, tol_mode=False)
    ref01 = scrolled_reference_2d(f0, dx01, dy01) if (dx01 != 0 or dy01 != 0) else f0
    f0_is_clean_pano = (int(np.count_nonzero(f1 == ref01)) * 10) >= (height * width * 8)

    def _canon_dark_g(frm: np.ndarray) -> np.ndarray:
        canon_g6 = _CIE8_CANON_G6[(frm >> 5) & 0x3F]
        return ((frm & np.uint16(0xF81F)) | (canon_g6 << np.uint16(5))).astype("<u2")

    anchor_interval = max(18, int(fps or 20))
    prev = f0.copy() if f0_is_clean_pano else _canon_dark_g(f0)
    out_frames: List[np.ndarray] = [prev]

    for k in range(1, count):
        cur_raw = arr[k]
        if np.array_equal(cur_raw, prev):
            out_frames.append(prev.copy())
            continue

        # Fast-path clean 1D / 2D artwork panoramas so they remain 100% lossless True-RGB565
        dy_1d = detect_scroll_1d(cur_raw, prev)
        if dy_1d is not None:
            ref_1d = scrolled_reference(prev, dy_1d)
            if (int(np.count_nonzero(cur_raw == ref_1d)) * 10) >= (height * width * 8):
                prev = cur_raw.copy()
                out_frames.append(prev)
                continue
        elif f0_is_clean_pano:
            dx_ex, dy_ex = detect_scroll_2d(cur_raw, prev, tol_mode=False)
            if dx_ex != 0 or dy_ex != 0:
                ref_ex = scrolled_reference_2d(prev, dx_ex, dy_ex)
                if (int(np.count_nonzero(cur_raw == ref_ex)) * 10) >= (height * width * 8):
                    prev = cur_raw.copy()
                    out_frames.append(prev)
                    continue

        cur = _canon_dark_g(cur_raw)
        if (k % anchor_interval) == 0:
            prev = cur
            out_frames.append(prev)
            continue

        dx, dy = detect_scroll_2d(cur, prev, tol_mode=True, r_tol=r_tol, g_tol=g_tol, b_tol=b_tol)
        ref = scrolled_reference_2d(prev, dx, dy) if (dx != 0 or dy != 0) else prev

        adr = np.abs(((cur >> 11) & 0x1F).astype(np.int16) - ((ref >> 11) & 0x1F).astype(np.int16))
        adg = np.abs(((cur >> 5) & 0x3F).astype(np.int16) - ((ref >> 5) & 0x3F).astype(np.int16))
        adb = np.abs((cur & 0x1F).astype(np.int16) - (ref & 0x1F).astype(np.int16))
        jitter = (
            (adr <= r_tol)
            & (adg <= g_tol)
            & (adb <= b_tol)
            & ((adr * 2 + adg + adb * 2) <= 10)
            & (cur != ref)
        )
        recon = np.where(jitter, ref, cur).astype("<u2") if np.any(jitter) else cur
        prev = recon
        out_frames.append(recon)

    return np.stack(out_frames).astype("<u2")


