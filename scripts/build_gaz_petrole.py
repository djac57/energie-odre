"""Construit site/autresenergies.js (page « Autres énergies » : gaz, pétrole, charbon, uranium)
à partir de data/raw/gaz_petrole/ et data/raw/ais/ (voir fetch_gaz_petrole.py, fetch_ais.py).

    python3 scripts/build_gaz_petrole.py [data/raw] [site/autresenergies.js]
"""
import hashlib
import json
import os
import re
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ais_ports import PORT_LIST, decode_destination, offshore_of, port_at  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "raw"), "gaz_petrole")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "site", "autresenergies.js")
PAGE = "autres-energies.html"
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


# ---------------------------------------------------------------- Europe (AGSI+, ALSI)
def agsi():
    a = load("agsi")
    if not a:
        return None
    days = sorted(a.get("FR", {}))
    out = {"days": days, "countries": {}}
    r1 = lambda x: None if x is None else round(x, 1)
    for c, v in a.items():
        out["countries"][c] = {"full": [r1(v.get(d, [None])[0]) for d in days],
                               "twh": [r1(v.get(d, [None, None])[1]) for d in days] if c in ("FR", "eu") else None,
                               "wgv": next((v[d][4] for d in reversed(days) if d in v and v[d][4]), None)}
    fr = a.get("FR", {})
    recent = days[-730:]
    out["frFlows"] = {"days": recent, "inj": [fr.get(d, [None] * 5)[2] for d in recent], "wd": [fr.get(d, [None] * 5)[3] for d in recent]}
    return out


def alsi():
    a = load("alsi")
    if not a:
        return None
    out = {}
    for name, v in a.items():
        days = sorted(v)
        monthly = defaultdict(list)
        for d in days:
            if v[d][1] is not None:
                monthly[d[:7]].append(v[d][1])
        recent = days[-400:]
        out[name] = {"days": recent, "inv": [v[d][0] for d in recent], "send": [v[d][1] for d in recent], "cap": next((v[d][2] for d in reversed(days) if v[d][2]), None),
                     "monthly": [[m, round(sum(x) / 1000, 2)] for m, x in sorted(monthly.items())]}  # TWh émis par mois
    return out


# ---------------------------------------------------------------- Flux physiques (ENTSOG)
ENTSOG_FR = {"ITP-00045": ["Norvège", "Dunkerque", 51.03, 2.24], "ITP-00115": ["Belgique (gaz L)", "Taisnières", 50.33, 3.92],
             "ITP-00526": ["Belgique", "Virtualys", 50.6, 3.5], "ITP-00137": ["Allemagne", "Obergailbach", 49.12, 7.21],
             "ITP-00304": ["Espagne", "Pirineos", 43.2, -1.6], "ITP-00039": ["Suisse", "Oltingue", 47.49, 7.39],
             "ITP-00281": ["Suisse (Jura)", "Jura", 46.3, 6.0], "LNG-00024": ["GNL", "Montoir", 47.31, -2.15],
             "LNG-00029": ["GNL", "Fos", 43.42, 4.87], "LNG-00071": ["GNL", "Le Havre", 49.47, 0.17]}
