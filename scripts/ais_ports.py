"""Table locale des ports utilisée par la carte des navires (fetch_ais.py, build_gaz_petrole.py).

Codes UN/LOCODE (identifiants, vérifiés sur la liste UNECE) et noms libres que les
navires écrivent dans le champ Destination de l'AIS. Les coordonnées sont celles des
bassins ou terminaux pétroliers et GNL, saisies à la main : la liste officielle UNECE
n'est pas redistribuée ici, ses conditions de réutilisation n'étant pas claires.

Champs : code (UN/LOCODE ou None), nom, lat, lon, rang (1 grand terminal français,
2 port important, 3 port secondaire), motif de nom libre, groupe (terminal suivi par
la page), rayon d'arrêt en milles (navire à moins de 0,5 nœud dans ce rayon = à quai).
"""
import math
import re

PORTS = [
    # --- France : Manche et mer du Nord
    ("FRDKK", "Dunkerque", 51.035, 2.205, 1, r"DUNK|DUNQ", "Dunkerque", 5),
    ("FRGRV", "Gravelines", 51.005, 2.115, 3, r"GRAVELINES", "Dunkerque", 2),
    ("FRCQF", "Calais", 50.965, 1.865, 2, r"CALAIS", None, 2),
    ("FRBOL", "Boulogne-sur-Mer", 50.725, 1.585, 2, r"BOULOGNE", None, 2),
    ("FRDPE", "Dieppe", 49.930, 1.085, 3, r"DIEPPE", None, 2),
    ("FRFEC", "Fécamp", 49.760, 0.370, 3, r"FECAMP", None, 2),
    (None, "Antifer", 49.655, 0.145, 2, r"ANTIFER", "Le Havre-Rouen", 3),
    ("FRLEH", "Le Havre", 49.470, 0.150, 1, r"LE ?HAVRE|FRLHV", "Le Havre-Rouen", 4),
    ("FRGLO", "Gonfreville-l'Orcher", 49.475, 0.230, 3, r"GONFREVILLE", "Le Havre-Rouen", 2),
    ("FRHON", "Honfleur", 49.420, 0.240, 3, r"HONFLEUR", "Le Havre-Rouen", 2),
    ("FRRAD", "Radicatel", 49.490, 0.500, 3, r"RADICATEL", "Le Havre-Rouen", 1.5),
    ("FRPJE", "Port-Jérôme", 49.475, 0.555, 2, r"JEROME|GRAVENCHON", "Le Havre-Rouen", 2),
    ("FRURO", "Rouen", 49.450, 1.060, 1, r"ROUEN", "Le Havre-Rouen", 4),
    ("FRCER", "Cherbourg", 49.650, -1.620, 2, r"CHERBOURG", None, 2),
    ("FRSML", "Saint-Malo", 48.645, -2.020, 3, r"MALO", None, 2),
    ("FRROS", "Roscoff", 48.720, -3.970, 3, r"ROSCOFF", None, 1.5),
    # --- France : Atlantique
    ("FRBES", "Brest", 48.380, -4.480, 2, r"BREST", None, 3),
    ("FRLRT", "Lorient", 47.730, -3.360, 3, r"LORIENT", None, 2),
    ("FRMTX", "Montoir-de-Bretagne", 47.300, -2.145, 1, r"MONTOIR", "Montoir-Donges", 3),
    ("FRDON", "Donges", 47.310, -2.070, 2, r"DONGES", "Montoir-Donges", 2.5),
    ("FRSNR", "Saint-Nazaire", 47.270, -2.200, 2, r"NAZAIRE", "Montoir-Donges", 2),
    ("FRLPE", "La Pallice", 46.158, -1.225, 2, r"PALLICE", "La Rochelle", 2),
    ("FRLRH", "La Rochelle", 46.155, -1.150, 1, r"ROCHELLE", "La Rochelle", 2),
    ("FRTON", "Tonnay-Charente", 45.940, -0.890, 3, r"TONNAY", None, 1.5),
    ("FRLVE", "Le Verdon-sur-Mer", 45.550, -1.060, 3, r"VERDON", "Gironde", 2),
    ("FRPAP", "Pauillac", 45.200, -0.740, 3, r"PAUILLAC", "Gironde", 1.5),
    ("FRBYE", "Blaye", 45.130, -0.665, 3, r"BLAYE", "Gironde", 1.5),
    ("FRAMS", "Ambès", 45.010, -0.535, 2, r"AMBES", "Gironde", 1.5),
    ("FRBAS", "Bassens", 44.900, -0.530, 2, r"BASSENS", "Gironde", 1.5),
    ("FRBOD", "Bordeaux", 44.860, -0.550, 1, r"BORDEAUX", "Gironde", 2),
    ("FRBAY", "Bayonne", 43.515, -1.490, 2, r"BAYONNE", None, 2),
    # --- France : Méditerranée
    ("FRNOU", "Port-la-Nouvelle", 43.015, 3.060, 3, r"NOUVELLE", None, 2),
    ("FRSET", "Sète", 43.400, 3.700, 1, r"SETE|FRONTIGNAN", "Sète", 2.5),
    ("FRFOS", "Fos-sur-Mer", 43.420, 4.870, 1, r"FOS|CAVAOU|TONKIN", "Fos-Lavéra", 4),
    ("FRPDB", "Port-de-Bouc", 43.400, 4.980, 3, r"PORT DE BOUC|PORTDEBOUC", "Fos-Lavéra", 1.5),
    ("FRLAV", "Lavéra", 43.390, 5.000, 2, r"LAVERA", "Fos-Lavéra", 2),
    ("FRMRS", "Marseille", 43.330, 5.350, 1, r"MARSEILLE", "Fos-Lavéra", 3),
    ("FRTLN", "Toulon", 43.110, 5.930, 2, r"TOULON", None, 2),
    ("FRNCE", "Nice", 43.690, 7.290, 3, r"NICE", None, 1.5),
    ("FRAJA", "Ajaccio", 41.920, 8.740, 3, r"AJACCIO", None, 2),
    ("FRBIA", "Bastia", 42.700, 9.450, 3, r"BASTIA", None, 2),
    # --- Benelux, Allemagne, Scandinavie, Baltique
    ("BEZEE", "Zeebrugge", 51.330, 3.200, 2, r"ZEEBRUGGE", None, 3),
    ("BEOST", "Ostende", 51.230, 2.930, 3, r"OOSTENDE|OSTEND", None, 2),
    ("BEGNE", "Gand", 51.100, 3.770, 3, r"GENT|GHENT|GAND", None, 4),
    ("BEANR", "Anvers", 51.280, 4.300, 2, r"ANTWERP|ANVERS|DEURGANCK|KALLO", None, 7),
    ("NLVLI", "Flessingue", 51.440, 3.620, 2, r"VLISSINGEN|FLUSHING|SLOE", None, 3),
    ("NLTNZ", "Terneuzen", 51.340, 3.830, 3, r"TERNEUZEN", None, 2),
    ("NLMOE", "Moerdijk", 51.690, 4.600, 3, r"MOERDIJK", None, 2),
    ("NLRTM", "Rotterdam", 51.950, 4.100, 2, r"ROTTERDAM|EUROPOORT|BOTLEK|MAASVLAKTE|PERNIS", None, 8),
    ("NLAMS", "Amsterdam", 52.410, 4.800, 2, r"AMSTERDAM", None, 4),
    ("DEWVN", "Wilhelmshaven", 53.570, 8.150, 2, r"WILHELMSHAVEN", None, 3),
    ("DEBRV", "Bremerhaven", 53.560, 8.560, 3, r"BREMERHAVEN", None, 3),
    ("DEBRE", "Brême", 53.100, 8.750, 3, r"BREMEN", None, 3),
    ("DEHAM", "Hambourg", 53.540, 9.970, 2, r"HAMBURG", None, 4),
    ("DKSKA", "Skagen", 57.720, 10.590, 3, r"SKAGEN|SKAW", None, 4),
    ("DKCPH", "Copenhague", 55.700, 12.610, 3, r"COPENHAGEN|KOBENHAVN", None, 3),
    ("SEGOT", "Göteborg", 57.690, 11.850, 3, r"GOTHENBURG|GOTEBORG|GOETEBORG", None, 3),
    ("PLSWI", "Świnoujście", 53.910, 14.260, 3, r"SWINOUJSCIE", None, 2),
    ("PLGDN", "Gdańsk", 54.400, 18.670, 3, r"GDANSK", None, 3),
    ("LTKLJ", "Klaipėda", 55.700, 21.130, 3, r"KLAIPEDA", None, 2),
    ("RUPRI", "Primorsk", 60.350, 28.700, 3, r"PRIMORSK", None, 2),
    ("RUULU", "Oust-Louga", 59.680, 28.400, 3, r"UST ?LUGA", None, 2),
    ("NOMON", "Mongstad", 60.810, 5.030, 3, r"MONGSTAD", None, 2),
    ("NOSTU", "Sture", 60.610, 4.850, 3, r"STURE", None, 2),
    ("NOKAR", "Kårstø", 59.270, 5.520, 3, r"KARSTO", None, 2),
    ("NOHFT", "Hammerfest", 70.690, 23.600, 3, r"HAMMERFEST|MELKOYA", None, 3),
    # --- Royaume-Uni, Irlande
    ("GBIOG", "Isle of Grain", 51.440, 0.710, 2, r"GRAIN", None, 2),
    ("GBLON", "Londres (Tamise)", 51.500, 0.450, 2, r"LONDON|THAMES|CORYTON", None, 6),
    ("GBFAW", "Fawley", 50.840, -1.330, 2, r"FAWLEY|SOLENT", None, 2.5),
    ("GBSOU", "Southampton", 50.900, -1.410, 3, r"SOUTHAMPTON", None, 2),
    ("GBPME", "Portsmouth", 50.800, -1.100, 3, r"PORTSMOUTH", None, 2),
    ("GBMLF", "Milford Haven", 51.700, -5.050, 2, r"MILFORD", None, 4),
    ("GBIMM", "Immingham", 53.630, -0.190, 2, r"IMMINGHAM", None, 3),
    ("GBTEE", "Teesport", 54.600, -1.160, 3, r"TEES", None, 3),
    ("GBBEL", "Belfast", 54.620, -5.900, 3, r"BELFAST", None, 3),
    ("GBSFW", "Scapa Flow", 58.840, -3.100, 3, r"SCAPA", None, 5),
    ("IEDUB", "Dublin", 53.350, -6.200, 3, r"DUBLIN", None, 2),
    ("IEORK", "Cork", 51.820, -8.250, 3, r"CORK|WHITEGATE", None, 3),
    # --- Péninsule ibérique
    ("ESBIO", "Bilbao", 43.360, -3.080, 2, r"BILBAO", None, 3),
    ("ESGIJ", "Gijón", 43.560, -5.700, 3, r"GIJON", None, 2),
    ("ESLCG", "La Corogne", 43.360, -8.390, 3, r"CORUNA", None, 2),
    ("ESMGR", "Mugardos", 43.460, -8.250, 3, r"MUGARDOS", None, 2),
    ("ESFRO", "Ferrol", 43.470, -8.250, 3, r"FERROL", None, 2),
    ("PTLEI", "Leixões", 41.180, -8.700, 3, r"LEIXOES", None, 2),
    ("PTLIS", "Lisbonne", 38.690, -9.150, 3, r"LISBOA|LISBON", None, 3),
    ("PTSIE", "Sines", 37.940, -8.870, 2, r"SINES", None, 3),
    ("ESHUV", "Huelva", 37.150, -6.830, 2, r"HUELVA", None, 4),
    ("ESALG", "Algésiras", 36.130, -5.430, 2, r"ALGECIRAS", None, 3),
    ("GIGIB", "Gibraltar", 36.130, -5.360, 2, r"GIBRALTAR", None, 3),
    ("ESCAR", "Carthagène", 37.570, -0.970, 2, r"CARTAGENA|ESCOMBRERAS", None, 3),
    ("ESVLC", "Valence", 39.440, -0.320, 3, r"VALENCIA", None, 3),
    ("ESCAS", "Castellón", 39.960, 0.030, 3, r"CASTELLON", None, 3),
    ("ESTAR", "Tarragone", 41.090, 1.220, 2, r"TARRAGONA", None, 3),
    ("ESBCN", "Barcelone", 41.340, 2.160, 2, r"BARCELONA", None, 3),
    ("ESPMI", "Palma", 39.560, 2.630, 3, r"PALMA", None, 2),
    # --- Italie, Malte, Méditerranée
    ("ITVDL", "Vado Ligure", 44.260, 8.440, 3, r"VADO", None, 2),
    ("ITSVN", "Savone", 44.300, 8.470, 3, r"SAVONA", None, 2),
    ("ITGOA", "Gênes", 44.400, 8.900, 2, r"GENOA|GENOVA|MULTEDO", None, 3),
    ("ITSPE", "La Spezia", 44.100, 9.850, 3, r"SPEZIA|PANIGAGLIA", None, 3),
    ("ITLIV", "Livourne", 43.560, 10.290, 3, r"LIVORNO|LEGHORN", None, 3),
    ("ITCVV", "Civitavecchia", 42.090, 11.780, 3, r"CIVITAVECCHIA", None, 2),
    ("ITPFX", "Sarroch", 39.070, 9.030, 3, r"SARROCH|FOXI", None, 3),
    ("ITTRS", "Trieste", 45.620, 13.770, 2, r"TRIESTE", None, 3),
    ("ITAUG", "Augusta", 37.210, 15.220, 3, r"AUGUSTA", None, 3),
    ("ITMLZ", "Milazzo", 38.220, 15.250, 3, r"MILAZZO", None, 2),
    ("MTMAR", "Malte", 35.820, 14.540, 3, r"MALTA|MARSAXLOKK", None, 4),
    ("DZAZW", "Arzew", 35.850, -0.300, 2, r"ARZEW", None, 3),
    ("DZSKI", "Skikda", 36.890, 6.920, 2, r"SKIKDA", None, 3),
    ("LYESI", "Es Sider", 30.630, 18.350, 3, r"SIDER|SIDR", None, 3),
    ("EGDAM", "Damiette", 31.470, 31.760, 3, r"DAMIETTA|DUMYAT", None, 3),
    ("TRCEY", "Ceyhan", 36.880, 35.930, 3, r"CEYHAN", None, 4),
    ("RUNVS", "Novorossiisk", 44.720, 37.790, 3, r"NOVOROSSIYSK", None, 3),
    # --- Grand large (destinations lointaines)
    ("QARLF", "Ras Laffan", 25.920, 51.600, 2, r"RAS ?LAFFAN", None, 4),
    ("USNSS", "Sabine Pass", 29.730, -93.870, 2, r"SABINE", None, 4),
    ("USCMU", "Cameron", 29.960, -93.330, 3, r"CAMERON", None, 4),
    ("USCRP", "Corpus Christi", 27.810, -97.400, 3, r"CORPUS", None, 4),
    ("NGBON", "Bonny", 4.420, 7.150, 3, r"BONNY", None, 4),
]

