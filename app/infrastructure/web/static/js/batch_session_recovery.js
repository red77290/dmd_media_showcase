/**
 * Persistent Batch Session Recovery, IndexedDB Handle Store & Progress Renderer
 * (`static/js/batch_session_recovery.js`).
 *
 * Guarantees zero lost visibility or stranded files when a user closes, refreshes, or switches
 * browser windows/devices during or after a Batch Scraper job:
 * 1. Persists `FileSystemDirectoryHandle` objects in `IndexedDB` (`dmd_showcase_batch_fs`) per `job_id`.
 * 2. Discovers active or uncollected server-side Batch jobs on page load (`GET /api/v1/batch-scraper/jobs`).
 * 3. Streams live `status_only=true` SSE snapshots to the UI when no `Web Worker` consumer is active,
 *    and renders `#batch-progress-card`, `#batch-reconnect-bar`, and `#batch-global-banner`.
 */

import { apiFetch, clampFloatInput, clampIntInput, esc, getActiveApiKey } from './api.js';
import { getSelectedBatchFilters } from './batch_bubbles.js';
import { updateBatchDocumentTitleProgress } from './batch_keepalive.js';
import { getCurrentUiLang, t } from './i18n/index.js';

const IDB_NAME = 'dmd_showcase_batch_fs';
const IDB_STORE = 'dir_handles';
const DISMISSED_JOB_STORAGE_KEY = 'dmd_dismissed_batch_job_id';

function openHandleDb() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      resolve(null);
      return;
    }
    try {
      const req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) {
          db.createObjectStore(IDB_STORE);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    } catch (_) {
      resolve(null);
    }
  });
}

export async function persistBatchFolderHandle(jobId, dirHandle) {
  if (!dirHandle) return;
  const db = await openHandleDb();
  if (!db) return;
  try {
    await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, 'readwrite');
      const store = tx.objectStore(IDB_STORE);
      const rec = { handle: dirHandle, name: dirHandle.name || 'Folder', savedAt: Date.now() };
      if (jobId) store.put(rec, `job:${jobId}`);
      store.put(rec, 'last_selected');
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch (_) {
  } finally {
    db.close();
  }
}

export async function loadBatchFolderHandle(jobId) {
  const db = await openHandleDb();
  if (!db) return null;
  try {
    const getKey = key => new Promise(resolve => {
      const tx = db.transaction(IDB_STORE, 'readonly');
      const req = tx.objectStore(IDB_STORE).get(key);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
    });
    if (jobId) {
      const byJob = await getKey(`job:${jobId}`);
      if (byJob && byJob.handle) return byJob;
    }
    const last = await getKey('last_selected');
    return last && last.handle ? last : null;
  } catch (_) {
    return null;
  } finally {
    db.close();
  }
}

