/**
 * ArcadeMatrix-Style Selectable Filter Bubbles for Tab #7 Batch Scraper (`static/js/batch_bubbles.js`).
 *
 * Replaces legacy `<select multiple>` (`Ctrl/Cmd + Click`) lists with 1-click / 1-tap toggle bubbles
 * matching `ArcadeMatrix` Showcase engine configuration:
 * - Dynamic relational filtering: selecting System bubbles automatically filters available Genre &
 *   Decade bubbles (`supported_genres` / `supported_decades`) and prunes incompatible selections.
 * - Instant search filter + 1-click "All (No filter)" reset per group.
 */

import { esc } from './api.js';
import { t } from './i18n/index.js';

const bubbleState = {
  domain: 'games',
  catalog: { systems: [], genres: [], decades: [] },
  selectedSystems: new Set(['snes', 'megadrive', 'neogeo']),
  selectedGenres: new Set(),
  selectedDecades: new Set(),
  searchQuery: {
    systems: '',
    genres: '',
    decades: ''
  }
};

function normalizeId(raw) {
  return String(raw || '').trim();
}

function getAllowedSetFromSelectedSystems(fieldKey) {
  if (bubbleState.domain !== 'games' || bubbleState.selectedSystems.size === 0) {
    return null;
  }
  const sysList = Array.isArray(bubbleState.catalog.systems) ? bubbleState.catalog.systems : [];
  const selectedLower = new Set(Array.from(bubbleState.selectedSystems).map(s => s.toLowerCase()));
  const allowed = new Set();
  for (const sysObj of sysList) {
    if (!sysObj || !selectedLower.has(String(sysObj.id || '').toLowerCase())) continue;
    const rel = fieldKey === 'genres'
      ? (sysObj.supported_genres || sysObj.genres)
      : (sysObj.supported_decades || sysObj.decades);
    if (Array.isArray(rel)) {
      rel.forEach(id => allowed.add(String(id).toLowerCase()));
    }
  }
  return allowed.size > 0 ? allowed : null;
}

function filterItemsForGroup(group) {
  const rawItems = Array.isArray(bubbleState.catalog[group]) ? bubbleState.catalog[group] : [];
  let items = rawItems;

  if (group === 'genres' || group === 'decades') {
    const allowed = getAllowedSetFromSelectedSystems(group);
    if (allowed) {
      items = items.filter(it => allowed.has(String(it.id || '').toLowerCase()));
    }
  }

  // Prune any selected token no longer present in the full group catalog (before text search)
  const targetSet = getTargetSet(group);
  const validLower = new Set(items.map(it => String(it.id || '').toLowerCase()));
  for (const tok of Array.from(targetSet)) {
    if (!validLower.has(tok.toLowerCase())) {
      targetSet.delete(tok);
    }
  }

  const q = String(bubbleState.searchQuery[group] || '').trim().toLowerCase();
  const visible = q
    ? items.filter(it =>
        String(it.label || '').toLowerCase().includes(q) ||
        String(it.id || '').toLowerCase().includes(q)
      )
    : items;

  return { totalAvailable: items.length, visible };
}

function getTargetSet(group) {
  if (group === 'systems') return bubbleState.selectedSystems;
  if (group === 'decades') return bubbleState.selectedDecades;
  return bubbleState.selectedGenres;
}

