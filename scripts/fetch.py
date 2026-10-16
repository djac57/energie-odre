"""Télécharge les données éCO2mix (RTE) depuis ODRÉ — odre.opendatasoft.com.

- national consolidé/définitif + temps réel : export CSV complet (pas semi-horaire),
  agrégé ensuite par build.py ;
- régional consolidé/définitif + temps réel : agrégé par mois directement côté API.

    python3 scripts/fetch.py [dossier_sortie]
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/{ds}/exports/{fmt}"
UA = "Mozilla/5.0 (compatible; energie-odre-viz/1.0)"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")

NATIONAL_FIELDS = ["date_heure", "consommation", "fioul", "charbon", "gaz", "nucleaire", "eolien", "solaire",
                   "hydraulique", "pompage", "bioenergies", "ech_physiques", "taux_co2", "ech_comm_angleterre",
                   "ech_comm_espagne", "ech_comm_italie", "ech_comm_suisse", "ech_comm_allemagne_belgique"]
# Le temps réel régional n'a pas le détail terrestre/en mer de l'éolien, ni le pompage en nombre
REGIONAL_FIELDS = {
    "eco2mix-regional-cons-def": ["consommation", "thermique", "nucleaire", "eolien_terrestre", "eolien_offshore",
                                  "solaire", "hydraulique", "bioenergies", "pompage", "ech_physiques"],
    "eco2mix-regional-tr": ["consommation", "thermique", "nucleaire", "eolien", "solaire", "hydraulique",
                            "bioenergies", "ech_physiques"],
}


def download(ds, fmt, params, dest):
    url = API.format(ds=ds, fmt=fmt) + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            print(f"  {os.path.basename(dest)} : {os.path.getsize(dest) // 1024} Ko")
            return
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  nouvel essai ({e})", file=sys.stderr)
            time.sleep(10)


def merge_monthly(dest, fresh):
    """Fusionne un agrégat mensuel fraîchement téléchargé avec celui déjà versionné.

    ODRÉ republie parfois un jeu en ne renvoyant qu'une partie de l'historique : on ne
    remplace donc jamais l'existant par moins de données. Clé : (région, mois) ; à clé
    égale, la version qui compte le plus de relevés l'emporte (mois complet)."""
    def key(r):
        month = r.get("mois") or next((v for k, v in r.items() if k.startswith("date_format(")), None)
        return (r.get("libelle_region") or r.get("region"), month)
    rows = {}
    if os.path.exists(dest):
        for r in json.load(open(dest, encoding="utf-8")):
            rows[key(r)] = r
    before = len(rows)
    for r in json.load(open(fresh, encoding="utf-8")):
        k = key(r)
        if k[1] and (k not in rows or (r.get("n") or 0) >= (rows[k].get("n") or 0)):
            rows[k] = r
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(sorted(rows.values(), key=lambda r: (str(key(r)[0]), str(key(r)[1]))), fh, ensure_ascii=False, indent=0)
    os.remove(fresh)
    print(f"  fusion : {before} -> {len(rows)} mois-région")


def main():
    os.makedirs(OUT, exist_ok=True)
    tz = {"timezone": "Europe/Paris"}
    for ds in ["eco2mix-national-cons-def", "eco2mix-national-tr"]:
        print(ds)
        download(ds, "csv", {"select": ",".join(NATIONAL_FIELDS), "delimiter": ";", "order_by": "date_heure", **tz},
                 os.path.join(OUT, ds + ".csv"))
    month = "date_format(date_heure,'yyyy-MM')"
    for ds, fields in REGIONAL_FIELDS.items():
        print(ds)
        sel = ", ".join(["libelle_region", f"{month} as mois"] + [f"avg({f}) as {f}" for f in fields] + ["count(consommation) as n"])
        dest = os.path.join(OUT, ds + ".json")
        download(ds, "json", {"select": sel, "group_by": f"libelle_region, {month}", **tz}, dest + ".new")
        merge_monthly(dest, dest + ".new")
    # NB : avant 2021, l'éolien régional n'existe que dans la colonne texte « eolien », que l'API ne sait pas
    # moyenner ; build.py le traite comme non disponible plutôt que comme zéro.
    # Météo régionale (Weathernews France) : vent à 100 m (m/s) et rayonnement solaire global, moyennés par mois
    ds = "rayonnement-solaire-vitesse-vent-tri-horaires-regionaux"
    print(ds)
    wmonth = "date_format(date,'yyyy-MM')"
    download(ds, "json", {"select": f"region, {wmonth} as mois, avg(uv100) as vent, avg(ssrd03h) as rayonnement, count(*) as n",
                          "group_by": f"region, {wmonth}", **tz}, os.path.join(OUT, ds + ".json.new"))
    merge_monthly(os.path.join(OUT, ds + ".json"), os.path.join(OUT, ds + ".json.new"))


if __name__ == "__main__":
    main()
