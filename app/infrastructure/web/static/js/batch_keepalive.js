/**
 * Background-Tab Continuity & Anti-Freeze Keep-Alive (`static/js/batch_keepalive.js`).
 *
 * Ensures long-running Batch Scraper streams continue at 100% speed when the user switches
 * browser tabs or minimizes the window (just like watching a video or playing media):
 * 1. Holds a Chromium `navigator.locks` Web Lock (explicitly disables Tab Freezing / Memory Saver).
 * 2. Runs an ultra-silent `AudioContext` sub-audible keep-alive (`gain = 0.00001`) started on
 *    the user's click gesture and torn down immediately on pause, cancel, or completion.
 */

let activeLockRelease = null;
let wakeLockSentinel = null;
let keepAliveWanted = false;
let visibilityBound = false;
let audioCtx = null;
let audioOsc = null;
let baseDocTitle = '';

async function requestScreenWakeLockIfSupported() {
  if (!keepAliveWanted || typeof navigator === 'undefined' || !navigator.wakeLock || !navigator.wakeLock.request) {
    return;
  }
  if (wakeLockSentinel && !wakeLockSentinel.released) return;
  try {
    wakeLockSentinel = await navigator.wakeLock.request('screen');
  } catch (_) {
    wakeLockSentinel = null;
  }
}

function ensureVisibilityWakeListener() {
  if (visibilityBound || typeof document === 'undefined') return;
  visibilityBound = true;
  document.addEventListener('visibilitychange', () => {
    if (keepAliveWanted && document.visibilityState === 'visible') {
      requestScreenWakeLockIfSupported();
    }
  });
}

export function acquireBatchBackgroundKeepAlive() {
  if (typeof window === 'undefined') return;
  keepAliveWanted = true;
  if (!baseDocTitle && typeof document !== 'undefined') {
    baseDocTitle = document.title || 'DMD Media Showcase Server';
  }

  // 1. Screen Wake Lock API — prevents mobile/tablet screens (iOS 16.4+ Safari & Android Chrome) from sleeping
  ensureVisibilityWakeListener();
  requestScreenWakeLockIfSupported();

  // 2. Web Locks API — prevents Chromium PageScheduler from freezing or discarding the background tab
  if (!activeLockRelease && typeof navigator !== 'undefined' && navigator.locks && navigator.locks.request) {
    try {
      navigator.locks.request('dmd_batch_scraper_active_stream', () => new Promise(resolve => {
        activeLockRelease = resolve;
      })).catch(() => {});
    } catch (_) {}
  }

  // 3. Sub-audible Web Audio keep-alive — grants active-media background tab immunity across OSes
  if (!audioCtx) {
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (AudioCtor) {
      try {
        audioCtx = new AudioCtor();
        const gain = audioCtx.createGain();
        gain.gain.value = 0.00001;
        audioOsc = audioCtx.createOscillator();
        audioOsc.frequency.value = 30;
        audioOsc.connect(gain);
        gain.connect(audioCtx.destination);
        audioOsc.start();
        if (audioCtx.state === 'suspended') {
          audioCtx.resume().catch(() => {});
        }
      } catch (_) {
        releaseAudioOnly();
      }
    }
  } else if (audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {});
  }
}

function releaseAudioOnly() {
  if (audioOsc) {
    try { audioOsc.stop(); } catch (_) {}
    try { audioOsc.disconnect(); } catch (_) {}
    audioOsc = null;
  }
  if (audioCtx) {
    try { audioCtx.close(); } catch (_) {}
    audioCtx = null;
  }
}

export function releaseBatchBackgroundKeepAlive() {
  keepAliveWanted = false;
  if (wakeLockSentinel) {
    const wl = wakeLockSentinel;
    wakeLockSentinel = null;
    try { wl.release().catch(() => {}); } catch (_) {}
  }
  if (activeLockRelease) {
    const rel = activeLockRelease;
    activeLockRelease = null;
    try { rel(); } catch (_) {}
  }
  releaseAudioOnly();
  if (baseDocTitle && typeof document !== 'undefined') {
    document.title = baseDocTitle;
  }
}

export function updateBatchDocumentTitleProgress(pct, active) {
  if (typeof document === 'undefined') return;
  if (!baseDocTitle) baseDocTitle = document.title || 'DMD Media Showcase Server';
  if (!active) {
    document.title = baseDocTitle;
    return;
  }
  const cleanPct = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  document.title = `[${cleanPct}%] ${baseDocTitle}`;
}
