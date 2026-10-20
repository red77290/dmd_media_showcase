# 🎬🎮 Serveur DMD Media Showcase (`dmd-media-showcase`)

> **[🇬🇧 Read the documentation in English (README.md)](README.md)**

**`dmd-media-showcase`** est un serveur Docker open-source, auto-hébergé et **100 % sans état (stateless)** qui transforme les bandes-annonces vidéo, openings et artworks promotionnels de **Jeux Rétro**, **Films**, **Séries TV** et **Animés** en animations LED ultra-contrastées et sans latence pour **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** et **[ArcadeMatrix_RPi (Raspberry Pi)](https://github.com/red77290/ArcadeMatrix_RPi)**.

Propulsé par la librairie **[`dmd-gif-converter` v8.0](https://github.com/red77290/dmd_gif_converter)**, il exécute la détection IA des meilleures scènes d'action (`MomentDetector`), le recadrage dynamique (`AutoActionCrop`) et l'optimisation colorimétrique LED (`SmartColorBoost`) au sein d'un pool de workers CPU strictement borné.

---

## ✨ Points Forts Architecturaux

1. **4 Domaines Dédiés & Multilingues (`FR`, `EN`, `ES`, `DE`, `IT`) :**
   - 🕹️ **Jeux Rétro & Arcade (`/api/v1/games`) :** ScreenScraper.fr (FR/EN/ES/DE/IT) + API publique Steam Store + Catalogue Classiques Arcade intégré.
   - 🎬 **Cinéma & Blockbusters (`/api/v1/movies`) :** TMDB API v3 (`fr-FR`, `en-US`, `es-ES`, etc.) + Catalogue Cinéma Culte intégré.
   - 📺 **Séries TV (`/api/v1/series`) :** TMDB TV + TheTVDB v4 + API publique TVMaze + Catalogue Séries Cultes intégré.
   - ⛩️ **Animés & Manga (`/api/v1/anime`) :** AnimeThemes.moe (Openings/Endings) + Jikan (MyAnimeList v4) + Synopsis localisés TMDB + Catalogue Animés intégré.
2. **100 % Fonctionnel sans Configuration (Mode Zero-Config) :**
   - Même **sans aucune clé API renseignée**, les 4 domaines fonctionnent immédiatement grâce aux API gratuites sans clé (Steam, TVMaze, AnimeThemes, Jikan) et aux catalogues multilingues embarqués.
3. **Protocole LAN en 2 Étapes Encapsulé dans l'Engine (Zéro Webhook côté DMD) :**
   - Les DMD n'exposent aucun endpoint webhook entrant. Toute la synchronisation repose sur 2 appels HTTP sortants depuis l'engine :
     1. **Enregistrement Proactif de Session (`POST /api/v1/{domain}/session`) :** Envoyé au démarrage du DMD ou à la sauvegarde WebUI avec le nombre exact `count = items_per_rotation` (ex: `10` items).
     2. **Vérification Rapide Pré-Rotation (`GET /api/v1/{domain}/batch?session_id=...`) :** Appel LAN `< 2 ms` (*« Es-tu prêt ? »*) juste avant le tour de rotation du moteur.
4. **Pool de Workers CPU Borné & File d'Attente Élastique (`MAX_CONCURRENT_JOBS`) :**
   - Le transcodage vidéo tourne dans des sous-processus isolés (`ProcessPoolExecutor`, `os.nice(10)`, `FFMPEG_THREADS_PER_JOB=1`) tandis que la boucle HTTP FastAPI répond en `< 2 ms`.
   - Si trop de DMD sollicitent une petite VM ou un Raspberry Pi, les jobs s'empilent proprement en FIFO sans jamais faire chauffer ni planter le serveur : les DMD sautent simplement un tour de rotation tant que leur batch est en `"warming"`, signal clair qu'il suffit d'espacer les rotations ou d'augmenter les vCPU de la VM.
5. **Cache Éphémère `tmpfs` & Troncature Dynamique $O(1)$ :**
   - Aucune base de données, aucun stockage disque permanent. Dès que `CurrentBatch` est servi, `NextBatch` (`N` nouveaux items) est préparé avec un coup d'avance et l'ancien batch est purgé après expiration du TTL.
   - Modifier la durée vidéo maximale sur un DMD (`?max_duration_s=5`) tronque le flux `.rgb565.stream` ou `.gif` à la volée en $O(1)$ **sans jamais relancer FFmpeg**.
6. **Prêt pour l'Affichage à la Demande & Assistants Vocaux (Google Home / Home Assistant) :**
   - Endpoint `POST /api/v1/{domain}/on-demand` intégré avec priorité `HIGH_ON_DEMAND` passant devant les lots de rotation classiques.

---

## 🚀 Démarrage Rapide (Docker Compose)

```bash
git clone https://github.com/red77290/dmd_media_showcase.git
cd dmd_media_showcase
cp .env.example .env   # Optionnel : ajoutez votre clé gratuite TMDB_API_KEY ou vos identifiants SCREENSCRAPER
docker compose up -d
```

Vérifiez l'état du serveur :
```bash
curl http://localhost:8080/api/v1/health
```

---

## 📚 Documentation Détaillée

| Guide | Français | English |
| :--- | :--- | :--- |
| **Guide des Scrapers & Clés API Gratuites** | [`docs/SCRAPERS_GUIDE_FR.md`](docs/SCRAPERS_GUIDE_FR.md) | [`docs/SCRAPERS_GUIDE.md`](docs/SCRAPERS_GUIDE.md) |
| **Déploiement, Dimensionnement CPU & Multi-DMD** | [`docs/DEPLOYMENT_AND_SIZING_FR.md`](docs/DEPLOYMENT_AND_SIZING_FR.md) | [`docs/DEPLOYMENT_AND_SIZING.md`](docs/DEPLOYMENT_AND_SIZING.md) |
| **API REST, Protocole de Rotation & Spécification `.rgb565.stream`** | [`docs/API_AND_PROTOCOL_FR.md`](docs/API_AND_PROTOCOL_FR.md) | [`docs/API_AND_PROTOCOL.md`](docs/API_AND_PROTOCOL.md) |

---

## 🧪 Développement Local & Tests

```bash
pip install -e ".[dev]"
pytest
```

## 📄 Licence

Licence MIT — Fait partie de l'écosystème open-source **ArcadeMatrix**.
