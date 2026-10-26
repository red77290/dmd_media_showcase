import { apiFetch } from './api.js';
import { t } from './i18n/index.js';
import { uiState } from './state.js';

export function renderUpdateBanner(info) {
  const banner = document.getElementById('update-banner');
  if (!banner) return;
  if (!info || !info.update_available || !info.latest_version) {
    banner.style.display = 'none';
    return;
  }
  try {
    if (sessionStorage.getItem('dmd_showcase_dismissed_update') === String(info.latest_version)) {
      banner.style.display = 'none';
      return;
    }
  } catch (_) {}

  const allowedPrefix = 'https://github.com/red77290/dmd_media_showcase/';
  const safeReleaseUrl = (info.release_url && String(info.release_url).startsWith(allowedPrefix))
    ? String(info.release_url)
    : 'https://github.com/red77290/dmd_media_showcase/releases';

  const badgeEl = document.getElementById('update-banner-badge');
  if (badgeEl) badgeEl.textContent = t('upd_badge');

  const txtEl = document.getElementById('update-banner-text');
  const msgKey = info.runtime_os === 'docker' ? 'upd_msg_docker' : 'upd_msg_os';
  if (txtEl) {
    txtEl.textContent = t(msgKey, {
      latest: info.latest_version,
      os: info.os_label || info.runtime_os || 'OS',
      current: info.current_version || '1.0.0'
    });
  }

  const relLink = document.getElementById('update-banner-release-link');
  if (relLink) {
    relLink.href = safeReleaseUrl;
    relLink.textContent = t('upd_btn_release');
  }

  const assetLink = document.getElementById('update-banner-asset-link');
  if (assetLink) {
    const dlUrl = info.asset_download_url ? String(info.asset_download_url) : '';
    if (info.asset_name && dlUrl.startsWith(allowedPrefix)) {
      assetLink.href = dlUrl;
      assetLink.textContent = t('upd_btn_download', {asset: info.asset_name});
      assetLink.style.display = 'inline-flex';
    } else {
      assetLink.style.display = 'none';
    }
  }
  banner.style.display = 'flex';
}

export function dismissUpdateBanner() {
  if (uiState.lastUpdateInfo && uiState.lastUpdateInfo.latest_version) {
    try {
      sessionStorage.setItem('dmd_showcase_dismissed_update', String(uiState.lastUpdateInfo.latest_version));
    } catch (_) {}
  }
  const banner = document.getElementById('update-banner');
  if (banner) banner.style.display = 'none';
}

export async function checkSystemUpdate(force = false) {
  try {
    const r = await apiFetch('/api/v1/system/update-check' + (force ? '?force=true' : ''), {
      cache: 'no-store'
    });
    if (!r.ok) return;
    uiState.lastUpdateInfo = await r.json();
    renderUpdateBanner(uiState.lastUpdateInfo);
  } catch (_) {}
}
