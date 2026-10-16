"""Construit site/gazpetrole.js à partir de data/raw/gaz_petrole/ (voir fetch_gaz_petrole.py).

    python3 scripts/build_gaz_petrole.py [data/raw] [site/gazpetrole.js]
"""
import hashlib
import json
import os
import re
import statistics
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "raw"), "gaz_petrole")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "site", "gazpetrole.js")
STORENGY = ["Atlantique", "Saline", "Sediane", "Sediane B", "Serene"]


def load(name, default=None):
    path = os.path.join(RAW, name + ".json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else (default if default is not None else {})


def storage():
    s = load("storage")
    days = sorted(s)
    pits = sorted({p for v in s.values() for p in v})
    cap = {p: max(v[p] for v in s.values() if p in v) for p in pits}  # maximum observé ≈ capacité utile
    total, terega, last_full = [], [], None
    for d in days:
        v = s[d]
        full = all(p in v for p in pits)
        total.append(round(sum(v.values()) / 1000, 2) if full else None)  # TWh
        terega.append(round(v["Teréga"] / 1000, 2) if "Teréga" in v else None)
        if full:
            last_full = d
    per_pits_last = {p: [max(d for d in days if p in s[d]), s[max(d for d in days if p in s[d])][p]] for p in pits}
    return {"days": days, "total": total, "terega": terega, "cap": {p: round(c / 1000, 2) for p, c in cap.items()},
            "capTotal": round(sum(cap.values()) / 1000, 1), "lastFull": last_full, "pitsLast": per_pits_last}


def consumption():
    dp, ind, temp = load("conso_dp"), load("conso_ind"), load("temperature")
    days = sorted(set(dp) | set(ind))
    rows = []
    for d in days:
        a, b = dp.get(d), ind.get(d)
        rows.append([d, a[0] if a and a[1] >= 2 else None, b[0] if b and b[2] >= 2 else None,
                     temp.get(d), 1 if b and b[1] else 0])  # date, DP (GWh), industriels (GWh), T moy, provisoire
    # le dernier jour où les deux opérateurs sont présents, pour annoncer la couverture
    last_dp = max((r[0] for r in rows if r[1] is not None), default=None)
    last_ind = max((r[0] for r in rows if r[2] is not None), default=None)
    return {"rows": rows, "lastDp": last_dp, "lastInd": last_ind}


def ecogaz():
    e = load("ecogaz")
    days = sorted(e)
    counts = defaultdict(int)
    for v in e.values():
        counts[v[1]] += 1
    return {"recent": [[d, *e[d]] for d in days[-45:]], "counts": dict(counts), "first": days[0] if days else None, "n": len(days)}


def brent():
    b = load("brent")
    days = sorted(b)
    monthly = defaultdict(list)
    for d in days:
        monthly[d[:7]].append(b[d])
    recent = [[d, b[d]] for d in days if d >= days[-1][:4] + "-00" or d >= str(int(days[-1][:4]) - 2) + days[-1][4:]]
    return {"monthly": [[m, round(statistics.mean(v), 2)] for m, v in sorted(monthly.items())], "daily": recent,
            "last": [days[-1], b[days[-1]]] if days else None}


def oil_bulletin():
    o = load("oil_bulletin")
    days = sorted(o)
    countries = ["FR", "DE", "BE", "LU", "ES", "IT", "NL", "EU"]
    return {"days": days, "series": {c: {f: [o[d].get(c, {}).get(f) for d in days] for f in ("euro95", "diesel")} for c in countries}}


def main():
    data = {"storage": storage(), "conso": consumption(), "ecogaz": ecogaz(), "fuel": load("fuel_snapshot"),
            "fuelDaily": load("fuel_daily"), "wob": oil_bulletin(), "brent": brent()}
    data["updated"] = max(filter(None, [data["storage"]["days"][-1] if data["storage"]["days"] else None,
                                        data["conso"]["lastDp"], data["fuel"].get("maj", "")[:10]]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.GP=")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    digest = hashlib.sha1(open(OUT, "rb").read()).hexdigest()[:10]
    page = os.path.join(os.path.dirname(OUT), "gaz-petrole.html")
    if os.path.exists(page):  # empreinte anti-cache
        src = open(page, encoding="utf-8").read()
        new = re.sub(r'src="gazpetrole\.js(\?v=[\w-]+)?"', f'src="gazpetrole.js?v={digest}"', src)
        if new != src:
            open(page, "w", encoding="utf-8").write(new)
    print(f"{OUT} : {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()
