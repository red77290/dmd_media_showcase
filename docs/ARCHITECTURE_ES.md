# Arquitectura DDD y Pipeline de Streaming en Tiempo Real — DMD Media Showcase Server (V3.0)

🇬🇧 [English](ARCHITECTURE.md) | 🇫🇷 [Français](ARCHITECTURE_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Arquitectura Hexagonal / DDD en Capas

```mermaid
flowchart TD
    subgraph DMD["Clientes DMD (ESP32 / ESP32-S3 / Raspberry Pi)"]
        WUI["WebUI DMD (Listas desplegables dinámicas)"]
        ENG["Motor Showcase (Ficha -> Panoramas -> Vídeo)"]
    end

    subgraph INFRA_IN["Adaptadores de Entrada (Infraestructura)"]
        HTTP["API REST FastAPI (:8890 + mDNS dmdmediashowcase.local)\n/api/v1/{domain}/filters & /config & /next"]
        MQTT["ShowcaseMqttGateway & EmbeddedMqttBroker (:1883)\nRetain=True + LWT"]
    end

    subgraph APP["Capa de Aplicación"]
        ORCH["ConsumeOnceOrchestrator\n(Slots sin estado + Pipelining N+1 + Invalidación Config)"]
        CHAIN["DomainSourceChainService\n(Enrutamiento por Capacidad + Exclusión Seen)"]
    end

    subgraph DOMAIN["Núcleo de Dominio (app/domain/)"]
        TAX["ReferentialConsolidator y Taxonomía Canónica\n(5 Idiomas, Normalización de Sinónimos, Sistema->Géneros)"]
        PORTS["Puertos: IScraperSource, IScraperReferentialStrategy,\nISeenHistoryRepository, IMediaTranscoderPort, IFrameStreamerPort"]
    end

    subgraph INFRA_OUT["Adaptadores de Salida (Infraestructura)"]
        SCRAPERS["8 Adaptadores de Scrapers\n+ LiveReferentialStore (./data/referentials/*.json)"]
        SEEN["JsonSeenHistoryRepository\n(./data/seen_history.json)"]
        CONV["DmdConverterAdapter\n(dmd_gif_converter + yt-dlp H.264 HD)"]
        UDP["FfmpegUdpClockStreamer\n(ffmpeg -re -> UDP DMDF 10 bytes)"]
    end

    WUI -->|"GET /api/v1/{domain}/filters?lang=es"| HTTP
    ENG <-->|"MQTT Retain + Events"| MQTT
    HTTP --> ORCH
    MQTT --> ORCH
    ORCH --> CHAIN
    ORCH --> CONV
    ORCH --> UDP
    CHAIN --> TAX
    CHAIN --> PORTS
    PORTS --> SCRAPERS
    PORTS --> SEEN
    UDP ==>|"Tramas UDP DMDF (client_ip:udp_port)"| ENG
```

---

## 2. Consolidación Dinámica de Referenciales (`ReferentialConsolidator`)

En lugar de codificar categorías fijas en el firmware o en la WebUI del DMD:
1. Cada adaptador posee su propia estrategia `IScraperReferentialStrategy` (`./data/referentials/{source_id}.json`):
   - **`LibretroWikidataReferentialStrategy`** (`games` consolas): Consulta Wikidata SPARQL estrictamente para Videojuegos (`wd:Q7889`) filtrados por el QID de la plataforma (`wdt:P400`) y del género (`wdt:P136/wdt:P279*`), extrae títulos localizados y enlaces de Wikipedia, y cruza con el árbol de archivos de GitHub `libretro-thumbnails/{repo}` con prioridad regional `(France) -> (Europe) -> (World) -> (USA)`.
   - **`ArcadeItaliaReferentialStrategy`** (`games` arcade): Consulta `https://adb.arcadeitalia.net` exclusivamente por HTTPS.
2. `ReferentialConsolidator` (`app/domain/taxonomy.py`) normaliza todos los sinónimos (`Famicom` / `Nintendo Entertainment System` $\to$ `nes`, `Plataformas` / `Platformer` $\to$ `platform`) y expone un esquema relacional (`Sistema -> Géneros y Décadas compatibles`) mediante `GET /api/v1/{domain}/filters?lang={lang}` y MQTT `dmd_showcase/catalog/{domain}/referential` (`retain=True`).

---

## 3. Protocolo MQTT en Tiempo Real, Pipelining `N+1` y Paquetes UDP `DMDF`

### Cabecera Binaria UDP `DMDF` (10 Bytes, Little-Endian `<4sHBBH`)

> Especificación formal completa (tópicos MQTT 3.1.1 + datagramas UDP `DMDF`): [`docs/asyncapi.yaml`](asyncapi.yaml) (también servida en vivo en `GET /asyncapi.yaml` y `GET /api/v1/asyncapi.yaml`).

| Offset | Tipo | Campo | Descripción |
| :--- | :--- | :--- | :--- |
| `0..3` | `char[4]` | `magic` | `"DMDF"` (`0x44 0x4D 0x44 0x46`) |
| `4..5` | `uint16le` | `frame_seq` | Número de secuencia monotónico del fotograma (`0..65535`) |
| `6` | `uint8` | `chunk_idx` | Índice del fragmento dentro del fotograma actual (`0..total_chunks-1`) |
| `7` | `uint8` | `total_chunks` | Total de fragmentos del fotograma (`1..64`) |
| `8..9` | `uint16le` | `byte_offset` | Desplazamiento en bytes (`chunk_idx * 1024`) dentro del búfer RGB565LE |
| `10..N` | `uint8[]` | `payload` | Datos de píxeles RGB565LE (`<= 1024` bytes, `<= 1034` bytes/paquete UDP) |

### Cabecera Binaria HTTP `DMDS` del Flujo Unificado (16 Bytes, Little-Endian `<4sBBHHHHH`)

| Offset | Tipo | Campo | Descripción |
| :--- | :--- | :--- | :--- |
| `0..3` | `char[4]` | `magic` | `"DMDS"` (`0x44 0x4D 0x44 0x53`) |
| `4` | `uint8` | `version` | Versión del formato binario (`1`) |
| `5` | `uint8` | `fps` | Frecuencia objetivo (`8..60` FPS) |
| `6..7` | `uint16le` | `width` | Ancho del panel en píxeles (ej. `128`) |
| `8..9` | `uint16le` | `height` | Alto del panel en píxeles (ej. `32`) |
| `10..11` | `uint16le` | `total_frames` | Total de fotogramas RGB565LE concatenados (`1..65535`) |
| `12..13` | `uint16le` | `frame_dur_ms` | Duración nominal por fotograma en ms (`round(1000 / fps)`) |
| `14..15` | `uint16le` | `reserved` | Reservado (`0`) |
| `16..EOF` | `uint8[]` | `frames` | Secuencia contigua de `total_frames * width * height * 2` bytes RGB565LE |

