import React, { useMemo, useState } from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';

export interface ColorBandSpec {
  id: string;
  en: string;
  he: string;
  hex: string;
  digit: number | null;
  multiplier: number | null;
  multLabel: string;
  tolerance: number | null;
  tolLabel: string;
  aliases: string[];
}

export const RESISTOR_COLORS: ColorBandSpec[] = [
  {
    id: 'black',
    en: 'Black',
    he: 'שחור',
    hex: '#1c1917',
    digit: 0,
    multiplier: 1,
    multLabel: '×1 Ω',
    tolerance: null,
    tolLabel: '—',
    aliases: ['black', 'blk', 'שחור'],
  },
  {
    id: 'brown',
    en: 'Brown',
    he: 'חום',
    hex: '#78350f',
    digit: 1,
    multiplier: 10,
    multLabel: '×10 Ω',
    tolerance: 1,
    tolLabel: '±1%',
    aliases: ['brown', 'brn', 'חום'],
  },
  {
    id: 'red',
    en: 'Red',
    he: 'אדום',
    hex: '#dc2626',
    digit: 2,
    multiplier: 100,
    multLabel: '×100 Ω',
    tolerance: 2,
    tolLabel: '±2%',
    aliases: ['red', 'אדום'],
  },
  {
    id: 'orange',
    en: 'Orange',
    he: 'כתום',
    hex: '#ea580c',
    digit: 3,
    multiplier: 1_000,
    multLabel: '×1 kΩ',
    tolerance: null,
    tolLabel: '—',
    aliases: ['orange', 'org', 'כתום'],
  },
  {
    id: 'yellow',
    en: 'Yellow',
    he: 'צהוב',
    hex: '#eab308',
    digit: 4,
    multiplier: 10_000,
    multLabel: '×10 kΩ',
    tolerance: null,
    tolLabel: '—',
    aliases: ['yellow', 'yel', 'צהוב'],
  },
  {
    id: 'green',
    en: 'Green',
    he: 'ירוק',
    hex: '#16a34a',
    digit: 5,
    multiplier: 100_000,
    multLabel: '×100 kΩ',
    tolerance: 0.5,
    tolLabel: '±0.5%',
    aliases: ['green', 'grn', 'ירוק'],
  },
  {
    id: 'blue',
    en: 'Blue',
    he: 'כחול',
    hex: '#2563eb',
    digit: 6,
    multiplier: 1_000_000,
    multLabel: '×1 MΩ',
    tolerance: 0.25,
    tolLabel: '±0.25%',
    aliases: ['blue', 'blu', 'כחול'],
  },
  {
    id: 'violet',
    en: 'Violet',
    he: 'סגול',
    hex: '#7c3aed',
    digit: 7,
    multiplier: 10_000_000,
    multLabel: '×10 MΩ',
    tolerance: 0.1,
    tolLabel: '±0.1%',
    aliases: ['violet', 'purple', 'vio', 'סגול'],
  },
  {
    id: 'grey',
    en: 'Grey',
    he: 'אפור',
    hex: '#6b7280',
    digit: 8,
    multiplier: 100_000_000,
    multLabel: '×100 MΩ',
    tolerance: 0.05,
    tolLabel: '±0.05%',
    aliases: ['grey', 'gray', 'gry', 'אפור'],
  },
  {
    id: 'white',
    en: 'White',
    he: 'לבן',
    hex: '#f8fafc',
    digit: 9,
    multiplier: 1_000_000_000,
    multLabel: '×1 GΩ',
    tolerance: null,
    tolLabel: '—',
    aliases: ['white', 'wht', 'לבן'],
  },
  {
    id: 'gold',
    en: 'Gold',
    he: 'זהב',
    hex: '#d97706',
    digit: null,
    multiplier: 0.1,
    multLabel: '×0.1 Ω',
    tolerance: 5,
    tolLabel: '±5%',
    aliases: ['gold', 'gld', 'זהב'],
  },
  {
    id: 'silver',
    en: 'Silver',
    he: 'כסף',
    hex: '#94a3b8',
    digit: null,
    multiplier: 0.01,
    multLabel: '×0.01 Ω',
    tolerance: 10,
    tolLabel: '±10%',
    aliases: ['silver', 'slv', 'כסף'],
  },
];

