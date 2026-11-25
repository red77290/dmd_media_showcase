/**
 * Studio Parity & Superset Controls for Batch Scraper (`static/js/batch_studio_parity.js`).
 *
 * Brings 100% of Card & Render Studio capabilities (and more) directly into Tab #7:
 * - 12 Content Broadcast Languages (`fr, en, es, de, it, pt, nl, pl, ru, ja, ko, zh`) across both
 *   Random Filter and Recalbox/Batocera `gamelist.xml` modes, with localized live preview samples.
 * - Interactive HUB75 resolution chips + 1-click `⇄ Mode TATE` vertical swap + geometry badge.
 * - Full Card typography (`arcade_pixel` / `clean_sans`), custom `#Hex` accent picker, metadata
 *   pill toggles (`Badge`, `Year`, `★ Rating`), scroll speed (`1..10`), and static hold (`0.2..4.0s`).
 * - 1-click Studio style & resolution import from any active/saved DMD profile.
 * - Embedded `<100ms` real-time HUB75 LED Card Simulator preview (`#batch-card-preview-canvas`).
 */

import { apiFetch, clampFloatInput, clampIntInput, esc } from './api.js';
import { getCurrentUiLang, t } from './i18n/index.js';
import { decodeDmdsBinaryBuffer } from './simulator.js';
import { STUDIO_DEMO_FALLBACKS, uiState } from './state.js';

const SAMPLE_SYNOPSIS_BY_LANG = {
  fr: "Marco, Tarma, Eri et Fio repartent affronter le général Morden et une armada extraterrestre en pixel-art Neo-Geo ultra fluide.",
  en: "Marco, Tarma, Eri, and Fio return to stop General Morden and a mysterious alien armada in crisp Neo-Geo pixel art.",
  es: "Marco, Tarma, Eri y Fio regresan para detener al general Morden y a una armada alienígena en vibrante pixel art de Neo-Geo.",
  de: "Marco, Tarma, Eri und Fio kehren zurück, um General Morden und eine außerirdische Armada in gestochen scharfer Neo-Geo-Pixelkunst aufzuhalten.",
  it: "Marco, Tarma, Eri e Fio tornano per fermare il generale Morden e un'armata aliena in splendida pixel art Neo-Geo.",
  pt: "Marco, Tarma, Eri e Fio retornam para deter o General Morden e uma armada alienígena em vibrante pixel art de Neo-Geo.",
  nl: "Marco, Tarma, Eri en Fio keren terug om generaal Morden en een buitenaardse vloot te stoppen in scherpe Neo-Geo pixelkunst.",
  pl: "Marco, Tarma, Eri i Fio powracają, aby powstrzymać generała Mordena i flotę obcych w klasycznym pixel-arcie Neo-Geo.",
  ru: "Марко, Тарма, Эри и Фио возвращаются, чтобы остановить генерала Мордена и инопланетный флот в классическом пиксель-арте Neo-Geo.",
  ja: "マルコ、ターマ、エリ、フィオが再び集結し、モーデン元帥と謎のエイリアン軍団に立ち向かう王道ドット絵アクション。",
  ko: "마르코, 타마, 에리, 피오가 다시 뭉쳐 모덴 원수와 외계 함대에 맞서는 네오지오 도트 액션 명작.",
  zh: "马可、塔尔马、英里与菲奥再度集结，在精致的Neo-Geo像素画面中迎战摩登元帅与外星舰队。"
};

const batchSimState = {
  baseW: 128,
  baseH: 32,
  isTate: false,
  frames: [],
  w: 128,
  h: 32,
  fps: 15,
  frameDurMs: 66,
  frameIdx: 0,
  animTimer: null,
  debounceTimer: null,
  abortCtrl: null,
  offscreen: null,
  customSample: null
};

export function getBatchAccentColorValue() {
  const modeEl = document.getElementById('batch-accent-mode');
  const pickerEl = document.getElementById('batch-accent-picker');
  if (modeEl && modeEl.value === 'custom' && pickerEl) {
    return pickerEl.value || '#ff6d00';
  }
  return 'auto';
}

export function onBatchAccentModeChange() {
  const modeEl = document.getElementById('batch-accent-mode');
  const pickerEl = document.getElementById('batch-accent-picker');
  if (pickerEl && modeEl) {
    pickerEl.disabled = modeEl.value !== 'custom';
  }
  scheduleBatchCardPreview();
}

