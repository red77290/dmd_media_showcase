import { apiFetch, esc, getActiveApiKey } from '../api.js';
import { getCurrentUiLang, t } from '../i18n/index.js';
import { uiState } from '../state.js';
import { controlDmd } from './overview.js';
import { loadHealthAndConfig } from './runtime_config.js';
import { loadStudioDmdQueue } from './studio_actions.js';

export async function loadDomainFilters() {
  const domEl = document.getElementById('test-domain');
  const sysEl = document.getElementById('test-system');
  if (!domEl || !sysEl) return;
  const dom = domEl.value;
  const isGames = dom === 'games';
  sysEl.disabled = !isGames;
  if (!isGames) {
    sysEl.innerHTML = `<option value="">${esc(t('opt_na'))}</option>`;
  }
  try {
    const contentLangEl = document.getElementById('test-content-lang');
    const filterLang = (contentLangEl && contentLangEl.value) ? contentLangEl.value : getCurrentUiLang();
    const r = await apiFetch(`/api/v1/${dom}/filters?lang=${encodeURIComponent(filterLang)}`);
    if (!r.ok) return;
    const d = await r.json();
    const allOpt = `<option value="">${esc(t('opt_all'))}</option>`;
    if (isGames) {
      sysEl.innerHTML = allOpt + (d.systems || []).map(
        s => `<option value="${esc(s.id)}">${esc(s.label)} (${esc(s.id)})</option>`
      ).join('');
    }
    const genEl = document.getElementById('test-genre');
    genEl.innerHTML = allOpt + (d.genres || []).map(
      g => `<option value="${esc(g.id)}">${esc(g.label)}</option>`
    ).join('');
    const decEl = document.getElementById('test-decade');
    decEl.innerHTML = allOpt + (d.decades || []).map(
      dec => `<option value="${esc(dec.id)}">${esc(dec.label)}</option>`
    ).join('');
  } catch (e) {
    console.error(e);
  }
}

export async function runLiveTest() {
  const dom = document.getElementById('test-domain').value;
  const contentLang = (document.getElementById('test-content-lang') || {}).value || getCurrentUiLang();
  const sys = document.getElementById('test-system').value;
  const gen = document.getElementById('test-genre').value;
  const dec = document.getElementById('test-decade').value;
  const vmode = document.getElementById('test-video-mode').value;
  const cboost = document.getElementById('test-color-boost').value;
  const [w, h] = document.getElementById('test-res').value.split('x').map(Number);
  const q = document.getElementById('test-query').value.trim();
  const st = document.getElementById('test-status');
  const btn = document.getElementById('btn-run-test');
  btn.disabled = true;
  st.textContent = t('msg_testing');

  try {
    let item = null;
    if (q) {
      const r = await apiFetch(`/api/v1/${dom}/on-demand`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          domain: dom, query: q, lang: contentLang, width: w, height: h,
          include_video: false, video_mode: vmode, color_boost: cboost
        })
      });
      if (!r.ok) throw new Error((await r.json()).detail || 'Not found');
      item = await r.json();
    } else {
      const params = new URLSearchParams({
        client_id: 'webui_tester', count: '1', lang: contentLang,
        width: String(w), height: String(h), include_video: 'false'
      });
      if (dom === 'games' && sys) params.set('systems', sys);
      if (gen) params.set('genres', gen);
      if (dec) params.set('decade', dec);
      const r = await apiFetch(`/api/v1/${dom}/random?` + params.toString());
      if (!r.ok) throw new Error('Discovery request failed');
      let batch = await r.json();
      for (let i = 0; i < 30 && (!batch.items || !batch.items.length); i++) {
        await new Promise(res => setTimeout(res, 600));
        const rb = await apiFetch(`/api/v1/${dom}/batch?session_id=${encodeURIComponent(batch.session_id)}`);
        if (rb.ok) batch = await rb.json();
      }
      if (batch.items && batch.items.length) item = batch.items[0];
    }

    if (!item) throw new Error(t('msg_no_candidate'));
    document.getElementById('test-preview').classList.add('visible');
    document.getElementById('prev-title').textContent = item.title || '—';
    document.getElementById('prev-badge').textContent = item.badge || dom.toUpperCase();
    document.getElementById('prev-year').textContent = item.year || '—';
    document.getElementById('prev-source').textContent = `Source: ${item.source_used || 'auto'} • ★ ${item.rating || 'N/A'}`;
    document.getElementById('prev-sub').textContent = item.subtitle || '';
    document.getElementById('prev-synopsis').textContent = item.synopsis || '';
    const imgEl = document.getElementById('prev-img');
    let posterUrl = item.poster_png_url || (item.assets && item.assets.poster_png) || '';
    const activeKey = getActiveApiKey();
    if (posterUrl && activeKey && posterUrl.startsWith('/api/v1/')) {
      posterUrl += (posterUrl.includes('?') ? '&' : '?') + 'api_key=' + encodeURIComponent(activeKey);
    }
    if (posterUrl) {
      imgEl.src = posterUrl;
      imgEl.style.display = 'block';
    } else {
      imgEl.style.display = 'none';
    }
    st.textContent = t('msg_preview_ok');
    loadHealthAndConfig(false);
  } catch (err) {
    st.textContent = '⚠️ ' + (err.message || String(err));
  } finally {
    btn.disabled = false;
  }
}

