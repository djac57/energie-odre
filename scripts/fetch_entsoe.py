"""Données ENTSO-E (plateforme de transparence) ouvertes à la réutilisation libre, fusionnées dans data/raw/entsoe/.

Conditions : ENTSO-E publie la « List of data available for free re-use » (version du 18/10/2023) — données
réutilisables sous licence Creative Commons Attribution 4.0 (CC BY 4.0), avec mention de la source. Seules des
données de cette liste sont récupérées ici :
- n° 18, flux physiques transfrontaliers (Transparency Regulation art. 12.1.g) — documentType A11 ;
- n° 27, prix des écarts (imbalance prices, art. 17.1.g) — documentType A85.
Exclusions de la licence respectées : données des interconnexions IFA (France–Angleterre) et Nemo Link
(Belgique–Angleterre), donc les frontières FR–GB et BE–GB ne sont pas récupérées ; Moldavie et Turquie absentes.
Ne sont PAS dans la liste (donc non publiés) : production réalisée par unité, indisponibilités des unités,
production par filière, prix day-ahead.

Stockage :
- flows_hourly.json : {« A>B »: {heure UTC: MW moyen}} sur 10 jours glissants ;
- flows_daily.json  : {« A>B »: {jour UTC: MW moyen}}, historique depuis 2024 (rempli à reculons, 40 requêtes par passage) ;
- imbalance.json    : {zone: {quart d'heure UTC: €/MWh}} sur 14 jours, + imbalance_daily.json (moyennes journalières, depuis 2024).

Clé : ENTSOE_TOKEN (variable d'environnement ou ~/.config/entsoe/credentials) ; absente → étape ignorée, code 0.

    python3 scripts/fetch_entsoe.py data/raw
"""
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
from entsoe_api import ENTSOE, MissingToken, NoData, period_points  # noqa: E402

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(RAW, "entsoe")
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
KEEP_HOURLY_DAYS = 10
KEEP_IMB_DAYS = 14
HISTORY_FROM = dt.datetime(2024, 1, 1, tzinfo=UTC)
WORKERS = 8  # requêtes simultanées
BACKFILL_PER_RUN = 40  # requêtes de rattrapage d'historique par passage (≈ 14 passages par fenêtre de 3 mois)

# Zones (pays ou zones de contrôle) — codes EIC
Z = {"FR": "10YFR-RTE------C", "BE": "10YBE----------2", "NL": "10YNL----------L", "DE": "10Y1001A1001A83F", "LU": "10YLU-CEGEDEL-NQ",
     "CH": "10YCH-SWISSGRIDZ", "AT": "10YAT-APG------L", "IT": "10YIT-GRTN-----B", "ES": "10YES-REE------0", "PT": "10YPT-REN------W",
     "GB": "10YGB----------A", "IE": "10Y1001A1001A59C", "DK": "10Y1001A1001A65H", "NO": "10YNO-0--------C", "SE": "10YSE-1--------K",
     "FI": "10YFI-1--------U", "PL": "10YPL-AREA-----S", "CZ": "10YCZ-CEPS-----N", "SK": "10YSK-SEPS-----K", "HU": "10YHU-MAVIR----U",
     "SI": "10YSI-ELES-----O", "HR": "10YHR-HEP------M", "RO": "10YRO-TEL------P", "BG": "10YCA-BULGARIA-R", "RS": "10YCS-SERBIATSOV",
     "BA": "10YBA-JPCC-----D", "ME": "10YCS-CG-TSO---S", "MK": "10YMK-MEPSO----8", "AL": "10YAL-KESH-----5", "GR": "10YGR-HTSO-----Y",
     "EE": "10Y1001A1001A39I", "LV": "10YLV-1001A00074", "LT": "10YLT-1001A0008Q"}
# Frontières (FR–GB et BE–GB exclues : données des interconnexions IFA et Nemo Link hors licence ouverte)
BORDERS = ["FR-BE", "FR-DE", "FR-CH", "FR-IT", "FR-ES", "BE-NL", "BE-DE", "BE-LU", "NL-DE", "NL-GB", "NL-NO", "NL-DK",
           "DE-DK", "DE-LU", "DE-CH", "DE-AT", "DE-CZ", "DE-PL", "DE-SE", "DE-NO", "CH-AT", "CH-IT", "AT-CZ", "AT-HU", "AT-SI", "AT-IT",
           "IT-SI", "IT-GR", "IT-ME", "ES-PT", "GB-IE", "GB-NO", "GB-DK", "DK-SE", "DK-NO", "NO-SE", "NO-FI", "SE-FI", "SE-PL", "SE-LT",
           "FI-EE", "PL-CZ", "PL-SK", "PL-LT", "CZ-SK", "SK-HU", "HU-RO", "HU-RS", "HU-HR", "HU-SI", "SI-HR", "HR-RS", "HR-BA",
           "RO-RS", "RO-BG", "BG-RS", "BG-MK", "BG-GR", "RS-MK", "RS-ME", "RS-BA", "RS-AL", "BA-ME", "ME-AL", "MK-GR", "MK-AL", "AL-GR",
           "EE-LV", "LV-LT"]