export async function removeBatchFolderHandle(jobId) {
  if (!jobId) return;
  const db = await openHandleDb();
  if (!db) return;
  try {
    await new Promise(resolve => {
      const tx = db.transaction(IDB_STORE, 'readwrite');
      tx.objectStore(IDB_STORE).delete(`job:${jobId}`);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } catch (_) {
  } finally {
    db.close();
  }
}

export async function queryDirectoryReadWritePermission(dirHandle) {
  if (!dirHandle || typeof dirHandle.queryPermission !== 'function') return 'denied';
  try {
    return await dirHandle.queryPermission({ mode: 'readwrite' });
  } catch (_) {
    return 'prompt';
  }
}

export async function requestDirectoryReadWritePermission(dirHandle) {
  if (!dirHandle || typeof dirHandle.requestPermission !== 'function') return 'denied';
  try {
    return await dirHandle.requestPermission({ mode: 'readwrite' });
  } catch (_) {
    return 'denied';
  }
}

export function markJobLocallyDismissed(jobId) {
  try {
    if (jobId && typeof localStorage !== 'undefined') {
      localStorage.setItem(DISMISSED_JOB_STORAGE_KEY, String(jobId));
    }
  } catch (_) {}
}

function getLocallyDismissedJobId() {
  try {
    return typeof localStorage !== 'undefined'
      ? (localStorage.getItem(DISMISSED_JOB_STORAGE_KEY) || '')
      : '';
  } catch (_) {
    return '';
  }
}

export function selectRecoverableBatchJob(jobs) {
  if (!Array.isArray(jobs) || !jobs.length) return null;
  const dismissedId = getLocallyDismissedJobId();

  const active = jobs.find(j => j && ['running', 'paused', 'queued'].includes(j.status));
  if (active) return active;

  const uncollected = jobs.find(
    j => j && j.status === 'completed' && Number(j.unacked_count || 0) > 0 && j.job_id !== dismissedId
  );
  if (uncollected) return uncollected;

  const nowSec = Date.now() / 1000;
  return jobs.find(j => {
    if (!j || j.job_id === dismissedId) return false;
    const createdTs = Number(j.created_wall_ts || 0);
    const elapsed = Number(j.elapsed_s || 0);
    const approxFinishedTs = createdTs > 0 ? createdTs + elapsed : 0;
    return approxFinishedTs > 0 && (nowSec - approxFinishedTs) <= 900;
  }) || null;
}

export async function fetchRecoverableBatchJobFromServer() {
  try {
    const r = await apiFetch('/api/v1/batch-scraper/jobs');
    if (!r.ok) return null;
    const data = await r.json();
    return selectRecoverableBatchJob(data && data.jobs ? data.jobs : []);
  } catch (_) {
    return null;
  }
}

export function startStatusOnlySseObserver(jobId, onSnapshot, onEnded) {
  let stopped = false;
  let abortCtrl = null;

  const stop = () => {
    stopped = true;
    if (abortCtrl) {
      try { abortCtrl.abort(); } catch (_) {}
      abortCtrl = null;
    }
  };

  (async () => {
    const headers = {};
    const key = getActiveApiKey();
    if (key) headers['X-Showcase-Key'] = key;

    while (!stopped) {
      abortCtrl = new AbortController();
      let terminalReached = false;
      try {
        const url = `/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}/events?status_only=true`;
        const resp = await fetch(url, { headers, signal: abortCtrl.signal });
        if (resp.status === 404) {
          terminalReached = true;
          break;
        }
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (!stopped) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) !== -1) {
            const rawEvt = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const dataLine = rawEvt.split('\n').find(l => l.startsWith('data:'));
            if (!dataLine) continue;
            const parsed = JSON.parse(dataLine.slice(5).trim());
            if (parsed && parsed.snapshot) {
              onSnapshot(parsed.snapshot);
              if (['completed', 'cancelled', 'failed'].includes(parsed.snapshot.status)) {
                terminalReached = true;
                break;
              }
            }
          }
          if (terminalReached) break;
        }
      } catch (_) {
        if (stopped) break;
        await new Promise(r => setTimeout(r, 1500));
      }
      if (terminalReached || stopped) break;
    }
    if (!stopped && typeof onEnded === 'function') onEnded();
  })();

  return { stop };
}

export function buildZipDownloadHref(jobId) {
  const key = getActiveApiKey();
  const q = key ? `?api_key=${encodeURIComponent(key)}` : '';
  return `/api/v1/batch-scraper/jobs/${encodeURIComponent(jobId)}/stream.zip${q}`;
}

