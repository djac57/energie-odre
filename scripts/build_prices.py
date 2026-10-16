"""Construit site/prices.js à partir de data/raw/prices/ (voir fetch_prices.py).

Prix spot day-ahead France : Bundesnetzagentur | SMARD.de (CC BY 4.0), via Energy-Charts (Fraunhofer ISE).
Sorties : prix journaliers (moyenne, min, max, heure de Paris), heures à prix négatif par an, profils horaires
moyens par année et saison, et les trois derniers jours publiés au pas d'origine (15 min ou 1 h).

    python3 scripts/build_prices.py data/raw site/prices.js
"""
import datetime as dt
import glob
import json
import os
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "site", "prices.js")
PARIS = ZoneInfo("Europe/Paris")
SEASON = {12: "hiver", 1: "hiver", 2: "hiver", 3: "printemps", 4: "printemps", 5: "printemps", 6: "été", 7: "été", 8: "été", 9: "automne", 10: "automne", 11: "automne"}


def main():
    pts = {}
    for f in sorted(glob.glob(os.path.join(RAW, "prices", "[0-9]*.json"))):
        with open(f, encoding="utf-8") as fh:
            pts.update({int(k): v for k, v in json.load(fh).items()})
    if not pts:
        print("Pas de prix : site/prices.js non généré"); return
    meta = {}
    try:
        meta = json.load(open(os.path.join(RAW, "prices", "meta.json"), encoding="utf-8"))
    except FileNotFoundError:
        pass
    ts = sorted(pts)
    # Durée de chaque pas : écart au pas suivant (1 h ou 15 min), borné
    dur = {t: min(3600, max(900, (ts[i + 1] - t) if i + 1 < len(ts) else ts[i] - ts[i - 1])) for i, t in enumerate(ts)}
    day = defaultdict(list)
    neg = defaultdict(float)
    prof = defaultdict(lambda: defaultdict(list))
    for t in ts:
        p, d = pts[t], dt.datetime.fromtimestamp(t, PARIS)
        day[d.date().isoformat()].append((p, dur[t]))
        if p < 0:
            neg[d.year] += dur[t] / 3600
        prof[(d.year, SEASON[d.month])][d.hour].append(p)
    daily = []
    for k in sorted(day):
        vals = day[k]
        w = sum(x[1] for x in vals)
        daily.append([k, round(sum(p * s for p, s in vals) / w, 2), round(min(p for p, _ in vals), 2), round(max(p for p, _ in vals), 2)])
    profiles = {f"{y}|{s}": [round(sum(h[i]) / len(h[i]), 1) if h.get(i) else None for i in range(24)] for (y, s), h in sorted(prof.items())}
    last_day = dt.datetime.fromtimestamp(ts[-1], PARIS).date()
    cut = dt.datetime.combine(last_day - dt.timedelta(days=2), dt.time(), PARIS).timestamp()
    recent = [[t, pts[t]] for t in ts if t >= cut]
    data = {"daily": daily, "negHours": {str(y): round(v, 1) for y, v in sorted(neg.items())}, "profiles": profiles,
            "recent": recent, "lastDay": last_day.isoformat(), "license": meta.get("license", "CC BY 4.0 from Bundesnetzagentur | SMARD.de"),
            "fetched": meta.get("fetched")}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.PRICES=")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    print(f"{len(daily)} jours ({daily[0][0]} → {daily[-1][0]}), {len(recent)} pas récents, {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()
