#!/bin/bash
# Run on Cloud Shell to download missing headshots
cd ~/mlb-betting
python3 - << 'PYEOF'
import urllib.request, json, os, sys
from pathlib import Path

OUT_DIR = Path("beezy-vip/public/headshots")
URL = "https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/{id}/headshot/67/current"

try:
    from rembg import remove as rembg_remove
    HAS_REMBG = True
except ImportError:
    HAS_REMBG = False
    print("WARNING: rembg not installed - installing...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "rembg[cpu]", "-q"], check=True)
    from rembg import remove as rembg_remove
    HAS_REMBG = True

missing = [
    ("aj_minter", 621345),
    ("aj_puk", 640462),
    ("bobby_witt_jr", 677951),
    ("fernando_tatis_jr", 665487),
    ("gabriel_rincones_jr", 687282),
    ("ha_seong_kim", 673490),
    ("isiah_kiner_falefa", 643396),
    ("jazz_chisholm_jr", 665862),
    ("jc_escarra", 641555),
    ("josé_a_ferrer", 678606),
    ("jp_crawford", 641487),
    ("jt_ginn", 669372),
    ("jt_realmuto", 592663),
    ("junior_caminero", 691406),
    ("kai_wei_teng", 678906),
    ("kebryan_hayes", 663647),
    ("lance_mccullers_jr", 621121),
    ("landen_roupp", 694738),
    ("logan_ohoppe", 681351),
    ("lourdes_gurriel_jr", 666971),
    ("luis_garcía_jr", 671277),
    ("luis_robert_jr", 673357),
    ("mark_leiter_jr", 643410),
    ("pj_higgins", 664731),
    ("riley_obrien", 676617),
    ("ronald_acuña_jr", 660670),
    ("ryan_ohearn", 656811),
    ("sawyer_gipson_long", 687830),
    ("tatsuya_imai", 837227),
    ("travis_darnaud", 518595),
    ("trey_yesavage", 702056),
    ("tyler_oneill", 641933),
    ("victor_mesa_jr", 683748),
    ("vladimir_guerrero_jr", 665489),
]

ok = 0
for slug, mlb_id in missing:
    out_path = OUT_DIR / f"{slug}.png"
    if out_path.exists():
        print(f"  SKIP {slug} (already exists)")
        ok += 1
        continue
    url = URL.format(id=mlb_id)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            img_bytes = r.read()
        print(f"  DL {slug} ({len(img_bytes):,}B)", end="", flush=True)
        result = rembg_remove(img_bytes)
        out_path.write_bytes(result)
        print(f" -> saved ({len(result):,}B)")
        ok += 1
    except Exception as e:
        print(f"  FAIL {slug}: {e}")

print(f"Done: {ok}/{len(missing)}")
PYEOF
