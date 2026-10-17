"""Récupère les données de pilotage RTE (portail data.rte-france.com) et les fusionne dans data/raw/rte/.

API utilisées (données publiques, réutilisables avec mention de la source — CGU du portail, art. 9) :
- Actual Generation v1 : production réalisée par filière et par groupe (pas horaire) ;
- Generation Installed Capacities v1 : groupes de production et puissances installées ;
- Unavailability Additional Information v7 : indisponibilités des moyens de production ;
- Generation Forecast v3 : prévisions éolien / solaire (J-1, intrajournalier, « current ») ;
- Consumption v1 (short_term) : consommation réalisée et prévue (J-1, intrajournalier) ;
- Balancing Energy v5 : prix et volumes d'ajustement, déséquilibre.
- Actual Generation v1, par groupe : historique journalier des réacteurs nucléaires depuis 2019 (rempli progressivement).
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
    """Stocks hydrauliques hebdomadaires (MWh), publiés le mercredi ; 366 jours max par appel ; historique depuis 2015."""
    wr = load("water_reserves.json", {})
    if wr and NOW.hour != 3:
        return "à jour (une fois par jour)"
    # Semaines calendaires : début et fin doivent tomber un lundi à minuit (heure de Paris)
    monday = day_floor(NOW) - dt.timedelta(days=day_floor(NOW).weekday())
    start = monday - dt.timedelta(weeks=156 if not wr else 6)
    # historique publié depuis début 2015 : rattrapé une fois (comparaison aux années passées)
    first = dt.datetime(2015, 1, 5, tzinfo=PARIS)
    if wr and min(wr) > "2015-01-31":
        start = first
    while start < monday:
        end = min(monday, start + dt.timedelta(weeks=52))
        res = api.get("actual_generation/v1/water_reserves", {"start_date": local_iso(start), "end_date": local_iso(end)})
        blk = res.get("water_reserves", {})
        for v in (blk.get("values", []) if isinstance(blk, dict) else [x for b2 in blk for x in b2.get("values", [])]):
            wr[v["start_date"][:10]] = v["value"]
        start = end
    save("water_reserves.json", wr)
    return f"{len(wr)} semaines"


def step_ecowatt(api):
    """Signal Ecowatt du jour et des 3 jours suivants (quota RTE : 1 appel / 15 min) ; historique accumulé par jour."""
    eco = load("ecowatt.json", {})
    res = api.get("ecowatt/v5/signals")
    for sig in res.get("signals", []):
        day = (sig.get("jour") or "")[:10]
        if not day:
            continue
        hv = [None] * 24
        for v in sig.get("values", []):
            if v.get("pas") is not None and 0 <= int(v["pas"]) < 24:
                hv[int(v["pas"])] = v.get("hvalue")
        eco[day] = {"d": sig.get("dvalue"), "msg": (sig.get("message") or "").strip()[:200], "h": hv, "gen": sig.get("GenerationFichier")}
    save("ecowatt.json", eco)
    return f"{len(eco)} jours"


def step_physical_flows(api):
    """Flux physiques aux frontières (pas de 15 min → horaire), un appel par heure recommandé.
    Séries horaires sur 120 jours + cumuls mensuels (MWh) conservés sans limite."""
    pf = load("physical_flows.json", {"hourly": {}, "monthly": {}})
    hourly, monthly = pf.setdefault("hourly", {}), pf.setdefault("monthly", {})
    known = [k for ser in hourly.values() for k in ser]
    start = since({k: 1 for k in known}, KEEP_HOURLY_DAYS, KEEP_HOURLY_DAYS)
    for a, b in windows(start, NOW + dt.timedelta(days=1), 31):
        res = api.get("physical_flow/v1/physical_flows", {"start_date": local_iso(a), "end_date": local_iso(b)})
        for blk in res.get("physical_flows", []):
            key = f"{blk['sender_country_name']}>{blk['receiver_country_name']}"
            hourly.setdefault(key, {}).update(hourly_mean(blk["values"]))
    # cumuls mensuels (MWh) recalculés sur la fenêtre horaire, seulement pour les mois entièrement couverts
    first_h = min((h for ser in hourly.values() for h in ser), default=None)
    acc = defaultdict(lambda: defaultdict(float))
    for key, ser in hourly.items():
        for h, v in ser.items():
            acc[key][h[:7]] += v
    for key, months in acc.items():
        monthly.setdefault(key, {}).update({m: round(v) for m, v in months.items() if first_h and m > first_h[:7]})
    # Historique mensuel depuis 2015, rempli à rebours par paquets de 12 mois pour rester sobre
    first = min((m for ser in monthly.values() for m in ser), default=NOW.strftime("%Y-%m"))
    y, mo = (int(x) for x in first.split("-"))
    filled = 0
    while (y, mo) > (2015, 1) and filled < 12:
        y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
        a = dt.datetime(y, mo, 1, tzinfo=PARIS)
        b = dt.datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=PARIS)
        res = api.get("physical_flow/v1/physical_flows", {"start_date": local_iso(a), "end_date": local_iso(b)})
        month_tot = defaultdict(float)
        for blk in res.get("physical_flows", []):  # un bloc par jour ; valeurs = puissances moyennes (MW)
            key = f"{blk['sender_country_name']}>{blk['receiver_country_name']}"
            vals = [v["value"] for v in blk["values"] if v.get("value") is not None]
            if vals:
                month_tot[key] += sum(vals) / len(vals) * (parse(blk["end_date"]) - parse(blk["start_date"])).total_seconds() / 3600
        for key, tot in month_tot.items():
            monthly.setdefault(key, {})[f"{y}-{mo:02d}"] = round(tot)
        filled += 1
    pf["hourly"] = {k: prune(v, KEEP_HOURLY_DAYS) for k, v in hourly.items()}
    save("physical_flows.json", pf)
    return f"{len(pf['hourly'])} sens de flux, historique mensuel depuis {min((m for ser in monthly.values() for m in ser), default='—')}"


def step_tempo(api):
    """Couleurs Tempo : publiées vers 10 h 40 pour le lendemain ; historique depuis 2014 (366 jours max par appel)."""
    tempo = load("tempo.json", {})
    tomorrow = (NOW.astimezone(PARIS) + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    if tempo and tomorrow in tempo and NOW.hour != 3:
        return "à jour"
    if not tempo:
        start = dt.datetime(2014, 9, 1, tzinfo=PARIS)
    else:
        start = NOW - dt.timedelta(days=10)
    end = day_floor(NOW) + dt.timedelta(days=2)
    for a, b in windows(start, end, 366):
        res = api.get("tempo_like_supply_contract/v1/tempo_like_calendars", {"start_date": local_iso(a), "end_date": local_iso(b)})
        blk = res.get("tempo_like_calendars", {})
        for v in blk.get("values", []) if isinstance(blk, dict) else []:
            tempo[v["start_date"][:10]] = v["value"]
    save("tempo.json", tempo)
    return f"{len(tempo)} jours"


def step_demand_response(api):
    """Opérateurs d'effacement (NEBEF) : un appel par jour ; on garde un relevé mensuel (nombre, agréments).
    Les volumes d'effacement sont aussi interrogés (souvent vides : publiés en M+1)."""
    dr = load("demand_response.json", {"operators": {}, "volumes": {}})
    month = NOW.astimezone(PARIS).strftime("%Y-%m")
    if month in dr["operators"] and NOW.hour != 3:
        return "à jour"
    ops = api.get("demand_response/v1/operators").get("operators", [])
    flags = ["nebef_rules_recognition", "nebef_rules_qualification", "trial_nebef_rules_agreement", "trial_nebef_rules_qualification"]
    dr["operators"][month] = {"n": len(ops), **{f: sum(1 for o in ops if o.get(f)) for f in flags},
                              "names": sorted({o.get("name", "").strip() for o in ops if o.get("nebef_rules_qualification")})[:80]}
    res = api.get("demand_response/v1/volumes", {"start_date": local_iso(day_floor(NOW) - dt.timedelta(days=40)), "end_date": local_iso(day_floor(NOW))})
    for v in res.get("volumes", []):
        for x in v.get("values", []):
            if x.get("value") is not None:
                dr["volumes"][x["start_date"][:16]] = x["value"]
    save("demand_response.json", dr)
    return f"{len(ops)} opérateurs, {len(dr['volumes'])} pas de volume"


