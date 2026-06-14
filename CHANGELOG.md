# Changelog — Karst Entry

Toutes les évolutions notables du plugin. Format inspiré de
[Keep a Changelog](https://keepachangelog.com/fr/). Schéma de données partagé
avec **KarstPro** (`karst_schema.json`, v1.2.0).

## [1.0] — 2026-06-13

Première version stable et son affinage. Saisie de terrain des phénomènes
karstiques de surface dans QGIS (3.16 → 4.x), sans dépendance externe.

### Saisie & couches
- Saisie de **cavités** par formulaire avec capture de point sur la carte,
  dates, explorateurs, commentaire et photos ; file d'attente pour enchaîner
  plusieurs saisies avant écriture.
- Saisie de **traçages hydrologiques** : point d'injection → sortie du colorant,
  colorant, résultat, dates, `distance_m` calculée par mesure géodésique.
- Couches **GeoPackage persistantes** sur disque (`Inventaire Cavités`,
  `Inventaire Traçages`), créées et réutilisées automatiquement — les données
  survivent au redémarrage de QGIS.
- **Photos portables** : copiées à côté du GeoPackage avec des chemins relatifs
  (couche + photos déplaçables d'un bloc).

### Localisation administrative automatique
- Remplissage **commune / code postal / département** (+ codes INSEE/dépt) à la
  capture, via [geo.api.gouv.fr](https://geo.api.gouv.fr) (point-dans-polygone).
- Appel réseau **asynchrone** : l'interface ne gèle jamais, même hors-ligne ;
  indicateur d'état discret, jamais bloquant.
- **Cache communal** local (un appel réseau par commune) et **persistant** sur
  disque (`karst_commune_cache.json`), rechargé à la session suivante.

### Recherche, filtres et vues
- **Recherche** (référence / nom / type) et **filtre par type** dans les onglets
  Modification, Fiche, Traçage et Suppression — insensible à la casse **et aux
  accents**.
- Onglet **Vues** : génère une couche filtrée vivante par valeur d'un champ
  (ex. une couche par commune), pointant sur le même GeoPackage (sans copie).
- **Fiche de synthèse** : champs vides masqués pour une lecture plus claire.

### Import / export
- **Import CSV** avec mapping automatique des colonnes, détection du CRS,
  destination nouvelle couche / couche existante / fichier, roundtrip des photos.
- **Export CSV** des cavités avec dossiers photos.
- **Dédoublonnage** à la saisie et à l'import : détection des points coïncidant
  (< 2 m), en **distance métrique réelle** correcte quel que soit le CRS.

### Robustesse
- Reprojection des coordonnées capturées vers le CRS de la couche à l'écriture
  (corrige des géométries fausses en cas de CRS projet ≠ CRS couche).
- **Migration de schéma** : proposition d'ajouter les colonnes manquantes aux
  couches créées par une version antérieure, au lieu de perdre des valeurs.
- Couches résolues par identifiant (jamais d'objet caché) pour éviter les
  erreurs « C/C++ object deleted ».

### Distribution & documentation
- Installeurs **Windows** (`install_plugin.bat`) et **Linux**
  (`install_plugin.sh`), détection automatique des profils QGIS 3 et 4.
- Script de **packaging** (`package.py`) produisant une archive datée et versionnée.
- **Guide PDF illustré** (`KarstEntry_Documentation.pdf`), accessible depuis
  l'onglet Info.
- Schéma aligné 1:1 avec KarstPro (v1.2.0).

### Outil annexe (hors plugin)
- `split_par_commune.py` : découpe un GeoPackage en un fichier par commune
  (export figé, complémentaire de l'onglet Vues).
