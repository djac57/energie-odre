"""Données de la page « Eau, feux & air » (sources ouvertes, sans clé).

    python3 scripts/fetch_eau_feux_air.py [data/raw]

Chaque source a sa fréquence : le script peut tourner toutes les heures, il ne
retélécharge que ce qui est périmé (dates dans data/raw/eau_feux_air/meta.json).

- NASA FIRMS (LANCE) : détections satellite de points chauds, VIIRS (S-NPP, NOAA-20,
  NOAA-21), fichiers CSV ouverts 24 h / 7 j — Europe + Afrique du Nord (lat >= 27).
  Usage libre (politique NASA d'open data), attribution demandée.
- VigiEau (ministère de la Transition écologique) : niveaux de restriction par
  département (API publique) + historique des arrêtés (data.gouv), Licence Ouverte 2.0.
- BDIFF (base de données sur les incendies de forêt, ministère de l'Agriculture) :
  export annuel des feux de forêt depuis 2006, Licence Ouverte 2.0.
- Météo des forêts (Météo-France) : danger d'incendie par département, J+1 et J+2,
  pendant la saison, Licence Ouverte 2.0.
- Atmo France : indice ATMO quotidien par commune (aujourd'hui + prévision), ODbL —
  les agrégats départementaux dérivés sont publiés sous la même licence.
- IGN, Inventaire forestier national : indicateurs par département, Licence Ouverte 2.0.
- Global Forest Watch (Hansen / UMD) : pertes de couvert arboré par département, CC BY 4.0.
- Contours des départements : france-geojson (G. David, d'après IGN Admin Express), Licence Ouverte.
"""
import csv
import datetime as dt
import gzip
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from http.cookiejar import CookieJar

ROOT = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
OUT = os.path.join(ROOT, "eau_feux_air")
UA = "Mozilla/5.0 (compatible; energie-odre-viz/1.0; +https://djac57.github.io/energie-odre/)"
TODAY = dt.date.today()
NOW = dt.datetime.now(dt.timezone.utc)

FIRMS = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/{prod}/csv/{name}_{region}_{span}.csv"
FIRMS_PRODUCTS = [("suomi-npp-viirs-c2", "SUOMI_VIIRS_C2", "N"), ("noaa-20-viirs-c2", "J1_VIIRS_C2", "N20"),
                  ("noaa-21-viirs-c2", "J2_VIIRS_C2", "N21")]
# (fichier régional FIRMS, latitude minimale, longitude maximale retenues)
# Le fichier « Europe » s'arrête à ~35° E : l'est de l'Ukraine, la Russie d'Europe et le Caucase
# viennent du fichier « Russia_Asia », limité ici à l'ouest de 50° E.
FIRMS_REGIONS = [("Europe", -90, 180), ("Northern_and_Central_Africa", 27.0, 180), ("Russia_Asia", 35.0, 50.0)]
RECENT_DAYS = 31  # détections conservées une à une ; au-delà, agrégats quotidiens

VIGIEAU_API = "https://api.vigieau.gouv.fr/api/departements"
VIGIEAU_DATASET = "https://www.data.gouv.fr/api/1/datasets/donnee-secheresse-vigieau/"
BDIFF = "https://bdiff.agriculture.gouv.fr"
MDF = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/BULLETIN/MDF/mdf_{y}.csv.gz"
ATMO_WFS = ("https://data.atmo-france.org/geoserver/ind/ows?service=WFS&version=2.0.0&request=GetFeature"
            "&typeNames=ind_atmo_2021&outputFormat=csv&propertyName=code_zone,type_zone,date_ech,code_qual,"
            "code_no2,code_o3,code_pm10,code_pm25,code_so2&CQL_FILTER=")
IFN_WFS = ("https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
           "&TYPENAMES=test_nl_ifn_stats_2024:stats_2024&OUTPUTFORMAT=application/json&CQL_FILTER=")
IFN_LABELS = ("ST", "ST_territoire", "tx_bois", "Vmort", "Vvif", "DEPERIS_ESS")
GFW = "https://gfw2-data.s3.amazonaws.com/country-pages/country_stats/download/FRA.xlsx"
DEPS = "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/departements-version-simplifiee.geojson"


