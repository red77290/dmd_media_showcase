# Guide de Démarrage Rapide (QuickStart) — DMD Media Showcase Server

🇬🇧 [English](QUICKSTART.md) | 🇫🇷 Français | 🇪🇸 [Español](QUICKSTART_ES.md)

[![Telegram Community](https://img.shields.io/badge/Telegram-Join%20Community-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/+DrSdAO8UMUViNmE0)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Project-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/red1l)

---

## 1. Modes de Déploiement

### Mode A : Image Docker Officielle Multi-Arch (Linux / Raspberry Pi 4 & 5 / NAS)

#### Option 1 — `docker-compose.yml` (Recommandé)

```yaml
services:
  dmd-media-showcase:
    image: ghcr.io/red77290/dmd_media_showcase:latest
    pull_policy: always
    container_name: dmd-media-showcase
    restart: unless-stopped
    ports:
      - "8890:8890"       # API REST FastAPI + Dashboard Admin + Swagger UI
      - "1883:1883"       # Broker MQTT 3.1.1 Embarqué
      - "5353:5353/udp"   # Auto-Découverte Réseau Local mDNS (dmdmediashowcase.local)
    volumes:
      - ./data:/app/data  # Persistance server_config.json, seen_history.json & referentials/
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```

Démarrage direct avec le [`docker-compose.yml`](../docker-compose.yml) du dépôt :
```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
docker logs -f dmd-media-showcase
```

#### Option 2 — Commande `docker run` Directe

```bash
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest
```

> [!TIP]
> **NAT Docker Bridge & Streaming UDP (`client_ip`)** : `ArcadeMatrix_RPi` et `ArcadeMatrix` (ESP32) détectent et déclarent automatiquement leur véritable IP LAN (`client_ip` / `udp_ip`) dans leurs payloads MQTT `config` et `start_stream`. Ainsi, `ffmpeg -re` envoie les paquets UDP `DMDF` directement au DMD même lorsque Docker tourne en mode bridge standard (`172.18.0.1`).

---

### Mode B : Installeurs Natifs & Archives Portables (macOS `.dmg`, Windows `.zip`, Linux `.tar.gz`)

1. **macOS (Apple Silicon `arm64`)** :
   - Téléchargez **`DMD_Media_Showcase_macOS_arm64.dmg`** depuis GitHub Releases, ouvrez-le et glissez **`DMD Media Showcase.app`** dans **`Applications`**.
   - Lancez **`DMD Media Showcase.app`** : l'icône `▣ DMD` apparaît dans la barre des menus macOS, ouvre automatiquement `http://localhost:8890/` et stocke les données dans `~/Library/Application Support/DMDMediaShowcase/data/`.
   - *Note Gatekeeper au 1er lancement (app open-source signée ad-hoc sans certificat Apple payant)* : Clic droit sur `DMD Media Showcase.app` → **Ouvrir** (ou **Réglages Système → Confidentialité et sécurité → Ouvrir quand même**, ou `xattr -cr "/Applications/DMD Media Showcase.app"` une seule fois).
2. **Windows 10/11 (`x64`)** :
   - Téléchargez **`DMD_Media_Showcase_Windows_x64.zip`**, extrayez-la (ex. `C:\DMD_Media_Showcase\`) et double-cliquez sur `Start_Showcase_Windows.bat` (ou `dmd-media-showcase.exe`). Données isolées dans `./data/`.
3. **Linux (`x64`)** :
   - Téléchargez **`DMD_Media_Showcase_Linux_x64.tar.gz`**, extrayez (`tar -xzf ...`) et lancez `./start_showcase.sh`.

---

## 2. Dashboard Web d'Administration (`http://dmdmediashowcase.local:8890/`)

Ouvrez `http://dmdmediashowcase.local:8890/` (ou `http://<ip-serveur>:8890/`) dans votre navigateur pour :
- **Ajuster à chaud le Runtime, les Workers & les Limites Vidéo en Temps Réel** (`2. Runtime, Workers & Limites Vidéo`, persisté dans `./data/server_config.json` sans redémarrage) :
  - Modifier dynamiquement le nombre de workers simultanés (`max_concurrent_jobs` : `0 = Auto borné CPU` ou `1..16`), le mode d'exécuteur (`ProcessPoolExecutor` vs `ThreadPoolExecutor`), les threads FFmpeg par job (`1..8`), la priorité CPU `nice` (`0..19`), la file d'attente (`10..1000`) et le niveau de log serveur en direct (`log_level`).
  - Plafonner globalement la durée vidéo maximale (`video_max_duration_limit_s`), la cible clip master (`server_master_clip_max_s`), la fréquence maximale (`video_max_fps_limit`), le poids max de téléchargement (`ytdlp_max_filesize_mb`), la durée de défilement des panoramas (`panorama_duration_s`), le cadrage/boost couleur par défaut (`default_video_mode`, `default_color_boost`), le répertoire de cache et les TTL de sessions/batches.
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
   - Vérifiez que le témoin visuel passe au vert : **`🟢 Communication OK`**.
3. Dans l'onglet **Screens / Rotation**, ajoutez l'un des 4 moteurs Showcase (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`) et définissez le nombre de **Media** à afficher par tour de rotation.
4. Le WebUI du DMD interroge automatiquement `GET /api/v1/{domain}/filters?lang={ui_lang}` pour peupler dynamiquement les listes **Systèmes**, **Genres** et **Décennies** (en filtrant automatiquement les genres et décennies supportés lorsque vous sélectionnez un système comme `NES` ou `Neo-Geo`).
5. Réglez la **Vitesse de défilement du synopsis** (`1 = Lent` à `10 = Turbo`, jusqu'à `160 px/s`) puis cliquez sur **Sauvegarder**.

