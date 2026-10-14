import requests, time
B = "https://api.elections.kalshi.com/trade-api/v2"
for i in range(3):
    r = requests.get(B + "/markets", params={"series_ticker": "KXNFLREC", "status": "open", "limit": 2},
                     headers={"User-Agent": "beezy-kalshi/1.0"}, timeout=30)
    print(f"try {i}: {r.status_code} {len(r.content)}B x-cache={r.headers.get('x-cache')} via={r.headers.get('via')}", flush=True)
    time.sleep(20)
try:
    print("egress ip:", requests.get("https://api.ipify.org", timeout=10).text)
except Exception as e:
    print("ip lookup failed", e)