# Gazoducs d'approvisionnement de l'UE depuis les zones de production : tracés schématiques
# dessinés à la main d'après des points de passage connus (lat, lon), état et date.
# flux : (point ENTSOG, sens « A>B ») lus dans entsog_borders, sinon le point d'entrée dans entsog_eu.
PIPELINES = [
    {"id": "franpipe", "origin": "Norvège", "name": "Franpipe", "from": "Troll / Kollsnes", "to": "Dunkerque",
     "path": [[60.65, 3.72], [60.55, 4.84], [59.2, 2.9], [58.19, 2.47], [55.5, 2.9], [53.0, 2.75], [51.03, 2.24]],
     "status": "ouvert", "since": "1998", "flow": [("ITP-00045", "NO>FR")]},
    {"id": "zeepipe", "origin": "Norvège", "name": "Zeepipe", "from": "Troll / Kollsnes", "to": "Zeebrugge",
     "path": [[60.55, 4.84], [59.0, 2.4], [58.37, 1.91], [55.5, 2.6], [53.0, 3.0], [51.33, 3.2]],
     "status": "ouvert", "since": "1993", "flow": [("ITP-00106", "NO>BE")]},
    {"id": "europipe", "origin": "Norvège", "name": "Europipe I et II", "from": "Kårstø / Draupner", "to": "Dornum",
     "path": [[59.27, 5.52], [58.6, 4.0], [58.19, 2.47], [56.6, 4.6], [55.0, 6.2], [53.65, 7.42]],
     "status": "ouvert", "since": "1995 et 1999", "flow": [("ITP-00525", "NO>DE")]},
    {"id": "norpipe", "origin": "Norvège", "name": "Norpipe", "from": "Ekofisk", "to": "Emden",
     "path": [[56.55, 3.21], [55.3, 5.5], [54.2, 6.7], [53.36, 7.2]],
     "status": "non publié", "since": "1977", "note": "point d'Emden non publié dans les données ENTSOG", "flow": []},
    {"id": "langeled", "origin": "Norvège", "name": "Langeled", "from": "Nyhamna (Ormen Lange)", "to": "Easington (Royaume-Uni)",
     "path": [[62.85, 6.93], [61.5, 4.0], [59.5, 2.6], [58.37, 1.91], [56.0, 1.3], [53.65, 0.12]],
     "status": "ouvert", "since": "2007", "flow": [("ITP-00091", "NO>UK")]},
    {"id": "vesterled", "origin": "Norvège", "name": "Vesterled", "from": "Heimdal", "to": "St Fergus (Royaume-Uni)",
     "path": [[59.57, 2.23], [58.4, 0.5], [57.57, -1.84]],
     "status": "ouvert", "since": "1978", "flow": [("ITP-00022", "NO>UK")]},
    {"id": "balticpipe", "origin": "Norvège", "name": "Baltic Pipe", "from": "Europipe II", "to": "Pologne (Niechorze)",
     "path": [[57.6, 4.6], [56.5, 6.5], [55.53, 8.1], [55.4, 10.4], [55.25, 12.12], [54.7, 13.6], [54.1, 15.1]],
     "status": "ouvert", "since": "30 nov. 2022", "flow": [("ITP-00630", "NO>DK")]},
    {"id": "nordstream", "origin": "Russie", "name": "Nord Stream 1 et 2", "from": "Vyborg / Oust-Louga", "to": "Lubmin",
     "path": [[60.55, 28.6], [60.0, 26.5], [59.4, 23.0], [58.0, 20.0], [56.2, 17.0], [55.0, 15.0], [54.15, 13.65]],
     "status": "fermé", "since": "31 août 2022", "note": "arrêté fin août 2022, conduites sabotées le 26 sept. 2022 ; point non publié", "flow": []},
    {"id": "yamal", "origin": "Russie", "name": "Yamal-Europe", "from": "Sibérie occidentale", "to": "Mallnow (Allemagne)",
     "path": [[66.0, 76.7], [64.0, 60.0], [58.5, 40.0], [57.0, 34.97], [55.0, 31.0], [53.9, 27.6], [52.4, 23.6], [52.25, 21.0], [52.45, 14.5]],
     "status": "fermé", "since": "mai 2022", "note": "plus de flux russe vers l'ouest depuis mai 2022 ; flux inversés Allemagne → Pologne", "flow": [("ITP-00096", "PL>DE")]},
    {"id": "brotherhood", "origin": "Russie", "name": "Brotherhood / Soyouz (transit Ukraine)", "from": "Sibérie occidentale", "to": "Velké Kapušany → Baumgarten",
     "path": [[66.0, 76.7], [62.0, 58.0], [55.0, 45.0], [51.2, 35.3], [50.4, 30.5], [49.6, 25.5], [48.55, 22.08], [48.4, 19.5], [48.31, 16.87]],
     "status": "fermé", "since": "1er janv. 2025", "note": "transit par l'Ukraine arrêté à l'expiration du contrat", "flow": [("ITP-00117", None)]},
    {"id": "turkstream", "origin": "Russie", "name": "TurkStream → Balkan Stream", "from": "Anapa (Russkaïa)", "to": "Bulgarie, Serbie, Hongrie",
     "path": [[44.9, 37.3], [43.6, 34.0], [42.3, 30.0], [41.63, 28.1], [42.05, 27.0], [42.9, 25.0], [43.75, 22.55], [44.8, 20.5], [46.17, 19.95], [47.0, 19.2]],
     "status": "ouvert", "since": "2020 (Hongrie : oct. 2021)", "flow": [("ITP-00549", "TR>BG")]},
    {"id": "belarus", "origin": "Russie", "name": "Minsk–Vilnius (vers Kaliningrad)", "from": "Russie via Biélorussie", "to": "Lituanie, Kaliningrad",
     "path": [[55.0, 31.0], [54.1, 28.5], [54.45, 25.9], [54.7, 23.6], [54.95, 22.0], [54.7, 20.5]],
     "status": "ouvert", "since": "transit seulement", "note": "gaz russe en transit vers Kaliningrad", "flow": [("ITP-00085", "BY>LT")]},
    {"id": "imatra", "origin": "Russie", "name": "Imatra (Finlande)", "from": "Russie", "to": "Finlande",
     "path": [[60.6, 33.0], [61.2, 28.8], [60.9, 26.5]],
     "status": "fermé", "since": "21 mai 2022", "flow": [("ITP-00024", "RU>FI")]},
    {"id": "transmed", "origin": "Algérie", "name": "Transmed (Enrico Mattei)", "from": "Hassi R'Mel", "to": "Mazara del Vallo (Italie)",
     "path": [[32.93, 3.27], [34.6, 5.5], [36.5, 8.3], [36.85, 10.2], [37.05, 11.0], [37.65, 12.59], [38.1, 15.6], [40.5, 15.8], [42.0, 13.5]],
     "status": "ouvert", "since": "1983", "flow": [("ITP-00093", "DZ>IT")]},
    {"id": "medgaz", "origin": "Algérie", "name": "Medgaz", "from": "Hassi R'Mel", "to": "Almería (Espagne)",
     "path": [[32.93, 3.27], [34.4, 0.6], [35.3, -1.4], [36.83, -2.46]],
     "status": "ouvert", "since": "2011", "flow": [("ITP-00048", "DZ>ES")]},
    {"id": "gme", "origin": "Algérie", "name": "Maghreb-Europe (GME)", "from": "Hassi R'Mel", "to": "Tarifa via le Maroc",
     "path": [[32.93, 3.27], [34.0, -1.6], [34.2, -4.0], [35.75, -5.8], [36.01, -5.6], [37.9, -4.8]],
     "status": "fermé", "since": "31 oct. 2021", "note": "contrat non renouvelé ; depuis 2022 le gaz circule de l'Espagne vers le Maroc", "flow": [("ITP-00082", "DZ>ES")]},
    {"id": "greenstream", "origin": "Libye", "name": "Greenstream", "from": "Wafa / Mellitah", "to": "Gela (Sicile)",
     "path": [[29.5, 10.0], [32.85, 12.25], [35.0, 13.3], [37.07, 14.25]],
     "status": "ouvert", "since": "2004", "flow": [("ITP-00074", "LY>IT")]},
    {"id": "sgc", "origin": "Azerbaïdjan", "name": "SCP + TANAP", "from": "Shah Deniz (Caspienne)", "to": "Kipoi (Grèce)",
     "path": [[39.9, 50.5], [40.18, 49.47], [41.0, 46.5], [41.7, 44.8], [40.9, 42.8], [39.9, 41.3], [39.8, 36.0], [39.8, 30.5], [40.4, 27.5], [40.96, 26.32]],
     "status": "ouvert", "since": "TANAP 2018", "flow": [("ITP-00274", "TR>GR")]},
    {"id": "tap", "origin": "Azerbaïdjan", "name": "TAP", "from": "Kipoi", "to": "Melendugno (Italie)",
     "path": [[40.96, 26.32], [40.9, 24.5], [40.65, 22.9], [40.6, 21.2], [40.72, 19.56], [40.4, 19.0], [40.27, 18.33]],
     "status": "ouvert", "since": "31 déc. 2020", "flow": [("ITP-00008", "AL>IT")]},
    {"id": "iuk", "origin": "Royaume-Uni", "name": "Interconnector", "from": "Bacton", "to": "Zeebrugge",
     "path": [[52.86, 1.46], [52.2, 2.4], [51.33, 3.2]], "status": "ouvert", "since": "1998", "flow": [("ITP-00061", "UK>BE")]},
    {"id": "bbl", "origin": "Royaume-Uni", "name": "BBL", "from": "Bacton", "to": "Balgzand (Pays-Bas)",
     "path": [[52.86, 1.46], [53.0, 3.0], [52.9, 4.8]], "status": "ouvert", "since": "2006", "flow": [("ITP-00207", "UK>NL")]},
]
# Emplacement schématique des frontières gazières (paires de pays triées), lat, lon
BORDER_POS = {
    "DE|NL": [52.45, 6.95], "NO|UK": [56.4, 0.4], "DE|NO": [54.1, 6.9], "BE|DE": [50.67, 6.1], "FR|NO": [51.25, 2.15], "BG|TR": [42.05, 27.05],
    "AT|DE": [48.45, 13.5], "BE|NO": [51.55, 3.0], "DZ|IT": [37.5, 12.2], "GR|TR": [40.96, 26.32], "AL|IT": [40.35, 18.7], "DZ|ES": [36.5, -2.3],
    "BG|RS": [43.75, 22.55], "CZ|DE": [50.6, 13.45], "CH|IT": [46.43, 8.35], "BE|FR": [50.33, 3.92], "HU|RS": [46.17, 19.95], "DK|PL": [54.7, 13.6],
    "IE|UK": [53.9, -5.4], "CH|FR": [47.49, 7.39], "BE|NL": [51.35, 4.2], "DK|NO": [55.9, 7.4], "BG|RO": [43.8, 28.1], "AT|IT": [46.55, 13.6],
    "NL|UK": [53.0, 3.0], "CH|DE": [47.6, 7.9], "HU|SK": [48.1, 19.3], "ES|PT": [39.0, -7.05], "HU|RO": [46.25, 20.85], "BE|UK": [51.75, 2.35],
    "BG|GR": [41.35, 23.4], "LT|LV": [56.35, 24.0], "BY|LT": [54.45, 25.9], "LT|RU": [54.95, 22.6], "DE|PL": [52.45, 14.5], "HU|UA": [48.2, 22.4],
    "ES|MA": [36.0, -5.6], "MD|RO": [47.2, 27.8], "HR|SI": [46.2, 15.6], "ES|FR": [43.25, -1.3], "IT|SI": [45.95, 13.62], "AT|SI": [46.7, 15.65],
    "EE|FI": [59.7, 24.4], "HR|HU": [45.85, 17.4], "IT|LY": [37.0, 14.0], "PL|UA": [50.05, 23.5], "AT|SK": [48.31, 16.9], "BG|MK": [42.3, 22.6],
    "CZ|SK": [48.7, 16.95], "AT|HU": [47.9, 17.2], "DE|DK": [54.8, 9.4], "SK|UA": [48.55, 22.1], "CZ|PL": [49.75, 18.6], "PL|SK": [49.35, 21.9],
    "LT|PL": [54.05, 23.4], "BE|LU": [49.6, 5.85], "DE|LU": [49.55, 6.37], "RO|UA": [45.2, 28.4], "FI|RU": [61.2, 28.8], "EE|LV": [57.75, 26.0],
    "EE|RU": [57.65, 27.6], "LV|RU": [57.4, 27.8], "GR|IT": [40.6, 19.3], "DE|RU": [54.15, 13.65], "BG|MK": [42.3, 22.6], "MD|UA": [46.6, 30.0],
}
EU2 = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}


