# Guide de Démarrage Rapide (QuickStart) — DMD Media Showcase Server (V3.0)

🇬🇧 [English](QUICKSTART.md) | 🇫🇷 Français | 🇪🇸 [Español](QUICKSTART_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Modes de Déploiement

### Mode A : Docker Compose (Linux / Raspberry Pi 4 & 5 / NAS)

```yaml
services:
  dmd-media-showcase:
    build: .
    container_name: dmd-media-showcase
    restart: unless-stopped
    ports:
      - "8890:8890"       # API REST FastAPI + Dashboard Admin + Swagger UI
      - "1883:1883"       # Broker MQTT 3.1.1 Embarqué
      - "5353:5353/udp"   # Auto-Découverte Réseau Local mDNS (dmdmediashowcase.local)
    volumes:
      - ./data:/app/data  # Persistance server_config.json, seen_history.json & referentials/
    environment:
      - LOG_LEVEL=INFO
      - MAX_WORKERS=2
```

Démarrage :
```bash
docker compose up -d --build
docker logs -f dmd-media-showcase
```

> [!TIP]
> **NAT Docker Bridge & Streaming UDP (`client_ip`)** : `ArcadeMatrix_RPi` et `ArcadeMatrix` (ESP32) détectent et déclarent automatiquement leur véritable IP LAN (`client_ip` / `udp_ip`) dans leurs payloads MQTT `config` et `start_stream`. Ainsi, `ffmpeg -re` envoie les paquets UDP `DMDF` directement au DMD même lorsque Docker tourne en mode bridge standard (`172.18.0.1`).

---

### Mode B : Exécutable Portable Hermétique (Windows 10/11 & macOS)

1. Téléchargez `DMD_Media_Showcase_Windows_x64.zip` ou `DMD_Media_Showcase_macOS.zip` depuis GitHub Releases.
2. Extrayez l'archive dans le dossier de votre choix (ex. `C:\DMD_Media_Showcase\`).
3. Lancez `dmd-media-showcase.exe` (Windows) ou `./dmd-media-showcase` (macOS).
4. Toutes les données sont isolées dans `./data/` (`server_config.json`, `seen_history.json`, `referentials/`, `stream_tmp/`). Les variables `PYTHONPATH` et `PYTHONHOME` de l'hôte sont automatiquement purgées (`enforce_hermetic_environment()`).

---

## 2. Dashboard Web d'Administration (`http://dmdmediashowcase.local:8890/`)

Ouvrez `http://dmdmediashowcase.local:8890/` (ou `http://<ip-serveur>:8890/`) dans votre navigateur pour :
- Configurer les identifiants **optionnels** des scrapers primaires :
  - **ScreenScraper** (`user`, `pass`, `dev_id`, `dev_pass`) pour `games`. Sans identifiants, `ArcadeItalia` (MAME HTTPS) + `Libretro+Wikidata` (SPARQL Console + Arbre GitHub) prennent le relais automatiquement sans aucune clé.
  - **Clé API TMDB / Jeton v4** (`tmdb_api_key`) et **Clé API TheTVDB v4** (`tvdb_api_key`) pour `movies`, `series` et `anime`. Sans clé, `Apple iTunes`, `TVMaze`, `Jikan + AnimeThemes` et `Wikipédia Interwiki` fonctionnent automatiquement.
- Choisir entre le **Broker MQTT Embarqué** (`:1883`, par défaut) ou un **Broker MQTT Externe** (Mosquitto / Home Assistant).
- Consulter et réinitialiser l'historique **`seen_history.json`** par domaine ou globalement.
- Forcer une synchronisation immédiate des référentiels scrapers (`POST /api/v1/admin/referentials/sync`).

---

## 3. Connecter votre DMD (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`)

1. Ouvrez le WebUI de votre DMD (`http://arcadematrix.local/` sur ESP32 ou `http://arcadematrix.local:8080/` sur Raspberry Pi).
2. Rendez-vous dans **`Settings -> Showcase Server`** (`#mqtt-subtab-showcase`) :
   - Le champ **Showcase Server URL** est déjà pré-rempli avec `http://dmdmediashowcase.local:8890` et **désactivé par défaut** (`⚪ Désactivé`) afin d'éviter tout accès réseau inutile si `dmd_media_showcase` n'est pas installé.
   - Activez le bouton **Enable DMD Media Showcase Server** puis cliquez sur **Save Showcase Server** (ou **Test Connection**).
   - Vérifiez que le témoin visuel passe au vert : **`🟢 Communication OK (v3.0.0)`**.
3. Dans l'onglet **Screens / Rotation**, ajoutez l'un des 4 moteurs Showcase (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`) et définissez le nombre de **Media** à afficher par tour de rotation.
4. Le WebUI du DMD interroge automatiquement `GET /api/v1/{domain}/filters?lang={ui_lang}` pour peupler dynamiquement les listes **Systèmes**, **Genres** et **Décennies** (en filtrant automatiquement les genres et décennies supportés lorsque vous sélectionnez un système comme `NES` ou `Neo-Geo`).
5. Réglez la **Vitesse de défilement du synopsis** (`1 = Lent` à `10 = Turbo`, jusqu'à `160 px/s`) puis cliquez sur **Sauvegarder**.

