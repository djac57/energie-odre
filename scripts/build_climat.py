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


# ---------- conséquences déjà visibles (agrégats compacts des autres pages, lecture seule)
def load_other(*parts, default=None):
    p = os.path.join(RAW, *parts)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


TODAY = dt.date.today()
NOW_UTC = dt.datetime.now(dt.timezone.utc)
ENV_RE = re.compile(r"causes? externes? li[ée]es? à l.environnement|contraintes? environnementales?|environmental issues", re.I)


def csq_vigieau():
    ar = (load_other("eau_feux_air", "vigieau_arretes.json") or {}).get("rows", [])
    if not ar:
        return None
    by_dep = defaultdict(list)
    for dep, deb, fin, lvl, _ in ar:
        by_dep[dep].append((deb, fin, lvl))
    weekly, d = [], dt.date(2012, 1, 2)
    while d <= TODAY:
        ws = d.isoformat()
        lv = [max((l for a, b, l in it if a <= ws <= b), default=0) for it in by_dep.values()]
        weekly.append([ws, sum(1 for v in lv if v >= 2), sum(1 for v in lv if v >= 4)])
        d += dt.timedelta(days=7)
    years = {}
    for y in range(2012, TODAY.year + 1):
        y0, y1 = f"{y}-01-01", f"{y}-12-31"
        crise, restr, any_ = set(), set(), set()
        for dep, it in by_dep.items():
            for a, b, l in it:
                if a <= y1 and b >= y0:
                    any_.add(dep)
                    if l >= 2: restr.add(dep)
                    if l >= 4: crise.add(dep)
        years[y] = [len(crise), len(restr), len(any_), max((w[1] for w in weekly if w[0][:4] == str(y)), default=0)]
    return {"weekly": weekly, "years": years}


def csq_lakes():
    w = load_other("rte", "water_reserves.json") or {}
    if not w:
        return None
    by_year = defaultdict(list)
    for d, v in sorted(w.items()):
        dd = dt.date.fromisoformat(d)
        by_year[dd.year].append([dd.timetuple().tm_yday, round(v / 1e6, 3)])
    last = max(w)
    ld = dt.date.fromisoformat(last)
    same = {}
    for d, v in w.items():  # même période de l'année (± 3 jours), une valeur par an
        dd = dt.date.fromisoformat(d)
        if abs(dd.timetuple().tm_yday - ld.timetuple().tm_yday) <= 3:
            same[dd.year] = v
    ranked = sorted(same, key=same.get)
    return {"years": dict(by_year), "last": last, "lastTWh": round(w[last] / 1e6, 2),
            "rankLow": ranked.index(ld.year) + 1, "of": len(ranked), "lowest": [[y, round(same[y] / 1e6, 2)] for y in ranked[:3]]}


def csq_nappes():
    np_ = load_other("hubeau", "nappes.json") or {}
    share = defaultdict(lambda: [0, 0, 0])
    for code, ser in np_.get("monthly", {}).items():
        if len(ser) < 120:
            continue
        by_cal = defaultdict(list)
        for m, v in ser.items():
            by_cal[m[5:]].append(v)
        for m, v in ser.items():
            vals = by_cal[m[5:]]
            if len(vals) < 10:
                continue
            rank = (sum(1 for x in vals if x < v) + .5 * sum(1 for x in vals if x == v)) / len(vals)
            c = share[m]; c[2] += 1
            if rank < .40: c[0] += 1
            if rank < .25: c[1] += 1
    out = [[m, round(a / n, 3), round(b / n, 3), n] for m, (a, b, n) in sorted(share.items()) if n >= 30 and m >= "1990-01"]
    return {"monthly": out} if out else None


def csq_fires():
    b = load_other("eau_feux_air", "bdiff.json") or {}
    bd = {int(y): [sum(v[0] for v in r["deps"].values()), round(sum(v[1] for v in r["deps"].values()))] for y, r in b.items()}
    fa = load_other("eau_feux_air", "firms_archive.json") or {}
    firms, months = {}, {}
    for k, v in fa.items():
        prod, iso, y = k.split("|")
        if iso == "FR":
            firms.setdefault(prod, {})[int(y)] = v["n"]
            if prod == "viirs-snpp":
                months.setdefault(int(y), [m if isinstance(m, (int, float)) else m[0] for m in v["months"]])
    return {"bdiff": dict(sorted(bd.items())), "firms": {k: dict(sorted(v.items())) for k, v in firms.items()},
            "viirsMonths": dict(sorted(months.items()))}