def _days_monthly(series, days):
    """Valeurs des derniers jours et moyennes mensuelles (GWh/j) depuis 2021."""
    monthly = defaultdict(list)
    for d, x in series.items():
        monthly[d[:7]].append(x or 0)
    return [round(series[d]) if series.get(d) is not None else None for d in days], {m: round(sum(v) / len(v), 1) for m, v in monthly.items()}


def gas_network():
    b, eu = load("entsog_borders"), load("entsog_eu")
    flows, points = b.get("flows", {}), b.get("points", {})
    alldays = sorted({d for f in flows.values() for v in f.values() for d in v} | {d for v in eu.values() for d in v})
    if not alldays:
        return None
    # dernier jour : médiane des derniers jours publiés par point (le jour en cours est souvent incomplet)
    lasts = sorted([max(v) for f in flows.values() for v in f.values() if v] + [max(v) for v in eu.values() if v])
    end = lasts[len(lasts) // 2]
    alldays = [d for d in alldays if d <= end]
    days = alldays[-400:]
    months = sorted({d[:7] for d in alldays if d >= "2021-01"})

    def pair_dir(pk, dirn):
        return flows.get(pk, {}).get(dirn) if dirn else None

    # --- gazoducs depuis l'origine
    pipes = []
    for p in PIPELINES:
        series = defaultdict(float)
        for pk, dirn in p["flow"]:
            s = pair_dir(pk, dirn) or eu.get(pk) or {}
            for d, x in s.items():
                series[d] += x or 0
        v, mo = _days_monthly(series, days) if series else ([None] * len(days), {})
        pipes.append({k: p[k] for k in ("id", "origin", "name", "from", "to", "path", "status", "since") if k in p} |
                     {"note": p.get("note"), "v": v, "m": [mo.get(m) for m in months], "points": [pk for pk, _ in p["flow"]]})

    # --- frontières : somme des points par sens ; si un point virtuel (VIP) et des points physiques publient
    # le même jour pour la même frontière, on garde la plus grande des deux sommes (évite les doubles comptes)
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))  # (paire, sens) -> jour -> [VIP, physiques]
    per_point = defaultdict(dict)
    for pk, f in flows.items():
        vip = "VIP" in points.get(pk, {}).get("label", "").upper()
        for dirn, s in f.items():
            a, c = dirn.split(">")
            if "SM" in (a, c):
                continue
            for d, x in s.items():
                agg[dirn][d][0 if vip else 1] += x or 0
            per_point[pk][dirn] = s
    borders = {}
    for dirn, byday in agg.items():
        a, c = dirn.split(">")
        key = "|".join(sorted((a, c)))
        if key not in BORDER_POS:
            continue
        series = {d: max(x) for d, x in byday.items()}
        v, mo = _days_monthly(series, days)
        br = borders.setdefault(key, {"pos": BORDER_POS[key], "a": sorted((a, c))[0], "b": sorted((a, c))[1], "dirs": {}, "points": []})
        br["dirs"][dirn] = {"v": v, "m": [mo.get(m) for m in months]}
    for pk, f in per_point.items():
        a, c = points[pk]["pair"]
        key = f"{a}|{c}"
        if key in borders:
            borders[key]["points"].append({"label": points[pk]["label"], "pk": pk,
                                           "dirs": {dirn: [_days_monthly(s, [])[1].get(m) for m in months] for dirn, s in f.items()}})  # GWh/j, moyenne du mois
    for br in borders.values():  # cat : entrée dans l'UE, échange intra-UE, hors UE
        br["cat"] = "intra" if br["a"] in EU2 and br["b"] in EU2 else "entry" if br["a"] in EU2 or br["b"] in EU2 else "other"
    lng = {k[5:]: {"v": [round(x) if x is not None else None for x in v["send"]], "days": v["days"]} for k, v in (alsi() or {}).items() if k.startswith("pays:")}
    extra_iso = {"GR": "300", "MD": "498", "MK": "807"}
    extra_nom = {"EE": "Estonie", "GR": "Grèce", "HR": "Croatie", "LU": "Luxembourg", "LV": "Lettonie", "MD": "Moldavie", "MK": "Macédoine du Nord", "SI": "Slovénie"}
    codes = sorted({c for br in borders.values() for c in (br["a"], br["b"])})
    names = {c: extra_nom.get(c) or FR_NAMES.get(c, c) for c in codes}
    iso = {c: extra_iso.get(c) or ISO_NUM.get(c) for c in codes}
    return {"days": days, "months": months, "pipes": pipes, "borders": borders, "lng": lng, "names": names, "iso": iso}

