import { esc } from './api.js';
import { t } from './i18n/index.js';
import { ensureDomainFilterCatalog, shortDomainDisplayName, uiState } from './state.js';
import { renderPlaylistEditorRows } from './tabs/profile_editor.js';

export async function openTagPickerModal(dom, mode) {
  if (uiState.editingProfileReadOnly) return;
  const lang = (document.getElementById('prof-lang') || {}).value || 'fr';
  const cat = await ensureDomainFilterCatalog(dom, lang);
  const slot = uiState.editingProfileSlots[dom] || {systems: [], genres: [], decades: []};
  uiState.tagModalState = {
    open: true,
    domain: dom,
    mode: mode,
    catalog: cat,
    selectedSystems: new Set(slot.systems || []),
    selectedGenres: new Set(slot.genres || []),
    selectedDecades: new Set(slot.decades || [])
  };
  const titleEl = document.getElementById('tag-modal-title');
  if (titleEl) {
    titleEl.textContent = mode === 'systems'
      ? t('tag_modal_sys_title')
      : t('tag_modal_gen_title', {d: shortDomainDisplayName(dom)});
  }
  const searchEl = document.getElementById('tag-modal-search');
  if (searchEl) searchEl.value = '';
  renderTagPickerOptions();
  const modal = document.getElementById('tag-picker-modal');
  if (modal) modal.classList.add('open');
  if (searchEl) setTimeout(() => searchEl.focus(), 30);
}

export function closeTagPickerModal() {
  uiState.tagModalState.open = false;
  const modal = document.getElementById('tag-picker-modal');
  if (modal) modal.classList.remove('open');
}

export function filterTagItemsByQuery(items, q) {
  if (!q) return items;
  const low = q.toLowerCase();
  return items.filter(it =>
    String(it.label || '').toLowerCase().includes(low) ||
    String(it.id || '').toLowerCase().includes(low)
  );
}

export function renderTagPickerOptions() {
  const body = document.getElementById('tag-modal-body');
  const countEl = document.getElementById('tag-modal-count');
  if (!body) return;
  const q = ((document.getElementById('tag-modal-search') || {}).value || '').trim();
  const ms = uiState.tagModalState;

  let html = '';
  let totalSelected = 0;

  if (ms.mode === 'systems') {
    totalSelected = ms.selectedSystems.size;
    const visSys = filterTagItemsByQuery(ms.catalog.systems || [], q);
    html += `<div class="tag-group-title">${esc(t('tag_sec_systems'))} (${visSys.length})</div>`;
    html += `<div class="tag-options-grid">` + visSys.map(it => {
      const sel = ms.selectedSystems.has(it.id);
      const safeId = esc(it.id).replace(/'/g, "\\'");
      return `<button type="button" class="tag-option-btn${sel ? ' selected' : ''}" onclick="toggleTagModalItem('systems','${safeId}')" title="${esc(it.id)}">
        <span class="tag-opt-check">${sel ? '✓' : ''}</span>
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(it.label)} <small style="color:var(--muted)">(${esc(it.id)})</small></span>
      </button>`;
    }).join('') + `</div>`;
  } else {
    totalSelected = ms.selectedGenres.size + ms.selectedDecades.size;
    const visGen = filterTagItemsByQuery(ms.catalog.genres || [], q);
    const visDec = filterTagItemsByQuery(ms.catalog.decades || [], q);

    html += `<div class="tag-group-title">${esc(t('tag_sec_genres'))} (${visGen.length})</div>`;
    html += `<div class="tag-options-grid">` + visGen.map(it => {
      const sel = ms.selectedGenres.has(it.id);
      const safeId = esc(it.id).replace(/'/g, "\\'");
      return `<button type="button" class="tag-option-btn${sel ? ' selected' : ''}" onclick="toggleTagModalItem('genres','${safeId}')" title="${esc(it.id)}">
        <span class="tag-opt-check">${sel ? '✓' : ''}</span>
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(it.label)}</span>
      </button>`;
    }).join('') + `</div>`;

    if ((ms.catalog.decades || []).length) {
      html += `<div class="tag-group-title">${esc(t('tag_sec_decades'))} (${visDec.length})</div>`;
      html += `<div class="tag-options-grid">` + visDec.map(it => {
        const sel = ms.selectedDecades.has(it.id);
        const safeId = esc(it.id).replace(/'/g, "\\'");
        return `<button type="button" class="tag-option-btn${sel ? ' selected' : ''}" onclick="toggleTagModalItem('decades','${safeId}')" title="${esc(it.id)}">
          <span class="tag-opt-check">${sel ? '✓' : ''}</span>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📅 ${esc(it.label)}</span>
        </button>`;
      }).join('') + `</div>`;
    }
  }

  body.innerHTML = html;
  if (countEl) {
    countEl.textContent = t('tag_selected_count', {n: totalSelected});
  }
}

export function toggleTagModalItem(group, id) {
  const ms = uiState.tagModalState;
  const targetSet = group === 'systems'
    ? ms.selectedSystems
    : (group === 'decades' ? ms.selectedDecades : ms.selectedGenres);
  if (targetSet.has(id)) targetSet.delete(id);
  else targetSet.add(id);
  renderTagPickerOptions();
}

export function clearAllTagModal() {
  const ms = uiState.tagModalState;
  if (ms.mode === 'systems') {
    ms.selectedSystems.clear();
  } else {
    ms.selectedGenres.clear();
    ms.selectedDecades.clear();
  }
  renderTagPickerOptions();
}

export function selectVisibleTagModal() {
  const ms = uiState.tagModalState;
  const q = ((document.getElementById('tag-modal-search') || {}).value || '').trim();
  if (ms.mode === 'systems') {
    filterTagItemsByQuery(ms.catalog.systems || [], q).forEach(it => ms.selectedSystems.add(it.id));
  } else {
    filterTagItemsByQuery(ms.catalog.genres || [], q).forEach(it => ms.selectedGenres.add(it.id));
    filterTagItemsByQuery(ms.catalog.decades || [], q).forEach(it => ms.selectedDecades.add(it.id));
  }
  renderTagPickerOptions();
}

export function applyTagPickerModal() {
  const ms = uiState.tagModalState;
  const dom = ms.domain;
  if (!uiState.editingProfileSlots[dom]) return;
  if (ms.mode === 'systems') {
    uiState.editingProfileSlots[dom].systems = Array.from(ms.selectedSystems);
  } else {
    uiState.editingProfileSlots[dom].genres = Array.from(ms.selectedGenres);
    uiState.editingProfileSlots[dom].decades = Array.from(ms.selectedDecades);
  }
  closeTagPickerModal();
  renderPlaylistEditorRows();
}

export function bindTagModalEscapeListener() {
  if (typeof document === 'undefined') return;
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape' && uiState.tagModalState.open) {
      closeTagPickerModal();
    }
  });
}