def csq_snow():
    sn = load_other("snow", "snow.json") or {}
    out = {}
    for name, rec in sn.items():
        d = rec.get("d", {})
        win = {}
        for y in range(2015, TODAY.year):
            vals = [v for k, v in d.items() if f"{y}-12-01" <= k <= f"{y + 1}-04-30" and v is not None]
            if len(vals) >= 140:  # hiver complet (déc.→avr. ≈ 151 jours)
                win[y + 1] = [round(sum(vals) / len(vals), 1), sum(1 for v in vals if v >= 10)]
        if win:
            out[name] = {"elev": rec.get("elev"), "winters": win}
    return out or None


def csq_nuclear():
    unav = load_other("rte", "unavailabilities.json") or {}
    hours = defaultdict(lambda: defaultdict(float))  # réacteur → heure → MW (max des versions)
    for u in unav.values():
        if u.get("fuel") != "NUCLEAR" or u.get("status") == "DISMISSED" or not ENV_RE.search(u.get("remarks") or ""):
            continue
        for a, b, mw in u.get("steps", []):
            if not mw:
                continue
            t = dt.datetime.fromisoformat(a.replace("Z", "+00:00")).replace(minute=0, second=0)
            e = min(dt.datetime.fromisoformat(b.replace("Z", "+00:00")), NOW_UTC)  # pas d'heures à venir
            while t < e:
                k = t.strftime("%Y-%m-%dT%H")
                hours[u["name"]][k] = max(hours[u["name"]][k], mw)
                t += dt.timedelta(hours=1)
    if not hours:
        return None
    gwh, days, units, month = defaultdict(float), defaultdict(set), defaultdict(set), defaultdict(float)
    for name, hh in hours.items():
        for k, mw in hh.items():
            y = int(k[:4]); gwh[y] += mw / 1000; days[y].add(k[:10]); units[y].add(name); month[k[:7]] += mw / 1000
    first = min(k for hh in hours.values() for k in hh)[:10]
    top = sorted(((n, sum(hh.values()) / 1000) for n, hh in hours.items()), key=lambda z: -z[1])[:3]
    return {"years": {y: [round(gwh[y]), len(days[y]), len(units[y])] for y in sorted(gwh)}, "first": first,
            "monthly": [[m, round(v)] for m, v in sorted(month.items())], "top": [[n, round(v)] for n, v in top]}


consequences = {}
for key, fn in [("vigieau", csq_vigieau), ("lakes", csq_lakes), ("nappes", csq_nappes), ("fires", csq_fires), ("snow", csq_snow), ("nuclear", csq_nuclear)]:
    try:
        consequences[key] = fn()
    except Exception as e:  # noqa: BLE001 — une source absente ne bloque pas la page
        print(f"  conséquences/{key} : {e}")
        consequences[key] = None

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
    "csq": consequences,
}

# ================================================================== climat2.js (sections chargées à la demande)
import gzip  # noqa: E402

PIC, DEB, FIN = 25.3, 23.4, 22.4  # seuils Météo-France de l'indicateur thermique national (°C)


