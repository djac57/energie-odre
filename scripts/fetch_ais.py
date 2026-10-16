"""Navires-citernes en route vers la France, via le flux AIS d'AISStream.io.

Trois écoutes par passage (3 connexions au plus, limite d'AISStream) :
1. repérage (AIS_SCAN_SECONDS, 240 s) : messages statiques du monde entier ; les
   pétroliers et méthaniers dont la destination déclarée est un port français
   (scripts/ais_ports.py) entrent dans la liste de suivi ;
2. côtière, en même temps (AIS_SECONDS, 240 s) : positions et messages statiques sur la
   façade française, pour les navires au mouillage ou à quai sans destination claire ;
3. suivi (AIS_TRACK_SECONDS, 180 s) : positions des navires de la liste où qu'ils soient,
   par lots de 200 MMSI (limite d'un abonnement).

La couverture d'AISStream est terrestre (stations côtières bénévoles) : en haute mer, les
navires ne sont pas reçus ; leurs traces ont donc des trous.

Fichiers de data/raw/ais/ :
- static.json : caractéristiques déclarées (nom, type, dimensions, tirant d'eau,
  destination, ETA) et historique des destinations déclarées ; cache de 30 jours ;
- watchlist.json : navires en route vers la France (date de repérage, destination,
  arrivée) ; retirés 30 jours sans nouvelles, 7 jours après l'arrivée, ou si leur
  destination n'est plus française ;
- tracks.json : positions par navire, décimées (1 point / 30 min, puis 1 / 3 h au-delà
  de 2 jours), depuis le dernier port quitté ;
- voyages.json : dernier port où le navire a été vu à l'arrêt (moins de 0,5 nœud dans le
  rayon d'un port connu), port précédent et première observation ;
- snapshot.json : instantané des navires retenus pour la page.

Clé : variable d'environnement AISSTREAM_KEY, sinon ~/.config/aisstream/credentials
(AISSTREAM_KEY=...). Usage serveur uniquement : la clé ne va jamais dans la page.
Dépendance : websockets (pip install websockets). Sans clé ni dépendance, le script
s'arrête proprement (code 0) et les dernières données versionnées restent en place.

    python3 scripts/fetch_ais.py [data/raw]
"""
import asyncio
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ais_ports import decode_destination, is_french, port_at, region  # noqa: E402

RAW = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw"), "ais")
URL = "wss://stream.aisstream.io/v0/stream"
BOX = [[[41.0, -6.5], [51.5, 9.8]]]  # façade française : Manche, Atlantique, Méditerranée
WORLD = [[[-90.0, -180.0], [90.0, 180.0]]]
SECONDS = int(os.environ.get("AIS_SECONDS", "240"))
SCAN_SECONDS = int(os.environ.get("AIS_SCAN_SECONDS", "240"))
TRACK_SECONDS = int(os.environ.get("AIS_TRACK_SECONDS", "180"))
MMSI_PER_SUB = 200
STOP_KN = 0.5
DAY = 86400

# Ports et terminaux pétroliers / GNL suivis : motifs de destination AIS et position
TERMINALS = {
    "Le Havre-Rouen": {"pos": (49.55, 0.10), "re": r"LE ?HAVRE|FRLEH|LEHAVRE|ANTIFER|HAVRE|FRLHV|ROUEN|FRURO|FR URO|JEROME|FRPJE|GONFREVILLE"},
    "Dunkerque": {"pos": (51.05, 2.25), "re": r"DUNK|FRDKK|DUNQ"},
    "Montoir-Donges": {"pos": (47.30, -2.10), "re": r"MONTOIR|FRMTX|DONGES|FRDON|NAZAIRE|FRSNR"},
    "Fos-Lavéra": {"pos": (43.40, 4.90), "re": r"FOS|FRFOS|LAVERA|FRLAV|MARSEILLE|FRMRS|CAVAOU|TONKIN"},
    "Gironde": {"pos": (45.20, -0.75), "re": r"BORDEAUX|FRBOD|BASSENS|AMBES|VERDON|PAUILLAC"},
    "La Rochelle": {"pos": (46.16, -1.22), "re": r"ROCHELLE|FRLRH|PALLICE|FRLPE|FR LPE"},
    "Sète": {"pos": (43.40, 3.70), "re": r"SETE|FRSET"},
}
NEAR_NM = 25


def api_key():
    if os.environ.get("AISSTREAM_KEY"):
        return os.environ["AISSTREAM_KEY"]
    path = os.path.expanduser("~/.config/aisstream/credentials")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith("AISSTREAM_KEY="):
                return line.strip().split("=", 1)[1]
    return None


def nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(h))


def terminal_for(dest):
    p, _ = decode_destination(dest)
    if p:
        return p["group"]
    d = re.sub(r"[^A-Z ]", " ", (dest or "").upper())
    for name, t in TERMINALS.items():
        if re.search(t["re"], d) or re.search(t["re"], d.replace(" ", "")):
            return name
    return None


