import { I18N_EN } from './en.js';
import { I18N_FR } from './fr.js';
import { I18N_ES } from './es.js';

export const I18N = {
  en: I18N_EN,
  fr: I18N_FR,
  es: I18N_ES
};

export function detectInitialLang() {
  try {
    if (typeof localStorage !== 'undefined') {
      const saved = localStorage.getItem('dmd_showcase_ui_lang');
      if (saved && I18N[saved]) return saved;
    }
  } catch (_) {}
  const rawNav = (typeof navigator !== 'undefined' && navigator.language) ? navigator.language : 'en';
  const nav = ((rawNav || 'en').split('-')[0] || 'en').toLowerCase();
  return I18N[nav] ? nav : 'en';
}

let currentUiLang = detectInitialLang();

export function getCurrentUiLang() {
  return currentUiLang;
}

export function setCurrentUiLang(lang) {
  currentUiLang = I18N[lang] ? lang : 'en';
  return currentUiLang;
}

export function t(key, vars) {
  let s = (I18N[currentUiLang] && I18N[currentUiLang][key]) || I18N.en[key] || key;
  if (vars) {
    Object.entries(vars).forEach(([k, v]) => {
      s = s.replace(`{${k}}`, String(v));
    });
  }
  return s;
}
