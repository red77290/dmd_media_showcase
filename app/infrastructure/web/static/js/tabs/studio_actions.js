import { apiFetch, esc } from '../api.js';
import { t } from '../i18n/index.js';
import { applyDecodedStudioStream, decodeDmdsBinaryBuffer } from '../simulator.js';
import {
  CANONICAL_DOMAINS,
  defaultPlaylistRows,
  normalizeDomainStylesObj,
  shortDomainDisplayName,
  uiState
} from '../state.js';
import { controlDmd, renderSourceBadge } from './overview.js';
import { renderPlaylistEditorRows } from './profile_editor.js';
import { loadHealthAndConfig } from './runtime_config.js';
import {
  applyStudioStyleToControls,
  buildStudioCommonPayload,
  captureCurrentControlsIntoStudioStyle,
  markStudioFullDirty,
  scheduleStudioCardPreview,
  updateStudioDomainTabsUi,
  updateStudioResUi
} from './studio_controls.js';
import { loadTargetDmdQueue } from './tester_queue.js';

export async function requestStudioCardPreviewNow() {
  if (uiState.studioCardAbortCtrl) {
    try { uiState.studioCardAbortCtrl.abort(); } catch (_) {}
  }
  const ctrl = new AbortController();
  uiState.studioCardAbortCtrl = ctrl;
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

export async function runStudioFullPreview() {
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
      uiState.studioFullPreviewId = previewId;
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

export function findTargetDmdMeta(dmdId) {
  if (!dmdId) return null;
  const cli = (uiState.lastClients || []).find(c => c && c.dmd_id === dmdId);
  const prof = (uiState.lastDmdProfiles || []).find(p => p && p.dmd_id === dmdId);
  if (!cli && !prof) return null;
  const activeTitle = (cli && (cli.playing_title || cli.active_title)) || '';
  const playingTitle = activeTitle || (cli && cli.ready_title) || '';
  const playingSource = cli
    ? (activeTitle ? (cli.playing_source || cli.last_played_source || '') : (cli.ready_source || cli.playing_source || ''))
    : '';
  return {
    dmd_id: dmdId,
    width: Number((prof && prof.width) || (cli && cli.width) || 128),
    height: Number((prof && prof.height) || (cli && cli.height) || 32),
    lang: (prof && prof.lang) || (cli && cli.lang) || 'fr',
    config_source: (prof && prof.config_source) || (cli && cli.config_source) || 'client',
    supports_local_card: prof ? prof.supports_local_card !== false : (cli ? cli.supports_local_card !== false : true),
    playing_title: playingTitle,
    playing_source: playingSource,
    udp_streaming: Boolean(cli && cli.udp_streaming),
    card_style: (prof && prof.card_style) || null,
    domain_styles: (prof && prof.domain_styles) || null
  };
}

export function isTargetDmdClientManaged(dmdId) {
  if (!dmdId) return false;
  const prof = (uiState.lastDmdProfiles || []).find(p => p && p.dmd_id === dmdId);
  if (prof && prof.config_source === 'server') return false;
  if (prof && prof.config_source === 'client') return true;
  const cli = (uiState.lastClients || []).find(c => c && c.dmd_id === dmdId);
  if (cli && (cli.config_source || 'client') === 'client') return true;
  if (uiState.editingProfileReadOnly && uiState.editingProfileDmdId === dmdId) return true;
  return false;
}

export function syncStudioSaveButtonsState() {
  const sel = document.getElementById('studio-target-dmd');
  const topSel = document.getElementById('studio-top-dmd-select');
  const dmdId = (sel && sel.value) || (topSel && topSel.value) || '';
  const ro = isTargetDmdClientManaged(dmdId);
  const tip = ro ? t('msg_studio_readonly_client') : '';
  ['btn-st-top-save', 'btn-st-top-save-all', 'btn-st-sec3-save', 'btn-st-sec3-save-all'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) {
      btn.disabled = ro;
      btn.title = tip;
    }
  });
}

