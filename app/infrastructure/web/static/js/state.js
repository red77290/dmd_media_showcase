import { apiFetch } from './api.js';
import { t } from './i18n/index.js';

export const CANONICAL_DOMAINS = ['games', 'movies', 'series', 'anime'];

export const STUDIO_DEMO_FALLBACKS = {
  games: {
    candidate_id: '',
    domain: 'games',
    title: 'Metal Slug X',
    year: '1999',
    badge: 'NEOGEO',
    badge_color: '#FF6D00',
    subtitle: 'SNK • Run & Gun',
    rating: '9.3',
    synopsis: 'Marco, Tarma, Eri, and Fio return to stop General Morden and a mysterious alien armada in crisp Neo-Geo pixel art.',
    lang: 'fr',
    poster_url: '',
    has_video: false
  },
  movies: {
    candidate_id: '',
    domain: 'movies',
    title: 'Blade Runner',
    year: '1982',
    badge: 'SCI-FI',
    badge_color: '#00E5FF',
    subtitle: 'Ridley Scott',
    rating: '8.7',
    synopsis: 'In a neon-soaked 2019 Los Angeles, Rick Deckard hunts four bioengineered replicants seeking their creator.',
    lang: 'fr',
    poster_url: '',
    has_video: false
  },
  series: {
    candidate_id: '',
    domain: 'series',
    title: 'Stranger Things',
    year: '2016',
    badge: 'SERIES',
    badge_color: '#FF1744',
    subtitle: 'Hawkins, 1983',
    rating: '8.6',
    synopsis: 'A vanished boy uncovers secret experiments, supernatural forces, and a mysterious girl in 1980s Indiana.',
    lang: 'fr',
    poster_url: '',
    has_video: false
  },
  anime: {
    candidate_id: '',
    domain: 'anime',
    title: 'Akira',
    year: '1988',
    badge: 'ANIME',
    badge_color: '#FFAB00',
    subtitle: 'Katsuhiro Otomo',
    rating: '8.8',
    synopsis: 'Kaneda races through Neo-Tokyo on his iconic red bike after Tetsuo awakens uncontrollable psychic powers.',
    lang: 'fr',
    poster_url: '',
    has_video: false
  }
};

export function defaultStudioStyleForDomain(dom) {
  const base = {
    include_card_intro: true,
    card_layout: 'classic_split',
    card_theme: 'tinted',
    card_font: 'arcade_pixel',
    card_accent_color: 'auto',
    include_poster: true,
    show_badge: true,
    show_year: true,
    show_rating: true,
    include_synopsis: true,
    text_scroll_speed: '4',
    card_hold_s: 0.9,
    jacket_panorama: true,
    marquee_panorama: false,
    snapshot_panorama_count: 1,
    panorama_duration_s: 3.5,
    include_video: true,
    video_type: 'any',
    video_max_duration_s: 10.0,
    video_fps: 15,
    video_mode: 'auto_action',
    color_boost: 'auto'
  };
  const overrides = {
    games: {
      card_layout: 'classic_split',
      card_theme: 'tinted',
      card_font: 'arcade_pixel',
      marquee_panorama: true,
      snapshot_panorama_count: 2,
      video_max_duration_s: 10.0,
      color_boost: 'pixel_art',
      video_type: 'any'
    },
    movies: {
      card_layout: 'poster_right',
      card_theme: 'poster_wash',
      card_font: 'clean_sans',
      snapshot_panorama_count: 1,
      video_max_duration_s: 12.0,
      color_boost: 'cinema',
      video_type: 'trailer'
    },
    series: {
      card_layout: 'classic_split',
      card_theme: 'tinted',
      card_font: 'clean_sans',
      snapshot_panorama_count: 1,
      video_max_duration_s: 10.0,
      color_boost: 'cinema',
      video_type: 'trailer'
    },
    anime: {
      card_layout: 'badge_header',
      card_theme: 'neon_border',
      card_font: 'clean_sans',
      snapshot_panorama_count: 2,
      video_max_duration_s: 12.0,
      color_boost: 'anime',
      video_type: 'opening'
    }
  };
  return Object.assign(base, overrides[dom] || {});
}

export function normalizeDomainStylesObj(rawDomainStyles, fallbackCardStyle) {
  const out = {};
  CANONICAL_DOMAINS.forEach(dom => {
    const base = defaultStudioStyleForDomain(dom);
    if (fallbackCardStyle && typeof fallbackCardStyle === 'object') {
      Object.assign(base, fallbackCardStyle);
    }
    if (rawDomainStyles && rawDomainStyles[dom] && typeof rawDomainStyles[dom] === 'object') {
      Object.assign(base, rawDomainStyles[dom]);
    }
    out[dom] = base;
  });
  return out;
}

