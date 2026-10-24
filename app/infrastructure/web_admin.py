"""
Centralized Web Administration Dashboard HTML for `dmd_media_showcase` (`http://dmdmediashowcase.local:8890/admin`).
Allows users to configure ScreenScraper & TMDB credentials, MQTT broker mode (`embedded` vs `external`),
and reset `seen_history.json` without any API keys on the DMD engines.
"""

from __future__ import annotations

ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DMD Media Showcase v3.0 — Server Dashboard</title>
  <style>
    :root {
      --bg: #0f1319;
      --card: #181f29;
      --border: #2a3444;
      --accent: #ff6d00;
      --cyan: #00e5ff;
      --text: #e6edf3;
      --muted: #8b949e;
    }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 24px;
    }
    .container { max-width: 900px; margin: 0 auto; }
    header {
      display: flex; justify-content: space-between; align-items: center;
      border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px;
    }
    h1 { margin: 0; font-size: 1.4rem; color: var(--accent); }
    .links a {
      color: var(--cyan); text-decoration: none; margin-left: 16px; font-weight: 600;
    }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 10px; padding: 20px; margin-bottom: 20px;
    }
    .card h2 { margin-top: 0; font-size: 1.1rem; color: var(--cyan); }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 4px; }
    input, select {
      width: 100%; box-sizing: border-box; padding: 9px 12px;
      background: #0b0e14; border: 1px solid var(--border);
      border-radius: 6px; color: var(--text); font-size: 0.92rem;
    }
    button {
      background: var(--accent); color: #fff; border: none; border-radius: 6px;
      padding: 10px 18px; font-weight: 600; cursor: pointer; margin-top: 12px;
    }
    button.secondary { background: #263238; border: 1px solid var(--border); }
    .badges { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 10px; }
    .badge {
      background: #0b0e14; border: 1px solid var(--border);
      border-radius: 6px; padding: 8px 14px; font-size: 0.85rem;
    }
    #status-msg { margin-left: 12px; font-size: 0.9rem; color: #00e676; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>DMD Media Showcase Server v3.0</h1>
        <small style="color:var(--muted)">DDD Architecture • MQTT Retain (:1883) + FFmpeg Clock UDP • Consume-Once</small>
      </div>
      <div class="links">
        <a href="/docs">Swagger API (/docs)</a>
        <a href="/redoc">ReDoc (/redoc)</a>
        <a href="/api/v1/health">Health JSON</a>
      </div>
    </header>

    <div class="card">
      <h2>1. Centralized Scraper Credentials (Primary + Fallback Sources)</h2>
      <p style="color:var(--muted);font-size:0.86rem;margin-top:0">
        Fallback sources (<b>ArcadeItalia</b>, <b>Apple iTunes Trailers</b>, <b>TVMaze</b>, <b>Jikan + AnimeThemes</b>, <b>AniList</b>) work out-of-the-box without keys. Configure optional Primary keys below:
      </p>
      <div class="grid">
        <div>
          <label>TMDB API Key v3 / v4 Read Access Token (Movies &amp; TV Series)</label>
          <input type="password" id="tmdb_api_key" placeholder="TMDB v3 API Key or v4 Bearer Token" />
        </div>
        <div>
          <label>TheTVDB API Key v4 (TV Series, Anime &amp; Movies Translations/Trailers)</label>
          <input type="password" id="tvdb_api_key" placeholder="TheTVDB v4 API Key" />
        </div>
        <div>
          <label>Default Metadata Language</label>
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
          <label>ScreenScraper Username (Primary for Retro Games)</label>
          <input type="text" id="screenscraper_user" placeholder="ScreenScraper SSID" />
        </div>
        <div>
          <label>ScreenScraper Password</label>
          <input type="password" id="screenscraper_pass" placeholder="ScreenScraper Password" />
        </div>
      </div>
      <h2 style="margin-top:20px">2. MQTT Broker Configuration</h2>
      <div class="grid">
        <div>
          <label>MQTT Broker Mode</label>
          <select id="mqtt_mode">
            <option value="embedded">Embedded Broker (:1883 built-in — Recommended)</option>
            <option value="external">External MQTT Broker</option>
          </select>
        </div>
        <div>
          <label>External MQTT Broker URL (if mode = external)</label>
          <input type="text" id="mqtt_external_url" placeholder="mqtt://192.168.1.50:1883" />
        </div>
      </div>
      <button onclick="saveConfig()">Save Server Configuration</button>
      <span id="status-msg"></span>
    </div>

    <div class="card">
      <h2>3. Consume-Once Seen History (seen_history.json)</h2>
      <p style="color:var(--muted);font-size:0.86rem;margin-top:0">
        Every title played on a DMD is immediately recorded so it is never repeated until reset.
      </p>
      <div class="badges" id="seen-badges"></div>
      <div style="margin-top:12px">
        <button class="secondary" onclick="resetHistory('')">Reset All Domains</button>
        <button class="secondary" onclick="resetHistory('games')">Reset Games</button>
        <button class="secondary" onclick="resetHistory('movies')">Reset Movies</button>
        <button class="secondary" onclick="resetHistory('series')">Reset Series</button>
        <button class="secondary" onclick="resetHistory('anime')">Reset Anime</button>
      </div>
    </div>
  </div>

  <script>
    async function loadDashboard() {
      const r = await fetch('/api/v1/admin/config');
      const d = await r.json();
      document.getElementById('tmdb_api_key').value = d.config.tmdb_api_key || '';
      document.getElementById('tvdb_api_key').value = d.config.tvdb_api_key || '';
      document.getElementById('screenscraper_user').value = d.config.screenscraper_user || '';
      document.getElementById('screenscraper_pass').value = d.config.screenscraper_pass || '';
      document.getElementById('mqtt_mode').value = d.config.mqtt_mode || 'embedded';
      document.getElementById('mqtt_external_url').value = d.config.mqtt_external_url || '';
      document.getElementById('default_lang').value = d.config.default_lang || 'fr';
      renderSeen(d.seen_history || {});
    }
    function renderSeen(stats) {
      const el = document.getElementById('seen-badges');
      el.innerHTML = Object.entries(stats).map(
        ([k, v]) => `<div class="badge"><b>${k.toUpperCase()}</b>: ${v} consumed</div>`
      ).join('');
    }
    async function saveConfig() {
      const payload = {
        tmdb_api_key: document.getElementById('tmdb_api_key').value.trim(),
        tvdb_api_key: document.getElementById('tvdb_api_key').value.trim(),
        screenscraper_user: document.getElementById('screenscraper_user').value.trim(),
        screenscraper_pass: document.getElementById('screenscraper_pass').value.trim(),
        mqtt_mode: document.getElementById('mqtt_mode').value,
        mqtt_external_url: document.getElementById('mqtt_external_url').value.trim(),
        default_lang: document.getElementById('default_lang').value
      };
      const r = await fetch('/api/v1/admin/config', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      if (r.ok) {
        document.getElementById('status-msg').textContent = 'Configuration saved!';
        setTimeout(() => document.getElementById('status-msg').textContent = '', 3000);
      }
    }
    async function resetHistory(domain) {
      const url = domain ? `/api/v1/admin/history/reset?domain=${domain}` : '/api/v1/admin/history/reset';
      const r = await fetch(url, {method: 'POST'});
      const d = await r.json();
      renderSeen(d.seen_history || {});
    }
    loadDashboard();
  </script>
</body>
</html>
"""