# ---------------------------------------------------------------- utilitaires
def log(*a):
    print(*a, flush=True)


def get(url, timeout=180, retries=3, insecure_fallback=False, opener=None, data=None):
    """Téléchargement avec reprises. insecure_fallback : seulement pour BDIFF, dont le serveur
    ne présente pas sa chaîne de certificats complète (erreur « unknown CA »)."""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA}, data=data)
            if opener:
                with opener.open(req, timeout=timeout) as r:
                    return r.read()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except ssl.SSLError as e:
            last = e
            if insecure_fallback:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout,
                                            context=ctx) as r:
                    return r.read()
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 * (i + 1))
    raise last


def jload(name, default):
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def jsave(name, obj, indent=None):
    p = os.path.join(OUT, name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":") if indent is None else None, indent=indent)
    os.replace(tmp, p)


def stale(meta, key, hours):
    t = meta.get(key)
    if not t:
        return True
    return (NOW - dt.datetime.fromisoformat(t)).total_seconds() > hours * 3600


def mark(meta, key):
    meta[key] = NOW.isoformat(timespec="seconds")


# ---------------------------------------------------------------- FIRMS
def fetch_firms(meta):
    recent = jload("firms_recent.json", {"cols": ["lat", "lon", "t", "sat", "frp", "conf", "dn"], "rows": []})
    have = {(r[0], r[1], r[2], r[3]) for r in recent["rows"]}
    first = not recent["rows"]
    spans = ["7d"] if first or stale(meta, "firms_7d", 24) else ["24h"]
    if "7d" not in spans and stale(meta, "firms_7d", 24 * 3):
        spans = ["7d"]
    added = 0
    for span in spans:
        for prod, name, sat in FIRMS_PRODUCTS:
            for region, latmin, lonmax in FIRMS_REGIONS:
                url = FIRMS.format(prod=prod, name=name, region=region, span=span)
                try:
                    raw = get(url, timeout=240).decode("utf-8", "replace")
                except Exception as e:  # noqa: BLE001
                    log(f"  FIRMS {name} {region} {span} : échec ({e})")
                    continue
                for r in csv.DictReader(io.StringIO(raw)):
                    try:
                        lat, lon = round(float(r["latitude"]), 3), round(float(r["longitude"]), 3)
                    except (KeyError, ValueError):
                        continue
                    if lat < latmin or lon > lonmax:
                        continue
                    t = f"{r['acq_date']}T{r['acq_time'].zfill(4)[:2]}:{r['acq_time'].zfill(4)[2:]}Z"
                    key = (lat, lon, t, sat)
                    if key in have:
                        continue
                    have.add(key)
                    try:
                        frp = round(float(r.get("frp") or 0), 1)
                    except ValueError:
                        frp = 0.0
                    conf = {"low": 0, "nominal": 1, "high": 2}.get((r.get("confidence") or "").strip().lower(), 1)
                    recent["rows"].append([lat, lon, t, sat, frp, conf, (r.get("daynight") or "D")[:1]])
                    added += 1
        mark(meta, "firms_" + span)
    # Archive quotidienne compacte (pour la saison) : par jour et par cellule de 0,1°
    daily = jload("firms_daily.json", {})
    cutoff = (TODAY - dt.timedelta(days=RECENT_DAYS)).isoformat()
    keep = []
    for row in recent["rows"]:
        day = row[2][:10]
        if day < cutoff:
            continue
        keep.append(row)
    recent["rows"] = sorted(keep, key=lambda r: r[2])
    # Reconstruit les agrégats des jours couverts par les détections récentes
    per_day = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for lat, lon, t, sat, frp, conf, dn in recent["rows"]:
        cell = f"{round(lat, 1)},{round(lon, 1)}"
        a = per_day[t[:10]][cell]
        a[0] += 1
        a[1] = round(a[1] + frp, 1)
    for day, cells in per_day.items():
        daily[day] = {c: v for c, v in cells.items()}
    jsave("firms_daily.json", dict(sorted(daily.items())))
    jsave("firms_recent.json", recent)
    log(f"  FIRMS : +{added} détections, {len(recent['rows'])} gardées ({RECENT_DAYS} j), {len(daily)} jours archivés")


