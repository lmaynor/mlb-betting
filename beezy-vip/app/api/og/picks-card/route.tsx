// beezy-vip/app/api/og/picks-card/route.tsx
// 1200x675 PNG card -- optimized for Twitter iOS (16:9)
// Usage: GET /api/og/picks-card

import { ImageResponse } from "next/og";

export const runtime = "edge";

const BEEZY_API_URL = process.env.BETTING_API_URL!;
const BEEZY_API_KEY = process.env.BETTING_API_KEY!;

const SYSTEMS: Record<string, { bg: string; border: string; color: string }> = {
  NRFI:  { bg: "#041a0f", border: "#10b98140", color: "#10b981" },
  HR:    { bg: "#1a1000", border: "#f59e0b40", color: "#f59e0b" },
  F5:    { bg: "#071228", border: "#3b82f640", color: "#3b82f6" },
  K:     { bg: "#100a1e", border: "#a78bfa40", color: "#a78bfa" },
  OUTS:  { bg: "#1a0d00", border: "#fb923c40", color: "#fb923c" },
};

function sys(system: string) {
  return SYSTEMS[system] ?? { bg: "#111114", border: "#ffffff20", color: "#f5f5f7" };
}

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatEdge(edge: number | string): string {
  const n = typeof edge === "string" ? parseFloat(edge) : edge;
  return `+${n.toFixed(1)}%`;
}

function formatGame(pick: any): string {
  const away = pick.away_team ?? pick.game ?? "";
  const home = pick.home_team ?? "";
  if (away && home) return `${away} @ ${home}`;
  if (away) return away;
  return "—";
}

function pickLabel(betType: string, system: string): string {
  if (betType === "HR")    return "HR Yes";
  if (betType === "NRFI")  return "No Run 1st Inn";
  if (betType === "YRFI")  return "Run 1st Inn";
  if (betType === "HOME")  return system === "F5" ? "F5 Home ML" : "Home ML";
  if (betType === "AWAY")  return system === "F5" ? "F5 Away ML" : "Away ML";
  if (betType === "1I_HOME") return "1st Inn Home";
  if (betType === "1I_AWAY") return "1st Inn Away";
  if (betType === "1I_DRAW") return "1st Inn Draw";
  if (betType.startsWith("K_OVER"))    return `Over ${betType.split("_")[2]} Ks`;
  if (betType.startsWith("K_UNDER"))   return `Under ${betType.split("_")[2]} Ks`;
  if (betType.startsWith("OUTS_OVER")) return `Over ${betType.split("_")[2]} Outs`;
  if (betType.startsWith("OUTS_UNDER"))return `Under ${betType.split("_")[2]} Outs`;
  return betType;
}