def build_daily():
    h = load("daily_hist.json.gz") if False else None
    p = os.path.join(SRC, "daily_hist.json.gz")
    if not os.path.exists(p):
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        h = json.load(fh)
    l = load("daily_latest.json", {"start": None, "st": {}})
    s0 = dt.date.fromisoformat(h["start"])
    nh = (dt.date.fromisoformat(h["end"]) - s0).days + 1
    nl = len(next(iter(l["st"].values()))) if l["st"] else 0
    ids = sorted(set(h["st"]) | set(l["st"]))
    ser = {i: (h["st"].get(i) or [None] * nh)[:nh] + (l["st"].get(i) or [None] * nl) for i in ids}
    n = nh + nl
    # dernier jour avec au moins 25 stations (le jour en cours est souvent incomplet)
    while n and sum(1 for i in ids if ser[i][n - 1] is not None) < 25:
        n -= 1
    dates = [s0 + dt.timedelta(days=k) for k in range(n)]
    doy = [min(d.timetuple().tm_yday, 365) - 1 for d in dates]
    clim = {}
    for i in ids:
        acc = [[0.0, 0] for _ in range(365)]
        for k in range(n):
            if 1991 <= dates[k].year <= 2020 and ser[i][k] is not None:
                for o in range(-15, 16):
                    a = acc[(doy[k] + o) % 365]
                    a[0] += ser[i][k]; a[1] += 1
        clim[i] = [a[0] / a[1] / 10 if a[1] else None for a in acc]
    cbar = [mean([clim[i][d] for i in ids]) for d in range(365)]
    itn, anom = [None] * n, [None] * n
    for k in range(n):
        an = [ser[i][k] / 10 - clim[i][doy[k]] for i in ids if ser[i][k] is not None and clim[i][doy[k]] is not None]
        if len(an) >= 15:
            anom[k] = sum(an) / len(an)
            itn[k] = cbar[doy[k]] + anom[k]
    # vagues de chaleur : pic ≥ 25,3 ; extension tant que l'indicateur ne passe pas sous 22,4
    # ni sous 23,4 trois jours de suite ; au moins 3 jours ≥ 23,4
    v = lambda k: itn[k] if 0 <= k < n and itn[k] is not None else -99  # noqa: E731
    stop = lambda k, st_: v(k) < FIN or all(v(k + st_ * j) < DEB for j in range(3))  # noqa: E731
    eps = []
    for pk in (k for k in range(n) if v(k) >= PIC):
        if eps and eps[-1][0] <= pk <= eps[-1][1]:
            continue
        a = pk
        while not stop(a - 1, -1):
            a -= 1
        b = pk
        while not stop(b + 1, 1):
            b += 1
        while v(a) < DEB:
            a += 1
        while v(b) < DEB:
            b -= 1
        if eps and a <= eps[-1][1] + 1:
            eps[-1][1] = max(eps[-1][1], b)
        else:
            eps.append([a, b])
    waves = []
    for a, b in eps:
        if b - a + 1 < 3:
            continue
        vals = [v(k) for k in range(a, b + 1)]
        waves.append({"start": dates[a].isoformat(), "end": dates[b].isoformat(), "days": b - a + 1, "max": r1(max(vals)),
                      "maxDay": dates[a + vals.index(max(vals))].isoformat(), "sev": r1(sum(max(0, z - DEB) for z in vals), 1),
                      "ongoing": b == n - 1})
    # degrés-jours (base 18 °C), station par station, jours manquants comblés par climatologie + anomalie du jour
    dju = {}
    for y in range(1950, dates[-1].year + 1):
        ks = [k for k in range(n) if dates[k].year == y]
        if len(ks) < 365 and y != dates[-1].year:
            continue
        hd, cd = [], []
        for i in ids:
            hh = cc = 0.0
            for k in ks:
                t = ser[i][k] / 10 if ser[i][k] is not None else (clim[i][doy[k]] + anom[k] if clim[i][doy[k]] is not None and anom[k] is not None else None)
                if t is None:
                    continue
                hh += max(0.0, 18 - t); cc += max(0.0, t - 18)
            hd.append(hh); cd.append(cc)
        dju[y] = [round(mean(hd)), round(mean(cd)), len(ks)]
    # été de l'année en cours, jour par jour, avec normale et extrêmes 1991-2020 du même jour
    cy = dates[-1].year
    summer = []
    for k in range(n):
        d = dates[k]
        if d.year == cy and 5 <= d.month <= 9:
            ref = [itn[j] for j in range(n) if 1991 <= dates[j].year <= 2020 and doy[j] == doy[k] and itn[j] is not None]
            summer.append([d.isoformat(), r1(itn[k]), r1(mean(ref)), r1(max(ref)) if ref else None])
    yearly = {}
    for w in waves:
        d0, d1 = dt.date.fromisoformat(w["start"]), dt.date.fromisoformat(w["end"])
        yearly[d0.year] = yearly.get(d0.year, 0) + (d1 - d0).days + 1
    return {"waves": waves, "daysByYear": yearly, "dju": dju, "summer": summer, "lastDay": dates[-1].isoformat(), "nStations": len(ids),
            "top": [[dates[k].isoformat(), r1(itn[k])] for k in sorted((k for k in range(n) if itn[k] is not None), key=lambda k: -itn[k])[:10]]}


