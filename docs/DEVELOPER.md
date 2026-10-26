# Developer & Modular DDD Extension Guide — DMD Media Showcase Server

🇬🇧 English | 🇫🇷 [Français](DEVELOPER_FR.md) | 🇪🇸 [Español](DEVELOPER_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Modular Repository Structure

```text
dmd_media_showcase/
├── app/
│   ├── domain/                           # Pure Domain Core (zero Infrastructure / FastAPI imports)
│   │   ├── entities.py                   # MediaCandidate, FilterCriteria, ShowcaseCard, PanelGeometry
│   │   ├── board_identity.py             # Physical DMD board key & Docker NAT / reconnect deduplication
│   │   ├── style_specs.py                # Card layouts, color themes, pixel fonts, accent palettes
│   │   ├── aggregates/                   # Generic & reusable Domain Aggregates
│   │   │   ├── artifact_cache.py         # BoundedTtlCache[TKey, TVal]
│   │   │   ├── slot_pipeline.py          # DualSlotPipeline[TKey, TItem] (Ready N + Standby N+1)
│   │   │   ├── priority_queue.py         # PriorityPlaybackQueue (enqueue_next vs play_now)
│   │   │   └── playlist_cursor.py        # WeightedPlaylistCursor (multi-domain slot quotas)
│   │   ├── taxonomy/                     # Modular multilingual Taxonomy & SynonymCatalogResolver
│   │   │   ├── common.py                 # Slug normalizers & decade helpers
│   │   │   ├── systems.py                # RETRO_SYSTEM_CATALOG
│   │   │   ├── game_genres.py            # GAME_GENRE_CATALOG
│   │   │   ├── cinema_genres.py          # MOVIE / SERIES / ANIME genre catalogs
│   │   │   ├── resolver.py               # Generic SynonymCatalogResolver
│   │   │   └── consolidator.py           # ReferentialConsolidator
│   │   └── ports/                        # Hexagonal Port interfaces + PortProviderRegistry
│   ├── application/                      # Application Services (depend strictly on domain & ports)
│   │   ├── showcase_state.py             # Shared ShowcaseRuntimeState container
│   │   ├── concurrency.py                # DynamicConcurrencyLimiter & key locks
│   │   ├── criteria_parser.py            # Domain criteria normalization & defaults
│   │   ├── client_registry_service.py    # DMD registration, hello, LWT & telemetry snapshots
│   │   ├── slot_preparation_service.py   # Ready/Standby slot preparation, claim & disk purge
│   │   ├── on_demand_queue_service.py    # Priority playback queue & zero-retranscode Studio promotion
│   │   ├── passive_director_service.py   # Server-managed Mode 2A/2B playlist loops & live controls
│   │   ├── studio_preview_service.py     # <100ms Card preview & full DMDS sequence builder
│   │   ├── source_chain_service.py       # [Primary, Fallback] capability-aware scraper routing
│   │   ├── update_checker.py             # Cached OS-aware GitHub Release checker
│   │   └── consume_once_orchestrator.py  # Thin orchestration facade
│   ├── container.py                      # Composition Root wiring Infrastructure adapters to Domain ports
│   ├── infrastructure/                   # Inbound & Outbound Concrete Adapters
│   │   ├── http/                         # FastAPI middleware, runtime reloader & 6 modular routers
│   │   │   └── routers/                  # system_admin, discovery, clients_and_queue, studio, consume_once, rotation_batch
│   │   ├── web/                          # Externalized Control Center WebUI (zero HTML/JS in Python)
│   │   │   ├── dashboard_loader.py       # Portable resource loader for index.html, CSS & ES modules
│   │   │   └── static/                   # index.html, css/{dashboard,studio}.css, js/ (15 ES modules)
│   │   ├── rendering/                    # Modular HUB75 RGB565LE rendering pipeline
│   │   │   ├── card_geometry.py          # Adaptive YOKO/Square/TATE geometry solver
│   │   │   ├── card_layout_engine.py     # 5 card layouts, bitmap fonts & DMDS card renderer
│   │   │   ├── imaging_primitives.py     # Color boost, framing & RGB565 conversion helpers
│   │   │   ├── panorama_and_static.py    # Ken Burns / pan-scan panoramas & mini-jackets
│   │   │   └── video_pipeline.py         # Action-window detection & trailer stream encoding
│   │   ├── mqtt/                         # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/                  # JsonSeenHistoryRepository, DmdProfileStore, ServerConfigStore
│   │   ├── scrapers/                     # games/, movies/, series/, anime/ adapters + per-source referentials
│   │   ├── streaming/                    # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/                   # DmdConverterAdapter (dmd_gif_converter)
│   ├── scheduler/                        # Bounded async + process/thread worker pool
│   │   ├── executor_lifecycle.py         # Generic BoundedWorkerExecutorMixin[TJob]
│   │   └── session_batch_manager.py      # HTTP rotation session & batch manager
│   ├── asyncapi_spec.py                  # Loads canonical docs/asyncapi.yaml from disk
│   └── main.py                           # Lean FastAPI bootstrap, /static mount & router inclusion
├── docs/                                 # EN/FR/ES Documentation, OpenAPI 3.1 & AsyncAPI 2.6 specs
└── tests/                                # Unit, integration, characterization & DDD fitness suites
```

---

## 2. Implementing a New `IScraperSource` & `IScraperReferentialStrategy`

Every scraper source implements `IScraperSource` (`app/domain/ports/scraper_source.py`) and pairs with a dedicated `IScraperReferentialStrategy` (`app/domain/ports/referential_strategy.py`) persisted in `./data/referentials/{source_id}.json`:

```python
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource
from app.infrastructure.scrapers.registry import scraper_registry


class CustomRetroAdapter(IScraperSource):
    source_id = "custom_retro"
    domain: DomainType = "games"
    role = "fallback"

    def is_configured(self) -> bool:
        return True

    def supported_filters(self, lang: str = "fr"):
        return {}

    async def fetch_random_unseen(
        self, criteria: FilterCriteria, seen_ids: set[str], count: int = 1
    ) -> list[MediaCandidate]:
        ...

    async def search_by_query(self, query: str, lang: str = "fr"):
        return None


scraper_registry.register(CustomRetroAdapter())
```

### Mandatory Architectural & Scraper Invariants
1. **Strict Layer Boundaries**:
   - `app/domain/` must never import `app.application`, `app.infrastructure`, `app.models`, or FastAPI/HTTPX/Pillow/OpenCV.
   - `app/application/` must never import concrete `app.infrastructure.*` adapters or `app.models`; register new ports in `app/domain/ports/` and wire them in `app/container.py`.
2. **Module & Class Size Budgets**: Every Python module must remain `<= 800` lines, every class `<= 750` lines, and every frontend ES module `<= 600` lines (`tests/test_architecture_ddd_fitness.py`). Never embed `<style>` or `<script>` inside `.py` files.
3. **Zero Hardcoded Categories on DMD**: Always normalize upstream genres and systems via `resolve_canonical_system()` and `resolve_canonical_genre()` in `app/domain/taxonomy/`.
4. **Strict Entity Filtering for Games (`wd:Q7889`)**: Console/computer scrapers must never return hardware console pages (`Nintendo Entertainment System`, `CIC`) or titles from other platforms. Every candidate must match a verified asset in `libretro-thumbnails/{repo}`.
5. **Regional Artwork & H.264 Video Exclusivity**: Prioritize localized artwork/synopses for `criteria.lang`, and keep `youtube_resolver.py` strictly H.264 (`[vcodec!*=av01]`) for ARM64 software decoding compatibility.

---

## 3. Modifying the Modular WebUI (`app/infrastructure/web/static/`)

The Control Center WebUI uses standard native ES Modules (`<script type="module" src="/static/js/app.js"></script>`) with zero build-step overhead:
- Add translations in lockstep across `js/i18n/en.js`, `js/i18n/fr.js`, and `js/i18n/es.js` (100% key parity is verified by pytest).
- Keep shared mutable UI state inside `uiState` (`js/state.js`).
- When adding an inline DOM event attribute (`onclick="myAction()"` in `index.html`), export `myAction` from its tab module and register it in `WINDOW_HANDLERS` inside `js/app.js` (verified automatically by `test_webui_static_es_modules_resolution_and_dom_handler_binding`).

---

## 4. Running the Test & Architectural Fitness Suites

```bash
# Run all unit, integration, characterization, and DDD architectural fitness tests
pytest -c pyproject.toml tests/

# Run only the Hexagonal/DDD architectural & generic-aggregate fitness gates
pytest -c pyproject.toml tests/test_architecture_ddd_fitness.py
```
