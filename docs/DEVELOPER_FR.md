# Guide Développeur & Extension DDD Modulaire — DMD Media Showcase Server

🇬🇧 [English](DEVELOPER.md) | 🇫🇷 Français | 🇪🇸 [Español](DEVELOPER_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Structure Modulaire du Dépôt

```text
dmd_media_showcase/
├── app/
│   ├── domain/                           # Cœur Domaine pur (zéro import Infrastructure / FastAPI)
│   │   ├── entities.py                   # MediaCandidate, FilterCriteria, ShowcaseCard, PanelGeometry
│   │   ├── board_identity.py             # Identité physique DMD & déduplication NAT Docker / reconnexions
│   │   ├── on_demand_cache_key.py        # Clé déterministe du cache persistant On-Demand
│   │   ├── style_specs.py                # Mises en page fiches, thèmes couleurs, polices bitmap, palettes
│   │   ├── aggregates/                   # Agrégats Domaine génériques & réutilisables
│   │   │   ├── artifact_cache.py         # BoundedTtlCache[TKey, TVal]
│   │   │   ├── slot_pipeline.py          # DualSlotPipeline[TKey, TItem] (Ready N + Standby N+1)
│   │   │   ├── priority_queue.py         # PriorityPlaybackQueue (enqueue_next vs play_now)
│   │   │   └── playlist_cursor.py        # WeightedPlaylistCursor (quotas de rotation multi-domaines)
│   │   ├── taxonomy/                     # Taxonomie multilingue modulaire & SynonymCatalogResolver
│   │   │   ├── common.py                 # Normalisation des slugs, empreintes & décennies
│   │   │   ├── systems.py                # Catalogue unifié RETRO_SYSTEM_CATALOG (66 systèmes / 55+ plateformes)
│   │   │   ├── systems_arcade_console.py # Systèmes arcade (MAME, CPS-1/2/3, Neo-Geo, NAOMI, Model 2/3) & consoles
│   │   │   ├── systems_handheld_computer.py # Consoles portables & micro-ordinateurs classiques
│   │   │   ├── game_genres.py            # GAME_GENRE_CATALOG
│   │   │   ├── cinema_genres.py          # Catalogues MOVIE / SERIES / ANIME
│   │   │   ├── resolver.py               # SynonymCatalogResolver générique
│   │   │   └── consolidator.py           # ReferentialConsolidator & routage par capacité
│   │   └── ports/                        # Interfaces des Ports Hexagonaux + PortProviderRegistry
│   ├── application/                      # Services Applicatifs (dépendent uniquement de domain & ports)
│   │   ├── showcase_state.py             # Conteneur d'état partagé ShowcaseRuntimeState
│   │   ├── concurrency.py                # DynamicConcurrencyLimiter & verrous par clé
│   │   ├── criteria_parser.py            # Normalisation des critères de filtrage par domaine
│   │   ├── client_registry_service.py    # Enregistrement DMD, hello, LWT & télémétrie source live
│   │   ├── slot_preparation_service.py   # Préparation Ready/Standby, claim & purge disque
│   │   ├── on_demand_queue_service.py    # File prioritaire On-Demand & promotion Studio zéro-retranscodage
│   │   ├── passive_director_service.py   # Boucles playlists serveur Modes 2A/2B & contrôles temps réel
│   │   ├── studio_preview_service.py     # Aperçu fiche <100ms & séquence complète DMDS
│   │   ├── source_chain_service.py       # Chaîne de responsabilité multi-sources configurable
│   │   ├── candidate_aggregator.py       # Enrichisseur composite <=3.0s inter-sources (enrich_missing)
│   │   ├── update_checker.py             # Vérificateur de mises à jour GitHub Releases en cache
│   │   └── consume_once_orchestrator.py  # Façade légère d'orchestration
│   ├── container.py                      # Composition Root câblant les adaptateurs aux Ports Domaine
│   ├── infrastructure/                   # Adaptateurs Concrets Entrants & Sortants
│   │   ├── http/                         # Middleware sécurité, reloader live & 6 routeurs FastAPI
│   │   │   └── routers/                  # system_admin, discovery, clients_and_queue, studio, consume_once, rotation_batch
│   │   ├── web/                          # WebUI Control Center externalisé (zéro HTML/JS dans Python)
│   │   │   ├── dashboard_loader.py       # Chargeur portable de index.html, CSS & modules ES
│   │   │   └── static/                   # index.html, css/{dashboard,studio,responsive}.css, js/ (15+ modules ES)
│   │   ├── rendering/                    # Pipeline modulaire de rendu HUB75 RGB565LE
│   │   │   ├── card_geometry.py          # Solveur géométrique YOKO / Carré / TATE
│   │   │   ├── card_layout_engine.py     # 5 layouts de fiches, polices bitmap & rendu DMDS
│   │   │   ├── imaging_primitives.py     # Boost couleur, cadrage & conversion RGB565
│   │   │   ├── panorama_and_static.py    # Panoramas déterministes TATE (G->D) / YOKO (H->B) zoom-to-cover
│   │   │   └── video_pipeline.py         # Détection de fenêtre d'action & encodage trailers
│   │   ├── mqtt/                         # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/                  # JsonSeenHistoryRepository, DmdProfileStore, ServerConfigStore (source_policies)
│   │   ├── scrapers/                     # 11 adaptateurs par domaine + ScraperRegistry + référentiels dédiés
│   │   │   ├── games/                    # igdb_adapter, screenscraper_adapter, arcadeitalia_adapter, libretro_wikidata_adapter, arcade_aliases
│   │   │   ├── movies/                   # tmdb_movie_adapter, itunes_movie_adapter
│   │   │   ├── series/                   # tmdb_series_adapter, tvmaze_adapter (+ tvdb_adapter)
│   │   │   └── anime/                    # jikan_animethemes_adapter, anilist_adapter
│   │   ├── streaming/                    # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/                   # DmdConverterAdapter (dmd_gif_converter >= 8.0.4)
│   ├── security/                         # Coffre-fort natif 3 niveaux & caviardage des logs
│   │   ├── vault.py                      # Chargeur runtime de l'extension native C _prod_vault*.so
│   │   └── log_redactor.py               # SecretRedactingLogFilter + bridage httpx/httpcore à WARNING
│   ├── scheduler/                        # Pool borné de workers async + process/thread
│   │   ├── executor_lifecycle.py         # BoundedWorkerExecutorMixin[TJob] générique
│   │   └── session_batch_manager.py      # Gestionnaire de sessions & batchs HTTP
│   ├── asyncapi_spec.py                  # Charge docs/asyncapi.yaml directement depuis le disque
│   └── main.py                           # Bootstrap FastAPI léger, montage /static & routeurs
├── scripts/compile_prod_vault.py         # Compilateur CI/CD PBKDF2-HMAC-SHA256 + XOR 3 parts (.so/.pyd)
├── docker-compose.override.yml           # Surcharge auto pour build local + dernier dmd_gif_converter
├── docs/                                 # Documentation EN/FR/ES, spécifications OpenAPI 3.1 & AsyncAPI 2.6
└── tests/                                # Suites de tests unitaires, d'intégration, caractérisation & fitness DDD
```

---

## 2. Ajouter une Nouvelle Source ou un Fallback en 3 Étapes Simples

Grâce au découplage `ScraperRegistry` + `DomainSourceChainService` + `CandidateAggregator`, l'ajout d'une source de données ou d'un fallback sur n'importe quel domaine (`games`, `movies`, `series`, `anime`) s'effectue en **3 étapes isolées, sans aucune modification de l'orchestrateur ni du code WebUI** :

### Étape 1 — Implémenter `IScraperSource` dans `app/infrastructure/scrapers/<domaine>/`

Créez votre adaptateur implémentant `IScraperSource` (`app/domain/ports/scraper_source.py`), éventuellement accompagné d'une stratégie `IScraperReferentialStrategy` (`app/domain/ports/referential_strategy.py`) persistée dans `./data/referentials/{source_id}.json` :

```python
from app.domain.entities import DomainType, FilterCriteria, MediaCandidate
from app.domain.ports.scraper_source import IScraperSource


class CustomRetroAdapter(IScraperSource):
    source_id = "custom_retro"
    display_name = "Custom Retro Catalog"
    domain: DomainType = "games"
    role = "fallback"

    def is_configured(self) -> bool:
        return True  # Ou vérifier une clé API via server_config_store.get_all()

    def is_in_cooldown(self) -> bool:
        return False  # Disjoncteur optionnel en cas de quota / surcharge distante

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

### Étape 2 — Enregistrer l'Adaptateur dans `ScraperRegistry` (`app/infrastructure/scrapers/registry.py`)

Ajoutez `CustomRetroAdapter()` dans `self._chains["games"]` au sein de `ScraperRegistry.__init__()` (ou via `scraper_registry.register(CustomRetroAdapter())`).

### Étape 3 — Déclarer le Slot par Défaut dans `DEFAULT_SOURCE_POLICIES` (`app/infrastructure/persistence/server_config_store.py`)

Ajoutez `"custom_retro"` dans `DEFAULT_SOURCE_POLICIES["games"]["ordered_sources"]`.

- **Prise en Charge Automatique (Chaîne, Agrégation & UI Admin)** : `ScraperRegistry.describe_chains_detailed()` expose automatiquement `source_id`, `display_name`, `priority`, `enabled`, `configured`, `aggregation_mode` (`"enrich_missing"` vs `"fallback_only"`) et `health` (`"ok"`, `"cooldown"`, `"unconfigured"`) sur `/api/v1/health`, `/api/v1/admin/config` ainsi que dans les deux éditeurs du Dashboard Web (**Vue d'Ensemble** et **Clés API & Scrapers**). En mode `"enrich_missing"`, `CandidateAggregator` (`<= 3.0s`) l'inclut automatiquement dans l'enrichissement croisé et l'attribution combinée (`source_used="igdb+custom_retro"`).

---

## 3. Invariants Architecturaux, Sécurité & Rendu Obligatoires

1. **Isolation Stricte des Couches Hexagonales** :
   - `app/domain/` ne doit jamais importer `app.application`, `app.infrastructure`, `app.models`, ni FastAPI/HTTPX/Pillow/OpenCV.
   - `app/application/` ne doit jamais importer les adaptateurs concrets `app.infrastructure.*` ni `app.models` ; déclarer tout nouveau contrat dans `app/domain/ports/` et le câbler dans `app/container.py`.
2. **Budgets Anti-God-Class & Anti-God-Module** : Chaque module Python reste `<= 800` lignes, chaque classe `<= 750` lignes, et chaque module ES frontend `<= 600` lignes (`tests/test_architecture_ddd_fitness.py`). Aucun bloc `<style>` ou `<script>` ne doit être intégré dans un fichier `.py`.
3. **Zéro Catégorie Figée sur le DMD** : Toujours normaliser les systèmes et genres via `resolve_canonical_system()` et `resolve_canonical_genre()` dans `app/domain/taxonomy/`, et résoudre les alias joueurs (`sf2x`, `3s`, `kof98`, `garou`) via `app/infrastructure/scrapers/games/arcade_aliases.py`.
4. **Isolation Stricte des Systèmes, Genres, Mods & Homonymes Wikipédia (`wd:Q7889`)** :
   - Les scrapers de jeux rejettent systématiquement les pages de consoles matérielles (`Nintendo Entertainment System`, `CIC`), les ROM hacks / mods amateurs / bootlegs pirates (`is_unofficial_game_release()`) ainsi que les attributions croisées inter-plateformes.
   - `neogeo` utilise exclusivement les identifiants IGDB dédiés `[79, 80, 136]` (jamais l'ID générique Arcade `52`), les titres consoles réédités sur borne PlayChoice-10 priorisent leur console dédiée avant `52`, les sous-systèmes d'arcade partageant `52`/`MAME` (`cps1`, `cps2`, `cps3`, `naomi`, `atomiswave`, `model2`, `model3`, `zn`, `pgm`) exigent une validation `ArcadeItaliaReferentialStrategy.is_rom_authoritative_for_system()`, les genres multiples IGDB sont ordonnés selon `CANONICAL_GAME_GENRE_PRIORITY`, les jetons `jeuRecherche.php` ScreenScraper font `>= 3` caractères, et `fetch_wikipedia_localized_synopsis()` vérifie la correspondance du titre d'article et rejette les homonymes films / jouets / dessins animés.
5. **Priorité Régionale, Hiérarchie Stricte de Localisation des Synopsis, Panoramas Déterministes & Exclusion AV1** :
   - Prioriser les jaquettes et synopsis localisés selon `criteria.lang` : les 11 scrapers marquent les synopsis humains officiels en `synopsis_localized="1"`, traduisent à la volée les synopsis exclusivement anglophones via `ensure_candidate_localized()` (`app/infrastructure/scrapers/translation_service.py`, cascade sans clé `Google GTX -> MyMemory -> Lingva` avec cache LRU 512 entrées) marqués `"auto"`, et `CandidateAggregator` applique strictement `"1" > "auto" > "0"` afin qu'un synopsis secondaire anglais plus long n'écrase jamais un synopsis localisé tandis qu'une traduction humaine officielle remplace toujours `"auto"`/`"0"`. Forcer H.264 (`[vcodec!*=av01]`) dans `youtube_resolver.py` pour une compatibilité ARM64 / Docker totale.
   - Préserver l'axe déterministe des panoramas *zoom-to-cover* (`resolve_showcase_panorama_axis()` dans `panorama_and_static.py` + `dmd_gif_converter >= 8.0.10`) : défilement **gauche $\to$ droite** sur panneaux **TATE** (`height > width`) et **haut $\to$ bas** sur panneaux **YOKO** (`width > height`), sans bandes noires.
6. **Coffre-Fort à 3 Niveaux & Zéro Fuite dans les Logs HTTP** :
   - Résoudre tous les secrets (`IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET`, `SCREENSCRAPER_DEV_ID`, `SCREENSCRAPER_DEV_PASS`, `SCREENSCRAPER_USER`, `SCREENSCRAPER_PASS`, `TMDB_API_KEY`, `TVDB_API_KEY`, `SHOWCASE_API_KEY`) exclusivement via `server_config_store` (`1. ./data/server_config.json` $\to$ `2. .env / env OS` $\to$ `3. app.security._prod_vault*.so` compilé en CI/CD avec `VAULT_MASTER_SECRET`, PBKDF2-HMAC-SHA256 + sel aléatoire 16 octets + XOR 3 parts).
   - Maintenir `install_secret_log_redaction()` (`SecretRedactingLogFilter` + `httpx`/`httpcore` bridés à `WARNING` dans `app/security/log_redactor.py`) actif afin qu'aucun secret en query-string (`devpassword`, `sspassword`, `api_key`, `client_secret`) ni jeton Bearer ne fuite dans stdout.
7. **Bundles Portables Hermétiques (`Windows_x64`, `macOS`, `Linux_x64`) & Résilience TLS Archives Rétro** :
   - Les exécutables PyInstaller figés résolvent `yt-dlp` via `resolve_ytdlp_command()` (dispatch précoce `[sys.executable, "--internal-ytdlp"]` dans `app/main.py` + repli Python `yt_dlp.YoutubeDL` dans `youtube_resolver.py`), éliminant tout besoin d'un binaire `yt-dlp.exe` externe malgré le `PATH` hermétique.
   - `enforce_hermetic_environment()` exporte le magasin CA `certifi` embarqué (`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`), tandis que `default_http_download_bytes()` (`imaging_primitives.py`) bascule automatiquement (avec cache par hôte) vers un contexte TLS non vérifié lorsqu'une archive rétro comme `adb.arcadeitalia.net` présente un certificat expiré.

---

## 4. Faire Évoluer le WebUI Modulaire (`app/infrastructure/web/static/`)

Le Control Center utilise des ES Modules natifs (`<script type="module" src="/static/js/app.js"></script>`) sans bundler lourd :
- Maintenir la parité 100 % des clés de traduction entre `js/i18n/en.js`, `js/i18n/fr.js` et `js/i18n/es.js` (vérifiée automatiquement par pytest).
- Centraliser tout état UI partagé dans `uiState` (`js/state.js`), y compris les badges de télémétrie source en direct (`playing_source`, `ready_source`, `standby_source`, `preparing_source`, `source_used`).
- Tout gestionnaire d'événement HTML inline (`onclick="monAction()"` dans `index.html` ou les templates JS dynamiques) doit être enregistré dans `WINDOW_HANDLERS` au sein de `js/app.js` (vérifié automatiquement par `test_webui_static_es_modules_resolution_and_dom_handler_binding`).
- Respecter l'ordre strict de cascade CSS (`css/dashboard.css` → `css/studio.css` → `css/responsive.css`) : toutes les règles `@media` responsives résident dans `css/responsive.css` (chargé en dernier) et chaque tableau HTML est encapsulé dans `.table-wrap` (vérifié automatiquement par `test_webui_css_brace_balance_cascade_order_and_responsive_tables`).

---

## 5. Build Docker Local & Exécution des Tests

```bash
# Compiler et démarrer les sources locales + dernier dmd_gif_converter (charge docker-compose.override.yml)
docker compose up -d --build

# Exécuter l'ensemble des suites unitaires, d'intégration, de caractérisation et de fitness DDD
pytest -c pyproject.toml tests/

# Exécuter uniquement les garde-fous d'architecture Hexagonale/DDD et de généricité
pytest -c pyproject.toml tests/test_architecture_ddd_fitness.py
```
