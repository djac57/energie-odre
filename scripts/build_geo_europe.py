"""Fond de carte Europe embarqué (site/geo_europe.js) pour la carte des flux ENTSO-E.

Source : world-atlas 2.0.2 (countries-50m), redistribution de Natural Earth (domaine public) — ISC.
Simplification Douglas-Peucker, découpe grossière à la fenêtre européenne. Les identifiants ISO 3166 numériques
sont traduits en codes ENTSO-E à deux lettres (GB, GR, MK…).

    python3 scripts/build_geo_europe.py countries-50m.json site/geo_europe.js
"""
import json
import os
import sys

WORLD, OUT = sys.argv[1:3]
VIEW = (-25.0, 33.0, 45.0, 72.0)
# ISO numérique → (code de zone, nom)
ISO = {"250": ("FR", "France"), "056": ("BE", "Belgique"), "528": ("NL", "Pays-Bas"), "276": ("DE", "Allemagne"), "442": ("LU", "Luxembourg"),
       "756": ("CH", "Suisse"), "040": ("AT", "Autriche"), "380": ("IT", "Italie"), "724": ("ES", "Espagne"), "620": ("PT", "Portugal"),
       "826": ("GB", "Royaume-Uni"), "372": ("IE", "Irlande"), "208": ("DK", "Danemark"), "578": ("NO", "Norvège"), "752": ("SE", "Suède"),
       "246": ("FI", "Finlande"), "616": ("PL", "Pologne"), "203": ("CZ", "Tchéquie"), "703": ("SK", "Slovaquie"), "348": ("HU", "Hongrie"),
       "705": ("SI", "Slovénie"), "191": ("HR", "Croatie"), "642": ("RO", "Roumanie"), "100": ("BG", "Bulgarie"), "688": ("RS", "Serbie"),
       "070": ("BA", "Bosnie-Herzégovine"), "499": ("ME", "Monténégro"), "807": ("MK", "Macédoine du Nord"), "008": ("AL", "Albanie"),
       "300": ("GR", "Grèce"), "233": ("EE", "Estonie"), "428": ("LV", "Lettonie"), "440": ("LT", "Lituanie"),
       # fond seulement
       "804": ("UA", "Ukraine"), "112": ("BY", "Biélorussie"), "498": ("MD", "Moldavie"), "792": ("TR", "Turquie"), "643": ("RU", "Russie"),
       "-99": ("XK", "Kosovo"), "352": ("IS", "Islande"), "504": ("MA", "Maroc"), "012": ("DZ", "Algérie"), "788": ("TN", "Tunisie"),
       "470": ("MT", "Malte"), "196": ("CY", "Chypre"), "020": ("AD", "Andorre"), "438": ("LI", "Liechtenstein")}


def dp(points, tol):
    if len(points) < 4:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** .5 or 1e-12
    best, idx = 0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [points[0], points[-1]]
    return dp(points[:idx + 1], tol)[:-1] + dp(points[idx:], tol)


def ring(points, tol):
    pts = [(round(x, 2), round(y, 2)) for x, y in points]
    x0, y0 = pts[0]
    k = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
    pts = dp(pts[:k + 1], tol)[:-1] + dp(pts[k:], tol) if 0 < k < len(pts) - 1 else pts
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return [list(p) for p in pts] if len(pts) >= 4 else None


def topo_features(topo, obj):
    t = topo["transform"]
    sx, sy = t["scale"]; tx, ty = t["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0; pts = []
        for dx, dy in arc:
            x += dx; y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def build_ring(idx):
        pts = []
        for i in idx:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(a if not pts else a[1:])
        return pts

    for g in topo["objects"][obj]["geometries"]:
        if g["type"] == "Polygon":
            coords = [[build_ring(r) for r in g["arcs"]]]
        elif g["type"] == "MultiPolygon":
            coords = [[build_ring(r) for r in p] for p in g["arcs"]]
        else:
            continue
        yield g.get("id"), g.get("properties", {}).get("name"), coords


def main():
    world = json.load(open(WORLD, encoding="utf-8"))
    feats = []
    for cid, name, polys in topo_features(world, "countries"):
        key = cid if cid in ISO else ("-99" if name == "Kosovo" else None)
        if not key:
            continue
        code, nom = ISO[key]
        out = []
        for poly in polys:
            rings = [r for r in (ring(r, .04) for r in poly) if r]
            if not rings:
                continue
            xs = [p[0] for p in rings[0]]; ys = [p[1] for p in rings[0]]
            if max(xs) < VIEW[0] or min(xs) > VIEW[2] or max(ys) < VIEW[1] or min(ys) > VIEW[3]:
                continue
            if (max(xs) - min(xs)) * (max(ys) - min(ys)) < .02 and code not in ("MT", "LU", "AD", "LI", "CY"):
                continue
            out.append(rings)
        if out:
            feats.append({"type": "Feature", "properties": {"code": code, "nom": nom}, "geometry": {"type": "MultiPolygon", "coordinates": out}})
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.GEO_EU=")
        json.dump({"type": "FeatureCollection", "features": feats}, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    print(f"{len(feats)} pays, {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()
