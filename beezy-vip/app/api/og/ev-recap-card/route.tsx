import { ImageResponse } from "next/og";
import { apiGetAnalyticsDeepDive, apiGetEvFeed } from "@/lib/betting-api";
import { formatOdds } from "@/lib/odds";
import { propStatLabel, SPORT_PILLARS } from "@/lib/sports";
import { bookDisplay, BOOK_META, pickLabel, SYSTEM_LABEL } from "@/lib/tokens";
import type { AnalyticsDeepDive, Bet, EvBookOutlierRow, EvFeed, EvMlbRow, EvNflRow } from "@/lib/types";

export const runtime = "edge";

// Literal hex only: Satori does not resolve CSS var().
const C = {
  canvas: "#090a0f",
  card: "#11131a",
  border: "#1e2230",
  emerald: "#57f287",
  text: "#f3f4f6",
  sub: "#9ca3af",
  mute: "#7d8596",
  lean: "#facc15",
  loss: "#ef4444",
};

type Sport = "mlb" | "nfl" | "nhl" | "nba";

interface Play {
  sport: Sport;
  shadow: boolean;
  lowConf: boolean;
  title: string;
  matchup: string;
  bookKey: string;
  price: string;
  ev: number;
}

interface Kpi {
  label: string;
  value: string;
  sub: string;
  color: string;
}

function mlbBet(r: EvMlbRow): Bet {
  return {
    id: r.id, system: "EV", game_date: r.game_date, game_pk: r.game_pk ?? 0, bet_type: r.bet_type,
    player: r.player, away_team: r.away_team, home_team: r.home_team, odds: r.odds, stake: r.stake,
    model_prob: r.model_prob ?? 0, market_prob: r.market_prob ?? 0, edge: r.edge, kelly_pct: r.kelly_pct,
    kelly_triggered: true, result: r.result, profit: r.profit, paper: true, book: r.book, notes: r.notes,
    created_at: r.created_at,
  };
}

function gameKeyMatchup(gk: string | null | undefined): string {
  return (gk ?? "").split(":")[2]?.replace("@", " @ ") ?? "";
}

function mlbPlay(r: EvMlbRow): Play {
  return {
    sport: "mlb", shadow: false, lowConf: false,
    title: pickLabel(mlbBet(r)),
    matchup: `${r.away_team ?? ""} @ ${r.home_team ?? ""}`.trim(),
    bookKey: (r.book ?? "").toLowerCase(),
    price: formatOdds(r.odds),
    ev: r.edge ?? 0,
  };
}

function nflKalshiPlay(r: EvNflRow): Play {
  const stat = SYSTEM_LABEL[(r.stat ?? "").toUpperCase()] ?? r.stat ?? "";
  return {
    sport: "nfl", shadow: true, lowConf: r.flag === false,
    title: `${r.player ?? "?"} ${r.side === "OVER" ? "Over" : "Under"} ${r.strike ?? ""} ${stat}`.replace(/\s+/g, " ").trim(),
    matchup: gameKeyMatchup(r.game_key),
    bookKey: r.venue.toLowerCase(),
    price: `${Math.round(r.ask * 100)}\u00a2`,
    ev: r.net_ev,
  };
}

function outlierPlay(r: EvBookOutlierRow, sport: "nfl" | "nhl" | "nba"): Play {
  const point = r.point ?? r.line;
  return {
    sport, shadow: true, lowConf: r.flag === false,
    title: `${r.player} ${String(r.side).toUpperCase() === "OVER" ? "Over" : "Under"} ${point ?? ""} ${propStatLabel(r.stat ?? r.market)}`.replace(/\s+/g, " ").trim(),
    matchup: gameKeyMatchup(r.game_key) || (r.away_team && r.home_team ? `${r.away_team} @ ${r.home_team}` : ""),
    bookKey: r.book.toLowerCase(),
    price: formatOdds(r.american),
    ev: r.ev,
  };
}

/** Flatten the +EV feed. Credible rows first, then by EV: a gate-failed shadow row never headlines the card. */
function collectPlays(feed: EvFeed | null): Play[] {
  if (!feed) return [];
  const out: Play[] = [];
  for (const r of feed.mlb?.rows ?? []) if (r.result == null) out.push(mlbPlay(r));
  for (const r of feed.nfl?.book_outliers ?? []) out.push(outlierPlay(r, "nfl"));
  for (const r of feed.nfl?.rows ?? []) out.push(nflKalshiPlay(r));
  for (const r of feed.nhl?.rows ?? []) out.push(outlierPlay(r, "nhl"));
  for (const r of feed.nba?.rows ?? []) out.push(outlierPlay(r, "nba"));
  return out
    .filter(p => isFinite(p.ev) && p.ev > 0)
    .sort((a, b) => Number(a.lowConf) - Number(b.lowConf) || b.ev - a.ev);
}

