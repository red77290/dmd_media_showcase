#!/usr/bin/env python3
"""
Deterministic Offline Media & Marquee Fixture Generator for the E2E Testbench.

Generates 100% local, zero-internet test assets with distinct quadrant RGB signatures:
1. `dmd_media_showcase` offline `catalog.json` + `poster.png`, `jacket.png`, `marquee.png`, and `gameplay.mp4`
   for `neogeo/mslug`, `snes/smw`, and `megadrive/sonic`.
2. `128x32` Pixelcade/Arcade marquees in `.bmp` (24-bit uncompressed), `.png`, and `.gif` (2-frame animated)
   across `ArcadeMatrix` ESP32 and `ArcadeMatrix_RPi` virtual SD/marquee layouts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Tuple

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

Color = Tuple[int, int, int]

# Distinct RGB signatures (survive RGB888 -> RGB565 quantization unambiguously)
GAME_SIGNATURES = {
    "mslug": {
        "system": "neogeo",
        "pixelcade_folder": "neogeo",
        "title": "Metal Slug",
        "year": "1996",
        "rating": "8.9",
        "publisher": "SNK",
        "developer": "Nazca",
        "genres": ["Run and Gun", "Arcade"],
        "synopsis": "Classic run-and-gun action across six explosive missions on Neo Geo MVS.",
        "quad_tl": (255, 0, 0),      # Red
        "quad_tr": (0, 255, 0),      # Green
        "quad_bot": (0, 80, 255),    # Royal Blue
        "video_bg": "navy",
        "video_box": "yellow",
    },
    "smw": {
        "system": "snes",
        "pixelcade_folder": "snes",
        "title": "Super Mario World",
        "year": "1990",
        "rating": "9.4",
        "publisher": "Nintendo",
        "developer": "Nintendo EAD",
        "genres": ["Platformer"],
        "synopsis": "Explore Dinosaur Land with Yoshi in Nintendo's flagship 16-bit adventure.",
        "quad_tl": (255, 255, 0),    # Yellow
        "quad_tr": (0, 255, 255),    # Cyan
        "quad_bot": (255, 0, 255),   # Magenta
        "video_bg": "darkgreen",
        "video_box": "red",
    },
    "sonic": {
        "system": "megadrive",
        "pixelcade_folder": "genesis",
        "title": "Sonic the Hedgehog",
        "year": "1991",
        "rating": "9.0",
        "publisher": "Sega",
        "developer": "Sonic Team",
        "genres": ["Platformer", "Action"],
        "synopsis": "Blast through Green Hill Zone at supersonic speed to stop Dr. Robotnik.",
        "quad_tl": (0, 120, 255),    # Sonic Blue
        "quad_tr": (255, 255, 255),  # White
        "quad_bot": (0, 220, 80),    # Emerald
        "video_bg": "darkblue",
        "video_box": "cyan",
    },
}

SYSTEM_SIGNATURES = {
    "neogeo": ((255, 140, 0), (255, 255, 255)),
    "snes": ((140, 0, 255), (255, 220, 0)),
    "megadrive": ((0, 80, 255), (255, 200, 0)),
    "genesis": ((0, 80, 255), (255, 200, 0)),
}


def write_bmp24_128x32(path: Path, tl: Color, tr: Color, bot: Color) -> None:
    """Writes a standard 54-byte header 24-bit bottom-up uncompressed 128x32 BMP."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 128, 32
    row_bytes = w * 3
    img_size = row_bytes * h
    file_size = 54 + img_size
    header = struct.pack(
        "<2sIHHI IIIHHIIIIII",
        b"BM",
        file_size,
        0,
        0,
        54,
        40,
        w,
        h,
        1,
        24,
        0,
        img_size,
        2835,
        2835,
        0,
        0,
    )
    rows = bytearray()
    for bmp_row in range(h):
        y = h - 1 - bmp_row
        for x in range(w):
            if y < 16:
                r, g, b = tl if x < 64 else tr
            else:
                r, g, b = bot
            rows.extend((b, g, r))
    path.write_bytes(header + rows)


def build_pil_marquee_128x32(tl: Color, tr: Color, bot: Color, alt: bool = False) -> "Image.Image":
    img = Image.new("RGB", (128, 32))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 63, 15], fill=tl)
    draw.rectangle([64, 0, 127, 15], fill=tr)
    draw.rectangle([0, 16, 127, 31], fill=bot)
    if alt:
        draw.rectangle([56, 12, 71, 19], fill=(255, 255, 255))
    return img


def write_marquee_triplet(base_no_ext: Path, tl: Color, tr: Color, bot: Color) -> None:
    write_bmp24_128x32(base_no_ext.with_suffix(".bmp"), tl, tr, bot)
    if Image is not None:
        f0 = build_pil_marquee_128x32(tl, tr, bot, alt=False)
        f1 = build_pil_marquee_128x32(tl, tr, bot, alt=True)
        base_no_ext.parent.mkdir(parents=True, exist_ok=True)
        f0.save(base_no_ext.with_suffix(".png"), format="PNG")
        f0.save(
            base_no_ext.with_suffix(".gif"),
            format="GIF",
            save_all=True,
            append_images=[f1],
            duration=120,
            loop=0,
        )
    elif shutil.which("ffmpeg"):
        bmp_p = base_no_ext.with_suffix(".bmp")
        for ext in (".png", ".gif"):
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(bmp_p), str(base_no_ext.with_suffix(ext))],
                check=True,
            )


