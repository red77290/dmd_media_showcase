"""
DMDZ v2 `FRAME_PAL8` (`0x05`) Hybrid Scene-Palette Codec for SRAM-Constrained Clients (`profile="sram"`).

Binary Layout (`FRAME_PAL8`, `version == 2`):
- Byte `0`: `0x05` (`FRAME_PAL8`)
- Byte `1`: `pal_hdr = (0x80 if has_scroll_2d else 0x00) | ((pal_count - 1) & 0x7F)`, `1 <= pal_count <= 128`
- Optional Bytes `2..3` (iff `has_scroll_2d`): `struct.pack("<bb", dx, dy)` (`abs(dx) < width`, `abs(dy) < height`)
- Next `pal_count * 2` bytes: `pal_count` little-endian `uint16` RGB565 scene palette entries
- Raster pixel op stream (`npx = width * height`, starting with `pos = 0`, `last_color = int(palette[0])`):
  - `0x00..0x7F` (`idx`): `PAL_INDEX` — `frame[pos] = palette[idx]`, `pos += 1`, `last_color = p` (`idx < pal_count`)
  - `0x80..0xBF` (`0x80 | (count - 1)`, `1..64`): `PAL_SKIP` — keep `count` ref pixels, `pos += count`, `last_color = frame[pos - 1]`
  - `0xC0..0xFE` (`0xC0 + (count - 2)`, `2..64`): `PAL_RUN` — repeat `last_color` `count` times, `pos += count`
  - `0xFF` + `<H` (`1..65535`): `PAL_LONG_SKIP` — keep `count` ref pixels, `pos += count`, `last_color = frame[pos - 1]`
"""
from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.infrastructure.rendering.dmdz_motion import detect_scroll_2d, scrolled_reference_2d

FRAME_PAL8 = 5
SRAM_DEFAULT_FRAME_CAP = 1400


def _append_pal8_skip_ops(out: bytearray, run: int) -> None:
    while run > 0:
        if run <= 64:
            out.append(0x80 | (run - 1))
            return
        if run <= 128:
            out.append(0xBF)
            out.append(0x80 | (run - 65))
            return
        count = min(run, 0xFFFF)
        out.append(0xFF)
        out.append(count & 0xFF)
        out.append((count >> 8) & 0xFF)
        run -= count