def daily_monthly(series, days_back=400):
    days = sorted(series)
    recent = days[-days_back:]
    monthly = defaultdict(float)
    for d in days:
        monthly[d[:7]] += series[d] or 0
    return {"days": recent, "v": [series[d] for d in recent], "monthly": [[m, round(x / 1000, 2)] for m, x in sorted(monthly.items())]}  # TWh/mois


def entsog_fr():
    e = load("entsog")
    out = {}
    for k, v in e.items():
        pk, direction = k.split("|")
        if pk in ENTSOG_FR and v:
            out[k] = {**daily_monthly(v), "label": ENTSOG_FR[pk][0], "site": ENTSOG_FR[pk][1], "dir": direction, "pos": ENTSOG_FR[pk][2:]}
    return out


# ---------------------------------------------------------------- Importations par origine (Eurostat)
ISO_NUM = {"NO": "578", "DZ": "012", "BE": "056", "NG": "566", "DE": "276", "QA": "634", "ES": "724", "RU": "643", "UA": "804", "NL": "528",
           "SK": "703", "AT": "040", "US": "840", "BY": "112", "CZ": "203", "TN": "788", "PL": "616", "UK": "826", "CH": "756", "TR": "792",
           "FR": "250", "AL": "008", "DK": "208", "MA": "504", "BG": "100", "LY": "434", "RS": "688", "TT": "780", "RO": "642", "PE": "604",
           "HU": "348", "AO": "024", "LT": "440", "EL": "300", "EG": "818", "LV": "428", "HR": "191", "KZ": "398", "SA": "682", "IQ": "368",
           "IR": "364", "AZ": "031", "BR": "076", "AE": "784", "CA": "124", "MX": "484", "GQ": "226", "CM": "120", "KW": "414", "GA": "266",
           "IT": "380", "CD": "180", "CI": "384", "CG": "178", "GH": "288", "NE": "562", "AR": "032", "TM": "795", "CO": "170", "GY": "328",
           "OM": "512", "EC": "218", "VE": "862", "SE": "752", "FI": "246", "IE": "372", "PT": "620", "MT": "470", "CY": "196", "SI": "705",
           "EE": "233", "LU": "442", "IL": "376", "AU": "036", "MY": "458", "ID": "360", "BN": "096", "PG": "598", "MZ": "508", "SN": "686",
           "MR": "478", "CN": "156", "IN": "356", "KR": "410", "JP": "392", "SG": "702", "GE": "268", "UZ": "860", "YE": "887", "TD": "148"}
