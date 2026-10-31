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
      - "8890:8890"       # API REST FastAPI + Streams HTTP DMDZ v2 / DMDS Sin Pérdidas + Panel Admin + Swagger UI
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

> **Compilación Local en un Clon Git (`docker-compose.override.yml`)**: Dentro de un repositorio clonado localmente, [`docker-compose.override.yml`](../docker-compose.override.yml) se carga automáticamente para que `docker compose up -d --build` compile `dmd-media-showcase:local` desde su árbol de código local (junto con la última versión de `dmd_gif_converter >= 8.0.4`) en lugar de descargar `ghcr.io/red77290/dmd_media_showcase:latest`.

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
- `server_config.json`: Credenciales personalizadas de scrapers, políticas multifuente por dominio (`source_policies`: `ordered_sources`, `disabled_sources`, `aggregation_mode`), concurrencia de workers y límites de vídeo.
- `dmd_profiles.json`: Perfiles DMD gestionados en el servidor (`config_source: "server"`), listas de reproducción, filtros e idioma de emisión (`lang`).
- `seen_history.json`: Historial Consume-Once por dominio (cero repeticiones).
- `referentials/*.json`: Caché local de catálogos de categorías de los scrapers.

El montaje **`${DMD_DATA_PATH:-./data}:/app/data`** en `docker-compose.yml` utiliza una carpeta real del host (configurable mediante `DMD_DATA_PATH=/mnt/nas/dmd_showcase` en `.env`), **100 % inmune a `docker compose down -v`** (`-v` solo elimina volúmenes con nombre de Docker, nunca directorios del host). Para **Portainer** o despliegues sin rutas locales, utilice un volumen con nombre (`-v dmd_showcase_data:/app/data`) y evite la bandera `-v` al detener el stack.

> [!TIP]
> **Por qué `MQTT + TCP` (`:8890`) es el Transporte por Defecto en Docker y VMs de Escritorio**: Los Modelos 1 (`ArcadeMatrix` ESP32 / ESP32-S3 / `ArcadeMatrix_RPi`) y 2A (`supports_local_card: true`) combinan **MQTT `:1883`** (`config`, `ready`, `start_card`, `stop_stream`, con conmutación automática HTTP `/config + /next` en el mismo turno) con un plano de datos HTTP `1.1` `Content-Length` unificado sin pérdidas (`GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge={true|false}&codec=dmdz2` + limpieza idempotente `DELETE`) — **cero pérdida de paquetes UDP por NAT de VM en Docker Desktop / Rancher Desktop** e identificadores `item_id` compactos sin colisiones (`pkg_<domain>_<12hex>`, `<= 24` caracteres). En **`esp32dev` (`0 KB` PSRAM)**, el cliente descarga `.dmdz2.stream` sin pérdidas en su tarjeta SD `25 MHz` (`/cache/showcase_live.dmdz`, abierta con `O_TRUNC`) durante la Ficha Informativa, cierra el socket TCP para `0` tráfico Wi-Fi en reproducción, decodifica mediante un anillo SPSC de `8.448–9.216 B` con señalización del consumidor hacia el lienzo `SRAM1` Cero-Copia, mantiene el último cuadro en `StreamEndHold` y elimina `/cache/showcase_live.dmdz` con `sd.remove` al salir/interrumpirse; en **`ESP32-S3` (`8 MB` PSRAM, estrictamente `0` E/S en tarjeta SD)**, precarga `.dmdz2.stream` (`<= 4 MB`) en PSRAM durante la Ficha Informativa (`0` tráfico Wi-Fi en reproducción) o transmite flujos HD `256x64` extensos por un anillo SPSC PSRAM de `1 MB`; en **`ArcadeMatrix_RPi`**, el cliente Linux precarga en RAM el flujo `dmdz2` sin pérdidas (`~12x–20x` más compacto, con respaldo automático a `DMDS` puro) tanto en rotación Showcase como en `FrontendSync`. Para receptores pasivos UDP `:4210` opcionales (**Modelo 2B**, `UDP-PASSIVE`, `supports_local_card: false`) o `mqtt_udp` heredado (`MQTT+UDP`), el cliente declara su IP LAN real (`client_ip` / `udp_ip`) para que los paquetes UDP `DMDF` eviten el NAT de Docker bridge en Linux (`172.18.0.1`).

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
- **Reordenar en Caliente las Cadenas Multifuente y el Modo de Agregación** (editable en vivo tanto en **1. Vista General** como en **5. Claves API y Scrapers**, persistido en `./data/server_config.json`):
  - Defina el orden de prioridad por dominio (`ordered_sources`: `#1 Primaria -> #2 Secundaria -> #3 Terciaria -> ...`) y habilite/deshabilite fuentes individuales (`disabled_sources`, manteniendo siempre al menos una fuente activa). Cuando la fuente Primaria devuelve `0` resultados, agota el tiempo de espera, entra en enfriamiento o no soporta el sistema solicitado, la búsqueda pasa de forma determinista a la siguiente fuente habilitada.
  - Seleccione el `aggregation_mode` por dominio:
    - **`enrich_missing` (Agregación Multifuente, por defecto)**: La fuente Primaria resuelve la identidad canónica, los alias y los metadatos (p. ej. `IGDB` resolviendo `"sf2x"` en `cps2` $\to$ `"Super Street Fighter II Turbo"` / `ssf2t`), y `CandidateAggregator` ejecuta un pase secundario acotado (`<= 3.0s`) en las fuentes secundarias activas (`ScreenScraper`, `ArcadeItalia`, `Apple iTunes`, `AnimeThemes`, etc.) para obtener recursos DMD faltantes (marquees/wheels recortados, capturas pixel-art, flujos directos `.mp4`/`.webm`/`.m4v`, sinopsis localizadas). Los elementos agregados muestran insignias combinadas (`🔗 igdb+screenscraper`, `🔗 igdb+arcadeitalia`, `🔗 tmdb_movies+itunes_movies`) en las ranuras DMD en vivo (`playing_source`, `ready_source`, `standby_source`, `preparing_source`) y en las colas prioritarias (`source_used`).
    - **`fallback_only` (Respaldo Estricto)**: Las fuentes secundarias solo se consultan cuando la fuente Primaria devuelve `0` resultados o falla.