export function renderStudioActiveDmdBanner() {
  syncStudioSaveButtonsState();
  const banner = document.getElementById('studio-active-dmd-banner');
  const sel = document.getElementById('studio-target-dmd');
  if (!banner || !sel) return;
  const dmdId = sel.value;
  const meta = findTargetDmdMeta(dmdId);
  if (!meta) {
    banner.innerHTML = esc(t('studio_opt_no_dmd'));
    return;
  }
  const isClientRo = isTargetDmdClientManaged(dmdId);
  const modeLbl = meta.config_source === 'server'
    ? (meta.supports_local_card === false ? t('mode_badge_2b') : t('mode_badge_2a'))
    : t('mode_badge_client');
  const srcPill = meta.playing_source ? ` ${renderSourceBadge(meta.playing_source)}` : '';
  const playStr = meta.playing_title
    ? `▶ <b>${esc(meta.playing_title)}</b>${srcPill}`
    : esc(t('studio_dmd_idle'));
  let mismatchHtml = '';
  if (meta.width !== uiState.studioWidth || meta.height !== uiState.studioHeight) {
    mismatchHtml = `<div style="color:var(--amber);margin-top:4px;font-size:0.78rem">⚠️ ${esc(t('studio_dmd_res_mismatch', {
      sw: uiState.studioWidth, sh: uiState.studioHeight, d: dmdId, dw: meta.width, dh: meta.height
    }))}</div>`;
  }
  const clientRoHtml = isClientRo
    ? `<div style="color:var(--cyan);margin-top:5px;font-size:0.78rem">📡 ${esc(t('studio_dmd_client_readonly_note'))}</div>`
    : '';
  banner.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
    <span>${playStr}</span>
    <span><span class="pill info">${esc(modeLbl)}</span> <code>${esc(meta.width)}×${esc(meta.height)}</code></span>
  </div>${mismatchHtml}${clientRoHtml}`;
}

export async function loadDmdDomainStylesIntoStudio(dmdId) {
  const stSel = document.getElementById('studio-target-dmd');
  const topSel = document.getElementById('studio-top-dmd-select');
  if (stSel && stSel.value !== dmdId) stSel.value = dmdId;
  if (topSel && topSel.value !== dmdId) topSel.value = dmdId;
  if (!dmdId) {
    uiState.studioLoadedDmdId = '';
    uiState.studioDraftDomainStyles = normalizeDomainStylesObj(null, null);
    applyStudioStyleToControls(uiState.studioDraftDomainStyles[uiState.studioActiveDomain]);
    updateStudioDomainTabsUi();
    return;
  }
  let prof = (uiState.lastDmdProfiles || []).find(p => p && p.dmd_id === dmdId) || null;
  try {
    const rp = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`);
    if (rp.ok) {
      prof = (await rp.json()).profile || prof;
    }
  } catch (_) {}
  uiState.studioLoadedDmdId = dmdId;
  uiState.studioDraftDomainStyles = normalizeDomainStylesObj(prof && prof.domain_styles, prof && prof.card_style);
  if (prof && prof.lang) {
    const langEl = document.getElementById('studio-lang');
    if (langEl) langEl.value = prof.lang;
  }
  applyStudioStyleToControls(uiState.studioDraftDomainStyles[uiState.studioActiveDomain]);
  updateStudioDomainTabsUi();
}

export async function onStudioTopDmdChange(dmdId) {
  await loadDmdDomainStylesIntoStudio(dmdId || '');
  renderStudioActiveDmdBanner();
  await loadStudioDmdQueue();
  scheduleStudioCardPreview();
}

export async function onStudioTargetDmdChange() {
  const sel = document.getElementById('studio-target-dmd');
  const dmdId = sel ? sel.value : '';
  await loadDmdDomainStylesIntoStudio(dmdId);
  renderStudioActiveDmdBanner();
  await loadStudioDmdQueue();
  scheduleStudioCardPreview();
}

export function syncStudioResFromActiveDmd() {
  const sel = document.getElementById('studio-target-dmd');
  const meta = sel ? findTargetDmdMeta(sel.value) : null;
  if (!meta) return;
  uiState.studioWidth = meta.width;
  uiState.studioHeight = meta.height;
  markStudioFullDirty();
  updateStudioResUi();
  scheduleStudioCardPreview();
}

