import { esc } from './api.js';
import { t } from './i18n/index.js';
import { uiState } from './state.js';

function getOffscreenCanvas() {
  if (!uiState.studioOffscreenCanvas && typeof document !== 'undefined') {
    uiState.studioOffscreenCanvas = document.createElement('canvas');
  }
  return uiState.studioOffscreenCanvas;
}

export function decodeDmdsBinaryBuffer(buf) {
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

export function applyDecodedStudioStream(decoded, chapters, isFullSeq) {
  if (!decoded || !decoded.frames.length) return;
  uiState.studioStreamW = decoded.w;
  uiState.studioStreamH = decoded.h;
  uiState.studioStreamFps = decoded.fps;
  uiState.studioFrameDurMs = decoded.frameDurMs;
  uiState.studioFrames = decoded.frames;
  uiState.studioChapters = Array.isArray(chapters) ? chapters : [];
  uiState.studioFrameIdx = 0;
  uiState.studioPlaying = true;
  const playBtn = document.getElementById('btn-studio-play');
  if (playBtn) playBtn.textContent = '⏸';
  const scrub = document.getElementById('studio-scrubber');
  if (scrub) {
    scrub.max = String(Math.max(0, uiState.studioFrames.length - 1));
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

export function renderStudioChaptersBar() {
  const bar = document.getElementById('studio-chapters-bar');
  if (!bar) return;
  if (!uiState.studioChapters.length) {
    bar.innerHTML = '';
    return;
  }
  bar.innerHTML = uiState.studioChapters.map((ch, idx) => {
    const startF = Number(ch.start_frame || 0);
    const durS = Number(ch.duration_s || 0).toFixed(1);
    return `<button type="button" class="chapter-pill" data-ch-idx="${idx}" onclick="jumpToStudioChapter(${startF})">
      ${esc(ch.label || ch.id)} (${durS}s)
    </button>`;
  }).join('');
}

export function jumpToStudioChapter(startFrame) {
  if (!uiState.studioFrames.length) return;
  uiState.studioFrameIdx = Math.max(0, Math.min(uiState.studioFrames.length - 1, Number(startFrame) || 0));
  redrawCurrentStudioFrame();
}

export function computeStudioScale(w, h) {
  const zoomVal = (document.getElementById('studio-led-zoom') || {}).value || 'auto';
  if (zoomVal !== 'auto') {
    return Math.max(2, Math.min(10, parseInt(zoomVal, 10) || 4));
  }
  const maxW = 480;
  const maxH = 380;
  return Math.max(2, Math.min(7, Math.floor(Math.min(maxW / Math.max(1, w), maxH / Math.max(1, h)))));
}

export function redrawCurrentStudioFrame() {
  const canvas = document.getElementById('studio-dmd-canvas');
  if (!canvas || !uiState.studioFrames.length) return;
  const rgba = uiState.studioFrames[uiState.studioFrameIdx];
  if (!rgba) return;

  const w = uiState.studioStreamW;
  const h = uiState.studioStreamH;
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
    const offscreen = getOffscreenCanvas();
    offscreen.width = w;
    offscreen.height = h;
    const octx = offscreen.getContext('2d');
    octx.putImageData(new ImageData(rgba, w, h), 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(offscreen, 0, 0, cw, ch);
  } else if (ledMode === 'pixel_grid') {
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
    const durTot = ((uiState.studioFrames.length * uiState.studioFrameDurMs) / 1000).toFixed(1);
    metaEl.textContent = `${w}×${h} • ${uiState.studioStreamFps} FPS • ${durTot}s`;
  }
  const fLbl = document.getElementById('studio-sim-frame-lbl');
  if (fLbl) {
    fLbl.textContent = `Frame ${uiState.studioFrameIdx + 1} / ${uiState.studioFrames.length}`;
  }
  const scrub = document.getElementById('studio-scrubber');
  if (scrub && document.activeElement !== scrub) {
    scrub.value = String(uiState.studioFrameIdx);
  }
  document.querySelectorAll('#studio-chapters-bar .chapter-pill').forEach((btn, idx) => {
    const ch = uiState.studioChapters[idx];
    if (!ch) return;
    const sf = Number(ch.start_frame || 0);
    const ef = sf + Number(ch.frames || 0);
    btn.classList.toggle('active', uiState.studioFrameIdx >= sf && uiState.studioFrameIdx < ef);
  });
}

export function startStudioAnimationLoop() {
  if (uiState.studioRafId) return;
  function tick(nowMs) {
    const pane = document.getElementById('tab-studio');
    if (!pane || !pane.classList.contains('active')) {
      uiState.studioRafId = 0;
      return;
    }
    if (uiState.studioPlaying && uiState.studioFrames.length > 1) {
      if (!uiState.studioLastTickMs) uiState.studioLastTickMs = nowMs;
      if (nowMs - uiState.studioLastTickMs >= uiState.studioFrameDurMs) {
        uiState.studioLastTickMs = nowMs;
        uiState.studioFrameIdx = (uiState.studioFrameIdx + 1) % uiState.studioFrames.length;
        redrawCurrentStudioFrame();
      }
    }
    uiState.studioRafId = requestAnimationFrame(tick);
  }
  uiState.studioRafId = requestAnimationFrame(tick);
}

export function toggleStudioPlay() {
  uiState.studioPlaying = !uiState.studioPlaying;
  const btn = document.getElementById('btn-studio-play');
  if (btn) btn.textContent = uiState.studioPlaying ? '⏸' : '▶';
}

export function studioStepFrame(delta) {
  if (!uiState.studioFrames.length) return;
  uiState.studioPlaying = false;
  const btn = document.getElementById('btn-studio-play');
  if (btn) btn.textContent = '▶';
  uiState.studioFrameIdx = (uiState.studioFrameIdx + delta + uiState.studioFrames.length) % uiState.studioFrames.length;
  redrawCurrentStudioFrame();
}

export function onStudioScrub(val) {
  if (!uiState.studioFrames.length) return;
  uiState.studioFrameIdx = Math.max(0, Math.min(uiState.studioFrames.length - 1, parseInt(val, 10) || 0));
  redrawCurrentStudioFrame();
}
