"""Hauteur de neige modélisée sur quelques massifs (Open-Meteo, réanalyse ERA5 / ERA5-Land de Copernicus, CC BY 4.0).

Ce n'est pas une mesure : c'est la hauteur de neige moyenne journalière d'une maille de réanalyse (environ 10 à 25 km),
à l'altitude de la maille (indiquée). Elle sert à comparer les hivers entre eux, pas à connaître l'enneigement d'une station.
Stockage : data/raw/snow/snow.json = {massif: {"elev": m, "d": {jour: cm}}}, depuis l'hiver 2015-2016, complété chaque jour.

    python3 scripts/fetch_snow.py data/raw
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(RAW, "snow")
UA = "energie-odre-viz/1.0 (projet personnel)"
FIRST = dt.date(2015, 9, 1)
# Points choisis en haute montagne (maille la plus haute possible), près des bassins des grands barrages
MASSIFS = {"Alpes du Nord": (45.45, 6.95), "Alpes du Sud": (44.65, 6.75), "Pyrénées": (42.80, 0.15), "Massif central": (45.53, 2.81)}


def main():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "snow.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    today = dt.date.today()
    for name, (lat, lon) in MASSIFS.items():
        rec = data.setdefault(name, {"lat": lat, "lon": lon, "d": {}})
        start = dt.date.fromisoformat(max(rec["d"])) - dt.timedelta(days=10) if rec["d"] else FIRST
        try:
            url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode({
                "latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": today.isoformat(),
                "daily": "snow_depth_mean", "timezone": "Europe/Paris"})
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
                res = json.load(r)
            rec["elev"] = res.get("elevation")
            for d, v in zip(res["daily"]["time"], res["daily"]["snow_depth_mean"]):
                if v is not None:
                    rec["d"][d] = round(v * 100)  # m → cm
            print(f"Neige {name} : {len(rec['d'])} jours, maille à {rec['elev']} m")
        except Exception as e:
            print(f"Neige {name} : échec ({str(e)[:120]}) — données conservées.")
        time.sleep(1)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True, indent=0)
    os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
