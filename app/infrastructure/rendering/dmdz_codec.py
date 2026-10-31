"""
DMDZ v1 & v2: lossless, in-place decodable RGB565 frame codec for memory-constrained DMD clients.

Embedded clients (classic ESP32 without PSRAM) run with a fixed ~5.7 KB TCP receive window, smaller
than one raw 128x32 RGB565 frame (8 KB). Raw DMDS throughput is therefore capped at `window / RTT`.
DMDZ shrinks real Showcase streams 3-7x (videos) and 23-64x (panoramas) with zero extra heap on the
ESP32, so multiple compressed frames fit inside the client's circular RAM jitter buffer.

### Container (Little-Endian)
- `0..15`: header `<4sBBHHHHH` = `"DMDZ"`, `version` (`1` or `2`), `fps`, `width`, `height`,
  `total_frames`, `frame_dur_ms`, `keyframe_interval` (`0` = only frame 0 is a keyframe).
- Per frame: `<H` payload length `L`, then `L` bytes:
  - `type == 0` (`FRAME_DELTA`): delta op stream against `prev`.
  - `type == 1` (`FRAME_SCROLL`): `dy` (`int8`) vertical `memmove` + op stream.
  - `type == 2` (`FRAME_KEY`): keyframe op stream (no prior reference required).
  - `type == 3` (`FRAME_SCROLL_2D`, v2): `dx` (`int8`), `dy` (`int8`) 2D `memmove` + op stream.
  - `type == 4` (`FRAME_RAW`, v2): raw uncompressed `width * height * 2` little-endian bytes (`L = 1 + 2*N`).

### 2D scroll reference (`type == 3`, v2)
1. Vertical `memmove` (`dy > 0` moves content up: rows `[0, h - dy) <- [dy, h)`; `dy < 0` moves down).
2. Horizontal `memmove` per row (`dx > 0` moves content left: cols `[0, w - dx) <- [dx, w)`; `dx < 0` moves right).

### Ops (state reset at each frame: `last = 0`, `cache[64] = 0`)
- `00nnnnnn` (`0x00..0x3F`) SKIP `n + 1`: pixels equal to reference; `last = frame[pos - 1]`, cached.
- `01nnnnnn` (`0x40..0x7F`) RUN `n + 1`: repeat `last`.
- `10iiiiii` (`0x80..0xBF`) INDEX: `cache[i]`.
- `110xxxxx` (`0xC0..0xDA`) DIFF: `x = (dr + 1) * 9 + (dg + 1) * 3 + (db + 1)` in `{-1, 0, 1}` from `last`.
- `0xDC..0xDF` + `b2` (v2) SPATIAL LUMA from `last`: `dg in [-8..7]`, `dr - floor(dg/2) in [-4..3]`,
  `db - floor(dg/2) in [-4..3]`; cached.
- `1110nnnn` (`0xE0..0xEF`) LIT `n + 1` raw pixels (`<H` each), each cached.
- `11110000` (`0xF0`) LONG SKIP with `<H` count.
- `0xF1..0xF4` + `b2` (v2) TEMPORAL LUMA from `frame[pos]` (`ref[pos]`): same 10-bit luma-coupled delta
  applied to the reference pixel already sitting in-place at `frame[pos]`; cached.
- `0xF6..0xF7` + `<H` (v2) PIXEL LZ77 backward copy: `count = ((packed17 >> 11) & 0x3F) + 3` (`3..66` px),
  `dist = (packed17 & 0x7FF) + 1` (`1..2048` px) copied forward from `pos - dist`; last pixel cached.
- `0xF8..0xFF` (v2) TEMPORAL DIFF: 8 1-byte neutral/near-neutral `(dr, dg, db)` fades from `frame[pos]`.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from app.infrastructure.rendering.dmdz_motion import (
    DMDS_MAGIC,
    HEADER_FMT,
    MAX_SCROLL_COLS,
    MAX_SCROLL_ROWS,
    detect_scroll_1d as _detect_scroll,
    detect_scroll_2d,
    scrolled_reference,
    scrolled_reference_2d,
    stabilize_rgb565_stream_temporal_dither,
)

DMDZ_MAGIC = b"DMDZ"
DMDZ_VERSION = 1
DMDZ_V2_VERSION = 2
DMDZ_CODEC_ID = "dmdz1"
DMDZ_V2_CODEC_ID = "dmdz2"

FRAME_DELTA = 0
FRAME_SCROLL = 1
FRAME_KEY = 2
FRAME_SCROLL_2D = 3
FRAME_RAW = 4

TDIFF_VECTORS: Tuple[Tuple[int, int, int], ...] = (
    (-1, -2, -1),
    (-1, -1, -1),
    (0, -1, 0),
    (0, 1, 0),
    (1, 1, 1),
    (1, 2, 1),
    (0, -2, 0),
    (0, 2, 0),
)
_TDIFF_MAP: Dict[Tuple[int, int, int], int] = {vec: idx for idx, vec in enumerate(TDIFF_VECTORS)}


def _hash(p: int) -> int:
    return (((p >> 11) & 0x1F) * 3 + ((p >> 5) & 0x3F) * 5 + (p & 0x1F) * 7) & 63


def dmdz_sibling_path(raw_stream_path: Path, version: int = 1) -> Path:
    """
    `unified_128x32.rgb565.stream` ->
    - `version == 1`: `unified_128x32.dmdz.stream`
    - `version == 2`: `unified_128x32.dmdz2.stream`
    """
    name = raw_stream_path.name
    if name.endswith(".rgb565.stream"):
        name = name[: -len(".rgb565.stream")]
    suffix = ".dmdz2.stream" if int(version) >= 2 else ".dmdz.stream"
    return raw_stream_path.with_name(f"{name}{suffix}")


def _append_skip_ops(out: bytearray, run: int) -> None:
    while run > 0:
        if run <= 64:
            out.append(run - 1)
            return
        count = min(run, 0xFFFF)
        out.append(0xF0)
        out.append(count & 0xFF)
        out.append((count >> 8) & 0xFF)
        run -= count


def encode_frame_ops(
    cur: Union[Sequence[int], np.ndarray],
    ref: Optional[Union[Sequence[int], np.ndarray]],
) -> bytearray:
    """Encodes one frame's pixel stream (raster order) in DMDZ v1 op format."""
    cur_arr = np.ascontiguousarray(cur, dtype="<u2").ravel()
    n = int(cur_arr.size)
    out = bytearray()
    if n == 0:
        return out

    ref_arr: Optional[np.ndarray] = None
    diff_mv: Optional[memoryview] = None
    diff_len = 0
    if ref is not None:
        ref_arr = np.ascontiguousarray(ref, dtype="<u2").ravel()
        diff_idx = np.flatnonzero(cur_arr != ref_arr)
        diff_len = int(diff_idx.size)
        if diff_len == 0:
            _append_skip_ops(out, n)
            return out
        diff_mv = memoryview(diff_idx)

    cur_mv = memoryview(cur_arr)
    cache = [0] * 64
    last = 0
    lit: List[int] = []

    def flush_lit() -> None:
        if lit:
            out.append(0xE0 | (len(lit) - 1))
            for px in lit:
                out.append(px & 0xFF)
                out.append((px >> 8) & 0xFF)
            lit.clear()

    i = 0
    d_pos = 0
    while i < n:
        if diff_mv is not None:
            while d_pos < diff_len and diff_mv[d_pos] < i:
                d_pos += 1
            if d_pos >= diff_len:
                flush_lit()
                _append_skip_ops(out, n - i)
                return out
            next_diff = int(diff_mv[d_pos])
            if next_diff > i:
                flush_lit()
                _append_skip_ops(out, next_diff - i)
                last = cur_mv[next_diff - 1]
                cache[_hash(last)] = last
                i = next_diff
                continue
            d_pos += 1

        p = cur_mv[i]
        if p == last:
            j = i + 1
            max_j = min(n, i + 64)
            while j < max_j and cur_mv[j] == last:
                j += 1
            flush_lit()
            out.append(0x40 | (j - i - 1))
            i = j
            continue
        h = _hash(p)
        if cache[h] == p:
            flush_lit()
            out.append(0x80 | h)
            last = p
            i += 1
            continue
        dr = ((p >> 11) & 0x1F) - ((last >> 11) & 0x1F)
        dg = ((p >> 5) & 0x3F) - ((last >> 5) & 0x3F)
        db = (p & 0x1F) - (last & 0x1F)
        if -1 <= dr <= 1 and -1 <= dg <= 1 and -1 <= db <= 1:
            flush_lit()
            out.append(0xC0 | ((dr + 1) * 9 + (dg + 1) * 3 + (db + 1)))
        else:
            lit.append(p)
            if len(lit) == 16:
                flush_lit()
        cache[h] = p
        last = p
        i += 1
    flush_lit()
    return out


