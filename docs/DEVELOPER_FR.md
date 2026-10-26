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
│   │   ├── style_specs.py                # Mises en page fiches, thèmes couleurs, polices bitmap, palettes
│   │   ├── aggregates/                   # Agrégats Domaine génériques & réutilisables
│   │   │   ├── artifact_cache.py         # BoundedTtlCache[TKey, TVal]
│   │   │   ├── slot_pipeline.py          # DualSlotPipeline[TKey, TItem] (Ready N + Standby N+1)
│   │   │   ├── priority_queue.py         # PriorityPlaybackQueue (enqueue_next vs play_now)
│   │   │   └── playlist_cursor.py        # WeightedPlaylistCursor (quotas de rotation multi-domaines)
│   │   ├── taxonomy/                     # Taxonomie multilingue modulaire & SynonymCatalogResolver
│   │   │   ├── common.py                 # Normalisation des slugs & décennies
│   │   │   ├── systems.py                # RETRO_SYSTEM_CATALOG
│   │   │   ├── game_genres.py            # GAME_GENRE_CATALOG
│   │   │   ├── cinema_genres.py          # Catalogues MOVIE / SERIES / ANIME
│   │   │   ├── resolver.py               # SynonymCatalogResolver générique
│   │   │   └── consolidator.py           # ReferentialConsolidator
│   │   └── ports/                        # Interfaces des Ports Hexagonaux + PortProviderRegistry
│   ├── application/                      # Services Applicatifs (dépendent uniquement de domain & ports)
│   │   ├── showcase_state.py             # Conteneur d'état partagé ShowcaseRuntimeState
│   │   ├── concurrency.py                # DynamicConcurrencyLimiter & verrous par clé
│   │   ├── criteria_parser.py            # Normalisation des critères de filtrage par domaine
│   │   ├── client_registry_service.py    # Enregistrement DMD, hello, LWT & télémétrie
│   │   ├── slot_preparation_service.py   # Préparation Ready/Standby, claim & purge disque
│   │   ├── on_demand_queue_service.py    # File prioritaire On-Demand & promotion Studio zéro-retranscodage
│   │   ├── passive_director_service.py   # Boucles playlists serveur Modes 2A/2B & contrôles temps réel
│   │   ├── studio_preview_service.py     # Aperçu fiche <100ms & séquence complète DMDS
│   │   ├── source_chain_service.py       # Routage [Primary, Fallback] par capacité
│   │   ├── update_checker.py             # Vérificateur de mises à jour GitHub Releases en cache
│   │   └── consume_once_orchestrator.py  # Façade légère d'orchestration
│   ├── container.py                      # Composition Root câblant les adaptateurs aux Ports Domaine
│   ├── infrastructure/                   # Adaptateurs Concrets Entrants & Sortants
│   │   ├── http/                         # Middleware sécurité, reloader live & 6 routeurs FastAPI
│   │   │   └── routers/                  # system_admin, discovery, clients_and_queue, studio, consume_once, rotation_batch
│   │   ├── web/                          # WebUI Control Center externalisé (zéro HTML/JS dans Python)
│   │   │   ├── dashboard_loader.py       # Chargeur portable de index.html, CSS & modules ES
│   │   │   └── static/                   # index.html, css/{dashboard,studio}.css, js/ (15 modules ES)
│   │   ├── rendering/                    # Pipeline modulaire de rendu HUB75 RGB565LE
│   │   │   ├── card_geometry.py          # Solveur géométrique YOKO / Carré / TATE
│   │   │   ├── card_layout_engine.py     # 5 layouts de fiches, polices bitmap & rendu DMDS
│   │   │   ├── imaging_primitives.py     # Boost couleur, cadrage & conversion RGB565
│   │   │   ├── panorama_and_static.py    # Panoramas Ken Burns / pan-scan & mini-jaquettes
│   │   │   └── video_pipeline.py         # Détection de fenêtre d'action & encodage trailers
│   │   ├── mqtt/                         # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/                  # JsonSeenHistoryRepository, DmdProfileStore, ServerConfigStore
│   │   ├── scrapers/                     # Adaptateurs games/, movies/, series/, anime/ + référentiels dédiés
│   │   ├── streaming/                    # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/                   # DmdConverterAdapter (dmd_gif_converter)
│   ├── scheduler/                        # Pool borné de workers async + process/thread
│   │   ├── executor_lifecycle.py         # BoundedWorkerExecutorMixin[TJob] générique
│   │   └── session_batch_manager.py      # Gestionnaire de sessions & batchs HTTP
│   ├── asyncapi_spec.py                  # Charge docs/asyncapi.yaml directement depuis le disque
│   └── main.py                           # Bootstrap FastAPI léger, montage /static & routeurs
├── docs/                                 # Documentation EN/FR/ES, spécifications OpenAPI 3.1 & AsyncAPI 2.6
└── tests/                                # Suites de tests unitaires, d'intégration, caractérisation & fitness DDD
```

---

## 2. Ajouter un Nouveau Scraper (`IScraperSource` & `IScraperReferentialStrategy`)

Chaque adaptateur implémente `IScraperSource` (`app/domain/ports/scraper_source.py`) et s'associe à une stratégie dédiée `IScraperReferentialStrategy` (`app/domain/ports/referential_strategy.py`) qui persiste son référentiel temps réel dans `./data/referentials/{source_id}.json`.

### Invariants Architecturaux & Scrapers Obligatoires
1. **Isolation Stricte des Couches Hexagonales** :
   - `app/domain/` ne doit jamais importer `app.application`, `app.infrastructure`, `app.models`, ni FastAPI/HTTPX/Pillow/OpenCV.
   - `app/application/` ne doit jamais importer les adaptateurs concrets `app.infrastructure.*` ni `app.models` ; déclarer tout nouveau contrat dans `app/domain/ports/` et le câbler dans `app/container.py`.
2. **Budgets Anti-God-Class & Anti-God-Module** : Chaque module Python reste `<= 800` lignes, chaque classe `<= 750` lignes, et chaque module ES frontend `<= 600` lignes (`tests/test_architecture_ddd_fitness.py`). Aucun bloc `<style>` ou `<script>` ne doit être intégré dans un fichier `.py`.
3. **Zéro Catégorie Figée sur le DMD** : Toujours normaliser les systèmes et genres via `resolve_canonical_system()` et `resolve_canonical_genre()` dans `app/domain/taxonomy/`.
4. **Filtrage Strict des Jeux Vidéo (`wd:Q7889`)** : Les scrapers consoles ne doivent jamais retourner d'articles de consoles matérielles (`Nintendo Entertainment System`, `CIC`) ni de jeux d'autres plateformes. Chaque candidat doit correspondre à un fichier vérifié dans `libretro-thumbnails/{repo}`.
5. **Priorité Régionale & Exclusion AV1** : Prioriser les jaquettes `(France) -> (Europe) -> (World) -> (USA)` et forcer H.264 (`[vcodec!*=av01]`) dans `youtube_resolver.py` pour une compatibilité ARM64 / Docker totale.

---

## 3. Faire Évoluer le WebUI Modulaire (`app/infrastructure/web/static/`)

Le Control Center utilise des ES Modules natifs (`<script type="module" src="/static/js/app.js"></script>`) sans bundler lourd :
- Maintenir la parité 100 % des clés de traduction entre `js/i18n/en.js`, `js/i18n/fr.js` et `js/i18n/es.js` (vérifiée automatiquement par pytest).
- Centraliser tout état UI partagé dans `uiState` (`js/state.js`).
- Tout gestionnaire d'événement HTML inline (`onclick="monAction()"` dans `index.html`) doit être enregistré dans `WINDOW_HANDLERS` au sein de `js/app.js` (vérifié automatiquement par `test_webui_static_es_modules_resolution_and_dom_handler_binding`).

---

## 4. Exécution des Tests & Garde-Fous d'Architecture

```bash
# Exécuter l'ensemble des suites unitaires, d'intégration, de caractérisation et de fitness DDD
pytest -c pyproject.toml tests/

# Exécuter uniquement les garde-fous d'architecture Hexagonale/DDD et de généricité
pytest -c pyproject.toml tests/test_architecture_ddd_fitness.py
```
