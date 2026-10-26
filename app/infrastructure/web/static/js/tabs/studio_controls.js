import { apiFetch, esc } from '../api.js';
import { getCurrentUiLang, t } from '../i18n/index.js';
import { startStudioAnimationLoop } from '../simulator.js';
import {
  CANONICAL_DOMAINS,
  STUDIO_DEMO_FALLBACKS,
  defaultStudioStyleForDomain,
  ensureDomainFilterCatalog,
  normalizeDomainStylesObj,
  uiState
} from '../state.js';
import { switchTab, syncTargetDmdSelector } from './overview.js';
import { renderPlaylistEditorRows } from './profile_editor.js';
import {
  loadStudioDmdQueue,
  onStudioTargetDmdChange,
  renderStudioActiveDmdBanner,
  requestStudioCardPreviewNow,
  syncStudioResFromActiveDmd
} from './studio_actions.js';

export function getActiveStudioCandidate() {
  if (uiState.studioActiveCandIdx >= 0 && uiState.studioCandidates[uiState.studioActiveCandIdx]) {
    return uiState.studioCandidates[uiState.studioActiveCandIdx];
  }
  const dom = (document.getElementById('studio-domain') || {}).value || 'games';
  return STUDIO_DEMO_FALLBACKS[dom] || STUDIO_DEMO_FALLBACKS.games;
}

export function getStudioAccentValue() {
  const modeEl = document.getElementById('studio-accent-mode');
  const picker = document.getElementById('studio-accent-picker');
  if (modeEl && modeEl.value === 'custom' && picker) {
    return picker.value || '#ff6d00';
  }
  return 'auto';
}

export function captureCurrentControlsIntoStudioStyle() {
  const fps = parseInt((document.getElementById('st-vid-fps') || {}).value || '15', 10) || 15;
  const st = {
    include_card_intro: document.getElementById('st-inc-card').checked,
    card_layout: uiState.studioCardLayout,
    card_theme: document.getElementById('studio-theme').value,
    card_font: document.getElementById('studio-font').value,
    card_accent_color: getStudioAccentValue(),
    include_poster: document.getElementById('st-inc-poster').checked,
    show_badge: document.getElementById('st-show-badge').checked,
    show_year: document.getElementById('st-show-year').checked,
    show_rating: document.getElementById('st-show-rating').checked,
    include_synopsis: document.getElementById('st-inc-synopsis').checked,
    text_scroll_speed: String(document.getElementById('st-scroll-speed').value || '4'),
    card_hold_s: parseFloat(document.getElementById('st-card-hold').value || '0.9'),
    jacket_panorama: document.getElementById('st-jacket-pan').checked,
    marquee_panorama: document.getElementById('st-marquee-pan').checked,
    snapshot_panorama_count: parseInt(document.getElementById('st-snap-count').value || '0', 10) || 0,
    panorama_duration_s: Math.max(1.5, Math.min(15.0, parseFloat(document.getElementById('st-pan-dur').value || '3.5') || 3.5)),
    include_video: document.getElementById('st-inc-video').checked,
    video_type: (document.getElementById('st-video-type') || {}).value || 'any',
    video_max_duration_s: Math.max(1.5, Math.min(60.0, parseFloat(document.getElementById('st-vid-dur').value || '8.0') || 8.0)),
    video_fps: fps,
    video_mode: document.getElementById('st-video-mode').value,
    color_boost: document.getElementById('st-color-boost').value
  };
  if (uiState.studioActiveDomain && CANONICAL_DOMAINS.includes(uiState.studioActiveDomain)) {
    uiState.studioDraftDomainStyles[uiState.studioActiveDomain] = Object.assign({}, st);
  }
  return st;
}