export function updateBatchCardSliderLabels() {
  const spEl = document.getElementById('batch-scroll-speed');
  const spLbl = document.getElementById('batch-val-scroll');
  if (spEl && spLbl) {
    const v = clampIntInput('batch-scroll-speed', 1, 10, 4);
    const tag = v <= 2 ? 'Slow' : (v <= 5 ? 'Normal' : (v <= 8 ? 'Fast' : 'Turbo'));
    spLbl.textContent = `${v} (${tag})`;
  }
  const holdEl = document.getElementById('batch-card-hold');
  const holdLbl = document.getElementById('batch-val-hold');
  if (holdEl && holdLbl) {
    const hv = clampFloatInput('batch-card-hold', 0.2, 4.0, 0.9);
    holdLbl.textContent = `${hv.toFixed(1)}s`;
  }
}

export function syncBatchCardLayoutBoxVisibility() {
  const cardBox = document.getElementById('batch-card-layout-box');
  if (!cardBox) return;
  const hasCard = Boolean(document.getElementById('batch-chk-card')?.checked);
  cardBox.style.display = hasCard ? '' : 'none';
}

export function applyBatchPresetControls(presetId) {
  document.querySelectorAll('.batch-preset-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.preset === presetId);
  });
  const presetsMap = {
    full_showcase: { card: true, poster: true, desc: true, jacket: true, marquee: false, snaps: 1, video: true },
    video_only: { card: false, poster: false, desc: false, jacket: false, marquee: false, snaps: 0, video: true },
    poster_only: { card: false, poster: false, desc: false, jacket: true, marquee: false, snaps: 0, video: false },
    marquee_only: { card: false, poster: false, desc: false, jacket: false, marquee: true, snaps: 0, video: false },
    card_description_only: { card: true, poster: true, desc: true, jacket: false, marquee: false, snaps: 0, video: false },
    description_only: { card: true, poster: false, desc: true, jacket: false, marquee: false, snaps: 0, video: false },
    screenshot_only: { card: false, poster: false, desc: false, jacket: false, marquee: false, snaps: 1, video: false }
  };
  const cfg = presetsMap[presetId];
  if (cfg) {
    const setChk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = Boolean(val); };
    setChk('batch-chk-card', cfg.card);
    setChk('batch-chk-poster', cfg.poster);
    setChk('batch-chk-desc', cfg.desc);
    setChk('batch-chk-jacket', cfg.jacket);
    setChk('batch-chk-marquee', cfg.marquee);
    setChk('batch-chk-video', cfg.video);
    const snapsEl = document.getElementById('batch-snap-count');
    if (snapsEl) snapsEl.value = String(cfg.snaps);
  }
  syncBatchCardLayoutBoxVisibility();
}

export function syncBatchGeometryUi() {
  const w = clampIntInput('batch-width', 16, 512, 128);
  const h = clampIntInput('batch-height', 16, 512, 32);
  batchSimState.isTate = h > w;
  if (h > w) {
    batchSimState.baseW = h;
    batchSimState.baseH = w;
  } else {
    batchSimState.baseW = w;
    batchSimState.baseH = h;
  }

  const orient = h > w ? 'TATE' : (w === h ? 'SQUARE' : 'YOKO');
  const pill = document.getElementById('batch-geom-pill');
  if (pill) pill.textContent = `${w}×${h} • ${orient}`;

  const tateBtn = document.getElementById('btn-batch-tate');
  if (tateBtn) tateBtn.classList.toggle('active', h > w);

  const baseKey = `${batchSimState.baseW}x${batchSimState.baseH}`;
  document.querySelectorAll('#batch-res-chips .res-chip').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.res === baseKey);
  });

  const sel = document.getElementById('batch-res-preset');
  const exactKey = `${w}x${h}`;
  if (sel) {
    const hasPreset = Array.from(sel.options).some(o => o.value === exactKey);
    sel.value = hasPreset ? exactKey : 'custom';
    const customWrap = document.getElementById('batch-custom-res-wrap');
    if (customWrap) customWrap.style.display = sel.value === 'custom' ? 'flex' : 'none';
  }
}

export function setBatchBaseRes(baseW, baseH) {
  const bw = Math.max(16, Math.min(512, Number(baseW) || 128));
  const bh = Math.max(16, Math.min(512, Number(baseH) || 32));
  batchSimState.baseW = bw;
  batchSimState.baseH = bh;
  if (bw === bh) batchSimState.isTate = false;
  const effW = batchSimState.isTate ? bh : bw;
  const effH = batchSimState.isTate ? bw : bh;
  const wEl = document.getElementById('batch-width');
  const hEl = document.getElementById('batch-height');
  if (wEl) wEl.value = String(effW);
  if (hEl) hEl.value = String(effH);
  syncBatchGeometryUi();
  scheduleBatchCardPreview();
}