# ---------------------------------------------------------------- FIRMS : archives par pays
ARCHIVE = "https://firms.modaps.eosdis.nasa.gov/data/country/{prod}/{y}/{prod}_{y}_{country}.csv"
ARCHIVE_COUNTRIES = {"France": "FR", "Spain": "ES", "Portugal": "PT", "Italy": "IT", "Greece": "GR"}
ARCHIVE_PRODUCTS = {"viirs-snpp": 2012, "modis": 2001}  # premières années retenues


class DepIndex:
    """Rattachement d'un point à un département (contours france-geojson), avec préfiltre par emprise."""

    def __init__(self, geo):
        self.items = []
        for f in geo["features"]:
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            xs = [p[0] for poly in polys for p in poly[0]]
            ys = [p[1] for poly in polys for p in poly[0]]
            self.items.append((f["properties"]["code"], (min(xs), min(ys), max(xs), max(ys)), polys))

    @staticmethod
    def _inside(x, y, ring):
        c = False
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
                c = not c
            j = i
        return c

    def find(self, lon, lat):
        for code, (x0, y0, x1, y1), polys in self.items:
            if x0 <= lon <= x1 and y0 <= lat <= y1:
                for poly in polys:
                    if self._inside(lon, lat, poly[0]) and not any(self._inside(lon, lat, h) for h in poly[1:]):
                        return code
        return None


def fetch_firms_archive(meta):
    """Archives annuelles FIRMS par pays (colonne « type » : 0 = feu de végétation présumé,
    1 = volcan, 2 = source fixe terrestre / industrielle, 3 = offshore). Téléchargées une fois,
    agrégées (grille 0,1°, mois, départements) ; seules les années manquantes sont récupérées."""
    arch = jload("firms_archive.json", {})
    static = jload("firms_static.json", {"cells": {}})  # cellule 0,01° -> années où une source fixe (type 2) est vue
    deps = DepIndex(jload("departements.json", {"features": []}))
    last = TODAY.year - 1
    budget = 30  # fichiers par passage au plus (premier remplissage étalé si besoin)
    for prod, first in ARCHIVE_PRODUCTS.items():
        for country, iso in ARCHIVE_COUNTRIES.items():
            for y in range(first, last + 1):
                key = f"{prod}|{iso}|{y}"
                if key in arch or budget <= 0:
                    continue
                budget -= 1
                try:
                    raw = get(ARCHIVE.format(prod=prod, y=y, country=country), timeout=600).decode("utf-8", "replace")
                except Exception as e:  # noqa: BLE001
                    log(f"  FIRMS archive {key} : échec ({e})")
                    continue
                n = 0
                frp = 0.0
                months = [0] * 12
                cells = defaultdict(lambda: [0, 0.0])
                per_dep = defaultdict(lambda: [0, 0.0])
                for r in csv.DictReader(io.StringIO(raw)):
                    try:
                        lat, lon = float(r["latitude"]), float(r["longitude"])
                        f = float(r.get("frp") or 0)
                        t = int(r.get("type") or 0)
                    except (KeyError, ValueError):
                        continue
                    if t == 2 and prod == "viirs-snpp":
                        c = f"{round(lat, 2)},{round(lon, 2)}"
                        ys = static["cells"].setdefault(c, [])
                        if y not in ys:
                            ys.append(y)
                    if t != 0:
                        continue
                    n += 1
                    frp += f
                    months[int(r["acq_date"][5:7]) - 1] += 1
                    a = cells[f"{round(lat, 1)},{round(lon, 1)}"]
                    a[0] += 1
                    a[1] += f
                    if iso == "FR":
                        d = deps.find(lon, lat)
                        if d:
                            b = per_dep[d]
                            b[0] += 1
                            b[1] += f
                arch[key] = {"n": n, "frp": round(frp), "months": months,
                             "cells": {c: [v[0], round(v[1])] for c, v in cells.items()},
                             "deps": {d: [v[0], round(v[1])] for d, v in per_dep.items()}}
                log(f"  FIRMS archive {key} : {n} détections de type 0")
    jsave("firms_archive.json", dict(sorted(arch.items())))
    jsave("firms_static.json", static)