FR_NAMES = {"NO": "Norvège", "DZ": "Algérie", "BE": "Belgique", "NG": "Nigeria", "DE": "Allemagne", "QA": "Qatar", "ES": "Espagne", "RU": "Russie",
            "UA": "Ukraine", "NL": "Pays-Bas", "SK": "Slovaquie", "AT": "Autriche", "US": "États-Unis", "BY": "Biélorussie", "CZ": "Tchéquie",
            "TN": "Tunisie", "PL": "Pologne", "UK": "Royaume-Uni", "CH": "Suisse", "TR": "Turquie", "FR": "France", "AL": "Albanie", "DK": "Danemark",
            "MA": "Maroc", "BG": "Bulgarie", "LY": "Libye", "RS": "Serbie", "TT": "Trinité-et-Tobago", "RO": "Roumanie", "PE": "Pérou",
            "HU": "Hongrie", "AO": "Angola", "LT": "Lituanie", "EL": "Grèce", "EG": "Égypte", "KZ": "Kazakhstan", "SA": "Arabie saoudite",
            "IQ": "Irak", "IR": "Iran", "AZ": "Azerbaïdjan", "BR": "Brésil", "AE": "Émirats arabes unis", "CA": "Canada", "MX": "Mexique",
            "GQ": "Guinée équatoriale", "CM": "Cameroun", "KW": "Koweït", "GA": "Gabon", "IT": "Italie", "CD": "RD Congo", "CI": "Côte d'Ivoire",
            "CG": "Congo", "GH": "Ghana", "NE": "Niger", "AR": "Argentine", "TM": "Turkménistan", "CO": "Colombie", "GY": "Guyana", "OM": "Oman",
            "EC": "Équateur", "VE": "Venezuela", "SE": "Suède", "FI": "Finlande", "IE": "Irlande", "PT": "Portugal", "MT": "Malte", "CY": "Chypre",
            "EG ": "Égypte", "IL": "Israël", "AU": "Australie", "MY": "Malaisie", "ID": "Indonésie", "MZ": "Mozambique", "SN": "Sénégal",
            "MR": "Mauritanie", "CN": "Chine", "GE": "Géorgie", "JP": "Japon", "IN": "Inde", "KR": "Corée du Sud", "SG": "Singapour", "UZ": "Ouzbékistan", "YE": "Yémen", "TD": "Tchad"}