export function toggleBatchTate() {
  const w = clampIntInput('batch-width', 16, 512, 128);
  const h = clampIntInput('batch-height', 16, 512, 32);
  if (w === h) {
    setBatchBaseRes(128, 64);
    batchSimState.isTate = true;
    const wEl = document.getElementById('batch-width');
    const hEl = document.getElementById('batch-height');
    if (wEl) wEl.value = '64';
    if (hEl) hEl.value = '128';
  } else {
    const wEl = document.getElementById('batch-width');
    const hEl = document.getElementById('batch-height');
    if (wEl) wEl.value = String(h);
    if (hEl) hEl.value = String(w);
  }
  syncBatchGeometryUi();
  scheduleBatchCardPreview();
}

export function populateBatchDmdProfileSelector() {
  const sel = document.getElementById('batch-import-dmd');
  if (!sel) return;
  const prev = sel.value;
  const clients = Array.isArray(uiState.lastClients) ? uiState.lastClients : [];
  const opts = [`<option value="">${esc(t('batch_opt_import_dmd_none'))}</option>`];
  clients.forEach(c => {
    if (!c || !c.dmd_id) return;
    const label = `${c.display_name || c.dmd_id} (${c.width || 128}×${c.height || 32} • ${(c.lang || getCurrentUiLang()).toUpperCase()})`;
    opts.push(`<option value="${esc(c.dmd_id)}">${esc(label)}</option>`);
  });
  sel.innerHTML = opts.join('');
  if (prev && clients.some(c => c && c.dmd_id === prev)) {
    sel.value = prev;
  }
}

export async function importBatchStyleFromDmd(onLayoutSelected) {
  const sel = document.getElementById('batch-import-dmd');
  const dmdId = sel ? sel.value : '';
  if (!dmdId) return null;
  try {
    const r = await apiFetch(`/api/v1/profiles/${encodeURIComponent(dmdId)}`);
    if (!r.ok) return null;
    const data = await r.json();
    const prof = data.profile || data;
    if (!prof) return null;

    const wEl = document.getElementById('batch-width');
    const hEl = document.getElementById('batch-height');
    if (wEl && prof.width) wEl.value = String(prof.width);
    if (hEl && prof.height) hEl.value = String(prof.height);
    syncBatchGeometryUi();

    const langEl = document.getElementById('batch-lang');
    if (langEl && prof.lang) langEl.value = prof.lang;

    const dom = document.getElementById('batch-domain')?.value || 'games';
    const st = (prof.domain_styles && prof.domain_styles[dom]) || prof.card_style || {};
    if (st.card_layout && typeof onLayoutSelected === 'function') {
      onLayoutSelected(st.card_layout);
    }
    const setVal = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = String(v); };
    const setChk = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.checked = Boolean(v); };

    setVal('batch-card-theme', st.card_theme);
    setVal('batch-card-font', st.card_font);
    const accMode = document.getElementById('batch-accent-mode');
    const accPick = document.getElementById('batch-accent-picker');
    if (accMode && accPick) {
      const isHex = typeof st.card_accent_color === 'string' && st.card_accent_color.startsWith('#') && st.card_accent_color.length === 7;
      accMode.value = isHex ? 'custom' : 'auto';
      accPick.disabled = !isHex;
      if (isHex) accPick.value = st.card_accent_color;
    }
    setChk('batch-chk-card', st.include_card_intro);
    setChk('batch-chk-poster', st.include_poster);
    setChk('batch-chk-badge', st.show_badge);
    setChk('batch-chk-year', st.show_year);
    setChk('batch-chk-rating', st.show_rating);
    setChk('batch-chk-desc', st.include_synopsis);
    if (st.text_scroll_speed && !Number.isNaN(parseInt(st.text_scroll_speed, 10))) {
      setVal('batch-scroll-speed', parseInt(st.text_scroll_speed, 10));
    }
    if (st.card_hold_s != null) setVal('batch-card-hold', st.card_hold_s);
    updateBatchCardSliderLabels();

    setChk('batch-chk-jacket', st.jacket_panorama);
    setChk('batch-chk-marquee', st.marquee_panorama);
    setChk('batch-chk-video', st.include_video);
    if (st.snapshot_panorama_count != null) setVal('batch-snap-count', Math.min(5, Number(st.snapshot_panorama_count)));
    if (st.panorama_duration_s != null) setVal('batch-pan-dur', st.panorama_duration_s);
    if (st.video_max_duration_s != null) {
      setVal('batch-video-dur', Math.max(2, Math.min(120, Math.round(Number(st.video_max_duration_s)))));
      const durLbl = document.getElementById('batch-video-dur-lbl');
      if (durLbl) durLbl.textContent = `${document.getElementById('batch-video-dur').value}s`;
    }
    if (st.video_fps != null) setVal('batch-fps', st.video_fps);
    if (st.video_type) setVal('batch-video-type', st.video_type);
    if (st.video_mode && ['auto_action', 'panorama', 'center_crop'].includes(st.video_mode)) {
      setVal('batch-video-mode', st.video_mode);
    }
    if (st.color_boost && ['auto', 'pixel_art', 'anime', 'cinema'].includes(String(st.color_boost))) {
      setVal('batch-content-mode', st.color_boost);
    }
    scheduleBatchCardPreview();
    return prof;
  } catch (_) {
    return null;
  }
}

