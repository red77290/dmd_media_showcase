"""
Centralized Multilingual Web Administration Dashboard (`http://dmdmediashowcase.local:8890/` & `/admin`).

Features:
1. Real-Time Overview: Worker pool & Consume-Once transcode telemetry, Embedded MQTT (:1883) status,
   active UDP `DMDF` streams, connected DMD clients (`ArcadeMatrix` ESP32 & `ArcadeMatrix_RPi`) with
   live playback, N (Ready) and N+1 (Standby) Consume-Once slots, and active `[Primary, Fallback]`
   metadata & trailer scraper chains.
2. Runtime, Workers & Video Limits: Hot-reloadable worker concurrency, ceilings, and cache controls.
3. Search, On-Demand DMD Queue & Live Showcase Tester: Interactive localized catalog search and queue control.
4. Mode 2B Card Studio & Real-Time HUB75 DMD Simulator: Pixel-perfect Card layout/theme customizer
   (YOKO & TATE), binary `DMDS` RGB565 LED simulator, and 0 ms simulation injection into active DMD queues.
5. API Keys & Encrypted Vault Status: Shows active status of `.env` / `docker-compose` or CI/CD
   Production Native Vault keys (`ScreenScraper Dev`, `TMDB`, `TheTVDB`) without ever exposing them
   in plaintext, while allowing users to configure their personal ScreenScraper player account
   (`SSID` / password) or optional overrides.
6. Network, MQTT & Maintenance: Default metadata language, MQTT broker mode, live referential sync,
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
    .mini-btn {
      height: 30px;
      padding: 0 9px;
      font-size: 0.78rem;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      white-space: nowrap;
    }
    .cand-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }
    .cand-card {
      background: #0d121b;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      display: flex;
      gap: 12px;
      align-items: flex-start;
    }
    .cand-thumb {
      width: 64px;
      height: 84px;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: #06080d;
      flex-shrink: 0;
    }
    .cand-body { flex: 1; min-width: 0; }
    .cand-title { font-weight: 700; font-size: 0.94rem; color: #fff; margin: 0 0 4px 0; }
    .cand-syn {
      color: var(--muted);
      font-size: 0.8rem;
      line-height: 1.38;
      margin: 6px 0 10px 0;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .cand-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .playlist-table input, .playlist-table select {
      height: 36px;
      line-height: 34px;
      font-size: 0.82rem;
      padding: 0 8px;
    }
    .playlist-table select { padding-right: 28px; }

    /* Mode 2B Card Studio & HUB75 LED Simulator */
    .studio-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
      gap: 18px;
      align-items: start;
    }
    .studio-search-bar {
      display: grid;
      grid-template-columns: 150px 135px 155px 155px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
    }
    .studio-cand-strip {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding: 10px 0 4px 0;
      margin-top: 8px;
    }
    .studio-cand-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: #0d121b;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 6px 10px;
      cursor: pointer;
      min-width: 190px;
      max-width: 260px;
      flex-shrink: 0;
      transition: border-color 0.15s ease, background 0.15s ease;
    }
    .studio-cand-pill:hover { border-color: #3b4d6b; background: #141c2b; }
    .studio-cand-pill.active {
      border-color: var(--accent);
      background: rgba(255, 109, 0, 0.12);
      box-shadow: 0 0 0 1px rgba(255, 109, 0, 0.35);
    }
    .studio-cand-pill img {
      width: 30px;
      height: 38px;
      object-fit: cover;
      border-radius: 4px;
      background: #05070b;
      flex-shrink: 0;
    }
    .studio-res-bar {
      display: flex;
      gap: 7px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
    }
    .res-chip {
      height: 32px;
      padding: 0 11px;
      font-size: 0.8rem;
      border-radius: 7px;
      background: #0d121b;
      border: 1px solid var(--border);
      color: var(--text);
    }
    .res-chip:hover { background: #182234; border-color: #3b4d6b; }
    .res-chip.active {
      background: rgba(0, 229, 255, 0.14);
      border-color: var(--cyan);
      color: var(--cyan);
    }
    .tate-btn {
      height: 32px;
      padding: 0 12px;
      font-size: 0.8rem;
      border-radius: 7px;
      background: #222c3c;
      border: 1px solid var(--border);
      color: var(--text);
    }
    .tate-btn.active {
      background: linear-gradient(135deg, var(--accent), #e65100);
      border-color: var(--accent);
      color: #fff;
    }
    .layout-cards-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 14px;
    }
    .layout-card-btn {
      background: #0d121b;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 6px;
      text-align: center;
      cursor: pointer;
      color: var(--muted);
      font-size: 0.74rem;
      font-weight: 600;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 5px;
    }
    .layout-card-btn:hover { background: #151e2d; color: var(--text); }
    .layout-card-btn.active {
      border-color: var(--accent);
      background: rgba(255, 109, 0, 0.14);
      color: #fff;
    }
    .layout-wire {
      width: 44px;
      height: 20px;
      border: 1px solid #3b4d6b;
      border-radius: 3px;
      display: flex;
      overflow: hidden;
      background: #06080d;
    }
    .studio-toggles {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 12px 0;
    }
    .chk-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: #0d121b;
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 6px 10px;
      font-size: 0.8rem;
      color: var(--text);
      cursor: pointer;
      margin: 0;
      user-select: none;
    }
    input[type="checkbox"].studio-chk {
      width: 15px;
      height: 15px;
      margin: 0;
      padding: 0;
      accent-color: var(--accent);
      cursor: pointer;
    }
    input[type="range"].studio-range {
      height: 26px;
      padding: 0;
      background: transparent;
      border: none;
      accent-color: var(--accent);
      cursor: pointer;
    }
    input[type="color"].studio-color {
      width: 44px;
      height: 42px;
      padding: 3px;
      cursor: pointer;
    }
    .sim-stage-wrap {
      background: #05070b;
      border: 2px solid #1f2a3c;
      border-radius: 10px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 210px;
      overflow: auto;
      box-shadow: inset 0 0 28px rgba(0, 0, 0, 0.85);
    }
    #studio-dmd-canvas {
      display: block;
      max-width: 100%;
      height: auto;
      background: #020305;
      border: 1px solid #222d40;
      border-radius: 4px;
      image-rendering: pixelated;
    }
    .sim-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    .sim-controls-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }
    .chapter-pills {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-top: 10px;
    }
    .chapter-pill {
      height: 26px;
      padding: 0 9px;
      font-size: 0.74rem;
      border-radius: 999px;
      background: #101725;
      border: 1px solid var(--border);
      color: var(--cyan);
    }
    .chapter-pill.active {
      background: rgba(0, 229, 255, 0.18);
      border-color: var(--cyan);
      color: #fff;
    }

    /* Tag Picker Pills, Studio Domain Switcher & Multi-Select Tag Modal */
    .tag-cell {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      align-items: center;
      min-width: 170px;
    }
    .tag-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 0.74rem;
      font-weight: 600;
      background: rgba(0, 229, 255, 0.12);
      border: 1px solid rgba(0, 229, 255, 0.35);
      color: #d8f8ff;
      white-space: nowrap;
    }
    .tag-chip.decade {
      background: rgba(255, 171, 0, 0.12);
      border-color: rgba(255, 171, 0, 0.42);
      color: #ffe082;
    }
    .tag-chip-rm {
      cursor: pointer;
      opacity: 0.72;
      font-size: 0.72rem;
      line-height: 1;
      padding: 0 2px;
    }
    .tag-chip-rm:hover { opacity: 1; color: #ff5252; }
    .tag-all-pill {
      display: inline-flex;
      align-items: center;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 0.74rem;
      color: var(--muted);
      background: #0d121b;
      border: 1px dashed #2b384e;
    }
    .tag-pick-btn {
      height: 28px !important;
      line-height: 26px !important;
      padding: 0 9px !important;
      font-size: 0.74rem !important;
      border-radius: 7px !important;
      background: #182234 !important;
      border: 1px solid #30415c !important;
      color: var(--text) !important;
      white-space: nowrap;
    }
    .tag-pick-btn:hover { border-color: var(--cyan) !important; color: #fff !important; }
    .studio-link-cell {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-width: 240px;
    }
    .studio-summary-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      align-items: center;
    }
    .studio-jump-btn {
      height: 30px !important;
      line-height: 28px !important;
      padding: 0 11px !important;
      font-size: 0.76rem !important;
      border-radius: 8px !important;
      background: rgba(255, 109, 0, 0.16) !important;
      border: 1px solid rgba(255, 109, 0, 0.55) !important;
      color: #ffb074 !important;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .studio-jump-btn:hover {
      background: var(--accent) !important;
      color: #fff !important;
    }
    .studio-profile-bar {
      background: linear-gradient(180deg, #131b28 0%, #0e141f 100%);
      border: 1px solid #2b3950;
      border-radius: 12px;
      padding: 14px 16px;
      margin-bottom: 16px;
    }
    .studio-dom-tabs {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .studio-dom-tab {
      height: auto !important;
      padding: 10px 12px !important;
      border-radius: 10px !important;
      background: #0b1018 !important;
      border: 1px solid var(--border) !important;
      color: var(--text) !important;
      text-align: left !important;
      display: flex;
      flex-direction: column;
      gap: 4px;
      cursor: pointer;
      transition: border-color 0.15s ease, background 0.15s ease;
    }
    .studio-dom-tab:hover { border-color: #3d5274 !important; background: #131c2b !important; }
    .studio-dom-tab.active {
      border-color: var(--accent) !important;
      background: rgba(255, 109, 0, 0.14) !important;
      box-shadow: 0 0 0 1px rgba(255, 109, 0, 0.35);
    }
    .studio-dom-tab-title {
      font-size: 0.85rem;
      font-weight: 700;
      color: #fff;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
    }
    .studio-dom-tab-sub {
      font-size: 0.72rem;
      color: var(--muted);
      font-weight: 500;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 9999;
      background: rgba(3, 6, 12, 0.78);
      backdrop-filter: blur(4px);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 18px;
    }
    .modal-backdrop.open { display: flex; }
    .tag-modal {
      width: 100%;
      max-width: 760px;
      max-height: 85vh;
      background: #121824;
      border: 1px solid #30415c;
      border-radius: 14px;
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.8);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .tag-modal-head {
      padding: 16px 20px 12px;
      border-bottom: 1px solid var(--border);
      background: #0e141f;
    }
    .tag-modal-body {
      padding: 14px 20px;
      overflow-y: auto;
      flex: 1;
    }
    .tag-modal-sec-title {
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--cyan);
      font-weight: 700;
      margin: 6px 0 10px;
    }
    .tag-modal-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }
    .tag-option-btn {
      height: auto !important;
      min-height: 34px;
      padding: 6px 12px !important;
      border-radius: 999px !important;
      font-size: 0.8rem !important;
      font-weight: 600 !important;
      background: #0b1018 !important;
      border: 1px solid #28354a !important;
      color: var(--muted) !important;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
    }
    .tag-option-btn:hover { border-color: #465d82 !important; color: #fff !important; }
    .tag-option-btn.selected {
      background: rgba(0, 229, 255, 0.16) !important;
      border-color: var(--cyan) !important;
      color: #fff !important;
    }
    .tag-option-btn.decade.selected {
      background: rgba(255, 171, 0, 0.18) !important;
      border-color: var(--amber) !important;
      color: #fff !important;
    }
    .tag-modal-foot {
      padding: 12px 20px;
      border-top: 1px solid var(--border);
      background: #0e141f;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    @media (max-width: 960px) {
      .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .providers-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .vault-grid { grid-template-columns: 1fr; }
      .form-grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .studio-grid { grid-template-columns: 1fr; }
      .studio-search-bar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout-cards-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .studio-dom-tabs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 720px) {
      .form-grid-2, .form-grid-3, .kpi-grid, .providers-grid, .cand-grid, .studio-dom-tabs { grid-template-columns: 1fr; }
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
            MQTT Retain (:1883) + Real-Time UDP • Consume-Once • HUB75 LED Matrices
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
        3. Search &amp; On-Demand Queue
      </button>
      <button class="tab-btn" onclick="switchTab('studio')" data-i18n="tab_studio">
        4. Studio Card &amp; Sim DMD (2B)
      </button>
      <button class="tab-btn" onclick="switchTab('keys')" data-i18n="tab_keys">
        5. API Keys &amp; Encrypted Vault
      </button>
      <button class="tab-btn" onclick="switchTab('network')" data-i18n="tab_network">
        6. Network, MQTT &amp; Maintenance
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
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="secondary" style="margin:0;padding:6px 12px;font-size:0.8rem" onclick="openProfileEditor('')" data-i18n="btn_new_profile">
              ➕ New Passive DMD Profile
            </button>
            <button class="secondary" style="margin:0;padding:6px 12px;font-size:0.8rem" onclick="refreshAll()" data-i18n="btn_refresh">
              Refresh
            </button>
          </div>
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
                <th data-i18n="th_actions">Control</th>
              </tr>
            </thead>
            <tbody id="clients-tbody">
              <tr><td colspan="8" style="color:var(--muted)" data-i18n="no_clients">No active DMD client registered yet.</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- PERSISTENT DMD PROFILE & SERVER-MANAGED PLAYLIST EDITOR -->
      <div class="card" id="dmd-profile-editor-card" style="display:none">
        <h2>
          <span data-i18n="prof_editor_title">DMD Profile &amp; Server-Managed Playlist (dmd_profiles.json)</span>
          <button class="secondary mini-btn" onclick="closeProfileEditor()" data-i18n="btn_close">✕ Close</button>
        </h2>
        <p class="desc" data-i18n="prof_editor_desc">
          Configure passive ESP32 / RPi receivers directly from the Showcase server (Mode 2A Hybrid or Mode 2B 100% Passive UDP), set their broadcast content language, and curate multi-domain rotation playlists persisted in ./data/dmd_profiles.json.
        </p>
        <div class="form-grid-3">
          <div class="field">
            <label data-i18n="lbl_prof_id">DMD Identifier (dmd_id)</label>
            <input type="text" id="prof-dmd-id" placeholder="esp32_cab_1" />
          </div>
          <div class="field">
            <label data-i18n="lbl_prof_name">Friendly Display Name</label>
            <input type="text" id="prof-display-name" placeholder="Living Room Arcade DMD" />
          </div>
          <div class="field">
            <label data-i18n="lbl_prof_mode">DMD Operating Mode</label>
            <select id="prof-mode">
              <option value="server_hybrid" data-i18n="opt_mode_2a">Mode 2A — Server Playlist + Local 60 FPS Card</option>
              <option value="server_passive" data-i18n="opt_mode_2b">Mode 2B — 100% Passive UDP Framebuffer</option>
              <option value="client" data-i18n="opt_mode_1">Mode 1 — Client-Driven (e.g. ArcadeMatrix)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_prof_lang">Broadcast Content Language (lang)</label>
            <select id="prof-lang" onchange="onProfileLangChange()">
              <option value="fr">🇫🇷 Français (fr)</option>
              <option value="en">🇬🇧 English (en)</option>
              <option value="es">🇪🇸 Español (es)</option>
              <option value="de">🇩🇪 Deutsch (de)</option>
              <option value="it">🇮🇹 Italiano (it)</option>
              <option value="pt">🇵🇹 Português (pt)</option>
              <option value="ja">🇯🇵 日本語 (ja)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_resolution">DMD Panel Resolution</label>
            <select id="prof-res">
              <option value="128x32">128×32 (Standard Marquee)</option>
              <option value="128x64">128×64 (Double Height)</option>
              <option value="256x64">256×64 (XL Arcade)</option>
              <option value="64x64">64×64 (Square)</option>
              <option value="64x128">64×128 (TATE Vertical)</option>
              <option value="32x128">32×128 (Slim TATE)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_prof_always_udp">Continuous UDP Push (without MQTT)</label>
            <select id="prof-always-udp">
              <option value="false" data-i18n="opt_udp_presence">Wait for MQTT / HTTP Hello Presence</option>
              <option value="true" data-i18n="opt_udp_always">Always Stream UDP to LAN IP (Pure UDP Sketch)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_prof_ip">Target DMD LAN IP (udp_ip)</label>
            <input type="text" id="prof-udp-ip" placeholder="192.168.1.85" />
          </div>
          <div class="field">
            <label data-i18n="lbl_prof_port">UDP Frame Port (udp_port)</label>
            <input type="number" id="prof-udp-port" min="1" max="65535" step="1" value="4210" />
          </div>
          <div class="field">
            <label data-i18n="lbl_prof_gap">Inter-Item Transition Gap (s — 0..10)</label>
            <input type="number" id="prof-gap-s" min="0" max="10" step="0.5" value="1.0" />
          </div>
        </div>

        <div style="margin-top:16px;padding:10px 14px;background:#0d131d;border:1px solid var(--border);border-radius:9px;font-size:0.82rem;color:var(--muted)" data-i18n="prof_rotation_hint">
          💡 Select Systems, Genres &amp; Decades with visual tags (empty = All). Video duration, framing, color boost, and Mode 2B Card layout are configured per domain inside Studio — click any domain's Studio button on the right to customize it.
        </div>

        <div style="overflow-x:auto;margin-top:12px">
          <table class="playlist-table">
            <thead>
              <tr>
                <th data-i18n="th_pl_on">On</th>
                <th data-i18n="th_domain">Domain</th>
                <th data-i18n="th_pl_count">Items / Turn</th>
                <th data-i18n="th_pl_systems_tags">Systems (Games)</th>
                <th data-i18n="th_pl_genres_tags">Genres &amp; Decades</th>
                <th data-i18n="th_pl_studio_link">Studio Profile (Card, Framing &amp; Video)</th>
              </tr>
            </thead>
            <tbody id="prof-playlist-tbody"></tbody>
          </table>
        </div>

        <div class="form-actions">
          <button onclick="saveDmdProfile()" data-i18n="btn_save_profile">💾 Save Profile &amp; Apply Live</button>
          <button class="danger" onclick="deleteDmdProfile()" data-i18n="btn_delete_profile">🗑️ Delete Profile</button>
          <span id="prof-status-msg" class="toast"></span>
        </div>
      </div>

      <div class="card">
        <h2 data-i18n="chains_title">Metadata &amp; Trailer Sources ([Primary, Fallback])</h2>
        <p class="desc" data-i18n="chains_desc">
          Each domain queries its Primary source first and seamlessly switches to its Fallback source if unconfigured or rate-limited.
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

    <!-- TAB 3: SEARCH, ON-DEMAND QUEUE & LIVE SHOWCASE TESTER -->
    <div id="tab-tester" class="tab-pane">
      <div class="card">
        <h2 data-i18n="tester_title">Search, On-Demand DMD Queue &amp; Live Tester</h2>
        <p class="desc" data-i18n="tester_desc">
          Search across official multilingual catalogs by category and text, choose the exact broadcast language, preview assets in the browser, or push any selected title directly to a DMD's priority queue (Next or Play Now).
        </p>
        <div class="form-grid-3">
          <div class="field">
            <label data-i18n="lbl_target_dmd">Target DMD Matrix (for Queue / Play Now)</label>
            <select id="test-target-dmd" onchange="loadTargetDmdQueue()">
              <option value="" data-i18n="opt_target_browser">🖥️ Browser Preview Only</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_content_lang">Content Broadcast Language (lang)</label>
            <select id="test-content-lang" onchange="loadDomainFilters()">
              <option value="fr">🇫🇷 Français (fr)</option>
              <option value="en">🇬🇧 English (en)</option>
              <option value="es">🇪🇸 Español (es)</option>
              <option value="de">🇩🇪 Deutsch (de)</option>
              <option value="it">🇮🇹 Italiano (it)</option>
              <option value="pt">🇵🇹 Português (pt)</option>
              <option value="ja">🇯🇵 日本語 (ja)</option>
            </select>
          </div>
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
              <option value="64x128">64×128 (TATE Vertical)</option>
            </select>
          </div>
          <div class="field span-2">
            <label data-i18n="lbl_query">Title / Keyword Search (Optional — leave empty to browse filtered picks)</label>
            <input type="text" id="test-query" onkeydown="if(event.key==='Enter') runCandidateSearch()" data-i18n-placeholder="ph_query" placeholder="e.g. Super Street Fighter II Turbo, The Matrix, Cowboy Bebop..." />
          </div>
          <div class="field">
            <label data-i18n="lbl_mark_seen">Consume-Once History Policy</label>
            <select id="test-mark-seen">
              <option value="true" data-i18n="opt_mark_seen_yes">Mark as Seen after playback</option>
              <option value="false" data-i18n="opt_mark_seen_no">Keep repeatable (FrontendSync mode)</option>
            </select>
          </div>
        </div>
        <div class="form-actions">
          <button onclick="runCandidateSearch()" id="btn-search-cands" data-i18n="btn_search_cands">
            🔍 Search / Browse Candidates
          </button>
          <button class="secondary" onclick="runLiveTest()" id="btn-run-test" data-i18n="btn_run_test">
            🎬 Direct Web Preview
          </button>
          <span id="test-status" class="toast"></span>
        </div>

        <div id="search-results-grid" class="cand-grid" style="display:none"></div>

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

      <div class="card" id="ondemand-queue-card">
        <h2>
          <span data-i18n="queue_panel_title">On-Demand Priority Queue (Target DMD)</span>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="secondary mini-btn" onclick="skipSelectedDmd()" data-i18n="btn_skip_now">⏭ Skip Current</button>
            <button class="secondary mini-btn" onclick="loadTargetDmdQueue()" data-i18n="btn_refresh">Refresh</button>
          </div>
        </h2>
        <p class="desc" data-i18n="queue_panel_desc">
          Items added with Queue Next play in strict FIFO order right after the current item without losing pre-transcoded random slots. Play Now preempts immediately.
        </p>
        <div id="ondemand-queue-body" style="color:var(--muted);font-size:0.86rem" data-i18n="queue_select_dmd">
          Select a Target DMD above to inspect or control its live priority queue.
        </div>
      </div>
    </div>

    <!-- TAB 4: MODE 2B CARD STUDIO & REAL-TIME HUB75 DMD SIMULATOR -->
    <div id="tab-studio" class="tab-pane">
      <!-- DOMAIN PROFILE SWITCHER BAR (PER-DOMAIN CARD + SEQUENCE PROFILES: GAMES / MOVIES / SERIES / ANIME) -->
      <div class="studio-profile-bar">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <span style="font-size:0.9rem;font-weight:700;color:#fff" data-i18n="studio_prof_bar_title">🎯 Domain Profiles on DMD:</span>
            <select id="studio-top-dmd-select" onchange="onStudioTopDmdChange(this.value)" style="width:auto;min-width:250px;height:36px;line-height:34px;font-size:0.84rem;padding:0 32px 0 10px">
              <option value="" data-i18n="studio_opt_no_dmd">— Select or connect a DMD —</option>
            </select>
            <button type="button" id="btn-studio-back-rotation" class="secondary mini-btn" onclick="returnToDmdRotationEditor()" style="display:none" data-i18n="btn_back_rotation">
              ← Back to DMD Rotation
            </button>
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <button type="button" class="mini-btn" onclick="saveStudioStyleToDmdProfile(false)" data-i18n="studio_btn_save_dom_prof">
              💾 Save Domain Profile to DMD
            </button>
            <button type="button" class="secondary mini-btn" onclick="saveStudioStyleToDmdProfile(true)" data-i18n="studio_btn_copy_all_doms">
              📋 Apply to All 4 Domains
            </button>
          </div>
        </div>
        <div id="studio-top-save-msg" class="toast" style="margin-top:6px"></div>

        <div class="studio-dom-tabs" id="studio-dom-tabs">
          <button type="button" class="studio-dom-tab active" data-st-dom="games" onclick="selectStudioDomainProfile('games')">
            <span class="studio-dom-tab-title"><span data-i18n="dom_prof_games">🕹️ Games Profile</span> <span class="pill info" style="padding:1px 6px;font-size:0.68rem">games</span></span>
            <span class="studio-dom-tab-sub" id="st-dom-sub-games">classic_split • tinted • 10s</span>
          </button>
          <button type="button" class="studio-dom-tab" data-st-dom="movies" onclick="selectStudioDomainProfile('movies')">
            <span class="studio-dom-tab-title"><span data-i18n="dom_prof_movies">🎬 Movies Profile</span> <span class="pill info" style="padding:1px 6px;font-size:0.68rem">movies</span></span>
            <span class="studio-dom-tab-sub" id="st-dom-sub-movies">poster_right • poster_wash • 12s</span>
          </button>
          <button type="button" class="studio-dom-tab" data-st-dom="series" onclick="selectStudioDomainProfile('series')">
            <span class="studio-dom-tab-title"><span data-i18n="dom_prof_series">📺 Series Profile</span> <span class="pill info" style="padding:1px 6px;font-size:0.68rem">series</span></span>
            <span class="studio-dom-tab-sub" id="st-dom-sub-series">classic_split • tinted • 10s</span>
          </button>
          <button type="button" class="studio-dom-tab" data-st-dom="anime" onclick="selectStudioDomainProfile('anime')">
            <span class="studio-dom-tab-title"><span data-i18n="dom_prof_anime">⛩️ Anime Profile</span> <span class="pill info" style="padding:1px 6px;font-size:0.68rem">anime</span></span>
            <span class="studio-dom-tab-sub" id="st-dom-sub-anime">badge_header • neon_border • 12s</span>
          </button>
        </div>
      </div>

      <!-- TOP SEARCH & RESOLUTION BAR -->
      <div class="card" style="padding:16px 20px">
        <h2>
          <span data-i18n="studio_title">Mode 2B Card Studio &amp; Real-Time HUB75 DMD Simulator</span>
          <span id="studio-geom-pill" class="pill info">128×32 • YOKO</span>
        </h2>
        <p class="desc" data-i18n="studio_desc">
          Search any game, movie, series, or anime via the On-Demand catalog, customize the Mode 2B pixel-art Info Card &amp; sequence options across standard and TATE resolutions, preview on the live HUB75 LED Simulator, and push your simulation directly into the active DMD queue.
        </p>

        <div class="studio-search-bar">
          <div class="field">
            <label data-i18n="lbl_domain">Domain</label>
            <select id="studio-domain" onchange="selectStudioDomainProfile(this.value)">
              <option value="games" data-i18n="opt_dom_games">🕹️ Retro Games (games)</option>
              <option value="movies" data-i18n="opt_dom_movies">🎬 Movies / Films (movies)</option>
              <option value="series" data-i18n="opt_dom_series">📺 TV Series (series)</option>
              <option value="anime" data-i18n="opt_dom_anime">⛩️ Anime / Japanimation (anime)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_content_lang">Content Broadcast Language (lang)</label>
            <select id="studio-lang" onchange="loadStudioFilters()">
              <option value="fr">🇫🇷 Français (fr)</option>
              <option value="en">🇬🇧 English (en)</option>
              <option value="es">🇪🇸 Español (es)</option>
              <option value="de">🇩🇪 Deutsch (de)</option>
              <option value="it">🇮🇹 Italiano (it)</option>
              <option value="pt">🇵🇹 Português (pt)</option>
              <option value="ja">🇯🇵 日本語 (ja)</option>
            </select>
          </div>
          <div class="field">
            <label data-i18n="lbl_system">System (Games)</label>
            <select id="studio-system"><option value="" data-i18n="opt_all">All</option></select>
          </div>
          <div class="field">
            <label data-i18n="lbl_genre">Genre</label>
            <select id="studio-genre"><option value="" data-i18n="opt_all">All</option></select>
          </div>
          <div class="field">
            <label data-i18n="studio_lbl_search">Search Catalog Content (Title / ROM / Franchise)</label>
            <input type="text" id="studio-query" onkeydown="if(event.key==='Enter') runStudioSearch()" data-i18n-placeholder="ph_query" placeholder="e.g. Metal Slug X, Blade Runner, Akira..." />
          </div>
          <button type="button" id="btn-studio-search" onclick="runStudioSearch()" style="height:42px;white-space:nowrap" data-i18n="studio_btn_search">
            🔍 Search
          </button>
        </div>

        <div id="studio-search-status" class="toast" style="margin-top:8px"></div>
        <div id="studio-cand-strip" class="studio-cand-strip"></div>

        <div class="studio-res-bar">
          <span style="font-size:0.8rem;font-weight:600;color:var(--muted)" data-i18n="studio_lbl_res_presets">HUB75 Presets:</span>
          <button type="button" class="res-chip" data-res="64x32" onclick="setStudioBaseRes(64, 32)">64×32</button>
          <button type="button" class="res-chip active" data-res="128x32" onclick="setStudioBaseRes(128, 32)">128×32</button>
          <button type="button" class="res-chip" data-res="64x64" onclick="setStudioBaseRes(64, 64)">64×64</button>
          <button type="button" class="res-chip" data-res="128x64" onclick="setStudioBaseRes(128, 64)">128×64</button>
          <button type="button" class="res-chip" data-res="256x64" onclick="setStudioBaseRes(256, 64)">256×64</button>
          <button type="button" id="btn-studio-tate" class="tate-btn" onclick="toggleStudioTate()" data-i18n="studio_btn_tate">
            ⇄ Mode TATE (Vertical)
          </button>
        </div>
      </div>

      <!-- TWO-COLUMN WORKSPACE: CUSTOMIZER (LEFT) + LED SIMULATOR & ACTIVE DMD QUEUE (RIGHT) -->
      <div class="studio-grid">
        <!-- LEFT COLUMN: CARD & SEQUENCE BUILDER -->
        <div>
          <div class="card">
            <h2 data-i18n="studio_card_sec_title">1. Mode 2B Card Layout, Theme &amp; Scroll</h2>
            <p class="desc" data-i18n="studio_card_sec_desc">
              Every tweak updates the binary RGB565 Card stream in under 100 ms. Layouts automatically reflow in TATE vertical orientation.
            </p>

            <label data-i18n="studio_lbl_layout">Pixel-Art Card Layout (5 Presets)</label>
            <div class="layout-cards-grid" id="studio-layout-grid">
              <button type="button" class="layout-card-btn active" data-layout="classic_split" onclick="selectStudioLayout('classic_split')">
                <span class="layout-wire"><span style="width:36%;background:#ff6d00;opacity:0.75"></span><span style="flex:1;padding:2px"><span style="display:block;height:3px;background:#e6edf3;margin-bottom:2px"></span><span style="display:block;height:2px;background:#00e5ff;width:70%"></span></span></span>
                <span data-i18n="lay_classic_split">Classic Split</span>
              </button>
              <button type="button" class="layout-card-btn" data-layout="poster_right" onclick="selectStudioLayout('poster_right')">
                <span class="layout-wire"><span style="flex:1;padding:2px"><span style="display:block;height:3px;background:#e6edf3;margin-bottom:2px"></span><span style="display:block;height:2px;background:#00e5ff;width:70%"></span></span><span style="width:36%;background:#ff6d00;opacity:0.75"></span></span>
                <span data-i18n="lay_poster_right">Poster Right</span>
              </button>
              <button type="button" class="layout-card-btn" data-layout="badge_header" onclick="selectStudioLayout('badge_header')">
                <span class="layout-wire" style="flex-direction:column"><span style="height:5px;background:#ff6d00"></span><span style="flex:1;display:flex"><span style="width:32%;background:#2c394f"></span><span style="flex:1"></span></span></span>
                <span data-i18n="lay_badge_header">Badge Header</span>
              </button>
              <button type="button" class="layout-card-btn" data-layout="hero_banner" onclick="selectStudioLayout('hero_banner')">
                <span class="layout-wire" style="flex-direction:column;background:linear-gradient(180deg,#2c394f,#06080d)"><span style="flex:1"></span><span style="height:7px;background:rgba(0,0,0,0.8);border-top:1px solid #00e5ff"></span></span>
                <span data-i18n="lay_hero_banner">Hero Banner</span>
              </button>
              <button type="button" class="layout-card-btn" data-layout="minimal_arcade" onclick="selectStudioLayout('minimal_arcade')">
                <span class="layout-wire" style="flex-direction:column;justify-content:center;padding:0 4px;gap:2px"><span style="height:4px;background:#ffab00"></span><span style="height:2px;background:#8b949e;width:80%"></span></span>
                <span data-i18n="lay_minimal_arcade">Minimal Text</span>
              </button>
            </div>

            <div class="form-grid-3">
              <div class="field">
                <label data-i18n="studio_lbl_theme">Visual Theme (card_theme)</label>
                <select id="studio-theme" onchange="scheduleStudioCardPreview()">
                  <option value="tinted" data-i18n="thm_tinted">Tinted Dark (Badge Glow)</option>
                  <option value="pure_black" data-i18n="thm_pure_black">Pure Black HUB75 (Zero Glow)</option>
                  <option value="neon_border" data-i18n="thm_neon_border">Neon Arcade Frame (1px)</option>
                  <option value="poster_wash" data-i18n="thm_poster_wash">Poster Color Wash</option>
                  <option value="amber_dmd" data-i18n="thm_amber_dmd">Amber Pinball DMD (#FF7300)</option>
                </select>
              </div>
              <div class="field">
                <label data-i18n="studio_lbl_font">Pixel Typography (card_font)</label>
                <select id="studio-font" onchange="scheduleStudioCardPreview()">
                  <option value="arcade_pixel" data-i18n="fnt_arcade_pixel">Arcade Pixel (1-bit Crisp)</option>
                  <option value="clean_sans" data-i18n="fnt_clean_sans">Clean Sans (Smooth)</option>
                </select>
              </div>
              <div class="field">
                <label data-i18n="studio_lbl_accent">Accent Color</label>
                <div class="input-row">
                  <select id="studio-accent-mode" onchange="onStudioAccentModeChange()" style="flex:1">
                    <option value="auto" data-i18n="opt_accent_auto">Auto (Badge)</option>
                    <option value="custom" data-i18n="opt_accent_custom">Custom #Hex</option>
                  </select>
                  <input type="color" id="studio-accent-picker" class="studio-color" value="#ff6d00" onchange="scheduleStudioCardPreview()" disabled />
                </div>
              </div>
            </div>

            <div class="studio-toggles">
              <label class="chk-pill"><input type="checkbox" id="st-inc-card" class="studio-chk" checked onchange="scheduleStudioCardPreview()" /> <span data-i18n="st_tog_card">Card Intro</span></label>
              <label class="chk-pill"><input type="checkbox" id="st-inc-poster" class="studio-chk" checked onchange="scheduleStudioCardPreview()" /> <span data-i18n="st_tog_poster">Poster / Boxart</span></label>
              <label class="chk-pill"><input type="checkbox" id="st-show-badge" class="studio-chk" checked onchange="scheduleStudioCardPreview()" /> <span data-i18n="st_tog_badge">System / Genre Badge</span></label>
              <label class="chk-pill"><input type="checkbox" id="st-show-year" class="studio-chk" checked onchange="scheduleStudioCardPreview()" /> <span data-i18n="st_tog_year">Year</span></label>
              <label class="chk-pill"><input type="checkbox" id="st-show-rating" class="studio-chk" checked onchange="scheduleStudioCardPreview()" /> <span data-i18n="st_tog_rating">★ Rating</span></label>
              <label class="chk-pill"><input type="checkbox" id="st-inc-synopsis" class="studio-chk" checked onchange="scheduleStudioCardPreview()" /> <span data-i18n="st_tog_synopsis">Synopsis Ticker</span></label>
            </div>

            <div class="form-grid-2">
              <div class="field">
                <label><span data-i18n="studio_lbl_scroll_speed">Scroll Speed (1..10)</span>: <b id="lbl-val-scroll" style="color:var(--cyan)">4 (Normal)</b></label>
                <input type="range" id="st-scroll-speed" class="studio-range" min="1" max="10" step="1" value="4" oninput="updateStudioSliderLabels(); scheduleStudioCardPreview();" />
              </div>
              <div class="field">
                <label><span data-i18n="studio_lbl_card_hold">Intro Static Hold (s)</span>: <b id="lbl-val-hold" style="color:var(--cyan)">0.9s</b></label>
                <input type="range" id="st-card-hold" class="studio-range" min="0.2" max="4.0" step="0.1" value="0.9" oninput="updateStudioSliderLabels(); scheduleStudioCardPreview();" />
              </div>
            </div>
          </div>

          <div class="card">
            <h2 data-i18n="studio_seq_sec_title">2. Full Sequence Content (Panoramas, Screenshots &amp; Video)</h2>
            <p class="desc" data-i18n="studio_seq_sec_desc">
              Choose which visual chapters follow the Info Card (ArcadeMatrix parity + extended screenshot &amp; framing controls).
            </p>
            <div class="studio-toggles" style="margin-top:0">
              <label class="chk-pill"><input type="checkbox" id="st-jacket-pan" class="studio-chk" checked onchange="markStudioFullDirty()" /> <span data-i18n="st_tog_jacket_pan">Poster Vertical Scan</span></label>
              <label class="chk-pill"><input type="checkbox" id="st-marquee-pan" class="studio-chk" onchange="markStudioFullDirty()" /> <span data-i18n="st_tog_marquee_pan">Marquee / Backdrop Scan</span></label>
              <label class="chk-pill"><input type="checkbox" id="st-inc-video" class="studio-chk" checked onchange="markStudioFullDirty()" /> <span data-i18n="st_tog_video">Video Trailer / Gameplay</span></label>
            </div>
            <div class="form-grid-3">
              <div class="field">
                <label data-i18n="studio_lbl_snaps">Screenshots Count (0..5)</label>
                <select id="st-snap-count" onchange="markStudioFullDirty()">
                  <option value="0">0</option>
                  <option value="1">1</option>
                  <option value="2" selected>2</option>
                  <option value="3">3</option>
                  <option value="4">4</option>
                  <option value="5">5</option>
                </select>
              </div>
              <div class="field">
                <label data-i18n="studio_lbl_pan_dur">Panorama Scan Duration (s)</label>
                <input type="number" id="st-pan-dur" min="1.5" max="15" step="0.5" value="3.5" onchange="markStudioFullDirty()" />
              </div>
              <div class="field">
                <label data-i18n="studio_lbl_vid_dur">Max Video Clip (s)</label>
                <input type="number" id="st-vid-dur" min="1.5" max="60" step="0.5" value="8.0" onchange="markStudioFullDirty()" />
              </div>
              <div class="field">
                <label data-i18n="studio_lbl_vid_fps">Stream Frame Rate (FPS)</label>
                <select id="st-vid-fps" onchange="scheduleStudioCardPreview()">
                  <option value="12">12 FPS</option>
                  <option value="15" selected>15 FPS</option>
                  <option value="20">20 FPS</option>
                  <option value="25">25 FPS</option>
                  <option value="30">30 FPS</option>
                </select>
              </div>
              <div class="field">
                <label data-i18n="studio_lbl_vid_type">Preferred Clip Type (video_type)</label>
                <select id="st-video-type" onchange="markStudioFullDirty()">
                  <option value="any" data-i18n="opt_vtype_any">Any (Gameplay / Trailer / Clip)</option>
                  <option value="trailer" data-i18n="opt_vtype_trailer">Official Trailer (Movies / Series)</option>
                  <option value="opening" data-i18n="opt_vtype_opening">Opening Theme OP (Anime)</option>
                  <option value="ending" data-i18n="opt_vtype_ending">Ending Theme ED (Anime)</option>
                  <option value="pv" data-i18n="opt_vtype_pv">Promotional Video PV</option>
                </select>
              </div>
              <div class="field">
                <label data-i18n="lbl_video_mode">Video Framing Mode (video_mode)</label>
                <select id="st-video-mode" onchange="markStudioFullDirty()">
                  <option value="auto_action" data-i18n="opt_vmode_auto">Auto (Let Me Handle It AI)</option>
                  <option value="panorama" data-i18n="opt_vmode_panorama">Panorama (Top-Bottom Scan)</option>
                  <option value="center_crop" data-i18n="opt_vmode_crop">Static Center Crop</option>
                </select>
              </div>
              <div class="field">
                <label data-i18n="lbl_color_boost">LED Color Boost (color_boost)</label>
                <select id="st-color-boost" onchange="scheduleStudioCardPreview()">
                  <option value="auto" data-i18n="opt_cboost_auto">Auto (Let Me Handle It AI)</option>
                  <option value="pixel_art" data-i18n="opt_cboost_pixel">Pixel Art (Crisp Retro)</option>
                  <option value="anime" data-i18n="opt_cboost_anime">Anime (Vibrant Cel-Shaded)</option>
                  <option value="cinema" data-i18n="opt_cboost_cinema">Cinema (Balanced Contrast &amp; Shadows)</option>
                </select>
              </div>
            </div>
          </div>
        </div>

        <!-- RIGHT COLUMN: REAL-TIME MOD SIM DMD + ACTIVE DMD QUEUE -->
        <div>
          <div class="card">
            <div class="sim-toolbar">
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <b style="color:var(--cyan)" data-i18n="studio_sim_title">HUB75 Mod Sim DMD</b>
                <span id="studio-sim-badge" class="pill ok">⚡ Card Preview</span>
              </div>
              <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                <select id="studio-led-mode" onchange="redrawCurrentStudioFrame()" style="width:auto;height:32px;line-height:30px;font-size:0.8rem;padding:0 28px 0 8px">
                  <option value="smd_round" data-i18n="sim_mode_smd">💡 SMD Round LEDs</option>
                  <option value="pixel_grid" data-i18n="sim_mode_grid">🟦 Square LED Grid</option>
                  <option value="raw" data-i18n="sim_mode_raw">🖼️ Raw 1:1 Pixels</option>
                </select>
                <select id="studio-led-zoom" onchange="redrawCurrentStudioFrame()" style="width:auto;height:32px;line-height:30px;font-size:0.8rem;padding:0 28px 0 8px">
                  <option value="auto" data-i18n="sim_zoom_auto">Zoom: Auto</option>
                  <option value="3">3×</option>
                  <option value="4">4×</option>
                  <option value="6">6×</option>
                  <option value="8">8×</option>
                </select>
              </div>
            </div>

            <div class="sim-stage-wrap">
              <canvas id="studio-dmd-canvas" width="512" height="128"></canvas>
              <div style="display:flex;justify-content:space-between;width:100%;margin-top:8px;font-size:0.76rem;color:var(--muted)">
                <span id="studio-sim-meta">128×32 • 15 FPS</span>
                <span id="studio-sim-frame-lbl">Frame 0 / 0</span>
              </div>
            </div>

            <div id="studio-chapters-bar" class="chapter-pills"></div>

            <div class="sim-controls-row">
              <button type="button" class="secondary mini-btn" onclick="studioStepFrame(-1)">⏮</button>
              <button type="button" class="secondary mini-btn" id="btn-studio-play" onclick="toggleStudioPlay()">⏸</button>
              <button type="button" class="secondary mini-btn" onclick="studioStepFrame(1)">⏭</button>
              <input type="range" id="studio-scrubber" class="studio-range" min="0" max="0" value="0" style="flex:1;min-width:110px" oninput="onStudioScrub(this.value)" />
            </div>

            <div class="form-actions" style="margin-top:12px">
              <button type="button" class="secondary" onclick="requestStudioCardPreviewNow()" data-i18n="studio_btn_fast_card">
                ⚡ Refresh Card (&lt;100ms)
              </button>
              <button type="button" id="btn-studio-full-sim" onclick="runStudioFullPreview()" data-i18n="studio_btn_full_sim">
                🎬 Simulate Full Sequence (Card + Snaps + Video)
              </button>
            </div>
            <div id="studio-sim-status" class="toast" style="margin-top:8px"></div>
          </div>

          <!-- ACTIVE DMD LIVE QUEUE INJECTION -->
          <div class="card">
            <h2>
              <span data-i18n="studio_queue_sec_title">3. Active DMD &amp; Live Simulation Queue</span>
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                <button type="button" class="secondary mini-btn" onclick="syncStudioResFromActiveDmd()" data-i18n="studio_btn_sync_res">📐 Match DMD Res</button>
                <button type="button" class="secondary mini-btn" onclick="skipStudioTargetDmd()" data-i18n="btn_skip_now">⏭ Skip Current</button>
              </div>
            </h2>
            <p class="desc" data-i18n="studio_queue_sec_desc">
              Inject this exact customized simulation into the active DMD's queue (0 ms instant promotion without re-transcoding when Full Sequence matches DMD geometry) or save the style as its default Mode 2B profile.
            </p>

            <div class="form-grid-2">
              <div class="field">
                <label data-i18n="studio_lbl_active_dmd">Active DMD Matrix</label>
                <select id="studio-target-dmd" onchange="onStudioTargetDmdChange()">
                  <option value="" data-i18n="studio_opt_no_dmd">— Select or connect a DMD —</option>
                </select>
              </div>
              <div class="field">
                <label data-i18n="lbl_mark_seen">Consume-Once History Policy</label>
                <select id="studio-mark-seen">
                  <option value="false" data-i18n="opt_mark_seen_no">Keep repeatable (FrontendSync mode)</option>
                  <option value="true" data-i18n="opt_mark_seen_yes">Mark as Seen after playback</option>
                </select>
              </div>
            </div>

            <div id="studio-active-dmd-banner" style="margin-top:10px;padding:8px 11px;background:#0d121b;border:1px solid var(--border);border-radius:8px;font-size:0.82rem;color:var(--muted)"></div>

            <div class="form-actions" style="margin-top:12px">
              <button type="button" onclick="enqueueStudioSim('enqueue_next')" data-i18n="studio_btn_add_sim_queue">
                ➕ Add Simulation to Active DMD Queue
              </button>
              <button type="button" class="secondary" onclick="enqueueStudioSim('play_now')" data-i18n="studio_btn_play_sim_now">
                ⚡ Broadcast Simulation Now
              </button>
              <button type="button" class="secondary" onclick="saveStudioStyleToDmdProfile(false)" data-i18n="studio_btn_save_dom_prof">
                💾 Save Domain Profile to DMD
              </button>
              <button type="button" class="secondary" onclick="saveStudioStyleToDmdProfile(true)" data-i18n="studio_btn_copy_all_doms">
                📋 Apply to All 4 Domains
              </button>
            </div>
            <div id="studio-queue-action-msg" class="toast" style="margin-top:8px"></div>

            <div id="studio-dmd-queue-list" style="margin-top:12px;font-size:0.84rem;color:var(--muted)"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 5: API KEYS & ENCRYPTED VAULT -->
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
              <option value="pt">Português (pt)</option>
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

  <!-- MULTI-SELECT TAG PICKER MODAL (SYSTEMS / GENRES & DECADES) -->
  <div id="tag-picker-modal" class="modal-backdrop" onclick="if(event.target===this) closeTagPickerModal()">
    <div class="tag-modal" role="dialog" aria-modal="true">
      <div class="tag-modal-head">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px">
          <b id="tag-modal-title" style="font-size:1.02rem;color:#fff">🏷️ Select Tags</b>
          <button type="button" class="secondary mini-btn" onclick="closeTagPickerModal()" data-i18n="btn_close">✕ Close</button>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input type="text" id="tag-modal-search" oninput="renderTagPickerOptions()" data-i18n-placeholder="ph_tag_search" placeholder="🔍 Filter systems, genres, or decades..." style="flex:1;min-width:200px;height:36px" />
          <button type="button" class="secondary mini-btn" onclick="clearAllTagModal()" data-i18n="btn_tag_clear_all">✨ All (No Filter)</button>
          <button type="button" class="secondary mini-btn" onclick="selectVisibleTagModal()" data-i18n="btn_tag_select_vis">✓ Select Visible</button>
        </div>
      </div>
      <div class="tag-modal-body" id="tag-modal-body"></div>
      <div class="tag-modal-foot">
        <span id="tag-modal-count" style="font-size:0.82rem;color:var(--muted)">0 selected</span>
        <div style="display:flex;gap:8px">
          <button type="button" class="secondary" onclick="closeTagPickerModal()" data-i18n="btn_cancel">Cancel</button>
          <button type="button" onclick="applyTagPickerModal()" data-i18n="btn_apply_tags">✓ Apply Selection</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const I18N = {
      en: {
        header_sub: "MQTT Retain (:1883) + Real-Time UDP • Consume-Once • HUB75 LED Matrices",
        status_online: "● Online",
        status_offline: "● Unreachable",
        tab_overview: "1. Overview & DMD Matrices",
        tab_runtime: "2. Runtime, Workers & Video Limits",
        tab_tester: "3. Search & On-Demand Queue",
        tab_studio: "4. Studio Card & Sim DMD (2B)",
        tab_keys: "5. API Keys & Encrypted Vault",
        tab_network: "6. Network, MQTT & Maintenance",
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
        chains_title: "Metadata & Trailer Sources ([Primary, Fallback])",
        chains_desc: "Each domain queries its Primary source first and seamlessly switches to its Fallback source if unconfigured or rate-limited.",
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
        vault_missing: "⚠️ Unconfigured (Fallback active)",
        btn_new_profile: "➕ New Passive DMD Profile",
        th_actions: "Control",
        prof_editor_title: "DMD Profile & Server-Managed Playlist (dmd_profiles.json)",
        btn_close: "✕ Close",
        prof_editor_desc: "Configure passive ESP32 / RPi receivers directly from the Showcase server (Mode 2A Hybrid or Mode 2B 100% Passive UDP), set their broadcast content language, and curate multi-domain rotation playlists persisted in ./data/dmd_profiles.json.",
        lbl_prof_id: "DMD Identifier (dmd_id)",
        lbl_prof_name: "Friendly Display Name",
        lbl_prof_mode: "DMD Operating Mode",
        opt_mode_2a: "Mode 2A — Server Playlist + Local 60 FPS Card",
        opt_mode_2b: "Mode 2B — 100% Passive UDP Framebuffer",
        opt_mode_1: "Mode 1 — Client-Driven (e.g. ArcadeMatrix)",
        lbl_prof_lang: "Broadcast Content Language (lang)",
        lbl_prof_always_udp: "Continuous UDP Push (without MQTT)",
        opt_udp_presence: "Wait for MQTT / HTTP Hello Presence",
        opt_udp_always: "Always Stream UDP to LAN IP (Pure UDP Sketch)",
        lbl_prof_ip: "Target DMD LAN IP (udp_ip)",
        lbl_prof_port: "UDP Frame Port (udp_port)",
        lbl_prof_gap: "Inter-Item Transition Gap (s — 0..10)",
        th_pl_on: "On",
        th_pl_count: "Items / Turn",
        th_pl_systems: "Systems (Games CSV)",
        th_pl_genres: "Genres (CSV)",
        th_pl_framing: "Framing",
        th_pl_cboost: "Color Boost",
        th_pl_vdur: "Max Video (s)",
        btn_save_profile: "💾 Save Profile & Apply Live",
        btn_delete_profile: "🗑️ Delete Profile",
        lbl_target_dmd: "Target DMD Matrix (for Queue / Play Now)",
        opt_target_browser: "🖥️ Browser Preview Only",
        lbl_content_lang: "Content Broadcast Language (lang)",
        lbl_mark_seen: "Consume-Once History Policy",
        opt_mark_seen_yes: "Mark as Seen after playback",
        opt_mark_seen_no: "Keep repeatable (FrontendSync mode)",
        btn_search_cands: "🔍 Search / Browse Candidates",
        queue_panel_title: "On-Demand Priority Queue (Target DMD)",
        btn_skip_now: "⏭ Skip Current",
        queue_panel_desc: "Items added with Queue Next play in strict FIFO order right after the current item without losing pre-transcoded random slots. Play Now preempts immediately.",
        queue_select_dmd: "Select a Target DMD above to inspect or control its live priority queue.",
        queue_empty: "Priority queue is currently empty — standard rotation active.",
        mode_badge_client: "Client",
        mode_badge_2a: "Server • 2A Hybrid",
        mode_badge_2b: "Server • 2B UDP",
        pill_paused: "⏸ Paused",
        btn_queue_next: "➕ Queue Next",
        btn_play_now: "⚡ Play Now",
        btn_preview_cand: "👁️ Preview",
        msg_searching: "⏳ Searching catalog...",
        msg_search_count: "✓ {n} candidate(s) found ({l})",
        msg_queued_ok: "✓ Sent '{t}' to {d} ({m})",
        msg_need_target_dmd: "⚠️ Select a Target DMD first to enqueue or play on hardware.",
        msg_prof_saved: "✓ DMD profile '{d}' saved & applied live!",
        msg_prof_deleted: "✓ DMD profile '{d}' deleted.",
        msg_prof_id_req: "⚠️ Please enter a DMD ID (e.g. esp32_cab_1).",
        studio_title: "Mode 2B Card Studio & Real-Time HUB75 DMD Simulator",
        studio_desc: "Search any game, movie, series, or anime via the On-Demand catalog, customize the Mode 2B pixel-art Info Card & sequence options across standard and TATE resolutions, preview on the live HUB75 LED Simulator, and push your simulation directly into the active DMD queue.",
        studio_lbl_search: "Search Catalog Content (Title / ROM / Franchise)",
        studio_btn_search: "🔍 Search",
        studio_lbl_res_presets: "HUB75 Presets:",
        studio_btn_tate: "⇄ Mode TATE (Vertical)",
        studio_card_sec_title: "1. Mode 2B Card Layout, Theme & Scroll",
        studio_card_sec_desc: "Every tweak updates the binary RGB565 Card stream in under 100 ms. Layouts automatically reflow in TATE vertical orientation.",
        studio_lbl_layout: "Pixel-Art Card Layout (5 Presets)",
        lay_classic_split: "Classic Split",
        lay_poster_right: "Poster Right",
        lay_badge_header: "Badge Header",
        lay_hero_banner: "Hero Banner",
        lay_minimal_arcade: "Minimal Text",
        studio_lbl_theme: "Visual Theme (card_theme)",
        thm_tinted: "Tinted Dark (Badge Glow)",
        thm_pure_black: "Pure Black HUB75 (Zero Glow)",
        thm_neon_border: "Neon Arcade Frame (1px)",
        thm_poster_wash: "Poster Color Wash",
        thm_amber_dmd: "Amber Pinball DMD (#FF7300)",
        studio_lbl_font: "Pixel Typography (card_font)",
        fnt_arcade_pixel: "Arcade Pixel (1-bit Crisp)",
        fnt_clean_sans: "Clean Sans (Smooth)",
        studio_lbl_accent: "Accent Color",
        opt_accent_auto: "Auto (Badge)",
        opt_accent_custom: "Custom #Hex",
        st_tog_card: "Card Intro",
        st_tog_poster: "Poster / Boxart",
        st_tog_badge: "System / Genre Badge",
        st_tog_year: "Year",
        st_tog_rating: "★ Rating",
        st_tog_synopsis: "Synopsis Ticker",
        studio_lbl_scroll_speed: "Scroll Speed (1..10)",
        studio_lbl_card_hold: "Intro Static Hold (s)",
        studio_seq_sec_title: "2. Full Sequence Content (Panoramas, Screenshots & Video)",
        studio_seq_sec_desc: "Choose which visual chapters follow the Info Card (ArcadeMatrix parity + extended screenshot & framing controls).",
        st_tog_jacket_pan: "Poster Vertical Scan",
        st_tog_marquee_pan: "Marquee / Backdrop Scan",
        st_tog_video: "Video Trailer / Gameplay",
        studio_lbl_snaps: "Screenshots Count (0..5)",
        studio_lbl_pan_dur: "Panorama Scan Duration (s)",
        studio_lbl_vid_dur: "Max Video Clip (s)",
        studio_lbl_vid_fps: "Stream Frame Rate (FPS)",
        studio_sim_title: "HUB75 Mod Sim DMD",
        sim_mode_smd: "💡 SMD Round LEDs",
        sim_mode_grid: "🟦 Square LED Grid",
        sim_mode_raw: "🖼️ Raw 1:1 Pixels",
        sim_zoom_auto: "Zoom: Auto",
        studio_btn_fast_card: "⚡ Refresh Card (<100ms)",
        studio_btn_full_sim: "🎬 Simulate Full Sequence (Card + Snaps + Video)",
        studio_queue_sec_title: "3. Active DMD & Live Simulation Queue",
        studio_btn_sync_res: "📐 Match DMD Res",
        studio_queue_sec_desc: "Inject this exact customized simulation into the active DMD's queue (0 ms instant promotion without re-transcoding when Full Sequence matches DMD geometry) or save the style as its default Mode 2B profile.",
        studio_lbl_active_dmd: "Active DMD Matrix",
        studio_opt_no_dmd: "— Select or connect a DMD —",
        studio_btn_add_sim_queue: "➕ Add Simulation to Active DMD Queue",
        studio_btn_play_sim_now: "⚡ Broadcast Simulation Now",
        studio_btn_save_2b_prof: "💾 Save Mode 2B Style to DMD",
        btn_open_studio: "🎨 Studio",
        msg_studio_card_ok: "✓ Card stream rendered ({f} frames @ {fps} FPS)",
        msg_studio_full_building: "⏳ Building full sequence (Card + Panoramas + Snapshots + Video)...",
        msg_studio_full_ok: "✓ Full sequence ready ({f} frames • 0ms Queue Ready)",
        msg_studio_sim_queued_0ms: "✓ Simulation '{t}' injected into {d} in 0ms (zero re-transcode)!",
        msg_studio_sim_queued_bg: "✓ Simulation '{t}' queued on {d} ({s})",
        msg_studio_2b_saved: "✓ Saved Mode 2B Card Style on DMD '{d}'!",
        studio_badge_card_fast: "⚡ Fast Card Preview",
        studio_badge_full_0ms: "🎬 Full Sequence (0ms Queue)",
        studio_dmd_idle: "Idle / Ready for broadcast",
        studio_dmd_res_mismatch: "Note: Studio is {sw}×{sh} while '{d}' is {dw}×{dh} (will adapt on queue).",
        pill_sim_studio: "🎨 Studio Sim",
        prof_rotation_hint: "💡 Pick Systems, Genres & Decades with visual tags (empty = All). Video duration, framing, color boost, and Mode 2B Card layout are configured per domain inside Studio — click any domain's Studio button on the right to customize it.",
        th_pl_systems_tags: "Systems (Games)",
        th_pl_genres_tags: "Genres & Decades",
        th_pl_studio_link: "Studio Profile (Card, Framing & Video)",
        tag_all_systems: "✨ All systems",
        tag_all_genres: "✨ All genres & eras",
        btn_pick_tags: "+ Filter",
        btn_edit_tags: "✎ Tags",
        btn_open_dom_studio: "🎨 Studio {d} →",
        tag_modal_sys_title: "🏷️ Select Game Systems",
        tag_modal_gen_title: "🏷️ Select Genres & Decades — {d}",
        tag_sec_systems: "🕹️ Arcade & Console Platforms",
        tag_sec_genres: "🎭 Categories & Genres",
        tag_sec_decades: "📅 Decades & Eras",
        ph_tag_search: "🔍 Filter systems, genres, or decades...",
        btn_tag_clear_all: "✨ All (No Filter)",
        btn_tag_select_vis: "✓ Select Visible",
        btn_cancel: "Cancel",
        btn_apply_tags: "✓ Apply Selection",
        tag_selected_count: "{n} tag(s) selected (0 = All)",
        tag_more_fmt: "+{n} more",
        studio_prof_bar_title: "🎯 Domain Profiles on DMD:",
        btn_back_rotation: "← Back to DMD Rotation",
        studio_btn_save_dom_prof: "💾 Save Domain Profile to DMD",
        studio_btn_copy_all_doms: "📋 Apply to All 4 Domains",
        dom_prof_games: "🕹️ Games Profile",
        dom_prof_movies: "🎬 Movies Profile",
        dom_prof_series: "📺 Series Profile",
        dom_prof_anime: "⛩️ Anime Profile",
        studio_lbl_vid_type: "Preferred Clip Type (video_type)",
        opt_vtype_any: "Any (Gameplay / Trailer / Clip)",
        opt_vtype_trailer: "Official Trailer (Movies / Series)",
        opt_vtype_opening: "Opening Theme OP (Anime)",
        opt_vtype_ending: "Ending Theme ED (Anime)",
        opt_vtype_pv: "Promotional Video PV",
        msg_studio_dom_saved: "✓ Saved '{dom}' Studio profile on DMD '{d}'!",
        msg_studio_all_doms_saved: "✓ Applied Studio profile across all 4 domains on DMD '{d}'!"
      },
      fr: {
        header_sub: "MQTT Retain (:1883) + UDP Temps Réel • Consume-Once • Matrices LED HUB75",
        status_online: "● En ligne",
        status_offline: "● Injoignable",
        tab_overview: "1. Vue d'ensemble & Matrices DMD",
        tab_runtime: "2. Runtime, Workers & Limites Vidéo",
        tab_tester: "3. Recherche & File On-Demand",
        tab_studio: "4. Studio Card & Sim DMD (2B)",
        tab_keys: "5. Clés API & Coffre Chiffré",
        tab_network: "6. Réseau, MQTT & Maintenance",
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
        chains_title: "Sources Métadonnées & Bandes-Annonces ([Primaire, Secours])",
        chains_desc: "Chaque domaine interroge sa source Primaire et bascule automatiquement sur sa source de Secours en cas de quota atteint ou d'absence de clé.",
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
        vault_missing: "⚠️ Non configurée (Fallback actif)",
        btn_new_profile: "➕ Nouveau Profil DMD Passif",
        th_actions: "Pilotage",
        prof_editor_title: "Profil DMD & Playlist Pilotée Serveur (dmd_profiles.json)",
        btn_close: "✕ Fermer",
        prof_editor_desc: "Pilote les récepteurs ESP32 / RPi passifs directement depuis Showcase (Mode 2A Hybride ou Mode 2B 100% Framebuffer UDP), choisis la langue du contenu diffusé et compose la playlist multi-domaines persistée dans ./data/dmd_profiles.json.",
        lbl_prof_id: "Identifiant DMD (dmd_id)",
        lbl_prof_name: "Nom d'Affichage",
        lbl_prof_mode: "Mode de Fonctionnement DMD",
        opt_mode_2a: "Mode 2A — Playlist Serveur + Fiche 60 FPS Locale",
        opt_mode_2b: "Mode 2B — 100% Passif Framebuffer UDP",
        opt_mode_1: "Mode 1 — Piloté par le Client (ex : ArcadeMatrix)",
        lbl_prof_lang: "Langue du Contenu Diffusé (lang)",
        lbl_prof_always_udp: "Diffusion UDP Continue (sans MQTT)",
        opt_udp_presence: "Attendre la présence MQTT / HTTP Hello",
        opt_udp_always: "Diffuser en continu vers l'IP LAN (Sketch UDP pur)",
        lbl_prof_ip: "IP LAN du DMD Cible (udp_ip)",
        lbl_prof_port: "Port UDP Frames (udp_port)",
        lbl_prof_gap: "Pause Inter-Œuvres (s — 0..10)",
        th_pl_on: "Actif",
        th_pl_count: "Œuvres / Tour",
        th_pl_systems: "Systèmes (Jeux CSV)",
        th_pl_genres: "Genres (CSV)",
        th_pl_framing: "Cadrage",
        th_pl_cboost: "Boost Couleur",
        th_pl_vdur: "Vidéo Max (s)",
        btn_save_profile: "💾 Enregistrer le Profil & Appliquer",
        btn_delete_profile: "🗑️ Supprimer le Profil",
        lbl_target_dmd: "Matrice DMD Cible (Queue / Diffusion)",
        opt_target_browser: "🖥️ Aperçu Navigateur Uniquement",
        lbl_content_lang: "Langue du Contenu Diffusé (lang)",
        lbl_mark_seen: "Politique Consume-Once",
        opt_mark_seen_yes: "Marquer comme vu après diffusion",
        opt_mark_seen_no: "Répétable (mode FrontendSync)",
        btn_search_cands: "🔍 Rechercher / Explorer les Contenus",
        queue_panel_title: "File Prioritaire On-Demand (DMD Cible)",
        btn_skip_now: "⏭ Passer l'œuvre en cours",
        queue_panel_desc: "Les titres ajoutés en File Suivante passent dans l'ordre FIFO juste après l'œuvre en cours sans perdre les slots aléatoires pré-calculés. Diffuser Maintenant préempte immédiatement.",
        queue_select_dmd: "Sélectionne un DMD Cible ci-dessus pour inspecter ou piloter sa file prioritaire.",
        queue_empty: "La file prioritaire est vide — rotation automatique active.",
        mode_badge_client: "Client",
        mode_badge_2a: "Serveur • 2A Hybride",
        mode_badge_2b: "Serveur • 2B UDP",
        pill_paused: "⏸ En pause",
        btn_queue_next: "➕ File (Suivant)",
        btn_play_now: "⚡ Diffuser Maintenant",
        btn_preview_cand: "👁️ Aperçu",
        msg_searching: "⏳ Recherche dans le catalogue...",
        msg_search_count: "✓ {n} contenu(s) trouvé(s) ({l})",
        msg_queued_ok: "✓ « {t} » envoyé sur {d} ({m})",
        msg_need_target_dmd: "⚠️ Sélectionne d'abord un DMD Cible pour ajouter à la file ou diffuser.",
        msg_prof_saved: "✓ Profil DMD « {d} » enregistré et appliqué !",
        msg_prof_deleted: "✓ Profil DMD « {d} » supprimé.",
        msg_prof_id_req: "⚠️ Indique un identifiant DMD (ex : esp32_cab_1).",
        studio_title: "Studio Card Mode 2B & Simulateur DMD HUB75 Temps Réel",
        studio_desc: "Recherche un jeu, film, série ou anime via les APIs On-Demand, personnalise la Card pixel-art du Mode 2B et la séquence complète (YOKO & TATE), visualise le rendu sur le Mod Sim DMD, puis ajoute directement ta simulation à la queue du DMD en cours.",
        studio_lbl_search: "Rechercher un Contenu (Titre / ROM / Saga)",
        studio_btn_search: "🔍 Chercher",
        studio_lbl_res_presets: "Résolutions HUB75 :",
        studio_btn_tate: "⇄ Mode TATE (Vertical)",
        studio_card_sec_title: "1. Mise en Page Card Mode 2B, Thème & Défilement",
        studio_card_sec_desc: "Chaque réglage régénère le flux binaire RGB565 de la Card en moins de 100 ms. La mise en page s'adapte automatiquement en orientation verticale TATE.",
        studio_lbl_layout: "Disposition Pixel-Art de la Card (5 Presets)",
        lay_classic_split: "Split Classique",
        lay_poster_right: "Affiche Droite",
        lay_badge_header: "Bandeau Haut",
        lay_hero_banner: "Bannière Hero",
        lay_minimal_arcade: "Texte Minimal",
        studio_lbl_theme: "Thème Visuel (card_theme)",
        thm_tinted: "Sombre Teinté (Halo Badge)",
        thm_pure_black: "Noir Pur HUB75 (Zéro Halo)",
        thm_neon_border: "Cadre Néon Arcade (1px)",
        thm_poster_wash: "Fond Coloré Affiche",
        thm_amber_dmd: "DMD Flipper Ambre (#FF7300)",
        studio_lbl_font: "Typographie Pixel (card_font)",
        fnt_arcade_pixel: "Arcade Pixel (1-bit Net)",
        fnt_clean_sans: "Sans Lissé",
        studio_lbl_accent: "Couleur d'Accent",
        opt_accent_auto: "Auto (Badge)",
        opt_accent_custom: "#Hex Perso",
        st_tog_card: "Intro Card",
        st_tog_poster: "Affiche / Jaquette",
        st_tog_badge: "Badge Système / Genre",
        st_tog_year: "Année",
        st_tog_rating: "★ Note",
        st_tog_synopsis: "Synopsis Défilant",
        studio_lbl_scroll_speed: "Vitesse Défilement (1..10)",
        studio_lbl_card_hold: "Pause Fixe Intro (s)",
        studio_seq_sec_title: "2. Contenu de la Séquence (Panoramas, Screenshots & Vidéo)",
        studio_seq_sec_desc: "Choisis ce qui s'enchaîne après la Card (options ArcadeMatrix + nombre de screenshots, panoramas et cadrage vidéo).",
        st_tog_jacket_pan: "Panorama Jaquette",
        st_tog_marquee_pan: "Panorama Marquee / Backdrop",
        st_tog_video: "Bande-Annonce / Vidéo",
        studio_lbl_snaps: "Nombre de Screenshots (0..5)",
        studio_lbl_pan_dur: "Durée Balayage Panorama (s)",
        studio_lbl_vid_dur: "Durée Max Vidéo (s)",
        studio_lbl_vid_fps: "Cadence du Flux (FPS)",
        studio_sim_title: "Mod Sim DMD HUB75",
        sim_mode_smd: "💡 LEDs SMD Rondes",
        sim_mode_grid: "🟦 Grille Pixels Carrés",
        sim_mode_raw: "🖼️ Pixels Bruts 1:1",
        sim_zoom_auto: "Zoom : Auto",
        studio_btn_fast_card: "⚡ Rafraîchir Card (<100ms)",
        studio_btn_full_sim: "🎬 Simuler Séquence Complète (Card + Snaps + Vidéo)",
        studio_queue_sec_title: "3. DMD en Cours & Injection dans la Queue",
        studio_btn_sync_res: "📐 Copier Rés. DMD",
        studio_queue_sec_desc: "Ajoute cette simulation personnalisée directement à la queue du DMD en cours (promotion instantanée en 0 ms sans re-transcodage si la Séquence Complète a la même résolution) ou sauvegarde ce style comme profil Mode 2B.",
        studio_lbl_active_dmd: "DMD en Cours",
        studio_opt_no_dmd: "— Sélectionner ou connecter un DMD —",
        studio_btn_add_sim_queue: "➕ Ajouter cette Simulation à la Queue du DMD",
        studio_btn_play_sim_now: "⚡ Diffuser Simulation Maintenant",
        studio_btn_save_2b_prof: "💾 Sauver Style Mode 2B sur ce DMD",
        btn_open_studio: "🎨 Studio",
        msg_studio_card_ok: "✓ Flux Card généré ({f} frames @ {fps} FPS)",
        msg_studio_full_building: "⏳ Assemblage de la séquence complète (Card + Panoramas + Screenshots + Vidéo)...",
        msg_studio_full_ok: "✓ Séquence complète prête ({f} frames • Prête Queue 0ms)",
        msg_studio_sim_queued_0ms: "✓ Simulation « {t} » injectée dans la queue de {d} en 0 ms (zéro re-transcodage) !",
        msg_studio_sim_queued_bg: "✓ Simulation « {t} » ajoutée à la queue de {d} ({s})",
        msg_studio_2b_saved: "✓ Style Mode 2B sauvegardé sur le DMD « {d} » !",
        studio_badge_card_fast: "⚡ Aperçu Rapide Card",
        studio_badge_full_0ms: "🎬 Séquence Complète (Queue 0ms)",
        studio_dmd_idle: "En attente / Prêt à diffuser",
        studio_dmd_res_mismatch: "Note : le Studio est en {sw}×{sh} alors que « {d} » est en {dw}×{dh} (adaptation auto à l'envoi).",
        pill_sim_studio: "🎨 Sim Studio",
        prof_rotation_hint: "💡 Sélectionne les Systèmes, Genres et Époques par tags visuels (vide = Tous). La durée vidéo, le cadrage, le boost couleur et le design de la Card Mode 2B se règlent par domaine dans le Studio via le bouton à droite de chaque ligne.",
        th_pl_systems_tags: "Systèmes (Jeux)",
        th_pl_genres_tags: "Genres & Époques",
        th_pl_studio_link: "Profil Studio (Card, Cadrage & Vidéo)",
        tag_all_systems: "✨ Tous les systèmes",
        tag_all_genres: "✨ Tous genres & époques",
        btn_pick_tags: "+ Filtrer",
        btn_edit_tags: "✎ Tags",
        btn_open_dom_studio: "🎨 Studio {d} →",
        tag_modal_sys_title: "🏷️ Choisir les Systèmes de Jeux",
        tag_modal_gen_title: "🏷️ Choisir Genres & Époques — {d}",
        tag_sec_systems: "🕹️ Bornes d'Arcade & Consoles Rétro",
        tag_sec_genres: "🎭 Catégories & Genres",
        tag_sec_decades: "📅 Décennies & Époques",
        ph_tag_search: "🔍 Filtrer un système, genre ou décennie...",
        btn_tag_clear_all: "✨ Tous (Aucun filtre)",
        btn_tag_select_vis: "✓ Cocher visibles",
        btn_cancel: "Annuler",
        btn_apply_tags: "✓ Valider les tags",
        tag_selected_count: "{n} tag(s) sélectionné(s) (0 = Tous)",
        tag_more_fmt: "+{n} autres",
        studio_prof_bar_title: "🎯 Profils par Domaine du DMD :",
        btn_back_rotation: "← Retour à la Rotation du DMD",
        studio_btn_save_dom_prof: "💾 Sauver ce Profil Domaine sur le DMD",
        studio_btn_copy_all_doms: "📋 Appliquer aux 4 Domaines",
        dom_prof_games: "🕹️ Profil Jeux",
        dom_prof_movies: "🎬 Profil Films",
        dom_prof_series: "📺 Profil Séries",
        dom_prof_anime: "⛩️ Profil Anime",
        studio_lbl_vid_type: "Type d'Extrait Préféré (video_type)",
        opt_vtype_any: "Tous (Gameplay / Trailer / Extrait)",
        opt_vtype_trailer: "Bande-Annonce Officielle (Films / Séries)",
        opt_vtype_opening: "Générique Opening OP (Anime)",
        opt_vtype_ending: "Générique Ending ED (Anime)",
        opt_vtype_pv: "Vidéo Promo PV",
        msg_studio_dom_saved: "✓ Profil Studio « {dom} » enregistré sur le DMD « {d} » !",
        msg_studio_all_doms_saved: "✓ Profil Studio appliqué aux 4 domaines sur le DMD « {d} » !"
      },
      es: {
        header_sub: "MQTT Retain (:1883) + UDP en Tiempo Real • Consume-Once • Matrices LED HUB75",
        status_online: "● En línea",
        status_offline: "● Sin conexión",
        tab_overview: "1. Vista General y Matrices DMD",
        tab_runtime: "2. Runtime, Workers y Límites de Vídeo",
        tab_tester: "3. Búsqueda y Cola On-Demand",
        tab_studio: "4. Estudio Card y Sim DMD (2B)",
        tab_keys: "5. Claves API y Bóveda Cifrada",
        tab_network: "6. Red, MQTT y Mantenimiento",
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
        chains_title: "Fuentes de Metadatos y Tráilers ([Primaria, Respaldo])",
        chains_desc: "Cada dominio consulta primero su fuente Primaria y conmuta automáticamente a su fuente de Respaldo si falta clave o límite.",
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
        vault_missing: "⚠️ No configurada (Fallback activo)",
        btn_new_profile: "➕ Nuevo Perfil DMD Pasivo",
        th_actions: "Control",
        prof_editor_title: "Perfil DMD y Playlist Gestionada por Servidor (dmd_profiles.json)",
        btn_close: "✕ Cerrar",
        prof_editor_desc: "Configura receptores ESP32 / RPi pasivos directamente desde Showcase (Modo 2A Híbrido o Modo 2B 100% Framebuffer UDP), elige el idioma del contenido emitido y define listas multidominio persistidas en ./data/dmd_profiles.json.",
        lbl_prof_id: "Identificador DMD (dmd_id)",
        lbl_prof_name: "Nombre Visible",
        lbl_prof_mode: "Modo Operativo DMD",
        opt_mode_2a: "Modo 2A — Playlist Servidor + Ficha 60 FPS Local",
        opt_mode_2b: "Modo 2B — 100% Pasivo Framebuffer UDP",
        opt_mode_1: "Modo 1 — Dirigido por Cliente (ej: ArcadeMatrix)",
        lbl_prof_lang: "Idioma del Contenido Emitido (lang)",
        lbl_prof_always_udp: "Envío UDP Continuo (sin MQTT)",
        opt_udp_presence: "Esperar presencia MQTT / HTTP Hello",
        opt_udp_always: "Emitir UDP continuo a la IP LAN (Sketch UDP puro)",
        lbl_prof_ip: "IP LAN del DMD Destino (udp_ip)",
        lbl_prof_port: "Puerto UDP de Fotogramas (udp_port)",
        lbl_prof_gap: "Pausa Entre Títulos (s — 0..10)",
        th_pl_on: "Activo",
        th_pl_count: "Títulos / Turno",
        th_pl_systems: "Sistemas (Juegos CSV)",
        th_pl_genres: "Géneros (CSV)",
        th_pl_framing: "Encuadre",
        th_pl_cboost: "Realce Color",
        th_pl_vdur: "Vídeo Máx (s)",
        btn_save_profile: "💾 Guardar Perfil y Aplicar",
        btn_delete_profile: "🗑️ Eliminar Perfil",
        lbl_target_dmd: "Matriz DMD Destino (Cola / Emisión)",
        opt_target_browser: "🖥️ Solo Vista Previa en Navegador",
        lbl_content_lang: "Idioma del Contenido Emitido (lang)",
        lbl_mark_seen: "Política Consume-Once",
        opt_mark_seen_yes: "Marcar como visto tras emitir",
        opt_mark_seen_no: "Repetible (modo FrontendSync)",
        btn_search_cands: "🔍 Buscar / Explorar Candidatos",
        queue_panel_title: "Cola Prioritaria On-Demand (DMD Destino)",
        btn_skip_now: "⏭ Saltar actual",
        queue_panel_desc: "Los títulos añadidos en Cola Siguiente se reproducen en orden FIFO tras el elemento actual sin perder ranuras aleatorias. Emitir Ahora interrumpe de inmediato.",
        queue_select_dmd: "Selecciona un DMD Destino arriba para ver o controlar su cola prioritaria.",
        queue_empty: "La cola prioritaria está vacía — rotación estándar activa.",
        mode_badge_client: "Cliente",
        mode_badge_2a: "Servidor • 2A Híbrido",
        mode_badge_2b: "Servidor • 2B UDP",
        pill_paused: "⏸ Pausado",
        btn_queue_next: "➕ Cola Siguiente",
        btn_play_now: "⚡ Emitir Ahora",
        btn_preview_cand: "👁️ Vista Previa",
        msg_searching: "⏳ Buscando en el catálogo...",
        msg_search_count: "✓ {n} candidato(s) encontrado(s) ({l})",
        msg_queued_ok: "✓ '{t}' enviado a {d} ({m})",
        msg_need_target_dmd: "⚠️ Selecciona primero un DMD Destino para encolar o emitir.",
        msg_prof_saved: "✓ ¡Perfil DMD '{d}' guardado y aplicado!",
        msg_prof_deleted: "✓ Perfil DMD '{d}' eliminado.",
        msg_prof_id_req: "⚠️ Introduce un identificador DMD (ej: esp32_cab_1).",
        studio_title: "Estudio Card Modo 2B y Simulador DMD HUB75 en Tiempo Real",
        studio_desc: "Busca cualquier juego, película, serie o anime en el catálogo On-Demand, personaliza la Card pixel-art del Modo 2B y su secuencia (YOKO y TATE), previsualiza en el Simulador LED HUB75 e inyecta tu simulación directamente en la cola del DMD activo.",
        studio_lbl_search: "Buscar Contenido (Título / ROM / Franquicia)",
        studio_btn_search: "🔍 Buscar",
        studio_lbl_res_presets: "Resoluciones HUB75:",
        studio_btn_tate: "⇄ Modo TATE (Vertical)",
        studio_card_sec_title: "1. Diseño de Card Modo 2B, Tema y Desplazamiento",
        studio_card_sec_desc: "Cada ajuste actualiza el flujo binario RGB565 de la Card en menos de 100 ms. Los diseños se adaptan automáticamente en orientación vertical TATE.",
        studio_lbl_layout: "Diseño Pixel-Art de la Card (5 Ajustes)",
        lay_classic_split: "Split Clásico",
        lay_poster_right: "Póster Derecha",
        lay_badge_header: "Franja Superior",
        lay_hero_banner: "Banner Hero",
        lay_minimal_arcade: "Texto Minimal",
        studio_lbl_theme: "Tema Visual (card_theme)",
        thm_tinted: "Oscuro Teñido (Brillo)",
        thm_pure_black: "Negro Puro HUB75 (Cero Brillo)",
        thm_neon_border: "Marco Neón Arcade (1px)",
        thm_poster_wash: "Fondo de Color Póster",
        thm_amber_dmd: "DMD Pinball Ámbar (#FF7300)",
        studio_lbl_font: "Tipografía Pixel (card_font)",
        fnt_arcade_pixel: "Arcade Pixel (1-bit Nítido)",
        fnt_clean_sans: "Sans Suave",
        studio_lbl_accent: "Color de Acento",
        opt_accent_auto: "Auto (Insignia)",
        opt_accent_custom: "#Hex Personal",
        st_tog_card: "Intro Card",
        st_tog_poster: "Póster / Carátula",
        st_tog_badge: "Insignia Sistema / Género",
        st_tog_year: "Año",
        st_tog_rating: "★ Nota",
        st_tog_synopsis: "Sinopsis Deslizante",
        studio_lbl_scroll_speed: "Velocidad Desplazamiento (1..10)",
        studio_lbl_card_hold: "Pausa Inicial Fija (s)",
        studio_seq_sec_title: "2. Contenido de Secuencia (Panoramas, Capturas y Vídeo)",
        studio_seq_sec_desc: "Elige qué capítulos siguen a la Card (paridad ArcadeMatrix + número de capturas, panoramas y encuadre de vídeo).",
        st_tog_jacket_pan: "Barrido Vertical Póster",
        st_tog_marquee_pan: "Barrido Marquee / Fondo",
        st_tog_video: "Tráiler / Vídeo",
        studio_lbl_snaps: "Número de Capturas (0..5)",
        studio_lbl_pan_dur: "Duración Barrido Panorama (s)",
        studio_lbl_vid_dur: "Duración Máx Vídeo (s)",
        studio_lbl_vid_fps: "Frecuencia de Flujo (FPS)",
        studio_sim_title: "Mod Sim DMD HUB75",
        sim_mode_smd: "💡 LEDs SMD Redondos",
        sim_mode_grid: "🟦 Cuadrícula LED Cuadrada",
        sim_mode_raw: "🖼️ Píxeles Puros 1:1",
        sim_zoom_auto: "Zoom: Auto",
        studio_btn_fast_card: "⚡ Actualizar Card (<100ms)",
        studio_btn_full_sim: "🎬 Simular Secuencia Completa (Card + Capturas + Vídeo)",
        studio_queue_sec_title: "3. DMD Activo e Inyección en Cola",
        studio_btn_sync_res: "📐 Igualar Res. DMD",
        studio_queue_sec_desc: "Añade esta simulación personalizada directamente a la cola del DMD activo (promoción en 0 ms sin recodificar si la Secuencia Completa coincide en resolución) o guarda el estilo como perfil Modo 2B.",
        studio_lbl_active_dmd: "Matriz DMD Activa",
        studio_opt_no_dmd: "— Seleccionar o conectar un DMD —",
        studio_btn_add_sim_queue: "➕ Añadir Simulación a la Cola del DMD",
        studio_btn_play_sim_now: "⚡ Emitir Simulación Ahora",
        studio_btn_save_2b_prof: "💾 Guardar Estilo Modo 2B en DMD",
        btn_open_studio: "🎨 Estudio",
        msg_studio_card_ok: "✓ Flujo Card generado ({f} fotogramas @ {fps} FPS)",
        msg_studio_full_building: "⏳ Generando secuencia completa (Card + Panoramas + Capturas + Vídeo)...",
        msg_studio_full_ok: "✓ Secuencia completa lista ({f} fotogramas • Cola 0ms)",
        msg_studio_sim_queued_0ms: "✓ ¡Simulación '{t}' añadida a la cola de {d} en 0 ms (cero recodificación)!",
        msg_studio_sim_queued_bg: "✓ Simulación '{t}' encolada en {d} ({s})",
        msg_studio_2b_saved: "✓ ¡Estilo Modo 2B guardado en el DMD '{d}'!",
        studio_badge_card_fast: "⚡ Vista Rápida Card",
        studio_badge_full_0ms: "🎬 Secuencia Completa (Cola 0ms)",
        studio_dmd_idle: "En espera / Listo para emitir",
        studio_dmd_res_mismatch: "Nota: el Estudio usa {sw}×{sh} y '{d}' usa {dw}×{dh} (se adaptará al encolar).",
        pill_sim_studio: "🎨 Sim Estudio",
        prof_rotation_hint: "💡 Elige Sistemas, Géneros y Décadas con etiquetas visuales (vacío = Todos). La duración de vídeo, encuadre, realce de color y diseño de Card Modo 2B se configuran por dominio en el Estudio pulsando el botón derecho de cada fila.",
        th_pl_systems_tags: "Sistemas (Juegos)",
        th_pl_genres_tags: "Géneros y Décadas",
        th_pl_studio_link: "Perfil Estudio (Card, Encuadre y Vídeo)",
        tag_all_systems: "✨ Todos los sistemas",
        tag_all_genres: "✨ Todos los géneros y épocas",
        btn_pick_tags: "+ Filtrar",
        btn_edit_tags: "✎ Etiquetas",
        btn_open_dom_studio: "🎨 Estudio {d} →",
        tag_modal_sys_title: "🏷️ Elegir Sistemas de Juegos",
        tag_modal_gen_title: "🏷️ Elegir Géneros y Décadas — {d}",
        tag_sec_systems: "🕹️ Plataformas Arcade y Consolas Retro",
        tag_sec_genres: "🎭 Categorías y Géneros",
        tag_sec_decades: "📅 Décadas y Épocas",
        ph_tag_search: "🔍 Filtrar sistemas, géneros o décadas...",
        btn_tag_clear_all: "✨ Todos (Sin filtro)",
        btn_tag_select_vis: "✓ Marcar visibles",
        btn_cancel: "Cancelar",
        btn_apply_tags: "✓ Aplicar selección",
        tag_selected_count: "{n} etiqueta(s) seleccionada(s) (0 = Todos)",
        tag_more_fmt: "+{n} más",
        studio_prof_bar_title: "🎯 Perfiles por Dominio del DMD:",
        btn_back_rotation: "← Volver a la Rotación del DMD",
        studio_btn_save_dom_prof: "💾 Guardar Perfil de Dominio en DMD",
        studio_btn_copy_all_doms: "📋 Aplicar a los 4 Dominios",
        dom_prof_games: "🕹️ Perfil Juegos",
        dom_prof_movies: "🎬 Perfil Películas",
        dom_prof_series: "📺 Perfil Series",
        dom_prof_anime: "⛩️ Perfil Anime",
        studio_lbl_vid_type: "Tipo de Clip Preferido (video_type)",
        opt_vtype_any: "Cualquiera (Gameplay / Tráiler / Clip)",
        opt_vtype_trailer: "Tráiler Oficial (Películas / Series)",
        opt_vtype_opening: "Opening OP (Anime)",
        opt_vtype_ending: "Ending ED (Anime)",
        opt_vtype_pv: "Vídeo Promocional PV",
        msg_studio_dom_saved: "✓ ¡Perfil Estudio '{dom}' guardado en el DMD '{d}'!",
        msg_studio_all_doms_saved: "✓ ¡Perfil Estudio aplicado a los 4 dominios en el DMD '{d}'!"
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
    let lastDmdProfiles = [];
    let lastSearchCandidates = [];

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
      loadStudioFilters();
      updateStudioSliderLabels();
      if (typeof updateStudioDomainTabsUi === 'function') updateStudioDomainTabsUi();
      renderStudioActiveDmdBanner();
      const profCard = document.getElementById('dmd-profile-editor-card');
      if (profCard && profCard.style.display !== 'none' && typeof renderPlaylistEditorRows === 'function') {
        renderPlaylistEditorRows();
      }
    }

    function switchTab(name) {
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      const idx = {overview: 0, runtime: 1, tester: 2, studio: 3, keys: 4, network: 5}[name] || 0;
      document.querySelectorAll('.tab-btn')[idx].classList.add('active');
      if (name === 'studio') {
        initStudioIfNeeded();
      }
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
      syncTargetDmdSelector();
      const tb = document.getElementById('clients-tbody');
      if (!tb) return;
      if (!lastClients.length) {
        tb.innerHTML = `<tr><td colspan="8" style="color:var(--muted)">${esc(t('no_clients'))}</td></tr>`;
        return;
      }
      tb.innerHTML = lastClients.map(c => {
        const transport = c.transport || 'MQTT+UDP';
        const seenSuffix = c.last_seen_ago_s != null ? ` • ${Number(c.last_seen_ago_s)}s` : '';
        const ipInfo = (c.client_ip ? `${esc(c.client_ip)} • ${esc(transport)}` : esc(transport)) + esc(seenSuffix);
        const cfgSrc = c.config_source || 'client';
        let modeBadge = `<span class="pill info">${esc(t('mode_badge_client'))}</span>`;
        if (cfgSrc === 'server') {
          modeBadge = c.supports_local_card === false
            ? `<span class="pill ok">${esc(t('mode_badge_2b'))}</span>`
            : `<span class="pill warn">${esc(t('mode_badge_2a'))}</span>`;
        }
        const langBadge = `<span class="pill info">${esc((c.lang || 'fr').toUpperCase())}</span>`;
        const qCount = Number(c.queue_length || 0);
        const qBadge = qCount > 0 ? ` <span class="pill warn">Queue: ${qCount}</span>` : '';

        let playingCell = '—';
        if (c.paused) {
          playingCell = `<span class="pill warn">${esc(t('pill_paused'))}</span>`;
        } else if (c.udp_streaming) {
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
        const safeId = esc(c.dmd_id).replace(/'/g, "\\'");
        const pauseAct = c.paused ? 'resume' : 'pause';
        const pauseIcon = c.paused ? '▶' : '⏸';
        return `
          <tr>
            <td>
              <b>${esc(c.display_name || c.dmd_id)}</b>
              <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">${modeBadge} ${langBadge}${qBadge}</div>
              <small style="color:var(--muted)">${ipInfo}</small>
            </td>
            <td><span class="pill info">${esc((c.domain || '').toUpperCase())}</span></td>
            <td><code>${esc(c.width)}×${esc(c.height)}</code></td>
            <td><code>${esc(c.video_mode)}</code> • <code>${esc(c.color_boost)}</code>${c.video_max_duration_s != null ? `<br/><small style="color:var(--muted)">≤${esc(Number(c.video_max_duration_s).toFixed(1))}s @ ${esc(c.video_fps || 15)}fps</small>` : ''}</td>
            <td>${playingCell}</td>
            <td>${readyCell}</td>
            <td>${standbyCell}</td>
            <td>
              <div style="display:flex;gap:5px;flex-wrap:nowrap">
                <button type="button" class="secondary mini-btn" title="Profile & Playlist" onclick="openProfileEditor('${safeId}')">⚙️</button>
                <button type="button" class="secondary mini-btn" title="Skip" onclick="controlDmd('${safeId}', 'skip')">⏭</button>
                <button type="button" class="secondary mini-btn" title="Pause / Resume" onclick="controlDmd('${safeId}', '${pauseAct}')">${pauseIcon}</button>
                <button type="button" class="secondary mini-btn" title="On-Demand Queue" onclick="jumpToQueueStudio('${safeId}')">🎯</button>
                <button type="button" class="secondary mini-btn" title="Card Studio 2B & Sim" onclick="openStudioForDmd('${safeId}')">🎨</button>
              </div>
            </td>
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
          lastDmdProfiles = Array.isArray(d.dmd_profiles) ? d.dmd_profiles : [];
          hydrateConfigInputs(d.config || {}, forceHydrate);
          renderVaultStatus(d.vault_status || {});
          renderProviders(d.providers || {});
          renderClients(d.clients || []);
          renderSeen(d.seen_history || {});
          loadTargetDmdQueue();
          renderStudioActiveDmdBanner();
          loadStudioDmdQueue();
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
        const contentLangEl = document.getElementById('test-content-lang');
        const filterLang = (contentLangEl && contentLangEl.value) ? contentLangEl.value : currentUiLang;
        const r = await apiFetch(`/api/v1/${dom}/filters?lang=${encodeURIComponent(filterLang)}`);
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
      const contentLang = (document.getElementById('test-content-lang') || {}).value || currentUiLang;
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
              domain: dom, query: q, lang: contentLang, width: w, height: h,
              include_video: false, video_mode: vmode, color_boost: cboost
            })
          });
          if (!r.ok) throw new Error((await r.json()).detail || 'Not found');
          item = await r.json();
        } else {
          const params = new URLSearchParams({
            client_id: 'webui_tester', count: '1', lang: contentLang,
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

    function syncTargetDmdSelector() {
      const sel = document.getElementById('test-target-dmd');
      if (!sel) return;
      const prev = sel.value;
      const known = new Map();
      (lastDmdProfiles || []).forEach(p => {
        if (p && p.dmd_id) {
          const tag = p.config_source === 'server'
            ? (p.supports_local_card === false ? '2B UDP' : '2A Hybrid')
            : 'Client';
          known.set(p.dmd_id, `${p.display_name || p.dmd_id} (${p.width}×${p.height} • ${tag} • ${(p.lang || 'fr').toUpperCase()})`);
        }
      });
      (lastClients || []).forEach(c => {
        if (c && c.dmd_id && !known.has(c.dmd_id)) {
          known.set(c.dmd_id, `${c.display_name || c.dmd_id} (${c.width}×${c.height} • ${(c.lang || 'fr').toUpperCase()})`);
        }
      });
      let html = `<option value="">${esc(t('opt_target_browser'))}</option>`;
      known.forEach((lbl, id) => {
        html += `<option value="${esc(id)}">📺 ${esc(lbl)}</option>`;
      });
      sel.innerHTML = html;
      if (prev && known.has(prev)) {
        sel.value = prev;
      } else if (!prev && known.size === 1) {
        sel.value = Array.from(known.keys())[0];
      }

      const stSel = document.getElementById('studio-target-dmd');
      const stTopSel = document.getElementById('studio-top-dmd-select');
      if (stSel) {
        const stPrev = stSel.value || (stTopSel ? stTopSel.value : '');
        let stHtml = `<option value="">${esc(t('studio_opt_no_dmd'))}</option>`;
        known.forEach((lbl, id) => {
          stHtml += `<option value="${esc(id)}">📺 ${esc(lbl)}</option>`;
        });
        stSel.innerHTML = stHtml;
        if (stTopSel) stTopSel.innerHTML = stHtml;
        let chosen = '';
        if (stPrev && known.has(stPrev)) {
          chosen = stPrev;
        } else if (known.size > 0) {
          const activeCli = (lastClients || []).find(
            c => c && c.dmd_id && (c.udp_streaming || c.is_playing || c.playing_title)
          );
          chosen = (activeCli && known.has(activeCli.dmd_id))
            ? activeCli.dmd_id
            : Array.from(known.keys())[0];
        }
        stSel.value = chosen;
        if (stTopSel) stTopSel.value = chosen;
        if (chosen && typeof studioLoadedDmdId !== 'undefined' && studioLoadedDmdId !== chosen) {
          hydrateStudioDraftStylesFromCache(chosen);
        }
      }
    }

    function jumpToQueueStudio(dmdId) {
      switchTab('tester');
      syncTargetDmdSelector();
      const sel = document.getElementById('test-target-dmd');
      if (sel && dmdId) {
        sel.value = dmdId;
        const prof = (lastDmdProfiles || []).find(p => p.dmd_id === dmdId);
        const cli = (lastClients || []).find(c => c.dmd_id === dmdId);
        const lang = (prof && prof.lang) || (cli && cli.lang);
        const langEl = document.getElementById('test-content-lang');
        if (lang && langEl) langEl.value = lang;
        loadTargetDmdQueue();
      }
    }

    /* =========================================================================
       LOCALIZED FILTER CATALOG CACHE, VISUAL TAG PICKER & PER-DOMAIN PROFILES
       ========================================================================= */
    const filterCatalogCache = new Map();

    async function ensureDomainFilterCatalog(dom, lang) {
      const l = lang || 'fr';
      const key = `${dom}:${l}`;
      if (filterCatalogCache.has(key)) return filterCatalogCache.get(key);
      const entry = {
        systems: [],
        genres: [],
        decades: [],
        sysMap: new Map(),
        genMap: new Map(),
        decMap: new Map()
      };
      try {
        const r = await apiFetch(`/api/v1/${dom}/filters?lang=${encodeURIComponent(l)}`);
        if (r.ok) {
          const d = await r.json();
          entry.systems = Array.isArray(d.systems) ? d.systems : [];
          entry.genres = Array.isArray(d.genres) ? d.genres : [];
          entry.decades = Array.isArray(d.decades) ? d.decades : [];
          entry.systems.forEach(s => { if (s && s.id) entry.sysMap.set(s.id, s.label || s.id); });
          entry.genres.forEach(g => { if (g && g.id) entry.genMap.set(g.id, g.label || g.id); });
          entry.decades.forEach(dc => { if (dc && dc.id) entry.decMap.set(dc.id, dc.label || dc.id); });
          filterCatalogCache.set(key, entry);
        }
      } catch (e) {
        console.error(e);
      }
      return entry;
    }

    async function preloadAllDomainFilterCatalogs(lang) {
      await Promise.all(CANONICAL_DOMAINS.map(d => ensureDomainFilterCatalog(d, lang)));
    }

    function defaultStudioStyleForDomain(dom) {
      const base = {
        include_card_intro: true,
        card_layout: 'classic_split',
        card_theme: 'tinted',
        card_font: 'arcade_pixel',
        card_accent_color: 'auto',
        include_poster: true,
        show_badge: true,
        show_year: true,
        show_rating: true,
        include_synopsis: true,
        text_scroll_speed: '4',
        card_hold_s: 0.9,
        jacket_panorama: true,
        marquee_panorama: false,
        snapshot_panorama_count: 1,
        panorama_duration_s: 3.5,
        include_video: true,
        video_type: 'any',
        video_max_duration_s: 10.0,
        video_fps: 15,
        video_mode: 'auto_action',
        color_boost: 'auto'
      };
      const overrides = {
        games: {
          card_layout: 'classic_split',
          card_theme: 'tinted',
          card_font: 'arcade_pixel',
          marquee_panorama: true,
          snapshot_panorama_count: 2,
          video_max_duration_s: 10.0,
          color_boost: 'pixel_art',
          video_type: 'any'
        },
        movies: {
          card_layout: 'poster_right',
          card_theme: 'poster_wash',
          card_font: 'clean_sans',
          snapshot_panorama_count: 1,
          video_max_duration_s: 12.0,
          color_boost: 'cinema',
          video_type: 'trailer'
        },
        series: {
          card_layout: 'classic_split',
          card_theme: 'tinted',
          card_font: 'clean_sans',
          snapshot_panorama_count: 1,
          video_max_duration_s: 10.0,
          color_boost: 'cinema',
          video_type: 'trailer'
        },
        anime: {
          card_layout: 'badge_header',
          card_theme: 'neon_border',
          card_font: 'clean_sans',
          snapshot_panorama_count: 2,
          video_max_duration_s: 12.0,
          color_boost: 'anime',
          video_type: 'opening'
        }
      };
      return Object.assign(base, overrides[dom] || {});
    }

    function normalizeDomainStylesObj(rawDomainStyles, fallbackCardStyle) {
      const out = {};
      CANONICAL_DOMAINS.forEach(dom => {
        const base = defaultStudioStyleForDomain(dom);
        if (fallbackCardStyle && typeof fallbackCardStyle === 'object') {
          Object.assign(base, fallbackCardStyle);
        }
        if (rawDomainStyles && rawDomainStyles[dom] && typeof rawDomainStyles[dom] === 'object') {
          Object.assign(base, rawDomainStyles[dom]);
        }
        out[dom] = base;
      });
      return out;
    }

    function shortDomainDisplayName(dom) {
      const names = {
        games: t('opt_dom_games'),
        movies: t('opt_dom_movies'),
        series: t('opt_dom_series'),
        anime: t('opt_dom_anime')
      };
      return String(names[dom] || dom.toUpperCase()).split(' (')[0].trim();
    }

    function defaultPlaylistRows(lang) {
      const l = lang || 'fr';
      return [
        {enabled: true, domain: 'games', count: 2, lang: l, systems: ['arcade', 'mame', 'neogeo', 'cps2', 'snes', 'megadrive'], genres: [], decades: []},
        {enabled: true, domain: 'movies', count: 1, lang: l, systems: [], genres: [], decades: []},
        {enabled: false, domain: 'series', count: 1, lang: l, systems: [], genres: [], decades: []},
        {enabled: false, domain: 'anime', count: 1, lang: l, systems: [], genres: [], decades: []}
      ];
    }

    function toUniqueTokenArray(val) {
      const raw = Array.isArray(val) ? val : (typeof val === 'string' && val.trim() ? val.split(',') : []);
      const seen = new Set();
      const out = [];
      raw.forEach(item => {
        const tok = String(item || '').trim();
        if (tok && !seen.has(tok)) {
          seen.add(tok);
          out.push(tok);
        }
      });
      return out;
    }

    let editingProfileDmdId = '';
    let editingProfileCardStyle = null;
    let editingProfileDomainStyles = normalizeDomainStylesObj(null, null);
    let editingProfileSlots = {};

    function hydrateEditingProfileSlots(playlist, lang) {
      const byDom = {};
      (playlist || []).forEach(s => { if (s && s.domain) byDom[s.domain] = s; });
      const defaults = {};
      defaultPlaylistRows(lang).forEach(d => { defaults[d.domain] = d; });
      const nextSlots = {};
      CANONICAL_DOMAINS.forEach(dom => {
        const src = byDom[dom] || defaults[dom];
        nextSlots[dom] = {
          enabled: src.enabled !== false,
          domain: dom,
          count: Math.max(1, Math.min(10, parseInt(src.count, 10) || 1)),
          systems: dom === 'games' ? toUniqueTokenArray(src.systems) : [],
          genres: toUniqueTokenArray(src.genres),
          decades: toUniqueTokenArray(src.decades),
          min_rating: Number(src.min_rating != null ? src.min_rating : 6.0)
        };
      });
      editingProfileSlots = nextSlots;
    }

    function onPlSlotToggle(dom, checked) {
      if (editingProfileSlots[dom]) {
        editingProfileSlots[dom].enabled = Boolean(checked);
      }
    }

    function onPlSlotCount(dom, val) {
      if (editingProfileSlots[dom]) {
        editingProfileSlots[dom].count = Math.max(1, Math.min(10, parseInt(val, 10) || 1));
      }
    }

    function removeSlotTag(dom, field, tagId) {
      const slot = editingProfileSlots[dom];
      if (!slot || !Array.isArray(slot[field])) return;
      slot[field] = slot[field].filter(x => x !== tagId);
      renderPlaylistEditorRows();
    }

    function summarizeDomainStudioStyleBadges(st) {
      const s = st || defaultStudioStyleForDomain('games');
      const cardTag = s.include_card_intro !== false ? s.card_layout : 'card:off';
      const vidTag = s.include_video !== false
        ? `🎬 ≤${Number(s.video_max_duration_s != null ? s.video_max_duration_s : 10).toFixed(0)}s`
        : '🎬 OFF';
      const snapCnt = Number(s.snapshot_panorama_count || 0);
      const snapTag = snapCnt > 0 ? `<span class="pill info">📸×${snapCnt}</span>` : '';
      return `
        <span class="pill info">${esc(cardTag)}</span>
        <span class="pill info">${esc(s.card_theme || 'tinted')}</span>
        <span class="pill warn">${esc(s.color_boost || 'auto')} • ${esc(s.video_mode || 'auto_action')}</span>
        <span class="pill ok">${esc(vidTag)}</span>
        ${snapTag}
      `;
    }

    function renderPlaylistEditorRows() {
      const tb = document.getElementById('prof-playlist-tbody');
      if (!tb) return;
      if (!Object.keys(editingProfileSlots).length) {
        hydrateEditingProfileSlots(defaultPlaylistRows('fr'), 'fr');
      }
      const profLang = (document.getElementById('prof-lang') || {}).value || 'fr';
      const maxVisibleChips = 4;

      tb.innerHTML = CANONICAL_DOMAINS.map(dom => {
        const s = editingProfileSlots[dom] || {enabled: false, domain: dom, count: 1, systems: [], genres: [], decades: []};
        const isGames = dom === 'games';
        const cat = filterCatalogCache.get(`${dom}:${profLang}`);
        const sysList = isGames ? (s.systems || []) : [];
        const genList = s.genres || [];
        const decList = s.decades || [];

        let sysCellHtml = '<span style="color:var(--muted);font-size:0.82rem">—</span>';
        if (isGames) {
          let chips = '';
          if (!sysList.length) {
            chips = `<span class="tag-all-pill">${esc(t('tag_all_systems'))}</span>`;
          } else {
            chips = sysList.slice(0, maxVisibleChips).map(id => {
              const lbl = (cat && cat.sysMap.get(id)) || id;
              const safeId = esc(id).replace(/'/g, "\\'");
              return `<span class="tag-chip" title="${esc(id)}">${esc(lbl)}<button type="button" class="tag-chip-rm" onclick="removeSlotTag('games','systems','${safeId}')">✕</button></span>`;
            }).join('');
            if (sysList.length > maxVisibleChips) {
              chips += `<span class="pill info">${esc(t('tag_more_fmt', {n: sysList.length - maxVisibleChips}))}</span>`;
            }
          }
          const btnLbl = sysList.length ? t('btn_edit_tags') : t('btn_pick_tags');
          sysCellHtml = `<div class="tag-cell">${chips}<button type="button" class="tag-pick-btn" onclick="openTagPickerModal('games','systems')">${esc(btnLbl)}</button></div>`;
        }

        const combinedGenDec = [
          ...genList.map(id => ({field: 'genres', id, isDec: false, lbl: (cat && cat.genMap.get(id)) || id})),
          ...decList.map(id => ({field: 'decades', id, isDec: true, lbl: '📅 ' + ((cat && cat.decMap.get(id)) || id)}))
        ];
        let genChips = '';
        if (!combinedGenDec.length) {
          genChips = `<span class="tag-all-pill">${esc(t('tag_all_genres'))}</span>`;
        } else {
          genChips = combinedGenDec.slice(0, maxVisibleChips).map(it => {
            const safeId = esc(it.id).replace(/'/g, "\\'");
            const cls = it.isDec ? 'tag-chip decade' : 'tag-chip';
            return `<span class="${cls}" title="${esc(it.id)}">${esc(it.lbl)}<button type="button" class="tag-chip-rm" onclick="removeSlotTag('${dom}','${it.field}','${safeId}')">✕</button></span>`;
          }).join('');
          if (combinedGenDec.length > maxVisibleChips) {
            genChips += `<span class="pill info">${esc(t('tag_more_fmt', {n: combinedGenDec.length - maxVisibleChips}))}</span>`;
          }
        }
        const genBtnLbl = combinedGenDec.length ? t('btn_edit_tags') : t('btn_pick_tags');
        const genCellHtml = `<div class="tag-cell">${genChips}<button type="button" class="tag-pick-btn" onclick="openTagPickerModal('${dom}','genres_decades')">${esc(genBtnLbl)}</button></div>`;

        const domStyle = (editingProfileDomainStyles && editingProfileDomainStyles[dom]) || defaultStudioStyleForDomain(dom);
        const studioBtnText = t('btn_open_dom_studio', {d: dom.toUpperCase()});
        const studioCellHtml = `<div class="studio-link-cell">
          <div class="studio-summary-badges">${summarizeDomainStudioStyleBadges(domStyle)}</div>
          <button type="button" class="studio-jump-btn" onclick="openStudioForDmdDomainFromEditor('${dom}')">${esc(studioBtnText)}</button>
        </div>`;

        return `<tr data-pl-domain="${esc(dom)}">
          <td><input type="checkbox" class="pl-enabled" style="width:20px;height:20px" ${s.enabled !== false ? 'checked' : ''} onchange="onPlSlotToggle('${dom}', this.checked)" /></td>
          <td><b>${esc(shortDomainDisplayName(dom))}</b></td>
          <td><input type="number" class="pl-count" min="1" max="10" value="${esc(s.count || 1)}" style="width:70px" onchange="onPlSlotCount('${dom}', this.value)" /></td>
          <td>${sysCellHtml}</td>
          <td>${genCellHtml}</td>
          <td>${studioCellHtml}</td>
        </tr>`;
      }).join('');
    }

    async function onProfileLangChange() {
      const lang = (document.getElementById('prof-lang') || {}).value || 'fr';
      await preloadAllDomainFilterCatalogs(lang);
      renderPlaylistEditorRows();
    }

    /* ----- Multi-Select Tag Picker Modal ----- */
    let tagModalState = {
      open: false,
      domain: 'games',
      mode: 'systems',
      catalog: {systems: [], genres: [], decades: []},
      selectedSystems: new Set(),
      selectedGenres: new Set(),
      selectedDecades: new Set()
    };

    async function openTagPickerModal(dom, mode) {
      const lang = (document.getElementById('prof-lang') || {}).value || 'fr';
      const cat = await ensureDomainFilterCatalog(dom, lang);
      const slot = editingProfileSlots[dom] || {systems: [], genres: [], decades: []};
      tagModalState = {
        open: true,
        domain: dom,
        mode: mode,
        catalog: cat,
        selectedSystems: new Set(slot.systems || []),
        selectedGenres: new Set(slot.genres || []),
        selectedDecades: new Set(slot.decades || [])
      };
      const titleEl = document.getElementById('tag-modal-title');
      if (titleEl) {
        titleEl.textContent = mode === 'systems'
          ? t('tag_modal_sys_title')
          : t('tag_modal_gen_title', {d: shortDomainDisplayName(dom)});
      }
      const searchEl = document.getElementById('tag-modal-search');
      if (searchEl) searchEl.value = '';
      renderTagPickerOptions();
      const modal = document.getElementById('tag-picker-modal');
      if (modal) modal.classList.add('open');
      if (searchEl) setTimeout(() => searchEl.focus(), 30);
    }

    function closeTagPickerModal() {
      tagModalState.open = false;
      const modal = document.getElementById('tag-picker-modal');
      if (modal) modal.classList.remove('open');
    }

    function filterTagItemsByQuery(items, q) {
      if (!q) return items;
      const low = q.toLowerCase();
      return items.filter(it =>
        String(it.label || '').toLowerCase().includes(low) ||
        String(it.id || '').toLowerCase().includes(low)
      );
    }

    function renderTagPickerOptions() {
      const body = document.getElementById('tag-modal-body');
      const countEl = document.getElementById('tag-modal-count');
      if (!body) return;
      const q = ((document.getElementById('tag-modal-search') || {}).value || '').trim();

      let html = '';
      let totalSelected = 0;

      if (tagModalState.mode === 'systems') {
        totalSelected = tagModalState.selectedSystems.size;
        const visSys = filterTagItemsByQuery(tagModalState.catalog.systems || [], q);
        html += `<div class="tag-group-title">${esc(t('tag_sec_systems'))} (${visSys.length})</div>`;
        html += `<div class="tag-options-grid">` + visSys.map(it => {
          const sel = tagModalState.selectedSystems.has(it.id);
          const safeId = esc(it.id).replace(/'/g, "\\'");
          return `<button type="button" class="tag-option-btn${sel ? ' selected' : ''}" onclick="toggleTagModalItem('systems','${safeId}')" title="${esc(it.id)}">
            <span class="tag-opt-check">${sel ? '✓' : ''}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(it.label)} <small style="color:var(--muted)">(${esc(it.id)})</small></span>
          </button>`;
        }).join('') + `</div>`;
      } else {
        totalSelected = tagModalState.selectedGenres.size + tagModalState.selectedDecades.size;
        const visGen = filterTagItemsByQuery(tagModalState.catalog.genres || [], q);
        const visDec = filterTagItemsByQuery(tagModalState.catalog.decades || [], q);

        html += `<div class="tag-group-title">${esc(t('tag_sec_genres'))} (${visGen.length})</div>`;
        html += `<div class="tag-options-grid">` + visGen.map(it => {
          const sel = tagModalState.selectedGenres.has(it.id);
          const safeId = esc(it.id).replace(/'/g, "\\'");
          return `<button type="button" class="tag-option-btn${sel ? ' selected' : ''}" onclick="toggleTagModalItem('genres','${safeId}')" title="${esc(it.id)}">
            <span class="tag-opt-check">${sel ? '✓' : ''}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(it.label)}</span>
          </button>`;
        }).join('') + `</div>`;

        if ((tagModalState.catalog.decades || []).length) {
          html += `<div class="tag-group-title">${esc(t('tag_sec_decades'))} (${visDec.length})</div>`;
          html += `<div class="tag-options-grid">` + visDec.map(it => {
            const sel = tagModalState.selectedDecades.has(it.id);
            const safeId = esc(it.id).replace(/'/g, "\\'");
            return `<button type="button" class="tag-option-btn${sel ? ' selected' : ''}" onclick="toggleTagModalItem('decades','${safeId}')" title="${esc(it.id)}">
              <span class="tag-opt-check">${sel ? '✓' : ''}</span>
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📅 ${esc(it.label)}</span>
            </button>`;
          }).join('') + `</div>`;
        }
      }

      body.innerHTML = html;
      if (countEl) {
        countEl.textContent = t('tag_selected_count', {n: totalSelected});
      }
    }

    function toggleTagModalItem(group, id) {
      const targetSet = group === 'systems'
        ? tagModalState.selectedSystems
        : (group === 'decades' ? tagModalState.selectedDecades : tagModalState.selectedGenres);
      if (targetSet.has(id)) targetSet.delete(id);
      else targetSet.add(id);
      renderTagPickerOptions();
    }

    function clearAllTagModal() {
      if (tagModalState.mode === 'systems') {
        tagModalState.selectedSystems.clear();
      } else {
        tagModalState.selectedGenres.clear();
        tagModalState.selectedDecades.clear();
      }
      renderTagPickerOptions();
    }

    function selectVisibleTagModal() {
      const q = ((document.getElementById('tag-modal-search') || {}).value || '').trim();
      if (tagModalState.mode === 'systems') {
        filterTagItemsByQuery(tagModalState.catalog.systems || [], q).forEach(it => tagModalState.selectedSystems.add(it.id));
      } else {
        filterTagItemsByQuery(tagModalState.catalog.genres || [], q).forEach(it => tagModalState.selectedGenres.add(it.id));
        filterTagItemsByQuery(tagModalState.catalog.decades || [], q).forEach(it => tagModalState.selectedDecades.add(it.id));
      }
      renderTagPickerOptions();
    }

    function applyTagPickerModal() {
      const dom = tagModalState.domain;
      if (!editingProfileSlots[dom]) return;
      if (tagModalState.mode === 'systems') {
        editingProfileSlots[dom].systems = Array.from(tagModalState.selectedSystems);
      } else {
        editingProfileSlots[dom].genres = Array.from(tagModalState.selectedGenres);
        editingProfileSlots[dom].decades = Array.from(tagModalState.selectedDecades);
      }
      closeTagPickerModal();
      renderPlaylistEditorRows();
    }

    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape' && tagModalState.open) {
        closeTagPickerModal();
      }
    });

    async function openProfileEditor(dmdId) {
      const card = document.getElementById('dmd-profile-editor-card');
      if (!card) return;
      card.style.display = 'block';
      document.getElementById('prof-status-msg').textContent = '';

      let prof = null;
      if (dmdId) {
        try {
          const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`);
          if (r.ok) {
            const d = await r.json();
            prof = d.profile;
          }
        } catch (_) {}
      }
      if (!prof) {
        const defLang = (lastServerConfig && lastServerConfig.default_lang) || 'fr';
        prof = {
          dmd_id: dmdId || 'esp32_passive_1',
          display_name: dmdId || 'ESP32 Passive DMD',
          config_source: 'server',
          supports_local_card: false,
          always_stream_udp: false,
          width: 128,
          height: 32,
          udp_ip: '',
          udp_port: 4210,
          lang: defLang,
          inter_item_gap_s: 1.0,
          domain_styles: normalizeDomainStylesObj(null, null),
          playlist: defaultPlaylistRows(defLang)
        };
      }
      editingProfileDmdId = prof.dmd_id || '';
      editingProfileCardStyle = prof.card_style || null;
      editingProfileDomainStyles = normalizeDomainStylesObj(prof.domain_styles, prof.card_style);
      document.getElementById('prof-dmd-id').value = prof.dmd_id || '';
      document.getElementById('prof-display-name').value = prof.display_name || prof.dmd_id || '';
      const modeVal = prof.config_source === 'client'
        ? 'client'
        : (prof.supports_local_card === false ? 'server_passive' : 'server_hybrid');
      document.getElementById('prof-mode').value = modeVal;
      const profLang = prof.lang || 'fr';
      document.getElementById('prof-lang').value = profLang;
      const resVal = `${prof.width || 128}x${prof.height || 32}`;
      const resEl = document.getElementById('prof-res');
      if (Array.from(resEl.options).some(o => o.value === resVal)) {
        resEl.value = resVal;
      }
      document.getElementById('prof-always-udp').value = String(Boolean(prof.always_stream_udp));
      document.getElementById('prof-udp-ip').value = prof.udp_ip || '';
      document.getElementById('prof-udp-port').value = String(prof.udp_port || 4210);
      document.getElementById('prof-gap-s').value = String(prof.inter_item_gap_s != null ? prof.inter_item_gap_s : 1.0);
      hydrateEditingProfileSlots(prof.playlist || defaultPlaylistRows(profLang), profLang);
      await preloadAllDomainFilterCatalogs(profLang);
      renderPlaylistEditorRows();
      card.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }

    function closeProfileEditor() {
      const card = document.getElementById('dmd-profile-editor-card');
      if (card) card.style.display = 'none';
    }

    async function saveDmdProfile() {
      const dmdId = document.getElementById('prof-dmd-id').value.trim();
      const msgEl = document.getElementById('prof-status-msg');
      if (!dmdId) {
        msgEl.style.color = 'var(--red)';
        msgEl.textContent = t('msg_prof_id_req');
        return;
      }
      const modeSel = document.getElementById('prof-mode').value;
      const configSource = modeSel === 'client' ? 'client' : 'server';
      const supportsLocalCard = modeSel !== 'server_passive';
      const [w, h] = document.getElementById('prof-res').value.split('x').map(Number);
      const lang = document.getElementById('prof-lang').value || 'fr';

      const playlist = CANONICAL_DOMAINS.map(dom => {
        const s = editingProfileSlots[dom] || {enabled: false, count: 1, systems: [], genres: [], decades: []};
        const ds = (editingProfileDomainStyles && editingProfileDomainStyles[dom]) || defaultStudioStyleForDomain(dom);
        return {
          enabled: Boolean(s.enabled),
          domain: dom,
          count: Math.max(1, Math.min(10, parseInt(s.count, 10) || 1)),
          lang: lang,
          systems: dom === 'games' ? toUniqueTokenArray(s.systems) : [],
          genres: toUniqueTokenArray(s.genres),
          decades: toUniqueTokenArray(s.decades),
          min_rating: Number(s.min_rating != null ? s.min_rating : 6.0),
          video_type: ds.video_type || 'any',
          jacket_panorama: ds.jacket_panorama !== false,
          marquee_panorama: Boolean(ds.marquee_panorama),
          snapshot_panorama_count: Number(ds.snapshot_panorama_count != null ? ds.snapshot_panorama_count : 1),
          panorama_duration_s: Number(ds.panorama_duration_s != null ? ds.panorama_duration_s : 3.5),
          include_video: ds.include_video !== false,
          video_max_duration_s: Number(ds.video_max_duration_s != null ? ds.video_max_duration_s : 10.0),
          video_fps: Number(ds.video_fps != null ? ds.video_fps : 15),
          video_mode: ds.video_mode || 'auto_action',
          color_boost: ds.color_boost || 'auto'
        };
      });

      const payload = {
        dmd_id: dmdId,
        display_name: document.getElementById('prof-display-name').value.trim() || dmdId,
        config_source: configSource,
        supports_local_card: supportsLocalCard,
        always_stream_udp: document.getElementById('prof-always-udp').value === 'true',
        width: w || 128,
        height: h || 32,
        udp_ip: document.getElementById('prof-udp-ip').value.trim(),
        udp_port: Math.max(1, Math.min(65535, parseInt(document.getElementById('prof-udp-port').value, 10) || 4210)),
        lang: lang,
        inter_item_gap_s: Math.max(0, Math.min(10, parseFloat(document.getElementById('prof-gap-s').value) || 0)),
        domain_styles: editingProfileDomainStyles,
        card_style: (editingProfileDomainStyles && editingProfileDomainStyles.games) || editingProfileCardStyle || undefined,
        playlist: playlist
      };

      try {
        const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        editingProfileDmdId = dmdId;
        if (typeof studioLoadedDmdId !== 'undefined' && studioLoadedDmdId === dmdId) {
          studioDraftDomainStyles = normalizeDomainStylesObj(editingProfileDomainStyles, null);
          if (typeof updateStudioDomainTabsUi === 'function') updateStudioDomainTabsUi();
        }
        msgEl.style.color = 'var(--green)';
        msgEl.textContent = t('msg_prof_saved', {d: dmdId});
        loadHealthAndConfig(false);
      } catch (err) {
        msgEl.style.color = 'var(--red)';
        msgEl.textContent = '⚠️ ' + (err.message || String(err));
      }
    }

    async function deleteDmdProfile() {
      const dmdId = document.getElementById('prof-dmd-id').value.trim();
      const msgEl = document.getElementById('prof-status-msg');
      if (!dmdId) return;
      try {
        const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`, {method: 'DELETE'});
        if (!r.ok && r.status !== 404) throw new Error('HTTP ' + r.status);
        msgEl.style.color = 'var(--green)';
        msgEl.textContent = t('msg_prof_deleted', {d: dmdId});
        closeProfileEditor();
        loadHealthAndConfig(false);
      } catch (err) {
        msgEl.style.color = 'var(--red)';
        msgEl.textContent = '⚠️ ' + (err.message || String(err));
      }
    }

    async function controlDmd(dmdId, action) {
      if (!dmdId) return;
      try {
        await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/control`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: action})
        });
        loadHealthAndConfig(false);
      } catch (e) {
        console.error(e);
      }
    }

    async function skipSelectedDmd() {
      const dmdId = (document.getElementById('test-target-dmd') || {}).value || '';
      const st = document.getElementById('test-status');
      if (!dmdId) {
        if (st) {
          st.style.color = 'var(--amber)';
          st.textContent = t('msg_need_target_dmd');
        }
        return;
      }
      await controlDmd(dmdId, 'skip');
    }

    async function runCandidateSearch() {
      const dom = document.getElementById('test-domain').value;
      const contentLang = document.getElementById('test-content-lang').value || 'fr';
      const sys = document.getElementById('test-system').value;
      const gen = document.getElementById('test-genre').value;
      const dec = document.getElementById('test-decade').value;
      const q = document.getElementById('test-query').value.trim();
      const st = document.getElementById('test-status');
      const btn = document.getElementById('btn-search-cands');
      const grid = document.getElementById('search-results-grid');
      btn.disabled = true;
      st.style.color = 'var(--cyan)';
      st.textContent = t('msg_searching');

      try {
        const params = new URLSearchParams({lang: contentLang, limit: '8'});
        if (q) params.set('q', q);
        if (dom === 'games' && sys) params.set('systems', sys);
        if (gen) params.set('genres', gen);
        if (dec) params.set('decades', dec);
        const r = await apiFetch(`/api/v1/${dom}/search?` + params.toString());
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        lastSearchCandidates = Array.isArray(d.items) ? d.items : [];
        if (!lastSearchCandidates.length) {
          grid.style.display = 'none';
          st.style.color = 'var(--amber)';
          st.textContent = t('msg_no_candidate');
          return;
        }
        renderSearchCandidates();
        st.style.color = 'var(--green)';
        st.textContent = t('msg_search_count', {n: lastSearchCandidates.length, l: contentLang.toUpperCase()});
      } catch (err) {
        st.style.color = 'var(--red)';
        st.textContent = '⚠️ ' + (err.message || String(err));
      } finally {
        btn.disabled = false;
      }
    }

    function renderSearchCandidates() {
      const grid = document.getElementById('search-results-grid');
      if (!grid) return;
      if (!lastSearchCandidates.length) {
        grid.style.display = 'none';
        return;
      }
      grid.style.display = 'grid';
      grid.innerHTML = lastSearchCandidates.map((c, idx) => {
        const thumb = c.poster_url
          ? `<img class="cand-thumb" src="${esc(c.poster_url)}" alt="" loading="lazy" onerror="this.style.display='none'" />`
          : '';
        const vidPill = c.has_video ? `<span class="pill ok">🎬 Video</span>` : '';
        return `<div class="cand-card">
          ${thumb}
          <div class="cand-body">
            <div class="cand-title">${esc(c.title)} ${c.year ? `<small style="color:var(--muted)">(${esc(c.year)})</small>` : ''}</div>
            <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center">
              <span class="pill info">${esc(c.badge || c.domain.toUpperCase())}</span>
              <span class="pill warn">${esc(c.source_id)} • ${(c.lang || 'fr').toUpperCase()}</span>
              ${vidPill}
            </div>
            <div class="cand-syn">${esc(c.synopsis || c.subtitle || '')}</div>
            <div class="cand-actions">
              <button type="button" class="secondary mini-btn" onclick="previewCandidate(${idx})">${esc(t('btn_preview_cand'))}</button>
              <button type="button" class="secondary mini-btn" onclick="openCandidateInStudio(${idx})">${esc(t('btn_open_studio'))}</button>
              <button type="button" class="secondary mini-btn" onclick="enqueueCandidate(${idx}, 'enqueue_next')">${esc(t('btn_queue_next'))}</button>
              <button type="button" class="mini-btn" onclick="enqueueCandidate(${idx}, 'play_now')">${esc(t('btn_play_now'))}</button>
            </div>
          </div>
        </div>`;
      }).join('');
    }

    function previewCandidate(idx) {
      const c = lastSearchCandidates[idx];
      if (!c) return;
      document.getElementById('test-preview').classList.add('visible');
      document.getElementById('prev-title').textContent = c.title || '—';
      document.getElementById('prev-badge').textContent = c.badge || c.domain.toUpperCase();
      document.getElementById('prev-year').textContent = c.year || '—';
      document.getElementById('prev-source').textContent = `Source: ${c.source_id || 'auto'} • ★ ${c.rating || 'N/A'} • ${(c.lang || 'fr').toUpperCase()}`;
      document.getElementById('prev-sub').textContent = c.subtitle || '';
      document.getElementById('prev-synopsis').textContent = c.synopsis || '';
      const imgEl = document.getElementById('prev-img');
      if (c.poster_url) {
        imgEl.src = c.poster_url;
        imgEl.style.display = 'block';
      } else {
        imgEl.style.display = 'none';
      }
    }

    async function enqueueCandidate(idx, mode) {
      const c = lastSearchCandidates[idx];
      if (!c) return;
      const targetDmd = document.getElementById('test-target-dmd').value;
      const st = document.getElementById('test-status');
      if (!targetDmd) {
        st.style.color = 'var(--amber)';
        st.textContent = t('msg_need_target_dmd');
        return;
      }
      const contentLang = document.getElementById('test-content-lang').value || c.lang || 'fr';
      const vmode = document.getElementById('test-video-mode').value;
      const cboost = document.getElementById('test-color-boost').value;
      const markSeen = document.getElementById('test-mark-seen').value === 'true';

      try {
        const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(targetDmd)}/queue`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            candidate_id: c.candidate_id,
            domain: c.domain,
            query: c.title,
            lang: contentLang,
            mode: mode,
            mark_seen: markSeen,
            video_mode: vmode,
            color_boost: cboost
          })
        });
        if (!r.ok) {
          const ej = await r.json().catch(() => ({}));
          throw new Error(ej.detail || ('HTTP ' + r.status));
        }
        st.style.color = 'var(--green)';
        st.textContent = t('msg_queued_ok', {t: c.title, d: targetDmd, m: mode === 'play_now' ? 'Play Now' : 'Next'});
        await loadTargetDmdQueue();
        loadHealthAndConfig(false);
      } catch (err) {
        st.style.color = 'var(--red)';
        st.textContent = '⚠️ ' + (err.message || String(err));
      }
    }

    async function loadTargetDmdQueue() {
      const targetDmd = (document.getElementById('test-target-dmd') || {}).value || '';
      const box = document.getElementById('ondemand-queue-body');
      if (!box) return;
      if (!targetDmd) {
        box.innerHTML = esc(t('queue_select_dmd'));
        return;
      }
      try {
        const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(targetDmd)}/queue`, {cache: 'no-store'});
        if (!r.ok) return;
        const d = await r.json();
        const items = Array.isArray(d.items) ? d.items : [];
        if (!items.length) {
          box.innerHTML = `<span style="color:var(--muted)">${esc(t('queue_empty'))}</span>`;
          return;
        }
        const safeDmd = esc(targetDmd).replace(/'/g, "\\'");
        box.innerHTML = items.map((it, i) => {
          const stClass = it.status === 'ready' || it.status === 'playing' ? 'ok' : 'warn';
          const safeQid = esc(it.queue_id).replace(/'/g, "\\'");
          return `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 10px;background:#0d121b;border:1px solid var(--border);border-radius:8px;margin-bottom:6px">
            <div>
              <b>#${i + 1} — ${esc(it.title)}</b>
              <span class="pill info" style="margin-left:6px">${esc((it.domain || '').toUpperCase())}</span>
              <span class="pill info">${esc((it.lang || 'fr').toUpperCase())}</span>
              <span class="pill ${stClass}">${esc(it.status)}</span>
              ${it.from_studio_sim ? `<span class="pill ok">${esc(t('pill_sim_studio'))}</span>` : ''}
            </div>
            <button type="button" class="danger mini-btn" onclick="removeQueueItem('${safeDmd}', '${safeQid}')">✕</button>
          </div>`;
        }).join('');
      } catch (e) {
        console.error(e);
      }
    }

    async function removeQueueItem(dmdId, queueId) {
      try {
        await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/queue/${encodeURIComponent(queueId)}`, {
          method: 'DELETE'
        });
        await loadTargetDmdQueue();
        await loadStudioDmdQueue();
        loadHealthAndConfig(false);
      } catch (e) {
        console.error(e);
      }
    }

    /* =========================================================================
       MODE 2B CARD STUDIO, BINARY DMDS PLAYER & HUB75 MOD SIM DMD
       ========================================================================= */
    let studioInitialized = false;
    let studioWidth = 128;
    let studioHeight = 32;
    let studioCardLayout = 'classic_split';
    let studioActiveDomain = 'games';
    let studioLoadedDmdId = '';
    let studioReturnToRotationDmd = '';
    let studioDraftDomainStyles = normalizeDomainStylesObj(null, null);
    let studioCandidates = [];
    let studioActiveCandIdx = -1;
    let studioFullPreviewId = '';
    let studioCardDebounceTimer = null;
    let studioCardAbortCtrl = null;

    // Decoded DMDS playback state
    let studioStreamW = 128;
    let studioStreamH = 32;
    let studioStreamFps = 15;
    let studioFrameDurMs = 67;
    let studioFrames = [];
    let studioChapters = [];
    let studioFrameIdx = 0;
    let studioPlaying = true;
    let studioLastTickMs = 0;
    let studioRafId = 0;
    const studioOffscreenCanvas = document.createElement('canvas');

    const STUDIO_DEMO_FALLBACKS = {
      games: {
        candidate_id: '',
        domain: 'games',
        title: 'Metal Slug X',
        year: '1999',
        badge: 'NEOGEO',
        badge_color: '#FF6D00',
        subtitle: 'SNK • Run & Gun',
        rating: '9.3',
        synopsis: 'Marco, Tarma, Eri, and Fio return to stop General Morden and a mysterious alien armada in crisp Neo-Geo pixel art.',
        lang: 'fr',
        poster_url: '',
        has_video: false
      },
      movies: {
        candidate_id: '',
        domain: 'movies',
        title: 'Blade Runner',
        year: '1982',
        badge: 'SCI-FI',
        badge_color: '#00E5FF',
        subtitle: 'Ridley Scott',
        rating: '8.7',
        synopsis: 'In a neon-soaked 2019 Los Angeles, Rick Deckard hunts four bioengineered replicants seeking their creator.',
        lang: 'fr',
        poster_url: '',
        has_video: false
      },
      series: {
        candidate_id: '',
        domain: 'series',
        title: 'Stranger Things',
        year: '2016',
        badge: 'SERIES',
        badge_color: '#FF1744',
        subtitle: 'Hawkins, 1983',
        rating: '8.6',
        synopsis: 'A vanished boy uncovers secret experiments, supernatural forces, and a mysterious girl in 1980s Indiana.',
        lang: 'fr',
        poster_url: '',
        has_video: false
      },
      anime: {
        candidate_id: '',
        domain: 'anime',
        title: 'Akira',
        year: '1988',
        badge: 'ANIME',
        badge_color: '#FFAB00',
        subtitle: 'Katsuhiro Otomo',
        rating: '8.8',
        synopsis: 'Kaneda races through Neo-Tokyo on his iconic red bike after Tetsuo awakens uncontrollable psychic powers.',
        lang: 'fr',
        poster_url: '',
        has_video: false
      }
    };

    function getActiveStudioCandidate() {
      if (studioActiveCandIdx >= 0 && studioCandidates[studioActiveCandIdx]) {
        return studioCandidates[studioActiveCandIdx];
      }
      const dom = (document.getElementById('studio-domain') || {}).value || 'games';
      return STUDIO_DEMO_FALLBACKS[dom] || STUDIO_DEMO_FALLBACKS.games;
    }

    function captureCurrentControlsIntoStudioStyle() {
      const fps = parseInt((document.getElementById('st-vid-fps') || {}).value || '15', 10) || 15;
      const st = {
        include_card_intro: document.getElementById('st-inc-card').checked,
        card_layout: studioCardLayout,
        card_theme: document.getElementById('studio-theme').value,
        card_font: document.getElementById('studio-font').value,
        card_accent_color: getStudioAccentValue(),
        include_poster: document.getElementById('st-inc-poster').checked,
        show_badge: document.getElementById('st-show-badge').checked,
        show_year: document.getElementById('st-show-year').checked,
        show_rating: document.getElementById('st-show-rating').checked,
        include_synopsis: document.getElementById('st-inc-synopsis').checked,
        text_scroll_speed: String(document.getElementById('st-scroll-speed').value || '4'),
        card_hold_s: parseFloat(document.getElementById('st-card-hold').value || '0.9'),
        jacket_panorama: document.getElementById('st-jacket-pan').checked,
        marquee_panorama: document.getElementById('st-marquee-pan').checked,
        snapshot_panorama_count: parseInt(document.getElementById('st-snap-count').value || '0', 10) || 0,
        panorama_duration_s: Math.max(1.5, Math.min(15.0, parseFloat(document.getElementById('st-pan-dur').value || '3.5') || 3.5)),
        include_video: document.getElementById('st-inc-video').checked,
        video_type: (document.getElementById('st-video-type') || {}).value || 'any',
        video_max_duration_s: Math.max(1.5, Math.min(60.0, parseFloat(document.getElementById('st-vid-dur').value || '8.0') || 8.0)),
        video_fps: fps,
        video_mode: document.getElementById('st-video-mode').value,
        color_boost: document.getElementById('st-color-boost').value
      };
      if (studioActiveDomain && CANONICAL_DOMAINS.includes(studioActiveDomain)) {
        studioDraftDomainStyles[studioActiveDomain] = Object.assign({}, st);
      }
      return st;
    }

    function applyStudioStyleToControls(styleObj) {
      const s = Object.assign(defaultStudioStyleForDomain(studioActiveDomain || 'games'), styleObj || {});
      studioCardLayout = s.card_layout || 'classic_split';
      document.querySelectorAll('#studio-layout-grid .layout-card-btn').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-layout') === studioCardLayout);
      });
      if (s.card_theme) document.getElementById('studio-theme').value = s.card_theme;
      if (s.card_font) document.getElementById('studio-font').value = s.card_font;

      const accMode = document.getElementById('studio-accent-mode');
      const accPick = document.getElementById('studio-accent-picker');
      if (accMode && accPick) {
        const isHex = typeof s.card_accent_color === 'string' && s.card_accent_color.startsWith('#') && s.card_accent_color.length === 7;
        accMode.value = isHex ? 'custom' : 'auto';
        accPick.disabled = !isHex;
        if (isHex) accPick.value = s.card_accent_color;
      }

      document.getElementById('st-inc-card').checked = s.include_card_intro !== false;
      document.getElementById('st-inc-poster').checked = s.include_poster !== false;
      document.getElementById('st-show-badge').checked = s.show_badge !== false;
      document.getElementById('st-show-year').checked = s.show_year !== false;
      document.getElementById('st-show-rating').checked = s.show_rating !== false;
      document.getElementById('st-inc-synopsis').checked = s.include_synopsis !== false;
      if (s.text_scroll_speed && !Number.isNaN(parseInt(s.text_scroll_speed, 10))) {
        document.getElementById('st-scroll-speed').value = String(s.text_scroll_speed);
      }
      if (s.card_hold_s != null) {
        document.getElementById('st-card-hold').value = String(s.card_hold_s);
      }
      updateStudioSliderLabels();

      document.getElementById('st-jacket-pan').checked = s.jacket_panorama !== false;
      document.getElementById('st-marquee-pan').checked = Boolean(s.marquee_panorama);
      document.getElementById('st-inc-video').checked = s.include_video !== false;
      document.getElementById('st-snap-count').value = String(s.snapshot_panorama_count != null ? s.snapshot_panorama_count : 1);
      document.getElementById('st-pan-dur').value = String(s.panorama_duration_s != null ? s.panorama_duration_s : 3.5);
      document.getElementById('st-vid-dur').value = String(s.video_max_duration_s != null ? s.video_max_duration_s : 10.0);
      const fpsEl = document.getElementById('st-vid-fps');
      if (fpsEl && s.video_fps != null) {
        fpsEl.value = String(s.video_fps);
      }
      const vtypeEl = document.getElementById('st-video-type');
      if (vtypeEl && s.video_type) {
        vtypeEl.value = s.video_type;
      }
      if (s.video_mode) document.getElementById('st-video-mode').value = s.video_mode;
      if (s.color_boost) document.getElementById('st-color-boost').value = s.color_boost;
    }

    function updateStudioDomainTabsUi() {
      document.querySelectorAll('#studio-dom-tabs .studio-dom-tab').forEach(btn => {
        const d = btn.getAttribute('data-st-dom');
        btn.classList.toggle('active', d === studioActiveDomain);
        const subEl = document.getElementById('st-dom-sub-' + d);
        if (subEl && d) {
          const st = (studioDraftDomainStyles && studioDraftDomainStyles[d]) || defaultStudioStyleForDomain(d);
          const lay = st.include_card_intro !== false ? st.card_layout : 'card:off';
          const vid = st.include_video !== false
            ? `🎬${Number(st.video_max_duration_s != null ? st.video_max_duration_s : 10).toFixed(0)}s`
            : '🎬off';
          subEl.textContent = `${lay} • ${st.card_theme || 'tinted'} • ${st.color_boost || 'auto'} • ${vid}`;
        }
      });
    }

    function hydrateStudioDraftStylesFromCache(dmdId) {
      if (!dmdId) return;
      const prof = (lastDmdProfiles || []).find(p => p && p.dmd_id === dmdId);
      studioLoadedDmdId = dmdId;
      studioDraftDomainStyles = normalizeDomainStylesObj(prof && prof.domain_styles, prof && prof.card_style);
      applyStudioStyleToControls(studioDraftDomainStyles[studioActiveDomain]);
      updateStudioDomainTabsUi();
    }

    async function selectStudioDomainProfile(dom, opts = {}) {
      if (!CANONICAL_DOMAINS.includes(dom)) dom = 'games';
      if (!opts.skipSaveCurrent && studioInitialized) {
        captureCurrentControlsIntoStudioStyle();
      }
      studioActiveDomain = dom;
      const domEl = document.getElementById('studio-domain');
      if (domEl) domEl.value = dom;
      applyStudioStyleToControls(studioDraftDomainStyles[dom]);
      updateStudioDomainTabsUi();

      if (!opts.keepCandidates) {
        const curCand = studioActiveCandIdx >= 0 ? studioCandidates[studioActiveCandIdx] : null;
        if (!curCand || curCand.domain !== dom) {
          studioCandidates = [];
          studioActiveCandIdx = -1;
          renderStudioCandidateStrip();
        }
      }
      markStudioFullDirty();
      await loadStudioFilters();
      if (studioInitialized) {
        requestStudioCardPreviewNow();
      }
    }

    function initStudioIfNeeded() {
      updateStudioResUi();
      updateStudioSliderLabels();
      updateStudioDomainTabsUi();
      renderStudioActiveDmdBanner();
      loadStudioDmdQueue();
      if (!studioInitialized) {
        studioInitialized = true;
        const langEl = document.getElementById('studio-lang');
        if (langEl && lastServerConfig && lastServerConfig.default_lang) {
          langEl.value = lastServerConfig.default_lang;
        }
        applyStudioStyleToControls(studioDraftDomainStyles[studioActiveDomain]);
        updateStudioDomainTabsUi();
        loadStudioFilters();
        requestStudioCardPreviewNow();
      }
      startStudioAnimationLoop();
    }

    async function loadStudioFilters() {
      const domEl = document.getElementById('studio-domain');
      const sysEl = document.getElementById('studio-system');
      const genEl = document.getElementById('studio-genre');
      if (!domEl || !sysEl || !genEl) return;
      const dom = domEl.value;
      const isGames = dom === 'games';
      sysEl.disabled = !isGames;
      if (!isGames) {
        sysEl.innerHTML = `<option value="">${esc(t('opt_na'))}</option>`;
      }
      try {
        const lang = (document.getElementById('studio-lang') || {}).value || currentUiLang;
        const d = await ensureDomainFilterCatalog(dom, lang);
        const allOpt = `<option value="">${esc(t('opt_all'))}</option>`;
        if (isGames) {
          sysEl.innerHTML = allOpt + (d.systems || []).map(
            s => `<option value="${esc(s.id)}">${esc(s.label)} (${esc(s.id)})</option>`
          ).join('');
        }
        genEl.innerHTML = allOpt + (d.genres || []).map(
          g => `<option value="${esc(g.id)}">${esc(g.label)}</option>`
        ).join('');
      } catch (err) {
        console.error(err);
      }
    }

    async function runStudioSearch() {
      const dom = document.getElementById('studio-domain').value;
      const lang = document.getElementById('studio-lang').value || 'fr';
      const sys = document.getElementById('studio-system').value;
      const gen = document.getElementById('studio-genre').value;
      const q = document.getElementById('studio-query').value.trim();
      const st = document.getElementById('studio-search-status');
      const btn = document.getElementById('btn-studio-search');
      btn.disabled = true;
      st.style.color = 'var(--cyan)';
      st.textContent = t('msg_searching');

      try {
        const params = new URLSearchParams({lang: lang, limit: '8'});
        if (q) params.set('q', q);
        if (dom === 'games' && sys) params.set('systems', sys);
        if (gen) params.set('genres', gen);
        const r = await apiFetch(`/api/v1/${dom}/search?` + params.toString());
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        studioCandidates = Array.isArray(d.items) ? d.items : [];
        if (!studioCandidates.length) {
          studioActiveCandIdx = -1;
          renderStudioCandidateStrip();
          st.style.color = 'var(--amber)';
          st.textContent = t('msg_no_candidate');
          return;
        }
        studioActiveCandIdx = 0;
        studioFullPreviewId = '';
        renderStudioCandidateStrip();
        st.style.color = 'var(--green)';
        st.textContent = t('msg_search_count', {n: studioCandidates.length, l: lang.toUpperCase()});
        requestStudioCardPreviewNow();
      } catch (err) {
        st.style.color = 'var(--red)';
        st.textContent = '⚠️ ' + (err.message || String(err));
      } finally {
        btn.disabled = false;
      }
    }

    function renderStudioCandidateStrip() {
      const strip = document.getElementById('studio-cand-strip');
      if (!strip) return;
      if (!studioCandidates.length) {
        strip.innerHTML = '';
        return;
      }
      strip.innerHTML = studioCandidates.map((c, idx) => {
        const activeCls = idx === studioActiveCandIdx ? ' active' : '';
        const imgHtml = c.poster_url
          ? `<img src="${esc(c.poster_url)}" alt="" loading="lazy" onerror="this.style.display='none'" />`
          : '';
        const snapTag = c.snapshot_count ? ` • 📸${Number(c.snapshot_count)}` : '';
        const vidTag = c.has_video ? ' • 🎬' : '';
        return `<div class="studio-cand-pill${activeCls}" onclick="selectStudioCandidate(${idx})">
          ${imgHtml}
          <div style="min-width:0;overflow:hidden">
            <div style="font-weight:700;font-size:0.82rem;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.title)}</div>
            <div style="font-size:0.72rem;color:var(--muted)">${esc(c.badge || c.domain.toUpperCase())} ${c.year ? '• ' + esc(c.year) : ''}${esc(snapTag)}${vidTag}</div>
          </div>
        </div>`;
      }).join('');
    }

    function selectStudioCandidate(idx) {
      if (!studioCandidates[idx]) return;
      studioActiveCandIdx = idx;
      markStudioFullDirty();
      renderStudioCandidateStrip();
      requestStudioCardPreviewNow();
    }

    async function openCandidateInStudio(idx) {
      const c = lastSearchCandidates[idx];
      if (!c) return;
      switchTab('studio');
      const langEl = document.getElementById('studio-lang');
      if (langEl && c.lang) langEl.value = c.lang;
      studioCandidates = lastSearchCandidates.slice();
      studioActiveCandIdx = idx;
      await selectStudioDomainProfile(c.domain || 'games', {keepCandidates: true});
      renderStudioCandidateStrip();
    }

    async function openStudioForDmd(dmdId) {
      const backBtn = document.getElementById('btn-studio-back-rotation');
      if (backBtn) backBtn.style.display = 'none';
      studioReturnToRotationDmd = '';
      switchTab('studio');
      syncTargetDmdSelector();
      const stSel = document.getElementById('studio-target-dmd');
      const topSel = document.getElementById('studio-top-dmd-select');
      if (dmdId) {
        if (stSel) stSel.value = dmdId;
        if (topSel) topSel.value = dmdId;
        await onStudioTargetDmdChange();
        syncStudioResFromActiveDmd();
      }
    }

    async function openStudioForDmdDomain(dmdId, dom) {
      studioReturnToRotationDmd = dmdId || '__editor__';
      const backBtn = document.getElementById('btn-studio-back-rotation');
      if (backBtn) backBtn.style.display = 'inline-flex';
      switchTab('studio');
      syncTargetDmdSelector();
      const stSel = document.getElementById('studio-target-dmd');
      const topSel = document.getElementById('studio-top-dmd-select');
      if (dmdId) {
        if (stSel && Array.from(stSel.options).some(o => o.value === dmdId)) stSel.value = dmdId;
        if (topSel && Array.from(topSel.options).some(o => o.value === dmdId)) topSel.value = dmdId;
        await onStudioTargetDmdChange();
        syncStudioResFromActiveDmd();
      }
      await selectStudioDomainProfile(dom || 'games', {skipSaveCurrent: true});
    }

    async function openStudioForDmdDomainFromEditor(dom) {
      const dmdId = (document.getElementById('prof-dmd-id') || {}).value.trim();
      const [ew, eh] = ((document.getElementById('prof-res') || {}).value || '128x32').split('x').map(Number);
      const edLang = (document.getElementById('prof-lang') || {}).value || 'fr';

      studioReturnToRotationDmd = dmdId || '__editor__';
      const backBtn = document.getElementById('btn-studio-back-rotation');
      if (backBtn) backBtn.style.display = 'inline-flex';

      switchTab('studio');
      syncTargetDmdSelector();
      const stSel = document.getElementById('studio-target-dmd');
      const topSel = document.getElementById('studio-top-dmd-select');
      if (dmdId) {
        if (stSel) {
          if (!Array.from(stSel.options).some(o => o.value === dmdId)) {
            const opt = `<option value="${esc(dmdId)}">📺 ${esc(dmdId)} (${ew || 128}×${eh || 32})</option>`;
            stSel.insertAdjacentHTML('beforeend', opt);
            if (topSel) topSel.insertAdjacentHTML('beforeend', opt);
          }
          stSel.value = dmdId;
        }
        if (topSel) topSel.value = dmdId;
        studioLoadedDmdId = dmdId;
      }
      const langEl = document.getElementById('studio-lang');
      if (langEl) langEl.value = edLang;
      if (ew && eh) {
        studioWidth = ew;
        studioHeight = eh;
        updateStudioResUi();
      }
      studioDraftDomainStyles = normalizeDomainStylesObj(editingProfileDomainStyles, editingProfileCardStyle);
      await selectStudioDomainProfile(dom || 'games', {skipSaveCurrent: true});
      await loadStudioDmdQueue();
    }

    function returnToDmdRotationEditor() {
      captureCurrentControlsIntoStudioStyle();
      editingProfileDomainStyles = normalizeDomainStylesObj(studioDraftDomainStyles, editingProfileCardStyle);
      switchTab('overview');
      const card = document.getElementById('dmd-profile-editor-card');
      if (card) {
        card.style.display = 'block';
        renderPlaylistEditorRows();
        card.scrollIntoView({behavior: 'smooth', block: 'nearest'});
      }
    }

    function updateStudioResUi() {
      const isTate = studioHeight > studioWidth;
      const geomPill = document.getElementById('studio-geom-pill');
      if (geomPill) {
        geomPill.textContent = `${studioWidth}×${studioHeight} • ${isTate ? 'TATE' : (studioWidth === studioHeight ? 'SQUARE' : 'YOKO')}`;
      }
      const tateBtn = document.getElementById('btn-studio-tate');
      if (tateBtn) {
        tateBtn.classList.toggle('active', isTate);
      }
      const normKey = isTate ? `${studioHeight}x${studioWidth}` : `${studioWidth}x${studioHeight}`;
      document.querySelectorAll('.res-chip').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-res') === normKey);
      });
      renderStudioActiveDmdBanner();
    }

    function setStudioBaseRes(w, h) {
      const wasTate = studioHeight > studioWidth;
      if (wasTate && w !== h) {
        studioWidth = h;
        studioHeight = w;
      } else {
        studioWidth = w;
        studioHeight = h;
      }
      markStudioFullDirty();
      updateStudioResUi();
      scheduleStudioCardPreview();
    }

    function toggleStudioTate() {
      if (studioWidth === studioHeight) {
        studioWidth = 64;
        studioHeight = 128;
      } else {
        const tmp = studioWidth;
        studioWidth = studioHeight;
        studioHeight = tmp;
      }
      markStudioFullDirty();
      updateStudioResUi();
      scheduleStudioCardPreview();
    }

    function selectStudioLayout(layoutId) {
      studioCardLayout = layoutId;
      document.querySelectorAll('#studio-layout-grid .layout-card-btn').forEach(b => {
        b.classList.toggle('active', b.getAttribute('data-layout') === layoutId);
      });
      scheduleStudioCardPreview();
    }

    function onStudioAccentModeChange() {
      const mode = document.getElementById('studio-accent-mode').value;
      const picker = document.getElementById('studio-accent-picker');
      if (picker) picker.disabled = mode !== 'custom';
      scheduleStudioCardPreview();
    }

    function getStudioAccentValue() {
      const modeEl = document.getElementById('studio-accent-mode');
      const picker = document.getElementById('studio-accent-picker');
      if (modeEl && modeEl.value === 'custom' && picker) {
        return picker.value || '#ff6d00';
      }
      return 'auto';
    }

    function updateStudioSliderLabels() {
      const spEl = document.getElementById('st-scroll-speed');
      const spLbl = document.getElementById('lbl-val-scroll');
      if (spEl && spLbl) {
        const v = parseInt(spEl.value, 10) || 4;
        const tag = v <= 2 ? 'Slow' : (v <= 5 ? 'Normal' : (v <= 8 ? 'Fast' : 'Turbo'));
        spLbl.textContent = `${v} (${tag})`;
      }
      const holdEl = document.getElementById('st-card-hold');
      const holdLbl = document.getElementById('lbl-val-hold');
      if (holdEl && holdLbl) {
        holdLbl.textContent = `${parseFloat(holdEl.value || '0.9').toFixed(1)}s`;
      }
    }

    function markStudioFullDirty() {
      studioFullPreviewId = '';
      if (studioInitialized) {
        captureCurrentControlsIntoStudioStyle();
        updateStudioDomainTabsUi();
      }
      const badge = document.getElementById('studio-sim-badge');
      if (badge) {
        badge.className = 'pill info';
        badge.textContent = t('studio_badge_card_fast');
      }
    }

    function buildStudioCommonPayload() {
      const cand = getActiveStudioCandidate();
      const dom = (document.getElementById('studio-domain') || {}).value || cand.domain || 'games';
      const lang = (document.getElementById('studio-lang') || {}).value || cand.lang || 'fr';
      const st = captureCurrentControlsIntoStudioStyle();
      updateStudioDomainTabsUi();
      return {
        domain: dom,
        candidate_id: cand.candidate_id || '',
        query: cand.title || '',
        lang: lang,
        width: studioWidth,
        height: studioHeight,
        fps: st.video_fps,
        video_fps: st.video_fps,
        title: cand.title || 'Metal Slug X',
        year: cand.year || '',
        badge: cand.badge || dom.toUpperCase(),
        badge_color: cand.badge_color || '#FF6D00',
        subtitle: cand.subtitle || '',
        rating: cand.rating || '',
        synopsis: cand.synopsis || '',
        poster_url: cand.poster_url || '',
        include_card_intro: st.include_card_intro,
        card_layout: st.card_layout,
        card_theme: st.card_theme,
        card_font: st.card_font,
        card_accent_color: st.card_accent_color,
        include_poster: st.include_poster,
        show_badge: st.show_badge,
        show_year: st.show_year,
        show_rating: st.show_rating,
        include_synopsis: st.include_synopsis,
        text_scroll_speed: st.text_scroll_speed,
        card_hold_s: st.card_hold_s,
        jacket_panorama: st.jacket_panorama,
        marquee_panorama: st.marquee_panorama,
        snapshot_panorama_count: st.snapshot_panorama_count,
        panorama_duration_s: st.panorama_duration_s,
        include_video: st.include_video,
        video_type: st.video_type,
        video_max_duration_s: st.video_max_duration_s,
        video_mode: st.video_mode,
        color_boost: st.color_boost
      };
    }

    function scheduleStudioCardPreview() {
      markStudioFullDirty();
      if (studioCardDebounceTimer) clearTimeout(studioCardDebounceTimer);
      studioCardDebounceTimer = setTimeout(() => {
        requestStudioCardPreviewNow();
      }, 170);
    }

    function decodeDmdsBinaryBuffer(buf) {
      if (!buf || buf.byteLength < 16) return null;
      const dv = new DataView(buf);
      const m0 = dv.getUint8(0), m1 = dv.getUint8(1), m2 = dv.getUint8(2), m3 = dv.getUint8(3);
      if (m0 !== 0x44 || m1 !== 0x4d || m2 !== 0x44 || m3 !== 0x53) return null;
      const fps = Math.max(1, dv.getUint8(5) || 15);
      const w = dv.getUint16(6, true);
      const h = dv.getUint16(8, true);
      const declaredFrames = dv.getUint16(10, true);
      const frameDurMs = Math.max(16, dv.getUint16(12, true) || Math.round(1000 / fps));
      const frameBytes = w * h * 2;
      if (w <= 0 || h <= 0 || frameBytes <= 0) return null;
      const availFrames = Math.floor((buf.byteLength - 16) / frameBytes);
      const totalFrames = Math.min(declaredFrames || availFrames, availFrames);
      const frames = [];
      const pxCount = w * h;
      for (let f = 0; f < totalFrames; f++) {
        const base = 16 + f * frameBytes;
        const rgba = new Uint8ClampedArray(pxCount * 4);
        for (let p = 0; p < pxCount; p++) {
          const px565 = dv.getUint16(base + p * 2, true);
          const r5 = (px565 >> 11) & 0x1f;
          const g6 = (px565 >> 5) & 0x3f;
          const b5 = px565 & 0x1f;
          const o = p * 4;
          rgba[o] = (r5 << 3) | (r5 >> 2);
          rgba[o + 1] = (g6 << 2) | (g6 >> 4);
          rgba[o + 2] = (b5 << 3) | (b5 >> 2);
          rgba[o + 3] = 255;
        }
        frames.push(rgba);
      }
      return {w, h, fps, frameDurMs, frames};
    }

    function applyDecodedStudioStream(decoded, chapters, isFullSeq) {
      if (!decoded || !decoded.frames.length) return;
      studioStreamW = decoded.w;
      studioStreamH = decoded.h;
      studioStreamFps = decoded.fps;
      studioFrameDurMs = decoded.frameDurMs;
      studioFrames = decoded.frames;
      studioChapters = Array.isArray(chapters) ? chapters : [];
      studioFrameIdx = 0;
      studioPlaying = true;
      const playBtn = document.getElementById('btn-studio-play');
      if (playBtn) playBtn.textContent = '⏸';
      const scrub = document.getElementById('studio-scrubber');
      if (scrub) {
        scrub.max = String(Math.max(0, studioFrames.length - 1));
        scrub.value = '0';
      }
      const badge = document.getElementById('studio-sim-badge');
      if (badge) {
        badge.className = isFullSeq ? 'pill ok' : 'pill info';
        badge.textContent = isFullSeq ? t('studio_badge_full_0ms') : t('studio_badge_card_fast');
      }
      renderStudioChaptersBar();
      redrawCurrentStudioFrame();
      startStudioAnimationLoop();
    }

    function renderStudioChaptersBar() {
      const bar = document.getElementById('studio-chapters-bar');
      if (!bar) return;
      if (!studioChapters.length) {
        bar.innerHTML = '';
        return;
      }
      bar.innerHTML = studioChapters.map((ch, idx) => {
        const startF = Number(ch.start_frame || 0);
        const durS = Number(ch.duration_s || 0).toFixed(1);
        return `<button type="button" class="chapter-pill" data-ch-idx="${idx}" onclick="jumpToStudioChapter(${startF})">
          ${esc(ch.label || ch.id)} (${durS}s)
        </button>`;
      }).join('');
    }

    function jumpToStudioChapter(startFrame) {
      if (!studioFrames.length) return;
      studioFrameIdx = Math.max(0, Math.min(studioFrames.length - 1, Number(startFrame) || 0));
      redrawCurrentStudioFrame();
    }

    function computeStudioScale(w, h) {
      const zoomVal = (document.getElementById('studio-led-zoom') || {}).value || 'auto';
      if (zoomVal !== 'auto') {
        return Math.max(2, Math.min(10, parseInt(zoomVal, 10) || 4));
      }
      const maxW = 480;
      const maxH = 380;
      return Math.max(2, Math.min(7, Math.floor(Math.min(maxW / Math.max(1, w), maxH / Math.max(1, h)))));
    }

    function redrawCurrentStudioFrame() {
      const canvas = document.getElementById('studio-dmd-canvas');
      if (!canvas || !studioFrames.length) return;
      const rgba = studioFrames[studioFrameIdx];
      if (!rgba) return;

      const w = studioStreamW;
      const h = studioStreamH;
      const scale = computeStudioScale(w, h);
      const cw = w * scale;
      const ch = h * scale;
      if (canvas.width !== cw || canvas.height !== ch) {
        canvas.width = cw;
        canvas.height = ch;
      }
      const ctx = canvas.getContext('2d');
      const ledMode = (document.getElementById('studio-led-mode') || {}).value || 'smd_round';

      if (ledMode === 'raw') {
        studioOffscreenCanvas.width = w;
        studioOffscreenCanvas.height = h;
        const octx = studioOffscreenCanvas.getContext('2d');
        octx.putImageData(new ImageData(rgba, w, h), 0, 0);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(studioOffscreenCanvas, 0, 0, cw, ch);
      } else if (ledMode === 'pixel_grid') {
        // Faithful port of dmd_gif_converter dmd_led_sim.py: (scale-1)x(scale-1) cells + 1px dark pitch
        ctx.fillStyle = '#05070a';
        ctx.fillRect(0, 0, cw, ch);
        const cell = Math.max(1, scale - 1);
        for (let y = 0; y < h; y++) {
          const py = y * scale;
          for (let x = 0; x < w; x++) {
            const o = (y * w + x) * 4;
            const r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
            ctx.fillStyle = (r + g + b < 8) ? '#0c1017' : `rgb(${r},${g},${b})`;
            ctx.fillRect(x * scale, py, cell, cell);
          }
        }
      } else {
        // SMD Round HUB75 LEDs with subtle glow bloom on bright pixels
        ctx.fillStyle = '#040609';
        ctx.fillRect(0, 0, cw, ch);
        const rad = Math.max(1.0, scale * 0.41);
        const half = scale * 0.5;
        for (let y = 0; y < h; y++) {
          const cy = y * scale + half;
          for (let x = 0; x < w; x++) {
            const cx = x * scale + half;
            const o = (y * w + x) * 4;
            const r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
            const lum = r + g + b;
            if (lum < 10) {
              ctx.fillStyle = '#0d1118';
              ctx.beginPath();
              ctx.arc(cx, cy, rad * 0.85, 0, Math.PI * 2);
              ctx.fill();
            } else {
              if (lum > 280 && scale >= 4) {
                ctx.fillStyle = `rgba(${r},${g},${b},0.22)`;
                ctx.beginPath();
                ctx.arc(cx, cy, rad * 1.32, 0, Math.PI * 2);
                ctx.fill();
              }
              ctx.fillStyle = `rgb(${r},${g},${b})`;
              ctx.beginPath();
              ctx.arc(cx, cy, rad, 0, Math.PI * 2);
              ctx.fill();
            }
          }
        }
      }

      const metaEl = document.getElementById('studio-sim-meta');
      if (metaEl) {
        const durTot = ((studioFrames.length * studioFrameDurMs) / 1000).toFixed(1);
        metaEl.textContent = `${w}×${h} • ${studioStreamFps} FPS • ${durTot}s`;
      }
      const fLbl = document.getElementById('studio-sim-frame-lbl');
      if (fLbl) {
        fLbl.textContent = `Frame ${studioFrameIdx + 1} / ${studioFrames.length}`;
      }
      const scrub = document.getElementById('studio-scrubber');
      if (scrub && document.activeElement !== scrub) {
        scrub.value = String(studioFrameIdx);
      }
      document.querySelectorAll('#studio-chapters-bar .chapter-pill').forEach((btn, idx) => {
        const ch = studioChapters[idx];
        if (!ch) return;
        const sf = Number(ch.start_frame || 0);
        const ef = sf + Number(ch.frames || 0);
        btn.classList.toggle('active', studioFrameIdx >= sf && studioFrameIdx < ef);
      });
    }

    function startStudioAnimationLoop() {
      if (studioRafId) return;
      function tick(nowMs) {
        const pane = document.getElementById('tab-studio');
        if (!pane || !pane.classList.contains('active')) {
          studioRafId = 0;
          return;
        }
        if (studioPlaying && studioFrames.length > 1) {
          if (!studioLastTickMs) studioLastTickMs = nowMs;
          if (nowMs - studioLastTickMs >= studioFrameDurMs) {
            studioLastTickMs = nowMs;
            studioFrameIdx = (studioFrameIdx + 1) % studioFrames.length;
            redrawCurrentStudioFrame();
          }
        }
        studioRafId = requestAnimationFrame(tick);
      }
      studioRafId = requestAnimationFrame(tick);
    }

    function toggleStudioPlay() {
      studioPlaying = !studioPlaying;
      const btn = document.getElementById('btn-studio-play');
      if (btn) btn.textContent = studioPlaying ? '⏸' : '▶';
    }

    function studioStepFrame(delta) {
      if (!studioFrames.length) return;
      studioPlaying = false;
      const btn = document.getElementById('btn-studio-play');
      if (btn) btn.textContent = '▶';
      studioFrameIdx = (studioFrameIdx + delta + studioFrames.length) % studioFrames.length;
      redrawCurrentStudioFrame();
    }

    function onStudioScrub(val) {
      if (!studioFrames.length) return;
      studioFrameIdx = Math.max(0, Math.min(studioFrames.length - 1, parseInt(val, 10) || 0));
      redrawCurrentStudioFrame();
    }

    async function requestStudioCardPreviewNow() {
      if (studioCardAbortCtrl) {
        try { studioCardAbortCtrl.abort(); } catch (_) {}
      }
      const ctrl = new AbortController();
      studioCardAbortCtrl = ctrl;
      const payload = buildStudioCommonPayload();
      const st = document.getElementById('studio-sim-status');
      try {
        const r = await apiFetch('/api/v1/studio/card-preview', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
          signal: ctrl.signal
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        let chapters = [];
        try {
          const chHdr = r.headers.get('X-DMD-Chapters');
          if (chHdr) chapters = JSON.parse(chHdr);
        } catch (_) {}
        const buf = await r.arrayBuffer();
        const decoded = decodeDmdsBinaryBuffer(buf);
        if (decoded) {
          applyDecodedStudioStream(decoded, chapters, false);
          if (st) {
            st.style.color = 'var(--green)';
            st.textContent = t('msg_studio_card_ok', {f: decoded.frames.length, fps: decoded.fps});
          }
        }
      } catch (err) {
        if (err && err.name === 'AbortError') return;
        if (st) {
          st.style.color = 'var(--red)';
          st.textContent = '⚠️ ' + (err.message || String(err));
        }
      }
    }

    async function runStudioFullPreview() {
      const btn = document.getElementById('btn-studio-full-sim');
      const st = document.getElementById('studio-sim-status');
      const payload = buildStudioCommonPayload();
      btn.disabled = true;
      st.style.color = 'var(--cyan)';
      st.textContent = t('msg_studio_full_building');

      try {
        const r = await apiFetch('/api/v1/studio/full-preview', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        if (!r.ok) {
          const ej = await r.json().catch(() => ({}));
          throw new Error(ej.detail || ('HTTP ' + r.status));
        }
        const previewId = (r.headers.get('X-DMD-Preview-Id') || '').trim();
        let chapters = [];
        try {
          const chHdr = r.headers.get('X-DMD-Chapters');
          if (chHdr) chapters = JSON.parse(chHdr);
        } catch (_) {}
        const buf = await r.arrayBuffer();
        const decoded = decodeDmdsBinaryBuffer(buf);
        if (decoded) {
          studioFullPreviewId = previewId;
          applyDecodedStudioStream(decoded, chapters, true);
          st.style.color = 'var(--green)';
          st.textContent = t('msg_studio_full_ok', {f: decoded.frames.length});
        }
      } catch (err) {
        st.style.color = 'var(--red)';
        st.textContent = '⚠️ ' + (err.message || String(err));
      } finally {
        btn.disabled = false;
      }
    }

    function findTargetDmdMeta(dmdId) {
      if (!dmdId) return null;
      const cli = (lastClients || []).find(c => c && c.dmd_id === dmdId);
      const prof = (lastDmdProfiles || []).find(p => p && p.dmd_id === dmdId);
      if (!cli && !prof) return null;
      return {
        dmd_id: dmdId,
        width: Number((prof && prof.width) || (cli && cli.width) || 128),
        height: Number((prof && prof.height) || (cli && cli.height) || 32),
        lang: (prof && prof.lang) || (cli && cli.lang) || 'fr',
        config_source: (prof && prof.config_source) || (cli && cli.config_source) || 'client',
        supports_local_card: prof ? prof.supports_local_card !== false : (cli ? cli.supports_local_card !== false : true),
        playing_title: (cli && (cli.playing_title || cli.active_title || cli.ready_title)) || '',
        udp_streaming: Boolean(cli && cli.udp_streaming),
        card_style: (prof && prof.card_style) || null,
        domain_styles: (prof && prof.domain_styles) || null
      };
    }

    function renderStudioActiveDmdBanner() {
      const banner = document.getElementById('studio-active-dmd-banner');
      const sel = document.getElementById('studio-target-dmd');
      if (!banner || !sel) return;
      const dmdId = sel.value;
      const meta = findTargetDmdMeta(dmdId);
      if (!meta) {
        banner.innerHTML = esc(t('studio_opt_no_dmd'));
        return;
      }
      const modeLbl = meta.config_source === 'server'
        ? (meta.supports_local_card === false ? t('mode_badge_2b') : t('mode_badge_2a'))
        : t('mode_badge_client');
      const playStr = meta.playing_title
        ? `▶ <b>${esc(meta.playing_title)}</b>`
        : esc(t('studio_dmd_idle'));
      let mismatchHtml = '';
      if (meta.width !== studioWidth || meta.height !== studioHeight) {
        mismatchHtml = `<div style="color:var(--amber);margin-top:4px;font-size:0.78rem">⚠️ ${esc(t('studio_dmd_res_mismatch', {
          sw: studioWidth, sh: studioHeight, d: dmdId, dw: meta.width, dh: meta.height
        }))}</div>`;
      }
      banner.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
        <span>${playStr}</span>
        <span><span class="pill info">${esc(modeLbl)}</span> <code>${esc(meta.width)}×${esc(meta.height)}</code></span>
      </div>${mismatchHtml}`;
    }

    async function loadDmdDomainStylesIntoStudio(dmdId) {
      const stSel = document.getElementById('studio-target-dmd');
      const topSel = document.getElementById('studio-top-dmd-select');
      if (stSel && stSel.value !== dmdId) stSel.value = dmdId;
      if (topSel && topSel.value !== dmdId) topSel.value = dmdId;
      if (!dmdId) {
        studioLoadedDmdId = '';
        studioDraftDomainStyles = normalizeDomainStylesObj(null, null);
        applyStudioStyleToControls(studioDraftDomainStyles[studioActiveDomain]);
        updateStudioDomainTabsUi();
        return;
      }
      let prof = (lastDmdProfiles || []).find(p => p && p.dmd_id === dmdId) || null;
      try {
        const rp = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`);
        if (rp.ok) {
          prof = (await rp.json()).profile || prof;
        }
      } catch (_) {}
      studioLoadedDmdId = dmdId;
      studioDraftDomainStyles = normalizeDomainStylesObj(prof && prof.domain_styles, prof && prof.card_style);
      if (prof && prof.lang) {
        const langEl = document.getElementById('studio-lang');
        if (langEl) langEl.value = prof.lang;
      }
      applyStudioStyleToControls(studioDraftDomainStyles[studioActiveDomain]);
      updateStudioDomainTabsUi();
    }

    async function onStudioTopDmdChange(dmdId) {
      await loadDmdDomainStylesIntoStudio(dmdId || '');
      renderStudioActiveDmdBanner();
      await loadStudioDmdQueue();
      scheduleStudioCardPreview();
    }

    async function onStudioTargetDmdChange() {
      const sel = document.getElementById('studio-target-dmd');
      const dmdId = sel ? sel.value : '';
      await loadDmdDomainStylesIntoStudio(dmdId);
      renderStudioActiveDmdBanner();
      await loadStudioDmdQueue();
      scheduleStudioCardPreview();
    }

    function syncStudioResFromActiveDmd() {
      const sel = document.getElementById('studio-target-dmd');
      const meta = sel ? findTargetDmdMeta(sel.value) : null;
      if (!meta) return;
      studioWidth = meta.width;
      studioHeight = meta.height;
      markStudioFullDirty();
      updateStudioResUi();
      scheduleStudioCardPreview();
    }

    async function skipStudioTargetDmd() {
      const dmdId = (document.getElementById('studio-target-dmd') || {}).value || '';
      const msgEl = document.getElementById('studio-queue-action-msg');
      if (!dmdId) {
        if (msgEl) {
          msgEl.style.color = 'var(--amber)';
          msgEl.textContent = t('msg_need_target_dmd');
        }
        return;
      }
      await controlDmd(dmdId, 'skip');
    }

    async function loadStudioDmdQueue() {
      const dmdId = (document.getElementById('studio-target-dmd') || {}).value || '';
      const box = document.getElementById('studio-dmd-queue-list');
      if (!box) return;
      if (!dmdId) {
        box.innerHTML = '';
        return;
      }
      try {
        const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/queue`, {cache: 'no-store'});
        if (!r.ok) return;
        const d = await r.json();
        const items = Array.isArray(d.items) ? d.items : [];
        if (!items.length) {
          box.innerHTML = `<span style="color:var(--muted)">${esc(t('queue_empty'))}</span>`;
          return;
        }
        const safeDmd = esc(dmdId).replace(/'/g, "\\'");
        box.innerHTML = items.map((it, i) => {
          const stClass = it.status === 'ready' || it.status === 'playing' ? 'ok' : 'warn';
          const safeQid = esc(it.queue_id).replace(/'/g, "\\'");
          const simPill = it.from_studio_sim ? ` <span class="pill ok">${esc(t('pill_sim_studio'))}</span>` : '';
          return `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:7px 10px;background:#0d121b;border:1px solid var(--border);border-radius:8px;margin-bottom:6px">
            <div>
              <b>#${i + 1} — ${esc(it.title)}</b>
              <span class="pill info" style="margin-left:5px">${esc((it.domain || '').toUpperCase())}</span>
              <span class="pill ${stClass}">${esc(it.status)}</span>${simPill}
            </div>
            <button type="button" class="danger mini-btn" onclick="removeQueueItem('${safeDmd}', '${safeQid}')">✕</button>
          </div>`;
        }).join('');
      } catch (err) {
        console.error(err);
      }
    }

    async function enqueueStudioSim(mode) {
      const dmdId = (document.getElementById('studio-target-dmd') || {}).value || '';
      const msgEl = document.getElementById('studio-queue-action-msg');
      if (!dmdId) {
        msgEl.style.color = 'var(--amber)';
        msgEl.textContent = t('msg_need_target_dmd');
        return;
      }
      const p = buildStudioCommonPayload();
      const markSeen = document.getElementById('studio-mark-seen').value === 'true';
      const usedPreviewId = studioFullPreviewId || null;

      const reqBody = {
        preview_id: usedPreviewId,
        candidate_id: p.candidate_id || '',
        domain: p.domain,
        query: p.query,
        lang: p.lang,
        mode: mode,
        mark_seen: markSeen,
        video_type: p.video_type || 'any',
        include_card_intro: p.include_card_intro,
        card_layout: p.card_layout,
        card_theme: p.card_theme,
        card_font: p.card_font,
        card_accent_color: p.card_accent_color,
        show_badge: p.show_badge,
        show_year: p.show_year,
        show_rating: p.show_rating,
        card_hold_s: p.card_hold_s,
        include_poster: p.include_poster,
        include_synopsis: p.include_synopsis,
        text_scroll_speed: p.text_scroll_speed,
        jacket_panorama: p.jacket_panorama,
        marquee_panorama: p.marquee_panorama,
        snapshot_panorama_count: p.snapshot_panorama_count,
        panorama_duration_s: p.panorama_duration_s,
        include_video: p.include_video,
        video_max_duration_s: p.video_max_duration_s,
        video_fps: p.video_fps,
        video_mode: p.video_mode,
        color_boost: p.color_boost
      };

      try {
        const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/queue`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(reqBody)
        });
        if (!r.ok) {
          const ej = await r.json().catch(() => ({}));
          throw new Error(ej.detail || ('HTTP ' + r.status));
        }
        const resJson = await r.json();
        const item = resJson.item || {};
        msgEl.style.color = 'var(--green)';
        if (usedPreviewId && item.status === 'ready') {
          msgEl.textContent = t('msg_studio_sim_queued_0ms', {t: item.title || p.title, d: dmdId});
        } else {
          msgEl.textContent = t('msg_studio_sim_queued_bg', {
            t: item.title || p.title,
            d: dmdId,
            s: item.status || 'queued'
          });
        }
        await loadStudioDmdQueue();
        await loadTargetDmdQueue();
        loadHealthAndConfig(false);
      } catch (err) {
        msgEl.style.color = 'var(--red)';
        msgEl.textContent = '⚠️ ' + (err.message || String(err));
      }
    }

    function setStudioSaveToast(msg, color) {
      ['studio-top-save-msg', 'studio-queue-action-msg'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.style.color = color;
        el.textContent = msg;
      });
    }

    async function saveStudioStyleToDmdProfile(applyAllDomains = false) {
      const dmdId = (document.getElementById('studio-target-dmd') || {}).value
        || (document.getElementById('studio-top-dmd-select') || {}).value
        || '';
      if (!dmdId) {
        setStudioSaveToast(t('msg_need_target_dmd'), 'var(--amber)');
        return;
      }
      const p = buildStudioCommonPayload();
      const curStyle = captureCurrentControlsIntoStudioStyle();

      let prof = null;
      try {
        const rp = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`);
        if (rp.ok) {
          prof = (await rp.json()).profile;
        }
      } catch (_) {}

      const mergedDomainStyles = normalizeDomainStylesObj(prof && prof.domain_styles, prof && prof.card_style);
      CANONICAL_DOMAINS.forEach(dom => {
        if (studioDraftDomainStyles && studioDraftDomainStyles[dom]) {
          mergedDomainStyles[dom] = Object.assign({}, mergedDomainStyles[dom], studioDraftDomainStyles[dom]);
        }
      });

      if (applyAllDomains) {
        CANONICAL_DOMAINS.forEach(dom => {
          mergedDomainStyles[dom] = Object.assign({}, curStyle);
        });
      } else {
        mergedDomainStyles[studioActiveDomain] = Object.assign({}, curStyle);
      }
      studioDraftDomainStyles = mergedDomainStyles;
      updateStudioDomainTabsUi();

      const baseMeta = findTargetDmdMeta(dmdId) || {width: studioWidth, height: studioHeight, lang: p.lang};
      const existingPl = (prof && Array.isArray(prof.playlist) && prof.playlist.length)
        ? prof.playlist
        : defaultPlaylistRows((prof && prof.lang) || p.lang);

      const updatedPl = existingPl.map(slot => {
        const dom = slot.domain || 'games';
        const ds = mergedDomainStyles[dom] || curStyle;
        return Object.assign({}, slot, {
          video_type: ds.video_type || slot.video_type || 'any',
          jacket_panorama: ds.jacket_panorama,
          marquee_panorama: ds.marquee_panorama,
          snapshot_panorama_count: ds.snapshot_panorama_count,
          panorama_duration_s: ds.panorama_duration_s,
          include_video: ds.include_video,
          video_max_duration_s: ds.video_max_duration_s,
          video_fps: ds.video_fps,
          video_mode: ds.video_mode,
          color_boost: ds.color_boost
        });
      });

      const supportsLocal = (prof && prof.config_source === 'server')
        ? Boolean(prof.supports_local_card)
        : false;

      const putPayload = {
        dmd_id: dmdId,
        display_name: (prof && prof.display_name) || dmdId,
        config_source: 'server',
        supports_local_card: supportsLocal,
        always_stream_udp: Boolean(prof && prof.always_stream_udp),
        width: (prof && prof.width) || baseMeta.width || studioWidth,
        height: (prof && prof.height) || baseMeta.height || studioHeight,
        udp_ip: (prof && prof.udp_ip) || '',
        udp_port: (prof && prof.udp_port) || 4210,
        lang: (prof && prof.lang) || p.lang,
        inter_item_gap_s: (prof && prof.inter_item_gap_s != null) ? prof.inter_item_gap_s : 1.0,
        domain_styles: mergedDomainStyles,
        card_style: {
          include_card_intro: curStyle.include_card_intro,
          card_layout: curStyle.card_layout,
          card_theme: curStyle.card_theme,
          card_font: curStyle.card_font,
          card_accent_color: curStyle.card_accent_color,
          include_poster: curStyle.include_poster,
          show_badge: curStyle.show_badge,
          show_year: curStyle.show_year,
          show_rating: curStyle.show_rating,
          include_synopsis: curStyle.include_synopsis,
          text_scroll_speed: curStyle.text_scroll_speed,
          card_hold_s: curStyle.card_hold_s
        },
        playlist: updatedPl
      };

      try {
        const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(putPayload)
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        studioLoadedDmdId = dmdId;
        if (editingProfileDmdId === dmdId || studioReturnToRotationDmd === dmdId) {
          editingProfileDomainStyles = normalizeDomainStylesObj(mergedDomainStyles, null);
          renderPlaylistEditorRows();
        }
        const okMsg = applyAllDomains
          ? t('msg_studio_all_doms_saved', {d: dmdId})
          : t('msg_studio_dom_saved', {dom: shortDomainDisplayName(studioActiveDomain), d: dmdId});
        setStudioSaveToast(okMsg, 'var(--green)');
        loadHealthAndConfig(false);
      } catch (err) {
        setStudioSaveToast('⚠️ ' + (err.message || String(err)), 'var(--red)');
      }
    }

    setUiLang(currentUiLang);
    loadHealthAndConfig(true);
    setInterval(() => loadHealthAndConfig(false), 3000);
  </script>
</body>
</html>
"""
