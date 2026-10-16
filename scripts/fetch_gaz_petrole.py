"""Gaz & pétrole : télécharge des données ouvertes, sans clé, et les fusionne dans data/raw/gaz_petrole/.

Sources (toutes réutilisables) :
- ODRÉ (Licence Ouverte 2.0) : stocks des stockages de gaz par PITS, consommation de gaz des
  distributions publiques et des industriels raccordés au transport, températures, signal Ecogaz ;
- data.economie.gouv.fr (Licence Ouverte) : prix des carburants, flux instantané (instantané figé
  au moment du build, la page le recharge aussi en direct) ;
- Commission européenne, Weekly Oil Bulletin (réutilisation autorisée, © Union européenne) :
  prix hebdomadaires à la pompe, France et voisins, depuis 2005 ;
- U.S. EIA via FRED (série DCOILBRENTEU, domaine public) : prix spot du Brent ;
- GIE AGSI+ et ALSI (clé gratuite AGSI_KEY) : stocks de gaz par pays et terminaux GNL ;
- ENTSOG Transparency Platform (sans clé) : flux physiques quotidiens aux points d'entrée français ;
- FMI via FRED (clé gratuite FRED_KEY, série PNGASEUUSDM) : prix mensuel du gaz en Europe (TTF).
Les clés se lisent dans l'environnement, sinon dans ~/.config/<agsi|fred>/credentials ; une clé
absente fait simplement sauter l'étape correspondante.

    python3 scripts/fetch_gaz_petrole.py [data/raw]

Chaque fichier est un dictionnaire {clé: valeur} fusionné avec l'existant : un téléchargement
partiel ne fait jamais disparaître l'historique déjà versionné.
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict

RAW = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw"), "gaz_petrole")
ODRE = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/{ds}/exports/json"
ECO = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/prix-des-carburants-en-france-flux-instantane-v2"
WOB_PAGE = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
BRENT = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
UA = "Mozilla/5.0 (compatible; energie-odre-viz/1.0)"
FUELS = ["gazole", "e10", "sp95", "sp98", "e85", "gplc"]
WOB_COUNTRIES = ["FR", "DE", "BE", "LU", "ES", "IT", "NL", "EU"]
AGSI_COUNTRIES = ["FR", "DE", "IT", "NL", "AT", "ES", "BE", "eu"]
ALSI_TERMINALS = {  # société, installation (listing ALSI)
    "Dunkerque": ("21X000000001331I", "21W0000000000451"),
    "Montoir-de-Bretagne": ("21X0000000010679", "63W631527814486R"),
    "Fos Tonkin": ("21X0000000010679", "63W179356656691A"),
    "Fos Cavaou": ("21X000000001070K", "63W943693783886F"),
    "Le Havre (FSRU)": ("63X218318139939Y", "63W835858561887S"),
}
# Points d'interconnexion côté réseau français (ENTSOG), avec l'origine ou la destination du gaz
ENTSOG_POINTS = {
    "ITP-00045": "Norvège (Dunkerque)", "ITP-00115": "Belgique (Taisnières L)", "ITP-00526": "Belgique (Virtualys)",
    "ITP-00137": "Allemagne (Obergailbach)", "ITP-00304": "Espagne (Pirineos)", "ITP-00039": "Suisse (Oltingue)",
    "ITP-00281": "Suisse (Jura)", "LNG-00024": "GNL Montoir", "LNG-00029": "GNL Fos", "LNG-00071": "GNL Le Havre",
}
ENTSOG = "https://transparency.entsog.eu/api/v1/operationaldatas"
# Grands points d'entrée de l'UE (côté gestionnaire européen), regroupés par corridor d'origine
EU_ENTRIES = {
    "ITP-00045": "Norvège", "ITP-00106": "Norvège", "ITP-00208": "Norvège", "ITP-00525": "Norvège", "ITP-00209": "Norvège",
    "ITP-00210": "Norvège", "ITP-00630": "Norvège",
    "ITP-00120": "Russie", "ITP-00501": "Russie", "ITP-00117": "Russie", "ITP-00549": "Russie", "ITP-00024": "Russie",
    "ITP-00085": "Russie",
    "ITP-00093": "Algérie", "ITP-00048": "Algérie", "ITP-00082": "Algérie", "ITP-00074": "Libye",
    "ITP-00008": "Azerbaïdjan", "ITP-00274": "Azerbaïdjan", "ITP-00061": "Royaume-Uni", "ITP-00207": "Royaume-Uni",
}
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
EU_CODES = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
            "PL", "PT", "RO", "SK", "SI", "ES", "SE"}


def key(name, env):
    if os.environ.get(env):
        return os.environ[env]
    path = os.path.expanduser(f"~/.config/{name}/credentials")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith(env + "="):
                return line.strip().split("=", 1)[1]
    return None


def last_date(name, sub=None, back=12):
    """Date de reprise : dernier jour connu moins `back` jours (révisions), ou None."""
    path = os.path.join(RAW, name + ".json")
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding="utf-8"))
    d = d.get(sub, {}) if sub else d
    if not d:
        return None
    t = time.mktime(time.strptime(max(d)[:10], "%Y-%m-%d")) - back * 86400
    return time.strftime("%Y-%m-%d", time.localtime(t))


def get(url, retries=3, timeout=180):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  nouvel essai ({e})", file=sys.stderr)
            time.sleep(5 * (attempt + 1))


def odre(ds, **params):
    return json.loads(get(ODRE.format(ds=ds) + "?" + urllib.parse.urlencode(params)))


def merge(name, fresh):
    """Fusionne {clé: valeur} dans RAW/name.json (les nouvelles valeurs l'emportent)."""
    path = os.path.join(RAW, name + ".json")
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    before = len(old)
    old.update(fresh)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(old.items())), fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  {name}: {before} -> {len(old)} entrées")


