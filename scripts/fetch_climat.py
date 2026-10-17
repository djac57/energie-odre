"""Données de la page Climat : séries longues, sources ouvertes sans clé.

    python3 scripts/fetch_climat.py data/raw

- Météo-France, données climatologiques de base mensuelles (meteo.data.gouv.fr / data.gouv.fr,
  Licence Ouverte 2.0) : ~40 stations de référence à longue série, une par département choisi.
  Les fichiers « avant-1949 » et « 1950-2024 » ne sont lus qu'une fois ; le fichier « 2025-2026 »
  (petit) est relu à chaque passage.
- PSMSL, niveau moyen annuel de la mer (RLR) à Brest (depuis 1807) et Marseille (depuis 1849),
  données libres avec citation (Holgate et al., 2013).
- WGMS, Fluctuations of Glaciers (FoG), bilans de masse annuels des glaciers français,
  « open access under the requirement of correct citation ».
- Our World in Data, émissions de CO2 et de gaz à effet de serre de la France (CC BY 4.0).

- Météo-France, données quotidiennes (même licence) des stations de plaine : température moyenne
  journalière (TNTXM) depuis 1947, pour l'indicateur de vagues de chaleur et les degrés-jours.
  L'historique (≤ 2024) est figé dans daily_hist.json.gz, téléchargé une seule fois (~500 Mo lus) ;
  seul daily_latest.json (2025 →) est rafraîchi.
- NOAA GML, CO2 mensuel de Mauna Loa (domaine public, citation demandée).
- NOAA NCEI, NOAAGlobalTemp (domaine public), anomalie mondiale de température.
- NOAA NCEI, ERSST v5 (domaine public), température de surface de la mer, Méditerranée nord-occidentale.
- Géorisques, base GASPAR, arrêtés de catastrophe naturelle (Licence Ouverte 2.0), agrégés.
- Insee, indicateurs de développement durable (API Melodi, sans clé, Licence Ouverte) : abondance des oiseaux
  communs (indicateur 15.i6, MNHN-CESCO / PatriNat, programme STOC-EPS de Vigie-Nature).
- Cerema, consommation d'espaces naturels, agricoles et forestiers (data.gouv.fr, Licence Ouverte 2.0), totaux annuels.

Sortie : data/raw/climat/{stations,sealevel,glaciers,emissions,daily_latest,world,sst,catnat,meta}.json
et daily_hist.json.gz (compacts, versionnés).
"""
import csv
import datetime as dt
import gzip
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
import zipfile

RAW = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
OUT = os.path.join(RAW, "climat")
UA = "Mozilla/5.0 (compatible; energie-odre-viz/1.0)"
MF = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/MENS/MENSQ_{dep}_{period}.csv.gz"
PSMSL = "https://psmsl.org/data/obtaining/rlr.annual.data/{id}.rlrdata"
WGMS_VERSIONS = "https://wgms.ch/data_databaseversions/"
OWID = "https://nyc3.digitaloceanspaces.com/owid-public/data/co2/owid-co2-data.csv"
NOW = dt.datetime.now(dt.timezone.utc)

# Station de référence par département : la série TX/TN la plus longue encore active en 2025-2026
# (choisie en lisant les fichiers mensuels Météo-France, octobre 2026).
STATIONS = [
    ("03", "03060001"), ("05", "05079001"), ("06", "06088001"), ("13", "13055001"), ("14", "14137001"),
    ("15", "15119001"), ("17", "17415003"), ("18", "18092001"), ("20", "20041001"), ("21", "21473001"),
    ("25", "25056001"), ("26", "26198001"), ("29", "29082001"), ("30", "30339001"), ("31", "31157001"),
    ("33", "33504001"), ("34", "34154001"), ("35", "35281001"), ("37", "37179001"), ("38", "38242001"),
    ("44", "44020001"), ("45", "45069001"), ("47", "47091001"), ("51", "51545001"), ("54", "54526001"),
    ("57", "57039001"), ("59", "59183001"), ("63", "63113001"), ("64", "64549001"), ("65", "65059001"),
    ("66", "66136001"), ("67", "67482001"), ("69", "69204002"), ("73", "73261001"), ("75", "75114001"),
    ("76", "76552001"), ("80", "80001001"), ("86", "86027001"), ("87", "87113001"),
]
# Stations de montagne exclues des séries quotidiennes (indicateur de plaine)
MOUNTAIN = {"05079001", "30339001", "38242001", "65059001", "73261001"}
DAILY_START = dt.date(1947, 1, 1)
HIST_END = dt.date(2024, 12, 31)
MFQ = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/QUOT/Q_{dep}_{period}_RR-T-Vent.csv.gz"
CO2 = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.csv"
NOAA_GLOBAL = ("https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/global/time-series/globe/tavg/land_ocean/1/0/1850-"
               + str(NOW.year) + "/data.csv")
