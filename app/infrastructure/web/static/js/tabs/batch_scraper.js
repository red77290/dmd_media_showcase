/**
 * Tab #7 Controller: Batch Scraper & Offline Packs (`static/js/tabs/batch_scraper.js`).
 *
 * Supports:
 * - 3-Click Simple Mode + Full À-La-Carte Advanced Accordion
 * - Mode A: Random Filter Batch (`<system>_<genre>/*.gif` + `index.txt` + `playlists.json`)
 * - Mode B: Recalbox / Batocera `gamelist.xml` Offline Showcase (`showcase[_tate]/<system>/<rom>.gif`)
 * - Universal Direct-to-Folder Streaming via Dedicated Web Worker (`batch_worker.js`) + Keep-Alive
 *   (with automatic native `stream.zip` fallback when `showDirectoryPicker` is unavailable).
 */

import { apiFetch, clampFloatInput, clampIntInput, esc, getActiveApiKey } from '../api.js';
import { getSelectedBatchFilters, setBatchFilterCatalog } from '../batch_bubbles.js';
import {
  acquireBatchBackgroundKeepAlive,
  releaseBatchBackgroundKeepAlive,
  updateBatchDocumentTitleProgress
} from '../batch_keepalive.js';
import { getCurrentUiLang, t } from '../i18n/index.js';
import { ensureDomainFilterCatalog } from '../state.js';

const batchRuntime = {
  initialized: false,
  mode: 'random_filter',
  preset: 'full_showcase',
  cardLayout: 'classic_split',
  dirHandle: null,
  dirName: '',
  existingRelpaths: [],
  gamelistFiles: [],
  gamelistPreview: null,
  worker: null,
  activeJobId: '',
  lastSnapshot: null,
  writtenOnDisk: 0,
  zipFallbackPollTimer: null
};

const RESOLUTION_PRESETS = {
  '128x32': [128, 32],
  '128x64': [128, 64],
  '256x64': [256, 64],
  '64x32': [64, 32],
  '64x64': [64, 64],
  '64x128': [64, 128],
  '32x128': [32, 128]
};

function ensureBatchWorker() {
  if (batchRuntime.worker || typeof Worker === 'undefined') return batchRuntime.worker;
  const w = new Worker('/static/js/batch_worker.js', { type: 'module' });
  w.addEventListener('message', evt => handleBatchWorkerMessage(evt.data || {}));
  batchRuntime.worker = w;
  return w;
}

export function supportsDirectFolderStream() {
  return typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function';
}

export function isMobileOrTabletBrowser() {
  if (typeof navigator === 'undefined') return false;
  const ua = String(navigator.userAgent || '');
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return true;
  // iPadOS 13+ desktop-class UA reports MacIntel with multi-touch points
  return navigator.platform === 'MacIntel' && Number(navigator.maxTouchPoints || 0) > 1;
}

export async function initBatchScraperIfNeeded() {
  if (!batchRuntime.initialized) {
    batchRuntime.initialized = true;
    updateDestinationCapabilitiesUi();
    await loadBatchFilterOptions();
    syncBatchPresetUi(batchRuntime.preset);
  }
}

function updateDestinationCapabilitiesUi() {
  const hintEl = document.getElementById('batch-dest-mode-hint');
  const pickBtn = document.getElementById('btn-batch-pick-folder');
  if (!hintEl) return;
  if (supportsDirectFolderStream()) {
    if (pickBtn) pickBtn.style.display = '';
    hintEl.textContent = batchRuntime.dirHandle
      ? t('batch_dest_selected_fmt', {
          name: batchRuntime.dirName,
          n: batchRuntime.existingRelpaths.length
        })
      : t('batch_dest_direct_ready');
  } else {
    if (pickBtn) pickBtn.style.display = 'none';
    hintEl.textContent = isMobileOrTabletBrowser()
      ? t('batch_dest_mobile_zip')
      : t('batch_dest_zip_fallback');
  }
}

