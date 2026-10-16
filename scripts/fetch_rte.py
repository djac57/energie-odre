"""Récupère les données de pilotage RTE (portail data.rte-france.com) et les fusionne dans data/raw/rte/.

API utilisées (données publiques, réutilisables avec mention de la source — CGU du portail, art. 9) :
- Actual Generation v1 : production réalisée par filière et par groupe (pas horaire) ;
- Generation Installed Capacities v1 : groupes de production et puissances installées ;
- Unavailability Additional Information v7 : indisponibilités des moyens de production ;
- Generation Forecast v3 : prévisions éolien / solaire (J-1, intrajournalier, « current ») ;
- Consumption v1 (short_term) : consommation réalisée et prévue (J-1, intrajournalier) ;
- Balancing Energy v5 : prix et volumes d'ajustement, déséquilibre.
L'API Wholesale Market (données EPEX SPOT / Nord Pool, non redistribuables) n'est volontairement pas utilisée.

Les séries sont stockées agrégées au pas horaire (clés UTC « AAAA-MM-JJTHH »), fusionnées à chaque passage
pour accumuler l'historique au-delà de la profondeur de l'API, puis élaguées (fenêtres glissantes).

Sans identifiants (RTE_CLIENT_ID / RTE_CLIENT_SECRET) ou si une API est en panne, le script le signale et sort
avec le code 0 : les données déjà versionnées sont conservées.

    python3 scripts/fetch_rte.py data/raw
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from rte_api import RTE, MissingCredentials  # noqa: E402

RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT = os.path.join(RAW, "rte")
UTC = dt.timezone.utc
PARIS = ZoneInfo("Europe/Paris")
NOW = dt.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
KEEP_HOURLY_DAYS = 120   # séries horaires (prévu/réalisé, ajustement, filières)
KEEP_UNIT_HOURLY_DAYS = 14  # courbe récente par groupe
KEEP_UNIT_DAILY_DAYS = 3 * 366
KEEP_UNAV_ENDED_DAYS = 2 * 366


def path(name):
    return os.path.join(OUT, name)


def load(name, default):
    try:
        with open(path(name), encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(name, data):
    tmp = path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        # une valeur par ligne (indent=0) : les mises à jour horaires ne modifient que quelques lignes dans git
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True, indent=0)
    os.replace(tmp, path(name))


def parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def hkey(d):
    return d.strftime("%Y-%m-%dT%H")


def day_floor(d):
    """Minuit (heure de Paris) du jour contenant d : les API RTE travaillent par journées entières."""
    loc = d.astimezone(PARIS)
    return dt.datetime(loc.year, loc.month, loc.day, tzinfo=PARIS)


def windows(start, end, days):
    """Découpe [start, end[ en journées entières (heure de Paris), au plus `days` jours par appel.
    Avec zoneinfo, minuit + n jours reste minuit, y compris aux changements d'heure."""
    cur, end = day_floor(start), day_floor(end)
    if end <= cur:
        end = cur + dt.timedelta(days=1)
    while cur < end:
        nxt = min(end, cur + dt.timedelta(days=days))
        yield cur, nxt
        cur = nxt


def local_iso(d):
    return d.astimezone(PARIS).isoformat(timespec="seconds")


def hourly_mean(values, key="value"):
    """Liste de pas (start_date, valeur) → moyenne par heure UTC."""
    acc = defaultdict(list)
    for v in values:
        x = v.get(key)
        if x is not None:
            acc[hkey(parse(v["start_date"]))].append(float(x))
    return {k: round(sum(a) / len(a), 1) for k, a in acc.items()}


def prune(series, days):
    cut = hkey(NOW - dt.timedelta(days=days))
    return {k: v for k, v in series.items() if k >= cut}


def last_key(series):
    return max(series) if series else None


def since(series, default_days, max_days=None):
    """Début de la récupération : dernière heure connue (moins une marge) ou historique initial."""
    lk = last_key(series)
    if lk:
        start = dt.datetime.strptime(lk, "%Y-%m-%dT%H").replace(tzinfo=UTC) - dt.timedelta(hours=26)
    else:
        start = NOW - dt.timedelta(days=default_days)
    if max_days:
        start = max(start, NOW - dt.timedelta(days=max_days))
    return start


# ---------------------------------------------------------------- étapes

def step_capacities(api):
    """Groupes ≥ 1 MW et capacités par filière : une fois par jour suffit (recommandation RTE)."""
    meta = load("capacities_units.json", {})
    if meta and NOW.hour != 3 and meta.get("_fetched", "")[:10] == NOW.strftime("%Y-%m-%d"):
        return "à jour"
    units = {}
    for u in api.get("generation_installed_capacities/v1/capacities_per_production_unit")["capacities_per_production_unit"]:
        pu, v = u["production_unit"], u["values"][-1]
        units[pu["code_eic"]] = {"name": pu["name"].strip(), "type": v["type"], "mw": v["installed_capacity"],
                                 "kv": v.get("voltage_level_connection"), "owner": (pu.get("name_producteur") or "").strip(), "since": v["start_date"][:10]}
    types = api.get("generation_installed_capacities/v1/capacities_per_production_type")["capacities_per_production_type"]
    by_type = {t["type"]: t["value"] for blk in types for t in blk["values"]}
    save("capacities_units.json", {"_fetched": NOW.isoformat(), "units": units, "types": by_type})
    return f"{len(units)} groupes"


def step_actual_units(api):
    """Production réalisée par groupe (pas horaire), 7 jours max par appel."""
    hourly = load("unit_hourly.json", {})  # eic_g → {name, type, h: {heure: MW}}
    daily = load("unit_daily.json", {})    # eic_g → {jour UTC: MWh}
    known = [k for u in hourly.values() for k in u.get("h", {})]
    start = since({k: 1 for k in known}, KEEP_UNIT_HOURLY_DAYS, KEEP_UNIT_HOURLY_DAYS)
    n = 0
    for a, b in windows(start, NOW + dt.timedelta(days=1), 7):
        res = api.get("actual_generation/v1/actual_generations_per_unit", {"start_date": local_iso(a), "end_date": local_iso(b)})
        for u in res.get("actual_generations_per_unit", []):
            unit, e = u["unit"], u["unit"]["eic_code"]
            rec = hourly.setdefault(e, {"h": {}})
            rec["name"], rec["type"] = unit["name"].strip(), unit["production_type"]
            for v in u["values"]:
                if v.get("value") is not None:
                    rec["h"][hkey(parse(v["start_date"]))] = v["value"]; n += 1
    # cumul journalier (UTC) recalculé sur les heures connues, conservé au-delà de la fenêtre horaire
    for e, rec in hourly.items():
        days = defaultdict(float)
        for k, v in rec["h"].items():
            days[k[:10]] += v
        d = daily.setdefault(e, {})
        for day, mwh in days.items():
            d[day] = round(mwh)
        rec["h"] = prune(rec["h"], KEEP_UNIT_HOURLY_DAYS)
    cut = (NOW - dt.timedelta(days=KEEP_UNIT_DAILY_DAYS)).strftime("%Y-%m-%d")
    daily = {e: {k: v for k, v in d.items() if k >= cut} for e, d in daily.items()}
    save("unit_hourly.json", hourly); save("unit_daily.json", daily)
    return f"{n} valeurs horaires, {len(hourly)} groupes"


def step_actual_types(api):
    """Production réalisée par filière (pas horaire), 155 jours max par appel."""
    series = load("type_hourly.json", {})
    start = since(series.get("TOTAL", {}), KEEP_HOURLY_DAYS, KEEP_HOURLY_DAYS)
    for a, b in windows(start, NOW + dt.timedelta(days=1), 155):
        res = api.get("actual_generation/v1/actual_generations_per_production_type", {"start_date": local_iso(a), "end_date": local_iso(b)})
        for blk in res.get("actual_generations_per_production_type", []):
            series.setdefault(blk["production_type"], {}).update(hourly_mean(blk["values"]))
    series = {k: prune(v, KEEP_HOURLY_DAYS) for k, v in series.items()}
    save("type_hourly.json", series)
    return f"{len(series)} filières"


def step_unavailabilities(api):
    """Indisponibilités des groupes : dernière version de chaque événement, fusionnée par identifiant."""
    store = load("unavailabilities.json", {})
    if not store or NOW.hour == 3:
        # Historique / resynchronisation quotidienne : tous les événements chevauchant [J-365, J+400]
        params = {"date_type": "EVENT_DATE", "last_version": "true",
                  "start_date": (NOW - dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end_date": (NOW + dt.timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    else:
        params = {"date_type": "PUBLICATION_DATE", "last_version": "true",
                  "start_date": (NOW - dt.timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end_date": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}  # une date de publication ne peut pas être future
    pages = api.get("unavailability_additional_information/v7/generation_unavailabilities", params)
    pages = pages if isinstance(pages, list) else [pages]
    n = 0
    for p in pages:
        for u in p.get("generation_unavailabilities", []):
            ident, ver = u["identifier"], u.get("version", 0)
            if u.get("event_status") == "DISMISSED":  # déclaration annulée par l'exploitant
                store.pop(ident, None)
                continue
            if ident in store and store[ident]["v"] > ver:
                continue
            store[ident] = {"v": ver, "eic": u["affected_asset_or_unit_eic_code"], "name": u["affected_asset_or_unit_name"].strip(),
                            "fuel": u.get("fuel_type"), "mw": u.get("affected_asset_or_unit_installed_capacity"),
                            "kind": u.get("unavailability_type"), "reason": u.get("reason"), "status": u.get("event_status"),
                            "remarks": ("" if (u.get("remarks") or "").strip() in ("-", "–") else u["remarks"].strip()[:300]), "start": u["start_date"], "end": u.get("end_date"),
                            "pub": u.get("publication_date"),
                            "steps": [[s["start_date"], s.get("end_date"), s.get("unavailable_capacity")] for s in u.get("values", [])]}
            n += 1
    cut = (NOW - dt.timedelta(days=KEEP_UNAV_ENDED_DAYS)).isoformat()
    store = {k: v for k, v in store.items() if (v.get("end") or "9999") >= cut[:19]}
    save("unavailabilities.json", store)
    return f"{n} versions reçues, {len(store)} événements conservés"


def step_consumption(api):
    """Consommation réalisée et prévue (J-1, intrajournalier), 186 jours max par appel."""
    fc = load("forecasts.json", {})
    conso = fc.setdefault("consumption", {})
    start = since(conso.get("REALISED", {}), KEEP_HOURLY_DAYS, KEEP_HOURLY_DAYS)
    for a, b in windows(start, NOW + dt.timedelta(days=1), 186):
        res = api.get("consumption/v1/short_term", {"type": "REALISED,ID,D-1", "start_date": local_iso(a), "end_date": local_iso(b)})
        for blk in res.get("short_term", []):
            conso.setdefault(blk["type"], {}).update(hourly_mean(blk["values"]))
    fc["consumption"] = {k: prune(v, KEEP_HOURLY_DAYS) for k, v in conso.items()}
    save("forecasts.json", fc)
    return f"{len(conso.get('REALISED', {}))} heures réalisées"


def step_generation_forecast(api):
    """Prévisions éolien / solaire : RTE recommande peu d'appels par jour → seulement à certaines heures."""
    fc = load("forecasts.json", {})
    gen = fc.setdefault("generation", {})
    have = gen.get("D-1|SOLAR", {})
    if have and NOW.hour not in (6, 16, 20):
        return "hors créneau (prévisions publiées vers 7 h, 17 h et 19 h 30)"
    start = since(have, KEEP_HOURLY_DAYS, KEEP_HOURLY_DAYS)
    for a, b in windows(start, NOW + dt.timedelta(days=1), 21):
        res = api.get("generation_forecast/v3/forecasts", {"production_type": "SOLAR,WIND_ONSHORE,WIND_OFFSHORE", "type": "D-1,ID,CURRENT",
                                                          "start_date": local_iso(a), "end_date": local_iso(b)})
        for blk in res.get("forecasts", []):
            gen.setdefault(f"{blk['type']}|{blk['production_type']}", {}).update(hourly_mean(blk["values"]))
    fc["generation"] = {k: prune(v, KEEP_HOURLY_DAYS) for k, v in gen.items()}
    save("forecasts.json", fc)
    return f"{len(gen)} séries"


def step_balancing(api):
    """Ajustement : prix, volumes activés par produit, déséquilibre ; 1 mois max par appel (JSON)."""
    be = load("balancing.json", {})
    for res_name, key in [("prices", "prices"), ("volumes_per_energy_type", "energy_type"), ("imbalance_data", "imbalance_data")]:
        ser = be.setdefault(res_name, {})
        start = since(ser, KEEP_HOURLY_DAYS, KEEP_HOURLY_DAYS)
        for a, b in windows(start, NOW + dt.timedelta(days=1), 28):
            res = api.get(f"balancing_energy/v5/{res_name}", {"start_date": local_iso(a), "end_date": local_iso(b)})
            for blk in res.get(key, []):
                acc = defaultdict(lambda: defaultdict(list))
                for v in blk.get("values", []):
                    h = hkey(parse(v["start_date"]))
                    for f, x in v.items():
                        if f not in BALANCING_FIELDS and f != "system_trend":
                            continue
                        if isinstance(x, (int, float)) and not isinstance(x, bool):
                            acc[h][f].append(float(x))
                        elif f == "system_trend" and x:
                            acc[h]["trend_up"].append(1.0 if x == "HAUSSE" else 0.0)
                for h, fields in acc.items():
                    ser[h] = {BALANCING_FIELDS.get(f, f): round(sum(a) / len(a), 1) for f, a in fields.items()}
        be[res_name] = prune(ser, KEEP_HOURLY_DAYS)
    save("balancing.json", be)
    return ", ".join(f"{k}: {len(v)} h" for k, v in be.items())


# Champs conservés (nom court) : prix marginaux / moyens, volumes par produit, déséquilibre et prix de règlement
BALANCING_FIELDS = {
    "upward_marginal_price": "up_mp", "downward_marginal_price": "dn_mp", "upward_weighted_average_price": "up_wap", "downward_weighted_average_price": "dn_wap",
    "upward_volume_fcr": "up_fcr", "downward_volume_fcr": "dn_fcr", "upward_volume_afrr": "up_afrr", "downward_volume_afrr": "dn_afrr",
    "upward_volume_mfrr": "up_mfrr", "downward_volume_mfrr": "dn_mfrr", "upward_volume_rr": "up_rr", "downward_volume_rr": "dn_rr",
    "imbalance": "imb", "positive_imbalance_settlement_price": "pos_p", "negative_imbalance_settlement_price": "neg_p",
}

def step_water_reserves(api):
    """Stocks hydrauliques hebdomadaires (MWh), publiés le mercredi ; 366 jours max par appel."""
    wr = load("water_reserves.json", {})
    if wr and NOW.hour != 3:
        return "à jour (une fois par jour)"
    # Semaines calendaires : début et fin doivent tomber un lundi à minuit (heure de Paris)
    monday = day_floor(NOW) - dt.timedelta(days=day_floor(NOW).weekday())
    start = monday - dt.timedelta(weeks=156 if not wr else 6)
    while start < monday:
        end = min(monday, start + dt.timedelta(weeks=52))
        res = api.get("actual_generation/v1/water_reserves", {"start_date": local_iso(start), "end_date": local_iso(end)})
        blk = res.get("water_reserves", {})
        for v in (blk.get("values", []) if isinstance(blk, dict) else [x for b2 in blk for x in b2.get("values", [])]):
            wr[v["start_date"][:10]] = v["value"]
        start = end
    save("water_reserves.json", wr)
    return f"{len(wr)} semaines"


STEPS = [("capacités installées", step_capacities), ("stocks hydrauliques", step_water_reserves), ("production par groupe", step_actual_units), ("production par filière", step_actual_types),
         ("indisponibilités", step_unavailabilities), ("consommation", step_consumption), ("prévisions de production", step_generation_forecast),
         ("ajustement", step_balancing)]


def main():
    os.makedirs(OUT, exist_ok=True)
    api = RTE()
    try:
        api.token()
    except MissingCredentials as e:
        print(f"RTE : {e} — étape ignorée, données versionnées conservées.")
        return 0
    except Exception as e:  # panne du serveur d'authentification
        print(f"RTE : authentification impossible ({type(e).__name__}) — données versionnées conservées.")
        return 0
    failures = 0
    for label, fn in STEPS:
        try:
            print(f"RTE {label} : {fn(api)}")
        except Exception as e:
            failures += 1
            print(f"RTE {label} : échec ({str(e)[:200]}) — données précédentes conservées.")
    meta = load("meta.json", {})
    meta.update({"last_run": NOW.isoformat(), "failures": failures})
    save("meta.json", meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