def step_reserves(api):
    """Réserves d'équilibrage (Balancing Capacity v5) : besoin de réserves contractualisées par RTE (FCR, aFRR, mFRR),
    moyenne journalière par produit et sens ; procured_reserves (arrêtée fin 2025) tentée sans bloquer."""
    rv = load("reserves.json", {"need": {}, "procured": {}})
    if rv["need"] and NOW.hour != 21:
        return "hors créneau (une fois par jour, vers 23 h)"
    last = max(rv["need"], default=None)
    start = (dt.datetime.fromisoformat(last).replace(tzinfo=PARIS) - dt.timedelta(days=3)) if last else NOW - dt.timedelta(days=60)
    for a, b in windows(start, NOW, 7):
        res = api.get("balancing_capacity/v5/tso_need_for_procured_reserves", {"start_date": local_iso(a), "end_date": local_iso(b)})
        acc = defaultdict(list)
        for n in res.get("needs", []):
            day = parse(n["start_date"]).astimezone(PARIS).strftime("%Y-%m-%d")
            acc[(day, f"{n.get('reserve')}|{n.get('direction')}|{n.get('time_horizon')}")].append(n.get("volume") or 0)
        for (day, k), vals in acc.items():
            rv["need"].setdefault(day, {})[k] = round(sum(vals) / len(vals))
    try:
        if not rv["procured"]:
            for a, b in windows(dt.datetime(2025, 1, 1, tzinfo=PARIS), dt.datetime(2026, 1, 1, tzinfo=PARIS), 124):
                res = api.get("balancing_capacity/v5/procured_reserves", {"start_date": local_iso(a), "end_date": local_iso(b)})
                for blk in res.get("procured_reserves", []):
                    for v in blk.get("values", []):
                        rv["procured"][v["start_date"][:13]] = {k: x for k, x in v.items() if isinstance(x, (int, float))}
    except Exception as e:
        print(f"RTE réserves contractualisées (procured_reserves) : indisponible ({str(e)[:80]})")
    save("reserves.json", rv)
    return f"{len(rv['need'])} jours de besoins"


