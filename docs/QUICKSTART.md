# QuickStart & Deployment Guide — DMD Media Showcase Server (V3.0)

🇬🇧 English | 🇫🇷 [Français](QUICKSTART_FR.md) | 🇪🇸 [Español](QUICKSTART_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Deployment Modes

### Mode A: Docker Compose (Linux / Raspberry Pi 4 & 5 / NAS)

```yaml
services:
  dmd-media-showcase:
    build: .
    container_name: dmd-media-showcase
    restart: unless-stopped
    ports:
      - "8080:8080"       # FastAPI REST + Web Admin Dashboard + Swagger UI
      - "1883:1883"       # Embedded MQTT 3.1.1 Control Plane Broker
    volumes:
      - ./data:/app/data  # Persistent server_config.json, seen_history.json & referentials/
    environment:
      - LOG_LEVEL=INFO
      - MAX_WORKERS=2
```

Launch with:
```bash
docker compose up -d --build
docker logs -f dmd-media-showcase
```

> [!TIP]
> **Docker Bridge NAT & UDP Streaming (`client_ip`)**: Both `ArcadeMatrix_RPi` and `ArcadeMatrix` (ESP32) automatically probe and declare their real LAN IP (`client_ip` / `udp_ip`) inside their MQTT `config` and `start_stream` payloads so `ffmpeg -re` streams `DMDF` UDP packets directly to your DMD even when Docker runs in default bridge mode (`172.18.0.1`).

---

### Mode B: Portable Hermetic Bundle (Windows 10/11 & macOS)

1. Download `DMD_Media_Showcase_Windows_x64.zip` or `DMD_Media_Showcase_macOS.zip` from GitHub Releases.
2. Extract to any folder (e.g. `C:\DMD_Media_Showcase\`).
3. Run `dmd-media-showcase.exe` (Windows) or `./dmd-media-showcase` (macOS).
4. All runtime state is isolated inside `./data/` (`server_config.json`, `seen_history.json`, `referentials/`, `stream_tmp/`). Host `PYTHONPATH` and `PYTHONHOME` are automatically stripped (`enforce_hermetic_environment()`).

---

## 2. Web Admin Dashboard (`http://<server-ip>:8080/`)

Open `http://<server-ip>:8080/` in your browser to:
- Configure **optional** Primary scraper credentials:
  - **ScreenScraper** (`user`, `pass`, `dev_id`, `dev_pass`) for `games`. Without credentials, `ArcadeItalia` (MAME HTTPS) + `Libretro+Wikidata` (Console SPARQL + GitHub Tree) operate automatically with zero configuration.
  - **TMDB API Key** (`tmdb_api_key`) for `movies` and `series`. Without a key, `Apple iTunes` and `TVMaze` operate automatically.
- Choose **Embedded MQTT Broker** (`:1883`, default) or connect to an **External MQTT Broker** (e.g. Mosquitto / Home Assistant).
- View per-domain **Seen History counters** and reset `seen_history.json` per domain or globally.
- Trigger a live **Scraper Referential Sync** (`POST /api/v1/admin/referentials/sync`).

---

## 3. Connecting Your DMD (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`)

1. Open your DMD WebUI (`http://<dmd-ip>/`).
2. Enable any of the 4 Showcase engines (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`).
3. Set `Showcase Server URL` to `http://<server-ip>:8080` and `MQTT Broker` to `mqtt://<server-ip>:1883`.
4. The DMD WebUI automatically queries `GET /api/v1/{domain}/filters?lang={ui_lang}` from the Showcase server to populate the **Systems**, **Genres**, and **Decades** dropdowns dynamically (filtering available genres/decades when you select a system such as `NES` or `Neo-Geo`).
5. Adjust **Synopsis Scroll Speed** (`1 = Slow` to `10 = Turbo`, up to `160 px/s`) and click **Save**.

