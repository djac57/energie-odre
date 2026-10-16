"""Construit site/rte.js à partir des données RTE fusionnées dans data/raw/rte/ (voir fetch_rte.py).

Coordonnées des sites : JRC Open Power Plants Database (JRC-PPDB-OPEN 1.0, 2019, CC BY 4.0), rapprochée par code
EIC (eic_p = unité de production des capacités installées, eic_g = groupe de la production réalisée et des
indisponibilités), complétée par scripts/rte_sites_extra.json (Wikidata CC0, centres de communes geo.api.gouv.fr).

    python3 scripts/build_rte.py data/raw site/rte.js
"""
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(__file__)
RAW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "raw")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "site", "rte.js")
R = os.path.join(RAW, "rte")
UTC = dt.timezone.utc
MIN_MW = 100


def load(name, default=None):
    try:
        with open(os.path.join(R, name), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default if default is not None else {}


def parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def hkey(d):
    return d.strftime("%Y-%m-%dT%H")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def dense(series, h0, n, scale=1):
    """Dictionnaire {heure: valeur} → tableau continu à partir de h0 (None si absent)."""
    out = []
    for i in range(n):
        v = series.get(hkey(h0 + dt.timedelta(hours=i)))
        out.append(None if v is None else round(v * scale) if scale != 1 else (round(v) if abs(v) >= 100 else round(v, 1)))
    return out


# Flux physiques : sens « pays>pays » → export / import mensuels (MWh) par voisin ; l'Angleterre cumule IFA et IFA2
NEIGHBOURS = {"Germany": "Allemagne", "Belgium": "Belgique", "England-IFA": "Royaume-Uni", "England-IFA2": "Royaume-Uni", "England": "Royaume-Uni",
              "Italy": "Italie", "Spain": "Espagne", "Switzerland": "Suisse"}


def physical_monthly(pf):
    out = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for key, months in pf.get("monthly", {}).items():
        a, b = key.split(">")
        if a == "France" and b in NEIGHBOURS:
            idx, nb = 0, NEIGHBOURS[b]
        elif b == "France" and a in NEIGHBOURS:
            idx, nb = 1, NEIGHBOURS[a]
        else:
            continue
        for m, v in months.items():
            out[nb][m][idx] += v
    return {nb: dict(sorted(ms.items())) for nb, ms in out.items()}


def physical_recent(pf, hours=48):
    """Dernières heures de flux physiques par voisin : {voisin: [[heure UTC, export MW, import MW], …]}."""
    keys = sorted({h for ser in pf.get("hourly", {}).values() for h in ser})[-hours:]
    out = defaultdict(lambda: {h: [0, 0] for h in keys})
    for key, ser in pf.get("hourly", {}).items():
        a, b = key.split(">")
        idx, nb = (0, NEIGHBOURS.get(b)) if a == "France" else (1, NEIGHBOURS.get(a)) if b == "France" else (None, None)
        if nb is None:
            continue
        for h in keys:
            if h in ser:
                out[nb][h][idx] += ser[h]
    return {nb: [[h, round(v[0]), round(v[1])] for h, v in sorted(d.items())] for nb, d in out.items()}


def main():
    meta = load("meta.json")
    caps = load("capacities_units.json")
    if not caps:
        print("Pas de données RTE : site/rte.js non généré"); return
    now = parse(meta.get("last_run", dt.datetime.now(UTC).isoformat())) if meta else dt.datetime.now(UTC)
    units = caps["units"]
    jrc = list(csv.DictReader(open(os.path.join(R, "jrc_france_units.csv"), encoding="utf-8")))
    extra = json.load(open(os.path.join(HERE, "rte_sites_extra.json"), encoding="utf-8"))
    p_coord = {}
    for r in jrc:
        p_coord.setdefault(r["eic_p"], (float(r["lat"]), float(r["lon"])))
    g_to_p = {r["eic_g"]: r["eic_p"] for r in jrc}

    # Sites ≥ 100 MW avec coordonnées
    sites = {}
    for eic, u in units.items():
        if (u["mw"] or 0) < MIN_MW:
            continue
        if eic in p_coord:
            lat, lon = p_coord[eic]; prec, src = "site", "JRC-PPDB-OPEN"
        elif u["name"] in extra:
            e = extra[u["name"]]; lat, lon, prec, src = e["lat"], e["lon"], e["precision"], e["source"]
        else:
            lat = lon = None; prec, src = None, None
        sites[eic] = {"id": eic, "name": u["name"], "type": u["type"], "mw": u["mw"], "owner": u["owner"], "kv": u["kv"],
                      "lat": lat, "lon": lon, "prec": prec, "src": src, "groups": []}

    # Groupes (production réalisée) → site
    hourly = load("unit_hourly.json")
    daily = load("unit_daily.json")
    site_by_norm = {norm(s["name"]): k for k, s in sites.items()}

    # Noms de groupes trop différents de celui de leur site entre les deux API
    alias = {"CPCU-STOUEN-GP": "FR_CPCUSO_GP", "CPCU-COGEVITRY–GP": "FR_CPCUCOGEVI_GP", "CPCU-COGEVITRY-GP": "FR_CPCUCOGEVI_GP"}
    by_name = {s["name"]: k for k, s in sites.items()}

    def site_of(eic_g, name):
        p = g_to_p.get(eic_g)
        if p in sites:
            return p
        if name.upper() in alias and alias[name.upper()] in by_name:
            return by_name[alias[name.upper()]]
        n = norm(name)
        best = None
        for sn, k in site_by_norm.items():  # le nom du site est contenu dans celui du groupe (« FECAMP 1 » ⊃ « FECAMP »)
            if sn and (sn in n or n.rstrip("0123456789") == sn) and (best is None or len(sn) > len(best[0])):
                best = (sn, k)
        return best[1] if best else None

    h_end = now.replace(minute=0, second=0, microsecond=0)
    h0 = h_end - dt.timedelta(days=14)
    nh = int((h_end - h0).total_seconds() // 3600) + 1
    groups = {}
    for eic_g, rec in hourly.items():
        sid = site_of(eic_g, rec.get("name", ""))
        if not sid:
            continue
        hs = rec.get("h", {})
        last = max(hs) if hs else None
        groups[eic_g] = {"id": eic_g, "name": rec.get("name"), "type": rec.get("type"), "site": sid,
                         "curve": dense(hs, h0, nh), "last": last, "lastMW": hs.get(last) if last else None,
                         "daily": sorted(daily.get(eic_g, {}).items())[-120:]}
        sites[sid]["groups"].append(eic_g)

    # Indisponibilités : événements chevauchant [J-365, J+365]
    unav = load("unavailabilities.json")
    w0, w1 = now - dt.timedelta(days=365), now + dt.timedelta(days=365)
    events, per_group = [], defaultdict(list)
    for ident, u in unav.items():
        s, e = parse(u["start"]), parse(u["end"]) if u.get("end") else w1
        if e < w0 or s > w1:
            continue
        mx = max([st[2] or 0 for st in u.get("steps", [])] or [0])
        ev = {"g": u["eic"], "n": u["name"], "f": u["fuel"], "k": "P" if u["kind"] == "PLANNED" else "F", "r": u["reason"], "s": u["start"][:16], "e": (u.get("end") or "")[:16],
              "mw": u.get("mw"), "u": mx, "rm": (u.get("remarks") or "")[:160]}
        if u["fuel"] == "NUCLEAR" or u["eic"] in groups:
            events.append(ev)
        per_group[u["eic"]].append(ev)

    # État actuel de chaque groupe
    for eic_g, g in groups.items():
        cur = [ev for ev in per_group.get(eic_g, []) if ev["s"] <= hkey(now) + ":00" <= (ev["e"] or "9999") and ev["u"]]
        unav_now = max([ev["u"] for ev in cur] or [0])
        cap = units.get(g["site"], {}).get("mw") or 0
        g["unavNow"] = unav_now
        recent = [v for v in g["curve"][-3:] if v is not None]
        g["state"] = "unavailable" if cur and unav_now >= 0.9 * (cur[0]["mw"] or unav_now) else ("running" if recent and max(recent) > 1 else "stopped")
        g["unavNowKind"] = cur[0]["k"] if cur else None

    # Puissance indisponible totale (pas de 6 h), programmée / fortuite, nucléaire / autres
    t0, t1 = now - dt.timedelta(days=365), now + dt.timedelta(days=180)
    step = dt.timedelta(hours=6)
    n_steps = int((t1 - t0) / step)
    tot = {k: [0.0] * n_steps for k in ("NP", "NF", "OP", "OF")}
    # Par groupe et par pas : le maximum déclaré (les « informations complémentaires » recouvrent souvent un arrêt
    # déjà déclaré pour le même groupe — les additionner compterait deux fois la même puissance)
    per_unit = defaultdict(dict)  # eic → {pas: (MW, classe)}
    for u in unav.values():
        cls = ("N" if u["fuel"] == "NUCLEAR" else "O") + ("P" if u["kind"] == "PLANNED" else "F")
        for st in u.get("steps", []):
            a = parse(st[0]); b = parse(st[1]) if st[1] else t1
            if st[2] is None or b <= t0 or a >= t1:
                continue
            i0 = max(0, int((a - t0) / step)); i1 = min(n_steps, int((b - t0) / step) + 1)
            cells = per_unit[u["eic"]]
            for i in range(i0, i1):
                if st[2] > cells.get(i, (0, None))[0]:
                    cells[i] = (st[2], cls)
    for cells in per_unit.values():
        for i, (mw, cls) in cells.items():
            tot[cls][i] += mw
    unav_series = {"t0": t0.isoformat(), "stepH": 6, **{k: [round(v) for v in arr] for k, arr in tot.items()}}

    # Prévu / réalisé et ajustement (pas horaire, 120 jours)
    fc = load("forecasts.json")
    th = load("type_hourly.json")
    be = load("balancing.json")
    all_h = [k for ser in fc.get("consumption", {}).values() for k in ser] + [k for ser in th.values() for k in ser]
    hh0 = dt.datetime.strptime(min(all_h), "%Y-%m-%dT%H").replace(tzinfo=UTC) if all_h else now - dt.timedelta(days=120)
    hh1 = h_end + dt.timedelta(days=1)
    nhh = int((hh1 - hh0).total_seconds() // 3600)

    def add(*series):
        keys = set().union(*[s.keys() for s in series]) if series else set()
        return {k: sum(s.get(k, 0) for s in series) for k in keys if all(k in s for s in series)}

    gen = fc.get("generation", {})
    con = fc.get("consumption", {})
    forecast = {
        "h0": hh0.isoformat(), "n": nhh,
        "conso": {k: dense(con.get(k, {}), hh0, nhh) for k in ("REALISED", "D-1", "ID")},
        "wind": {"REALISED": dense(add(th.get("WIND_ONSHORE", {}), th.get("WIND_OFFSHORE", {})), hh0, nhh),
                 "D-1": dense(add(gen.get("D-1|WIND_ONSHORE", {}), gen.get("D-1|WIND_OFFSHORE", {})), hh0, nhh),
                 "ID": dense(add(gen.get("ID|WIND_ONSHORE", {}), gen.get("ID|WIND_OFFSHORE", {})), hh0, nhh)},
        "solar": {"REALISED": dense(th.get("SOLAR", {}), hh0, nhh), "D-1": dense(gen.get("D-1|SOLAR", {}), hh0, nhh), "ID": dense(gen.get("ID|SOLAR", {}), hh0, nhh)},
    }

    def field(res, f):
        return {k: v.get(f) for k, v in be.get(res, {}).items() if v.get(f) is not None}
    vol = be.get("volumes_per_energy_type", {})

    def vsum(prefix):
        return {k: sum(v.get(prefix + p, 0) or 0 for p in ("fcr", "afrr", "mfrr", "rr")) for k, v in vol.items()}
    balancing = {"h0": hh0.isoformat(), "n": nhh,
                 "upPrice": dense(field("prices", "up_mp"), hh0, nhh), "dnPrice": dense(field("prices", "dn_mp"), hh0, nhh),
                 "upVol": dense(vsum("up_"), hh0, nhh), "dnVol": dense(vsum("dn_"), hh0, nhh),
                 "upAfrr": dense({k: v.get("up_afrr") or 0 for k, v in vol.items()}, hh0, nhh), "dnAfrr": dense({k: v.get("dn_afrr") or 0 for k, v in vol.items()}, hh0, nhh),
                 "imb": dense(field("imbalance_data", "imb"), hh0, nhh), "posP": dense(field("imbalance_data", "pos_p"), hh0, nhh), "negP": dense(field("imbalance_data", "neg_p"), hh0, nhh)}

    # Sites physiques : tranches de même famille à moins de ~1,5 km (CATTENOM 1..4 → CATTENOM)
    FAMILY = {"NUCLEAR": "nuc", "FOSSIL_GAS": "gas", "FOSSIL_HARD_COAL": "coal", "FOSSIL_OIL": "oil", "WIND_OFFSHORE": "wind",
              "WIND_ONSHORE": "wind", "SOLAR": "sun", "BIOMASS": "bio", "WASTE": "bio", "MARINE": "marine", "OTHER": "other"}
    fam = lambda t: "hydro" if t.startswith("HYDRO") else FAMILY.get(t, "other")

    def km(a, b):
        import math
        dy = (a["lat"] - b["lat"]) * 111.2
        dx = (a["lon"] - b["lon"]) * 111.2 * math.cos(math.radians(a["lat"]))
        return (dx * dx + dy * dy) ** .5

    def site_name(names):
        base = [re.sub(r"[\s\-_]*(\d+|[IVX]+|TG\d*|TV\d*)$", "", n.strip()) for n in names]
        pref = os.path.commonprefix(base).strip(" -_")
        return pref if len(pref) >= 3 else base[0]

    clusters = []
    for u in sorted(sites.values(), key=lambda x: -x["mw"]):
        for c in clusters:
            if u["lat"] is not None and c["lat"] is not None and fam(u["type"]) == c["fam"] and km(u, c) < 1.5:
                c["units"].append(u); break
            if u["lat"] is None and c["lat"] is None and site_name([u["name"]]) == site_name([c["units"][0]["name"]]):
                c["units"].append(u); break
        else:
            clusters.append({"fam": fam(u["type"]), "lat": u["lat"], "lon": u["lon"], "units": [u]})
    phys = []
    for c in clusters:
        us = sorted(c["units"], key=lambda x: (len(x["name"]), x["name"]))
        sid = "S-" + us[0]["id"]
        for u in us:
            for g in u["groups"]:
                groups[g]["unit"], groups[g]["site"] = u["id"], sid
        types = sorted({u["type"] for u in us}, key=lambda t: -sum(x["mw"] for x in us if x["type"] == t))
        phys.append({"id": sid, "name": site_name([u["name"] for u in us]) if len(us) > 1 else us[0]["name"], "type": types[0], "fam": c["fam"],
                     "mw": sum(u["mw"] for u in us), "lat": c["lat"], "lon": c["lon"], "prec": us[0]["prec"], "src": us[0]["src"],
                     "owner": ", ".join(sorted({u["owner"] for u in us if u["owner"]})),
                     "units": [{"id": u["id"], "name": u["name"], "type": u["type"], "mw": u["mw"], "kv": u["kv"], "groups": u["groups"]} for u in us]})
    phys.sort(key=lambda x: -x["mw"])

    data = {"updated": meta.get("last_run"), "sites": phys, "groups": groups, "events": events, "unavTotal": unav_series,
            "forecast": forecast, "balancing": balancing, "water": sorted(load("water_reserves.json").items()),
            "capacityByType": caps.get("types", {}), "curveStart": h0.isoformat(),
            "ecowatt": sorted(load("ecowatt.json").items()), "tempo": load("tempo.json"),
            "physical": physical_monthly(load("physical_flows.json", {"monthly": {}, "hourly": {}})),
            "physicalRecent": physical_recent(load("physical_flows.json", {"monthly": {}, "hourly": {}})),
            "demandResponse": load("demand_response.json", {}).get("operators", {}),
            "reserves": load("reserves.json", {}).get("need", {})}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.RTE=")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    located = sum(1 for s in phys if s["lat"] is not None)
    print(f"{len(phys)} sites physiques ({located} localisés), {len(sites)} unités ≥ {MIN_MW} MW, {len(groups)} groupes, {len(events)} indisponibilités, {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()
