import { apiFetch, esc } from '../api.js';
import { t } from '../i18n/index.js';
import {
  CANONICAL_DOMAINS,
  defaultPlaylistRows,
  defaultStudioStyleForDomain,
  filterCatalogCache,
  normalizeDomainStylesObj,
  preloadAllDomainFilterCatalogs,
  shortDomainDisplayName,
  toUniqueTokenArray,
  uiState
} from '../state.js';
import { jumpToQueueStudio } from './overview.js';
import { loadHealthAndConfig } from './runtime_config.js';
import { openStudioForDmd, updateStudioDomainTabsUi } from './studio_controls.js';

export function hydrateEditingProfileSlots(playlist, lang) {
  const byDom = {};
  (playlist || []).forEach(s => { if (s && s.domain) byDom[s.domain] = s; });
  const defaults = {};
  defaultPlaylistRows(lang).forEach(d => { defaults[d.domain] = d; });
  const nextSlots = {};
  CANONICAL_DOMAINS.forEach(dom => {
    const src = byDom[dom] || defaults[dom];
    nextSlots[dom] = {
      enabled: src.enabled !== false,
      domain: dom,
      count: Math.max(1, Math.min(10, parseInt(src.count, 10) || 1)),
      systems: dom === 'games' ? toUniqueTokenArray(src.systems) : [],
      genres: toUniqueTokenArray(src.genres),
      decades: toUniqueTokenArray(src.decades),
      min_rating: Number(src.min_rating != null ? src.min_rating : 6.0),
      engine_dmd_id: src.engine_dmd_id || null,
      transport: src.transport || null
    };
  });
  uiState.editingProfileSlots = nextSlots;
}

export function applyProfileEditorReadOnlyUi(isReadOnly, livePayload) {
  uiState.editingProfileReadOnly = Boolean(isReadOnly);
  uiState.editingProfileLivePayload = livePayload || null;
  const card = document.getElementById('dmd-profile-editor-card');
  if (card) card.classList.toggle('is-readonly', uiState.editingProfileReadOnly);

  const titleSpan = document.getElementById('prof-editor-title-span');
  if (titleSpan) {
    const key = uiState.editingProfileReadOnly ? 'prof_readonly_title' : 'prof_editor_title';
    titleSpan.setAttribute('data-i18n', key);
    titleSpan.textContent = t(key);
  }

  const descP = document.getElementById('prof-editor-desc-p');
  if (descP) descP.style.display = uiState.editingProfileReadOnly ? 'none' : '';
  const hintBox = document.getElementById('prof-rotation-hint-box');
  if (hintBox) hintBox.style.display = uiState.editingProfileReadOnly ? 'none' : '';
  const roBanner = document.getElementById('prof-readonly-banner');
  if (roBanner) roBanner.style.display = uiState.editingProfileReadOnly ? 'block' : 'none';

  [
    'prof-dmd-id',
    'prof-display-name',
    'prof-mode',
    'prof-lang',
    'prof-res',
    'prof-always-udp',
    'prof-udp-ip',
    'prof-udp-port',
    'prof-gap-s'
  ].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = uiState.editingProfileReadOnly;
  });

  ['btn-save-prof', 'btn-del-prof'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) {
      btn.disabled = uiState.editingProfileReadOnly;
      btn.style.display = uiState.editingProfileReadOnly ? 'none' : '';
    }
  });

  const jsonWrap = document.getElementById('prof-mqtt-json-wrap');
  const jsonPre = document.getElementById('prof-mqtt-json-pre');
  if (jsonWrap && jsonPre) {
    if (uiState.editingProfileReadOnly && uiState.editingProfileLivePayload) {
      jsonPre.textContent = JSON.stringify(uiState.editingProfileLivePayload, null, 2);
      jsonWrap.style.display = 'block';
    } else {
      jsonPre.textContent = '';
      jsonWrap.style.display = 'none';
    }
  }
}

export function jumpToQueueFromInspector() {
  if (uiState.editingProfileDmdId) jumpToQueueStudio(uiState.editingProfileDmdId);
}

export function openStudioFromInspector() {
  if (uiState.editingProfileDmdId) openStudioForDmd(uiState.editingProfileDmdId);
}

export function onPlSlotToggle(dom, checked) {
  if (uiState.editingProfileReadOnly) return;
  if (uiState.editingProfileSlots[dom]) {
    uiState.editingProfileSlots[dom].enabled = Boolean(checked);
  }
}

