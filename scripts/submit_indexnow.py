"""Submit beezy.fyi URLs to IndexNow (Bing and partners). Run AFTER deploy: the key file must already be live.

    python3 scripts/submit_indexnow.py            # submit every URL in the live sitemap
    python3 scripts/submit_indexnow.py --dry-run  # print what would be sent

The key is public by design (it is served at https://beezy.fyi/<key>.txt, from beezy-vip/public/).
"""
import json
import re
import sys
import urllib.request

HOST = "beezy.fyi"
KEY = "a7f6f4624a6f7373c776fcc2275fd71c"
ENDPOINT = "https://api.indexnow.org/indexnow"


def sitemap_urls() -> list[str]:
    with urllib.request.urlopen(f"https://{HOST}/sitemap.xml", timeout=30) as r:
        return re.findall(r"<loc>([^<]+)</loc>", r.read().decode())


def main() -> int:
    unknown = [a for a in sys.argv[1:] if a != "--dry-run"]
    if unknown:
        sys.exit(f"unknown argument {unknown}; only --dry-run is accepted")
    urls = sitemap_urls()
    payload = {"host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt", "urlList": urls}
    if "--dry-run" in sys.argv:
        print(f"would submit {len(urls)} URLs")
        return 0
    req = urllib.request.Request(ENDPOINT, json.dumps(payload).encode(), {"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"IndexNow HTTP {r.status}: submitted {len(urls)} URLs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