def load(name, default):
    path = os.path.join(RAW, name + ".json")
    try:
        return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default
    except Exception:
        return default


def save(name, obj):
    with open(os.path.join(RAW, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))


def static_record(body, meta):
    s = body.get("ShipStaticData", {})
    dim = s.get("Dimension") or {}
    eta = s.get("Eta") or {}
    return {"name": (s.get("Name") or meta.get("ShipName") or "").strip(), "type": s.get("Type"),
            "imo": s.get("ImoNumber"), "call": (s.get("CallSign") or "").strip(),
            "len": (dim.get("A") or 0) + (dim.get("B") or 0), "beam": (dim.get("C") or 0) + (dim.get("D") or 0),
            "draught": s.get("MaximumStaticDraught"), "dest": (s.get("Destination") or "").strip(),
            "eta": [eta.get("Month"), eta.get("Day"), eta.get("Hour"), eta.get("Minute")], "seen": int(time.time())}


def put_static(static, mmsi, rec):
    """Met à jour la fiche en gardant l'historique des destinations déclarées (5 dernières)."""
    old = static.get(mmsi) or {}
    dests = old.get("dests") or ([[old.get("seen", 0), old["dest"]]] if old.get("dest") else [])
    if rec["dest"] and (not dests or dests[-1][1] != rec["dest"]):
        dests.append([rec["seen"], rec["dest"]])
    static[mmsi] = {**rec, "dests": dests[-5:]}


async def listen(key, boxes, types, seconds, on_msg, mmsi=None):
    import websockets  # dépendance optionnelle
    sub = {"APIKey": key, "BoundingBoxes": boxes, "FilterMessageTypes": types}
    if mmsi:
        sub["FiltersShipMMSI"] = mmsi
    n = 0
    deadline = time.time() + seconds
    async with websockets.connect(URL, compression="deflate", max_size=None, open_timeout=20, ping_interval=20) as ws:
        await ws.send(json.dumps(sub))
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if "error" in msg:
                raise RuntimeError(str(msg["error"])[:200])
            n += 1
            on_msg(msg)
    return n


def tanker(s):
    """Navire-citerne de mer : types AIS 80-89 ou méthanier nommé « LNG … », hors barges et caboteurs fluviaux."""
    if not s:
        return False
    if not (80 <= (s.get("type") or 0) <= 89 or "LNG" in (s.get("name") or "").upper()):
        return False
    return (s.get("len") or 0) >= 100 or (s.get("draught") or 0) >= 6


FOREIGN_PORTS = r"ANTWERP|ROTTERDAM|VLISSINGEN|FLUSHING|AMSTERDAM|HAMBURG|BREMEN|WILHELMSHAVEN|ZEEBRUGGE|GHENT|GENT|TERNEUZEN|IMMINGHAM|MILFORD|FAWLEY|THAMES|LONDON|ISLE OF GRAIN|BILBAO|HUELVA|ALGECIRAS|CARTAGENA|BARCELONA|TARRAGONA|CASTELLON|GENOA|GENOVA|LIVORNO|TRIESTE|AUGUSTA|SARROCH|PORTO|SINES|LISBOA|GIBRALTAR|CEUTA|MALTA|SKAW|SKAGEN|GOTHENBURG|GDANSK|BALTIC"


def foreign(dest):
    """Destination déclarée clairement hors de France (code ONU d'un autre pays ou grand port étranger)."""
    d = (dest or "").upper().strip()
    last = re.split(r">+", d)[-1].strip()
    code = re.sub(r"[^A-Z0-9]", "", last)
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", code) and code[:2] != "FR":  # code ONU (LOCODE) à 5 caractères
        return True
    return bool(re.search(FOREIGN_PORTS, last))


def bound_for_france(s):
    if not tanker(s) or foreign(s.get("dest")):
        return None
    p, _ = decode_destination(s.get("dest"))
    return p if is_french(p) else None


def relevant(s, pos):
    """Tankers et méthaniers à destination de la France ou au mouillage tout près d'un terminal."""
    if not tanker(s):
        return None
    term = terminal_for(s.get("dest"))
    if not term and pos and not foreign(s.get("dest")):
        near = min(TERMINALS, key=lambda t: nm((pos["lat"], pos["lon"]), TERMINALS[t]["pos"]))
        if nm((pos["lat"], pos["lon"]), TERMINALS[near]["pos"]) <= NEAR_NM:
            term = near + " (à proximité)"
    return term


