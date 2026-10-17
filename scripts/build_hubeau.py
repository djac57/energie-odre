"""Construit site/hubeau.js : rivières des centrales nucléaires et nappes phréatiques.

Entrées (voir fetch_hubeau.py et fetch_rte.py) :
- data/raw/hubeau/stations.json, rivers.json : débit QmnJ (m³/s), température de l'eau, température max de l'air ;
- data/raw/rte/nuclear_daily.json : production journalière de chaque réacteur (RTE, depuis 2019) ;
- data/raw/rte/unavailabilities.json : indisponibilités dont la remarque cite des « causes externes liées à l'environnement » ;
- data/raw/hubeau/nappes.json : niveaux mensuels moyens d'un piézomètre par département ;
- data/raw/snow/snow.json : hauteur de neige modélisée sur quatre massifs (Open-Meteo, ERA5).

Niveau relatif des nappes : pour chaque piézomètre et chaque mois calendaire, la valeur du mois est classée parmi
toutes les valeurs du même mois calendaire de sa chronique (rang centile, au moins 10 années) ; sept classes,
comme les bulletins de situation hydrogéologique du BRGM, mais calculées ici sur la chronique disponible
(ce n'est pas l'IPS officiel, qui repose sur une loi statistique ajustée).

    python3 scripts/build_hubeau.py data/raw site/hubeau.js
"""
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict

RAW, OUT = sys.argv[1], sys.argv[2]
SUMMER = (5, 1), (10, 31)        # fenêtre « été » affichée, du 1er mai au 31 octobre
FIRST_YEAR = 2019
ENV = re.compile(r"causes? externes? li[ée]es? à l.environnement|contraintes? environnementales?|environmental issues", re.I)
NAMES = {"SAINT ALBAN": "ST ALBAN", "SAINT LAURENT": "ST LAURENT"}
TITLE = {"SAINT ALBAN": "Saint-Alban", "SAINT LAURENT": "Saint-Laurent", "BELLEVILLE": "Belleville", "BLAYAIS": "Blayais", "BUGEY": "Bugey",
         "CATTENOM": "Cattenom", "CHINON": "Chinon", "CHOOZ": "Chooz", "CIVAUX": "Civaux", "CRUAS": "Cruas", "DAMPIERRE": "Dampierre",
         "GOLFECH": "Golfech", "NOGENT": "Nogent", "TRICASTIN": "Tricastin"}
CLASSES = [(.10, "très bas"), (.25, "bas"), (.40, "modérément bas"), (.60, "autour de la normale"), (.75, "modérément haut"), (.90, "haut"), (1.01, "très haut")]
MAX_TEMP_KM = 35  # au-delà, la station de température ne renseigne plus vraiment sur l'eau prélevée par la centrale


def nice(name):
    """« LOIRE à MUIDES-SUR-LOIRE » → « Loire à Muides-sur-Loire » ; les libellés déjà en casse mixte sont gardés."""
    if not any(c.isupper() for c in name[1:]) or name != name.upper() and not name.split()[0].isupper():
        return name
    small = {"a": "à", "à": "à", "sur": "sur", "de": "de", "du": "du", "des": "des", "en": "en", "et": "et", "la": "la", "le": "le", "les": "les"}
    words = []
    for i, w in enumerate(name.lower().split()):
        w = small.get(w, w) if i else w
        words.append("-".join(x if x in small.values() and (i or j) else x[:1].upper() + x[1:] for j, x in enumerate(w.split("-"))) if w not in ("à",) else w)
    return " ".join(words)


