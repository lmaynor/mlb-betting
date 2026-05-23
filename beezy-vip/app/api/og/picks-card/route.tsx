import { ImageResponse } from "next/og";
export const runtime = "edge";

const BEEZY_API_URL = process.env.BETTING_API_URL!;
const BEEZY_API_KEY = process.env.BETTING_API_KEY!;

const SYSTEMS: Record<string, { bg: string; border: string; color: string }> = {
  NRFI: { bg: "#041a0f", border: "#10b98140", color: "#10b981" },
  HR:   { bg: "#1a1000", border: "#f59e0b40", color: "#f59e0b" },
  F5:   { bg: "#071228", border: "#3b82f640", color: "#3b82f6" },
  K:    { bg: "#100a1e", border: "#a78bfa40", color: "#a78bfa" },
  OUTS: { bg: "#1a0d00", border: "#fb923c40", color: "#fb923c" },
};

function sys(s: string) {
  return SYSTEMS[s] ?? { bg: "#111114", border: "#ffffff20", color: "#f5f5f7" };
}
function fmtOdds(o: number) { return o > 0 ? `+${o}` : `${o}`; }
function fmtEdge(e: any) { return `+${parseFloat(e).toFixed(1)}%`; }
function fmtGame(p: any) {
  const a = p.away_team ?? "", h = p.home_team ?? "";
  if (a && h) return `${a} @ ${h}`;
  return p.game ?? "—";
}
function pickLabel(bt: string, sys: string) {
  if (bt === "HR") return "HR Yes";
  if (bt === "NRFI") return "No Run 1st";
  if (bt === "YRFI") return "Run 1st";
  if (bt === "HOME") return sys === "F5" ? "F5 Home ML" : "Home ML";
  if (bt === "AWAY") return sys === "F5" ? "F5 Away ML" : "Away ML";
  if (bt.startsWith("K_OVER"))     return `Over ${bt.split("_")[2]} Ks`;
  if (bt.startsWith("K_UNDER"))    return `Under ${bt.split("_")[2]} Ks`;
  if (bt.startsWith("OUTS_OVER"))  return `Over ${bt.split("_")[2]} Outs`;
  if (bt.startsWith("OUTS_UNDER")) return `Under ${bt.split("_")[2]} Outs`;
  return bt;
}