export async function selectBatchOutputFolder() {
  if (!supportsDirectFolderStream()) return;
  try {
    const handle = await window.showDirectoryPicker({ mode: 'readwrite' });
    batchRuntime.dirHandle = handle;
    batchRuntime.dirName = handle.name || 'Selected Folder';
    batchRuntime.existingRelpaths = [];
    setBatchStatusMsg(t('batch_scanning_folder'), false);
    const worker = ensureBatchWorker();
    if (worker) {
      worker.postMessage({ type: 'SCAN_DIR', dirHandle: handle });
    } else {
      updateDestinationCapabilitiesUi();
    }
  } catch (err) {
    if (err && err.name !== 'AbortError') {
      setBatchStatusMsg(String(err.message || err), true);
    }
  }
}

export function setBatchMode(mode) {
  batchRuntime.mode = mode === 'recalbox_gamelist' ? 'recalbox_gamelist' : 'random_filter';
  const randCard = document.getElementById('batch-mode-card-random');
  const glCard = document.getElementById('batch-mode-card-gamelist');
  const randPane = document.getElementById('batch-random-filters-box');
  const glPane = document.getElementById('batch-gamelist-box');
  if (randCard) randCard.classList.toggle('active', batchRuntime.mode === 'random_filter');
  if (glCard) glCard.classList.toggle('active', batchRuntime.mode === 'recalbox_gamelist');
  if (randPane) randPane.style.display = batchRuntime.mode === 'random_filter' ? '' : 'none';
  if (glPane) glPane.style.display = batchRuntime.mode === 'recalbox_gamelist' ? '' : 'none';
}

export async function loadBatchFilterOptions() {
  const domEl = document.getElementById('batch-domain');
  const langEl = document.getElementById('batch-lang');
  const dom = domEl ? domEl.value : 'games';
  const lang = langEl ? langEl.value : getCurrentUiLang();
  const cat = await ensureDomainFilterCatalog(dom, lang);
  setBatchFilterCatalog(dom, cat);
}

export function selectBatchCardLayout(layoutId) {
  batchRuntime.cardLayout = layoutId || 'classic_split';
  document.querySelectorAll('#batch-layout-grid .layout-card-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.layout === batchRuntime.cardLayout);
  });
}

function syncBatchCardLayoutBoxVisibility() {
  const cardBox = document.getElementById('batch-card-layout-box');
  if (!cardBox) return;
  const hasCard = Boolean(document.getElementById('batch-chk-card')?.checked);
  cardBox.style.display = hasCard ? '' : 'none';
}

export function selectBatchPreset(presetId) {
  batchRuntime.preset = presetId || 'full_showcase';
  syncBatchPresetUi(batchRuntime.preset);
}

function syncBatchPresetUi(presetId) {
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

export function onBatchChapterToggleManual() {
  batchRuntime.preset = 'custom';
  document.querySelectorAll('.batch-preset-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.preset === 'custom');
  });
  syncBatchCardLayoutBoxVisibility();
}

export function onBatchResolutionPresetChange() {
  const sel = document.getElementById('batch-res-preset');
  const customWrap = document.getElementById('batch-custom-res-wrap');
  const val = sel ? sel.value : '128x32';
  if (val === 'custom') {
    if (customWrap) customWrap.style.display = 'flex';
    return;
  }
  if (customWrap) customWrap.style.display = 'none';
  const dims = RESOLUTION_PRESETS[val] || [128, 32];
  const wEl = document.getElementById('batch-width');
  const hEl = document.getElementById('batch-height');
  if (wEl) wEl.value = String(dims[0]);
  if (hEl) hEl.value = String(dims[1]);
}

export function onBatchDurationChange() {
  const val = clampFloatInput('batch-video-dur', 2.0, 120.0, 8.0);
  const lbl = document.getElementById('batch-video-dur-lbl');
  if (lbl) lbl.textContent = `${val}s`;
}

