# Guide Développeur & Stratégies Scrapers — DMD Media Showcase Server

🇬🇧 [English](DEVELOPER.md) | 🇫🇷 Français | 🇪🇸 [Español](DEVELOPER_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Structure du Dépôt

```text
dmd_media_showcase/
├── app/
│   ├── domain/
│   │   ├── entities.py               # MediaCandidate, FilterCriteria, ShowcaseCard, PanelGeometry
│   │   ├── taxonomy.py               # ReferentialConsolidator, Systèmes/Genres/Décennies Canoniques, Synonymes
│   │   └── ports/                    # IScraperSource, IScraperReferentialStrategy, ISeenHistoryRepository
│   ├── application/
│   │   ├── consume_once_orchestrator.py # Slots Consume-Once sans état + Pipelining N+1
│   │   └── source_chain_service.py   # Routage [Primary, Fallback] par capacité
│   ├── infrastructure/
│   │   ├── mqtt/                     # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/              # JsonSeenHistoryRepository & ServerConfigStore
│   │   ├── scrapers/                 # games/, movies/, series/, anime/, referential_store.py, youtube_resolver.py
│   │   ├── streaming/                # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/               # DmdConverterAdapter (dmd_gif_converter)
│   └── main.py                       # API REST FastAPI + Lifespan + Dashboard Web Admin
├── docs/                             # Documentation EN/FR/ES, spécifications OpenAPI 3.1 & AsyncAPI 2.6
└── tests/                            # Suites de tests unitaires et d'intégration DDD
```

---

## 2. Ajouter un Nouveau Scraper (`IScraperSource` & `IScraperReferentialStrategy`)

Chaque adaptateur implémente `IScraperSource` (`app/domain/ports/scraper_source.py`) et s'associe à une stratégie `IScraperReferentialStrategy` (`app/infrastructure/scrapers/referential_store.py`) qui persiste son référentiel temps réel dans `./data/referentials/{source_id}.json`.

### Invariants Obligatoires des Scrapers
1. **Zéro Catégorie Figée sur le DMD** : Toujours normaliser les systèmes et genres via `resolve_canonical_system()` et `resolve_canonical_genre()` dans `app/domain/taxonomy.py`.
2. **Filtrage Strict des Jeux Vidéo (`wd:Q7889`)** : Les scrapers consoles ne doivent jamais retourner d'articles de consoles matérielles (`Nintendo Entertainment System`, `CIC`) ni de jeux d'autres plateformes. Chaque candidat doit correspondre à un fichier vérifié dans `libretro-thumbnails/{repo}`.
3. **Priorité Régionale & Synopsis Localisé** : Lorsque `lang="fr"`, prioriser les jaquettes `(France) -> (Europe) -> (World) -> (USA)` et les synopsis issus de `fr.wikipedia.org`.
4. **Exclusion Stricte du Codec AV1** : `youtube_resolver.py` exclut formellement `av01` (`[vcodec!*=av01]`) et force `-S vcodec:h264,res:720,ext:mp4:m4a` pour garantir un décodage logiciel fluide sur ARM64 / Docker.

---

## 3. Exécution des Tests Unitaires

```bash
pytest -c pyproject.toml tests/
```

