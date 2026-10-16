"""Hub'Eau (hubeau.eaufrance.fr, sans clé) : rivières des centrales nucléaires et nappes phréatiques.

Données du Système d'information sur l'eau (Eaufrance : « sauf mention contraire, contenus sous licence Etalab 2.0 »).
- Température des cours d'eau (api/v1/temperature) et débits journaliers (api/v2/hydrometrie, QmnJ) aux stations
  les plus proches de chaque centrale nucléaire de bord de rivière, sur le même cours d'eau ;
  température maximale de l'air au site via Open-Meteo (archive ERA5, CC BY 4.0), faute de thermie récente partout ;
- Piézométrie (api/v1/niveaux_nappes) : échantillon de piézomètres à longue chronique, un par département,
  agrégé en moyennes mensuelles.
Stockage : data/raw/hubeau/{rivers,stations,nappes}.json, fusionnés à chaque passage (quotidien).

    python3 scripts/fetch_hubeau.py data/raw [rivers|nappes]
"""
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(RAW, "hubeau")
API = "https://hubeau.eaufrance.fr/api/"
UA = "energie-odre-viz/1.0 (projet personnel)"
TODAY = dt.date.today()
TEMP_SINCE = dt.date(2019, 1, 1)
FLOW_SINCE = dt.date(2012, 1, 1)

# Centrales nucléaires de bord de rivière (ou d'estuaire) et le cours d'eau à suivre (mot-clé du libellé Hub'Eau)
PLANTS = [("BUGEY", "Rhône"), ("SAINT ALBAN", "Rhône"), ("CRUAS", "Rhône"), ("TRICASTIN", "Rhône"), ("GOLFECH", "Garonne"),
          ("BLAYAIS", "Gironde|Garonne|Dordogne"), ("CHINON", "Loire"), ("SAINT LAURENT", "Loire"), ("DAMPIERRE", "Loire"),
          ("BELLEVILLE", "Loire"), ("NOGENT", "Seine"), ("CATTENOM", "Moselle"), ("CHOOZ", "Meuse"), ("CIVAUX", "Vienne")]
JRC_NAMES = {"SAINT ALBAN": "ST ALBAN", "SAINT LAURENT": "ST LAURENT"}


def get(path, params, retries=4):
    url = API + path + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(10 * (attempt + 1)); continue
            raise
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5)


def pages(path, params, max_pages=20):
    """Itère sur les pages Hub'Eau (champ next)."""
    data = get(path, params)
    yield from data.get("data", [])
    n = 1
    while data.get("next") and n < max_pages:
        time.sleep(0.3)
        with urllib.request.urlopen(urllib.request.Request(data["next"], headers={"User-Agent": UA}), timeout=90) as r:
            data = json.load(r)
        yield from data.get("data", [])
        n += 1


