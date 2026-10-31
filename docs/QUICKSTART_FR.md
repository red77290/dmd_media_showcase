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
      - "8890:8890"       # API REST FastAPI + Flux HTTP DMDZ v2 / DMDS Sans Perte + Dashboard Admin + Swagger UI
      - "1883:1883"       # Broker MQTT 3.1.1 Embarqué
      - "5353:5353/udp"   # Auto-Découverte Réseau Local mDNS (dmdmediashowcase.local)
    volumes:
      - ${DMD_DATA_PATH:-./data}:/app/data  # Persistance server_config.json, dmd_profiles.json, seen_history.json & referentials/
    tmpfs:
      - /tmp/dmd_media_showcase_cache:size=256m,mode=1777
```

Démarrage direct avec le [`docker-compose.yml`](../docker-compose.yml) du dépôt :
```bash
curl -fsSLO https://raw.githubusercontent.com/red77290/dmd_media_showcase/main/docker-compose.yml
docker compose up -d
docker logs -f dmd-media-showcase
```

> **Build Local depuis un Clone Git (`docker-compose.override.yml`)** : Depuis une copie locale du dépôt Git, [`docker-compose.override.yml`](../docker-compose.override.yml) est pris en compte automatiquement afin que `docker compose up -d --build` compile `dmd-media-showcase:local` à partir de vos fichiers locaux (ainsi que la dernière version de `dmd_gif_converter >= 8.0.4`) au lieu de télécharger `ghcr.io/red77290/dmd_media_showcase:latest`.

#### Option 2 — Commande `docker run` Directe (Bind Mount Hôte ou Volume Nommé Portainer)

```bash
# Option 2A : Montage répertoire hôte (./data — protégé contre `docker compose down -v`)
docker run -d \
  --name dmd-media-showcase \
  --restart unless-stopped \
  -p 8890:8890 \
  -p 1883:1883 \
  -p 5353:5353/udp \
  -v ./data:/app/data \
  --tmpfs /tmp/dmd_media_showcase_cache:size=256m,mode=1777 \
  ghcr.io/red77290/dmd_media_showcase:latest

# Option 2B : Volume nommé Docker (`dmd_showcase_data` — idéal pour Portainer / déploiement autonome)
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

#### Persistance des Données & Protection des Volumes (`/app/data`)

Toutes les données persistantes du serveur sont regroupées dans `/app/data` :
- `server_config.json` : Surcharges d'identifiants scrapers, politiques multi-sources par domaine (`source_policies` : `ordered_sources`, `disabled_sources`, `aggregation_mode`), concurrence des workers et limites vidéo.
- `dmd_profiles.json` : Profils DMD gérés par le serveur (`config_source: "server"`), playlists, filtres et langue de diffusion (`lang`).
- `seen_history.json` : Historique Consume-Once par domaine (zéro répétition).
- `referentials/*.json` : Cache local des référentiels de catégories scrapers.

Le montage **`${DMD_DATA_PATH:-./data}:/app/data`** dans `docker-compose.yml` pointe vers un répertoire hôte réel (surchargeable via `DMD_DATA_PATH=/mnt/nas/dmd_showcase` dans `.env`), **100 % insensible à `docker compose down -v`** (`-v` ne supprime que les volumes nommés Docker, jamais un dossier hôte). Sous **Portainer** ou sans chemin hôte dédié, utilisez un volume nommé (`-v dmd_showcase_data:/app/data`) en évitant l'option `-v` lors d'un `down`.