def imports():
    e = load("eurostat_imports")
    if not e:
        return None
    out = {}

    def pack(series, prefix):
        months = sorted({t for k, v in series.items() if k.startswith(prefix) for t in v})
        tot = series.get(prefix + "TOTAL", {})
        partners = {}
        for k, v in series.items():
            if not k.startswith(prefix):
                continue
            code = k[len(prefix):]
            if code in ISO_NUM and sum(x or 0 for x in v.values()) > 0:
                partners[code] = {"id": ISO_NUM[code], "nom": FR_NAMES.get(code, e["labels"].get(code, code)), "v": [v.get(m) for m in months]}
        return {"months": months, "total": [tot.get(m) for m in months], "partners": partners}

    out["gasFR"] = pack(e["gas_FR"], "G3000|")
    out["lngFR"] = pack(e["gas_FR"], "G3200|")
    out["gasEU"] = pack(e["gas_EU27_2020"], "G3000|")
    out["lngEU"] = pack(e["gas_EU27_2020"], "G3200|")
    out["oilFR"] = pack({"X|" + k: v for k, v in e["oil_FR"].items()}, "X|")
    return out


def ttf():
    t = load("ttf")
    return [[m, t[m]] for m in sorted(t)] if t else None


# ---------------------------------------------------------------- Navires (AIS)
MID = {"227": "France", "228": "France", "226": "France", "636": "Libéria", "637": "Libéria", "538": "Îles Marshall", "351": "Panama", "352": "Panama",
       "353": "Panama", "354": "Panama", "355": "Panama", "356": "Panama", "357": "Panama", "370": "Panama", "371": "Panama", "372": "Panama",
       "373": "Panama", "374": "Panama", "311": "Bahamas", "308": "Bahamas", "309": "Bahamas", "215": "Malte", "229": "Malte", "248": "Malte",
       "249": "Malte", "256": "Malte", "209": "Chypre", "210": "Chypre", "212": "Chypre", "240": "Grèce", "241": "Grèce", "237": "Grèce",
       "239": "Grèce", "244": "Pays-Bas", "245": "Pays-Bas", "246": "Pays-Bas", "219": "Danemark", "220": "Danemark", "257": "Norvège",
       "258": "Norvège", "259": "Norvège", "563": "Singapour", "564": "Singapour", "565": "Singapour", "566": "Singapour", "477": "Hong Kong",
       "412": "Chine", "413": "Chine", "414": "Chine", "431": "Japon", "432": "Japon", "440": "Corée du Sud", "441": "Corée du Sud",
       "232": "Royaume-Uni", "233": "Royaume-Uni", "234": "Royaume-Uni", "235": "Royaume-Uni", "247": "Italie", "224": "Espagne", "225": "Espagne",
       "211": "Allemagne", "218": "Allemagne", "205": "Belgique", "255": "Portugal (Madère)", "263": "Portugal", "273": "Russie", "271": "Turquie",
       "470": "Émirats arabes unis", "471": "Émirats arabes unis", "466": "Qatar", "403": "Arabie saoudite", "422": "Iran", "605": "Algérie",
       "657": "Nigeria", "338": "États-Unis", "366": "États-Unis", "367": "États-Unis", "368": "États-Unis", "369": "États-Unis", "316": "Canada",
       "525": "Indonésie", "533": "Malaisie", "548": "Philippines", "574": "Viêt Nam", "419": "Inde", "265": "Suède", "266": "Suède", "230": "Finlande",
       "276": "Estonie", "261": "Pologne", "238": "Croatie", "250": "Irlande", "253": "Luxembourg", "613": "Cameroun", "620": "Comores",
       "667": "Sierra Leone", "671": "Togo", "341": "Saint-Kitts-et-Nevis", "376": "Saint-Vincent-et-les-Grenadines", "377": "Saint-Vincent-et-les-Grenadines",
       "312": "Belize", "314": "Barbade", "305": "Antigua-et-Barbuda", "319": "Îles Caïmans", "310": "Bermudes", "518": "Îles Cook", "576": "Vanuatu",
       "577": "Vanuatu", "511": "Palaos", "457": "Mongolie", "620 ": "Comores", "601": "Afrique du Sud", "645": "Maurice", "672": "Tunisie",
       "622": "Égypte", "450": "Gabon"}


def capacity(s):
    """Capacité estimée d'après les dimensions déclarées (AIS ne donne pas la cargaison)."""
    L, draught, name = s.get("len") or 0, s.get("draught") or 0, (s.get("name") or "").upper()
    lng = "LNG" in name or ((s.get("type") in (80, 84) and 270 <= L <= 350 and (s.get("beam") or 0) >= 42 and draught <= 12.6) and s.get("lngdest"))
    if lng:
        if L >= 340:
            cls, m3, laden = "Q-Max", 266000, 12.0
        elif L >= 315:
            cls, m3, laden = "Q-Flex", 210000, 12.0
        elif L >= 280:
            cls, m3, laden = "Méthanier conventionnel", 170000, 11.5
        elif L >= 240:
            cls, m3, laden = "Méthanier moyen", 140000, 11.0
        else:
            cls, m3, laden = "Petit méthanier", max(5000, round(L * 180, -3)), 8.0
        return {"kind": "lng", "cls": cls, "m3": m3, "gwh": round(m3 * 0.0064, 0), "laden": None if not draught else draught >= .85 * laden}
    if L >= 320:
        cls, dwt, laden = "VLCC", 300000, 20.0
    elif L >= 250:
        cls, dwt, laden = "Suezmax", 155000, 15.5
    elif L >= 225:
        cls, dwt, laden = "Aframax / LR2", 105000, 13.5
    elif L >= 195:
        cls, dwt, laden = "Panamax / LR1", 70000, 12.5
    elif L >= 165:
        cls, dwt, laden = "MR (produits)", 47000, 11.5
    elif L >= 120:
        cls, dwt, laden = "Handy (produits / chimie)", 20000, 9.0
    else:
        cls, dwt, laden = "Caboteur", 6000, 6.5
    return {"kind": "oil", "cls": cls, "dwt": dwt, "laden": None if not draught else draught >= .8 * laden}


