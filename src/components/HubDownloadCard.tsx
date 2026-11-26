import React, { useCallback, useEffect, useState } from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';

interface GitHubAsset {
  name: string;
  size: number;
  browser_download_url: string;
}

interface GitHubReleaseResponse {
  tag_name?: string;
  published_at?: string;
  html_url?: string;
  assets?: GitHubAsset[];
}

interface ResolvedAsset {
  name: string;
  url: string;
  sizeMb?: string;
  isFallbackLink: boolean;
}

interface ResolvedRelease {
  tag: string;
  publishedAt: string | null;
  releaseUrl: string;
  windows: ResolvedAsset;
  macos: ResolvedAsset;
  source: 'api' | 'cdn' | 'static';
}

interface CachedEntry {
  savedAt: number;
  data: ResolvedRelease;
}

const RELEASES_API_URL =
  'https://api.github.com/repos/urikorisky/BCI_Workshop_2026_Hub/releases/latest';
const TAURI_CONF_RAW_URL =
  'https://raw.githubusercontent.com/urikorisky/BCI_Workshop_2026_Hub/main/src-tauri/tauri.conf.json';
const RELEASES_LATEST_URL =
  'https://github.com/urikorisky/BCI_Workshop_2026_Hub/releases/latest';
const CACHE_KEY = 'bci_hub_latest_release_v1';
const CACHE_TTL_MS = 5 * 60 * 1000;

function formatMb(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function detectClientOs(): 'windows' | 'macos' | 'other' {
  if (typeof navigator === 'undefined') {
    return 'other';
  }
  const navWithUa = navigator as Navigator & {
    userAgentData?: { platform?: string };
  };
  const hint = `${navWithUa.userAgentData?.platform ?? ''} ${navigator.platform ?? ''} ${navigator.userAgent ?? ''}`.toLowerCase();
  if (hint.includes('win')) {
    return 'windows';
  }
  if (hint.includes('mac') || hint.includes('darwin')) {
    return 'macos';
  }
  return 'other';
}

function formatDateLabel(isoDate: string | null, isHe: boolean): string | null {
  if (!isoDate) {
    return null;
  }
  const parsed = new Date(isoDate);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  try {
    return new Intl.DateTimeFormat(isHe ? 'he-IL' : 'en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    }).format(parsed);
  } catch {
    return parsed.toISOString().slice(0, 10);
  }
}

function staticFallbackRelease(): ResolvedRelease {
  return {
    tag: '',
    publishedAt: null,
    releaseUrl: RELEASES_LATEST_URL,
    windows: {
      name: '.msi / .exe',
      url: RELEASES_LATEST_URL,
      isFallbackLink: true,
    },
    macos: {
      name: '.dmg',
      url: RELEASES_LATEST_URL,
      isFallbackLink: true,
    },
    source: 'static',
  };
}

function readCache(): ResolvedRelease | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as CachedEntry;
    if (
      typeof parsed?.savedAt === 'number' &&
      Date.now() - parsed.savedAt < CACHE_TTL_MS &&
      parsed.data
    ) {
      return parsed.data;
    }
  } catch {
    // Ignore storage errors (private mode / blocked storage)
  }
  return null;
}

function writeCache(data: ResolvedRelease): void {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    const payload: CachedEntry = { savedAt: Date.now(), data };
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(payload));
  } catch {
    // Ignore storage errors
  }
}

