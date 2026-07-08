# Changelog — Karst Entry

Toutes les évolutions notables du plugin. Format inspiré de
[Keep a Changelog](https://keepachangelog.com/fr/). Schéma de données partagé
avec **KarstPro** (`karst_schema.json`, v1.3.0).

## [1.7] — 2026-07-08

### Corrigé
- **Dépôt QGIS custom (mises à jour automatiques)** : installation depuis le dépôt impossible et logo absent, corrigés.

## [1.6] — 2026-07-08

### Ajouté
- **Dépôt QGIS custom** (mises à jour automatiques, sans passer par le dépôt
  officiel — licence PolyForm Noncommercial incompatible GPL) :
  `generate_plugins_xml.py` produit le manifeste `plugins.xml`, hébergé sur
  GitHub Pages du dépôt de distribution.
- **`add_qgis_repo.bat`/`.sh`/`.py`** : enregistre automatiquement ce dépôt
  dans QGIS (tous profils détectés, QGIS 3 et 4, Windows/Linux/macOS) — plus
  besoin de le faire à la main dans Extensions → Paramètres → Dépôts.
- `metadata.txt` : `homepage`, `repository`, `tracker` renseignés (étaient
  vides depuis toujours).

## [1.5] — 2026-07-03

### Ajouté
- **Sauvegarde automatique avant import sur couche existante** : une copie
  datée du jour du GeoPackage est créée dans le même dossier avant toute
  modification (schéma ou import). Une seule sauvegarde conservée par jour
  (n'écrase pas l'état d'avant le premier import de la journée).

### Interne
- **Nettoyage du code** : suppression d'imports Qt/QGIS devenus morts après le
  refactor en mixins (résidus d'une extraction mécanique), factorisation de la
  logique de persistance GeoPackage à l'import (cavités/traçages) désormais
  partagée (`_persist_new_layer_as_gpkg`), suppression d'un fichier vide
  committé par erreur. Aucun changement de comportement fonctionnel.
- Correction du test de cohérence de schéma et de deux tests d'intégration
  affectés par le nettoyage.

## [1.4] — 2026-06-27

### Corrigé
- **Traçages non affichés** : la géométrie des traçages ajoutés dans une couche
  dont le CRS diffère de celui du projet n'était pas reprojetée (même classe de
  bug que la reprojection cavités) — coordonnées écrites dans les mauvaises
  unités, traçages invisibles ou hors champ. `_tr_flush_queue` reprojette
  désormais systématiquement vers le CRS de la couche cible.
- **Résilience du géocodage** : `reverse_geocode` réessaie les erreurs
  réseau/timeout (backoff exponentiel) avant d'abandonner.

### Interne
- Test de cohérence du schéma partagé avec KarstPro (verrouille version et
  champs noyau, compare aux deux copies si trouvables) — évite une divergence
  silencieuse comme celle déjà rencontrée (`altitude` vs `altitude_m`).

### Ajouté
- **Traçages : import / export complet** sans nouveau bouton. L'export sérialise
  la géométrie ligne en colonne **WKT** ; l'import auto-détecte un CSV de
  traçages et reconstruit la couche. Le sélecteur « Couche active » liste
  désormais les couches de points **et** de lignes.
- **Import : toujours une vraie couche GeoPackage** (plus de couche mémoire
  volatile) — annuler la boîte d'enregistrement utilise l'emplacement par défaut.

### Performance
- Import CSV en **lot** (`addFeatures`) au lieu d'entité par entité — nettement
  plus rapide sur les gros inventaires.

### Corrigé
- **Roundtrip CSV** : la colonne réservée `fid` (clé primaire GeoPackage) n'est
  plus exportée et est ignorée à l'import (corrige « wrong field type for fid »
  et `KeyError: 'fid'`).
- **Export CSV** : plus de faux « export interrompu », et plus d'erreur de copie
  quand la photo source est déjà dans le dossier de destination (WinError 32).
- **Barre de progression** : modale et visible pendant toute la durée de
  l'opération (imports comme exports), bouton Annuler fonctionnel.

### Interne
- Refactor : `karst_dialog.py` scindé en modules (`ui_tabs`, `layers`, `schema`,
  `ui_constants`, `csv_io`). Boucle de tests d'intégration **PyQGIS réelle**
  (`run_tests.ps1` + smoke).

## [1.3] — 2026-06-27

### Ajouté
- **Barre de progression** à l'import (nouvelle couche / couche existante) et à
  l'export (CSV, ZIP, GPX), avec bouton **Annuler** — utile sur les gros
  inventaires. La fenêtre ne s'affiche que si l'opération dure (> ~0,4 s).

### Corrigé
- **Export CSV — encodage** : les fichiers sont désormais écrits en UTF-8 **avec
  BOM** (`utf-8-sig`). Excel sous Windows détecte correctement l'UTF-8 ; fini les
  caractères parasites type `NÂ°` à la place de `N°`. La réimport reste compatible.

## [1.2] — 2026-06-27

### Ajouté
- **Photos rangées par couche** : à la saisie, à l'import et à l'export, les
  images sont copiées sous `<nom_couche>/<référence>/` — le nom de dossier est
  assaini (sans accents, espaces ni caractères spéciaux). Plusieurs couches d'un
  même dossier ne mélangent plus leurs photos.
- **Import CSV — nettoyage du commentaire** : le boilerplate HTML/Microsoft
  Office du champ `comment` (balises, styles `mso-…`, entités `&nbsp;`/`&quot;`)
  est retiré automatiquement à l'import.

### Documentation
- Guide utilisateur : arborescence type d'un dossier projet illustrée, et
  schémas des couches présentés sous forme de tableaux (Champ / Type / Description).

## [1.1] — 2026-06-14

### Schéma
- **Champ `altitude`** (Double, mètres) ajouté aux couches `cavites` et
  `cavites_connues` — **schéma v1.3.0**. Saisi dans le formulaire, exporté en
  `ele` GPX, mappé automatiquement depuis `alt`/`altitude` à l'import.
  ⚠ À répercuter **à l'identique dans KarstPro** (schéma partagé).

### Ajouté
- **Placement du point depuis une photo géolocalisée** (EXIF GPS) : à l'ajout
  de photos, si aucune position n'est saisie, proposition de placer la cavité
  à l'emplacement de la photo (via `QgsExifTools`, sans dépendance).
- **Export GPX** des cavités en waypoints (WGS84) pour recharger l'inventaire
  sur un GPS de terrain.
- **Export ZIP** : archive unique CSV + photos (portable, à partager d'un bloc).
- **Import CSV → nouvelle couche persistante** : possibilité d'enregistrer la
  couche créée dans un GeoPackage sur disque (emplacement au choix), au lieu
  d'une couche mémoire volatile.
- **Symbologie automatique** appliquée à la création des couches **et à
  l'import** (nouvelle couche avec champ `type`) : cavités catégorisées par
  `type`, traçages par `resultat` ; style enregistré dans le GeoPackage
  (repris par QField et aux réouvertures).
- **Onglet Stats** : statistiques par commune (nombre, développement cumulé,
  ventilation par type) avec export CSV du récapitulatif, et bouton
  **« Remplir les communes manquantes »** — géocodage par lot (asynchrone,
  cache communal, ne touche que les champs vides) des entités sans commune.

- **Choix de la couche de destination** (Saisie, Traçage, Import CSV) : couche
  existante (avec **validation du schéma** — géométrie vérifiée, ajout des
  colonnes manquantes proposé) ou **nouvelle couche au nom choisi** (défaut
  « Inventaire Cavités » / « Inventaire Traçages »).

- **Import CSV — mapping universel** : correspondance automatique des colonnes
  insensible à la casse, aux accents et aux espaces, avec **synonymes**
  (`numero`→`reference`, `nom`→`name`, `lon`/`long`→`x`, `lat`→`y`,
  `alt`→`altitude`, `commentaire`→`comment`…). Lecture x/y tolérante de même.

### Corrigé
- **Import CSV** : en-têtes avec espaces parasites (« nom ») désormais
  normalisés (ne cassent plus la lecture des valeurs).
- **Import CSV** : détection automatique de l'encodage (UTF-8 / Windows-1252) —
  les CSV produits sous Excel FR (« é » = 0xe9) ne provoquent plus d'erreur de
  décodage.

### Qualité
- **Smoke tests d'intégration** sous un vrai PyQGIS (construction des onglets,
  mesure géodésique, aller-retour GeoPackage).
- **Intégration continue** (GitHub Actions) : suite mockée + smoke QGIS à
  chaque push.

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
