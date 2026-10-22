# DDD Architecture & Real-Time Streaming Pipeline — DMD Media Showcase Server (V3.0)

🇬🇧 English | 🇫🇷 [Français](ARCHITECTURE_FR.md) | 🇪🇸 [Español](ARCHITECTURE_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Hexagonal / DDD Layered Architecture

```mermaid
flowchart TD
    subgraph DMD["DMD Clients (ESP32 / ESP32-S3 / Raspberry Pi)"]
        WUI["DMD WebUI (Dynamic Referential Dropdowns)"]
        ENG["Showcase Renderer (Card -> Panoramas -> Video)"]
    end

    subgraph INFRA_IN["Inbound Adapters (Infrastructure)"]
        HTTP["FastAPI REST API (:8080)\n/api/v1/{domain}/filters & /config & /next"]
        MQTT["ShowcaseMqttGateway & EmbeddedMqttBroker (:1883)\nRetain=True + LWT"]
    end

    subgraph APP["Application Layer"]
        ORCH["ConsumeOnceOrchestrator\n(Stateless Slots + N+1 Pipelining + Config Invalidation)"]
        CHAIN["DomainSourceChainService\n(Capability-Aware Routing + Seen Exclusion)"]
    end

    subgraph DOMAIN["Domain Core (app/domain/)"]
        TAX["ReferentialConsolidator & Canonical Taxonomy\n(5 Languages, Synonym Normalization, System->Genres)"]
        PORTS["Ports: IScraperSource, IScraperReferentialStrategy,\nISeenHistoryRepository, IMediaTranscoderPort, IFrameStreamerPort"]
    end

    subgraph INFRA_OUT["Outbound Adapters (Infrastructure)"]
        SCRAPERS["8 Domain Scraper Adapters\n+ LiveReferentialStore (./data/referentials/*.json)"]
        SEEN["JsonSeenHistoryRepository\n(./data/seen_history.json)"]
        CONV["DmdConverterAdapter\n(dmd_gif_converter + yt-dlp H.264 HD)"]
        UDP["FfmpegUdpClockStreamer\n(ffmpeg -re -> DMDF 10-Byte Header UDP)"]
    end

    WUI -->|"GET /api/v1/{domain}/filters?lang=fr"| HTTP
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
    UDP ==>|"UDP DMDF Frames (client_ip:udp_port)"| ENG
```

---

## 2. Dynamic Scraper Referential Consolidation (`ReferentialConsolidator`)

Instead of hardcoding category names on the DMD firmware or WebUI:
1. Each scraper adapter owns an `IScraperReferentialStrategy` (`./data/referentials/{source_id}.json`) that harvests live identifiers from its upstream API:
   - **`LibretroWikidataReferentialStrategy`** (`games` consoles): Queries Wikidata SPARQL strictly for Video Games (`wd:Q7889`) filtered by platform QID (`wdt:P400`) and genre QID (`wdt:P136/wdt:P279*`), extracts localized titles (`titleFr` / `titleEn`) and Wikipedia sitelinks (`frwiki`), and indexes the GitHub file tree of `libretro-thumbnails/{repo}` (`Named_Boxarts`, `Named_Titles`, `Named_Snaps`) with regional priority `(France) -> (Europe) -> (World) -> (USA)`.
   - **`ArcadeItaliaReferentialStrategy`** (`games` arcade): Queries `https://adb.arcadeitalia.net` strictly over HTTPS (`lista_mame.php` + Wikidata `wdt:P2451` MAME ROM IDs).
   - **`JikanReferentialStrategy` / `AniListReferentialStrategy` / `TvMazeReferentialStrategy`**: Harvest live anime and TV genres.
2. `ReferentialConsolidator` (`app/domain/taxonomy.py`) normalizes all scraper-specific synonyms (`Famicom` / `Nintendo Entertainment System` $\to$ `nes`, `Plateforme` / `Platformer` $\to$ `platform`) and exposes a relational schema (`System -> Supported Genres & Decades`) via `GET /api/v1/{domain}/filters?lang={lang}` and MQTT `dmd_showcase/catalog/{domain}/referential` (`retain=True`).
3. **Capability-Aware Routing**: When a user selects `systems=["nes"]`, `ReferentialConsolidator.source_supports_criteria("arcadeitalia", criteria)` returns `False` and `ArcadeItaliaAdapter` delegates 100% to `LibretroWikidataAdapter`, guaranteeing zero MAME queries and zero console hardware photos.

---

## 3. Real-Time MQTT Handshake, `N+1` Pipelining & UDP `DMDF` Protocol

```mermaid
sequenceDiagram
    participant DMD as DMD Client (ESP32 / RPi)
    participant MQTT as Showcase MQTT (:1883)
    participant ORCH as ConsumeOnceOrchestrator
    participant FFMPEG as FfmpegUdpClockStreamer

    DMD->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/config (retain=True, client_ip, domains)
    MQTT->>ORCH: handle_mqtt_client_config() (invalidates stale slot if config changed)
    ORCH->>ORCH: Prepare Unseen Item N (Card + Unified DMDS Stream)
    ORCH->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/{domain}/ready (retain=True, ready=True, card)
    MQTT->>DMD: Deliver Retained Ready Card (Item N)

    Note over DMD: Rotation turn starts -> Phase 1: Info Card (Poster + Scrolling Synopsis)
    DMD->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/events {"event":"start_card", "item_id": N}
    MQTT->>ORCH: claim_next_package(N) -> mark_consumed(N) in seen_history.json
    ORCH->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/{domain}/ready (retain=True, ready=False)
    ORCH->>ORCH: Background N+1 Pipelining: immediately start preparing Item N+1!

    Note over DMD: Info Card finishes scrolling -> DMD requests video/panorama stream
    DMD->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/events {"event":"start_stream", "item_id": N, "udp_ip": client_ip, "udp_port": port}
    MQTT->>ORCH: handle_mqtt_client_event("start_stream")
    ORCH->>MQTT: PUBLISH stream_started
    ORCH->>FFMPEG: Spawn ffmpeg -re rawvideo rgb565le pipe
    loop Every Frame (15-25 FPS)
        FFMPEG-->>DMD: UDP DMDF Chunks (10-Byte Header + <=1024B RGB565LE Payload) to client_ip:udp_port
    end
    FFMPEG->>ORCH: EOF reached
    ORCH->>MQTT: PUBLISH stream_eof {"event":"stream_eof", "item_id": N}
    ORCH->>ORCH: purge_consumed_package(N) (rm -rf item_dir)
    ORCH->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/{domain}/ready (retain=True, ready=True, Item N+1)
```

### Binary UDP `DMDF` Packet Header (10 Bytes, Little-Endian `<4sHBBI`)

| Offset | Type | Field | Description |
| :--- | :--- | :--- | :--- |
| `0..3` | `char[4]` | `magic` | `"DMDF"` (`0x44 0x4D 0x44 0x46`) |
| `4..5` | `uint16` | `frame_seq` | Monotonic frame sequence number (`0..65535`) |
| `6` | `uint8` | `chunk_idx` | Chunk index within the current frame (`0..total_chunks-1`) |
| `7` | `uint8` | `total_chunks` | Total chunks required to assemble the complete frame |
| `8..11` | `uint32` | `byte_offset` | Byte offset of this chunk inside the RGB565LE frame buffer |
| `10..N` | `uint8[]` | `payload` | Raw RGB565LE pixel slice (`<= 1024` bytes, MTU-safe) |
