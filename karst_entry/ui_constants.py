# Copyright (c) 2026 Julien Tournois — PolyForm Noncommercial 1.0
"""Constantes UI et compatibilité enum PyQt5/PyQt6, partagées entre
`karst_dialog` (logique + couches) et `ui_tabs` (construction des onglets).

Sépare la compat Qt (QGIS 3/Qt5 ↔ QGIS 4/Qt6) et la palette/thème du dialogue
afin que les deux modules réfèrent les mêmes objets sans import circulaire.
"""
import os
import json
import warnings

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QMessageBox, QListWidget, QAbstractItemView, QHeaderView, QSizePolicy,
)

__all__ = [
    "_qenum", "_CONFIG_PATH", "_load_config", "_WindowModal",
    "_WStaysOnTop", "_WNoHelpBtn", "_AlignTop", "_AlignLeft", "_AlignCenter",
    "_KeepRatio", "_Smooth", "_TextSelect", "_ISODate", "_UserRole",
    "_MsgYes", "_MsgNo",
    "_ListIconMode", "_ListAdjust", "_ExtendedSel", "_SelectRows",
    "_NoEditTriggers", "_HeaderStretch", "_SizePolicyExpanding",
    "_SizePolicyPreferred",
    "KARST_TYPES", "_TYPE_COLORS", "_TYPE_COLOR_DEFAULT", "_TYPE_MARKERS",
    "_TYPE_MARKER_DEFAULT", "_TYPE_MARKER_SIZE", "_RESULT_COLORS",
    "_RESULT_COLOR_DEFAULT", "PHOTO_THUMB_SIZE", "_DEFAULT_COLORANTS",
    "_DEFAULT_RESULTATS",
]


# ---------------------------------------------------------------------------
# Compatibilité PyQt5 (QGIS 3) / PyQt6 (QGIS 4) — les enums Qt ont été
# déplacés dans des sous-classes en PyQt6 (Qt.WindowType, Qt.AlignmentFlag…)
# ---------------------------------------------------------------------------
def _qenum(obj, *path):
    """Résout un enum Qt compatible PyQt5 (plat) et PyQt6 (sous-classes).

    Exemple : _qenum(Qt, 'WindowType', 'WindowStaysOnTopHint')
              → Qt.WindowType.WindowStaysOnTopHint  (PyQt6)
              → Qt.WindowStaysOnTopHint              (PyQt5 fallback)
    """
    try:
        result = obj
        for p in path:
            result = getattr(result, p)
        return result
    except AttributeError:
        # PyQt5 : l'enum est directement sur l'objet parent
        return getattr(obj, path[-1])


# Qt enums
_WStaysOnTop = _qenum(Qt, 'WindowType',       'WindowStaysOnTopHint')
_WNoHelpBtn  = _qenum(Qt, 'WindowType',       'WindowContextHelpButtonHint')
_AlignTop    = _qenum(Qt, 'AlignmentFlag',    'AlignTop')
_AlignLeft   = _qenum(Qt, 'AlignmentFlag',    'AlignLeft')
_AlignCenter = _qenum(Qt, 'AlignmentFlag',    'AlignCenter')
_KeepRatio   = _qenum(Qt, 'AspectRatioMode',  'KeepAspectRatio')
_Smooth      = _qenum(Qt, 'TransformationMode', 'SmoothTransformation')
_TextSelect  = _qenum(Qt, 'TextInteractionFlag', 'TextSelectableByMouse')
_ISODate     = _qenum(Qt, 'DateFormat',       'ISODate')
_UserRole    = _qenum(Qt, 'ItemDataRole',     'UserRole')
_WindowModal = _qenum(Qt, 'WindowModality',   'WindowModal')

# QMessageBox enums
_MsgYes = _qenum(QMessageBox, 'StandardButton', 'Yes')
_MsgNo  = _qenum(QMessageBox, 'StandardButton', 'No')

# QListWidget / vues enums
_ListIconMode        = _qenum(QListWidget,       'ViewMode',          'IconMode')
_ListAdjust          = _qenum(QListWidget,       'ResizeMode',        'Adjust')
_ExtendedSel         = _qenum(QAbstractItemView, 'SelectionMode',     'ExtendedSelection')
_SelectRows          = _qenum(QAbstractItemView, 'SelectionBehavior', 'SelectRows')
_NoEditTriggers      = _qenum(QAbstractItemView, 'EditTrigger',       'NoEditTriggers')
_HeaderStretch       = _qenum(QHeaderView,       'ResizeMode',        'Stretch')
_SizePolicyExpanding = _qenum(QSizePolicy,       'Policy',            'Expanding')
_SizePolicyPreferred = _qenum(QSizePolicy,       'Policy',            'Preferred')

# Types de phénomènes karstiques proposés dans le formulaire.
KARST_TYPES = [
    "Gouffre", "Résurgence", "Perte", "Grotte", "Doline",
    "Inversac", "Vallée Sèche", "Lapiaz", "Canyon", "Faille", "Autre"
]

# Couleurs de symbologie par type de cavité (palette « Roche » + contrastes).
_TYPE_COLORS = {
    "Gouffre":      "#C0392B",  # rouge
    "Résurgence":   "#2E86C1",  # eau (sortie)
    "Perte":        "#C0392B",  # rouge
    "Grotte":       "#7D6608",  # ocre sombre
    "Doline":       "#BB6A2E",  # ocre
    "Inversac":     "#1ABC9C",
    "Vallée Sèche": "#A89A82",  # grès
    "Lapiaz":       "#909497",
    "Canyon":       "#884EA0",
    "Faille":       "#000000",  # noir
    "Autre":        "#FFFFFF",  # blanc
}
_TYPE_COLOR_DEFAULT = "#A89A82"

# Forme du marqueur par type (défaut : cercle).
_TYPE_MARKERS = {
    "Gouffre": "star",
}
_TYPE_MARKER_DEFAULT = "circle"
_TYPE_MARKER_SIZE = "1.5"

# Couleurs de symbologie des traçages par résultat.
_RESULT_COLORS = {
    "Positif":      "#27AE60",
    "Négatif":      "#C0392B",
    "Indéterminé":  "#909497",
}
_RESULT_COLOR_DEFAULT = "#BB6A2E"

# Taille en pixels des miniatures photo dans la liste.
PHOTO_THUMB_SIZE = 80

# Valeurs par défaut pour les listes de traçage (si karst_config.json absent).
_DEFAULT_COLORANTS = [
    "Fluorescéine", "Uranine", "Sulforhodamine B",
    "Rhodamine WT", "Lycopode", "Tinopal", "Autre"
]
_DEFAULT_RESULTATS = ["Positif", "Négatif", "Indéterminé"]

# Chemin du fichier de configuration (même répertoire que le plugin).
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "karst_config.json")


def _load_config():
    """Charge karst_config.json et retourne le dict de configuration.

    Retourne un dict vide si le fichier est absent ou invalide.
    L'appelant est responsable de fournir les valeurs par défaut.
    """
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        warnings.warn(f"karst_config.json illisible : {exc}")
        return {}
