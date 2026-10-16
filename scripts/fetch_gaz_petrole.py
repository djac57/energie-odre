"""Gaz & pétrole : télécharge des données ouvertes, sans clé, et les fusionne dans data/raw/gaz_petrole/.

Sources (toutes réutilisables) :
- ODRÉ (Licence Ouverte 2.0) : stocks des stockages de gaz par PITS, consommation de gaz des
  distributions publiques et des industriels raccordés au transport, températures, signal Ecogaz ;
- data.economie.gouv.fr (Licence Ouverte) : prix des carburants, flux instantané (instantané figé
  au moment du build, la page le recharge aussi en direct) ;
- Commission européenne, Weekly Oil Bulletin (réutilisation autorisée, © Union européenne) :
  prix hebdomadaires à la pompe, France et voisins, depuis 2005 ;
- U.S. EIA via FRED (série DCOILBRENTEU, domaine public) : prix spot du Brent.

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

RAW = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw"), "gaz_petrole")
ODRE = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/{ds}/exports/json"
ECO = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/prix-des-carburants-en-france-flux-instantane-v2"
WOB_PAGE = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
BRENT = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
UA = "Mozilla/5.0 (compatible; energie-odre-viz/1.0)"
FUELS = ["gazole", "e10", "sp95", "sp98", "e85", "gplc"]
WOB_COUNTRIES = ["FR", "DE", "BE", "LU", "ES", "IT", "NL", "EU"]


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


def main():
    os.makedirs(RAW, exist_ok=True)
    step("ODRÉ — stocks des stockages de gaz", gas_storage)
    step("ODRÉ — consommation de gaz et températures", gas_consumption)
    step("ODRÉ — signal Ecogaz", ecogaz)
    step("data.economie — prix des carburants (instantané)", fuels_snapshot)
    step("Commission européenne — Weekly Oil Bulletin", oil_bulletin)
    step("EIA via FRED — Brent", brent)


if __name__ == "__main__":
    main()
