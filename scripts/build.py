"""Agrège les données éCO2mix téléchargées par fetch.py en un site/data.js compact.

    python3 scripts/build.py [dossier_brut] [site/data.js]

Sorties (puissances moyennes en MW, arrondies) :
- jour par jour : mix de production, consommation, pompage, solde des échanges,
  taux de CO2 et échanges commerciaux par frontière ;
- heure par heure : consommation, CO2, solaire, éolien (pour les cartes de chaleur) ;
- jour type : moyenne par mois et par heure de chaque filière ;
- régions : moyenne mensuelle par filière ;
- records sur les pas semi-horaires.
"""
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(__file__)
RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "raw")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "site", "data.js")

SOURCES = ["nucleaire", "hydraulique", "eolien", "solaire", "bioenergies", "gaz", "charbon", "fioul"]
OTHER = ["consommation", "pompage", "ech_physiques", "taux_co2"]
BORDERS = ["ech_comm_angleterre", "ech_comm_espagne", "ech_comm_italie", "ech_comm_suisse", "ech_comm_allemagne_belgique"]
DAILY = SOURCES + OTHER + BORDERS
HOURLY = ["consommation", "taux_co2", "solaire", "eolien"]
PROFILE = SOURCES + ["consommation", "pompage", "ech_physiques"]
REGION_FIELDS = ["consommation", "nucleaire", "hydraulique", "eolien", "solaire", "bioenergies", "thermique", "ech_physiques"]


def num(v):
    v = v.strip()
    return float(v) if v not in ("", "-", "ND") else None