def load(name, default):
    try:
        with open(os.path.join(OUT, name), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save(name, data):
    tmp = os.path.join(OUT, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True, indent=0)
    os.replace(tmp, os.path.join(OUT, name))


def km(lat1, lon1, lat2, lon2):
    dy = (lat1 - lat2) * 111.2
    dx = (lon1 - lon2) * 111.2 * math.cos(math.radians(lat1))
    return (dx * dx + dy * dy) ** .5


def plant_coords():
    rows = list(csv.DictReader(open(os.path.join(RAW, "rte", "jrc_france_units.csv"), encoding="utf-8")))
    out = {}
    for name, _ in PLANTS:
        key = JRC_NAMES.get(name, name)
        pts = [(float(r["lat"]), float(r["lon"])) for r in rows if r["type_g"] == "Nuclear" and r["name_g"].upper().startswith(key)]
        if pts:
            out[name] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    return out


def matches(label, rivers, before_a=False):
    """Le cours d'eau doit figurer dans le libellé ; pour les stations hydrométriques (« La Loire à Gien »),
    seulement avant « à », sinon « Meung-sur-Loire » ou « Valdivienne » donneraient de faux positifs."""
    label = (label or "").lower()
    if before_a:
        label = label.split(" à ")[0]
    return any(r.lower() in label.split() or f" {r.lower()}" in f" {label}" for r in rivers.split("|"))


def last_temp(code):
    d = get("v1/temperature/chronique", {"code_station": code, "size": 1, "sort": "desc", "fields": "date_mesure_temp"})
    return d["data"][0]["date_mesure_temp"] if d.get("data") else None


def flow_since(site):
    d = get("v2/hydrometrie/obs_elab", {"code_entite": site, "grandeur_hydro_elab": "QmnJ", "date_debut_obs_elab": FLOW_SINCE.isoformat(),
                                        "size": 1, "fields": "date_obs_elab"})
    return d["data"][0]["date_obs_elab"] if d.get("data") else None


def discover(coords):
    """Stations retenues par centrale, sur le même cours d'eau (une fois, puis mémorisées) :
    - température : les deux plus proches (≤ 80 km) ayant des mesures depuis 2019 — le réseau public de thermie
      est clairsemé et beaucoup de stations se sont arrêtées vers 2010-2016 ;
    - débit : le site hydrométrique le plus proche dont la chronique QmnJ couvre 2015, sinon le plus long."""
    st = load("stations.json", {})
    for name, river in PLANTS:
        if name in st or name not in coords:
            continue
        lat, lon = coords[name]
        wide = f"{lon - 1.0},{lat - .7},{lon + 1.0},{lat + .7}"
        temp = [s for s in pages("v1/temperature/station", {"bbox": wide, "size": 300, "format": "json"}, 3) if matches(s.get("libelle_cours_eau"), river)]
        temp.sort(key=lambda s: km(lat, lon, s["latitude"], s["longitude"]))
        tsel = []
        for s in temp[:12]:
            d = km(lat, lon, s["latitude"], s["longitude"])
            if d > 80 or len(tsel) >= 2:
                break
            last = last_temp(s["code_station"]); time.sleep(.3)
            if last and last >= TEMP_SINCE.isoformat():
                tsel.append({"code": s["code_station"], "name": s["libelle_station"].strip(), "lat": s["latitude"], "lon": s["longitude"], "km": round(d, 1), "last": last})
        bbox = f"{lon - .6},{lat - .4},{lon + .6},{lat + .4}"
        hydro = [s for s in pages("v2/hydrometrie/referentiel/stations", {"bbox": bbox, "size": 200, "format": "json", "en_service": "true"}, 3)
                 if matches(s.get("libelle_station") or s.get("libelle_site"), river, before_a=True)]
        hydro.sort(key=lambda s: km(lat, lon, s["latitude_station"], s["longitude_station"]))
        sites, fsel = [], None
        for s in hydro:
            if s["code_site"] in [x["code"] for x in sites] or len(sites) >= 5:
                continue
            first = flow_since(s["code_site"]); time.sleep(.3)
            if first:
                sites.append({"code": s["code_site"], "name": (s.get("libelle_site") or s["libelle_station"]).strip(), "lat": s["latitude_station"],
                              "lon": s["longitude_station"], "km": round(km(lat, lon, s["latitude_station"], s["longitude_station"]), 1), "first": first})
        fsel = next((x for x in sites if x["first"] <= "2015-12-31"), None) or (min(sites, key=lambda x: x["first"]) if sites else None)
        st[name] = {"lat": lat, "lon": lon, "river": river.split("|")[0], "temp": tsel, "flow": [fsel] if fsel else []}
        print(f"  {name} : température {[t['name'] for t in tsel]}, débit {fsel['name'] if fsel else '—'}")
        time.sleep(0.5)
    save("stations.json", st)
    return st


def fetch_rivers(st):
    """Température de l'eau journalière (moyenne, max), débit moyen journalier (QmnJ) et, pour situer la chaleur,
    température maximale de l'air au site (Open-Meteo, réanalyse ERA5, CC BY 4.0). Fusionnés à chaque passage."""
    rv = load("rivers.json", {"temp": {}, "flow": {}, "air": {}})
    rv.setdefault("air", {})
    for name, s in st.items():
        for t in s["temp"]:
            ser = rv["temp"].setdefault(t["code"], {})
            start = (dt.date.fromisoformat(max(ser)) - dt.timedelta(days=3)) if ser else TEMP_SINCE
            if ser and start < TODAY - dt.timedelta(days=400) and TODAY.weekday() != 0:
                continue  # station muette depuis plus d'un an : revérifiée seulement le lundi
            # l'API plafonne à 20 000 lignes par requête (mesures horaires) : une requête par semestre
            while start <= TODAY:
                end = min(TODAY, dt.date(start.year, 6, 30) if start.month <= 6 else dt.date(start.year, 12, 31))
                acc = defaultdict(list)
                try:
                    for m in pages("v1/temperature/chronique", {"code_station": t["code"], "date_debut_mesure": start.isoformat(), "date_fin_mesure": end.isoformat(),
                                                               "size": 20000, "fields": "date_mesure_temp,resultat", "sort": "asc"}, 1):
                        if m.get("resultat") is not None and -1 < float(m["resultat"]) < 40:
                            acc[m["date_mesure_temp"][:10]].append(float(m["resultat"]))
                except Exception as e:
                    print(f"  température {t['code']} {start} : échec ({str(e)[:80]})")
                for d, v in acc.items():
                    ser[d] = [round(sum(v) / len(v), 2), round(max(v), 2)]
                start = end + dt.timedelta(days=1)
                time.sleep(0.3)
        for f in s["flow"]:
            ser = rv["flow"].setdefault(f["code"], {})
            since = (dt.date.fromisoformat(max(ser)) - dt.timedelta(days=10)) if ser else FLOW_SINCE
            try:
                for m in pages("v2/hydrometrie/obs_elab", {"code_entite": f["code"], "grandeur_hydro_elab": "QmnJ", "date_debut_obs_elab": since.isoformat(),
                                                          "size": 20000, "fields": "date_obs_elab,resultat_obs_elab"}, 3):
                    if m.get("resultat_obs_elab") is not None:
                        ser[m["date_obs_elab"][:10]] = round(m["resultat_obs_elab"] / 1000, 1)  # l/s → m³/s
            except Exception as e:
                print(f"  débit {f['code']} : échec ({str(e)[:80]})")
            time.sleep(0.3)
        ser = rv["air"].setdefault(name, {})
        since = (dt.date.fromisoformat(max(ser)) - dt.timedelta(days=10)) if ser else TEMP_SINCE
        try:
            url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode({
                "latitude": round(s["lat"], 3), "longitude": round(s["lon"], 3), "start_date": since.isoformat(), "end_date": TODAY.isoformat(),
                "daily": "temperature_2m_max", "timezone": "Europe/Paris"})
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
                dd = json.load(r)["daily"]
            for d, v in zip(dd["time"], dd["temperature_2m_max"]):
                if v is not None:
                    ser[d] = v
        except Exception as e:
            print(f"  air {name} : échec ({str(e)[:80]})")
        time.sleep(0.5)
    save("rivers.json", rv)
    return sum(len(v) for v in rv["temp"].values()), sum(len(v) for v in rv["flow"].values())


def fetch_nappes():
    """Échantillon : par département, le piézomètre actif à la plus longue chronique ; moyennes mensuelles (m NGF)."""
    np_ = load("nappes.json", {"stations": {}, "monthly": {}})
    if not np_["stations"]:
        recent = (TODAY - dt.timedelta(days=120)).isoformat()
        for dep in [f"{i:02d}" for i in range(1, 96) if i != 20] + ["2A", "2B"]:
            try:
                cands = [s for s in pages("v1/niveaux_nappes/stations", {"code_departement": dep, "size": 1000, "format": "json",
                         "fields": "code_bss,nom_commune,x,y,date_debut_mesure,date_fin_mesure,nb_mesures_piezo,libelle_pe,code_departement,altitude_station"}, 3)
                         if (s.get("date_fin_mesure") or "") >= recent and (s.get("date_debut_mesure") or "9999") <= "2000-01-01"]
            except Exception as e:
                print(f"  piézomètres {dep} : échec ({str(e)[:60]})"); continue
            if cands:
                best = max(cands, key=lambda s: s.get("nb_mesures_piezo") or 0)
                np_["stations"][best["code_bss"]] = {"dep": dep, "commune": best.get("nom_commune"), "lat": best.get("y"), "lon": best.get("x"),
                                                     "since": best.get("date_debut_mesure"), "nappe": (best.get("libelle_pe") or "")[:90]}
            time.sleep(0.2)
    for code in np_["stations"]:
        ser = np_["monthly"].setdefault(code, {})
        since = (max(ser) + "-01") if ser else "1970-01-01"
        acc = defaultdict(list)
        try:
            for m in pages("v1/niveaux_nappes/chroniques", {"code_bss": code, "date_debut_mesure": since, "size": 20000,
                                                           "fields": "date_mesure,niveau_nappe_eau", "sort": "asc"}, 5):
                if m.get("niveau_nappe_eau") is not None:
                    acc[m["date_mesure"][:7]].append(float(m["niveau_nappe_eau"]))
        except Exception as e:
            print(f"  chronique {code} : échec ({str(e)[:60]})")
        for mth, v in acc.items():
            ser[mth] = round(sum(v) / len(v), 2)
        time.sleep(0.2)
    save("nappes.json", np_)
    return len(np_["stations"]), sum(len(v) for v in np_["monthly"].values())


def main():
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[2] if len(sys.argv) > 2 else None  # « rivers » ou « nappes » pour ne faire qu'une partie
    try:
        if only == "nappes":
            raise StopIteration
        st = discover(plant_coords())
        nt, nf = fetch_rivers(st)
        print(f"Hub'Eau rivières : {len(st)} centrales, {nt} jours de température, {nf} jours de débit")
    except StopIteration:
        pass
    except Exception as e:
        print(f"Hub'Eau rivières : échec ({str(e)[:160]}) — données versionnées conservées.")
    try:
        if only == "rivers":
            return 0
        ns, nm = fetch_nappes()
        print(f"Hub'Eau nappes : {ns} piézomètres, {nm} mois")
    except Exception as e:
        print(f"Hub'Eau nappes : échec ({str(e)[:160]}) — données versionnées conservées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
