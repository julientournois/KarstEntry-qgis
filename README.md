# Karst Entry — distribution QGIS

Paquet prêt à l'emploi du plugin QGIS **Karst Entry** (saisie de phénomènes
karstiques de surface). Ce dépôt ne contient que ce qui est nécessaire aux
utilisateurs. Le code source complet, les tests et les outils de
développement sont dans [KarstEntry](https://github.com/julientournois/KarstEntry).

## Installation

- **Windows** : double-cliquer sur `install_plugin.bat`
- **Linux** : `chmod +x install_plugin.sh && ./install_plugin.sh`

Le script copie le dossier `karst_entry/` dans le profil QGIS détecté
(QGIS 3 et/ou 4). Activez ensuite l'extension dans QGIS :
*Extensions > Gérer et installer les extensions > Installées > Karst Entry*.

### Installation manuelle

Copier le dossier `karst_entry/` dans :

- Windows : `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\`
- Linux : `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/`

## Documentation

Voir [`KarstEntry_Documentation.pdf`](KarstEntry_Documentation.pdf), également accessible depuis l'onglet *Info*
du plugin.

## Licence

PolyForm Noncommercial 1.0 — usage non-commercial uniquement.
Contact : julien.tournois@gmail.com