export async function runBatchSampleSearch() {
  const qEl = document.getElementById('batch-sample-query');
  const query = qEl ? qEl.value.trim() : '';
  const dom = document.getElementById('batch-domain')?.value || 'games';
  const lang = document.getElementById('batch-lang')?.value || getCurrentUiLang();
  if (!query) {
    batchSimState.customSample = null;
    requestBatchCardPreviewNow();
    return;
  }
  try {
    const url = `/api/v1/${encodeURIComponent(dom)}/candidates?lang=${encodeURIComponent(lang)}&limit=1&q=${encodeURIComponent(query)}`;
    const r = await apiFetch(url);
    if (r.ok) {
      const d = await r.json();
      const first = Array.isArray(d.candidates) && d.candidates.length ? d.candidates[0] : null;
      if (first) batchSimState.customSample = first;
    }
  } catch (_) {}
  requestBatchCardPreviewNow();
}

export function scheduleBatchCardPreview() {
  if (batchSimState.debounceTimer) clearTimeout(batchSimState.debounceTimer);
  batchSimState.debounceTimer = setTimeout(() => {
    requestBatchCardPreviewNow();
  }, 160);
}

export async function requestBatchCardPreviewNow(cardLayoutOverride) {
  const tabPane = document.getElementById('tab-batch');
  const canvas = document.getElementById('batch-card-preview-canvas');
  if (!canvas || (tabPane && !tabPane.classList.contains('active'))) return;

  if (batchSimState.abortCtrl) {
    try { batchSimState.abortCtrl.abort(); } catch (_) {}
  }
  const ctrl = new AbortController();
  batchSimState.abortCtrl = ctrl;

  const dom = document.getElementById('batch-domain')?.value || 'games';
  const lang = document.getElementById('batch-lang')?.value || getCurrentUiLang();
  const baseCand = (batchSimState.customSample && batchSimState.customSample.domain === dom)
    ? batchSimState.customSample
    : (STUDIO_DEMO_FALLBACKS[dom] || STUDIO_DEMO_FALLBACKS.games);

  const w = clampIntInput('batch-width', 16, 512, 128);
  const h = clampIntInput('batch-height', 16, 512, 32);
  const fps = clampIntInput('batch-fps', 6, 30, 15);
  const activeLayoutBtn = document.querySelector('#batch-layout-grid .layout-card-btn.active');
  const layout = cardLayoutOverride || (activeLayoutBtn && activeLayoutBtn.dataset.layout) || 'classic_split';

  const synopsis = (batchSimState.customSample && batchSimState.customSample.synopsis)
    ? batchSimState.customSample.synopsis
    : (dom === 'games' ? (SAMPLE_SYNOPSIS_BY_LANG[lang] || baseCand.synopsis) : baseCand.synopsis);

  const payload = {
    domain: dom,
    candidate_id: baseCand.candidate_id || '',
    query: baseCand.title || 'Metal Slug X',
    lang,
    width: w,
    height: h,
    fps,
    video_fps: fps,
    title: baseCand.title || 'Metal Slug X',
    year: baseCand.year || '1999',
    badge: baseCand.badge || dom.toUpperCase(),
    badge_color: baseCand.badge_color || '#FF6D00',
    subtitle: baseCand.subtitle || '',
    rating: baseCand.rating || '9.3',
    synopsis,
    poster_url: baseCand.poster_url || '',
    include_card_intro: true,
    card_layout: layout,
    card_theme: document.getElementById('batch-card-theme')?.value || 'tinted',
    card_font: document.getElementById('batch-card-font')?.value || 'arcade_pixel',
    card_accent_color: getBatchAccentColorValue(),
    include_poster: Boolean(document.getElementById('batch-chk-poster')?.checked),
    show_badge: Boolean(document.getElementById('batch-chk-badge')?.checked),
    show_year: Boolean(document.getElementById('batch-chk-year')?.checked),
    show_rating: Boolean(document.getElementById('batch-chk-rating')?.checked),
    include_synopsis: Boolean(document.getElementById('batch-chk-desc')?.checked),
    text_scroll_speed: String(clampIntInput('batch-scroll-speed', 1, 10, 4)),
    card_hold_s: clampFloatInput('batch-card-hold', 0.2, 4.0, 0.9),
    color_boost: document.getElementById('batch-content-mode')?.value || 'auto'
  };

  try {
    const r = await apiFetch('/api/v1/studio/card-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctrl.signal
    });
    if (!r.ok) return;
    const decoded = decodeDmdsBinaryBuffer(await r.arrayBuffer());
    if (!decoded || !decoded.frames.length) return;
    batchSimState.frames = decoded.frames;
    batchSimState.w = decoded.w;
    batchSimState.h = decoded.h;
    batchSimState.fps = decoded.fps;
    batchSimState.frameDurMs = decoded.frameDurMs;
    batchSimState.frameIdx = 0;
    const metaEl = document.getElementById('batch-sim-meta');
    if (metaEl) {
      metaEl.textContent = `${decoded.w}×${decoded.h} • ${lang.toUpperCase()} • ${decoded.frames.length}f @ ${decoded.fps} FPS`;
    }
    redrawBatchCardPreviewFrame();
    ensureBatchSimLoop();
  } catch (_) {}
}