# ---------------------------------------------------------------- VigiEau
def fetch_vigieau(meta):
    try:
        deps = json.loads(get(VIGIEAU_API, timeout=120))
        snap = {"date": NOW.isoformat(timespec="minutes"), "deps": {}}
        for d in deps:
            snap["deps"][d["code"]] = {"nom": d.get("nom"), "max": d.get("niveauGraviteMax"),
                                       "sup": d.get("niveauGraviteSupMax"), "sou": d.get("niveauGraviteSouMax"),
                                       "aep": d.get("niveauGraviteAepMax")}
        jsave("vigieau_now.json", snap)
        log(f"  VigiEau : {len(snap['deps'])} départements")
    except Exception as e:  # noqa: BLE001
        log(f"  VigiEau API : échec ({e}), instantané précédent conservé")
    if not stale(meta, "vigieau_arretes", 20):
        return
    try:
        ds = json.loads(get(VIGIEAU_DATASET, timeout=60))
        url = next(r["url"] for r in ds["resources"] if r["title"].strip() == "Arrêtés")
        rows = csv.DictReader(io.StringIO(get(url, timeout=300).decode("utf-8", "replace")))
    except Exception as e:  # noqa: BLE001
        log(f"  VigiEau arrêtés : échec ({e})")
        return
    order = {"vigilance": 1, "alerte": 2, "alerte_renforcee": 3, "crise": 4}
    out = []
    for r in rows:
        lv = r.get("zones_alerte.niveau_gravite") or ""
        levels = re.findall(r"[a-z_]+", lv)
        lvl = max((order.get(x, 0) for x in levels), default=0)
        if not lvl or not r.get("date_debut") or not r.get("departement"):
            continue
        types = set(re.findall(r"SUP|SOU|AEP", r.get("zones_alerte.type") or ""))
        out.append([r["departement"], r["date_debut"], r.get("date_fin") or TODAY.isoformat(), lvl,
                    "".join(sorted(t[0:2] for t in types))])
    jsave("vigieau_arretes.json", {"cols": ["dep", "debut", "fin", "niveau", "types"], "rows": out})
    mark(meta, "vigieau_arretes")
    log(f"  VigiEau arrêtés : {len(out)}")


# ---------------------------------------------------------------- BDIFF
def bdiff_year(year):
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    q = urllib.parse.urlencode({"if[periodeAnnees][anneeDeb]": year, "if[periodeAnnees][anneeFin]": year})
    for attempt in range(2):
        try:
            opener.open(urllib.request.Request(f"{BDIFF}/incendies?{q}", headers={"User-Agent": UA}), timeout=180).read()
            raw = opener.open(urllib.request.Request(f"{BDIFF}/incendies/zip", headers={"User-Agent": UA}),
                              timeout=600).read()
            break
        except Exception as e:  # noqa: BLE001
            if "CERTIFICATE" not in str(e).upper() or attempt:
                raise
            # Le serveur BDIFF n'envoie pas sa chaîne de certificats complète : repli non vérifié,
            # limité à ce site de données publiques.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                                 urllib.request.HTTPSHandler(context=ctx))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read("Incendies.csv").decode("utf-8-sig", "replace")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    h = next(i for i, r in enumerate(rows) if r and r[0] == "Année")
    head, data = rows[h], rows[h + 1:]
    if data and data[0] and data[0][0] != str(year):
        raise ValueError(f"export BDIFF inattendu pour {year} (année {data[0][0]})")
    ix = {k: head.index(k) for k in head}

    def num(r, k):
        try:
            return float((r[ix[k]] or "0").replace(",", "."))
        except (KeyError, ValueError, IndexError):
            return 0.0
    per_dep = defaultdict(lambda: [0, 0.0, 0.0])
    big = []
    for r in data:
        if len(r) < len(head) - 2:
            continue
        dep = r[ix["Département"]].strip()
        s = num(r, "Surface parcourue (m2)") / 1e4
        f = num(r, "Surface forêt (m2)") / 1e4
        a = per_dep[dep]
        a[0] += 1
        a[1] += s
        a[2] += f
        if s >= 100:
            big.append([dep, r[ix["Nom de la commune"]], r[ix["Date de première alerte"]][:10], round(s)])
    return {"deps": {d: [v[0], round(v[1], 1), round(v[2], 1)] for d, v in per_dep.items()},
            "grands": sorted(big, key=lambda x: -x[3])[:60]}