export function updateStudioSliderLabels() {
  const spEl = document.getElementById('st-scroll-speed');
  const spLbl = document.getElementById('lbl-val-scroll');
  if (spEl && spLbl) {
    const v = parseInt(spEl.value, 10) || 4;
    const tag = v <= 2 ? 'Slow' : (v <= 5 ? 'Normal' : (v <= 8 ? 'Fast' : 'Turbo'));
    spLbl.textContent = `${v} (${tag})`;
  }
  const holdEl = document.getElementById('st-card-hold');
  const holdLbl = document.getElementById('lbl-val-hold');
  if (holdEl && holdLbl) {
    holdLbl.textContent = `${parseFloat(holdEl.value || '0.9').toFixed(1)}s`;
  }
}

export function applyStudioStyleToControls(styleObj) {
  const s = Object.assign(defaultStudioStyleForDomain(uiState.studioActiveDomain || 'games'), styleObj || {});
  uiState.studioCardLayout = s.card_layout || 'classic_split';
  document.querySelectorAll('#studio-layout-grid .layout-card-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-layout') === uiState.studioCardLayout);
  });
  if (s.card_theme) document.getElementById('studio-theme').value = s.card_theme;
  if (s.card_font) document.getElementById('studio-font').value = s.card_font;

  const accMode = document.getElementById('studio-accent-mode');
  const accPick = document.getElementById('studio-accent-picker');
  if (accMode && accPick) {
    const isHex = typeof s.card_accent_color === 'string' && s.card_accent_color.startsWith('#') && s.card_accent_color.length === 7;
    accMode.value = isHex ? 'custom' : 'auto';
    accPick.disabled = !isHex;
    if (isHex) accPick.value = s.card_accent_color;
  }

  document.getElementById('st-inc-card').checked = s.include_card_intro !== false;
  document.getElementById('st-inc-poster').checked = s.include_poster !== false;
  document.getElementById('st-show-badge').checked = s.show_badge !== false;
  document.getElementById('st-show-year').checked = s.show_year !== false;
  document.getElementById('st-show-rating').checked = s.show_rating !== false;
  document.getElementById('st-inc-synopsis').checked = s.include_synopsis !== false;
  if (s.text_scroll_speed && !Number.isNaN(parseInt(s.text_scroll_speed, 10))) {
    document.getElementById('st-scroll-speed').value = String(s.text_scroll_speed);
  }
  if (s.card_hold_s != null) {
    document.getElementById('st-card-hold').value = String(s.card_hold_s);
  }
  updateStudioSliderLabels();

  document.getElementById('st-jacket-pan').checked = s.jacket_panorama !== false;
  document.getElementById('st-marquee-pan').checked = Boolean(s.marquee_panorama);
  document.getElementById('st-inc-video').checked = s.include_video !== false;
  document.getElementById('st-snap-count').value = String(s.snapshot_panorama_count != null ? s.snapshot_panorama_count : 1);
  document.getElementById('st-pan-dur').value = String(s.panorama_duration_s != null ? s.panorama_duration_s : 3.5);
  document.getElementById('st-vid-dur').value = String(s.video_max_duration_s != null ? s.video_max_duration_s : 10.0);
  const fpsEl = document.getElementById('st-vid-fps');
  if (fpsEl && s.video_fps != null) {
    fpsEl.value = String(s.video_fps);
  }
  const vtypeEl = document.getElementById('st-video-type');
  if (vtypeEl && s.video_type) {
    vtypeEl.value = s.video_type;
  }
  if (s.video_mode) document.getElementById('st-video-mode').value = s.video_mode;
  if (s.color_boost) document.getElementById('st-color-boost').value = s.color_boost;
}

export function updateStudioDomainTabsUi() {
  document.querySelectorAll('#studio-dom-tabs .studio-dom-tab').forEach(btn => {
    const d = btn.getAttribute('data-st-dom');
    btn.classList.toggle('active', d === uiState.studioActiveDomain);
    const subEl = document.getElementById('st-dom-sub-' + d);
    if (subEl && d) {
      const st = (uiState.studioDraftDomainStyles && uiState.studioDraftDomainStyles[d]) || defaultStudioStyleForDomain(d);
      const lay = st.include_card_intro !== false ? st.card_layout : 'card:off';
      const vid = st.include_video !== false
        ? `🎬${Number(st.video_max_duration_s != null ? st.video_max_duration_s : 10).toFixed(0)}s`
        : '🎬off';
      subEl.textContent = `${lay} • ${st.card_theme || 'tinted'} • ${st.color_boost || 'auto'} • ${vid}`;
    }
  });
}