def ais():
    base = os.path.join(RAW, "..", "ais")
    if not os.path.exists(os.path.join(base, "snapshot.json")):
        return None
    rd = lambda n: json.load(open(os.path.join(base, n + ".json"), encoding="utf-8")) if os.path.exists(os.path.join(base, n + ".json")) else {}
    snap, tracks, voyages = rd("snapshot"), rd("tracks"), rd("voyages")
    ships = []
    for x in snap.get("ships", []):
        x = dict(x)
        x["lngdest"] = bool(re.search(r"Montoir|Fos|Dunkerque|Le Havre", x.get("terminal") or ""))
        x["cap"] = capacity(x)
        x["flag"] = MID.get(str(x["mmsi"])[:3], "")
        x.pop("lngdest", None)
        # Destination déclarée -> port (code UN/LOCODE ou nom libre), sinon non décodée
        port, how = decode_destination(x.get("dest"))
        x["port"] = {"id": port["id"], "name": port["name"], "code": port["code"], "lat": port["lat"], "lon": port["lon"], "how": how} if port else None
        # Trace observée (1 point / 20 min au plus pour la page) et départ déduit de l'historique
        tr, last = [], None
        for p in tracks.get(x["mmsi"], []):
            if last is None or p[0] - last >= 1200:
                tr.append([p[0], p[1], p[2]])
                last = p[0]
        x["track"] = tr
        v = voyages.get(x["mmsi"], {})
        stop, prev, first = v.get("stop"), v.get("prev"), v.get("first")
        here = port_at(x["lat"], x["lon"]) if (x.get("sog") or 0) < 0.5 else None
        if here and stop and stop["id"] == here["id"]:
            x["atPort"] = {"name": stop["name"], "since": stop["t0"]}
            dep = prev
        else:
            x["atPort"] = {"name": here["name"], "since": None} if here else None
            dep = stop
        if dep:
            x["dep"] = {"kind": "port", "name": dep["name"], "t": dep["t"], "lat": dep["lat"], "lon": dep["lon"]}
        else:
            f = first or (tr[0] if tr else [x["t"], x["lat"], x["lon"]])
            near, dist = offshore_of(f[1], f[2])
            x["dep"] = {"kind": "first", "name": near, "nm": dist, "t": f[0], "lat": f[1], "lon": f[2]}
        ships.append(x)
    ports = [{"name": p["name"], "code": p["code"], "lat": p["lat"], "lon": p["lon"], "rank": p["rank"]} for p in PORT_LIST]
    return {"t": snap.get("t"), "seconds": snap.get("seconds"), "messages": snap.get("messages"), "ships": ships, "ports": ports}


# ---------------------------------------------------------------- Charbon, uranium, prix (FMI)
def coal():
    c = load("coal")
    if not c:
        return None
    lab = c.get("labels", {})
    years = sorted({t for v in c["imports"].values() for t in v})
    partners = {p: {"id": ISO_NUM[p], "nom": FR_NAMES.get(p, lab.get(p, p)), "v": [v.get(y) for y in years]}
                for p, v in c["imports"].items() if p in ISO_NUM and sum(x or 0 for x in v.values()) > 0}
    tot = c["imports"].get("TOTAL", {})
    power = load("coal_power")
    units = {}
    rte_js = os.path.join(os.path.dirname(OUT), "rte.js")
    daily = load("../rte/unit_daily")
    if os.path.exists(rte_js) and daily:  # production quotidienne des groupes charbon (lecture seule des données RTE)
        src = open(rte_js, encoding="utf-8").read()
        groups = json.loads(src[src.index("=") + 1:].rstrip().rstrip(";")).get("groups", {})
        for gid, g in groups.items():
            if g.get("type") == "FOSSIL_HARD_COAL" and gid in daily:
                v = daily[gid]
                ks = sorted(v) if isinstance(v, dict) else []
                units[g["name"]] = [[k, v[k]] for k in ks]
    return {"imports": {"years": years, "total": [tot.get(y) for y in years], "partners": partners},
            "balance": c.get("balance", {}), "power": [[m, power[m]] for m in sorted(power)], "units": units}


