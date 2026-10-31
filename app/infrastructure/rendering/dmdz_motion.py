"""
Lossless 1D & 2D Integer Motion-Vector Estimation for DMDZ v2 Streams.

Provides:
- Exact 1D (`dy`) and 2D (`dx, dy`) in-place `memmove` scroll reference synthesis matching the
  ESP32 (`DmdzStreamDecoder`) and Raspberry Pi (`dmd_stream.rs`) state machines byte-for-byte.
- Two-stage coarse-to-fine exact 2D integer motion search (`dx in [-16..16]`, `dy in [-8..8]`)
  for `DMDZ` v2 `FRAME_SCROLL` and `FRAME_SCROLL_2D`.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

DMDS_MAGIC = b"DMDS"
HEADER_FMT = "<4sBBHHHHH"
MAX_SCROLL_ROWS = 8
MAX_SCROLL_COLS = 16


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


def detect_scroll_2d(cur: np.ndarray, prev: np.ndarray) -> Tuple[int, int]:
    """
    Fast two-stage exact 2D `(dx, dy)` integer camera-pan detector for side-scrolling games & panoramas.

    Counts exact `uint16` pixel matches after the exact in-place 2D `memmove` (including unshifted
    edge pixels), guaranteeing `FRAME_SCROLL_2D` only fires when it strictly increases `SKIP` matches.
    """
    h, w = int(cur.shape[0]), int(cur.shape[1])
    if h < 4 or w < 4:
        return (0, 0)

    base = int(np.count_nonzero(cur == prev))
    if base == h * w:
        return (0, 0)

    max_dx = min(MAX_SCROLL_COLS, w - 2)
    max_dy = min(MAX_SCROLL_ROWS, h - 2)

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
        return int(np.count_nonzero(cur[y0_c:y1_c, x0_c:x1_c] == prev[y0_p:y1_p, x0_p:x1_p]))

    coarse_best = (0, 0)
    coarse_score = base
    tested = {(0, 0)}

    for dx in range(-max_dx, max_dx + 1):
        if dx == 0:
            continue
        tested.add((dx, 0))
        sc = _score_interior(dx, 0)
        if sc > coarse_score:
            coarse_score = sc
            coarse_best = (dx, 0)

    for dy in range(-max_dy, max_dy + 1):
        if dy == 0:
            continue
        tested.add((0, dy))
        sc = _score_interior(0, dy)
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
            sc = _score_interior(dx, dy)
            if sc > coarse_score:
                coarse_score = sc
                coarse_best = (dx, dy)

    cx, cy = coarse_best
    best_full = (cx, cy)
    best_full_score = coarse_score
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
    full_matches = int(np.count_nonzero(cur == scrolled_reference_2d(prev, bx, by)))
    return (bx, by) if full_matches > (base + min_gain) else (0, 0)
