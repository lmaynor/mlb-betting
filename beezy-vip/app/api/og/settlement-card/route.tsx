import { ImageResponse } from "next/og";
export const runtime = "edge";

// Nightly settlement receipt: every graded bet for one slate, wins AND losses,
// plus the slate record and the season +EV ledger. ASCII only (Satori + the
// ASCII test in tests/settlement-card.test.ts); literal hex only (no CSS vars).
// GET /api/og/settlement-card?date=YYYY-MM-DD&sport=all   (defaults: latest settled date, all)

const API = process.env.BETTING_API_URL!;
const KEY = process.env.BETTING_API_KEY!;

const WIN = "#57f287";
const LOSS = "#ef4444";
const FLAT = "#9ca3af";
const MAX_ROWS = 7;
const SPORTS = ["mlb", "nhl", "nba"];

type Row = {
  sport: string; system: string; game_date: string; bet_type: string; player: string | null
  away_team: string | null; home_team: string | null; odds: number; stake: number
  edge: number | string | null; result: string | null; profit: number | string | null; book: string | null
}

const num = (x: unknown) => { const n = typeof x === "number" ? x : parseFloat(String(x)); return isFinite(n) ? n : 0 };
const fmtOdds = (o: number) => (o > 0 ? `+${o}` : `${o}`);
const fmtU = (u: number) => `${u >= 0 ? "+" : "-"}${Math.abs(u).toFixed(2)}u`;
const BOOK: Record<string, string> = { draftkings: "DK", fanduel: "FD", betmgm: "MGM", caesars: "CZR", bet365: "365", hardrock: "HR", novig: "NOVIG", kalshi: "KALSHI" };
const book = (b: string | null) => (b ? BOOK[b.toLowerCase()] ?? b.slice(0, 6).toUpperCase() : "--");

const NOUN: Record<string, string> = { K: "Ks", OUTS: "Outs", BATTER_TB: "TB", BATTER_HITS: "Hits", PITCHER_ER: "ER", BATTER_K: "Batter Ks", SB: "SB" };
function prop(r: Row): string {
  const bt = r.bet_type ?? "";
  const who = r.player ? `${r.player} ` : "";
  if (bt === "HR") return `${who}HR Yes`;
  if (bt === "NRFI") return "No Run 1st";
  if (bt === "YRFI") return "Run 1st";
  if (bt === "HOME" || bt === "GAME_HOME") return "Home ML";
  if (bt === "AWAY" || bt === "GAME_AWAY") return "Away ML";
  const m = bt.match(/^(K|OUTS|BATTER_TB|BATTER_HITS|PITCHER_ER|BATTER_K|SB)_(OVER|UNDER|\d+PLUS)_([\d.]+)$/);
  if (m) {
    const plus = m[2].match(/^(\d+)PLUS$/);
    const noun = NOUN[m[1]] ?? m[1];
    return plus ? `${who}${plus[1]}+ ${noun}` : `${who}${m[2] === "OVER" ? "Over" : "Under"} ${m[3]} ${noun}`;
  }
  return `${who}${bt.replace(/_/g, " ")}`.trim();
}

