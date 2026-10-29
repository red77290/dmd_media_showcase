# DMD Media Showcase Server

🇬🇧 [English](README.md) | 🇫🇷 [Français](README_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Comunidad de Telegram:** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Apoya el proyecto en Ko-fi:** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** es un microservicio autoalojado de descubrimiento multimedia sin repetición, transcodificación y streaming en tiempo real **MQTT + HTTP TCP `DMDS`** (además de UDP pasivo `DMDF`) diseñado para paneles LED HUB75 **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** y **ArcadeMatrix_RPi**.

---

## 🎬 Demostración en Vivo sobre Matriz LED HUB75 e Interfaz Web

### 🕹️ Secuencias Reales en Matriz LED HUB75 128×32 (Generadas en Vivo mediante la API Showcase)

Cada título ensambla automáticamente una emisión RGB565 multicapítulo adaptada a la resolución exacta de su panel (`128×32` DMD clásico `4:1` abajo):
**`1. Ficha Informativa Animada`** (carátula, insignia de sistema, desplazamiento automático de año/puntuación y texto deslizante) $\to$ **`2. Panoramas Zoom-to-Cover de Carátula y Marquee`** $\to$ **`3. Barrido de Captura In-Game`** $\to$ **`4. Vídeo de Gameplay / Tráiler Encuadrado por IA`**.

| **Garou: Mark of the Wolves (`Neo Geo` • `128×32`)** | **Super Street Fighter II X / Turbo (`CPS2` • `128×32`)** | **Metal Slug X (`Neo Geo` • `128×32`)** |
| :---: | :---: | :---: |
| ![Garou: Mark of the Wolves Demo DMD HUB75 128x32](demo/dmd_garou_motw_neogeo.gif) | ![Super Street Fighter II X CPS2 Demo DMD HUB75 128x32](demo/dmd_ssf2x_cps2.gif) | ![Metal Slug X Neo Geo Demo DMD HUB75 128x32](demo/dmd_metal_slug_x_neogeo.gif) |
| `Classic Split` • `Tinted` • `auto_action` + `pixel_art` | `Badge Header` • `Neon Border` • `auto_action` + `pixel_art` | `Classic Split` • `Poster Wash` • `auto_action` + `pixel_art` |

### 🖥️ Centro de Control Web Multilingüe (`ES` / `EN` / `FR`)

| **1. Vista General en Vivo y Matrices HUB75 Conectadas** | **2. Estudio Card y Simulador LED HUB75 en Tiempo Real** |
| :---: | :---: |
| [![Vista General y Matrices DMD Conectadas](demo/ui_overview_live.png)](demo/ui_overview_live.png) | [![Estudio Card Modo 2B y Simulador HUB75](demo/ui_card_studio.png)](demo/ui_card_studio.png) |
| Telemetría MQTT (`:1883`) + transportes en vivo (`MQTT+TCP` por defecto, `HTTP`, `UDP-PASSIVE`, `MQTT+UDP` opcional), ranuras precalculadas `Ready (N)` y `Standby (N+1)` con insignias de fuente en vivo (`🔗 igdb+screenscraper`), historial Consume-Once y cadenas multifuente editables en caliente. | Perfiles por dominio (`Games`, `Movies`, `Series`, `Anime`), 5 diseños pixel-art (YOKO y TATE), simulador LED SMD en vivo e inyección en `0 ms` en la cola activa. |
| **3. Búsqueda Multicatálogo y Cola Prioritaria On-Demand** | **4. Perfiles DMD Pasivos y Selector Visual de Etiquetas** |
| [![Búsqueda en Catálogo y Cola On-Demand](demo/ui_ondemand_search.png)](demo/ui_ondemand_search.png) | [![Rotación DMD Pasiva y Etiquetas Multiselección](demo/ui_passive_rotation.png)](demo/ui_passive_rotation.png) |
| Busque Juegos (incluidos alias cortos como `sf2x`, `3s`, `kof98`), Películas, Series y Anime en el idioma deseado y envíelos mediante `Queue Next` (`FIFO`) o emisión inmediata `Play Now`. | Configure listas Modo 2A/2B con etiquetas visuales multiselección (Sistemas, Géneros y Décadas) y acceso directo a cada perfil del Estudio. |

---

## ✨ Características Principales

- **4 Dominios Showcase y Cadenas Multifuente Configurables (`games`, `movies`, `series`, `anime`)**:
  - **Retro & Arcade Games (`55+` / `66` Sistemas Canónicos)**: Placas Arcade (`MAME`, `CPS-1/2/3`, `Neo-Geo`, `NAOMI`, `Atomiswave`, `Model 2/3`), Consolas, Portátiles y Microordenadores impulsados por **4 fuentes independientes seleccionables**:
    1. **`igdb`** (`IGDB API v4` mediante OAuth2 gratuito de Twitch — más de 200 plataformas, búsqueda instantánea por acrónimos/alias con `alternative_names`, muestreo aleatorio por offset, carátulas/artworks y tráilers).
    2. **`screenscraper`** (`ScreenScraper` — wheels/marquees recortados, capturas pixel-art, vídeos cortos `.mp4`, con detección automática de cuota/sobrecarga HTTP-200 en texto plano y disyuntor de enfriamiento).
    3. **`arcadeitalia`** (scraper HTTPS sin clave de referencia para MAME/CPS/Neo-Geo/NAOMI con alias integrados que resuelven `sf2x`, `3s`, `kof98`, `garou` en una sola petición).
    4. **`libretro_wikidata`** (fuente principal sin clave para consolas/portátiles/microordenadores que combina Wikidata SPARQL, `libretro-thumbnails` verificados, sinopsis localizadas de Wikipedia y vídeos de Archive.org).
  - **Movies**: Cine Clásico y Taquillazos (`tmdb_movies` $\to$ `itunes_movies` sin clave).
  - **TV Series**: Series de Culto y Modernas (`tmdb_series` $\to$ `tvdb_series` $\to$ `tvmaze` sin clave).
  - **Anime / Manga**: Openings (`OP`), Endings (`ED`) sin créditos y Tráilers (`jikan` + `AnimeThemes` $\to$ `anilist` sin clave).
- **Prioridad Multifuente en Caliente y Agregación Cruzada (`source_policies`)**:
  - Reordene la prioridad (`#1 Primaria -> #2 Secundaria -> #3 Terciaria -> ...`) y active/desactive fuentes por dominio en vivo desde el Panel Web (**Vista General** y **Claves API y Scrapers**), persistido en `./data/server_config.json`. Cuando una fuente devuelve `0` resultados, agota el tiempo de espera, entra en enfriamiento o no soporta el sistema solicitado, la búsqueda pasa de forma determinista a la siguiente fuente habilitada.
  - Elija por dominio entre **`enrich_missing` (Agregación Multifuente, por defecto)** — donde la fuente Primaria resuelve la identidad canónica y los alias (p. ej. `IGDB` resolviendo `"sf2x"` en `cps2` $\to$ `"Super Street Fighter II Turbo"` / `ssf2t`) y `CandidateAggregator` ejecuta un enriquecimiento secundario acotado (`<= 3.0s`) para obtener marquees/wheels DMD, capturas pixel-art, flujos directos `.mp4`/`.webm`/`.m4v` y sinopsis localizadas con atribución combinada transparente (`source_used="igdb+screenscraper"`, `"igdb+arcadeitalia"`, `"tmdb_movies+itunes_movies"`) — y **`fallback_only` (Respaldo Estricto)**.
- **Referenciales Dinámicos Consolidados**: Expone catálogos de categorías localizados (`fr`, `en`, `es`, `de`, `it`) e independientes del scraper (`GET /api/v1/{domain}/filters?lang=es` y MQTT `dmd_showcase/catalog/{domain}/referential`) con relación `Sistema -> Géneros y Décadas compatibles`.
- **Streaming Principal `MQTT + TCP` (`DMDS` `:8890`) y Soporte UDP Pasivo `DMDF` (`:4210`)**:
  - **Plano de Control MQTT (`:1883`) + Streaming HTTP TCP `DMDS` Sincronizado en Tiempo Real (`:8890`, `MQTT+TCP` por defecto)**: Los Modelos 1 (`ArcadeMatrix` ESP32 / `ArcadeMatrix_RPi`) y 2A (`supports_local_card: true`) combinan el broker MQTT 3.1.1 integrado sin configuración (`config`, `ready`, `start_card` con `retain=True` + `LWT`) con streaming HTTP TCP `DMDS` sincronizado por el servidor (`GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge=true&paced=1&drop_late=1` — cabecera Little-Endian de 16 bytes `<4sBBHHHHH` + fotogramas RGB565LE marcados por `X-DMD-Paced: 1`).
  - **Por qué `MQTT + TCP` (`DMDS`) es el Transporte Recomendado por Defecto**: Cero pérdida de paquetes UDP por NAT de VM en Docker Desktop / Rancher Desktop, cero sobrecarga de reensamblado de fragmentos MTU, cero heap en el cliente (`lwip_socket` lee `HTTP/1.0` crudo directamente en `m_frame565` mientras `X-DMD-Paced: 1` regula los fotogramas en el servidor para que `TCP_WND` nunca se bloquee) e identificadores `item_id` compactos sin colisiones (`pkg_<domain>_<12hex>`, `<= 24` caracteres). Telemetría en vivo de transporte (`MQTT+TCP`, `HTTP`, `UDP-PASSIVE`, `MQTT+UDP` heredado opcional) y fuente por ranura (`playing_source`, `ready_source`, `standby_source`, `preparing_source`).
- **Protocolo Consume-Once Estricto y Pipelining `N+1` en Segundo Plano**:
  - Registra cada título mostrado en `./data/seen_history.json` (cero repeticiones hasta agotar el catálogo).
  - Prepara el elemento `N+1` en segundo plano en cuanto el elemento `N` muestra su ficha (`start_card`) y elimina del disco los archivos consumidos al completarse la respuesta HTTP TCP `DMDS` (`?purge=true`) o `stream_eof`.
- **Perfiles ESP32 Pasivos Gestionados en el Servidor (`dmd_profiles.json`) y Cola Bajo Demanda**:
  - Soporta DMDs multimotores completos (`ArcadeMatrix`, Modelo 1) y receptores híbridos gestionados en el servidor (Modelo 2A, `MQTT+TCP`) **además de** receptores pasivos UDP `:4210` `DMDF` ultramínimos opcionales (**Modelo 2B**, ~180 líneas C++, cero UI, cero PSRAM en `ESP32_STD`, `supports_local_card: false`, `UDP-PASSIVE` donde incluso la Ficha Informativa de la Fase 1 se renderiza en el stream RGB565) configurados en `./data/dmd_profiles.json`.
  - Búsqueda de catálogo localizada (`GET /api/v1/{domain}/search`), cola prioritaria asíncrona por DMD (`POST /api/v1/clients/{dmd_id}/queue/async`, `enqueue_next` / `play_now`), controles en vivo `Skip` / `Pause` / `Resume` / `Stop` e integración **`FrontendSync`** (`mark_seen: false` para Recalbox / Batocera / RetroPie / RetroBat / LaunchBox / Playnite).
- **Caché Persistente de Conversiones On-Demand y Vaciado en 1 Clic**:
  - Caché en disco persistente opcional (`Panel de Administración -> 2. Runtime, Workers y Límites de Vídeo -> Caché Persistente de Conversiones On-Demand`) indexada por **`(Sistema + Juego + Resolución del Panel + Estilo de Tarjeta/Vídeo)`** con cuota máxima configurable (MB) para arranque instantáneo (`0 ms`, vaciado en 1 clic con `POST /api/v1/cache/on-demand/evict`).
- **Impulsado por `dmd_gif_converter` (`>= 8.0.10`)**: Encuadre de vídeo ortogonal (`auto_action` IA Let Me Handle It, `panorama`, `center_crop`), colorimetría LED HUB75 (`auto` Smart Color Boost IA, `pixel_art`, `anime`, `cinema`), reescalado Lanczos + `unsharp` y **panoramas zoom-to-cover uniformes deterministas** sin bandas negras — **YOKO** (`128x32`, `128x64`, `256x64`) desplazándose de **arriba a abajo**, y **TATE** (`64x128`, `32x128`) desplazándose de **izquierda a derecha**.
- **Bóveda de Credenciales de 3 Niveles y Redacción Automática de Logs HTTP**: Resuelve credenciales en `1. Panel Web server_config.json` $\to$ `2. .env / docker-compose` $\to$ `3. Bóveda nativa C compilada en CI/CD (_prod_vault)`, protegida por `SecretRedactingLogFilter` (`httpx`/`httpcore` fijados en `WARNING`) para que claves API, secretos OAuth y contraseñas en URL nunca se filtren a los logs del contenedor.
- **Notificación de Nuevas Versiones según el Sistema Operativo**: Comprobación integrada ligera (`GET /api/v1/system/update-check`) que muestra un aviso discreto en la Web UI cuando existe una nueva versión oficial en GitHub Releases para su entorno activo (`macOS .dmg`, `Windows .zip`, `Linux .tar.gz` o `Docker`).

---

## 🚀 Instalación Rápida y Ejemplo Corto

### Opción 1: Imagen Docker Oficial Multi-Arch (`amd64` / `arm64` — Raspberry Pi / NAS / Home-Lab)

#### A. Docker Compose (Recomendado — sin `git clone` ni compilación local)

Descargue el archivo [`docker-compose.yml`](docker-compose.yml) listo para usar e inicie el servidor:

```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
```

> **Compilación Local desde Código Fuente (`git clone`):** Dentro de un repositorio clonado localmente, [`docker-compose.override.yml`](docker-compose.override.yml) se carga automáticamente para que `docker compose up -d --build` compile su código local y la última versión de `dmd_gif_converter` en lugar de descargar la imagen remota.

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

> **Persistencia de Datos (`/app/data`)**: Almacena `server_config.json` (credenciales de scrapers, ajustes de runtime y políticas multifuente `source_policies`), `dmd_profiles.json` (perfiles DMD gestionados en el servidor e idioma de emisión), `seen_history.json` y `referentials/`. El montaje `${DMD_DATA_PATH:-./data}:/app/data` apunta a un directorio del host (personalizable mediante `DMD_DATA_PATH` en `.env`) **protegido frente a `docker compose down -v`**, mientras que `-v dmd_showcase_data:/app/data` utiliza un volumen gestionado por Docker ideal para Portainer.

- **Panel Web de Administración**: `http://dmdmediashowcase.local:8890/` (o `http://<ip-servidor>:8890/` — ordene cadenas multifuente, configure claves opcionales de IGDB / ScreenScraper / TMDB / TheTVDB y restablezca `seen_history.json`)
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
# 1. Comprobar el estado del servidor, cadenas multifuente activas, workers y puerto MQTT
curl -s http://localhost:8890/api/v1/health | jq .

# 2. Obtener el referencial consolidado en español para Retro Games (55+ Sistemas -> Géneros y Décadas)
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
