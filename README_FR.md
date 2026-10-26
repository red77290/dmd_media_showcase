# DMD Media Showcase Server

🇬🇧 [English](README.md) | 🇫🇷 Français | 🇪🇸 [Español](README_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Communauté Telegram :** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Soutenir le projet sur Ko-fi :** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** est un microservice auto-hébergé de découverte média sans répétition, de transcodage et de streaming temps réel MQTT/UDP conçu pour les matrices LED HUB75 **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** et **ArcadeMatrix_RPi**.

---

## 🎬 Aperçu Réel sur Matrice LED HUB75 & Interface Web

### 🕹️ Séquences Réelles sur Matrice LED HUB75 128×32 (Générées en Direct via l'API Showcase)

Chaque œuvre assemble automatiquement une diffusion RGB565 multi-chapitres adaptée à la géométrie exacte de votre panneau (`128×32` DMD classique `4:1` ci-dessous) :
**`1. Fiche Info Animée`** (jaquette, badge système, défilement auto année/note & synopsis fluide) $\to$ **`2. Panoramas Verticaux Jaquette & Marquee`** $\to$ **`3. Balayage Capture In-Game`** $\to$ **`4. Vidéo Gameplay / Trailer Cadrée par IA`**.

| **Garou: Mark of the Wolves (`Neo Geo` • `128×32`)** | **Super Street Fighter II X / Turbo (`CPS2` • `128×32`)** | **Metal Slug X (`Neo Geo` • `128×32`)** |
| :---: | :---: | :---: |
| ![Garou: Mark of the Wolves Démo DMD HUB75 128x32](demo/dmd_garou_motw_neogeo.gif) | ![Super Street Fighter II X CPS2 Démo DMD HUB75 128x32](demo/dmd_ssf2x_cps2.gif) | ![Metal Slug X Neo Geo Démo DMD HUB75 128x32](demo/dmd_metal_slug_x_neogeo.gif) |
| `Classic Split` • `Tinted` • `auto_action` + `pixel_art` | `Badge Header` • `Neon Border` • `auto_action` + `pixel_art` | `Classic Split` • `Poster Wash` • `auto_action` + `pixel_art` |

### 🖥️ Centre de Contrôle Web Multilingue (`FR` / `EN` / `ES`)

| **1. Vue d'Ensemble Live & Matrices HUB75 Connectées** | **2. Studio Card & Simulateur LED HUB75 Temps Réel** |
| :---: | :---: |
| [![Vue d'ensemble et matrices DMD connectées](demo/ui_overview_live.png)](demo/ui_overview_live.png) | [![Studio Card Mode 2B et Simulateur HUB75](demo/ui_card_studio.png)](demo/ui_card_studio.png) |
| Télémétrie MQTT (`:1883`) + UDP en direct, slots pré-calculés `Ready (N)` & `Standby (N+1)` sur les 4 domaines, historique Consume-Once et sources `[Primaire, Fallback]`. | Profils dédiés par domaine (`Games`, `Movies`, `Series`, `Anime`), 5 mises en page pixel-art (YOKO & TATE), simulateur LED SMD temps réel et injection `0 ms` en file active. |
| **3. Recherche Multi-Catalogues & File Prioritaire On-Demand** | **4. Profils DMD Passifs & Sélecteur Visuel de Tags** |
| [![Recherche Catalogue et File On-Demand](demo/ui_ondemand_search.png)](demo/ui_ondemand_search.png) | [![Rotation DMD Passive et Tags Multi-Sélection](demo/ui_passive_rotation.png)](demo/ui_passive_rotation.png) |
| Recherche localisée (Jeux, Films, Séries, Anime) dans la langue souhaitée et envoi immédiat via `Queue Next` (`FIFO`) ou préemption directe `Play Now`. | Configuration visuelle de rotations Mode 2A/2B par puces multi-sélection (Systèmes, Genres & Décennies) avec raccourci direct vers chaque profil Studio. |

---

## ✨ Fonctionnalités Clés

- **4 Domaines Showcase (`games`, `movies`, `series`, `anime`)** :
  - **Retro Games** : 30 systèmes Arcade, Consoles, Portables et Micro-ordinateurs (`ScreenScraper` Primaire $\to$ `ArcadeItalia` MAME HTTPS + `Libretro+Wikidata` SPARQL Console en Fallback sans clé).
  - **Movies** : Cinéma Classique & Blockbusters (`TMDB` Primaire $\to$ `Apple iTunes` Fallback).
  - **TV Series** : Séries TV Cultes & Modernes (`TMDB` Primaire $\to$ `TVMaze` Fallback).
  - **Anime / Manga** : Openings (`OP`), Endings (`ED`) sans crédits et Trailers (`Jikan + AnimeThemes` Primaire $\to$ `AniList` Fallback).
- **Référentiels Dynamiques Consolidés** : Expose des référentiels de catégories localisés (`fr`, `en`, `es`, `de`, `it`) et indépendants des sources (`GET /api/v1/{domain}/filters?lang=fr` & MQTT `dmd_showcase/catalog/{domain}/referential`) avec filtrage relationnel `Système -> Genres & Décennies supportés` afin que le WebUI du DMD n'ait jamais de listes figées en dur.
- **Plan de Contrôle MQTT Temps Réel (`:1883`) + Streaming UDP `DMDF` via `ffmpeg -re`** :
  - Broker MQTT 3.1.1 embarqué zéro-configuration (`retain=True` + `Last Will and Testament`).
  - Streaming UDP `DMDF` cadencé matériellement par `ffmpeg -re` directement vers l'IP LAN réelle du DMD (`client_ip` / `udp_ip`, contournant le NAT Docker Bridge).
- **Protocole Consume-Once Strict & Pipelining `N+1` en Tâche de Fond** :
  - Mémorise chaque œuvre présentée dans `./data/seen_history.json` (zéro répétition jusqu'à épuisement du catalogue).
  - Prépare l'œuvre suivante (`N+1`) en tâche de fond dès que l'œuvre `N` commence l'affichage de sa fiche (`start_card`), et purge les fichiers de `N` dès `stream_eof`.
- **Profils ESP32 Passifs Gérés par le Serveur (`dmd_profiles.json`) & File d'Attente à la Demande** :
  - Supporte à la fois les DMD multi-moteurs complets (`ArcadeMatrix`) **et** les récepteurs passifs UDP `:4210` ultra-légers (~180 lignes C++, zéro UI, zéro PSRAM sur `ESP32_STD`, `supports_local_card: false` où même la Fiche Info de Phase 1 est rendue dans le flux RGB565) configurés centralement dans `./data/dmd_profiles.json`.
  - Recherche catalogue localisée (`GET /api/v1/{domain}/search`), file prioritaire par DMD (`enqueue_next` / `play_now`), contrôles temps réel `Skip` / `Pause` / `Resume` / `Stop` et intégration **`FrontendSync`** (`mark_seen: false` pour Batocera / RetroBat / LaunchBox / Playnite).
- **Propulsé par `dmd_gif_converter`** : Cadrage vidéo orthogonal (`auto_action` IA Let Me Handle It, `panorama` balayage haut-bas, `center_crop`) et colorimétrie LED HUB75 (`auto` Smart Color Boost IA, `pixel_art`, `anime`, `cinema`), redimensionnement Lanczos + `unsharp`, et panoramas verticaux fluides (`Jaquette`, `Marquee`, `Captures`) sur toutes les géométries Paysage (`128x32`, `128x64`, `256x64`), Carré (`64x64`) et TATE (`64x128`, `32x128`).
- **Notification de Mise à Jour Sensible à l'OS** : Vérification légère intégrée (`GET /api/v1/system/update-check`) affichant une bannière discrète dans la Web UI dès qu'une nouvelle release GitHub officielle est disponible pour votre environnement actif (`macOS .dmg`, `Windows .zip`, `Linux .tar.gz` ou `Docker`) avec lien direct vers la release et l'installeur correspondant.

---

## 🚀 Installation Rapide & Exemple Court

### Option 1 : Image Docker Officielle Multi-Arch (`amd64` / `arm64` — Raspberry Pi / NAS / Home-Lab)

#### A. Docker Compose (Recommandé — sans `git clone` ni compilation locale)

Téléchargez le fichier [`docker-compose.yml`](docker-compose.yml) prêt à l'emploi et démarrez le serveur :

```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
```

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

> **Persistance des Données (`/app/data`)** : Stocke `server_config.json`, `dmd_profiles.json` (profils DMD pilotés par le serveur et langue de diffusion), `seen_history.json` et `referentials/`. Le montage `${DMD_DATA_PATH:-./data}:/app/data` cible un dossier hôte (personnalisable via `DMD_DATA_PATH` dans `.env`) **insensible à `docker compose down -v`**, tandis que `-v dmd_showcase_data:/app/data` utilise un volume nommé géré par Docker pour Portainer.

- **Dashboard Web d'Administration** : `http://dmdmediashowcase.local:8890/` (ou `http://<ip-serveur>:8890/` — configuration optionnelle des clés TMDB / ScreenScraper et remise à zéro de `seen_history.json`)
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
# 1. Vérifier l'état du serveur, du pool de workers et du broker MQTT
curl -s http://localhost:8890/api/v1/health | jq .

# 2. Récupérer le référentiel consolidé en français pour Retro Games (Systèmes -> Genres & Décennies)
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
| **Spécification MQTT & UDP AsyncAPI 2.6** | [docs/asyncapi.yaml](docs/asyncapi.yaml) | — | — |
