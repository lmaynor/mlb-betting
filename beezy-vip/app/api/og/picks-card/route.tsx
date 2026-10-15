import { ImageResponse } from "next/og";
export const runtime = "edge";

const API = process.env.BETTING_API_URL!;
const KEY = process.env.BETTING_API_KEY!;

// Per-system identity colors (match lib/tokens.ts SYSTEM_COLOR), with dark
// tinted backgrounds approximating color-mix(in oklab, <hue> 16%, carbon).
const SYS: Record<string, { bg: string; accent: string }> = {
  NRFI:        { bg: "#122422", accent: "#5fd0a0" },
  "1IOU":      { bg: "#122422", accent: "#5fd0a0" },
  HR:          { bg: "#291525", accent: "#ee6fae" },
  F5:          { bg: "#0f1d30", accent: "#4ea6f5" },
  K:           { bg: "#1e182f", accent: "#a987f0" },
  OUTS:        { bg: "#291c16", accent: "#ef9a52" },
  GAME:        { bg: "#292213", accent: "#e3b261" },
  BATTER_TB:   { bg: "#202813", accent: "#a9d166" },
  BATTER_HITS: { bg: "#122624", accent: "#4fc7bd" },
  PITCHER_ER:  { bg: "#291714", accent: "#ef7f6e" },
  SB:          { bg: "#26240f", accent: "#d9cf5a" },
};
// short display name so the card never shows raw registry keys
const SYS_NAME: Record<string, string> = {
  "1IOU": "NRFI", BATTER_TB: "TB", BATTER_HITS: "HITS", PITCHER_ER: "ER",
};
const sysName = (sys: string) => SYS_NAME[sys] ?? sys;
const s = (sys: string) => SYS[sys] ?? { bg: "#171a24", accent: "#9ca3af" };

const fmtOdds = (o: number) => o > 0 ? `+${o}` : `${o}`;
const fmtEdge = (e: any) => `+${parseFloat(e).toFixed(1)}%`;
const fmtGame = (p: any) => {
  const a = p.away_team ?? "", h = p.home_team ?? "";
  return a && h ? `${a} @ ${h}` : p.game ?? "TBD";
};
const fmtPick = (bt: string, sys: string) => {
  if (bt === "HR") return "HR Yes";
  if (bt === "NRFI") return "No Run 1st";
  if (bt === "YRFI") return "Run 1st";
  if (bt === "HOME") return sys === "F5" ? "F5 Home ML" : "Home ML";
  if (bt === "AWAY") return sys === "F5" ? "F5 Away ML" : "Away ML";
  // K_OVER_7.5 / OUTS_UNDER_14.5 / BATTER_TB_OVER_1.5 / SB_UNDER_0.5 / ... and
  // the one-sided 2+/3+ threshold sub-markets (K_2PLUS_2.0 etc, added
  // 2026-08-19 -- side is "{N}PLUS" instead of "OVER"/"UNDER", same line
  // repeated after it, so the line is dropped rather than printed twice).
  const m = bt.match(/^(K|OUTS|BATTER_TB|BATTER_HITS|PITCHER_ER|BATTER_K|SB)_(OVER|UNDER|\d+PLUS)_([\d.]+)$/);
  if (m) {
    const NOUN: Record<string, string> = {
      K: "Ks", OUTS: "Outs", BATTER_TB: "TB", BATTER_HITS: "Hits",
      PITCHER_ER: "ER", BATTER_K: "Batter Ks", SB: "SB",
    };
    const noun = NOUN[m[1]] ?? m[1];
    const plus = m[2].match(/^(\d+)PLUS$/);
    if (plus) return `${plus[1]}+ ${noun}`;
    return `${m[2] === "OVER" ? "Over" : "Under"} ${m[3]} ${noun}`;
  }
  if (bt.startsWith("GAME_")) return bt === "GAME_HOME" ? "Home ML" : "Away ML";
  return bt.replace(/_/g, " ");
};

