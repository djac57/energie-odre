"""Navires en approche des ports et terminaux français, via le flux AIS d'AISStream.io.

Écoute le flux pendant quelques minutes (AIS_SECONDS, 240 par défaut) sur la façade
française, puis met à jour data/raw/ais/ :
- static.json : caractéristiques déclarées de chaque navire (nom, type, dimensions,
  tirant d'eau, destination, ETA), conservées d'un passage à l'autre car ces messages
  n'arrivent que toutes les ~6 minutes ;
- tracks.json : positions des navires-citernes sur 5 jours (1 point / 10 min au plus),
  remise à zéro quand le navire est vu à quai (la trace part alors de ce port) ;
- voyages.json : dernier port où chaque navire-citerne a été vu à l'arrêt (moins de
  0,5 nœud dans le rayon d'un port de scripts/ais_ports.py), port précédent et première
  observation : l'AIS ne transmet pas le port de départ, on le déduit de l'historique ;
- snapshot.json : instantané des tankers et méthaniers retenus.

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
from ais_ports import decode_destination, port_at  # noqa: E402

RAW = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "raw"), "ais")
URL = "wss://stream.aisstream.io/v0/stream"
BOX = [[[41.0, -6.5], [51.5, 9.8]]]  # façade française : Manche, Atlantique, Méditerranée
SECONDS = int(os.environ.get("AIS_SECONDS", "240"))
KEEP_H = 120
STOP_KN = 0.5

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


async def listen(key, static, positions):
    import websockets  # dépendance optionnelle
    sub = {"APIKey": key, "BoundingBoxes": BOX, "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}
    n = 0
    deadline = time.time() + SECONDS
    async with websockets.connect(URL, compression="deflate", max_size=None, open_timeout=20, ping_interval=20) as ws:
        await ws.send(json.dumps(sub))
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            n += 1
            meta, body, kind = msg.get("MetaData", {}), msg.get("Message", {}), msg.get("MessageType")
            mmsi = str(meta.get("MMSI") or "")
            if not mmsi:
                continue
            if kind == "ShipStaticData":
                s = body.get("ShipStaticData", {})
                dim = s.get("Dimension") or {}
                eta = s.get("Eta") or {}
                static[mmsi] = {"name": (s.get("Name") or meta.get("ShipName") or "").strip(), "type": s.get("Type"),
                                "imo": s.get("ImoNumber"), "call": (s.get("CallSign") or "").strip(),
                                "len": (dim.get("A") or 0) + (dim.get("B") or 0), "beam": (dim.get("C") or 0) + (dim.get("D") or 0),
                                "draught": s.get("MaximumStaticDraught"), "dest": (s.get("Destination") or "").strip(),
                                "eta": [eta.get("Month"), eta.get("Day"), eta.get("Hour"), eta.get("Minute")], "seen": int(time.time())}
            elif kind == "PositionReport":
                p = body.get("PositionReport", {})
                lat, lon = p.get("Latitude"), p.get("Longitude")
                if lat is None or lon is None or abs(lat) > 90:
                    continue
                positions[mmsi] = {"lat": round(lat, 5), "lon": round(lon, 5), "sog": p.get("Sog"), "cog": p.get("Cog"),
                                   "hdg": p.get("TrueHeading"), "nav": p.get("NavigationalStatus"), "t": int(time.time()),
                                   "name": (meta.get("ShipName") or "").strip()}
    return n


def tanker(s):
    """Navire-citerne de mer : types AIS 80-89 ou méthanier nommé « LNG … », hors barges et caboteurs fluviaux."""
    if not s:
        return False
    if not (80 <= (s.get("type") or 0) <= 89 or "LNG" in (s.get("name") or "").upper()):
        return False
    return (s.get("len") or 0) >= 100 or (s.get("draught") or 0) >= 6


def relevant(s, pos):
    """Tankers (types 80-89) et méthaniers de mer, à destination de la France ou près d'un terminal."""
    if not tanker(s):
        return None
    term = terminal_for(s.get("dest"))
    if not term and pos and not foreign(s.get("dest")):
        # destination vague (« NW EUROPE », vide…) : retenu seulement très près d'un terminal
        near = min(TERMINALS, key=lambda t: nm((pos["lat"], pos["lon"]), TERMINALS[t]["pos"]))
        if nm((pos["lat"], pos["lon"]), TERMINALS[near]["pos"]) <= NEAR_NM:
            term = near + " (à proximité)"
    return term