- **Ajustar en caliente Runtime, Workers y Límites de Vídeo en Tiempo Real** (`2. Runtime, Workers y Límites de Vídeo`, guardado en `./data/server_config.json` sin reiniciar el servidor): número de workers concurrentes (`max_concurrent_jobs`), modo de ejecutor (`ProcessPoolExecutor` / `ThreadPoolExecutor`), hilos FFmpeg, prioridad CPU `nice`, duración máxima de vídeo (`video_max_duration_limit_s`), FPS máximos (`video_max_fps_limit`), duración de panoramas zoom-to-cover (`panorama_duration_s` — desplazamiento vertical en paneles YOKO, horizontal de izquierda a derecha en paneles TATE), tamaño máximo de descarga, caché, TTLs, puerto MQTT, mDNS y nivel de log en vivo.
- **Activar la Caché Persistente de Conversiones On-Demand**: Si dispone de espacio en disco, active **Caché Persistente de Conversiones On-Demand** (con cuota máxima opcional en MB). Los juegos solicitados bajo demanda (como desde Recalbox / Batocera / RetroPie `FrontendSync`) se guardan por `(Sistema + Juego + Resolución + Estilo de Tarjeta)` para arrancar **al instante (`0 ms`)** en futuras partidas sin volver a buscar ni convertir, con vaciado en un clic mediante **🗑 Vaciar Caché On-Demand**.
- **Configurar Credenciales Opcionales de Scrapers** (`5. Claves API y Scrapers` o `.env` — resolución en 3 niveles `server_config.json` $\to$ `.env` / variables del SO $\to$ bóveda nativa C `_prod_vault`, con redacción automática de logs mediante `SecretRedactingLogFilter` y `httpx`/`httpcore` en `WARNING`):
  - **IGDB v4 (Portal de Desarrolladores de Twitch)**: `igdb_client_id` y `igdb_client_secret` (`IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET`, aplicación gratuita en `https://dev.twitch.tv/console/apps`) para `games` en los 66 sistemas retro/arcade canónicos (`55+` plataformas), búsqueda por alias (`alternative_names`) y descubrimiento aleatorio por offset.
  - **ScreenScraper** (`screenscraper_user`, `screenscraper_pass`, `screenscraper_dev_id`, `screenscraper_dev_pass`) para `games` (marquees/wheels recortados, capturas pixel-art, vídeos `.mp4`, con detección automática de cuota/sobrecarga HTTP-200 y disyuntor de enfriamiento). Sin credenciales, **`ArcadeItalia`** (MAME/CPS/Neo-Geo/NAOMI HTTPS + alias cortos `sf2x`, `3s`, `kof98`, `garou`) y **`Libretro+Wikidata`** (SPARQL Consola/Portátil/Micro + `libretro-thumbnails` + vídeos de Archive.org) funcionan automáticamente sin configuración.
  - **TMDB** (`tmdb_api_key` / `TMDB_API_KEY`) y **TheTVDB v4** (`tvdb_api_key` / `TVDB_API_KEY`) para `movies`, `series` y `anime`. Sin claves, `Apple iTunes`, `TVMaze`, `Jikan + AnimeThemes` y `AniList` operan automáticamente.