export function redrawBatchCardPreviewFrame() {
  const canvas = document.getElementById('batch-card-preview-canvas');
  if (!canvas || !batchSimState.frames.length) return;
  const rgba = batchSimState.frames[batchSimState.frameIdx];
  if (!rgba) return;

  const w = batchSimState.w;
  const h = batchSimState.h;
  const scale = Math.max(2, Math.min(6, Math.floor(Math.min(420 / Math.max(1, w), 220 / Math.max(1, h)))));
  const cw = w * scale;
  const ch = h * scale;
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }
  const ctx = canvas.getContext('2d');
  const ledMode = document.getElementById('batch-led-mode')?.value || 'smd_round';

  if (ledMode === 'raw') {
    if (!batchSimState.offscreen) batchSimState.offscreen = document.createElement('canvas');
    batchSimState.offscreen.width = w;
    batchSimState.offscreen.height = h;
    batchSimState.offscreen.getContext('2d').putImageData(new ImageData(rgba, w, h), 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(batchSimState.offscreen, 0, 0, cw, ch);
    return;
  }

  ctx.fillStyle = '#05070a';
  ctx.fillRect(0, 0, cw, ch);
  const isRound = ledMode === 'smd_round';
  const radius = Math.max(0.9, scale * 0.39);
  const cell = Math.max(1, scale - 1);

  for (let y = 0; y < h; y++) {
    const py = y * scale;
    for (let x = 0; x < w; x++) {
      const o = (y * w + x) * 4;
      const r = rgba[o], g = rgba[o + 1], b = rgba[o + 2];
      if (r < 6 && g < 6 && b < 6) {
        ctx.fillStyle = '#0c1017';
      } else {
        ctx.fillStyle = `rgb(${r},${g},${b})`;
      }
      if (isRound) {
        ctx.beginPath();
        ctx.arc(x * scale + scale * 0.5, py + scale * 0.5, radius, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.fillRect(x * scale, py, cell, cell);
      }
    }
  }
}

function ensureBatchSimLoop() {
  if (batchSimState.animTimer) clearInterval(batchSimState.animTimer);
  batchSimState.animTimer = setInterval(() => {
    const tabPane = document.getElementById('tab-batch');
    if (!tabPane || !tabPane.classList.contains('active') || !batchSimState.frames.length) return;
    batchSimState.frameIdx = (batchSimState.frameIdx + 1) % batchSimState.frames.length;
    redrawBatchCardPreviewFrame();
  }, Math.max(33, batchSimState.frameDurMs || 66));
}