def build_world():
    w = load("world.json", {})
    out = {}
    if w.get("co2"):
        out["co2"] = [[y, m, v] for y, m, v in w["co2"]]
    g = (w.get("global") or {}).get("monthly", {})
    if g:
        ann = {}
        for ym, v in g.items():
            ann.setdefault(int(ym[:4]), []).append(v)
        ann = {y: sum(v) / 12 for y, v in ann.items() if len(v) == 12 and None not in v}
        base = mean([ann.get(y) for y in range(1991, 2021)])
        glob = {y: v - base for y, v in sorted(ann.items())}
        fr = {int(y): v for y, v in ann_tm_fr.items()}
        common = [y for y in glob if y in fr]
        out["global"] = {"source": w["global"]["source"], "annual": {y: r1(v) for y, v in glob.items()},
                         "trend1961": r1(trend_dec(glob, 1961, max(common))), "trend1900": r1(trend_dec(glob, 1900, max(common))),
                         "early": r1(mean([glob.get(y) for y in range(1900, 1931)])), "recent10": r1(mean([glob.get(y) for y in range(max(common) - 9, max(common) + 1)])),
                         "lastFull": max(glob)}
        cur = sorted(k for k in g if k.startswith(str(NOW_UTC.year)) and g[k] is not None)
        if cur:
            out["global"]["ytd"] = [len(cur), r1(sum(g[k] for k in cur) / len(cur) - base)]
    return out


def build_sst():
    s = load("sst.json", {})
    m = s.get("monthly", {})
    if not m:
        return None
    clim = {mo: mean([v for k, v in m.items() if int(k[:4]) in range(1991, 2021) and int(k[5:]) == mo]) for mo in range(1, 13)}
    anom = {k: v - clim[int(k[5:])] for k, v in m.items()}
    yrs = sorted({int(k[:4]) for k in m})
    annual = {y: mean([anom.get(f"{y}-{mo:02d}") for mo in range(1, 13)]) for y in yrs if all(f"{y}-{mo:02d}" in m for mo in range(1, 13))}
    summer = {y: mean([m.get(f"{y}-{mo:02d}") for mo in (7, 8)]) for y in yrs if all(f"{y}-{mo:02d}" in m for mo in (7, 8))}
    last = max(m)
    return {"box": s.get("box"), "clim": {mo: r1(v) for mo, v in clim.items()},
            "monthly": [[k, r1(v), r1(anom[k])] for k, v in sorted(m.items()) if k >= "1982-01"],
            "annual": {y: r1(v) for y, v in annual.items()}, "summer": {y: r1(v) for y, v in summer.items()},
            "trend1982": r1(trend_dec(annual, 1982, max(annual))), "trend1900": r1(trend_dec(annual, 1900, max(annual))), "last": last}


def simplify(ring, tol=.02):
    if len(ring) < 8:
        return ring
    keep = [False] * len(ring); keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = ring[a], ring[b]
        dmax, idx = 0, None
        for i in range(a + 1, b):
            x0, y0 = ring[i]
            den = math.hypot(x2 - x1, y2 - y1)
            d = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1) / den if den else math.hypot(x0 - x1, y0 - y1)
            if d > dmax:
                dmax, idx = d, i
        if idx is not None and dmax > tol:
            keep[idx] = True; stack += [(a, idx), (idx, b)]
    return [[round(p[0], 3), round(p[1], 3)] for p, k in zip(ring, keep) if k]


def wind(ring, outer):
    """d3 (géométrie sphérique) attend l'anneau extérieur dans le sens horaire, les trous dans l'autre sens."""
    area = sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1))
    return ring[::-1] if (area > 0) == outer else ring