def step(label, fn):
    try:
        print(label)
        fn()
    except Exception as e:  # une source en panne ne bloque pas les autres
        print(f"  ÉCHEC {label} : {e}", file=sys.stderr)


def gas_storage():
    rows = odre("stock-quotidien-stockages-gaz", select="date,pits,stock_fin_de_journee")
    out = {}
    for r in rows:
        if r.get("stock_fin_de_journee") is not None:
            out.setdefault(r["date"][:10], {})[r["pits"]] = round(r["stock_fin_de_journee"], 1)  # GWh PCS
    # fusion au niveau du jour : on complète les PITS sans écraser ceux déjà connus
    path = os.path.join(RAW, "storage.json")
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    for d, v in out.items():
        old.setdefault(d, {}).update(v)
    merge("storage", old)


def gas_consumption():
    dp = odre("conso-jour-nat-eldgrd-grt", select="date,sum(conso_journaliere_mwh_pcs_0degc) as v,count(*) as n", group_by="date")
    merge("conso_dp", {r["date"][:10]: [round(r["v"] / 1000, 1), r["n"]] for r in dp if r.get("v")})  # GWh, nb d'opérateurs
    ind = odre("conso-journa-industriel-grtgazterega", select="date,operateur,sum(consommation_journaliere_mwh_pcs) as v", group_by="date,operateur")
    days = {}
    for r in ind:
        if r.get("v"):
            days.setdefault(r["date"][:10], {})[r["operateur"]] = r["v"]
    # Provisoire (mois en cours) : somme des colonnes horaires
    prov = odre("consommation-nationale-horaire-de-gaz-donnees-provisoires-grtgaz-terega-v2")
    for r in prov:
        d = r["date"][:10]
        hours = [v for k, v in r.items() if re.fullmatch(r"\d\d_00", k) and isinstance(v, (int, float))]
        if len(hours) >= 20 and r["operateur"] not in days.get(d, {}):
            days.setdefault(d, {})[r["operateur"] + "*"] = sum(hours)  # * = provisoire
    merge("conso_ind", {d: [round(sum(v.values()) / 1000, 1), int(any(k.endswith("*") for k in v)), len(v)] for d, v in days.items()})
    ext = odre("consommation-journaliere-maximale-de-gaz-maille-nationale", select="date,tmoy")
    merge("temperature", {r["date"][:10]: round(r["tmoy"], 2) for r in ext if r.get("tmoy") is not None})


def ecogaz():
    rows = odre("signal-ecogaz", select="gas_day,indice_de_couleur,couleur_du_signal_fr")
    merge("ecogaz", {r["gas_day"][:10]: [int(r["indice_de_couleur"] or 0), r["couleur_du_signal_fr"]] for r in rows})


