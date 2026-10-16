"""Routes maritimes calculées en mer, sur le fond de carte embarqué par la page (site/geo_world.js).

La terre (Natural Earth 110 m pour le monde, 50 m pour l'Europe élargie) est rastérisée sur une
grille de 0,25° ; les passages trop étroits pour cette grille (canal de Suez, Bosphore, Dardanelles,
Gibraltar, détroits danois, Bab-el-Mandeb, Ormuz, Pas-de-Calais, canal de Panama) y sont rouverts
à la main. Une route est le plus court chemin A* sur la grille, puis simplifiée en ne gardant que
des segments dont tous les points, tous les 10 km, sont en mer d'après les polygones exacts.

check() échantillonne une route tous les 20 km et renvoie les points tombés sur la terre, hors
approches des terminaux (premier et dernier tronçons, du terminal au premier point en pleine eau)
et hors passages étroits rouverts.
"""
import heapq
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(HERE, "..", "site", "geo_world.js")
RES = 0.25
NX, NY = int(360 / RES), int(180 / RES)

# Passages étroits rouverts dans la grille (lat, lon), dessinés comme des routes maritimes ordinaires
CHANNELS = {
    "canal de Suez et golfe de Suez": [[31.30, 32.33], [30.60, 32.32], [30.00, 32.56], [29.90, 32.55], [29.50, 32.62], [29.00, 32.78], [28.50, 33.10], [28.00, 33.50], [27.60, 33.85]],
    "Bosphore": [[41.25, 29.12], [41.12, 29.07], [41.02, 28.99], [40.95, 28.95]],
    "Dardanelles": [[40.45, 26.75], [40.20, 26.42], [40.05, 26.22], [40.00, 26.10]],
    "détroit de Gibraltar": [[35.95, -6.10], [35.97, -5.60], [36.05, -5.30]],
    "détroits danois (Grand Belt)": [[57.80, 10.70], [56.80, 11.50], [56.00, 11.10], [55.30, 10.95], [54.70, 11.00], [54.50, 11.50]],
    "Øresund": [[56.10, 12.60], [55.70, 12.75], [55.40, 12.80]],
    "Bab-el-Mandeb": [[13.00, 43.10], [12.60, 43.35], [12.40, 43.60]],
    "détroit d'Ormuz": [[26.70, 56.10], [26.50, 56.50], [26.20, 56.70], [25.80, 57.00]],
    "Pas-de-Calais": [[51.30, 1.80], [51.00, 1.45], [50.80, 1.20]],
    "canal de Panama": [[9.40, -79.92], [9.10, -79.70], [8.90, -79.55]],
}


def _hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1, math.sqrt(h)))  # km


def _interp(a, b, step_km):
    d = _hav(a, b)
    n = max(1, int(d / step_km))
    return [[a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n] for i in range(n + 1)]


