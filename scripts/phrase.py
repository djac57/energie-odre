"""« Phrase du jour » : une phrase déterministe (sans LLM) tirée des chiffres de data.js.

    phrase_du_jour(data) -> str | None

`data` est le dictionnaire écrit dans site/data.js par build.py (clés start, days, lastStamp, sources, daily).
Jour retenu : celui du dernier relevé s'il couvre au moins 6 heures, sinon la veille (jour complet).
Chaque morceau de phrase n'est écrit que si ses chiffres existent ; s'il n'en reste aucun, None (la page masque le bloc).

    python3 scripts/phrase.py site/data.js   # affiche la phrase calculée sur un data.js existant
"""
import datetime as dt
import json
import sys

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
FRACTIONS = [(1 / 10, "un dixième"), (1 / 5, "un cinquième"), (1 / 4, "un quart"), (1 / 3, "un tiers"), (2 / 5, "deux cinquièmes"),
             (1 / 2, "la moitié"), (3 / 5, "trois cinquièmes"), (2 / 3, "les deux tiers"), (3 / 4, "les trois quarts")]
MIN_HOURS = 6


def _de(x):
    """« de » élidé ou contracté devant une fraction : d'un quart, de la moitié, des trois quarts."""
    if x.startswith("les "):
        return "des " + x[4:]
    return ("d'" if x[0] in "aeiouéh" else "de ") + x


def _pct(r):
    return f"{round(r * 100)}\u00a0%"


def _part(r):
    """« près d'un quart (24 %) », « un tiers (33 %) » ; en dehors de 8–85 % : le pourcentage seul."""
    if r < 0.08 or r > 0.85:
        return _pct(r)
    f, name = min(FRACTIONS, key=lambda t: abs(t[0] - r))
    if abs(f - r) <= 0.012:
        words = name
    elif abs(f - r) > 0.025:  # trop loin d'une fraction simple : le pourcentage seul
        return _pct(r)
    elif r < f:
        words = "près " + _de(name)
    else:
        words = "un peu plus " + _de(name)
    return f"{words} ({_pct(r)})"


def _gw(mw):
    gw = abs(mw) / 1000
    txt = f"{gw:.1f}".replace(".", ",").replace(",0", "") if gw < 10 else f"{gw:.0f}"
    return txt + "\u00a0GW"


def phrase_du_jour(data):
    try:
        start = dt.date.fromisoformat(data["start"])
        daily, n = data["daily"], int(data["days"])
        stamp = data.get("lastStamp") or ""
        last_hour = int(stamp[11:13]) if len(stamp) >= 13 else 24
    except (KeyError, TypeError, ValueError):
        return None
    i, partial = n - 1, True
    if last_hour < MIN_HOURS:
        i, partial = n - 2, False
    if i < 0:
        return None

    def val(k):
        a = daily.get(k)
        return a[i] if a and i < len(a) else None

    day = start + dt.timedelta(days=i)
    when = f"{JOURS[day.weekday()]} {day.day}{'er' if day.day == 1 else ''} {MOIS[day.month - 1]}"
    if partial and last_hour < 23:
        mm = stamp[14:16]
        when += f", de 0 h à {last_hour} h" + (f" {mm}" if mm and mm != "00" else "")

    parts = []
    prod_vals = [val(k) for k in data.get("sources", [])]
    # part du nucléaire : seulement si toutes les filières sont connues (sinon la part serait gonflée)
    prod = sum(prod_vals) if prod_vals and all(v is not None for v in prod_vals) else None
    nuc, conso, eol, ech = val("nucleaire"), val("consommation"), val("eolien"), val("ech_physiques")
    if prod and nuc is not None:
        parts.append(f"{_pct(nuc / prod)} de l'électricité produite vient du nucléaire")
    if conso and conso > 0:
        if eol is not None:
            parts.append(f"le vent couvre {_part(eol / conso)} de la consommation")
    if ech is not None and abs(ech) >= 500:
        parts.append(f"la France {'exporte' if ech < 0 else 'importe'} {_gw(ech)} en moyenne")
    if not parts:
        return None
    return f"{when} : " + " ; ".join(parts) + "."


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "site/data.js"
    s = open(path, encoding="utf-8").read()
    s = s[s.index("{"): s.rstrip().rstrip(";").rindex("}") + 1]
    print(phrase_du_jour(json.loads(s)))