export async function GET() {
  const [picksRes, statsRes] = await Promise.all([
    fetch(`${BEEZY_API_URL}/api/public/picks/today`, {
      headers: { "X-API-Key": BEEZY_API_KEY },
      cache: "no-store",
    }),
    fetch(`${BEEZY_API_URL}/api/public/stats/summary`, {
      headers: { "X-API-Key": BEEZY_API_KEY },
      cache: "no-store",
    }),
  ]);

  const picksData = await picksRes.json();
  const statsData = await statsRes.json();

  const allPicks: any[] = Array.isArray(picksData)
    ? picksData
    : picksData.picks ?? [];

  // Top 5 by edge descending, kelly_triggered only
  const top = allPicks
    .filter((p) => p.kelly_triggered)
    .sort((a, b) => parseFloat(b.edge) - parseFloat(a.edge))
    .slice(0, 5);

  const overall = statsData?.overall ?? {};
  const roi = overall.roi ?? null;
  const totalBets = overall.total_bets ?? "—";
  const winRate = overall.win_rate ?? "—";

  const today = new Date().toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "America/Chicago",
  }).toUpperCase();

  // Row height depends on whether notes exist
  const hasNotes = top.some((p) => p.notes);
  const rowH = hasNotes ? 80 : 62;
  const picksH = top.length * (rowH + 8) + 8;
  const totalH = Math.max(580, 130 + picksH + 90);

  return new ImageResponse(
    (
      <div
        style={{
          width: "1200px",
          height: `${totalH}px`,
          background: "#0a0a0c",
          display: "flex",
          flexDirection: "column",
          fontFamily: "monospace",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Subtle dot grid */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage: "radial-gradient(circle, #10b98108 1px, transparent 1px)",
            backgroundSize: "32px 32px",
          }}
        />

        {/* Green accent line top */}
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: "3px", background: "linear-gradient(90deg, #10b981, #10b98140)" }} />

        {/* HEADER */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "28px 48px 20px",
            borderBottom: "0.5px solid #1f1f24",
            position: "relative",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            {/* Logo */}
            <div style={{ display: "flex", alignItems: "baseline", gap: "2px" }}>
              <span style={{ fontSize: "32px", fontWeight: 800, color: "#f5f5f7", letterSpacing: "-1px" }}>
                beezy
              </span>
              <span style={{ fontSize: "32px", fontWeight: 800, color: "#10b981", letterSpacing: "-1px" }}>
                .vip
              </span>
            </div>
            {/* Divider */}
            <div style={{ width: "1px", height: "24px", background: "#2a2a30" }} />
            <span style={{ fontSize: "12px", color: "#52525b", letterSpacing: "2px" }}>
              MODEL-DRIVEN MLB PICKS
            </span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "2px" }}>
            <span style={{ fontSize: "13px", color: "#71717a", letterSpacing: "1.5px" }}>{today}</span>
            <span style={{ fontSize: "11px", color: "#3f3f46" }}>TOP {top.length} BY EDGE</span>
          </div>
        </div>

        {/* PICKS */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            padding: "16px 48px 8px",
            gap: "8px",
          }}
        >
          {top.length === 0 ? (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "#3f3f46", fontSize: "20px", letterSpacing: "2px" }}>
              NO PICKS TODAY
            </div>
          ) : (
            top.map((pick, i) => {
              const s = sys(pick.system);
              const game = formatGame(pick);
              const label = pickLabel(pick.bet_type, pick.system);
              const notes = pick.notes ?? "";

              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    background: "#0e0e11",
                    border: `0.5px solid #1f1f24`,
                    borderLeft: `3px solid ${s.color}`,
                    borderRadius: "6px",
                    padding: "12px 20px",
                    gap: "6px",
                  }}
                >
                  {/* Main row */}
                  <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
                    {/* Rank */}
                    <span style={{ fontSize: "11px", color: "#3f3f46", minWidth: "16px" }}>
                      #{i + 1}
                    </span>

                    {/* System pill */}
                    <div
                      style={{
                        background: s.bg,
                        border: `0.5px solid ${s.border}`,
                        borderRadius: "4px",
                        padding: "3px 10px",
                        fontSize: "11px",
                        fontWeight: 700,
                        color: s.color,
                        letterSpacing: "1px",
                        minWidth: "48px",
                        textAlign: "center",
                      }}
                    >
                      {pick.system}
                    </div>

                    {/* Game */}
                    <span style={{ fontSize: "15px", color: "#a1a1aa", flex: 1, minWidth: "180px" }}>
                      {game}
                    </span>

                    {/* Pick label */}
                    <span style={{ fontSize: "15px", color: "#f5f5f7", fontWeight: 600, minWidth: "160px" }}>
                      {label}
                    </span>

                    {/* Odds */}
                    <span style={{ fontSize: "14px", color: "#71717a", minWidth: "60px", textAlign: "right" }}>
                      {formatOdds(pick.odds)}
                    </span>

                    {/* Edge -- prominent */}
                    <div
                      style={{
                        background: "#041a0f",
                        border: "0.5px solid #10b98130",
                        borderRadius: "4px",
                        padding: "4px 12px",
                        minWidth: "80px",
                        textAlign: "center",
                      }}
                    >
                      <span style={{ fontSize: "16px", fontWeight: 800, color: "#10b981" }}>
                        {formatEdge(pick.edge)}
                      </span>
                    </div>

                    {/* Stake */}
                    <span style={{ fontSize: "12px", color: "#3f3f46", minWidth: "44px", textAlign: "right" }}>
                      {pick.stake ? `${parseFloat(pick.stake).toFixed(2)}u` : ""}
                    </span>
                  </div>

                  {/* Notes / rationale subtext */}
                  {notes ? (
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", paddingLeft: "30px" }}>
                      <span style={{ fontSize: "11px", color: "#10b98170", letterSpacing: "1px" }}>▸</span>
                      <span style={{ fontSize: "12px", color: "#52525b", fontStyle: "italic" }}>
                        {notes}
                      </span>
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>

        {/* FOOTER */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "14px 48px 20px",
            borderTop: "0.5px solid #1f1f24",
            position: "relative",
          }}
        >
          {/* Season stats */}
          <div style={{ display: "flex", gap: "36px", alignItems: "center" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "9px", color: "#3f3f46", letterSpacing: "1.5px" }}>SEASON ROI</span>
              <span style={{
                fontSize: "18px",
                fontWeight: 800,
                color: roi !== null && parseFloat(roi) >= 0 ? "#10b981" : "#ef4444",
              }}>
                {roi !== null
                  ? `${parseFloat(roi) > 0 ? "+" : ""}${parseFloat(roi).toFixed(1)}%`
                  : "—"}
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "9px", color: "#3f3f46", letterSpacing: "1.5px" }}>BETS TRACKED</span>
              <span style={{ fontSize: "18px", fontWeight: 800, color: "#f5f5f7" }}>{totalBets}</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "9px", color: "#3f3f46", letterSpacing: "1.5px" }}>WIN RATE</span>
              <span style={{ fontSize: "18px", fontWeight: 800, color: "#f5f5f7" }}>
                {winRate !== "—" ? `${parseFloat(winRate).toFixed(1)}%` : "—"}
              </span>
            </div>
          </div>

          {/* CTAs */}
          <div style={{ display: "flex", gap: "28px", alignItems: "center" }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "3px" }}>
              <span style={{ fontSize: "9px", color: "#3f3f46", letterSpacing: "1.5px" }}>FULL PICKS + METHODOLOGY</span>
              <span style={{ fontSize: "16px", color: "#10b981", fontWeight: 700, letterSpacing: "-0.5px" }}>
                beezy.vip
              </span>
            </div>
            <div style={{ width: "1px", height: "36px", background: "#1f1f24" }} />
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "3px" }}>
              <span style={{ fontSize: "9px", color: "#3f3f46", letterSpacing: "1.5px" }}>JOIN THE COMMUNITY</span>
              <span style={{ fontSize: "16px", color: "#a78bfa", fontWeight: 700 }}>
                discord.gg/HfMYCmbmE
              </span>
            </div>
          </div>
        </div>
      </div>
    ),
    {
      width: 1200,
      height: totalH,
    }
  );
}