ERSST = "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/ascii/ersst.v5.{y}.asc"
SST_BOX = [(lat, lon) for lat in (42, 44) for lon in (4, 6, 8)]  # cellules de 2° : golfe du Lion, mer Ligure
GASPAR = "https://files.georisques.fr/GASPAR/gaspar.zip"
MELODI = "https://api.insee.fr/melodi/data/DS_NAT_SDG?INDICATOR_SDG=15.i6a&INDICATOR_SDG=15.i6b&maxResult=1000"
ENAF_DATASET = "https://www.data.gouv.fr/api/2/datasets/search/?q=consommation+espaces+naturels+agricoles+forestiers&organization=cerema&page_size=20"
# Variables mensuelles retenues : TX, TN, TM moyens (°C), RR (mm), jours TX≥30, TX≥35, TN≥20, gelées
FIELDS = ["TX", "TN", "TM", "RR", "NBJTX30", "NBJTX35", "NBJTNS20", "NBJGELEE"]


def log(*a):
    print(*a, flush=True)


def get(url, timeout=180, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if i == retries - 1:
                raise
            log(f"  nouvel essai {url} ({e})")
            time.sleep(5 * (i + 1))


def jload(name, default):
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def jsave(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))


def stale(meta, key, hours):
    t = meta.get(key)
    return not t or (NOW - dt.datetime.fromisoformat(t)).total_seconds() > hours * 3600


def mark(meta, key):
    meta[key] = NOW.isoformat(timespec="seconds")


def num(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


# ------------------------------------------------------------------ Météo-France
def read_mf(dep, period, wanted):
    """Lignes mensuelles des stations voulues : {poste: {"meta": …, "m": {AAAAMM: [valeurs]}}}"""
    raw = get(MF.format(dep=dep, period=period), timeout=300)
    out = {}
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            k = r.get("NUM_POSTE")
            if k not in wanted:
                continue
            o = out.setdefault(k, {"meta": [r["NOM_USUEL"], round(float(r["LAT"]), 4), round(float(r["LON"]), 4), int(float(r["ALTI"] or 0))], "m": {}})
            tm = num(r.get("TM")) if num(r.get("TM")) is not None else num(r.get("TMM"))
            vals = [num(r.get("TX")), num(r.get("TN")), tm, num(r.get("RR")),
                    num(r.get("NBJTX30")), num(r.get("NBJTX35")), num(r.get("NBJTNS20")), num(r.get("NBJGELEE"))]
            if any(v is not None for v in vals):
                out[k]["m"][r["AAAAMM"]] = vals
    return out


def fetch_stations(meta):
    st = jload("stations.json", {"fields": FIELDS, "stations": {}})
    by_dep = {}
    for dep, poste in STATIONS:
        by_dep.setdefault(dep, set()).add(poste)
    for dep, postes in by_dep.items():
        periods = ["latest-2025-2026"]
        if any(p not in st["stations"] or not st["stations"][p].get("hist") for p in postes):
            periods = ["avant-1949", "previous-1950-2024", "latest-2025-2026"]
        for period in periods:
            try:
                rows = read_mf(dep, period, postes)
            except Exception as e:  # noqa: BLE001
                log(f"  Météo-France {dep} {period} : échec ({e})")
                continue
            for poste, o in rows.items():
                s = st["stations"].setdefault(poste, {"dep": dep, "meta": o["meta"], "m": {}})
                s["meta"] = o["meta"]
                s["m"].update(o["m"])
                if period != "latest-2025-2026":
                    s["hist"] = True
        log(f"  {dep} : {', '.join(periods)}")
    # tri chronologique pour des diffs lisibles
    for s in st["stations"].values():
        s["m"] = dict(sorted(s["m"].items()))
    jsave("stations.json", st)
    mark(meta, "stations")


# ------------------------------------------------------------------ PSMSL
def fetch_sealevel(meta):
    out = {}
    for name, pid in [("Brest", 1), ("Marseille", 61)]:
        raw = get(PSMSL.format(id=pid)).decode("utf-8", "replace")
        ser = {}
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split(";")]
            if len(parts) >= 2 and parts[0].isdigit():
                v = int(parts[1])
                if v != -99999:
                    ser[parts[0]] = v  # mm, référentiel RLR (relatif)
        out[name] = ser
    jsave("sealevel.json", out)
    mark(meta, "sealevel")