export async function GET() {
  const [pr, sr] = await Promise.all([
    fetch(`${BEEZY_API_URL}/api/public/picks/today`, { headers: { "X-API-Key": BEEZY_API_KEY }, cache: "no-store" }),
    fetch(`${BEEZY_API_URL}/api/public/stats/summary`, { headers: { "X-API-Key": BEEZY_API_KEY }, cache: "no-store" }),
  ]);
  const pd = await pr.json();
  const sd = await sr.json();
  const all: any[] = Array.isArray(pd) ? pd : pd.picks ?? [];
  const top = all.filter(p => p.kelly_triggered).sort((a,b) => parseFloat(b.edge)-parseFloat(a.edge)).slice(0,5);
  const ov = sd?.overall ?? {};
  const roi = ov.roi ?? null;
  const totalBets = ov.total_bets ?? "—";
  const winRate = ov.win_rate ?? "—";
  const today = new Date().toLocaleDateString("en-US", { weekday:"short", month:"short", day:"numeric", timeZone:"America/Chicago" }).toUpperCase();

  return new ImageResponse((
    <div style={{ width:"1200px", height:"675px", background:"#0a0a0c", display:"flex", flexDirection:"column", fontFamily:"monospace", position:"relative", overflow:"hidden" }}>

      {/* dot grid */}
      <div style={{ position:"absolute", inset:0, backgroundImage:"radial-gradient(circle, #10b98108 1px, transparent 1px)", backgroundSize:"32px 32px", display:"flex" }} />

      {/* top accent */}
      <div style={{ position:"absolute", top:0, left:0, right:0, height:"3px", background:"linear-gradient(90deg, #10b981, #10b98140)", display:"flex" }} />

      {/* HEADER */}
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"24px 48px 18px", borderBottom:"0.5px solid #1f1f24" }}>
        <div style={{ display:"flex", alignItems:"center", gap:"16px" }}>
          <div style={{ display:"flex", alignItems:"baseline", gap:"2px" }}>
            <span style={{ fontSize:"30px", fontWeight:800, color:"#f5f5f7", letterSpacing:"-1px" }}>beezy</span>
            <span style={{ fontSize:"30px", fontWeight:800, color:"#10b981", letterSpacing:"-1px" }}>.vip</span>
          </div>
          <div style={{ width:"1px", height:"22px", background:"#2a2a30", display:"flex" }} />
          <span style={{ fontSize:"11px", color:"#52525b", letterSpacing:"2px" }}>MODEL-DRIVEN MLB PICKS</span>
        </div>
        <div style={{ display:"flex", flexDirection:"column", alignItems:"flex-end", gap:"2px" }}>
          <span style={{ fontSize:"12px", color:"#71717a", letterSpacing:"1.5px" }}>{today}</span>
          <span style={{ fontSize:"10px", color:"#3f3f46" }}>TOP {top.length} BY EDGE</span>
        </div>
      </div>

      {/* PICKS */}
      <div style={{ display:"flex", flexDirection:"column", flex:1, padding:"12px 48px 8px", gap:"7px" }}>
        {top.length === 0 ? (
          <div style={{ display:"flex", flex:1, alignItems:"center", justifyContent:"center", color:"#3f3f46", fontSize:"20px", letterSpacing:"2px" }}>NO PICKS TODAY</div>
        ) : (
          <div style={{ display:"flex", flexDirection:"column", gap:"7px" }}>
            {top.map((pick, i) => {
              const s = sys(pick.system);
              const notes = pick.notes ?? "";
              return (
                <div key={i} style={{ display:"flex", flexDirection:"column", background:"#0e0e11", borderLeft:`3px solid ${s.color}`, borderTop:"0.5px solid #1f1f24", borderRight:"0.5px solid #1f1f24", borderBottom:"0.5px solid #1f1f24", borderRadius:"6px", padding: notes ? "10px 20px 8px" : "10px 20px", gap:"5px" }}>
                  <div style={{ display:"flex", alignItems:"center", gap:"12px" }}>
                    <span style={{ fontSize:"11px", color:"#3f3f46", minWidth:"16px" }}>#{i+1}</span>
                    <div style={{ display:"flex", background:s.bg, border:`0.5px solid ${s.border}`, borderRadius:"4px", padding:"3px 10px" }}>
                      <span style={{ fontSize:"11px", fontWeight:700, color:s.color, letterSpacing:"1px" }}>{pick.system}</span>
                    </div>
                    <span style={{ fontSize:"14px", color:"#a1a1aa", flex:1 }}>{fmtGame(pick)}</span>
                    <span style={{ fontSize:"14px", color:"#f5f5f7", fontWeight:600, minWidth:"150px" }}>{pickLabel(pick.bet_type, pick.system)}</span>
                    <span style={{ fontSize:"13px", color:"#71717a", minWidth:"55px", textAlign:"right" }}>{fmtOdds(pick.odds)}</span>
                    <div style={{ display:"flex", background:"#041a0f", border:"0.5px solid #10b98130", borderRadius:"4px", padding:"3px 12px", minWidth:"76px", justifyContent:"center" }}>
                      <span style={{ fontSize:"15px", fontWeight:800, color:"#10b981" }}>{fmtEdge(pick.edge)}</span>
                    </div>
                    <span style={{ fontSize:"11px", color:"#3f3f46", minWidth:"40px", textAlign:"right" }}>{pick.stake ? `${parseFloat(pick.stake).toFixed(2)}u` : ""}</span>
                  </div>
                  {notes !== "" && (
                    <div style={{ display:"flex", alignItems:"center", gap:"8px", paddingLeft:"28px" }}>
                      <span style={{ fontSize:"11px", color:"#10b98160" }}>▸</span>
                      <span style={{ fontSize:"12px", color:"#52525b", fontStyle:"italic" }}>{notes}</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* FOOTER */}
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"12px 48px 18px", borderTop:"0.5px solid #1f1f24" }}>
        <div style={{ display:"flex", gap:"32px", alignItems:"center" }}>
          <div style={{ display:"flex", flexDirection:"column", gap:"2px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f46", letterSpacing:"1.5px" }}>SEASON ROI</span>
            <span style={{ fontSize:"18px", fontWeight:800, color: roi !== null && parseFloat(roi) >= 0 ? "#10b981" : "#ef4444" }}>
              {roi !== null ? `${parseFloat(roi)>0?"+":""}${parseFloat(roi).toFixed(1)}%` : "—"}
            </span>
          </div>
          <div style={{ display:"flex", flexDirection:"column", gap:"2px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f46", letterSpacing:"1.5px" }}>BETS TRACKED</span>
            <span style={{ fontSize:"18px", fontWeight:800, color:"#f5f5f7" }}>{totalBets}</span>
          </div>
          <div style={{ display:"flex", flexDirection:"column", gap:"2px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f46", letterSpacing:"1.5px" }}>WIN RATE</span>
            <span style={{ fontSize:"18px", fontWeight:800, color:"#f5f5f7" }}>{winRate !== "—" ? `${parseFloat(winRate).toFixed(1)}%` : "—"}</span>
          </div>
        </div>
        <div style={{ display:"flex", gap:"24px", alignItems:"center" }}>
          <div style={{ display:"flex", flexDirection:"column", alignItems:"flex-end", gap:"3px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f46", letterSpacing:"1.5px" }}>FULL PICKS + METHODOLOGY</span>
            <span style={{ fontSize:"16px", color:"#10b981", fontWeight:700 }}>beezy.vip</span>
          </div>
          <div style={{ width:"1px", height:"32px", background:"#1f1f24", display:"flex" }} />
          <div style={{ display:"flex", flexDirection:"column", alignItems:"flex-end", gap:"3px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f46", letterSpacing:"1.5px" }}>JOIN THE COMMUNITY</span>
            <span style={{ fontSize:"16px", color:"#a78bfa", fontWeight:700 }}>discord.gg/HfMYCmbmE</span>
          </div>
        </div>
      </div>

    </div>
  ), { width: 1200, height: 675 });
}
