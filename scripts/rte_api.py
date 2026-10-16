"""Client minimal pour les API du portail data.rte-france.com (OAuth2 « client credentials »).

Identifiants : variables d'environnement RTE_CLIENT_ID / RTE_CLIENT_SECRET, sinon fichier
~/.config/rte/credentials (lignes KEY=VALUE). Ils ne sont jamais affichés ni écrits.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://digital.iservices.rte-france.com/token/oauth/"
BASE = "https://digital.iservices.rte-france.com/open_api/"


class MissingCredentials(Exception):
    pass


def _credentials():
    cid, secret = os.environ.get("RTE_CLIENT_ID"), os.environ.get("RTE_CLIENT_SECRET")
    if not (cid and secret):
        path = os.path.expanduser("~/.config/rte/credentials")
        if os.path.exists(path):
            vals = {}
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, v = line.split("=", 1)
                        vals[k.strip()] = v.strip().strip('"').strip("'")
            cid, secret = cid or vals.get("RTE_CLIENT_ID"), secret or vals.get("RTE_CLIENT_SECRET")
    if not (cid and secret):
        raise MissingCredentials("identifiants RTE absents (RTE_CLIENT_ID / RTE_CLIENT_SECRET)")
    return cid, secret


class RTE:
    def __init__(self):
        self._token, self._exp = None, 0

    def token(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        cid, secret = _credentials()
        auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        req = urllib.request.Request(TOKEN_URL, data=b"", method="POST",
                                     headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
        self._token, self._exp = body["access_token"], time.time() + int(body.get("expires_in", 3600))
        return self._token

    def get(self, path, params=None, retries=3):
        """GET JSON ; suit les jetons de continuation (HTTP 206) des indisponibilités."""
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        out, cont = [], None
        while True:
            headers = {"Authorization": "Bearer " + self.token(), "Accept": "application/json"}
            if cont:
                headers["continuation_token"] = cont
            for attempt in range(retries):
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as r:
                        status, body = r.status, r.read()
                        cont_next = r.headers.get("continuation_token") or r.headers.get("continuation-token")
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < retries - 1:
                        time.sleep(int(e.headers.get("Retry-After") or 30))
                        continue
                    msg = e.read()[:300].decode("utf-8", "replace")
                    raise RuntimeError(f"{path} : HTTP {e.code} {msg}") from None
            data = json.loads(body) if body else {}
            out.append(data)
            if status == 206 and cont_next:
                cont = cont_next
                continue
            return out if len(out) > 1 else out[0]