# ------------------------------------------------------------------ WGMS
def fetch_glaciers(meta):
    page = get(WGMS_VERSIONS).decode("utf-8", "replace")
    links = re.findall(r'href="(https://wgms\.ch/downloads/DOI-WGMS-FoG-[0-9a-z-]+\.zip)"', page)
    if not links:
        raise RuntimeError("lien FoG introuvable")
    url = links[0]
    if jload("glaciers.json", {}).get("source") == url:
        log("  WGMS : version déjà à jour")
        return mark(meta, "glaciers")
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "fog.zip")
        with open(p, "wb") as fh:
            fh.write(get(url, timeout=600))
        z = zipfile.ZipFile(p)
        glaciers = {r["id"]: r["short_name"] for r in csv.DictReader(io.TextIOWrapper(z.open("data/glacier.csv"), encoding="utf-8"))
                    if r.get("country") == "FR"}
        ser = {}
        for r in csv.DictReader(io.TextIOWrapper(z.open("data/mass_balance.csv"), encoding="utf-8")):
            g = glaciers.get(r.get("glacier_id"))
            v = num(r.get("annual_balance"))
            if g and v is not None:
                ser.setdefault(g, {})[r["year"]] = round(v, 3)  # m d'équivalent eau par an
        cit = json.loads(z.read("datapackage.json")).get("citation", "")
    ser = {k: dict(sorted(v.items())) for k, v in ser.items() if len(v) >= 10}
    jsave("glaciers.json", {"source": url, "citation": cit, "series": ser})
    mark(meta, "glaciers")


# ------------------------------------------------------------------ OWID
def fetch_emissions(meta):
    raw = get(OWID, timeout=300).decode("utf-8", "replace")
    keep = ["year", "co2", "total_ghg", "total_ghg_excluding_lucf", "consumption_co2", "co2_per_capita", "ghg_per_capita", "population"]
    rows = []
    for r in csv.DictReader(io.StringIO(raw)):
        if r.get("country") == "France":
            rows.append([int(r["year"])] + [num(r.get(k)) for k in keep[1:]])
    jsave("emissions.json", {"cols": keep, "rows": rows})
    mark(meta, "emissions")


# ------------------------------------------------------------------ Météo-France quotidien
def read_mfq(dep, period, wanted, start):
    """{poste: {jour (index depuis start): TNTXM en dixièmes de °C}}"""
    raw = get(MFQ.format(dep=dep, period=period), timeout=600)
    out = {}
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            k = r.get("NUM_POSTE")
            if k not in wanted:
                continue
            v = num(r.get("TNTXM"))
            if v is None and num(r.get("TN")) is not None and num(r.get("TX")) is not None:
                v = (num(r["TN"]) + num(r["TX"])) / 2
            if v is None:
                continue
            d = r["AAAAMMJJ"]
            i = (dt.date(int(d[:4]), int(d[4:6]), int(d[6:])) - start).days
            if i >= 0:
                out.setdefault(k, {})[i] = round(v * 10)
    return out


def pack(days, n):
    arr = [None] * n
    for i, v in days.items():
        if i < n:
            arr[i] = v
    return arr


