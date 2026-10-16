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
# Corridors d'approvisionnement de l'UE : point d'entrée, origine, tracé schématique (lat, lon)
CORRIDORS = {
    "ITP-00045": ("Norvège", "Franpipe → Dunkerque", [[60.6, 3.7], [56.5, 3.1], [51.03, 2.24]]),
    "ITP-00106": ("Norvège", "Zeepipe → Zeebrugge", [[60.6, 3.7], [56.6, 3.4], [51.33, 3.2]]),
    "ITP-00525": ("Norvège", "Europipe → Dornum", [[58.9, 2.9], [55.6, 5.4], [53.65, 7.42]]),
    "ITP-00630": ("Norvège", "Baltic Pipe → Danemark", [[58.5, 2.5], [56.5, 6.5], [55.6, 8.1]]),
    "ITP-00117": ("Russie", "Transit Ukraine → Velké Kapušany", [[51.2, 35.3], [49.6, 28.4], [48.55, 22.08]]),
    "ITP-00549": ("Russie", "TurkStream → Strandzha", [[44.9, 37.3], [43.0, 32.0], [41.75, 28.1], [42.05, 27.0]]),
    "ITP-00085": ("Russie", "Biélorussie → Kotlovka (Lituanie)", [[54.1, 30.5], [54.45, 25.9]]),
    "ITP-00024": ("Russie", "Imatra (Finlande)", [[60.9, 30.6], [61.2, 28.8]]),
    "ITP-00093": ("Algérie", "Transmed → Mazara del Vallo", [[32.9, 3.25], [36.6, 8.6], [37.05, 11.0], [37.65, 12.59]]),
    "ITP-00048": ("Algérie", "Medgaz → Almería", [[32.9, 3.25], [35.3, -1.4], [36.83, -2.46]]),
    "ITP-00082": ("Algérie", "GME via Maroc → Tarifa", [[32.9, 3.25], [34.6, -2.2], [35.75, -5.8], [36.01, -5.6]]),
    "ITP-00074": ("Libye", "Greenstream → Gela", [[32.85, 12.3], [37.07, 14.25]]),
    "ITP-00274": ("Azerbaïdjan", "TANAP/TAP → Kipoi", [[40.4, 49.9], [40.6, 41.0], [40.6, 31.0], [40.96, 26.32]]),
    "ITP-00008": ("Azerbaïdjan", "TAP → Melendugno", [[40.96, 26.32], [40.6, 21.2], [40.27, 18.33]]),
    "ITP-00061": ("Royaume-Uni", "Interconnector → Zeebrugge", [[52.9, 1.4], [51.33, 3.2]]),
}


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


def entsog_eu():
    e = load("entsog_eu")
    out = {}
    for pk, (origin, name, path) in CORRIDORS.items():
        if e.get(pk):
            out[pk] = {**daily_monthly(e[pk]), "origin": origin, "name": name, "path": path}
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
    snap, tracks = load("ais/snapshot", {}) if os.path.exists(os.path.join(RAW, "..", "ais", "snapshot.json")) else {}, {}
    base = os.path.join(RAW, "..", "ais")
    if not os.path.exists(os.path.join(base, "snapshot.json")):
        return None
    snap = json.load(open(os.path.join(base, "snapshot.json"), encoding="utf-8"))
    tracks = json.load(open(os.path.join(base, "tracks.json"), encoding="utf-8")) if os.path.exists(os.path.join(base, "tracks.json")) else {}
    ships = []
    for x in snap.get("ships", []):
        x = dict(x)
        x["lngdest"] = bool(re.search(r"Montoir|Fos|Dunkerque|Le Havre", x.get("terminal") or ""))
        x["cap"] = capacity(x)
        x["flag"] = MID.get(str(x["mmsi"])[:3], "")
        x["track"] = [[p[0], p[1], p[2]] for p in tracks.get(x["mmsi"], [])]
        x.pop("lngdest", None)
        ships.append(x)
    return {"t": snap.get("t"), "seconds": snap.get("seconds"), "messages": snap.get("messages"), "ships": ships}


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


def imf_prices():
    f = load("fred_prices")
    return {k: [[m, v[m]] for m in sorted(v)] for k, v in f.items()} if f else None


def main():
    data = {"storage": storage(), "conso": consumption(), "ecogaz": ecogaz(), "fuel": load("fuel_snapshot"),
            "fuelDaily": load("fuel_daily"), "wob": oil_bulletin(), "brent": brent(),
            "agsi": agsi(), "alsi": alsi(), "entsogFR": entsog_fr(), "corridors": entsog_eu(), "imports": imports(), "ttf": ttf(), "ais": ais(),
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
        if new != src:
            open(page, "w", encoding="utf-8").write(new)
    print(f"{OUT} : {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()
