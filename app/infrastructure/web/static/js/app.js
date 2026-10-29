import { clearField, initApiKeyFromUrl, togglePwd } from './api.js';
import { I18N, getCurrentUiLang, setCurrentUiLang, t } from './i18n/index.js';
import {
  jumpToStudioChapter,
  onStudioScrub,
  redrawCurrentStudioFrame,
  studioStepFrame,
  toggleStudioPlay
} from './simulator.js';
import { uiState } from './state.js';
import {
  applyTagPickerModal,
  bindTagModalEscapeListener,
  clearAllTagModal,
  closeTagPickerModal,
  openTagPickerModal,
  renderTagPickerOptions,
  selectVisibleTagModal,
  toggleTagModalItem
} from './tag_picker.js';
import {
  controlDmd,
  jumpToQueueStudio,
  purgeDmdClient,
  renderClients,
  renderHealth,
  renderRuntimeSummary,
  renderSeen,
  renderVaultStatus,
  switchTab
} from './tabs/overview.js';
import {
  moveDomainSource,
  onSourcePrimaryChange,
  onSourceSecondaryChange,
  onSourceStrategyChange,
  renderProviders,
  renderSourceBadge,
  toggleDomainSource
} from './tabs/source_chains.js';
import {
  closeProfileEditor,
  deleteDmdProfile,
  jumpToQueueFromInspector,
  onPlSlotCount,
  onPlSlotToggle,
  onProfileLangChange,
  openProfileEditor,
  openStudioFromInspector,
  removeSlotTag,
  renderPlaylistEditorRows,
  saveDmdProfile
} from './tabs/profile_editor.js';
import {
  evictOnDemandCache,
  loadHealthAndConfig,
  populateConfigForm,
  refreshAll,
  resetHistory,
  saveApiKeys,
  saveConfig,
  saveServerConfig,
  saveSourcePolicies,
  syncMdnsUi,
  syncMqttModeUi,
  syncReferentials
} from './tabs/runtime_config.js';
import {
  enqueueStudioSim,
  onStudioTargetDmdChange,
  onStudioTopDmdChange,
  renderStudioActiveDmdBanner,
  requestStudioCardPreviewNow,
  runStudioFullPreview,
  saveStudioStyleToDmdProfile,
  skipStudioTargetDmd,
  syncStudioResFromActiveDmd
} from './tabs/studio_actions.js';
import {
  loadStudioFilters,
  markStudioFullDirty,
  onStudioAccentModeChange,
  openCandidateInStudio,
  openStudioForDmd,
  openStudioForDmdDomain,
  openStudioForDmdDomainFromEditor,
  returnToDmdRotationEditor,
  runStudioSearch,
  scheduleStudioCardPreview,
  selectStudioCandidate,
  selectStudioDomainProfile,
  selectStudioLayout,
  setStudioBaseRes,
  toggleStudioTate,
  updateStudioDomainTabsUi,
  updateStudioSliderLabels
} from './tabs/studio_controls.js';
import {
  enqueueCandidate,
  loadDomainFilters,
  loadTargetDmdQueue,
  previewCandidate,
  removeQueueItem,
  runCandidateSearch,
  runLiveTest,
  runPreviewTest,
  skipSelectedDmd
} from './tabs/tester_queue.js';
import { checkSystemUpdate, dismissUpdateBanner, renderUpdateBanner } from './update_banner.js';

export function setUiLang(lang) {
  const effectiveLang = setCurrentUiLang(lang);
  try {
    localStorage.setItem('dmd_showcase_ui_lang', effectiveLang);
  } catch (_) {}
  document.documentElement.lang = effectiveLang;
  const sel = document.getElementById('ui-lang');
  if (sel) sel.value = effectiveLang;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const k = el.getAttribute('data-i18n');
    if (I18N[effectiveLang][k] || I18N.en[k]) el.textContent = t(k);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const k = el.getAttribute('data-i18n-placeholder');
    if (I18N[effectiveLang][k] || I18N.en[k]) el.placeholder = t(k);
  });
  syncMqttModeUi();
  syncMdnsUi();
  renderRuntimeSummary(uiState.lastServerConfig, uiState.lastHealth);
  renderVaultStatus(uiState.lastVaultStatus);
  renderProviders(uiState.lastProviders, uiState.lastSourcePolicies);
  renderClients(uiState.lastClients);
  renderSeen(uiState.lastSeenStats);
  if (uiState.lastHealth) renderHealth(uiState.lastHealth);
  renderUpdateBanner(uiState.lastUpdateInfo);
  loadDomainFilters();
  loadStudioFilters();
  updateStudioSliderLabels();
  updateStudioDomainTabsUi();
  renderStudioActiveDmdBanner();
  const profCard = document.getElementById('dmd-profile-editor-card');
  if (profCard && profCard.style.display !== 'none') {
    renderPlaylistEditorRows();
  }
}

const WINDOW_HANDLERS = {
  applyTagPickerModal,
  clearAllTagModal,
  clearField,
  closeProfileEditor,
  closeTagPickerModal,
  controlDmd,
  deleteDmdProfile,
  dismissUpdateBanner,
  enqueueCandidate,
  enqueueStudioSim,
  evictOnDemandCache,
  jumpToQueueFromInspector,
  jumpToQueueStudio,
  jumpToStudioChapter,
  loadDomainFilters,
  loadStudioFilters,
  loadTargetDmdQueue,
  markStudioFullDirty,
  moveDomainSource,
  onPlSlotCount,
  onPlSlotToggle,
  onProfileLangChange,
  onSourcePrimaryChange,
  onSourceSecondaryChange,
  onSourceStrategyChange,
  onStudioAccentModeChange,
  onStudioScrub,
  onStudioTargetDmdChange,
  onStudioTopDmdChange,
  openCandidateInStudio,
  openProfileEditor,
  openStudioForDmd,
  openStudioForDmdDomain,
  openStudioForDmdDomainFromEditor,
  openStudioFromInspector,
  openTagPickerModal,
  populateConfigForm,
  previewCandidate,
  purgeDmdClient,
  redrawCurrentStudioFrame,
  refreshAll,
  removeQueueItem,
  removeSlotTag,
  renderSourceBadge,
  renderTagPickerOptions,
  requestStudioCardPreviewNow,
  resetHistory,
  returnToDmdRotationEditor,
  runCandidateSearch,
  runLiveTest,
  runPreviewTest,
  runStudioFullPreview,
  runStudioSearch,
  saveApiKeys,
  saveConfig,
  saveDmdProfile,
  saveServerConfig,
  saveSourcePolicies,
  saveStudioStyleToDmdProfile,
  scheduleStudioCardPreview,
  selectStudioCandidate,
  selectStudioDomainProfile,
  selectStudioLayout,
  selectVisibleTagModal,
  setStudioBaseRes,
  setUiLang,
  skipSelectedDmd,
  skipStudioTargetDmd,
  studioStepFrame,
  switchTab,
  syncMdnsUi,
  syncMqttModeUi,
  syncReferentials,
  syncStudioResFromActiveDmd,
  toggleDomainSource,
  togglePwd,
  toggleStudioPlay,
  toggleStudioTate,
  toggleTagModalItem,
  updateStudioSliderLabels
};

if (typeof window !== 'undefined') {
  Object.assign(window, WINDOW_HANDLERS);
  initApiKeyFromUrl();
  bindTagModalEscapeListener();
  setUiLang(getCurrentUiLang());
  loadHealthAndConfig(true);
  checkSystemUpdate(false);
  setInterval(() => loadHealthAndConfig(false), 3000);
  setInterval(() => checkSystemUpdate(false), 3600000);
}
