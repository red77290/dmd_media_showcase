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
│   │   ├── style_specs.py                # Diseños de tarjetas, temas de color, fuentes píxel, paletas
│   │   ├── aggregates/                   # Agregados de Dominio genéricos y reutilizables
│   │   │   ├── artifact_cache.py         # BoundedTtlCache[TKey, TVal]
│   │   │   ├── slot_pipeline.py          # DualSlotPipeline[TKey, TItem] (Ready N + Standby N+1)
│   │   │   ├── priority_queue.py         # PriorityPlaybackQueue (enqueue_next vs play_now)
│   │   │   └── playlist_cursor.py        # WeightedPlaylistCursor (cuotas multi-dominio)
│   │   ├── taxonomy/                     # Taxonomía multilingüe modular & SynonymCatalogResolver
│   │   └── ports/                        # Interfaces de Puertos Hexagonales + PortProviderRegistry
│   ├── application/                      # Servicios de Aplicación (dependen solo de domain y ports)
│   │   ├── showcase_state.py             # Estado compartido ShowcaseRuntimeState
│   │   ├── concurrency.py                # DynamicConcurrencyLimiter y bloqueos por clave
│   │   ├── criteria_parser.py            # Normalización de criterios por dominio
│   │   ├── client_registry_service.py    # Registro DMD, hello, LWT y telemetría
│   │   ├── slot_preparation_service.py   # Preparación Ready/Standby, claim y limpieza en disco
│   │   ├── on_demand_queue_service.py    # Cola prioritaria On-Demand y promoción Studio
│   │   ├── passive_director_service.py   # Listas gestionadas por servidor Modos 2A/2B y controles
│   │   ├── studio_preview_service.py     # Vista previa <100ms y secuencia completa DMDS
│   │   ├── source_chain_service.py       # Enrutamiento [Primary, Fallback] por capacidad
│   │   ├── update_checker.py             # Comprobador en caché de versiones GitHub Releases
│   │   └── consume_once_orchestrator.py  # Fachada ligera de orquestación
│   ├── container.py                      # Composition Root que conecta adaptadores con puertos
│   ├── infrastructure/                   # Adaptadores Concretos de Entrada y Salida
│   │   ├── http/                         # Middleware de seguridad, reloader en vivo y 6 routers FastAPI
│   │   ├── web/                          # WebUI Control Center externalizada (index.html, CSS y 15 módulos ES)
│   │   ├── rendering/                    # Motor modular de renderizado HUB75 RGB565LE
│   │   ├── mqtt/                         # EmbeddedMqttBroker (:1883) & ShowcaseMqttGateway
│   │   ├── persistence/                  # JsonSeenHistoryRepository, DmdProfileStore, ServerConfigStore
│   │   ├── scrapers/                     # Adaptadores games/, movies/, series/, anime/ + referenciales por fuente
│   │   ├── streaming/                    # FfmpegUdpClockStreamer (ffmpeg -re -> UDP DMDF)
│   │   └── transcoder/                   # DmdConverterAdapter (dmd_gif_converter)
│   ├── scheduler/                        # BoundedWorkerExecutorMixin[TJob] & SessionBatchManager
│   ├── asyncapi_spec.py                  # Carga docs/asyncapi.yaml directamente desde disco
│   └── main.py                           # Arranque ligero de FastAPI, montaje /static y routers
├── docs/                                 # Documentación EN/FR/ES, especificaciones OpenAPI 3.1 y AsyncAPI 2.6
└── tests/                                # Suites de pruebas unitarias, integración, caracterización y fitness DDD
```

---

## 2. Invariantes Arquitectónicos y de Scrapers

1. **Aislamiento Estricto de Capas Hexagonales**:
   - `app/domain/` nunca debe importar `app.application`, `app.infrastructure`, `app.models` ni FastAPI/HTTPX/Pillow/OpenCV.
   - `app/application/` nunca debe importar adaptadores concretos de `app.infrastructure.*` ni `app.models`; declarar los contratos en `app/domain/ports/` y conectarlos en `app/container.py`.
2. **Presupuestos Anti-God-Class y Anti-God-Module**: Cada módulo Python se mantiene `<= 800` líneas, cada clase `<= 750` líneas y cada módulo ES frontend `<= 600` líneas (`tests/test_architecture_ddd_fitness.py`), con cero etiquetas `<style>` o `<script>` incrustadas en Python.
3. **Cero Categorías Fijas en el DMD**: Normalizar siempre sistemas y géneros mediante `resolve_canonical_system()` y `resolve_canonical_genre()` en `app/domain/taxonomy/`.
4. **Filtrado Estricto de Videojuegos (`wd:Q7889`) y Exclusión de AV1**: Cada juego de consola debe corresponder a un recurso verificado en `libretro-thumbnails/{repo}`, y `youtube_resolver.py` excluye `av01` (`[vcodec!*=av01]`) priorizando H.264.

---

## 3. Ejecución de Pruebas y Validación de Arquitectura

```bash
# Ejecutar todas las pruebas unitarias, de integración, caracterización y fitness DDD
pytest -c pyproject.toml tests/

# Ejecutar únicamente las pruebas de arquitectura Hexagonal/DDD y agregados genéricos
pytest -c pyproject.toml tests/test_architecture_ddd_fitness.py
```
