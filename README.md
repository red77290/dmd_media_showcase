# 🎬🎮 DMD Media Showcase Server (`dmd-media-showcase`)

> **[🇫🇷 Lire la documentation en Français (README_FR.md)](README_FR.md)**

**`dmd-media-showcase`** is an open-source, self-hosted, stateless Dockerized server that transforms promotional video trailers, openings, and artwork from **Retro Games**, **Movies**, **TV Series**, and **Anime** into ultra-vibrant, zero-latency LED matrix animations for **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** and **[ArcadeMatrix_RPi (Raspberry Pi)](https://github.com/red77290/ArcadeMatrix_RPi)**.

Powered by **[`dmd-gif-converter` v8.0](https://github.com/red77290/dmd_gif_converter)**, it performs AI highlight detection (`MomentDetector`), automatic action-crop (`AutoActionCrop`), and LED colorimetric boost (`SmartColorBoost`) inside a strictly bounded CPU worker pool.

---

## ✨ Key Architectural Highlights

1. **4 Dedicated Multilingual Domains (`FR`, `EN`, `ES`, `DE`, `IT`):**
   - 🕹️ **Retro & Arcade Games (`/api/v1/games`):** ScreenScraper.fr (FR/EN/ES/DE/IT) + Steam Store Public API + Built-in Arcade Classics Catalog.
   - 🎬 **Cinema & Blockbusters (`/api/v1/movies`):** TMDB API v3 (`fr-FR`, `en-US`, `es-ES`, etc.) + Built-in Cult Cinema Catalog.
   - 📺 **TV Series (`/api/v1/series`):** TMDB TV + TheTVDB v4 + TVMaze Public API + Built-in Cult Series Catalog.
   - ⛩️ **Anime & Manga (`/api/v1/anime`):** AnimeThemes.moe (Openings/Endings) + Jikan (MyAnimeList v4) + TMDB Localized Synopses + Built-in Anime Catalog.
2. **100% Zero-Config Out-of-the-Box:**
   - Even with **zero API keys configured**, all 4 domains work immediately using free keyless APIs (Steam, TVMaze, AnimeThemes, Jikan) and rich built-in multilingual curated catalogs.
3. **Engine-Encapsulated 2-Step LAN Protocol (Zero Webhook Leakage):**
   - DMD engines never expose HTTP server webhooks. Instead, each engine uses 2 outgoing LAN calls:
     1. **Proactive Session Registration (`POST /api/v1/{domain}/session`):** Sent on DMD boot or WebUI save with the exact `count = items_per_rotation` (e.g. `10` items).
     2. **Fast Pre-Rotation Check (`GET /api/v1/{domain}/batch?session_id=...`):** `< 2 ms` LAN check (*"Are you ready?"*) right before the engine's rotation turn.
4. **Bounded CPU Worker Pool & Elastic Queue (`MAX_CONCURRENT_JOBS`):**
   - Video encoding runs in isolated sub-processes (`ProcessPoolExecutor`, `os.nice(10)`, `FFMPEG_THREADS_PER_JOB=1`) while the FastAPI event loop stays 100% responsive (`< 2 ms`).
   - If many DMDs request videos on a modest VM/Raspberry Pi, jobs queue peacefully in FIFO order without ever overheating or crashing the host: DMDs simply skip a rotation turn if their batch is still `"warming"`.
5. **Stateless `tmpfs` Cache & Dynamic $O(1)$ Duration Truncation:**
   - No database, no persistent disk bloat. When `CurrentBatch` is served, `NextBatch` (`N` new items) is prepared one step ahead and expired batches are purged automatically.
   - Changing the video duration slider on a DMD (`?max_duration_s=5`) truncates the `.rgb565.stream` or `.gif` payload on the fly in $O(1)$ **without re-running FFmpeg**.
6. **Future-Ready On-Demand & Voice Assistant Priority Queue:**
   - Built-in `POST /api/v1/{domain}/on-demand` endpoint with `HIGH_ON_DEMAND` priority to support upcoming Google Home / Home Assistant / Webhook voice triggers.

---

## 🚀 Quick Start (Docker Compose)

```bash
git clone https://github.com/red77290/dmd_media_showcase.git
cd dmd_media_showcase
cp .env.example .env   # Optional: add free TMDB_API_KEY or SCREENSCRAPER credentials
docker compose up -d
```

Verify the server is running:
```bash
curl http://localhost:8080/api/v1/health
```

---

## 📚 Detailed Documentation

| Guide | English | Français |
| :--- | :--- | :--- |
| **Scrapers & Free API Keys Guide** | [`docs/SCRAPERS_GUIDE.md`](docs/SCRAPERS_GUIDE.md) | [`docs/SCRAPERS_GUIDE_FR.md`](docs/SCRAPERS_GUIDE_FR.md) |
| **Deployment, CPU Sizing & Multi-DMD Scaling** | [`docs/DEPLOYMENT_AND_SIZING.md`](docs/DEPLOYMENT_AND_SIZING.md) | [`docs/DEPLOYMENT_AND_SIZING_FR.md`](docs/DEPLOYMENT_AND_SIZING_FR.md) |
| **REST API, Rotation Protocol & Binary `.rgb565.stream` Spec** | [`docs/API_AND_PROTOCOL.md`](docs/API_AND_PROTOCOL.md) | [`docs/API_AND_PROTOCOL_FR.md`](docs/API_AND_PROTOCOL_FR.md) |

---

## 🧪 Local Development & Testing

```bash
pip install -e ".[dev]"
pytest
```

## 📄 License

MIT License — Part of the **ArcadeMatrix** open-source ecosystem.