def encode_frame_ops_v2(
    cur: Union[Sequence[int], np.ndarray],
    ref: Optional[Union[Sequence[int], np.ndarray]],
) -> bytearray:
    """
    Encodes one frame's pixel stream (raster order) in DMDZ v2 op format:
    adds backward Pixel-LZ77 (`0xF6..0xF7`), 1-byte temporal `TDIFF` (`0xF8..0xFF`),
    2-byte spatial `OP_LUMA` (`0xDC..0xDF`), and 2-byte temporal `OP_TLUMA` (`0xF1..0xF4`).
    """
    cur_arr = np.ascontiguousarray(cur, dtype="<u2").ravel()
    n = int(cur_arr.size)
    out = bytearray()
    if n == 0:
        return out

    ref_mv: Optional[memoryview] = None
    diff_mv: Optional[memoryview] = None
    diff_len = 0
    if ref is not None:
        ref_arr = np.ascontiguousarray(ref, dtype="<u2").ravel()
        diff_idx = np.flatnonzero(cur_arr != ref_arr)
        diff_len = int(diff_idx.size)
        if diff_len == 0:
            _append_skip_ops(out, n)
            return out
        diff_mv = memoryview(diff_idx)
        ref_mv = memoryview(ref_arr)

    cur_mv = memoryview(cur_arr)
    pair_slots = [-1] * 1024
    cache = [0] * 64
    last = 0
    lit: List[int] = []

    def record_pair(pos_idx: int) -> None:
        if 0 <= pos_idx < n - 1:
            p0 = cur_mv[pos_idx]
            p1 = cur_mv[pos_idx + 1]
            pair_slots[((p0 * 40503) ^ (p1 * 257)) & 1023] = pos_idx

    def flush_lit() -> None:
        if lit:
            out.append(0xE0 | (len(lit) - 1))
            for px in lit:
                out.append(px & 0xFF)
                out.append((px >> 8) & 0xFF)
            lit.clear()

    def encode_luma_pair(pr: int, pg: int, pb: int, base_p: int, base_op: int) -> Optional[Tuple[int, int]]:
        dr = pr - ((base_p >> 11) & 0x1F)
        dg = pg - ((base_p >> 5) & 0x3F)
        db = pb - (base_p & 0x1F)
        if -8 <= dg <= 7:
            dg_rb = dg >> 1
            dr_dg = dr - dg_rb
            db_dg = db - dg_rb
            if -4 <= dr_dg <= 3 and -4 <= db_dg <= 3:
                u_dg = dg + 8
                return (
                    base_op + (u_dg >> 2),
                    ((u_dg & 0x03) << 6) | ((dr_dg + 4) << 3) | (db_dg + 4),
                )
        return None

    i = 0
    d_pos = 0
    while i < n:
        if diff_mv is not None:
            while d_pos < diff_len and diff_mv[d_pos] < i:
                d_pos += 1
            if d_pos >= diff_len:
                flush_lit()
                _append_skip_ops(out, n - i)
                return out
            next_diff = int(diff_mv[d_pos])
            if next_diff > i:
                flush_lit()
                _append_skip_ops(out, next_diff - i)
                record_pair(i)
                if next_diff >= 2:
                    record_pair(next_diff - 2)
                record_pair(next_diff - 1)
                last = cur_mv[next_diff - 1]
                cache[_hash(last)] = last
                i = next_diff
                continue
            d_pos += 1

        p = cur_mv[i]
        if p == last:
            j = i + 1
            max_j = min(n, i + 64)
            while j < max_j and cur_mv[j] == last:
                j += 1
            flush_lit()
            out.append(0x40 | (j - i - 1))
            record_pair(i)
            record_pair(j - 1)
            i = j
            continue

        # Backward Pixel-LZ77 match (`3..66` pixels within `1..2048` backward distance -> 3 bytes)
        if i + 3 <= n:
            p1 = cur_mv[i + 1]
            slot = ((p * 40503) ^ (p1 * 257)) & 1023
            cand = pair_slots[slot]
            if (
                cand >= 0
                and 1 <= (i - cand) <= 2048
                and cur_mv[cand] == p
                and cur_mv[cand + 1] == p1
                and cur_mv[cand + 2] == cur_mv[i + 2]
            ):
                max_len = min(66, n - i)
                mlen = 3
                while mlen < max_len and cur_mv[cand + mlen] == cur_mv[i + mlen]:
                    mlen += 1
                flush_lit()
                packed17 = ((mlen - 3) << 11) | ((i - cand) - 1)
                out.append(0xF6 | ((packed17 >> 16) & 0x01))
                out.append(packed17 & 0xFF)
                out.append((packed17 >> 8) & 0xFF)
                for k in range(i, i + mlen):
                    record_pair(k)
                last = cur_mv[i + mlen - 1]
                cache[_hash(last)] = last
                i += mlen
                continue

        h = _hash(p)
        pr = (p >> 11) & 0x1F
        pg = (p >> 5) & 0x3F
        pb = p & 0x1F

        one_byte_op = -1
        if cache[h] == p:
            one_byte_op = 0x80 | h
        else:
            dr = pr - ((last >> 11) & 0x1F)
            dg = pg - ((last >> 5) & 0x3F)
            db = pb - (last & 0x1F)
            if -1 <= dr <= 1 and -1 <= dg <= 1 and -1 <= db <= 1:
                one_byte_op = 0xC0 | ((dr + 1) * 9 + (dg + 1) * 3 + (db + 1))
            elif ref_mv is not None:
                rp = ref_mv[i]
                t_idx = _TDIFF_MAP.get(
                    (pr - ((rp >> 11) & 0x1F), pg - ((rp >> 5) & 0x3F), pb - (rp & 0x1F))
                )
                if t_idx is not None:
                    one_byte_op = 0xF8 | t_idx

        if one_byte_op >= 0:
            flush_lit()
            out.append(one_byte_op)
            record_pair(i)
            cache[h] = p
            last = p
            i += 1
            continue

        # 2-byte LUMA / TLUMA: when `not lit`, `2 B` is ALWAYS `<= LIT` (never pays a split-header penalty)
        if not lit:
            luma_pair = encode_luma_pair(pr, pg, pb, last, 0xDC)
            if luma_pair is None and ref_mv is not None:
                luma_pair = encode_luma_pair(pr, pg, pb, ref_mv[i], 0xF1)
            if luma_pair is not None:
                out.append(luma_pair[0])
                out.append(luma_pair[1])
                record_pair(i)
                cache[h] = p
                last = p
                i += 1
                continue

        lit.append(p)
        if len(lit) == 16:
            flush_lit()
        record_pair(i)
        cache[h] = p
        last = p
        i += 1

    flush_lit()
    return out


