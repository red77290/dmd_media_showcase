import { esc, showConfigToast } from '../api.js';
import { t } from '../i18n/index.js';
import {
  CANONICAL_DOMAINS,
  DEFAULT_DOMAIN_SOURCES,
  normalizeSourcePolicies,
  shortDomainDisplayName,
  uiState
} from '../state.js';
import { saveSourcePolicies } from './runtime_config.js';

export function renderSourceBadge(src, opts = {}) {
  const raw = String(src || '').trim();
  if (!raw || raw === 'none' || raw === 'unknown') return '';
  if (raw === 'studio_preview' && !opts.allowStudioPreview) return '';
  if (raw.includes('+')) {
    return `<span class="pill source-pill aggregated" title="${esc(raw)}">🔗 ${esc(raw)}</span>`;
  }
  return `<span class="pill source-pill" title="${esc(raw)}">${esc(raw)}</span>`;
}

function buildDomainSourceMetaMap(dom, rawChain) {
  const metaMap = new Map();
  (DEFAULT_DOMAIN_SOURCES[dom] || []).forEach(def => {
    metaMap.set(def.source_id, {
      source_id: def.source_id,
      display_name: def.display_name || def.source_id,
      configured: true,
      health: 'ok',
      enabled: true
    });
  });

  let chainList = [];
  if (Array.isArray(rawChain)) {
    chainList = rawChain;
  } else if (typeof rawChain === 'string') {
    chainList = rawChain
      .split('->')
      .map(s => s.trim())
      .filter(s => s && s !== 'none')
      .map((sid, idx) => ({
        source_id: sid,
        role: idx === 0 ? 'primary' : 'fallback',
        configured: true,
        health: 'ok'
      }));
  }

  chainList.forEach(item => {
    if (!item || !item.source_id || item.source_id === 'none') return;
    const prev = metaMap.get(item.source_id) || {
      source_id: item.source_id,
      display_name: item.source_id,
      configured: true,
      health: 'ok',
      enabled: true
    };
    const configured = item.configured !== undefined ? Boolean(item.configured) : prev.configured;
    const health = item.health || (configured === false ? 'unconfigured' : prev.health || 'ok');
    metaMap.set(item.source_id, {
      source_id: item.source_id,
      display_name: item.display_name || prev.display_name || item.source_id,
      configured,
      health,
      enabled: item.enabled !== undefined ? Boolean(item.enabled) : prev.enabled
    });
  });

  return metaMap;
}

