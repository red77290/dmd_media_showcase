"""
Centralized Multilingual Web Administration Dashboard (`http://dmdmediashowcase.local:8890/` & `/admin`).

Features:
1. Real-Time Overview: Worker pool telemetry, Embedded MQTT (:1883) status, connected DMD clients
   (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`) with N / N+1 Consume-Once queue slots, and active
   DDD `[Primary, Fallback]` scraper chains.
2. Live Showcase Tester: Interactive domain/system/genre/decade selector, orthogonal `video_mode`
   and `color_boost` selectors, and on-demand card preview.
3. API Keys & Encrypted Vault Status: Shows active status of `.env` / `docker-compose` or CI/CD
   Production Native Vault keys (`ScreenScraper Dev`, `TMDB`, `TheTVDB`) without ever exposing them
   in plaintext, while allowing users to configure their personal ScreenScraper player account
   (`SSID` / password) or optional overrides.
4. Network, MQTT & Maintenance: Default language, MQTT broker mode, live referential sync, and
   selective or full `seen_history.json` reset.
"""

from __future__ import annotations

ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DMD Media Showcase Server v3.0 — Control Center</title>
  <style>
    :root {
      --bg: #0b0e14;
      --bg-elev: #131924;
      --card: #171f2e;
      --border: #28344a;
      --accent: #ff6d00;
      --accent-hover: #ff8524;
      --cyan: #00e5ff;
      --green: #00e676;
      --amber: #ffab00;
      --red: #ff5252;
      --text: #e6edf3;
      --muted: #8b949e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
      background: radial-gradient(circle at top right, #162035 0%, var(--bg) 55%);
      color: var(--text);
      min-height: 100vh;
      padding: 20px;
    }
    .container { max-width: 1140px; margin: 0 auto; }
    header {
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;
      background: var(--bg-elev); border: 1px solid var(--border); border-radius: 12px;
      padding: 16px 22px; margin-bottom: 18px;
    }
    .brand { display: flex; align-items: center; gap: 14px; }
    .brand-logo {
      width: 42px; height: 42px; border-radius: 10px;
      background: linear-gradient(135deg, var(--accent), #ff3d00);
      display: flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 1.1rem; color: #fff;
      box-shadow: 0 4px 14px rgba(255, 109, 0, 0.35);
    }
    h1 { margin: 0; font-size: 1.28rem; color: #fff; letter-spacing: 0.2px; }
    .subtitle { color: var(--muted); font-size: 0.82rem; margin-top: 3px; }
    .header-right { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .links a {
      color: var(--cyan); text-decoration: none; font-size: 0.85rem; font-weight: 600;
      padding: 6px 10px; border-radius: 6px; border: 1px solid rgba(0, 229, 255, 0.25);
      background: rgba(0, 229, 255, 0.06);
    }
    .links a:hover { background: rgba(0, 229, 255, 0.14); }
    .lang-switch {
      width: auto; padding: 6px 10px; font-size: 0.85rem; font-weight: 600;
      background: #0b0e14; border: 1px solid var(--border); color: var(--text); border-radius: 6px;
    }
    .tabs {
      display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px;
      border-bottom: 1px solid var(--border); padding-bottom: 10px;
    }
    .tab-btn {
      background: var(--bg-elev); color: var(--muted); border: 1px solid var(--border);
      padding: 9px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 0.9rem;
      margin-top: 0; transition: all 0.15s ease;
    }
    .tab-btn:hover { color: var(--text); border-color: #3b4d6b; }
    .tab-btn.active {
      background: linear-gradient(135deg, var(--accent), #e65100);
      color: #fff; border-color: var(--accent);
      box-shadow: 0 3px 12px rgba(255, 109, 0, 0.3);
    }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    .kpi-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 14px; margin-bottom: 18px;
    }
    .kpi-card {
      background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px;
    }
    .kpi-label { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); }
    .kpi-val { font-size: 1.45rem; font-weight: 700; color: #fff; margin: 6px 0 4px; }
    .kpi-sub { font-size: 0.8rem; color: var(--cyan); }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 20px; margin-bottom: 18px;
    }
    .card h2 {
      margin: 0 0 8px 0; font-size: 1.06rem; color: var(--cyan);
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
    }
    .card p.desc { color: var(--muted); font-size: 0.86rem; margin: 0 0 16px 0; line-height: 1.45; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
    .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
    label { display: block; font-size: 0.83rem; color: var(--muted); margin-bottom: 5px; font-weight: 500; }
    input, select {
      width: 100%; padding: 9px 12px; background: #0b0e14; border: 1px solid var(--border);
      border-radius: 7px; color: var(--text); font-size: 0.9rem;
    }
    input:focus, select:focus { outline: none; border-color: var(--cyan); }
    .input-row { display: flex; gap: 8px; align-items: center; }
    .input-row input { flex: 1; }
    .icon-btn {
      margin-top: 0; padding: 9px 12px; background: #222c3c; border: 1px solid var(--border);
      font-size: 0.82rem; border-radius: 7px; color: var(--text); cursor: pointer;
    }
    button {
      background: var(--accent); color: #fff; border: none; border-radius: 7px;
      padding: 10px 18px; font-weight: 600; font-size: 0.9rem; cursor: pointer; margin-top: 12px;
      transition: background 0.15s ease;
    }
    button:hover { background: var(--accent-hover); }
    button.secondary { background: #222c3c; border: 1px solid var(--border); color: var(--text); }
    button.secondary:hover { background: #2c394f; }
    button.danger { background: rgba(255, 82, 82, 0.16); border: 1px solid var(--red); color: #ff8a80; }
    button.danger:hover { background: rgba(255, 82, 82, 0.28); }
    .vault-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 12px; margin-bottom: 16px;
    }
    .vault-box {
      background: #0d121b; border: 1px solid var(--border); border-radius: 9px; padding: 14px;
    }
    .vault-title { font-weight: 700; font-size: 0.92rem; margin-bottom: 6px; color: #fff; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 4px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
    }
    .pill.ok { background: rgba(0, 230, 118, 0.12); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.35); }
    .pill.warn { background: rgba(255, 171, 0, 0.12); color: var(--amber); border: 1px solid rgba(255, 171, 0, 0.35); }
    .pill.info { background: rgba(0, 229, 255, 0.12); color: var(--cyan); border: 1px solid rgba(0, 229, 255, 0.35); }
    table { width: 100%; border-collapse: collapse; font-size: 0.86rem; margin-top: 8px; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; background: #101622; }
    .badges { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
    .badge {
      background: #0d121b; border: 1px solid var(--border);
      border-radius: 8px; padding: 10px 14px; font-size: 0.86rem;
    }
    .preview-box {
      display: none; margin-top: 16px; background: #0d121b; border: 1px solid var(--border);
      border-radius: 10px; padding: 16px; gap: 18px; align-items: flex-start; flex-wrap: wrap;
    }
    .preview-box.visible { display: flex; }
    .preview-poster {
      width: 96px; height: 96px; object-fit: cover; border-radius: 8px;
      border: 1px solid var(--border); background: #000; image-rendering: pixelated;
    }
    .preview-meta { flex: 1; min-width: 240px; }
    .preview-meta h3 { margin: 0 0 6px 0; font-size: 1.1rem; color: #fff; }
    .preview-synopsis { color: var(--text); font-size: 0.88rem; line-height: 1.45; margin-top: 8px; }
    .toast {
      margin-left: 12px; font-size: 0.88rem; font-weight: 600; color: var(--green);
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-logo">DMD</div>
        <div>
          <h1>DMD Media Showcase Server v3.0</h1>
          <div class="subtitle" data-i18n="header_sub">
            Architecture DDD • MQTT Retain (:1883) + UDP Temps Réel • Consume-Once
          </div>
        </div>
      </div>
      <div class="header-right">
        <span id="health-pill" class="pill ok">● Online</span>
        <div class="links">
          <a href="/docs">Swagger (/docs)</a>
          <a href="/redoc">ReDoc</a>
          <a href="/asyncapi.yaml">AsyncAPI (MQTT/UDP)</a>
          <a href="/api/v1/health">Health JSON</a>
        </div>
        <select id="ui-lang" class="lang-switch" onchange="setUiLang(this.value)">
          <option value="fr">🇫🇷 Français</option>
          <option value="en">🇬🇧 English</option>
          <option value="es">🇪🇸 Español</option>
        </select>
      </div>
    </header>

    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('overview')" data-i18n="tab_overview">
        1. Vue d'ensemble &amp; Matrices DMD
      </button>
      <button class="tab-btn" onclick="switchTab('tester')" data-i18n="tab_tester">
        2. Testeur &amp; Aperçu Showcase
      </button>
      <button class="tab-btn" onclick="switchTab('keys')" data-i18n="tab_keys">
        3. Clés API &amp; Coffre Chiffré
      </button>
      <button class="tab-btn" onclick="switchTab('network')" data-i18n="tab_network">
        4. Réseau, MQTT &amp; Maintenance
      </button>
    </div>

    <!-- TAB 1: OVERVIEW & CONNECTED DMDS -->
    <div id="tab-overview" class="tab-pane active">
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_workers">Workers &amp; File de Transcodage</div>
          <div class="kpi-val" id="kpi-workers-val">0 / 2</div>
          <div class="kpi-sub" id="kpi-queue-sub">Queue: 0</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_mqtt">Broker MQTT &amp; Sessions</div>
          <div class="kpi-val" id="kpi-mqtt-val">Embedded :1883</div>
          <div class="kpi-sub" id="kpi-sessions-sub">0 DMD client(s)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_jobs">Jobs Préparés &amp; Latence</div>
          <div class="kpi-val" id="kpi-jobs-val">0</div>
          <div class="kpi-sub" id="kpi-avg-sub">Moyenne: 0.0s</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_seen">Titres Consommés (Consume-Once)</div>
          <div class="kpi-val" id="kpi-seen-val">0</div>
          <div class="kpi-sub" data-i18n="kpi_seen_sub">Zéro répétition garantie</div>
        </div>
      </div>

      <div class="card">
        <h2>
          <span data-i18n="dmd_clients_title">Matrices DMD Connectées (ESP32 / Raspberry Pi)</span>
          <button class="secondary" style="margin:0;padding:6px 12px;font-size:0.8rem" onclick="refreshAll()" data-i18n="btn_refresh">
            Actualiser
          </button>
        </h2>
        <p class="desc" data-i18n="dmd_clients_desc">
          Affiche en temps réel les panneaux HUB75 enregistrés via MQTT ou HTTP Polling, leurs réglages orthogonaux de cadrage/couleur et leurs slots pré-calculés N (Ready) et N+1 (Standby).
        </p>
        <div style="overflow-x:auto">
          <table>
            <thead>
              <tr>
                <th>DMD ID</th>
                <th data-i18n="th_domain">Domaine</th>
                <th data-i18n="th_geom">Géométrie</th>
                <th data-i18n="th_framing">Cadrage &amp; Couleurs</th>
                <th data-i18n="th_ready">Slot Prêt (N)</th>
                <th data-i18n="th_standby">Slot Standby (N+1)</th>
              </tr>
            </thead>
            <tbody id="clients-tbody">
              <tr><td colspan="6" style="color:var(--muted)" data-i18n="no_clients">Aucun client DMD actif pour le moment.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2 data-i18n="chains_title">Chaînes de Scrapers DDD ([Primary, Fallback])</h2>
        <p class="desc" data-i18n="chains_desc">
          Chaque domaine utilise sa source Primaire et bascule automatiquement sans interruption sur sa source Fallback en cas de quota atteint ou d'absence de clé.
        </p>
        <div class="grid" id="providers-grid"></div>
      </div>
    </div>

    <!-- TAB 2: LIVE SHOWCASE TESTER -->
    <div id="tab-tester" class="tab-pane">
      <div class="card">
        <h2 data-i18n="tester_title">Testeur &amp; Aperçu Live Showcase</h2>
        <p class="desc" data-i18n="tester_desc">
          Teste immédiatement la découverte officielle (ScreenScraper, ArcadeItalia, TMDB, Apple iTunes, TVMaze, Jikan/AnimeThemes, AniList) et vérifie le cadrage IA Let Me Handle It ou le Boost Colorimétrique.
        </p>
        <div class="grid-3">
          <div>
            <label data-i18n="lbl_domain">Domaine</label>
            <select id="test-domain" onchange="loadDomainFilters()">
              <option value="games">🕹️ Retro Games (games)</option>
              <option value="movies">🎬 Movies / Films (movies)</option>
              <option value="series">📺 TV Series (series)</option>
              <option value="anime">⛩️ Anime / Japanimation (anime)</option>
            </select>
          </div>
          <div id="test-system-wrap">
            <label data-i18n="lbl_system">Système (Jeux)</label>
            <select id="test-system"><option value="">Tous (All)</option></select>
          </div>
          <div>
            <label data-i18n="lbl_genre">Genre</label>
            <select id="test-genre"><option value="">Tous (All)</option></select>
          </div>
          <div>
            <label data-i18n="lbl_decade">Décennie</label>
            <select id="test-decade"><option value="">Toutes (All)</option></select>
          </div>
          <div>
            <label data-i18n="lbl_video_mode">Mode de Cadrage Vidéo (video_mode)</label>
            <select id="test-video-mode">
              <option value="auto_action">Auto (Let Me Handle It AI)</option>
              <option value="panorama">Panorama (Top-Bottom Scan)</option>
              <option value="center_crop">Static Center Crop</option>
            </select>
          </div>
          <div>
            <label data-i18n="lbl_color_boost">Boost Colorimétrique LED (color_boost)</label>
            <select id="test-color-boost">
              <option value="auto">Auto (Let Me Handle It AI)</option>
              <option value="pixel_art">Pixel Art (Crisp Retro)</option>
              <option value="anime">Anime (Vibrant Cel-Shaded)</option>
              <option value="cinema">Cinema (Balanced Contrast &amp; Shadows)</option>
            </select>
          </div>
          <div>
            <label data-i18n="lbl_resolution">Résolution Panneau DMD</label>
            <select id="test-res">
              <option value="128x32">128×32 (Standard Marquee)</option>
              <option value="128x64">128×64 (Double Height)</option>
              <option value="64x32">64×32 (Compact)</option>
              <option value="64x64">64×64 (Square)</option>
              <option value="256x64">256×64 (XL Arcade)</option>
            </select>
          </div>
          <div style="grid-column: span 2">
            <label data-i18n="lbl_query">Recherche directe par titre (Optionnel — vide = tirage aléatoire filtré)</label>
            <input type="text" id="test-query" placeholder="Ex: Super Street Fighter II Turbo, The Matrix, Cowboy Bebop..." />
          </div>
        </div>
        <div>
          <button onclick="runLiveTest()" id="btn-run-test" data-i18n="btn_run_test">
            Lancer la Découverte &amp; Générer l'Aperçu
          </button>
          <span id="test-status" class="toast"></span>
        </div>

        <div id="test-preview" class="preview-box">
          <img id="prev-img" class="preview-poster" src="" alt="Poster" />
          <div class="preview-meta">
            <h3 id="prev-title"></h3>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <span id="prev-badge" class="pill info"></span>
              <span id="prev-year" class="pill ok"></span>
              <span id="prev-source" class="pill warn"></span>
            </div>
            <div id="prev-sub" style="color:var(--muted);font-size:0.84rem;margin-top:6px"></div>
            <div id="prev-synopsis" class="preview-synopsis"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 3: API KEYS & ENCRYPTED VAULT -->
    <div id="tab-keys" class="tab-pane">
      <div class="card">
        <h2 data-i18n="vault_title">État des Clés Embarquées (Prod Vault / .env / docker-compose)</h2>
        <p class="desc" data-i18n="vault_desc">
          Priorité d'exécution : 1. Surcharge WebUI (`server_config.json`) → 2. Variables `.env` / `docker-compose.yml` → 3. Binaire natif chiffré `_prod_vault` (Release Prod CI/CD). Les clés dev ne sont jamais exposées en clair.
        </p>
        <div class="vault-grid">
          <div class="vault-box">
            <div class="vault-title">ScreenScraper Dev API (devid / devpassword)</div>
            <div id="vault-ss-dev" class="pill warn">Vérification...</div>
            <div style="color:var(--muted);font-size:0.78rem;margin-top:8px" data-i18n="vault_ss_note">
              Identifiants développeur requis par l'API v2 ScreenScraper. Tant qu'ils sont vides, le serveur utilise automatiquement le Fallback ArcadeItalia.
            </div>
          </div>
          <div class="vault-box">
            <div class="vault-title">TMDB API v3/v4 (Movies &amp; TV Series)</div>
            <div id="vault-tmdb" class="pill warn">Vérification...</div>
            <div style="color:var(--muted);font-size:0.78rem;margin-top:8px" data-i18n="vault_tmdb_note">
              Clé active par défaut (.env / Prod Vault). Peut être surchargée ci-dessous par une clé personnelle.
            </div>
          </div>
          <div class="vault-box">
            <div class="vault-title">TheTVDB API v4 (Series, Anime &amp; Movies)</div>
            <div id="vault-tvdb" class="pill warn">Vérification...</div>
            <div style="color:var(--muted);font-size:0.78rem;margin-top:8px" data-i18n="vault_tvdb_note">
              Clé active par défaut (.env / Prod Vault) pour les traductions et bandes-annonces officielles.
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <h2 data-i18n="user_keys_title">Identifiants Utilisateur &amp; Surcharges Optionnelles</h2>
        <p class="desc" data-i18n="user_keys_desc">
          Renseigne ton compte membre gratuit ScreenScraper (`SSID` / mot de passe) pour associer ton quota joueur. Les champs TMDB et TheTVDB sont optionnels si une clé `.env` ou Prod Vault est déjà active.
        </p>
        <div class="grid">
          <div>
            <label data-i18n="lbl_ss_user">Compte Joueur ScreenScraper — Identifiant (SSID)</label>
            <input type="text" id="screenscraper_user" placeholder="Ton pseudo ScreenScraper (ex: player1)" />
          </div>
          <div>
            <label data-i18n="lbl_ss_pass">Compte Joueur ScreenScraper — Mot de passe</label>
            <div class="input-row">
              <input type="password" id="screenscraper_pass" placeholder="Mot de passe joueur ScreenScraper" />
              <button type="button" class="icon-btn" onclick="togglePwd('screenscraper_pass')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('screenscraper_pass')">✕</button>
            </div>
          </div>
          <div>
            <label data-i18n="lbl_tmdb_override">Clé TMDB Personnelle (Optionnel — vide = utilise .env / Prod Vault)</label>
            <div class="input-row">
              <input type="password" id="tmdb_api_key" placeholder="Utilise la clé embarquée/.env si laissé vide" />
              <button type="button" class="icon-btn" onclick="togglePwd('tmdb_api_key')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('tmdb_api_key')">✕</button>
            </div>
          </div>
          <div>
            <label data-i18n="lbl_tvdb_override">Clé TheTVDB v4 Personnelle (Optionnel — vide = utilise .env / Prod Vault)</label>
            <div class="input-row">
              <input type="password" id="tvdb_api_key" placeholder="Utilise la clé embarquée/.env si laissé vide" />
              <button type="button" class="icon-btn" onclick="togglePwd('tvdb_api_key')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('tvdb_api_key')">✕</button>
            </div>
          </div>
        </div>
        <button onclick="saveConfig()" data-i18n="btn_save">Enregistrer la Configuration</button>
        <span id="status-msg-keys" class="toast"></span>
      </div>
    </div>

    <!-- TAB 4: NETWORK, MQTT & MAINTENANCE -->
    <div id="tab-network" class="tab-pane">
      <div class="card">
        <h2 data-i18n="net_title">Paramètres Langue, Broker MQTT &amp; Sécurité</h2>
        <div class="grid">
          <div>
            <label data-i18n="lbl_default_lang">Langue par défaut des métadonnées &amp; synopsis</label>
            <select id="default_lang">
              <option value="fr">Français (fr)</option>
              <option value="en">English (en)</option>
              <option value="es">Español (es)</option>
              <option value="de">Deutsch (de)</option>
              <option value="it">Italiano (it)</option>
              <option value="ja">日本語 (ja)</option>
            </select>
          </div>
          <div>
            <label data-i18n="lbl_mqtt_mode">Mode du Broker MQTT</label>
            <select id="mqtt_mode">
              <option value="embedded">Broker Embarqué (:1883 intégré — Recommandé)</option>
              <option value="external">Broker MQTT Externe (Mosquitto / Home Assistant)</option>
            </select>
          </div>
          <div>
            <label data-i18n="lbl_mqtt_url">URL du Broker MQTT Externe (si mode = external)</label>
            <input type="text" id="mqtt_external_url" placeholder="mqtt://192.168.1.50:1883" />
          </div>
          <div>
            <label data-i18n="lbl_showcase_key">Clé d'accès HTTP/Cloud optionnelle (X-Showcase-Key — vide = ouvert LAN)</label>
            <div class="input-row">
              <input type="password" id="showcase_api_key" placeholder="Vide = mode Home-Lab sans mot de passe" />
              <button type="button" class="icon-btn" onclick="togglePwd('showcase_api_key')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('showcase_api_key')">✕</button>
            </div>
          </div>
        </div>
        <button onclick="saveConfig()" data-i18n="btn_save">Enregistrer la Configuration</button>
        <span id="status-msg-net" class="toast"></span>
      </div>

      <div class="card">
        <h2 data-i18n="ref_title">Synchronisation des Référentiels Amont (Catalogues Officiels)</h2>
        <p class="desc" data-i18n="ref_desc">
          Force le rafraîchissement immédiat des index de genres et systèmes amont (ArcadeItalia MAME ADB, Libretro+Wikidata, Jikan MAL, AniList GraphQL, TVMaze) et republie le catalogue MQTT Retain.
        </p>
        <button class="secondary" onclick="syncReferentials()" id="btn-sync-ref" data-i18n="btn_sync_ref">
          🔄 Synchroniser les Référentiels Maintenant
        </button>
        <span id="sync-status" class="toast"></span>
        <div class="badges" id="ref-badges"></div>
      </div>

      <div class="card">
        <h2 data-i18n="seen_title">Historique Consume-Once (seen_history.json)</h2>
        <p class="desc" data-i18n="seen_desc">
          Chaque œuvre diffusée sur une matrice DMD est immédiatement marquée comme vue pour garantir un renouvellement permanent sans répétition.
        </p>
        <div class="badges" id="seen-badges"></div>
        <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
          <button class="danger" onclick="resetHistory('')" data-i18n="btn_reset_all">Purger Tous les Domaines</button>
          <button class="secondary" onclick="resetHistory('games')">Reset Games</button>
          <button class="secondary" onclick="resetHistory('movies')">Reset Movies</button>
          <button class="secondary" onclick="resetHistory('series')">Reset Series</button>
          <button class="secondary" onclick="resetHistory('anime')">Reset Anime</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const I18N = {
      fr: {
        header_sub: "Architecture DDD • MQTT Retain (:1883) + UDP Temps Réel • Consume-Once",
        tab_overview: "1. Vue d'ensemble & Matrices DMD",
        tab_tester: "2. Testeur & Aperçu Showcase",
        tab_keys: "3. Clés API & Coffre Chiffré",
        tab_network: "4. Réseau, MQTT & Maintenance",
        kpi_workers: "Workers & File de Transcodage",
        kpi_mqtt: "Broker MQTT & Sessions",
        kpi_jobs: "Jobs Préparés & Latence",
        kpi_seen: "Titres Consommés (Consume-Once)",
        kpi_seen_sub: "Zéro répétition garantie",
        dmd_clients_title: "Matrices DMD Connectées (ESP32 / Raspberry Pi)",
        dmd_clients_desc: "Affiche en temps réel les panneaux HUB75 enregistrés via MQTT ou HTTP Polling, leurs réglages orthogonaux de cadrage/couleur et leurs slots pré-calculés N (Ready) et N+1 (Standby).",
        btn_refresh: "Actualiser",
        no_clients: "Aucun client DMD actif pour le moment.",
        chains_title: "Chaînes de Scrapers DDD ([Primary, Fallback])",
        chains_desc: "Chaque domaine utilise sa source Primaire et bascule automatiquement sans interruption sur sa source Fallback en cas de quota atteint ou d'absence de clé.",
        tester_title: "Testeur & Aperçu Live Showcase",
        tester_desc: "Teste immédiatement la découverte officielle (ScreenScraper, ArcadeItalia, TMDB, Apple iTunes, TVMaze, Jikan/AnimeThemes, AniList) et vérifie le cadrage IA Let Me Handle It ou le Boost Colorimétrique.",
        btn_run_test: "Lancer la Découverte & Générer l'Aperçu",
        vault_title: "État des Clés Embarquées (Prod Vault / .env / docker-compose)",
        vault_desc: "Priorité d'exécution : 1. Surcharge WebUI (`server_config.json`) → 2. Variables `.env` / `docker-compose.yml` → 3. Binaire natif chiffré `_prod_vault` (Release Prod CI/CD). Les clés dev ne sont jamais exposées en clair.",
        user_keys_title: "Identifiants Utilisateur & Surcharges Optionnelles",
        btn_save: "Enregistrer la Configuration",
        btn_sync_ref: "🔄 Synchroniser les Référentiels Maintenant",
        btn_reset_all: "Purger Tous les Domaines",
        vault_active: "🔒 Clé Active (.env / Prod Vault)",
        vault_override: "🛠️ Surcharge Utilisateur Active",
        vault_missing: "⚠️ Non configurée (Fallback actif)"
      },
      en: {
        header_sub: "DDD Architecture • MQTT Retain (:1883) + Real-Time UDP • Consume-Once",
        tab_overview: "1. Overview & DMD Matrices",
        tab_tester: "2. Live Showcase Tester",
        tab_keys: "3. API Keys & Encrypted Vault",
        tab_network: "4. Network, MQTT & Maintenance",
        kpi_workers: "Workers & Transcode Queue",
        kpi_mqtt: "MQTT Broker & Sessions",
        kpi_jobs: "Prepared Jobs & Latency",
        kpi_seen: "Consumed Titles (Consume-Once)",
        kpi_seen_sub: "Zero repetition guaranteed",
        dmd_clients_title: "Connected DMD Matrices (ESP32 / Raspberry Pi)",
        dmd_clients_desc: "Live view of HUB75 panels registered via MQTT or HTTP Polling, their orthogonal framing/color presets, and their pre-computed N (Ready) and N+1 (Standby) slots.",
        btn_refresh: "Refresh",
        no_clients: "No active DMD client registered yet.",
        chains_title: "DDD Scraper Chains ([Primary, Fallback])",
        chains_desc: "Each domain queries its Primary adapter first and seamlessly fails over to its Fallback adapter if unconfigured or rate-limited.",
        tester_title: "Live Showcase Tester & Preview",
        tester_desc: "Test official discovery adapters (ScreenScraper, ArcadeItalia, TMDB, Apple iTunes, TVMaze, Jikan/AnimeThemes, AniList) and preview AI framing & LED color boost.",
        btn_run_test: "Run Discovery & Generate Preview",
        vault_title: "Built-in & Environment Key Status (Prod Vault / .env / docker-compose)",
        vault_desc: "Runtime priority: 1. WebUI Override (`server_config.json`) → 2. `.env` / `docker-compose.yml` → 3. Native `_prod_vault` binary (CI/CD Prod Release). Developer keys are never exposed in plaintext.",
        user_keys_title: "User Credentials & Optional Overrides",
        btn_save: "Save Server Configuration",
        btn_sync_ref: "🔄 Sync Upstream Referentials Now",
        btn_reset_all: "Reset All Domains",
        vault_active: "🔒 Built-in / Env Key Active",
        vault_override: "🛠️ Custom User Override Active",
        vault_missing: "⚠️ Unconfigured (Fallback active)"
      },
      es: {
        header_sub: "Arquitectura DDD • MQTT Retain (:1883) + UDP en Tiempo Real • Consume-Once",
        tab_overview: "1. Vista General y Matrices DMD",
        tab_tester: "2. Probador Showcase en Vivo",
        tab_keys: "3. Claves API y Bóveda Cifrada",
        tab_network: "4. Red, MQTT y Mantenimiento",
        kpi_workers: "Workers y Cola de Transcodificación",
        kpi_mqtt: "Broker MQTT y Sesiones",
        kpi_jobs: "Trabajos Preparados y Latencia",
        kpi_seen: "Títulos Consumidos (Consume-Once)",
        kpi_seen_sub: "Cero repeticiones garantizado",
        dmd_clients_title: "Matrices DMD Conectadas (ESP32 / Raspberry Pi)",
        dmd_clients_desc: "Vista en tiempo real de paneles HUB75 registrados por MQTT o HTTP Polling y sus ranuras N (Ready) y N+1 (Standby).",
        btn_refresh: "Actualizar",
        no_clients: "Ningún cliente DMD activo por ahora.",
        chains_title: "Cadenas de Scrapers DDD ([Primary, Fallback])",
        chains_desc: "Cada dominio consulta su fuente Primaria y conmuta automáticamente a su fuente Fallback.",
        tester_title: "Probador y Vista Previa en Vivo",
        tester_desc: "Prueba el descubrimiento oficial y los ajustes ortogonales de encuadre IA y color LED.",
        btn_run_test: "Lanzar Descubrimiento y Vista Previa",
        vault_title: "Estado de Claves Integradas (Prod Vault / .env / docker-compose)",
        vault_desc: "Prioridad en ejecución: 1. WebUI (`server_config.json`) → 2. `.env` / `docker-compose.yml` → 3. Binario nativo `_prod_vault` (Release Prod CI/CD).",
        user_keys_title: "Credenciales de Usuario y Anulaciones Opcionales",
        btn_save: "Guardar Configuración",
        btn_sync_ref: "🔄 Sincronizar Referenciales Ahora",
        btn_reset_all: "Reiniciar Todos los Dominios",
        vault_active: "🔒 Clave Activa (.env / Prod Vault)",
        vault_override: "🛠️ Clave Personalizada Activa",
        vault_missing: "⚠️ No configurada (Fallback activo)"
      }
    };

    let currentUiLang = localStorage.getItem('dmd_showcase_ui_lang') || 'fr';
    let lastVaultStatus = {};

    function t(key) {
      return (I18N[currentUiLang] && I18N[currentUiLang][key]) || I18N.fr[key] || key;
    }

    function setUiLang(lang) {
      currentUiLang = lang;
      localStorage.setItem('dmd_showcase_ui_lang', lang);
      document.getElementById('ui-lang').value = lang;
      document.querySelectorAll('[data-i18n]').forEach(el => {
        const k = el.getAttribute('data-i18n');
        if (I18N[lang] && I18N[lang][k]) el.textContent = I18N[lang][k];
      });
      renderVaultStatus(lastVaultStatus);
    }

    function switchTab(name) {
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      const idx = {overview: 0, tester: 1, keys: 2, network: 3}[name] || 0;
      document.querySelectorAll('.tab-btn')[idx].classList.add('active');
    }

    function togglePwd(id) {
      const el = document.getElementById(id);
      el.type = el.type === 'password' ? 'text' : 'password';
    }

    function clearField(id) {
      const el = document.getElementById(id);
      el.value = '';
      el.dataset.cleared = '1';
    }

    function renderVaultStatus(vs) {
      lastVaultStatus = vs || {};
      const ssEl = document.getElementById('vault-ss-dev');
      if (vs.screenscraper_dev_active) {
        ssEl.className = 'pill ok';
        ssEl.textContent = t('vault_active');
      } else {
        ssEl.className = 'pill warn';
        ssEl.textContent = t('vault_missing');
      }

      const tmdbEl = document.getElementById('vault-tmdb');
      if (vs.tmdb_user_override) {
        tmdbEl.className = 'pill info';
        tmdbEl.textContent = t('vault_override');
      } else if (vs.tmdb_builtin_active) {
        tmdbEl.className = 'pill ok';
        tmdbEl.textContent = t('vault_active');
      } else {
        tmdbEl.className = 'pill warn';
        tmdbEl.textContent = t('vault_missing');
      }

      const tvdbEl = document.getElementById('vault-tvdb');
      if (vs.tvdb_user_override) {
        tvdbEl.className = 'pill info';
        tvdbEl.textContent = t('vault_override');
      } else if (vs.tvdb_builtin_active) {
        tvdbEl.className = 'pill ok';
        tvdbEl.textContent = t('vault_active');
      } else {
        tvdbEl.className = 'pill warn';
        tvdbEl.textContent = t('vault_missing');
      }
    }

    function renderProviders(providers) {
      const grid = document.getElementById('providers-grid');
      if (!providers) return;
      grid.innerHTML = Object.entries(providers).map(([dom, list]) => {
        const items = (list || []).map(p => {
          const ok = p.configured;
          const badge = `<span class="pill ${ok ? 'ok' : 'warn'}">${p.role.toUpperCase()}: ${p.source_id} ${ok ? '✓' : '⚠️'}</span>`;
          return badge;
        }).join(' ');
        return `<div class="vault-box">
          <div class="vault-title">${dom.toUpperCase()}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">${items}</div>
        </div>`;
      }).join('');
    }

    function renderClients(clients) {
      const tb = document.getElementById('clients-tbody');
      if (!clients || !clients.length) {
        tb.innerHTML = `<tr><td colspan="6" style="color:var(--muted)">${t('no_clients')}</td></tr>`;
        return;
      }
      tb.innerHTML = clients.map(c => `
        <tr>
          <td><b>${c.dmd_id}</b><br/><small style="color:var(--muted)">${c.client_ip || 'LAN'}</small></td>
          <td><span class="pill info">${c.domain.toUpperCase()}</span></td>
          <td>${c.width}×${c.height}</td>
          <td><code>${c.video_mode}</code> • <code>${c.color_boost}</code></td>
          <td>${c.ready_title ? `<span class="pill ok">✓ ${c.ready_title}</span>` : (c.preparing ? '<span class="pill warn">⏳ Préparation...</span>' : '—')}</td>
          <td>${c.standby_title ? `<span class="pill info">✓ ${c.standby_title}</span>` : '—'}</td>
        </tr>
      `).join('');
    }

    function renderSeen(stats) {
      const el = document.getElementById('seen-badges');
      let total = 0;
      el.innerHTML = Object.entries(stats || {}).map(([k, v]) => {
        total += Number(v || 0);
        return `<div class="badge"><b>${k.toUpperCase()}</b>: ${v} consumed</div>`;
      }).join('');
      document.getElementById('kpi-seen-val').textContent = String(total);
    }

    async function loadHealthAndConfig() {
      try {
        const [rCfg, rHealth] = await Promise.all([
          fetch('/api/v1/admin/config'),
          fetch('/api/v1/health')
        ]);
        if (rCfg.ok) {
          const d = await rCfg.json();
          const cfg = d.config || {};
          document.getElementById('tmdb_api_key').value = cfg.tmdb_api_key || '';
          document.getElementById('tvdb_api_key').value = cfg.tvdb_api_key || '';
          document.getElementById('screenscraper_user').value = cfg.screenscraper_user || '';
          document.getElementById('screenscraper_pass').value = cfg.screenscraper_pass || '';
          document.getElementById('mqtt_mode').value = cfg.mqtt_mode || 'embedded';
          document.getElementById('mqtt_external_url').value = cfg.mqtt_external_url || '';
          document.getElementById('default_lang').value = cfg.default_lang || 'fr';
          document.getElementById('showcase_api_key').value = cfg.showcase_api_key || '';
          renderVaultStatus(d.vault_status || {});
          renderProviders(d.providers || {});
          renderClients(d.clients || []);
          renderSeen(d.seen_history || {});
        }
        if (rHealth.ok) {
          const h = await rHealth.json();
          document.getElementById('kpi-workers-val').textContent = `${h.active_workers} / ${h.max_workers}`;
          document.getElementById('kpi-queue-sub').textContent = `Queue: ${h.queue_depth}`;
          document.getElementById('kpi-mqtt-val').textContent = `${h.mqtt_mode === 'embedded' ? 'Embedded' : 'External'} :${h.mqtt_port}`;
          document.getElementById('kpi-sessions-sub').textContent = `${h.active_sessions} session(s)`;
          document.getElementById('kpi-jobs-val').textContent = String(h.completed_jobs_total || 0);
          document.getElementById('kpi-avg-sub').textContent = `Avg: ${(h.avg_job_duration_s || 0).toFixed(1)}s`;
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function loadDomainFilters() {
      const dom = document.getElementById('test-domain').value;
      document.getElementById('test-system-wrap').style.display = dom === 'games' ? 'block' : 'none';
      try {
        const r = await fetch(`/api/v1/${dom}/filters?lang=${currentUiLang}`);
        if (!r.ok) return;
        const d = await r.json();
        const sysEl = document.getElementById('test-system');
        sysEl.innerHTML = `<option value="">Tous (All)</option>` + (d.systems || []).map(
          s => `<option value="${s.id}">${s.label} (${s.id})</option>`
        ).join('');
        const genEl = document.getElementById('test-genre');
        genEl.innerHTML = `<option value="">Tous (All)</option>` + (d.genres || []).map(
          g => `<option value="${g.id}">${g.label}</option>`
        ).join('');
        const decEl = document.getElementById('test-decade');
        decEl.innerHTML = `<option value="">Toutes (All)</option>` + (d.decades || []).map(
          dec => `<option value="${dec.id}">${dec.label}</option>`
        ).join('');
      } catch (e) {
        console.error(e);
      }
    }

    async function runLiveTest() {
      const dom = document.getElementById('test-domain').value;
      const sys = document.getElementById('test-system').value;
      const gen = document.getElementById('test-genre').value;
      const dec = document.getElementById('test-decade').value;
      const vmode = document.getElementById('test-video-mode').value;
      const cboost = document.getElementById('test-color-boost').value;
      const [w, h] = document.getElementById('test-res').value.split('x').map(Number);
      const q = document.getElementById('test-query').value.trim();
      const st = document.getElementById('test-status');
      const btn = document.getElementById('btn-run-test');
      btn.disabled = true;
      st.textContent = '⏳ Découverte & transcodage en cours...';

      try {
        let item = null;
        if (q) {
          const r = await fetch(`/api/v1/${dom}/on-demand`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              domain: dom, query: q, lang: currentUiLang, width: w, height: h,
              include_video: false, video_mode: vmode, color_boost: cboost
            })
          });
          if (!r.ok) throw new Error((await r.json()).detail || 'Not found');
          item = await r.json();
        } else {
          const params = new URLSearchParams({
            client_id: 'webui_tester', count: '1', lang: currentUiLang,
            width: String(w), height: String(h), include_video: 'false'
          });
          if (dom === 'games' && sys) params.set('systems', sys);
          if (gen) params.set('genres', gen);
          if (dec) params.set('decade', dec);
          const r = await fetch(`/api/v1/${dom}/random?` + params.toString());
          if (!r.ok) throw new Error('Discovery request failed');
          let batch = await r.json();
          for (let i = 0; i < 30 && (!batch.items || !batch.items.length); i++) {
            await new Promise(res => setTimeout(res, 600));
            const rb = await fetch(`/api/v1/${dom}/batch?session_id=${encodeURIComponent(batch.session_id)}`);
            if (rb.ok) batch = await rb.json();
          }
          if (batch.items && batch.items.length) item = batch.items[0];
        }

        if (!item) throw new Error('Aucun candidat trouvé pour ces critères.');
        document.getElementById('test-preview').classList.add('visible');
        document.getElementById('prev-title').textContent = item.title || '—';
        document.getElementById('prev-badge').textContent = item.badge || dom.toUpperCase();
        document.getElementById('prev-year').textContent = item.year || '—';
        document.getElementById('prev-source').textContent = `Source: ${item.source_used || 'auto'} • ★ ${item.rating || 'N/A'}`;
        document.getElementById('prev-sub').textContent = item.subtitle || '';
        document.getElementById('prev-synopsis').textContent = item.synopsis || '';
        const imgEl = document.getElementById('prev-img');
        if (item.poster_png_url) {
          imgEl.src = item.poster_png_url;
          imgEl.style.display = 'block';
        } else {
          imgEl.style.display = 'none';
        }
        st.textContent = '✓ Aperçu généré !';
        loadHealthAndConfig();
      } catch (err) {
        st.textContent = '⚠️ ' + (err.message || String(err));
      } finally {
        btn.disabled = false;
      }
    }

    async function saveConfig() {
      const payload = {
        tmdb_api_key: document.getElementById('tmdb_api_key').value.trim(),
        tvdb_api_key: document.getElementById('tvdb_api_key').value.trim(),
        screenscraper_user: document.getElementById('screenscraper_user').value.trim(),
        screenscraper_pass: document.getElementById('screenscraper_pass').value.trim(),
        mqtt_mode: document.getElementById('mqtt_mode').value,
        mqtt_external_url: document.getElementById('mqtt_external_url').value.trim(),
        default_lang: document.getElementById('default_lang').value,
        showcase_api_key: document.getElementById('showcase_api_key').value.trim()
      };
      const r = await fetch('/api/v1/admin/config', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (r.ok) {
        ['status-msg-keys', 'status-msg-net'].forEach(id => {
          const el = document.getElementById(id);
          el.textContent = '✓ Saved!';
          setTimeout(() => { el.textContent = ''; }, 3000);
        });
        loadHealthAndConfig();
      }
    }

    async function syncReferentials() {
      const st = document.getElementById('sync-status');
      st.textContent = '⏳ Syncing...';
      try {
        const r = await fetch('/api/v1/admin/referentials/sync', {method: 'POST'});
        if (r.ok) {
          const d = await r.json();
          const counts = d.referential_counts || {};
          document.getElementById('ref-badges').innerHTML = Object.entries(counts).map(
            ([k, v]) => `<div class="badge"><b>${k}</b>: ${v} tokens</div>`
          ).join('');
          st.textContent = '✓ Synchronisé !';
        }
      } catch (e) {
        st.textContent = '⚠️ Erreur de synchronisation';
      }
    }

    async function resetHistory(domain) {
      const q = domain ? `?domain=${domain}` : '';
      await fetch('/api/v1/admin/history/reset' + q, {method: 'POST'});
      loadHealthAndConfig();
    }

    function refreshAll() {
      loadHealthAndConfig();
    }

    setUiLang(currentUiLang);
    loadHealthAndConfig();
    loadDomainFilters();
    setInterval(loadHealthAndConfig, 8000);
  </script>
</body>
</html>
"""