def encode_dmdz_frames(
    frames: np.ndarray,
    keyframe_interval: int = 0,
    version: int = 1,
) -> List[bytes]:
    """Encodes `(n, h, w)` uint16 RGB565 frames into DMDZ frame payloads (without length prefix)."""
    use_v2 = int(version) >= 2
    payloads: List[bytes] = []
    prev: Optional[np.ndarray] = None
    for k in range(frames.shape[0]):
        cur = frames[k]
        is_key = prev is None or (keyframe_interval > 0 and k % keyframe_interval == 0)
        if not use_v2:
            if is_key:
                payload = bytes([FRAME_KEY]) + bytes(encode_frame_ops(cur, None))
            else:
                dy = _detect_scroll(cur, prev)
                if dy is not None:
                    ref = scrolled_reference(prev, dy)
                    payload = bytes([FRAME_SCROLL, dy & 0xFF]) + bytes(encode_frame_ops(cur, ref))
                else:
                    payload = bytes([FRAME_DELTA]) + bytes(encode_frame_ops(cur, prev))
        else:
            raw_fallback = bytes([FRAME_RAW]) + np.ascontiguousarray(cur, dtype="<u2").tobytes()
            if is_key:
                cand_payload = bytes([FRAME_KEY]) + bytes(encode_frame_ops_v2(cur, None))
            else:
                assert prev is not None
                dx, dy = detect_scroll_2d(cur, prev, tol_mode=False)
                if dx == 0 and dy == 0:
                    cand_payload = bytes([FRAME_DELTA]) + bytes(encode_frame_ops_v2(cur, prev))
                elif dx == 0:
                    ref = scrolled_reference(prev, dy)
                    cand_payload = bytes([FRAME_SCROLL, dy & 0xFF]) + bytes(encode_frame_ops_v2(cur, ref))
                else:
                    ref = scrolled_reference_2d(prev, dx, dy)
                    cand_payload = bytes([FRAME_SCROLL_2D, dx & 0xFF, dy & 0xFF]) + bytes(
                        encode_frame_ops_v2(cur, ref)
                    )
            payload = cand_payload if len(cand_payload) <= len(raw_fallback) else raw_fallback

        if len(payload) > 0xFFFF:
            raise ValueError("DMDZ frame payload exceeds 65535 bytes")
        payloads.append(payload)
        prev = cur
    return payloads