export function hydrateStudioDraftStylesFromCache(dmdId) {
  if (!dmdId) return;
  const prof = (uiState.lastDmdProfiles || []).find(p => p && p.dmd_id === dmdId);
  uiState.studioLoadedDmdId = dmdId;
  uiState.studioDraftDomainStyles = normalizeDomainStylesObj(prof && prof.domain_styles, prof && prof.card_style);
  applyStudioStyleToControls(uiState.studioDraftDomainStyles[uiState.studioActiveDomain]);
  updateStudioDomainTabsUi();
}

export function markStudioFullDirty() {
  uiState.studioFullPreviewId = '';
  if (uiState.studioInitialized) {
    captureCurrentControlsIntoStudioStyle();
    updateStudioDomainTabsUi();
  }
  const badge = document.getElementById('studio-sim-badge');
  if (badge) {
    badge.className = 'pill info';
    badge.textContent = t('studio_badge_card_fast');
  }
}

export async function selectStudioDomainProfile(dom, opts = {}) {
  if (!CANONICAL_DOMAINS.includes(dom)) dom = 'games';
  if (!opts.skipSaveCurrent && uiState.studioInitialized) {
    captureCurrentControlsIntoStudioStyle();
  }
  uiState.studioActiveDomain = dom;
  const domEl = document.getElementById('studio-domain');
  if (domEl) domEl.value = dom;
  applyStudioStyleToControls(uiState.studioDraftDomainStyles[dom]);
  updateStudioDomainTabsUi();

  if (!opts.keepCandidates) {
    const curCand = uiState.studioActiveCandIdx >= 0 ? uiState.studioCandidates[uiState.studioActiveCandIdx] : null;
    if (!curCand || curCand.domain !== dom) {
      uiState.studioCandidates = [];
      uiState.studioActiveCandIdx = -1;
      renderStudioCandidateStrip();
    }
  }
  markStudioFullDirty();
  await loadStudioFilters();
  if (uiState.studioInitialized) {
    requestStudioCardPreviewNow();
  }
}

export function initStudioIfNeeded() {
  updateStudioResUi();
  updateStudioSliderLabels();
  updateStudioDomainTabsUi();
  renderStudioActiveDmdBanner();
  loadStudioDmdQueue();
  if (!uiState.studioInitialized) {
    uiState.studioInitialized = true;
    const langEl = document.getElementById('studio-lang');
    if (langEl && uiState.lastServerConfig && uiState.lastServerConfig.default_lang) {
      langEl.value = uiState.lastServerConfig.default_lang;
    }
    applyStudioStyleToControls(uiState.studioDraftDomainStyles[uiState.studioActiveDomain]);
    updateStudioDomainTabsUi();
    loadStudioFilters();
    requestStudioCardPreviewNow();
  }
  startStudioAnimationLoop();
}

export async function loadStudioFilters() {
  const domEl = document.getElementById('studio-domain');
  const sysEl = document.getElementById('studio-system');
  const genEl = document.getElementById('studio-genre');
  if (!domEl || !sysEl || !genEl) return;
  const dom = domEl.value;
  const isGames = dom === 'games';
  sysEl.disabled = !isGames;
  if (!isGames) {
    sysEl.innerHTML = `<option value="">${esc(t('opt_na'))}</option>`;
  }
  try {
    const lang = (document.getElementById('studio-lang') || {}).value || getCurrentUiLang();
    const d = await ensureDomainFilterCatalog(dom, lang);
    const allOpt = `<option value="">${esc(t('opt_all'))}</option>`;
    if (isGames) {
      sysEl.innerHTML = allOpt + (d.systems || []).map(
        s => `<option value="${esc(s.id)}">${esc(s.label)} (${esc(s.id)})</option>`
      ).join('');
    }
    genEl.innerHTML = allOpt + (d.genres || []).map(
      g => `<option value="${esc(g.id)}">${esc(g.label)}</option>`
    ).join('');
  } catch (err) {
    console.error(err);
  }
}

