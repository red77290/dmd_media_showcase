# DMD Media Showcase Server

🇬🇧 [English](README.md) | 🇫🇷 [Français](README_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Comunidad de Telegram:** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Apoya el proyecto en Ko-fi:** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** es un microservicio autoalojado de descubrimiento multimedia sin repetición, transcodificación y streaming en tiempo real por MQTT/UDP diseñado para paneles LED HUB75 **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** y **ArcadeMatrix_RPi**.

---

## ✨ Características Principales

- **4 Dominios Showcase (`games`, `movies`, `series`, `anime`)**:
  - **Retro Games**: 30 sistemas Arcade, Consolas, Portátiles y Microordenadores (`ScreenScraper` Primario $\to$ `ArcadeItalia` MAME HTTPS + `Libretro+Wikidata` SPARQL Consola como Fallback sin clave).
  - **Movies**: Cine Clásico y Taquillazos (`TMDB` Primario $\to$ `Apple iTunes` Fallback).
  - **TV Series**: Series de Culto y Modernas (`TMDB` Primario $\to$ `TVMaze` Fallback).
  - **Anime / Manga**: Openings (`OP`), Endings (`ED`) sin créditos y Tráilers (`Jikan + AnimeThemes` Primario $\to$ `AniList` Fallback).
- **Referenciales Dinámicos Consolidados**: Expone catálogos de categorías localizados (`fr`, `en`, `es`, `de`, `it`) e independientes del scraper (`GET /api/v1/{domain}/filters?lang=es` y MQTT `dmd_showcase/catalog/{domain}/referential`) con relación `Sistema -> Géneros y Décadas compatibles`.
- **Plano de Control MQTT en Tiempo Real (`:1883`) + Streaming UDP `DMDF` vía `ffmpeg -re`**:
  - Broker MQTT 3.1.1 integrado sin configuración (`retain=True` + `Last Will and Testament`).
  - Streaming UDP `DMDF` sincronizado por reloj mediante `ffmpeg -re` directamente a la IP LAN real del DMD (`client_ip` / `udp_ip`, evitando el NAT de Docker Bridge).
- **Protocolo Consume-Once Estricto y Pipelining `N+1` en Segundo Plano**:
  - Registra cada título mostrado en `./data/seen_history.json` (cero repeticiones hasta agotar el catálogo).
  - Prepara el elemento `N+1` en segundo plano en cuanto el elemento `N` muestra su ficha (`start_card`) y elimina del disco los archivos consumidos al recibir `stream_eof`.
- **Impulsado por `dmd_gif_converter`**: Encuadre de vídeo ortogonal (`auto_action` IA Let Me Handle It, `panorama` barrido vertical, `center_crop`) y colorimetría LED HUB75 (`auto` Smart Color Boost IA, `pixel_art`, `anime`, `cinema`), reescalado Lanczos + `unsharp` y panoramas verticales fluidos (`Carátula`, `Marquee`, `Capturas`) en todas las geometrías Horizontal (`128x32`, `128x64`, `256x64`), Cuadrada (`64x64`) y TATE (`64x128`, `32x128`).

---

## 🚀 Instalación Rápida y Ejemplo Corto

### Opción 1: Docker Compose (Recomendado para Raspberry Pi / NAS / Home-Lab)

```bash
git clone https://github.com/red77290/dmd_media_showcase.git
cd dmd_media_showcase
docker compose up -d --build
```

- **Panel Web de Administración**: `http://dmdmediashowcase.local:8890/` (o `http://<ip-servidor>:8890/`)
- **Swagger UI Interactivo**: `http://dmdmediashowcase.local:8890/docs`
- **Broker MQTT Integrado**: `mqtt://dmdmediashowcase.local:1883`
- **Autodescubrimiento en Red Local (mDNS)**: Anuncia automáticamente `dmdmediashowcase.local` (`224.0.0.251:5353`) para que su DMD (`ArcadeMatrix` ESP32 o `ArcadeMatrix_RPi`) lo detecte sin introducir una IP. Basta con activar el interruptor Showcase en la Web UI del DMD (`Settings -> Showcase Server`), verificar el indicador **`🟢 Comunicación OK`** y añadir los motores Showcase a su rotación.

### Opción 2: Paquete Portátil Autónomo (Windows / macOS)

Descargue el archivo portátil (`DMD_Media_Showcase_Windows_x64.zip` o `DMD_Media_Showcase_macOS.zip`) desde **[GitHub Releases](https://github.com/red77290/dmd_media_showcase/releases/latest)**, descomprímalo y ejecute `dmd-media-showcase`.

### Ejemplo Rápido de Verificación

```bash
# 1. Comprobar el estado del servidor, workers y puerto MQTT
curl -s http://localhost:8890/api/v1/health | jq .

# 2. Obtener el referencial consolidado en español para Retro Games
curl -s "http://localhost:8890/api/v1/games/filters?lang=es" | jq '.systems[0], .genres[:3]'
```

---

## 📚 Documentación y Especificaciones API

| Documento | 🇬🇧 English | 🇫🇷 Français | 🇪🇸 Español |
| :--- | :--- | :--- | :--- |
| **Guía de Inicio Rápido (QuickStart)** | [docs/QUICKSTART.md](docs/QUICKSTART.md) | [docs/QUICKSTART_FR.md](docs/QUICKSTART_FR.md) | [docs/QUICKSTART_ES.md](docs/QUICKSTART_ES.md) |
| **Arquitectura DDD y Pipeline de Streaming** | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/ARCHITECTURE_FR.md](docs/ARCHITECTURE_FR.md) | [docs/ARCHITECTURE_ES.md](docs/ARCHITECTURE_ES.md) |
| **Guía para Desarrolladores y Scrapers** | [docs/DEVELOPER.md](docs/DEVELOPER.md) | [docs/DEVELOPER_FR.md](docs/DEVELOPER_FR.md) | [docs/DEVELOPER_ES.md](docs/DEVELOPER_ES.md) |
| **Especificación REST OpenAPI 3.1** | [docs/openapi.yaml](docs/openapi.yaml) / [docs/openapi.json](docs/openapi.json) | — | — |
| **Especificación MQTT y UDP AsyncAPI 2.6** | [docs/asyncapi.yaml](docs/asyncapi.yaml) | — | — |

