# Developer & Scraper Strategy Guide — DMD Media Showcase Server

🇬🇧 English | 🇫🇷 [Français](DEVELOPER_FR.md) | 🇪🇸 [Español](DEVELOPER_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Repository Structure

```text
dmd_media_showcase/
├── app/
│   ├── domain/
│   │   ├── entities.py               # MediaCandidate, FilterCriteria, ShowcaseCard, PanelGeometry
│   │   ├── taxonomy.py               # ReferentialConsolidator, Canonical Systems/Genres/Decades, Synonyms
│   │   └── ports/                    # IScraperSource, IScraperReferentialStrategy, ISeenHistoryRepository
│   ├── application/
│   │   ├── consume_once_orchestrator.py # Stateless Consume-Once slots + N+1 background pipelining
│   │   └── source_chain_service.py   # [Primary, Fallback] capability-aware scraper routing
│   ├── infrastructure/
│   │   ├── mqtt/                     # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/              # JsonSeenHistoryRepository & ServerConfigStore
│   │   ├── scrapers/                 # games/, movies/, series/, anime/, referential_store.py, youtube_resolver.py
│   │   ├── streaming/                # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/               # DmdConverterAdapter (dmd_gif_converter)
│   └── main.py                       # FastAPI REST API + Lifespan + Web Admin Dashboard
├── docs/                             # EN/FR/ES Documentation, OpenAPI 3.1 & AsyncAPI 2.6 specs
└── tests/                            # End-to-end & DDD unit test suites
```

---

## 2. Implementing a New `IScraperSource` & `IScraperReferentialStrategy`

Every scraper source implements `IScraperSource` (`app/domain/ports/scraper_source.py`) and pairs with an `IScraperReferentialStrategy` (`app/infrastructure/scrapers/referential_store.py`) that persists live upstream tokens in `./data/referentials/{source_id}.json`:

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

    async def fetch_random_unseen(self, criteria: FilterCriteria, seen_ids: set[str], count: int = 1) -> list[MediaCandidate]:
        ...

    async def search_by_query(self, query: str, lang: str = "fr"):
        return None

scraper_registry.register(CustomRetroAdapter())
```

### Mandatory Scraper Invariants
1. **Zero Hardcoded Categories on DMD**: Always map upstream genres and systems through `resolve_canonical_system()` and `resolve_canonical_genre()` in `app/domain/taxonomy.py`.
2. **Strict Entity Filtering for Games (`wd:Q7889`)**: Console/computer game scrapers must never return hardware consoles (`Nintendo Entertainment System`, `CIC`) or games from other platforms. Every candidate must match a verified asset in `libretro-thumbnails/{repo}`.
3. **Regional Artwork & Synopsis Priority**: When `lang="fr"`, prioritize `(France) -> (Europe) -> (World) -> (USA)` boxarts and `fr.wikipedia.org` synopses.
4. **H.264 Video Exclusivity**: `youtube_resolver.py` strictly excludes `av01` (`[vcodec!*=av01]`) and enforces `-S vcodec:h264,res:720,ext:mp4:m4a` so ARM64 containers without hardware AV1 decoding never fail.

---

## 3. Running the Test Suite

```bash
pytest -c pyproject.toml tests/
```

