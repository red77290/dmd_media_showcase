# Guía para Desarrolladores y Estrategias de Scrapers — DMD Media Showcase Server (V3.0)

🇬🇧 [English](DEVELOPER.md) | 🇫🇷 [Français](DEVELOPER_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Estructura del Repositorio

```text
dmd_media_showcase/
├── app/
│   ├── domain/
│   │   ├── entities.py               # MediaCandidate, FilterCriteria, ShowcaseCard, PanelGeometry
│   │   ├── taxonomy.py               # ReferentialConsolidator, Sistemas/Géneros/Décadas Canónicos, Sinónimos
│   │   └── ports/                    # IScraperSource, IScraperReferentialStrategy, ISeenHistoryRepository
│   ├── application/
│   │   ├── consume_once_orchestrator.py # Slots Consume-Once sin estado + Pipelining N+1
│   │   └── source_chain_service.py   # Enrutamiento [Primary, Fallback] por capacidad
│   ├── infrastructure/
│   │   ├── mqtt/                     # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/              # JsonSeenHistoryRepository & ServerConfigStore
│   │   ├── scrapers/                 # games/, movies/, series/, anime/, referential_store.py, youtube_resolver.py
│   │   ├── streaming/                # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/               # DmdConverterAdapter (dmd_gif_converter)
│   └── main.py                       # API REST FastAPI + Lifespan + Panel Web Admin
├── docs/                             # Documentación EN/FR/ES, especificaciones OpenAPI 3.1 y AsyncAPI 2.6
└── tests/                            # Suites de pruebas unitarias y de integración DDD
```

---

## 2. Invariantes Obligatorios de los Scrapers

1. **Cero Categorías Fijas en el DMD**: Normalizar siempre sistemas y géneros mediante `resolve_canonical_system()` y `resolve_canonical_genre()` en `app/domain/taxonomy.py`.
2. **Filtrado Estricto de Videojuegos (`wd:Q7889`)**: Los scrapers de consolas nunca deben devolver artículos de hardware (`Nintendo Entertainment System`, `CIC`) ni juegos de otras plataformas. Cada candidato debe coincidir con un archivo verificado en `libretro-thumbnails/{repo}`.
3. **Prioridad Regional y Sinopsis Localizada**: Priorizar carátulas regionales `(Spain) / (France) -> (Europe) -> (World) -> (USA)` y sinopsis de Wikipedia en el idioma configurado.
4. **Exclusión Estricta del Códec AV1**: `youtube_resolver.py` excluye `av01` (`[vcodec!*=av01]`) y prioriza H.264 (`avc1`) a 720p/1080p.

---

## 3. Ejecución de Pruebas

```bash
pytest -c pyproject.toml tests/
```

