# 🔑 Guide des Scrapers & Clés API Gratuites (`dmd-media-showcase`)

> **[🇬🇧 Read this guide in English (SCRAPERS_GUIDE.md)](SCRAPERS_GUIDE.md)**

`dmd-media-showcase` s'appuie **exclusivement sur des API de scrapers officielles, publiques et gratuites** (bandes-annonces promotionnelles, vidéos de gameplay « video snaps », jaquettes, logos d'arcade et synopsis traduits). Aucun contenu piraté, aucune ROM et aucun film ou épisode complet n'est utilisé.

---

## 1. Tableau Récapitulatif des Sources par Domaine

| Domaine Média | Source Principale (Compte Gratuit) | Sources Publiques Sans Clé (0 Config) | Catalogue Hors-Ligne Intégré | Langues Supportées |
| :--- | :--- | :--- | :--- | :--- |
| 🕹️ **Jeux Rétro** (`/api/v1/games`) | **ScreenScraper.fr** (`SCREENSCRAPER_USER` / `PASS`) | **Steam Store API** (Ports PC / Arcade) | Classiques Arcade (12+ titres) | `fr`, `en`, `es`, `de`, `it`, `pt` |
| 🎬 **Films** (`/api/v1/movies`) | **TMDB API v3** (`TMDB_API_KEY`) | — | Classiques du Cinéma (10+ titres) | `fr-FR`, `en-US`, `es-ES`, `de-DE`, `it-IT`, `ja-JP` |
| 📺 **Séries TV** (`/api/v1/series`) | **TMDB TV** (`TMDB_API_KEY`) & **TheTVDB v4** (`TVDB_API_KEY`) | **TVMaze Public API** | Séries Cultes (10+ titres) | `fr-FR`, `en-US`, `es-ES`, `de-DE`, `it-IT` |
| ⛩️ **Animés** (`/api/v1/anime`) | **TMDB** (`TMDB_API_KEY` pour les synopsis FR/ES/DE) | **AnimeThemes.moe** (OP/ED WebM) + **Jikan v4** (MyAnimeList) | Classiques Animés (10+ titres) | `fr`, `en`, `es`, `de`, `it`, `ja` |

---

## 2. Mode Zero-Config (Fonctionnement Immédiat Sans Clé)

Si vous démarrez le conteneur **sans aucun fichier `.env`**, le serveur fonctionne immédiatement en **Mode Zero-Config** :
- **Jeux Rétro :** Combine le catalogue multilingue intégré des classiques d'arcade (Pac-Man, Metal Slug, Street Fighter II, Chrono Trigger, Castlevania: Symphony of the Night, Sonic 2, OutRun, etc.) avec l'API publique sans clé **Steam Store**.
- **Films :** Utilise le catalogue multilingue intégré des classiques du cinéma (Blade Runner, Retour vers le Futur, Matrix, Terminator 2, Jurassic Park, Akira, Mad Max: Fury Road, Alien, etc.).
- **Séries TV :** Enrichit le catalogue multilingue des séries cultes (Stranger Things, Breaking Bad, X-Files, K2000, The Mandalorian, MacGyver, Batman la série animée, etc.) avec les affiches de l'API publique **TVMaze**.
- **Animés :** Exploite les API publiques sans clé **AnimeThemes.moe** et **Jikan (MyAnimeList v4)** combinées au catalogue multilingue intégré (Dragon Ball Z, Neon Genesis Evangelion, Cowboy Bebop, One Piece, L'Attaque des Titans, Les Chevaliers du Zodiaque / Saint Seiya, Goldorak, etc.).

---

## 3. Pas-à-Pas : Obtenir vos Clés API Gratuites

### A. TMDB (The Movie Database) — Films, Séries & Synopsis d'Animés en Français
1. Créez un compte gratuit sur **[https://www.themoviedb.org/signup](https://www.themoviedb.org/signup)**.
2. Rendez-vous dans **Paramètres → API** (`https://www.themoviedb.org/settings/api`) et générez une clé **Developer** gratuite.
3. Copiez la **Clé d'API (v3 auth)** dans votre fichier `.env` :
   ```env
   TMDB_API_KEY=votre_cle_tmdb_v3_de_32_caracteres
   ```
4. **Intérêt :** Débloque des dizaines de milliers de films, séries TV et animés avec leurs titres, affiches et synopsis officiels traduits (`language=fr-FR`, `en-US`, `es-ES`, `de-DE`, `it-IT`).

### B. ScreenScraper.fr — Jeux Rétro, Arcade & Vidéos de Gameplay (`video-normalized`)
1. Créez un compte membre gratuit sur **[https://www.screenscraper.fr/](https://www.screenscraper.fr/)**.
2. Renseignez vos identifiants dans `.env` :
   ```env
   SCREENSCRAPER_USER=votre_pseudo
   SCREENSCRAPER_PASS=votre_mot_de_passe
   # Optionnel (si vous disposez également d'identifiants développeur ScreenScraper) :
   SCREENSCRAPER_DEV_ID=
   SCREENSCRAPER_DEV_PASS=
   ```
3. **Intérêt :** ScreenScraper est la référence mondiale du rétrogaming francophone et international (utilisée par Batocera, Recalbox, RetroBat). Elle fournit les synopsis localisés (`synopsis-fr`, `synopsis-en`, `synopsis-es`), les logos `wheel` d'arcade, les jaquettes `box-2D` et les extraits vidéo de gameplay `video-normalized` au format MP4.

### C. TheTVDB v4 (Optionnel) — Séries TV Classiques & Internationales
1. Demandez une clé API gratuite sur **[https://thetvdb.com/api-information](https://thetvdb.com/api-information)**.
2. Renseignez `TVDB_API_KEY` dans `.env` :
   ```env
   TVDB_API_KEY=votre_cle_tvdb_v4
   ```

### D. Sources Sans Clé (Activées Automatiquement)
- **Steam Store API (`store.steampowered.com/api`) :** Fournit directement des micro-trailers de gameplay en `.webm` / `.mp4` et des bannières HD sans authentification.
- **AnimeThemes.moe (`api.animethemes.moe`) :** Encyclopédie communautaire d'Openings (`OP`) et d'Endings (`ED`) d'animés en streaming direct.
- **Jikan v4 (`api.jikan.moe/v4`) :** Passerelle REST open-source vers MyAnimeList.
- **TVMaze (`api.tvmaze.com`) :** API publique de métadonnées et d'affiches de séries télévisées.
