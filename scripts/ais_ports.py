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
    ("USSAB", "Sabine Pass", 29.730, -93.870, 3, r"SABINE", None, 4),  # code de la ville de Sabine, souvent déclaré
    ("USCMU", "Cameron", 29.960, -93.330, 3, r"CAMERON", None, 4),
    ("USCRP", "Corpus Christi", 27.810, -97.400, 3, r"CORPUS", None, 4),
    ("NGBON", "Bonny", 4.420, 7.150, 3, r"BONNY", None, 4),
    # --- Terminaux d'exportation lointains (départs possibles) : coordonnées seules, codes non vérifiés omis
    (None, "Freeport (Texas)", 28.940, -95.310, 3, r"FREEPORT", None, 4),
    (None, "Houston", 29.730, -95.010, 3, r"HOUSTON", None, 6),
    (None, "Calcasieu Pass", 29.770, -93.340, 3, r"CALCASIEU", None, 3),
    (None, "Plaquemines LNG", 29.580, -89.680, 3, r"PLAQUEMINES", None, 3),
    (None, "Cove Point", 38.400, -76.400, 3, r"COVE POINT", None, 3),
    (None, "Point Fortin (Trinité)", 10.180, -61.700, 3, r"FORTIN", None, 3),
    (None, "Ras Tanura", 26.650, 50.170, 3, r"TANURA", None, 5),
    (None, "Juaymah", 26.850, 50.000, 3, r"JUAYMAH", None, 6),
    (None, "Yanbu", 24.000, 38.080, 3, r"YANBU", None, 5),
    (None, "Fujairah", 25.170, 56.370, 3, r"FUJAIRAH", None, 6),
    (None, "Mina al-Ahmadi", 29.060, 48.160, 3, r"AHMADI", None, 5),
    (None, "Bassorah", 29.700, 48.800, 3, r"BASRA", None, 8),
    (None, "Das Island", 25.150, 52.870, 3, r"DAS ISLAND", None, 4),
    (None, "Sidi Kerir", 31.110, 29.630, 3, r"SIDI KERIR", None, 4),
    (None, "Idku", 31.330, 30.300, 3, r"IDKU", None, 4),
    (None, "Zawiya", 32.780, 12.720, 3, r"ZAWIYA|ZAWIA", None, 3),
    (None, "Bethioua", 35.810, -0.270, 3, r"BETHIOUA", None, 3),
    (None, "Forcados", 5.200, 5.200, 3, r"FORCADOS", None, 8),
    (None, "Escravos", 5.500, 5.000, 3, r"ESCRAVOS", None, 8),
    (None, "Brass", 4.200, 6.300, 3, r"BRASS", None, 6),
    (None, "Bonga (FPSO)", 4.550, 4.600, 3, r"BONGA", None, 6),
    (None, "Soyo (Angola LNG)", -6.100, 12.330, 3, r"SOYO", None, 4),
    (None, "Luanda", -8.780, 13.250, 3, r"LUANDA", None, 4),
    (None, "Djeno", -4.920, 11.880, 3, r"DJENO", None, 5),
    (None, "Kribi", 2.900, 9.870, 3, r"KRIBI", None, 5),
    (None, "Abidjan", 5.270, -4.010, 3, r"ABIDJAN", None, 4),
    (None, "Murmansk", 69.000, 33.050, 3, r"MURMANSK", None, 4),
    (None, "Sabetta (Yamal LNG)", 71.270, 72.070, 3, r"SABETTA", None, 4),
    (None, "Sikka / Jamnagar", 22.440, 69.800, 3, r"SIKKA|JAMNAGAR", None, 6),
    (None, "Sohar", 24.500, 56.630, 3, r"SOHAR", None, 4),
    (None, "Qalhat (Oman LNG)", 22.700, 59.400, 3, r"QALHAT", None, 3),
    (None, "Point Lisas", 10.400, -61.480, 3, r"LISAS", None, 3),
    (None, "Bethany / Altamira", 22.480, -97.860, 3, r"ALTAMIRA", None, 3),
    (None, "Santos", -23.980, -46.300, 3, r"SANTOS", None, 4),
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


def is_french(port):
    return bool(port) and ((port["code"] or "").startswith("FR") or bool(port["group"]))


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
            # motif en début de mot (« VENICE » n'est pas Nice), ou sur le texte collé (« LEHAVRE »)
            if re.search(r"\b(?:%s)" % p["re"], compact) or (" " not in compact.strip() and re.match(r"(?:%s)" % p["re"], compact.strip())):
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
    cands = [(p["name"], p["lat"], p["lon"]) for p in PORT_LIST if p["rank"] <= 2 or p["code"] is None] + LANDMARKS
    name, la, lo = min(cands, key=lambda c: nm((lat, lon), (c[1], c[2])))
    d = round(nm((lat, lon), (la, lo)))
    return (name, d) if d <= 200 else (region(lat, lon), None)  # très au large : la zone maritime


