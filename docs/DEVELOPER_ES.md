# Guía para Desarrolladores y Extensión DDD Modular — DMD Media Showcase Server

🇬🇧 [English](DEVELOPER.md) | 🇫🇷 [Français](DEVELOPER_FR.md) | 🇪🇸 Español

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Estructura Modular del Repositorio

```text
dmd_media_showcase/
├── app/
│   ├── domain/                           # Núcleo de Dominio puro (cero imports de Infraestructura / FastAPI)
│   │   ├── entities.py                   # MediaCandidate, FilterCriteria, ShowcaseCard, PanelGeometry
│   │   ├── board_identity.py             # Identidad física de placa DMD y deduplicación NAT Docker
│   │   ├── on_demand_cache_key.py        # Clave determinista de la caché persistente On-Demand
│   │   ├── style_specs.py                # Diseños de tarjetas, temas de color, fuentes píxel, paletas
│   │   ├── aggregates/                   # Agregados de Dominio genéricos y reutilizables
│   │   │   ├── artifact_cache.py         # BoundedTtlCache[TKey, TVal]
│   │   │   ├── slot_pipeline.py          # DualSlotPipeline[TKey, TItem] (Ready N + Standby N+1)
│   │   │   ├── priority_queue.py         # PriorityPlaybackQueue (enqueue_next vs play_now)
│   │   │   └── playlist_cursor.py        # WeightedPlaylistCursor (cuotas multi-dominio)
│   │   ├── taxonomy/                     # Taxonomía multilingüe modular & SynonymCatalogResolver
│   │   │   ├── common.py                 # Normalización de slugs, huellas y décadas
│   │   │   ├── systems.py                # Catálogo unificado RETRO_SYSTEM_CATALOG (66 sistemas / 55+ plataformas)
│   │   │   ├── systems_arcade_console.py # Placas arcade (MAME, CPS-1/2/3, Neo-Geo, NAOMI, Model 2/3) y consolas
│   │   │   ├── systems_handheld_computer.py # Consolas portátiles y microordenadores clásicos
│   │   │   ├── game_genres.py            # GAME_GENRE_CATALOG
│   │   │   ├── cinema_genres.py          # Catálogos MOVIE / SERIES / ANIME
│   │   │   ├── resolver.py               # SynonymCatalogResolver genérico
│   │   │   └── consolidator.py           # ReferentialConsolidator y enrutamiento por capacidad
│   │   └── ports/                        # Interfaces de Puertos Hexagonales + PortProviderRegistry
│   ├── application/                      # Servicios de Aplicación (dependen solo de domain y ports)
│   │   ├── showcase_state.py             # Estado compartido ShowcaseRuntimeState
│   │   ├── concurrency.py                # DynamicConcurrencyLimiter y bloqueos por clave
│   │   ├── criteria_parser.py            # Normalización de criterios por dominio
│   │   ├── client_registry_service.py    # Registro DMD, hello, LWT y telemetría de fuente en vivo
│   │   ├── slot_preparation_service.py   # Preparación Ready/Standby, claim y limpieza en disco
│   │   ├── on_demand_queue_service.py    # Cola prioritaria On-Demand y promoción Studio
│   │   ├── passive_director_service.py   # Listas gestionadas por servidor Modos 2A/2B y controles
│   │   ├── studio_preview_service.py     # Vista previa <100ms y secuencia completa DMDS
│   │   ├── source_chain_service.py       # Cadena de responsabilidad multifuente configurable
│   │   ├── candidate_aggregator.py       # Enriquecedor compuesto <=3.0s multifuente (enrich_missing)
│   │   ├── update_checker.py             # Comprobador en caché de versiones GitHub Releases
│   │   └── consume_once_orchestrator.py  # Fachada ligera de orquestación
│   ├── container.py                      # Composition Root que conecta adaptadores con puertos
│   ├── infrastructure/                   # Adaptadores Concretos de Entrada y Salida
│   │   ├── http/                         # Middleware de seguridad, reloader en vivo y 6 routers FastAPI
│   │   ├── web/                          # WebUI Control Center externalizada (index.html, CSS y 15+ módulos ES)
│   │   ├── rendering/                    # Motor modular HUB75 RGB565LE (incl. panoramas YOKO/TATE zoom-to-cover)
│   │   ├── mqtt/                         # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/                  # JsonSeenHistoryRepository, DmdProfileStore, ServerConfigStore (source_policies)
│   │   ├── scrapers/                     # 11 adaptadores por dominio + ScraperRegistry + referenciales por fuente
│   │   │   ├── games/                    # igdb_adapter, screenscraper_adapter, arcadeitalia_adapter, libretro_wikidata_adapter, arcade_aliases
│   │   │   ├── movies/                   # tmdb_movie_adapter, itunes_movie_adapter
│   │   │   ├── series/                   # tmdb_series_adapter, tvmaze_adapter (+ tvdb_adapter)
│   │   │   └── anime/                    # jikan_animethemes_adapter, anilist_adapter
│   │   ├── streaming/                    # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/                   # DmdConverterAdapter (dmd_gif_converter >= 8.0.4)
│   ├── security/                         # Bóveda nativa de 3 niveles y redacción de logs
│   │   ├── vault.py                      # Cargador en tiempo de ejecución de _prod_vault*.so
│   │   └── log_redactor.py               # SecretRedactingLogFilter + httpx/httpcore en WARNING
│   ├── scheduler/                        # BoundedWorkerExecutorMixin[TJob] & SessionBatchManager
│   ├── asyncapi_spec.py                  # Carga docs/asyncapi.yaml directamente desde disco
│   └── main.py                           # Arranque ligero de FastAPI, montaje /static y routers
├── scripts/compile_prod_vault.py         # Compilador CI/CD PBKDF2-HMAC-SHA256 + XOR de 3 partes (.so/.pyd)
├── docker-compose.override.yml           # Override automático de compilación local + dmd_gif_converter
├── docs/                                 # Documentación EN/FR/ES, especificaciones OpenAPI 3.1 y AsyncAPI 2.6
└── tests/                                # Suites de pruebas unitarias, integración, caracterización y fitness DDD
```