def fuels_snapshot():
    sel = ",".join(["code_departement", "departement", "count(*) as n"] + [f"avg({f}_prix) as {f}" for f in FUELS])
    url = f"{ECO}/records?" + urllib.parse.urlencode({"select": sel, "group_by": "code_departement,departement", "limit": 120})
    deps = json.loads(get(url))["results"]
    nat = json.loads(get(f"{ECO}/records?" + urllib.parse.urlencode({"select": ",".join(["count(*) as n"] + [f"avg({f}_prix) as {f}" for f in FUELS] + [f"min({f}_prix) as {f}_min" for f in FUELS] + [f"max({f}_maj) as maj_{f}" for f in FUELS])})))["results"][0]
    stamp = max((v for k, v in nat.items() if k.startswith("maj_") and v), default="")[:16]
    snap = {"maj": stamp, "national": {f: round(nat[f], 3) for f in FUELS if nat.get(f)}, "stations": nat["n"],
            "departements": {r["code_departement"]: {"nom": r["departement"], "n": r["n"], **{f: round(r[f], 3) for f in FUELS if r.get(f)}}
                             for r in deps if r.get("code_departement")}}
    merge("fuel_daily", {stamp[:10]: {"national": snap["national"], "stations": snap["stations"]}})  # historique au fil des builds
    with open(os.path.join(RAW, "fuel_snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  fuel_snapshot : {len(snap['departements'])} départements, {snap['stations']} stations, maj {stamp}")


def xlsx_rows(data, sheet_index=1):
    z = zipfile.ZipFile(io.BytesIO(data))
    strs = [re.sub("<[^>]+>", "", m) for m in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S)]
    sheet = z.read(f"xl/worksheets/sheet{sheet_index}.xml").decode("utf-8")
    for row in re.findall(r"<row [^>]*>(.*?)</row>", sheet, re.S):
        cells = {}
        for ref, attrs, v in re.findall(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(?:<f>.*?</f>)?<v>([^<]*)</v>)', row, re.S):
            if v != "":
                cells[ref] = strs[int(v)] if 't="s"' in attrs else v
        yield cells


def oil_bulletin():
    page = get(WOB_PAGE).decode("utf-8", "ignore")
    m = re.search(r'href="([^"]+Prices_History[^"]+\.xlsx)"', page)
    if not m:
        raise RuntimeError("fichier « Prices History » introuvable sur la page du Weekly Oil Bulletin")
    url = urllib.parse.urljoin(WOB_PAGE, m.group(1).replace("&amp;", "&"))
    rows = xlsx_rows(get(url))
    header = next(rows)
    cols = {}
    for ref, name in header.items():
        mm = re.fullmatch(r"([A-Z]{2,3})_price_with_tax_(euro95|diesel)", name.strip())
        if mm and mm.group(1) in WOB_COUNTRIES:
            cols[ref] = (mm.group(1), mm.group(2))
    out = {}
    for cells in rows:
        a = cells.get("A", "")
        if not re.fullmatch(r"\d{5}(\.0)?", a):
            continue
        d = time.strftime("%Y-%m-%d", time.gmtime((int(float(a)) - 25569) * 86400))  # date Excel
        vals = {}
        for ref, (c, f) in cols.items():
            try:
                vals.setdefault(c, {})[f] = round(float(cells[ref]) / 1000, 3)  # €/1000 l -> €/l
            except (KeyError, ValueError):
                pass
        if vals:
            out[d] = vals
    merge("oil_bulletin", out)


def brent():
    text = get(BRENT).decode("utf-8")
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        v = r.get("DCOILBRENTEU", "")
        if v and v != ".":
            out[r["observation_date"]] = float(v)
    merge("brent", out)