/** Distinct venues actually present in the feed payload (book keys incl. all_books quotes; exchanges counted separately). */
function venuesInFeed(feed: EvFeed | null): { total: number; exchanges: number } {
  const keys = new Set<string>();
  const add = (k: string | null | undefined) => { const v = (k ?? "").trim().toLowerCase(); if (v) keys.add(v); };
  for (const r of feed?.mlb?.rows ?? []) add(r.book);
  for (const r of feed?.nfl?.rows ?? []) add(r.venue);
  const outliers: EvBookOutlierRow[] = [
    ...(feed?.nfl?.book_outliers ?? []), ...(feed?.nhl?.rows ?? []), ...(feed?.nba?.rows ?? []),
  ];
  for (const r of outliers) {
    add(r.book);
    for (const q of r.all_books ?? []) add(q.book);
  }
  let exchanges = 0;
  keys.forEach(k => { if (BOOK_META[k]?.isExchange) exchanges += 1; });
  return { total: keys.size, exchanges };
}

function money(n: number): string {
  return `$${Math.round(n).toLocaleString("en-US")}`;
}

function buildKpis(dd: AnalyticsDeepDive | null, feed: EvFeed | null): Kpi[] {
  const out: Kpi[] = [];
  const h = dd?.ev_headline;
  if (h) {
    out.push({ label: "GRADED ALERTS", value: h.graded.toLocaleString("en-US"), sub: `${h.wins.toLocaleString("en-US")}W / ${h.losses.toLocaleString("en-US")}L`, color: C.text });
    if (h.flat_roi_pct != null && isFinite(h.flat_roi_pct)) {
      out.push({
        label: "FLAT ROI", value: `${h.flat_roi_pct > 0 ? "+" : ""}${h.flat_roi_pct.toFixed(2)}%`,
        sub: `$${h.flat_stake_unit} flat, voids out`, color: h.flat_roi_pct >= 0 ? C.emerald : C.loss,
      });
    }
    let ending: number | null = isFinite(h.kelly_ending_bankroll) ? h.kelly_ending_bankroll : null;
    const curve = dd?.bankroll_curve ?? [];
    if (ending == null && curve.length > 0 && isFinite(h.bankroll)) ending = h.bankroll + curve[curve.length - 1].ev_kelly_cum;
    if (ending != null && isFinite(h.bankroll)) {
      out.push({ label: "1/4 KELLY BANKROLL", value: `${money(h.bankroll)} -> ${money(ending)}`, sub: `${Math.round(h.kelly_cap * 100)}% cap per bet`, color: ending >= h.bankroll ? C.emerald : C.loss });
    }
  }
  const v = venuesInFeed(feed);
  if (v.total > 0) {
    out.push({ label: "VENUES SCANNED", value: String(v.total), sub: v.exchanges > 0 ? `incl. ${v.exchanges} exchange${v.exchanges === 1 ? "" : "s"}` : "sportsbooks, live feed", color: C.text });
  }
  return out;
}

function SportBadge({ sport, size }: { sport: Sport; size: number }) {
  const accent = sport === "mlb" ? C.emerald : SPORT_PILLARS[sport].accent;
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minWidth: `${size * 3.4}px`, border: `1px solid ${accent}66`, borderRadius: "6px", padding: "3px 8px" }}>
      <span style={{ fontSize: `${size}px`, fontWeight: 800, color: accent, letterSpacing: "1px" }}>{sport.toUpperCase()}</span>
    </div>
  );
}

