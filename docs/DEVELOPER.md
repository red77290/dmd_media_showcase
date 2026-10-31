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
│   │   ├── on_demand_cache_key.py        # Deterministic On-Demand conversion cache key builder
│   │   ├── style_specs.py                # Card layouts, color themes, pixel fonts, accent palettes
│   │   ├── aggregates/                   # Generic & reusable Domain Aggregates
│   │   │   ├── artifact_cache.py         # BoundedTtlCache[TKey, TVal]
│   │   │   ├── slot_pipeline.py          # DualSlotPipeline[TKey, TItem] (Ready N + Standby N+1)
│   │   │   ├── priority_queue.py         # PriorityPlaybackQueue (enqueue_next vs play_now)
│   │   │   └── playlist_cursor.py        # WeightedPlaylistCursor (multi-domain slot quotas)
│   │   ├── taxonomy/                     # Modular multilingual Taxonomy & SynonymCatalogResolver
│   │   │   ├── common.py                 # Slug normalizers, fingerprints & decade helpers
│   │   │   ├── systems.py                # Unified 66-system RETRO_SYSTEM_CATALOG (55+ platforms)
│   │   │   ├── systems_arcade_console.py # Arcade boards (MAME, CPS-1/2/3, Neo-Geo, NAOMI, Model 2/3) & main consoles
│   │   │   ├── systems_handheld_computer.py # Handhelds & classic home computers
│   │   │   ├── game_genres.py            # GAME_GENRE_CATALOG
│   │   │   ├── cinema_genres.py          # MOVIE / SERIES / ANIME genre catalogs
│   │   │   ├── resolver.py               # Generic SynonymCatalogResolver
│   │   │   └── consolidator.py           # ReferentialConsolidator & capability-aware routing
│   │   └── ports/                        # Hexagonal Port interfaces + PortProviderRegistry
│   ├── application/                      # Application Services (depend strictly on domain & ports)
│   │   ├── showcase_state.py             # Shared ShowcaseRuntimeState container
│   │   ├── concurrency.py                # DynamicConcurrencyLimiter & key locks
│   │   ├── criteria_parser.py            # Domain criteria normalization & defaults
│   │   ├── client_registry_service.py    # DMD registration, client_transports (MQTT+TCP default), LWT & live telemetry
│   │   ├── slot_preparation_service.py   # Compact pkg_<domain>_<12hex> IDs, Ready/Standby slots, claim & disk purge
│   │   ├── on_demand_queue_service.py    # Priority playback queue & zero-retranscode Studio promotion
│   │   ├── passive_director_service.py   # Server-managed Mode 2A/2B playlist loops & live controls
│   │   ├── studio_preview_service.py     # <100ms Card preview & full DMDS sequence builder
│   │   ├── source_chain_service.py       # Composable multi-source Chain-of-Responsibility router
│   │   ├── candidate_aggregator.py       # <=3.0s Cross-Source Composite Enricher (enrich_missing)
│   │   ├── update_checker.py             # Cached OS-aware GitHub Release checker
│   │   └── consume_once_orchestrator.py  # Thin orchestration facade
│   ├── container.py                      # Composition Root wiring Infrastructure adapters to Domain ports
│   ├── infrastructure/                   # Inbound & Outbound Concrete Adapters
│   │   ├── http/                         # FastAPI middleware, runtime reloader & 6 modular routers
│   │   │   └── routers/                  # system_admin, discovery, clients_and_queue, studio, rotation_batch
│   │   │       └── consume_once.py       # Lossless HTTP Content-Length DMDZ v2 / DMDS stream + DELETE cleanup
│   │   ├── web/                          # Externalized Control Center WebUI (zero HTML/JS in Python)
│   │   │   ├── dashboard_loader.py       # Portable resource loader for index.html, CSS & ES modules
│   │   │   └── static/                   # index.html, css/{dashboard,studio,responsive}.css, js/ (15+ ES modules)
│   │   ├── rendering/                    # Modular HUB75 RGB565LE rendering pipeline
│   │   │   ├── card_geometry.py          # Adaptive YOKO/Square/TATE geometry solver
│   │   │   ├── card_layout_engine.py     # 5 card layouts, bitmap fonts & DMDS card renderer
│   │   │   ├── dmdz_codec.py             # Lossless DMDZ v2 True-RGB565 codec (encode_dmdz_bytes, ensure_dmdz_sibling)
│   │   │   ├── dmdz_motion.py            # Exact lossless 1D/2D scroll search & shifted reference helpers
│   │   │   ├── imaging_primitives.py     # Color boost, framing & RGB565 conversion helpers
│   │   │   ├── panorama_and_static.py    # Deterministic TATE (L->R) / YOKO (T->B) zoom-to-cover panoramas
│   │   │   └── video_pipeline.py         # Action-window detection & direct RGB565 trailer stream encoding
│   │   ├── mqtt/                         # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/                  # JsonSeenHistoryRepository, DmdProfileStore, ServerConfigStore (source_policies)
│   │   │   └── on_demand_cache_store.py  # shutil.copy2 mtime-preserving .rgb565.stream + .dmdz2.stream sibling cache
│   │   ├── scrapers/                     # 11 domain adapters + ScraperRegistry + per-source referentials
│   │   │   ├── games/                    # igdb_adapter, screenscraper_adapter, arcadeitalia_adapter, libretro_wikidata_adapter, arcade_aliases
│   │   │   ├── movies/                   # tmdb_movie_adapter, itunes_movie_adapter
│   │   │   ├── series/                   # tmdb_series_adapter, tvmaze_adapter (+ tvdb_adapter)
│   │   │   └── anime/                    # jikan_animethemes_adapter, anilist_adapter
│   │   ├── streaming/                    # FfmpegUdpClockStreamer (Mode 2B Passive :4210 & optional MQTT+UDP DMDF)
│   │   └── transcoder/                   # DmdConverterAdapter (dmd_gif_converter >= 8.0.13)
│   ├── security/                         # 3-Tier Native Vault & Secret Log Redaction
│   │   ├── vault.py                      # Runtime loader for compiled C extension _prod_vault*.so
│   │   └── log_redactor.py               # SecretRedactingLogFilter + httpx/httpcore WARNING clamp
│   ├── scheduler/                        # Bounded async + process/thread worker pool
│   │   ├── executor_lifecycle.py         # Generic BoundedWorkerExecutorMixin[TJob]
│   │   └── session_batch_manager.py      # HTTP rotation session & batch manager
│   ├── asyncapi_spec.py                  # Loads canonical docs/asyncapi.yaml from disk
│   └── main.py                           # Lean FastAPI bootstrap, /static mount & routers
├── scripts/compile_prod_vault.py         # CI/CD PBKDF2-HMAC-SHA256 + 3-way XOR C extension builder
├── docker-compose.override.yml           # Auto local source + dmd_gif_converter build override
├── docs/                                 # EN/FR/ES Documentation, OpenAPI 3.1 & AsyncAPI 2.6 specs
└── tests/                                # Unit, integration, characterization & DDD fitness suites
```

---

## 2. Adding a New Data Source or Fallback in 3 Clean Steps

Thanks to the composable `ScraperRegistry` + `DomainSourceChainService` + `CandidateAggregator` design, adding a new scraper or fallback to any domain (`games`, `movies`, `series`, `anime`) requires **3 self-contained steps and zero orchestrator or UI code changes**:

### Step 1 — Implement `IScraperSource` in `app/infrastructure/scrapers/<domain>/`

Create your adapter implementing `IScraperSource` (`app/domain/ports/scraper_source.py`), optionally paired with an `IScraperReferentialStrategy` (`app/domain/ports/referential_strategy.py`) persisted in `./data/referentials/{source_id}.json`:

```python
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource


