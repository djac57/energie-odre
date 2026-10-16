# Le réseau électrique français, en direct et sur quinze ans

Visualisation des données éCO2mix de RTE publiées sur ODRÉ (Open Data Réseaux Énergies) : mix de production jour après jour depuis 2012, quinze ans de mix annuel, carte de chaleur heure × jour, journées types, échanges aux frontières (carte animée), régions avec renouvelables, vent et ensoleillement (historique ou en direct), records.

En ligne : https://djac57.github.io/energie-odre/

## Structure

```
site/            page publiée : index.html, data.js (généré), geo.js (fonds de carte), favicon.svg
scripts/
  fetch.py       téléchargement ODRÉ → data/raw/
  build.py       agrégation → site/data.js
  build_geo.py   simplification des fonds de carte → site/geo.js (à lancer à la main)
data/raw/        agrégats mensuels régionaux et météo (versionnés), exports nationaux (non versionnés)
```

## Mise à jour automatique

Le workflow GitHub Actions tourne chaque jour et à chaque push : il retélécharge les données ODRÉ, régénère `site/data.js`, committe s'il y a du nouveau et déploie `site/` sur GitHub Pages. Le mode LIVE de la page interroge directement les API ODRÉ et Open-Meteo depuis le navigateur.

Les agrégats régionaux et météo sont **fusionnés** avec l'historique versionné, jamais remplacés : ODRÉ republie parfois un jeu en ne renvoyant qu'une partie de l'historique (cas du régional consolidé en octobre 2026).

## En local

```sh
python3 scripts/fetch.py data/raw
python3 scripts/build.py data/raw site/data.js
open site/index.html
```

Bibliothèque standard Python uniquement.

## Sources

- ODRÉ : `eco2mix-national-cons-def`, `eco2mix-national-tr`, `eco2mix-regional-cons-def`, `eco2mix-regional-tr`, `rayonnement-solaire-vitesse-vent-tri-horaires-regionaux` — Licence Ouverte 2.0.
- Météo en direct : Open-Meteo — CC BY 4.0.
- Fonds de carte : régions de france-geojson (Grégoire David, d'après Admin Express IGN, Licence Ouverte) ; pays Natural Earth via world-atlas (domaine public).