def encode_dmdz_bytes(raw: bytes, keyframe_interval: int = 0, version: int = 1) -> bytes:
    """Converts a raw `DMDS` stream (16-byte header + RGB565 frames) to a `DMDZ` v1 or v2 stream."""
    if len(raw) < 16 or raw[:4] != DMDS_MAGIC:
        raise ValueError("Not a DMDS stream")
    eff_ver = DMDZ_V2_VERSION if int(version) >= 2 else DMDZ_VERSION
    _, _ver, fps, width, height, total, frame_dur_ms, _res = struct.unpack(HEADER_FMT, raw[:16])
    frame_bytes = int(width) * int(height) * 2
    if frame_bytes <= 0:
        raise ValueError("Invalid DMDS geometry")
    avail = (len(raw) - 16) // frame_bytes
    count = min(int(total), avail) if total else avail
    frames = np.frombuffer(raw[16 : 16 + count * frame_bytes], dtype="<u2").reshape(count, height, width)
    payloads = encode_dmdz_frames(frames, keyframe_interval=keyframe_interval, version=eff_ver)
    out = bytearray(
        struct.pack(HEADER_FMT, DMDZ_MAGIC, eff_ver, fps, width, height, count, frame_dur_ms, keyframe_interval)
    )
    for payload in payloads:
        out += struct.pack("<H", len(payload))
        out += payload
    return bytes(out)