const BY_ID: Record<string, ColorBandSpec> = Object.fromEntries(
  RESISTOR_COLORS.map((c) => [c.id, c]),
);

const ALIAS_MAP: Record<string, string> = {};
for (const c of RESISTOR_COLORS) {
  for (const a of c.aliases) {
    ALIAS_MAP[a.toLowerCase()] = c.id;
  }
}

const BAND1_OPTIONS = RESISTOR_COLORS.filter((c) => c.digit !== null && c.digit >= 1);
const DIGIT_OPTIONS = RESISTOR_COLORS.filter((c) => c.digit !== null);
const MULT_OPTIONS = RESISTOR_COLORS.filter((c) => c.multiplier !== null);
const TOL_OPTIONS = RESISTOR_COLORS.filter((c) => c.tolerance !== null);

const PRESETS: Array<{ label: string; bands: [string, string, string, string, string] }> = [
  { label: '220 Ω (±1%)', bands: ['red', 'red', 'black', 'black', 'brown'] },
  { label: '330 Ω (±1%)', bands: ['orange', 'orange', 'black', 'black', 'brown'] },
  { label: '10 kΩ (±1%)', bands: ['brown', 'black', 'black', 'red', 'brown'] },
  { label: '1 MΩ (±1%)', bands: ['brown', 'black', 'black', 'yellow', 'brown'] },
];

function formatOhms(ohms: number): string {
  if (!Number.isFinite(ohms)) return '—';
  const fmt = (val: number) => {
    const rounded = Number(val.toPrecision(4));
    return `${rounded}`;
  };
  if (ohms >= 1e9) return `${fmt(ohms / 1e9)} GΩ`;
  if (ohms >= 1e6) return `${fmt(ohms / 1e6)} MΩ`;
  if (ohms >= 1e3) return `${fmt(ohms / 1e3)} kΩ`;
  return `${fmt(ohms)} Ω`;
}