def read_national():
    """Pas semi-horaires (les lignes au quart d'heure sont vides) : dict horodatage -> valeurs."""
    rows = {}
    for name in ["eco2mix-national-cons-def.csv", "eco2mix-national-tr.csv"]:
        path = os.path.join(RAW, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                c = num(r["consommation"] or "")
                if c is None:
                    continue
                rows[r["date_heure"][:16]] = {k: num(r.get(k) or "") for k in DAILY}  # consolidé d'abord, puis TR
    return rows


def avg(lst):
    lst = [x for x in lst if x is not None]
    return sum(lst) / len(lst) if lst else None


def main():
    nat = read_national()
    stamps = sorted(nat)
    first = dt.date.fromisoformat(stamps[0][:10])
    last = dt.date.fromisoformat(stamps[-1][:10])
    ndays = (last - first).days + 1

    day_acc = defaultdict(lambda: defaultdict(list))
    hour_acc = defaultdict(lambda: defaultdict(list))
    prof_acc = defaultdict(lambda: defaultdict(list))
    # Échanges par frontière et par mois, sens séparés : moyenne des exports (<0) et des imports (>0)
    border_acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))
    month_days = defaultdict(set)
    records = {}

    def record(key, value, stamp, better):
        if value is None:
            return
        if key not in records or better(value, records[key][0]):
            records[key] = (value, stamp)

    for st in stamps:
        v = nat[st]
        d = (dt.date.fromisoformat(st[:10]) - first).days
        h = int(st[11:13])
        for k in DAILY:
            day_acc[d][k].append(v[k])
        for k in HOURLY:
            hour_acc[d * 24 + h][k].append(v[k])
        for k in PROFILE:
            prof_acc[(st[:7], h)][k].append(v[k])
        month_days[st[:7]].add(st[:10])
        for k in BORDERS:
            x = v[k]
            if x is not None:
                a = border_acc[st[:7]][k]
                a[0] += max(0.0, -x); a[1] += max(0.0, x); a[2] += 1
        record("conso_max", v["consommation"], st, lambda a, b: a > b)
        record("conso_min", v["consommation"], st, lambda a, b: a < b)
        record("solaire_max", v["solaire"], st, lambda a, b: a > b)
        record("eolien_max", v["eolien"], st, lambda a, b: a > b)
        record("nucleaire_max", v["nucleaire"], st, lambda a, b: a > b)
        record("co2_max", v["taux_co2"], st, lambda a, b: a > b)
        record("co2_min", v["taux_co2"], st, lambda a, b: a < b)
        record("export_max", v["ech_physiques"], st, lambda a, b: a < b)  # négatif = exportations
        record("import_max", v["ech_physiques"], st, lambda a, b: a > b)

    def series(acc, n, fields):
        out = {k: [] for k in fields}
        for i in range(n):
            for k in fields:
                a = avg(acc[i][k]) if i in acc else None
                out[k].append(None if a is None else round(a))
        return out

    daily = series(day_acc, ndays, DAILY)
    hourly = series(hour_acc, ndays * 24, HOURLY)
    months = sorted({k[0] for k in prof_acc})
    profile = {k: [[None if (a := avg(prof_acc[(m, h)][k])) is None else round(a) for h in range(24)] for m in months]
               for k in PROFILE}

    border_months = sorted(border_acc)
    borders = {}
    for k in BORDERS:
        rows = []
        for m in border_months:
            ex, im, n = border_acc[m][k]
            hours = len(month_days[m]) * 24
            rows.append([round(ex / n * hours / 1e6, 3), round(im / n * hours / 1e6, 3)] if n else None)  # TWh
        borders[k] = rows

    # Météo régionale (Weathernews France via ODRÉ) : vent à 100 m (m/s), rayonnement solaire global (W/m²)
    weather = defaultdict(dict)
    wpath = os.path.join(RAW, "rayonnement-solaire-vitesse-vent-tri-horaires-regionaux.json")
    if os.path.exists(wpath):
        for r in json.load(open(wpath, encoding="utf-8")):
            m = r.get("mois") or r.get("date_format(date,'yyyy-MM')")
            if m and (r.get("n") or 0) >= 200:  # mois complets seulement (≈ 8 relevés par jour)
                weather[r["region"]][m] = [round(r["vent"], 2), round(r["rayonnement"], 1)]
    w_months = sorted({m for v in weather.values() for m in v})
    weather_out = {name: [v.get(m) for m in w_months] for name, v in sorted(weather.items())}

    # Régions : moyenne mensuelle par filière (TR seulement pour les mois absents du consolidé)
    regions = defaultdict(dict)
    for name in ["eco2mix-regional-cons-def.json", "eco2mix-regional-tr.json"]:
        path = os.path.join(RAW, name)
        if not os.path.exists(path):
            continue
        for r in json.load(open(path, encoding="utf-8")):
            m = r.get("mois") or r.get("date_format(date_heure,'yyyy-MM')")
            if not m or not r.get("n") or m in regions[r["libelle_region"]]:
                continue
            eol = r.get("eolien")
            if eol is None and (r.get("eolien_terrestre") is not None or r.get("eolien_offshore") is not None):
                eol = (r.get("eolien_terrestre") or 0) + (r.get("eolien_offshore") or 0)
            # sinon : éolien non disponible (avant 2021, seulement en texte dans la source)
            vals = {**r, "eolien": eol}
            regions[r["libelle_region"]][m] = [None if vals.get(k) is None else round(vals[k]) for k in REGION_FIELDS]
    reg_months = sorted({m for v in regions.values() for m in v})
    # Les sous-colonnes éolien terrestre / en mer ne sont remplies qu'à partir de 2024 (zéros avant, alors que la
    # colonne texte a des valeurs) : avant le premier mois non nul d'une région, l'éolien est « non disponible »
    ie = REGION_FIELDS.index("eolien")
    for name, by_month in regions.items():
        started = False
        for m in sorted(by_month):
            v = by_month[m]
            if v[ie]:
                started = True
            elif not started:
                v[ie] = None
    reg_out = {name: [v.get(m) for m in reg_months] for name, v in sorted(regions.items())}

    data = {
        "start": first.isoformat(), "days": ndays, "lastStamp": stamps[-1],
        "sources": SOURCES, "daily": daily, "hourly": hourly,
        "profileMonths": months, "profile": profile,
        "regionFields": REGION_FIELDS, "regionMonths": reg_months, "regions": reg_out,
        "records": {k: [round(v), s] for k, (v, s) in records.items()},
        "borderMonths": border_months, "borders": borders,
        "weatherMonths": w_months, "weather": weather_out,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.DATA=")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    print(f"{first} → {stamps[-1]} : {ndays} jours, {len(months)} mois de profils, {len(reg_out)} régions "
          f"({reg_months[0]} → {reg_months[-1]}), {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()


# Empreinte de data.js dans la page : évite qu'un navigateur garde un vieux fichier en cache
def stamp_page():
    import hashlib
    digest = hashlib.sha1(open(OUT, "rb").read()).hexdigest()[:10]
    page = os.path.join(os.path.dirname(OUT), "index.html")
    if os.path.exists(page):
        src = open(page, encoding="utf-8").read()
        new = re.sub(r'src="data\.js(\?v=[\w-]+)?"', f'src="data.js?v={digest}"', src)
        if new != src:
            open(page, "w", encoding="utf-8").write(new)


stamp_page()
