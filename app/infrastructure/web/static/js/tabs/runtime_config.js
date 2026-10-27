import { apiFetch, clampFloatInput, clampIntInput, esc, isMaskedSecret, showConfigToast } from '../api.js';
import { t } from '../i18n/index.js';
import { normalizeSourcePolicies, uiState } from '../state.js';
import {
  renderClients,
  renderHealth,
  renderProviders,
  renderRuntimeSummary,
  renderSeen,
  renderVaultStatus
} from './overview.js';
import { loadStudioDmdQueue, renderStudioActiveDmdBanner } from './studio_actions.js';
import { loadTargetDmdQueue } from './tester_queue.js';

const SECRET_INPUT_IDS = [
  'tmdb_api_key',
  'tvdb_api_key',
  'screenscraper_pass',
  'igdb_client_id',
  'igdb_client_secret',
  'screenscraper_dev_id',
  'screenscraper_dev_pass',
  'showcase_api_key'
];

function readSecretFieldPayload(id) {
  const el = document.getElementById(id);
  if (!el) return undefined;
  const raw = el.value.trim();
  if (isMaskedSecret(raw)) {
    return undefined;
  }
  if (raw === '') {
    if (el.dataset.cleared === '1' || el.dataset.hadMasked === '1') {
      return '';
    }
    return undefined;
  }
  return raw;
}

function resetSecretClearedFlags() {
  SECRET_INPUT_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) delete el.dataset.cleared;
  });
}

export function syncMqttModeUi() {
  const modeEl = document.getElementById('mqtt_mode');
  const urlEl = document.getElementById('mqtt_external_url');
  const portEl = document.getElementById('mqtt_port');
  if (!modeEl || !urlEl) return;
  const isExt = modeEl.value === 'external';
  urlEl.disabled = !isExt;
  urlEl.placeholder = isExt ? t('ph_mqtt_url') : t('ph_mqtt_embedded');
  if (portEl) portEl.disabled = isExt;
}

export function syncMdnsUi() {
  const mdnsEl = document.getElementById('mdns_enabled');
  const hostEl = document.getElementById('mdns_hostname');
  if (!mdnsEl || !hostEl) return;
  hostEl.disabled = mdnsEl.value === 'false';
}

export function hydrateConfigInputs(cfg, force) {
  if (!cfg) return;
  uiState.lastServerConfig = cfg;
  if (cfg.source_policies && (force || Date.now() >= uiState.sourcePoliciesDirtyUntil)) {
    uiState.lastSourcePolicies = normalizeSourcePolicies(uiState.lastProviders, cfg.source_policies);
  }
  renderRuntimeSummary(cfg, uiState.lastHealth);
  if (uiState.configHydrated && !force) return;
  const fields = {
    tmdb_api_key: cfg.tmdb_api_key || '',
    tvdb_api_key: cfg.tvdb_api_key || '',
    screenscraper_user: cfg.screenscraper_user || '',
    screenscraper_pass: cfg.screenscraper_pass || '',
    screenscraper_dev_id: cfg.screenscraper_dev_id || '',
    screenscraper_dev_pass: cfg.screenscraper_dev_pass || '',
    igdb_client_id: cfg.igdb_client_id || '',
    igdb_client_secret: cfg.igdb_client_secret || '',
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
    on_demand_cache_enabled: String(Boolean(cfg.on_demand_cache_enabled)),
    on_demand_cache_max_mb: String(cfg.on_demand_cache_max_mb != null ? cfg.on_demand_cache_max_mb : 0),
    log_level: String(cfg.log_level || 'INFO').toUpperCase(),
    mdns_enabled: String(cfg.mdns_enabled !== false),
    mdns_hostname: cfg.mdns_hostname || 'dmdmediashowcase',
    trust_proxy_headers: String(cfg.trust_proxy_headers !== false)
  };
  Object.entries(fields).forEach(([id, val]) => {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) {
      if (!force && el.dataset.cleared === '1') return;
      el.value = val;
      if (SECRET_INPUT_IDS.includes(id)) {
        el.dataset.hadMasked = isMaskedSecret(val) ? '1' : '0';
        delete el.dataset.cleared;
      }
    }
  });
  syncMqttModeUi();
  syncMdnsUi();
  uiState.configHydrated = true;
}

export const populateConfigForm = hydrateConfigInputs;

