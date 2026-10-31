# DMD Media Showcase Server

🇬🇧 [English](README.md) | 🇫🇷 Français | 🇪🇸 [Español](README_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Communauté Telegram :** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Soutenir le projet sur Ko-fi :** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** est un microservice auto-hébergé de découverte média sans répétition, de transcodage et de streaming temps réel **MQTT + WebSocket `DMDZ` Acquitté par Crédits / HTTP `DMDS`** (ainsi qu'UDP passif `DMDF`) conçu pour les matrices LED HUB75 **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** et **ArcadeMatrix_RPi**.

---

## 🎬 Aperçu Réel sur Matrice LED HUB75 & Interface Web

### 🕹️ Séquences Réelles sur Matrice LED HUB75 128×32 (Générées en Direct via l'API Showcase)

Chaque œuvre assemble automatiquement une diffusion RGB565 multi-chapitres adaptée à la géométrie exacte de votre panneau (`128×32` DMD classique `4:1` ci-dessous) :
**`1. Fiche Info Animée`** (jaquette, badge système, défilement auto année/note & synopsis fluide) $\to$ **`2. Panoramas Zoom-to-Cover Jaquette & Marquee`** $\to$ **`3. Balayage Capture In-Game`** $\to$ **`4. Vidéo Gameplay / Trailer Cadrée par IA`**.

| **Garou: Mark of the Wolves (`Neo Geo` • `128×32`)** | **Super Street Fighter II X / Turbo (`CPS2` • `128×32`)** | **Metal Slug X (`Neo Geo` • `128×32`)** |
| :---: | :---: | :---: |
| ![Garou: Mark of the Wolves Démo DMD HUB75 128x32](demo/dmd_garou_motw_neogeo.gif) | ![Super Street Fighter II X CPS2 Démo DMD HUB75 128x32](demo/dmd_ssf2x_cps2.gif) | ![Metal Slug X Neo Geo Démo DMD HUB75 128x32](demo/dmd_metal_slug_x_neogeo.gif) |
| `Classic Split` • `Tinted` • `auto_action` + `pixel_art` | `Badge Header` • `Neon Border` • `auto_action` + `pixel_art` | `Classic Split` • `Poster Wash` • `auto_action` + `pixel_art` |

### 🖥️ Centre de Contrôle Web Multilingue (`FR` / `EN` / `ES`)

| **1. Vue d'Ensemble Live & Matrices HUB75 Connectées** | **2. Studio Card & Simulateur LED HUB75 Temps Réel** |
| :---: | :---: |
| [![Vue d'ensemble et matrices DMD connectées](demo/ui_overview_live.png)](demo/ui_overview_live.png) | [![Studio Card Mode 2B et Simulateur HUB75](demo/ui_card_studio.png)](demo/ui_card_studio.png) |
| Télémétrie MQTT (`:1883`) + transports en direct (`MQTT+TCP` par défaut, `HTTP`, `UDP-PASSIVE`, `MQTT+UDP` optionnel), slots pré-calculés `Ready (N)` & `Standby (N+1)` avec badges de source live (`🔗 igdb+screenscraper`), historique Consume-Once et chaînes scrapers multi-sources configurables à chaud. | Profils dédiés par domaine (`Games`, `Movies`, `Series`, `Anime`), 5 mises en page pixel-art (YOKO & TATE), simulateur LED SMD temps réel et injection `0 ms` en file active. |
| **3. Recherche Multi-Catalogues & File Prioritaire On-Demand** | **4. Profils DMD Passifs & Sélecteur Visuel de Tags** |
| [![Recherche Catalogue et File On-Demand](demo/ui_ondemand_search.png)](demo/ui_ondemand_search.png) | [![Rotation DMD Passive et Tags Multi-Sélection](demo/ui_passive_rotation.png)](demo/ui_passive_rotation.png) |
| Recherche localisée (Jeux dont raccourcis joueurs `sf2x`, `3s`, `kof98`, Films, Séries, Anime) dans la langue souhaitée et envoi immédiat via `Queue Next` (`FIFO`) ou préemption directe `Play Now`. | Configuration visuelle de rotations Mode 2A/2B par puces multi-sélection (Systèmes, Genres & Décennies) avec raccourci direct vers chaque profil Studio. |

---

## ✨ Fonctionnalités Clés

- **4 Domaines Showcase & Chaînes Multi-Sources Configurables (`games`, `movies`, `series`, `anime`)** :
  - **Retro & Arcade Games (`55+` / `66` Systèmes Canoniques)** : Systèmes Arcade (`MAME`, `CPS-1/2/3`, `Neo-Geo`, `NAOMI`, `Atomiswave`, `Model 2/3`), Consoles, Portables et Micro-ordinateurs propulsés par **4 sources indépendantes sélectionnables** :
    1. **`igdb`** (`IGDB API v4` via OAuth2 Twitch gratuit — 200+ plateformes, recherche instantanée par acronyme/alias via `alternative_names`, tirage aléatoire par offset, jaquettes/artworks & trailers).
    2. **`screenscraper`** (`ScreenScraper` — wheels/marquees détourés, captures pixel-art, extraits vidéo `.mp4`, avec détection automatique de quota/surcharge HTTP-200 en texte brut et disjoncteur de cooldown).
    3. **`arcadeitalia`** (scraper HTTPS sans clé de référence MAME/CPS/Neo-Geo/NAOMI avec table d'alias intégrée résolvant `sf2x`, `3s`, `kof98`, `garou` en une seule requête).
    4. **`libretro_wikidata`** (source console/portable/micro de premier plan sans clé combinant Wikidata SPARQL, `libretro-thumbnails` vérifiés, synopsis Wikipédia localisés et vidéos Archive.org).
  - **Movies** : Cinéma Classique & Blockbusters (`tmdb_movies` $\to$ `itunes_movies` sans clé).
  - **TV Series** : Séries TV Cultes & Modernes (`tmdb_series` $\to$ `tvdb_series` $\to$ `tvmaze` sans clé).
  - **Anime / Manga** : Openings (`OP`), Endings (`ED`) sans crédits et Trailers (`jikan` + `AnimeThemes` $\to$ `anilist` sans clé).
- **Priorité Multi-Sources à Chaud & Agrégation Inter-Sources (`source_policies`)** :
  - Réordonnez la priorité (`#1 Primaire -> #2 Secondaire -> #3 Tertiaire -> ...`) et activez/désactivez chaque source par domaine en direct depuis le Dashboard Web (**Vue d'Ensemble** et **Clés API & Scrapers**), persisté dans `./data/server_config.json`. Dès qu'une source renvoie `0` résultat, expire, entre en cooldown ou ne couvre pas le système demandé, l'exécution bascule de manière déterministe vers la source active suivante.
  - Choisissez par domaine entre **`enrich_missing` (Agrégation Multi-Sources, par défaut)** — où la source Primaire résout l'identité canonique et les alias (ex. `IGDB` résolvant `"sf2x"` sur `cps2` $\to$ `"Super Street Fighter II Turbo"` / `ssf2t`) tandis que `CandidateAggregator` exécute une passe secondaire bornée (`<= 3.0s`) pour récupérer les assets DMD manquants (marquees/wheels détourés, snaps pixel-art, flux directs `.mp4`/`.webm`/`.m4v`, synopsis localisés) avec attribution combinée transparente (`source_used="igdb+screenscraper"`, `"igdb+arcadeitalia"`, `"tmdb_movies+itunes_movies"`) — et **`fallback_only` (Fallback Strict)**.
- **Référentiels Dynamiques Consolidés** : Expose des référentiels de catégories localisés (`fr`, `en`, `es`, `de`, `it`) et indépendants des sources (`GET /api/v1/{domain}/filters?lang=fr` & MQTT `dmd_showcase/catalog/{domain}/referential`) avec filtrage relationnel `Système -> Genres & Décennies supportés` afin que le WebUI du DMD n'ait jamais de listes figées en dur.
- **Streaming Principal `MQTT + TCP` (WebSocket `DMDZ` Acquitté par Crédits & HTTP `DMDS` `:8890`) & Support UDP Passif `DMDF` (`:4210`)** :
  - **Plan de Contrôle MQTT (`:1883`) + Plan de Données `:8890` Adaptatif à 3 Niveaux (`MQTT+TCP` par défaut, `profile=sram | psram | psram_hd`)** : Les Modèles 1 (`ArcadeMatrix` ESP32 / ESP32-S3 / `ArcadeMatrix_RPi`) et 2A (`supports_local_card: true`) associent le broker MQTT 3.1.1 embarqué zéro-configuration (`config`, `ready`, `start_card` avec `retain=True` + `LWT` et bascule automatique HTTP `/config + /next` dans le même tour) à un flux `:8890` à 3 profils — **`profile=sram`** (`esp32dev` 0 PSRAM : WebSocket `DMDZ` v2 acquitté `WS /api/v1/{domain}/ws/{item_id}.dmdz?v=2&credits=4&chunk=1360&profile=sram` servant `.dmdz2.sram.stream` hybride True-RGB565 + `FRAME_PAL8` `0x05` plafonné `<= 1 400 o/trame`, en-tête `16 o` + Trame `0` fusionnés et cadencement inter-chunks `2 ms` à `20–24 FPS`), **`profile=psram` / `psram_hd`** (`ESP32-S3` : pré-chargement PSRAM HTTP non cadencé `.dmdz2.stream` `?purge=false&paced=0&codec=dmdz2&profile=psram|psram_hd` jusqu'à `1.5–3.5 Mo` pendant la Fiche Info pour `0` trafic Wi-Fi durant la lecture à `24–30 FPS`, plus repli WS `credits=12..16&chunk=1400`), et pré-chargement RAM HTTP `DMDS` brut non cadencé (`GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge=true`) sur `ArcadeMatrix_RPi`.
  - **Pourquoi `DMDZ` v2 Adaptatif + `MQTT + TCP` est Recommandé par Défaut** : Assure du True-RGB565 `24–30 FPS` sur PSRAM/RPi tout en plafonnant strictement chaque trame `esp32dev` (`0 Ko` PSRAM) à `<= 1 400 o` via `FRAME_PAL8` (`0x05`) afin qu'un anneau SPSC sans verrou de `8–10 Ko` absorbe `300–600 ms` de gigue Wi-Fi en préservant `>= 20 Ko` de DRAM libre pour `esp_wifi`/`lwIP` et le HUB75 8-bit CIE-1931 complet ; élimine les pertes NAT UDP sous Docker Desktop / Rancher Desktop et le buffering proxy HTTP (`101 Switching Protocols`) ; et utilise des `item_id` compacts sans collision (`pkg_<domain>_<12hex>`, `<= 24` caractères). Télémétrie en direct du transport (`MQTT+TCP`, `HTTP`, `UDP-PASSIVE`, `MQTT+UDP` legacy optionnel) et de la source par slot (`playing_source`, `ready_source`, `standby_source`, `preparing_source`).
- **Protocole Consume-Once Strict & Pipelining `N+1` en Tâche de Fond** :
  - Mémorise chaque œuvre présentée dans `./data/seen_history.json` (zéro répétition jusqu'à épuisement du catalogue).
  - Prépare l'œuvre suivante (`N+1`) en tâche de fond dès que l'œuvre `N` commence l'affichage de sa fiche (`start_card`), et purge les fichiers de `N` dès la fin du flux WebSocket `DMDZ` ou HTTP `DMDS` (`?purge=true`) ou `stream_eof`.
- **Profils ESP32 Passifs Gérés par le Serveur (`dmd_profiles.json`) & File d'Attente à la Demande** :
  - Supporte les DMD multi-moteurs complets (`ArcadeMatrix`, Modèle 1) et les récepteurs hybrides gérés par le serveur (Modèle 2A, `MQTT+TCP`) **ainsi que** les récepteurs passifs UDP `:4210` `DMDF` ultra-légers optionnels (**Modèle 2B**, ~180 lignes C++, zéro UI, zéro PSRAM sur `ESP32_STD`, `supports_local_card: false`, `UDP-PASSIVE` où même la Fiche Info de Phase 1 est rendue dans le flux RGB565) configurés centralement dans `./data/dmd_profiles.json`.
  - Recherche catalogue localisée (`GET /api/v1/{domain}/search`), file prioritaire asynchrone par DMD (`POST /api/v1/clients/{dmd_id}/queue/async`, `enqueue_next` / `play_now`), contrôles temps réel `Skip` / `Pause` / `Resume` / `Stop` et intégration **`FrontendSync`** (`mark_seen: false` pour Recalbox / Batocera / RetroPie / RetroBat / LaunchBox / Playnite).
- **Cache Persistant des Conversions On-Demand & Purge en 1 Clic** :
  - Cache disque persistant optionnel (`Dashboard Admin -> 2. Runtime, Workers & Limites Vidéo -> Cache Persistant des Conversions On-Demand`) indexé par **`(Système + Jeu + Résolution du Panneau + Style Carte/Vidéo)`** avec quota disque configurable (Mo) pour une relance instantanée (`0 ms`, purge en 1 clic via `POST /api/v1/cache/on-demand/evict`).
- **Propulsé par `dmd_gif_converter` (`>= 8.0.10`)** : Cadrage vidéo orthogonal (`auto_action` IA Let Me Handle It, `panorama`, `center_crop`), colorimétrie LED HUB75 (`auto` Smart Color Boost IA, `pixel_art`, `anime`, `cinema`), redimensionnement Lanczos + `unsharp`, et **panoramas zoom-to-cover uniformes déterministes** sans bandes noires — **YOKO** (`128x32`, `128x64`, `256x64`) défilant de **haut en bas**, et **TATE** (`64x128`, `32x128`) défilant de **gauche à droite**.
- **Coffre-Fort d'Identifiants à 3 Niveaux & Caviardage Automatique des Logs HTTP** : Résolution `1. Surcharges WebUI server_config.json` $\to$ `2. Variables .env / docker-compose` $\to$ `3. Coffre natif C compilé en CI/CD (_prod_vault)`, protégée par `SecretRedactingLogFilter` (`httpx`/`httpcore` bridés à `WARNING`) garantissant qu'aucun secret API ou mot de passe URL ne fuite dans les logs conteneur.
- **Notification de Mise à Jour Sensible à l'OS** : Vérification légère intégrée (`GET /api/v1/system/update-check`) affichant une bannière discrète dans la Web UI dès qu'une nouvelle release GitHub officielle est disponible pour votre environnement actif (`macOS .dmg`, `Windows .zip`, `Linux .tar.gz` ou `Docker`).

---

## 🚀 Installation Rapide & Exemple Court

### Option 1 : Image Docker Officielle Multi-Arch (`amd64` / `arm64` — Raspberry Pi / NAS / Home-Lab)

#### A. Docker Compose (Recommandé — sans `git clone` ni compilation locale)

Téléchargez le fichier [`docker-compose.yml`](docker-compose.yml) prêt à l'emploi et démarrez le serveur :

```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
```

> **Build Local depuis les Sources (`git clone`) :** Dans un dépôt cloné localement, [`docker-compose.override.yml`](docker-compose.override.yml) est chargé automatiquement afin que `docker compose up -d --build` compile vos sources locales et la dernière version de `dmd_gif_converter` au lieu de tirer l'image distante.

<details>
<summary>📄 Afficher le <code>docker-compose.yml</code> autonome</summary>

```yaml
services:
  dmd-media-showcase:
    image: ghcr.io/red77290/dmd_media_showcase:latest
    pull_policy: always
    container_name: dmd-media-showcase
    restart: unless-stopped
    ports:
      - "8890:8890"
      - "1883:1883"
      - "5353:5353/udp"
    volumes:
      - ${DMD_DATA_PATH:-./data}:/app/data
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```
</details>

#### B. Commande `docker run` Directe (Bind Mount Hôte ou Volume Nommé Portainer)

```bash
# Option B1 : Montage répertoire hôte (./data — protégé contre `docker compose down -v`)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest

# Option B2 : Volume nommé Docker (`dmd_showcase_data` — idéal pour Portainer / déploiement autonome)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v dmd_showcase_data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest
```

> **Persistance des Données (`/app/data`)** : Stocke `server_config.json` (identifiants scrapers, réglages runtime et politiques multi-sources `source_policies`), `dmd_profiles.json` (profils DMD pilotés par le serveur et langue de diffusion), `seen_history.json` et `referentials/`. Le montage `${DMD_DATA_PATH:-./data}:/app/data` cible un dossier hôte (personnalisable via `DMD_DATA_PATH` dans `.env`) **insensible à `docker compose down -v`**, tandis que `-v dmd_showcase_data:/app/data` utilise un volume nommé géré par Docker pour Portainer.

- **Dashboard Web d'Administration** : `http://dmdmediashowcase.local:8890/` (ou `http://<ip-serveur>:8890/` — ordonnancement des chaînes multi-sources, clés optionnelles IGDB / ScreenScraper / TMDB / TheTVDB et remise à zéro de `seen_history.json`)
- **Swagger UI Interactif** : `http://dmdmediashowcase.local:8890/docs`
- **Broker MQTT Embarqué** : `mqtt://dmdmediashowcase.local:1883`
- **Auto-Découverte Réseau Local (mDNS)** : Annonce automatiquement `dmdmediashowcase.local` (`224.0.0.251:5353`) afin que votre DMD (`ArcadeMatrix` ESP32 ou `ArcadeMatrix_RPi`) le détecte sans saisir d'adresse IP. Il suffit d'activer le toggle Showcase dans la Web UI du DMD (`Settings -> Showcase Server`), de vérifier le témoin **`🟢 Communication OK`**, puis d'ajouter les moteurs Showcase à votre rotation !

### Option 2 : Installeurs Natifs & Archives Portables (macOS `.dmg` / Windows `.zip` / Linux `.tar.gz`)

Téléchargez le paquet correspondant à votre OS depuis **[GitHub Releases](https://github.com/red77290/dmd_media_showcase/releases/latest)** (aucune installation Python ou FFmpeg requise sur la machine hôte) :
- **macOS (Apple Silicon `arm64`)** : Ouvrez **`DMD_Media_Showcase_macOS_arm64.dmg`**, glissez **`DMD Media Showcase.app`** dans **`Applications`**, puis lancez-la (l'icône `▣ DMD` apparaît dans la barre des menus macOS et ouvre automatiquement `http://localhost:8890/`). *(1er lancement d'une app open-source non notarisée Apple : Clic droit → **Ouvrir** ou `Réglages Système → Confidentialité et sécurité → Ouvrir quand même`, ou `xattr -cr "/Applications/DMD Media Showcase.app"` une seule fois).*
- **Windows (`x64`)** : Décompressez **`DMD_Media_Showcase_Windows_x64.zip`** et double-cliquez sur `Start_Showcase_Windows.bat`.
- **Linux (`x64`)** : Décompressez **`DMD_Media_Showcase_Linux_x64.tar.gz`** et lancez `./start_showcase.sh`.

### Exemple Rapide de Vérification

```bash
# 1. Vérifier l'état du serveur, les chaînes multi-sources actives, les workers et le port MQTT
curl -s http://localhost:8890/api/v1/health | jq .

# 2. Récupérer le référentiel consolidé en français pour Retro Games (55+ Systèmes -> Genres & Décennies)
curl -s "http://localhost:8890/api/v1/games/filters?lang=fr" | jq '.systems[0], .genres[:3]'
```

---

## 📚 Documentation & Spécifications API

| Document | 🇬🇧 English | 🇫🇷 Français | 🇪🇸 Español |
| :--- | :--- | :--- | :--- |
| **Guide de Démarrage Rapide (QuickStart)** | [docs/QUICKSTART.md](docs/QUICKSTART.md) | [docs/QUICKSTART_FR.md](docs/QUICKSTART_FR.md) | [docs/QUICKSTART_ES.md](docs/QUICKSTART_ES.md) |
| **Guide d'Intégration ESP32 & Récepteur Passif** | [docs/ESP32_INTEGRATION.md](docs/ESP32_INTEGRATION.md) | [docs/ESP32_INTEGRATION_FR.md](docs/ESP32_INTEGRATION_FR.md) | [docs/ESP32_INTEGRATION_ES.md](docs/ESP32_INTEGRATION_ES.md) |
| **Architecture Système & Pipeline de Streaming** | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/ARCHITECTURE_FR.md](docs/ARCHITECTURE_FR.md) | [docs/ARCHITECTURE_ES.md](docs/ARCHITECTURE_ES.md) |
| **Guide Développeur & Stratégies Scrapers** | [docs/DEVELOPER.md](docs/DEVELOPER.md) | [docs/DEVELOPER_FR.md](docs/DEVELOPER_FR.md) | [docs/DEVELOPER_ES.md](docs/DEVELOPER_ES.md) |
| **Spécification REST OpenAPI 3.1** | [docs/openapi.yaml](docs/openapi.yaml) / [docs/openapi.json](docs/openapi.json) | — | — |
| **Spécification MQTT, WebSocket DMDZ & UDP AsyncAPI 2.6** | [docs/asyncapi.yaml](docs/asyncapi.yaml) | — | — |
