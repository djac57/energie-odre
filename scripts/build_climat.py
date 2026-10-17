"""Construit site/climat.js à partir de data/raw/climat/ (voir fetch_climat.py).

    python3 scripts/build_climat.py data/raw site/climat.js

Méthode (rappelée sur la page) :
- normales 1991-2020 calculées station par station et mois par mois (au moins 20 années) ;
- anomalie mensuelle d'une station = valeur du mois − normale du mois ;
- indicateur France = moyenne des anomalies des stations de plaine (altitude < 800 m) disponibles,
  années retenues si les 12 mois ont au moins MIN_ST stations ; séries brutes, non homogénéisées ;
- tendances : régression linéaire (moindres carrés) sur les années complètes.
"""
import datetime as dt
import hashlib
import json
import os
import re
import statistics as stx
import sys
from collections import defaultdict

RAW = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
OUT = sys.argv[2] if len(sys.argv) > 2 else "site/climat.js"
SRC = os.path.join(RAW, "climat")
LOWLAND = 800
MIN_ST = 5          # stations minimum par mois pour l'indicateur France
NORMAL = (1991, 2020)
OLD = (1961, 1990)
EARLY = (1900, 1930)
TX, TN, TM, RR, J30, J35, TN20, GEL = range(8)


def load(name, default=None):
    p = os.path.join(SRC, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def r1(v, n=2):
    return None if v is None else round(v, n)


def slope(xs, ys):
    """Pente des moindres carrés (unités de y par unité de x)."""
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(pts) < 10:
        return None
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    den = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else None


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


st = load("stations.json")
stations = st["stations"]
# Le mois en cours est incomplet dans les fichiers Météo-France : on l'écarte des calculs
CUR_YM = dt.date.today().strftime("%Y%m")
partial = {}
for s in stations.values():
    for ym in [ym for ym in s["m"] if ym >= CUR_YM]:
        partial[ym] = True
        del s["m"][ym]

# ---------- normales et anomalies par station
normals = {}   # poste -> {var: [12 normales]}
for k, s in stations.items():
    nm = {}
    for var in (TX, TN, TM, RR, J30, J35, TN20, GEL):
        arr = []
        for mo in range(1, 13):
            vals = [v[var] for ym, v in s["m"].items() if NORMAL[0] <= int(ym[:4]) <= NORMAL[1] and int(ym[4:]) == mo and v[var] is not None]
            arr.append(sum(vals) / len(vals) if len(vals) >= 20 else None)
        nm[var] = arr
    normals[k] = nm

years_all = sorted({int(ym[:4]) for s in stations.values() for ym in s["m"]})
Y0, Y1 = years_all[0], years_all[-1]

# Anomalie mensuelle nationale (TM, TX, TN) : moyenne des stations de plaine
def national_monthly(var):
    out = {}
    for y in range(Y0, Y1 + 1):
        for mo in range(1, 13):
            ym = f"{y}{mo:02d}"
            an = []
            for k, s in stations.items():
                if s["meta"][3] >= LOWLAND:
                    continue
                v = s["m"].get(ym)
                nv = normals[k][var][mo - 1]
                if v and v[var] is not None and nv is not None:
                    an.append(v[var] - nv)
            if len(an) >= MIN_ST:
                out[ym] = (sum(an) / len(an), len(an))
    return out


nat = {var: national_monthly(var) for var in (TM, TX, TN)}


def annual(series, months=range(1, 13), year_shift_dec=False):
    """Moyenne annuelle des anomalies mensuelles ; None si un mois manque."""
    out = {}
    for y in range(Y0, Y1 + 1):
        vals = []
        for mo in months:
            yy = y - 1 if (year_shift_dec and mo == 12) else y
            v = series.get(f"{yy}{mo:02d}")
            if v is None:
                vals = None
                break
            vals.append(v[0])
        if vals:
            out[y] = sum(vals) / len(vals)
    return out


ann = {var: annual(nat[var]) for var in (TM, TX, TN)}
tm_years = sorted(ann[TM])
last_full = tm_years[-1]
off_old = mean([ann[TM].get(y) for y in range(OLD[0], OLD[1] + 1)])
off_early = mean([ann[TM].get(y) for y in range(EARLY[0], EARLY[1] + 1)])

seasons = {"hiver": ([12, 1, 2], True), "printemps": ([3, 4, 5], False), "été": ([6, 7, 8], False), "automne": ([9, 10, 11], False)}
seas = {name: annual(nat[TM], months, shift) for name, (months, shift) in seasons.items()}


def running(d, w=11, minn=8):
    ys = sorted(d)
    out = {}
    for y in ys:
        vals = [d[z] for z in range(y - w // 2, y + w // 2 + 1) if z in d]
        if len(vals) >= minn:
            out[y] = sum(vals) / len(vals)
    return out


def trend_dec(d, y0, y1):
    xs = [y for y in sorted(d) if y0 <= y <= y1]
    s = slope(xs, [d[y] for y in xs])
    return None if s is None else s * 10


ranked = sorted(tm_years, key=lambda y: -ann[TM][y])

# ---------- l'année en cours, mois par mois
cur_year = Y1 if Y1 > last_full else None
months_cur = []
if cur_year:
    for mo in range(1, 13):
        v = nat[TM].get(f"{cur_year}{mo:02d}")
        if v is None:
            continue
        hist = sorted([nat[TM][f"{y}{mo:02d}"][0] for y in range(1950, cur_year) if f"{y}{mo:02d}" in nat[TM]], reverse=True)
        rank = 1 + sum(1 for h in hist if h > v[0])
        months_cur.append([mo, r1(v[0]), v[1], rank, len(hist) + 1])
ytd = None
if months_cur:
    mos = [m[0] for m in months_cur]
    cur = mean([m[1] for m in months_cur])
    past = {}
    for y in range(1950, cur_year):
        vals = [nat[TM].get(f"{y}{mo:02d}") for mo in mos]
        if all(vals):
            past[y] = sum(v[0] for v in vals) / len(vals)
    rank = 1 + sum(1 for v in past.values() if v > cur)
    top = sorted(past.items(), key=lambda kv: -kv[1])[:5]
    ytd = {"months": mos, "anom": r1(cur), "rank": rank, "of": len(past) + 1, "top": [[y, r1(v)] for y, v in top]}

# ---------- stations : tendance et dernière année
st_out = []
for k, s in stations.items():
    name, lat, lon, alt = s["meta"]
    yearly = {}
    for y in range(Y0, Y1 + 1):
        vals = [s["m"].get(f"{y}{mo:02d}") for mo in range(1, 13)]
        if all(v and v[TM] is not None for v in vals):
            yearly[y] = sum(v[TM] for v in vals) / 12
    yrs61 = [y for y in yearly if 1961 <= y <= last_full]
    tr = trend_dec(yearly, 1961, last_full) if len(yrs61) >= 0.8 * (last_full - 1960) else None
    norm = mean(normals[k][TM]) if all(v is not None for v in normals[k][TM]) else None
    last = yearly.get(last_full)
    first = min(yearly) if yearly else None
    # jours chauds annuels de la station
    hot = {}
    for var, key in ((J30, "j30"), (J35, "j35"), (TN20, "tn20"), (GEL, "gel")):
        d = {}
        for y in range(Y0, Y1 + 1):
            vals = [s["m"].get(f"{y}{mo:02d}") for mo in range(1, 13)]
            if all(v and v[var] is not None for v in vals):
                d[y] = sum(v[var] for v in vals)
        hot[key] = d
    st_out.append({"id": k, "dep": s["dep"], "name": name.title(), "lat": lat, "lon": lon, "alt": alt,
                   "first": first, "trend": r1(tr), "normal": r1(norm, 1),
                   "lastAnom": r1(last - norm) if last is not None and norm is not None else None,
                   "tm": {y: r1(v) for y, v in yearly.items()},
                   "j30": hot["j30"], "j35": hot["j35"], "tn20": hot["tn20"], "gel": hot["gel"],
                   "j30dec": {}})

# ---------- jours chauds / nuits tropicales / gelées : moyenne des stations de plaine
def national_counts(key, minst=10):
    out = {}
    for y in range(1950, Y1 + 1):
        vals = [s[key].get(y) for s in st_out if s["alt"] < LOWLAND and s[key].get(y) is not None]
        if len(vals) >= minst:
            out[y] = sum(vals) / len(vals)
    return out


counts = {key: national_counts(key) for key in ("j30", "j35", "tn20", "gel")}
decades = []
for d0 in range(1951, last_full - 8, 10):
    d1 = d0 + 9
    row = {"label": f"{d0}-{d1}"}
    for key in counts:
        row[key] = r1(mean([counts[key].get(y) for y in range(d0, d1 + 1)]), 1)
    decades.append(row)

# ---------- précipitations (rapport à la normale, plaine)
def precip(months):
    out = {}
    for y in range(1950, Y1 + 1):
        ratios = []
        for s in stations.values():
            k = next(kk for kk, ss in stations.items() if ss is s)
            if s["meta"][3] >= LOWLAND:
                continue
            tot, nor = 0, 0
            ok = True
            for mo in months:
                v = s["m"].get(f"{y}{mo:02d}")
                n = normals[k][RR][mo - 1]
                if not v or v[RR] is None or n is None:
                    ok = False
                    break
                tot += v[RR]
                nor += n
            if ok and nor > 0:
                ratios.append(tot / nor)
        if len(ratios) >= 10:
            out[y] = sum(ratios) / len(ratios)
    return out


rr_year = precip(range(1, 13))
rr_summer = precip([6, 7, 8])

# ---------- niveau de la mer, glaciers, émissions
sea = load("sealevel.json", {})
sea_out = {}
for name, ser in sea.items():
    d = {int(y): v for y, v in ser.items()}
    ref = mean([d.get(y) for y in range(1991, 2021)])
    sea_out[name] = {"series": {y: v - ref for y, v in sorted(d.items())} if ref else {},
                     "trend1900": r1(slope([y for y in d if y >= 1900], [d[y] for y in d if y >= 1900]), 2),
                     "trend1993": r1(slope([y for y in d if y >= 1993], [d[y] for y in d if y >= 1993]), 2)}

gl = load("glaciers.json", {}) or {}
gl_out = {}
for name, ser in (gl.get("series") or {}).items():
    cum, acc = {}, 0.0
    for y, v in sorted(ser.items()):
        acc += v
        cum[int(y)] = round(acc, 2)
    ys = sorted(int(y) for y in ser)
    # cumul seulement pour les séries sans trou (sinon il sous-estimerait la perte)
    contiguous = all(b - a == 1 for a, b in zip(ys, ys[1:]))
    gl_out[name] = {"annual": {int(y): v for y, v in ser.items()}, "cum": cum if contiguous else None,
                    "first": ys[0], "last": ys[-1], "n": len(ys)}

em = load("emissions.json", {}) or {}
em_cols = em.get("cols", [])
em_rows = em.get("rows", [])

# ---------- sortie
nat_out = {
    "lastFull": last_full, "curYear": cur_year,
    "annual": {y: r1(v) for y, v in sorted(ann[TM].items())},
    "annualTx": {y: r1(v) for y, v in sorted(ann[TX].items())},
    "annualTn": {y: r1(v) for y, v in sorted(ann[TN].items())},
    "smooth": {y: r1(v) for y, v in running(ann[TM]).items()},
    "nStations": {y: min(nat[TM][f"{y}{mo:02d}"][1] for mo in range(1, 13)) for y in tm_years},
    "offOld": r1(off_old), "offEarly": r1(off_early),
    "trend1961": r1(trend_dec(ann[TM], 1961, last_full)), "trend1900": r1(trend_dec(ann[TM], 1900, last_full)),
    "trendTx": r1(trend_dec(ann[TX], 1961, last_full)), "trendTn": r1(trend_dec(ann[TN], 1961, last_full)),
    "warmest": [[y, r1(ann[TM][y])] for y in ranked[:10]],
    "rankLast": ranked.index(last_full) + 1, "nYears": len(ranked),
    "recent10": r1(mean([ann[TM].get(y) for y in range(last_full - 9, last_full + 1)])),
    "seasons": {name: {"annual": {y: r1(v) for y, v in sorted(d.items())}, "trend": r1(trend_dec(d, 1961, last_full))}
                for name, d in seas.items()},
    "months": months_cur, "ytd": ytd,
    "lastMonths": [[ym, r1(v[0]), v[1]] for ym, v in sorted(nat[TM].items())[-24:]],
}
data = {
    "updated": max(ym for s in stations.values() for ym in s["m"]),
    "national": nat_out,
    "stations": [{kk: vv for kk, vv in s.items() if kk not in ("j30dec",)} for s in st_out],
    "counts": {k: {y: r1(v, 1) for y, v in sorted(d.items())} for k, d in counts.items()},
    "countTrend": {k: r1(trend_dec(d, 1961, last_full), 1) for k, d in counts.items()},
    "decades": decades,
    "rain": {"year": {y: r1(v, 3) for y, v in sorted(rr_year.items())}, "summer": {y: r1(v, 3) for y, v in sorted(rr_summer.items())}},
    "sea": sea_out,
    "glaciers": gl_out, "glacierCitation": gl.get("citation", ""),
    "emissions": {"cols": em_cols, "rows": [r for r in em_rows if r[0] >= 1850]},
    "method": {"lowland": LOWLAND, "minSt": MIN_ST, "normal": NORMAL},
    "partialMonth": sorted(partial)[0] if partial else None,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("window.CLIMAT=")
    json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    fh.write(";")
digest = hashlib.sha1(open(OUT, "rb").read()).hexdigest()[:10]
page = os.path.join(os.path.dirname(OUT), "climat.html")
if os.path.exists(page):
    src = open(page, encoding="utf-8").read()
    new = re.sub(r'src="climat\.js(\?v=[\w-]+)?"', f'src="climat.js?v={digest}"', src)
    if new != src:
        open(page, "w", encoding="utf-8").write(new)
print(f"{OUT} : {os.path.getsize(OUT) // 1024} Ko · {len(stations)} stations · France {tm_years[0]}–{last_full}"
      f" · {last_full} : {ann[TM][last_full]:+.2f} °C (rang {nat_out['rankLast']}/{len(ranked)}) · tendance 1961→ {nat_out['trend1961']} °C/déc.")