def encode_dmdz_file(
    raw_stream_path: Path,
    dest_path: Optional[Path] = None,
    version: int = 1,
) -> Path:
    """Writes the DMDZ v1 or v2 sibling of a raw DMDS stream atomically and returns its path."""
    eff_ver = DMDZ_V2_VERSION if int(version) >= 2 else DMDZ_VERSION
    dest = dest_path or dmdz_sibling_path(raw_stream_path, version=eff_ver)
    data = encode_dmdz_bytes(raw_stream_path.read_bytes(), version=eff_ver)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def ensure_dmdz_sibling(raw_stream_path: Path, version: int = 1) -> Optional[Path]:
    """Returns an up-to-date DMDZ sibling (`v1` or `v2`), encoding on demand; `None` on error."""
    try:
        eff_ver = DMDZ_V2_VERSION if int(version) >= 2 else DMDZ_VERSION
        dest = dmdz_sibling_path(raw_stream_path, version=eff_ver)
        if dest.exists() and dest.stat().st_mtime >= raw_stream_path.stat().st_mtime:
            return dest
        return encode_dmdz_file(raw_stream_path, dest, version=eff_ver)
    except (OSError, ValueError):
        return None


def decode_dmdz_bytes(data: bytes) -> np.ndarray:
    """Reference decoder (mirrors the ESP32 `DmdzStreamDecoder` v1 & v2 byte for byte)."""
    if len(data) < 16 or data[:4] != DMDZ_MAGIC:
        raise ValueError("Not a DMDZ stream")
    _, ver, _fps, width, height, total, _dur, _kf = struct.unpack(HEADER_FMT, data[:16])
    if ver not in (DMDZ_VERSION, DMDZ_V2_VERSION):
        raise ValueError(f"Unsupported DMDZ version {ver}")
    is_v2 = ver == DMDZ_V2_VERSION
    npx = int(width) * int(height)
    frame = [0] * npx
    frames: List[np.ndarray] = []
    off = 16
    for _ in range(int(total)):
        (length,) = struct.unpack_from("<H", data, off)
        off += 2
        end = off + length
        if length == 0 or end > len(data):
            raise ValueError("DMDZ truncated frame")
        ftype = data[off]
        off += 1
        if ftype == FRAME_RAW:
            if not is_v2 or (end - off) != npx * 2:
                raise ValueError("Invalid DMDZ FRAME_RAW")
            frame = np.frombuffer(data[off:end], dtype="<u2").astype(np.uint16).tolist()
            off = end
            frames.append(np.array(frame, dtype=np.uint16).reshape(height, width))
            continue
        if ftype == FRAME_SCROLL:
            dy = struct.unpack_from("<b", data, off)[0]
            off += 1
            if abs(dy) >= height:
                raise ValueError("DMDZ scroll dy overflow")
            arr = np.array(frame, dtype=np.uint16).reshape(height, width)
            frame = scrolled_reference(arr, dy).ravel().tolist()
        elif ftype == FRAME_SCROLL_2D:
            if not is_v2 or (off + 2) > end:
                raise ValueError("Invalid DMDZ FRAME_SCROLL_2D")
            dx, dy = struct.unpack_from("<bb", data, off)
            off += 2
            if abs(dx) >= width or abs(dy) >= height:
                raise ValueError("DMDZ scroll 2D overflow")
            arr = np.array(frame, dtype=np.uint16).reshape(height, width)
            frame = scrolled_reference_2d(arr, dx, dy).ravel().tolist()
        elif ftype not in (FRAME_DELTA, FRAME_KEY):
            raise ValueError(f"Unknown DMDZ frame type {ftype}")

        cache = [0] * 64
        last = 0
        pos = 0
        while off < end:
            b = data[off]
            off += 1
            if b < 0x40 or b == 0xF0:
                if b == 0xF0:
                    (count,) = struct.unpack_from("<H", data, off)
                    off += 2
                else:
                    count = b + 1
                pos += count
                if pos > npx or count == 0:
                    raise ValueError("DMDZ skip overflow")
                last = frame[pos - 1]
                cache[_hash(last)] = last
            elif b < 0x80:
                count = (b & 0x3F) + 1
                if pos + count > npx:
                    raise ValueError("DMDZ run overflow")
                for _k in range(count):
                    frame[pos] = last
                    pos += 1
            elif b < 0xC0:
                if pos >= npx:
                    raise ValueError("DMDZ index overflow")
                last = cache[b & 0x3F]
                frame[pos] = last
                pos += 1
            elif b <= 0xDA:
                if pos >= npx:
                    raise ValueError("DMDZ diff overflow")
                x = b & 0x1F
                r = ((last >> 11) & 0x1F) + (x // 9 - 1)
                g = ((last >> 5) & 0x3F) + ((x // 3) % 3 - 1)
                bl = (last & 0x1F) + (x % 3 - 1)
                p = ((r & 0x1F) << 11) | ((g & 0x3F) << 5) | (bl & 0x1F)
                frame[pos] = p
                pos += 1
                cache[_hash(p)] = p
                last = p
            elif 0xDC <= b <= 0xDF:
                if not is_v2 or off >= end or pos >= npx:
                    raise ValueError("DMDZ spatial luma invalid")
                b2 = data[off]
                off += 1
                dg = (((b & 0x03) << 2) | (b2 >> 6)) - 8
                dg_rb = dg >> 1
                dr = dg_rb + (((b2 >> 3) & 0x07) - 4)
                db = dg_rb + ((b2 & 0x07) - 4)
                r = ((last >> 11) & 0x1F) + dr
                g = ((last >> 5) & 0x3F) + dg
                bl = (last & 0x1F) + db
                p = ((r & 0x1F) << 11) | ((g & 0x3F) << 5) | (bl & 0x1F)
                frame[pos] = p
                pos += 1
                cache[_hash(p)] = p
                last = p
            elif 0xE0 <= b <= 0xEF:
                count = (b & 0x0F) + 1
                if pos + count > npx or (off + count * 2) > end:
                    raise ValueError("DMDZ lit overflow")
                for _k in range(count):
                    (p,) = struct.unpack_from("<H", data, off)
                    off += 2
                    frame[pos] = p
                    pos += 1
                    cache[_hash(p)] = p
                    last = p
            elif 0xF1 <= b <= 0xF4:
                if not is_v2 or off >= end or pos >= npx:
                    raise ValueError("DMDZ temporal luma invalid")
                b2 = data[off]
                off += 1
                ref_p = frame[pos]
                dg = (((b - 0xF1) << 2) | (b2 >> 6)) - 8
                dg_rb = dg >> 1
                dr = dg_rb + (((b2 >> 3) & 0x07) - 4)
                db = dg_rb + ((b2 & 0x07) - 4)
                r = ((ref_p >> 11) & 0x1F) + dr
                g = ((ref_p >> 5) & 0x3F) + dg
                bl = (ref_p & 0x1F) + db
                p = ((r & 0x1F) << 11) | ((g & 0x3F) << 5) | (bl & 0x1F)
                frame[pos] = p
                pos += 1
                cache[_hash(p)] = p
                last = p
            elif b in (0xF6, 0xF7):
                if not is_v2 or (off + 2) > end:
                    raise ValueError("DMDZ lz invalid")
                (lo16,) = struct.unpack_from("<H", data, off)
                off += 2
                packed17 = ((b & 0x01) << 16) | lo16
                count = ((packed17 >> 11) & 0x3F) + 3
                dist = (packed17 & 0x7FF) + 1
                if dist > pos or (pos + count) > npx:
                    raise ValueError("DMDZ lz bounds overflow")
                src_idx = pos - dist
                for k in range(count):
                    frame[pos] = frame[src_idx + k]
                    pos += 1
                last = frame[pos - 1]
                cache[_hash(last)] = last
            elif 0xF8 <= b <= 0xFF:
                if not is_v2 or pos >= npx:
                    raise ValueError("DMDZ temporal diff invalid")
                dr, dg, db = TDIFF_VECTORS[b & 0x07]
                ref_p = frame[pos]
                r = ((ref_p >> 11) & 0x1F) + dr
                g = ((ref_p >> 5) & 0x3F) + dg
                bl = (ref_p & 0x1F) + db
                p = ((r & 0x1F) << 11) | ((g & 0x3F) << 5) | (bl & 0x1F)
                frame[pos] = p
                pos += 1
                cache[_hash(p)] = p
                last = p
            else:
                raise ValueError(f"Unknown DMDZ op 0x{b:02x}")
        if pos != npx or off != end:
            raise ValueError("DMDZ frame size mismatch")
        frames.append(np.array(frame, dtype=np.uint16).reshape(height, width))
    return np.stack(frames) if frames else np.zeros((0, height, width), dtype=np.uint16)
