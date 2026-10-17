"""Client minimal pour l'API REST de la plateforme de transparence ENTSO-E (web-api.tp.entsoe.eu).

Clé : variable d'environnement ENTSOE_TOKEN, sinon fichier ~/.config/entsoe/credentials (ligne ENTSOE_TOKEN=…).
Elle n'est jamais affichée, ni écrite, ni incluse dans les messages d'erreur.
Limite ENTSO-E : 400 requêtes par minute et par utilisateur — départs espacés d'au moins 0,25 s (≤ 240/min),
même quand plusieurs requêtes sont en vol (l'API met souvent plusieurs secondes à répondre).
"""
import io
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

BASE = "https://web-api.tp.entsoe.eu/api"
UA = "energie-odre-viz/1.0 (projet personnel)"
MIN_GAP = 0.25


class MissingToken(Exception):
    pass


class NoData(Exception):
    """Réponse « No matching data found » (Acknowledgement_MarketDocument)."""


def _token():
    tok = os.environ.get("ENTSOE_TOKEN")
    if not tok:
        path = os.path.expanduser("~/.config/entsoe/credentials")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("ENTSOE_TOKEN="):
                        tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not tok:
        raise MissingToken("clé ENTSO-E absente (ENTSOE_TOKEN)")
    return tok


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


class ENTSOE:
    def __init__(self):
        self._tok = _token()
        self._last = 0.0
        self._lock = threading.Lock()
        self.calls = 0

    def get(self, params, retries=4):
        """Renvoie la liste des racines XML (plusieurs si la réponse est un ZIP)."""
        query = urllib.parse.urlencode(params)
        for attempt in range(retries):
            with self._lock:  # espacement des départs, partagé entre les fils
                wait = self._last + MIN_GAP - time.time()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
                self.calls += 1
            url = f"{BASE}?securityToken={urllib.parse.quote(self._tok)}&{query}"
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
                    body = r.read()
            except urllib.error.HTTPError as e:
                body = e.read()
                if e.code == 400 and b"No matching data" in body:
                    raise NoData() from None
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(int(e.headers.get("Retry-After") or 10 * (attempt + 1)))
                    continue
                # message d'erreur ENTSO-E (sans l'URL, qui contient la clé)
                raise RuntimeError(f"HTTP {e.code} : {_reason(body)}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries - 1:
                    time.sleep(5 * (attempt + 1)); continue
                raise RuntimeError(f"réseau : {type(e).__name__}") from None
            if body[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    return [_strip_ns(ET.fromstring(z.read(n))) for n in z.namelist()]
            root = _strip_ns(ET.fromstring(body))
            if root.tag == "Acknowledgement_MarketDocument":
                reason = _reason(body)
                if "No matching data" in reason:
                    raise NoData()
                raise RuntimeError(reason)
            return [root]
        raise RuntimeError("échec après plusieurs tentatives")


def _reason(body):
    try:
        root = _strip_ns(ET.fromstring(body))
        return " ".join((t.text or "") for t in root.iter("text"))[:200] or root.tag
    except Exception:
        return body[:120].decode("utf-8", "replace")


def period_points(ts):
    """TimeSeries → [(début UTC ISO, résolution en minutes, position, élément Point)]"""
    import datetime as dt
    out = []
    for per in ts.iter("Period"):
        start = dt.datetime.strptime(per.find("timeInterval/start").text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
        res = per.find("resolution").text
        minutes = {"PT15M": 15, "PT30M": 30, "PT60M": 60, "P1D": 1440}.get(res, 60)
        for p in per.iter("Point"):
            pos = int(p.find("position").text)
            out.append((start + dt.timedelta(minutes=minutes * (pos - 1)), minutes, p))
    return out
