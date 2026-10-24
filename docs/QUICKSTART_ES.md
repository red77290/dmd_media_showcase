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
      - ./data:/app/data  # Persistencia server_config.json, seen_history.json y referentials/
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```

Iniciar directamente con el [`docker-compose.yml`](../docker-compose.yml) del repositorio:
```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
docker logs -f dmd-media-showcase
```

#### Opción 2 — Comando `docker run` Directo

```bash
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest
```

> [!TIP]
> **NAT de Docker Bridge y Streaming UDP (`client_ip`)**: Tanto `ArcadeMatrix_RPi` como `ArcadeMatrix` (ESP32) detectan y declaran automáticamente su IP LAN real (`client_ip` / `udp_ip`) en sus mensajes MQTT `config` y `start_stream`, permitiendo que `ffmpeg -re` envíe paquetes UDP `DMDF` directamente al DMD incluso en modo bridge (`172.18.0.1`).

---

### Modo B: Paquete Portátil Hermético (Windows 10/11 y macOS)

1. Descargue `DMD_Media_Showcase_Windows_x64.zip` o `DMD_Media_Showcase_macOS.zip` desde GitHub Releases.
2. Extraiga el archivo en cualquier carpeta y ejecute `dmd-media-showcase`.
3. Todos los datos se almacenan en `./data/` (`server_config.json`, `seen_history.json`, `referentials/`, `stream_tmp/`).

---

## 2. Panel Web de Administración (`http://dmdmediashowcase.local:8890/`)

Abra `http://dmdmediashowcase.local:8890/` (o `http://<ip-servidor>:8890/`) en su navegador para:
- Configurar credenciales **opcionales** de scrapers primarios (`ScreenScraper`, `TMDB`). Sin credenciales, los adaptadores sin clave (`ArcadeItalia`, `Libretro+Wikidata`, `iTunes`, `TVMaze`, `Jikan+AnimeThemes`, `AniList`) funcionan automáticamente.
- Elegir entre el **Broker MQTT Integrado** (`:1883`) o un **Broker MQTT Externo**.
- Ver y restablecer el historial **`seen_history.json`** por dominio o globalmente.

---

## 3. Conexión de su DMD (`ArcadeMatrix` ESP32 y `ArcadeMatrix_RPi`)

1. Abra la WebUI de su DMD (`http://arcadematrix.local/` en ESP32 o `http://arcadematrix.local:8080/` en Raspberry Pi).
2. Vaya a **`Settings -> Showcase Server`** (`#mqtt-subtab-showcase`):
   - El campo **Showcase Server URL** ya viene pre-rellenado como `http://dmdmediashowcase.local:8890` y **desactivado por defecto** (`⚪ Desactivado`) para evitar peticiones de red innecesarias si `dmd_media_showcase` no está instalado.
   - Active el interruptor **Enable DMD Media Showcase Server** y haga clic en **Save Showcase Server** (o **Test Connection**).
   - Verifique que el indicador visual se ponga en verde: **`🟢 Comunicación OK`**.
3. Añada cualquiera de los 4 motores Showcase (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`) a su rotación.
4. La WebUI del DMD consulta automáticamente `GET /api/v1/{domain}/filters?lang={ui_lang}` para poblar dinámicamente las listas de **Sistemas**, **Géneros** y **Décadas**.
5. Ajuste la **Velocidad de desplazamiento de la sinopsis** (`1 = Lento` a `10 = Turbo`, hasta `160 px/s`) y guarde los cambios.

