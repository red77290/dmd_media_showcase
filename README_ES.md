# DMD Media Showcase Server

🇬🇧 [English](README.md) | 🇫🇷 [Français](README_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Comunidad de Telegram:** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Apoya el proyecto en Ko-fi:** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** es un microservicio autoalojado de descubrimiento multimedia sin repetición, transcodificación y streaming en tiempo real por MQTT/UDP diseñado para paneles LED HUB75 **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** y **ArcadeMatrix_RPi**.

---

## 🎬 Demostración en Vivo sobre Matriz LED HUB75 e Interfaz Web

### 🕹️ Secuencias Reales en Matriz LED HUB75 (Generadas en Vivo mediante la API Showcase)

Cada título ensambla automáticamente una emisión RGB565 multicapítulo adaptada a la resolución exacta de su panel:
**`1. Ficha Informativa Animada`** (carátula, insignia de sistema, año, puntuación y texto deslizante) $\to$ **`2. Panoramas Verticales de Carátula y Marquee`** $\to$ **`3. Barrido de Captura In-Game`** $\to$ **`4. Vídeo de Gameplay / Tráiler Encuadrado por IA`**.

| **Garou: Mark of the Wolves (`Neo Geo`)** | **Super Street Fighter II X / Turbo (`CPS2`)** | **Metal Slug X (`Neo Geo`)** |
| :---: | :---: | :---: |
| ![Garou: Mark of the Wolves Demo DMD HUB75](demo/dmd_garou_motw_neogeo.gif) | ![Super Street Fighter II X CPS2 Demo DMD HUB75](demo/dmd_ssf2x_cps2.gif) | ![Metal Slug X Neo Geo Demo DMD HUB75](demo/dmd_metal_slug_x_neogeo.gif) |
| `Classic Split` • `Tinted` • `auto_action` + `pixel_art` | `Badge Header` • `Neon Border` • `auto_action` + `pixel_art` | `Classic Split` • `Poster Wash` • `auto_action` + `pixel_art` |

### 🖥️ Centro de Control Web Multilingüe (`ES` / `EN` / `FR`)

| **1. Vista General en Vivo y Matrices HUB75 Conectadas** | **2. Estudio Card y Simulador LED HUB75 en Tiempo Real** |
| :---: | :---: |
| [![Vista General y Matrices DMD Conectadas](demo/ui_overview_live.png)](demo/ui_overview_live.png) | [![Estudio Card Modo 2B y Simulador HUB75](demo/ui_card_studio.png)](demo/ui_card_studio.png) |
| Telemetría MQTT (`:1883`) + UDP en vivo, ranuras precalculadas `Ready (N)` y `Standby (N+1)` en los 4 dominios, historial Consume-Once y fuentes `[Primaria, Respaldo]`. | Perfiles por dominio (`Games`, `Movies`, `Series`, `Anime`), 5 diseños pixel-art (YOKO y TATE), simulador LED SMD en vivo e inyección en `0 ms` en la cola activa. |
| **3. Búsqueda Multicatálogo y Cola Prioritaria On-Demand** | **4. Perfiles DMD Pasivos y Selector Visual de Etiquetas** |
| [![Búsqueda en Catálogo y Cola On-Demand](demo/ui_ondemand_search.png)](demo/ui_ondemand_search.png) | [![Rotación DMD Pasiva y Etiquetas Multiselección](demo/ui_passive_rotation.png)](demo/ui_passive_rotation.png) |
| Busque Juegos, Películas, Series y Anime en el idioma deseado y envíelos mediante `Queue Next` (`FIFO`) o emisión inmediata `Play Now`. | Configure listas Modo 2A/2B con etiquetas visuales multiselección (Sistemas, Géneros y Décadas) y acceso directo a cada perfil del Estudio. |

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
- **Perfiles ESP32 Pasivos Gestionados en el Servidor (`dmd_profiles.json`) y Cola Bajo Demanda**:
  - Soporta tanto DMDs multimotores completos (`ArcadeMatrix`) **como** receptores pasivos UDP `:4210` ultramínimos (~180 líneas C++, cero UI, cero PSRAM en `ESP32_STD`, `supports_local_card: false` donde incluso la Ficha Informativa de la Fase 1 se renderiza en el stream RGB565) configurados en `./data/dmd_profiles.json`.
  - Búsqueda de catálogo localizada (`GET /api/v1/{domain}/search`), cola prioritaria por DMD (`enqueue_next` / `play_now`), controles en vivo `Skip` / `Pause` / `Resume` / `Stop` e integración **`FrontendSync`** (`mark_seen: false` para Batocera / RetroBat / LaunchBox / Playnite).
- **Impulsado por `dmd_gif_converter`**: Encuadre de vídeo ortogonal (`auto_action` IA Let Me Handle It, `panorama` barrido vertical, `center_crop`) y colorimetría LED HUB75 (`auto` Smart Color Boost IA, `pixel_art`, `anime`, `cinema`), reescalado Lanczos + `unsharp` y panoramas verticales fluidos (`Carátula`, `Marquee`, `Capturas`) en todas las geometrías Horizontal (`128x32`, `128x64`, `256x64`), Cuadrada (`64x64`) y TATE (`64x128`, `32x128`).
- **Notificación de Nuevas Versiones según el Sistema Operativo**: Comprobación integrada ligera (`GET /api/v1/system/update-check`) que muestra un aviso discreto en la Web UI cuando existe una nueva versión oficial en GitHub Releases para su entorno activo (`macOS .dmg`, `Windows .zip`, `Linux .tar.gz` o `Docker`) con enlace directo a la versión y al instalador correspondiente.

---

## 🚀 Instalación Rápida y Ejemplo Corto

### Opción 1: Imagen Docker Oficial Multi-Arch (`amd64` / `arm64` — Raspberry Pi / NAS / Home-Lab)

#### A. Docker Compose (Recomendado — sin `git clone` ni compilación local)

Descargue el archivo [`docker-compose.yml`](docker-compose.yml) listo para usar e inicie el servidor:

```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
```

<details>
<summary>📄 Ver <code>docker-compose.yml</code> autónomo</summary>

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

#### B. Comando `docker run` Directo (Bind Mount del Host o Volumen con Nombre en Portainer)

```bash
# Opción B1: Montaje de carpeta del host (./data — inmune a `docker compose down -v`)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest

# Opción B2: Volumen con nombre de Docker (`dmd_showcase_data` — ideal para Portainer / despliegues autónomos)
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

> **Persistencia de Datos (`/app/data`)**: Almacena `server_config.json`, `dmd_profiles.json` (perfiles DMD gestionados en el servidor e idioma de emisión), `seen_history.json` y `referentials/`. El montaje `${DMD_DATA_PATH:-./data}:/app/data` apunta a un directorio del host (personalizable mediante `DMD_DATA_PATH` en `.env`) **protegido frente a `docker compose down -v`**, mientras que `-v dmd_showcase_data:/app/data` utiliza un volumen gestionado por Docker ideal para Portainer.

- **Panel Web de Administración**: `http://dmdmediashowcase.local:8890/` (o `http://<ip-servidor>:8890/`)
- **Swagger UI Interactivo**: `http://dmdmediashowcase.local:8890/docs`
- **Broker MQTT Integrado**: `mqtt://dmdmediashowcase.local:1883`
- **Autodescubrimiento en Red Local (mDNS)**: Anuncia automáticamente `dmdmediashowcase.local` (`224.0.0.251:5353`) para que su DMD (`ArcadeMatrix` ESP32 o `ArcadeMatrix_RPi`) lo detecte sin introducir una IP. Basta con activar el interruptor Showcase en la Web UI del DMD (`Settings -> Showcase Server`), verificar el indicador **`🟢 Comunicación OK`** y añadir los motores Showcase a su rotación.

### Opción 2: Instaladores Nativos y Paquetes Portátiles (macOS `.dmg` / Windows `.zip` / Linux `.tar.gz`)

Descargue el paquete para su sistema desde **[GitHub Releases](https://github.com/red77290/dmd_media_showcase/releases/latest)** (sin necesidad de Python ni FFmpeg en el equipo):
- **macOS (Apple Silicon `arm64`)**: Abra **`DMD_Media_Showcase_macOS_arm64.dmg`**, arrastre **`DMD Media Showcase.app`** a **`Applications`** e iníciela (el icono `▣ DMD` aparece en la barra de menús de macOS y abre `http://localhost:8890/`). *(Primer inicio sin notarización de Apple: Clic derecho → **Abrir**, o ejecute `xattr -cr "/Applications/DMD Media Showcase.app"` una vez).*
- **Windows (`x64`)**: Extraiga **`DMD_Media_Showcase_Windows_x64.zip`** y ejecute `Start_Showcase_Windows.bat`.
- **Linux (`x64`)**: Extraiga **`DMD_Media_Showcase_Linux_x64.tar.gz`** y ejecute `./start_showcase.sh`.

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
| **Guía de Integración ESP32 y Receptor Pasivo** | [docs/ESP32_INTEGRATION.md](docs/ESP32_INTEGRATION.md) | [docs/ESP32_INTEGRATION_FR.md](docs/ESP32_INTEGRATION_FR.md) | [docs/ESP32_INTEGRATION_ES.md](docs/ESP32_INTEGRATION_ES.md) |
| **Arquitectura del Sistema y Pipeline de Streaming** | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/ARCHITECTURE_FR.md](docs/ARCHITECTURE_FR.md) | [docs/ARCHITECTURE_ES.md](docs/ARCHITECTURE_ES.md) |
| **Guía para Desarrolladores y Scrapers** | [docs/DEVELOPER.md](docs/DEVELOPER.md) | [docs/DEVELOPER_FR.md](docs/DEVELOPER_FR.md) | [docs/DEVELOPER_ES.md](docs/DEVELOPER_ES.md) |
| **Especificación REST OpenAPI 3.1** | [docs/openapi.yaml](docs/openapi.yaml) / [docs/openapi.json](docs/openapi.json) | — | — |
| **Especificación MQTT y UDP AsyncAPI 2.6** | [docs/asyncapi.yaml](docs/asyncapi.yaml) | — | — |