export async function skipSelectedDmd() {
  const dmdId = (document.getElementById('test-target-dmd') || {}).value || '';
  const st = document.getElementById('test-status');
  if (!dmdId) {
    if (st) {
      st.style.color = 'var(--amber)';
      st.textContent = t('msg_need_target_dmd');
    }
    return;
  }
  await controlDmd(dmdId, 'skip');
}

export async function runCandidateSearch() {
  const dom = document.getElementById('test-domain').value;
  const contentLang = document.getElementById('test-content-lang').value || 'fr';
  const sys = document.getElementById('test-system').value;
  const gen = document.getElementById('test-genre').value;
  const dec = document.getElementById('test-decade').value;
  const q = document.getElementById('test-query').value.trim();
  const st = document.getElementById('test-status');
  const btn = document.getElementById('btn-search-cands');
  const grid = document.getElementById('search-results-grid');
  btn.disabled = true;
  st.style.color = 'var(--cyan)';
  st.textContent = t('msg_searching');

  try {
    const params = new URLSearchParams({lang: contentLang, limit: '8'});
    if (q) params.set('q', q);
    if (dom === 'games' && sys) params.set('systems', sys);
    if (gen) params.set('genres', gen);
    if (dec) params.set('decades', dec);
    const r = await apiFetch(`/api/v1/${dom}/search?` + params.toString());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    uiState.lastSearchCandidates = Array.isArray(d.items) ? d.items : [];
    if (!uiState.lastSearchCandidates.length) {
      grid.style.display = 'none';
      st.style.color = 'var(--amber)';
      st.textContent = t('msg_no_candidate');
      return;
    }
    renderSearchCandidates();
    st.style.color = 'var(--green)';
    st.textContent = t('msg_search_count', {n: uiState.lastSearchCandidates.length, l: contentLang.toUpperCase()});
  } catch (err) {
    st.style.color = 'var(--red)';
    st.textContent = '⚠️ ' + (err.message || String(err));
  } finally {
    btn.disabled = false;
  }
}

