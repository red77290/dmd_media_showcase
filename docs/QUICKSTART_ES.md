# Guía de Inicio Rápido (QuickStart) — DMD Media Showcase Server

🇬🇧 [English](QUICKSTART.md) | 🇫🇷 [Français](QUICKSTART_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Modos de Despliegue

### Modo A: Imagen Docker Oficial Multi-Arch (Linux / Raspberry Pi 4 y 5 / NAS)

#### Opción 1 — `docker-compose.yml` (Recomendado)

```yaml
services:
  dmd-media-showcase:
    image: ghcr.io/red77290/dmd_media_showcase:latest
    pull_policy: always
    container_name: dmd-media-showcase
    restart: unless-stopped
    ports:
      - "8890:8890"       # API REST FastAPI + Panel Admin + Swagger UI
      - "1883:1883"       # Broker MQTT 3.1.1 Integrado
      - "5353:5353/udp"   # Autodescubrimiento en Red Local mDNS (dmdmediashowcase.local)
    volumes:
      - ${DMD_DATA_PATH:-./data}:/app/data  # Persistencia server_config.json, dmd_profiles.json, seen_history.json y referentials/
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```

Iniciar directamente con el [`docker-compose.yml`](../docker-compose.yml) del repositorio:
```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
docker logs -f dmd-media-showcase
```

#### Opción 2 — Comando `docker run` Directo (Bind Mount del Host o Volumen con Nombre en Portainer)

```bash
# Opción 2A: Montaje de carpeta del host (./data — protegido frente a `docker compose down -v`)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest

# Opción 2B: Volumen con nombre de Docker (`dmd_showcase_data` — ideal para Portainer / despliegues autónomos)
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

#### Persistencia de Datos y Protección de Volúmenes (`/app/data`)

Todos los archivos persistentes del servidor residen en `/app/data`:
- `server_config.json`: Credenciales cifradas de scrapers, concurrencia de workers y límites de vídeo.
- `dmd_profiles.json`: Perfiles DMD gestionados en el servidor (`config_source: "server"`), listas de reproducción, filtros e idioma de emisión (`lang`).
- `seen_history.json`: Historial Consume-Once por dominio (cero repeticiones).
- `referentials/*.json`: Caché local de catálogos de categorías de los scrapers.

El montaje **`${DMD_DATA_PATH:-./data}:/app/data`** en `docker-compose.yml` utiliza una carpeta real del host (configurable mediante `DMD_DATA_PATH=/mnt/nas/dmd_showcase` en `.env`), **100 % inmune a `docker compose down -v`** (`-v` solo elimina volúmenes con nombre de Docker, nunca directorios del host). Para **Portainer** o despliegues sin rutas locales, utilice un volumen con nombre (`-v dmd_showcase_data:/app/data`) y evite la bandera `-v` al detener el stack.

> [!TIP]
> **NAT de Docker Bridge y Streaming UDP (`client_ip`)**: Tanto `ArcadeMatrix_RPi` como `ArcadeMatrix` (ESP32) detectan y declaran automáticamente su IP LAN real (`client_ip` / `udp_ip`) en sus mensajes MQTT `config` y `start_stream`, permitiendo que `ffmpeg -re` envíe paquetes UDP `DMDF` directamente al DMD incluso en modo bridge (`172.18.0.1`).

---

### Modo B: Instaladores Nativos y Paquetes Portátiles (macOS `.dmg`, Windows `.zip`, Linux `.tar.gz`)

1. **macOS (Apple Silicon `arm64`)**:
   - Descargue **`DMD_Media_Showcase_macOS_arm64.dmg`** desde GitHub Releases, ábralo y arrastre **`DMD Media Showcase.app`** a **`Applications`**.
   - Inicie **`DMD Media Showcase.app`**: el icono `▣ DMD` aparece en la barra de menús de macOS, abre `http://localhost:8890/` y guarda los datos en `~/Library/Application Support/DMDMediaShowcase/data/`.
   - *Nota Gatekeeper en el primer inicio*: Clic derecho en `DMD Media Showcase.app` → **Abrir** (o ejecute `xattr -cr "/Applications/DMD Media Showcase.app"` una vez).
2. **Windows 10/11 (`x64`)**:
   - Descargue **`DMD_Media_Showcase_Windows_x64.zip`**, extráigalo y ejecute `Start_Showcase_Windows.bat`.
3. **Linux (`x64`)**:
   - Descargue **`DMD_Media_Showcase_Linux_x64.tar.gz`**, extráigalo y ejecute `./start_showcase.sh`.

---

## 2. Panel Web de Administración (`http://dmdmediashowcase.local:8890/`)

Abra `http://dmdmediashowcase.local:8890/` (o `http://<ip-servidor>:8890/`) en su navegador para:
- **Ajustar en caliente Runtime, Workers y Límites de Vídeo en Tiempo Real** (`2. Runtime, Workers y Límites de Vídeo`, guardado en `./data/server_config.json` sin reiniciar el servidor): número de workers concurrentes (`max_concurrent_jobs`), modo de ejecutor (`ProcessPoolExecutor` / `ThreadPoolExecutor`), hilos FFmpeg, prioridad CPU `nice`, duración máxima de vídeo (`video_max_duration_limit_s`), FPS máximos (`video_max_fps_limit`), duración de panoramas (`panorama_duration_s`), tamaño máximo de descarga, caché, TTLs, puerto MQTT, mDNS y nivel de log en vivo.
- Configurar credenciales **opcionales** de scrapers primarios (`ScreenScraper`, `TMDB`). Sin credenciales, los adaptadores sin clave (`ArcadeItalia`, `Libretro+Wikidata`, `iTunes`, `TVMaze`, `Jikan+AnimeThemes`, `AniList`) funcionan automáticamente.
- Elegir entre el **Broker MQTT Integrado** (`:1883`) o un **Broker MQTT Externo**.
- **Gestionar Perfiles DMD en el Servidor (`./data/dmd_profiles.json`)**: Configurar listas de reproducción (`games`, `movies`, `series`, `anime`), filtros (`systems`, `genres`, `decades`), ajustes de vídeo/color e idioma de emisión (`lang`) para receptores ESP32 pasivos o híbridos (`PUT /api/v1/clients/{dmd_id}/profile`).
- **Buscar en el Catálogo y Controlar la Cola Bajo Demanda / Reproducción en Vivo**: Buscar títulos localizados (`GET /api/v1/{domain}/search`), encolarlos en cualquier DMD con **`enqueue_next`** o **`play_now`** (`POST /api/v1/clients/{dmd_id}/queue`, incluido `FrontendSync` con `mark_seen: false`) y ejecutar **`Skip` / `Pause` / `Resume` / `Stop`** en tiempo real (`POST /api/v1/clients/{dmd_id}/control`).
- Ver y restablecer el historial **`seen_history.json`** por dominio o globalmente.

---

## 3. Conexión de su DMD (`ArcadeMatrix`, Receptores ESP32 Pasivos y `ArcadeMatrix_RPi`)

### 3.1 DMDs Multimotores Controlados por el Cliente (`ArcadeMatrix` ESP32 y `ArcadeMatrix_RPi`)

1. Abra la WebUI de su DMD (`http://arcadematrix.local/` en ESP32 o `http://arcadematrix.local:8080/` en Raspberry Pi).
2. Vaya a **`Settings -> Showcase Server`** (`#mqtt-subtab-showcase`):
   - El campo **Showcase Server URL** ya viene pre-rellenado como `http://dmdmediashowcase.local:8890` y **desactivado por defecto** (`⚪ Desactivado`) para evitar peticiones de red innecesarias si `dmd_media_showcase` no está instalado.
   - Active el interruptor **Enable DMD Media Showcase Server** y haga clic en **Save Showcase Server** (o **Test Connection**).
   - Verifique que el indicador visual se ponga en verde: **`🟢 Comunicación OK`**.
3. Añada cualquiera de los 4 motores Showcase (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`) a su rotación.
4. La WebUI del DMD consulta automáticamente `GET /api/v1/{domain}/filters?lang={ui_lang}` para poblar dinámicamente las listas de **Sistemas**, **Géneros** y **Décadas**.
5. Ajuste la **Velocidad de desplazamiento de la sinopsis** (`1 = Lento` a `10 = Turbo`, hasta `160 px/s`) y guarde los cambios.

### 3.2 Receptores ESP32 Pasivos e Híbridos Gestionados en el Servidor (`config_source: "server"`)

Para marquesinas o toppers dedicados en `ESP32_STD` (sin PSRAM) o `ESP32-S3` sin WebUI local (~180 líneas de C++ escuchando en UDP `:4210`):
1. Flashee el esqueleto de referencia de la **[Guía de Integración ESP32 y Receptor Pasivo](ESP32_INTEGRATION_ES.md)** (anuncia automáticamente geometría e IP LAN mediante `POST /api/v1/clients/hello` o MQTT `dmd_showcase/clients/{dmd_id}/hello`).
2. Abra `http://dmdmediashowcase.local:8890/`, seleccione el panel detectado, elija su idioma de emisión (`lang`), active dominios/categorías y guarde su perfil en `./data/dmd_profiles.json` — ¡el servidor renderiza incluso la Ficha Informativa de la Fase 1 directamente dentro del stream UDP `DMDF` RGB565!

