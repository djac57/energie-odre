"""Construit site/eaufeuxair.js pour la page « Eau, feux & air ».

    python3 scripts/build_eau_feux_air.py [data/raw] [site/eaufeuxair.js]
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

RAW = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
OUT = sys.argv[2] if len(sys.argv) > 2 else "site/eaufeuxair.js"
SRC = os.path.join(RAW, "eau_feux_air")
SITE = os.path.dirname(OUT) or "site"
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()


def load(name, default=None):
    p = os.path.join(SRC, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_js(name):
    p = os.path.join(SITE, name)
    s = open(p, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))


class Index:
    """Point → entité (polygones), avec préfiltre par emprise."""

    def __init__(self, feats, key):
        self.items = []
        for f in feats:
            g = f["geometry"]
            if not g:
                continue
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            xs = [p[0] for poly in polys for p in poly[0]]
            ys = [p[1] for poly in polys for p in poly[0]]
            self.items.append((key(f), (min(xs), min(ys), max(xs), max(ys)), polys))

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


def simplify_geo(geo, digits=2):
    def rnd(c):
        if isinstance(c[0], list):
            out = [rnd(x) for x in c]
            if out and not isinstance(out[0][0], list):  # anneau : retire les points consécutifs identiques
                ded = [out[0]] + [p for i, p in enumerate(out[1:], 1) if p != out[i - 1]]
                return ded if len(ded) >= 4 else out
            return out
        return [round(c[0], digits), round(c[1], digits)]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"code": f["properties"]["code"], "nom": f["properties"]["nom"]},
         "geometry": {"type": f["geometry"]["type"], "coordinates": rnd(f["geometry"]["coordinates"])}}
        for f in geo["features"]]}


def main():
    data = {"updated": NOW.isoformat(timespec="minutes")}
    deps_geo = load("departements.json", {"features": []})
    data["deps"] = simplify_geo(deps_geo, 3)
    dep_names = {f["properties"]["code"]: f["properties"]["nom"] for f in deps_geo["features"]}
    dep_ix = Index(deps_geo["features"], lambda f: f["properties"]["code"])
    eu = load_js("geo_europe.js")
    cty_ix = Index(eu["features"], lambda f: f["properties"]["code"])
    cty_names = {f["properties"]["code"]: f["properties"]["nom"] for f in eu["features"]}
    data["countryNames"] = cty_names

    # ---------------- FIRMS en direct
    recent = load("firms_recent.json", {"rows": []})["rows"]
    static_cells = {c for c, ys in load("firms_static.json", {"cells": {}})["cells"].items() if len(ys) >= 2}
    static_pts = [tuple(map(float, c.split(","))) for c in static_cells]

    def is_static(lat, lon):  # à ~1 km d'une source fixe récurrente (type 2 dans les archives VIIRS)
        k = (round(lat, 2), round(lon, 2))
        for dy in (-0.01, 0, 0.01):
            for dx in (-0.01, 0, 0.01):
                if f"{round(k[0] + dy, 2)},{round(k[1] + dx, 2)}" in static_cells:
                    return True
        return False
    rows = []
    older = defaultdict(lambda: [0, 0.0])  # 3 à 7 jours : agrégé en cellules de 0,1°
    country_day = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    dep_day = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    cut7 = (NOW - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%MZ")
    for lat, lon, t, sat, frp, conf, dn in recent:
        st = is_static(lat, lon)
        cty = cty_ix.find(lon, lat) or "—"
        dep = dep_ix.find(lon, lat) if cty == "FR" else None
        day = t[:10]
        if not st:
            a = country_day[day][cty]
            a[0] += 1
            a[1] += frp
            if dep:
                b = dep_day[day][dep]
                b[0] += 1
                b[1] += frp
        if t >= cut7:
            hours = (NOW - dt.datetime.strptime(t, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)).total_seconds() / 3600
            if hours <= 72:
                rows.append([round(lat, 3), round(lon, 3), round(hours), round(frp), {"N": 0, "N20": 1, "N21": 2}.get(sat, 0),
                             conf, 1 if st else 0, cty, dep or ""])
            elif not st:
                o = older[(round(lat, 1), round(lon, 1))]
                o[0] += 1
                o[1] += frp
    last_t = max((r[2] for r in recent), default=None)
    data["firms"] = {
        "cols": ["lat", "lon", "heures", "frp", "sat", "conf", "fixe", "pays", "dep"],
        "rows": rows, "last": last_t,
        "older": [[a, b, n, round(f)] for (a, b), (n, f) in older.items()],
        "countryDay": {d: {c: [v[0], round(v[1])] for c, v in cs.items()} for d, cs in sorted(country_day.items())},
        "depDay": {d: {c: [v[0], round(v[1])] for c, v in cs.items()} for d, cs in sorted(dep_day.items())},
        "static": sorted(static_pts),
    }

    # ---------------- FIRMS archives
    arch = load("firms_archive.json", {})
    years = defaultdict(lambda: defaultdict(dict))
    grid = defaultdict(dict)
    fr_deps = {}
    for key, v in arch.items():
        prod, iso, y = key.split("|")
        years[iso][prod][y] = [v["n"], v["frp"], v["months"]]
        if prod == "viirs-snpp":
            grid[y][iso] = [[*map(float, c.split(",")), n, f] for c, (n, f) in v["cells"].items()]
            if iso == "FR":
                fr_deps[y] = v["deps"]
    # Année en cours (NRT, hors sources fixes) pour prolonger les séries
    cur = str(TODAY.year)
    cur_cells = defaultdict(lambda: [0, 0.0])
    for lat, lon, t, sat, frp, conf, dn in recent:
        if t[:4] == cur and sat == "N" and not is_static(lat, lon):
            c = cur_cells[(round(lat, 1), round(lon, 1))]
            c[0] += 1
            c[1] += frp
    data["archive"] = {"years": years, "frDeps": fr_deps,
                       # grille 0,1° par année, à plat : [lat×10, lon×10, détections, PRF MW] répétés
                       "grid": {y: {iso: [x for a, b, n, f in cells for x in (round(a * 10), round(b * 10), n, round(f))]
                                    for iso, cells in g.items()} for y, g in sorted(grid.items())}}

    # ---------------- BDIFF
    data["bdiff"] = load("bdiff.json", {})

    # ---------------- Météo des forêts
    mdf = load("mdf.json", {"years": {}})["years"]
    last_year = max(mdf) if mdf else None
    mdf_out = {"last": None, "season": {}}
    if last_year:
        last_day = max(mdf[last_year])
        mdf_out["last"] = {"date": last_day, "deps": mdf[last_year][last_day]}
        mdf_out["recent"] = {d: v for d, v in list(sorted(mdf[last_year].items()))[-60:]}
    for y, days in mdf.items():  # nombre de jours à danger élevé (3) ou plus, par département
        per = defaultdict(lambda: [0, 0, 0])
        for d, deps in days.items():
            for dep, (j1, j2) in deps.items():
                p = per[dep]
                p[0] += 1
                if j1 >= 3:
                    p[1] += 1
                if j1 >= 4:
                    p[2] += 1
        mdf_out["season"][y] = {dep: v for dep, v in per.items()}
        mdf_out.setdefault("national", {})[y] = [[d, sum(1 for v in deps.values() if v[0] >= 3),
                                                   sum(1 for v in deps.values() if v[0] >= 4)]
                                                  for d, deps in sorted(days.items())]
    data["mdf"] = mdf_out

    # ---------------- VigiEau
    data["vigieau"] = {"now": load("vigieau_now.json", {}), "weekly": []}
    ar = load("vigieau_arretes.json", {"rows": []})["rows"]
    if ar:
        by_dep = defaultdict(list)
        for dep, deb, fin, lvl, types in ar:
            by_dep[dep].append((deb, fin, lvl))
        start = dt.date(2012, 1, 2)
        weeks = []
        d = start
        while d <= TODAY:
            weeks.append(d)
            d += dt.timedelta(days=7)
        out = []
        for w in weeks:
            ws = w.isoformat()
            cnt = [0, 0, 0, 0, 0]
            for dep, items in by_dep.items():
                m = max((lvl for deb, fin, lvl in items if deb <= ws <= fin), default=0)
                cnt[m] += 1
            out.append([ws] + cnt[1:])
        data["vigieau"]["weekly"] = out

    # ---------------- Qualité de l'air
    raw_atmo = load("atmo.json", {})
    atmo = dict(raw_atmo.get("_old", {}))  # jours pas encore réagrégés (rattrapage en cours)
    atmo.update({k: v for k, v in raw_atmo.items() if not k.startswith("_") and v})
    days = sorted(atmo)
    today = TODAY.isoformat()
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    series = []
    for d in days:
        n = [0] * 7
        for v in atmo[d].values():
            for i, x in enumerate(v["n"]):
                n[i] += x
        series.append([d] + n)
    per_dep_bad = defaultdict(int)  # jours où au moins 10 % des communes sont « mauvais » ou pire
    for d in days:
        if d > today:
            continue
        for dep, v in atmo[d].items():
            tot = sum(v["n"][1:7])
            if tot and sum(v["n"][4:7]) / tot >= 0.1:
                per_dep_bad[dep] += 1
    today_d = today if today in atmo else ([d for d in days if d <= today] or [None])[-1]
    cur = {k: dict(v) for k, v in atmo.get(today_d, {}).items()}
    src = {}  # AASQA de chaque département (pour expliquer les absences)
    for d in days[-30:]:
        for dep, v in atmo[d].items():
            if v.get("src"):
                src[dep] = v["src"]
    for d in reversed([x for x in days if x < (today_d or "")][-3:]):  # repli : dernier jour publié (≤ 3 j)
        for dep, v in atmo[d].items():
            if dep not in cur:
                cur[dep] = dict(v, fb=d)
    data["atmo"] = {"today": cur, "src": src,
                    "todayDate": today_d,
                    "tomorrow": atmo.get(tomorrow), "tomorrowDate": tomorrow if tomorrow in atmo else None,
                    "series": series, "badDays": dict(per_dep_bad), "first": days[0] if days else None}

    # ---------------- Forêts
    ifn = load("ifn.json", {})
    REG = {"11": "75 77 78 91 92 93 94 95", "24": "18 28 36 37 41 45", "27": "21 25 39 58 70 71 89 90", "28": "14 27 50 61 76",
           "32": "02 59 60 62 80", "44": "08 10 51 52 54 55 57 67 68", "52": "44 49 53 72 85", "53": "22 29 35 56",
           "75": "16 17 19 23 24 33 40 47 64 79 86 87", "76": "09 11 12 30 31 32 34 46 48 65 66 81 82",
           "84": "01 03 07 15 26 38 42 43 63 69 73 74", "93": "04 05 06 13 83 84", "94": "2A 2B"}
    dep_reg = {d: r for r, ds in REG.items() for d in ds.split()}
    data["ifn"] = {k: v for k, v in ifn.items() if not k.startswith("R")}
    for dep in dep_names:  # département non publié par l'IFN : valeur de la région, signalée comme telle
        r = ifn.get("R" + dep_reg.get(dep, ""))
        if dep not in data["ifn"] and r:
            data["ifn"][dep] = dict(r, reg=r.get("nom"))
    data["gfw"] = load("gfw.json", {})

    def norm(s):
        import unicodedata
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z]", "", s)
    by_norm = {norm(n): c for c, n in dep_names.items()}
    data["gfwCodes"] = {name: by_norm.get(norm(name)) for name in data["gfw"]}
    data["depNames"] = dep_names

    js = "window.EFA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(js)
    digest = hashlib.sha1(js.encode()).hexdigest()[:10]
    page = os.path.join(SITE, "eau-feux-air.html")
    if os.path.exists(page):
        html = open(page, encoding="utf-8").read()
        new = re.sub(r'src="eaufeuxair\.js(\?v=[\w-]+)?"', f'src="eaufeuxair.js?v={digest}"', html)
        if new != html:
            open(page, "w", encoding="utf-8").write(new)
    unmatched = [n for n, c in data["gfwCodes"].items() if not c]
    print(f"{OUT} : {len(js) // 1024} Ko · FIRMS {len(rows)} détections (72 h), {len(static_pts)} sources fixes · "
          f"archives {len(arch)} · BDIFF {len(data['bdiff'])} ans · Atmo {len(days)} j · VigiEau {len(data['vigieau']['weekly'])} sem."
          + (f" · GFW non reliés : {unmatched}" if unmatched else ""))


if __name__ == "__main__":
    main()
