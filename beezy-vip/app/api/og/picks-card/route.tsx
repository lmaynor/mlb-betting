// beezy-vip/app/api/og/picks-card/route.tsx
// Returns a 1200x630 PNG card of today's top picks.
// Usage: GET /api/og/picks-card
// Tweet drafter fetches this URL and uploads the image to Typefully.

import { ImageResponse } from "next/og";

export const runtime = "edge";

const BEEZY_API_URL = process.env.BETTING_API_URL!;
const BEEZY_API_KEY = process.env.BETTING_API_KEY!;

const SYSTEM_COLORS: Record<string, { bg: string; color: string; label: string }> = {
  NRFI:  { bg: "#052016", color: "#10b981", label: "NRFI"  },
  HR:    { bg: "#1a1200", color: "#f59e0b", label: "HR"    },
  F5:    { bg: "#0a1628", color: "#3b82f6", label: "F5"    },
  K:     { bg: "#130d1f", color: "#a78bfa", label: "K"     },
  OUTS:  { bg: "#1a0d00", color: "#fb923c", label: "OUTS"  },
};

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatEdge(edge: number | string): string {
  const n = typeof edge === "string" ? parseFloat(edge) : edge;
  return `+${n.toFixed(1)}%`;
}

function pickLabel(betType: string): string {
  if (betType === "HR") return "HR Yes";
  if (betType === "NRFI") return "No Run 1st";
  if (betType === "YRFI") return "Run 1st";
  if (betType === "HOME" || betType === "F5_HOME") return "F5 Home ML";
  if (betType === "AWAY" || betType === "F5_AWAY") return "F5 Away ML";
  if (betType.startsWith("K_OVER"))  return `Over ${betType.split("_")[2]} Ks`;
  if (betType.startsWith("K_UNDER")) return `Under ${betType.split("_")[2]} Ks`;
  if (betType.startsWith("OUTS_OVER"))  return `Over ${betType.split("_")[2]} Outs`;
  if (betType.startsWith("OUTS_UNDER")) return `Under ${betType.split("_")[2]} Outs`;
  return betType;
}