- Elegir entre el **Broker MQTT Integrado** (`:1883`) o un **Broker MQTT Externo**.
- **Gestionar Perfiles DMD en el Servidor (`./data/dmd_profiles.json`)**: Configurar listas de reproducción (`games`, `movies`, `series`, `anime`), filtros (`systems`, `genres`, `decades`), ajustes de vídeo/color e idioma de emisión (`lang`) para receptores ESP32 pasivos o híbridos (`PUT /api/v1/clients/{dmd_id}/profile`).
- **Buscar en el Catálogo y Controlar la Cola Bajo Demanda / Reproducción en Vivo**: Buscar títulos localizados (`GET /api/v1/{domain}/search`), encolarlos en cualquier DMD con **`enqueue_next`** o **`play_now`** (`POST /api/v1/clients/{dmd_id}/queue`, incluido `FrontendSync` con `mark_seen: false`) y ejecutar **`Skip` / `Pause` / `Resume` / `Stop`** en tiempo real (`POST /api/v1/clients/{dmd_id}/control`).
- Ver y restablecer el historial **`seen_history.json`** por dominio o globalmente, y sincronizar referenciales en vivo (`POST /api/v1/admin/referentials/sync`).

---

## 3. Conexión de su DMD (`ArcadeMatrix`, Receptores ESP32 Pasivos y `ArcadeMatrix_RPi`)

### 3.1 DMDs Multimotores Controlados por el Cliente (`ArcadeMatrix` ESP32 y `ArcadeMatrix_RPi`)

1. Abra la WebUI de su DMD (`http://arcadematrix.local/` en ESP32 o `http://arcadematrix.local:8080/` en Raspberry Pi).
2. Vaya a **`Settings -> Showcase Server`** (`#mqtt-subtab-showcase`):
   - El campo **Showcase Server URL** ya viene pre-rellenado como `http://dmdmediashowcase.local:8890` y **desactivado por defecto** (`⚪ Desactivado`) para evitar peticiones de red innecesarias si `dmd_media_showcase` no está instalado.
   - Active el interruptor **Enable DMD Media Showcase Server** y haga clic en **Save Showcase Server** (o **Test Connection**).
   - Verifique que el indicador visual se ponga en verde: **`🟢 Comunicación OK`**.
