# DMD Media Showcase Server (V3.0)

🇬🇧 [English](README.md) | 🇫🇷 Français | 🇪🇸 [Español](README_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

💬 **Communauté Telegram :** [https://t.me/+DrSdAO8UMUViNmE0](https://t.me/+DrSdAO8UMUViNmE0)  
☕ **Soutenir le projet sur Ko-fi :** [https://ko-fi.com/red1l](https://ko-fi.com/red1l)

**DMD Media Showcase Server** est un microservice auto-hébergé de découverte média sans répétition, de transcodage et de streaming temps réel MQTT/UDP conçu pour les matrices LED HUB75 **[ArcadeMatrix (ESP32 / ESP32-S3)](https://github.com/red77290/ArcadeMatrix)** et **ArcadeMatrix_RPi**.

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
- **Propulsé par `dmd_gif_converter`** : Cadrage vidéo orthogonal (`auto_action` IA Let Me Handle It, `panorama` balayage haut-bas, `center_crop`) et colorimétrie LED HUB75 (`auto` Smart Color Boost IA, `pixel_art`, `anime`, `cinema`), redimensionnement Lanczos + `unsharp`, et panoramas verticaux fluides (`Jaquette`, `Marquee`, `Captures`) sur toutes les géométries Paysage (`128x32`, `128x64`, `256x64`), Carré (`64x64`) et TATE (`64x128`, `32x128`).

---

## 🚀 Installation Rapide & Exemple Court

### Option 1 : Docker Compose (Recommandé pour Raspberry Pi / NAS / Home-Lab)

```bash
git clone https://github.com/red77290/dmd_media_showcase.git
cd dmd_media_showcase
docker compose up -d --build
```

- **Dashboard Web d'Administration** : `http://dmdmediashowcase.local:8890/` (ou `http://<ip-serveur>:8890/` — configuration optionnelle des clés TMDB / ScreenScraper et remise à zéro de `seen_history.json`)
- **Swagger UI Interactif** : `http://dmdmediashowcase.local:8890/docs`
- **Broker MQTT Embarqué** : `mqtt://dmdmediashowcase.local:1883`
- **Auto-Découverte Réseau Local (mDNS)** : Annonce automatiquement `dmdmediashowcase.local` (`224.0.0.251:5353`) afin que votre DMD (`ArcadeMatrix` ESP32 ou `ArcadeMatrix_RPi`) le détecte sans saisir d'adresse IP. Il suffit d'activer le toggle Showcase dans la Web UI du DMD (`Settings -> Showcase Server`), de vérifier le témoin **`🟢 Communication OK (v3.0.0)`**, puis d'ajouter les moteurs Showcase à votre rotation !

### Option 2 : Exécutable Portable Autonome (Windows / macOS)

Téléchargez l'archive portable (`DMD_Media_Showcase_Windows_x64.zip` ou `DMD_Media_Showcase_macOS.zip`) depuis **[GitHub Releases](https://github.com/red77290/dmd_media_showcase/releases/latest)**, décompressez-la et lancez `dmd-media-showcase` (aucune installation Python ou FFmpeg requise sur la machine hôte).

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
| **Architecture DDD & Pipeline de Streaming** | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/ARCHITECTURE_FR.md](docs/ARCHITECTURE_FR.md) | [docs/ARCHITECTURE_ES.md](docs/ARCHITECTURE_ES.md) |
| **Guide Développeur & Stratégies Scrapers** | [docs/DEVELOPER.md](docs/DEVELOPER.md) | [docs/DEVELOPER_FR.md](docs/DEVELOPER_FR.md) | [docs/DEVELOPER_ES.md](docs/DEVELOPER_ES.md) |
| **Spécification REST OpenAPI 3.1** | [docs/openapi.yaml](docs/openapi.yaml) / [docs/openapi.json](docs/openapi.json) | — | — |
| **Spécification MQTT & UDP AsyncAPI 2.6** | [docs/asyncapi.yaml](docs/asyncapi.yaml) | — | — |
