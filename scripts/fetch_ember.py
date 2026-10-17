"""Ember (ember-energy.org) : exports publics annuels et mensuels, sans clé — licence CC BY 4.0
(« All content is released under a Creative Commons Attribution Licence (CC-BY-4.0) »).

Ne garde qu'un extrait compact : pays d'Europe et agrégat UE ; mix par filière (TWh), production totale,
intensité carbone (gCO2/kWh), demande, imports nets et (mensuel) prix day-ahead.
Ne retélécharge que si le fichier publié a changé (en-tête Last-Modified), ce qui arrive environ une fois par mois.

    python3 scripts/fetch_ember.py data/raw
"""
import csv
import io
import json
import os
import sys
import urllib.request
from collections import defaultdict

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(RAW, "ember")
BASE = "https://storage.googleapis.com/emb-prod-bkt-publicdata/public-downloads/"
FILES = {"yearly": ("yearly_full_release_long_format.csv", "Year"), "monthly": ("monthly_full_release_long_format.csv", "Date")}
# Pays européens (code ISO 3 d'Ember → code à deux lettres de la page) et agrégat « EU »
ISO3 = {"FRA": "FR", "DEU": "DE", "BEL": "BE", "ESP": "ES", "ITA": "IT", "CHE": "CH", "NLD": "NL", "GBR": "GB", "AUT": "AT", "POL": "PL", "PRT": "PT",
        "IRL": "IE", "DNK": "DK", "NOR": "NO", "SWE": "SE", "FIN": "FI", "CZE": "CZ", "SVK": "SK", "HUN": "HU", "SVN": "SI", "HRV": "HR", "ROU": "RO",
        "BGR": "BG", "SRB": "RS", "BIH": "BA", "MNE": "ME", "MKD": "MK", "ALB": "AL", "GRC": "GR", "EST": "EE", "LVA": "LV", "LTU": "LT", "LUX": "LU",
        "UKR": "UA", "MDA": "MD", "TUR": "TR", "XKX": "XK", "BLR": "BY", "CYP": "CY", "MLT": "MT", "ISL": "IS"}
AREAS = {"EU": "EU"}
KEEP = {("Electricity generation", "Fuel"): "gen", ("Electricity generation", "Total"): "gen",
        ("Power sector emissions", "CO2 intensity"): "co2", ("Electricity demand", "Demand"): "demand",
        ("Electricity imports", "Electricity imports"): "imports", ("Electricity prices", "Day-ahead electricity price"): "price"}
UA = "energie-odre-viz/1.0 (projet personnel)"


def head(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.headers.get("Last-Modified")


def main():
    os.makedirs(OUT, exist_ok=True)
    meta_path = os.path.join(OUT, "meta.json")
    meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
    for kind, (fname, datecol) in FILES.items():
        url = BASE + fname
        try:
            lm = head(url)
            if meta.get(kind) == lm and os.path.exists(os.path.join(OUT, kind + ".json")):
                print(f"Ember {kind} : inchangé ({lm})"); continue
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=600) as r:
                text = io.TextIOWrapper(r, encoding="utf-8")
                out = defaultdict(lambda: defaultdict(dict))
                for row in csv.DictReader(text):
                    code = AREAS.get(row["Area"]) or ISO3.get(row.get("ISO 3 code") or "")
                    grp = KEEP.get((row["Category"], row["Subcategory"]))
                    if not code or not grp or row["Value"] in ("", None):
                        continue
                    if grp == "gen" and row["Unit"] != "TWh":
                        continue
                    var = row["Variable"] if grp == "gen" else grp
                    date = row[datecol][:7] if kind == "monthly" else row[datecol]
                    out[code][var][date] = round(float(row["Value"]), 3)
            tmp = os.path.join(OUT, kind + ".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True, indent=0)
            os.replace(tmp, os.path.join(OUT, kind + ".json"))
            meta[kind] = lm
            print(f"Ember {kind} : {len(out)} zones, mis à jour ({lm})")
        except Exception as e:
            print(f"Ember {kind} : échec ({str(e)[:120]}) — extrait versionné conservé.")
    meta["license"] = "CC BY 4.0 — Ember (ember-energy.org)"
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
