"""Construit site/entsoe.js à partir de data/raw/entsoe/ (voir fetch_entsoe.py).

- Flux physiques transfrontaliers ENTSO-E (CC BY 4.0) : solde net par frontière, heure par heure sur 10 jours,
  et moyenne journalière depuis 2024. La frontière France–Royaume-Uni (hors licence ouverte sur ENTSO-E) est
  reprise des flux physiques de RTE (interconnexions IFA et IFA2) ; Belgique–Royaume-Uni (Nemo Link) manque.
- Prix des écarts (imbalance prices, CC BY 4.0) au quart d'heure sur 14 jours, et moyennes journalières.

Un sens de frontière sans valeur pour une heure compte pour 0 si l'autre sens est publié (certains GRT ne
publient que le sens actif) ; sans aucun des deux, l'heure reste vide.

    python3 scripts/build_entsoe.py data/raw site/entsoe.js
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
RAW, OUT = sys.argv[1], sys.argv[2]
sys.argv = sys.argv[:2]
from fetch_entsoe import BORDERS, IMB  # noqa: E402

UTC = dt.timezone.utc
NAMES = {"FR": "France", "BE": "Belgique", "NL": "Pays-Bas", "DE": "Allemagne", "LU": "Luxembourg", "CH": "Suisse", "AT": "Autriche", "IT": "Italie",
         "ES": "Espagne", "PT": "Portugal", "GB": "Royaume-Uni", "IE": "Irlande", "DK": "Danemark", "NO": "Norvège", "SE": "Suède", "FI": "Finlande",
         "PL": "Pologne", "CZ": "Tchéquie", "SK": "Slovaquie", "HU": "Hongrie", "SI": "Slovénie", "HR": "Croatie", "RO": "Roumanie", "BG": "Bulgarie",
         "RS": "Serbie", "BA": "Bosnie-Herzégovine", "ME": "Monténégro", "MK": "Macédoine du Nord", "AL": "Albanie", "GR": "Grèce",
         "EE": "Estonie", "LV": "Lettonie", "LT": "Lituanie"}
RTE_GB = (["France>England-IFA", "France>England-IFA2"], ["England-IFA>France", "England-IFA2>France"])


def load(*p, default=None):
    try:
        with open(os.path.join(RAW, *p), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def net(ab, ba, keys):
    out = []
    for k in keys:
        x, y = ab.get(k), ba.get(k)
        out.append(None if x is None and y is None else round((x or 0) - (y or 0)))
    return out


def main():
    hourly = load("entsoe", "flows_hourly.json", default={})
    daily = load("entsoe", "flows_daily.json", default={})
    meta = load("entsoe", "meta.json", default={})
    rte = load("rte", "physical_flows.json", default={}).get("hourly", {})
    hours = sorted({h for s in hourly.values() for h in s})
    if not hours:
        print("entsoe.js : pas de données"); return 0
    # fenêtre horaire : jusqu'à la dernière heure où au moins la moitié des sens de frontière sont publiés
    counts = {h: 0 for h in hours}
    for s in hourly.values():
        for h in s:
            counts[h] += 1
    last = max(h for h in hours if counts[h] >= len(hourly) * .5)
    t1 = dt.datetime.strptime(last, "%Y-%m-%dT%H").replace(tzinfo=UTC)
    t0 = t1 - dt.timedelta(days=9, hours=23)
    hkeys = [(t0 + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in range(240)]
    days = sorted({d for s in daily.values() for d in s if not d.startswith("_")})
    d0 = dt.date.fromisoformat(days[0]) if days else t0.date()
    dkeys = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((dt.date.fromisoformat(days[-1]) - d0).days + 1)] if days else []
    borders, H, Dd = [], {}, {}
    for b in BORDERS + ["FR-GB"]:
        a, c = b.split("-")
        if b == "FR-GB":
            ab = {k: sum(rte.get(x, {}).get(k, 0) for x in RTE_GB[0]) for k in hkeys if any(k in rte.get(x, {}) for x in RTE_GB[0])}
            ba = {k: sum(rte.get(x, {}).get(k, 0) for x in RTE_GB[1]) for k in hkeys if any(k in rte.get(x, {}) for x in RTE_GB[1])}
            dab, dba = {}, {}
            src = "rte"
        else:
            ab, ba = hourly.get(f"{a}>{c}", {}), hourly.get(f"{c}>{a}", {})
            dab, dba = daily.get(f"{a}>{c}", {}), daily.get(f"{c}>{a}", {})
            src = "entsoe"
        hs = net(ab, ba, hkeys)
        if not any(v is not None for v in hs):
            continue
        borders.append({"a": a, "b": c, "src": src})
        H[b] = hs
        Dd[b] = net(dab, dba, dkeys) if dkeys and (dab or dba) else None
    # Prix des écarts
    imb = load("entsoe", "imbalance.json", default={})
    imbd = load("entsoe", "imbalance_daily.json", default={})
    qk = sorted({k for z, s in imb.items() if z in IMB for k in s})
    q0 = dt.datetime.strptime(qk[0], "%Y-%m-%dT%H:%M") if qk else None
    nq = int((dt.datetime.strptime(qk[-1], "%Y-%m-%dT%H:%M") - q0).total_seconds() // 900) + 1 if qk else 0
    qkeys = [(q0 + dt.timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M") for i in range(nq)] if qk else []
    zones = {}
    for z in IMB:
        s = imb.get(z, {})
        if not s:
            continue
        dual = any(isinstance(v, list) and v[0] != v[1] for v in s.values())
        lo = [(s[k][0] if isinstance(s[k], list) else s[k]) if k in s else None for k in qkeys]
        hi = [(s[k][1] if isinstance(s[k], list) else s[k]) if k in s else None for k in qkeys] if dual else None
        zones[z] = {"ex": lo, "de": hi, "daily": sorted([d, v] for d, v in imbd.get(z, {}).items())}
    out = {"updated": meta.get("last_run"), "names": NAMES, "borders": borders, "h0": t0.strftime("%Y-%m-%dT%H"), "net": H,
           "d0": d0.isoformat() if dkeys else None, "netDaily": Dd,
           "imb": {"t0": qkeys[0] if qkeys else None, "zones": zones},
           "license": meta.get("license", "ENTSO-E Transparency Platform, CC BY 4.0")}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.ENTSOE = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"entsoe.js : {len(borders)} frontières, {len(dkeys)} jours d'historique, prix des écarts {', '.join(zones)}, {os.path.getsize(OUT) // 1024} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