def decimate(tr, now):
    """1 point / 30 min, puis 1 point / 3 h pour les positions de plus de 2 jours (garde le premier et le dernier)."""
    out = []
    for p in tr:
        gap = 3 * 3600 if now - p[0] > 2 * DAY else 1800
        if not out or p[0] - out[-1][0] >= gap or nm((out[-1][1], out[-1][2]), (p[1], p[2])) > 30:
            out.append(p)
    if tr and out[-1] is not tr[-1]:
        out.append(tr[-1])
    return out


def main():
    key = api_key()
    if not key:
        print("clé AISSTREAM_KEY absente : rien à faire")
        return
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("module websockets absent (pip install websockets) : rien à faire")
        return
    os.makedirs(RAW, exist_ok=True)
    static, tracks, voyages, watch = load("static", {}), load("tracks", {}), load("voyages", {}), load("watchlist", {})
    positions, scan_pos = {}, {}
    counts = {"coast": 0, "scan": 0, "track": 0}

    def pos_of(meta, body):
        p = body.get("PositionReport", {})
        lat, lon = p.get("Latitude"), p.get("Longitude")
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            return None
        return {"lat": round(lat, 5), "lon": round(lon, 5), "sog": p.get("Sog"), "cog": p.get("Cog"), "hdg": p.get("TrueHeading"),
                "nav": p.get("NavigationalStatus"), "t": int(time.time()), "name": (meta.get("ShipName") or "").strip()}

    def on_coast(msg):
        meta, body, kind = msg.get("MetaData", {}), msg.get("Message", {}), msg.get("MessageType")
        mmsi = str(meta.get("MMSI") or "")
        if not mmsi:
            return
        if kind == "ShipStaticData":
            put_static(static, mmsi, static_record(body, meta))
        elif kind == "PositionReport":
            p = pos_of(meta, body)
            if p:
                positions[mmsi] = p

    def on_scan(msg):
        meta, body = msg.get("MetaData", {}), msg.get("Message", {})
        mmsi = str(meta.get("MMSI") or "")
        if not mmsi or msg.get("MessageType") != "ShipStaticData":
            return
        rec = static_record(body, meta)
        if not tanker(rec):
            return  # seuls les navires-citernes sont gardés en cache
        put_static(static, mmsi, rec)
        if meta.get("latitude") is not None and meta.get("longitude") is not None:
            scan_pos[mmsi] = {"lat": round(meta["latitude"], 5), "lon": round(meta["longitude"], 5), "t": int(time.time())}

    def on_track(msg):
        meta, body = msg.get("MetaData", {}), msg.get("Message", {})
        mmsi = str(meta.get("MMSI") or "")
        if mmsi and msg.get("MessageType") == "PositionReport":
            p = pos_of(meta, body)
            if p:
                positions[mmsi] = p

    async def phase1():
        r = await asyncio.gather(listen(key, BOX, ["PositionReport", "ShipStaticData"], SECONDS, on_coast),
                                 listen(key, WORLD, ["ShipStaticData"], SCAN_SECONDS, on_scan), return_exceptions=True)
        for name, x in zip(("coast", "scan"), r):
            if isinstance(x, Exception):
                print(f"écoute {name} indisponible : {type(x).__name__} {x}"[:200], file=sys.stderr)
            else:
                counts[name] = x

    try:
        asyncio.run(phase1())
    except Exception as e:
        print(f"flux AIS indisponible : {type(e).__name__}", file=sys.stderr)
        return
    now = int(time.time())

    # --- liste de suivi : repérés dans le monde avec une destination française
    found = 0
    for m, s in static.items():
        if s.get("seen", 0) < now - 3600 or not tanker(s):
            continue
        port = bound_for_france(s)
        w = watch.get(m)
        if port:
            if not w:
                found += 1
                w = watch[m] = {"since": now, "port": port["id"]}
            w.update({"port": port["id"], "dest": s.get("dest"), "seen": now})
            if m in scan_pos:
                w["pos"] = scan_pos[m]
        elif w and s.get("dest") and not w.get("arrived"):
            w["gone"] = now  # destination devenue étrangère : le voyage vers la France est abandonné
    watch = {m: w for m, w in watch.items() if not w.get("gone") and now - w.get("seen", 0) < 30 * DAY
             and not (w.get("arrived") and now - w["arrived"] > 7 * DAY)}

    # --- suivi : positions des navires de la liste, partout
    ids = sorted(watch, key=lambda m: -watch[m].get("seen", 0))[:MMSI_PER_SUB * 3]
    chunks = [ids[i:i + MMSI_PER_SUB] for i in range(0, len(ids), MMSI_PER_SUB)]

    async def phase2():
        r = await asyncio.gather(*[listen(key, WORLD, ["PositionReport"], TRACK_SECONDS, on_track, mmsi=c) for c in chunks], return_exceptions=True)
        for x in r:
            if isinstance(x, Exception):
                print(f"suivi indisponible : {type(x).__name__} {x}"[:200], file=sys.stderr)
            else:
                counts["track"] += x

    if chunks:
        try:
            asyncio.run(phase2())
        except Exception as e:
            print(f"suivi indisponible : {type(e).__name__}", file=sys.stderr)
    now = int(time.time())
    for m, p in scan_pos.items():  # position jointe aux messages statiques, faute de mieux
        if m in watch and m not in positions:
            positions[m] = {**p, "sog": None, "cog": None, "hdg": None, "nav": None, "name": static.get(m, {}).get("name", ""), "approx": True}

    # --- historique : traces, ports d'escale, arrivées
    static = {m: s for m, s in static.items() if now - s.get("seen", 0) < (30 if tanker(s) else 2) * DAY}  # navires-citernes 30 j, autres 2 j
    snap = []
    for m, p in positions.items():
        s = static.get(m)
        if not tanker(s):
            continue
        dport, _ = decode_destination(s.get("dest"))
        v = voyages.setdefault(m, {"first": [p["t"], p["lat"], p["lon"]]})
        v["seen"] = p["t"]
        tr = tracks.setdefault(m, [])
        port = port_at(p["lat"], p["lon"]) if (p.get("sog") is not None and p["sog"] < STOP_KN) else None
        if port:
            stop = v.get("stop")
            if stop and stop["id"] == port["id"]:
                stop["t"] = p["t"]
            else:
                if stop:
                    v["prev"] = stop
                v["stop"] = {"id": port["id"], "name": port["name"], "t0": p["t"], "t": p["t"], "lat": p["lat"], "lon": p["lon"]}
            if m in watch and dport and port["id"] == dport["id"] and not watch[m].get("arrived"):
                watch[m]["arrived"] = p["t"]
        stop = v.get("stop")
        if stop and not (dport and stop["id"] == dport["id"]):
            tr[:] = [x for x in tr if x[0] >= stop["t0"]]  # la trace du voyage en cours part du dernier port quitté
        if not tr or p["t"] - tr[-1][0] >= 600 or nm((tr[-1][1], tr[-1][2]), (p["lat"], p["lon"])) > 1:
            tr.append([p["t"], p["lat"], p["lon"], p["sog"]])
        tr[:] = decimate(tr, now)
        term = relevant(s, p) or (dport["group"] if m in watch and dport else None)
        if not term:
            continue
        snap.append({"mmsi": m, **{k: s.get(k) for k in ("name", "type", "imo", "len", "beam", "draught", "dest", "eta")}, "terminal": term, **p})
    # navires de la liste non reçus ce passage : dernière position connue (≤ 30 jours)
    seen = {x["mmsi"] for x in snap}
    for m, w in watch.items():
        if m in seen or not tracks.get(m):
            continue
        s = static.get(m)
        if not s:
            continue
        dport, _ = decode_destination(s.get("dest"))
        last = tracks[m][-1]
        snap.append({"mmsi": m, **{k: s.get(k) for k in ("name", "type", "imo", "len", "beam", "draught", "dest", "eta")},
                     "terminal": (dport or {}).get("group") or "France", "lat": last[1], "lon": last[2], "sog": last[3], "cog": None, "hdg": None,
                     "nav": None, "t": last[0], "name": s.get("name", ""), "stale": True})
    seen = {x["mmsi"] for x in snap}
    old = load("snapshot", {}).get("ships", [])
    for x in old:  # navires côtiers non revus : dernière position (≤ 6 h), filtre réévalué
        if x["mmsi"] not in seen and now - x.get("t", 0) <= 6 * 3600 and not x.get("stale"):
            term = relevant(static.get(x["mmsi"]), x)
            if term:
                snap.append({**x, "terminal": term})
    keep = set(watch) | {x["mmsi"] for x in snap}
    tracks = {m: tr for m, tr in tracks.items() if tr and (m in keep or now - tr[-1][0] <= 5 * DAY)}
    voyages = {m: v for m, v in voyages.items() if now - v.get("seen", 0) < 30 * DAY}
    save("static", static)
    save("tracks", tracks)
    save("voyages", voyages)
    save("watchlist", watch)
    zones = {}
    for x in snap:
        if x["mmsi"] in watch:
            z = region(x["lat"], x["lon"])
            zones[z] = zones.get(z, 0) + 1
    save("snapshot", {"t": now, "messages": counts["coast"] + counts["scan"] + counts["track"], "seconds": SECONDS, "scanSeconds": SCAN_SECONDS,
                      "trackSeconds": TRACK_SECONDS, "counts": counts, "watch": len(watch), "ships": snap})
    print(f"AIS : côtier {counts['coast']} messages, repérage monde {counts['scan']} messages statiques ({found} nouveaux navires vers la France), "
          f"suivi {counts['track']} positions ; liste de suivi {len(watch)}, {len(snap)} navires retenus ; zones {zones}")


if __name__ == "__main__":
    main()
