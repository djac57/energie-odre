"""Construit site/europe.js : la France et ses voisins.

- Ember (CC BY 4.0) : mix annuel par filière (TWh), intensité carbone (gCO2/kWh), demande, imports nets ;
  séries mensuelles (intensité carbone, part bas carbone et renouvelable) — voir fetch_ember.py ;
- Energy-Charts (CC BY 4.0, Bundesnetzagentur | SMARD.de) : prix day-ahead moyens par zone — voir fetch_prices_eu.py.

    python3 scripts/build_europe.py data/raw site/europe.js
"""
import json
import os
import sys
from collections import defaultdict

RAW, OUT = sys.argv[1], sys.argv[2]
ZONES = [("FR", "France", "250"), ("DE", "Allemagne", "276"), ("BE", "Belgique", "056"), ("ES", "Espagne", "724"), ("IT", "Italie", "380"),
         ("CH", "Suisse", "756"), ("GB", "Royaume-Uni", "826"), ("NL", "Pays-Bas", "528"), ("AT", "Autriche", "040"), ("PL", "Pologne", "616"),
         ("PT", "Portugal", "620"), ("EU", "Union européenne", None)]
FUELS = ["Nuclear", "Hydro", "Wind", "Solar", "Bioenergy", "Other Renewables", "Gas", "Coal", "Other Fossil"]
LOW = {"Nuclear", "Hydro", "Wind", "Solar", "Bioenergy", "Other Renewables"}
REN = LOW - {"Nuclear"}


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def main():
    ey = load(os.path.join(RAW, "ember", "yearly.json"), {})
    em = load(os.path.join(RAW, "ember", "monthly.json"), {})
    out = {"zones": [{"code": c, "name": n, "geo": g} for c, n, g in ZONES], "fuels": FUELS, "yearly": {}, "monthly": {}, "prices": {}}
    for c, _, _ in ZONES:
        z = ey.get(c, {})
        years = sorted({y for f in FUELS for y in z.get(f, {})})
        out["yearly"][c] = {y: {"mix": [z.get(f, {}).get(y, 0) for f in FUELS], "co2": z.get("co2", {}).get(y),
                                "demand": z.get("demand", {}).get(y), "imports": z.get("imports", {}).get(y)} for y in years}
        m = em.get(c, {})
        months = sorted({k for f in FUELS for k in m.get(f, {}) if k >= "2015-01"})
        rows = []
        for k in months:
            mix = {f: m.get(f, {}).get(k, 0) for f in FUELS}
            tot = sum(mix.values())
            if tot <= 0:
                continue
            rows.append([k, m.get("co2", {}).get(k), round(sum(mix[f] for f in LOW) / tot, 4), round(sum(mix[f] for f in REN) / tot, 4),
                         [round(mix[f], 3) for f in FUELS]])
        out["monthly"][c] = rows
    # Prix : moyennes mensuelles depuis 2015 et journalières sur les 400 derniers jours
    for c, _, _ in ZONES:
        ser = load(os.path.join(RAW, "prices_eu", c + ".json"), {})
        if not ser:
            continue
        acc = defaultdict(list)
        for d, v in ser.items():
            acc[d[:7]].append(v)
        days = sorted(ser)
        out["prices"][c] = {"monthly": [[k, round(sum(v) / len(v), 1), len(v)] for k, v in sorted(acc.items())],
                            "daily": [[d, ser[d]] for d in days[-400:]]}
    meta_e = load(os.path.join(RAW, "ember", "meta.json"), {})
    meta_p = load(os.path.join(RAW, "prices_eu", "meta.json"), {})
    out["meta"] = {"ember": meta_e.get("license", "CC BY 4.0 — Ember"), "emberYearly": meta_e.get("yearly"), "emberMonthly": meta_e.get("monthly"),
                   "prices": meta_p.get("license", "CC BY 4.0"), "priceZones": meta_p.get("zones", {})}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.EUROPE = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"europe.js : {len(out['yearly'])} zones, prix pour {len(out['prices'])} zones, {os.path.getsize(OUT) // 1024} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