export async function runStudioSearch() {
  const dom = document.getElementById('studio-domain').value;
  const lang = document.getElementById('studio-lang').value || 'fr';
  const sys = document.getElementById('studio-system').value;
  const gen = document.getElementById('studio-genre').value;
  const q = document.getElementById('studio-query').value.trim();
  const st = document.getElementById('studio-search-status');
  const btn = document.getElementById('btn-studio-search');
  btn.disabled = true;
  st.style.color = 'var(--cyan)';
  st.textContent = t('msg_searching');

  try {
    const params = new URLSearchParams({lang: lang, limit: '8'});
    if (q) params.set('q', q);
    if (dom === 'games' && sys) params.set('systems', sys);
    if (gen) params.set('genres', gen);
    const r = await apiFetch(`/api/v1/${dom}/search?` + params.toString());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    uiState.studioCandidates = Array.isArray(d.items) ? d.items : [];
    if (!uiState.studioCandidates.length) {
      uiState.studioActiveCandIdx = -1;
      renderStudioCandidateStrip();
      st.style.color = 'var(--amber)';
      st.textContent = t('msg_no_candidate');
      return;
    }
    uiState.studioActiveCandIdx = 0;
    uiState.studioFullPreviewId = '';
    renderStudioCandidateStrip();
    st.style.color = 'var(--green)';
    st.textContent = t('msg_search_count', {n: uiState.studioCandidates.length, l: lang.toUpperCase()});
    requestStudioCardPreviewNow();
  } catch (err) {
    st.style.color = 'var(--red)';
    st.textContent = '⚠️ ' + (err.message || String(err));
  } finally {
    btn.disabled = false;
  }
}

export function renderStudioCandidateStrip() {
  const strip = document.getElementById('studio-cand-strip');
  if (!strip) return;
  if (!uiState.studioCandidates.length) {
    strip.innerHTML = '';
    return;
  }
  strip.innerHTML = uiState.studioCandidates.map((c, idx) => {
    const activeCls = idx === uiState.studioActiveCandIdx ? ' active' : '';
    const imgHtml = c.poster_url
      ? `<img src="${esc(c.poster_url)}" alt="" loading="lazy" onerror="this.style.display='none'" />`
      : '';
    const snapTag = c.snapshot_count ? ` • 📸${Number(c.snapshot_count)}` : '';
    const vidTag = c.has_video ? ' • 🎬' : '';
    return `<div class="studio-cand-pill${activeCls}" onclick="selectStudioCandidate(${idx})">
      ${imgHtml}
      <div style="min-width:0;overflow:hidden">
        <div style="font-weight:700;font-size:0.82rem;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.title)}</div>
        <div style="font-size:0.72rem;color:var(--muted)">${esc(c.badge || c.domain.toUpperCase())} ${c.year ? '• ' + esc(c.year) : ''}${esc(snapTag)}${vidTag}</div>
      </div>
    </div>`;
  }).join('');
}

export function selectStudioCandidate(idx) {
  if (!uiState.studioCandidates[idx]) return;
  uiState.studioActiveCandIdx = idx;
  markStudioFullDirty();
  renderStudioCandidateStrip();
  requestStudioCardPreviewNow();
}

export async function openCandidateInStudio(idx) {
  const c = uiState.lastSearchCandidates[idx];
  if (!c) return;
  switchTab('studio');
  const langEl = document.getElementById('studio-lang');
  if (langEl && c.lang) langEl.value = c.lang;
  uiState.studioCandidates = uiState.lastSearchCandidates.slice();
  uiState.studioActiveCandIdx = idx;
  await selectStudioDomainProfile(c.domain || 'games', {keepCandidates: true});
  renderStudioCandidateStrip();
}

