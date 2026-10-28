# DMD Media Showcase Server

🇬🇧 English | 🇫🇷 [Français](README_FR.md) | 🇪🇸 [Español](README_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Telegram Community:** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Support the Project on Ko-fi:** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** is a self-hosted, zero-repetition media discovery, transcoding, and real-time UDP/MQTT streaming microservice designed for **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** and **ArcadeMatrix_RPi** HUB75 LED matrix displays.

---

## 🎬 Live HUB75 DMD Output & Web Control Center

### 🕹️ Real 128×32 HUB75 LED Matrix Sequences (Generated Live via the Showcase API)

Every title automatically assembles a complete multi-chapter RGB565 broadcast tailored to your panel geometry (`128×32` classic `4:1` DMD shown below):
**`1. Animated Info Card`** (boxart, system badge, auto-scrolling year/rating & smooth ticker) $\to$ **`2. Zoom-to-Cover Boxart & Marquee Panoramas`** $\to$ **`3. In-Game Screenshot Scan`** $\to$ **`4. AI-Framed Gameplay / Trailer Video`**.

| **Garou: Mark of the Wolves (`Neo Geo` • `128×32`)** | **Super Street Fighter II X / Turbo (`CPS2` • `128×32`)** | **Metal Slug X (`Neo Geo` • `128×32`)** |
| :---: | :---: | :---: |
| ![Garou: Mark of the Wolves 128x32 HUB75 DMD Demo](demo/dmd_garou_motw_neogeo.gif) | ![Super Street Fighter II X CPS2 128x32 HUB75 DMD Demo](demo/dmd_ssf2x_cps2.gif) | ![Metal Slug X Neo Geo 128x32 HUB75 DMD Demo](demo/dmd_metal_slug_x_neogeo.gif) |
| `Classic Split` • `Tinted` • `auto_action` + `pixel_art` | `Badge Header` • `Neon Border` • `auto_action` + `pixel_art` | `Classic Split` • `Poster Wash` • `auto_action` + `pixel_art` |

### 🖥️ Multilingual Web Control Center (`EN` / `FR` / `ES`)

| **1. Live Overview & Connected HUB75 Matrices** | **2. Card Studio & Real-Time HUB75 LED Simulator** |
| :---: | :---: |
| [![Live Overview & Connected DMD Matrices](demo/ui_overview_live.png)](demo/ui_overview_live.png) | [![Mode 2B Card Studio & HUB75 Simulator](demo/ui_card_studio.png)](demo/ui_card_studio.png) |
| Live MQTT (`:1883`) + UDP telemetry, pre-warmed `Ready (N)` & `Standby (N+1)` slots with live source badges (`🔗 igdb+screenscraper`), Consume-Once history, and hot-editable multi-source scraper chains. | Per-domain profiles (`Games`, `Movies`, `Series`, `Anime`), 5 pixel-art layouts (YOKO & TATE), live SMD LED simulator, and `0 ms` injection into any connected DMD queue. |
| **3. Multi-Catalog Search & On-Demand Priority Queue** | **4. Passive DMD Profiles & Visual Multi-Tag Rotation** |
| [![Catalog Search & On-Demand Queue](demo/ui_ondemand_search.png)](demo/ui_ondemand_search.png) | [![Passive DMD Rotation & Multi-Tag Picker](demo/ui_passive_rotation.png)](demo/ui_passive_rotation.png) |
| Search Games (including player shorthands like `sf2x`, `3s`, `kof98`), Movies, TV Series, and Anime in your preferred language and push titles via `Queue Next` (`FIFO`) or immediate `Play Now` preemption. | Configure Mode 2A/2B server-driven playlists with interactive System, Genre & Decade tag pickers and direct links to each domain's Studio profile. |

---

## ✨ Key Features

- **4 Showcase Domains & Configurable Multi-Source Chains (`games`, `movies`, `series`, `anime`)**:
  - **Retro & Arcade Games (`55+` / `66` Canonical Systems)**: Arcade boards (`MAME`, `CPS-1/2/3`, `Neo-Geo`, `NAOMI`, `Atomiswave`, `Model 2/3`), Consoles, Handhelds, and Classic Computers powered by **4 independent selectable sources**:
    1. **`igdb`** (`IGDB API v4` via free Twitch OAuth2 — 200+ platforms, instant acronym/alias search via `alternative_names`, random offset discovery, covers/artworks & trailers).
    2. **`screenscraper`** (`ScreenScraper` — clean détouré wheels/marquees, pixel-art snaps, short `.mp4` video snaps, with automatic HTTP-200 plaintext quota/overload detection & cooldown circuit-breaker).
    3. **`arcadeitalia`** (keyless authoritative MAME/CPS/Neo-Geo/NAOMI HTTPS scraper with built-in shorthand aliases resolving `sf2x`, `3s`, `kof98`, `garou` in a single request).
    4. **`libretro_wikidata`** (first-class keyless console/handheld/computer source combining Wikidata SPARQL, verified `libretro-thumbnails`, localized Wikipedia synopses, and Archive.org video snaps).
  - **Movies**: Classic & Blockbuster Cinema (`tmdb_movies` $\to$ `itunes_movies` keyless).
  - **TV Series**: Cult & Modern TV Shows (`tmdb_series` $\to$ `tvdb_series` $\to$ `tvmaze` keyless).
  - **Anime / Manga**: Creditless Openings (`OP`), Endings (`ED`), and Trailers (`jikan` + `AnimeThemes` $\to$ `anilist` keyless).
- **Hot-Configurable Source Priority & Cross-Source Aggregation (`source_policies`)**:
  - Reorder priority (`#1 Primary -> #2 Secondary -> #3 Tertiary -> ...`) and toggle individual sources per domain live in the Web Admin Dashboard (**Overview** and **API Keys & Scrapers**), persisted in `./data/server_config.json`. Whenever a source returns `0` results, times out, enters rate-limit cooldown, or doesn't support a target system, discovery deterministically falls through to the next enabled source.
  - Choose per domain between **`enrich_missing` (Multi-Source Aggregation, default)** — where Primary resolves canonical identity & aliases (e.g., `IGDB` resolving `"sf2x"` on `cps2` $\to$ `"Super Street Fighter II Turbo"` / `ssf2t`) and `CandidateAggregator` runs a bounded (`<= 3.0s`) secondary pass to pull missing DMD marquees/wheels, pixel-art snaps, direct `.mp4`/`.webm`/`.m4v` streams, and localized synopses with transparent composite attribution (`source_used="igdb+screenscraper"`, `"igdb+arcadeitalia"`, `"tmdb_movies+itunes_movies"`) — and **`fallback_only` (Strict Fallback)**.
- **Dynamic Consolidated Referentials**: Exposes localized (`fr`, `en`, `es`, `de`, `it`), source-agnostic category referentials (`GET /api/v1/{domain}/filters?lang=fr` & MQTT `dmd_showcase/catalog/{domain}/referential`) with relational `System -> Supported Genres & Decades` mapping so DMD WebUIs never hardcode scraper categories.
- **Real-Time MQTT Control Plane (`:1883`) + `ffmpeg -re` UDP `DMDF` Streaming**:
  - Zero-config embedded MQTT 3.1.1 broker (`retain=True` + `Last Will and Testament`).
  - Hardware-timed `ffmpeg -re` UDP `DMDF` chunk streaming directly to the DMD's LAN IP (`client_ip` / `udp_ip`, bypassing Docker bridge NAT), with live per-slot source telemetry (`playing_source`, `ready_source`, `standby_source`, `preparing_source`) in the Admin UI.
- **Strict Consume-Once & `N+1` Background Pipelining**:
  - Tracks every presented title in `./data/seen_history.json` (zero repetition until catalog exhaustion).
  - Pre-builds item `N+1` in the background as soon as item `N` starts displaying its Info Card (`start_card`), and purges item `N` from disk immediately on `stream_eof`.
- **Server-Managed Passive ESP32 Profiles (`dmd_profiles.json`) & Live On-Demand Queue**:
  - Supports full multi-engine DMDs (`ArcadeMatrix`) **and** ultra-light ~180-line zero-UI/zero-PSRAM `ESP32_STD` passive UDP `:4210` receivers (`supports_local_card: false`, with even Phase 1 Info Cards rendered into the RGB565 stream) configured centrally in `./data/dmd_profiles.json`.
  - Localized catalog search (`GET /api/v1/{domain}/search`), asynchronous per-DMD priority queue (`POST /api/v1/clients/{dmd_id}/queue/async`, `enqueue_next` / `play_now`), live `Skip` / `Pause` / `Resume` / `Stop` controls, and **`FrontendSync`** (`mark_seen: false` for Recalbox / Batocera / RetroPie / RetroBat / LaunchBox / Playnite).
- **Persistent On-Demand Conversion Cache & 1-Click Eviction**:
  - Optional persistent disk cache (`Admin Dashboard -> 2. Runtime, Workers & Video Limits -> Persistent On-Demand Conversion Cache`) indexed by **`(System + Game + Panel Resolution + Card/Video Style)`** with configurable disk quota (MB) for `0 ms` instant replays (`POST /api/v1/cache/on-demand/evict` to clear in one click).
- **Powered by `dmd_gif_converter` (`>= 8.0.10`)**: Orthogonal video framing (`auto_action` Let Me Handle It AI motion saliency, `panorama` scan, `center_crop`), HUB75 LED colorimetry (`auto` Smart Color Boost AI, `pixel_art`, `anime`, `cinema`), Lanczos + unsharp downscaling, and **deterministic uniform zoom-to-cover panoramas** with zero black bars — **YOKO** (`128x32`, `128x64`, `256x64`) scrolling smoothly **top-to-bottom**, and **TATE** (`64x128`, `32x128`) scrolling smoothly **left-to-right**.
- **3-Tier Credential Vault & Automatic HTTP Log Redaction**: Resolves credentials across `1. WebUI server_config.json` $\to$ `2. .env / docker-compose` $\to$ `3. CI/CD compiled native C vault (_prod_vault)`, while `SecretRedactingLogFilter` (`httpx`/`httpcore` clamped to `WARNING`) guarantees API keys, OAuth secrets, and query-string passwords never leak into container stdout logs.
- **OS-Aware Release Update Notification**: Built-in non-intrusive release checker (`GET /api/v1/system/update-check`) that notifies you in the Web UI whenever a newer version is published on GitHub for your runtime platform (`macOS .dmg`, `Windows .zip`, `Linux .tar.gz`, or `Docker`).

---

## 🚀 Quick Installation & Short Example

### Option 1: Official Multi-Arch Docker Image (`amd64` / `arm64` — Raspberry Pi / NAS / Home-Lab)

#### A. Docker Compose (Recommended — zero `git clone` or local build needed)

Download the ready-to-use [`docker-compose.yml`](docker-compose.yml) and start the stack:

```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
```

> **Local Source Build (`git clone`):** Inside a cloned repository, [`docker-compose.override.yml`](docker-compose.override.yml) is loaded automatically so `docker compose up -d --build` builds your local source tree and latest `dmd_gif_converter` instead of pulling the remote image.

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
      - ${DMD_DATA_PATH:-./data}:/app/data
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```
</details>

#### B. Direct `docker run` Command (Host Bind Mount or Portainer Named Volume)

```bash
# Option B1: Host directory bind mount (./data — immune to `docker compose down -v`)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest

# Option B2: Named Docker volume (`dmd_showcase_data` — ideal for Portainer / standalone hosts)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v dmd_showcase_data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest
```

> **Persistent State (`/app/data`)**: Stores `server_config.json` (scraper credentials, runtime tuning & per-domain `source_policies`), `dmd_profiles.json` (server-managed DMD profiles & broadcast language), `seen_history.json`, and `referentials/`. Using `${DMD_DATA_PATH:-./data}:/app/data` binds a host folder (customizable via `DMD_DATA_PATH` in `.env`) that is **never erased by `docker compose down -v`**, whereas `-v dmd_showcase_data:/app/data` uses a Docker-managed named volume suited for Portainer.

- **Web Admin Dashboard**: `http://dmdmediashowcase.local:8890/` (or `http://<server-ip>:8890/` — reorder multi-source chains, configure optional IGDB / ScreenScraper / TMDB / TheTVDB credentials & reset `seen_history.json`)
- **Interactive Swagger UI**: `http://dmdmediashowcase.local:8890/docs`
- **Embedded MQTT Broker**: `mqtt://dmdmediashowcase.local:1883`
- **Automatic Local Network Discovery (mDNS)**: Automatically advertises `dmdmediashowcase.local` (`224.0.0.251:5353`) so your DMD (`ArcadeMatrix` ESP32 or `ArcadeMatrix_RPi`) finds it without typing an IP address. Simply toggle Showcase **ON** in the DMD Web UI (`Settings -> Showcase Server`), verify the **`🟢 Communication OK`** badge, and add the Showcase engines to your rotation!

### Option 2: Native Desktop Installers & Portable Packages (macOS `.dmg` / Windows `.zip` / Linux `.tar.gz`)

Download the package for your OS from **[GitHub Releases](https://github.com/red77290/dmd_media_showcase/releases/latest)** (`0` host Python or FFmpeg installation required):
- **macOS (Apple Silicon `arm64`)**: Open **`DMD_Media_Showcase_macOS_arm64.dmg`**, drag **`DMD Media Showcase.app`** to **`Applications`**, and launch it (`▣ DMD` appears in the macOS Menu Bar and automatically opens `http://localhost:8890/`). *(First launch without Apple Developer notarization: Right-click `DMD Media Showcase.app` → **Open**, or run `xattr -cr "/Applications/DMD Media Showcase.app"` once).*
- **Windows (`x64`)**: Extract **`DMD_Media_Showcase_Windows_x64.zip`** and double-click `Start_Showcase_Windows.bat`.
- **Linux (`x64`)**: Extract **`DMD_Media_Showcase_Linux_x64.tar.gz`** and run `./start_showcase.sh`.

### Quick Verification Example

```bash
# 1. Check server health, active multi-source chains, worker pool, and MQTT broker port
curl -s http://localhost:8890/api/v1/health | jq .

# 2. Fetch consolidated French referential for Retro Games (55+ Systems -> Genres & Decades)
curl -s "http://localhost:8890/api/v1/games/filters?lang=fr" | jq '.systems[0], .genres[:3]'
```

---

## 📚 Documentation & API Specifications

| Document | 🇬🇧 English | 🇫🇷 Français | 🇪🇸 Español |
| :--- | :--- | :--- | :--- |
| **QuickStart & Deployment Guide** | [docs/QUICKSTART.md](docs/QUICKSTART.md) | [docs/QUICKSTART_FR.md](docs/QUICKSTART_FR.md) | [docs/QUICKSTART_ES.md](docs/QUICKSTART_ES.md) |
| **ESP32 Integration & Passive Receiver Guide** | [docs/ESP32_INTEGRATION.md](docs/ESP32_INTEGRATION.md) | [docs/ESP32_INTEGRATION_FR.md](docs/ESP32_INTEGRATION_FR.md) | [docs/ESP32_INTEGRATION_ES.md](docs/ESP32_INTEGRATION_ES.md) |
| **System Architecture & Streaming Pipeline** | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/ARCHITECTURE_FR.md](docs/ARCHITECTURE_FR.md) | [docs/ARCHITECTURE_ES.md](docs/ARCHITECTURE_ES.md) |
| **Developer & Scraper Strategy Guide** | [docs/DEVELOPER.md](docs/DEVELOPER.md) | [docs/DEVELOPER_FR.md](docs/DEVELOPER_FR.md) | [docs/DEVELOPER_ES.md](docs/DEVELOPER_ES.md) |
| **OpenAPI 3.1 REST Specification** | [docs/openapi.yaml](docs/openapi.yaml) / [docs/openapi.json](docs/openapi.json) | — | — |
| **AsyncAPI 2.6 MQTT & UDP Specification** | [docs/asyncapi.yaml](docs/asyncapi.yaml) | — | — |