export function toggleBatchAdvanced() {
  const box = document.getElementById('batch-advanced-body');
  const chevron = document.getElementById('batch-adv-chevron');
  if (!box) return;
  const open = box.style.display === 'none';
  box.style.display = open ? '' : 'none';
  if (chevron) chevron.textContent = open ? '▾' : '▸';
}

export async function onBatchGamelistFilesSelected(inputEl) {
  const fileList = inputEl && inputEl.files ? Array.from(inputEl.files) : [];
  await ingestGamelistFileObjects(fileList);
}

async function ingestGamelistFileObjects(files) {
  const xmlFiles = files.filter(f => f && f.name && f.name.toLowerCase().endsWith('.xml'));
  if (!xmlFiles.length) {
    setBatchStatusMsg(t('batch_err_no_xml'), true);
    return;
  }
  const payloads = [];
  for (const f of xmlFiles.slice(0, 128)) {
    const text = await f.text();
    const rel = f.webkitRelativePath || f.name;
    payloads.push({ relpath: rel, system: '', xml_content: text });
  }
  const sysHintEl = document.getElementById('batch-gamelist-sys-hint');
  const hint = sysHintEl ? sysHintEl.value.trim() : '';
  if (hint && payloads.length === 1) payloads[0].system = hint;

  batchRuntime.gamelistFiles = payloads;
  try {
    const r = await apiFetch('/api/v1/batch-scraper/gamelist/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        files: payloads,
        skip_hidden: true,
        skip_bios: true
      })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    batchRuntime.gamelistPreview = d;
    const summaryEl = document.getElementById('batch-gamelist-summary');
    if (summaryEl) {
      summaryEl.innerHTML = `<span class="pill ok">✓ ${Number(d.total_games || 0)} ${esc(t('batch_gl_games_detected'))}</span> ` +
        (d.systems || []).map(s => `<span class="badge">${esc(s.toUpperCase())}</span>`).join(' ');
    }
  } catch (err) {
    setBatchStatusMsg(String(err.message || err), true);
  }
}

function collectBatchJobRequest() {
  const w = clampIntInput('batch-width', 16, 512, 128);
  const h = clampIntInput('batch-height', 16, 512, 32);
  const fps = clampIntInput('batch-fps', 6, 30, 15);
  const dur = clampFloatInput('batch-video-dur', 2.0, 120.0, 8.0);
  const lmh = Boolean(document.getElementById('batch-let-me-handle')?.checked);
  const dom = document.getElementById('batch-domain')?.value || 'games';
  const lang = document.getElementById('batch-lang')?.value || getCurrentUiLang();

  const { systems, genres, decades } = getSelectedBatchFilters();

  const useDirectFolder = Boolean(supportsDirectFolderStream() && batchRuntime.dirHandle);
  return {
    mode: batchRuntime.mode,
    delivery_mode: useDirectFolder ? 'direct_folder' : 'zip_stream',
    domain: dom,
    lang,
    systems,
    genres,
    decades,
    target_count: clampIntInput('batch-target-count', 1, 10000, 100),
    max_workers: clampIntInput('batch-workers', 0, 8, 0),
    ffmpeg_threads_per_worker: clampIntInput('batch-ffmpeg-threads', 1, 4, 1),
    folder_template: document.getElementById('batch-folder-tpl')?.value || 'system_genre',
    safe_ascii_folders: Boolean(document.getElementById('batch-safe-ascii')?.checked),
    wrap_orientation_root: Boolean(document.getElementById('batch-wrap-orient')?.checked),
    include_marquee_png_companion: Boolean(document.getElementById('batch-marquee-png')?.checked),
    preset: batchRuntime.preset,
    chapters: {
      include_card: Boolean(document.getElementById('batch-chk-card')?.checked),
      include_poster_in_card: Boolean(document.getElementById('batch-chk-poster')?.checked),
      include_description: Boolean(document.getElementById('batch-chk-desc')?.checked),
      card_layout: batchRuntime.cardLayout || 'classic_split',
      card_theme: document.getElementById('batch-card-theme')?.value || 'tinted',
      include_jacket: Boolean(document.getElementById('batch-chk-jacket')?.checked),
      jacket_mode: document.getElementById('batch-jacket-mode')?.value || 'panorama',
      include_marquee: Boolean(document.getElementById('batch-chk-marquee')?.checked),
      snapshot_count: clampIntInput('batch-snap-count', 0, 4, 1),
      panorama_duration_s: clampFloatInput('batch-pan-dur', 1.5, 15.0, 4.0),
      include_video: Boolean(document.getElementById('batch-chk-video')?.checked)
    },
    encoding: {
      width: w,
      height: h,
      fps,
      video_max_duration_s: dur,
      let_me_handle_it: lmh,
      video_mode: document.getElementById('batch-video-mode')?.value || 'auto_action',
      content_mode: document.getElementById('batch-content-mode')?.value || 'auto',
      dither: document.getElementById('batch-dither')?.value || 'none',
      palette_mode: document.getElementById('batch-palette-mode')?.value || 'per_scene_rgb888',
      reject_threshold: clampIntInput('batch-reject-score', 0, 95, 0)
    },
    existing_relpaths: batchRuntime.existingRelpaths || [],
    gamelist_files: batchRuntime.mode === 'recalbox_gamelist' ? batchRuntime.gamelistFiles : []
  };
}