export async function openStudioForDmd(dmdId) {
  const backBtn = document.getElementById('btn-studio-back-rotation');
  if (backBtn) backBtn.style.display = 'none';
  uiState.studioReturnToRotationDmd = '';
  switchTab('studio');
  syncTargetDmdSelector();
  const stSel = document.getElementById('studio-target-dmd');
  const topSel = document.getElementById('studio-top-dmd-select');
  if (dmdId) {
    if (stSel) stSel.value = dmdId;
    if (topSel) topSel.value = dmdId;
    await onStudioTargetDmdChange();
    syncStudioResFromActiveDmd();
  }
}

export async function openStudioForDmdDomain(dmdId, dom) {
  uiState.studioReturnToRotationDmd = dmdId || '__editor__';
  const backBtn = document.getElementById('btn-studio-back-rotation');
  if (backBtn) backBtn.style.display = 'inline-flex';
  switchTab('studio');
  syncTargetDmdSelector();
  const stSel = document.getElementById('studio-target-dmd');
  const topSel = document.getElementById('studio-top-dmd-select');
  if (dmdId) {
    if (stSel && Array.from(stSel.options).some(o => o.value === dmdId)) stSel.value = dmdId;
    if (topSel && Array.from(topSel.options).some(o => o.value === dmdId)) topSel.value = dmdId;
    await onStudioTargetDmdChange();
    syncStudioResFromActiveDmd();
  }
  await selectStudioDomainProfile(dom || 'games', {skipSaveCurrent: true});
}

export async function openStudioForDmdDomainFromEditor(dom, explicitDmdId = '') {
  const dmdId = (explicitDmdId || (document.getElementById('prof-dmd-id') || {}).value || '').trim();
  const [ew, eh] = ((document.getElementById('prof-res') || {}).value || '128x32').split('x').map(Number);
  const edLang = (document.getElementById('prof-lang') || {}).value || 'fr';

  uiState.studioReturnToRotationDmd = dmdId || '__editor__';
  const backBtn = document.getElementById('btn-studio-back-rotation');
  if (backBtn) backBtn.style.display = 'inline-flex';

  switchTab('studio');
  syncTargetDmdSelector();
  const stSel = document.getElementById('studio-target-dmd');
  const topSel = document.getElementById('studio-top-dmd-select');
  if (dmdId) {
    if (stSel) {
      if (!Array.from(stSel.options).some(o => o.value === dmdId)) {
        const opt = `<option value="${esc(dmdId)}">📺 ${esc(dmdId)} (${ew || 128}×${eh || 32})</option>`;
        stSel.insertAdjacentHTML('beforeend', opt);
        if (topSel) topSel.insertAdjacentHTML('beforeend', opt);
      }
      stSel.value = dmdId;
    }
    if (topSel) topSel.value = dmdId;
    uiState.studioLoadedDmdId = dmdId;
  }
  const langEl = document.getElementById('studio-lang');
  if (langEl) langEl.value = edLang;
  if (ew && eh) {
    uiState.studioWidth = ew;
    uiState.studioHeight = eh;
    updateStudioResUi();
  }
  uiState.studioDraftDomainStyles = normalizeDomainStylesObj(
    uiState.editingProfileDomainStyles,
    uiState.editingProfileCardStyle
  );
  renderStudioActiveDmdBanner();
  await selectStudioDomainProfile(dom || 'games', {skipSaveCurrent: true});
  await loadStudioDmdQueue();
}

export function returnToDmdRotationEditor() {
  captureCurrentControlsIntoStudioStyle();
  if (!uiState.editingProfileReadOnly) {
    uiState.editingProfileDomainStyles = normalizeDomainStylesObj(
      uiState.studioDraftDomainStyles,
      uiState.editingProfileCardStyle
    );
  }
  switchTab('overview');
  const card = document.getElementById('dmd-profile-editor-card');
  if (card) {
    card.style.display = 'block';
    renderPlaylistEditorRows();
    card.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }
}

