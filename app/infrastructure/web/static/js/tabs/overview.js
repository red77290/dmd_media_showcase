import { apiFetch, esc } from '../api.js';
import { t } from '../i18n/index.js';
import { CANONICAL_DOMAINS, uiState } from '../state.js';
import { hydrateStudioDraftStylesFromCache, initStudioIfNeeded } from './studio_controls.js';
import { loadTargetDmdQueue } from './tester_queue.js';
import { loadHealthAndConfig } from './runtime_config.js';
import { renderProviders, renderSourceBadge } from './source_chains.js';

export { renderProviders, renderSourceBadge };

export function switchTab(name) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const targetPane = document.getElementById('tab-' + name);
  if (targetPane) targetPane.classList.add('active');
  const idx = {overview: 0, runtime: 1, tester: 2, studio: 3, keys: 4, network: 5}[name] || 0;
  const tabBtns = document.querySelectorAll('.tab-btn');
  if (tabBtns[idx]) tabBtns[idx].classList.add('active');
  if (name === 'studio') {
    initStudioIfNeeded();
  }
}

export function renderRuntimeSummary(cfg, h) {
  const c = cfg || uiState.lastServerConfig || {};
  const health = h || uiState.lastHealth || {};
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

function resolveVaultKeyState(vs, key, legacy = {}) {
  const raw = vs ? vs[key] : undefined;
  if (typeof raw === 'string' && raw) {
    if (raw === 'user_override' || raw === 'env' || raw === 'prod_vault' || raw === 'missing') {
      return raw;
    }
  }
  if (legacy.userOverride && vs && vs[legacy.userOverride]) return 'user_override';
  if (legacy.prodVault && vs && vs[legacy.prodVault]) return 'prod_vault';
  if (legacy.active && vs && vs[legacy.active]) return 'env';
  return 'missing';
}

function applyVaultSummaryPill(elId, states, fallbackActive = false, fallbackProd = false) {
  const el = document.getElementById(elId);
  if (!el) return;
  const allPresent = states.length > 0 && states.every(s => s !== 'missing');
  if (allPresent && states.includes('user_override')) {
    el.className = 'pill info';
    el.textContent = t('vault_override');
  } else if ((allPresent && states.includes('prod_vault')) || (!allPresent && fallbackProd)) {
    el.className = 'pill ok';
    el.textContent = t('vault_prod_active');
  } else if (allPresent || fallbackActive) {
    el.className = 'pill ok';
    el.textContent = t('vault_active');
  } else {
    el.className = 'pill warn';
    el.textContent = t('vault_missing');
  }
}

function applyVaultFieldBadge(fieldId, state) {
  const el = document.getElementById('vault-badge-' + fieldId);
  if (!el) return;
  if (state === 'user_override') {
    el.className = 'vault-field-badge override';
    el.textContent = t('vbadge_override');
  } else if (state === 'prod_vault') {
    el.className = 'vault-field-badge ok';
    el.textContent = t('vbadge_prod_vault');
  } else if (state === 'env') {
    el.className = 'vault-field-badge ok';
    el.textContent = t('vbadge_env');
  } else {
    el.className = 'vault-field-badge missing';
    el.textContent = t('vbadge_missing');
  }
}

export function renderVaultStatus(vs) {
  uiState.lastVaultStatus = vs || {};
  const v = uiState.lastVaultStatus;

  const igdbActiveFlag = Boolean(v.igdb_active || v.igdb_configured || v.igdb_builtin_active);
  const igdbIdState = resolveVaultKeyState(v, 'igdb_client_id', {
    userOverride: 'igdb_user_override',
    prodVault: 'igdb_from_prod_vault',
    active: v.igdb_active ? 'igdb_active' : (v.igdb_configured ? 'igdb_configured' : 'igdb_builtin_active')
  });
  const igdbSecretState = resolveVaultKeyState(v, 'igdb_client_secret', {
    userOverride: 'igdb_user_override',
    prodVault: 'igdb_from_prod_vault',
    active: v.igdb_active ? 'igdb_active' : (v.igdb_configured ? 'igdb_configured' : 'igdb_builtin_active')
  });
  const ssDevIdState = resolveVaultKeyState(v, 'screenscraper_dev_id', {
    userOverride: 'screenscraper_dev_user_override',
    prodVault: 'screenscraper_dev_from_prod_vault',
    active: 'screenscraper_dev_active'
  });
  const ssDevPassState = resolveVaultKeyState(v, 'screenscraper_dev_pass', {
    userOverride: 'screenscraper_dev_user_override',
    prodVault: 'screenscraper_dev_from_prod_vault',
    active: 'screenscraper_dev_active'
  });
  const tmdbState = resolveVaultKeyState(v, 'tmdb_api_key', {
    userOverride: 'tmdb_user_override',
    prodVault: 'tmdb_from_prod_vault',
    active: 'tmdb_builtin_active'
  });
  const tvdbState = resolveVaultKeyState(v, 'tvdb_api_key', {
    userOverride: 'tvdb_user_override',
    prodVault: 'tvdb_from_prod_vault',
    active: 'tvdb_builtin_active'
  });

  applyVaultSummaryPill('vault-igdb', [igdbIdState, igdbSecretState], igdbActiveFlag, Boolean(v.igdb_from_prod_vault));
  applyVaultSummaryPill(
    'vault-ss-dev',
    [ssDevIdState, ssDevPassState],
    Boolean(v.screenscraper_dev_active),
    Boolean(v.screenscraper_dev_from_prod_vault)
  );
  applyVaultSummaryPill('vault-tmdb', [tmdbState], Boolean(v.tmdb_builtin_active), Boolean(v.tmdb_from_prod_vault));
  applyVaultSummaryPill('vault-tvdb', [tvdbState], Boolean(v.tvdb_builtin_active), Boolean(v.tvdb_from_prod_vault));

  applyVaultFieldBadge('igdb_client_id', igdbIdState);
  applyVaultFieldBadge('igdb_client_secret', igdbSecretState);
  applyVaultFieldBadge('screenscraper_dev_id', ssDevIdState);
  applyVaultFieldBadge('screenscraper_dev_pass', ssDevPassState);
  applyVaultFieldBadge('tmdb_api_key', tmdbState);
  applyVaultFieldBadge('tvdb_api_key', tvdbState);
}

export function renderClients(clients) {
  uiState.lastClients = Array.isArray(clients) ? clients : [];
  syncTargetDmdSelector();
  const tb = document.getElementById('clients-tbody');
  if (!tb) return;
  if (!uiState.lastClients.length) {
    tb.innerHTML = `<tr><td colspan="8" style="color:var(--muted)">${esc(t('no_clients'))}</td></tr>`;
    return;
  }
  tb.innerHTML = uiState.lastClients.map(c => {
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
      const pausedSrc = c.playing_title ? renderSourceBadge(c.playing_source) : '';
      playingCell = `<div class="slot-cell-stack"><span class="pill warn slot-pill">${esc(t('pill_paused'))}</span>${pausedSrc}</div>`;
    } else if (c.udp_streaming) {
      const liveTitle = c.playing_title || c.active_title || 'Live';
      playingCell = `<div class="slot-cell-stack"><span class="pill ok slot-pill" title="${esc(liveTitle)}">${esc(t('pill_streaming'))} ${esc(liveTitle)}</span>${renderSourceBadge(c.playing_source)}</div>`;
    } else if (c.is_playing && c.playing_title) {
      playingCell = `<div class="slot-cell-stack"><span class="pill info slot-pill" title="${esc(c.playing_title)}">▶ ${esc(c.playing_title)}</span>${renderSourceBadge(c.playing_source)}</div>`;
    } else if (c.playing_title) {
      playingCell = `<div class="slot-cell-stack"><span class="pill info slot-pill" title="${esc(c.playing_title)}">▶ ${esc(c.playing_title)}</span>${renderSourceBadge(c.playing_source)}</div>`;
    } else if (c.last_played_title || c.active_title) {
      const prevTitle = c.last_played_title || c.active_title;
      playingCell = `<div class="slot-cell-stack"><span class="last-played slot-pill" title="${esc(prevTitle)}">↺ ${esc(prevTitle)}</span>${renderSourceBadge(c.last_played_source || c.playing_source)}</div>`;
    }
    const readyCell = c.ready_title
      ? `<div class="slot-cell-stack"><span class="pill ok slot-pill" title="${esc(c.ready_title)}">✓ ${esc(c.ready_title)}</span>${renderSourceBadge(c.ready_source)}</div>`
      : (c.preparing
        ? `<div class="slot-cell-stack"><span class="pill warn slot-pill">${esc(t('pill_preparing'))}</span>${renderSourceBadge(c.preparing_source)}</div>`
        : '—');
    const standbyCell = c.standby_title
      ? `<div class="slot-cell-stack"><span class="pill info slot-pill" title="${esc(c.standby_title)}">✓ ${esc(c.standby_title)}</span>${renderSourceBadge(c.standby_source)}</div>`
      : (c.ready_title && c.preparing
        ? `<div class="slot-cell-stack"><span class="pill warn slot-pill">${esc(t('pill_preparing'))}</span>${renderSourceBadge(c.preparing_source)}</div>`
        : '—');
    const safeId = esc(c.dmd_id).replace(/'/g, "\\'");
    const pauseAct = c.paused ? 'resume' : 'pause';
    const pauseIcon = c.paused ? '▶' : '⏸';
    return `
      <tr>
        <td class="dmd-id-cell">
          <b>${esc(c.display_name || c.dmd_id)}</b>
          <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">${modeBadge} ${langBadge}${qBadge}</div>
          <small style="color:var(--muted)">${ipInfo}</small>
        </td>
        <td><span class="pill info">${esc((c.domain || '').toUpperCase())}</span></td>
        <td><code>${esc(c.width)}×${esc(c.height)}</code></td>
        <td class="framing-cell"><code>${esc(c.video_mode)}</code> • <code>${esc(c.color_boost)}</code>${c.video_max_duration_s != null ? `<br/><small style="color:var(--muted)">≤${esc(Number(c.video_max_duration_s).toFixed(1))}s @ ${esc(c.video_fps || 15)}fps</small>` : ''}</td>
        <td>${playingCell}</td>
        <td>${readyCell}</td>
        <td>${standbyCell}</td>
        <td class="actions-cell">
          <div class="client-actions">
            <button type="button" class="secondary mini-btn" title="${esc(t('btn_tip_profile'))}" onclick="openProfileEditor('${safeId}')">⚙️</button>
            <button type="button" class="secondary mini-btn" title="${esc(t('btn_tip_skip'))}" onclick="controlDmd('${safeId}', 'skip')">⏭</button>
            <button type="button" class="secondary mini-btn" title="${esc(t('btn_tip_pause'))}" onclick="controlDmd('${safeId}', '${pauseAct}')">${pauseIcon}</button>
            <button type="button" class="secondary mini-btn" title="${esc(t('btn_tip_queue'))}" onclick="jumpToQueueStudio('${safeId}')">🎯</button>
            <button type="button" class="secondary mini-btn" title="${esc(t('btn_tip_studio'))}" onclick="openStudioForDmd('${safeId}')">🎨</button>
            <button type="button" class="danger mini-btn" title="${esc(t('btn_tip_purge_dmd'))}" onclick="purgeDmdClient('${safeId}')">✕</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

export function renderSeen(stats) {
  uiState.lastSeenStats = stats || {};
  const el = document.getElementById('seen-badges');
  const orderedKeys = [
    ...CANONICAL_DOMAINS,
    ...Object.keys(uiState.lastSeenStats).filter(k => !CANONICAL_DOMAINS.includes(k))
  ];
  let total = 0;
  const nonZeroParts = [];
  const html = orderedKeys.map(k => {
    const n = Number(uiState.lastSeenStats[k] || 0);
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

export function renderHealth(h) {
  uiState.lastHealth = h;
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
      : (new Set(uiState.lastClients.map(c => c.dmd_id)).size || h.active_sessions || 0)
  );
  const slotsCount = Number(
    h.registered_dmd_slots != null ? h.registered_dmd_slots : (uiState.lastClients.length || h.active_sessions || 0)
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

  if (!Object.keys(uiState.lastProviders).length) {
    const fallbackProviders = (h.providers_detailed && Object.keys(h.providers_detailed).length)
      ? h.providers_detailed
      : h.providers_configured;
    if (fallbackProviders && Object.keys(fallbackProviders).length) {
      renderProviders(fallbackProviders);
    }
  }
  renderRuntimeSummary(uiState.lastServerConfig, h);
}

export function syncTargetDmdSelector() {
  const sel = document.getElementById('test-target-dmd');
  if (!sel) return;
  const prev = sel.value;
  const known = new Map();
  (uiState.lastDmdProfiles || []).forEach(p => {
    if (p && p.dmd_id) {
      const tag = p.config_source === 'server'
        ? (p.supports_local_card === false ? '2B UDP' : '2A Hybrid')
        : 'Client';
      known.set(p.dmd_id, `${p.display_name || p.dmd_id} (${p.width}×${p.height} • ${tag} • ${(p.lang || 'fr').toUpperCase()})`);
    }
  });
  (uiState.lastClients || []).forEach(c => {
    if (c && c.dmd_id && !known.has(c.dmd_id)) {
      const trTag = c.transport || 'Client';
      known.set(c.dmd_id, `${c.display_name || c.dmd_id} (${c.width}×${c.height} • ${trTag} • ${(c.lang || 'fr').toUpperCase()})`);
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
      const activeCli = (uiState.lastClients || []).find(
        c => c && c.dmd_id && (c.udp_streaming || c.is_playing || c.playing_title)
      );
      chosen = (activeCli && known.has(activeCli.dmd_id))
        ? activeCli.dmd_id
        : Array.from(known.keys())[0];
    }
    stSel.value = chosen;
    if (stTopSel) stTopSel.value = chosen;
    if (chosen && uiState.studioLoadedDmdId !== chosen) {
      hydrateStudioDraftStylesFromCache(chosen);
    }
  }
}

export function jumpToQueueStudio(dmdId) {
  switchTab('tester');
  syncTargetDmdSelector();
  const sel = document.getElementById('test-target-dmd');
  if (sel && dmdId) {
    sel.value = dmdId;
    const prof = (uiState.lastDmdProfiles || []).find(p => p.dmd_id === dmdId);
    const cli = (uiState.lastClients || []).find(c => c.dmd_id === dmdId);
    const lang = (prof && prof.lang) || (cli && cli.lang);
    const langEl = document.getElementById('test-content-lang');
    if (lang && langEl) langEl.value = lang;
    loadTargetDmdQueue();
  }
}

export async function controlDmd(dmdId, action) {
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

export async function purgeDmdClient(dmdId) {
  if (!dmdId) return;
  if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
    if (!window.confirm(t('confirm_purge_dmd', {d: dmdId}))) {
      return;
    }
  }
  const msgEl = document.getElementById('clients-status-msg');
  try {
    const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}`, {
      method: 'DELETE'
    });
    if (!r.ok) {
      throw new Error('HTTP ' + r.status);
    }
    if (uiState.editingProfileDmdId === dmdId) {
      const card = document.getElementById('dmd-profile-editor-card');
      if (card) card.style.display = 'none';
      uiState.editingProfileDmdId = '';
    }
    if (msgEl) {
      msgEl.style.color = 'var(--green)';
      msgEl.textContent = t('msg_dmd_purged', {d: dmdId});
    }
    await loadHealthAndConfig(false);
  } catch (err) {
    if (msgEl) {
      msgEl.style.color = 'var(--red)';
      msgEl.textContent = '⚠️ ' + (err.message || String(err));
    }
    console.error(err);
  }
}