export async function loadHealthAndConfig(forceHydrate = false) {
  const [cfgRes, healthRes] = await Promise.allSettled([
    apiFetch('/api/v1/admin/config', {cache: 'no-store'}),
    fetch('/api/v1/health', {cache: 'no-store'})
  ]);

  if (cfgRes.status === 'fulfilled' && cfgRes.value.ok) {
    try {
      const d = await cfgRes.value.json();
      const cfg = d.config || {};
      uiState.lastDmdProfiles = Array.isArray(d.dmd_profiles) ? d.dmd_profiles : [];
      if (cfg.source_policies && (forceHydrate || Date.now() >= uiState.sourcePoliciesDirtyUntil)) {
        uiState.lastSourcePolicies = normalizeSourcePolicies(d.providers || {}, cfg.source_policies);
      } else if (Date.now() >= uiState.sourcePoliciesDirtyUntil) {
        uiState.lastSourcePolicies = normalizeSourcePolicies(d.providers || {}, uiState.lastSourcePolicies);
      }
      hydrateConfigInputs(cfg, forceHydrate);
      renderOnDemandCacheStats(d.on_demand_cache || {});
      const mergedVault = Object.assign({}, cfg.vault_status || {}, d.vault_status || {});
      renderVaultStatus(mergedVault);
      renderProviders(d.providers || {}, uiState.lastSourcePolicies);
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

export async function saveSourcePolicies() {
  uiState.sourcePoliciesDirtyUntil = Date.now() + 2500;
  renderProviders(uiState.lastProviders, uiState.lastSourcePolicies);
  try {
    const r = await apiFetch('/api/v1/admin/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source_policies: uiState.lastSourcePolicies})
    });
    if (!r.ok) {
      let errDetail = 'HTTP ' + r.status;
      try {
        const errJson = await r.json();
        errDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail || errJson);
      } catch (_) {}
      uiState.sourcePoliciesDirtyUntil = 0;
      showConfigToast('⚠️ ' + errDetail, true, ['status-msg-sources-ov', 'status-msg-sources-keys']);
      loadHealthAndConfig(false);
      return;
    }
    const resData = await r.json();
    uiState.sourcePoliciesDirtyUntil = 0;
    const nextCfg = resData.config || uiState.lastServerConfig || {};
    uiState.lastServerConfig = nextCfg;
    const nextProviders = resData.providers || uiState.lastProviders;
    const nextPolicies = (nextCfg && nextCfg.source_policies) || uiState.lastSourcePolicies;
    uiState.lastSourcePolicies = normalizeSourcePolicies(nextProviders, nextPolicies);
    if (resData.vault_status || nextCfg.vault_status) {
      renderVaultStatus(Object.assign({}, nextCfg.vault_status || {}, resData.vault_status || {}));
    }
    renderProviders(nextProviders, uiState.lastSourcePolicies);
    showConfigToast(t('msg_sources_saved'), false, ['status-msg-sources-ov', 'status-msg-sources-keys']);
  } catch (err) {
    uiState.sourcePoliciesDirtyUntil = 0;
    showConfigToast('⚠️ ' + (err.message || String(err)), true, ['status-msg-sources-ov', 'status-msg-sources-keys']);
  }
}

export async function saveConfig() {
  const showcaseKeyEl = document.getElementById('showcase_api_key');
  const rawShowcaseInput = showcaseKeyEl ? showcaseKeyEl.value.trim() : '';
  const payload = {
    tmdb_api_key: readSecretFieldPayload('tmdb_api_key'),
    tvdb_api_key: readSecretFieldPayload('tvdb_api_key'),
    screenscraper_user: document.getElementById('screenscraper_user').value.trim(),
    screenscraper_pass: readSecretFieldPayload('screenscraper_pass'),
    screenscraper_dev_id: readSecretFieldPayload('screenscraper_dev_id'),
    screenscraper_dev_pass: readSecretFieldPayload('screenscraper_dev_pass'),
    igdb_client_id: readSecretFieldPayload('igdb_client_id'),
    igdb_client_secret: readSecretFieldPayload('igdb_client_secret'),
    source_policies: uiState.lastSourcePolicies,
    mqtt_mode: document.getElementById('mqtt_mode').value,
    mqtt_port: clampIntInput('mqtt_port', 1, 65535, 1883),
    mqtt_external_url: document.getElementById('mqtt_external_url').value.trim(),
    default_lang: document.getElementById('default_lang').value,
    showcase_api_key: readSecretFieldPayload('showcase_api_key'),
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
    on_demand_cache_enabled: document.getElementById('on_demand_cache_enabled')?.value === 'true',
    on_demand_cache_max_mb: clampIntInput('on_demand_cache_max_mb', 0, 1048576, 0),
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
      if (rawShowcaseInput === '') {
        localStorage.removeItem('dmd_showcase_api_key');
      } else if (!isMaskedSecret(rawShowcaseInput)) {
        localStorage.setItem('dmd_showcase_api_key', rawShowcaseInput);
      }
    } catch (_) {}
    resetSecretClearedFlags();
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

export const saveServerConfig = saveConfig;
export const saveApiKeys = saveConfig;

export async function syncReferentials() {
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

export async function resetHistory(domain) {
  const q = domain ? `?domain=${encodeURIComponent(domain)}` : '';
  await apiFetch('/api/v1/admin/history/reset' + q, {method: 'POST'});
  loadHealthAndConfig(false);
}

export function renderOnDemandCacheStats(stats) {
  const pill = document.getElementById('ondemand-cache-stats-pill');
  if (!pill || !stats) return;
  const count = Number(stats.entry_count || 0);
  const mb = Number(stats.total_mb || 0).toFixed(1);
  const enabled = Boolean(stats.enabled);
  pill.className = enabled ? 'pill ok' : 'pill info';
  pill.textContent = t('pill_ondemand_cache_stats', {n: count, mb});
}

export async function evictOnDemandCache() {
  try {
    const r = await apiFetch('/api/v1/cache/on-demand/evict', {method: 'POST'});
    if (!r.ok) {
      showConfigToast('⚠️ HTTP ' + r.status, true);
      return;
    }
    const res = await r.json();
    showConfigToast(
      t('msg_ondemand_cache_evicted', {
        n: res.evicted_count || 0,
        mb: Number(res.freed_mb || 0).toFixed(1)
      }),
      false
    );
    loadHealthAndConfig(false);
  } catch (err) {
    showConfigToast('⚠️ ' + (err.message || String(err)), true);
  }
}

export function refreshAll() {
  loadHealthAndConfig(true);
}
