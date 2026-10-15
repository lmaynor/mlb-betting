"""Backtest the Beezy Score against realised flat-stake ROI on settled +EV alerts (public API, read-only).

Usage:  python3 scripts/beezy_score_backtest.py [--since 2026-07-01] [--until YYYY-MM-DD]

Pulls system=EV settled rows from https://beezy.fyi/api/picks (the public proxy), ports beezyscore() from
beezy-vip/lib/beezy-score.ts, and prints ROI by score decile and by tier. Keep the formula here in sync with the TS.
Caveat: tier cut points were chosen on this same sample (in-sample).
"""
import argparse, datetime, json, math, re, statistics as st, urllib.request

BOOKS = ['draftkings', 'fanduel', 'betmgm', 'caesars', 'bet365', 'hardrock', 'fanatics', 'partycasino', 'novig', 'parx',
         'underdog', 'prophetx', 'thescore', 'betrivers', 'sugarhouse', 'fliff', 'sleeper', 'kalshi', 'pointsbet']
STRONG, LEAN = 70, 45


def get(q):
    req = urllib.request.Request('https://beezy.fyi/api/picks?' + q, headers={'User-Agent': 'Mozilla/5.0'})
    return json.load(urllib.request.urlopen(req, timeout=60))['picks']


def pull(since, until):
    rows, d = {}, since
    while d <= until:
        day = get(f'system=EV&status=settled&date={d}&limit=200')
        if len(day) >= 200:  # the API caps a page at 200: split heavy days by book
            day = [r for b in BOOKS for r in get(f'system=EV&status=settled&date={d}&book={b}&limit=200')]
        for r in day:
            rows[r['id']] = r
        d += datetime.timedelta(days=1)
    return list(rows.values())


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def score(r):
    e = r['edge'] * 100 if abs(r['edge']) < 2 else r['edge']
    e = max(e, 0)
    m = re.search(r'\((\d+) books\)', r.get('notes') or '')
    cons = 20 * clamp((int(m.group(1)) - 3) / 9, 0, 1) if m else 10
    p = r['model_prob'] if r['model_prob'] <= 1.5 else r['model_prob'] / 100
    hit = 20 * clamp((p - 0.15) / 0.35, 0, 1)
    return round(min(e * 4, 60) + (cons + hit) * min(e / 2, 1))


def agg(rs):
    if not rs:
        return 'n=0'
    rets = [r['profit'] / r['stake'] for r in rs]
    se = st.pstdev(rets) / math.sqrt(len(rs)) * 100
    hit = sum(r['result'] == 'win' for r in rs) / len(rs) * 100
    return f'n={len(rs):5d} roi={st.mean(rets) * 100:6.1f}% (+-{se:4.1f}) hit={hit:4.1f}%'


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-07-01')
    ap.add_argument('--until', default=datetime.date.today().isoformat())
    a = ap.parse_args()
    rows = [r for r in pull(datetime.date.fromisoformat(a.since), datetime.date.fromisoformat(a.until))
            if r['result'] in ('win', 'loss') and r.get('stake')]
    print(f'{len(rows)} graded +EV rows')
    for lo in range(0, 100, 10):
        print(f'  {lo:3d}-{lo + 9:3d}', agg([r for r in rows if lo <= score(r) < lo + 10]))
    print('STRONG', agg([r for r in rows if score(r) >= STRONG]))
    print('LEAN  ', agg([r for r in rows if LEAN <= score(r) < STRONG]))
    print('WATCH ', agg([r for r in rows if score(r) < LEAN]))
