# Le réseau électrique français, en direct et sur quinze ans

Visualisation des données éCO2mix de RTE publiées sur ODRÉ (Open Data Réseaux Énergies) : mix de production jour après jour depuis 2012, quinze ans de mix annuel, carte de chaleur heure × jour, journées types, échanges aux frontières (carte animée), régions avec renouvelables, vent et ensoleillement (historique ou en direct), records ; et, via les API de pilotage RTE : carte des centrales ≥ 100 MW et de leur état, indisponibilités (frise des réacteurs), prévu / réalisé, ajustement ; prix spot depuis 2015.

En ligne : https://djac57.github.io/energie-odre/

## Structure

```
site/            page publiée : index.html, data.js (généré), geo.js (fonds de carte), favicon.svg
scripts/
  fetch.py       téléchargement ODRÉ → data/raw/
  build.py       agrégation → site/data.js
  build_geo.py   simplification des fonds de carte → site/geo.js (à lancer à la main)
  rte_api.py     client OAuth2 des API data.rte-france.com
  fetch_rte.py   API RTE → data/raw/rte/ (agrégé au pas horaire, fusionné, fenêtres glissantes)
  build_rte.py   → site/rte.js (non versionné, reconstruit par le workflow)
  rte_sites_extra.json  coordonnées des sites absents de la base JRC
  fetch_prices.py  prix spot Energy-Charts → data/raw/prices/<année>.json
  build_prices.py  → site/prices.js (non versionné, reconstruit par le workflow)
data/raw/        agrégats mensuels régionaux et météo (versionnés), exports nationaux (non versionnés)
```

## Mise à jour automatique

Le workflow GitHub Actions tourne chaque heure (API RTE et prix) et chaque jour (export ODRÉ complet et `site/data.js`), ainsi qu'à chaque push ; il committe les données brutes s'il y a du nouveau et déploie `site/` sur GitHub Pages. Les étapes RTE et prix ne peuvent pas casser le déploiement : sans identifiants ou en cas de panne, les dernières données versionnées sont conservées. Identifiants RTE : secrets `RTE_CLIENT_ID` / `RTE_CLIENT_SECRET` en CI, variables d'environnement ou `~/.config/rte/credentials` en local (jamais dans le dépôt). Le mode LIVE de la page interroge directement les API ODRÉ et Open-Meteo depuis le navigateur.

Les agrégats régionaux et météo sont **fusionnés** avec l'historique versionné, jamais remplacés : ODRÉ republie parfois un jeu en ne renvoyant qu'une partie de l'historique (cas du régional consolidé en octobre 2026).

## En local

```sh
python3 scripts/fetch.py data/raw
python3 scripts/build.py data/raw site/data.js
python3 scripts/fetch_rte.py data/raw && python3 scripts/build_rte.py data/raw site/rte.js
python3 scripts/fetch_prices.py data/raw && python3 scripts/build_prices.py data/raw site/prices.js
open site/index.html
```

Bibliothèque standard Python uniquement.

## Sources

- ODRÉ : `eco2mix-national-cons-def`, `eco2mix-national-tr`, `eco2mix-regional-cons-def`, `eco2mix-regional-tr`, `rayonnement-solaire-vitesse-vent-tri-horaires-regionaux` — Licence Ouverte 2.0.
- Météo en direct : Open-Meteo — CC BY 4.0.
- API RTE (data.rte-france.com, données publiques, réutilisation avec mention de la source, CGU art. 9) : Actual Generation v1, Generation Installed Capacities v1, Unavailability Additional Information v7, Generation Forecast v3, Consumption v1, Balancing Energy v5. L'API Wholesale Market (EPEX SPOT / Nord Pool, usage personnel uniquement) n'est pas utilisée.
- Prix spot day-ahead France : Bundesnetzagentur | SMARD.de, CC BY 4.0, via l'API Energy-Charts (Fraunhofer ISE).
- Coordonnées des centrales : JRC Open Power Plants Database (JRC-PPDB-OPEN 1.0, CC BY 4.0) par code EIC ; compléments Wikidata (CC0) et centres de communes geo.api.gouv.fr (Licence Ouverte).
- Fonds de carte : régions de france-geojson (Grégoire David, d'après Admin Express IGN, Licence Ouverte) ; pays Natural Earth via world-atlas (domaine public).