export async function GET(req: Request) {
  const vertical = new URL(req.url).searchParams.get("format") === "vertical";
  const W = vertical ? 1080 : 1200;
  const H = vertical ? 1350 : 630;

  const [ddRes, feedRes] = await Promise.allSettled([
    apiGetAnalyticsDeepDive("EV"),
    apiGetEvFeed({ minEv: 0.03, limit: 50 }),
  ]);
  const dd = ddRes.status === "fulfilled" ? ddRes.value : null;
  const feed = feedRes.status === "fulfilled" ? feedRes.value : null;

  // A malformed payload must degrade to the empty card, never a 500.
  let kpis: Kpi[] = [];
  let plays: Play[] = [];
  try { kpis = buildKpis(dd, feed); } catch { kpis = []; }
  try { plays = collectPlays(feed).slice(0, 5); } catch { plays = []; }

  // Sizing: the landscape card is dense, the vertical card has room to breathe.
  const pad = vertical ? 48 : 28;
  const rowH = vertical ? 120 : 54;
  const titleSz = vertical ? 30 : 18;
  const metaSz = vertical ? 18 : 12;
  const kpiVal = vertical ? 38 : 26;
  const kpiLab = vertical ? 15 : 10;
  const gap = vertical ? 22 : 12;

  const today = new Date().toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric", timeZone: "America/Chicago",
  }).toUpperCase();

  return new ImageResponse((
    <div style={{ width: `${W}px`, height: `${H}px`, background: C.canvas, display: "flex", flexDirection: "column", fontFamily: "monospace", padding: `${pad}px`, gap: `${gap}px` }}>

      {/* HEADER */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <div style={{ display: "flex", alignItems: "baseline" }}>
            <span style={{ fontSize: `${vertical ? 56 : 38}px`, fontWeight: 900, color: C.text, letterSpacing: "-2px" }}>BEEZY</span>
            <span style={{ fontSize: `${vertical ? 56 : 38}px`, fontWeight: 900, color: C.emerald, letterSpacing: "-2px" }}>.FYI</span>
          </div>
          <span style={{ fontSize: `${vertical ? 17 : 12}px`, color: C.sub, letterSpacing: "2px", fontWeight: 700 }}>+EV SCANNER &amp; MULTI-SPORT DISLOCATIONS</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
          <div style={{ display: "flex", border: `1px solid ${C.lean}66`, borderRadius: "999px", padding: "4px 14px" }}>
            <span style={{ fontSize: `${vertical ? 15 : 11}px`, fontWeight: 800, color: C.lean, letterSpacing: "2px" }}>PAPER RESULTS</span>
          </div>
          <span style={{ fontSize: `${metaSz}px`, color: C.mute, letterSpacing: "1px" }}>{today}</span>
        </div>
      </div>

      {/* KPI BANNER */}
      {kpis.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            {kpis.map(k => (
              <div key={k.label} style={{ display: "flex", flexDirection: "column", gap: "6px", flex: "1 1 0", minWidth: vertical ? "230px" : "0px", background: C.card, border: `1px solid ${C.border}`, borderRadius: "12px", padding: vertical ? "18px 20px" : "12px 16px" }}>
                <span style={{ fontSize: `${kpiLab}px`, color: C.mute, letterSpacing: "2px", fontWeight: 700 }}>{k.label}</span>
                <span style={{ fontSize: `${kpiVal}px`, fontWeight: 900, color: k.color, letterSpacing: "-1px", lineHeight: 1 }}>{k.value}</span>
                <span style={{ fontSize: `${metaSz - 1}px`, color: C.sub }}>{k.sub}</span>
              </div>
            ))}
          </div>
          <span style={{ fontSize: `${vertical ? 14 : 10}px`, color: C.mute, letterSpacing: "0.5px" }}>
            PAPER: flagged-price results, no slippage or limits; the same prop at several books counts several times.
          </span>
        </div>
      )}

      {/* TOP PLAYS */}
      <div style={{ display: "flex", flexDirection: "column", gap: "8px", flex: 1 }}>
        <span style={{ fontSize: `${kpiLab}px`, color: C.mute, letterSpacing: "2px", fontWeight: 700 }}>TOP +EV PLAYS RIGHT NOW &middot; MLB / NFL / NHL / NBA</span>
        {plays.length === 0 ? (
          <div style={{ display: "flex", flex: 1, alignItems: "center", justifyContent: "center", background: C.card, border: `1px solid ${C.border}`, borderRadius: "12px", color: C.mute, fontSize: `${vertical ? 26 : 18}px`, letterSpacing: "2px" }}>
            NO LIVE +EV PLAYS RIGHT NOW
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {plays.map((p, i) => {
              const book = bookDisplay(p.bookKey);
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: "14px", height: `${rowH}px`, background: C.card, border: `1px solid ${C.border}`, borderRadius: "10px", padding: "0 16px" }}>
                  <SportBadge sport={p.sport} size={vertical ? 16 : 11} />
                  <div style={{ display: "flex", flexDirection: "column", flex: 1, gap: "4px", minWidth: 0 }}>
                    <span style={{ fontSize: `${titleSz}px`, fontWeight: 800, color: C.text, lineHeight: 1.1 }}>{p.title}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      {p.matchup !== "" && <span style={{ fontSize: `${metaSz}px`, color: C.mute }}>{p.matchup}</span>}
                      {p.shadow && (
                        <div style={{ display: "flex", border: `1px solid ${C.lean}66`, borderRadius: "4px", padding: "1px 6px" }}>
                          <span style={{ fontSize: `${vertical ? 13 : 9}px`, fontWeight: 800, color: C.lean, letterSpacing: "1px" }}>SHADOW</span>
                        </div>
                      )}
                    </div>
                  </div>
                  <div style={{ display: "flex", border: `1px solid ${book.color}66`, borderRadius: "6px", padding: "3px 9px" }}>
                    <span style={{ fontSize: `${metaSz}px`, fontWeight: 800, color: book.color }}>{book.label}</span>
                  </div>
                  <span style={{ fontSize: `${titleSz}px`, fontWeight: 800, color: C.text, minWidth: `${vertical ? 90 : 62}px`, justifyContent: "flex-end", display: "flex" }}>{p.price}</span>
                  <div style={{ display: "flex", background: C.emerald, borderRadius: "8px", padding: "4px 10px", minWidth: `${vertical ? 100 : 74}px`, justifyContent: "center" }}>
                    <span style={{ fontSize: `${titleSz - 2}px`, fontWeight: 900, color: C.canvas }}>{`+${(p.ev * 100).toFixed(1)}%`}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* FOOTER */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: `${vertical ? 15 : 10}px`, color: C.mute, letterSpacing: "0.5px" }}>
          NFL / NHL / NBA rows are shadow signals: unvalidated, no stakes. Not financial advice. 21+.
        </span>
        <span style={{ fontSize: `${vertical ? 22 : 16}px`, color: C.emerald, fontWeight: 800 }}>beezy.fyi/ev</span>
      </div>
    </div>
  ), { width: W, height: H });
}
