# DMD Media Showcase Server

🇬🇧 English | 🇫🇷 [Français](README_FR.md) | 🇪🇸 [Español](README_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Telegram Community:** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Support the Project on Ko-fi:** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** is a self-hosted, zero-repetition media discovery, transcoding, and real-time UDP/MQTT streaming microservice designed for **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** and **ArcadeMatrix_RPi** HUB75 LED matrix displays.

---

## ✨ Key Features

- **4 Showcase Domains (`games`, `movies`, `series`, `anime`)**:
  - **Retro Games**: 30 Arcade, Console, Handheld, and Home Computer systems (`ScreenScraper` Primary $\to$ `ArcadeItalia` MAME HTTPS + `Libretro+Wikidata` Console SPARQL Fallback).
  - **Movies**: Classic & Blockbuster Cinema (`TMDB` Primary $\to$ `Apple iTunes` Fallback).
  - **TV Series**: Cult & Modern TV Shows (`TMDB` Primary $\to$ `TVMaze` Fallback).
  - **Anime / Manga**: Creditless Openings (`OP`), Endings (`ED`), and Trailers (`Jikan + AnimeThemes` Primary $\to$ `AniList` Fallback).
- **Dynamic Consolidated Referentials**: Exposes localized (`fr`, `en`, `es`, `de`, `it`), source-agnostic category referentials (`GET /api/v1/{domain}/filters?lang=fr` & MQTT `dmd_showcase/catalog/{domain}/referential`) with relational `System -> Supported Genres & Decades` mapping so DMD WebUIs never hardcode scraper categories.
- **Real-Time MQTT Control Plane (`:1883`) + `ffmpeg -re` UDP `DMDF` Streaming**:
  - Zero-config embedded MQTT 3.1.1 broker (`retain=True` + `Last Will and Testament`).
  - Hardware-timed `ffmpeg -re` UDP `DMDF` chunk streaming directly to the DMD's LAN IP (`client_ip` / `udp_ip`, bypassing Docker bridge NAT).
- **Strict Consume-Once & `N+1` Background Pipelining**:
  - Tracks every presented title in `./data/seen_history.json` (zero repetition until catalog exhaustion).
  - Pre-builds item `N+1` in the background as soon as item `N` starts displaying its Info Card (`start_card`), and purges item `N` from disk immediately on `stream_eof`.
- **Powered by `dmd_gif_converter`**: Orthogonal video framing (`auto_action` Let Me Handle It AI motion saliency, `panorama` top-to-bottom scan, `center_crop`) and HUB75 LED colorimetry (`auto` Smart Color Boost AI, `pixel_art`, `anime`, `cinema`), Lanczos + unsharp downscaling, and top-to-bottom sub-pixel scrolling panoramas (`Jacket`, `Marquee`, `Snapshots`) across all Landscape (`128x32`, `128x64`, `256x64`), Square (`64x64`), and TATE (`64x128`, `32x128`) resolutions.

---

## 🚀 Quick Installation & Short Example

### Option 1: Official Multi-Arch Docker Image (`amd64` / `arm64` — Raspberry Pi / NAS / Home-Lab)

#### A. Docker Compose (Recommended — zero `git clone` or local build needed)

Download the ready-to-use [`docker-compose.yml`](docker-compose.yml) and start the stack:

```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
```

<details>
<summary>📄 View standalone <code>docker-compose.yml</code></summary>

```yaml
services:
  dmd-media-showcase:
    image: ghcr.io/red77290/dmd_media_showcase:latest
    pull_policy: always
    container_name: dmd-media-showcase
    restart: unless-stopped
    ports:
      - "8890:8890"
      - "1883:1883"
      - "5353:5353/udp"
    volumes:
      - ./data:/app/data
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```
</details>

#### B. Direct `docker run` Command

```bash
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest
```

- **Web Admin Dashboard**: `http://dmdmediashowcase.local:8890/` (or `http://<server-ip>:8890/` — configure optional TMDB / ScreenScraper credentials & reset `seen_history.json`)
- **Interactive Swagger UI**: `http://dmdmediashowcase.local:8890/docs`
- **Embedded MQTT Broker**: `mqtt://dmdmediashowcase.local:1883`
- **Automatic Local Network Discovery (mDNS)**: Automatically advertises `dmdmediashowcase.local` (`224.0.0.251:5353`) so your DMD (`ArcadeMatrix` ESP32 or `ArcadeMatrix_RPi`) finds it without typing an IP address. Simply toggle Showcase **ON** in the DMD Web UI (`Settings -> Showcase Server`), verify the **`🟢 Communication OK`** badge, and add the Showcase engines to your rotation!

### Option 2: Portable Standalone Bundle (Windows / macOS)

Download the latest portable archive (`DMD_Media_Showcase_Windows_x64.zip` or `DMD_Media_Showcase_macOS.zip`) from **[GitHub Releases](https://github.com/red77290/dmd_media_showcase/releases/latest)**, extract it anywhere, and launch `dmd-media-showcase` (`0` host Python or FFmpeg installation required).

### Quick Verification Example

```bash
# 1. Check server health, active worker pool, and MQTT broker port
curl -s http://localhost:8890/api/v1/health | jq .

# 2. Fetch consolidated French referential for Retro Games (Systems -> Genres & Decades)
curl -s "http://localhost:8890/api/v1/games/filters?lang=fr" | jq '.systems[0], .genres[:3]'
```

---

## 📚 Documentation & API Specifications

| Document | 🇬🇧 English | 🇫🇷 Français | 🇪🇸 Español |
| :--- | :--- | :--- | :--- |
| **QuickStart & Deployment Guide** | [docs/QUICKSTART.md](docs/QUICKSTART.md) | [docs/QUICKSTART_FR.md](docs/QUICKSTART_FR.md) | [docs/QUICKSTART_ES.md](docs/QUICKSTART_ES.md) |
| **DDD Architecture & Streaming Pipeline** | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/ARCHITECTURE_FR.md](docs/ARCHITECTURE_FR.md) | [docs/ARCHITECTURE_ES.md](docs/ARCHITECTURE_ES.md) |
| **Developer & Scraper Strategy Guide** | [docs/DEVELOPER.md](docs/DEVELOPER.md) | [docs/DEVELOPER_FR.md](docs/DEVELOPER_FR.md) | [docs/DEVELOPER_ES.md](docs/DEVELOPER_ES.md) |
| **OpenAPI 3.1 REST Specification** | [docs/openapi.yaml](docs/openapi.yaml) / [docs/openapi.json](docs/openapi.json) | — | — |
| **AsyncAPI 2.6 MQTT & UDP Specification** | [docs/asyncapi.yaml](docs/asyncapi.yaml) | — | — |