def generate_showcase_catalog(out_dir: Path) -> None:
    """Generates `/e2e_fixtures/catalog.json` + posters, marquees, jackets, and MP4 gameplay clips."""
    out_dir.mkdir(parents=True, exist_ok=True)
    media_dir = out_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    games_list = []
    for rom, meta in GAME_SIGNATURES.items():
        poster_rel = f"media/{rom}_poster.png"
        marquee_rel = f"media/{rom}_marquee.png"
        jacket_rel = f"media/{rom}_jacket.png"
        video_rel = f"media/{rom}_gameplay.mp4"

        if Image is not None:
            # Poster (128x180)
            poster = Image.new("RGB", (128, 180), meta["quad_tl"])
            dp = ImageDraw.Draw(poster)
            dp.rectangle([8, 8, 119, 88], fill=meta["quad_tr"])
            dp.rectangle([8, 96, 119, 171], fill=meta["quad_bot"])
            poster.save(out_dir / poster_rel, format="PNG")

            # Wide Marquee Panorama (384x96 -> triggers horizontal scroll in DMDZ v2!)
            marquee = Image.new("RGB", (384, 96), (16, 16, 24))
            dm = ImageDraw.Draw(marquee)
            for stripe_x in range(0, 384, 32):
                col = meta["quad_tl"] if (stripe_x // 32) % 3 == 0 else (
                    meta["quad_tr"] if (stripe_x // 32) % 3 == 1 else meta["quad_bot"]
                )
                dm.rectangle([stripe_x + 2, 6, stripe_x + 29, 89], fill=col)
            marquee.save(out_dir / marquee_rel, format="PNG")

            # Vertical Jacket Panorama (256x256)
            jacket = Image.new("RGB", (256, 256), meta["quad_bot"])
            dj = ImageDraw.Draw(jacket)
            for band_y in range(0, 256, 32):
                col = meta["quad_tl"] if (band_y // 32) % 2 == 0 else meta["quad_tr"]
                dj.rectangle([12, band_y + 4, 243, band_y + 27], fill=col)
            jacket.save(out_dir / jacket_rel, format="PNG")

        # Gameplay MP4 (2.5s @ 15fps, animated lavfi testsrc + game-distinct color badge)
        video_abs = out_dir / video_rel
        if shutil.which("ffmpeg"):
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=256x128:rate=15:duration=2.5",
                    "-vf",
                    f"drawbox=x=8:y=8:w=64:h=48:color={meta['video_box']}@1.0:t=fill",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-preset",
                    "ultrafast",
                    "-movflags",
                    "+faststart",
                    str(video_abs),
                ],
                check=True,
            )

        aliases = [rom, meta["title"].lower()]
        sys_list = [meta["system"]]
        if meta["system"] == "megadrive":
            sys_list.append("genesis")

        games_list.append(
            {
                "id": f"e2e_{rom}",
                "domain": "games",
                "rom": rom,
                "aliases": aliases,
                "systems": sys_list,
                "system": meta["system"],
                "title": meta["title"],
                "year": meta["year"],
                "release_year": meta["year"],
                "rating": meta["rating"],
                "synopsis": meta["synopsis"],
                "publisher": meta["publisher"],
                "developer": meta["developer"],
                "players": "1-2",
                "genres": meta["genres"],
                "poster_rel": poster_rel,
                "marquee_rel": marquee_rel,
                "jacket_rel": jacket_rel,
                "video_rel": video_rel,
                "poster": poster_rel,
                "marquee": marquee_rel,
                "fanart": jacket_rel,
                "video": video_rel,
            }
        )

    catalog = {"version": 1, "items": games_list, "games": games_list}
    (out_dir / "catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def populate_dmd_sd_roots(base_dir: Path) -> None:
    """Populates SD/cache marquee layouts for ArcadeMatrix ESP32 and ArcadeMatrix RPi."""
    am_sd = base_dir / "am_esp32_sd"
    rpi_marquees = base_dir / "rpi_marquees"

    for rom, meta in GAME_SIGNATURES.items():
        tl, tr, bot = meta["quad_tl"], meta["quad_tr"], meta["quad_bot"]
        folder = meta["pixelcade_folder"]
        sys_id = meta["system"]

        # 1. ArcadeMatrix ESP32 SD (`/sd/pixelcade/<folder>/<rom>.gif|.bmp|.png`)
        write_marquee_triplet(am_sd / "pixelcade" / folder / rom, tl, tr, bot)

        # 2. ArcadeMatrix RPi (`data/marquees/<sys>_<rom>.png` + `genesis_<rom>.png`)
        rpi_marquees.mkdir(parents=True, exist_ok=True)
        for sys_alias in ({sys_id, folder}):
            write_marquee_triplet(rpi_marquees / f"{sys_alias}_{rom}", tl, tr, bot)

    for sys_name, (c1, c2) in SYSTEM_SIGNATURES.items():
        write_marquee_triplet(am_sd / "pixelcade" / "console" / f"default-{sys_name}", c1, c2, (20, 20, 60))
        write_marquee_triplet(rpi_marquees / f"system_{sys_name}", c1, c2, (20, 20, 60))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate offline E2E media and marquee fixtures")
    parser.add_argument("--mode", choices=["all", "showcase", "dmd-sd"], default="all")
    parser.add_argument("--out", default="/e2e_fixtures")
    args = parser.parse_args()

    out_path = Path(args.out)
    if args.mode in ("all", "showcase"):
        generate_showcase_catalog(out_path)
    if args.mode in ("all", "dmd-sd"):
        populate_dmd_sd_roots(out_path)
    print(f"[fixtures] Generated mode={args.mode} in {out_path}", flush=True)


if __name__ == "__main__":
    main()
