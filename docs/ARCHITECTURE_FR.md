# Architecture DDD & Pipeline de Streaming Temps Réel — DMD Media Showcase Server (V3.0)

🇬🇧 [English](ARCHITECTURE.md) | 🇫🇷 Français | 🇪🇸 [Español](ARCHITECTURE_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Architecture Hexagonale / DDD en Couches

```mermaid
flowchart TD
    subgraph DMD["Clients DMD (ESP32 / ESP32-S3 / Raspberry Pi)"]
        WUI["WebUI DMD (Listes déroulantes dynamiques)"]
        ENG["Moteur Showcase (Fiche -> Panoramas -> Vidéo)"]
    end

    subgraph INFRA_IN["Adaptateurs Entrants (Infrastructure)"]
        HTTP["API REST FastAPI (:8890 + mDNS dmdmediashowcase.local)\n/api/v1/{domain}/filters & /config & /next"]
        MQTT["ShowcaseMqttGateway & EmbeddedMqttBroker (:1883)\nRetain=True + LWT"]
    end

    subgraph APP["Couche Application"]
        ORCH["ConsumeOnceOrchestrator\n(Slots sans état + Pipelining N+1 + Invalidation Config)"]
        CHAIN["DomainSourceChainService\n(Routage par Capacité + Exclusion Seen)"]
    end

    subgraph DOMAIN["Cœur Domaine (app/domain/)"]
        TAX["ReferentialConsolidator & Taxonomie Canonique\n(5 Langues, Normalisation Synonymes, Système->Genres)"]
        PORTS["Ports : IScraperSource, IScraperReferentialStrategy,\nISeenHistoryRepository, IMediaTranscoderPort, IFrameStreamerPort"]
    end

    subgraph INFRA_OUT["Adaptateurs Sortants (Infrastructure)"]
        SCRAPERS["8 Adaptateurs Scrapers\n+ LiveReferentialStore (./data/referentials/*.json)"]
        SEEN["JsonSeenHistoryRepository\n(./data/seen_history.json)"]
        CONV["DmdConverterAdapter\n(dmd_gif_converter + yt-dlp H.264 HD)"]
        UDP["FfmpegUdpClockStreamer\n(ffmpeg -re -> UDP DMDF 10 octets)"]
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
    UDP ==>|"Trames UDP DMDF (client_ip:udp_port)"| ENG
```

---

## 2. Consolidation Dynamique des Référentiels Scrapers (`ReferentialConsolidator`)

Au lieu de figer des catégories en dur dans le firmware ou le WebUI du DMD :
1. Chaque adaptateur de scraper possède sa propre stratégie `IScraperReferentialStrategy` (`./data/referentials/{source_id}.json`) qui synchronise en temps réel les identifiants depuis la source distante :
   - **`LibretroWikidataReferentialStrategy`** (`games` consoles) : Interroge Wikidata SPARQL strictement pour des Jeux Vidéo (`wd:Q7889`) filtrés par le QID exact de la plateforme (`wdt:P400`) et du genre (`wdt:P136/wdt:P279*`), extrait les titres localisés (`titleFr` / `titleEn`) et les liens Wikipédia (`frwiki`), puis croise avec l'arbre GitHub mis en cache de `libretro-thumbnails/{repo}` (`Named_Boxarts`, `Named_Titles`, `Named_Snaps`) selon la priorité régionale `(France) -> (Europe) -> (World) -> (USA)`.
   - **`ArcadeItaliaReferentialStrategy`** (`games` arcade) : Interroge `https://adb.arcadeitalia.net` exclusivement en HTTPS (`lista_mame.php` + propriété Wikidata `wdt:P2451` MAME ROM).
   - **`JikanReferentialStrategy` / `AniListReferentialStrategy` / `TvMazeReferentialStrategy`** : Moissonnent dynamiquement les genres Anime et Séries TV.
2. `ReferentialConsolidator` (`app/domain/taxonomy.py`) normalise tous les synonymes (`Famicom` / `Nintendo Entertainment System` $\to$ `nes`, `Plateforme` / `Platformer` $\to$ `platform`) et expose un schéma relationnel (`Système -> Genres & Décennies supportés`) via `GET /api/v1/{domain}/filters?lang={lang}` et MQTT `dmd_showcase/catalog/{domain}/referential` (`retain=True`).
3. **Routage par Capacité** : Lorsque l'utilisateur sélectionne `systems=["nes"]`, `ArcadeItaliaAdapter` ne contacte jamais `adb.arcadeitalia.net` et délègue 100 % de la recherche à `LibretroWikidataAdapter`, garantissant zéro photo de console matérielle et zéro jeu hors-système.

---

## 3. Cinématique MQTT Temps Réel, Pipelining `N+1` & Protocole UDP `DMDF`

```mermaid
sequenceDiagram
    participant DMD as Client DMD (ESP32 / RPi)
    participant MQTT as Showcase MQTT (:1883)
    participant ORCH as ConsumeOnceOrchestrator
    participant FFMPEG as FfmpegUdpClockStreamer

    DMD->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/config (retain=True, client_ip, domains)
    MQTT->>ORCH: handle_mqtt_client_config() (invalide immédiatement le slot si la config a changé)
    ORCH->>ORCH: Prépare l'œuvre non vue N (Card + Flux unifié DMDS)
    ORCH->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/{domain}/ready (retain=True, ready=True, card)
    MQTT->>DMD: Délivre la fiche retenue (Œuvre N)

    Note over DMD: Tour de rotation -> Phase 1 : Fiche Info (Jaquette + Synopsis défilant)
    DMD->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/events {"event":"start_card", "item_id": N}
    MQTT->>ORCH: claim_next_package(N) -> mark_consumed(N) dans seen_history.json
    ORCH->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/{domain}/ready (retain=True, ready=False)
    ORCH->>ORCH: Pipelining N+1 en tâche de fond : lance immédiatement la préparation de N+1 !

    Note over DMD: Fin du défilement du synopsis -> Le DMD demande le flux vidéo/panorama
    DMD->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/events {"event":"start_stream", "item_id": N, "udp_ip": client_ip, "udp_port": port}
    MQTT->>ORCH: handle_mqtt_client_event("start_stream")
    ORCH->>MQTT: PUBLISH stream_started
    ORCH->>FFMPEG: Lance ffmpeg -re rawvideo rgb565le pipe
    loop Chaque Trame (15-25 FPS)
        FFMPEG-->>DMD: Paquets UDP DMDF (En-tête 10 octets + <=1024o RGB565LE) vers client_ip:udp_port
    end
    FFMPEG->>ORCH: Fin du flux (EOF)
    ORCH->>MQTT: PUBLISH stream_eof {"event":"stream_eof", "item_id": N}
    ORCH->>ORCH: purge_consumed_package(N) (suppression immédiate du dossier disque)
    ORCH->>MQTT: PUBLISH dmd_showcase/clients/{dmd_id}/{domain}/ready (retain=True, ready=True, Œuvre N+1)
```

### En-tête Binaire UDP `DMDF` (10 Octets, Little-Endian `<4sHBBI`)

| Offset | Type | Champ | Description |
| :--- | :--- | :--- | :--- |
| `0..3` | `char[4]` | `magic` | `"DMDF"` (`0x44 0x4D 0x44 0x46`) |
| `4..5` | `uint16` | `frame_seq` | Numéro de séquence monotone de la trame (`0..65535`) |
| `6` | `uint8` | `chunk_idx` | Index du fragment dans la trame courante (`0..total_chunks-1`) |
| `7` | `uint8` | `total_chunks` | Nombre total de fragments composant la trame |
| `8..11` | `uint32` | `byte_offset` | Offset en octets de ce fragment dans le framebuffer RGB565LE |
| `10..N` | `uint8[]` | `payload` | Tranche de pixels bruts RGB565LE (`<= 1024` octets, compatible MTU Wi-Fi) |