def quantize_rgb565_palette(
    pixels_u16: np.ndarray,
    max_colors: int = 128,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Fast vectorized NumPy RGB565 scene quantizer (`1 <= max_colors <= 128`).

    Returns `(palette_u16, mapped_indices_i16)` sorted by descending frequency so `palette[0]`
    maximizes initial `last_color` hits. Uses sub-linear `sqrt(count)` median-cut box splitting
    over 12-bit RGB444 centroids (`<= 7` rounds) so both smooth gradients and small bright sprites
    retain dedicated palette entries.
    """
    flat = np.ascontiguousarray(pixels_u16, dtype="<u2").ravel()
    cap = max(1, min(128, int(max_colors)))
    if flat.size == 0:
        return np.zeros(1, dtype="<u2"), np.zeros(0, dtype=np.int16)

    u_vals, u_inv, u_cnts = np.unique(flat, return_inverse=True, return_counts=True)
    if u_vals.size <= cap:
        order = np.argsort(-u_cnts, kind="mergesort")
        palette = u_vals[order].astype("<u2")
        rank = np.empty(order.size, dtype=np.int16)
        rank[order] = np.arange(order.size, dtype=np.int16)
        return palette, rank[u_inv]

    key12 = (
        (((flat >> np.uint16(12)) & np.uint16(0x0F)) << np.uint16(8))
        | (((flat >> np.uint16(7)) & np.uint16(0x0F)) << np.uint16(4))
        | ((flat >> np.uint16(1)) & np.uint16(0x0F))
    ).astype(np.int32)
    cnt12 = np.bincount(key12, minlength=4096)
    nz = np.flatnonzero(cnt12)
    w_cnt = cnt12[nz].astype(np.float32)

    r5_px = ((flat >> np.uint16(11)) & np.uint16(0x1F)).astype(np.float32)
    g6_px = ((flat >> np.uint16(5)) & np.uint16(0x3F)).astype(np.float32)
    b5_px = (flat & np.uint16(0x1F)).astype(np.float32)
    cr = np.bincount(key12, weights=r5_px, minlength=4096)[nz] / w_cnt
    cg = np.bincount(key12, weights=g6_px, minlength=4096)[nz] / w_cnt
    cb = np.bincount(key12, weights=b5_px, minlength=4096)[nz] / w_cnt

    if nz.size <= cap:
        boxes: List[np.ndarray] = [np.array([i], dtype=np.int32) for i in range(nz.size)]
    else:
        pts_r = cr * 2.0
        pts_g = cg
        pts_b = cb * 2.0
        pts_w = np.sqrt(w_cnt)
        active: List[Tuple[np.ndarray, int]] = [(np.arange(nz.size, dtype=np.int32), cap)]
        for _ in range(8):
            if not any(q > 1 and idx.size > 1 for idx, q in active):
                break
            nxt: List[Tuple[np.ndarray, int]] = []
            for idx, q in active:
                if q <= 1 or idx.size <= 1:
                    nxt.append((idx, 1))
                    continue
                r_s, g_s, b_s = pts_r[idx], pts_g[idx], pts_b[idx]
                sp_r = (float(r_s.max()) - float(r_s.min())) * 1.1
                sp_g = (float(g_s.max()) - float(g_s.min())) * 1.2
                sp_b = float(b_s.max()) - float(b_s.min())
                if sp_g >= sp_r and sp_g >= sp_b:
                    axis_vals = g_s
                elif sp_r >= sp_b:
                    axis_vals = r_s
                else:
                    axis_vals = b_s
                ord_idx = idx[np.argsort(axis_vals, kind="mergesort")]
                q_left = max(1, min(q - 1, ord_idx.size - 1, q // 2))
                cum_w = np.cumsum(pts_w[ord_idx])
                target_w = float(cum_w[-1]) * (float(q_left) / float(q))
                split_at = int(np.searchsorted(cum_w, target_w, side="left"))
                split_at = max(1, min(ord_idx.size - 1, split_at))
                ql = max(1, min(q_left, split_at))
                qr = max(1, min(q - ql, ord_idx.size - split_at))
                nxt.append((ord_idx[:split_at], ql))
                nxt.append((ord_idx[split_at:], qr))
            active = nxt
        boxes = [idx for idx, _ in active]

    color_weights: Dict[int, float] = {}
    for idx in boxes:
        bw = w_cnt[idx]
        bsum = float(bw.sum())
        pr = max(0, min(31, int(round(float((cr[idx] * bw).sum()) / bsum))))
        pg = max(0, min(63, int(round(float((cg[idx] * bw).sum()) / bsum))))
        pb = max(0, min(31, int(round(float((cb[idx] * bw).sum()) / bsum))))
        c565 = ((pr & 0x1F) << 11) | ((pg & 0x3F) << 5) | (pb & 0x1F)
        color_weights[c565] = color_weights.get(c565, 0.0) + bsum

    sorted_pal = [c for c, _ in sorted(color_weights.items(), key=lambda kv: -kv[1])][:cap]
    palette = np.array(sorted_pal, dtype="<u2")
    pal_r = ((palette >> np.uint16(11)) & np.uint16(0x1F)).astype(np.float32)
    pal_g = ((palette >> np.uint16(5)) & np.uint16(0x3F)).astype(np.float32)
    pal_b = (palette & np.uint16(0x1F)).astype(np.float32)

    dr = (cr[:, None] - pal_r[None, :]) * 2.0
    dg = cg[:, None] - pal_g[None, :]
    db = (cb[:, None] - pal_b[None, :]) * 2.0
    dist2 = 3.0 * (dr * dr) + 4.0 * (dg * dg) + 2.0 * (db * db)
    nearest_pal = np.argmin(dist2, axis=1).astype(np.int16)

    lut12 = np.zeros(4096, dtype=np.int16)
    lut12[nz] = nearest_pal
    return palette, lut12[key12]


def encode_frame_pal8_payload(
    target_2d: np.ndarray,
    palette_u16: np.ndarray,
    pal_idx_flat: np.ndarray,
    ref_2d: Optional[np.ndarray] = None,
    dx: int = 0,
    dy: int = 0,
) -> bytes:
    """Encodes a complete `FRAME_PAL8` (`0x05`) payload from `target_2d`, `palette_u16`, and `pal_idx_flat`."""
    target = np.ascontiguousarray(target_2d, dtype="<u2").ravel()
    npx = int(target.size)
    pal = np.ascontiguousarray(palette_u16, dtype="<u2").ravel()
    if pal.size == 0:
        pal = np.zeros(1, dtype="<u2")
    if pal.size > 128:
        raise ValueError("FRAME_PAL8 palette exceeds 128 colors")
    pal_count = int(pal.size)

    has_scroll = bool(ref_2d is not None and (dx != 0 or dy != 0))
    pal_hdr = (0x80 if has_scroll else 0x00) | ((pal_count - 1) & 0x7F)
    out = bytearray([FRAME_PAL8, pal_hdr])
    if has_scroll:
        out += struct.pack("<bb", int(dx), int(dy))
    out += pal.tobytes()

    if npx == 0:
        return bytes(out)

    diff_mv: Optional[memoryview] = None
    diff_len = 0
    if ref_2d is not None:
        ref_flat = np.ascontiguousarray(ref_2d, dtype="<u2").ravel()
        diff_idx = np.flatnonzero(target != ref_flat)
        diff_len = int(diff_idx.size)
        if diff_len == 0:
            _append_pal8_skip_ops(out, npx)
            return bytes(out)
        diff_mv = memoryview(diff_idx)

    target_mv = memoryview(target)
    idx_mv = memoryview(np.ascontiguousarray(pal_idx_flat, dtype=np.int16).ravel())
    last_color = int(pal[0])
    pos = 0
    d_pos = 0

    while pos < npx:
        if diff_mv is not None:
            while d_pos < diff_len and int(diff_mv[d_pos]) < pos:
                d_pos += 1
            if d_pos >= diff_len:
                _append_pal8_skip_ops(out, npx - pos)
                return bytes(out)
            next_diff = int(diff_mv[d_pos])
            if next_diff > pos:
                _append_pal8_skip_ops(out, next_diff - pos)
                last_color = int(target_mv[next_diff - 1])
                pos = next_diff
                continue
            d_pos += 1

        p = int(target_mv[pos])
        if p == last_color:
            if pos + 1 < npx and int(target_mv[pos + 1]) == last_color:
                j = pos + 2
                max_j = min(npx, pos + 64)
                while j < max_j and int(target_mv[j]) == last_color:
                    j += 1
                out.append(0xC0 + (j - pos - 2))
                pos = j
                continue
            out.append(int(idx_mv[pos]))
            pos += 1
            continue

        out.append(int(idx_mv[pos]))
        last_color = p
        pos += 1
        if pos + 1 < npx and int(target_mv[pos]) == last_color and int(target_mv[pos + 1]) == last_color:
            j = pos + 2
            max_j = min(npx, pos + 64)
            while j < max_j and int(target_mv[j]) == last_color:
                j += 1
            # Prefer 1-byte PAL_RUN(j - pos) unless a contiguous PAL_SKIP starting at `pos` reaches at least `j`
            has_diff_before_j = diff_mv is None or (d_pos < diff_len and int(diff_mv[d_pos]) < j)
            if has_diff_before_j:
                out.append(0xC0 + (j - pos - 2))
                pos = j

    return bytes(out)


def _coalesce_horizontal_runs(work_2d: np.ndarray, step: int) -> np.ndarray:
    """Coalesces `step`-wide horizontal pixel groups to their RGB565 centroid for extreme noisy cuts."""
    if step <= 1:
        return work_2d
    h, w = int(work_2d.shape[0]), int(work_2d.shape[1])
    usable_w = (w // step) * step
    out = work_2d.copy()
    if usable_w > 0:
        blk = out[:, :usable_w].reshape(h, usable_w // step, step)
        r = (((blk >> np.uint16(11)) & np.uint16(0x1F)).astype(np.uint16).sum(axis=2) + (step // 2)) // step
        g = (((blk >> np.uint16(5)) & np.uint16(0x3F)).astype(np.uint16).sum(axis=2) + (step // 2)) // step
        b = ((blk & np.uint16(0x1F)).astype(np.uint16).sum(axis=2) + (step // 2)) // step
        rep = ((r << np.uint16(11)) | (g << np.uint16(5)) | b).astype("<u2")
        out[:, :usable_w] = np.repeat(rep, step, axis=1)
    if usable_w < w:
        out[:, usable_w:] = out[:, usable_w - 1 : usable_w] if usable_w > 0 else out[:, :1]
    return out


def encode_adaptive_pal8_sram(
    cur_2d: np.ndarray,
    prev_2d: Optional[np.ndarray],
    is_key: bool = False,
    frame_cap: int = SRAM_DEFAULT_FRAME_CAP,
    scroll_hint: Optional[Tuple[int, int]] = None,
) -> Tuple[bytes, np.ndarray]:
    """
    Encodes `cur_2d` as `FRAME_PAL8` (`0x05`) within `frame_cap` bytes (`1,400 B` default) and
    returns `(pal8_payload, exact_reconstructed_frame_2d_u16)` for zero-drift reference tracking.
    """
    cur = np.ascontiguousarray(cur_2d, dtype="<u2")
    h, w = int(cur.shape[0]), int(cur.shape[1])
    dx, dy = 0, 0
    ref: Optional[np.ndarray] = None

    if not is_key and prev_2d is not None:
        prev = np.ascontiguousarray(prev_2d, dtype="<u2")
        if scroll_hint is not None:
            dx, dy = int(scroll_hint[0]), int(scroll_hint[1])
        else:
            dx, dy = detect_scroll_2d(cur, prev, tol_mode=True, r_tol=2, g_tol=3, b_tol=2)
        ref = scrolled_reference_2d(prev, dx, dy) if (dx != 0 or dy != 0) else prev

    # Fidelity ladder: choose smart entry rung from changed-pixel density to avoid wasted passes
    full_ladder: Tuple[Tuple[int, Tuple[int, int, int], int], ...] = (
        (128, (1, 2, 1), 1),
        (64, (2, 3, 2), 1),
        (48, (2, 4, 2), 2),
        (32, (3, 5, 3), 4),
        (24, (3, 6, 3), 8),
        (16, (4, 7, 4), 16),
    )

    horiz_runs = int(np.count_nonzero(cur[:, 1:] == cur[:, :-1])) if w > 1 else 0
    if ref is not None:
        raw_changed = int(np.count_nonzero(cur != ref))
    else:
        raw_changed = h * w

    start_idx = 0
    if raw_changed > int(frame_cap * 1.45) and horiz_runs < (h * w) // 2:
        start_idx = 2
    elif raw_changed > int(frame_cap * 0.90) and horiz_runs < (h * w) // 2:
        start_idx = 1

    best_payload: Optional[bytes] = None
    best_recon: Optional[np.ndarray] = None

    for max_colors, (rt, gt, bt), h_step in full_ladder[start_idx:]:
        if h_step == 16 and (h * w) <= 4096 and best_payload is not None and len(best_payload) <= frame_cap:
            break
        work = _coalesce_horizontal_runs(cur.copy(), h_step) if h_step > 1 else cur.copy()

        if ref is not None:
            adr = np.abs(((work >> 11) & 0x1F).astype(np.int16) - ((ref >> 11) & 0x1F).astype(np.int16))
            adg = np.abs(((work >> 5) & 0x3F).astype(np.int16) - ((ref >> 5) & 0x3F).astype(np.int16))
            adb = np.abs((work & 0x1F).astype(np.int16) - (ref & 0x1F).astype(np.int16))
            snap = (adr <= rt) & (adg <= gt) & (adb <= bt)
            if h_step > 1:
                # Only snap full `h_step`-aligned horizontal groups so isolated coincidences never split runs
                usable_w = (w // h_step) * h_step
                if usable_w > 0:
                    blk_snap = np.all(snap[:, :usable_w].reshape(h, usable_w // h_step, h_step), axis=2)
                    snap[:, :usable_w] = np.repeat(blk_snap, h_step, axis=1)
                if usable_w < w:
                    snap[:, usable_w:] = False
            if np.any(snap):
                work = np.where(snap, ref, work).astype("<u2")
            changed_mask = work != ref
            if not np.any(changed_mask):
                pal = np.array([int(ref.ravel()[0])], dtype="<u2")
                idx_full = np.zeros(h * w, dtype=np.int16)
                payload = encode_frame_pal8_payload(ref, pal, idx_full, ref_2d=ref, dx=dx, dy=dy)
                return payload, ref.copy()

            changed_px = work[changed_mask]
            pal, ch_idx = quantize_rgb565_palette(changed_px, max_colors=max_colors)
            recon = ref.copy()
            recon[changed_mask] = pal[ch_idx]
            idx_full = np.zeros(h * w, dtype=np.int16)
            idx_full[changed_mask.ravel()] = ch_idx
            payload = encode_frame_pal8_payload(recon, pal, idx_full, ref_2d=ref, dx=dx, dy=dy)

            # On abrupt scene cuts (`h_step >= 4`), an unrelated `ref` may still exceed run-only coding
            if len(payload) > frame_cap and h_step >= 4:
                pal_k, idx_k = quantize_rgb565_palette(work.ravel(), max_colors=max_colors)
                recon_k = pal_k[idx_k].reshape(h, w).astype("<u2")
                payload_k = encode_frame_pal8_payload(recon_k, pal_k, idx_k, ref_2d=None, dx=0, dy=0)
                if len(payload_k) < len(payload):
                    payload, recon = payload_k, recon_k
        else:
            pal, idx_full = quantize_rgb565_palette(work.ravel(), max_colors=max_colors)
            recon = pal[idx_full].reshape(h, w).astype("<u2")
            payload = encode_frame_pal8_payload(recon, pal, idx_full, ref_2d=None, dx=0, dy=0)

        if best_payload is None or len(payload) < len(best_payload):
            best_payload = payload
            best_recon = recon
        if len(payload) <= frame_cap:
            return payload, recon

    assert best_payload is not None and best_recon is not None
    return best_payload, best_recon


def decode_frame_pal8(
    data: bytes,
    off: int,
    end: int,
    frame: List[int],
    width: int,
    height: int,
) -> List[int]:
    """Reference decoder for `FRAME_PAL8` (`0x05`), mirroring ESP32 `DmdzStreamDecoder` byte for byte."""
    npx = int(width) * int(height)
    if off >= end:
        raise ValueError("DMDZ PAL8 truncated header")
    pal_hdr = data[off]
    off += 1
    has_scroll_2d = (pal_hdr & 0x80) != 0
    pal_count = (pal_hdr & 0x7F) + 1

    if has_scroll_2d:
        if (off + 2) > end:
            raise ValueError("DMDZ PAL8 scroll 2D truncated")
        dx, dy = struct.unpack_from("<bb", data, off)
        off += 2
        if abs(dx) >= width or abs(dy) >= height:
            raise ValueError("DMDZ PAL8 scroll 2D overflow")
        arr = np.array(frame, dtype=np.uint16).reshape(height, width)
        frame = scrolled_reference_2d(arr, dx, dy).ravel().tolist()

    pal_bytes = pal_count * 2
    if (off + pal_bytes) > end:
        raise ValueError("DMDZ PAL8 palette truncated")
    palette = np.frombuffer(data, dtype="<u2", count=pal_count, offset=off).astype(int).tolist()
    off += pal_bytes

    pos = 0
    last_color = int(palette[0])
    while off < end:
        b = data[off]
        off += 1
        if b <= 0x7F:
            if b >= pal_count or pos >= npx:
                raise ValueError("DMDZ PAL8 index overflow")
            p = palette[b]
            frame[pos] = p
            pos += 1
            last_color = p
        elif b <= 0xBF:
            count = (b & 0x3F) + 1
            if (pos + count) > npx:
                raise ValueError("DMDZ PAL8 skip overflow")
            pos += count
            last_color = frame[pos - 1]
        elif b <= 0xFE:
            count = (b - 0xC0) + 2
            if (pos + count) > npx:
                raise ValueError("DMDZ PAL8 run overflow")
            for _ in range(count):
                frame[pos] = last_color
                pos += 1
        else:
            if (off + 2) > end:
                raise ValueError("DMDZ PAL8 long skip truncated")
            (count,) = struct.unpack_from("<H", data, off)
            off += 2
            if count == 0 or (pos + count) > npx:
                raise ValueError("DMDZ PAL8 long skip overflow")
            pos += count
            last_color = frame[pos - 1]

    if pos != npx or off != end:
        raise ValueError("DMDZ PAL8 frame size mismatch")
    return frame