# Prix des écarts : zones testées (une zone sans données est simplement ignorée)
IMB = {"FR": "10YFR-RTE------C", "BE": "10YBE----------2", "NL": "10YNL----------L", "DE": "10YDE-RWENET---I", "CH": "10YCH-SWISSGRIDZ",
       "AT": "10YAT-APG------L", "ES": "10YES-REE------0", "PT": "10YPT-REN------W"}  # zones en euros
FMT = "%Y%m%d%H%M"


def path(name):
    return os.path.join(OUT, name)


def load(name, default):
    try:
        with open(path(name), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save(name, data):
    tmp = path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True, indent=0)
    os.replace(tmp, path(name))


def directions():
    for b in BORDERS:
        a, c = b.split("-")
        yield a, c
        yield c, a


def flow_series(api, a, b, start, end):
    """Flux physique a → b (MW) au pas d'origine, moyenné par heure UTC."""
    try:
        roots = api.get({"documentType": "A11", "out_Domain": Z[a], "in_Domain": Z[b], "periodStart": start.strftime(FMT), "periodEnd": end.strftime(FMT)})
    except NoData:
        return {}
    acc = defaultdict(list)
    for r in roots:
        for ts in r.iter("TimeSeries"):
            for t, _, p in period_points(ts):
                q = p.find("quantity")
                if q is not None and q.text:
                    acc[t.strftime("%Y-%m-%dT%H")].append(float(q.text))
    return {k: round(sum(v) / len(v), 1) for k, v in acc.items()}


def daily_means(hourly):
    acc = defaultdict(list)
    for k, v in hourly.items():
        acc[k[:10]].append(v)
    return {d: round(sum(v) / len(v), 1) for d, v in acc.items() if len(v) >= 20}


def step_flows(api):
    hourly = load("flows_hourly.json", {})
    daily = load("flows_daily.json", {})
    meta = load("meta.json", {})
    start = NOW - dt.timedelta(days=2)
    have = [k for s in hourly.values() for k in s]
    if not have:
        start = NOW - dt.timedelta(days=KEEP_HOURLY_DAYS)
    def one(ab):
        try:
            return ab, flow_series(api, ab[0], ab[1], start, NOW + dt.timedelta(hours=1)), None
        except Exception as e:
            return ab, {}, str(e)[:100]
    n_ok = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for (a, b), got, err in pool.map(one, list(directions())):
            if err:
                print(f"  flux {a}>{b} : échec ({err})")
            if got:
                n_ok += 1
                hourly.setdefault(f"{a}>{b}", {}).update(got)
    cut = (NOW - dt.timedelta(days=KEEP_HOURLY_DAYS)).strftime("%Y-%m-%dT%H")
    hourly = {k: {h: v for h, v in s.items() if h >= cut} for k, s in hourly.items()}
    for key, s in hourly.items():
        d = daily.setdefault(key, {})
        today = NOW.strftime("%Y-%m-%d")
        for day, v in daily_means(s).items():
            if day < today:
                d[day] = v
    # Rattrapage de l'historique journalier, à reculons, par fenêtres d'environ 3 mois (quelques requêtes par passage)
    bf = meta.get("flows_back") or {"end": (NOW - dt.timedelta(days=KEEP_HOURLY_DAYS - 1)).strftime("%Y-%m-%d"), "done": []}
    done = 0
    while bf["end"] > HISTORY_FROM.strftime("%Y-%m-%d") and done < BACKFILL_PER_RUN:
        end = dt.datetime.strptime(bf["end"], "%Y-%m-%d").replace(tzinfo=UTC)
        w0 = max(HISTORY_FROM, end - dt.timedelta(days=92))
        todo = [(a, b) for a, b in directions() if f"{a}>{b}" not in bf["done"]][:BACKFILL_PER_RUN - done]
        done += len(todo)

        def hist(ab):
            try:
                return ab, flow_series(api, ab[0], ab[1], w0, end), None
            except Exception as e:
                return ab, {}, str(e)[:80]
        with ThreadPoolExecutor(WORKERS) as pool:
            for (a, b), got, err in pool.map(hist, todo):
                if err:
                    print(f"  historique {a}>{b} : échec ({err})")  # on n'insiste pas : fenêtre suivante
                daily.setdefault(f"{a}>{b}", {}).update(daily_means(got))
                bf["done"].append(f"{a}>{b}")
        if len(bf["done"]) >= 2 * len(BORDERS):
            bf = {"end": w0.strftime("%Y-%m-%d"), "done": []}
    meta["flows_back"] = bf
    meta["flows_history_from"] = bf["end"]
    save("flows_hourly.json", hourly); save("flows_daily.json", daily); save("meta.json", meta)
    return f"{n_ok} sens de frontière avec données, historique depuis {meta['flows_history_from']}"


