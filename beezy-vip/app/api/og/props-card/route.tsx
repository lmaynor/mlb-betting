import { ImageResponse } from "next/og";
export const runtime = "edge";

const API = process.env.BETTING_API_URL!;
const KEY = process.env.BETTING_API_KEY!;
const BASE = process.env.NEXT_PUBLIC_BASE_URL ?? "https://mlb-betting-rose.vercel.app";

const TEAMS: Record<string, { p: string; s: string; slug: string }> = {
  ARI: { p: "167,25,48",   s: "0,0,0",       slug: "ari" },
  ATL: { p: "206,17,65",   s: "19,39,79",     slug: "atl" },
  BAL: { p: "223,70,1",    s: "39,37,31",     slug: "bal" },
  BOS: { p: "189,48,57",   s: "12,35,64",     slug: "bos" },
  CHC: { p: "14,51,134",   s: "204,52,51",    slug: "chc" },
  CWS: { p: "39,37,31",    s: "196,206,212",  slug: "cws" },
  CIN: { p: "198,1,31",    s: "0,0,0",        slug: "cin" },
  CLE: { p: "0,56,93",     s: "229,0,34",     slug: "cle" },
  COL: { p: "51,51,102",   s: "35,31,32",     slug: "col" },
  DET: { p: "12,35,64",    s: "250,70,22",    slug: "det" },
  HOU: { p: "0,45,98",     s: "235,110,31",   slug: "hou" },
  KC:  { p: "0,70,135",    s: "189,155,96",   slug: "kc"  },
  LAA: { p: "0,50,99",     s: "186,0,33",     slug: "laa" },
  LAD: { p: "0,90,156",    s: "239,62,66",    slug: "lad" },
  MIA: { p: "0,163,224",   s: "0,0,0",        slug: "mia" },
  MIL: { p: "18,40,75",    s: "255,197,47",   slug: "mil" },
  MIN: { p: "0,43,92",     s: "211,17,69",    slug: "min" },
  NYM: { p: "0,45,114",    s: "252,89,16",    slug: "nym" },
  NYY: { p: "0,48,135",    s: "12,35,64",     slug: "nyy" },
  OAK: { p: "0,56,49",     s: "239,178,30",   slug: "oak" },
  PHI: { p: "232,24,40",   s: "0,45,114",     slug: "phi" },
  PIT: { p: "39,37,31",    s: "253,184,39",   slug: "pit" },
  SD:  { p: "47,36,29",    s: "255,196,37",   slug: "sd"  },
  SF:  { p: "253,90,30",   s: "39,37,31",     slug: "sf"  },
  SEA: { p: "12,44,86",    s: "0,92,92",      slug: "sea" },
  STL: { p: "196,30,58",   s: "12,35,64",     slug: "stl" },
  TB:  { p: "9,44,92",     s: "143,188,230",  slug: "tb"  },
  TEX: { p: "0,50,120",    s: "192,17,31",    slug: "tex" },
  TOR: { p: "19,74,142",   s: "232,41,28",    slug: "tor" },
  WSH: { p: "171,0,3",     s: "20,34,90",     slug: "wsh" },
};

const SYS: Record<string, { accent: string; bg: string; label: string }> = {
  HR:   { accent: "#f59e0b", bg: "#1a1200", label: "HR YES" },
  K:    { accent: "#a78bfa", bg: "#100a1e", label: "STRIKEOUTS" },
  OUTS: { accent: "#fb923c", bg: "#1a0d00", label: "PITCHER OUTS" },
};

function playerHeadshot(name: string, base: string): string | null {
  if (!name) return null;
  const key = name.toLowerCase().replace(/ /g, "_");
  return `${base}/headshots/${key}.jpg`;
}

function fmtOdds(o: number) { return o > 0 ? `+${o}` : `${o}`; }
function fmtEdge(e: any) { return `+${(parseFloat(e) * 100).toFixed(1)}%`; }
function fmtPick(bt: string) {
  if (bt === "HR") return "HR Yes";
  if (bt.startsWith("K_OVER"))     return `Over ${bt.split("_")[2]} Ks`;
  if (bt.startsWith("K_UNDER"))    return `Under ${bt.split("_")[2]} Ks`;
  if (bt.startsWith("OUTS_OVER"))  return `Over ${bt.split("_")[2]} Outs`;
  if (bt.startsWith("OUTS_UNDER")) return `Under ${bt.split("_")[2]} Outs`;
  return bt;
}
function teamInfo(abbrev: string) {
  return TEAMS[abbrev?.toUpperCase()] ?? { p: "30,30,40", s: "10,10,15", slug: "nyy" };
}

