"""
DMDZ v1: lossless, in-place decodable RGB565 frame codec for memory-constrained DMD clients.

Embedded clients (classic ESP32 without PSRAM) run with a fixed ~5.7 KB TCP receive window, smaller
than one raw 128x32 RGB565 frame (8 KB). Raw DMDS throughput is therefore capped at `window / RTT`.
DMDZ shrinks real Showcase streams 2.4-6x (videos) and 23-64x (panoramas) without any loss, so
several frames fit in the window, which doubles as a zero-RAM jitter buffer on the client.

### Container (Little-Endian)
- `0..15`: header `<4sBBHHHHH` = `"DMDZ"`, `version=1`, `fps`, `width`, `height`, `total_frames`,
  `frame_dur_ms`, `keyframe_interval` (`0` = only frame 0 is a keyframe).
- Per frame: `<H` payload length `L`, then `L` bytes: `type` (`0` delta, `1` vertical scroll + delta,
  `2` keyframe), `dy` (`int8`, only for `type == 1`), then the op stream covering exactly
  `width * height` pixels in raster order.

### Vertical scroll reference (`type == 1`)
`dy > 0` moves content up: rows `[0, h - dy)` take previous rows `[dy, h)`; rows `[h - dy, h)` keep
their previous content. `dy < 0` moves content down symmetrically. This is exactly an in-place
`memmove` on the client frame buffer.

### Ops (state reset at each frame: `last = 0`, `cache[64] = 0`)
- `00nnnnnn` SKIP `n + 1`: pixels equal to the reference; then `last = frame[pos - 1]`, cached.
- `01nnnnnn` RUN `n + 1`: repeat `last`.
- `10iiiiii` INDEX: `cache[i]`.
- `110xxxxx` DIFF: `x = (dr + 1) * 9 + (dg + 1) * 3 + (db + 1)` with `dr, dg, db` in `{-1, 0, 1}`
  applied to `last`'s R5/G6/B5 components; result cached.
- `1110nnnn` LIT `n + 1` raw pixels (`<H` each), each cached.
- `11110000` LONG SKIP with `<H` count.

`hash(p) = (r * 3 + g * 5 + b * 7) & 63` on the R5/G6/B5 components.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

DMDZ_MAGIC = b"DMDZ"
DMDS_MAGIC = b"DMDS"
DMDZ_VERSION = 1
DMDZ_CODEC_ID = "dmdz1"
HEADER_FMT = "<4sBBHHHHH"
MAX_SCROLL_ROWS = 8

FRAME_DELTA = 0
FRAME_SCROLL = 1
FRAME_KEY = 2


def _hash(p: int) -> int:
    return (((p >> 11) & 0x1F) * 3 + ((p >> 5) & 0x3F) * 5 + (p & 0x1F) * 7) & 63


def dmdz_sibling_path(raw_stream_path: Path) -> Path:
    """`unified_128x32.rgb565.stream` -> `unified_128x32.dmdz.stream` (same package directory)."""
    name = raw_stream_path.name
    if name.endswith(".rgb565.stream"):
        name = name[: -len(".rgb565.stream")]
    return raw_stream_path.with_name(f"{name}.dmdz.stream")


def scrolled_reference(prev: np.ndarray, dy: int) -> np.ndarray:
    """Reference frame after the client's in-place `memmove` vertical scroll."""
    ref = prev.copy()
    h = prev.shape[0]
    if dy > 0:
        ref[: h - dy] = prev[dy:]
    elif dy < 0:
        ref[-dy:] = prev[: h + dy]
    return ref


def _detect_scroll(cur: np.ndarray, prev: np.ndarray) -> Optional[int]:
    h = cur.shape[0]
    for mag in range(1, min(MAX_SCROLL_ROWS, h - 1) + 1):
        for dy in (mag, -mag):
            if dy > 0:
                if np.array_equal(cur[: h - dy], prev[dy:]):
                    return dy
            elif np.array_equal(cur[-dy:], prev[: h + dy]):
                return dy
    return None


def encode_frame_ops(cur: Sequence[int], ref: Optional[Sequence[int]]) -> bytearray:
    """Encodes one frame's pixel stream (raster order) against an optional reference."""
    out = bytearray()
    cache = [0] * 64
    last = 0
    lit: List[int] = []
    n = len(cur)

    def flush_lit() -> None:
        if lit:
            out.append(0xE0 | (len(lit) - 1))
            for px in lit:
                out.append(px & 0xFF)
                out.append((px >> 8) & 0xFF)
            lit.clear()

    i = 0
    while i < n:
        p = cur[i]
        if ref is not None and p == ref[i]:
            j = i + 1
            while j < n and cur[j] == ref[j]:
                j += 1
            flush_lit()
            run = j - i
            while run > 0:
                if run <= 64:
                    out.append(run - 1)
                    run = 0
                else:
                    count = min(run, 0xFFFF)
                    out.append(0xF0)
                    out.append(count & 0xFF)
                    out.append((count >> 8) & 0xFF)
                    run -= count
            last = cur[j - 1]
            cache[_hash(last)] = last
            i = j
            continue
        if p == last:
            j = i + 1
            while j < n and cur[j] == last and (j - i) < 64:
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


