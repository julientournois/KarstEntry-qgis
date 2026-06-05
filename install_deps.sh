#!/usr/bin/env bash
# Karst Entry — Installation des dépendances Python (Linux / macOS)
# Détecte le Python de QGIS et installe les paquets de requirements.txt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# requirements.txt : à côté du script (dépôt) ou dans karst_entry/ (paquet)
REQ="${SCRIPT_DIR}/requirements.txt"
[ -f "${REQ}" ] || REQ="${SCRIPT_DIR}/karst_entry/requirements.txt"

echo ""
echo "============================================================"
echo "  Karst Entry — Installation des dépendances Python"
echo "============================================================"
echo ""

if [ ! -f "${REQ}" ]; then
    echo "[ERREUR] requirements.txt introuvable."
    exit 1
fi

# Le fichier contient-il au moins une vraie dépendance (hors # et lignes vides) ?
if ! grep -qE '^[[:space:]]*[^#[:space:]]' "${REQ}"; then
    echo "[INFO] Aucune dépendance externe requise par Karst Entry."
    echo "       Le plugin n'utilise que la stdlib Python et PyQGIS."
    echo "       Rien à installer."
    echo ""
    exit 0
fi

# ── Détection du Python QGIS ──────────────────────────────────────────────────
PYTHON_EXE=""
QGIS_VER=""

if command -v python3 &>/dev/null && python3 -c "import qgis" &>/dev/null 2>&1; then
    PYTHON_EXE="$(command -v python3)"
    QGIS_VER="$(python3 -c "from qgis.core import Qgis; print(Qgis.QGIS_VERSION.split('.')[0])" 2>/dev/null || echo '?')"
    echo "[OK] QGIS détecté via le Python courant — version QGIS ${QGIS_VER}"
elif dpkg -s python3-qgis &>/dev/null 2>&1; then
    PYTHON_EXE="$(command -v python3)"
    QGIS_VER="?"
    echo "[OK] QGIS détecté via le paquet système python3-qgis"
else
    for FP_DIR in "/var/lib/flatpak/app/org.qgis.qgis" "${HOME}/.local/share/flatpak/app/org.qgis.qgis"; do
        if [ -d "${FP_DIR}" ]; then
            echo "[INFO] QGIS Flatpak détecté. Installez depuis un terminal Flatpak :"
            echo "         flatpak run --command=bash org.qgis.qgis"
            echo "         pip install -r requirements.txt"
            exit 0
        fi
    done
fi

if [ -z "${PYTHON_EXE}" ]; then
    echo "[ERREUR] QGIS introuvable."
    echo "  - sudo apt install qgis python3-qgis"
    echo "  - ou activez l'environnement conda contenant QGIS, puis relancez."
    exit 1
fi

echo "     Python : ${PYTHON_EXE}"
echo ""

PIP_FLAGS="--upgrade"
if python3 -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)" &>/dev/null 2>&1; then
    echo "[INFO] Environnement virtuel détecté — installation sans --user"
elif [ "${EUID:-$(id -u)}" -eq 0 ]; then
    echo "[INFO] Exécution en root — installation système"
else
    PIP_FLAGS="--user ${PIP_FLAGS}"
    echo "[INFO] Installation dans ~/.local (--user)"
fi

echo ""
# shellcheck disable=SC2086
"${PYTHON_EXE}" -m pip install ${PIP_FLAGS} -r "${REQ}"

echo ""
echo "============================================================"
echo "  Installation terminée. Rechargez le plugin Karst Entry."
echo "============================================================"
echo ""