export function onPlSlotCount(dom, val) {
  if (uiState.editingProfileReadOnly) return;
  if (uiState.editingProfileSlots[dom]) {
    uiState.editingProfileSlots[dom].count = Math.max(1, Math.min(10, parseInt(val, 10) || 1));
  }
}

export function removeSlotTag(dom, field, tagId) {
  if (uiState.editingProfileReadOnly) return;
  const slot = uiState.editingProfileSlots[dom];
  if (!slot || !Array.isArray(slot[field])) return;
  slot[field] = slot[field].filter(x => x !== tagId);
  renderPlaylistEditorRows();
}

export function summarizeDomainStudioStyleBadges(st) {
  const s = st || defaultStudioStyleForDomain('games');
  const cardTag = s.include_card_intro !== false ? s.card_layout : 'card:off';
  const vidTag = s.include_video !== false
    ? `🎬 ≤${Number(s.video_max_duration_s != null ? s.video_max_duration_s : 10).toFixed(0)}s`
    : '🎬 OFF';
  const snapCnt = Number(s.snapshot_panorama_count || 0);
  const snapTag = snapCnt > 0 ? `<span class="pill info">📸×${snapCnt}</span>` : '';
  return `
    <span class="pill info">${esc(cardTag)}</span>
    <span class="pill info">${esc(s.card_theme || 'tinted')}</span>
    <span class="pill warn">${esc(s.color_boost || 'auto')} • ${esc(s.video_mode || 'auto_action')}</span>
    <span class="pill ok">${esc(vidTag)}</span>
    ${snapTag}
  `;
}

export function summarizeLiveClientDomainBadges(st) {
  const s = st || defaultStudioStyleForDomain('games');
  const cardPill = uiState.editingProfileSupportsLocalCard
    ? `<span class="pill info">${esc(t('pill_local_card_60fps'))}</span>`
    : `<span class="pill info">${esc(s.card_layout || 'classic_split')}</span>`;
  const vidTag = s.include_video !== false
    ? `🎬 ≤${Number(s.video_max_duration_s != null ? s.video_max_duration_s : 10).toFixed(0)}s @ ${esc(s.video_fps || 15)}fps`
    : '🎬 OFF';
  const snapCnt = Number(s.snapshot_panorama_count || 0);
  const snapTag = snapCnt > 0 ? `<span class="pill info">📸×${snapCnt}</span>` : '';
  return `
    ${cardPill}
    <span class="pill warn">${esc(s.color_boost || 'auto')} • ${esc(s.video_mode || 'auto_action')}</span>
    <span class="pill ok">${esc(vidTag)}</span>
    ${snapTag}
  `;
}