FOREIGN_PORTS = r"ANTWERP|ROTTERDAM|VLISSINGEN|FLUSHING|AMSTERDAM|HAMBURG|BREMEN|WILHELMSHAVEN|ZEEBRUGGE|GHENT|GENT|TERNEUZEN|IMMINGHAM|MILFORD|FAWLEY|THAMES|LONDON|ISLE OF GRAIN|BILBAO|HUELVA|ALGECIRAS|CARTAGENA|BARCELONA|TARRAGONA|CASTELLON|GENOA|GENOVA|LIVORNO|TRIESTE|AUGUSTA|SARROCH|PORTO|SINES|LISBOA|GIBRALTAR|CEUTA|MALTA|SKAW|SKAGEN|GOTHENBURG|GDANSK|BALTIC"


def foreign(dest):
    """Destination déclarée clairement hors de France (code ONU d'un autre pays ou grand port étranger)."""
    d = (dest or "").upper().strip()
    last = re.split(r">+", d)[-1].strip()
    code = re.sub(r"[^A-Z0-9]", "", last)
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", code) and code[:2] != "FR":  # code ONU (LOCODE) à 5 caractères
        return True
    return bool(re.search(FOREIGN_PORTS, last))


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
    static, tracks, voyages = load("static", {}), load("tracks", {}), load("voyages", {})
    positions = {}
    try:
        n = asyncio.run(listen(key, static, positions))
    except Exception as e:
        print(f"flux AIS indisponible : {type(e).__name__}", file=sys.stderr)
        return
    now = int(time.time())
    static = {m: s for m, s in static.items() if now - s.get("seen", 0) < 14 * 86400}  # cache 14 jours
    snap = []
    for m, p in positions.items():
        s = static.get(m)
        if not tanker(s):
            continue
        # Historique de tous les navires-citernes : un navire peut changer de destination après son départ
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
            tr[:] = [x for x in tr if x[0] >= v["stop"]["t0"]]  # la trace du voyage suivant partira de ce port
        if not tr or now - tr[-1][0] >= 600 or nm((tr[-1][1], tr[-1][2]), (p["lat"], p["lon"])) > 1:
            tr.append([p["t"], p["lat"], p["lon"], p["sog"]])
        term = relevant(s, p)
        if not term:
            continue
        snap.append({"mmsi": m, **{k: s.get(k) for k in ("name", "type", "imo", "len", "beam", "draught", "dest", "eta")}, "terminal": term, **p})
    # Traces : 5 jours glissants, navires non revus depuis 5 jours oubliés ; voyages oubliés après 14 jours
    voyages = {m: v for m, v in voyages.items() if now - v.get("seen", 0) < 14 * 86400}
    tracks = {m: [x for x in tr if now - x[0] <= KEEP_H * 3600] for m, tr in tracks.items()}
    tracks = {m: tr for m, tr in tracks.items() if tr}
    # Les navires suivis mais absents de ce passage restent dans l'instantané avec leur dernière position (≤ 6 h)
    seen = {x["mmsi"] for x in snap}
    old = load("snapshot", {}).get("ships", [])
    for x in old:  # le filtre est réévalué : une règle durcie s'applique aussi aux navires déjà connus
        if x["mmsi"] not in seen and now - x.get("t", 0) <= 6 * 3600:
            term = relevant(static.get(x["mmsi"]), x)
            if term:
                snap.append({**x, "terminal": term})
    save("static", static)
    save("tracks", tracks)
    save("voyages", voyages)
    save("snapshot", {"t": now, "messages": n, "seconds": SECONDS, "ships": snap})
    print(f"AIS : {n} messages en {SECONDS} s, {len(positions)} navires vus, {len(snap)} tankers/méthaniers retenus, {len(static)} fiches en cache")


if __name__ == "__main__":
    main()
