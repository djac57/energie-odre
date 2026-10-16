"""Prix spot day-ahead, zone France, via l'API Energy-Charts (Fraunhofer ISE), sans clé.

Source des données : Bundesnetzagentur | SMARD.de, licence CC BY 4.0 (champ license_info de l'API).
Stockage : data/raw/prices/<année>.json = {horodatage unix (s): prix €/MWh}, une valeur par ligne (diffable).
Pas horaire jusqu'au passage européen au quart d'heure (2025), puis 15 min.
Premier passage : historique depuis 2015 par tranches mensuelles (pause entre appels) ; ensuite seulement
les derniers jours et le lendemain (publié vers 13 h).

    python3 scripts/fetch_prices.py data/raw
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(RAW, "prices")
API = "https://api.energy-charts.info/price"
UA = "energie-odre-viz/1.0 (projet personnel)"
FIRST = dt.date(2015, 1, 1)


def get(start, end, retries=4):
    url = API + "?" + urllib.parse.urlencode({"bzn": "FR", "start": start.isoformat(), "end": end.isoformat()})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(int(e.headers.get("Retry-After") or 20 * (attempt + 1)))
                continue
            if e.code == 404:  # pas de données sur la période
                return {"unix_seconds": [], "price": []}
            raise


def load_year(y):
    try:
        with open(os.path.join(OUT, f"{y}.json"), encoding="utf-8") as fh:
            return {int(k): v for k, v in json.load(fh).items()}
    except FileNotFoundError:
        return {}


def save_year(y, data):
    tmp = os.path.join(OUT, f"{y}.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({str(k): data[k] for k in sorted(data)}, fh, separators=(",", ":"), indent=0)
    os.replace(tmp, os.path.join(OUT, f"{y}.json"))


def main():
    os.makedirs(OUT, exist_ok=True)
    today = dt.date.today()
    years = {y: load_year(y) for y in range(FIRST.year, today.year + 2)}
    have = max((k for d in years.values() for k in d), default=None)
    if have is None:
        chunks, cur = [], FIRST
        while cur <= today:
            nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            chunks.append((cur, nxt - dt.timedelta(days=1)))
            cur = nxt
    else:
        start = dt.datetime.fromtimestamp(have, dt.timezone.utc).date() - dt.timedelta(days=3)
        chunks = [(start, today + dt.timedelta(days=1))]
    lic, n = None, 0
    try:
        for a, b in chunks:
            res = get(a, b)
            lic = res.get("license_info") or lic
            for t, p in zip(res.get("unix_seconds", []), res.get("price", [])):
                if p is not None:
                    years.setdefault(dt.datetime.fromtimestamp(t, dt.timezone.utc).year, {})[t] = p; n += 1
            if len(chunks) > 1:
                time.sleep(1.5)  # API publique : on reste sobre
    except Exception as e:
        print(f"Prix : échec ({type(e).__name__}: {str(e)[:160]}) — données déjà récupérées conservées.")
    for y, d in years.items():
        if d:
            save_year(y, d)
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"license": lic or "CC BY 4.0 from Bundesnetzagentur | SMARD.de", "source": API + "?bzn=FR",
                   "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, fh, ensure_ascii=False, indent=0)
    print(f"Prix : {n} valeurs reçues sur {len(chunks)} appel(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