def step_imbalance(api):
    q = load("imbalance.json", {})
    daily = load("imbalance_daily.json", {})
    meta = load("meta.json", {})
    out = []
    for zone, eic in IMB.items():
        ser = q.setdefault(zone, {})
        start = NOW - dt.timedelta(days=2 if ser else KEEP_IMB_DAYS)
        try:
            roots = api.get({"documentType": "A85", "controlArea_Domain": eic, "periodStart": start.strftime(FMT), "periodEnd": (NOW + dt.timedelta(hours=1)).strftime(FMT)})
        except NoData:
            continue
        except Exception as e:
            print(f"  écarts {zone} : échec ({str(e)[:100]})"); continue
        acc = defaultdict(dict)
        for r in roots:
            for ts in r.iter("TimeSeries"):
                for t, _, p in period_points(ts):
                    a, c = p.find("imbalance_Price.amount"), p.find("imbalance_Price.category")
                    if a is not None and a.text:
                        acc[t.strftime("%Y-%m-%dT%H:%M")][c.text if c is not None else "A04"] = float(a.text)
        for k, cats in acc.items():
            # prix unique (France, Belgique…) ou double prix (excédent A04 / déficit A05) : on garde la moyenne et l'écart
            vals = list(cats.values())
            ser[k] = round(vals[0], 2) if len(vals) == 1 else [round(cats.get("A04", vals[0]), 2), round(cats.get("A05", vals[-1]), 2)]
        if acc:
            out.append(zone)
    cut = (NOW - dt.timedelta(days=KEEP_IMB_DAYS)).strftime("%Y-%m-%dT%H:%M")
    for zone, ser in q.items():
        dd = defaultdict(list)
        for k, v in ser.items():
            dd[k[:10]].append(v if not isinstance(v, list) else sum(v) / 2)
        d = daily.setdefault(zone, {})
        for day, v in dd.items():
            if len(v) >= 20 and day < NOW.strftime("%Y-%m-%d"):
                d[day] = round(sum(v) / len(v), 2)
    q = {z: {k: v for k, v in s.items() if k >= cut} for z, s in q.items()}
    # historique journalier des prix des écarts France, à reculons par mois
    back = dt.datetime.strptime(meta.get("imb_history_from") or NOW.strftime("%Y-%m-%d"), "%Y-%m-%d").replace(tzinfo=UTC) - dt.timedelta(days=KEEP_IMB_DAYS - 1 if not meta.get("imb_history_from") else 0)
    for _ in range(4):
        if back <= HISTORY_FROM:
            break
        w0 = max(HISTORY_FROM, back - dt.timedelta(days=31))
        for zone in ("FR", "BE", "NL"):
            try:
                roots = api.get({"documentType": "A85", "controlArea_Domain": IMB[zone], "periodStart": w0.strftime(FMT), "periodEnd": back.strftime(FMT)})
            except NoData:
                continue
            except Exception as e:
                print(f"  historique écarts {zone} : échec ({str(e)[:80]})"); continue
            dd = defaultdict(list)
            for r in roots:
                for ts in r.iter("TimeSeries"):
                    for t, _, p in period_points(ts):
                        a = p.find("imbalance_Price.amount")
                        if a is not None and a.text:
                            dd[t.strftime("%Y-%m-%d")].append(float(a.text))
            daily.setdefault(zone, {}).update({d: round(sum(v) / len(v), 2) for d, v in dd.items() if len(v) >= 20})
        back = w0
    meta["imb_history_from"] = back.strftime("%Y-%m-%d")
    save("imbalance.json", q); save("imbalance_daily.json", daily); save("meta.json", meta)
    return f"zones {', '.join(out) or '—'}, historique journalier depuis {meta['imb_history_from']}"


def main():
    os.makedirs(OUT, exist_ok=True)
    try:
        api = ENTSOE()
    except MissingToken as e:
        print(f"ENTSO-E : {e} — étape ignorée, données versionnées conservées.")
        return 0
    for label, fn in (("flux physiques", step_flows), ("prix des écarts", step_imbalance)):
        try:
            print(f"ENTSO-E {label} : {fn(api)}")
        except Exception as e:
            print(f"ENTSO-E {label} : échec ({type(e).__name__}: {str(e)[:160]}) — données précédentes conservées.")
    meta = load("meta.json", {})
    meta.update({"last_run": NOW.isoformat(), "calls": api.calls,
                 "license": "ENTSO-E Transparency Platform — List of data available for free re-use (18/10/2023), CC BY 4.0"})
    save("meta.json", meta)
    print(f"ENTSO-E : {api.calls} requêtes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