3. Añada cualquiera de los 4 motores Showcase (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`) a su rotación.
4. La WebUI del DMD consulta automáticamente `GET /api/v1/{domain}/filters?lang={ui_lang}` para poblar dinámicamente las listas de **Sistemas** (`55+` plataformas), **Géneros** y **Décadas**.
5. Ajuste la **Velocidad de desplazamiento de la sinopsis** (`1 = Lento` a `10 = Turbo`, hasta `160 px/s`) y guarde los cambios — el DMD transmite automáticamente mediante **`MQTT+TCP`** (MQTT `:1883` + HTTP `1.1` `Content-Length` `DMDZ` v2 sin pérdidas en `:8890`: caché SD `25 MHz` autolimpiable en `esp32dev`, `100 % PSRAM` precargada / anillo SPSC PSRAM de `1 MB` con `0` SD en `ESP32-S3`, y precarga RAM con respaldo `DMDS` puro en `ArcadeMatrix_RPi`; `HTTP` y `MQTT+UDP` opcional también están soportados).

### 3.2 Receptores ESP32 Híbridos (Modelo 2A) y Pasivos UDP (Modelo 2B) Gestionados en el Servidor (`config_source: "server"`)

Para marquesinas o toppers dedicados en `ESP32_STD` (sin PSRAM) o `ESP32-S3` sin WebUI local — ya sea en **Modelo 2A Híbrido** (`supports_local_card: true`, Ficha Informativa local a 60 FPS + stream `MQTT+TCP` HTTP `1.1` `DMDZ` v2 / `DMDS` sin pérdidas en `:8890`) o en **Modelo 2B Pasivo** (`supports_local_card: false`, ~180 líneas de C++ escuchando en `UDP-PASSIVE` `:4210` `DMDF`):
1. Flashee el firmware de referencia de la **[Guía de Integración ESP32 y Receptor Pasivo](ESP32_INTEGRATION_ES.md)** (anuncia automáticamente geometría e IP LAN mediante MQTT `dmd_showcase/clients/{dmd_id}/hello` o `POST /api/v1/clients/hello`).
2. Abra `http://dmdmediashowcase.local:8890/`, seleccione el panel detectado, elija su idioma de emisión (`lang`), active dominios/categorías y guarde su perfil en `./data/dmd_profiles.json` — el Modelo 2A transmite `DMDZ` v2 sin pérdidas por HTTP `1.1` en `:8890` (caché SD `25 MHz` + `sd.remove` en `esp32dev`, `100 % PSRAM` en `ESP32-S3`), mientras que el Modelo 2B renderiza incluso la Ficha Informativa de la Fase 1 directamente dentro del stream UDP `DMDF` (`:4210`) RGB565.

### 3.3 Vincular Showcase con Recalbox / Batocera / RetroPie (`FrontendSync` Showcase en Juego)

Cuando su DMD (`ArcadeMatrix` ESP32 o `ArcadeMatrix_RPi`) está conectado a una consola retro, Showcase enriquece automáticamente las partidas sin ralentizar la navegación por menús:
1. **Instalar el script en la consola una sola vez**: Apunte el script de Recalbox, Batocera (`v33+`) o RetroPie **únicamente a la IP de su DMD**. La consola consulta dinámicamente al DMD qué broker MQTT utilizar, por lo que nunca tendrá que reinstalar el script al encender o apagar Showcase.
2. **Activar Showcase en Juego en el DMD**: En la WebUI de su DMD (`Sistema -> MQTT & API -> 🎮 FrontendSync`), active **Usar Broker MQTT de Showcase si está activo** (libera RAM local en el DMD compartiendo el broker `:1883` de Showcase) y **Showcase en Juego (Tarjeta + Vídeo Gameplay)**.
3. **Elegir el Ciclo y el Diseño de Tarjeta**:
   - **Navegación por Menús (`0 ms`)**: Desplazarse por juegos muestra al instante las marquesinas Pixelcade locales.
   - **Al Lanzar un Juego**: El DMD muestra la marquesina clásica de inmediato mientras Showcase prepara en segundo plano la **Ficha Informativa Animada + Panoramas + Vídeo de Gameplay** y toma el relevo suavemente cuando está lista (`Una vez`, `Alternar Marquesina y Showcase` o `Bucle`).
   - **Estilo de Tarjeta**: Mantenga **Heredar perfil DMD desde Showcase Studio** (configurado visualmente en el Studio de Showcase) o seleccione **Personalizado en el DMD** directamente en `FrontendSync`.
4. **Caché de Inicio Instantáneo**: Active **Caché Persistente de Conversiones On-Demand** en Showcase (`2. Runtime, Workers y Límites de Vídeo`) para que los juegos ya jugados arranquen al instante en sesiones futuras, y pulse **🗑️ Vaciar Caché de Showcase** (en Showcase o desde el DMD) cuando quiera regenerar las tarjetas tras cambiar su estilo.