export function updateStudioResUi() {
  const w = uiState.studioWidth;
  const h = uiState.studioHeight;
  const isTate = h > w;
  const geomPill = document.getElementById('studio-geom-pill');
  if (geomPill) {
    geomPill.textContent = `${w}×${h} • ${isTate ? 'TATE' : (w === h ? 'SQUARE' : 'YOKO')}`;
  }
  const tateBtn = document.getElementById('btn-studio-tate');
  if (tateBtn) {
    tateBtn.classList.toggle('active', isTate);
  }
  const normKey = isTate ? `${h}x${w}` : `${w}x${h}`;
  document.querySelectorAll('.res-chip').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-res') === normKey);
  });
  renderStudioActiveDmdBanner();
}

export function setStudioBaseRes(w, h) {
  const wasTate = uiState.studioHeight > uiState.studioWidth;
  if (wasTate && w !== h) {
    uiState.studioWidth = h;
    uiState.studioHeight = w;
  } else {
    uiState.studioWidth = w;
    uiState.studioHeight = h;
  }
  markStudioFullDirty();
  updateStudioResUi();
  scheduleStudioCardPreview();
}

export function toggleStudioTate() {
  if (uiState.studioWidth === uiState.studioHeight) {
    uiState.studioWidth = 64;
    uiState.studioHeight = 128;
  } else {
    const tmp = uiState.studioWidth;
    uiState.studioWidth = uiState.studioHeight;
    uiState.studioHeight = tmp;
  }
  markStudioFullDirty();
  updateStudioResUi();
  scheduleStudioCardPreview();
}

export function selectStudioLayout(layoutId) {
  uiState.studioCardLayout = layoutId;
  document.querySelectorAll('#studio-layout-grid .layout-card-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-layout') === layoutId);
  });
  scheduleStudioCardPreview();
}

export function onStudioAccentModeChange() {
  const mode = document.getElementById('studio-accent-mode').value;
  const picker = document.getElementById('studio-accent-picker');
  if (picker) picker.disabled = mode !== 'custom';
  scheduleStudioCardPreview();
}

export function buildStudioCommonPayload() {
  const cand = getActiveStudioCandidate();
  const dom = (document.getElementById('studio-domain') || {}).value || cand.domain || 'games';
  const lang = (document.getElementById('studio-lang') || {}).value || cand.lang || 'fr';
  const st = captureCurrentControlsIntoStudioStyle();
  updateStudioDomainTabsUi();
  return {
    domain: dom,
    candidate_id: cand.candidate_id || '',
    query: cand.title || '',
    lang: lang,
    width: uiState.studioWidth,
    height: uiState.studioHeight,
    fps: st.video_fps,
    video_fps: st.video_fps,
    title: cand.title || 'Metal Slug X',
    year: cand.year || '',
    badge: cand.badge || dom.toUpperCase(),
    badge_color: cand.badge_color || '#FF6D00',
    subtitle: cand.subtitle || '',
    rating: cand.rating || '',
    synopsis: cand.synopsis || '',
    poster_url: cand.poster_url || '',
    include_card_intro: st.include_card_intro,
    card_layout: st.card_layout,
    card_theme: st.card_theme,
    card_font: st.card_font,
    card_accent_color: st.card_accent_color,
    include_poster: st.include_poster,
    show_badge: st.show_badge,
    show_year: st.show_year,
    show_rating: st.show_rating,
    include_synopsis: st.include_synopsis,
    text_scroll_speed: st.text_scroll_speed,
    card_hold_s: st.card_hold_s,
    jacket_panorama: st.jacket_panorama,
    marquee_panorama: st.marquee_panorama,
    snapshot_panorama_count: st.snapshot_panorama_count,
    panorama_duration_s: st.panorama_duration_s,
    include_video: st.include_video,
    video_type: st.video_type,
    video_max_duration_s: st.video_max_duration_s,
    video_mode: st.video_mode,
    color_boost: st.color_boost
  };
}

export function scheduleStudioCardPreview() {
  markStudioFullDirty();
  if (uiState.studioCardDebounceTimer) clearTimeout(uiState.studioCardDebounceTimer);
  uiState.studioCardDebounceTimer = setTimeout(() => {
    requestStudioCardPreviewNow();
  }, 170);
}