export async function startBatchScrapeJob() {
  if (supportsDirectFolderStream() && !batchRuntime.dirHandle) {
    await selectBatchOutputFolder();
    if (!batchRuntime.dirHandle) return;
  }
  if (batchRuntime.mode === 'recalbox_gamelist' && !batchRuntime.gamelistFiles.length) {
    setBatchStatusMsg(t('batch_err_need_gamelist'), true);
    return;
  }

  // Acquire WakeLock + Web Lock + Silent Web Audio Keep-Alive synchronously inside user click gesture!
  acquireBatchBackgroundKeepAlive();

  const payload = collectBatchJobRequest();
  setBatchStatusMsg(t('batch_starting'), false);

  try {
    const r = await apiFetch('/api/v1/batch-scraper/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);

    const snap = d.job;
    batchRuntime.activeJobId = snap.job_id;
    batchRuntime.writtenOnDisk = 0;
    renderBatchProgressUi(snap);

    const isTate = payload.encoding.height > payload.encoding.width;
    const isClockMode = payload.mode === 'random_filter';

    if (supportsDirectFolderStream() && batchRuntime.dirHandle) {
      const worker = ensureBatchWorker();
      worker.postMessage({
        type: 'START_STREAM',
        jobId: snap.job_id,
        baseUrl: window.location.origin,
        apiKey: getActiveApiKey(),
        dirHandle: batchRuntime.dirHandle,
        plannedDirs: snap.planned_directories || [],
        isClockMode,
        isTate,
        wrapOrientationRoot: payload.wrap_orientation_root
      });
    } else {
      // On Mobile (iOS Safari / Android), avoid synthetic post-await a.click() which WebKit blocks
      // or severs on app-switch; user taps the native #btn-batch-download-zip button anytime or at 100%!
      if (!isMobileOrTabletBrowser()) {
        triggerStreamingZipDownload(snap.job_id);
      }
      startFallbackProgressMonitor(snap.job_id);
    }
  } catch (err) {
    releaseBatchBackgroundKeepAlive();
    setBatchStatusMsg(String(err.message || err), true);
  }
}

function buildZipDownloadHref(jobId) {
  const key = getActiveApiKey();
  const q = key ? `?api_key=${encodeURIComponent(key)}` : '';
  return `/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}/stream.zip${q}`;
}

function triggerStreamingZipDownload(jobId) {
  const a = document.createElement('a');
  a.href = buildZipDownloadHref(jobId);
  a.download = `dmd_pack_${jobId}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function startFallbackProgressMonitor(jobId) {
  if (batchRuntime.zipFallbackPollTimer) clearInterval(batchRuntime.zipFallbackPollTimer);
  batchRuntime.zipFallbackPollTimer = setInterval(async () => {
    try {
      const r = await apiFetch(`/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}`);
      if (!r.ok) return;
      const snap = await r.json();
      batchRuntime.writtenOnDisk = Number(snap.acked_count || 0);
      renderBatchProgressUi(snap);
      if (['completed', 'cancelled', 'failed'].includes(snap.status)) {
        clearInterval(batchRuntime.zipFallbackPollTimer);
        batchRuntime.zipFallbackPollTimer = null;
        releaseBatchBackgroundKeepAlive();
        if (snap.status === 'completed' && isMobileOrTabletBrowser()) {
          setBatchStatusMsg(t('batch_mobile_ready_toast'), false);
        }
      }
    } catch (_) {}
  }, 1500);
}

export async function controlActiveBatchJob(action) {
  if (!batchRuntime.activeJobId) return;
  if (action === 'cancel' && batchRuntime.worker) {
    batchRuntime.worker.postMessage({ type: 'STOP_STREAM' });
  }
  try {
    const r = await apiFetch(`/api/v1/batch-scraper/jobs/${encodeURIComponent(batchRuntime.activeJobId)}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    });
    if (r.ok) {
      const d = await r.json();
      renderBatchProgressUi(d.job);
      if (action === 'pause' || action === 'cancel') releaseBatchBackgroundKeepAlive();
      if (action === 'resume') acquireBatchBackgroundKeepAlive();
    }
  } catch (err) {
    setBatchStatusMsg(String(err.message || err), true);
  }
}

