/**
 * Tab #7 Controller: Batch Scraper & Persistent Offline Packs (`static/js/tabs/batch_scraper.js`).
 *
 * Supports:
 * - 3-Click Simple Mode (1-click bubbles + Studio visual wireframes) + Full À-La-Carte Accordion
 * - Mode A: Random Filter Batch (`<system>_<genre>/*.gif` + `index.txt` + `playlists.json`)
 * - Mode B: Recalbox / Batocera `gamelist.xml` Offline Showcase (`showcase[_tate]/<system>/<rom>.gif`)
 * - Persistent server-side jobs surviving window/tab closure, F5 reloads, LAN device switches,
 *   and server restarts (`batch_session_recovery.js` + `IndexedDB` handle re-attachment).
 */

import { apiFetch, clampFloatInput, clampIntInput, esc, getActiveApiKey } from '../api.js';
import { getSelectedBatchFilters, setBatchFilterCatalog } from '../batch_bubbles.js';
import {
  acquireBatchBackgroundKeepAlive,
  releaseBatchBackgroundKeepAlive
} from '../batch_keepalive.js';
import {
  buildBatchJobRequestPayload,
  buildZipDownloadHref,
  fetchRecoverableBatchJobFromServer,
  hideBatchProgressCardAndBanner,
  loadBatchFolderHandle,
  markJobLocallyDismissed,
  persistBatchFolderHandle,
  queryDirectoryReadWritePermission,
  removeBatchFolderHandle,
  renderBatchProgressCardAndBanner,
  requestDirectoryReadWritePermission,
  startStatusOnlySseObserver
} from '../batch_session_recovery.js';
import { getCurrentUiLang, t } from '../i18n/index.js';
import { ensureDomainFilterCatalog } from '../state.js';