> [!TIP]
> **Pourquoi `MQTT + TCP` (`:8890`) est le Transport par Défaut sous Docker & VM Desktop** : Les Modèles 1 (`ArcadeMatrix` ESP32 / ESP32-S3 / `ArcadeMatrix_RPi`) et 2A (`supports_local_card: true`) associent **MQTT `:1883`** (`config`, `ready`, `start_card`, `stop_stream`, avec bascule automatique HTTP `/config + /next` dans le même tour) à un plan de données HTTP `1.1` `Content-Length` unifié sans perte (`GET /api/v1/{domain}/stream/{item_id}.rgb565.stream?purge={true|false}&codec=dmdz2` + nettoyage idempotent `DELETE`) — **zéro perte de paquets UDP liée au NAT des VM Docker Desktop / Rancher Desktop** et identifiants `item_id` compacts sans collision (`pkg_<domain>_<12hex>`, `<= 24` caractères). Sur **`esp32dev` (`0 Ko` PSRAM)**, le client télécharge `.dmdz2.stream` sans perte sur sa carte SD `25 MHz` (`/cache/showcase_live.dmdz`, ouverte en `O_TRUNC`) pendant la Fiche Info, ferme le socket TCP pour `0` trafic Wi-Fi durant la lecture, décode via un anneau SPSC de `8 448–9 216 o` avec signalement consommateur dans le canevas `SRAM1` Zéro-Copie, fige la dernière trame en `StreamEndHold` et supprime `/cache/showcase_live.dmdz` via `sd.remove` dès la sortie/interruption ; sur **`ESP32-S3` (`8 Mo` PSRAM, strictement `0` accès carte SD)**, le client pré-charge `.dmdz2.stream` (`<= 4 Mo`) en PSRAM pendant la Fiche Info (`0` trafic Wi-Fi en lecture) ou diffuse les longs flux HD `256x64` via un anneau SPSC PSRAM de `1 Mo` ; sur **`ArcadeMatrix_RPi`**, le client Linux pré-charge en RAM le flux `dmdz2` sans perte (`~12x–20x` plus compact, avec repli `DMDS` brut automatique) tant en rotation Showcase qu'en `FrontendSync`. Pour les récepteurs passifs UDP `:4210` optionnels (**Modèle 2B**, `UDP-PASSIVE`, `supports_local_card: false`) ou le mode legacy `mqtt_udp` (`MQTT+UDP`), le client déclare son IP LAN réelle (`client_ip` / `udp_ip`) afin que les paquets UDP `DMDF` contournent le NAT Docker bridge Linux (`172.18.0.1`).

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
- **Ordonner à Chaud les Chaînes Scrapers Multi-Sources & le Mode d'Agrégation** (éditable en direct dans **1. Vue d'Ensemble** comme dans **5. Clés API & Scrapers**, persisté dans `./data/server_config.json`) :
  - Définir l'ordre de priorité par domaine (`ordered_sources` : `#1 Primaire -> #2 Secondaire -> #3 Tertiaire -> ...`) et activer/désactiver chaque source (`disabled_sources`, avec garde-fou conservant au moins une source active par domaine). Dès que la source Primaire renvoie `0` résultat, expire, entre en cooldown de quota ou ne supporte pas le système ciblé, l'exécution bascule automatiquement sur la source active suivante.
  - Choisir le `aggregation_mode` par domaine :
    - **`enrich_missing` (Agrégation Multi-Sources, par défaut)** : La source Primaire résout l'identité canonique, les alias/acronymes et les métadonnées (ex. `IGDB` résolvant `"sf2x"` sur `cps2` $\to$ `"Super Street Fighter II Turbo"` / `ssf2t`), puis `CandidateAggregator` lance une passe secondaire bornée (`<= 3.0s`) sur les sources secondaires actives (`ScreenScraper`, `ArcadeItalia`, `Apple iTunes`, `AnimeThemes`, etc.) afin de compléter les assets DMD manquants (marquees/wheels détourés, snaps pixel-art, flux directs `.mp4`/`.webm`/`.m4v`, synopsis localisés). Les médias agrégés affichent des badges combinés (`🔗 igdb+screenscraper`, `🔗 igdb+arcadeitalia`, `🔗 tmdb_movies+itunes_movies`) dans les slots DMD en direct (`playing_source`, `ready_source`, `standby_source`, `preparing_source`) et les files prioritaires (`source_used`).
    - **`fallback_only` (Fallback Strict)** : Les sources secondaires ne sont interrogées que lorsque la source Primaire renvoie `0` résultat ou échoue.
- **Ajuster à chaud le Runtime, les Workers & les Limites Vidéo en Temps Réel** (`2. Runtime, Workers & Limites Vidéo`, persisté dans `./data/server_config.json` sans redémarrage) :
  - Modifier dynamiquement le nombre de workers simultanés (`max_concurrent_jobs` : `0 = Auto borné CPU` ou `1..16`), le mode d'exécuteur (`ProcessPoolExecutor` vs `ThreadPoolExecutor`), les threads FFmpeg par job (`1..8`), la priorité CPU `nice` (`0..19`), la file d'attente (`10..1000`) et le niveau de log serveur en direct (`log_level`).
  - Plafonner globalement la durée vidéo maximale (`video_max_duration_limit_s`), la cible clip master (`server_master_clip_max_s`), la fréquence maximale (`video_max_fps_limit`), le poids max de téléchargement (`ytdlp_max_filesize_mb`), la durée de défilement des panoramas zoom-to-cover (`panorama_duration_s` — haut-bas sur panneaux YOKO, gauche-droite sur panneaux TATE), le cadrage/boost couleur par défaut (`default_video_mode`, `default_color_boost`), le répertoire de cache et les TTL de sessions/batches.
  - **Activer le Cache Persistant des Conversions On-Demand** : Activez **Cache Persistant des Conversions On-Demand** (avec quota max en Mo optionnel). Les jeux demandés à la volée (notamment via `FrontendSync` depuis Recalbox / Batocera / RetroPie) sont conservés par `(Système + Jeu + Résolution + Style de Carte)` pour démarrer **instantanément (`0 ms`)** lors des prochains lancements sans recherche ni reconversion. Le bouton **🗑 Vider le Cache On-Demand** permet de purger ce cache en un clic.
