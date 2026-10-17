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

Sortie : data/raw/climat/{stations,sealevel,glaciers,emissions,meta}.json (compacts, versionnés).
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


def main():
    os.makedirs(OUT, exist_ok=True)
    meta = jload("meta.json", {})
    jobs = [("Météo-France", fetch_stations, "stations", 20), ("PSMSL", fetch_sealevel, "sealevel", 24 * 30),
            ("WGMS", fetch_glaciers, "glaciers", 24 * 30), ("OWID", fetch_emissions, "emissions", 24 * 14)]
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