---

## 2. Añadir una Nueva Fuente de Datos o Respaldo en 3 Pasos Limpios

Gracias al diseño componible `ScraperRegistry` + `DomainSourceChainService` + `CandidateAggregator`, añadir una nueva fuente o respaldo a cualquier dominio (`games`, `movies`, `series`, `anime`) requiere **3 pasos aislados y cero cambios en el orquestador o en el código de la UI**:

### Paso 1 — Implementar `IScraperSource` en `app/infrastructure/scrapers/<dominio>/`

Cree su adaptador implementando `IScraperSource` (`app/domain/ports/scraper_source.py`), opcionalmente junto con una estrategia `IScraperReferentialStrategy` (`app/domain/ports/referential_strategy.py`) persistida en `./data/referentials/{source_id}.json`:

```python
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource


class CustomRetroAdapter(IScraperSource):
    source_id = "custom_retro"
    display_name = "Custom Retro Catalog"
    domain: DomainType = "games"
    role = "fallback"

    def is_configured(self) -> bool:
        return True  # O comprobar credenciales mediante server_config_store.get_all()

    def is_in_cooldown(self) -> bool:
        return False  # Disyuntor opcional ante cuota o sobrecarga remota

    def supported_filters(self, lang: str = "es"):
        return {}

    async def fetch_random_unseen(
        self, criteria: FilterCriteria, seen_ids: set[str], count: int = 1
    ) -> list[MediaCandidate]:
        ...

    async def search_by_query(self, query: str, lang: str = "es"):
        return None

    async def search_candidates(
        self, query: str, criteria: FilterCriteria, limit: int = 8
    ) -> list[MediaCandidate]:
        ...
```

### Paso 2 — Registrar el Adaptador en `ScraperRegistry` (`app/infrastructure/scrapers/registry.py`)