def load(*p, default=None):
    try:
        with open(os.path.join(RAW, *p), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = (len(sorted_vals) - 1) * q
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def summer_days(year):
    d, end = dt.date(year, *SUMMER[0]), dt.date(year, *SUMMER[1])
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def env_events(unav):
    """Indisponibilités nucléaires « environnement » : dernière version de chaque événement, par réacteur.
    Les versions successives se recouvrent : la puissance perdue est prise au maximum heure par heure."""
    by_unit = defaultdict(lambda: defaultdict(float))  # nom → heure → MW
    for u in unav.values():
        if u.get("fuel") != "NUCLEAR" or not ENV.search(u.get("remarks") or "") or u.get("status") == "DISMISSED":
            continue
        for a, b, mw in u.get("steps", []):
            if not mw:
                continue
            t = dt.datetime.fromisoformat(a.replace("Z", "+00:00")).replace(minute=0, second=0)
            e = dt.datetime.fromisoformat(b.replace("Z", "+00:00"))
            while t < e:
                k = t.strftime("%Y-%m-%dT%H")
                by_unit[u["name"]][k] = max(by_unit[u["name"]][k], mw)
                t += dt.timedelta(hours=1)
    out = {}
    for name, hours in by_unit.items():
        # regroupe en épisodes contigus [début, fin, MW max, MWh perdus]
        eps, cur = [], None
        for k in sorted(hours):
            t = dt.datetime.strptime(k, "%Y-%m-%dT%H")
            if cur and t - cur["e"] <= dt.timedelta(hours=1):
                cur["e"] = t; cur["mw"] = max(cur["mw"], hours[k]); cur["mwh"] += hours[k]
            else:
                cur = {"s": t, "e": t, "mw": hours[k], "mwh": hours[k]}; eps.append(cur)
        out[name] = [[c["s"].strftime("%Y-%m-%dT%H"), (c["e"] + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H"), round(c["mw"]), round(c["mwh"])] for c in eps]
    return out


def rivers():
    st = load("hubeau", "stations.json", default={})
    rv = load("hubeau", "rivers.json", default={"temp": {}, "flow": {}, "air": {}})
    nd = load("rte", "nuclear_daily.json", default={"units": {}})
    unav = load("rte", "unavailabilities.json", default={})
    caps = defaultdict(int)
    for u in unav.values():
        if u.get("fuel") == "NUCLEAR":
            caps[u["name"]] = max(caps[u["name"]], u.get("mw") or 0)
    env = env_events(unav)
    today = dt.date.today()
    years = list(range(FIRST_YEAR, today.year + 1))
    plants = []
    for key, s in st.items():
        rte_name = NAMES.get(key, key)
        units = sorted({u["name"] for u in nd["units"].values() if u["name"].rsplit(" ", 1)[0] == rte_name} | {n for n in caps if n.rsplit(" ", 1)[0] == rte_name},
                       key=lambda n: int(n.rsplit(" ", 1)[1]) if n.rsplit(" ", 1)[1].isdigit() else 0)
        unit_daily = [u["d"] for u in nd["units"].values() if u["name"] in units]
        flow_st = s["flow"][0] if s["flow"] else None
        flow = rv["flow"].get(flow_st["code"], {}) if flow_st else {}
        temps = [t for t in s["temp"] if t["km"] <= MAX_TEMP_KM and rv["temp"].get(t["code"])]
        water = rv["temp"].get(temps[0]["code"], {}) if temps else {}
        air = rv["air"].get(key, {})
        # climatologie du débit : quantiles par jour de l'été, sur toutes les années disponibles sauf l'année affichée
        clim = {}
        for y in years:
            q10, q50, q90 = [], [], []
            for d in summer_days(y):
                vals = sorted(v for yy in range(2012, today.year + 1) if yy != y
                              for v in [flow.get(dt.date(yy, d.month, d.day).isoformat())] if v is not None)
                q10.append(round(quantile(vals, .1), 1) if len(vals) >= 5 else None)
                q50.append(round(quantile(vals, .5), 1) if len(vals) >= 5 else None)
                q90.append(round(quantile(vals, .9), 1) if len(vals) >= 5 else None)
            clim[y] = [q10, q50, q90]
        series = {}
        for y in years:
            days = [d.isoformat() for d in summer_days(y)]
            prod = [round(sum(u.get(d, 0) for u in unit_daily) / 1000, 1) if any(d in u for u in unit_daily) else None for d in days]
            series[y] = {"prod": prod, "flow": [flow.get(d) for d in days], "air": [air.get(d) for d in days],
                         "water": [water[d][1] if d in water else None for d in days], "clim": clim[y]}
        mw = sum(caps.get(u, 0) for u in units)
        plants.append({"key": key, "name": TITLE.get(key, key.title()), "river": s["river"], "lat": s["lat"], "lon": s["lon"], "mw": mw, "units": units,
                       "flowStation": {"name": flow_st["name"], "km": flow_st["km"], "code": flow_st["code"]} if flow_st else None,
                       "tempStation": {"name": nice(temps[0]["name"]), "km": temps[0]["km"], "code": temps[0]["code"], "last": max(water) if water else None} if temps else None,
                       "series": series})
    plants.sort(key=lambda p: p["name"])
    # Synthèse « environnement » pour tout le parc (y compris bord de mer), par réacteur
    env_units = {n: {"mw": caps.get(n), "eps": e} for n, e in env.items()}
    return {"plants": plants, "years": years, "summerStart": "%02d-%02d" % SUMMER[0], "env": env_units}


def nappes():
    np_ = load("hubeau", "nappes.json", default={"stations": {}, "monthly": {}})
    stations = []
    for code, meta in np_["stations"].items():
        ser = np_["monthly"].get(code, {})
        if len(ser) < 120 or meta.get("lat") is None:
            continue
        months = sorted(ser)
        by_cal = defaultdict(list)
        for m in months:
            by_cal[m[5:]].append(ser[m])
        for k in by_cal:
            by_cal[k].sort()
        cls = {}
        for m in months:
            vals = by_cal[m[5:]]
            if len(vals) < 10:
                continue
            below = sum(1 for v in vals if v < ser[m]); equal = sum(1 for v in vals if v == ser[m])
            rank = (below + .5 * equal) / len(vals)
            cls[m] = next(i for i, (q, _) in enumerate(CLASSES) if rank < q)
        if not cls:
            continue
        first = months[0]
        y0, m0 = int(first[:4]), int(first[5:])
        n = (int(months[-1][:4]) - y0) * 12 + int(months[-1][5:]) - m0 + 1
        keys = ["%d-%02d" % (y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1) for i in range(n)]
        stations.append({"code": code, "dep": meta["dep"], "commune": meta.get("commune"), "lat": round(meta["lat"], 4), "lon": round(meta["lon"], 4),
                         "nappe": meta.get("nappe"), "m0": first, "lvl": [ser.get(k) for k in keys], "cls": [cls.get(k) for k in keys]})
    stations.sort(key=lambda s: s["dep"])
    return {"stations": stations, "classes": [c[1] for c in CLASSES]}


def snow():
    """Hauteur de neige modélisée (cm) par massif, série journalière continue depuis le 1er septembre 2015 (voir fetch_snow.py)."""
    sn = load("snow", "snow.json", default={})
    out = {}
    for name, rec in sn.items():
        d = rec.get("d", {})
        if not d:
            continue
        d0 = dt.date.fromisoformat(min(d)); n = (dt.date.fromisoformat(max(d)) - d0).days + 1
        out[name] = {"elev": rec.get("elev"), "d0": d0.isoformat(), "v": [d.get((d0 + dt.timedelta(days=i)).isoformat()) for i in range(n)]}
    return out


def main():
    out = {"rivers": rivers(), "nappes": nappes(), "snow": snow(), "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.HUBEAU = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"hubeau.js : {len(out['rivers']['plants'])} centrales, {len(out['nappes']['stations'])} piézomètres, {os.path.getsize(OUT) // 1024} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
