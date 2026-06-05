#!/usr/bin/env bash
# Installation du plugin Karst Entry sous Linux.
# Copie le plugin dans les profils QGIS 3 et/ou QGIS 4 détectés.
#
# Usage :
#   ./install_plugin.sh
#
# Emplacements standards des plugins (profil « default ») :
#   ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/karst_entry
#   ~/.local/share/QGIS/QGIS4/profiles/default/python/plugins/karst_entry
# Surchargables via la variable d'environnement QGIS_DATA_HOME.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="karst_entry"
# Paquet utilisateur : le plugin est dans un sous-dossier karst_entry/ ;
# dépôt de dev : le plugin est à côté du script.
if [ -d "$SCRIPT_DIR/$PLUGIN_NAME" ]; then
    SOURCE="$SCRIPT_DIR/$PLUGIN_NAME"
else
    SOURCE="$SCRIPT_DIR"
fi
QGIS_HOME="${QGIS_DATA_HOME:-$HOME/.local/share/QGIS}"

# Fichiers/dossiers à ne pas déployer dans le plugin installé.
EXCLUDES=(
    ".git" ".gitignore" "__pycache__" ".pytest_cache"
    "tests" "docs/build_guide_pdf.py"
    "install_plugin.bat" "install_plugin.sh"
)

installed=0

install_into() {
    local qgis_ver="$1"
    local base="$QGIS_HOME/$qgis_ver"
    [ -d "$base" ] || return 0

    local dest="$base/profiles/default/python/plugins/$PLUGIN_NAME"
    rm -rf "$dest"
    mkdir -p "$dest"

    if command -v rsync >/dev/null 2>&1; then
        local args=()
        for e in "${EXCLUDES[@]}"; do args+=(--exclude "$e"); done
        rsync -a "${args[@]}" "$SOURCE"/ "$dest"/
    else
        # Repli sans rsync : copie tout puis retire les exclusions.
        cp -a "$SOURCE"/. "$dest"/
        for e in "${EXCLUDES[@]}"; do rm -rf "$dest/$e"; done
    fi

    echo "[$qgis_ver] Plugin installé dans : $dest"
    installed=1
}

install_into "QGIS3"
install_into "QGIS4"

if [ "$installed" -eq 0 ]; then
    echo "Aucune installation QGIS 3 ou 4 détectée dans $QGIS_HOME/"
    echo "Définissez QGIS_DATA_HOME si votre profil est ailleurs."
    exit 1
fi

echo "Rechargez le plugin dans QGIS (Extensions > Recharger) ou redémarrez QGIS."
