"""
Zero-RAM Streaming PKZIP (`ZIP_STORED`) Writer (`app/infrastructure/persistence/zip_stream_writer.py`).

Provides universal browser fallback when `window.showDirectoryPicker` is unavailable (e.g., Firefox,
Safari, or plain non-localhost HTTP without HTTPS): streams completed `.gif` files, `index.txt`, and
`playlists.json` directly into a live HTTP `.zip` download in `64 KB` chunks (`0` compression CPU/RAM
since GIFs are already LZW-compressed) and unlinks each staged file immediately after streaming it!
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct
import time
from typing import Dict, Iterator, List, Tuple
import zlib


@dataclass
class _ZipCentralRecord:
    arcname_bytes: bytes
    crc32: int
    file_size: int
    header_offset: int
    dos_time: int
    dos_date: int
    is_dir: bool = False


def _current_dos_datetime() -> Tuple[int, int]:
    lt = time.localtime()
    yr = max(1980, min(2107, lt.tm_year)) - 1980
    dos_date = (yr << 9) | (lt.tm_mon << 5) | lt.tm_mday
    dos_time = (lt.tm_hour << 11) | (lt.tm_min << 5) | (lt.tm_sec // 2)
    return dos_time, dos_date


def _compute_file_crc32(path: Path, chunk_size: int = 65536) -> Tuple[int, int]:
    crc = 0
    total = 0
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            crc = zlib.crc32(buf, crc)
            total += len(buf)
    return crc & 0xFFFFFFFF, total


class StreamingZipArchiveBuilder:
    """Stateful O(1)-RAM `ZIP_STORED` byte stream emitter."""

    def __init__(self) -> None:
        self._offset: int = 0
        self._records: List[_ZipCentralRecord] = []
        self._seen_dirs: set[str] = set()
        self._playlist_files: Dict[str, List[str]] = {}

    def emit_directory(self, rel_dir: str) -> bytes:
        clean = (rel_dir or "").replace("\\", "/").strip("/")
        if not clean:
            return b""
        out = bytearray()
        parts = clean.split("/")
        for idx in range(1, len(parts) + 1):
            sub = "/".join(parts[:idx]) + "/"
            if sub in self._seen_dirs:
                continue
            self._seen_dirs.add(sub)
            name_b = sub.encode("utf-8")
            dos_time, dos_date = _current_dos_datetime()
            # Local File Header (`PK\x03\x04`), UTF-8 flag `0x0800`, method `0` (STORE)
            lfh = struct.pack(
                "<4sHHHHHIIIHH",
                b"PK\x03\x04",
                20,
                0x0800,
                0,
                dos_time,
                dos_date,
                0,
                0,
                0,
                len(name_b),
                0,
            ) + name_b
            self._records.append(
                _ZipCentralRecord(
                    arcname_bytes=name_b,
                    crc32=0,
                    file_size=0,
                    header_offset=self._offset,
                    dos_time=dos_time,
                    dos_date=dos_date,
                    is_dir=True,
                )
            )
            self._offset += len(lfh)
            out.extend(lfh)
        return bytes(out)

    def iter_stored_file_chunks(
        self,
        arc_relpath: str,
        src_path: Path,
        record_for_clock_index: bool = False,
        chunk_size: int = 65536,
    ) -> Iterator[bytes]:
        norm_arc = arc_relpath.replace("\\", "/").lstrip("/")
        parent_dir = norm_arc.rsplit("/", 1)[0] if "/" in norm_arc else ""
        if parent_dir:
            dir_hdr = self.emit_directory(parent_dir)
            if dir_hdr:
                yield dir_hdr

        crc, fsize = _compute_file_crc32(src_path, chunk_size=chunk_size)
        name_b = norm_arc.encode("utf-8")
        dos_time, dos_date = _current_dos_datetime()
        lfh = struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04",
            20,
            0x0800,
            0,
            dos_time,
            dos_date,
            crc,
            fsize,
            fsize,
            len(name_b),
            0,
        ) + name_b
        self._records.append(
            _ZipCentralRecord(
                arcname_bytes=name_b,
                crc32=crc,
                file_size=fsize,
                header_offset=self._offset,
                dos_time=dos_time,
                dos_date=dos_date,
                is_dir=False,
            )
        )
        self._offset += len(lfh)
        yield lfh

        with src_path.open("rb") as f:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                self._offset += len(buf)
                yield buf

        if record_for_clock_index and parent_dir:
            leaf_file = norm_arc.rsplit("/", 1)[-1]
            self._playlist_files.setdefault(parent_dir, []).append(leaf_file)

    def emit_bytes_entry(self, arc_relpath: str, data: bytes) -> bytes:
        norm_arc = arc_relpath.replace("\\", "/").lstrip("/")
        parent_dir = norm_arc.rsplit("/", 1)[0] if "/" in norm_arc else ""
        prefix = self.emit_directory(parent_dir) if parent_dir else b""
        crc = zlib.crc32(data) & 0xFFFFFFFF
        fsize = len(data)
        name_b = norm_arc.encode("utf-8")
        dos_time, dos_date = _current_dos_datetime()
        lfh = struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04",
            20,
            0x0800,
            0,
            dos_time,
            dos_date,
            crc,
            fsize,
            fsize,
            len(name_b),
            0,
        ) + name_b
        self._records.append(
            _ZipCentralRecord(
                arcname_bytes=name_b,
                crc32=crc,
                file_size=fsize,
                header_offset=self._offset,
                dos_time=dos_time,
                dos_date=dos_date,
                is_dir=False,
            )
        )
        self._offset += len(lfh) + fsize
        return prefix + lfh + data

    def emit_clock_manifests_if_any(self, is_tate: bool = False) -> bytes:
        if not self._playlist_files:
            return b""
        out = bytearray()
        playlists_manifest: Dict[str, Dict[str, object]] = {}
        root_prefix = "gifs_tate" if is_tate else "gifs"
        for folder_rel, files in sorted(self._playlist_files.items()):
            unique_files = sorted(set(files))
            index_txt = ("\n".join(unique_files) + "\n").encode("utf-8")
            out.extend(self.emit_bytes_entry(f"{folder_rel}/index.txt", index_txt))
            leaf_folder = folder_rel.split("/")[-1]
            playlists_manifest[leaf_folder] = {
                "path": f"/{root_prefix}/{leaf_folder}",
                "count": len(unique_files),
            }
        manifest_rel = (
            f"{root_prefix}/playlists.json"
            if any(k.startswith(f"{root_prefix}/") for k in self._playlist_files)
            else "playlists.json"
        )
        manifest_bytes = (json.dumps(playlists_manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        out.extend(self.emit_bytes_entry(manifest_rel, manifest_bytes))
        return bytes(out)

    def finalize_central_directory(self) -> bytes:
        cd_start = self._offset
        cd_buf = bytearray()
        for rec in self._records:
            ext_attr = (0o40755 << 16) | 0x10 if rec.is_dir else (0o100644 << 16)
            cdfh = struct.pack(
                "<4sHHHHHHIIIHHHHHII",
                b"PK\x01\x02",
                20,
                20,
                0x0800,
                0,
                rec.dos_time,
                rec.dos_date,
                rec.crc32,
                rec.file_size,
                rec.file_size,
                len(rec.arcname_bytes),
                0,
                0,
                0,
                0,
                ext_attr,
                min(0xFFFFFFFF, rec.header_offset),
            ) + rec.arcname_bytes
            cd_buf.extend(cdfh)

        cd_size = len(cd_buf)
        count = min(0xFFFF, len(self._records))
        eocd = struct.pack(
            "<4sHHHHIIH",
            b"PK\x05\x06",
            0,
            0,
            count,
            count,
            min(0xFFFFFFFF, cd_size),
            min(0xFFFFFFFF, cd_start),
            0,
        )
        self._offset += cd_size + len(eocd)
        return bytes(cd_buf) + eocd