export async function skipStudioTargetDmd() {
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

export async function loadStudioDmdQueue() {
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
      const srcPill = renderSourceBadge(it.source_used || it.source_id);
      const simPill = it.from_studio_sim ? ` <span class="pill ok">${esc(t('pill_sim_studio'))}</span>` : '';
      return `<div class="queue-item-row">
        <div class="queue-item-meta">
          <b>#${i + 1} — ${esc(it.title)}</b>
          <span class="pill info">${esc((it.domain || '').toUpperCase())}</span>
          <span class="pill ${stClass}">${esc(it.status)}</span>
          ${srcPill}${simPill}
        </div>
        <button type="button" class="danger mini-btn" onclick="removeQueueItem('${safeDmd}', '${safeQid}')">✕</button>
      </div>`;
    }).join('');
  } catch (err) {
    console.error(err);
  }
}

export async function enqueueStudioSim(mode) {
  const dmdId = (document.getElementById('studio-target-dmd') || {}).value || '';
  const msgEl = document.getElementById('studio-queue-action-msg');
  if (!dmdId) {
    msgEl.style.color = 'var(--amber)';
    msgEl.textContent = t('msg_need_target_dmd');
    return;
  }
  const p = buildStudioCommonPayload();
  const markSeen = document.getElementById('studio-mark-seen').value === 'true';
  const usedPreviewId = uiState.studioFullPreviewId || null;

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

export function setStudioSaveToast(msg, color) {
  ['studio-top-save-msg', 'studio-queue-action-msg'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.color = color;
    el.textContent = msg;
  });
}

export async function saveStudioStyleToDmdProfile(applyAllDomains = false) {
  const dmdId = (document.getElementById('studio-target-dmd') || {}).value
    || (document.getElementById('studio-top-dmd-select') || {}).value
    || '';
  if (!dmdId) {
    setStudioSaveToast(t('msg_need_target_dmd'), 'var(--amber)');
    return;
  }
  if (isTargetDmdClientManaged(dmdId)) {
    setStudioSaveToast(t('msg_studio_readonly_client'), 'var(--amber)');
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
    if (uiState.studioDraftDomainStyles && uiState.studioDraftDomainStyles[dom]) {
      mergedDomainStyles[dom] = Object.assign({}, mergedDomainStyles[dom], uiState.studioDraftDomainStyles[dom]);
    }
  });

  if (applyAllDomains) {
    CANONICAL_DOMAINS.forEach(dom => {
      mergedDomainStyles[dom] = Object.assign({}, curStyle);
    });
  } else {
    mergedDomainStyles[uiState.studioActiveDomain] = Object.assign({}, curStyle);
  }
  uiState.studioDraftDomainStyles = mergedDomainStyles;
  updateStudioDomainTabsUi();

  const baseMeta = findTargetDmdMeta(dmdId) || {width: uiState.studioWidth, height: uiState.studioHeight, lang: p.lang};
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
    width: (prof && prof.width) || baseMeta.width || uiState.studioWidth,
    height: (prof && prof.height) || baseMeta.height || uiState.studioHeight,
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
    uiState.studioLoadedDmdId = dmdId;
    if (uiState.editingProfileDmdId === dmdId || uiState.studioReturnToRotationDmd === dmdId) {
      uiState.editingProfileDomainStyles = normalizeDomainStylesObj(mergedDomainStyles, null);
      renderPlaylistEditorRows();
    }
    const okMsg = applyAllDomains
      ? t('msg_studio_all_doms_saved', {d: dmdId})
      : t('msg_studio_dom_saved', {dom: shortDomainDisplayName(uiState.studioActiveDomain), d: dmdId});
    setStudioSaveToast(okMsg, 'var(--green)');
    loadHealthAndConfig(false);
  } catch (err) {
    setStudioSaveToast('⚠️ ' + (err.message || String(err)), 'var(--red)');
  }
}