export function shortDomainDisplayName(dom) {
  const names = {
    games: t('opt_dom_games'),
    movies: t('opt_dom_movies'),
    series: t('opt_dom_series'),
    anime: t('opt_dom_anime')
  };
  return String(names[dom] || dom.toUpperCase()).split(' (')[0].trim();
}

export function defaultPlaylistRows(lang) {
  const l = lang || 'fr';
  return [
    {enabled: true, domain: 'games', count: 2, lang: l, systems: ['arcade', 'mame', 'neogeo', 'cps2', 'snes', 'megadrive'], genres: [], decades: []},
    {enabled: true, domain: 'movies', count: 1, lang: l, systems: [], genres: [], decades: []},
    {enabled: false, domain: 'series', count: 1, lang: l, systems: [], genres: [], decades: []},
    {enabled: false, domain: 'anime', count: 1, lang: l, systems: [], genres: [], decades: []}
  ];
}

export function toUniqueTokenArray(val) {
  const raw = Array.isArray(val) ? val : (typeof val === 'string' && val.trim() ? val.split(',') : []);
  const seen = new Set();
  const out = [];
  raw.forEach(item => {
    const tok = String(item || '').trim();
    if (tok && !seen.has(tok)) {
      seen.add(tok);
      out.push(tok);
    }
  });
  return out;
}

export const filterCatalogCache = new Map();

export async function ensureDomainFilterCatalog(dom, lang) {
  const l = lang || 'fr';
  const key = `${dom}:${l}`;
  if (filterCatalogCache.has(key)) return filterCatalogCache.get(key);
  const entry = {
    systems: [],
    genres: [],
    decades: [],
    sysMap: new Map(),
    genMap: new Map(),
    decMap: new Map()
  };
  try {
    const r = await apiFetch(`/api/v1/${dom}/filters?lang=${encodeURIComponent(l)}`);
    if (r.ok) {
      const d = await r.json();
      entry.systems = Array.isArray(d.systems) ? d.systems : [];
      entry.genres = Array.isArray(d.genres) ? d.genres : [];
      entry.decades = Array.isArray(d.decades) ? d.decades : [];
      entry.systems.forEach(s => { if (s && s.id) entry.sysMap.set(s.id, s.label || s.id); });
      entry.genres.forEach(g => { if (g && g.id) entry.genMap.set(g.id, g.label || g.id); });
      entry.decades.forEach(dc => { if (dc && dc.id) entry.decMap.set(dc.id, dc.label || dc.id); });
      filterCatalogCache.set(key, entry);
    }
  } catch (e) {
    console.error(e);
  }
  return entry;
}

export async function preloadAllDomainFilterCatalogs(lang) {
  await Promise.all(CANONICAL_DOMAINS.map(d => ensureDomainFilterCatalog(d, lang)));
}

export const uiState = {
  configHydrated: false,
  lastVaultStatus: {},
  lastProviders: {},
  lastClients: [],
  lastSeenStats: {},
  lastHealth: null,
  lastUpdateInfo: null,
  lastServerConfig: {},
  lastDmdProfiles: [],
  lastSearchCandidates: [],

  editingProfileDmdId: '',
  editingProfileReadOnly: false,
  editingProfileLivePayload: null,
  editingProfileSupportsLocalCard: false,
  editingProfileCardStyle: null,
  editingProfileDomainStyles: normalizeDomainStylesObj(null, null),
  editingProfileSlots: {},

  tagModalState: {
    open: false,
    domain: 'games',
    mode: 'systems',
    catalog: {systems: [], genres: [], decades: []},
    selectedSystems: new Set(),
    selectedGenres: new Set(),
    selectedDecades: new Set()
  },

  studioInitialized: false,
  studioWidth: 128,
  studioHeight: 32,
  studioCardLayout: 'classic_split',
  studioActiveDomain: 'games',
  studioLoadedDmdId: '',
  studioReturnToRotationDmd: '',
  studioDraftDomainStyles: normalizeDomainStylesObj(null, null),
  studioCandidates: [],
  studioActiveCandIdx: -1,
  studioFullPreviewId: '',
  studioCardDebounceTimer: null,
  studioCardAbortCtrl: null,

  studioStreamW: 128,
  studioStreamH: 32,
  studioStreamFps: 15,
  studioFrameDurMs: 67,
  studioFrames: [],
  studioChapters: [],
  studioFrameIdx: 0,
  studioPlaying: true,
  studioLastTickMs: 0,
  studioRafId: 0,
  studioOffscreenCanvas: null
};