function formatEta(etaSec) {
  if (etaSec == null) return '—';
  const s = Math.max(0, Math.round(Number(etaSec)));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m ${rem}s`;
}

export function hideBatchProgressCardAndBanner() {
  updateBatchDocumentTitleProgress(0, false);
  const card = document.getElementById('batch-progress-card');
  if (card) card.style.display = 'none';
  const banner = document.getElementById('batch-global-banner');
  if (banner) banner.style.display = 'none';
  const recBar = document.getElementById('batch-reconnect-bar');
  if (recBar) recBar.style.display = 'none';
}

export function renderBatchProgressCardAndBanner(snap, opts = {}) {
  if (!snap) return;
  const {
    writtenOnDisk = 0,
    isWorkerStreaming = false,
    pendingSavedFolderName = '',
    supportsDirectFolder = false
  } = opts;

  const pct = Number(snap.progress_pct || 0);
  const isRunning = snap.status === 'running';
  const isPaused = snap.status === 'paused';
  const isTerminal = ['completed', 'cancelled', 'failed'].includes(snap.status);
  const unackedCount = Number(snap.unacked_count || 0);
  const doneCount = Math.max(
    Number(writtenOnDisk || 0),
    Number(snap.succeeded_count || 0) + Number(snap.skipped_existing_count || 0)
  );
  updateBatchDocumentTitleProgress(pct, isRunning || isPaused);

  const card = document.getElementById('batch-progress-card');
  if (card) card.style.display = '';

  const zipBtn = document.getElementById('btn-batch-download-zip');
  if (zipBtn) {
    const showZip = Boolean(snap.job_id) && !isWorkerStreaming && (
      isRunning || isPaused || unackedCount > 0
    );
    zipBtn.style.display = showZip ? 'inline-flex' : 'none';
    if (showZip) {
      zipBtn.href = buildZipDownloadHref(snap.job_id);
      zipBtn.download = `dmd_pack_${snap.job_id}.zip`;
      const isReady = snap.status === 'completed' && unackedCount > 0;
      zipBtn.classList.toggle('ready', isReady);
      zipBtn.textContent = isReady ? t('btn_batch_dl_zip_ready') : t('btn_batch_dl_zip');
    }
  }

  const recBar = document.getElementById('batch-reconnect-bar');
  const recText = document.getElementById('batch-reconnect-text');
  const recBtn = document.getElementById('btn-batch-reconnect-dir');
  const wasDirectFolderJob = (
    (snap.config_summary && snap.config_summary.delivery_mode === 'direct_folder') ||
    Boolean(pendingSavedFolderName)
  );
  if (recBar) {
    const needReconnect = supportsDirectFolder && !isWorkerStreaming && wasDirectFolderJob && (
      isRunning || isPaused || unackedCount > 0
    );
    recBar.style.display = needReconnect ? 'flex' : 'none';
    if (needReconnect && recText) {
      recText.textContent = pendingSavedFolderName
        ? t('batch_reattach_prompt_fmt', { n: unackedCount, name: pendingSavedFolderName })
        : t('batch_reattach_no_handle_fmt', { n: unackedCount });
    }
    if (recBtn) {
      recBtn.style.display = pendingSavedFolderName ? '' : 'none';
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
  const cancelBtn = document.getElementById('btn-batch-cancel');
  const dismissBtn = document.getElementById('btn-batch-dismiss');
  if (pauseBtn) pauseBtn.style.display = isRunning ? '' : 'none';
  if (resumeBtn) resumeBtn.style.display = isPaused ? '' : 'none';
  if (cancelBtn) cancelBtn.style.display = isTerminal ? 'none' : '';
  if (dismissBtn) dismissBtn.style.display = isTerminal ? '' : 'none';

  const recentTbody = document.getElementById('batch-recent-tbody');
  if (recentTbody && Array.isArray(snap.recent_items)) {
    recentTbody.innerHTML = snap.recent_items.length
      ? snap.recent_items.map(it => (
          `<tr><td><code>${esc(it.rel_dir)}</code></td><td>${esc(it.filename)}</td>` +
          `<td>${esc(it.duration_s)}s</td><td>${(Number(it.size_bytes || 0) / 1024).toFixed(0)} KB</td></tr>`
        )).join('')
      : `<tr><td colspan="4" style="color:var(--muted)">—</td></tr>`;
  }

  const banner = document.getElementById('batch-global-banner');
  const bannerTxt = document.getElementById('batch-global-banner-text');
  const bannerFill = document.getElementById('batch-global-banner-fill');
  const showBanner = isRunning || isPaused || (snap.status === 'completed' && unackedCount > 0 && !isWorkerStreaming);
  if (banner) {
    banner.style.display = showBanner ? 'flex' : 'none';
    if (bannerTxt) {
      bannerTxt.textContent = (snap.status === 'completed' && unackedCount > 0)
        ? t('batch_banner_ready_fmt', { done: doneCount, total: snap.total_target, unacked: unackedCount })
        : `⚡ Batch: ${doneCount}/${snap.total_target} (${pct.toFixed(0)}%) • ETA ${formatEta(snap.eta_s)}`;
    }
    if (bannerFill) bannerFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }
}

export function buildBatchJobRequestPayload(runtime, useDirectFolder, accentColor = 'auto') {
  const w = clampIntInput('batch-width', 16, 512, 128);
  const h = clampIntInput('batch-height', 16, 512, 32);
  const fps = clampIntInput('batch-fps', 6, 30, 15);
  const dur = clampFloatInput('batch-video-dur', 2.0, 120.0, 8.0);
  const lmh = Boolean(document.getElementById('batch-let-me-handle')?.checked);
  const dom = document.getElementById('batch-domain')?.value || 'games';
  const lang = document.getElementById('batch-lang')?.value || getCurrentUiLang();
  const { systems, genres, decades } = getSelectedBatchFilters();
  const badgeEl = document.getElementById('batch-chk-badge');
  const yearEl = document.getElementById('batch-chk-year');
  const ratingEl = document.getElementById('batch-chk-rating');

  return {
    mode: runtime.mode,
    delivery_mode: useDirectFolder ? 'direct_folder' : 'zip_stream',
    domain: dom,
    lang,
    systems,
    genres,
    decades,
    min_rating: clampFloatInput('batch-min-rating', 0.0, 9.5, 0.0),
    video_type: document.getElementById('batch-video-type')?.value || 'any',
    target_count: clampIntInput('batch-target-count', 1, 10000, 100),
    max_workers: clampIntInput('batch-workers', 0, 8, 0),
    ffmpeg_threads_per_worker: clampIntInput('batch-ffmpeg-threads', 1, 4, 1),
    folder_template: document.getElementById('batch-folder-tpl')?.value || 'system_genre',
    safe_ascii_folders: Boolean(document.getElementById('batch-safe-ascii')?.checked),
    wrap_orientation_root: Boolean(document.getElementById('batch-wrap-orient')?.checked),
    include_marquee_png_companion: Boolean(document.getElementById('batch-marquee-png')?.checked),
    preset: runtime.preset,
    chapters: {
      include_card: Boolean(document.getElementById('batch-chk-card')?.checked),
      include_poster_in_card: Boolean(document.getElementById('batch-chk-poster')?.checked),
      show_badge: badgeEl ? Boolean(badgeEl.checked) : true,
      show_year: yearEl ? Boolean(yearEl.checked) : true,
      show_rating: ratingEl ? Boolean(ratingEl.checked) : true,
      include_description: Boolean(document.getElementById('batch-chk-desc')?.checked),
      card_layout: runtime.cardLayout || 'classic_split',
      card_theme: document.getElementById('batch-card-theme')?.value || 'tinted',
      card_font: document.getElementById('batch-card-font')?.value || 'arcade_pixel',
      card_accent_color: accentColor || 'auto',
      text_scroll_speed: String(clampIntInput('batch-scroll-speed', 1, 10, 4)),
      card_hold_s: clampFloatInput('batch-card-hold', 0.2, 4.0, 0.9),
      include_jacket: Boolean(document.getElementById('batch-chk-jacket')?.checked),
      jacket_mode: document.getElementById('batch-jacket-mode')?.value || 'panorama',
      include_marquee: Boolean(document.getElementById('batch-chk-marquee')?.checked),
      marquee_mode: document.getElementById('batch-marquee-mode')?.value || 'panorama',
      snapshot_count: clampIntInput('batch-snap-count', 0, 5, 1),
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
    existing_relpaths: runtime.existingRelpaths || [],
    gamelist_files: runtime.mode === 'recalbox_gamelist' ? runtime.gamelistFiles : []
  };
}