export async function onBatchWorkersLiveChange() {
  const val = clampIntInput('batch-workers', 0, 8, 0);
  const lbl = document.getElementById('batch-workers-lbl');
  if (lbl) lbl.textContent = val === 0 ? t('batch_workers_auto') : String(val);
  if (!batchRuntime.activeJobId || val <= 0) return;
  try {
    const r = await apiFetch(`/api/v1/batch-scraper/jobs/${encodeURIComponent(batchRuntime.activeJobId)}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'set_workers', max_workers: val })
    });
    if (r.ok) {
      const d = await r.json();
      renderBatchProgressUi(d.job);
    }
  } catch (_) {}
}

function handleBatchWorkerMessage(msg) {
  if (msg.type === 'SCAN_RESULT') {
    batchRuntime.existingRelpaths = Array.isArray(msg.existingRelpaths) ? msg.existingRelpaths : [];
    updateDestinationCapabilitiesUi();
    setBatchStatusMsg(
      t('batch_dest_selected_fmt', { name: batchRuntime.dirName, n: batchRuntime.existingRelpaths.length }),
      false
    );
    return;
  }
  if (msg.type === 'ITEM_WRITTEN') {
    batchRuntime.writtenOnDisk = Number(msg.writtenTotal || 0);
    return;
  }
  if (msg.type === 'SNAPSHOT' && msg.snapshot) {
    batchRuntime.writtenOnDisk = Number(msg.writtenTotal || msg.snapshot.acked_count || 0);
    renderBatchProgressUi(msg.snapshot);
    return;
  }
  if (msg.type === 'STREAM_DONE') {
    releaseBatchBackgroundKeepAlive();
    if (batchRuntime.lastSnapshot) {
      renderBatchProgressUi(batchRuntime.lastSnapshot);
    }
  }
}

function formatEta(etaSec) {
  if (etaSec == null) return '—';
  const s = Math.max(0, Math.round(Number(etaSec)));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m ${rem}s`;
}

function renderBatchProgressUi(snap) {
  if (!snap) return;
  batchRuntime.lastSnapshot = snap;
  const pct = Number(snap.progress_pct || 0);
  const isRunning = snap.status === 'running';
  const isPaused = snap.status === 'paused';
  const doneCount = Math.max(
    batchRuntime.writtenOnDisk,
    Number(snap.succeeded_count || 0) + Number(snap.skipped_existing_count || 0)
  );
  updateBatchDocumentTitleProgress(pct, isRunning || isPaused);

  const card = document.getElementById('batch-progress-card');
  if (card) card.style.display = '';

  const zipBtn = document.getElementById('btn-batch-download-zip');
  if (zipBtn) {
    const showZip = !(supportsDirectFolderStream() && batchRuntime.dirHandle) && Boolean(snap.job_id);
    zipBtn.style.display = showZip ? 'inline-flex' : 'none';
    if (showZip) {
      zipBtn.href = buildZipDownloadHref(snap.job_id);
      zipBtn.download = `dmd_pack_${snap.job_id}.zip`;
      const isReady = snap.status === 'completed' && Number(snap.unacked_count || 0) > 0;
      zipBtn.classList.toggle('ready', isReady);
      zipBtn.textContent = isReady ? t('btn_batch_dl_zip_ready') : t('btn_batch_dl_zip');
    }
  }

  const fill = document.getElementById('batch-progress-fill');
  if (fill) fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;

  const statsEl = document.getElementById('batch-progress-stats');
  if (statsEl) {
    statsEl.textContent = `${doneCount} / ${snap.total_target} (${pct.toFixed(1)}%) • ${snap.items_per_min || 0}/min • ETA ${formatEta(snap.eta_s)}`;
  }

  const pillsEl = document.getElementById('batch-progress-pills');
  if (pillsEl) {
    const spoolMb = ((Number(snap.unacked_bytes || 0)) / (1024 * 1024)).toFixed(1);
    pillsEl.innerHTML =
      `<span class="pill ${isRunning ? 'ok' : (isPaused ? 'warn' : '')}">${esc(snap.status.toUpperCase())}</span> ` +
      `<span class="badge">✓ ${Number(snap.succeeded_count || 0)}</span> ` +
      `<span class="badge">⏭ ${Number(snap.skipped_existing_count || 0)}</span> ` +
      `<span class="badge">✕ ${Number(snap.failed_count || 0)}</span> ` +
      `<span class="badge">⚙ ${Number(snap.active_workers || 0)}/${Number(snap.max_workers || 1)} workers</span> ` +
      `<span class="badge">Spool: ${spoolMb} MB</span>`;
  }

  const pauseBtn = document.getElementById('btn-batch-pause');
  const resumeBtn = document.getElementById('btn-batch-resume');
  if (pauseBtn) pauseBtn.style.display = isRunning ? '' : 'none';
  if (resumeBtn) resumeBtn.style.display = isPaused ? '' : 'none';

  const recentTbody = document.getElementById('batch-recent-tbody');
  if (recentTbody && Array.isArray(snap.recent_items)) {
    recentTbody.innerHTML = snap.recent_items.length
      ? snap.recent_items.map(it => (
          `<tr><td><code>${esc(it.rel_dir)}</code></td><td>${esc(it.filename)}</td>` +
          `<td>${esc(it.duration_s)}s</td><td>${(Number(it.size_bytes || 0) / 1024).toFixed(0)} KB</td></tr>`
        )).join('')
      : `<tr><td colspan="4" style="color:var(--muted)">—</td></tr>`;
  }

  // Persistent Cross-Tab Header Mini-Banner so user sees live progress from ANY tab
  const banner = document.getElementById('batch-global-banner');
  const bannerTxt = document.getElementById('batch-global-banner-text');
  const bannerFill = document.getElementById('batch-global-banner-fill');
  if (banner) {
    banner.style.display = (isRunning || isPaused) ? 'flex' : 'none';
    if (bannerTxt) {
      bannerTxt.textContent = `⚡ Batch: ${doneCount}/${snap.total_target} (${pct.toFixed(0)}%) • ETA ${formatEta(snap.eta_s)}`;
    }
    if (bannerFill) bannerFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }
}

function setBatchStatusMsg(msg, isError) {
  const el = document.getElementById('batch-status-msg');
  if (!el) return;
  el.style.color = isError ? 'var(--red)' : 'var(--green)';
  el.textContent = msg || '';
}