export async function GET() {
  // Load player name -> MLBAM id map for headshots
  let playerMap: Record<string, number> = {};
  try {
    const mr = await fetch(`${BASE}/headshots/player_map.json`, { cache: "no-store" });
    playerMap = await mr.json();
  } catch {}

  const [pr, sr] = await Promise.all([
    fetch(`${API}/api/public/picks/today`, { headers: { "X-API-Key": KEY }, cache: "no-store" }),
    fetch(`${API}/api/public/stats/summary`, { headers: { "X-API-Key": KEY }, cache: "no-store" }),
  ]);
  const pd = await pr.json();
  const sd = await sr.json();
  const all: any[] = Array.isArray(pd) ? pd : pd.picks ?? [];

  // Player props: HR, K, OUTS only
  const picks = all
    .filter(p => p.kelly_triggered && ["HR","K","OUTS"].includes(p.system))
    .sort((a,b) => parseFloat(b.edge) - parseFloat(a.edge))
    .slice(0, 4);

  const ov = sd?.overall ?? {};
  const roi = ov.roi ?? null;
  const roiNum = roi !== null ? parseFloat(roi) : null;
  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric", timeZone: "America/Chicago"
  }).toUpperCase();

  const W = 900;
  const HEADER_H = 150;
  const ROW_H    = 190;
  const FOOTER_H = 80;
  const GAP  = 10;
  const PAD  = 20;
  const H = HEADER_H + PAD + picks.length * ROW_H + (picks.length - 1) * GAP + PAD + FOOTER_H;

  return new ImageResponse((
    <div style={{ width:`${W}px`, height:`${H}px`, background:"#06060a", display:"flex", flexDirection:"column", fontFamily:"monospace", position:"relative" }}>

      {/* top gradient bar */}
      <div style={{ display:"flex", position:"absolute", top:0, left:0, right:0, height:"5px", background:"linear-gradient(90deg,#f59e0b 0%,#ec4899 40%,#a78bfa 100%)" }} />

      {/* HEADER */}
      <div style={{ display:"flex", flexDirection:"column", justifyContent:"center", alignItems:"center", height:`${HEADER_H}px`, borderBottom:"1px solid #1a1a22", gap:"10px" }}>
        <div style={{ display:"flex", alignItems:"baseline", gap:"0px" }}>
          <span style={{ fontSize:"52px", fontWeight:900, color:"#ffffff", letterSpacing:"-3px" }}>BEEZY</span>
          <span style={{ fontSize:"52px", fontWeight:900, color:"#10b981", letterSpacing:"-3px" }}>.VIP</span>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:"14px" }}>
          <div style={{ display:"flex", background:"rgba(245,158,11,0.12)", border:"1px solid rgba(245,158,11,0.35)", borderRadius:"4px", padding:"4px 14px" }}>
            <span style={{ fontSize:"12px", color:"#f59e0b", letterSpacing:"3px", fontWeight:800 }}>PLAYER PROPS</span>
          </div>
          <span style={{ fontSize:"12px", color:"#3f3f50", letterSpacing:"1px" }}>{today}</span>
        </div>
      </div>

      {/* PICKS */}
      <div style={{ display:"flex", flexDirection:"column", padding:`${PAD}px 20px`, gap:`${GAP}px` }}>
        {picks.length === 0 ? (
          <div style={{ display:"flex", height:`${ROW_H}px`, alignItems:"center", justifyContent:"center", color:"#3f3f46", fontSize:"18px", letterSpacing:"3px" }}>
            NO PLAYER PROPS TODAY
          </div>
        ) : picks.map((pick, i) => {
          const teamAbbrev = pick.home_team ?? pick.away_team ?? "NYY";
          const team = teamInfo(teamAbbrev);
          const sys  = SYS[pick.system] ?? { accent:"#71717a", bg:"#111114", label:pick.system };
          const bullets = (pick.notes ?? "").trim().split(" · ").filter(Boolean).slice(0, 3);
          const playerName = (pick.player ?? "").toUpperCase();
          const mlbamId = pick.player
            ? playerMap[pick.player.toLowerCase().replace(/ /g,"_")]
            : null;

          return (
            <div key={i} style={{ display:"flex", height:`${ROW_H}px`, borderRadius:"14px", overflow:"hidden", position:"relative", border:`1px solid ${sys.accent}28` }}>

              {/* team color gradient — stronger left bleed */}
              <div style={{ display:"flex", position:"absolute", inset:0, background:`linear-gradient(120deg, rgba(${team.p},0.95) 0%, rgba(${team.p},0.5) 30%, rgba(${team.s},0.25) 55%, #09090d 100%)` }} />
              {/* subtle glow on left edge */}
              <div style={{ display:"flex", position:"absolute", top:0, left:0, bottom:0, width:"4px", background:`linear-gradient(180deg, ${sys.accent}, transparent)` }} />

              {/* content row */}
              <div style={{ display:"flex", alignItems:"center", width:"100%", padding:"0 24px 0 28px", gap:"18px", position:"relative", zIndex:1 }}>

                {/* rank */}
                <span style={{ fontSize:"11px", color:"rgba(255,255,255,0.25)", fontWeight:700, minWidth:"18px" }}>#{i+1}</span>

                {/* headshot — large, prominent */}
                {mlbamId ? (
                  <img
                    src={`${BASE}/headshots/${mlbamId}.jpg`}
                    width={130} height={130}
                    style={{ objectFit:"cover", borderRadius:"50%", border:`3px solid ${sys.accent}60`, flexShrink:0 }}
                  />
                ) : (
                  <img
                    src={`${BASE}/logos/${team.slug}.png`}
                    width={100} height={100}
                    style={{ objectFit:"contain", flexShrink:0, opacity:0.85 }}
                  />
                )}

                {/* player info + bullets */}
                <div style={{ display:"flex", flexDirection:"column", flex:1, gap:"7px" }}>
                  {/* system pill + matchup */}
                  <div style={{ display:"flex", alignItems:"center", gap:"10px" }}>
                    <div style={{ display:"flex", background:`${sys.bg}dd`, border:`1px solid ${sys.accent}55`, borderRadius:"4px", padding:"3px 10px" }}>
                      <span style={{ fontSize:"10px", fontWeight:800, color:sys.accent, letterSpacing:"1.5px" }}>{sys.label}</span>
                    </div>
                    {pick.away_team && pick.home_team && (
                      <span style={{ fontSize:"12px", color:"rgba(255,255,255,0.4)", letterSpacing:"0.5px" }}>
                        {pick.away_team} @ {pick.home_team}
                      </span>
                    )}
                  </div>

                  {/* player name — big */}
                  <span style={{ fontSize:"32px", fontWeight:900, color:"#ffffff", letterSpacing:"-1px", lineHeight:"1" }}>
                    {playerName || fmtPick(pick.bet_type)}
                  </span>

                  {/* pick line + odds */}
                  <span style={{ fontSize:"14px", color:"rgba(255,255,255,0.55)", fontWeight:600, letterSpacing:"0.3px" }}>
                    {fmtPick(pick.bet_type)} · {fmtOdds(pick.odds)}
                  </span>

                  {/* stat bullets */}
                  {bullets.length > 0 && (
                    <div style={{ display:"flex", flexDirection:"column", gap:"3px" }}>
                      {bullets.map((b, bi) => (
                        <div key={bi} style={{ display:"flex", alignItems:"center", gap:"6px" }}>
                          <span style={{ fontSize:"10px", color:sys.accent, fontWeight:800 }}>▸</span>
                          <span style={{ fontSize:"11px", color:"rgba(255,255,255,0.45)", letterSpacing:"0.2px" }}>{b}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* edge hero box */}
                <div style={{ display:"flex", flexDirection:"column", alignItems:"center", background:"rgba(0,0,0,0.6)", border:`1px solid ${sys.accent}50`, borderRadius:"12px", padding:"14px 20px", gap:"5px", flexShrink:0, boxShadow:`0 0 20px ${sys.accent}18` }}>
                  <span style={{ fontSize:"9px", color:"rgba(255,255,255,0.3)", letterSpacing:"2px" }}>EDGE</span>
                  <span style={{ fontSize:"30px", fontWeight:900, color:sys.accent, letterSpacing:"-1px" }}>{fmtEdge(pick.edge)}</span>
                  <span style={{ fontSize:"10px", color:"rgba(255,255,255,0.22)" }}>{pick.stake ? `${parseFloat(pick.stake).toFixed(2)}u` : ""}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* FOOTER */}
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"0 28px", height:`${FOOTER_H}px`, borderTop:"1px solid #1a1a22" }}>
        <div style={{ display:"flex", gap:"28px" }}>
          <div style={{ display:"flex", flexDirection:"column", gap:"3px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f50", letterSpacing:"2px" }}>SEASON ROI</span>
            <span style={{ fontSize:"20px", fontWeight:800, color: roiNum !== null ? (roiNum >= 0 ? "#10b981" : "#ef4444") : "#f5f5f7" }}>
              {roiNum !== null ? `${roiNum > 0 ? "+" : ""}${roiNum.toFixed(1)}%` : "—"}
            </span>
          </div>
          <div style={{ display:"flex", flexDirection:"column", gap:"3px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f50", letterSpacing:"2px" }}>BETS TRACKED</span>
            <span style={{ fontSize:"20px", fontWeight:800, color:"#f5f5f7" }}>{ov.total_bets ?? "—"}</span>
          </div>
        </div>
        <div style={{ display:"flex", gap:"24px", alignItems:"center" }}>
          <div style={{ display:"flex", flexDirection:"column", alignItems:"flex-end", gap:"3px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f50", letterSpacing:"2px" }}>FULL PICKS</span>
            <span style={{ fontSize:"18px", color:"#10b981", fontWeight:800 }}>beezy.vip</span>
          </div>
          <div style={{ display:"flex", width:"1px", height:"32px", background:"#1a1a22" }} />
          <div style={{ display:"flex", flexDirection:"column", alignItems:"flex-end", gap:"3px" }}>
            <span style={{ fontSize:"9px", color:"#3f3f50", letterSpacing:"2px" }}>COMMUNITY</span>
            <span style={{ fontSize:"18px", color:"#a78bfa", fontWeight:800 }}>discord.gg/HfMYCmbmE</span>
          </div>
        </div>
      </div>

    </div>
  ), { width: W, height: H });
}