export default function ResistorInterpreter(): React.ReactElement {
  const { i18n } = useDocusaurusContext();
  const isHe = i18n.currentLocale === 'he';

  const [bands, setBands] = useState<[string, string, string, string, string]>([
    'brown',
    'black',
    'black',
    'yellow',
    'brown',
  ]);
  const [seriesText, setSeriesText] = useState<string>(
    isHe ? 'חום שחור שחור צהוב חום' : 'brown black black yellow brown',
  );
  const [textError, setTextError] = useState<string | null>(null);

  const labelFor = (c: ColorBandSpec) => (isHe ? c.he : c.en);

  const syncTextFromBands = (next: [string, string, string, string, string]) => {
    setSeriesText(next.map((id) => labelFor(BY_ID[id])).join(' '));
    setTextError(null);
  };

  const handleSelectBand = (pos: number, colorId: string) => {
    const next: [string, string, string, string, string] = [...bands] as [
      string,
      string,
      string,
      string,
      string,
    ];
    next[pos] = colorId;
    setBands(next);
    syncTextFromBands(next);
  };

  const handleTextChange = (raw: string) => {
    setSeriesText(raw);
    const tokens = raw
      .trim()
      .split(/[\s,;|\-–—/]+/)
      .filter(Boolean);

    if (tokens.length === 0) {
      setTextError(null);
      return;
    }

    const mapped: string[] = [];
    for (const tok of tokens) {
      const resolved = ALIAS_MAP[tok.toLowerCase()];
      if (!resolved) {
        setTextError(
          isHe
            ? `צבע לא מוכר: "${tok}". השתמשו בשמות כמו: חום, שחור, אדום, כתום, צהוב, ירוק, כחול, סגול, אפור, לבן, זהב, כסף (או באנגלית).`
            : `Unknown color "${tok}". Use: black, brown, red, orange, yellow, green, blue, violet, grey, white, gold, silver.`,
        );
        return;
      }
      mapped.push(resolved);
    }

    if (mapped.length !== 5) {
      setTextError(
        isHe
          ? `נדרשים 5 פסים (הוקלדו ${mapped.length}).`
          : `Enter 5 bands (currently ${mapped.length}).`,
      );
      return;
    }

    const [b1, b2, b3, b4, b5] = mapped;
    if (BY_ID[b1].digit === null || BY_ID[b1].digit === 0) {
      setTextError(
        isHe
          ? `פס 1 חייב להיות ספרה בין 1 ל-9 (לא ${labelFor(BY_ID[b1])}).`
          : `Band 1 must be a non-zero digit 1–9 (not ${BY_ID[b1].en}).`,
      );
      return;
    }
    if (BY_ID[b2].digit === null || BY_ID[b3].digit === null) {
      setTextError(
        isHe
          ? 'פסים 2 ו-3 חייבים להיות ספרות (שחור עד לבן, לא זהב/כסף).'
          : 'Bands 2 and 3 must be digit colors (Black–White, not Gold/Silver).',
      );
      return;
    }
    if (BY_ID[b4].multiplier === null) {
      setTextError(
        isHe ? 'פס 4 חייב להיות צבע מכפיל תקין.' : 'Band 4 must be a valid multiplier color.',
      );
      return;
    }
    if (BY_ID[b5].tolerance === null) {
      setTextError(
        isHe
          ? `פס 5 (סבילות) אינו יכול להיות ${labelFor(BY_ID[b5])}.`
          : `Band 5 (Tolerance) cannot be ${BY_ID[b5].en}.`,
      );
      return;
    }

    setTextError(null);
    setBands([b1, b2, b3, b4, b5]);
  };

  const handleFlip = () => {
    const rev: [string, string, string, string, string] = [
      bands[4],
      bands[3],
      bands[2],
      bands[1],
      bands[0],
    ];
    if (
      BY_ID[rev[0]].digit === null ||
      BY_ID[rev[0]].digit === 0 ||
      BY_ID[rev[1]].digit === null ||
      BY_ID[rev[2]].digit === null ||
      BY_ID[rev[3]].multiplier === null ||
      BY_ID[rev[4]].tolerance === null
    ) {
      setTextError(
        isHe
          ? 'לא ניתן להפוך כיוון: הצבעים בכיוון ההפוך אינם יוצרים קוד 5-פסים תקני.'
          : 'Cannot flip: reversed colors do not form a valid 5-band sequence.',
      );
      return;
    }
    setBands(rev);
    syncTextFromBands(rev);
  };

  const applyPreset = (presetBands: [string, string, string, string, string]) => {
    setBands(presetBands);
    syncTextFromBands(presetBands);
  };

  const calc = useMemo(() => {
    const c1 = BY_ID[bands[0]];
    const c2 = BY_ID[bands[1]];
    const c3 = BY_ID[bands[2]];
    const c4 = BY_ID[bands[3]];
    const c5 = BY_ID[bands[4]];

    const d1 = c1.digit ?? 0;
    const d2 = c2.digit ?? 0;
    const d3 = c3.digit ?? 0;
    const baseDigits = d1 * 100 + d2 * 10 + d3;
    const mult = c4.multiplier ?? 1;
    const tol = c5.tolerance ?? 1;
    const ohms = baseDigits * mult;
    const minOhms = ohms * (1 - tol / 100);
    const maxOhms = ohms * (1 + tol / 100);

    return { c1, c2, c3, c4, c5, baseDigits, mult, tol, ohms, minOhms, maxOhms };
  }, [bands]);

  return (
    <div className="resistor-card">
      <div className="resistor-card-header">
        <span className="resistor-card-title">
          {isHe ? 'מפענח נגד 5 פסים (אינטראקטיבי)' : 'Interactive 5-Band Resistor Interpreter'}
        </span>
        <div className="resistor-presets">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              className="resistor-chip-btn"
              onClick={() => applyPreset(p.bands)}
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            className="resistor-chip-btn resistor-chip-btn--flip"
            onClick={handleFlip}
            title={isHe ? 'היפוך סדר הפסים' : 'Reverse band order'}
          >
            {isHe ? '⇄ היפוך כיוון' : '⇄ Flip Bands'}
          </button>
        </div>
      </div>

      {/* Live SVG 5-band resistor (always LTR so Band 1 stays on the left) */}
      <div className="resistor-svg-wrap" dir="ltr">
        <svg viewBox="0 0 460 110" width="100%" height="100%" role="img" aria-label="5-band resistor diagram">
          {/* Wire leads */}
          <line x1="15" y1="55" x2="95" y2="55" stroke="#94a3b8" strokeWidth="6" strokeLinecap="round" />
          <line x1="365" y1="55" x2="445" y2="55" stroke="#94a3b8" strokeWidth="6" strokeLinecap="round" />

          {/* Metal-film blue body */}
          <rect x="92" y="25" width="276" height="60" rx="18" fill="#60a5fa" stroke="#1d4ed8" strokeWidth="2.5" />
          <rect x="128" y="31" width="204" height="48" rx="8" fill="#3b82f6" />

          {/* 5 Bands with authentic wider gap before Band 5 (tolerance) */}
          <rect x="122" y="26" width="16" height="58" fill={calc.c1.hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="158" y="31" width="16" height="48" fill={calc.c2.hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="194" y="31" width="16" height="48" fill={calc.c3.hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="230" y="31" width="16" height="48" fill={calc.c4.hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="316" y="26" width="16" height="58" fill={calc.c5.hex} stroke="#0f172a" strokeWidth="0.8" />

          {/* Labels below bands */}
          <text x="130" y="102" textAnchor="middle" fontSize="11" fill="currentColor">1</text>
          <text x="166" y="102" textAnchor="middle" fontSize="11" fill="currentColor">2</text>
          <text x="202" y="102" textAnchor="middle" fontSize="11" fill="currentColor">3</text>
          <text x="238" y="102" textAnchor="middle" fontSize="11" fill="currentColor">×M</text>
          <text x="324" y="102" textAnchor="middle" fontSize="11" fill="currentColor">±%</text>
        </svg>
      </div>

      {/* 5 Dropdown Band Selectors (always LTR to match physical left-to-right bands) */}
      <div className="resistor-bands-grid" dir="ltr">
        {[
          { idx: 0, title: isHe ? 'פס 1 (מאות)' : 'Band 1 (100s)', opts: BAND1_OPTIONS },
          { idx: 1, title: isHe ? 'פס 2 (עשרות)' : 'Band 2 (10s)', opts: DIGIT_OPTIONS },
          { idx: 2, title: isHe ? 'פס 3 (אחדות)' : 'Band 3 (1s)', opts: DIGIT_OPTIONS },
          { idx: 3, title: isHe ? 'פס 4 (מכפיל)' : 'Band 4 (Mult)', opts: MULT_OPTIONS },
          { idx: 4, title: isHe ? 'פס 5 (סבילות)' : 'Band 5 (Tol)', opts: TOL_OPTIONS },
        ].map(({ idx, title, opts }) => {
          const currentSpec = BY_ID[bands[idx]];
          return (
            <label key={idx} className="resistor-band-field">
              <span className="resistor-band-label">
                <span className="resistor-swatch" style={{ backgroundColor: currentSpec.hex }} />
                {title}
              </span>
              <select
                className="resistor-select"
                value={bands[idx]}
                onChange={(e) => handleSelectBand(idx, e.target.value)}
              >
                {opts.map((opt) => {
                  const detail =
                    idx < 3
                      ? `${opt.digit}`
                      : idx === 3
                        ? opt.multLabel
                        : opt.tolLabel;
                  return (
                    <option key={opt.id} value={opt.id}>
                      {labelFor(opt)} ({detail})
                    </option>
                  );
                })}
              </select>
            </label>
          );
        })}
      </div>

      {/* Text Series Input */}
      <div className="resistor-text-row">
        <label className="resistor-text-label" htmlFor="resistor-series-input">
          {isHe
            ? 'או הקלידו סדרת צבעים (עברית או אנגלית, מופרדת ברווח או פסיק):'
            : 'Or enter a 5-color series (English or Hebrew, space/comma separated):'}
        </label>
        <input
          id="resistor-series-input"
          type="text"
          className="resistor-text-input"
          value={seriesText}
          onChange={(e) => handleTextChange(e.target.value)}
          placeholder={isHe ? 'לדוגמה: חום שחור שחור צהוב חום' : 'e.g. brown black black yellow brown'}
        />
        {textError && <div className="resistor-error">{textError}</div>}
      </div>

      {/* Computed Output */}
      <div className="resistor-result">
        <div className="resistor-result-main">
          <span className="resistor-ohms">{formatOhms(calc.ohms)}</span>
          <span className="resistor-tol">{calc.c5.tolLabel}</span>
        </div>
        <div className="resistor-result-sub">
          <div>
            <strong>{isHe ? 'חישוב:' : 'Calculation:'}</strong>{' '}
            <code dir="ltr">
              {calc.baseDigits} × {calc.mult < 1 ? calc.mult : calc.mult.toLocaleString()} Ω ={' '}
              {calc.ohms.toLocaleString()} Ω ({formatOhms(calc.ohms)})
            </code>
          </div>
          <div>
            <strong>{isHe ? 'טווח תקין:' : 'Valid range:'}</strong>{' '}
            <span dir="ltr">
              {formatOhms(calc.minOhms)} – {formatOhms(calc.maxOhms)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ResistorLegendTable(): React.ReactElement {
  const { i18n } = useDocusaurusContext();
  const isHe = i18n.currentLocale === 'he';

  return (
    <div className="resistor-legend-wrap" dir="ltr">
      <table className="resistor-legend-table">
        <thead>
          <tr>
            <th>{isHe ? 'Color (צבע)' : 'Color'}</th>
            <th>{isHe ? 'Band 1 (100s)' : 'Band 1 (100s)'}</th>
            <th>{isHe ? 'Band 2 (10s)' : 'Band 2 (10s)'}</th>
            <th>{isHe ? 'Band 3 (1s)' : 'Band 3 (1s)'}</th>
            <th>{isHe ? 'Band 4 (×M)' : 'Band 4 (Multiplier)'}</th>
            <th>{isHe ? 'Band 5 (±%)' : 'Band 5 (Tolerance)'}</th>
          </tr>
        </thead>
        <tbody>
          {RESISTOR_COLORS.map((c) => (
            <tr key={c.id}>
              <td className="resistor-legend-color-cell">
                <span className="resistor-swatch" style={{ backgroundColor: c.hex }} />
                <strong>{isHe ? `${c.en} (${c.he})` : c.en}</strong>
              </td>
              <td>{c.digit !== null && c.digit >= 1 ? <code>{c.digit}</code> : '—'}</td>
              <td>{c.digit !== null ? <code>{c.digit}</code> : '—'}</td>
              <td>{c.digit !== null ? <code>{c.digit}</code> : '—'}</td>
              <td>{c.multiplier !== null ? <code>{c.multLabel}</code> : '—'}</td>
              <td>{c.tolerance !== null ? <code>{c.tolLabel}</code> : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResistorWorkedExample(): React.ReactElement {
  const { i18n } = useDocusaurusContext();
  const isHe = i18n.currentLocale === 'he';
  const ex = ['brown', 'black', 'black', 'yellow', 'brown'].map((id) => BY_ID[id]);

  return (
    <div className="resistor-example-card" dir="ltr">
      <div className="resistor-example-title">
        {isHe
          ? 'Worked Example (Left → Right): Brown – Black – Black – Yellow – Brown'
          : 'Brown – Black – Black – Yellow – Brown'}
      </div>

      <div className="resistor-svg-wrap" style={{ maxWidth: 360 }}>
        <svg viewBox="0 0 460 110" width="100%" height="100%" role="img" aria-label="1 MOhm 5-band resistor example">
          <line x1="15" y1="55" x2="95" y2="55" stroke="#94a3b8" strokeWidth="6" strokeLinecap="round" />
          <line x1="365" y1="55" x2="445" y2="55" stroke="#94a3b8" strokeWidth="6" strokeLinecap="round" />
          <rect x="92" y="25" width="276" height="60" rx="18" fill="#60a5fa" stroke="#1d4ed8" strokeWidth="2.5" />
          <rect x="128" y="31" width="204" height="48" rx="8" fill="#3b82f6" />
          <rect x="122" y="26" width="16" height="58" fill={ex[0].hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="158" y="31" width="16" height="48" fill={ex[1].hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="194" y="31" width="16" height="48" fill={ex[2].hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="230" y="31" width="16" height="48" fill={ex[3].hex} stroke="#0f172a" strokeWidth="0.8" />
          <rect x="316" y="26" width="16" height="58" fill={ex[4].hex} stroke="#0f172a" strokeWidth="0.8" />
          <text x="130" y="102" textAnchor="middle" fontSize="11" fill="currentColor">1</text>
          <text x="166" y="102" textAnchor="middle" fontSize="11" fill="currentColor">0</text>
          <text x="202" y="102" textAnchor="middle" fontSize="11" fill="currentColor">0</text>
          <text x="238" y="102" textAnchor="middle" fontSize="11" fill="currentColor">×10k</text>
          <text x="324" y="102" textAnchor="middle" fontSize="11" fill="currentColor">±1%</text>
        </svg>
      </div>

      <ol className="resistor-example-steps">
        <li>
          <span className="resistor-swatch" style={{ backgroundColor: ex[0].hex }} />
          <strong>Band 1 (Brown{isHe ? ' / חום' : ''}):</strong> hundreds digit = <code>1</code>
        </li>
        <li>
          <span className="resistor-swatch" style={{ backgroundColor: ex[1].hex }} />
          <strong>Band 2 (Black{isHe ? ' / שחור' : ''}):</strong> tens digit = <code>0</code>
        </li>
        <li>
          <span className="resistor-swatch" style={{ backgroundColor: ex[2].hex }} />
          <strong>Band 3 (Black{isHe ? ' / שחור' : ''}):</strong> ones digit = <code>0</code> → base digits = <code>100</code>
        </li>
        <li>
          <span className="resistor-swatch" style={{ backgroundColor: ex[3].hex }} />
          <strong>Band 4 (Yellow{isHe ? ' / צהוב' : ''}):</strong> multiplier = <code>×10,000 Ω</code> (<code>×10 kΩ</code>)
        </li>
        <li>
          <span className="resistor-swatch" style={{ backgroundColor: ex[4].hex }} />
          <strong>Band 5 (Brown{isHe ? ' / חום' : ''}):</strong> tolerance = <code>±1%</code>
        </li>
      </ol>

      <pre className="resistor-example-formula">
        <code>R = 100 × 10,000 Ω = 1,000,000 Ω = 1 MΩ ±1%</code>
      </pre>

      <p className="resistor-example-note">
        {isHe ? (
          <>
            Day 1 LED resistor check: <strong>Red – Red – Black – Black – Brown</strong> (אדום – אדום – שחור – שחור – חום) →{' '}
            <code>220 × 1 Ω = 220 Ω ±1%</code>
          </>
        ) : (
          <>
            Quick check on your Day 1 LED resistor: <strong>Red – Red – Black – Black – Brown</strong> →{' '}
            <code>220 × 1 Ω = 220 Ω ±1%</code>
          </>
        )}
      </p>
    </div>
  );
}