async function get(path: string): Promise<any> {
  const r = await fetch(`${API}${path}`, { headers: { "X-API-Key": KEY }, cache: "no-store" });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

export async function GET(req: Request) {
  const sp = new URL(req.url).searchParams;
  const wantDate = /^\d{4}-\d{2}-\d{2}$/.test(sp.get("date") ?? "") ? sp.get("date")! : null;
  const sportParam = (sp.get("sport") ?? "all").toLowerCase();
  const sports = sportParam === "all" ? SPORTS : SPORTS.includes(sportParam) ? [sportParam] : SPORTS;

  const [perSport, deep] = await Promise.all([
    Promise.all(sports.map(async (s) => {
      try {
        const d = await get(`/api/public/picks/recent?limit=200&sport=${s}`);
        return ((Array.isArray(d) ? d : d.picks ?? []) as Row[]).map(r => ({ ...r, sport: s }));
      } catch { return [] as Row[]; }
    })),
    get(`/api/public/analytics/deep-dive?system=EV`).catch(() => null),
  ]);
  const settled = perSport.flat().filter(r => r.result === "win" || r.result === "loss" || r.result === "push");
  const date = wantDate ?? settled.map(r => r.game_date).sort().pop() ?? "";
  const slate = settled.filter(r => r.game_date === date).sort((a, b) => num(b.profit) - num(a.profit));
  const shown = slate.length > MAX_ROWS
    ? [...slate.slice(0, Math.ceil(MAX_ROWS / 2)), ...slate.slice(-Math.floor(MAX_ROWS / 2))]
    : slate;

  const w = slate.filter(r => r.result === "win").length;
  const l = slate.filter(r => r.result === "loss").length;
  const p = slate.filter(r => r.result === "push").length;
  const net = slate.reduce((a, r) => a + num(r.profit), 0);
  const hero = slate.find(r => r.result === "win");
  const hl = deep?.ev_headline ?? null;
  const pct = (x: number | null | undefined) => (x == null ? "--" : `${x >= 0 ? "+" : ""}${x.toFixed(1)}%`);

  const W = 1200, H = 675;
  return new ImageResponse((
    <div style={{ width: `${W}px`, height: `${H}px`, background: "#090a0f", display: "flex", flexDirection: "column", fontFamily: "monospace" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 44px", height: "76px", borderBottom: "1px solid #1e2230" }}>
        <div style={{ display: "flex", alignItems: "baseline" }}>
          <span style={{ fontSize: "30px", fontWeight: 800, color: "#ffffff" }}>BEEZY</span>
          <span style={{ fontSize: "30px", fontWeight: 800, color: WIN }}>.FYI</span>
        </div>
        <span style={{ fontSize: "15px", color: "#9ca3af", letterSpacing: "2px" }}>{`DAILY SETTLEMENT RECEIPT \u00b7 ${date}`}</span>
        <span style={{ fontSize: "12px", color: "#7d8596", letterSpacing: "2px" }}>PUBLIC LEDGER</span>
      </div>

      <div style={{ display: "flex", flex: 1, padding: "20px 44px 24px", gap: "20px" }}>
        <div style={{ display: "flex", flexDirection: "column", width: "68%", background: "#11131a", border: "1px solid #1e2230", borderRadius: "8px" }}>
          <div style={{ display: "flex", padding: "10px 16px", borderBottom: "1px solid #1e2230", fontSize: "11px", color: "#7d8596", letterSpacing: "1.5px" }}>
            <span style={{ display: "flex", width: "64px" }}>RESULT</span>
            <span style={{ display: "flex", width: "56px" }}>SPORT</span>
            <span style={{ display: "flex", flex: 1 }}>PROPOSITION</span>
            <span style={{ display: "flex", width: "110px", justifyContent: "flex-end" }}>BOOK / ODDS</span>
            <span style={{ display: "flex", width: "70px", justifyContent: "flex-end" }}>EDGE</span>
            <span style={{ display: "flex", width: "86px", justifyContent: "flex-end" }}>NET</span>
          </div>
          {shown.length === 0 ? (
            <div style={{ display: "flex", flex: 1, alignItems: "center", justifyContent: "center", color: "#7d8596", fontSize: "16px", letterSpacing: "3px" }}>NO SETTLED BETS</div>
          ) : shown.map((r, i) => {
            const c = r.result === "win" ? WIN : r.result === "loss" ? LOSS : FLAT;
            const label = r.result === "win" ? "WIN" : r.result === "loss" ? "LOSS" : "PUSH";
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", padding: "0 16px", height: "62px", borderBottom: i < shown.length - 1 ? "1px solid #151822" : "none" }}>
                <div style={{ display: "flex", width: "64px" }}>
                  <span style={{ display: "flex", fontSize: "12px", fontWeight: 800, color: c, border: `1px solid ${c}`, borderRadius: "6px", padding: "2px 7px" }}>{label}</span>
                </div>
                <span style={{ display: "flex", width: "56px", fontSize: "12px", color: "#9ca3af" }}>{r.sport.toUpperCase()}</span>
                <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
                  <span style={{ display: "flex", fontSize: "16px", color: "#f3f4f6", fontWeight: 600 }}>{prop(r)}</span>
                  <span style={{ display: "flex", fontSize: "11px", color: "#7d8596" }}>{`${r.system} \u00b7 ${r.away_team ?? ""} @ ${r.home_team ?? ""}`}</span>
                </div>
                <span style={{ display: "flex", width: "110px", justifyContent: "flex-end", fontSize: "14px", color: "#f3f4f6" }}>{`${book(r.book)} ${fmtOdds(num(r.odds))}`}</span>
                <span style={{ display: "flex", width: "70px", justifyContent: "flex-end", fontSize: "13px", color: "#9ca3af" }}>{r.edge == null ? "--" : `+${num(r.edge) < 2 ? (num(r.edge) * 100).toFixed(1) : num(r.edge).toFixed(1)}%`}</span>
                <span style={{ display: "flex", width: "86px", justifyContent: "flex-end", fontSize: "15px", fontWeight: 800, color: c }}>{fmtU(num(r.profit))}</span>
              </div>
            );
          })}
          {slate.length > shown.length && (
            <div style={{ display: "flex", padding: "8px 16px", fontSize: "11px", color: "#7d8596" }}>{`${slate.length - shown.length} more graded bets on this slate: beezy.fyi/results`}</div>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", width: "32%", background: "#11131a", border: "1px solid #1e2230", borderRadius: "8px", padding: "18px", gap: "18px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span style={{ display: "flex", fontSize: "11px", color: "#7d8596", letterSpacing: "1.5px" }}>SLATE RECORD</span>
            <span style={{ display: "flex", fontSize: "30px", fontWeight: 800, color: "#f3f4f6" }}>{`${w}-${l}-${p}`}</span>
            <span style={{ display: "flex", fontSize: "18px", fontWeight: 800, color: net >= 0 ? WIN : LOSS }}>{fmtU(net)}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span style={{ display: "flex", fontSize: "11px", color: "#7d8596", letterSpacing: "1.5px" }}>BIGGEST HIT</span>
            <span style={{ display: "flex", fontSize: "14px", color: "#f3f4f6" }}>
              {hero ? `${hero.player ?? prop(hero)} (${fmtOdds(num(hero.odds))}, ${fmtU(num(hero.profit))})` : "--"}
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span style={{ display: "flex", fontSize: "11px", color: "#7d8596", letterSpacing: "1.5px" }}>SEASON +EV ROI</span>
            <span style={{ display: "flex", fontSize: "14px", color: "#f3f4f6" }}>{hl ? `${pct(hl.flat_roi_pct)} Flat / ${pct(hl.kelly_roi_pct)} Kelly` : "--"}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span style={{ display: "flex", fontSize: "11px", color: "#7d8596", letterSpacing: "1.5px" }}>GRADED SAMPLE</span>
            <span style={{ display: "flex", fontSize: "14px", color: "#f3f4f6" }}>{hl ? `${hl.graded.toLocaleString("en-US")} public bets` : "--"}</span>
          </div>
          <span style={{ display: "flex", marginTop: "auto", fontSize: "11px", color: "#7d8596" }}>Paper results at flagged price. Wins and losses both shown.</span>
        </div>
      </div>
    </div>
  ), { width: W, height: H });
}