const batchRuntime = {
  initialized: false,
  recoveryBooted: false,
  mode: 'random_filter',
  preset: 'full_showcase',
  cardLayout: 'classic_split',
  dirHandle: null,
  dirName: '',
  pendingSavedHandle: null,
  pendingSavedFolderName: '',
  existingRelpaths: [],
  gamelistFiles: [],
  gamelistPreview: null,
  worker: null,
  isWorkerStreaming: false,
  statusObserver: null,
  activeJobId: '',
  lastSnapshot: null,
  writtenOnDisk: 0
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

function stopStatusObserver() {
  if (batchRuntime.statusObserver) {
    batchRuntime.statusObserver.stop();
    batchRuntime.statusObserver = null;
  }
}

function renderCurrentProgress(snap) {
  if (!snap) return;
  batchRuntime.lastSnapshot = snap;
  renderBatchProgressCardAndBanner(snap, {
    writtenOnDisk: batchRuntime.writtenOnDisk,
    isWorkerStreaming: batchRuntime.isWorkerStreaming,
    pendingSavedFolderName: batchRuntime.pendingSavedFolderName,
    supportsDirectFolder: supportsDirectFolderStream()
  });
}

function ensureStatusObserverForJob(jobId) {
  stopStatusObserver();
  if (!jobId) return;
  batchRuntime.statusObserver = startStatusOnlySseObserver(
    jobId,
    snap => {
      batchRuntime.writtenOnDisk = Math.max(batchRuntime.writtenOnDisk, Number(snap.acked_count || 0));
      renderCurrentProgress(snap);
      if (['completed', 'cancelled', 'failed'].includes(snap.status)) {
        releaseBatchBackgroundKeepAlive();
        if (snap.status === 'completed' && isMobileOrTabletBrowser()) {
          setBatchStatusMsg(t('batch_mobile_ready_toast'), false);
        }
      }
    },
    () => {
      batchRuntime.statusObserver = null;
    }
  );
}

function startDirectWorkerStream(snap, dirHandle) {
  if (!snap || !dirHandle) return;
  stopStatusObserver();
  const summary = snap.config_summary || {};
  const isTate = summary.is_tate != null
    ? Boolean(summary.is_tate)
    : (clampIntInput('batch-height', 16, 512, 32) > clampIntInput('batch-width', 16, 512, 128));
  const isClockMode = (summary.mode || batchRuntime.mode) !== 'recalbox_gamelist';
  const wrapOrientationRoot = summary.wrap_orientation_root != null
    ? Boolean(summary.wrap_orientation_root)
    : Boolean(document.getElementById('batch-wrap-orient')?.checked);

  batchRuntime.dirHandle = dirHandle;
  batchRuntime.dirName = dirHandle.name || batchRuntime.dirName || 'Selected Folder';
  batchRuntime.pendingSavedHandle = null;
  batchRuntime.pendingSavedFolderName = '';
  batchRuntime.isWorkerStreaming = true;
  renderCurrentProgress(snap);

  const worker = ensureBatchWorker();
  if (!worker) return;
  worker.postMessage({
    type: 'START_STREAM',
    jobId: snap.job_id,
    baseUrl: window.location.origin,
    apiKey: getActiveApiKey(),
    dirHandle,
    plannedDirs: snap.planned_directories || [],
    isClockMode,
    isTate,
    wrapOrientationRoot,
    alreadyAckedCount: Number(snap.acked_count || 0)
  });
}

export function supportsDirectFolderStream() {
  return typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function';
}

export function isMobileOrTabletBrowser() {
  if (typeof navigator === 'undefined') return false;
  const ua = String(navigator.userAgent || '');
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return true;
  return navigator.platform === 'MacIntel' && Number(navigator.maxTouchPoints || 0) > 1;
}

export async function initBatchScraperIfNeeded() {
  if (!batchRuntime.initialized) {
    batchRuntime.initialized = true;
    updateDestinationCapabilitiesUi();
    await loadBatchFilterOptions();
    syncBatchPresetUi(batchRuntime.preset);
  }
  await reattachActiveBatchJobOnLoad();
}

export async function reattachActiveBatchJobOnLoad() {
  if (batchRuntime.activeJobId && (batchRuntime.isWorkerStreaming || batchRuntime.statusObserver)) {
    if (batchRuntime.lastSnapshot) renderCurrentProgress(batchRuntime.lastSnapshot);
    return;
  }
  const snap = await fetchRecoverableBatchJobFromServer();
  if (!snap || !snap.job_id) return;

  batchRuntime.activeJobId = snap.job_id;
  batchRuntime.writtenOnDisk = Number(snap.acked_count || 0);
  const isActive = ['running', 'paused', 'queued'].includes(snap.status);
  const hasUnacked = Number(snap.unacked_count || 0) > 0;
  const wasDirect = snap.config_summary && snap.config_summary.delivery_mode === 'direct_folder';

  if (supportsDirectFolderStream() && wasDirect && (isActive || hasUnacked)) {
    const savedRec = await loadBatchFolderHandle(snap.job_id);
    if (savedRec && savedRec.handle) {
      const perm = await queryDirectoryReadWritePermission(savedRec.handle);
      if (perm === 'granted') {
        setBatchStatusMsg(t('batch_reattach_streaming_ok', { name: savedRec.name }), false);
        startDirectWorkerStream(snap, savedRec.handle);
        return;
      }
      batchRuntime.pendingSavedHandle = savedRec.handle;
      batchRuntime.pendingSavedFolderName = savedRec.name || 'Folder';
    }
  }

  renderCurrentProgress(snap);
  if (isActive) {
    ensureStatusObserverForJob(snap.job_id);
  }
}

export async function reconnectSavedBatchFolder() {
  const handle = batchRuntime.pendingSavedHandle || batchRuntime.dirHandle;
  if (!handle || !batchRuntime.activeJobId) {
    await attachNewFolderToActiveBatchJob();
    return;
  }
  acquireBatchBackgroundKeepAlive();
  const perm = await requestDirectoryReadWritePermission(handle);
  if (perm !== 'granted') {
    releaseBatchBackgroundKeepAlive();
    return;
  }
  await persistBatchFolderHandle(batchRuntime.activeJobId, handle);
  const freshRes = await apiFetch(`/api/v1/batch-scraper/jobs/${encodeURIComponent(batchRuntime.activeJobId)}`);
  const snap = freshRes.ok ? await freshRes.json() : batchRuntime.lastSnapshot;
  if (snap) {
    setBatchStatusMsg(t('batch_reattach_streaming_ok', { name: handle.name || 'Folder' }), false);
    startDirectWorkerStream(snap, handle);
  }
}

export async function attachNewFolderToActiveBatchJob() {
  if (!supportsDirectFolderStream() || !batchRuntime.activeJobId) return;
  try {
    const handle = await window.showDirectoryPicker({ mode: 'readwrite' });
    acquireBatchBackgroundKeepAlive();
    await persistBatchFolderHandle(batchRuntime.activeJobId, handle);
    const freshRes = await apiFetch(`/api/v1/batch-scraper/jobs/${encodeURIComponent(batchRuntime.activeJobId)}`);
    const snap = freshRes.ok ? await freshRes.json() : batchRuntime.lastSnapshot;
    if (snap) {
      setBatchStatusMsg(t('batch_reattach_streaming_ok', { name: handle.name || 'Folder' }), false);
      startDirectWorkerStream(snap, handle);
    }
  } catch (err) {
    if (err && err.name !== 'AbortError') {
      setBatchStatusMsg(String(err.message || err), true);
    }
  }
}

export async function dismissActiveBatchJob() {
  const jobId = batchRuntime.activeJobId;
  stopStatusObserver();
  if (batchRuntime.worker && batchRuntime.isWorkerStreaming) {
    batchRuntime.worker.postMessage({ type: 'STOP_STREAM' });
  }
  batchRuntime.isWorkerStreaming = false;
  releaseBatchBackgroundKeepAlive();
  if (jobId) {
    markJobLocallyDismissed(jobId);
    await removeBatchFolderHandle(jobId);
    try {
      await apiFetch(`/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
    } catch (_) {}
  }
  batchRuntime.activeJobId = '';
  batchRuntime.lastSnapshot = null;
  batchRuntime.pendingSavedHandle = null;
  batchRuntime.pendingSavedFolderName = '';
  hideBatchProgressCardAndBanner();
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
    await persistBatchFolderHandle(batchRuntime.activeJobId, handle);
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
      body: JSON.stringify({ files: payloads, skip_hidden: true, skip_bios: true })
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

export async function startBatchScrapeJob() {
  if (supportsDirectFolderStream() && !batchRuntime.dirHandle) {
    await selectBatchOutputFolder();
    if (!batchRuntime.dirHandle) return;
  }
  if (batchRuntime.mode === 'recalbox_gamelist' && !batchRuntime.gamelistFiles.length) {
    setBatchStatusMsg(t('batch_err_need_gamelist'), true);
    return;
  }

  acquireBatchBackgroundKeepAlive();
  const payload = buildBatchJobRequestPayload(
    batchRuntime,
    Boolean(supportsDirectFolderStream() && batchRuntime.dirHandle)
  );
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

    if (supportsDirectFolderStream() && batchRuntime.dirHandle) {
      await persistBatchFolderHandle(snap.job_id, batchRuntime.dirHandle);
      startDirectWorkerStream(snap, batchRuntime.dirHandle);
    } else {
      batchRuntime.isWorkerStreaming = false;
      renderCurrentProgress(snap);
      if (!isMobileOrTabletBrowser()) {
        const a = document.createElement('a');
        a.href = buildZipDownloadHref(snap.job_id);
        a.download = `dmd_pack_${snap.job_id}.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
      ensureStatusObserverForJob(snap.job_id);
    }
  } catch (err) {
    releaseBatchBackgroundKeepAlive();
    setBatchStatusMsg(String(err.message || err), true);
  }
}

export async function controlActiveBatchJob(action) {
  if (!batchRuntime.activeJobId) return;
  if (action === 'cancel' && batchRuntime.worker) {
    batchRuntime.worker.postMessage({ type: 'STOP_STREAM' });
    batchRuntime.isWorkerStreaming = false;
  }
  try {
    const r = await apiFetch(`/api/v1/batch-scraper/jobs/${encodeURIComponent(batchRuntime.activeJobId)}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    });
    if (r.ok) {
      const d = await r.json();
      renderCurrentProgress(d.job);
      if (action === 'pause' || action === 'cancel') releaseBatchBackgroundKeepAlive();
      if (action === 'resume') {
        acquireBatchBackgroundKeepAlive();
        if (!batchRuntime.isWorkerStreaming) ensureStatusObserverForJob(d.job.job_id);
      }
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
      renderCurrentProgress(d.job);
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
    renderCurrentProgress(msg.snapshot);
    return;
  }
  if (msg.type === 'STREAM_DONE') {
    batchRuntime.isWorkerStreaming = false;
    releaseBatchBackgroundKeepAlive();
    if (batchRuntime.lastSnapshot) {
      renderCurrentProgress(batchRuntime.lastSnapshot);
    }
  }
}

function setBatchStatusMsg(msg, isError) {
  const el = document.getElementById('batch-status-msg');
  if (!el) return;
  el.style.color = isError ? 'var(--red)' : 'var(--green)';
  el.textContent = msg || '';
}