function renderOneBubbleGroup(group) {
  const boxEl = document.getElementById(`batch-bubbles-${group}`);
  const statusEl = document.getElementById(`batch-bubbles-status-${group}`);
  const countPillEl = document.getElementById(`batch-bubbles-count-${group}`);
  if (!boxEl) return;

  if (group === 'systems' && bubbleState.domain !== 'games') {
    boxEl.innerHTML = '';
    return;
  }

  const { totalAvailable, visible } = filterItemsForGroup(group);
  const targetSet = getTargetSet(group);
  const isFilteredBySys = (
    bubbleState.domain === 'games' &&
    bubbleState.selectedSystems.size > 0 &&
    (group === 'genres' || group === 'decades')
  );

  if (statusEl) {
    statusEl.textContent = totalAvailable > 0
      ? t(isFilteredBySys ? 'batch_ref_avail_filtered' : 'batch_ref_avail_all', { n: totalAvailable })
      : t('batch_ref_empty');
  }

  if (countPillEl) {
    countPillEl.textContent = targetSet.size > 0
      ? t('batch_bubbles_selected_fmt', { n: targetSet.size })
      : t('batch_bubbles_all_active');
    countPillEl.className = targetSet.size > 0 ? 'pill info' : 'tag-all-pill';
  }

  const variantCls = group === 'genres' ? ' genre' : (group === 'decades' ? ' decade' : '');
  if (!visible.length) {
    boxEl.innerHTML = `<span style="font-size:0.78rem;color:var(--muted);padding:4px 6px">${esc(t('batch_ref_empty'))}</span>`;
    return;
  }

  boxEl.innerHTML = visible.map(it => {
    const id = normalizeId(it.id || it.value);
    if (!id) return '';
    const label = it.label || id;
    const isSel = targetSet.has(id);
    const safeId = esc(id).replace(/'/g, "\\'");
    const checkPrefix = isSel ? '<span class="batch-bubble-chk">✓</span>' : '';
    const titleAttr = it.category ? `${label} (${it.category})` : `${label} (${id})`;
    return (
      `<button type="button" class="batch-bubble${variantCls}${isSel ? ' active' : ''}" ` +
      `data-id="${esc(id)}" onclick="toggleBatchBubble('${group}','${safeId}')" title="${esc(titleAttr)}">` +
      `${checkPrefix}<span>${esc(label)}</span></button>`
    );
  }).join('');
}

export function renderAllBatchBubbles() {
  const sysField = document.getElementById('batch-systems-field');
  if (sysField) {
    sysField.style.display = bubbleState.domain === 'games' ? '' : 'none';
  }
  const decField = document.getElementById('batch-decades-field');
  const hasDecades = Array.isArray(bubbleState.catalog.decades) && bubbleState.catalog.decades.length > 0;
  if (decField) {
    decField.style.display = hasDecades ? '' : 'none';
  }
  renderOneBubbleGroup('systems');
  renderOneBubbleGroup('genres');
  renderOneBubbleGroup('decades');
}

export function setBatchFilterCatalog(domain, catalog) {
  const prevDomain = bubbleState.domain;
  bubbleState.domain = domain || 'games';
  bubbleState.catalog = {
    systems: Array.isArray(catalog?.systems) ? catalog.systems : [],
    genres: Array.isArray(catalog?.genres) ? catalog.genres : [],
    decades: Array.isArray(catalog?.decades) ? catalog.decades : []
  };
  if (prevDomain !== bubbleState.domain) {
    bubbleState.selectedGenres.clear();
    bubbleState.selectedDecades.clear();
  }
  renderAllBatchBubbles();
}

export function toggleBatchBubble(group, id) {
  const clean = normalizeId(id);
  if (!clean) return;
  const targetSet = getTargetSet(group);
  if (targetSet.has(clean)) {
    targetSet.delete(clean);
  } else {
    targetSet.add(clean);
  }
  if (group === 'systems') {
    renderAllBatchBubbles();
  } else {
    renderOneBubbleGroup(group);
  }
}

export function clearBatchBubbles(group) {
  const targetSet = getTargetSet(group);
  targetSet.clear();
  const searchEl = document.getElementById(`batch-bubble-search-${group}`);
  if (searchEl) searchEl.value = '';
  bubbleState.searchQuery[group] = '';
  if (group === 'systems') {
    renderAllBatchBubbles();
  } else {
    renderOneBubbleGroup(group);
  }
}

export function onBatchBubbleSearch(group, query) {
  bubbleState.searchQuery[group] = String(query || '');
  renderOneBubbleGroup(group);
}

export function getSelectedBatchFilters() {
  const systems = bubbleState.domain === 'games'
    ? (bubbleState.selectedSystems.size > 0 ? Array.from(bubbleState.selectedSystems) : ['all'])
    : [];
  return {
    systems,
    genres: Array.from(bubbleState.selectedGenres),
    decades: Array.from(bubbleState.selectedDecades)
  };
}