- **Configurer les Identifiants Scrapers Optionnels** (`5. Clés API & Scrapers` ou `.env` — résolution sur 3 niveaux `server_config.json` $\to$ `.env` / variables OS $\to$ coffre natif C `_prod_vault`, avec caviardage automatique des logs par `SecretRedactingLogFilter` et `httpx`/`httpcore` bridés à `WARNING`) :
  - **IGDB v4 (Portail Développeur Twitch)** : `igdb_client_id` & `igdb_client_secret` (`IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET`, application gratuite sur `https://dev.twitch.tv/console/apps`) pour `games` sur les 66 systèmes rétro/arcade canoniques (`55+` plateformes), recherche par alias (`alternative_names`) et découverte aléatoire par offset.
  - **ScreenScraper** (`screenscraper_user`, `screenscraper_pass`, `screenscraper_dev_id`, `screenscraper_dev_pass`) pour `games` (marquees/wheels détourés, snaps pixel-art, vidéos `.mp4`, avec détection automatique de quota/surcharge HTTP-200 et disjoncteur de cooldown). Sans identifiants, **`ArcadeItalia`** (MAME/CPS/Neo-Geo/NAOMI HTTPS + alias joueurs `sf2x`, `3s`, `kof98`, `garou`) et **`Libretro+Wikidata`** (SPARQL Console/Portable/Micro + `libretro-thumbnails` + vidéos Archive.org) opèrent automatiquement sans aucune clé.
  - **Clé API TMDB / Jeton v4** (`tmdb_api_key` / `TMDB_API_KEY`) et **Clé API TheTVDB v4** (`tvdb_api_key` / `TVDB_API_KEY`) pour `movies`, `series` et `anime`. Sans clé, `Apple iTunes`, `TVMaze`, `Jikan + AnimeThemes` et `AniList` fonctionnent automatiquement.
- Choisir entre le **Broker MQTT Embarqué** (`:1883`, par défaut) ou un **Broker MQTT Externe** (Mosquitto / Home Assistant).
- **Configurer les Profils DMD Gérés par le Serveur (`./data/dmd_profiles.json`)** : Définir centralement les playlists (`games`, `movies`, `series`, `anime`), filtres scrapers (`systems`, `genres`, `decades`), préréglages vidéo/couleurs et la langue du contenu diffusé (`lang`) pour les récepteurs ESP32 passifs ou hybrides (`PUT /api/v1/clients/{dmd_id}/profile`).
- **Rechercher un Titre & Piloter la File à la Demande / Contrôles Live** : Rechercher dans le catalogue localisé (`GET /api/v1/{domain}/search`), envoyer un média en **`enqueue_next`** ou **`play_now`** (`POST /api/v1/clients/{dmd_id}/queue`, y compris via `FrontendSync` avec `mark_seen: false`), et déclencher **`Skip` / `Pause` / `Resume` / `Stop`** en direct (`POST /api/v1/clients/{dmd_id}/control`).
- Consulter et réinitialiser l'historique **`seen_history.json`** par domaine ou globalement.
- Forcer une synchronisation immédiate des référentiels scrapers (`POST /api/v1/admin/referentials/sync`).

---

## 3. Connecter votre DMD (`ArcadeMatrix`, Récepteurs ESP32 Passifs & `ArcadeMatrix_RPi`)

### 3.1 DMD Multi-Moteurs Pilotés par le Client (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`)

1. Ouvrez le WebUI de votre DMD (`http://arcadematrix.local/` sur ESP32 ou `http://arcadematrix.local:8080/` sur Raspberry Pi).
2. Rendez-vous dans **`Settings -> Showcase Server`** (`#mqtt-subtab-showcase`) :
   - Le champ **Showcase Server URL** est déjà pré-rempli avec `http://dmdmediashowcase.local:8890` et **désactivé par défaut** (`⚪ Désactivé`) afin d'éviter tout accès réseau inutile si `dmd_media_showcase` n'est pas installé.
   - Activez le bouton **Enable DMD Media Showcase Server** puis cliquez sur **Save Showcase Server** (ou **Test Connection**).
   - Vérifiez que le témoin visuel passe au vert : **`🟢 Communication OK`**.
