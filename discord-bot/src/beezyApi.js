// Typed fetch wrapper for the beezy.fyi public API (Cloud Run service).
//
// Every call takes an optional `opts` object so tests (and the Gemini tool
// layer) can inject { fetchImpl, baseUrl, apiKey, timeoutMs }. Defaults are
// read from the environment at call time. The API key is sent as the
// X-API-Key header only; it never appears in a URL, a log line or an error.

export const DEFAULT_TIMEOUT_MS = 10_000;
export const SPORTS = ['mlb', 'nfl', 'nba', 'nhl'];

export class BeezyApiError extends Error {
  /**
   * @param {string} message plain-language message, safe to show to a member
   * @param {{kind: 'config'|'timeout'|'network'|'http'|'parse', status?: number, path?: string}} info
   */
  constructor(message, { kind, status = null, path = null } = {}) {
    super(message);
    this.name = 'BeezyApiError';
    this.kind = kind;
    this.status = status;
    this.path = path;
  }
}

// -- pure formatting helpers ---------------------------------------------

/** American odds with an explicit plus sign: 110 -> "+110", -120 -> "-120". */
export function formatAmerican(odds) {
  const n = Number(odds);
  if (odds === null || odds === undefined || odds === '' || !Number.isFinite(n) || n === 0) return 'n/a';
  const rounded = Math.round(n);
  return rounded > 0 ? `+${rounded}` : String(rounded);
}

/** A 0-1 fraction as a percent string: 0.5132 -> "51.3%". signed adds a + for positives. */
export function formatPct(fraction, { digits = 1, signed = false } = {}) {
  const n = Number(fraction);
  if (fraction === null || fraction === undefined || fraction === '' || !Number.isFinite(n)) return 'n/a';
  const text = `${(n * 100).toFixed(digits)}%`;
  return signed && n > 0 ? `+${text}` : text;
}

/** A value that is already a percent (e.g. roi_pct 8.2) -> "+8.2%". */
export function formatPctPoints(value, { digits = 1, signed = true } = {}) {
  const n = Number(value);
  if (value === null || value === undefined || value === '' || !Number.isFinite(n)) return 'n/a';
  const text = `${n.toFixed(digits)}%`;
  return signed && n > 0 ? `+${text}` : text;
}

/** Plain number, trimmed: 24.5 -> "24.5", 3 -> "3", null -> "n/a". */
export function formatNum(value, digits = 1) {
  const n = Number(value);
  if (value === null || value === undefined || value === '' || !Number.isFinite(n)) return 'n/a';
  return Number.isInteger(n) ? String(n) : n.toFixed(digits).replace(/\.?0+$/, '');
}

/**
 * Quarter-Kelly stake as a fraction of bankroll, for a given fair probability
 * and American odds. Returns null when there is no positive edge or inputs
 * are missing.
 */
export function quarterKellyFraction(pFair, american) {
  const p = Number(pFair);
  const a = Number(american);
  if (!Number.isFinite(p) || !Number.isFinite(a) || a === 0 || p <= 0 || p >= 1) return null;
  const b = a > 0 ? a / 100 : 100 / Math.abs(a);
  const full = (b * p - (1 - p)) / b;
  if (!(full > 0)) return null;
  return full / 4;
}

// -- request core ---------------------------------------------------------

function resolveConfig(opts = {}) {
  const baseUrl = (opts.baseUrl ?? process.env.BETTING_API_URL ?? '').trim().replace(/\/+$/, '');
  const apiKey = opts.apiKey ?? process.env.BETTING_API_KEY ?? '';
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  return { baseUrl, apiKey, fetchImpl, timeoutMs };
}

/** Build a query string, skipping undefined/null/empty values. */
export function buildQuery(params = {}) {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    usp.set(key, String(value));
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : '';
}