def fetch_daily(meta):
    wanted = [(dep, p) for dep, p in STATIONS if p not in MOUNTAIN]
    hist_path = os.path.join(OUT, "daily_hist.json.gz")
    if not os.path.exists(hist_path):
        n = (HIST_END - DAILY_START).days + 1
        st = {}
        for dep, p in wanted:
            days = {}
            for period in ("avant-1949", "previous-1950-2024"):
                try:
                    days.update(read_mfq(dep, period, {p}, DAILY_START).get(p, {}))
                except Exception as e:  # noqa: BLE001
                    log(f"  quotidien {dep} {period} : échec ({e})")
            if days:
                st[p] = pack(days, n)
            log(f"  quotidien {dep} : {len(days)} jours")
        with gzip.open(hist_path, "wt", encoding="utf-8") as fh:
            json.dump({"start": DAILY_START.isoformat(), "end": HIST_END.isoformat(), "st": st}, fh, separators=(",", ":"))
    start = HIST_END + dt.timedelta(days=1)
    n = (NOW.date() - start).days + 1
    st = {}
    for dep, p in wanted:
        try:
            days = read_mfq(dep, "latest-2025-2026", {p}, start).get(p, {})
        except Exception as e:  # noqa: BLE001
            log(f"  quotidien récent {dep} : échec ({e})")
            continue
        if days:
            st[p] = pack(days, n)
    if st:
        jsave("daily_latest.json", {"start": start.isoformat(), "st": st})
    mark(meta, "daily")


# ------------------------------------------------------------------ Monde : CO2 et température
def fetch_world(meta):
    out = jload("world.json", {})
    try:
        co2 = []
        for line in get(CO2).decode("utf-8", "replace").splitlines():
            p = line.split(",")
            if len(p) >= 4 and p[0].isdigit():
                v = num(p[3])
                if v is not None and v > 0:
                    co2.append([int(p[0]), int(p[1]), v])
        if co2:
            out["co2"] = co2  # [année, mois, ppm (moyenne mensuelle)]
    except Exception as e:  # noqa: BLE001
        log(f"  CO2 Mauna Loa : échec ({e})")
    try:  # NOAAGlobalTemp (NOAA NCEI, domaine public), anomalies mensuelles, référence 1901-2000
        glb = {}
        for line in get(NOAA_GLOBAL).decode("utf-8", "replace").splitlines():
            p = line.split(",")
            if len(p) == 2 and p[0].isdigit() and len(p[0]) == 6:
                glb[p[0]] = num(p[1])
        if glb:
            out["global"] = {"source": "NOAAGlobalTemp v6 (NOAA NCEI)", "base": "1901-2000", "monthly": glb}
    except Exception as e:  # noqa: BLE001
        log(f"  NOAAGlobalTemp : échec ({e})")
    jsave("world.json", out)
    mark(meta, "world")


