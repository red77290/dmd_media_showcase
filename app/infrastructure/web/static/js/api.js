export function initApiKeyFromUrl() {
  try {
    if (typeof window === 'undefined' || typeof localStorage === 'undefined') return;
    const urlParams = new URLSearchParams(window.location.search);
    const qk = (urlParams.get('api_key') || '').trim();
    if (qk) localStorage.setItem('dmd_showcase_api_key', qk);
  } catch (_) {}
}

export function isMaskedSecret(val) {
  return val === '••••••••' || val === '********';
}

export function getActiveApiKey() {
  let stored = '';
  try {
    if (typeof localStorage !== 'undefined') {
      stored = (localStorage.getItem('dmd_showcase_api_key') || '').trim();
    }
  } catch (_) {}
  if (stored && !isMaskedSecret(stored)) return stored;
  if (typeof document !== 'undefined') {
    const inputEl = document.getElementById('showcase_api_key');
    const typed = inputEl ? inputEl.value.trim() : '';
    if (typed && !isMaskedSecret(typed)) return typed;
  }
  return '';
}

export function apiFetch(url, options = {}) {
  const opts = Object.assign({}, options);
  const hdrs = Object.assign({}, opts.headers || {});
  const key = getActiveApiKey();
  if (key) hdrs['X-Showcase-Key'] = key;
  opts.headers = hdrs;
  return fetch(url, opts);
}

export function esc(val) {
  return String(val == null ? '' : val)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function clampIntInput(id, minVal, maxVal, fallback) {
  const el = document.getElementById(id);
  const raw = parseInt(el ? el.value : '', 10);
  if (Number.isNaN(raw)) return fallback;
  return Math.max(minVal, Math.min(maxVal, raw));
}

export function clampFloatInput(id, minVal, maxVal, fallback) {
  const el = document.getElementById(id);
  const raw = parseFloat(el ? el.value : '');
  if (Number.isNaN(raw)) return fallback;
  return Math.max(minVal, Math.min(maxVal, raw));
}

export function showConfigToast(msg, isError = false, targetIds = null) {
  const ids = targetIds || [
    'status-msg-runtime',
    'status-msg-keys',
    'status-msg-net',
    'status-msg-sources-ov',
    'status-msg-sources-keys'
  ];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.color = isError ? 'var(--red)' : 'var(--green)';
    el.textContent = msg;
    setTimeout(() => {
      if (el.textContent === msg) el.textContent = '';
    }, 4000);
  });
}

export function togglePwd(id) {
  const el = document.getElementById(id);
  if (el) el.type = el.type === 'password' ? 'text' : 'password';
}

export function clearField(id) {
  const el = document.getElementById(id);
  if (el) {
    el.value = '';
    el.dataset.cleared = '1';
  }
}