def build_deps_geo():
    p = os.path.join(RAW, "eau_feux_air", "departements.json")
    if not os.path.exists(p):
        return None
    g = json.load(open(p, encoding="utf-8"))
    feats = []
    for f in g["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        polys = [[wind(simplify(r), j == 0) for j, r in enumerate(poly)] for poly in polys]
        polys = [poly for poly in polys if len(poly[0]) >= 4]
        feats.append({"type": "Feature", "properties": {"c": f["properties"]["code"], "n": f["properties"]["nom"]},
                      "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    return {"type": "FeatureCollection", "features": feats}


def build_catnat():
    c = load("catnat.json", {})
    if not c:
        return None
    main = ["ICB", "SEC", "MVT", "TMP"]
    years = {}
    for y, t in c["years"].items():
        if "1982" <= y <= str(NOW_UTC.year):
            years[int(y)] = [t.get(k, 0) for k in main] + [sum(v for k, v in t.items() if k not in main)]
    deps = {}
    for dep, t in c["deps"].items():
        if len(dep) != 2:
            continue  # outre-mer : hors carte
        deps[dep] = {k: {int(y): n for y, n in t.get(k, {}).items()} for k in ("SEC", "ICB")}
    return {"types": main + ["autres"], "labels": {k: c["labels"].get(k, k) for k in main}, "years": years, "deps": deps, "source": c.get("source")}


HEALTH = {  # Santé publique France, « Chaleur et santé. Bilan de l'été 2025 », tableau 4 (édition du 26 février 2026)
    "attrib": {2017: [3354, 733], 2018: [4166, 1277], 2019: [4441, 1879], 2020: [4329, 1531], 2021: [1927, 88],
               2022: [6969, 2051], 2023: [5167, 1523], 2024: [3711, 684], 2025: [5722, 1918]},
    "excess": [[2003, 14802, "Inserm (Hémon et Jougla, 2004), du 1er au 20 août"], [2006, 2065, "InVS (Fouillet et al., 2008), canicule de juillet"]],
}

import math  # noqa: E402

BIO_FACTS = {  # valeurs publiées, citées telles quelles (pas de données ouvertes en série)
    "cti": [{"label": "Oiseaux, France 1989-2006", "community": 91, "temperature": 273,
             "ref": "Devictor et al., « Birds are tracking climate warming, but not fast enough », Proc. R. Soc. B 275, 2008"},
            {"label": "Oiseaux, Europe 1990-2008", "community": 37, "temperature": 249,
             "ref": "Devictor et al., « Differences in the climatic debts of birds and butterflies at a continental scale », Nature Climate Change 2, 2012"},
            {"label": "Papillons, Europe 1990-2008", "community": 114, "temperature": 249, "ref": "idem"}],
    "redlist": {"evaluated": 13842, "threatened": 2430, "extinct": 187, "date": "mars 2021",
                "ref": "Comité français de l'UICN et PatriNat (OFB-CNRS-MNHN), « La Liste rouge des espèces menacées en France : 13 ans de résultats », 2021",
                "threatened2024": 2857, "unprotected2024": 56, "ref2024": "Comité français de l'UICN, analyse de la couverture de protection des espèces menacées, juin 2024"},
    "bats": {"decline": 43, "period": "2006-2021", "ref": "Note nationale BSV « Chauves-souris » 2025 (MNHN, DGAL), d'après le programme Vigie-Chiro"},
}
ann_tm_fr = ann[TM]
data2 = {"world": build_world(), "heat": build_daily(), "sst": build_sst(), "catnat": build_catnat(), "geo": build_deps_geo(),
         "mosquito": load("moustique.json", None), "health": HEALTH,
         "bio": {**{k: v for k, v in load("bio.json", {}).items() if k in ("birds",)},
                 "enaf": {k: v for k, v in (load("bio.json", {}).get("enaf") or {}).items() if k != "source"}, "facts": BIO_FACTS}}
OUT2 = os.path.join(os.path.dirname(OUT), "climat2.js")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("window.CLIMAT=")
    json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    fh.write(";")
with open(OUT2, "w", encoding="utf-8") as fh:
    fh.write("window.CLIMAT2=")
    json.dump(data2, fh, ensure_ascii=False, separators=(",", ":"))
    fh.write(";")
digest = hashlib.sha1(open(OUT, "rb").read()).hexdigest()[:10]
digest2 = hashlib.sha1(open(OUT2, "rb").read()).hexdigest()[:10]
page = os.path.join(os.path.dirname(OUT), "climat.html")
if os.path.exists(page):
    src = open(page, encoding="utf-8").read()
    new = re.sub(r'src="climat\.js(\?v=[\w-]+)?"', f'src="climat.js?v={digest}"', src)
    new = re.sub(r'"climat2\.js(\?v=[\w-]+)?"', f'"climat2.js?v={digest2}"', new)
    if new != src:
        open(page, "w", encoding="utf-8").write(new)
print(f"{OUT} : {os.path.getsize(OUT) // 1024} Ko · {len(stations)} stations · France {tm_years[0]}–{last_full}"
      f" · {last_full} : {ann[TM][last_full]:+.2f} °C (rang {nat_out['rankLast']}/{len(ranked)}) · tendance 1961→ {nat_out['trend1961']} °C/déc.")