# ------------------------------------------------------------------ Méditerranée : ERSST v5
def fetch_sst(meta):
    sst = jload("sst.json", {"box": SST_BOX, "monthly": {}})
    have = {k[:4] for k in sst["monthly"]}
    years = [y for y in range(1900, NOW.year + 1) if str(y) not in have or y >= NOW.year - 1]
    for y in years:
        try:
            lines = get(ERSST.format(y=y), timeout=300).decode("ascii", "replace").split("\n")
        except Exception as e:  # noqa: BLE001
            log(f"  ERSST {y} : échec ({e})")
            continue
        for m in range(len(lines) // 180):
            vals = []
            for lat, lon in SST_BOX:
                row = lines[m * 180 + lon // 2].split()
                v = int(row[(lat + 88) // 2])
                if v > -900:
                    vals.append(v / 100)
            if len(vals) == len(SST_BOX):
                sst["monthly"][f"{y}-{m + 1:02d}"] = round(sum(vals) / len(vals), 2)
    sst["monthly"] = dict(sorted(sst["monthly"].items()))
    jsave("sst.json", sst)
    mark(meta, "sst")


# ------------------------------------------------------------------ CatNat (GASPAR)
def fetch_catnat(meta):
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "gaspar.zip")
        with open(p, "wb") as fh:
            fh.write(get(GASPAR, timeout=600))
        z = zipfile.ZipFile(p)
        name = next(n for n in z.namelist() if n.startswith("catnat_"))
        by_year, by_dep, labels = {}, {}, {}
        seen = set()
        for r in csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace"), delimiter=";"):
            key = (r["id_gaspar"], r["code_commune"], r["num_risque_jo"])
            if key in seen:
                continue
            seen.add(key)
            y, t = r["date_debut"][:4], r["num_risque_jo"]
            if not y.isdigit():
                continue
            labels[t] = r["lib_risque_jo"]
            c = r["code_commune"]
            dep = c[:3] if c.startswith("97") else c[:2]
            by_year.setdefault(y, {}).setdefault(t, 0)
            by_year[y][t] += 1
            by_dep.setdefault(dep, {}).setdefault(t, {}).setdefault(y, 0)
            by_dep[dep][t][y] += 1
    jsave("catnat.json", {"source": name, "labels": labels, "years": dict(sorted(by_year.items())), "deps": by_dep})
    mark(meta, "catnat")


# ------------------------------------------------------------------ Biodiversité
def fetch_bio(meta):
    out = jload("bio.json", {})
    try:
        d = json.loads(get(MELODI))
        birds = {}
        for o in d.get("observations", []):
            dim = o["dimensions"]
            v = o["measures"]["OBS_VALUE_NIVEAU"].get("value")
            if v is None:
                continue
            key = {"15.i6b": "generalistes"}.get(dim["INDICATOR_SDG"]) or {"_T": "specialistes", "HABIT_TYP1": "agricoles",
                                                                              "HABIT_TYP2": "forestiers", "HABIT_TYP3": "batis"}.get(dim["COMPOSITE_BREAKDOWN"])
            if key:
                birds.setdefault(key, {})[dim["TIME_PERIOD"]] = v
        if birds:
            out["birds"] = {"base": 1989, "series": {k: dict(sorted(v.items())) for k, v in birds.items()}}
    except Exception as e:  # noqa: BLE001
        log(f"  Insee Melodi : échec ({e})")
    try:
        found = json.loads(get("https://www.data.gouv.fr/api/2/datasets/search/?q=consommation%20espaces%20naturels%20agricoles%20forestiers&page_size=20"))
        cands = [x for x in found.get("data", []) if (x.get("organization") or {}).get("name") == "Cerema" and "au 1er janvier" in x["title"]]
        ds = max(cands, key=lambda x: x["title"][-4:])
        res = json.loads(get(f"https://www.data.gouv.fr/api/1/datasets/{ds['id']}/"))["resources"]
        url = next(r["url"] for r in res if r["format"] == "csv" and "com" in r["title"])
        if (out.get("enaf") or {}).get("source") != url:
            rows = csv.DictReader(io.StringIO(get(url, timeout=600).decode("utf-8", "replace")))
            tot = {}
            for r in rows:
                for k, v in r.items():
                    m = re.fullmatch(r"(naf|art)(\d\d)(art|hab|act|inc|mix|fer|rou)(\d\d)", k or "")
                    if m and int(m.group(4)) == int(m.group(2)) + 1 and (m.group(1) == "naf" or m.group(3) in ("hab", "act")):
                        key = "total" if m.group(1) == "naf" else m.group(3)
                        y = 2000 + int(m.group(2))
                        tot.setdefault(key, {}).setdefault(y, 0.0)
                        tot[key][y] += float(v or 0)
            out["enaf"] = {"source": url, "title": ds["title"], "ha": {k: {y: round(v / 1e4) for y, v in sorted(t.items())} for k, t in tot.items()}}
    except Exception as e:  # noqa: BLE001
        log(f"  Cerema ENAF : échec ({e})")
    jsave("bio.json", out)
    mark(meta, "bio")


def main():
    os.makedirs(OUT, exist_ok=True)
    meta = jload("meta.json", {})
    jobs = [("Météo-France", fetch_stations, "stations", 20), ("PSMSL", fetch_sealevel, "sealevel", 24 * 30),
            ("WGMS", fetch_glaciers, "glaciers", 24 * 30), ("OWID", fetch_emissions, "emissions", 24 * 14),
            ("Météo-France quotidien", fetch_daily, "daily", 20), ("NOAA/NASA", fetch_world, "world", 24 * 7),
            ("ERSST", fetch_sst, "sst", 24 * 7), ("GASPAR", fetch_catnat, "catnat", 24 * 7), ("Biodiversité", fetch_bio, "bio", 24 * 14)]
    for name, fn, key, hours in jobs:
        if not stale(meta, key, hours):
            log(f"{name} : à jour")
            continue
        log(name)
        try:
            fn(meta)
        except Exception as e:  # noqa: BLE001 — une source en panne garde ses données précédentes
            log(f"  {name} : erreur {e}")
        jsave("meta.json", meta)


if __name__ == "__main__":
    main()