def gie(base, params, k):
    rows, page = [], 1
    while True:
        url = base + "?" + urllib.parse.urlencode({**params, "size": 300, "page": page})
        req = urllib.request.Request(url, headers={"User-Agent": UA, "x-key": k})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
        rows += d.get("data", [])
        if page >= int(d.get("last_page") or 1):
            return rows
        page += 1
        time.sleep(0.3)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def agsi():
    k = key("agsi", "AGSI_KEY")
    if not k:
        print("  clé AGSI_KEY absente : étape ignorée")
        return
    path = os.path.join(RAW, "agsi.json")
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    for c in AGSI_COUNTRIES:
        start = last_date("agsi", c) or "2011-01-01"
        params = {"type": "eu"} if c == "eu" else {"country": c}
        rows = gie("https://agsi.gie.eu/api", {**params, "from": start, "to": time.strftime("%Y-%m-%d")}, k)
        cur = data.setdefault(c, {})
        for r in rows:
            # % plein, stock (TWh), injection et soutirage (GWh/j), volume utile (TWh)
            cur[r["gasDayStart"]] = [num(r.get("full")), num(r.get("gasInStorage")), num(r.get("injection")), num(r.get("withdrawal")), num(r.get("workingGasVolume"))]
        data[c] = dict(sorted(cur.items()))
        print(f"  AGSI {c} : {len(rows)} jours lus depuis {start}, {len(cur)} au total")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))


def alsi():
    k = key("agsi", "AGSI_KEY")  # même clé pour AGSI+ et ALSI
    if not k:
        print("  clé AGSI_KEY absente : étape ignorée")
        return
    path = os.path.join(RAW, "alsi.json")
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    targets = {"France": {"country": "FR"}, "UE": {"type": "eu"}}
    targets.update({n: {"country": "FR", "company": c, "facility": f} for n, (c, f) in ALSI_TERMINALS.items()})
    for name, params in targets.items():
        start = last_date("alsi", name) or "2016-01-01"
        rows = gie("https://alsi.gie.eu/api", {**params, "from": start, "to": time.strftime("%Y-%m-%d")}, k)
        cur = data.setdefault(name, {})
        for r in rows:
            inv = r.get("inventory") or {}
            dtmi = r.get("dtmi") or {}
            # stock (GWh), émission vers le réseau (GWh/j), capacité de stockage déclarée (GWh)
            cur[r["gasDayStart"]] = [num(inv.get("gwh")), num(r.get("sendOut")), num(dtmi.get("gwh"))]
        data[name] = dict(sorted(cur.items()))
        print(f"  ALSI {name} : {len(rows)} jours lus depuis {start}")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))


def entsog():
    path = os.path.join(RAW, "entsog.json")
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    start = last_date("entsog", "ITP-00045|entry", back=20) or "2021-01-01"
    y, m = int(start[:4]), int(start[5:7])
    today = time.strftime("%Y-%m-%d")
    while f"{y}-{m:02d}-01" <= today:
        a = f"{y}-{m:02d}-01"
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        b = f"{ny}-{nm:02d}-01"
        url = ENTSOG + "?" + urllib.parse.urlencode({"pointKey": ",".join(ENTSOG_POINTS), "indicator": "Physical Flow", "periodType": "day",
                                                     "from": max(a, start), "to": b, "timezone": "CET", "limit": -1})
        rows = json.loads(get(url, timeout=300)).get("operationaldatas", [])
        for r in rows:
            if not str(r.get("operatorKey", "")).startswith("FR-TSO") or r.get("value") in (None, ""):
                continue
            k2 = f"{r['pointKey']}|{r['directionKey']}"
            d = r["periodFrom"][:10]
            data.setdefault(k2, {})[d] = round(float(r["value"]) / 1e6, 2)  # kWh/j -> GWh/j
        y, m = ny, nm
        time.sleep(0.5)
    data = {k2: dict(sorted(v.items())) for k2, v in sorted(data.items())}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print(f"  ENTSOG : {len(data)} séries, depuis {start}")


def ttf():
    k = key("fred", "FRED_KEY")
    if not k:
        print("  clé FRED_KEY absente : étape ignorée")
        return
    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode({"series_id": "PNGASEUUSDM", "api_key": k, "file_type": "json"})
    obs = json.loads(get(url)).get("observations", [])
    merge("ttf", {o["date"][:7]: float(o["value"]) for o in obs if o.get("value") not in (None, ".", "")})


def eurostat_series(dataset, params):
    """JSON-stat Eurostat -> {partenaire: {période: valeur}}."""
    url = EUROSTAT + dataset + "?" + urllib.parse.urlencode(params, doseq=True)
    d = json.loads(get(url, timeout=300))
    dims, size = d["id"], d["size"]
    cats = {k: sorted(d["dimension"][k]["category"]["index"], key=d["dimension"][k]["category"]["index"].get) for k in dims}
    labels = d["dimension"]["partner"]["category"].get("label", {})
    out = defaultdict(dict)
    for flat, v in d["value"].items():
        i, coords = int(flat), {}
        for k, n in zip(reversed(dims), reversed(size)):
            coords[k] = cats[k][i % n]
            i //= n
        out[(coords.get("siec"), coords["partner"])][coords["time"]] = v
    return out, labels


