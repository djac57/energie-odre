"""Fonds de carte de la page Gaz & pétrole : site/geo_world.js (à lancer à la main, une fois).

Source : world-atlas 2.0.2 (Natural Earth, domaine public ; licence ISC du paquet),
countries-110m pour le monde et countries-50m découpé sur l'Europe élargie
(Afrique du Nord, Proche-Orient, Russie d'Europe). Les TopoJSON sont décodés en
GeoJSON et les coordonnées arrondies pour rester légers.

    python3 scripts/build_world_geo.py <countries-110m.json> <countries-50m.json> [site/geo_world.js]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "..", "site", "geo_world.js")
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


def main():
    world = decode(json.load(open(sys.argv[1])), digits=1)
    europe = decode(json.load(open(sys.argv[2])), digits=2, box=EUROPE_BOX)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.GEOW=")
        json.dump({"world": world, "europe": europe}, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    print(f"{OUT} : {os.path.getsize(OUT) // 1024} Ko, {len(world['features'])} pays monde, {len(europe['features'])} pays Europe")


if __name__ == "__main__":
    main()