export async function GET() {
  const [pr, sr] = await Promise.all([
    fetch(`${API}/api/public/picks/today`, { headers: { "X-API-Key": KEY }, cache: "no-store" }),
    fetch(`${API}/api/public/stats/summary`, { headers: { "X-API-Key": KEY }, cache: "no-store" }),
  ]);
  const pd = await pr.json();
  const sd = await sr.json();
  const all: any[] = Array.isArray(pd) ? pd : pd.picks ?? [];
  // Card showcases model picks -- exclude pooled +EV alerts (system="EV", a
  // cross-market soft-line/Kalshi scanner, not a model prediction; see
  // lib/tokens.ts resolveEvUnderlying for the equivalent exclusion on the
  // Daily Card).
  const top = all.filter(p => p.kelly_triggered && p.system !== "EV").sort((a, b) => parseFloat(b.edge) - parseFloat(a.edge)).slice(0, 5);
  const ov = sd?.overall ?? {};
  const roi = ov.roi ?? null;
  const roiNum = roi !== null ? parseFloat(roi) : null;
  const today = new Date().toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "America/Chicago" }).toUpperCase();

  // Taller rows = better readability on mobile
  const ROW_H = 96;
  const HEADER_H = 88;
  const FOOTER_H = 88;
  const GAP = 10;
  const VPAD = 24;
  const picksH = top.length > 0 ? top.length * ROW_H + (top.length - 1) * GAP : ROW_H;
  const H = HEADER_H + VPAD + picksH + VPAD + FOOTER_H;
  const W = 1200;

  return new ImageResponse((
    <div style={{ width: `${W}px`, height: `${H}px`, background: "#090a0f", display: "flex", flexDirection: "column", fontFamily: "monospace" }}>

      {/* top bar */}
      <div style={{ display: "flex", position: "absolute", top: 0, left: 0, right: 0, height: "3px", background: "linear-gradient(90deg,#57f287,#57f28700)" }} />

      {/* HEADER */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 52px", height: `${HEADER_H}px`, borderBottom: "1px solid #1e2230" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "18px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ display: "flex", width: "11px", height: "11px", borderRadius: "50%", background: "#57f287" }} />
            <div style={{ display: "flex", alignItems: "baseline" }}>
              <span style={{ fontSize: "34px", fontWeight: 800, color: "#ffffff", letterSpacing: "-1.5px" }}>BEEZY</span>
              <span style={{ fontSize: "34px", fontWeight: 800, color: "#57f287", letterSpacing: "-1.5px" }}>.FYI</span>
            </div>
          </div>
          <div style={{ display: "flex", width: "1px", height: "28px", background: "#2e3446" }} />
          <span style={{ fontSize: "12px", color: "#7d8596", letterSpacing: "2.5px" }}>MODEL-DRIVEN MLB PICKS</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
          <span style={{ fontSize: "14px", color: "#9ca3af", letterSpacing: "1px" }}>{today}</span>
          <span style={{ fontSize: "10px", color: "#7d8596", letterSpacing: "1.5px" }}>TOP {top.length} PICKS BY EDGE</span>
        </div>
      </div>

      {/* PICKS */}
      <div style={{ display: "flex", flexDirection: "column", padding: `${VPAD}px 52px`, gap: `${GAP}px` }}>
        {top.length === 0 ? (
          <div style={{ display: "flex", height: `${ROW_H}px`, alignItems: "center", justifyContent: "center", color: "#7d8596", fontSize: "18px", letterSpacing: "3px" }}>
            NO PICKS TODAY
          </div>
        ) : top.map((pick, i) => {
          const c = s(pick.system);
          const notes = (pick.notes ?? "").trim();
          return (
            <div key={i} style={{ display: "flex", height: `${ROW_H}px`, alignItems: "center", background: "#11131a", borderLeft: `4px solid ${c.accent}`, borderTop: "1px solid #1e2230", borderRight: "1px solid #1e2230", borderBottom: "1px solid #1e2230", borderRadius: "10px", padding: "0 24px 0 20px", gap: "16px" }}>

              {/* rank */}
              <span style={{ fontSize: "13px", color: "#7d8596", minWidth: "24px", fontWeight: 700 }}>#{i + 1}</span>

              {/* system pill */}
              <div style={{ display: "flex", background: c.bg, border: `1px solid ${c.accent}55`, borderRadius: "999px", padding: "6px 14px", minWidth: "60px", justifyContent: "center" }}>
                <span style={{ fontSize: "13px", fontWeight: 800, color: c.accent, letterSpacing: "1px" }}>{sysName(pick.system)}</span>
              </div>

              {/* game */}
              <div style={{ display: "flex", flex: 1, flexDirection: "column", gap: "4px", minWidth: "180px" }}>
                <span style={{ fontSize: "17px", color: "#ffffff", fontWeight: 600 }}>{fmtGame(pick)}</span>
                {notes !== "" && (
                  <span style={{ fontSize: "12px", color: "#7d8596" }}>{notes}</span>
                )}
              </div>

              {/* pick label */}
              <span style={{ fontSize: "16px", color: "#9ca3af", minWidth: "160px" }}>{fmtPick(pick.bet_type, pick.system)}</span>

              {/* odds */}
              <span style={{ fontSize: "15px", color: "#9ca3af", minWidth: "65px", textAlign: "right" }}>{fmtOdds(pick.odds)}</span>

              {/* edge -- hero number */}
              <div style={{ display: "flex", background: "#15241e", border: "1px solid #2f553b", borderRadius: "10px", padding: "8px 18px", minWidth: "96px", justifyContent: "center", alignItems: "center" }}>
                <span style={{ fontSize: "22px", fontWeight: 900, color: "#57f287", letterSpacing: "-0.5px" }}>{fmtEdge(pick.edge)}</span>
              </div>

              {/* stake */}
              <span style={{ fontSize: "12px", color: "#7d8596", minWidth: "48px", textAlign: "right" }}>
                {pick.stake ? `${parseFloat(pick.stake).toFixed(2)}u` : ""}
              </span>
            </div>
          );
        })}
      </div>

      {/* FOOTER */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 52px", height: `${FOOTER_H}px`, borderTop: "1px solid #1e2230" }}>
        {/* stats */}
        <div style={{ display: "flex", gap: "40px", alignItems: "center" }}>
          {[
            { label: "SEASON ROI", value: roiNum !== null ? `${roiNum > 0 ? "+" : ""}${roiNum.toFixed(1)}%` : "\u2014", color: roiNum !== null ? (roiNum >= 0 ? "#57f287" : "#ef4444") : "#ffffff" },
            { label: "BETS TRACKED", value: String(ov.total_bets ?? "\u2014"), color: "#ffffff" },
            { label: "WIN RATE", value: ov.win_rate ? `${parseFloat(ov.win_rate).toFixed(1)}%` : "\u2014", color: "#ffffff" },
          ].map((stat, i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <span style={{ fontSize: "9px", color: "#7d8596", letterSpacing: "2px" }}>{stat.label}</span>
              <span style={{ fontSize: "22px", fontWeight: 800, color: stat.color }}>{stat.value}</span>
            </div>
          ))}
        </div>
        {/* ctas */}
        <div style={{ display: "flex", gap: "32px", alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
            <span style={{ fontSize: "9px", color: "#7d8596", letterSpacing: "2px" }}>FULL PICKS + METHODOLOGY</span>
            <span style={{ fontSize: "20px", color: "#57f287", fontWeight: 800, letterSpacing: "-0.5px" }}>beezy.fyi</span>
          </div>
          <div style={{ display: "flex", width: "1px", height: "40px", background: "#1e2230" }} />
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
            <span style={{ fontSize: "9px", color: "#7d8596", letterSpacing: "2px" }}>JOIN THE COMMUNITY</span>
            <span style={{ fontSize: "20px", color: "#8b92f0", fontWeight: 800 }}>discord.gg/HfMYCmbmE</span>
          </div>
        </div>
      </div>

    </div>
  ), { width: W, height: H });
}