async function resolveFromGitHubApi(): Promise<ResolvedRelease> {
  const resp = await fetch(RELEASES_API_URL, {
    headers: { Accept: 'application/vnd.github+json' },
  });
  if (!resp.ok) {
    throw new Error(`GitHub API HTTP ${resp.status}`);
  }
  const json = (await resp.json()) as GitHubReleaseResponse;
  const tag = json.tag_name?.trim() || '';
  const releaseUrl = json.html_url || RELEASES_LATEST_URL;
  const assets = Array.isArray(json.assets) ? json.assets : [];

  const msiAsset = assets.find((a) => a.name.toLowerCase().endsWith('.msi'));
  const exeAsset = assets.find((a) => a.name.toLowerCase().endsWith('.exe'));
  const winRaw = msiAsset ?? exeAsset;

  const universalDmg = assets.find((a) => {
    const lower = a.name.toLowerCase();
    return lower.endsWith('.dmg') && lower.includes('universal');
  });
  const anyDmg = assets.find((a) => a.name.toLowerCase().endsWith('.dmg'));
  const macRaw = universalDmg ?? anyDmg;

  const windows: ResolvedAsset = winRaw
    ? {
        name: winRaw.name,
        url: winRaw.browser_download_url,
        sizeMb: formatMb(winRaw.size),
        isFallbackLink: false,
      }
    : {
        name: 'Windows (.msi)',
        url: releaseUrl,
        isFallbackLink: true,
      };

  const macos: ResolvedAsset = macRaw
    ? {
        name: macRaw.name,
        url: macRaw.browser_download_url,
        sizeMb: formatMb(macRaw.size),
        isFallbackLink: false,
      }
    : {
        name: 'macOS (.dmg)',
        url: releaseUrl,
        isFallbackLink: true,
      };

  return {
    tag,
    publishedAt: json.published_at ?? null,
    releaseUrl,
    windows,
    macos,
    source: 'api',
  };
}

async function resolveFromRawManifest(): Promise<ResolvedRelease> {
  const resp = await fetch(TAURI_CONF_RAW_URL);
  if (!resp.ok) {
    throw new Error(`Raw manifest HTTP ${resp.status}`);
  }
  const conf = (await resp.json()) as { version?: string };
  const version = conf.version?.trim();
  if (!version) {
    throw new Error('Missing version in tauri.conf.json');
  }
  const tag = version.startsWith('v') ? version : `v${version}`;
  const cleanVer = version.replace(/^v/, '');
  const base = `https://github.com/urikorisky/BCI_Workshop_2026_Hub/releases/download/${tag}`;

  return {
    tag,
    publishedAt: null,
    releaseUrl: `https://github.com/urikorisky/BCI_Workshop_2026_Hub/releases/tag/${tag}`,
    windows: {
      name: `BCI.Workshop.Hub_${cleanVer}_x64_en-US.msi`,
      url: `${base}/BCI.Workshop.Hub_${cleanVer}_x64_en-US.msi`,
      isFallbackLink: false,
    },
    macos: {
      name: `BCI.Workshop.Hub_${cleanVer}_universal.dmg`,
      url: `${base}/BCI.Workshop.Hub_${cleanVer}_universal.dmg`,
      isFallbackLink: false,
    },
    source: 'cdn',
  };
}