PORT_LIST = [{"id": (code or name.upper().replace(" ", "")), "code": code, "name": name, "lat": lat, "lon": lon, "rank": rank,
              "re": pat, "group": group, "r": r} for code, name, lat, lon, rank, pat, group, r in PORTS]
BY_CODE = {p["code"]: p for p in PORT_LIST if p["code"]}

# Repères côtiers pour situer une première observation en mer (« au large de … »)
LANDMARKS = [("Ouessant", 48.46, -5.10), ("la pointe du Raz", 48.04, -4.74), ("Belle-Île", 47.33, -3.18), ("l'île d'Yeu", 46.71, -2.35),
             ("l'île d'Oléron", 45.95, -1.40), ("Arcachon", 44.62, -1.25), ("la Hague", 49.72, -1.94), ("Barfleur", 49.70, -1.26),
             ("Fécamp", 49.76, 0.37), ("le cap Gris-Nez", 50.87, 1.58), ("l'île de Wight", 50.62, -1.30), ("le cap Lizard", 49.96, -5.20),
             ("les Sorlingues", 49.92, -6.30), ("Guernesey", 49.45, -2.58), ("le cap Finisterre", 42.88, -9.27), ("le cap Peñas", 43.66, -5.85),
             ("Saint-Sébastien", 43.33, -1.98), ("le cap Saint-Vincent", 37.02, -8.99), ("le cap Creus", 42.32, 3.32), ("le golfe du Lion", 43.00, 4.00),
             ("la Camargue", 43.35, 4.50), ("Toulon", 43.05, 5.93), ("le cap Corse", 43.00, 9.42), ("Bonifacio", 41.38, 9.17),
             ("Minorque", 39.95, 4.10), ("Majorque", 39.60, 2.70), ("Ibiza", 38.90, 1.40), ("le cap de la Nao", 38.73, 0.23),
             ("Gibraltar", 36.13, -5.36), ("Dunkerque", 51.10, 2.20), ("Ostende", 51.30, 2.80), ("la Bretagne", 48.00, -5.50)]


def nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(h))


def decode_destination(dest):
    """Champ Destination AIS -> (port, méthode) ; méthode 'code' (UN/LOCODE) ou 'nom' ; (None, None) sinon.

    Les navires écrivent « FRLEH », « FR LEH », « NLRTM>FRLEH », « LE HAVRE », « ANTWERPEN 4E HAVENDOK »…
    Seul le dernier segment (après « > ») est la destination."""
    d = (dest or "").upper().strip()
    if not d:
        return None, None
    last = re.split(r">+|=>|->", d)[-1].strip() or d
    for chunk in (last, d):
        compact = re.sub(r"[^A-Z0-9 ]", " ", chunk)
        for m in re.finditer(r"\b([A-Z]{2}) ?([A-Z0-9]{3})\b", compact):
            p = BY_CODE.get(m.group(1) + m.group(2))
            if p:
                return p, "code"
        for p in PORT_LIST:
            if re.search(p["re"], compact) or re.search(p["re"], compact.replace(" ", "")):
                return p, "nom"
    return None, None


def port_at(lat, lon):
    """Port connu dont le rayon d'arrêt contient la position, sinon None."""
    best = None
    for p in PORT_LIST:
        d = nm((lat, lon), (p["lat"], p["lon"]))
        if d <= p["r"] and (best is None or d < best[0]):
            best = (d, p)
    return best[1] if best else None


def offshore_of(lat, lon):
    """Repère le plus proche (port ou repère côtier) et distance en milles, pour « au large de … »."""
    cands = [(p["name"], p["lat"], p["lon"]) for p in PORT_LIST if p["rank"] <= 2] + LANDMARKS
    name, la, lo = min(cands, key=lambda c: nm((lat, lon), (c[1], c[2])))
    return name, round(nm((lat, lon), (la, lo)))
