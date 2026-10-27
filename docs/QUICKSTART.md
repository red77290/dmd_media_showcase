# QuickStart & Deployment Guide — DMD Media Showcase Server

🇬🇧 English | 🇫🇷 [Français](QUICKSTART_FR.md) | 🇪🇸 [Español](QUICKSTART_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Deployment Modes

### Mode A: Official Multi-Arch Docker Image (Linux / Raspberry Pi 4 & 5 / NAS)

#### Option 1 — `docker-compose.yml` (Recommended)

```yaml
services:
  dmd-media-showcase:
    image: ghcr.io/red77290/dmd_media_showcase:latest
    pull_policy: always
    container_name: dmd-media-showcase
    restart: unless-stopped
    ports:
      - "8890:8890"       # FastAPI REST + Web Admin Dashboard + Swagger UI
      - "1883:1883"       # Embedded MQTT 3.1.1 Control Plane Broker
      - "5353:5353/udp"   # Local Network mDNS Auto-Discovery (dmdmediashowcase.local)
    volumes:
      - ${DMD_DATA_PATH:-./data}:/app/data  # Persistent server_config.json, dmd_profiles.json, seen_history.json & referentials/
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```

Launch directly using the repository's [`docker-compose.yml`](../docker-compose.yml):
```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
docker logs -f dmd-media-showcase
```

> **Local Source Build (`git clone` + `docker-compose.override.yml`)**: When running inside a local Git checkout, [`docker-compose.override.yml`](../docker-compose.override.yml) is loaded automatically so `docker compose up -d --build` builds `dmd-media-showcase:local` from your working tree (plus the latest `dmd_gif_converter >= 8.0.4`) instead of pulling `ghcr.io/red77290/dmd_media_showcase:latest`.

#### Option 2 — Direct `docker run` Command (Host Bind Mount or Portainer Named Volume)

```bash
# Option 2A: Host bind mount (./data — protected against `docker compose down -v`)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest

# Option 2B: Named Docker volume (`dmd_showcase_data` — ideal for Portainer / standalone hosts)
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

#### Persistent State & Volume Protection (`/app/data`)

All persistent server state is stored under `/app/data`:
- `server_config.json`: User scraper credential overrides, per-domain multi-source policies (`source_policies`: `ordered_sources`, `disabled_sources`, `aggregation_mode`), worker concurrency, and video ceilings.
- `dmd_profiles.json`: Server-managed DMD profiles (`config_source: "server"`), playlists, filters, and broadcast content language (`lang`).
- `seen_history.json`: Per-domain Consume-Once playback history (zero title repetition).
- `referentials/*.json`: Cached upstream scraper category referentials.

Using **`${DMD_DATA_PATH:-./data}:/app/data`** in `docker-compose.yml` mounts a real host directory (overrideable via `DMD_DATA_PATH=/mnt/nas/dmd_showcase` in `.env`), which is **100% immune to `docker compose down -v`** (`-v` only removes named Docker volumes, never host bind mounts). For **Portainer** or standalone setups without host directory paths, mount a named volume (`-v dmd_showcase_data:/app/data`) and avoid passing `-v` when tearing down stacks.

> [!TIP]
> **Docker Bridge NAT & UDP Streaming (`client_ip`)**: Both `ArcadeMatrix_RPi` and `ArcadeMatrix` (ESP32) automatically probe and declare their real LAN IP (`client_ip` / `udp_ip`) inside their MQTT `config` and `start_stream` payloads so `ffmpeg -re` streams `DMDF` UDP packets directly to your DMD even when Docker runs in default bridge mode (`172.18.0.1`).

---

### Mode B: Native Desktop Installers & Portable Bundles (macOS `.dmg`, Windows `.zip`, Linux `.tar.gz`)

1. **macOS (Apple Silicon `arm64`)**:
   - Download **`DMD_Media_Showcase_macOS_arm64.dmg`** from GitHub Releases, open it, and drag **`DMD Media Showcase.app`** to **`Applications`**.
   - Launch **`DMD Media Showcase.app`**: the `▣ DMD` icon appears in your macOS Menu Bar, automatically opens `http://localhost:8890/`, and stores persistent data in `~/Library/Application Support/DMDMediaShowcase/data/`.
   - *First launch Gatekeeper note (open-source ad-hoc signed build)*: Right-click `DMD Media Showcase.app` → **Open** (or **System Settings → Privacy & Security → Open Anyway**, or run `xattr -cr "/Applications/DMD Media Showcase.app"` once).
2. **Windows 10/11 (`x64`)**:
   - Download **`DMD_Media_Showcase_Windows_x64.zip`**, extract anywhere (e.g. `C:\DMD_Media_Showcase\`), and run `Start_Showcase_Windows.bat` (or `dmd-media-showcase.exe`). Persistent state is stored in `./data/`.
3. **Linux (`x64`)**:
   - Download **`DMD_Media_Showcase_Linux_x64.tar.gz`**, extract (`tar -xzf ...`), and run `./start_showcase.sh`.

---

## 2. Web Admin Dashboard (`http://dmdmediashowcase.local:8890/`)

Open `http://dmdmediashowcase.local:8890/` (or `http://<server-ip>:8890/`) in your browser to:
- **Hot-Reorder Multi-Source Scraper Chains & Aggregation Policy** (editable live under both **1. Live Overview** and **5. API Keys & Scrapers**, persisted in `./data/server_config.json`):
  - Reorder priority per domain (`ordered_sources`: `#1 Primary -> #2 Secondary -> #3 Tertiary -> ...`) and toggle individual sources (`disabled_sources`, with a built-in guard keeping at least one active source per domain). Whenever Primary returns `0` results, times out, enters rate-limit/quota cooldown, or doesn't support the requested system, discovery deterministically falls through to the next enabled source.
  - Select per-domain `aggregation_mode`:
    - **`enrich_missing` (Multi-Source Aggregation, default)**: Primary resolves canonical identity, fuzzy/alias search, and metadata (e.g., `IGDB` resolving `"sf2x"` on `cps2` $\to$ `"Super Street Fighter II Turbo"` / `ssf2t`), and `CandidateAggregator` runs a fast bounded secondary pass (`<= 3.0s`) on enabled secondary sources (`ScreenScraper`, `ArcadeItalia`, `Apple iTunes`, `AnimeThemes`, etc.) to pull missing DMD assets (clean détouré marquees/wheels, pixel-art snaps, direct `.mp4`/`.webm`/`.m4v` streams, localized synopses). Enriched items display composite attribution badges (`🔗 igdb+screenscraper`, `🔗 igdb+arcadeitalia`, `🔗 tmdb_movies+itunes_movies`) across live DMD slots (`playing_source`, `ready_source`, `standby_source`, `preparing_source`) and priority queues (`source_used`).
    - **`fallback_only` (Strict Fallback)**: Secondary sources are queried strictly when Primary returns `0` results or fails.
- **Hot-Tune Runtime, Workers & Video Limits in Real Time** (`2. Runtime, Workers & Video Limits`, persisted in `./data/server_config.json` with zero server restart):
  - Resize concurrent transcode workers (`max_concurrent_jobs`: `0 = Auto CPU-bounded` or `1..16`), executor isolation (`ProcessPoolExecutor` vs `ThreadPoolExecutor`), FFmpeg threads per job (`1..8`), OS CPU `nice` priority (`0..19`), queue capacity (`10..1000`), and live server `log_level`.
  - Enforce global ceilings on maximum video duration (`video_max_duration_limit_s`), master clip target (`server_master_clip_max_s`), max frame rate (`video_max_fps_limit`), source trailer download size (`ytdlp_max_filesize_mb`), deterministic zoom-to-cover panorama scroll duration (`panorama_duration_s` — top-to-bottom on YOKO panels, left-to-right on TATE panels), default AI framing/colorimetry (`default_video_mode`, `default_color_boost`), cache directory, and session/batch TTLs.
  - **Enable Persistent On-Demand Conversion Cache**: Toggle **Persistent On-Demand Conversion Cache** ON (and optionally set a max MB quota). Games requested on demand (such as from Recalbox / Batocera / RetroPie `FrontendSync`) are saved by `(System + Game + Panel Resolution + Card Style)` so relaunching a previously played game starts **instantly (`0 ms`)** without re-scraping or re-converting. Click **🗑 Evict On-Demand Cache** anytime to clear cached conversions in one click.
- **Configure Optional Scraper Credentials** (`5. API Keys & Scrapers` or `.env` — resolved via 3-tier priority `server_config.json` $\to$ `.env` / OS env $\to$ compiled native C vault `_prod_vault`, with `SecretRedactingLogFilter` + `httpx`/`httpcore` `WARNING` level scrubbing all query/OAuth secrets from logs):
  - **IGDB v4 (Twitch Developer Portal)**: `igdb_client_id` & `igdb_client_secret` (`IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET`, free app at `https://dev.twitch.tv/console/apps`) for `games` across 66 canonical retro & arcade systems (`55+` platforms), acronym/alias matching (`alternative_names`), and random offset discovery.
  - **ScreenScraper** (`screenscraper_user`, `screenscraper_pass`, `screenscraper_dev_id`, `screenscraper_dev_pass`) for `games` (détouré marquees/wheels, pixel-art snaps, `.mp4` video snaps, with automatic HTTP-200 plaintext quota/overload detection & cooldown circuit-breaker). Without credentials, keyless **`ArcadeItalia`** (MAME/CPS/Neo-Geo/NAOMI HTTPS + player shorthands `sf2x`, `3s`, `kof98`, `garou`) and **`Libretro+Wikidata`** (Console/Handheld/Computer SPARQL + `libretro-thumbnails` + Archive.org snaps) operate automatically with zero configuration.
  - **TMDB API Key / v4 Read Access Token** (`tmdb_api_key` / `TMDB_API_KEY`) and **TheTVDB v4 API Key** (`tvdb_api_key` / `TVDB_API_KEY`) for `movies`, `series`, and `anime`. Without keys, `Apple iTunes`, `TVMaze`, `Jikan + AnimeThemes`, and `AniList` operate automatically.
- Choose **Embedded MQTT Broker** (`:1883`, default) or connect to an **External MQTT Broker** (e.g. Mosquitto / Home Assistant).
- **Manage Server-Driven DMD Profiles (`./data/dmd_profiles.json`)**: Configure domain playlists (`games`, `movies`, `series`, `anime`), scraper filters (`systems`, `genres`, `decades`), framing/color presets, and broadcast content language (`lang`) centrally for thin/passive ESP32 receivers (`PUT /api/v1/clients/{dmd_id}/profile`).
- **Search Catalog & Trigger On-Demand Queue / Live Controls**: Search localized titles (`GET /api/v1/{domain}/search`), enqueue items onto any DMD with **`enqueue_next`** or **`play_now`** (`POST /api/v1/clients/{dmd_id}/queue`, including `FrontendSync` with `mark_seen: false`), and trigger live **`Skip` / `Pause` / `Resume` / `Stop`** (`POST /api/v1/clients/{dmd_id}/control`).
- View per-domain **Seen History counters** and reset `seen_history.json` per domain or globally.
- Trigger a live **Scraper Referential Sync** (`POST /api/v1/admin/referentials/sync`).

---

## 3. Connecting Your DMD (`ArcadeMatrix`, Passive ESP32 Receivers & `ArcadeMatrix_RPi`)

### 3.1 Client-Driven Multi-Engine DMDs (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`)

1. Open your DMD WebUI (`http://arcadematrix.local/` on ESP32 or `http://arcadematrix.local:8080/` on Raspberry Pi).
2. Navigate to **`Settings -> Showcase Server`** (`#mqtt-subtab-showcase`):
   - The **Showcase Server URL** is already pre-filled as `http://dmdmediashowcase.local:8890` and **disabled by default** (`⚪ Disabled`) so the DMD makes zero network requests if `dmd_media_showcase` is not installed.
   - Toggle **Enable DMD Media Showcase Server** ON and click **Save Showcase Server** (or **Test Connection**).
   - Verify that the live connection status indicator turns green: **`🟢 Communication OK`**.
3. Go to the **Screens / Rotation** tab, add any of the 4 Showcase engines (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`), and set the number of **Media** items per rotation turn.
4. The DMD WebUI automatically queries `GET /api/v1/{domain}/filters?lang={ui_lang}` from the Showcase server to populate the **Systems** (`55+` platforms), **Genres**, and **Decades** dropdowns dynamically (filtering available genres/decades when you select a system such as `NES`, `CPS-2`, or `Neo-Geo`).
5. Adjust **Synopsis Scroll Speed** (`1 = Slow` to `10 = Turbo`, up to `160 px/s`) and click **Save**.

### 3.2 Server-Managed Passive & Hybrid ESP32 Receivers (`config_source: "server"`)

For dedicated marquee/topper builds on `ESP32_STD` (no PSRAM) or `ESP32-S3` without a local WebUI (~180 lines of C++ listening on UDP `:4210`):
1. Flash the reference firmware skeleton from **[ESP32 Integration & Passive Receiver Guide](ESP32_INTEGRATION.md)** (announces geometry & LAN IP via `POST /api/v1/clients/hello` or MQTT `dmd_showcase/clients/{dmd_id}/hello`).
2. Open `http://dmdmediashowcase.local:8890/`, select the discovered DMD board, choose its broadcast content language (`lang`), enable domains/categories, and save its profile to `./data/dmd_profiles.json` — the server renders even Phase 1 Info Cards directly into the RGB565 UDP `DMDF` stream!

### 3.3 Pairing Showcase with Recalbox / Batocera / RetroPie (`FrontendSync` In-Game Showcase)

When your DMD (`ArcadeMatrix` ESP32 or `ArcadeMatrix_RPi`) is paired with a retro console, Showcase automatically enriches game launches without slowing down menu browsing:
1. **Install the Console Sync Script Once**: Point your Recalbox, Batocera (`v33+`), or RetroPie sync script to **your DMD's IP address**. The console asks the DMD dynamically which MQTT broker to use — so you never need to reinstall scripts when turning Showcase ON or OFF.
2. **Enable In-Game Showcase on the DMD**: In your DMD WebUI (`System -> MQTT & API -> 🎮 FrontendSync`), enable **Share Showcase MQTT Broker** (frees local RAM on the DMD by sharing Showcase's `:1883` broker) and **In-Game Showcase (Card + Gameplay Video)**.
3. **Choose Your Cycle & Card Design**:
   - **Menu Browsing (`0 ms`)**: Scrolling games displays local Pixelcade marquees immediately.
   - **Launching a Game**: Your DMD displays the classic marquee immediately while Showcase prepares the game's **Animated Info Card + Panoramas + Gameplay Video** in the background and smoothly takes over once ready (`Play Once`, `Alternate Marquee & Showcase`, or `Loop`).
   - **Card Style**: Either keep **Inherit DMD Profile from Showcase Studio** (configure your Games card once in Showcase's Card Studio) or select **Custom Override on DMD** directly inside `FrontendSync`.
4. **Instant Replay Cache**: Turn on **Persistent On-Demand Conversion Cache** in Showcase (`2. Runtime, Workers & Video Limits`) so previously played games launch instantaneously on future sessions, and use **🗑️ Evict Showcase Cache** (in Showcase or on the DMD) anytime you want to refresh cached card styles.