def fetch_bdiff(meta):
    data = jload("bdiff.json", {})
    last_full = TODAY.year - 1
    todo = [y for y in range(2006, last_full + 1) if str(y) not in data]
    if stale(meta, "bdiff_refresh", 24 * 30):  # l'année écoulée est consolidée au fil des mois
        todo += [y for y in (last_full,) if y not in todo]
    for y in todo[:25]:
        try:
            data[str(y)] = bdiff_year(y)
            log(f"  BDIFF {y} : {sum(v[0] for v in data[str(y)]['deps'].values())} feux")
        except Exception as e:  # noqa: BLE001
            log(f"  BDIFF {y} : échec ({e})")
    if todo:
        mark(meta, "bdiff_refresh")
    jsave("bdiff.json", dict(sorted(data.items())))


# ---------------------------------------------------------------- Météo des forêts
def fetch_mdf(meta):
    data = jload("mdf.json", {})
    years = [TODAY.year] + [y for y in (TODAY.year - 1, TODAY.year - 2) if str(y) not in data.get("years", {})]
    if not stale(meta, "mdf", 6):
        years = [y for y in years if y != TODAY.year]
    data.setdefault("years", {})
    for y in years:
        try:
            raw = gzip.decompress(get(MDF.format(y=y), timeout=120)).decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            log(f"  Météo des forêts {y} : échec ({e})")
            continue
        days = defaultdict(dict)
        delim = ";" if raw.split("\n", 1)[0].count(";") > raw.split("\n", 1)[0].count(",") else ","
        for r in csv.DictReader(io.StringIO(raw), delimiter=delim):
            d = (r.get("date") or "")[:10]
            dep = (r.get("num_dep") or "").strip()
            dep = dep.zfill(2) if dep.isdigit() else dep
            try:
                days[d][dep] = [int(r["niveau_j1"]), int(r["niveau_j2"])]
            except (ValueError, KeyError, TypeError):
                continue
        data["years"][str(y)] = dict(sorted(days.items()))
        log(f"  Météo des forêts {y} : {len(days)} bulletins")
    if TODAY.year in years:
        mark(meta, "mdf")
    jsave("mdf.json", data)


# ---------------------------------------------------------------- Qualité de l'air
def atmo_day(day, layer="ind_atmo_2021"):
    flt = urllib.parse.quote(f"date_ech='{day}'")
    raw = get(ATMO_WFS.replace("ind_atmo_2021", layer) + flt, timeout=300).decode("utf-8", "replace")
    per = defaultdict(lambda: {"n": [0] * 7, "pol": defaultdict(int)})
    count = 0
    for r in csv.DictReader(io.StringIO(raw)):
        if r.get("type_zone") != "commune":
            continue
        z = r.get("code_zone") or ""
        dep = z[:3] if z.startswith("97") else z[:2]
        try:
            q = int(r["code_qual"])
        except (KeyError, ValueError):
            continue
        if not 0 <= q <= 6:
            continue
        a = per[dep]
        a["n"][q] += 1
        count += 1
        if q >= 3:  # polluant(s) responsable(s) du sous-indice le plus élevé
            subs = {p: int(r.get(f"code_{p}") or 0) for p in ("no2", "o3", "pm10", "pm25", "so2")}
            m = max(subs.values())
            for p, v in subs.items():
                if v == m:
                    a["pol"][p] += 1
    return {d: {"n": v["n"], "pol": dict(v["pol"])} for d, v in per.items()}, count