/** GET {baseUrl}{path}?{params} with X-API-Key and a hard timeout. Returns parsed JSON. */
export async function apiGet(path, params = {}, opts = {}) {
  const { baseUrl, apiKey, fetchImpl, timeoutMs } = resolveConfig(opts);
  if (!baseUrl) throw new BeezyApiError('The Beezy API is not configured (BETTING_API_URL is unset).', { kind: 'config', path });
  if (!apiKey) throw new BeezyApiError('The Beezy API is not configured (BETTING_API_KEY is unset).', { kind: 'config', path });
  if (typeof fetchImpl !== 'function') throw new BeezyApiError('No fetch implementation available.', { kind: 'config', path });

  const url = `${baseUrl}${path}${buildQuery(params)}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let resp;
    try {
      resp = await fetchImpl(url, {
        method: 'GET',
        headers: { 'X-API-Key': apiKey, Accept: 'application/json' },
        signal: controller.signal,
      });
    } catch (err) {
      if (err?.name === 'AbortError' || controller.signal.aborted) {
        throw new BeezyApiError(`The Beezy API timed out after ${Math.round(timeoutMs / 1000)}s.`, { kind: 'timeout', path });
      }
      throw new BeezyApiError('Could not reach the Beezy API.', { kind: 'network', path });
    }

    let text;
    try {
      text = await resp.text();
    } catch (err) {
      if (err?.name === 'AbortError' || controller.signal.aborted) {
        throw new BeezyApiError(`The Beezy API timed out after ${Math.round(timeoutMs / 1000)}s.`, { kind: 'timeout', path });
      }
      throw new BeezyApiError('Could not read the Beezy API response.', { kind: 'network', path });
    }

    if (!resp.ok) {
      let detail = '';
      try {
        const body = JSON.parse(text);
        if (body && typeof body.error === 'string') detail = body.error.split(apiKey).join('[redacted]').slice(0, 160);
      } catch { /* non-JSON error body */ }
      let message = `The Beezy API returned HTTP ${resp.status}.`;
      if (resp.status === 401 || resp.status === 403) message = 'The Beezy API rejected the bot credentials.';
      else if (resp.status === 404) message = 'That Beezy API endpoint is not available yet.';
      else if (resp.status === 429) message = 'The Beezy API is rate limiting requests. Try again shortly.';
      else if (resp.status >= 500) message = 'The Beezy API had a server error. Try again shortly.';
      if (detail && resp.status < 500 && resp.status !== 401 && resp.status !== 403) message += ` (${detail})`;
      throw new BeezyApiError(message, { kind: 'http', status: resp.status, path });
    }

    try {
      return JSON.parse(text);
    } catch {
      throw new BeezyApiError('The Beezy API returned a response that is not valid JSON.', { kind: 'parse', path });
    }
  } finally {
    clearTimeout(timer);
  }
}

function cleanSport(sport) {
  if (!sport) return undefined;
  const s = String(sport).toLowerCase();
  return s === 'all' ? undefined : s;
}

function arrayOr(value) {
  return Array.isArray(value) ? value : [];
}

// -- endpoints ------------------------------------------------------------

/** GET /api/public/ev/feed (sport, min_ev, limit). Returns the raw nested feed. */
export async function fetchEvFeed({ sport, minEv, limit } = {}, opts = {}) {
  return apiGet('/api/public/ev/feed', { sport: cleanSport(sport), min_ev: minEv, limit }, opts);
}

/**
 * Flatten the nested EV feed ({mlb:{rows}, nfl:{rows, book_outliers}, nhl, nba})
 * into one list of plays sorted by EV descending.
 *
 * play: { sport, source, player, stat, line, side, book, american, price_text,
 *         ev, p_fair, n_books, validated, flag, matchup }
 */
export function normalizeEvFeed(feed, { book, sport, minEv } = {}) {
  const plays = [];
  if (!feed || typeof feed !== 'object') return plays;
  const wantBook = book ? String(book).toLowerCase() : null;
  const wantSport = cleanSport(sport);

  for (const row of arrayOr(feed.mlb?.rows)) {
    plays.push({
      sport: 'mlb',
      source: 'ev_alert',
      player: row.player ?? null,
      stat: row.bet_type ?? null,
      line: null,
      side: null,
      book: row.book ?? null,
      american: row.odds ?? null,
      price_text: formatAmerican(row.odds),
      ev: row.edge ?? null,
      p_fair: row.model_prob ?? null,
      n_books: row.n_books ?? null,
      validated: true,
      flag: true,
      matchup: row.away_team && row.home_team ? `${row.away_team} @ ${row.home_team}` : null,
    });
  }

  for (const key of ['nfl', 'nhl', 'nba']) {
    const block = feed[key];
    if (!block || typeof block !== 'object') continue;
    const validated = block.validated === true;
    for (const row of arrayOr(block.rows)) {
      if (row.net_ev !== undefined || row.ask !== undefined) {
        // Kalshi / exchange order-book row.
        plays.push({
          sport: key,
          source: 'exchange',
          player: row.player ?? null,
          stat: row.stat ?? null,
          line: row.strike ?? null,
          side: row.side ?? null,
          book: row.venue ?? 'exchange',
          american: null,
          price_text: Number.isFinite(Number(row.ask)) ? `${Math.round(Number(row.ask) * 100)}c` : 'n/a',
          ev: row.net_ev ?? null,
          p_fair: row.fair_prob ?? null,
          n_books: row.n_books ?? null,
          validated,
          flag: row.flag !== false,
          matchup: null,
        });
      } else {
        plays.push(outlierToPlay(key, row, validated));
      }
    }
    for (const row of arrayOr(block.book_outliers)) plays.push(outlierToPlay(key, row, validated));
  }

  return plays
    .filter((p) => (wantSport ? p.sport === wantSport : true))
    .filter((p) => (wantBook ? String(p.book || '').toLowerCase().includes(wantBook) : true))
    .filter((p) => (Number.isFinite(minEv) ? Number(p.ev) >= minEv : true))
    .sort((a, b) => (Number(b.ev) || -Infinity) - (Number(a.ev) || -Infinity));
}

function outlierToPlay(sport, row, validated) {
  return {
    sport: row.sport ?? sport,
    source: 'book_outlier',
    player: row.player ?? null,
    stat: row.stat ?? row.market ?? null,
    line: row.point ?? row.line ?? null,
    side: row.side ?? null,
    book: row.book ?? null,
    american: row.american ?? null,
    price_text: formatAmerican(row.american),
    ev: row.ev ?? null,
    p_fair: row.p_fair ?? null,
    n_books: row.n_books ?? null,
    validated,
    flag: row.flag !== false,
    matchup: row.away_team && row.home_team ? `${row.away_team} @ ${row.home_team}` : null,
  };
}

/** GET /api/public/projections (sport, market, limit). Response: {sport, as_of, engine, validated, status, rows}. */
export async function fetchProjections({ sport, market, limit } = {}, opts = {}) {
  const data = await apiGet('/api/public/projections', { sport: cleanSport(sport), market, limit }, opts);
  return { ...data, rows: arrayOr(data?.rows) };
}

/** Case-insensitive player-name match: every whitespace-separated query token must appear in the name. */
export function matchesPlayer(rowPlayer, query) {
  const name = String(rowPlayer || '').toLowerCase();
  const tokens = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!tokens.length) return true;
  return tokens.every((t) => name.includes(t));
}

/** GET /api/public/edge/cockpit (sport, limit), then filter rows client-side by player name. */
export async function fetchCockpit({ sport, player, limit = 200 } = {}, opts = {}) {
  const data = await apiGet('/api/public/edge/cockpit', { sport: cleanSport(sport), limit }, opts);
  const rows = arrayOr(data?.rows);
  return {
    ...data,
    rows: player ? rows.filter((r) => matchesPlayer(r?.player, player)) : rows,
  };
}

/** GET /api/public/analytics/deep-dive (system, optional sport). */
export async function fetchAnalytics({ system, sport } = {}, opts = {}) {
  return apiGet('/api/public/analytics/deep-dive', { system, sport: cleanSport(sport) }, opts);
}