export function renderSearchCandidates() {
  const grid = document.getElementById('search-results-grid');
  if (!grid) return;
  if (!uiState.lastSearchCandidates.length) {
    grid.style.display = 'none';
    return;
  }
  grid.style.display = 'grid';
  grid.innerHTML = uiState.lastSearchCandidates.map((c, idx) => {
    const thumb = c.poster_url
      ? `<img class="cand-thumb" src="${esc(c.poster_url)}" alt="" loading="lazy" onerror="this.style.display='none'" />`
      : '';
    const vidPill = c.has_video ? `<span class="pill ok">🎬 Video</span>` : '';
    return `<div class="cand-card">
      ${thumb}
      <div class="cand-body">
        <div class="cand-title">${esc(c.title)} ${c.year ? `<small style="color:var(--muted)">(${esc(c.year)})</small>` : ''}</div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center">
          <span class="pill info">${esc(c.badge || c.domain.toUpperCase())}</span>
          <span class="pill warn">${esc(c.source_id)} • ${(c.lang || 'fr').toUpperCase()}</span>
          ${vidPill}
        </div>
        <div class="cand-syn">${esc(c.synopsis || c.subtitle || '')}</div>
        <div class="cand-actions">
          <button type="button" class="secondary mini-btn" onclick="previewCandidate(${idx})">${esc(t('btn_preview_cand'))}</button>
          <button type="button" class="secondary mini-btn" onclick="openCandidateInStudio(${idx})">${esc(t('btn_open_studio'))}</button>
          <button type="button" class="secondary mini-btn" onclick="enqueueCandidate(${idx}, 'enqueue_next')">${esc(t('btn_queue_next'))}</button>
          <button type="button" class="mini-btn" onclick="enqueueCandidate(${idx}, 'play_now')">${esc(t('btn_play_now'))}</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

export function previewCandidate(idx) {
  const c = uiState.lastSearchCandidates[idx];
  if (!c) return;
  document.getElementById('test-preview').classList.add('visible');
  document.getElementById('prev-title').textContent = c.title || '—';
  document.getElementById('prev-badge').textContent = c.badge || c.domain.toUpperCase();
  document.getElementById('prev-year').textContent = c.year || '—';
  document.getElementById('prev-source').textContent = `Source: ${c.source_id || 'auto'} • ★ ${c.rating || 'N/A'} • ${(c.lang || 'fr').toUpperCase()}`;
  document.getElementById('prev-sub').textContent = c.subtitle || '';
  document.getElementById('prev-synopsis').textContent = c.synopsis || '';
  const imgEl = document.getElementById('prev-img');
  if (c.poster_url) {
    imgEl.src = c.poster_url;
    imgEl.style.display = 'block';
  } else {
    imgEl.style.display = 'none';
  }
}

export async function enqueueCandidate(idx, mode) {
  const c = uiState.lastSearchCandidates[idx];
  if (!c) return;
  const targetDmd = document.getElementById('test-target-dmd').value;
  const st = document.getElementById('test-status');
  if (!targetDmd) {
    st.style.color = 'var(--amber)';
    st.textContent = t('msg_need_target_dmd');
    return;
  }
  const contentLang = document.getElementById('test-content-lang').value || c.lang || 'fr';
  const vmode = document.getElementById('test-video-mode').value;
  const cboost = document.getElementById('test-color-boost').value;
  const markSeen = document.getElementById('test-mark-seen').value === 'true';

  try {
    const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(targetDmd)}/queue`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        candidate_id: c.candidate_id,
        domain: c.domain,
        query: c.title,
        lang: contentLang,
        mode: mode,
        mark_seen: markSeen,
        video_mode: vmode,
        color_boost: cboost
      })
    });
    if (!r.ok) {
      const ej = await r.json().catch(() => ({}));
      throw new Error(ej.detail || ('HTTP ' + r.status));
    }
    st.style.color = 'var(--green)';
    st.textContent = t('msg_queued_ok', {t: c.title, d: targetDmd, m: mode === 'play_now' ? 'Play Now' : 'Next'});
    await loadTargetDmdQueue();
    loadHealthAndConfig(false);
  } catch (err) {
    st.style.color = 'var(--red)';
    st.textContent = '⚠️ ' + (err.message || String(err));
  }
}

export async function loadTargetDmdQueue() {
  const targetDmd = (document.getElementById('test-target-dmd') || {}).value || '';
  const box = document.getElementById('ondemand-queue-body');
  if (!box) return;
  if (!targetDmd) {
    box.innerHTML = esc(t('queue_select_dmd'));
    return;
  }
  try {
    const r = await apiFetch(`/api/v1/clients/${encodeURIComponent(targetDmd)}/queue`, {cache: 'no-store'});
    if (!r.ok) return;
    const d = await r.json();
    const items = Array.isArray(d.items) ? d.items : [];
    if (!items.length) {
      box.innerHTML = `<span style="color:var(--muted)">${esc(t('queue_empty'))}</span>`;
      return;
    }
    const safeDmd = esc(targetDmd).replace(/'/g, "\\'");
    box.innerHTML = items.map((it, i) => {
      const stClass = it.status === 'ready' || it.status === 'playing' ? 'ok' : 'warn';
      const safeQid = esc(it.queue_id).replace(/'/g, "\\'");
      return `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 10px;background:#0d121b;border:1px solid var(--border);border-radius:8px;margin-bottom:6px">
        <div>
          <b>#${i + 1} — ${esc(it.title)}</b>
          <span class="pill info" style="margin-left:6px">${esc((it.domain || '').toUpperCase())}</span>
          <span class="pill info">${esc((it.lang || 'fr').toUpperCase())}</span>
          <span class="pill ${stClass}">${esc(it.status)}</span>
          ${it.from_studio_sim ? `<span class="pill ok">${esc(t('pill_sim_studio'))}</span>` : ''}
        </div>
        <button type="button" class="danger mini-btn" onclick="removeQueueItem('${safeDmd}', '${safeQid}')">✕</button>
      </div>`;
    }).join('');
  } catch (e) {
    console.error(e);
  }
}

export async function removeQueueItem(dmdId, queueId) {
  try {
    await apiFetch(`/api/v1/clients/${encodeURIComponent(dmdId)}/queue/${encodeURIComponent(queueId)}`, {
      method: 'DELETE'
    });
    await loadTargetDmdQueue();
    await loadStudioDmdQueue();
    loadHealthAndConfig(false);
  } catch (e) {
    console.error(e);
  }
}