export function renderProviders(providers, sourcePolicies = null) {
  uiState.lastProviders = providers || {};
  const effectivePolicies = normalizeSourcePolicies(
    uiState.lastProviders,
    sourcePolicies
      || (uiState.lastSourcePolicies && Object.keys(uiState.lastSourcePolicies).length ? uiState.lastSourcePolicies : null)
      || (uiState.lastServerConfig && uiState.lastServerConfig.source_policies)
  );
  uiState.lastSourcePolicies = effectivePolicies;

  const grids = [
    document.getElementById('providers-grid'),
    document.getElementById('keys-providers-grid')
  ].filter(Boolean);
  if (!grids.length) return;

  const rawKeys = Object.keys(uiState.lastProviders);
  const orderedDomains = [
    ...CANONICAL_DOMAINS,
    ...rawKeys.filter(d => !CANONICAL_DOMAINS.includes(d))
  ];

  const html = orderedDomains.map(dom => {
    const domPol = effectivePolicies[dom] || {
      ordered_sources: [],
      disabled_sources: [],
      aggregation_mode: 'enrich_missing'
    };
    const metaMap = buildDomainSourceMetaMap(dom, uiState.lastProviders[dom]);
    const orderedIds = domPol.ordered_sources.length
      ? domPol.ordered_sources
      : Array.from(metaMap.keys());
    const disabledSet = new Set(domPol.disabled_sources || []);
    const enabledIds = orderedIds.filter(sid => !disabledSet.has(sid));
    const primaryId = enabledIds[0] || orderedIds[0] || '';
    const secondaryId = enabledIds[1] || '';
    const isAgg = domPol.aggregation_mode !== 'fallback_only';
    const stratBadge = isAgg
      ? `<span class="pill ok">${esc(t('pill_agg_enrich'))}</span>`
      : `<span class="pill info">${esc(t('pill_agg_fallback'))}</span>`;

    const safeDom = esc(dom).replace(/'/g, "\\'");
    const primaryOpts = orderedIds.map(sid => {
      const m = metaMap.get(sid) || {display_name: sid, health: 'ok', configured: true};
      const icon = m.health === 'cooldown' ? '⏳' : (m.health === 'unconfigured' || !m.configured ? '○' : '✓');
      const sel = sid === primaryId ? ' selected' : '';
      return `<option value="${esc(sid)}"${sel}>${esc(m.display_name)} (${icon})</option>`;
    }).join('');

    const secondaryOpts = [
      `<option value=""${!secondaryId ? ' selected' : ''}>${esc(t('opt_src_none'))}</option>`,
      ...orderedIds.map(sid => {
        const m = metaMap.get(sid) || {display_name: sid, health: 'ok', configured: true};
        const icon = m.health === 'cooldown' ? '⏳' : (m.health === 'unconfigured' || !m.configured ? '○' : '✓');
        const sel = sid === secondaryId ? ' selected' : '';
        return `<option value="${esc(sid)}"${sel}>${esc(m.display_name)} (${icon})</option>`;
      })
    ].join('');

    const chainRows = orderedIds.map((sid, idx) => {
      const m = metaMap.get(sid) || {display_name: sid, health: 'ok', configured: true};
      const isEnabled = !disabledSet.has(sid);
      const activeRank = isEnabled ? (enabledIds.indexOf(sid) + 1) : 0;
      let rankPill = `<span class="pill source-rank-pill off">${esc(t('src_rank_off'))}</span>`;
      if (isEnabled) {
        if (activeRank === 1) {
          rankPill = `<span class="pill ok source-rank-pill">#1 ${esc(t('role_primary'))}</span>`;
        } else if (activeRank === 2) {
          rankPill = `<span class="pill info source-rank-pill">#2 ${esc(t('role_secondary'))}</span>`;
        } else {
          rankPill = `<span class="pill source-rank-pill fallback">#${activeRank} ${esc(t('role_fallback'))}</span>`;
        }
      }

      let healthPill = `<span class="pill ok source-health-pill" title="${esc(t('src_health_ok'))}">✓</span>`;
      if (m.health === 'cooldown') {
        healthPill = `<span class="pill warn source-health-pill" title="${esc(t('src_health_cooldown'))}">⏳ ${esc(t('src_health_cooldown'))}</span>`;
      } else if (m.health === 'unconfigured' || m.configured === false) {
        healthPill = `<span class="pill source-health-pill unconf" title="${esc(t('src_health_unconfigured'))}">○ ${esc(t('src_health_unconfigured'))}</span>`;
      }

      const safeSid = esc(sid).replace(/'/g, "\\'");
      const upDis = idx === 0 ? ' disabled' : '';
      const dnDis = idx === orderedIds.length - 1 ? ' disabled' : '';
      const rowMutedCls = isEnabled ? '' : ' is-disabled';
      const togCls = isEnabled ? 'is-on' : 'is-off';
      const togTitle = isEnabled ? t('btn_src_disable') : t('btn_src_enable');

      return `<div class="provider-chain-row${rowMutedCls}">
        <div class="provider-chain-left">
          ${rankPill}
          <span class="provider-src-name" title="${esc(sid)}">${esc(m.display_name)}</span>
          <code class="provider-src-code">${esc(sid)}</code>
          ${healthPill}
        </div>
        <div class="provider-chain-actions">
          <button type="button" class="secondary src-ctrl-btn" title="${esc(t('btn_src_up'))}" onclick="moveDomainSource('${safeDom}', '${safeSid}', -1)"${upDis}>↑</button>
          <button type="button" class="secondary src-ctrl-btn" title="${esc(t('btn_src_down'))}" onclick="moveDomainSource('${safeDom}', '${safeSid}', 1)"${dnDis}>↓</button>
          <button type="button" class="secondary src-ctrl-btn ${togCls}" title="${esc(togTitle)}" onclick="toggleDomainSource('${safeDom}', '${safeSid}')">${isEnabled ? '✓' : '○'}</button>
        </div>
      </div>`;
    }).join('');

    return `<div class="vault-box provider-domain-card">
      <div class="provider-card-head">
        <div class="vault-title" style="margin:0;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span>${esc(shortDomainDisplayName(dom))}</span>
          <code>${esc(dom)}</code>
        </div>
        ${stratBadge}
      </div>
      <div class="provider-selects-grid">
        <div class="field">
          <label>${esc(t('lbl_src_primary'))}</label>
          <select onchange="onSourcePrimaryChange('${safeDom}', this.value)">${primaryOpts}</select>
        </div>
        <div class="field">
          <label>${esc(t('lbl_src_secondary'))}</label>
          <select onchange="onSourceSecondaryChange('${safeDom}', this.value)">${secondaryOpts}</select>
        </div>
        <div class="field provider-strategy-field">
          <label>${esc(t('lbl_src_strategy'))}</label>
          <select onchange="onSourceStrategyChange('${safeDom}', this.value)">
            <option value="enrich_missing"${isAgg ? ' selected' : ''}>${esc(t('opt_agg_enrich'))}</option>
            <option value="fallback_only"${!isAgg ? ' selected' : ''}>${esc(t('opt_agg_fallback'))}</option>
          </select>
        </div>
      </div>
      <div class="provider-chain-list">${chainRows}</div>
    </div>`;
  }).join('');

  grids.forEach(g => {
    g.innerHTML = html;
  });
}

function ensureEditableDomainPolicy(dom) {
  uiState.lastSourcePolicies = normalizeSourcePolicies(uiState.lastProviders, uiState.lastSourcePolicies);
  if (!uiState.lastSourcePolicies[dom]) {
    uiState.lastSourcePolicies[dom] = {
      ordered_sources: [],
      disabled_sources: [],
      aggregation_mode: 'enrich_missing'
    };
  }
  return uiState.lastSourcePolicies[dom];
}

export async function onSourcePrimaryChange(dom, newPrimaryId) {
  if (!dom || !newPrimaryId) return;
  const pol = ensureEditableDomainPolicy(dom);
  pol.disabled_sources = (pol.disabled_sources || []).filter(sid => sid !== newPrimaryId);
  pol.ordered_sources = [
    newPrimaryId,
    ...(pol.ordered_sources || []).filter(sid => sid !== newPrimaryId)
  ];
  await saveSourcePolicies();
}

export async function onSourceSecondaryChange(dom, newSecondaryId) {
  if (!dom) return;
  const pol = ensureEditableDomainPolicy(dom);
  const oldOrdered = (pol.ordered_sources || []).slice();
  const disabledSet = new Set(pol.disabled_sources || []);
  const enabledIds = oldOrdered.filter(sid => !disabledSet.has(sid));
  const curPrimary = enabledIds[0] || oldOrdered[0] || '';
  if (!curPrimary) return;

  if (!newSecondaryId) {
    pol.disabled_sources = oldOrdered.filter(sid => sid !== curPrimary);
  } else if (newSecondaryId === curPrimary) {
    const prevSecondary = enabledIds[1] || oldOrdered.find(sid => sid !== curPrimary);
    if (prevSecondary) {
      pol.disabled_sources = (pol.disabled_sources || []).filter(
        sid => sid !== curPrimary && sid !== prevSecondary
      );
      pol.ordered_sources = [
        prevSecondary,
        curPrimary,
        ...oldOrdered.filter(sid => sid !== prevSecondary && sid !== curPrimary)
      ];
    }
  } else {
    pol.disabled_sources = (pol.disabled_sources || []).filter(
      sid => sid !== curPrimary && sid !== newSecondaryId
    );
    pol.ordered_sources = [
      curPrimary,
      newSecondaryId,
      ...oldOrdered.filter(sid => sid !== curPrimary && sid !== newSecondaryId)
    ];
  }
  await saveSourcePolicies();
}

export async function onSourceStrategyChange(dom, newMode) {
  if (!dom) return;
  const pol = ensureEditableDomainPolicy(dom);
  pol.aggregation_mode = newMode === 'fallback_only' ? 'fallback_only' : 'enrich_missing';
  await saveSourcePolicies();
}

export async function moveDomainSource(dom, sourceId, delta) {
  if (!dom || !sourceId || !delta) return;
  const pol = ensureEditableDomainPolicy(dom);
  const arr = (pol.ordered_sources || []).slice();
  const idx = arr.indexOf(sourceId);
  const nextIdx = idx + Number(delta);
  if (idx < 0 || nextIdx < 0 || nextIdx >= arr.length) return;
  const tmp = arr[idx];
  arr[idx] = arr[nextIdx];
  arr[nextIdx] = tmp;
  pol.ordered_sources = arr;
  await saveSourcePolicies();
}

export async function toggleDomainSource(dom, sourceId) {
  if (!dom || !sourceId) return;
  const pol = ensureEditableDomainPolicy(dom);
  const dis = new Set(pol.disabled_sources || []);
  if (dis.has(sourceId)) {
    dis.delete(sourceId);
  } else {
    const enabledCount = (pol.ordered_sources || []).filter(sid => !dis.has(sid)).length;
    if (enabledCount <= 1) {
      showConfigToast(t('msg_source_min_one'), true, ['status-msg-sources-ov', 'status-msg-sources-keys']);
      renderProviders(uiState.lastProviders, uiState.lastSourcePolicies);
      return;
    }
    dis.add(sourceId);
  }
  pol.disabled_sources = Array.from(dis);
  await saveSourcePolicies();
}