# Grandes zones maritimes (lat min, lat max, lon min, lon max), de la plus précise à la plus large
REGIONS = [
    ("Manche et mer du Nord", 48.3, 62, -6, 10), ("golfe de Gascogne", 43, 48.3, -10, -1), ("Méditerranée occidentale", 35, 44.5, -6, 12),
    ("Méditerranée orientale", 30, 41, 12, 37), ("mer Baltique", 53.5, 66, 10, 31), ("mer de Norvège et Arctique", 62, 80, -10, 75),
    ("mer Noire", 40.5, 47.5, 27, 42), ("mer Rouge", 12, 30, 32, 44), ("golfe Persique", 23, 31, 47, 57), ("mer d'Arabie et océan Indien", -40, 25, 44, 100),
    ("golfe du Mexique", 18, 31, -98, -80), ("mer des Caraïbes", 8, 22, -88, -59), ("côte est des États-Unis", 24, 46, -82, -60),
    ("golfe de Guinée", -15, 8, -10, 15), ("côtes d'Afrique de l'Ouest", 8, 35, -26, -5), ("Atlantique Sud", -60, -15, -70, 20),
    ("Atlantique Nord", 0, 70, -80, -5), ("Asie-Pacifique", -50, 70, 100, 180),
]


def region(lat, lon):
    for name, a, b, c, d in REGIONS:
        if a <= lat <= b and c <= lon <= d:
            return name
    return "autre"


# Origine probable (best guess) d'un navire en route vers la France quand son départ n'a pas été observé :
# grands terminaux d'exportation, par type de cargaison (coordonnées saisies à la main, approximatives).
EXPORT_TERMINALS = [
    # méthaniers : usines de liquéfaction
    ("Sabine Pass", 29.73, -93.87, "lng"), ("Cameron LNG", 29.96, -93.33, "lng"), ("Calcasieu Pass", 29.77, -93.34, "lng"),
    ("Corpus Christi", 27.88, -97.27, "lng"), ("Freeport LNG", 28.94, -95.31, "lng"), ("Plaquemines LNG", 29.58, -89.68, "lng"),
    ("Cove Point", 38.40, -76.40, "lng"), ("Point Fortin (Trinité)", 10.18, -61.70, "lng"), ("Ras Laffan", 25.92, 51.60, "lng"),
    ("Das Island", 25.15, 52.87, "lng"), ("Qalhat (Oman)", 22.70, 59.40, "lng"), ("Bonny", 4.42, 7.15, "lng"), ("Soyo (Angola)", -6.10, 12.33, "lng"),
    ("Hammerfest (Melkøya)", 70.69, 23.60, "lng"), ("Arzew / Bethioua", 35.82, -0.28, "lng"), ("Skikda", 36.89, 6.92, "lng"),
    ("Damiette", 31.47, 31.76, "lng"), ("Idku", 31.33, 30.30, "lng"), ("Sabetta (Yamal)", 71.27, 72.07, "lng"),
    ("Kribi (Cameroun)", 2.90, 9.87, "lng"), ("Bioko (Guinée équatoriale)", 3.78, 8.73, "lng"),
    # pétroliers : grands terminaux d'exportation de brut et de produits
    ("Houston", 29.73, -95.01, "oil"), ("Corpus Christi", 27.81, -97.40, "oil"), ("Ras Tanura", 26.65, 50.17, "oil"), ("Yanbu", 24.00, 38.08, "oil"),
    ("Bassorah", 29.70, 48.80, "oil"), ("Fujairah", 25.17, 56.37, "oil"), ("Sikka / Jamnagar", 22.44, 69.80, "oil"), ("Ceyhan", 36.88, 35.93, "oil"),
    ("Novorossiisk", 44.72, 37.79, "oil"), ("Primorsk", 60.35, 28.70, "oil"), ("Mongstad", 60.81, 5.03, "oil"), ("Sture", 60.61, 4.85, "oil"),
    ("Es Sider", 30.63, 18.35, "oil"), ("Zawiya", 32.78, 12.72, "oil"), ("Arzew", 35.85, -0.30, "oil"), ("Skikda", 36.89, 6.92, "oil"),
    ("Sidi Kerir", 31.11, 29.63, "oil"), ("Forcados", 5.20, 5.20, "oil"), ("Bonny", 4.42, 7.15, "oil"), ("Djeno (Congo)", -4.92, 11.88, "oil"),
    ("Luanda", -8.78, 13.25, "oil"), ("Abidjan", 5.27, -4.01, "oil"), ("Rotterdam", 51.95, 4.10, "oil"), ("Anvers", 51.28, 4.30, "oil"),
    ("Santos", -23.98, -46.30, "oil"), ("Point Lisas", 10.40, -61.48, "oil"), ("Algésiras", 36.13, -5.43, "oil"), ("Sines", 37.94, -8.87, "oil"),
]


def _bearing(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def guess_origin(kind, lat, lon, cog=None, sog=None, dest=None):
    """Terminal d'exportation le plus plausible : le plus proche de la première observation parmi ceux
    du bon type situés « derrière » le navire (sens opposé à sa route), hors port de destination."""
    best = None
    for name, la, lo, k in EXPORT_TERMINALS:
        if k != kind:
            continue
        d = nm((lat, lon), (la, lo))
        if dest and nm((la, lo), dest) < 60:
            continue
        if cog is not None and sog and sog > 3 and d > 30:
            diff = abs((_bearing((lat, lon), (la, lo)) - cog + 180) % 360 - 180)
            if diff < 100:  # le navire fait route vers ce terminal : ce n'est pas son origine
                continue
        if best is None or d < best[0]:
            best = (d, name, la, lo)
    return {"name": best[1], "lat": best[2], "lon": best[3]} if best else None
