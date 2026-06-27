<div align="center">

<img src="karst_entry/brand/karstentry-k-integre-accroche-inverse-1024.png" alt="Karst Entry" width="420">

# Karst Entry

**Plugin QGIS de saisie de terrain pour les phénomènes karstiques de surface.**

Paquet prêt à l'emploi destiné aux utilisateurs.
Le code source complet est dans
[**KarstEntry**](https://github.com/julientournois/KarstEntry).

[![Version](https://img.shields.io/badge/version-1.4-2b6cb0)](https://github.com/julientournois/KarstEntry/releases)
[![QGIS](https://img.shields.io/badge/QGIS-3.16%20%E2%86%92%204.x-589632?logo=qgis&logoColor=white)](https://qgis.org)
[![Licence](https://img.shields.io/badge/licence-PolyForm%20Noncommercial%201.0-555)](karst_entry/LICENSE)

</div>

---

## Fonctionnalités

- 📍 **Saisie par formulaire** des cavités, avec point capturé sur la carte
- 🔗 **Traçages hydrologiques** perte → résurgence (distance calculée)
- 🏛 **Localisation automatique** commune / code postal / département
- 📷 **Photos portables** (chemins relatifs, copiées à côté de la couche)
- ✏ **Modification, suppression, fiche de synthèse**
- 📥 **Import / export CSV** avec mapping automatique
- 💾 **Couches GeoPackage persistantes** sur disque

## Installation

- **Windows** : double-cliquer sur `install_plugin.bat`
- **Linux** : `chmod +x install_plugin.sh && ./install_plugin.sh`

Le script copie le dossier `karst_entry/` dans le profil QGIS détecté
(QGIS 3 et/ou 4). Activez ensuite l'extension dans QGIS :
**Extensions → Gérer et installer les extensions → Installées → Karst Entry**.

Le plugin n'a **aucune dépendance externe** (stdlib Python + PyQGIS).

### Installation manuelle

Copier le dossier `karst_entry/` dans :

- Windows : `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\`
- Linux : `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/`

## Documentation

Voir [`KarstEntry_Documentation.pdf`](karst_entry/KarstEntry_Documentation.pdf), également
accessible depuis l'onglet *Info* du plugin.

## Licence

PolyForm Noncommercial 1.0 — usage non-commercial uniquement.
Contact : julien.tournois@gmail.com