export async function GET() {
  // Fetch today's picks + season stats in parallel
  const [picksRes, statsRes] = await Promise.all([
    fetch(`${BEEZY_API_URL}/api/public/picks/today`, {
      headers: { "X-API-Key": BEEZY_API_KEY },
    }),
    fetch(`${BEEZY_API_URL}/api/public/stats/summary`, {
      headers: { "X-API-Key": BEEZY_API_KEY },
    }),
  ]);

  const picksData = await picksRes.json();
  const statsData = await statsRes.json();

  const picks: any[] = Array.isArray(picksData)
    ? picksData
    : picksData.picks ?? [];

  // Top 5 by edge descending
  const top = picks
    .filter((p) => p.kelly_triggered)
    .sort((a, b) => parseFloat(b.edge) - parseFloat(a.edge))
    .slice(0, 5);

  const overall = statsData?.overall ?? {};
  const roi = overall.roi ?? "—";
  const totalBets = overall.total_bets ?? "—";
  const winRate = overall.win_rate ?? "—";

  const today = new Date().toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "America/Chicago",
  });

  return new ImageResponse(
    (
      <div
        style={{
          width: "1200px",
          height: "630px",
          background: "#0a0a0c",
          display: "flex",
          flexDirection: "column",
          fontFamily: "'JetBrains Mono', monospace",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Subtle grid texture */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage:
              "linear-gradient(rgba(16,185,129,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(16,185,129,0.03) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }}
        />

        {/* Green top accent line */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "2px",
            background: "#10b981",
          }}
        />

        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "32px 48px 24px",
            borderBottom: "0.5px solid #1f1f24",
            position: "relative",
          }}
        >
          {/* Wordmark */}
          <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
            <span
              style={{
                fontSize: "28px",
                fontWeight: 700,
                color: "#f5f5f7",
                letterSpacing: "-0.5px",
              }}
            >
              beezy
            </span>
            <span style={{ fontSize: "28px", fontWeight: 700, color: "#10b981" }}>
              .vip
            </span>
            <span
              style={{
                fontSize: "11px",
                color: "#71717a",
                marginLeft: "8px",
                letterSpacing: "2px",
                textTransform: "uppercase",
              }}
            >
              model-driven picks
            </span>
          </div>

          {/* Date */}
          <span style={{ fontSize: "14px", color: "#71717a", letterSpacing: "1px" }}>
            {today.toUpperCase()}
          </span>
        </div>

        {/* Picks */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            padding: "20px 48px",
            gap: "10px",
          }}
        >
          {top.length === 0 ? (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#71717a",
                fontSize: "18px",
              }}
            >
              No picks today
            </div>
          ) : (
            top.map((pick, i) => {
              const sys = SYSTEM_COLORS[pick.system] ?? {
                bg: "#111114",
                color: "#f5f5f7",
                label: pick.system,
              };
              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "16px",
                    background: "#111114",
                    border: "0.5px solid #1f1f24",
                    borderRadius: "6px",
                    padding: "12px 20px",
                  }}
                >
                  {/* System pill */}
                  <div
                    style={{
                      background: sys.bg,
                      border: `0.5px solid ${sys.color}40`,
                      borderRadius: "4px",
                      padding: "4px 10px",
                      fontSize: "11px",
                      fontWeight: 700,
                      color: sys.color,
                      letterSpacing: "1px",
                      minWidth: "52px",
                      textAlign: "center",
                    }}
                  >
                    {sys.label}
                  </div>

                  {/* Game */}
                  <span
                    style={{
                      fontSize: "14px",
                      color: "#a1a1aa",
                      minWidth: "160px",
                      flex: 1,
                    }}
                  >
                    {pick.game ?? "—"}
                  </span>

                  {/* Pick label */}
                  <span
                    style={{
                      fontSize: "14px",
                      color: "#f5f5f7",
                      fontWeight: 600,
                      minWidth: "140px",
                    }}
                  >
                    {pickLabel(pick.bet_type)}
                  </span>

                  {/* Odds */}
                  <span
                    style={{
                      fontSize: "14px",
                      color: "#a1a1aa",
                      minWidth: "60px",
                      textAlign: "right",
                    }}
                  >
                    {formatOdds(pick.odds)}
                  </span>

                  {/* Edge */}
                  <span
                    style={{
                      fontSize: "15px",
                      fontWeight: 700,
                      color: "#10b981",
                      minWidth: "72px",
                      textAlign: "right",
                    }}
                  >
                    {formatEdge(pick.edge)}
                  </span>

                  {/* Stake */}
                  <span
                    style={{
                      fontSize: "12px",
                      color: "#71717a",
                      minWidth: "48px",
                      textAlign: "right",
                    }}
                  >
                    {pick.stake ? `${pick.stake}u` : ""}
                  </span>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "16px 48px",
            borderTop: "0.5px solid #1f1f24",
            position: "relative",
          }}
        >
          {/* Season stats */}
          <div style={{ display: "flex", gap: "32px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "10px", color: "#71717a", letterSpacing: "1px" }}>
                SEASON ROI
              </span>
              <span
                style={{
                  fontSize: "16px",
                  fontWeight: 700,
                  color: parseFloat(roi) >= 0 ? "#10b981" : "#ef4444",
                }}
              >
                {typeof roi === "number" ? `${roi > 0 ? "+" : ""}${roi.toFixed(1)}%` : roi}
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "10px", color: "#71717a", letterSpacing: "1px" }}>
                BETS
              </span>
              <span style={{ fontSize: "16px", fontWeight: 700, color: "#f5f5f7" }}>
                {totalBets}
              </span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <span style={{ fontSize: "10px", color: "#71717a", letterSpacing: "1px" }}>
                WIN RATE
              </span>
              <span style={{ fontSize: "16px", fontWeight: 700, color: "#f5f5f7" }}>
                {winRate}
              </span>
            </div>
          </div>

          {/* CTAs */}
          <div style={{ display: "flex", gap: "24px", alignItems: "center" }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "2px" }}>
              <span style={{ fontSize: "10px", color: "#71717a", letterSpacing: "1px" }}>
                FULL PICKS + METHODOLOGY
              </span>
              <span style={{ fontSize: "14px", color: "#10b981", fontWeight: 600 }}>
                beezy.vip
              </span>
            </div>
            <div
              style={{
                width: "1px",
                height: "32px",
                background: "#1f1f24",
              }}
            />
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "2px" }}>
              <span style={{ fontSize: "10px", color: "#71717a", letterSpacing: "1px" }}>
                JOIN THE COMMUNITY
              </span>
              <span style={{ fontSize: "14px", color: "#a78bfa", fontWeight: 600 }}>
                discord.gg/HfMYCmbmE
              </span>
            </div>
          </div>
        </div>
      </div>
    ),
    {
      width: 1200,
      height: 630,
    }
  );
}