class CustomRetroAdapter(IScraperSource):
    source_id = "custom_retro"
    display_name = "Custom Retro Catalog"
    domain: DomainType = "games"
    role = "fallback"

    def is_configured(self) -> bool:
        return True  # Or check credentials via server_config_store.get_all()

    def is_in_cooldown(self) -> bool:
        return False  # Optional circuit-breaker when upstream hits rate-limit/quota

    def supported_filters(self, lang: str = "fr"):
        return {}

    async def fetch_random_unseen(
        self, criteria: FilterCriteria, seen_ids: set[str], count: int = 1
    ) -> list[MediaCandidate]:
        ...

    async def search_by_query(self, query: str, lang: str = "fr"):
        return None

    async def search_candidates(
        self, query: str, criteria: FilterCriteria, limit: int = 8
    ) -> list[MediaCandidate]:
        ...
```

### Step 2 — Register the Adapter in `ScraperRegistry` (`app/infrastructure/scrapers/registry.py`)

Add an instance of `CustomRetroAdapter()` to `self._chains["games"]` inside `ScraperRegistry.__init__()` (or call `scraper_registry.register(CustomRetroAdapter())`).

### Step 3 — Add Default Priority Slot in `DEFAULT_SOURCE_POLICIES` (`app/infrastructure/persistence/server_config_store.py`)

Append `"custom_retro"` to `DEFAULT_SOURCE_POLICIES["games"]["ordered_sources"]`.

- **Automatic Chain-of-Responsibility, Aggregation & Admin UI Wiring**: `ScraperRegistry.describe_chains_detailed()` automatically exposes the new source's `source_id`, `display_name`, `priority`, `enabled`, `configured`, `aggregation_mode` (`"enrich_missing"` vs `"fallback_only"`), and `health` (`"ok"`, `"cooldown"`, `"unconfigured"`) to `/api/v1/health`, `/api/v1/admin/config`, and both Web Admin source editors (**Overview** & **API Keys & Scrapers**). In `"enrich_missing"` mode, `CandidateAggregator` (`<= 3.0s` timeout) automatically includes it in cross-source enrichment and composite attribution (`source_used="igdb+custom_retro"`).

---

## 3. Mandatory Architectural, Security & Rendering Invariants

1. **Strict Layer Boundaries**:
   - `app/domain/` must never import `app.application`, `app.infrastructure`, `app.models`, or FastAPI/HTTPX/Pillow/OpenCV.
   - `app/application/` must never import concrete `app.infrastructure.*` adapters or `app.models`; register new ports in `app/domain/ports/` and wire them in `app/container.py`.
2. **Module & Class Size Budgets**: Every Python module must remain `<= 800` lines, every class `<= 750` lines, and every frontend ES module `<= 600` lines (`tests/test_architecture_ddd_fitness.py`). Never embed `<style>` or `<script>` inside `.py` files.
3. **Zero Hardcoded Categories on DMD**: Always normalize upstream genres and systems via `resolve_canonical_system()` and `resolve_canonical_genre()` in `app/domain/taxonomy/`, and expand player shorthands (`sf2x`, `3s`, `kof98`, `garou`) via `app/infrastructure/scrapers/games/arcade_aliases.py`.
4. **Strict Entity, Board, Genre & Wikipedia Disambiguation for Games (`wd:Q7889`)**:
   - Console/computer scrapers must never return hardware console pages (`Nintendo Entertainment System`, `CIC`), unofficial fan mods/ROM hacks/pirate bootlegs (`is_unofficial_game_release()`), or titles from other platforms.
   - `neogeo` maps exclusively to dedicated IGDB platform IDs `[79, 80, 136]` (never generic Arcade `52`), multi-platform console+PlayChoice-10 releases prioritize specific console IDs before `52`, strict `52`/`MAME` arcade sub-boards (`cps1`, `cps2`, `cps3`, `naomi`, `atomiswave`, `model2`, `model3`, `zn`, `pgm`) require `ArcadeItaliaReferentialStrategy.is_rom_authoritative_for_system()` verification, IGDB multi-genre arrays resolve via `CANONICAL_GAME_GENRE_PRIORITY` intersected with `req_genres`, ScreenScraper `jeuRecherche.php` discovery tokens remain `>= 3` characters, and `fetch_wikipedia_localized_synopsis()` enforces page-title token overlap plus video-game vs film/toy/cartoon disambiguation.
5. **Regional Artwork, Strict Synopsis Localization Precedence, Deterministic Panoramas & H.264 Video Exclusivity**:
   - Prioritize localized artwork/synopses for `criteria.lang`: all 11 scrapers tag official human-localized synopses as `synopsis_localized="1"`, translate English-only upstream synopses via `ensure_candidate_localized()` (`app/infrastructure/scrapers/translation_service.py`, `Google GTX -> MyMemory -> Lingva` zero-key cascade cached in a 512-entry LRU) tagged as `"auto"`, and `CandidateAggregator` enforces `"1" > "auto" > "0"` so longer English secondary synopses never overwrite localized primaries while human translations always upgrade `"auto"`/`"0"`. Keep `youtube_resolver.py` strictly H.264 (`[vcodec!*=av01]`) for ARM64 software decoding compatibility.
   - Preserve deterministic uniform zoom-to-cover panorama scrolling (`resolve_showcase_panorama_axis()` in `panorama_and_static.py` + `dmd_gif_converter >= 8.0.10`): **TATE** panels (`height > width`) always scroll **left-to-right**, and **YOKO** panels (`width > height`) always scroll **top-to-bottom** with zero black bars.
6. **3-Tier Credential Vault & Zero-Leak Log Redaction**:
   - Resolve all optional credentials (`IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`, `SCREENSCRAPER_DEV_ID`, `SCREENSCRAPER_DEV_PASS`, `SCREENSCRAPER_USER`, `SCREENSCRAPER_PASS`, `TMDB_API_KEY`, `TVDB_API_KEY`, `SHOWCASE_API_KEY`) exclusively through `server_config_store` (`1. ./data/server_config.json` $\to$ `2. .env / OS env` $\to$ `3. app.security._prod_vault*.so` compiled in CI/CD via `VAULT_MASTER_SECRET` using PBKDF2-HMAC-SHA256 + 16-byte random salt + 3-way XOR).
   - Keep `install_secret_log_redaction()` (`SecretRedactingLogFilter` + `httpx`/`httpcore` `WARNING` level in `app/security/log_redactor.py`) active so query-string secrets (`devpassword`, `sspassword`, `api_key`, `client_secret`) and Bearer tokens never leak into container stdout logs.
7. **Hermetic Portable Bundles (`Windows_x64`, `macOS`, `Linux_x64`) & Retro-Archive TLS Resilience**:
   - Frozen PyInstaller executables resolve `yt-dlp` via `resolve_ytdlp_command()` (`[sys.executable, "--internal-ytdlp"]` early dispatch in `app/main.py` plus in-process `yt_dlp.YoutubeDL` fallback in `youtube_resolver.py`) so hermetic `PATH` isolation never requires an external `yt-dlp.exe` binary.
   - `enforce_hermetic_environment()` exports bundled `certifi` CA roots (`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`), while `default_http_download_bytes()` (`imaging_primitives.py`) retries with an unverified TLS context (cached per host) when legacy retro archives such as `adb.arcadeitalia.net` serve expired certificates.

8. **Unified Lossless `MQTT+TCP` Streaming (`codec=dmdz2`), Compact `item_id`s & Client Transports**:
   - Models 1 and 2A (`supports_local_card: true`) default to `MQTT+TCP`: MQTT (`:1883`) handles `config`, retained `ready`, `start_card`, and `stop_stream`, while the `:8890` data plane (`consume_once.py` + `dmdz_codec.py` + `dmdz_motion.py`) serves a single 100% lossless True-RGB565 `.dmdz2.stream` sibling over plain HTTP `1.1` (`GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge={true|false}&codec=dmdz2`, `X-DMD-Codec: dmdz2`, exact `Content-Length`, plus idempotent `DELETE /api/v1/{domain}/stream/{item_id}.rgb565.stream` cleanup):
     - **`esp32dev` (`0` PSRAM)**: Downloads `.dmdz2.stream` onto its `25 MHz` SD card (`/cache/showcase_live.dmdz`) during the Phase 1 Info Card, closes the TCP socket once downloaded (`0` Wi-Fi traffic and `0` `lwIP` `pbuf`s during playback), and decodes locally via an `8,448 B` lock-free SPSC ring into the Zero-Copy `SRAM1` Canvas (`ScopedCanvasGuard`).
     - **`ESP32-S3` (`8 MB` Octal PSRAM, `0` SD card)**: Preloads `.dmdz2.stream` up to `4 MB` (`freePsram - 1.5 MB` reserve) into PSRAM during the Phase 1 Info Card (`0` Wi-Fi traffic during playback), or streams larger `256x64` HD clips through a `1 MB` PSRAM SPSC ring.
     - **`ArcadeMatrix_RPi` (Linux RAM)**: Preloads lossless `.dmdz2.stream` directly into RAM (with automatic fallback to raw `DMDS` when `codec=dmdz2` is omitted).
     - Preserve `shutil.copy2` mtime copying in `FileOnDemandCacheStore` (`app/infrastructure/persistence/on_demand_cache_store.py`) across `.rgb565.stream` and `.dmdz2.stream` siblings so `ensure_dmdz_sibling()` (`dest.stat().st_mtime >= raw.stat().st_mtime`) never re-encodes on cache checkouts.
   - Keep `item_id` formatted as `pkg_<domain>_<12hex>` (`<= 24` chars) across `SlotPreparationService`, `OnDemandQueueService`, and `StudioPreviewService` so embedded clients never allocate heap buffers, and preserve `ShowcaseRuntimeState.client_transports` (`MQTT+TCP` default, `HTTP`, `UDP-PASSIVE` for Mode 2B `:4210`, and optional `MQTT+UDP`).

---

## 4. Modifying the Modular WebUI (`app/infrastructure/web/static/`)

The Control Center WebUI uses standard native ES Modules (`<script type="module" src="/static/js/app.js"></script>`) with zero build-step overhead:
- Add translations in lockstep across `js/i18n/en.js`, `js/i18n/fr.js`, and `js/i18n/es.js` (100% key parity is verified by pytest).
- Keep shared mutable UI state inside `uiState` (`js/state.js`), including live source badges (`playing_source`, `ready_source`, `standby_source`, `preparing_source`, `source_used`).
- When adding an inline DOM event attribute (`onclick="myAction()"` in `index.html` or dynamic JS templates), export `myAction` from its tab module and register it in `WINDOW_HANDLERS` inside `js/app.js` (verified automatically by `test_webui_static_es_modules_resolution_and_dom_handler_binding`).
- Preserve the strict stylesheet cascade (`css/dashboard.css` → `css/studio.css` → `css/responsive.css`): keep all `@media` breakpoints in `css/responsive.css` (loaded last) and wrap all tables in `.table-wrap` (verified automatically by `test_webui_css_brace_balance_cascade_order_and_responsive_tables`).

---

## 5. Local Docker Build & Running the Test Suites

```bash
# Build and launch local source + latest dmd_gif_converter (uses docker-compose.override.yml automatically)
docker compose up -d --build

# Run all unit, integration, characterization, and DDD architectural fitness tests
pytest -c pyproject.toml tests/

# Run only the Hexagonal/DDD architectural & generic-aggregate fitness gates
pytest -c pyproject.toml tests/test_architecture_ddd_fitness.py
```