def uranium():
    u = load("uranium")
    if not u:
        return None
    lab = u.get("labels", {})
    q, val = u["series"].get("2844|QUANTITY_IN_100KG", {}), u["series"].get("2844|VALUE_IN_EUROS", {})
    years = sorted({t[:4] for v in q.values() for t in v})

    def yearly(series):
        out = defaultdict(float)
        for t, x in series.items():
            out[t[:4]] += x or 0
        return out

    partners = {}
    for p, v in q.items():
        if p in ISO_NUM:
            y = yearly(v)
            if sum(y.values()) > 0:
                yv = yearly(val.get(p, {}))
                partners[p] = {"id": ISO_NUM[p], "nom": FR_NAMES.get(p, lab.get(p, p)), "t": [round(y.get(k, 0) / 10, 1) for k in years],  # tonnes
                               "eur": [round(yv.get(k, 0) / 1e6, 1) for k in years]}  # M€
    world = yearly(q.get("WORLD", {}))
    months = sorted(q.get("WORLD", {}))
    return {"years": years, "total": [round(world.get(k, 0) / 10, 1) for k in years], "partners": partners,
            "monthly": [[m, round((q["WORLD"].get(m) or 0) / 10, 1)] for m in months]}


def gas_prices():
    """Prix du gaz par pays (Eurostat), €/kWh -> c€/kWh, semestres."""
    g = load("gas_prices")
    if not g:
        return None
    out = {}
    for seg, series in g.items():
        sems = sorted({t for v in series.values() for t in v})
        out[seg] = {"sems": sems, "s": {k: [round(v[t] * 100, 2) if v.get(t) is not None else None for t in sems] for k, v in series.items()}}
    out["iso"] = {"AL": "008", "AT": "040", "BA": "070", "BE": "056", "BG": "100", "CZ": "203", "DE": "276", "DK": "208", "EE": "233", "EL": "300", "ES": "724",
                  "FR": "250", "GE": "268", "HR": "191", "HU": "348", "IE": "372", "IT": "380", "LI": "438", "LT": "440", "LU": "442", "LV": "428", "MD": "498",
                  "MK": "807", "NL": "528", "PL": "616", "PT": "620", "RO": "642", "RS": "688", "SE": "752", "SI": "705", "SK": "703", "TR": "792", "UA": "804",
                  "UK": "826", "FI": "246", "NO": "578", "CY": "196", "MT": "470", "ME": "499", "XK": "-99"}
    out["names"] = {"AL": "Albanie", "AT": "Autriche", "BA": "Bosnie-Herzégovine", "BE": "Belgique", "BG": "Bulgarie", "CZ": "Tchéquie", "DE": "Allemagne",
                    "DK": "Danemark", "EE": "Estonie", "EL": "Grèce", "ES": "Espagne", "FR": "France", "GE": "Géorgie", "HR": "Croatie", "HU": "Hongrie",
                    "IE": "Irlande", "IT": "Italie", "LI": "Liechtenstein", "LT": "Lituanie", "LU": "Luxembourg", "LV": "Lettonie", "MD": "Moldavie",
                    "MK": "Macédoine du Nord", "NL": "Pays-Bas", "PL": "Pologne", "PT": "Portugal", "RO": "Roumanie", "RS": "Serbie", "SE": "Suède",
                    "SI": "Slovénie", "SK": "Slovaquie", "TR": "Turquie", "UA": "Ukraine", "UK": "Royaume-Uni", "EU27_2020": "Moyenne UE", "EA": "Zone euro"}
    return out


def imf_prices():
    f = load("fred_prices")
    return {k: [[m, v[m]] for m in sorted(v)] for k, v in f.items()} if f else None


def main():
    data = {"storage": storage(), "conso": consumption(), "ecogaz": ecogaz(), "fuel": load("fuel_snapshot"),
            "fuelDaily": load("fuel_daily"), "wob": oil_bulletin(), "brent": brent(),
            "agsi": agsi(), "alsi": alsi(), "entsogFR": entsog_fr(), "network": gas_network(), "gasPrices": gas_prices(), "imports": imports(), "ttf": ttf(), "ais": ais(),
            "coal": coal(), "uranium": uranium(), "imf": imf_prices()}
    data["updated"] = max(filter(None, [data["storage"]["days"][-1] if data["storage"]["days"] else None,
                                        data["conso"]["lastDp"], data["fuel"].get("maj", "")[:10]]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.AE=")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    digest = hashlib.sha1(open(OUT, "rb").read()).hexdigest()[:10]
    page = os.path.join(os.path.dirname(OUT), PAGE)
    if os.path.exists(page):  # empreinte anti-cache
        src = open(page, encoding="utf-8").read()
        new = re.sub(r'src="autresenergies\.js(\?v=[\w-]+)?"', f'src="autresenergies.js?v={digest}"', src)
        coast = os.path.join(os.path.dirname(OUT), "geo_coast10.js")
        if os.path.exists(coast):  # fond de carte détaillé chargé à la demande par la carte des navires
            cd = hashlib.sha1(open(coast, "rb").read()).hexdigest()[:10]
            new = re.sub(r'"geo_coast10\.js(\?v=[\w-]+)?"', f'"geo_coast10.js?v={cd}"', new)
        if new != src:
            open(page, "w", encoding="utf-8").write(new)
    print(f"{OUT} : {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()