export default function HubDownloadCard(): React.ReactElement {
  const { i18n } = useDocusaurusContext();
  const isHe = i18n.currentLocale === 'he';

  const [release, setRelease] = useState<ResolvedRelease>(() => staticFallbackRelease());
  const [clientOs, setClientOs] = useState<'windows' | 'macos' | 'other'>('other');
  const [loading, setLoading] = useState<boolean>(false);

  const loadRelease = useCallback(async (bypassCache: boolean) => {
    if (!bypassCache) {
      const cached = readCache();
      if (cached) {
        setRelease(cached);
        return;
      }
    }

    setLoading(true);
    try {
      const resolved = await resolveFromGitHubApi();
      setRelease(resolved);
      writeCache(resolved);
    } catch {
      try {
        const cdnResolved = await resolveFromRawManifest();
        setRelease(cdnResolved);
        writeCache(cdnResolved);
      } catch {
        setRelease(staticFallbackRelease());
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setClientOs(detectClientOs());
    void loadRelease(false);
  }, [loadRelease]);

  const dateText = formatDateLabel(release.publishedAt, isHe);

  const copy = isHe
    ? {
        title: 'הורדה ישירה של BCI Workshop Hub',
        loadingBadge: 'בודק גרסה עדכנית...',
        versionPrefix: 'גרסה עדכנית:',
        fallbackBadge: 'עמוד ההורדה הרשמי (GitHub Releases)',
        compareHint:
          'כבר ניסיתם להתקין בעבר? בדקו שמספר הגרסה כאן חדש יותר ממה שמותקן אצלכם, והתקינו מחדש רק אם יצא עדכון.',
        winTitle: 'הורדה ל-Windows (64-bit)',
        winSub: 'קובץ התקנה (.msi)',
        macTitle: 'הורדה ל-Mac (Apple Silicon & Intel)',
        macSub: 'קובץ Universal (.dmg)',
        yourOsBadge: 'המחשב שלך',
        buildingAsset: 'נבנה כרגע — פתחו את עמוד השחרור ב-GitHub',
        refreshBtn: 'בדוק עדכון',
        refreshingBtn: 'מעדכן...',
        allReleases: 'לכל הגרסאות והערות השחרור ב-GitHub ←',
      }
    : {
        title: 'Direct Download — BCI Workshop Hub',
        loadingBadge: 'Checking latest release...',
        versionPrefix: 'Latest Version:',
        fallbackBadge: 'Official Download Page (GitHub Releases)',
        compareHint:
          'Tried installing earlier? Compare this version number with your previous attempt and update only if a newer version is shown.',
        winTitle: 'Download for Windows (64-bit)',
        winSub: 'Installer (.msi)',
        macTitle: 'Download for macOS (Apple Silicon & Intel)',
        macSub: 'Universal disk image (.dmg)',
        yourOsBadge: 'Your OS',
        buildingAsset: 'Building now — open GitHub release page',
        refreshBtn: 'Refresh',
        refreshingBtn: 'Checking...',
        allReleases: 'All releases & release notes on GitHub →',
      };

  const renderAssetButton = (
    os: 'windows' | 'macos',
    asset: ResolvedAsset,
    title: string,
    subtitle: string
  ) => {
    const isRecommended = clientOs === os;
    const classes = [
      'hub-download-btn',
      isRecommended ? 'hub-download-btn--recommended' : '',
    ]
      .filter(Boolean)
      .join(' ');

    return (
      <a className={classes} href={asset.url} target="_blank" rel="noopener noreferrer">
        <div className="hub-download-btn__top">
          <span className="hub-download-btn__title">{title}</span>
          {isRecommended && (
            <span className="hub-os-chip">{copy.yourOsBadge}</span>
          )}
        </div>
        <div className="hub-download-btn__meta">
          {asset.isFallbackLink && release.source === 'api' ? (
            <span>{copy.buildingAsset}</span>
          ) : (
            <>
              <span dir="ltr">{asset.name}</span>
              {asset.sizeMb ? <span>• {asset.sizeMb}</span> : <span>• {subtitle}</span>}
            </>
          )}
        </div>
      </a>
    );
  };

  return (
    <div className="hub-card" dir={isHe ? 'rtl' : 'ltr'}>
      <div className="hub-card-header">
        <div className="hub-card-title">{copy.title}</div>
        <span className="hub-version-badge">
          {loading && !release.tag ? (
            copy.loadingBadge
          ) : release.tag ? (
            <>
              {copy.versionPrefix}{' '}
              <strong dir="ltr">{release.tag}</strong>
              {dateText ? <span className="hub-version-date">({dateText})</span> : null}
            </>
          ) : (
            copy.fallbackBadge
          )}
        </span>
      </div>

      <p className="hub-compare-hint">{copy.compareHint}</p>

      <div className="hub-btn-grid">
        {renderAssetButton('windows', release.windows, copy.winTitle, copy.winSub)}
        {renderAssetButton('macos', release.macos, copy.macTitle, copy.macSub)}
      </div>

      <div className="hub-card-footer">
        <button
          type="button"
          className="hub-refresh-btn"
          onClick={() => void loadRelease(true)}
          disabled={loading}
        >
          {loading ? copy.refreshingBtn : copy.refreshBtn}
        </button>
        <a
          className="hub-all-releases-link"
          href={release.releaseUrl || RELEASES_LATEST_URL}
          target="_blank"
          rel="noopener noreferrer"
        >
          {copy.allReleases}
        </a>
      </div>
    </div>
  );
}
