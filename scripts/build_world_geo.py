"""Fonds de carte de la page Gaz & pétrole : site/geo_world.js (à lancer à la main, une fois).

Source : world-atlas 2.0.2 (Natural Earth, domaine public ; licence ISC du paquet),
countries-110m pour le monde et countries-50m découpé sur l'Europe élargie
(Afrique du Nord, Proche-Orient, Russie d'Europe). Les TopoJSON sont décodés en
GeoJSON et les coordonnées arrondies pour rester légers.

Avec --10m, écrit aussi site/geo_coast10.js (window.GEOC) : countries-10m découpé au
rectangle Europe de l'Ouest - Méditerranée occidentale, chargé par la carte des
navires seulement aux grands zooms.

    python3 scripts/build_world_geo.py <countries-110m.json> <countries-50m.json> [site/geo_world.js] [--10m countries-10m.json]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
TEN = sys.argv[sys.argv.index("--10m") + 1] if "--10m" in sys.argv else None
if TEN in ARGS:
    ARGS.remove(TEN)
OUT = ARGS[2] if len(ARGS) > 2 else os.path.join(HERE, "..", "site", "geo_world.js")
OUT10 = os.path.join(os.path.dirname(OUT), "geo_coast10.js")
COAST_BOX = (-14.0, 34.0, 20.0, 56.0)
EUROPE_BOX = (-26.0, 24.0, 62.0, 72.0)  # lon min, lat min, lon max, lat max

# Noms français des pays (id ISO 3166-1 numérique de world-atlas) utilisés par la page
NAMES = {
    "578": "Norvège", "643": "Russie", "012": "Algérie", "434": "Libye", "031": "Azerbaïdjan", "792": "Turquie", "840": "États-Unis",
    "634": "Qatar", "566": "Nigeria", "024": "Angola", "818": "Égypte", "124": "Canada", "076": "Brésil", "862": "Venezuela",
    "682": "Arabie saoudite", "368": "Irak", "364": "Iran", "784": "Émirats arabes unis", "414": "Koweït", "398": "Kazakhstan",
    "826": "Royaume-Uni", "528": "Pays-Bas", "056": "Belgique", "276": "Allemagne", "724": "Espagne", "380": "Italie",
    "756": "Suisse", "040": "Autriche", "250": "France", "616": "Pologne", "804": "Ukraine", "112": "Biélorussie",
    "504": "Maroc", "788": "Tunisie", "036": "Australie", "604": "Pérou", "620": "Portugal", "208": "Danemark",
    "752": "Suède", "246": "Finlande", "372": "Irlande", "300": "Grèce", "100": "Bulgarie", "642": "Roumanie",
    "348": "Hongrie", "703": "Slovaquie", "203": "Tchéquie", "440": "Lituanie", "428": "Lettonie", "233": "Estonie",
    "226": "Guinée équatoriale", "266": "Gabon", "178": "Congo", "120": "Cameroun", "288": "Ghana", "384": "Côte d'Ivoire",
    "170": "Colombie", "218": "Équateur", "484": "Mexique", "032": "Argentine", "780": "Trinité-et-Tobago",
    "512": "Oman", "887": "Yémen", "760": "Syrie", "400": "Jordanie", "376": "Israël", "422": "Liban", "196": "Chypre",
    "268": "Géorgie", "051": "Arménie", "795": "Turkménistan", "860": "Ouzbékistan", "156": "Chine", "356": "Inde",
    "360": "Indonésie", "458": "Malaisie", "096": "Brunei", "598": "Papouasie-Nouvelle-Guinée", "604": "Pérou",
}


def decode(topo, obj="countries", digits=2, box=None):
    sc, tr = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append([round(x * sc[0] + tr[0], digits), round(y * sc[1] + tr[1], digits)])
        arcs.append(pts)

    def ring(ids):
        out = []
        for i in ids:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(a if not out else a[1:])
        return out

    feats = []
    for g in topo["objects"][obj]["geometries"]:
        if g["type"] == "Polygon":
            polys = [[ring(r) for r in g["arcs"]]]
        elif g["type"] == "MultiPolygon":
            polys = [[ring(r) for r in p] for p in g["arcs"]]
        else:
            continue
        if box:
            polys = [p for p in polys if any(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in p[0])]
            if not polys:
                continue
        gid = str(g.get("id", "")).zfill(3)
        props = {"id": gid, "nom": NAMES.get(gid, (g.get("properties") or {}).get("name", ""))}
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    return {"type": "FeatureCollection", "features": feats}


def clip_ring(ring, box):
    """Découpe d'un anneau par un rectangle (Sutherland-Hodgman)."""
    x0, y0, x1, y1 = box
    edges = [(lambda p: p[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
             (lambda p: p[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1))]
    pts = ring
    for inside, cut in edges:
        if not pts:
            break
        out = []
        for i, b in enumerate(pts):
            a = pts[i - 1]
            if inside(b):
                if not inside(a):
                    out.append(cut(a, b))
                out.append(b)
            elif inside(a):
                out.append(cut(a, b))
        pts = out
    pts = [[round(x, 3), round(y, 3)] for x, y in pts]
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if len(pts) >= 3 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts if len(pts) >= 4 else None


def coast10(path):
    fc = decode(json.load(open(path)), digits=4, box=COAST_BOX)
    feats = []
    for f in fc["features"]:
        polys = []
        for poly in f["geometry"]["coordinates"]:
            rings = [clip_ring(r, COAST_BOX) for r in poly]
            if rings[0]:
                polys.append([r for r in rings if r])
        if polys:
            feats.append({**f, "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    with open(OUT10, "w", encoding="utf-8") as fh:
        fh.write("window.GEOC=")
        json.dump({"type": "FeatureCollection", "box": COAST_BOX, "features": feats}, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    print(f"{OUT10} : {os.path.getsize(OUT10) // 1024} Ko, {len(feats)} pays")


def main():
    if TEN:
        coast10(TEN)
    world = decode(json.load(open(ARGS[0])), digits=1)
    europe = decode(json.load(open(ARGS[1])), digits=2, box=EUROPE_BOX)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.GEOW=")
        json.dump({"world": world, "europe": europe}, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    print(f"{OUT} : {os.path.getsize(OUT) // 1024} Ko, {len(world['features'])} pays monde, {len(europe['features'])} pays Europe")


if __name__ == "__main__":
    main()