Añada `CustomRetroAdapter()` a `self._chains["games"]` en `ScraperRegistry.__init__()` (o mediante `scraper_registry.register(CustomRetroAdapter())`).

### Paso 3 — Añadir la Ranura por Defecto en `DEFAULT_SOURCE_POLICIES` (`app/infrastructure/persistence/server_config_store.py`)

Añada `"custom_retro"` a `DEFAULT_SOURCE_POLICIES["games"]["ordered_sources"]`.

- **Integración Automática en Cadena, Agregador y Panel Web**: `ScraperRegistry.describe_chains_detailed()` expone automáticamente `source_id`, `display_name`, `priority`, `enabled`, `configured`, `aggregation_mode` (`"enrich_missing"` vs `"fallback_only"`) y `health` (`"ok"`, `"cooldown"`, `"unconfigured"`) en `/api/v1/health`, `/api/v1/admin/config` y en ambos editores del Panel Web (**Vista General** y **Claves API y Scrapers**). En modo `"enrich_missing"`, `CandidateAggregator` (`<= 3.0s`) lo incorpora automáticamente al enriquecimiento cruzado y a la atribución combinada (`source_used="igdb+custom_retro"`).

---

## 3. Invariantes Arquitectónicos, de Seguridad y Renderizado

1. **Aislamiento Estricto de Capas Hexagonales**:
   - `app/domain/` nunca debe importar `app.application`, `app.infrastructure`, `app.models` ni FastAPI/HTTPX/Pillow/OpenCV.
   - `app/application/` nunca debe importar adaptadores concretos de `app.infrastructure.*` ni `app.models`; declarar los contratos en `app/domain/ports/` y conectarlos en `app/container.py`.
2. **Presupuestos Anti-God-Class y Anti-God-Module**: Cada módulo Python se mantiene `<= 800` líneas, cada clase `<= 750` líneas y cada módulo ES frontend `<= 600` líneas (`tests/test_architecture_ddd_fitness.py`), con cero etiquetas `<style>` o `<script>` incrustadas en Python.
3. **Cero Categorías Fijas en el DMD**: Normalizar siempre sistemas y géneros mediante `resolve_canonical_system()` y `resolve_canonical_genre()` en `app/domain/taxonomy/`, y expandir alias cortos de jugadores (`sf2x`, `3s`, `kof98`, `garou`) mediante `app/infrastructure/scrapers/games/arcade_aliases.py`.
4. **Aislamiento Estricto de Placas, Géneros, Mods y Homónimos de Wikipedia (`wd:Q7889`)**:
   - Los scrapers de videojuegos rechazan páginas de consolas físicas, ROM hacks / mods no oficiales / bootlegs piratas (`is_unofficial_game_release()`) y atribuciones cruzadas entre plataformas.
   - `neogeo` utiliza exclusivamente los IDs dedicados de IGDB `[79, 80, 136]` (nunca el ID genérico Arcade `52`), los juegos de consola lanzados también en PlayChoice-10 priorizan su consola dedicada antes que `52`, las subplacas `52`/`MAME` (`cps1`, `cps2`, `cps3`, `naomi`, `atomiswave`, `model2`, `model3`, `zn`, `pgm`) exigen verificación `ArcadeItaliaReferentialStrategy.is_rom_authoritative_for_system()`, los géneros múltiples de IGDB se priorizan con `CANONICAL_GAME_GENRE_PRIORITY`, los tokens `jeuRecherche.php` de ScreenScraper tienen `>= 3` caracteres y `fetch_wikipedia_localized_synopsis()` valida los tokens del título rechazando películas, juguetes o series homónimas.