def encode_dmdz_frames(frames: np.ndarray, keyframe_interval: int = 0) -> List[bytes]:
    """Encodes `(n, h, w)` uint16 RGB565 frames into DMDZ frame payloads (without length prefix)."""
    payloads: List[bytes] = []
    prev: Optional[np.ndarray] = None
    for k in range(frames.shape[0]):
        cur = frames[k]
        cur_list = cur.ravel().tolist()
        is_key = prev is None or (keyframe_interval > 0 and k % keyframe_interval == 0)
        if is_key:
            payload = bytes([FRAME_KEY]) + bytes(encode_frame_ops(cur_list, None))
        else:
            dy = _detect_scroll(cur, prev)
            if dy is not None:
                ref = scrolled_reference(prev, dy)
                payload = bytes([FRAME_SCROLL, dy & 0xFF]) + bytes(
                    encode_frame_ops(cur_list, ref.ravel().tolist())
                )
            else:
                payload = bytes([FRAME_DELTA]) + bytes(
                    encode_frame_ops(cur_list, prev.ravel().tolist())
                )
        if len(payload) > 0xFFFF:
            raise ValueError("DMDZ frame payload exceeds 65535 bytes")
        payloads.append(payload)
        prev = cur
    return payloads


def encode_dmdz_bytes(raw: bytes, keyframe_interval: int = 0) -> bytes:
    """Converts a raw `DMDS` stream (16-byte header + RGB565 frames) to a `DMDZ` stream."""
    if len(raw) < 16 or raw[:4] != DMDS_MAGIC:
        raise ValueError("Not a DMDS stream")
    _, _ver, fps, width, height, total, frame_dur_ms, _res = struct.unpack(HEADER_FMT, raw[:16])
    frame_bytes = int(width) * int(height) * 2
    if frame_bytes <= 0:
        raise ValueError("Invalid DMDS geometry")
    avail = (len(raw) - 16) // frame_bytes
    count = min(int(total), avail) if total else avail
    frames = np.frombuffer(raw[16 : 16 + count * frame_bytes], dtype="<u2").reshape(count, height, width)
    payloads = encode_dmdz_frames(frames, keyframe_interval)
    out = bytearray(
        struct.pack(HEADER_FMT, DMDZ_MAGIC, DMDZ_VERSION, fps, width, height, count, frame_dur_ms, keyframe_interval)
    )
    for payload in payloads:
        out += struct.pack("<H", len(payload))
        out += payload
    return bytes(out)


def encode_dmdz_file(raw_stream_path: Path, dest_path: Optional[Path] = None) -> Path:
    """Writes the DMDZ sibling of a raw DMDS stream atomically and returns its path."""
    dest = dest_path or dmdz_sibling_path(raw_stream_path)
    data = encode_dmdz_bytes(raw_stream_path.read_bytes())
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def ensure_dmdz_sibling(raw_stream_path: Path) -> Optional[Path]:
    """Returns an up-to-date DMDZ sibling, encoding it on demand; `None` when encoding is impossible."""
    try:
        dest = dmdz_sibling_path(raw_stream_path)
        if dest.exists() and dest.stat().st_mtime >= raw_stream_path.stat().st_mtime:
            return dest
        return encode_dmdz_file(raw_stream_path, dest)
    except (OSError, ValueError):
        return None


def decode_dmdz_bytes(data: bytes) -> np.ndarray:
    """Reference decoder (mirrors the ESP32 `DmdzStreamDecoder` byte for byte)."""
    if len(data) < 16 or data[:4] != DMDZ_MAGIC:
        raise ValueError("Not a DMDZ stream")
    _, _ver, _fps, width, height, total, _dur, _kf = struct.unpack(HEADER_FMT, data[:16])
    npx = int(width) * int(height)
    frame = [0] * npx
    frames: List[np.ndarray] = []
    off = 16
    for _ in range(int(total)):
        (length,) = struct.unpack_from("<H", data, off)
        off += 2
        end = off + length
        ftype = data[off]
        off += 1
        if ftype == FRAME_SCROLL:
            dy = struct.unpack_from("<b", data, off)[0]
            off += 1
            arr = np.array(frame, dtype=np.uint16).reshape(height, width)
            frame = scrolled_reference(arr, dy).ravel().tolist()
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
                last = cache[b & 0x3F]
                frame[pos] = last
                pos += 1
            elif b < 0xE0:
                x = b & 0x1F
                if x > 26:
                    raise ValueError("DMDZ invalid diff")
                r = ((last >> 11) & 0x1F) + (x // 9 - 1)
                g = ((last >> 5) & 0x3F) + ((x // 3) % 3 - 1)
                bl = (last & 0x1F) + (x % 3 - 1)
                p = ((r & 0x1F) << 11) | ((g & 0x3F) << 5) | (bl & 0x1F)
                frame[pos] = p
                pos += 1
                cache[_hash(p)] = p
                last = p
            elif b < 0xF0:
                count = (b & 0x0F) + 1
                for _k in range(count):
                    (p,) = struct.unpack_from("<H", data, off)
                    off += 2
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

