"""Prix spot day-ahead des voisins (moyennes journalières), via l'API Energy-Charts (Fraunhofer ISE), sans clé.

Licence Energy-Charts : « Unless stated otherwise, … CC BY 4.0 … attribution to Energy-Charts.info »
(données d'origine Bundesnetzagentur | SMARD.de, champ license_info). Le Royaume-Uni n'est pas proposé.
Stockage : data/raw/prices_eu/<zone>.json = {date (heure de Paris): prix moyen €/MWh}.
Premier passage : historique depuis 2015, une requête par an et par zone, avec une pause d'environ 3 s
(l'API répond 429 au-delà) ; ensuite les 10 derniers jours seulement. Lancé une fois par jour.

    python3 scripts/fetch_prices_eu.py data/raw
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(RAW, "prices_eu")
API = "https://api.energy-charts.info/price"
UA = "energie-odre-viz/1.0 (projet personnel)"
FIRST = dt.date(2015, 1, 5)
PARIS = ZoneInfo("Europe/Paris")
# zone affichée → zones Energy-Charts (la zone allemande était commune avec l'Autriche avant octobre 2018)
ZONES = {"FR": ["FR"], "DE": ["DE-LU", "DE-AT-LU"], "BE": ["BE"], "ES": ["ES"], "IT": ["IT-North"], "CH": ["CH"],
         "NL": ["NL"], "AT": ["AT"], "PL": ["PL"], "PT": ["PT"]}
PAUSE = 3.2


def get(bzn, start, end, retries=5):
    url = API + "?" + urllib.parse.urlencode({"bzn": bzn, "start": start.isoformat(), "end": end.isoformat()})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(int(e.headers.get("Retry-After") or 15 * (attempt + 1)))
                continue
            if e.code in (400, 404):
                return {"unix_seconds": [], "price": []}
            raise


def daily(res):
    acc = defaultdict(list)
    for t, p in zip(res.get("unix_seconds") or [], res.get("price") or []):
        if p is not None:
            acc[dt.datetime.fromtimestamp(t, PARIS).date().isoformat()].append(p)
    return {d: round(sum(v) / len(v), 2) for d, v in acc.items() if len(v) >= 20}  # jour complet (24 h ou 96 quarts d'heure)


def main():
    os.makedirs(OUT, exist_ok=True)
    today = dt.date.today()
    lic = None
    for code, bzns in ZONES.items():
        path = os.path.join(OUT, code + ".json")
        try:
            ser = json.load(open(path, encoding="utf-8"))
        except FileNotFoundError:
            ser = {}
        if ser:
            chunks = [(dt.date.fromisoformat(max(ser)) - dt.timedelta(days=10), today + dt.timedelta(days=1))]
        else:
            chunks = [(max(FIRST, dt.date(y, 1, 1)), min(dt.date(y, 12, 31), today + dt.timedelta(days=1)))
                      for y in range(FIRST.year, today.year + 1)]
        n = 0
        try:
            for a, b in chunks:
                got = {}
                for bzn in bzns:  # zone actuelle, puis l'ancienne si la période la précède
                    res = get(bzn, a, b)
                    lic = res.get("license_info") or lic
                    got = {**daily(res), **got}
                    time.sleep(PAUSE)
                    if got and min(got) <= (a + dt.timedelta(days=2)).isoformat():
                        break
                ser.update(got); n += len(got)
        except Exception as e:
            print(f"  {code} : échec ({type(e).__name__}: {str(e)[:120]}) — données déjà reçues conservées.")
        if ser:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(dict(sorted(ser.items())), fh, separators=(",", ":"), indent=0)
            os.replace(tmp, path)
        print(f"Prix {code} : {n} jours reçus, {len(ser)} au total ({min(ser) if ser else '—'} → {max(ser) if ser else '—'})")
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"license": lic or "CC BY 4.0 from Bundesnetzagentur | SMARD.de", "attribution": "Energy-Charts.info",
                   "zones": {k: v[0] for k, v in ZONES.items()}}, fh, ensure_ascii=False, indent=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