def fetch_atmo(meta):
    data = jload("atmo.json", {})
    days = [TODAY.isoformat(), (TODAY + dt.timedelta(days=1)).isoformat()]
    # rattrapage progressif de l'historique (365 jours disponibles), 20 jours par passage
    back = [(TODAY - dt.timedelta(days=i)).isoformat() for i in range(1, 366)]
    missing = [d for d in back if d not in data][:20]
    if not stale(meta, "atmo", 3):
        days = []
    for d in days + missing:
        try:
            agg, n = atmo_day(d, "ind_atmo_2021" if d in days else "ind_atmo")  # ind_atmo : historique 365 j
            if n:
                data[d] = agg
            log(f"  Atmo {d} : {n} communes")
        except Exception as e:  # noqa: BLE001
            log(f"  Atmo {d} : échec ({e})")
    if days:
        mark(meta, "atmo")
    cutoff = (TODAY - dt.timedelta(days=400)).isoformat()
    jsave("atmo.json", {k: v for k, v in sorted(data.items()) if k >= cutoff})


# ---------------------------------------------------------------- Forêts (IFN, GFW)
def fetch_ifn(meta):
    if not stale(meta, "ifn", 24 * 30) and os.path.exists(os.path.join(OUT, "ifn.json")):
        return
    labs = ",".join(f"'{x}'" for x in IFN_LABELS)
    flt = urllib.parse.quote(f"categorie_territoire='DPD4' AND label IN ({labs})")
    try:
        d = json.loads(get(IFN_WFS + flt, timeout=600))
    except Exception as e:  # noqa: BLE001
        log(f"  IFN : échec ({e})")
        return
    out = defaultdict(dict)

    def val(s):
        try:
            return float(str(s).replace(",", "."))
        except (TypeError, ValueError):
            return None
    for f in d["features"]:
        p = f["properties"]
        dep, lab, per = p["code_territoire"], p["label"], p["periode"]
        v = val(p.get("tot_stand"))
        if lab in ("ST", "ST_territoire", "tx_bois", "Vvif"):
            out[dep].setdefault(lab, {})[per] = [v, val(p.get("ic_stand")), bool(p.get("signif"))]
        elif lab == "Vmort":  # bois mort récent (≤ 5 ans) / ancien, par année de campagne
            age = "recent" if "5 ans ou moins" in (p.get("libelle_1") or "") else "ancien"
            pct = re.search(r"(\d+(?:[.,]\d+)?) ?%", p.get("tooltip") or "")
            out[dep].setdefault("Vmort", {}).setdefault(per, {})[age] = [v, bool(p.get("signif"))]
        elif lab == "DEPERIS_ESS":  # part des tiges dépérissantes par essence
            pct = re.search(r"(\d+(?:[.,]\d+)?) ?%", p.get("tooltip") or "")
            out[dep].setdefault("deperis", {}).setdefault(per, {})[p.get("libelle_1") or "?"] = [
                v, val(pct.group(1)) if pct else None, bool(p.get("signif"))]
        out[dep]["nom"] = p.get("territoire")
    jsave("ifn.json", dict(sorted(out.items())))
    mark(meta, "ifn")
    log(f"  IFN : {len(out)} départements")