5. **Prioridad Regional, Jerarquía Estricta de Localización de Sinopsis, Panoramas Deterministas y Exclusión de AV1**:
   - Los 11 scrapers etiquetan las sinopsis humanas oficiales como `synopsis_localized="1"`, traducen las sinopsis exclusivamente en inglés mediante `ensure_candidate_localized()` (`app/infrastructure/scrapers/translation_service.py`, cascada gratuita `Google GTX -> MyMemory -> Lingva` con caché LRU de 512 entradas) como `"auto"`, y `CandidateAggregator` impone `"1" > "auto" > "0"` para que una sinopsis secundaria en inglés más larga nunca sobrescriba una sinopsis localizada. `youtube_resolver.py` excluye `av01` (`[vcodec!*=av01]`) priorizando H.264 para decodificación ARM64.
   - `resolve_showcase_panorama_axis()` (`panorama_and_static.py` + `dmd_gif_converter >= 8.0.10`) aplica escalado uniforme *zoom-to-cover* sin bandas negras: desplazamiento **izquierda $\to$ derecha** en paneles **TATE** (`height > width`) y **arriba $\to$ abajo** en paneles **YOKO** (`width > height`).
6. **Bóveda de Credenciales de 3 Niveles y Cero Filtración en Logs HTTP**:
   - Resolver todas las credenciales opcionales (`IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`, `SCREENSCRAPER_DEV_ID`, `SCREENSCRAPER_DEV_PASS`, `SCREENSCRAPER_USER`, `SCREENSCRAPER_PASS`, `TMDB_API_KEY`, `TVDB_API_KEY`, `SHOWCASE_API_KEY`) exclusivamente a través de `server_config_store` (`1. ./data/server_config.json` $\to$ `2. .env / SO` $\to$ `3. app.security._prod_vault*.so` compilado en CI/CD con `VAULT_MASTER_SECRET`, PBKDF2-HMAC-SHA256 + sal aleatoria de 16 bytes + XOR de 3 partes).
   - Mantener activo `install_secret_log_redaction()` (`SecretRedactingLogFilter` + `httpx`/`httpcore` en `WARNING` en `app/security/log_redactor.py`) para que secretos en query-string (`devpassword`, `sspassword`, `api_key`, `client_secret`) y tokens Bearer nunca lleguen a stdout.
7. **Paquetes Portátiles Herméticos (`Windows_x64`, `macOS`, `Linux_x64`) y Resiliencia TLS en Archivos Retro**:
   - Los ejecutables PyInstaller congelados resuelven `yt-dlp` mediante `resolve_ytdlp_command()` (despacho temprano `[sys.executable, "--internal-ytdlp"]` en `app/main.py` más respaldo en proceso `yt_dlp.YoutubeDL` en `youtube_resolver.py`) sin requerir un binario `yt-dlp.exe` externo.
   - `enforce_hermetic_environment()` exporta los certificados raíz `certifi` incluidos (`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`), mientras que `default_http_download_bytes()` (`imaging_primitives.py`) reintenta con contexto TLS no verificado (cacheado por host) cuando archivos retro como `adb.arcadeitalia.net` presentan certificados caducados.
8. **Cascada CSS, Paridad I18n y Tablas Responsivas del WebUI (`app/infrastructure/web/static/`)**: Mantener paridad al 100 % entre `js/i18n/{en,fr,es}.js`, conservar el estado compartido (incluidas las insignias de fuente `playing_source`, `ready_source`, `standby_source`, `preparing_source`, `source_used`) en `uiState` (`js/state.js`), respetar el orden `css/dashboard.css` → `css/studio.css` → `css/responsive.css` (todos los `@media` en `responsive.css`), envolver cada `<table>` en `.table-wrap` y registrar cada manejador inline `onclick`/`onchange` en `WINDOW_HANDLERS` de `js/app.js`.

---

## 4. Compilación Docker Local y Ejecución de Pruebas

```bash
# Compilar e iniciar el código local + último dmd_gif_converter (usa docker-compose.override.yml automáticamente)
docker compose up -d --build

# Ejecutar todas las pruebas unitarias, de integración, caracterización y fitness DDD
pytest -c pyproject.toml tests/

# Ejecutar únicamente las pruebas de arquitectura Hexagonal/DDD y agregados genéricos
pytest -c pyproject.toml tests/test_architecture_ddd_fitness.py
```