class Sea:
    def __init__(self, geo=GEO):
        src = open(geo, encoding="utf-8").read()
        d = json.loads(src[src.index("=") + 1:].rstrip().rstrip(";"))
        self.polys = []
        for f in d["europe"]["features"] + d["world"]["features"]:
            for poly in f["geometry"]["coordinates"]:
                poly = [_unwrap(r) for r in poly]  # anneaux qui traversent l'antiméridien : longitudes continues
                edges = defaultdict_list()
                xs = [p[0] for p in poly[0]]
                ys = [p[1] for p in poly[0]]
                for ring in poly:
                    for i in range(len(ring) - 1):
                        (x1, y1), (x2, y2) = ring[i], ring[i + 1]
                        for band in range(int(math.floor(min(y1, y2))), int(math.floor(max(y1, y2))) + 1):
                            edges[band].append((x1, y1, x2, y2))
                self.polys.append(((min(xs), min(ys), max(xs), max(ys)), edges))
        self.tiles = {}
        for k, (bb, _) in enumerate(self.polys):
            for tx in range(int(bb[0] // 5), int(bb[2] // 5) + 1):
                for ty in range(int(bb[1] // 5), int(bb[3] // 5) + 1):
                    self.tiles.setdefault((tx, ty), []).append(k)
        self._raster()

    # --- terre exacte (polygones), par lancer de rayon sur les arêtes de la bande de latitude
    def land(self, lat, lon):
        lon = (lon + 180) % 360 - 180
        return any(self._land(lat, x) for x in (lon, lon + 360, lon - 360))

    def _land(self, lat, lon):
        for k in self.tiles.get((int(lon // 5), int(lat // 5)), ()):
            bb, edges = self.polys[k]
            if not (bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]):
                continue
            inside = False
            for x1, y1, x2, y2 in edges.get(int(math.floor(lat)), ()):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
            if inside:
                return True
        return False

    # --- grille
    def cell(self, lat, lon):
        return min(NY - 1, max(0, int((lat + 90) / RES))), int(((lon + 180) % 360) / RES) % NX

    def center(self, j, i):
        return [-90 + (j + .5) * RES, -180 + (i + .5) * RES]

    def _raster(self):
        grid = bytearray(NX * NY)  # 1 = terre
        for bb, edges in self.polys:
            j0, j1 = int((bb[1] + 90) / RES), int((bb[3] + 90) / RES) + 1
            for j in range(max(0, j0), min(NY, j1 + 1)):
                lat = -90 + (j + .5) * RES
                xs = sorted(x1 + (lat - y1) * (x2 - x1) / (y2 - y1) for x1, y1, x2, y2 in edges.get(int(math.floor(lat)), ()) if (y1 > lat) != (y2 > lat))
                for a, b in zip(xs[0::2], xs[1::2]):
                    for i in range(int((a + 180) / RES + .5), int((b + 180) / RES + .5)):
                        grid[j * NX + i % NX] = 1
        self.channel = bytearray(NX * NY)
        for line in CHANNELS.values():
            for a, b in zip(line, line[1:]):
                for p in _interp(a, b, 3):
                    j, i = self.cell(*p)
                    for dj in (-1, 0, 1):
                        for di in (-1, 0, 1):
                            k = (j + dj) * NX + (i + di) % NX
                            if dj == 0 or di == 0:
                                grid[k], self.channel[k] = 0, 1
        # mer navigable : composante connexe du grand océan
        sea = bytearray(NX * NY)
        start = self.cell(0, -30)
        stack = [start[0] * NX + start[1]]
        sea[stack[0]] = 1
        while stack:
            k = stack.pop()
            j, i = divmod(k, NX)
            for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                jj = j + dj
                if 0 <= jj < NY:
                    kk = jj * NX + (i + di) % NX
                    if not sea[kk] and not grid[kk]:
                        sea[kk] = 1
                        stack.append(kk)
        self.sea = sea

    def snap(self, lat, lon):
        """Cellule de pleine mer la plus proche."""
        j0, i0 = self.cell(lat, lon)
        for r in range(0, 80):
            best = None
            for j in range(j0 - r, j0 + r + 1):
                for i in (range(i0 - r, i0 + r + 1) if j in (j0 - r, j0 + r) else (i0 - r, i0 + r)):
                    if 0 <= j < NY and self.sea[j * NX + i % NX]:
                        d = _hav([lat, lon], self.center(j, i % NX))
                        if best is None or d < best[0]:
                            best = (d, j, i % NX)
            if best:
                return best[1], best[2]
        return j0, i0

    def astar(self, a, b):
        sa, sb = self.snap(*a), self.snap(*b)
        goal = self.center(*sb)
        start = sa[0] * NX + sa[1]
        g = {start: 0.0}
        prev = {}
        heap = [(0.0, start)]
        target = sb[0] * NX + sb[1]
        while heap:
            _, k = heapq.heappop(heap)
            if k == target:
                break
            j, i = divmod(k, NX)
            c = self.center(j, i)
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    if not dj and not di:
                        continue
                    jj = j + dj
                    if not 0 <= jj < NY:
                        continue
                    kk = jj * NX + (i + di) % NX
                    if not self.sea[kk]:
                        continue
                    lat = c[0] + dj * RES / 2
                    step = RES * 111.2 * math.hypot(dj, di * math.cos(math.radians(lat)))
                    ng = g[k] + step
                    if ng < g.get(kk, 1e18):
                        g[kk] = ng
                        prev[kk] = k
                        heapq.heappush(heap, (ng + _hav(self.center(jj, (i + di) % NX), goal), kk))
        path, k = [], target
        while k != start and k in prev:
            path.append(self.center(*divmod(k, NX)))
            k = prev[k]
        path.append(self.center(*sa))
        return path[::-1]

    def clear(self, a, b, step=10):
        # mêmes points que check() (tous les 20 km) et, en plus, tous les 10 km
        for p in _interp(a, b, step)[1:-1] + _interp(a, b, 20)[1:-1]:
            j, i = self.cell(*p)
            if self.land(*p) and not self.channel[j * NX + i]:
                return False
        return True

    def route(self, a, b):
        """Route en mer de a à b ([lat, lon]) : [a, …points en mer…, b]."""
        if self.clear(a, b, 10) and _hav(a, b) < 3000:
            return [list(a), list(b)]
        cells = self.astar(a, b)
        # longitude continue (traversée éventuelle de l'antiméridien)
        for k in range(1, len(cells)):
            while cells[k][1] - cells[k - 1][1] > 180:
                cells[k][1] -= 360
            while cells[k][1] - cells[k - 1][1] < -180:
                cells[k][1] += 360
        out, i = [cells[0]], 0
        while i < len(cells) - 1:
            j = i + 1
            while j + 1 < len(cells) and self.clear(cells[i], cells[j + 1]):
                j += 1
            out.append(cells[j])
            i = j
        return [list(a)] + [[round(p[0], 3), round(p[1], 3)] for p in out] + [list(b)]

    def check(self, path, step=20):
        """Points sur la terre le long d'une route, hors approches (premier et dernier tronçons) et passages rouverts."""
        bad = []
        for a, b in list(zip(path, path[1:]))[1:-1]:
            for p in _interp(a, b, step):
                j, i = self.cell(*p)
                if self.land(*p) and not self.channel[j * NX + i]:
                    bad.append([round(p[0], 2), round(p[1], 2)])
        return bad


def _unwrap(ring):
    out = [list(ring[0])]
    for x, y in ring[1:]:
        while x - out[-1][0] > 180:
            x -= 360
        while x - out[-1][0] < -180:
            x += 360
        out.append([x, y])
    return out


def defaultdict_list():
    from collections import defaultdict
    return defaultdict(list)


class Router:
    """Routes avec cache disque (les mêmes origines reviennent chaque mois)."""

    def __init__(self, cache_path):
        self.cache_path = cache_path
        try:
            self.cache = json.load(open(cache_path, encoding="utf-8"))
        except Exception:
            self.cache = {}
        self._sea = None
        self.used = set()

    @property
    def sea(self):
        if self._sea is None:
            self._sea = Sea()
        return self._sea

    def route(self, a, b):
        key = f"{a[0]:.2f},{a[1]:.2f}>{b[0]:.2f},{b[1]:.2f}"
        self.used.add(key)
        if key not in self.cache:
            self.cache[key] = self.sea.route(a, b)
        return self.cache[key]

    def save(self, keep_unused=500):
        # garder les routes utilisées, plus les plus récentes des autres (positions de navires)
        keep = {k: v for k, v in self.cache.items() if k in self.used}
        rest = [k for k in self.cache if k not in self.used][-keep_unused:]
        keep.update({k: self.cache[k] for k in rest})
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, separators=(",", ":"))