3. Dans l'onglet **Screens / Rotation**, ajoutez l'un des 4 moteurs Showcase (`retro_games`, `movie_showcase`, `series_showcase`, `anime_showcase`) et définissez le nombre de **Media** à afficher par tour de rotation.
4. Le WebUI du DMD interroge automatiquement `GET /api/v1/{domain}/filters?lang={ui_lang}` pour peupler dynamiquement les listes **Systèmes** (`55+` plateformes), **Genres** et **Décennies** (en filtrant automatiquement les genres et décennies supportés lorsque vous sélectionnez un système comme `NES`, `CPS-2` ou `Neo-Geo`).
5. Réglez la **Vitesse de défilement du synopsis** (`1 = Lent` à `10 = Turbo`, jusqu'à `160 px/s`) puis cliquez sur **Sauvegarder** — le DMD diffuse automatiquement via **`MQTT+TCP`** (MQTT `:1883` + flux HTTP `1.1` `Content-Length` `DMDZ` v2 sans perte sur `:8890` : cache SD `25 MHz` auto-nettoyant sur `esp32dev`, `100 % PSRAM` pré-chargée / anneau SPSC PSRAM `1 Mo` avec `0` SD sur `ESP32-S3`, et pré-chargement RAM avec repli `DMDS` brut sur `ArcadeMatrix_RPi` ; les modes `HTTP` et `MQTT+UDP` optionnel sont également supportés).

### 3.2 Récepteurs ESP32 Hybrides (Modèle 2A) & Passifs UDP (Modèle 2B) Gérés par le Serveur (`config_source: "server"`)

Pour un topper/marquee dédié sur `ESP32_STD` (sans PSRAM) ou `ESP32-S3` sans WebUI local — soit en **Modèle 2A Hybride** (`supports_local_card: true`, Fiche Info locale 60 FPS + flux `MQTT+TCP` HTTP `1.1` `DMDZ` v2 / `DMDS` sans perte sur `:8890`), soit en **Modèle 2B Passif** (`supports_local_card: false`, ~180 lignes C++ à l'écoute sur `UDP-PASSIVE` `:4210` `DMDF`) :
1. Flashez le firmware de référence du **[Guide d'Intégration ESP32 & Récepteur Passif](ESP32_INTEGRATION_FR.md)** (annonce automatique de la géométrie et de l'IP via MQTT `dmd_showcase/clients/{dmd_id}/hello` ou `POST /api/v1/clients/hello`).
2. Ouvrez `http://dmdmediashowcase.local:8890/`, sélectionnez le panneau détecté, choisissez sa langue de diffusion (`lang`), activez vos domaines/catégories et enregistrez son profil dans `./data/dmd_profiles.json` — le Modèle 2A diffuse en HTTP `1.1` `DMDZ` v2 sans perte sur `:8890` (cache SD `25 MHz` + `sd.remove` sur `esp32dev`, `100 % PSRAM` sur `ESP32-S3`), tandis que le Modèle 2B génère même la Fiche Info de Phase 1 directement dans le flux UDP `DMDF` (`:4210`) RGB565 !

### 3.3 Coupler Showcase avec Recalbox / Batocera / RetroPie (`FrontendSync` Showcase en Jeu)

Lorsque votre DMD (`ArcadeMatrix` ESP32 ou `ArcadeMatrix_RPi`) est relié à une console rétro, Showcase enrichit automatiquement vos lancements de jeux sans jamais ralentir la navigation dans les menus :
1. **Installer le script console une seule fois** : Renseignez **uniquement l'adresse IP de votre DMD** lors de l'installation du script sur Recalbox, Batocera (`v33+`) ou RetroPie. La console interroge dynamiquement le DMD pour connaître le broker MQTT actif — vous n'aurez jamais besoin de réinstaller le script en activant ou coupant Showcase.
2. **Activer le Showcase en Jeu sur le DMD** : Dans la WebUI du DMD (`Système -> MQTT & API -> 🎮 FrontendSync`), cochez **Utiliser le Broker MQTT Showcase si actif** (libère la RAM du DMD en partageant le broker `:1883` de Showcase) et **Showcase en Jeu (Carte + Vidéo Gameplay)**.
3. **Choisir le Cycle & le Design de Carte** :
   - **Navigation Menus (`0 ms`)** : Le parcours des listes affiche instantanément les marquees Pixelcade locaux.
   - **Lancement d'un Jeu** : Le marquee classique s'affiche tout de suite pendant que Showcase prépare en tâche de fond la **Fiche Info Animée + Panoramas + Vidéo de Gameplay**, puis prend le relais en douceur dès qu'elle est prête (`Une fois`, `Alterner Marquee & Showcase` ou `Boucle`).
   - **Style de la Carte** : Gardez **Hériter du profil DMD sur Showcase Studio** (réglé visuellement dans le Studio de Showcase) ou choisissez **Personnalisé sur le DMD** directement dans `FrontendSync`.
4. **Cache Instantané** : Activez **Cache Persistant des Conversions On-Demand** dans Showcase (`2. Runtime, Workers & Limites Vidéo`) pour que les jeux déjà lancés démarrent instantanément lors des sessions suivantes, et utilisez **🗑️ Vider le Cache Showcase** (sur Showcase ou depuis le DMD) à tout moment pour régénérer vos fiches après un changement de style.
