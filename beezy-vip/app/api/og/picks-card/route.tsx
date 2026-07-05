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
};
// short display name so the card never shows raw registry keys
const SYS_NAME: Record<string, string> = {
  "1IOU": "NRFI", BATTER_TB: "TB", BATTER_HITS: "HITS", PITCHER_ER: "ER",
};
const sysName = (sys: string) => SYS_NAME[sys] ?? sys;
const s = (sys: string) => SYS[sys] ?? { bg: "#1a191b", accent: "#b5b2bc" };

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
  if (bt.startsWith("K_OVER"))     return `Over ${bt.split("_")[2]} Ks`;
  if (bt.startsWith("K_UNDER"))    return `Under ${bt.split("_")[2]} Ks`;
  if (bt.startsWith("OUTS_OVER"))  return `Over ${bt.split("_")[2]} Outs`;
  if (bt.startsWith("OUTS_UNDER")) return `Under ${bt.split("_")[2]} Outs`;
  // BATTER_TB_OVER_1.5 / BATTER_HITS_UNDER_0.5 / PITCHER_ER_OVER_2.5
  const m = bt.match(/^(BATTER_TB|BATTER_HITS|PITCHER_ER|BATTER_K)_(OVER|UNDER)_([\d.]+)$/);
  if (m) {
    const noun = m[1] === "BATTER_TB" ? "TB" : m[1] === "BATTER_HITS" ? "Hits"
      : m[1] === "PITCHER_ER" ? "ER" : "Batter Ks";
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
  const top = all.filter(p => p.kelly_triggered).sort((a, b) => parseFloat(b.edge) - parseFloat(a.edge)).slice(0, 5);
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
    <div style={{ width: `${W}px`, height: `${H}px`, background: "#04040b", display: "flex", flexDirection: "column", fontFamily: "monospace" }}>

      {/* top bar */}
      <div style={{ display: "flex", position: "absolute", top: 0, left: 0, right: 0, height: "3px", background: "linear-gradient(90deg,#71d083,#71d08300)" }} />

      {/* HEADER */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 52px", height: `${HEADER_H}px`, borderBottom: "1px solid #2b292d" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "18px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ display: "flex", width: "11px", height: "11px", borderRadius: "50%", background: "#71d083" }} />
            <div style={{ display: "flex", alignItems: "baseline" }}>
              <span style={{ fontSize: "34px", fontWeight: 800, color: "#f3f2f5", letterSpacing: "-1.5px" }}>BEEZY</span>
              <span style={{ fontSize: "34px", fontWeight: 800, color: "#71d083", letterSpacing: "-1.5px" }}>.FYI</span>
            </div>
          </div>
          <div style={{ display: "flex", width: "1px", height: "28px", background: "#323035" }} />
          <span style={{ fontSize: "12px", color: "#8a8893", letterSpacing: "2.5px" }}>MODEL-DRIVEN MLB PICKS</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
          <span style={{ fontSize: "14px", color: "#b5b2bc", letterSpacing: "1px" }}>{today}</span>
          <span style={{ fontSize: "10px", color: "#8a8893", letterSpacing: "1.5px" }}>TOP {top.length} PICKS BY EDGE</span>
        </div>
      </div>

      {/* PICKS */}
      <div style={{ display: "flex", flexDirection: "column", padding: `${VPAD}px 52px`, gap: `${GAP}px` }}>
        {top.length === 0 ? (
          <div style={{ display: "flex", height: `${ROW_H}px`, alignItems: "center", justifyContent: "center", color: "#8a8893", fontSize: "18px", letterSpacing: "3px" }}>
            NO PICKS TODAY
          </div>
        ) : top.map((pick, i) => {
          const c = s(pick.system);
          const notes = (pick.notes ?? "").trim();
          return (
            <div key={i} style={{ display: "flex", height: `${ROW_H}px`, alignItems: "center", background: "#121113", borderLeft: `4px solid ${c.accent}`, borderTop: "1px solid #2b292d", borderRight: "1px solid #2b292d", borderBottom: "1px solid #2b292d", borderRadius: "10px", padding: "0 24px 0 20px", gap: "16px" }}>

              {/* rank */}
              <span style={{ fontSize: "13px", color: "#8a8893", minWidth: "24px", fontWeight: 700 }}>#{i + 1}</span>

              {/* system pill */}
              <div style={{ display: "flex", background: c.bg, border: `1px solid ${c.accent}55`, borderRadius: "999px", padding: "6px 14px", minWidth: "60px", justifyContent: "center" }}>
                <span style={{ fontSize: "13px", fontWeight: 800, color: c.accent, letterSpacing: "1px" }}>{sysName(pick.system)}</span>
              </div>

              {/* game */}
              <div style={{ display: "flex", flex: 1, flexDirection: "column", gap: "4px", minWidth: "180px" }}>
                <span style={{ fontSize: "17px", color: "#f3f2f5", fontWeight: 600 }}>{fmtGame(pick)}</span>
                {notes !== "" && (
                  <span style={{ fontSize: "12px", color: "#8a8893" }}>{notes}</span>
                )}
              </div>

              {/* pick label */}
              <span style={{ fontSize: "16px", color: "#b5b2bc", minWidth: "160px" }}>{fmtPick(pick.bet_type, pick.system)}</span>

              {/* odds */}
              <span style={{ fontSize: "15px", color: "#b5b2bc", minWidth: "65px", textAlign: "right" }}>{fmtOdds(pick.odds)}</span>

              {/* edge — hero number */}
              <div style={{ display: "flex", background: "#15241e", border: "1px solid #2f553b", borderRadius: "10px", padding: "8px 18px", minWidth: "96px", justifyContent: "center", alignItems: "center" }}>
                <span style={{ fontSize: "22px", fontWeight: 900, color: "#71d083", letterSpacing: "-0.5px" }}>{fmtEdge(pick.edge)}</span>
              </div>

              {/* stake */}
              <span style={{ fontSize: "12px", color: "#8a8893", minWidth: "48px", textAlign: "right" }}>
                {pick.stake ? `${parseFloat(pick.stake).toFixed(2)}u` : ""}
              </span>
            </div>
          );
        })}
      </div>

      {/* FOOTER */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 52px", height: `${FOOTER_H}px`, borderTop: "1px solid #2b292d" }}>
        {/* stats */}
        <div style={{ display: "flex", gap: "40px", alignItems: "center" }}>
          {[
            { label: "SEASON ROI", value: roiNum !== null ? `${roiNum > 0 ? "+" : ""}${roiNum.toFixed(1)}%` : "—", color: roiNum !== null ? (roiNum >= 0 ? "#71d083" : "#ec6a6a") : "#f3f2f5" },
            { label: "BETS TRACKED", value: String(ov.total_bets ?? "—"), color: "#f3f2f5" },
            { label: "WIN RATE", value: ov.win_rate ? `${parseFloat(ov.win_rate).toFixed(1)}%` : "—", color: "#f3f2f5" },
          ].map((stat, i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <span style={{ fontSize: "9px", color: "#8a8893", letterSpacing: "2px" }}>{stat.label}</span>
              <span style={{ fontSize: "22px", fontWeight: 800, color: stat.color }}>{stat.value}</span>
            </div>
          ))}
        </div>
        {/* ctas */}
        <div style={{ display: "flex", gap: "32px", alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
            <span style={{ fontSize: "9px", color: "#8a8893", letterSpacing: "2px" }}>FULL PICKS + METHODOLOGY</span>
            <span style={{ fontSize: "20px", color: "#71d083", fontWeight: 800, letterSpacing: "-0.5px" }}>beezy.fyi</span>
          </div>
          <div style={{ display: "flex", width: "1px", height: "40px", background: "#2b292d" }} />
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
            <span style={{ fontSize: "9px", color: "#8a8893", letterSpacing: "2px" }}>JOIN THE COMMUNITY</span>
            <span style={{ fontSize: "20px", color: "#8b92f0", fontWeight: 800 }}>discord.gg/HfMYCmbmE</span>
          </div>
        </div>
      </div>

    </div>
  ), { width: W, height: H });
}
