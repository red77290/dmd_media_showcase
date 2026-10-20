# 🔑 Scrapers & Free API Keys Guide (`dmd-media-showcase`)

> **[🇫🇷 Lire ce guide en Français (SCRAPERS_GUIDE_FR.md)](SCRAPERS_GUIDE_FR.md)**

`dmd-media-showcase` relies **exclusively on official, free, and public scraper APIs** (promotional trailers, video snaps, posters, wheel logos, and localized synopses). No pirated content, ROMs, or full-length movies/episodes are ever used.

---

## 1. Summary Matrix of Supported Providers

| Media Domain | Primary Provider (Free Account) | Keyless Public Providers (0 Config) | Built-in Offline Fallback | Supported Languages |
| :--- | :--- | :--- | :--- | :--- |
| 🕹️ **Retro Games** (`/api/v1/games`) | **ScreenScraper.fr** (`SCREENSCRAPER_USER` / `PASS`) | **Steam Store API** (PC / Arcade ports) | Curated Arcade Classics (12+ titles) | `fr`, `en`, `es`, `de`, `it`, `pt` |
| 🎬 **Movies** (`/api/v1/movies`) | **TMDB API v3** (`TMDB_API_KEY`) | — | Curated Cinema Classics (10+ titles) | `fr-FR`, `en-US`, `es-ES`, `de-DE`, `it-IT`, `ja-JP` |
| 📺 **TV Series** (`/api/v1/series`) | **TMDB TV** (`TMDB_API_KEY`) & **TheTVDB v4** (`TVDB_API_KEY`) | **TVMaze Public API** | Curated Cult Series (10+ titles) | `fr-FR`, `en-US`, `es-ES`, `de-DE`, `it-IT` |
| ⛩️ **Anime** (`/api/v1/anime`) | **TMDB** (`TMDB_API_KEY` for FR/ES/DE synopses) | **AnimeThemes.moe** (OP/ED WebM) + **Jikan v4** (MyAnimeList) | Curated Anime Classics (10+ titles) | `fr`, `en`, `es`, `de`, `it`, `ja` |

---

## 2. Zero-Config Mode (Out-of-the-Box)

If you start the container **without configuring any `.env` file**, the server operates in **Zero-Config Mode**:
- **Games:** Combines the built-in multilingual Arcade Classics catalog (Pac-Man, Metal Slug, Street Fighter II, Chrono Trigger, Castlevania: Symphony of the Night, Sonic 2, OutRun, etc.) with live queries to the keyless **Steam Store API**.
- **Movies:** Uses the built-in multilingual Cult Cinema catalog (Blade Runner, Back to the Future, The Matrix, Terminator 2, Jurassic Park, Akira, Mad Max: Fury Road, Alien, etc.).
- **Series:** Enriches the built-in Cult Series catalog (Stranger Things, Breaking Bad, The X-Files, K2000 / Knight Rider, The Mandalorian, MacGyver, Batman TAS, etc.) with live high-resolution posters from **TVMaze**.
- **Anime:** Uses the keyless **AnimeThemes.moe** and **Jikan (MyAnimeList v4)** APIs alongside the built-in multilingual Anime catalog (Dragon Ball Z, Neon Genesis Evangelion, Cowboy Bebop, One Piece, Attack on Titan, Saint Seiya, Goldorak / UFO Robot Grendizer, etc.).

---

## 3. Step-by-Step: Getting Your Free API Keys

### A. TMDB (The Movie Database) — Movies, Series & Localized Anime Synopses
1. Create a free account at **[https://www.themoviedb.org/signup](https://www.themoviedb.org/signup)**.
2. Open **Settings → API** (`https://www.themoviedb.org/settings/api`) and request a free **Developer API Key**.
3. Copy the **API Key (v3 auth)** into your `.env` file:
   ```env
   TMDB_API_KEY=your_32_char_tmdb_v3_api_key
   ```
4. **Why it matters:** Unlocks tens of thousands of movies, TV series, and anime with official localized titles, posters, and synopses (`language=fr-FR`, `en-US`, `es-ES`, `de-DE`, `it-IT`).

### B. ScreenScraper.fr — Retro Arcade & Console Video Snaps (`video-normalized`)
1. Create a free member account at **[https://www.screenscraper.fr/](https://www.screenscraper.fr/)**.
2. Add your member credentials to `.env`:
   ```env
   SCREENSCRAPER_USER=your_username
   SCREENSCRAPER_PASS=your_password
   # Optional (if you also have a ScreenScraper developer ID):
   SCREENSCRAPER_DEV_ID=
   SCREENSCRAPER_DEV_PASS=
   ```
3. **Why it matters:** ScreenScraper is the gold-standard retrogaming database (used by Batocera, Recalbox, RetroBat). It provides localized descriptions (`synopsis-fr`, `synopsis-en`, `synopsis-es`, `synopsis-de`), authentic arcade `wheel` marquees, `box-2D` covers, and pre-cropped gameplay `video-normalized` MP4 snaps.

### C. TheTVDB v4 (Optional) — Classic & International TV Series
1. Request a free project API key at **[https://thetvdb.com/api-information](https://thetvdb.com/api-information)**.
2. Set `TVDB_API_KEY` in `.env`:
   ```env
   TVDB_API_KEY=your_tvdb_v4_key
   ```

### D. Keyless Providers (Enabled Automatically)
- **Steam Store API (`store.steampowered.com/api`):** Public JSON endpoint returning direct `.webm` / `.mp4` gameplay micro-trailers and header capsules.
- **AnimeThemes.moe (`api.animethemes.moe`):** Community-curated encyclopedia of creditless Anime Openings (`OP`) and Endings (`ED`).
- **Jikan v4 (`api.jikan.moe/v4`):** Open-source REST gateway to MyAnimeList.
- **TVMaze (`api.tvmaze.com`):** Free television metadata and artwork API.