def eurostat():
    start = "2015-01"
    data = {"labels": {}}
    oil, labels = eurostat_series("nrg_ti_oilm", {"geo": "FR", "siec": "O4100_TOT", "unit": "THS_T", "sinceTimePeriod": start})
    data["labels"] = dict(labels)
    for geo in ("FR", "EU27_2020"):  # un appel par géographie : la clé interne ne garde que (produit, partenaire)
        g, lab = eurostat_series("nrg_ti_gasm", {"geo": geo, "siec": ["G3000", "G3200"], "unit": "TJ_GCV", "sinceTimePeriod": start})
        data["labels"].update(lab)
        data[f"gas_{geo}"] = {f"{s_}|{p}": {t: round(v / 3600, 3) for t, v in series.items()} for (s_, p), series in g.items()}  # TJ -> TWh
    data["oil_FR"] = {p: series for (_, p), series in oil.items()}  # milliers de tonnes
    with open(os.path.join(RAW, "eurostat_imports.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  Eurostat : {len(data['gas_FR'])} séries gaz FR, {len(data['gas_EU27_2020'])} UE, {len(data['oil_FR'])} pétrole FR")


def entsog_eu(points=None, start=None):
    path = os.path.join(RAW, "entsog_eu.json")
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    points = points or list(EU_ENTRIES)
    start = start or last_date("entsog_eu", "ITP-00045", back=20) or "2021-01-01"
    y, m = int(start[:4]), int(start[5:7])
    today = time.strftime("%Y-%m-%d")
    while f"{y}-{m:02d}-01" <= today:
        a = f"{y}-{m:02d}-01"
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        url = ENTSOG + "?" + urllib.parse.urlencode({"pointKey": ",".join(points), "indicator": "Physical Flow", "periodType": "day", "directionKey": "entry",
                                                     "from": max(a, start), "to": f"{ny}-{nm:02d}-01", "timezone": "CET", "limit": -1})
        rows = json.loads(get(url, timeout=300)).get("operationaldatas", [])
        day = defaultdict(lambda: defaultdict(float))
        for r in rows:
            op = str(r.get("operatorKey", ""))
            # côté gestionnaire européen ; TAP (AL-TSO) publie seul le point d'entrée de Kipoi
            if r.get("directionKey") != "entry" or op[:2] not in EU_CODES | {"GR", "AL"} or r.get("value") in (None, ""):
                continue
            day[r["pointKey"]][r["periodFrom"][:10]] += float(r["value"]) / 1e6  # kWh/j -> GWh/j, somme des gestionnaires
        for pk, v in day.items():
            data.setdefault(pk, {}).update({d: round(x, 1) for d, x in v.items()})
        y, m = ny, nm
        time.sleep(0.5)
    data = {k: dict(sorted(v.items())) for k, v in sorted(data.items())}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print(f"  ENTSOG UE : {len(data)} points, depuis {start}")


def jsonstat(url):
    """JSON-stat Eurostat/Comext -> liste de (coordonnées, valeur)."""
    d = json.loads(get(url, timeout=300))
    if "error" in d:
        raise RuntimeError(str(d["error"])[:200])
    dims, size = d["id"], d["size"]
    cats = {k: sorted(d["dimension"][k]["category"]["index"], key=d["dimension"][k]["category"]["index"].get) for k in dims}
    out = []
    for flat, v in d["value"].items():
        i, coords = int(flat), {}
        for k, n in zip(reversed(dims), reversed(size)):
            coords[k] = cats[k][i % n]
            i //= n
        out.append((coords, v))
    labels = d["dimension"].get("partner", {}).get("category", {}).get("label", {})
    return out, labels


def coal():
    data = {}
    rows, lab = jsonstat(EUROSTAT + "nrg_ti_sff?" + urllib.parse.urlencode({"geo": "FR", "siec": "C0100", "unit": "THS_T", "sinceTimePeriod": "2010"}))
    imp = defaultdict(dict)
    for c, v in rows:
        imp[c["partner"]][c["time"]] = v  # milliers de tonnes de houille
    data["imports"], data["labels"] = imp, lab
    rows, _ = jsonstat(EUROSTAT + "nrg_cb_sff?" + urllib.parse.urlencode({"geo": "FR", "siec": "C0000X0350-0370", "unit": "THS_T", "nrg_bal": ["IC_CAL", "IPRD", "IMP", "TI_EHG_MAPE_E"], "sinceTimePeriod": "1990"}, doseq=True))
    bal = defaultdict(dict)
    for c, v in rows:
        bal[c["nrg_bal"]][c["time"]] = v  # consommation intérieure brute, production, importations, entrée des centrales
    data["balance"] = bal
    # Production des centrales au charbon (éCO2mix national, déjà téléchargé par fetch.py) : cumul mensuel
    csv_path = os.path.join(os.path.dirname(RAW), "eco2mix-national-cons-def.csv")
    if os.path.exists(csv_path):
        days = defaultdict(lambda: [0.0, 0])  # somme des MW et nombre de pas renseignés par jour
        for r in csv.DictReader(open(csv_path, encoding="utf-8-sig"), delimiter=";"):
            v = r.get("charbon")
            if v not in (None, ""):
                d = days[r["date_heure"][:10]]
                d[0] += float(v)
                d[1] += 1
        monthly = defaultdict(float)
        for d, (tot, n) in days.items():
            monthly[d[:7]] += tot * 24 / n  # MWh : moyenne des pas renseignés (semi-horaires ou quart-horaires) × 24 h
        merge("coal_power", {m: round(x / 1000, 1) for m, x in monthly.items()})  # GWh
    path = os.path.join(RAW, "coal.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  charbon : {len(imp)} partenaires, bilan {list(bal)}")


def uranium():
    url = ("https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data/DS-045409?" +
           urllib.parse.urlencode({"reporter": "FR", "flow": "1", "product": ["2612", "2844"], "freq": "M",
                                   "indicators": ["QUANTITY_IN_100KG", "VALUE_IN_EUROS"], "sinceTimePeriod": "2015-01"}, doseq=True))
    rows, lab = jsonstat(url)
    out = defaultdict(lambda: defaultdict(dict))
    for c, v in rows:
        out[f"{c['product']}|{c['indicators']}"][c["partner"]][c["time"]] = v
    with open(os.path.join(RAW, "uranium.json"), "w", encoding="utf-8") as fh:
        json.dump({"series": out, "labels": lab}, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"  uranium : {', '.join(f'{k} ({len(v)} partenaires)' for k, v in out.items())}")


def fred_prices():
    k = key("fred", "FRED_KEY")
    if not k:
        print("  clé FRED_KEY absente : étape ignorée")
        return
    out = {}
    for sid in ("PCOALAUUSDM", "PURANUSDM"):
        url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode({"series_id": sid, "api_key": k, "file_type": "json"})
        out[sid] = {o["date"][:7]: float(o["value"]) for o in json.loads(get(url)).get("observations", []) if o.get("value") not in (None, ".", "")}
    merge("fred_prices", out)


def main():
    os.makedirs(RAW, exist_ok=True)
    step("ODRÉ — stocks des stockages de gaz", gas_storage)
    step("ODRÉ — consommation de gaz et températures", gas_consumption)
    step("ODRÉ — signal Ecogaz", ecogaz)
    step("data.economie — prix des carburants (instantané)", fuels_snapshot)
    step("Commission européenne — Weekly Oil Bulletin", oil_bulletin)
    step("EIA via FRED — Brent", brent)
    step("GIE AGSI+ — stocks de gaz en Europe", agsi)
    step("GIE ALSI — terminaux GNL", alsi)
    step("ENTSOG — flux physiques aux points d'entrée", entsog)
    step("FMI via FRED — prix du gaz en Europe (TTF)", ttf)
    step("Eurostat — importations par pays d'origine", eurostat)
    step("ENTSOG — grands points d'entrée de l'UE", entsog_eu)
    step("Eurostat — charbon (importations, bilan) et production des centrales", coal)
    step("Eurostat Comext — importations d'uranium", uranium)
    step("FMI via FRED — prix du charbon et de l'uranium", fred_prices)


if __name__ == "__main__":
    main()