def xlsx_rows(raw, sheet_name):
    z = zipfile.ZipFile(io.BytesIO(raw))
    wb = z.read("xl/workbook.xml").decode()
    rels = z.read("xl/_rels/workbook.xml.rels").decode()
    sheets = re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wb)
    rid = dict(sheets)[sheet_name]
    target = re.search(rf'Id="{rid}"[^>]*Target="([^"]+)"', rels) or re.search(rf'Target="([^"]+)"[^>]*Id="{rid}"', rels)
    path = "xl/" + target.group(1).lstrip("/").replace("xl/", "")
    strs = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = z.read("xl/sharedStrings.xml").decode()
        strs = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<si>(.*?)</si>", ss, re.S)]
    x = z.read(path).decode()
    for row in re.findall(r"<row[^>]*>(.*?)</row>", x, re.S):
        cells = []
        for attrs, body in re.findall(r"<c ([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
            v = re.search(r"<v>(.*?)</v>", body or "")
            v = v.group(1) if v else ""
            if 't="s"' in attrs and v:
                v = strs[int(v)]
            col = re.search(r'r="([A-Z]+)\d+"', attrs).group(1)
            idx = 0
            for ch in col:
                idx = idx * 26 + ord(ch) - 64
            while len(cells) < idx - 1:
                cells.append("")
            cells.append(v)
        yield cells


def fetch_gfw(meta):
    if not stale(meta, "gfw", 24 * 30) and os.path.exists(os.path.join(OUT, "gfw.json")):
        return
    try:
        raw = get(GFW, timeout=180)
        rows = list(xlsx_rows(raw, "Subnational 2 tree cover loss"))
    except Exception as e:  # noqa: BLE001
        log(f"  Global Forest Watch : échec ({e})")
        return
    head = rows[0]
    names = []  # (région, département, couvert 2000 en ha) au seuil de 30 %
    for r in rows[1:]:
        if len(r) >= len(head) and r[head.index("threshold")] == "30":
            names.append((r[head.index("subnational1")], r[head.index("subnational2")],
                          float(r[head.index("extent_2000_ha")] or 0)))
    # Pertes annuelles 2001→ (classeur arrêté à 2020) : API de données GFW, identifiants GADM
    api = "https://data-api.globalforestwatch.org/dataset/{ds}/latest/download/csv?sql="
    q_ext = ("select adm1, adm2, sum(umd_tree_cover_extent_2000__ha) as ext from data where iso='FRA' "
             "and umd_tree_cover_density_2000__threshold=30 group by adm1, adm2")
    q_loss = ("select adm1, adm2, umd_tree_cover_loss__year as y, sum(umd_tree_cover_loss__ha) as loss, "
              "sum(umd_tree_cover_loss_from_fires__ha) as fire from data where iso='FRA' "
              "and umd_tree_cover_density_2000__threshold=30 group by adm1, adm2, umd_tree_cover_loss__year")
    ext = list(csv.DictReader(io.StringIO(get(api.format(ds="gadm__tcl__adm2_summary") + urllib.parse.quote(q_ext),
                                              timeout=300).decode())))
    loss = list(csv.DictReader(io.StringIO(get(api.format(ds="gadm__tcl__adm2_change") + urllib.parse.quote(q_loss),
                                               timeout=300).decode())))
    # Les identifiants GADM sont reliés aux noms par le couvert 2000, identique dans les deux sources
    ids = {}
    for e in ext:
        x = float(e["ext"])
        reg, dep, ex = min(names, key=lambda n: abs(n[2] - x))
        if abs(ex - x) <= max(2, 0.001 * x):
            ids[(e["adm1"], e["adm2"])] = (reg, dep, round(ex))
    out = {}
    for r in loss:
        k = ids.get((r["adm1"], r["adm2"]))
        if not k:
            continue
        o = out.setdefault(k[1], {"region": k[0], "extent2000": k[2], "loss": {}, "fire": {}})
        o["loss"][r["y"]] = round(float(r["loss"]))
        o["fire"][r["y"]] = round(float(r["fire"] or 0))
    jsave("gfw.json", out)
    mark(meta, "gfw")
    yrs = sorted({y for v in out.values() for y in v["loss"]})
    log(f"  Global Forest Watch : {len(out)}/{len(ext)} départements reliés, années {yrs[0]}–{yrs[-1]}")


def fetch_deps(meta):
    p = os.path.join(OUT, "departements.json")
    if os.path.exists(p):
        return
    g = json.loads(get(DEPS, timeout=120))

    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], list) else [round(c[0], 3), round(c[1], 3)]
    feats = [{"type": "Feature", "properties": f["properties"],
              "geometry": {"type": f["geometry"]["type"], "coordinates": rnd(f["geometry"]["coordinates"])}}
             for f in g["features"]]
    jsave("departements.json", {"type": "FeatureCollection", "features": feats})
    log(f"  Départements : {len(feats)}")


def main():
    os.makedirs(OUT, exist_ok=True)
    meta = jload("meta.json", {})
    for name, fn in [("départements", fetch_deps), ("FIRMS", fetch_firms), ("FIRMS archives", fetch_firms_archive), ("VigiEau", fetch_vigieau),
                     ("Météo des forêts", fetch_mdf), ("Atmo", fetch_atmo), ("BDIFF", fetch_bdiff),
                     ("IFN", fetch_ifn), ("GFW", fetch_gfw)]:
        log(name)
        try:
            fn(meta)
        except Exception as e:  # noqa: BLE001 — une source en panne ne bloque pas les autres
            log(f"  {name} : erreur {e}")
        jsave("meta.json", meta, indent=1)


if __name__ == "__main__":
    main()