export function renderPlaylistEditorRows() {
  const tb = document.getElementById('prof-playlist-tbody');
  if (!tb) return;
  if (!Object.keys(uiState.editingProfileSlots).length) {
    hydrateEditingProfileSlots(defaultPlaylistRows('fr'), 'fr');
  }
  const profLang = (document.getElementById('prof-lang') || {}).value || 'fr';
  const maxVisibleChips = 4;
  const ro = uiState.editingProfileReadOnly;

  tb.innerHTML = CANONICAL_DOMAINS.map(dom => {
    const s = uiState.editingProfileSlots[dom] || {enabled: false, domain: dom, count: 1, systems: [], genres: [], decades: []};
    const isEnabled = s.enabled !== false;
    const isGames = dom === 'games';
    const cat = filterCatalogCache.get(`${dom}:${profLang}`);
    const sysList = isGames ? (s.systems || []) : [];
    const genList = s.genres || [];
    const decList = s.decades || [];

    let sysCellHtml = '<span style="color:var(--muted);font-size:0.82rem">—</span>';
    if (isGames && (!ro || isEnabled)) {
      let chips = '';
      if (!sysList.length) {
        chips = `<span class="tag-all-pill">${esc(t('tag_all_systems'))}</span>`;
      } else {
        chips = sysList.slice(0, maxVisibleChips).map(id => {
          const lbl = (cat && cat.sysMap.get(id)) || id;
          const safeId = esc(id).replace(/'/g, "\\'");
          const rmBtn = ro
            ? ''
            : `<button type="button" class="tag-chip-rm" onclick="removeSlotTag('games','systems','${safeId}')">✕</button>`;
          return `<span class="tag-chip" title="${esc(id)}">${esc(lbl)}${rmBtn}</span>`;
        }).join('');
        if (sysList.length > maxVisibleChips) {
          chips += `<span class="pill info">${esc(t('tag_more_fmt', {n: sysList.length - maxVisibleChips}))}</span>`;
        }
      }
      const pickBtn = ro
        ? ''
        : `<button type="button" class="tag-pick-btn" onclick="openTagPickerModal('games','systems')">${esc(sysList.length ? t('btn_edit_tags') : t('btn_pick_tags'))}</button>`;
      sysCellHtml = `<div class="tag-cell">${chips}${pickBtn}</div>`;
    }

    let genCellHtml = '<span style="color:var(--muted);font-size:0.82rem">—</span>';
    if (!ro || isEnabled) {
      const combinedGenDec = [
        ...genList.map(id => ({field: 'genres', id, isDec: false, lbl: (cat && cat.genMap.get(id)) || id})),
        ...decList.map(id => ({field: 'decades', id, isDec: true, lbl: '📅 ' + ((cat && cat.decMap.get(id)) || id)}))
      ];
      let genChips = '';
      if (!combinedGenDec.length) {
        genChips = `<span class="tag-all-pill">${esc(t('tag_all_genres'))}</span>`;
      } else {
        genChips = combinedGenDec.slice(0, maxVisibleChips).map(it => {
          const safeId = esc(it.id).replace(/'/g, "\\'");
          const cls = it.isDec ? 'tag-chip decade' : 'tag-chip';
          const rmBtn = ro
            ? ''
            : `<button type="button" class="tag-chip-rm" onclick="removeSlotTag('${dom}','${it.field}','${safeId}')">✕</button>`;
          return `<span class="${cls}" title="${esc(it.id)}">${esc(it.lbl)}${rmBtn}</span>`;
        }).join('');
        if (combinedGenDec.length > maxVisibleChips) {
          genChips += `<span class="pill info">${esc(t('tag_more_fmt', {n: combinedGenDec.length - maxVisibleChips}))}</span>`;
        }
      }
      const genPickBtn = ro
        ? ''
        : `<button type="button" class="tag-pick-btn" onclick="openTagPickerModal('${dom}','genres_decades')">${esc(combinedGenDec.length ? t('btn_edit_tags') : t('btn_pick_tags'))}</button>`;
      genCellHtml = `<div class="tag-cell">${genChips}${genPickBtn}</div>`;
    }

    const domStyle = (uiState.editingProfileDomainStyles && uiState.editingProfileDomainStyles[dom]) || defaultStudioStyleForDomain(dom);
    let studioCellHtml = '<span style="color:var(--muted);font-size:0.82rem">—</span>';
    if (ro) {
      if (isEnabled) {
        const targetEng = esc(s.engine_dmd_id || uiState.editingProfileDmdId).replace(/'/g, "\\'");
        const simBtnText = t('btn_sim_dom_queue', {d: dom.toUpperCase()});
        studioCellHtml = `<div class="studio-link-cell">
          <div class="studio-summary-badges">${summarizeLiveClientDomainBadges(domStyle)}</div>
          <button type="button" class="studio-jump-btn" onclick="openStudioForDmdDomainFromEditor('${dom}', '${targetEng}')">${esc(simBtnText)}</button>
        </div>`;
      }
    } else {
      const studioBtnText = t('btn_open_dom_studio', {d: dom.toUpperCase()});
      studioCellHtml = `<div class="studio-link-cell">
        <div class="studio-summary-badges">${summarizeDomainStudioStyleBadges(domStyle)}</div>
        <button type="button" class="studio-jump-btn" onclick="openStudioForDmdDomainFromEditor('${dom}')">${esc(studioBtnText)}</button>
      </div>`;
    }

    let domSubHtml = '';
    if (ro) {
      if (isEnabled && s.engine_dmd_id) {
        const trPart = s.transport ? ` • ${esc(s.transport)}` : '';
        domSubHtml = `<div style="margin-top:3px"><code style="font-size:0.72rem;color:var(--cyan)">${esc(s.engine_dmd_id)}</code><small style="color:var(--muted)">${trPart}</small></div>`;
      } else if (!isEnabled) {
        domSubHtml = `<div style="margin-top:3px"><span class="pill" style="background:#141b26;color:var(--muted);border:1px solid #263246">${esc(t('pill_dom_not_instantiated'))}</span></div>`;
      }
    }

    const countCellHtml = ro
      ? (isEnabled
        ? `<span class="pill" style="background:#111824;color:var(--muted);border:1px solid #263246">${esc(t('pill_dmd_driven_turn'))}</span>`
        : '<span style="color:var(--muted);font-size:0.82rem">—</span>')
      : `<input type="number" class="pl-count" min="1" max="10" value="${esc(s.count || 1)}" style="width:70px" onchange="onPlSlotCount('${dom}', this.value)" />`;

    const trCls = (ro && !isEnabled) ? ' class="ro-dom-inactive"' : '';
    const chkDis = ro ? ' disabled' : '';

    return `<tr data-pl-domain="${esc(dom)}"${trCls}>
      <td><input type="checkbox" class="pl-enabled" style="width:20px;height:20px" ${isEnabled ? 'checked' : ''}${chkDis} onchange="onPlSlotToggle('${dom}', this.checked)" /></td>
      <td><b>${esc(shortDomainDisplayName(dom))}</b>${domSubHtml}</td>
      <td>${countCellHtml}</td>
      <td>${sysCellHtml}</td>
      <td>${genCellHtml}</td>
      <td>${studioCellHtml}</td>
    </tr>`;
  }).join('');
}

export async function onProfileLangChange() {
  const lang = (document.getElementById('prof-lang') || {}).value || 'fr';
  await preloadAllDomainFilterCatalogs(lang);
  renderPlaylistEditorRows();
}

export async function openProfileEditor(dmdId) {
  const card = document.getElementById('dmd-profile-editor-card');
  if (!card) return;
  card.style.display = 'block';
  document.getElementById('prof-status-msg').textContent = '';

  let prof = null;
  let isReadOnly = false;
  let livePayload = null;
  if (dmdId) {
    try {
      const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`);
      if (r.ok) {
        const d = await r.json();
        prof = d.profile;
        isReadOnly = Boolean(
          d.read_only_client_managed || (prof && prof.config_source === 'client')
        );
        livePayload = d.client_live_payload || null;
      }
    } catch (_) {}
  }
  if (!prof) {
    isReadOnly = false;
    livePayload = null;
    const defLang = (uiState.lastServerConfig && uiState.lastServerConfig.default_lang) || 'fr';
    prof = {
      dmd_id: dmdId || 'esp32_passive_1',
      display_name: dmdId || 'ESP32 Passive DMD',
      config_source: 'server',
      supports_local_card: false,
      always_stream_udp: false,
      width: 128,
      height: 32,
      udp_ip: '',
      udp_port: 4210,
      lang: defLang,
      inter_item_gap_s: 1.0,
      domain_styles: normalizeDomainStylesObj(null, null),
      playlist: defaultPlaylistRows(defLang)
    };
  }
  uiState.editingProfileDmdId = prof.dmd_id || '';
  uiState.editingProfileSupportsLocalCard = prof.supports_local_card !== false;
  uiState.editingProfileCardStyle = prof.card_style || null;
  uiState.editingProfileDomainStyles = normalizeDomainStylesObj(prof.domain_styles, prof.card_style);
  document.getElementById('prof-dmd-id').value = prof.dmd_id || '';
  document.getElementById('prof-display-name').value = prof.display_name || prof.dmd_id || '';
  const modeVal = prof.config_source === 'client'
    ? 'client'
    : (prof.supports_local_card === false ? 'server_passive' : 'server_hybrid');
  document.getElementById('prof-mode').value = modeVal;
  const profLang = prof.lang || 'fr';
  document.getElementById('prof-lang').value = profLang;
  const resVal = `${prof.width || 128}x${prof.height || 32}`;
  const resEl = document.getElementById('prof-res');
  if (!Array.from(resEl.options).some(o => o.value === resVal)) {
    resEl.insertAdjacentHTML('beforeend', `<option value="${esc(resVal)}">${esc(prof.width || 128)}×${esc(prof.height || 32)}</option>`);
  }
  resEl.value = resVal;
  document.getElementById('prof-always-udp').value = String(Boolean(prof.always_stream_udp));
  document.getElementById('prof-udp-ip').value = prof.udp_ip || '';
  document.getElementById('prof-udp-port').value = String(prof.udp_port || 4210);
  document.getElementById('prof-gap-s').value = String(prof.inter_item_gap_s != null ? prof.inter_item_gap_s : 1.0);
  applyProfileEditorReadOnlyUi(isReadOnly, livePayload);
  hydrateEditingProfileSlots(prof.playlist || defaultPlaylistRows(profLang), profLang);
  await preloadAllDomainFilterCatalogs(profLang);
  renderPlaylistEditorRows();
  card.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

export function closeProfileEditor() {
  const card = document.getElementById('dmd-profile-editor-card');
  if (card) card.style.display = 'none';
}

export async function saveDmdProfile() {
  if (uiState.editingProfileReadOnly) return;
  const dmdId = document.getElementById('prof-dmd-id').value.trim();
  const msgEl = document.getElementById('prof-status-msg');
  if (!dmdId) {
    msgEl.style.color = 'var(--red)';
    msgEl.textContent = t('msg_prof_id_req');
    return;
  }
  const modeSel = document.getElementById('prof-mode').value;
  const configSource = modeSel === 'client' ? 'client' : 'server';
  const supportsLocalCard = modeSel !== 'server_passive';
  const [w, h] = document.getElementById('prof-res').value.split('x').map(Number);
  const lang = document.getElementById('prof-lang').value || 'fr';

  const playlist = CANONICAL_DOMAINS.map(dom => {
    const s = uiState.editingProfileSlots[dom] || {enabled: false, count: 1, systems: [], genres: [], decades: []};
    const ds = (uiState.editingProfileDomainStyles && uiState.editingProfileDomainStyles[dom]) || defaultStudioStyleForDomain(dom);
    return {
      enabled: Boolean(s.enabled),
      domain: dom,
      count: Math.max(1, Math.min(10, parseInt(s.count, 10) || 1)),
      lang: lang,
      systems: dom === 'games' ? toUniqueTokenArray(s.systems) : [],
      genres: toUniqueTokenArray(s.genres),
      decades: toUniqueTokenArray(s.decades),
      min_rating: Number(s.min_rating != null ? s.min_rating : 6.0),
      video_type: ds.video_type || 'any',
      jacket_panorama: ds.jacket_panorama !== false,
      marquee_panorama: Boolean(ds.marquee_panorama),
      snapshot_panorama_count: Number(ds.snapshot_panorama_count != null ? ds.snapshot_panorama_count : 1),
      panorama_duration_s: Number(ds.panorama_duration_s != null ? ds.panorama_duration_s : 3.5),
      include_video: ds.include_video !== false,
      video_max_duration_s: Number(ds.video_max_duration_s != null ? ds.video_max_duration_s : 10.0),
      video_fps: Number(ds.video_fps != null ? ds.video_fps : 15),
      video_mode: ds.video_mode || 'auto_action',
      color_boost: ds.color_boost || 'auto'
    };
  });

  const payload = {
    dmd_id: dmdId,
    display_name: document.getElementById('prof-display-name').value.trim() || dmdId,
    config_source: configSource,
    supports_local_card: supportsLocalCard,
    always_stream_udp: document.getElementById('prof-always-udp').value === 'true',
    width: w || 128,
    height: h || 32,
    udp_ip: document.getElementById('prof-udp-ip').value.trim(),
    udp_port: Math.max(1, Math.min(65535, parseInt(document.getElementById('prof-udp-port').value, 10) || 4210)),
    lang: lang,
    inter_item_gap_s: Math.max(0, Math.min(10, parseFloat(document.getElementById('prof-gap-s').value) || 0)),
    domain_styles: uiState.editingProfileDomainStyles,
    card_style: (uiState.editingProfileDomainStyles && uiState.editingProfileDomainStyles.games) || uiState.editingProfileCardStyle || undefined,
    playlist: playlist
  };

  try {
    const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    uiState.editingProfileDmdId = dmdId;
    if (uiState.studioLoadedDmdId === dmdId) {
      uiState.studioDraftDomainStyles = normalizeDomainStylesObj(uiState.editingProfileDomainStyles, null);
      updateStudioDomainTabsUi();
    }
    msgEl.style.color = 'var(--green)';
    msgEl.textContent = t('msg_prof_saved', {d: dmdId});
    loadHealthAndConfig(false);
  } catch (err) {
    msgEl.style.color = 'var(--red)';
    msgEl.textContent = '⚠️ ' + (err.message || String(err));
  }
}

export async function deleteDmdProfile() {
  if (uiState.editingProfileReadOnly) return;
  const dmdId = document.getElementById('prof-dmd-id').value.trim();
  const msgEl = document.getElementById('prof-status-msg');
  if (!dmdId) return;
  try {
    const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/profile`, {method: 'DELETE'});
    if (!r.ok && r.status !== 404) throw new Error('HTTP ' + r.status);
    msgEl.style.color = 'var(--green)';
    msgEl.textContent = t('msg_prof_deleted', {d: dmdId});
    closeProfileEditor();
    loadHealthAndConfig(false);
  } catch (err) {
    msgEl.style.color = 'var(--red)';
    msgEl.textContent = '⚠️ ' + (err.message || String(err));
  }
}
