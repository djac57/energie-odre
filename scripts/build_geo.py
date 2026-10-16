"""Prépare les fonds de carte embarqués (site/geo.js), simplifiés et sans dépendance réseau.

- Régions : france-geojson (Grégoire David), version simplifiée, issue d'Admin Express (IGN) — Licence Ouverte.
- Pays voisins : world-atlas 2.0.2 (countries-50m), redistribution de Natural Earth (domaine public) — ISC.

    python3 scripts/build_geo.py regions.geojson countries-50m.json site/geo.js
"""
import json
import sys

REGIONS, WORLD, OUT = sys.argv[1:4]
VIEW = (-11.0, 35.5, 19.0, 57.0)  # lon min, lat min, lon max, lat max
COUNTRIES = {"250": "France", "826": "Royaume-Uni", "056": "Belgique", "276": "Allemagne", "756": "Suisse", "380": "Italie",
             "724": "Espagne", "442": "Luxembourg", "528": "Pays-Bas", "040": "Autriche", "620": "Portugal", "020": "Andorre",
             "372": "Irlande", "208": "Danemark", "203": "Tchéquie", "616": "Pologne", "705": "Slovénie", "191": "Croatie",
             "438": "Liechtenstein", "674": "Saint-Marin", "504": "Maroc", "012": "Algérie", "788": "Tunisie"}


def dp(points, tol):
    """Douglas-Peucker."""
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
    pts = [(round(x, 3), round(y, 3)) for x, y in points]
    # Anneau fermé : on le coupe au point le plus éloigné du départ, sinon Douglas-Peucker l'écrase
    x0, y0 = pts[0]
    k = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
    pts = dp(pts[:k + 1], tol)[:-1] + dp(pts[k:], tol) if 0 < k < len(pts) - 1 else pts
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return [list(p) for p in pts] if len(pts) >= 4 else None


def in_view(polygon):
    xs = [p[0] for p in polygon[0]]; ys = [p[1] for p in polygon[0]]
    return not (max(xs) < VIEW[0] or min(xs) > VIEW[2] or max(ys) < VIEW[1] or min(ys) > VIEW[3])


def simplify(geom, tol, min_ring=0):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        rings = [r for r in (ring(r, tol) for r in poly) if r]
        if rings and in_view(rings):
            xs = [p[0] for p in rings[0]]; ys = [p[1] for p in rings[0]]
            if (max(xs) - min(xs)) * (max(ys) - min(ys)) >= min_ring:  # retire les îlots minuscules
                out.append(rings)
    return {"type": "MultiPolygon", "coordinates": out} if out else None


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

    def arc_pts(i):
        return arcs[i] if i >= 0 else arcs[~i][::-1]

    def build_ring(idx):
        pts = []
        for i in idx:
            a = arc_pts(i)
            pts.extend(a if not pts else a[1:])
        return pts

    for g in topo["objects"][obj]["geometries"]:
        if g["type"] == "Polygon":
            coords = [[build_ring(r) for r in g["arcs"]]]
        elif g["type"] == "MultiPolygon":
            coords = [[build_ring(r) for r in p] for p in g["arcs"]]
        else:
            continue
        yield g.get("id"), {"type": "MultiPolygon", "coordinates": coords}


def main():
    reg = json.load(open(REGIONS, encoding="utf-8"))
    regions = []
    for f in reg["features"]:
        geom = simplify(f["geometry"], .012, min_ring=.0004)
        if geom:
            regions.append({"type": "Feature", "properties": {"nom": f["properties"]["nom"], "code": f["properties"]["code"]}, "geometry": geom})
    world = json.load(open(WORLD, encoding="utf-8"))
    countries = []
    for cid, geom in topo_features(world, "countries"):
        if cid in COUNTRIES:
            g = simplify(geom, .03, min_ring=.01)
            if g:
                countries.append({"type": "Feature", "properties": {"id": cid, "nom": COUNTRIES[cid]}, "geometry": g})
    data = {"regions": {"type": "FeatureCollection", "features": regions}, "countries": {"type": "FeatureCollection", "features": countries}}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("window.GEO=")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";")
    import os
    print(f"{len(regions)} régions, {len(countries)} pays, {os.path.getsize(OUT) // 1024} Ko")


if __name__ == "__main__":
    main()
