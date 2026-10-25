"""
Centralized Multilingual Web Administration Dashboard (`http://dmdmediashowcase.local:8890/` & `/admin`).

Features:
1. Real-Time Overview: Worker pool & Consume-Once transcode telemetry, Embedded MQTT (:1883) status,
   active UDP `DMDF` streams, connected DMD clients (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`) with
   live playback, N (Ready) and N+1 (Standby) Consume-Once slots, and active DDD `[Primary, Fallback]`
   scraper chains.
2. Live Showcase Tester: Interactive domain/system/genre/decade selector, orthogonal `video_mode`
   and `color_boost` selectors, and on-demand card preview.
3. API Keys & Encrypted Vault Status: Shows active status of `.env` / `docker-compose` or CI/CD
   Production Native Vault keys (`ScreenScraper Dev`, `TMDB`, `TheTVDB`) without ever exposing them
   in plaintext, while allowing users to configure their personal ScreenScraper player account
   (`SSID` / password) or optional overrides.
4. Network, MQTT & Maintenance: Default metadata language, MQTT broker mode, live referential sync,
   and selective or full `seen_history.json` reset.
"""

from __future__ import annotations

ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DMD Media Showcase Server — Control Center</title>
  <style>
    :root {
      color-scheme: dark;
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
    .header-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .links { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .links a {
      display: inline-flex; align-items: center; height: 34px;
      color: var(--cyan); text-decoration: none; font-size: 0.82rem; font-weight: 600;
      padding: 0 10px; border-radius: 6px; border: 1px solid rgba(0, 229, 255, 0.25);
      background: rgba(0, 229, 255, 0.06); white-space: nowrap;
    }
    .links a:hover { background: rgba(0, 229, 255, 0.14); }
    .tabs {
      display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px;
      border-bottom: 1px solid var(--border); padding-bottom: 10px;
    }
    .tab-btn {
      background: var(--bg-elev); color: var(--muted); border: 1px solid var(--border);
      padding: 9px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 0.9rem;
      margin: 0; transition: all 0.15s ease;
    }
    .tab-btn:hover { background: #1c2536; color: var(--text); border-color: #3b4d6b; }
    .tab-btn.active,
    .tab-btn.active:hover {
      background: linear-gradient(135deg, var(--accent), #e65100);
      color: #fff; border-color: var(--accent);
      box-shadow: 0 3px 12px rgba(255, 109, 0, 0.3);
    }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    .kpi-grid {
      display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px; margin-bottom: 18px;
    }
    .kpi-card {
      background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px;
      display: flex; flex-direction: column; justify-content: space-between;
    }
    .kpi-label { font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.55px; color: var(--muted); }
    .kpi-val { font-size: 1.48rem; font-weight: 700; color: #fff; margin: 8px 0 4px; }
    .kpi-sub { font-size: 0.8rem; color: var(--cyan); }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 20px; margin-bottom: 18px;
    }
    .card h2 {
      margin: 0 0 8px 0; font-size: 1.06rem; color: var(--cyan);
      display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;
    }
    .card p.desc { color: var(--muted); font-size: 0.86rem; margin: 0 0 16px 0; line-height: 1.45; }

    /* Aligned Form Grids: controls on the same row always align flush regardless of label wrapping */
    .form-grid-2 {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px 20px;
      align-items: end;
    }
    .form-grid-3 {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px 18px;
      align-items: end;
    }
    .providers-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .field {
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      min-width: 0;
      margin: 0;
    }
    .field.span-2 { grid-column: span 2; }
    label {
      display: block;
      font-size: 0.83rem;
      color: var(--muted);
      margin: 0 0 6px 0;
      font-weight: 500;
      line-height: 1.3;
    }
    input, select {
      box-sizing: border-box;
      width: 100%;
      height: 42px;
      margin: 0;
      padding: 0 12px;
      vertical-align: middle;
      background-color: #0b0e14;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text);
      font-family: inherit;
      font-size: 0.9rem;
      line-height: 40px;
      transition: border-color 0.15s ease, opacity 0.15s ease;
    }
    select {
      -webkit-appearance: none;
      -moz-appearance: none;
      appearance: none;
      padding: 0 36px 0 12px;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%238b949e' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 12px center;
      background-size: 14px 14px;
      cursor: pointer;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    select option, option {
      background-color: #131924;
      color: #e6edf3;
    }
    input:focus, select:focus { outline: none; border-color: var(--cyan); }
    input:disabled, select:disabled { opacity: 0.45; cursor: not-allowed; }
    .lang-switch {
      width: auto;
      min-width: 138px;
      height: 34px;
      line-height: 32px;
      margin: 0;
      padding: 0 32px 0 10px;
      font-size: 0.84rem;
      font-weight: 600;
      border-radius: 6px;
    }
    .input-row {
      box-sizing: border-box;
      display: flex;
      gap: 8px;
      align-items: center;
      width: 100%;
      height: 42px;
      margin: 0;
    }
    .input-row input { flex: 1; min-width: 0; height: 42px; margin: 0; }
    button {
      box-sizing: border-box;
      margin: 0;
      vertical-align: middle;
      font-family: inherit;
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 10px 18px;
      font-weight: 600;
      font-size: 0.9rem;
      cursor: pointer;
      transition: background 0.15s ease, border-color 0.15s ease;
    }
    button:hover { background: var(--accent-hover); }
    button:disabled { opacity: 0.55; cursor: wait; }
    button.secondary { background: #222c3c; border: 1px solid var(--border); color: var(--text); }
    button.secondary:hover { background: #2c394f; border-color: #3b4d6b; }
    button.danger { background: rgba(255, 82, 82, 0.16); border: 1px solid var(--red); color: #ff8a80; }
    button.danger:hover { background: rgba(255, 82, 82, 0.28); }
    .icon-btn {
      margin: 0;
      height: 42px;
      min-width: 42px;
      padding: 0 12px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #222c3c;
      border: 1px solid var(--border);
      font-size: 0.85rem;
      border-radius: 8px;
      color: var(--text);
      cursor: pointer;
      flex-shrink: 0;
    }
    .icon-btn:hover { background: #2c394f; border-color: #3b4d6b; }
    .form-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 16px;
    }
    .vault-grid {
      display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px; margin-bottom: 16px;
    }
    .vault-box {
      background: #0d121b; border: 1px solid var(--border); border-radius: 9px; padding: 14px;
    }
    .vault-title { font-weight: 700; font-size: 0.92rem; margin-bottom: 6px; color: #fff; }
    .pill {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 3px 9px; border-radius: 999px; font-size: 0.76rem; font-weight: 600;
    }
    .pill.ok { background: rgba(0, 230, 118, 0.12); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.35); }
    .pill.warn { background: rgba(255, 171, 0, 0.12); color: var(--amber); border: 1px solid rgba(255, 171, 0, 0.35); }
    .pill.info { background: rgba(0, 229, 255, 0.12); color: var(--cyan); border: 1px solid rgba(0, 229, 255, 0.35); }
    .pill.err { background: rgba(255, 82, 82, 0.14); color: var(--red); border: 1px solid rgba(255, 82, 82, 0.4); }
    .last-played { color: var(--muted); font-size: 0.8rem; }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 8px; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
    th { color: var(--muted); font-weight: 600; font-size: 0.76rem; text-transform: uppercase; background: #101622; white-space: nowrap; }
    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.78rem; background: #0d121b; padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border);
    }
    .badges { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
    .badge {
      background: #0d121b; border: 1px solid var(--border);
      border-radius: 8px; padding: 8px 13px; font-size: 0.84rem;
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
      margin: 0; font-size: 0.88rem; font-weight: 600; color: var(--green);
    }

    @media (max-width: 960px) {
      .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .providers-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .vault-grid { grid-template-columns: 1fr; }
      .form-grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 720px) {
      .form-grid-2, .form-grid-3, .kpi-grid, .providers-grid { grid-template-columns: 1fr; }
      .field.span-2 { grid-column: span 1; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-logo">DMD</div>
        <div>
          <h1>DMD Media Showcase Server</h1>
          <div class="subtitle" data-i18n="header_sub">
            DDD Architecture • MQTT Retain (:1883) + Real-Time UDP • Consume-Once
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
          <option value="en">🇬🇧 English</option>
          <option value="fr">🇫🇷 Français</option>
          <option value="es">🇪🇸 Español</option>
        </select>
      </div>
    </header>

    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('overview')" data-i18n="tab_overview">
        1. Overview &amp; DMD Matrices
      </button>
      <button class="tab-btn" onclick="switchTab('runtime')" data-i18n="tab_runtime">
        2. Runtime, Workers &amp; Video Limits
      </button>
      <button class="tab-btn" onclick="switchTab('tester')" data-i18n="tab_tester">
        3. Live Showcase Tester
      </button>
      <button class="tab-btn" onclick="switchTab('keys')" data-i18n="tab_keys">
        4. API Keys &amp; Encrypted Vault
      </button>
      <button class="tab-btn" onclick="switchTab('network')" data-i18n="tab_network">
        5. Network, MQTT &amp; Maintenance
      </button>
    </div>

    <!-- TAB 1: OVERVIEW & CONNECTED DMDS -->
    <div id="tab-overview" class="tab-pane active">
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_workers">Workers &amp; Transcode Queue</div>
          <div class="kpi-val" id="kpi-workers-val">0 / 2</div>
          <div class="kpi-sub" id="kpi-queue-sub">Queue: 0</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_mqtt">MQTT Broker &amp; Sessions</div>
          <div class="kpi-val" id="kpi-mqtt-val">Embedded :1883</div>
          <div class="kpi-sub" id="kpi-sessions-sub">0 DMD client(s)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_jobs">Prepared Jobs &amp; Latency</div>
          <div class="kpi-val" id="kpi-jobs-val">0</div>
          <div class="kpi-sub" id="kpi-avg-sub">Average: 0.0s</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label" data-i18n="kpi_seen">Consumed Titles (Consume-Once)</div>
          <div class="kpi-val" id="kpi-seen-val">0</div>
          <div class="kpi-sub" id="kpi-seen-sub" data-i18n="kpi_seen_sub">Zero repetition guaranteed</div>
        </div>
      </div>

      <div class="card">
        <h2>
          <span data-i18n="dmd_clients_title">Connected DMD Matrices (ESP32 / Raspberry Pi)</span>
          <button class="secondary" style="margin:0;padding:6px 12px;font-size:0.8rem" onclick="refreshAll()" data-i18n="btn_refresh">
            Refresh
          </button>
        </h2>
        <p class="desc" data-i18n="dmd_clients_desc">
          Live view of HUB75 panels registered via MQTT or HTTP Polling, their orthogonal framing/color presets, active broadcast, and pre-computed N (Ready) and N+1 (Standby) slots.
        </p>
        <div style="overflow-x:auto">
          <table>
            <thead>
              <tr>
                <th data-i18n="th_dmd_id">DMD ID</th>
                <th data-i18n="th_domain">Domain</th>
                <th data-i18n="th_geom">Geometry</th>
                <th data-i18n="th_framing">Framing &amp; Colors</th>
                <th data-i18n="th_playing">Now Playing</th>
                <th data-i18n="th_ready">Ready Slot (N)</th>
                <th data-i18n="th_standby">Standby Slot (N+1)</th>
              </tr>
            </thead>
            <tbody id="clients-tbody">
              <tr><td colspan="7" style="color:var(--muted)" data-i18n="no_clients">No active DMD client registered yet.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2 data-i18n="chains_title">DDD Scraper Chains ([Primary, Fallback])</h2>
        <p class="desc" data-i18n="chains_desc">
          Each domain queries its Primary adapter first and seamlessly fails over to its Fallback adapter if unconfigured or rate-limited.
        </p>
        <div class="providers-grid" id="providers-grid"></div>
      </div>
    </div>

    <!-- TAB 2: RUNTIME, WORKERS & VIDEO LIMITS (REAL-TIME HOT RELOAD) -->
    <div id="tab-runtime" class="tab-pane">
      <div class="card">
        <h2>
          <span data-i18n="rt_workers_title">Workers, Concurrency &amp; CPU Protection</span>
          <span id="rt-live-workers-pill" class="pill info">⚡ Auto</span>
        </h2>
        <p class="desc" data-i18n="rt_workers_desc">
          Adjust background transcoding workers, executor isolation, FFmpeg threads, queue capacity, and live log verbosity in real time with zero server restart and zero interruption to active DMD streams.
        </p>
        <div class="form-grid-3">
          <div class="field">
            <label data-i18n="lbl_max_workers">Concurrent Transcode Workers (max_concurrent_jobs)</label>
            <select id="max_concurrent_jobs">
              <option value="0" id="opt-workers-auto" data-i18n="opt_workers_auto">Auto (CPU-bounded — Recommended)</option>
              <option value="1">1</option>
              <option value="2">2</option>
              <option value="3">3</option>
              <option value="4">4</option>
              <option value="5">5</option>
              <option value="6">6</option>
              <option value="7">7</option>
              <option value="8">8</option>
              <option value="9">9</option>
              <option value="10">10</option>
              <option value="11">11</option>
              <option value="12">12</option>
              <option value="13">13</option>
              <option value="14">14</option>
              <option value="15">15</option>
              <option value="16">16</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_pool_mode">Worker Executor Mode (use_process_pool)</label>
            <select id="use_process_pool">
              <option value="true" data-i18n="opt_pool_process">ProcessPoolExecutor (Multi-Core Isolation)</option>
              <option value="false" data-i18n="opt_pool_thread">ThreadPoolExecutor (Shared Memory)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_ffmpeg_threads">FFmpeg / OpenCV Threads per Job (1..8)</label>
            <input type="number" id="ffmpeg_threads_per_job" min="1" max="8" step="1" value="1" />
          </div>
          <div class="field">
            <label data-i18n="lbl_worker_nice">Worker OS CPU Nice Increment (0..19)</label>
            <input type="number" id="worker_nice_increment" min="0" max="19" step="1" value="10" />
          </div>
          <div class="field">
            <label data-i18n="lbl_max_queue">Max Pending Job Queue Size (10..1000)</label>
            <input type="number" id="max_queue_size" min="10" max="1000" step="10" value="128" />
          </div>
          <div class="field">
            <label data-i18n="lbl_log_level">Live Server Log Level (log_level)</label>
            <select id="log_level">
              <option value="DEBUG">DEBUG</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
            </select>
          </div>
        </div>
      </div>

      <div class="card">
        <h2>
          <span data-i18n="rt_video_title">Video Ceiling, Panoramas, Cache &amp; Session Limits</span>
          <span id="rt-live-video-pill" class="pill ok">🎬 ≤ 30.0s @ 30 FPS</span>
        </h2>
        <p class="desc" data-i18n="rt_video_desc">
          Enforce global server ceilings on video trailer duration, frame rate, source download size, panorama scroll duration, default AI framing/colorimetry, and cache TTLs. Lowering video ceilings automatically refreshes oversized standby slots.
        </p>
        <div class="form-grid-3">
          <div class="field">
            <label data-i18n="lbl_video_max_dur">Hard Max Video Duration Ceiling (s — 1.5..120)</label>
            <input type="number" id="video_max_duration_limit_s" min="1.5" max="120" step="0.5" value="30.0" />
          </div>
          <div class="field">
            <label data-i18n="lbl_master_clip_max">Master Pre-Encode Clip Target (s — 1.5..120)</label>
            <input type="number" id="server_master_clip_max_s" min="1.5" max="120" step="0.5" value="30.0" />
          </div>
          <div class="field">
            <label data-i18n="lbl_video_max_fps">Hard Max Video Frame Rate (FPS — 6..60)</label>
            <input type="number" id="video_max_fps_limit" min="6" max="60" step="1" value="30" />
          </div>
          <div class="field">
            <label data-i18n="lbl_panorama_dur">Panorama Vertical Scroll Duration (s — 1.5..15)</label>
            <input type="number" id="panorama_duration_s" min="1.5" max="15" step="0.5" value="4.0" />
          </div>
          <div class="field">
            <label data-i18n="lbl_ytdlp_max_mb">Max Trailer Source Download Size (MB — 10..250)</label>
            <input type="number" id="ytdlp_max_filesize_mb" min="10" max="250" step="5" value="60" />
          </div>
          <div class="field">
            <label data-i18n="lbl_default_vmode">Default Video Framing (default_video_mode)</label>
            <select id="default_video_mode">
              <option value="auto_action" data-i18n="opt_vmode_auto">Auto (Let Me Handle It AI)</option>
              <option value="panorama" data-i18n="opt_vmode_panorama">Panorama (Top-Bottom Scan)</option>
              <option value="center_crop" data-i18n="opt_vmode_crop">Static Center Crop</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_default_cboost">Default LED Color Boost (default_color_boost)</label>
            <select id="default_color_boost">
              <option value="auto" data-i18n="opt_cboost_auto">Auto (Let Me Handle It AI)</option>
              <option value="pixel_art" data-i18n="opt_cboost_pixel">Pixel Art (Crisp Retro)</option>
              <option value="anime" data-i18n="opt_cboost_anime">Anime (Vibrant Cel-Shaded)</option>
              <option value="cinema" data-i18n="opt_cboost_cinema">Cinema (Balanced Contrast &amp; Shadows)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_session_ttl">Inactive HTTP Session TTL (s — 60..86400)</label>
            <input type="number" id="session_ttl_seconds" min="60" max="86400" step="60" value="3600" />
          </div>
          <div class="field">
            <label data-i18n="lbl_batch_ttl">Prepared Batch Cache TTL (s — 30..7200)</label>
            <input type="number" id="batch_ttl_seconds" min="30" max="7200" step="30" value="1800" />
          </div>
        </div>
        <div class="form-grid-2" style="margin-top:14px">
          <div class="field span-2">
            <label data-i18n="lbl_cache_dir">Transcoded Media Cache Directory (cache_dir)</label>
            <input type="text" id="cache_dir" placeholder="/tmp/dmd_showcase_cache" />
          </div>
        </div>
        <div class="form-actions">
          <button onclick="saveConfig()" data-i18n="btn_save_live">Save &amp; Apply in Real Time</button>
          <span id="status-msg-runtime" class="toast"></span>
        </div>
      </div>
    </div>

    <!-- TAB 3: LIVE SHOWCASE TESTER -->
    <div id="tab-tester" class="tab-pane">
      <div class="card">
        <h2 data-i18n="tester_title">Live Showcase Tester &amp; Preview</h2>
        <p class="desc" data-i18n="tester_desc">
          Test official discovery adapters (ScreenScraper, ArcadeItalia, TMDB, Apple iTunes, TVMaze, Jikan/AnimeThemes, AniList) and preview AI framing &amp; LED color boost.
        </p>
        <div class="form-grid-3">
          <div class="field">
            <label data-i18n="lbl_domain">Domain</label>
            <select id="test-domain" onchange="loadDomainFilters()">
              <option value="games" data-i18n="opt_dom_games">🕹️ Retro Games (games)</option>
              <option value="movies" data-i18n="opt_dom_movies">🎬 Movies / Films (movies)</option>
              <option value="series" data-i18n="opt_dom_series">📺 TV Series (series)</option>
              <option value="anime" data-i18n="opt_dom_anime">⛩️ Anime / Japanimation (anime)</option>
            </select>
          </div>
          <div class="field" id="test-system-wrap">
            <label data-i18n="lbl_system">System (Games)</label>
            <select id="test-system"><option value="" data-i18n="opt_all">All</option></select>
          </div>
          <div class="field">
            <label data-i18n="lbl_genre">Genre</label>
            <select id="test-genre"><option value="" data-i18n="opt_all">All</option></select>
          </div>
          <div class="field">
            <label data-i18n="lbl_decade">Decade</label>
            <select id="test-decade"><option value="" data-i18n="opt_all">All</option></select>
          </div>
          <div class="field">
            <label data-i18n="lbl_video_mode">Video Framing Mode (video_mode)</label>
            <select id="test-video-mode">
              <option value="auto_action" data-i18n="opt_vmode_auto">Auto (Let Me Handle It AI)</option>
              <option value="panorama" data-i18n="opt_vmode_panorama">Panorama (Top-Bottom Scan)</option>
              <option value="center_crop" data-i18n="opt_vmode_crop">Static Center Crop</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_color_boost">LED Color Boost (color_boost)</label>
            <select id="test-color-boost">
              <option value="auto" data-i18n="opt_cboost_auto">Auto (Let Me Handle It AI)</option>
              <option value="pixel_art" data-i18n="opt_cboost_pixel">Pixel Art (Crisp Retro)</option>
              <option value="anime" data-i18n="opt_cboost_anime">Anime (Vibrant Cel-Shaded)</option>
              <option value="cinema" data-i18n="opt_cboost_cinema">Cinema (Balanced Contrast &amp; Shadows)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_resolution">DMD Panel Resolution</label>
            <select id="test-res">
              <option value="128x32">128×32 (Standard Marquee)</option>
              <option value="128x64">128×64 (Double Height)</option>
              <option value="64x32">64×32 (Compact)</option>
              <option value="64x64">64×64 (Square)</option>
              <option value="256x64">256×64 (XL Arcade)</option>
            </select>
          </div>
          <div class="field span-2">
            <label data-i18n="lbl_query">Direct Title Search (Optional — leave empty for random filtered pick)</label>
            <input type="text" id="test-query" data-i18n-placeholder="ph_query" placeholder="e.g. Super Street Fighter II Turbo, The Matrix, Cowboy Bebop..." />
          </div>
        </div>
        <div class="form-actions">
          <button onclick="runLiveTest()" id="btn-run-test" data-i18n="btn_run_test">
            Run Discovery &amp; Generate Preview
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
        <h2 data-i18n="vault_title">Built-in &amp; Environment Key Status (Prod Vault / .env / docker-compose)</h2>
        <p class="desc" data-i18n="vault_desc">
          Runtime priority: 1. WebUI Override (server_config.json) → 2. .env / docker-compose.yml → 3. Native _prod_vault binary (CI/CD Prod Release). Developer keys are never exposed in plaintext.
        </p>
        <div class="vault-grid">
          <div class="vault-box">
            <div class="vault-title">ScreenScraper Dev API (devid / devpassword)</div>
            <div id="vault-ss-dev" class="pill warn">Checking...</div>
            <div style="color:var(--muted);font-size:0.78rem;margin-top:8px" data-i18n="vault_ss_note">
              Developer credentials required by ScreenScraper API v2. When absent, the server automatically fails over to ArcadeItalia.
            </div>
          </div>
          <div class="vault-box">
            <div class="vault-title">TMDB API v3/v4 (Movies &amp; TV Series)</div>
            <div id="vault-tmdb" class="pill warn">Checking...</div>
            <div style="color:var(--muted);font-size:0.78rem;margin-top:8px" data-i18n="vault_tmdb_note">
              Default key active via .env or Prod Vault. Can optionally be overridden below with your own personal key.
            </div>
          </div>
          <div class="vault-box">
            <div class="vault-title">TheTVDB API v4 (Series, Anime &amp; Movies)</div>
            <div id="vault-tvdb" class="pill warn">Checking...</div>
            <div style="color:var(--muted);font-size:0.78rem;margin-top:8px" data-i18n="vault_tvdb_note">
              Default key active via .env or Prod Vault for localized synopses and official trailers.
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <h2 data-i18n="user_keys_title">User Credentials &amp; Optional Overrides</h2>
        <p class="desc" data-i18n="user_keys_desc">
          Enter your free ScreenScraper member account (SSID / password) to use your player quota. TMDB and TheTVDB fields are optional when an .env or Prod Vault key is already active.
        </p>
        <div class="form-grid-2">
          <div class="field">
            <label data-i18n="lbl_ss_user">ScreenScraper Player Account — Username (SSID)</label>
            <input type="text" id="screenscraper_user" data-i18n-placeholder="ph_ss_user" placeholder="Your ScreenScraper username (e.g. player1)" />
          </div>
          <div class="field">
            <label data-i18n="lbl_ss_pass">ScreenScraper Player Account — Password</label>
            <div class="input-row">
              <input type="password" id="screenscraper_pass" data-i18n-placeholder="ph_ss_pass" placeholder="ScreenScraper player password" />
              <button type="button" class="icon-btn" onclick="togglePwd('screenscraper_pass')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('screenscraper_pass')">✕</button>
            </div>
          </div>
          <div class="field">
            <label data-i18n="lbl_tmdb_override">Custom TMDB Key (Optional — leave empty to use .env / Prod Vault)</label>
            <div class="input-row">
              <input type="password" id="tmdb_api_key" data-i18n-placeholder="ph_vault_default" placeholder="Uses built-in / .env key when left empty" />
              <button type="button" class="icon-btn" onclick="togglePwd('tmdb_api_key')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('tmdb_api_key')">✕</button>
            </div>
          </div>
          <div class="field">
            <label data-i18n="lbl_tvdb_override">Custom TheTVDB v4 Key (Optional — leave empty to use .env / Prod Vault)</label>
            <div class="input-row">
              <input type="password" id="tvdb_api_key" data-i18n-placeholder="ph_vault_default" placeholder="Uses built-in / .env key when left empty" />
              <button type="button" class="icon-btn" onclick="togglePwd('tvdb_api_key')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('tvdb_api_key')">✕</button>
            </div>
          </div>
        </div>
        <div class="form-actions">
          <button onclick="saveConfig()" data-i18n="btn_save">Save Server Configuration</button>
          <span id="status-msg-keys" class="toast"></span>
        </div>
      </div>
    </div>

    <!-- TAB 5: NETWORK, MQTT & MAINTENANCE -->
    <div id="tab-network" class="tab-pane">
      <div class="card">
        <h2 data-i18n="net_title">Language, MQTT Broker, mDNS &amp; Security Settings</h2>
        <div class="form-grid-2">
          <div class="field">
            <label data-i18n="lbl_default_lang">Default Metadata &amp; Synopsis Language</label>
            <select id="default_lang">
              <option value="fr">Français (fr)</option>
              <option value="en">English (en)</option>
              <option value="es">Español (es)</option>
              <option value="de">Deutsch (de)</option>
              <option value="it">Italiano (it)</option>
              <option value="ja">日本語 (ja)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_mqtt_mode">MQTT Broker Mode</label>
            <select id="mqtt_mode" onchange="syncMqttModeUi()">
              <option value="embedded" data-i18n="opt_mqtt_embedded">Embedded Broker (:1883 built-in — Recommended)</option>
              <option value="external" data-i18n="opt_mqtt_external">External MQTT Broker (Mosquitto / Home Assistant)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_mqtt_port">Embedded MQTT Broker TCP Port (mqtt_port)</label>
            <input type="number" id="mqtt_port" min="1" max="65535" step="1" value="1883" />
          </div>
          <div class="field">
            <label data-i18n="lbl_mqtt_url">External MQTT Broker URL (when mode = external)</label>
            <input type="text" id="mqtt_external_url" data-i18n-placeholder="ph_mqtt_url" placeholder="mqtt://192.168.1.50:1883" />
          </div>
          <div class="field">
            <label data-i18n="lbl_mdns_enabled">LAN mDNS Auto-Discovery (:5353 .local)</label>
            <select id="mdns_enabled" onchange="syncMdnsUi()">
              <option value="true" data-i18n="opt_bool_enabled">Enabled</option>
              <option value="false" data-i18n="opt_bool_disabled">Disabled</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_mdns_hostname">mDNS Hostname (.local)</label>
            <input type="text" id="mdns_hostname" placeholder="dmdmediashowcase" />
          </div>
          <div class="field">
            <label data-i18n="lbl_trust_proxy">Trust Reverse Proxy Headers (X-Forwarded-Proto/For)</label>
            <select id="trust_proxy_headers">
              <option value="true" data-i18n="opt_bool_enabled">Enabled</option>
              <option value="false" data-i18n="opt_bool_disabled">Disabled</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_showcase_key">Optional HTTP/Cloud Access Key (X-Showcase-Key — empty = open LAN)</label>
            <div class="input-row">
              <input type="password" id="showcase_api_key" data-i18n-placeholder="ph_showcase_key" placeholder="Empty = passwordless Home-Lab LAN mode" />
              <button type="button" class="icon-btn" onclick="togglePwd('showcase_api_key')">👁️</button>
              <button type="button" class="icon-btn" onclick="clearField('showcase_api_key')">✕</button>
            </div>
          </div>
        </div>
        <div class="form-actions">
          <button onclick="saveConfig()" data-i18n="btn_save_live">Save &amp; Apply in Real Time</button>
          <span id="status-msg-net" class="toast"></span>
        </div>
      </div>

      <div class="card">
        <h2 data-i18n="ref_title">Upstream Referential Synchronization (Official Catalogs)</h2>
        <p class="desc" data-i18n="ref_desc">
          Force immediate synchronization of upstream genre &amp; system indexes (ArcadeItalia MAME ADB, Libretro+Wikidata, Jikan MAL, AniList GraphQL, TVMaze) and republish retained MQTT catalogs.
        </p>
        <div class="form-actions" style="margin-top:0">
          <button class="secondary" onclick="syncReferentials()" id="btn-sync-ref" data-i18n="btn_sync_ref">
            🔄 Sync Upstream Referentials Now
          </button>
          <span id="sync-status" class="toast"></span>
        </div>
        <div class="badges" id="ref-badges"></div>
      </div>

      <div class="card">
        <h2 data-i18n="seen_title">Consume-Once History (seen_history.json)</h2>
        <p class="desc" data-i18n="seen_desc">
          Every title played on a DMD matrix is immediately marked as consumed to guarantee continuous discovery with zero repetition.
        </p>
        <div class="badges" id="seen-badges"></div>
        <div class="form-actions">
          <button class="danger" onclick="resetHistory('')" data-i18n="btn_reset_all">Reset All Domains</button>
          <button class="secondary" onclick="resetHistory('games')" data-i18n="btn_reset_games">Reset Games</button>
          <button class="secondary" onclick="resetHistory('movies')" data-i18n="btn_reset_movies">Reset Movies</button>
          <button class="secondary" onclick="resetHistory('series')" data-i18n="btn_reset_series">Reset Series</button>
          <button class="secondary" onclick="resetHistory('anime')" data-i18n="btn_reset_anime">Reset Anime</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const I18N = {
      en: {
        header_sub: "DDD Architecture • MQTT Retain (:1883) + Real-Time UDP • Consume-Once",
        status_online: "● Online",
        status_offline: "● Unreachable",
        tab_overview: "1. Overview & DMD Matrices",
        tab_runtime: "2. Runtime, Workers & Video Limits",
        tab_tester: "3. Live Showcase Tester",
        tab_keys: "4. API Keys & Encrypted Vault",
        tab_network: "5. Network, MQTT & Maintenance",
        rt_workers_title: "Workers, Concurrency & CPU Protection",
        rt_workers_desc: "Adjust background transcoding workers, executor isolation, FFmpeg threads, queue capacity, and live log verbosity in real time with zero server restart and zero interruption to active DMD streams.",
        rt_workers_pill_fmt: "⚡ Active: {w} workers • {c} CPU cores • {p}",
        lbl_max_workers: "Concurrent Transcode Workers (max_concurrent_jobs)",
        opt_workers_auto: "Auto (CPU-bounded — Recommended)",
        opt_workers_auto_fmt: "Auto ({w} workers / {c} CPU cores — Recommended)",
        lbl_pool_mode: "Worker Executor Mode (use_process_pool)",
        opt_pool_process: "ProcessPoolExecutor (Multi-Core Isolation)",
        opt_pool_thread: "ThreadPoolExecutor (Shared Memory)",
        lbl_ffmpeg_threads: "FFmpeg / OpenCV Threads per Job (1..8)",
        lbl_worker_nice: "Worker OS CPU Nice Increment (0..19)",
        lbl_max_queue: "Max Pending Job Queue Size (10..1000)",
        lbl_log_level: "Live Server Log Level (log_level)",
        rt_video_title: "Video Ceiling, Panoramas, Cache & Session Limits",
        rt_video_desc: "Enforce global server ceilings on video trailer duration, frame rate, source download size, panorama scroll duration, default AI framing/colorimetry, and cache TTLs. Lowering video ceilings automatically refreshes oversized standby slots.",
        rt_video_pill_fmt: "🎬 ≤ {d}s @ {f} FPS",
        lbl_video_max_dur: "Hard Max Video Duration Ceiling (s — 1.5..120)",
        lbl_master_clip_max: "Master Pre-Encode Clip Target (s — 1.5..120)",
        lbl_video_max_fps: "Hard Max Video Frame Rate (FPS — 6..60)",
        lbl_panorama_dur: "Panorama Vertical Scroll Duration (s — 1.5..15)",
        lbl_ytdlp_max_mb: "Max Trailer Source Download Size (MB — 10..250)",
        lbl_default_vmode: "Default Video Framing (default_video_mode)",
        lbl_default_cboost: "Default LED Color Boost (default_color_boost)",
        lbl_session_ttl: "Inactive HTTP Session TTL (s — 60..86400)",
        lbl_batch_ttl: "Prepared Batch Cache TTL (s — 30..7200)",
        lbl_cache_dir: "Transcoded Media Cache Directory (cache_dir)",
        lbl_mqtt_port: "Embedded MQTT Broker TCP Port (mqtt_port)",
        lbl_mdns_enabled: "LAN mDNS Auto-Discovery (:5353 .local)",
        lbl_mdns_hostname: "mDNS Hostname (.local)",
        lbl_trust_proxy: "Trust Reverse Proxy Headers (X-Forwarded-Proto/For)",
        opt_bool_enabled: "Enabled",
        opt_bool_disabled: "Disabled",
        btn_save_live: "Save & Apply in Real Time",
        msg_saved_live: "✓ Applied live ({w} workers • ≤{d}s @ {f}fps)",
        kpi_workers: "Workers & Transcode Queue",
        kpi_queue_fmt: "Queue: {q}",
        kpi_queue_udp_fmt: "Queue: {q} • UDP Live: {u}",
        kpi_sub_udp_live: "📡 UDP Live: {u} • Ready: {r} • Queue: {q}",
        kpi_sub_playing: "▶ Playing: {p} • Ready: {r} • Queue: {q}",
        kpi_sub_ready_slots: "Queue: {q} • Pre-warmed: {r}",
        kpi_mqtt: "MQTT Broker & Sessions",
        kpi_mqtt_embedded: "Embedded",
        kpi_mqtt_external: "External",
        kpi_clients_zero: "0 DMD client(s)",
        kpi_clients_fmt: "{c} DMD client(s) • {s} slot(s)",
        kpi_jobs: "Prepared Jobs & Latency",
        kpi_avg_fmt: "Average: {a}s",
        kpi_seen: "Consumed Titles (Consume-Once)",
        kpi_seen_sub: "Zero repetition guaranteed",
        dmd_clients_title: "Connected DMD Matrices (ESP32 / Raspberry Pi)",
        dmd_clients_desc: "Live view of HUB75 panels registered via MQTT or HTTP Polling, their orthogonal framing/color presets, active broadcast, and pre-computed N (Ready) and N+1 (Standby) slots.",
        btn_refresh: "Refresh",
        th_dmd_id: "DMD ID",
        th_domain: "Domain",
        th_geom: "Geometry",
        th_framing: "Framing & Colors",
        th_playing: "Now Playing",
        th_ready: "Ready Slot (N)",
        th_standby: "Standby Slot (N+1)",
        no_clients: "No active DMD client registered yet.",
        pill_preparing: "⏳ Preparing...",
        pill_streaming: "📡 UDP:",
        chains_title: "DDD Scraper Chains ([Primary, Fallback])",
        chains_desc: "Each domain queries its Primary adapter first and seamlessly fails over to its Fallback adapter if unconfigured or rate-limited.",
        role_primary: "PRIMARY",
        role_fallback: "FALLBACK",
        tester_title: "Live Showcase Tester & Preview",
        tester_desc: "Test official discovery adapters (ScreenScraper, ArcadeItalia, TMDB, Apple iTunes, TVMaze, Jikan/AnimeThemes, AniList) and preview AI framing & LED color boost.",
        lbl_domain: "Domain",
        opt_dom_games: "🕹️ Retro Games (games)",
        opt_dom_movies: "🎬 Movies / Films (movies)",
        opt_dom_series: "📺 TV Series (series)",
        opt_dom_anime: "⛩️ Anime / Japanimation (anime)",
        lbl_system: "System (Games)",
        lbl_genre: "Genre",
        lbl_decade: "Decade",
        opt_all: "All",
        opt_na: "N/A (Games only)",
        lbl_video_mode: "Video Framing Mode (video_mode)",
        opt_vmode_auto: "Auto (Let Me Handle It AI)",
        opt_vmode_panorama: "Panorama (Top-Bottom Scan)",
        opt_vmode_crop: "Static Center Crop",
        lbl_color_boost: "LED Color Boost (color_boost)",
        opt_cboost_auto: "Auto (Let Me Handle It AI)",
        opt_cboost_pixel: "Pixel Art (Crisp Retro)",
        opt_cboost_anime: "Anime (Vibrant Cel-Shaded)",
        opt_cboost_cinema: "Cinema (Balanced Contrast & Shadows)",
        lbl_resolution: "DMD Panel Resolution",
        lbl_query: "Direct Title Search (Optional — leave empty for random filtered pick)",
        ph_query: "e.g. Super Street Fighter II Turbo, The Matrix, Cowboy Bebop...",
        btn_run_test: "Run Discovery & Generate Preview",
        msg_testing: "⏳ Running discovery & transcoding...",
        msg_no_candidate: "No candidate found for these criteria.",
        msg_preview_ok: "✓ Preview generated!",
        vault_title: "Built-in & Environment Key Status (Prod Vault / .env / docker-compose)",
        vault_desc: "Runtime priority: 1. WebUI Override (server_config.json) → 2. .env / docker-compose.yml → 3. Native _prod_vault binary (CI/CD Prod Release). Developer keys are never exposed in plaintext.",
        vault_ss_note: "Developer credentials required by ScreenScraper API v2. When absent, the server automatically fails over to ArcadeItalia.",
        vault_tmdb_note: "Default key active via .env or Prod Vault. Can optionally be overridden below with your own personal key.",
        vault_tvdb_note: "Default key active via .env or Prod Vault for localized synopses and official trailers.",
        user_keys_title: "User Credentials & Optional Overrides",
        user_keys_desc: "Enter your free ScreenScraper member account (SSID / password) to use your player quota. TMDB and TheTVDB fields are optional when an .env or Prod Vault key is already active.",
        lbl_ss_user: "ScreenScraper Player Account — Username (SSID)",
        ph_ss_user: "Your ScreenScraper username (e.g. player1)",
        lbl_ss_pass: "ScreenScraper Player Account — Password",
        ph_ss_pass: "ScreenScraper player password",
        lbl_tmdb_override: "Custom TMDB Key (Optional — leave empty to use .env / Prod Vault)",
        lbl_tvdb_override: "Custom TheTVDB v4 Key (Optional — leave empty to use .env / Prod Vault)",
        ph_vault_default: "Uses built-in / .env key when left empty",
        net_title: "Language, MQTT Broker & Security Settings",
        lbl_default_lang: "Default Metadata & Synopsis Language",
        lbl_mqtt_mode: "MQTT Broker Mode",
        opt_mqtt_embedded: "Embedded Broker (:1883 built-in — Recommended)",
        opt_mqtt_external: "External MQTT Broker (Mosquitto / Home Assistant)",
        lbl_mqtt_url: "External MQTT Broker URL (when mode = external)",
        ph_mqtt_url: "mqtt://192.168.1.50:1883",
        ph_mqtt_embedded: "Managed automatically by Embedded Broker (:1883)",
        lbl_showcase_key: "Optional HTTP/Cloud Access Key (X-Showcase-Key — empty = open LAN)",
        ph_showcase_key: "Empty = passwordless Home-Lab LAN mode",
        btn_save: "Save Server Configuration",
        msg_saved: "✓ Configuration saved!",
        ref_title: "Upstream Referential Synchronization (Official Catalogs)",
        ref_desc: "Force immediate synchronization of upstream genre & system indexes (ArcadeItalia MAME ADB, Libretro+Wikidata, Jikan MAL, AniList GraphQL, TVMaze) and republish retained MQTT catalogs.",
        btn_sync_ref: "🔄 Sync Upstream Referentials Now",
        msg_syncing: "⏳ Syncing...",
        msg_synced: "✓ Synchronized!",
        msg_sync_err: "⚠️ Synchronization error",
        seen_title: "Consume-Once History (seen_history.json)",
        seen_desc: "Every title played on a DMD matrix is immediately marked as consumed to guarantee continuous discovery with zero repetition.",
        seen_badge_fmt: "{v} consumed",
        btn_reset_all: "Reset All Domains",
        btn_reset_games: "Reset Games",
        btn_reset_movies: "Reset Movies",
        btn_reset_series: "Reset Series",
        btn_reset_anime: "Reset Anime",
        vault_active: "🔒 Built-in / Env Key Active",
        vault_override: "🛠️ Custom User Override Active",
        vault_missing: "⚠️ Unconfigured (Fallback active)"
      },
      fr: {
        header_sub: "Architecture DDD • MQTT Retain (:1883) + UDP Temps Réel • Consume-Once",
        status_online: "● En ligne",
        status_offline: "● Injoignable",
        tab_overview: "1. Vue d'ensemble & Matrices DMD",
        tab_runtime: "2. Runtime, Workers & Limites Vidéo",
        tab_tester: "3. Testeur & Aperçu Showcase",
        tab_keys: "4. Clés API & Coffre Chiffré",
        tab_network: "5. Réseau, MQTT & Maintenance",
        rt_workers_title: "Workers, Parallélisme & Protection CPU",
        rt_workers_desc: "Modifie à chaud le nombre de workers de transcodage, le mode d'exécution, les threads FFmpeg, la file d'attente et le niveau de log sans redémarrer le serveur ni couper les flux DMD actifs.",
        rt_workers_pill_fmt: "⚡ Actifs : {w} workers • {c} cœurs CPU • {p}",
        lbl_max_workers: "Workers de Transcodage Simultanés (max_concurrent_jobs)",
        opt_workers_auto: "Auto (Borné CPU — Recommandé)",
        opt_workers_auto_fmt: "Auto ({w} workers / {c} cœurs CPU — Recommandé)",
        lbl_pool_mode: "Mode d'Exécuteur Worker (use_process_pool)",
        opt_pool_process: "ProcessPoolExecutor (Isolation Multi-Cœurs)",
        opt_pool_thread: "ThreadPoolExecutor (Mémoire Partagée)",
        lbl_ffmpeg_threads: "Threads FFmpeg / OpenCV par Job (1..8)",
        lbl_worker_nice: "Priorité OS Nice des Workers (0..19)",
        lbl_max_queue: "Taille Max de la File de Jobs (10..1000)",
        lbl_log_level: "Niveau de Log Serveur en Direct (log_level)",
        rt_video_title: "Plafonds Vidéo, Panoramas, Cache & Sessions",
        rt_video_desc: "Applique en temps réel les plafonds serveur sur la durée maximale des vidéos, les FPS, le poids source, le défilement des panoramas, les réglages IA par défaut et les TTL de cache. Abaisser une limite vidéo recalcule automatiquement les slots en attente.",
        rt_video_pill_fmt: "🎬 ≤ {d}s @ {f} FPS",
        lbl_video_max_dur: "Durée Vidéo Maximale Autorisée (s — 1.5..120)",
        lbl_master_clip_max: "Durée Cible Clip Master Pré-encodé (s — 1.5..120)",
        lbl_video_max_fps: "Fréquence Vidéo Maximale (FPS — 6..60)",
        lbl_panorama_dur: "Durée de Balayage Vertical Panorama (s — 1.5..15)",
        lbl_ytdlp_max_mb: "Poids Max Téléchargement Bande-Annonce (Mo — 10..250)",
        lbl_default_vmode: "Cadrage Vidéo par Défaut (default_video_mode)",
        lbl_default_cboost: "Boost Couleur LED par Défaut (default_color_boost)",
        lbl_session_ttl: "TTL Session HTTP Inactive (s — 60..86400)",
        lbl_batch_ttl: "TTL Cache Batch Préparé (s — 30..7200)",
        lbl_cache_dir: "Répertoire de Cache Média Transcodé (cache_dir)",
        lbl_mqtt_port: "Port TCP du Broker MQTT Embarqué (mqtt_port)",
        lbl_mdns_enabled: "Découverte Auto LAN mDNS (:5353 .local)",
        lbl_mdns_hostname: "Nom d'Hôte mDNS (.local)",
        lbl_trust_proxy: "Confiance Reverse Proxy (X-Forwarded-Proto/For)",
        opt_bool_enabled: "Activé",
        opt_bool_disabled: "Désactivé",
        btn_save_live: "Enregistrer & Appliquer en Temps Réel",
        msg_saved_live: "✓ Appliqué à chaud ({w} workers • ≤{d}s @ {f}fps)",
        kpi_workers: "Workers & File de Transcodage",
        kpi_queue_fmt: "File d'attente : {q}",
        kpi_queue_udp_fmt: "File : {q} • Flux UDP : {u}",
        kpi_sub_udp_live: "📡 Flux UDP : {u} • Prêts : {r} • File : {q}",
        kpi_sub_playing: "▶ En diffusion : {p} • Prêts : {r} • File : {q}",
        kpi_sub_ready_slots: "File : {q} • Pré-calculés : {r}",
        kpi_mqtt: "Broker MQTT & Sessions",
        kpi_mqtt_embedded: "Embarqué",
        kpi_mqtt_external: "Externe",
        kpi_clients_zero: "0 client(s) DMD",
        kpi_clients_fmt: "{c} client(s) DMD • {s} slot(s)",
        kpi_jobs: "Jobs Préparés & Latence",
        kpi_avg_fmt: "Moyenne : {a}s",
        kpi_seen: "Titres Consommés (Consume-Once)",
        kpi_seen_sub: "Zéro répétition garantie",
        dmd_clients_title: "Matrices DMD Connectées (ESP32 / Raspberry Pi)",
        dmd_clients_desc: "Affiche en temps réel les panneaux HUB75 enregistrés via MQTT ou HTTP Polling, leurs réglages orthogonaux de cadrage/couleur, la diffusion en cours et leurs slots pré-calculés N (Ready) et N+1 (Standby).",
        btn_refresh: "Actualiser",
        th_dmd_id: "DMD ID",
        th_domain: "Domaine",
        th_geom: "Géométrie",
        th_framing: "Cadrage & Couleurs",
        th_playing: "En Diffusion",
        th_ready: "Slot Prêt (N)",
        th_standby: "Slot Standby (N+1)",
        no_clients: "Aucun client DMD actif pour le moment.",
        pill_preparing: "⏳ Préparation...",
        pill_streaming: "📡 UDP :",
        chains_title: "Chaînes de Scrapers DDD ([Primary, Fallback])",
        chains_desc: "Chaque domaine utilise sa source Primaire et bascule automatiquement sans interruption sur sa source Fallback en cas de quota atteint ou d'absence de clé.",
        role_primary: "PRIMAIRE",
        role_fallback: "FALLBACK",
        tester_title: "Testeur & Aperçu Live Showcase",
        tester_desc: "Teste immédiatement la découverte officielle (ScreenScraper, ArcadeItalia, TMDB, Apple iTunes, TVMaze, Jikan/AnimeThemes, AniList) et vérifie le cadrage IA Let Me Handle It ou le Boost Colorimétrique.",
        lbl_domain: "Domaine",
        opt_dom_games: "🕹️ Jeux Rétro (games)",
        opt_dom_movies: "🎬 Films / Cinéma (movies)",
        opt_dom_series: "📺 Séries TV (series)",
        opt_dom_anime: "⛩️ Anime / Japanimation (anime)",
        lbl_system: "Système (Jeux)",
        lbl_genre: "Genre",
        lbl_decade: "Décennie",
        opt_all: "Tous (All)",
        opt_na: "N/A (Jeux uniquement)",
        lbl_video_mode: "Mode de Cadrage Vidéo (video_mode)",
        opt_vmode_auto: "Auto (IA Let Me Handle It)",
        opt_vmode_panorama: "Panorama (Balayage Haut-Bas)",
        opt_vmode_crop: "Recadrage Centré Fixe",
        lbl_color_boost: "Boost Colorimétrique LED (color_boost)",
        opt_cboost_auto: "Auto (IA Let Me Handle It)",
        opt_cboost_pixel: "Pixel Art (Rétro Net)",
        opt_cboost_anime: "Anime (Cel-Shading Vibrant)",
        opt_cboost_cinema: "Cinéma (Contraste & Ombres)",
        lbl_resolution: "Résolution Panneau DMD",
        lbl_query: "Recherche directe par titre (Optionnel — vide = tirage aléatoire filtré)",
        ph_query: "Ex : Super Street Fighter II Turbo, The Matrix, Cowboy Bebop...",
        btn_run_test: "Lancer la Découverte & Générer l'Aperçu",
        msg_testing: "⏳ Découverte & transcodage en cours...",
        msg_no_candidate: "Aucun candidat trouvé pour ces critères.",
        msg_preview_ok: "✓ Aperçu généré !",
        vault_title: "État des Clés Embarquées (Prod Vault / .env / docker-compose)",
        vault_desc: "Priorité d'exécution : 1. Surcharge WebUI (server_config.json) → 2. Variables .env / docker-compose.yml → 3. Binaire natif chiffré _prod_vault (Release Prod CI/CD). Les clés dev ne sont jamais exposées en clair.",
        vault_ss_note: "Identifiants développeur requis par l'API v2 ScreenScraper. Tant qu'ils sont vides, le serveur utilise automatiquement le Fallback ArcadeItalia.",
        vault_tmdb_note: "Clé active par défaut (.env / Prod Vault). Peut être surchargée ci-dessous par une clé personnelle.",
        vault_tvdb_note: "Clé active par défaut (.env / Prod Vault) pour les traductions et bandes-annonces officielles.",
        user_keys_title: "Identifiants Utilisateur & Surcharges Optionnelles",
        user_keys_desc: "Renseigne ton compte membre gratuit ScreenScraper (SSID / mot de passe) pour associer ton quota joueur. Les champs TMDB et TheTVDB sont optionnels si une clé .env ou Prod Vault est déjà active.",
        lbl_ss_user: "Compte Joueur ScreenScraper — Identifiant (SSID)",
        ph_ss_user: "Ton pseudo ScreenScraper (ex : player1)",
        lbl_ss_pass: "Compte Joueur ScreenScraper — Mot de passe",
        ph_ss_pass: "Mot de passe joueur ScreenScraper",
        lbl_tmdb_override: "Clé TMDB Personnelle (Optionnel — vide = utilise .env / Prod Vault)",
        lbl_tvdb_override: "Clé TheTVDB v4 Personnelle (Optionnel — vide = utilise .env / Prod Vault)",
        ph_vault_default: "Utilise la clé embarquée/.env si laissé vide",
        net_title: "Paramètres Langue, Broker MQTT & Sécurité",
        lbl_default_lang: "Langue par défaut des métadonnées & synopsis",
        lbl_mqtt_mode: "Mode du Broker MQTT",
        opt_mqtt_embedded: "Broker Embarqué (:1883 intégré — Recommandé)",
        opt_mqtt_external: "Broker MQTT Externe (Mosquitto / Home Assistant)",
        lbl_mqtt_url: "URL du Broker MQTT Externe (si mode = external)",
        ph_mqtt_url: "mqtt://192.168.1.50:1883",
        ph_mqtt_embedded: "Géré automatiquement par le Broker Embarqué (:1883)",
        lbl_showcase_key: "Clé d'accès HTTP/Cloud optionnelle (X-Showcase-Key — vide = ouvert LAN)",
        ph_showcase_key: "Vide = mode Home-Lab sans mot de passe",
        btn_save: "Enregistrer la Configuration",
        msg_saved: "✓ Configuration enregistrée !",
        ref_title: "Synchronisation des Référentiels Amont (Catalogues Officiels)",
        ref_desc: "Force le rafraîchissement immédiat des index de genres et systèmes amont (ArcadeItalia MAME ADB, Libretro+Wikidata, Jikan MAL, AniList GraphQL, TVMaze) et republie le catalogue MQTT Retain.",
        btn_sync_ref: "🔄 Synchroniser les Référentiels Maintenant",
        msg_syncing: "⏳ Synchronisation...",
        msg_synced: "✓ Synchronisé !",
        msg_sync_err: "⚠️ Erreur de synchronisation",
        seen_title: "Historique Consume-Once (seen_history.json)",
        seen_desc: "Chaque œuvre diffusée sur une matrice DMD est immédiatement marquée comme vue pour garantir un renouvellement permanent sans répétition.",
        seen_badge_fmt: "{v} consommé(s)",
        btn_reset_all: "Purger Tous les Domaines",
        btn_reset_games: "Purger Games",
        btn_reset_movies: "Purger Movies",
        btn_reset_series: "Purger Series",
        btn_reset_anime: "Purger Anime",
        vault_active: "🔒 Clé Active (.env / Prod Vault)",
        vault_override: "🛠️ Surcharge Utilisateur Active",
        vault_missing: "⚠️ Non configurée (Fallback actif)"
      },
      es: {
        header_sub: "Arquitectura DDD • MQTT Retain (:1883) + UDP en Tiempo Real • Consume-Once",
        status_online: "● En línea",
        status_offline: "● Sin conexión",
        tab_overview: "1. Vista General y Matrices DMD",
        tab_runtime: "2. Runtime, Workers y Límites de Vídeo",
        tab_tester: "3. Probador Showcase en Vivo",
        tab_keys: "4. Claves API y Bóveda Cifrada",
        tab_network: "5. Red, MQTT y Mantenimiento",
        rt_workers_title: "Workers, Concurrencia y Protección CPU",
        rt_workers_desc: "Ajusta en caliente los workers de transcodificación, el modo de ejecutor, hilos FFmpeg, cola máxima y nivel de registro sin reiniciar el servidor ni interrumpir emisiones DMD activas.",
        rt_workers_pill_fmt: "⚡ Activos: {w} workers • {c} núcleos CPU • {p}",
        lbl_max_workers: "Workers de Transcodificación Simultáneos (max_concurrent_jobs)",
        opt_workers_auto: "Auto (Limitado por CPU — Recomendado)",
        opt_workers_auto_fmt: "Auto ({w} workers / {c} núcleos CPU — Recomendado)",
        lbl_pool_mode: "Modo de Ejecutor Worker (use_process_pool)",
        opt_pool_process: "ProcessPoolExecutor (Aislamiento Multinúcleo)",
        opt_pool_thread: "ThreadPoolExecutor (Memoria Compartida)",
        lbl_ffmpeg_threads: "Hilos FFmpeg / OpenCV por Trabajo (1..8)",
        lbl_worker_nice: "Prioridad OS Nice de Workers (0..19)",
        lbl_max_queue: "Tamaño Máximo de Cola de Trabajos (10..1000)",
        lbl_log_level: "Nivel de Registro en Vivo (log_level)",
        rt_video_title: "Techos de Vídeo, Panoramas, Caché y Sesiones",
        rt_video_desc: "Aplica en tiempo real límites globales de duración máxima de vídeo, FPS, tamaño de descarga, barrido de panoramas, ajustes IA por defecto y TTLs de caché. Reducir límites regenera automáticamente ranuras en espera.",
        rt_video_pill_fmt: "🎬 ≤ {d}s @ {f} FPS",
        lbl_video_max_dur: "Duración Máxima de Vídeo Permitida (s — 1.5..120)",
        lbl_master_clip_max: "Objetivo de Clip Maestro Precodificado (s — 1.5..120)",
        lbl_video_max_fps: "Frecuencia Máxima de Vídeo (FPS — 6..60)",
        lbl_panorama_dur: "Duración de Barrido Vertical Panorama (s — 1.5..15)",
        lbl_ytdlp_max_mb: "Tamaño Máximo de Descarga de Tráiler (MB — 10..250)",
        lbl_default_vmode: "Encuadre de Vídeo por Defecto (default_video_mode)",
        lbl_default_cboost: "Realce LED por Defecto (default_color_boost)",
        lbl_session_ttl: "TTL de Sesión HTTP Inactiva (s — 60..86400)",
        lbl_batch_ttl: "TTL de Caché de Lotes Preparados (s — 30..7200)",
        lbl_cache_dir: "Directorio de Caché Multimedia (cache_dir)",
        lbl_mqtt_port: "Puerto TCP del Broker MQTT Integrado (mqtt_port)",
        lbl_mdns_enabled: "Autodescubrimiento LAN mDNS (:5353 .local)",
        lbl_mdns_hostname: "Nombre de Host mDNS (.local)",
        lbl_trust_proxy: "Confiar en Cabeceras Proxy (X-Forwarded-Proto/For)",
        opt_bool_enabled: "Activado",
        opt_bool_disabled: "Desactivado",
        btn_save_live: "Guardar y Aplicar en Tiempo Real",
        msg_saved_live: "✓ Aplicado en vivo ({w} workers • ≤{d}s @ {f}fps)",
        kpi_workers: "Workers y Cola de Transcodificación",
        kpi_queue_fmt: "Cola: {q}",
        kpi_queue_udp_fmt: "Cola: {q} • UDP Activo: {u}",
        kpi_sub_udp_live: "📡 UDP Activo: {u} • Listos: {r} • Cola: {q}",
        kpi_sub_playing: "▶ En emisión: {p} • Listos: {r} • Cola: {q}",
        kpi_sub_ready_slots: "Cola: {q} • Precalculados: {r}",
        kpi_mqtt: "Broker MQTT y Sesiones",
        kpi_mqtt_embedded: "Integrado",
        kpi_mqtt_external: "Externo",
        kpi_clients_zero: "0 cliente(s) DMD",
        kpi_clients_fmt: "{c} cliente(s) DMD • {s} ranura(s)",
        kpi_jobs: "Trabajos Preparados y Latencia",
        kpi_avg_fmt: "Promedio: {a}s",
        kpi_seen: "Títulos Consumidos (Consume-Once)",
        kpi_seen_sub: "Cero repeticiones garantizado",
        dmd_clients_title: "Matrices DMD Conectadas (ESP32 / Raspberry Pi)",
        dmd_clients_desc: "Vista en tiempo real de paneles HUB75 registrados por MQTT o HTTP Polling, sus ajustes de encuadre/color, emisión activa y ranuras N (Ready) y N+1 (Standby).",
        btn_refresh: "Actualizar",
        th_dmd_id: "DMD ID",
        th_domain: "Dominio",
        th_geom: "Geometría",
        th_framing: "Encuadre y Color",
        th_playing: "En Emisión",
        th_ready: "Ranura Lista (N)",
        th_standby: "Ranura Standby (N+1)",
        no_clients: "Ningún cliente DMD activo por ahora.",
        pill_preparing: "⏳ Preparando...",
        pill_streaming: "📡 UDP:",
        chains_title: "Cadenas de Scrapers DDD ([Primary, Fallback])",
        chains_desc: "Cada dominio consulta primero su fuente Primaria y conmuta automáticamente a su fuente Fallback si falta clave o límite.",
        role_primary: "PRIMARIA",
        role_fallback: "FALLBACK",
        tester_title: "Probador y Vista Previa en Vivo",
        tester_desc: "Prueba los adaptadores oficiales (ScreenScraper, ArcadeItalia, TMDB, Apple iTunes, TVMaze, Jikan/AnimeThemes, AniList) y el encuadre IA y realce LED.",
        lbl_domain: "Dominio",
        opt_dom_games: "🕹️ Juegos Retro (games)",
        opt_dom_movies: "🎬 Películas / Cine (movies)",
        opt_dom_series: "📺 Series de TV (series)",
        opt_dom_anime: "⛩️ Anime / Animación Japonesa (anime)",
        lbl_system: "Sistema (Juegos)",
        lbl_genre: "Género",
        lbl_decade: "Década",
        opt_all: "Todos (All)",
        opt_na: "N/A (Solo juegos)",
        lbl_video_mode: "Modo de Encuadre de Vídeo (video_mode)",
        opt_vmode_auto: "Auto (IA Let Me Handle It)",
        opt_vmode_panorama: "Panorama (Barrido Arriba-Abajo)",
        opt_vmode_crop: "Recorte Central Fijo",
        lbl_color_boost: "Realce de Color LED (color_boost)",
        opt_cboost_auto: "Auto (IA Let Me Handle It)",
        opt_cboost_pixel: "Pixel Art (Retro Nítido)",
        opt_cboost_anime: "Anime (Cel-Shaded Vibrante)",
        opt_cboost_cinema: "Cine (Contraste y Sombras)",
        lbl_resolution: "Resolución del Panel DMD",
        lbl_query: "Búsqueda directa por título (Opcional — vacío = selección aleatoria filtrada)",
        ph_query: "Ej: Super Street Fighter II Turbo, The Matrix, Cowboy Bebop...",
        btn_run_test: "Lanzar Descubrimiento y Vista Previa",
        msg_testing: "⏳ Descubriendo y transcodificando...",
        msg_no_candidate: "Ningún candidato encontrado con estos criterios.",
        msg_preview_ok: "✓ ¡Vista previa generada!",
        vault_title: "Estado de Claves Integradas (Prod Vault / .env / docker-compose)",
        vault_desc: "Prioridad en ejecución: 1. WebUI (server_config.json) → 2. .env / docker-compose.yml → 3. Binario nativo _prod_vault (Release Prod CI/CD).",
        vault_ss_note: "Credenciales de desarrollador requeridas por ScreenScraper API v2. Si faltan, usa automáticamente ArcadeItalia.",
        vault_tmdb_note: "Clave activa por defecto (.env / Prod Vault). Puede sustituirse abajo con tu clave personal.",
        vault_tvdb_note: "Clave activa por defecto (.env / Prod Vault) para sinopsis traducidas y tráilers oficiales.",
        user_keys_title: "Credenciales de Usuario y Anulaciones Opcionales",
        user_keys_desc: "Introduce tu cuenta gratuita de ScreenScraper (SSID / contraseña). Los campos TMDB y TheTVDB son opcionales si ya hay una clave activa.",
        lbl_ss_user: "Cuenta de Jugador ScreenScraper — Usuario (SSID)",
        ph_ss_user: "Tu usuario de ScreenScraper (ej: player1)",
        lbl_ss_pass: "Cuenta de Jugador ScreenScraper — Contraseña",
        ph_ss_pass: "Contraseña de jugador ScreenScraper",
        lbl_tmdb_override: "Clave TMDB Personal (Opcional — vacío = usa .env / Prod Vault)",
        lbl_tvdb_override: "Clave TheTVDB v4 Personal (Opcional — vacío = usa .env / Prod Vault)",
        ph_vault_default: "Usa la clave integrada/.env si se deja vacío",
        net_title: "Idioma, Broker MQTT y Seguridad",
        lbl_default_lang: "Idioma por defecto de metadatos y sinopsis",
        lbl_mqtt_mode: "Modo del Broker MQTT",
        opt_mqtt_embedded: "Broker Integrado (:1883 incluido — Recomendado)",
        opt_mqtt_external: "Broker MQTT Externo (Mosquitto / Home Assistant)",
        lbl_mqtt_url: "URL del Broker MQTT Externo (cuando modo = external)",
        ph_mqtt_url: "mqtt://192.168.1.50:1883",
        ph_mqtt_embedded: "Gestionado automáticamente por el Broker Integrado (:1883)",
        lbl_showcase_key: "Clave opcional de acceso HTTP/Cloud (X-Showcase-Key — vacío = LAN abierta)",
        ph_showcase_key: "Vacío = modo Home-Lab sin contraseña",
        btn_save: "Guardar Configuración",
        msg_saved: "✓ ¡Configuración guardada!",
        ref_title: "Sincronización de Referenciales Oficiales",
        ref_desc: "Fuerza la actualización inmediata de índices de géneros y sistemas (ArcadeItalia, Libretro+Wikidata, Jikan MAL, AniList, TVMaze) y republica en MQTT Retain.",
        btn_sync_ref: "🔄 Sincronizar Referenciales Ahora",
        msg_syncing: "⏳ Sincronizando...",
        msg_synced: "✓ ¡Sincronizado!",
        msg_sync_err: "⚠️ Error de sincronización",
        seen_title: "Historial Consume-Once (seen_history.json)",
        seen_desc: "Cada título emitido en una matriz DMD se marca inmediatamente como visto para garantizar cero repeticiones.",
        seen_badge_fmt: "{v} consumido(s)",
        btn_reset_all: "Reiniciar Todos los Dominios",
        btn_reset_games: "Reiniciar Games",
        btn_reset_movies: "Reiniciar Movies",
        btn_reset_series: "Reiniciar Series",
        btn_reset_anime: "Reiniciar Anime",
        vault_active: "🔒 Clave Activa (.env / Prod Vault)",
        vault_override: "🛠️ Clave Personalizada Activa",
        vault_missing: "⚠️ No configurada (Fallback activo)"
      }
    };

    const CANONICAL_DOMAINS = ['games', 'movies', 'series', 'anime'];

    function detectInitialLang() {
      try {
        const saved = localStorage.getItem('dmd_showcase_ui_lang');
        if (saved && I18N[saved]) return saved;
      } catch (_) {}
      const nav = ((navigator.language || 'en').split('-')[0] || 'en').toLowerCase();
      return I18N[nav] ? nav : 'en';
    }

    function initApiKeyFromUrl() {
      try {
        const urlParams = new URLSearchParams(window.location.search);
        const qk = (urlParams.get('api_key') || '').trim();
        if (qk) localStorage.setItem('dmd_showcase_api_key', qk);
      } catch (_) {}
    }

    function isMaskedSecret(val) {
      return val === '••••••••' || val === '********';
    }

    function getActiveApiKey() {
      let stored = '';
      try {
        stored = (localStorage.getItem('dmd_showcase_api_key') || '').trim();
      } catch (_) {}
      if (stored && !isMaskedSecret(stored)) return stored;
      const inputEl = document.getElementById('showcase_api_key');
      const typed = inputEl ? inputEl.value.trim() : '';
      if (typed && !isMaskedSecret(typed)) return typed;
      return '';
    }

    function apiFetch(url, options = {}) {
      const opts = Object.assign({}, options);
      const hdrs = Object.assign({}, opts.headers || {});
      const key = getActiveApiKey();
      if (key) hdrs['X-Showcase-Key'] = key;
      opts.headers = hdrs;
      return fetch(url, opts);
    }

    initApiKeyFromUrl();
    let currentUiLang = detectInitialLang();
    let configHydrated = false;
    let lastVaultStatus = {};
    let lastProviders = {};
    let lastClients = [];
    let lastSeenStats = {};
    let lastHealth = null;
    let lastServerConfig = {};

    function esc(val) {
      return String(val == null ? '' : val)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function t(key, vars) {
      let s = (I18N[currentUiLang] && I18N[currentUiLang][key]) || I18N.en[key] || key;
      if (vars) {
        Object.entries(vars).forEach(([k, v]) => {
          s = s.replace(`{${k}}`, String(v));
        });
      }
      return s;
    }

    function syncMqttModeUi() {
      const modeEl = document.getElementById('mqtt_mode');
      const urlEl = document.getElementById('mqtt_external_url');
      const portEl = document.getElementById('mqtt_port');
      if (!modeEl || !urlEl) return;
      const isExt = modeEl.value === 'external';
      urlEl.disabled = !isExt;
      urlEl.placeholder = isExt ? t('ph_mqtt_url') : t('ph_mqtt_embedded');
      if (portEl) portEl.disabled = isExt;
    }

    function syncMdnsUi() {
      const mdnsEl = document.getElementById('mdns_enabled');
      const hostEl = document.getElementById('mdns_hostname');
      if (!mdnsEl || !hostEl) return;
      hostEl.disabled = mdnsEl.value === 'false';
    }

    function renderRuntimeSummary(cfg, h) {
      const c = cfg || lastServerConfig || {};
      const health = h || lastHealth || {};
      const effW = Number(health.max_workers || c.effective_max_workers || 2);
      const cpuCores = Number(c.detected_cpu_cores || 2);
      const autoWorkers = Math.max(1, Math.min(4, Math.max(1, cpuCores - 1)));
      const useProc = health.use_process_pool != null ? Boolean(health.use_process_pool) : (c.use_process_pool !== false);
      const poolShort = useProc ? 'ProcessPool' : 'ThreadPool';
      const maxDur = Number(
        health.video_max_duration_limit_s != null
          ? health.video_max_duration_limit_s
          : (c.video_max_duration_limit_s != null ? c.video_max_duration_limit_s : 30.0)
      ).toFixed(1);
      const maxFps = Number(
        health.video_max_fps_limit != null
          ? health.video_max_fps_limit
          : (c.video_max_fps_limit != null ? c.video_max_fps_limit : 30)
      );

      const wPill = document.getElementById('rt-live-workers-pill');
      if (wPill) {
        wPill.textContent = t('rt_workers_pill_fmt', {w: effW, c: cpuCores, p: poolShort});
      }
      const vPill = document.getElementById('rt-live-video-pill');
      if (vPill) {
        vPill.textContent = t('rt_video_pill_fmt', {d: maxDur, f: maxFps});
      }
      const autoOpt = document.getElementById('opt-workers-auto');
      if (autoOpt) {
        autoOpt.textContent = t('opt_workers_auto_fmt', {w: autoWorkers, c: cpuCores});
      }
    }

    function setUiLang(lang) {
      if (!I18N[lang]) lang = 'en';
      currentUiLang = lang;
      try {
        localStorage.setItem('dmd_showcase_ui_lang', lang);
      } catch (_) {}
      document.documentElement.lang = lang;
      document.getElementById('ui-lang').value = lang;
      document.querySelectorAll('[data-i18n]').forEach(el => {
        const k = el.getAttribute('data-i18n');
        if (I18N[lang][k] || I18N.en[k]) el.textContent = t(k);
      });
      document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const k = el.getAttribute('data-i18n-placeholder');
        if (I18N[lang][k] || I18N.en[k]) el.placeholder = t(k);
      });
      syncMqttModeUi();
      syncMdnsUi();
      renderRuntimeSummary(lastServerConfig, lastHealth);
      renderVaultStatus(lastVaultStatus);
      renderProviders(lastProviders);
      renderClients(lastClients);
      renderSeen(lastSeenStats);
      if (lastHealth) renderHealth(lastHealth);
      loadDomainFilters();
    }

    function switchTab(name) {
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      const idx = {overview: 0, runtime: 1, tester: 2, keys: 3, network: 4}[name] || 0;
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
      if (lastVaultStatus.screenscraper_dev_active) {
        ssEl.className = 'pill ok';
        ssEl.textContent = t('vault_active');
      } else {
        ssEl.className = 'pill warn';
        ssEl.textContent = t('vault_missing');
      }

      const tmdbEl = document.getElementById('vault-tmdb');
      if (lastVaultStatus.tmdb_user_override) {
        tmdbEl.className = 'pill info';
        tmdbEl.textContent = t('vault_override');
      } else if (lastVaultStatus.tmdb_builtin_active) {
        tmdbEl.className = 'pill ok';
        tmdbEl.textContent = t('vault_active');
      } else {
        tmdbEl.className = 'pill warn';
        tmdbEl.textContent = t('vault_missing');
      }

      const tvdbEl = document.getElementById('vault-tvdb');
      if (lastVaultStatus.tvdb_user_override) {
        tvdbEl.className = 'pill info';
        tvdbEl.textContent = t('vault_override');
      } else if (lastVaultStatus.tvdb_builtin_active) {
        tvdbEl.className = 'pill ok';
        tvdbEl.textContent = t('vault_active');
      } else {
        tvdbEl.className = 'pill warn';
        tvdbEl.textContent = t('vault_missing');
      }
    }

    function renderProviders(providers) {
      lastProviders = providers || {};
      const grid = document.getElementById('providers-grid');
      if (!grid) return;
      const rawKeys = Object.keys(lastProviders);
      if (!rawKeys.length) {
        grid.innerHTML = '';
        return;
      }
      const orderedDomains = [
        ...CANONICAL_DOMAINS.filter(d => d in lastProviders),
        ...rawKeys.filter(d => !CANONICAL_DOMAINS.includes(d))
      ];
      grid.innerHTML = orderedDomains.map(dom => {
        const rawChain = lastProviders[dom];
        let chainList = [];
        if (Array.isArray(rawChain)) {
          chainList = rawChain;
        } else if (typeof rawChain === 'string') {
          chainList = rawChain.split('->').map(s => s.trim()).filter(Boolean).map((sid, idx) => ({
            source_id: sid,
            role: idx === 0 ? 'primary' : 'fallback',
            configured: sid !== 'none'
          }));
        }
        const items = chainList.map(p => {
          const ok = Boolean(p.configured);
          const roleLbl = p.role === 'primary' ? t('role_primary') : t('role_fallback');
          return `<span class="pill ${ok ? 'ok' : 'warn'}">${esc(roleLbl)}: ${esc(p.source_id)} ${ok ? '✓' : '⚠️'}</span>`;
        }).join(' ');
        return `<div class="vault-box">
          <div class="vault-title">${esc(dom.toUpperCase())}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${items}</div>
        </div>`;
      }).join('');
    }

    function renderClients(clients) {
      lastClients = Array.isArray(clients) ? clients : [];
      const tb = document.getElementById('clients-tbody');
      if (!tb) return;
      if (!lastClients.length) {
        tb.innerHTML = `<tr><td colspan="7" style="color:var(--muted)">${esc(t('no_clients'))}</td></tr>`;
        return;
      }
      tb.innerHTML = lastClients.map(c => {
        const transport = c.transport || 'MQTT+UDP';
        const seenSuffix = c.last_seen_ago_s != null ? ` • ${Number(c.last_seen_ago_s)}s` : '';
        const ipInfo = (c.client_ip ? `${esc(c.client_ip)} • ${esc(transport)}` : esc(transport)) + esc(seenSuffix);
        let playingCell = '—';
        if (c.udp_streaming) {
          const liveTitle = c.playing_title || c.active_title || 'Live';
          playingCell = `<span class="pill ok">${esc(t('pill_streaming'))} ${esc(liveTitle)}</span>`;
        } else if (c.is_playing && c.playing_title) {
          playingCell = `<span class="pill info">▶ ${esc(c.playing_title)}</span>`;
        } else if (c.playing_title) {
          playingCell = `<span class="pill info">▶ ${esc(c.playing_title)}</span>`;
        } else if (c.last_played_title || c.active_title) {
          playingCell = `<span class="last-played">↺ ${esc(c.last_played_title || c.active_title)}</span>`;
        }
        const readyCell = c.ready_title
          ? `<span class="pill ok">✓ ${esc(c.ready_title)}</span>`
          : (c.preparing ? `<span class="pill warn">${esc(t('pill_preparing'))}</span>` : '—');
        const standbyCell = c.standby_title
          ? `<span class="pill info">✓ ${esc(c.standby_title)}</span>`
          : (c.ready_title && c.preparing ? `<span class="pill warn">${esc(t('pill_preparing'))}</span>` : '—');
        return `
          <tr>
            <td><b>${esc(c.dmd_id)}</b><br/><small style="color:var(--muted)">${ipInfo}</small></td>
            <td><span class="pill info">${esc((c.domain || '').toUpperCase())}</span></td>
            <td><code>${esc(c.width)}×${esc(c.height)}</code></td>
            <td><code>${esc(c.video_mode)}</code> • <code>${esc(c.color_boost)}</code>${c.video_max_duration_s != null ? `<br/><small style="color:var(--muted)">≤${esc(Number(c.video_max_duration_s).toFixed(1))}s @ ${esc(c.video_fps || 15)}fps</small>` : ''}</td>
            <td>${playingCell}</td>
            <td>${readyCell}</td>
            <td>${standbyCell}</td>
          </tr>
        `;
      }).join('');
    }

    function renderSeen(stats) {
      lastSeenStats = stats || {};
      const el = document.getElementById('seen-badges');
      const orderedKeys = [
        ...CANONICAL_DOMAINS,
        ...Object.keys(lastSeenStats).filter(k => !CANONICAL_DOMAINS.includes(k))
      ];
      let total = 0;
      const nonZeroParts = [];
      const html = orderedKeys.map(k => {
        const n = Number(lastSeenStats[k] || 0);
        total += n;
        if (n > 0) nonZeroParts.push(`${k.toUpperCase()}: ${n}`);
        return `<div class="badge"><b>${esc(k.toUpperCase())}</b>: ${esc(t('seen_badge_fmt', {v: n}))}</div>`;
      }).join('');
      if (el) el.innerHTML = html;
      const valEl = document.getElementById('kpi-seen-val');
      if (valEl) valEl.textContent = String(total);
      const subEl = document.getElementById('kpi-seen-sub');
      if (subEl) {
        subEl.textContent = nonZeroParts.length > 0 ? nonZeroParts.join(' • ') : t('kpi_seen_sub');
      }
    }

    function renderHealth(h) {
      lastHealth = h;
      const pill = document.getElementById('health-pill');
      if (pill) {
        pill.className = 'pill ok';
        pill.textContent = `${t('status_online')}${h.version ? ' v' + h.version : ''}`;
      }
      const activeW = Number(h.active_workers || 0);
      const maxW = Number(h.max_workers || 2);
      const qDepth = Number(h.queue_depth || 0);
      const udpLive = Number(h.active_udp_streams || 0);
      const playingNow = Number(h.active_broadcasts || 0);
      const prewarmed = Number(h.ready_slots || 0) + Number(h.standby_slots || 0);

      document.getElementById('kpi-workers-val').textContent = `${activeW} / ${maxW}`;
      const qSubEl = document.getElementById('kpi-queue-sub');
      if (udpLive > 0) {
        qSubEl.textContent = t('kpi_sub_udp_live', {u: udpLive, r: prewarmed, q: qDepth});
      } else if (playingNow > 0) {
        qSubEl.textContent = t('kpi_sub_playing', {p: playingNow, r: prewarmed, q: qDepth});
      } else if (prewarmed > 0) {
        qSubEl.textContent = t('kpi_sub_ready_slots', {q: qDepth, r: prewarmed});
      } else {
        qSubEl.textContent = t('kpi_queue_fmt', {q: qDepth});
      }

      const modeLabel = h.mqtt_mode === 'embedded' ? t('kpi_mqtt_embedded') : t('kpi_mqtt_external');
      document.getElementById('kpi-mqtt-val').textContent = `${modeLabel} :${h.mqtt_port || 1883}`;

      const distinctClients = Number(
        h.mqtt_connected_clients != null
          ? h.mqtt_connected_clients
          : (new Set(lastClients.map(c => c.dmd_id)).size || h.active_sessions || 0)
      );
      const slotsCount = Number(
        h.registered_dmd_slots != null ? h.registered_dmd_slots : (lastClients.length || h.active_sessions || 0)
      );
      document.getElementById('kpi-sessions-sub').textContent = (distinctClients > 0 || slotsCount > 0)
        ? t('kpi_clients_fmt', {c: Math.max(distinctClients, slotsCount > 0 ? 1 : 0), s: slotsCount})
        : t('kpi_clients_zero');

      document.getElementById('kpi-jobs-val').textContent = String(h.completed_jobs_total || 0);
      document.getElementById('kpi-avg-sub').textContent = t('kpi_avg_fmt', {
        a: Number(h.avg_job_duration_s || 0).toFixed(1)
      });

      if (h.seen_history_counts && Object.keys(h.seen_history_counts).length) {
        renderSeen(h.seen_history_counts);
      }

      if (!Object.keys(lastProviders).length) {
        const fallbackProviders = (h.providers_detailed && Object.keys(h.providers_detailed).length)
          ? h.providers_detailed
          : h.providers_configured;
        if (fallbackProviders && Object.keys(fallbackProviders).length) {
          renderProviders(fallbackProviders);
        }
      }
      renderRuntimeSummary(lastServerConfig, h);
    }

    function hydrateConfigInputs(cfg, force) {
      if (!cfg) return;
      lastServerConfig = cfg;
      renderRuntimeSummary(cfg, lastHealth);
      if (configHydrated && !force) return;
      const fields = {
        tmdb_api_key: cfg.tmdb_api_key || '',
        tvdb_api_key: cfg.tvdb_api_key || '',
        screenscraper_user: cfg.screenscraper_user || '',
        screenscraper_pass: cfg.screenscraper_pass || '',
        mqtt_mode: cfg.mqtt_mode || 'embedded',
        mqtt_port: String(cfg.mqtt_port != null ? cfg.mqtt_port : 1883),
        mqtt_external_url: cfg.mqtt_external_url || '',
        default_lang: cfg.default_lang || 'fr',
        showcase_api_key: cfg.showcase_api_key || '',
        max_concurrent_jobs: String(cfg.max_concurrent_jobs != null ? cfg.max_concurrent_jobs : 0),
        use_process_pool: String(cfg.use_process_pool !== false),
        ffmpeg_threads_per_job: String(cfg.ffmpeg_threads_per_job != null ? cfg.ffmpeg_threads_per_job : 1),
        worker_nice_increment: String(cfg.worker_nice_increment != null ? cfg.worker_nice_increment : 10),
        max_queue_size: String(cfg.max_queue_size != null ? cfg.max_queue_size : 128),
        video_max_duration_limit_s: String(cfg.video_max_duration_limit_s != null ? cfg.video_max_duration_limit_s : 30.0),
        server_master_clip_max_s: String(cfg.server_master_clip_max_s != null ? cfg.server_master_clip_max_s : 30.0),
        video_max_fps_limit: String(cfg.video_max_fps_limit != null ? cfg.video_max_fps_limit : 30),
        panorama_duration_s: String(cfg.panorama_duration_s != null ? cfg.panorama_duration_s : 4.0),
        ytdlp_max_filesize_mb: String(cfg.ytdlp_max_filesize_mb != null ? cfg.ytdlp_max_filesize_mb : 60),
        default_video_mode: cfg.default_video_mode || 'auto_action',
        default_color_boost: cfg.default_color_boost || 'auto',
        session_ttl_seconds: String(cfg.session_ttl_seconds != null ? cfg.session_ttl_seconds : 3600),
        batch_ttl_seconds: String(cfg.batch_ttl_seconds != null ? cfg.batch_ttl_seconds : 1800),
        cache_dir: cfg.cache_dir || '/tmp/dmd_showcase_cache',
        log_level: String(cfg.log_level || 'INFO').toUpperCase(),
        mdns_enabled: String(cfg.mdns_enabled !== false),
        mdns_hostname: cfg.mdns_hostname || 'dmdmediashowcase',
        trust_proxy_headers: String(cfg.trust_proxy_headers !== false)
      };
      Object.entries(fields).forEach(([id, val]) => {
        const el = document.getElementById(id);
        if (el && document.activeElement !== el) {
          el.value = val;
        }
      });
      syncMqttModeUi();
      syncMdnsUi();
      configHydrated = true;
    }

    async function loadHealthAndConfig(forceHydrate = false) {
      const [cfgRes, healthRes] = await Promise.allSettled([
        apiFetch('/api/v1/admin/config', {cache: 'no-store'}),
        fetch('/api/v1/health', {cache: 'no-store'})
      ]);

      if (cfgRes.status === 'fulfilled' && cfgRes.value.ok) {
        try {
          const d = await cfgRes.value.json();
          hydrateConfigInputs(d.config || {}, forceHydrate);
          renderVaultStatus(d.vault_status || {});
          renderProviders(d.providers || {});
          renderClients(d.clients || []);
          renderSeen(d.seen_history || {});
        } catch (err) {
          console.error('Failed parsing /api/v1/admin/config:', err);
        }
      }

      if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
        try {
          const h = await healthRes.value.json();
          renderHealth(h);
        } catch (err) {
          console.error('Failed parsing /api/v1/health:', err);
        }
      } else {
        const pill = document.getElementById('health-pill');
        if (pill) {
          pill.className = 'pill err';
          pill.textContent = t('status_offline');
        }
      }
    }

    async function loadDomainFilters() {
      const domEl = document.getElementById('test-domain');
      const sysEl = document.getElementById('test-system');
      if (!domEl || !sysEl) return;
      const dom = domEl.value;
      const isGames = dom === 'games';
      sysEl.disabled = !isGames;
      if (!isGames) {
        sysEl.innerHTML = `<option value="">${esc(t('opt_na'))}</option>`;
      }
      try {
        const r = await apiFetch(`/api/v1/${dom}/filters?lang=${encodeURIComponent(currentUiLang)}`);
        if (!r.ok) return;
        const d = await r.json();
        const allOpt = `<option value="">${esc(t('opt_all'))}</option>`;
        if (isGames) {
          sysEl.innerHTML = allOpt + (d.systems || []).map(
            s => `<option value="${esc(s.id)}">${esc(s.label)} (${esc(s.id)})</option>`
          ).join('');
        }
        const genEl = document.getElementById('test-genre');
        genEl.innerHTML = allOpt + (d.genres || []).map(
          g => `<option value="${esc(g.id)}">${esc(g.label)}</option>`
        ).join('');
        const decEl = document.getElementById('test-decade');
        decEl.innerHTML = allOpt + (d.decades || []).map(
          dec => `<option value="${esc(dec.id)}">${esc(dec.label)}</option>`
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
      st.textContent = t('msg_testing');

      try {
        let item = null;
        if (q) {
          const r = await apiFetch(`/api/v1/${dom}/on-demand`, {
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
          const r = await apiFetch(`/api/v1/${dom}/random?` + params.toString());
          if (!r.ok) throw new Error('Discovery request failed');
          let batch = await r.json();
          for (let i = 0; i < 30 && (!batch.items || !batch.items.length); i++) {
            await new Promise(res => setTimeout(res, 600));
            const rb = await apiFetch(`/api/v1/${dom}/batch?session_id=${encodeURIComponent(batch.session_id)}`);
            if (rb.ok) batch = await rb.json();
          }
          if (batch.items && batch.items.length) item = batch.items[0];
        }

        if (!item) throw new Error(t('msg_no_candidate'));
        document.getElementById('test-preview').classList.add('visible');
        document.getElementById('prev-title').textContent = item.title || '—';
        document.getElementById('prev-badge').textContent = item.badge || dom.toUpperCase();
        document.getElementById('prev-year').textContent = item.year || '—';
        document.getElementById('prev-source').textContent = `Source: ${item.source_used || 'auto'} • ★ ${item.rating || 'N/A'}`;
        document.getElementById('prev-sub').textContent = item.subtitle || '';
        document.getElementById('prev-synopsis').textContent = item.synopsis || '';
        const imgEl = document.getElementById('prev-img');
        let posterUrl = item.poster_png_url || (item.assets && item.assets.poster_png) || '';
        const activeKey = getActiveApiKey();
        if (posterUrl && activeKey && posterUrl.startsWith('/api/v1/')) {
          posterUrl += (posterUrl.includes('?') ? '&' : '?') + 'api_key=' + encodeURIComponent(activeKey);
        }
        if (posterUrl) {
          imgEl.src = posterUrl;
          imgEl.style.display = 'block';
        } else {
          imgEl.style.display = 'none';
        }
        st.textContent = t('msg_preview_ok');
        loadHealthAndConfig(false);
      } catch (err) {
        st.textContent = '⚠️ ' + (err.message || String(err));
      } finally {
        btn.disabled = false;
      }
    }

    function clampIntInput(id, minVal, maxVal, fallback) {
      const raw = parseInt(document.getElementById(id).value, 10);
      if (Number.isNaN(raw)) return fallback;
      return Math.max(minVal, Math.min(maxVal, raw));
    }

    function clampFloatInput(id, minVal, maxVal, fallback) {
      const raw = parseFloat(document.getElementById(id).value);
      if (Number.isNaN(raw)) return fallback;
      return Math.max(minVal, Math.min(maxVal, raw));
    }

    function showConfigToast(msg, isError = false) {
      ['status-msg-runtime', 'status-msg-keys', 'status-msg-net'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.style.color = isError ? 'var(--red)' : 'var(--green)';
        el.textContent = msg;
        setTimeout(() => {
          if (el.textContent === msg) el.textContent = '';
        }, 4000);
      });
    }

    async function saveConfig() {
      const payload = {
        tmdb_api_key: document.getElementById('tmdb_api_key').value.trim(),
        tvdb_api_key: document.getElementById('tvdb_api_key').value.trim(),
        screenscraper_user: document.getElementById('screenscraper_user').value.trim(),
        screenscraper_pass: document.getElementById('screenscraper_pass').value.trim(),
        mqtt_mode: document.getElementById('mqtt_mode').value,
        mqtt_port: clampIntInput('mqtt_port', 1, 65535, 1883),
        mqtt_external_url: document.getElementById('mqtt_external_url').value.trim(),
        default_lang: document.getElementById('default_lang').value,
        showcase_api_key: document.getElementById('showcase_api_key').value.trim(),
        max_concurrent_jobs: clampIntInput('max_concurrent_jobs', 0, 16, 0),
        use_process_pool: document.getElementById('use_process_pool').value === 'true',
        ffmpeg_threads_per_job: clampIntInput('ffmpeg_threads_per_job', 1, 8, 1),
        worker_nice_increment: clampIntInput('worker_nice_increment', 0, 19, 10),
        max_queue_size: clampIntInput('max_queue_size', 10, 1000, 128),
        video_max_duration_limit_s: clampFloatInput('video_max_duration_limit_s', 1.5, 120.0, 30.0),
        server_master_clip_max_s: clampFloatInput('server_master_clip_max_s', 1.5, 120.0, 30.0),
        video_max_fps_limit: clampIntInput('video_max_fps_limit', 6, 60, 30),
        panorama_duration_s: clampFloatInput('panorama_duration_s', 1.5, 15.0, 4.0),
        ytdlp_max_filesize_mb: clampIntInput('ytdlp_max_filesize_mb', 10, 250, 60),
        default_video_mode: document.getElementById('default_video_mode').value,
        default_color_boost: document.getElementById('default_color_boost').value,
        session_ttl_seconds: clampIntInput('session_ttl_seconds', 60, 86400, 3600),
        batch_ttl_seconds: clampIntInput('batch_ttl_seconds', 30, 7200, 1800),
        cache_dir: document.getElementById('cache_dir').value.trim() || '/tmp/dmd_showcase_cache',
        log_level: document.getElementById('log_level').value,
        mdns_enabled: document.getElementById('mdns_enabled').value === 'true',
        mdns_hostname: document.getElementById('mdns_hostname').value.trim() || 'dmdmediashowcase',
        trust_proxy_headers: document.getElementById('trust_proxy_headers').value === 'true'
      };
      try {
        const r = await apiFetch('/api/v1/admin/config', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
        });
        if (!r.ok) {
          let errDetail = 'HTTP ' + r.status;
          try {
            const errJson = await r.json();
            errDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail || errJson);
          } catch (_) {}
          showConfigToast('⚠️ ' + errDetail, true);
          return;
        }
        const resData = await r.json();
        try {
          if (payload.showcase_api_key === '') {
            localStorage.removeItem('dmd_showcase_api_key');
          } else if (!isMaskedSecret(payload.showcase_api_key)) {
            localStorage.setItem('dmd_showcase_api_key', payload.showcase_api_key);
          }
        } catch (_) {}
        const applied = resData.applied_live || {};
        const effW = applied.effective_max_workers != null ? applied.effective_max_workers : payload.max_concurrent_jobs;
        const maxD = Number(applied.video_max_duration_limit_s != null ? applied.video_max_duration_limit_s : payload.video_max_duration_limit_s).toFixed(1);
        const maxF = applied.video_max_fps_limit != null ? applied.video_max_fps_limit : payload.video_max_fps_limit;
        showConfigToast(t('msg_saved_live', {w: effW, d: maxD, f: maxF}), false);
        loadHealthAndConfig(true);
      } catch (err) {
        showConfigToast('⚠️ ' + (err.message || String(err)), true);
      }
    }

    async function syncReferentials() {
      const st = document.getElementById('sync-status');
      st.textContent = t('msg_syncing');
      try {
        const r = await apiFetch('/api/v1/admin/referentials/sync', {method: 'POST'});
        if (r.ok) {
          const d = await r.json();
          const counts = d.referential_counts || {};
          document.getElementById('ref-badges').innerHTML = Object.entries(counts).map(
            ([k, v]) => `<div class="badge"><b>${esc(k)}</b>: ${esc(v)} tokens</div>`
          ).join('');
          st.textContent = t('msg_synced');
        }
      } catch (e) {
        st.textContent = t('msg_sync_err');
      }
    }

    async function resetHistory(domain) {
      const q = domain ? `?domain=${encodeURIComponent(domain)}` : '';
      await apiFetch('/api/v1/admin/history/reset' + q, {method: 'POST'});
      loadHealthAndConfig(false);
    }

    function refreshAll() {
      loadHealthAndConfig(true);
    }

    setUiLang(currentUiLang);
    loadHealthAndConfig(true);
    setInterval(() => loadHealthAndConfig(false), 3000);
  </script>
</body>
</html>
"""