def step_nuclear_history(api):
    """Production journalière (MWh, jour de Paris) de chaque réacteur depuis 2019, pour comparer les étés
    (chaleur et débit des rivières). Fusion vers l'avant à chaque passage, puis remontée progressive du passé :
    au plus 8 appels de 7 jours par passage, jusqu'au 1er janvier 2019."""
    hist = load("nuclear_daily.json", {"units": {}, "from": None, "to": None})
    first = dt.datetime(2019, 1, 1, tzinfo=PARIS)
    todo = []
    if hist["to"]:
        todo += list(windows(dt.datetime.fromisoformat(hist["to"]).replace(tzinfo=PARIS) - dt.timedelta(days=2), NOW, 7))
    else:
        todo += list(windows(NOW - dt.timedelta(days=14), NOW, 7))
    if not hist["from"] or dt.datetime.fromisoformat(hist["from"]).replace(tzinfo=PARIS) > first:
        end = dt.datetime.fromisoformat(hist["from"]).replace(tzinfo=PARIS) if hist["from"] else day_floor(NOW - dt.timedelta(days=14))
        back = list(windows(max(first, end - dt.timedelta(days=7 * 8)), end, 7))
        todo += back[::-1]
    try:
        _nuclear_windows(api, hist, todo)
    finally:  # garder ce qui a été reçu même si un appel échoue
        save("nuclear_daily.json", hist)
    return f"{len(todo)} fenêtres, historique du {hist['from']} au {hist['to']}"


def _nuclear_windows(api, hist, todo):
    for a, b in todo:
        res = api.get("actual_generation/v1/actual_generations_per_unit", {"start_date": local_iso(a), "end_date": local_iso(b)})
        for u in res.get("actual_generations_per_unit", []):
            if u["unit"]["production_type"] != "NUCLEAR":
                continue
            rec = hist["units"].setdefault(u["unit"]["eic_code"], {"name": u["unit"]["name"].strip(), "d": {}})
            days = defaultdict(float)
            for v in u["values"]:
                if v.get("value") is not None:
                    days[parse(v["start_date"]).astimezone(PARIS).strftime("%Y-%m-%d")] += v["value"]
            for k, mwh in days.items():
                rec["d"][k] = round(mwh)
        a_s, b_s = a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")
        hist["from"] = min(hist["from"] or a_s, a_s)
        hist["to"] = max(hist["to"] or b_s, b_s)


STEPS = [("capacités installées", step_capacities), ("stocks hydrauliques", step_water_reserves), ("production par groupe", step_actual_units), ("production par filière", step_actual_types),
         ("indisponibilités", step_unavailabilities), ("consommation", step_consumption), ("prévisions de production", step_generation_forecast),
         ("ajustement", step_balancing), ("Ecowatt", step_ecowatt), ("flux physiques", step_physical_flows), ("Tempo", step_tempo),
         ("effacement", step_demand_response), ("réserves", step_reserves),
         ("historique nucléaire", step_nuclear_history)]


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
