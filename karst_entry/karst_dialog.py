# Copyright (c) 2026 Julien Tournois
# Licence : PolyForm Noncommercial License 1.0.0
# Usage commercial interdit sans autorisation écrite — julien.tournois@gmail.com

"""
karst_dialog.py
===============
Dialogue principal du plugin Karst Entry.

Contient :
  - KarstDialog   : fenêtre Qt multi-onglets (QDialog)
  - Fonctions utilitaires : _last3, _build_reference, _qenum
  - Bloc de compatibilité PyQt5/PyQt6 (QGIS 3 & 4)

Onglets
-------
  Nouvelle saisie          : formulaire + file d'attente + export CSV
  🔗 Traçage               : traçages hydrologiques entre cavités
  ✏ Modification            : édition des attributs d'une entité existante
  🗑 Suppression           : suppression d'entités + dossiers photos
  🔍 Fiche                 : consultation détaillée + vignettes photos
  📥 Import CSV            : import avec détection CRS et dédoublonnage
  ℹ Info                   : licence et guide utilisateur

Configuration
-------------
  karst_config.json (même dossier) — listes personnalisables (colorants, résultats…).
  Absent ou invalide → valeurs par défaut intégrées utilisées silencieusement.
"""

import os
import csv
import json
import shutil
import threading
import unicodedata

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDateEdit, QPushButton, QTabWidget, QWidget, QMessageBox,
    QFileDialog, QScrollArea, QGroupBox, QSizePolicy, QListWidget,
    QListWidgetItem, QAbstractItemView, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QRadioButton
)
from qgis.PyQt.QtCore import Qt, QDate, QSize, QVariant, pyqtSignal
from qgis.PyQt.QtGui import QPixmap, QIcon

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsWkbTypes, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsDistanceArea, QgsVectorFileWriter
)
from qgis.gui import QgsProjectionSelectionDialog

from .map_tool import PointCaptureTool
from . import geocode_utils

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

# QMessageBox enums
_MsgYes = _qenum(QMessageBox, 'StandardButton', 'Yes')
_MsgNo  = _qenum(QMessageBox, 'StandardButton', 'No')

# QListWidget enums (résolus après import des widgets)
def _wdg_enums():
    global _ListIconMode, _ListAdjust, _ExtendedSel
    global _SelectRows, _NoEditTriggers, _HeaderStretch
    global _SizePolicyExpanding, _SizePolicyPreferred
    _ListIconMode       = _qenum(QListWidget,       'ViewMode',      'IconMode')
    _ListAdjust         = _qenum(QListWidget,       'ResizeMode',    'Adjust')
    _ExtendedSel        = _qenum(QAbstractItemView, 'SelectionMode', 'ExtendedSelection')
    _SelectRows         = _qenum(QAbstractItemView, 'SelectionBehavior', 'SelectRows')
    _NoEditTriggers     = _qenum(QAbstractItemView, 'EditTrigger',   'NoEditTriggers')
    _HeaderStretch      = _qenum(QHeaderView,       'ResizeMode',    'Stretch')
    _SizePolicyExpanding = _qenum(QSizePolicy,      'Policy',        'Expanding')
    _SizePolicyPreferred = _qenum(QSizePolicy,      'Policy',        'Preferred')

_wdg_enums()

# Types de phénomènes karstiques proposés dans le formulaire.
KARST_TYPES = [
    "Gouffre", "Résurgence", "Perte", "Grotte", "Doline",
    "Inversac", "Vallée Sèche", "Lapiaz", "Canyon", "Faille", "Autre"
]

# Taille en pixels des miniatures photo dans la liste.
PHOTO_THUMB_SIZE = 80

# Valeurs par défaut pour les listes de traçage (utilisées si karst_config.json est absent).
_DEFAULT_COLORANTS = [
    "Fluorescéine", "Uranine", "Sulforhodamine B",
    "Rhodamine WT", "Lycopode", "Tinopal", "Autre"
]
_DEFAULT_RESULTATS = ["Positif", "Négatif", "Indéterminé"]

# Chemin du fichier de configuration (même répertoire que ce module).
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "karst_config.json")

# Contrat de schéma partagé avec KarstPro (copie locale par projet, cf.
# karst_schema.json). Source de vérité des noms/types de champs des couches.
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "karst_schema.json")

# Correspondance type JSON (vocabulaire pandas/contrat) → QVariant QGIS.
_QVARIANT_BY_TYPE = {
    "str": QVariant.String,
    "float": QVariant.Double,
    "int64": QVariant.Int,
    "int": QVariant.Int,
    "bool": QVariant.Bool,
}

# Replis utilisés si karst_schema.json est absent/illisible : le plugin doit
# rester fonctionnel sans le fichier. Ordres alignés sur le contrat.
_FALLBACK_CAVITES_FIELDS = {
    "name": "str", "type": "str", "reference": "str", "comment": "str",
    "dim_entree_longueur": "float", "dim_entree_largeur": "float",
    "developpement_estime": "float", "topographiable": "int64", "lien_topo": "str",
    "date_disc": "str", "date_expl": "str", "prot_id": "str", "explorers": "str",
    "photos": "str", "commune": "str", "code_insee": "str", "code_postal": "str",
    "departement": "str", "code_dept": "str",
}
_FALLBACK_TRACAGES_FIELDS = {
    "point_injection": "str", "point_sortie": "str", "colorant": "str",
    "resultat": "str", "date_injection": "str", "date_detection": "str",
    "temps_transit": "str", "distance_m": "float", "operateurs": "str",
    "commentaire": "str",
}


def _load_schema():
    """Charge karst_schema.json. Dict vide si absent/illisible (replis pris)."""
    try:
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        import warnings
        warnings.warn(f"karst_schema.json illisible : {exc}")
        return {}


_SCHEMA = _load_schema()


def _schema_fields(layer_key, fallback):
    """Retourne le dict {nom: type} des champs d'une couche du schéma, ou le repli."""
    fields = (_SCHEMA.get("layers", {}).get(layer_key, {}).get("fields"))
    return fields if fields else fallback


def _qgs_fields(fields_def, extra=()):
    """Construit une liste de QgsField depuis un dict {nom: type_json}.

    `extra` : champs supplémentaires (nom, QVariant) propres à Karst Entry,
    ajoutés s'ils ne sont pas déjà dans le schéma (ex. x/y, miroir géométrie).
    """
    defs = [QgsField(name, _QVARIANT_BY_TYPE.get(t, QVariant.String))
            for name, t in fields_def.items()]
    for name, qvar in extra:
        if name not in fields_def:
            defs.append(QgsField(name, qvar))
    return defs


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
        import warnings
        warnings.warn(f"karst_config.json illisible : {exc}")
        return {}


def _last3(coord):
    """Retourne les 3 derniers chiffres de la partie entière d'une coordonnée,
    complétés par des zéros à gauche si nécessaire.

    Exemples : 543210.5 → '210',  5 → '005',  0 → '000'
    """
    return str(abs(int(coord)))[-3:].zfill(3)


def _build_reference(feature_id, x, y):
    """Construit la référence unique d'une entité.

    Format : {feature_id}-{last3(x)}{last3(y)}
    Exemple : feature_id=1, x=543210.5, y=4891234.2 → '1-210234'
    """
    return f"{feature_id}-{_last3(x)}{_last3(y)}"


class KarstDialog(QDialog):
    """Fenêtre principale du plugin Karst Entry.

    Architecture de la saisie
    -------------------------
    Les entrées passent par deux états avant d'atteindre QGIS :

      1. File d'attente (self._queue) : liste Python de dicts en mémoire.
         Alimentée par _add_to_queue() sans aucun accès QGIS.

      2. Couche persistante (id caché dans self._new_layer_id) : créée à la
         première validation, re-résolue via mapLayer(id). Alimentée par
         _flush_queue_to_layer().

    Cela permet d'enchaîner plusieurs saisies avant de toucher QGIS,
    et d'annuler sans effet de bord.

    Paramètres
    ----------
    iface : QgisInterface
        Interface QGIS injectée par classFactory.
    parent : QWidget, optional
        Fenêtre parente Qt.
    """

    # Résultat du géocodage asynchrone : (info_dict_ou_None, génération).
    # Émis depuis le thread de travail ; Qt route vers le thread UI (queued).
    _admin_fetched = pyqtSignal(object, int)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface  = iface
        self.canvas = iface.mapCanvas()

        self._map_tool        = None   # PointCaptureTool actif (ou None)
        self._prev_tool       = None   # outil carte précédent, restauré après capture
        self._captured_point  = None   # QgsPointXY du dernier clic carte
        self._captured_crs    = None   # CRS du canevas au moment de la capture
        # On ne cache QUE l'id (str) de la couche, jamais l'objet QgsVectorLayer :
        # un objet caché peut être détruit côté C++ (suppression de la couche) et
        # tout accès lèverait « wrapped C/C++ object ... deleted ». L'id se re-résout
        # via QgsProject.instance().mapLayer(id) — None si la couche n'existe plus.
        self._new_layer_id    = None   # id couche "Inventaire Cavités"
        self._photo_paths     = []     # chemins des photos de l'entrée en cours
        self._queue           = []     # entrées en attente (dicts) non encore écrites dans QGIS
        self._tracage_layer_id = None  # id couche "Inventaire Traçages"
        self._tracage_queue   = []     # traçages en attente
        # Cache des communes déjà géocodées : liste de (polygones_wgs84, info).
        # Évite un appel réseau par cavité — un seul par commune distincte.
        self._commune_cache  = []
        # Compteur de génération du géocodage asynchrone : une réponse réseau
        # qui arrive après une nouvelle capture est obsolète et ignorée.
        self._admin_gen      = 0
        self._admin_fetched.connect(self._on_admin_fetched)

        self.setWindowTitle("Saisie du Phénomène Karstique")
        self.setMinimumWidth(580)
        self.setMinimumHeight(700)
        self.setWindowFlags(self.windowFlags() | _WStaysOnTop)

        self._build_ui()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        root = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_new_tab(), "Nouvelle saisie")
        self._tabs.addTab(self._build_tracage_tab(), "🔗 Traçage")
        self._tabs.addTab(self._build_edit_tab(), "✏ Modification")
        self._tabs.addTab(self._build_delete_tab(), "🗑 Suppression")
        self._tabs.addTab(self._build_fiche_tab(), "🔍 Fiche")
        self._tabs.addTab(self._build_views_tab(), "🗂 Vues")
        self._tabs.addTab(self._build_import_tab(), "📥 Import CSV")
        self._tabs.addTab(self._build_info_tab(), "ℹ Info")
        root.addWidget(self._tabs)

        crs = self.canvas.mapSettings().destinationCrs()
        self._lbl_crs = QLabel(self._crs_text(crs))
        self._lbl_crs.setStyleSheet("color: #666; font-size: 10px;")
        root.addWidget(self._lbl_crs)
        self.canvas.destinationCrsChanged.connect(self._on_crs_changed)
        # Defer focus so the dialog is fully shown first
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._btn_queue.setFocus())

    # -------- Tab: new layer --------

    def _build_new_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        scroll.setWidget(form_widget)

        self._f_name = QLineEdit()
        form.addRow("Nom de la cavité *", self._f_name)

        self._f_type = QComboBox()
        self._f_type.addItems(KARST_TYPES)
        form.addRow("Type *", self._f_type)

        self._f_date_disc = QDateEdit()
        self._f_date_disc.setCalendarPopup(True)
        self._f_date_disc.setDate(QDate.currentDate())
        form.addRow("Date de découverte", self._f_date_disc)

        self._f_date_expl = QDateEdit()
        self._f_date_expl.setCalendarPopup(True)
        self._f_date_expl.setDate(QDate.currentDate())
        form.addRow("Date d'exploration", self._f_date_expl)

        self._f_prot_id = QLineEdit()
        self._f_prot_id.setToolTip(
            "Identifiant libre et optionnel, stocké dans le champ « prot_id ».\n"
            "Utile par exemple pour un ID de zone si vous quadrillez le secteur.\n"
            "N'intervient PAS dans la référence : celle-ci est générée automatiquement\n"
            "à partir de l'identifiant interne QGIS et des coordonnées."
        )
        self._f_prot_id.setPlaceholderText("optionnel")
        form.addRow("ID", self._f_prot_id)

        self._f_explorers = QLineEdit()
        self._f_explorers.setPlaceholderText("Nom1, Nom2, …")
        form.addRow("Explorateurs", self._f_explorers)

        self._f_comment = QTextEdit()
        self._f_comment.setFixedHeight(80)
        self._f_comment.setTabChangesFocus(True)
        form.addRow("Commentaire", self._f_comment)

        # Localisation administrative — remplie automatiquement à la capture
        # (geo.api.gouv.fr), modifiable à la main. Champs codes masqués.
        self._f_commune = QLineEdit()
        self._f_commune.setPlaceholderText("auto à la capture")
        form.addRow("Commune", self._f_commune)
        self._f_code_postal = QLineEdit()
        self._f_code_postal.setPlaceholderText("auto à la capture")
        form.addRow("Code postal", self._f_code_postal)
        self._f_departement = QLineEdit()
        self._f_departement.setPlaceholderText("auto à la capture")
        form.addRow("Département", self._f_departement)
        # État du géocodage : recherche en cours / échec (jamais bloquant).
        self._admin_status = QLabel("")
        self._admin_status.setStyleSheet("color: #888; font-size: 10px;")
        self._admin_status.setWordWrap(True)
        form.addRow("", self._admin_status)
        # Codes conservés dans le schéma mais non affichés (remplis par l'API)
        self._f_code_insee = QLineEdit()
        self._f_code_dept = QLineEdit()

        # Coordinates
        coord_group = QGroupBox("Coordonnées (clic sur la carte)")
        coord_layout = QHBoxLayout(coord_group)

        self._btn_capture = QPushButton("📍 Capturer un point")
        self._btn_capture.setCheckable(True)
        self._btn_capture.clicked.connect(self._toggle_capture)
        coord_layout.addWidget(self._btn_capture)

        self._lbl_x = QLineEdit()
        self._lbl_x.setPlaceholderText("X / Longitude")
        self._lbl_y = QLineEdit()
        self._lbl_y.setPlaceholderText("Y / Latitude")
        coord_layout.addWidget(QLabel("X:"))
        coord_layout.addWidget(self._lbl_x)
        coord_layout.addWidget(QLabel("Y:"))
        coord_layout.addWidget(self._lbl_y)
        form.addRow(coord_group)

        # Photos
        photo_group = QGroupBox("Photos")
        photo_layout = QVBoxLayout(photo_group)

        photo_btn_row = QHBoxLayout()
        btn_add_photo = QPushButton("📷 Ajouter photo(s)")
        btn_add_photo.clicked.connect(self._add_photos)
        btn_remove_photo = QPushButton("🗑 Supprimer")
        btn_remove_photo.clicked.connect(self._remove_selected_photo)
        photo_btn_row.addWidget(btn_add_photo)
        photo_btn_row.addWidget(btn_remove_photo)
        photo_layout.addLayout(photo_btn_row)

        self._photo_list = QListWidget()
        self._photo_list.setViewMode(_ListIconMode)
        self._photo_list.setIconSize(QSize(PHOTO_THUMB_SIZE, PHOTO_THUMB_SIZE))
        self._photo_list.setFixedHeight(PHOTO_THUMB_SIZE + 30)
        self._photo_list.setResizeMode(_ListAdjust)
        self._photo_list.setSelectionMode(_ExtendedSel)
        photo_layout.addWidget(self._photo_list)

        form.addRow(photo_group)
        layout.addWidget(scroll)

        # Queue counter
        self._lbl_queue = QLabel("File d'attente : 0 point(s)")
        self._lbl_queue.setStyleSheet("font-weight: bold; color: #555;")
        layout.addWidget(self._lbl_queue)

        btn_row = QHBoxLayout()

        btn_add_qgis = QPushButton("🗺 Ajouter dans QGIS")
        btn_add_qgis.setToolTip("Enregistre l'entrée et ajoute la couche au projet QGIS")
        btn_add_qgis.clicked.connect(self._save_and_add_to_qgis)
        btn_row.addWidget(btn_add_qgis)

        btn_queue = QPushButton("➕ Ajouter à la file d'attente")
        btn_queue.setToolTip("Met l'entrée en attente sans l'envoyer dans QGIS")
        btn_queue.clicked.connect(self._add_to_queue)
        btn_queue.setDefault(True)
        btn_queue.setAutoDefault(True)
        btn_row.addWidget(btn_queue)
        self._btn_queue = btn_queue

        btn_export = QPushButton("📤 Exporter en CSV")
        btn_export.clicked.connect(self._export_csv)
        btn_row.addWidget(btn_export)

        layout.addLayout(btn_row)
        return tab

    # -------- Tab: modification --------

    def _build_edit_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Couche :"))
        self._edit_layer_combo = QComboBox()
        self._edit_layer_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._edit_layer_combo.currentIndexChanged.connect(self._edit_on_layer_changed)
        layer_row.addWidget(self._edit_layer_combo)
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(32)
        btn_refresh.clicked.connect(self._edit_populate_layers)
        layer_row.addWidget(btn_refresh)
        layout.addLayout(layer_row)

        # Recherche (référence/nom) + filtre par type
        filter_row = QHBoxLayout()
        self._edit_search = QLineEdit()
        self._edit_search.setPlaceholderText("🔎 Rechercher (référence, nom)…")
        self._edit_search.setClearButtonEnabled(True)
        self._edit_search.textChanged.connect(self._edit_populate_features)
        filter_row.addWidget(self._edit_search, 2)
        self._edit_type_filter = QComboBox()
        self._edit_type_filter.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._edit_type_filter.currentIndexChanged.connect(self._edit_populate_features)
        filter_row.addWidget(self._edit_type_filter, 1)
        layout.addLayout(filter_row)

        feat_row = QHBoxLayout()
        feat_row.addWidget(QLabel("Entité :"))
        self._edit_feat_combo = QComboBox()
        self._edit_feat_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._edit_feat_combo.currentIndexChanged.connect(self._edit_load_feature)
        feat_row.addWidget(self._edit_feat_combo)
        layout.addLayout(feat_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._edit_form_widget = QWidget()
        self._edit_form_layout = QFormLayout(self._edit_form_widget)
        scroll.setWidget(self._edit_form_widget)
        layout.addWidget(scroll)

        self._edit_field_widgets = {}

        btn_save = QPushButton("💾 Enregistrer les modifications")
        btn_save.clicked.connect(self._edit_save)
        layout.addWidget(btn_save)

        self._edit_populate_layers()
        return tab

    def _edit_populate_layers(self):
        self._edit_layer_combo.blockSignals(True)
        self._edit_layer_combo.clear()
        default_index = -1
        for layer in QgsProject.instance().mapLayers().values():
            # Duck-typing : une couche vecteur expose getFeatures/fields ;
            # exclut les rasters sans recourir à isinstance (incompatible stubs).
            if not (hasattr(layer, "getFeatures") and hasattr(layer, "fields")):
                continue
            try:
                if not layer.isValid():
                    continue
                name, lid = layer.name(), layer.id()
            except (AttributeError, RuntimeError):
                continue
            self._edit_layer_combo.addItem(name, lid)
            if name == self._CAVITES_LAYER_NAME:
                default_index = self._edit_layer_combo.count() - 1
        if default_index != -1:
            self._edit_layer_combo.setCurrentIndex(default_index)
        self._edit_layer_combo.blockSignals(False)
        self._edit_on_layer_changed()

    def _current_edit_layer(self):
        layer_id = self._edit_layer_combo.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    # --- Recherche / filtre par type (partagé Modification & Fiche) ----------

    @staticmethod
    def _feature_label(feat, fields):
        """Libellé d'une entité : « référence — nom », repli sur nom puis #id."""
        if "reference" in fields and feat["reference"]:
            label = str(feat["reference"])
            if "name" in fields and feat["name"]:
                label += f" — {feat['name']}"
        elif "name" in fields and feat["name"]:
            label = str(feat["name"])
        else:
            label = f"#{feat.id()}"
        return label

    @staticmethod
    def _fold(text):
        """Normalise pour comparaison : minuscules + suppression des accents.

        « Résurgence » → « resurgence », « Vallée Sèche » → « vallee seche ».
        Permet une recherche insensible à la casse ET aux accents.
        """
        if not text:
            return ""
        norm = unicodedata.normalize("NFKD", str(text))
        return "".join(c for c in norm if not unicodedata.combining(c)).lower()

    @staticmethod
    def _feature_matches(feat, fields, search, type_filter):
        """True si l'entité passe le filtre type ET la recherche texte.

        search : sous-chaîne cherchée dans référence + nom + type, insensible
                 à la casse ET aux accents.
        type_filter : valeur exacte du champ `type` ; vide = tous.
        """
        ftype = str(feat["type"]) if "type" in fields and feat["type"] else ""
        if type_filter and ftype != type_filter:
            return False
        if search:
            parts = []
            for f in ("reference", "name"):
                if f in fields and feat[f]:
                    parts.append(str(feat[f]))
            if ftype:
                parts.append(ftype)
            if KarstDialog._fold(search) not in KarstDialog._fold(" ".join(parts)):
                return False
        return True

    @staticmethod
    def _populate_type_filter(combo, layer):
        """Remplit un combo de filtre avec « Tous les types » + valeurs distinctes."""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Tous les types", "")
        if layer is not None:
            try:
                fields = [f.name() for f in layer.fields()]
                if "type" in fields:
                    types = sorted({str(feat["type"]) for feat in layer.getFeatures()
                                    if feat["type"]})
                    for t in types:
                        combo.addItem(t, t)
            except (AttributeError, RuntimeError):
                pass
        combo.blockSignals(False)

    def _edit_on_layer_changed(self):
        """Couche changée : rebâtir le filtre de type puis la liste d'entités."""
        combo = getattr(self, "_edit_type_filter", None)
        if combo is not None:
            self._populate_type_filter(combo, self._current_edit_layer())
        self._edit_populate_features()

    def _edit_populate_features(self):
        self._edit_feat_combo.blockSignals(True)
        self._edit_feat_combo.clear()
        layer = self._current_edit_layer()
        if layer is not None:
            fields = [f.name() for f in layer.fields()]
            search = (self._edit_search.text().strip().lower()
                      if hasattr(self, "_edit_search") else "")
            type_f = (self._edit_type_filter.currentData() or ""
                      if hasattr(self, "_edit_type_filter") else "")
            for feat in layer.getFeatures():
                if not self._feature_matches(feat, fields, search, type_f):
                    continue
                self._edit_feat_combo.addItem(
                    self._feature_label(feat, fields), feat.id())
        self._edit_feat_combo.blockSignals(False)
        self._edit_load_feature()

    def _edit_load_feature(self):
        # Reconstruit le formulaire d'après les champs de la couche, pré-rempli
        # avec les valeurs de l'entité sélectionnée. « reference » est en
        # lecture seule (identité). La géométrie n'est pas modifiable ici.
        while self._edit_form_layout.rowCount():
            self._edit_form_layout.removeRow(0)
        self._edit_field_widgets.clear()

        layer = self._current_edit_layer()
        if layer is None:
            return
        fid = self._edit_feat_combo.currentData()
        if fid is None:
            return
        feat = layer.getFeature(fid)

        for field in layer.fields():
            name = field.name()
            value = feat[name]
            type_name = field.typeName().lower()
            if type_name in ("date", "datetime"):
                widget = QDateEdit()
                widget.setCalendarPopup(True)
                d = QDate.fromString(str(value), _ISODate)
                widget.setDate(d if d.isValid() else QDate.currentDate())
            else:
                widget = QLineEdit()
                widget.setText("" if value is None else str(value))
            if name == "reference":
                widget.setEnabled(False)  # identité : non modifiable
            self._edit_field_widgets[name] = widget
            self._edit_form_layout.addRow(name, widget)

    def _edit_save(self):
        layer = self._current_edit_layer()
        if layer is None:
            QMessageBox.warning(self, "Erreur", "Aucune couche sélectionnée.")
            return
        fid = self._edit_feat_combo.currentData()
        if fid is None:
            QMessageBox.warning(self, "Erreur", "Aucune entité sélectionnée.")
            return

        field_index = {f.name(): layer.fields().indexOf(f.name())
                       for f in layer.fields()}
        layer.startEditing()
        for name, widget in self._edit_field_widgets.items():
            if name == "reference":
                continue  # identité : jamais réécrite
            idx = field_index.get(name, -1)
            if idx == -1:
                continue
            if isinstance(widget, QDateEdit):
                value = widget.date().toString(_ISODate)
            else:
                value = widget.text()
            layer.changeAttributeValue(fid, idx, value)
        if not layer.commitChanges():
            QMessageBox.warning(self, "Échec",
                                "Impossible d'enregistrer les modifications.")
            return

        QMessageBox.information(self, "Modifié",
                               "Les modifications ont été enregistrées.")
        self._edit_populate_features()

    # ---------------------------------------------------------------- Photos --

    def _add_photos(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Sélectionner des photos", "",
            "Images (*.jpg *.jpeg *.png *.bmp *.tif *.tiff)"
        )
        for path in paths:
            if path not in self._photo_paths:
                self._photo_paths.append(path)
                icon = QIcon(QPixmap(path).scaled(
                    PHOTO_THUMB_SIZE, PHOTO_THUMB_SIZE,
                    _KeepRatio, _Smooth
                ))
                item = QListWidgetItem(icon, os.path.basename(path))
                item.setData(_UserRole, path)
                item.setToolTip(path)
                self._photo_list.addItem(item)

    def _remove_selected_photo(self):
        for item in self._photo_list.selectedItems():
            path = item.data(_UserRole)
            if path in self._photo_paths:
                self._photo_paths.remove(path)
            self._photo_list.takeItem(self._photo_list.row(item))

    @staticmethod
    def _store_photos_beside_layer(photo_paths, layer_dir, reference):
        """Copie les photos sous layer_dir/<reference>/ et renvoie les chemins
        relatifs (« <référence>/<fichier> », séparés par « ; »).

        - layer_dir vide (couche mémoire) → renvoie les chemins absolus tels
          quels (repli non portable, mais affichable).
        - fichier source absent → le token est conservé sans copie.
        """
        if not photo_paths:
            return ""
        if not layer_dir:
            return ";".join(p for p in photo_paths if p)
        out = []
        for src in photo_paths:
            if not src:
                continue
            if os.path.isfile(src):
                base = os.path.basename(src)
                dst = os.path.join(layer_dir, reference, base)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copy2(src, dst)
                out.append(f"{reference}/{base}")
            else:
                out.append(src)
        return ";".join(out)

    # ---------------------------------------------------------- New layer I/O --

    def _read_form(self):
        """Read current form values into a dict. Returns None if name is empty."""
        if not self._f_name.text().strip():
            QMessageBox.warning(self, "Champ requis", "Le nom de la cavité est obligatoire.")
            return None
        x, y = self._get_xy()
        return {
            "name":      self._f_name.text().strip(),
            "type":      self._f_type.currentText(),
            "date_disc": self._f_date_disc.date().toString(_ISODate),
            "date_expl": self._f_date_expl.date().toString(_ISODate),
            "prot_id":   self._f_prot_id.text().strip(),
            "explorers": self._f_explorers.text().strip(),
            "comment":   self._f_comment.toPlainText().strip(),
            "commune":     self._f_commune.text().strip(),
            "code_insee":  self._f_code_insee.text().strip(),
            "code_postal": self._f_code_postal.text().strip(),
            "departement": self._f_departement.text().strip(),
            "code_dept":   self._f_code_dept.text().strip(),
            "x":         x,
            "y":         y,
            # CRS dans lequel x/y sont exprimés (= CRS du canevas à la capture).
            # Sert à reprojeter vers le CRS de la couche à l'écriture.
            "src_crs":   getattr(self, "_captured_crs", None)
                         or self.canvas.mapSettings().destinationCrs(),
            "photos":    list(self._photo_paths),
        }

    def _add_to_queue(self):
        """Push current form to the in-memory queue without touching QGIS."""
        entry = self._read_form()
        if entry is None:
            return
        self._queue.append(entry)
        self._clear_new_form()
        self._reset_capture()
        self._update_queue_counter()
        self._btn_queue.setFocus()

    def _save_and_add_to_qgis(self):
        """Push current form to queue (if filled) then flush everything to the QGIS layer."""
        if self._f_name.text().strip():
            entry = self._read_form()
            if entry is None:
                return
            self._queue.append(entry)
            self._clear_new_form()
            self._reset_capture()
        elif not self._queue:
            QMessageBox.warning(self, "Rien à ajouter",
                                "Le formulaire est vide et la file d'attente est vide.")
            return
        self._flush_queue_to_layer()

    _CAVITES_LAYER_NAME = "Inventaire Cavités"

    @staticmethod
    def _cavites_field_defs():
        """Champs de la couche cavités, chargés depuis karst_schema.json.

        x/y (miroir de la géométrie) sont propres à Karst Entry, ajoutés
        en plus des champs du contrat partagé.
        """
        return _qgs_fields(
            _schema_fields("cavites_connues", _FALLBACK_CAVITES_FIELDS),
            extra=[("x", QVariant.Double), ("y", QVariant.Double)])

    def _resolve_cavites_layer(self):
        """Retrouve une couche cavités existante dans le projet, sans en créer.

        Évite de recréer une couche mémoire à chaque session : on cherche une
        couche point « Inventaire Cavités » (ou portant le schéma cavité
        name/type/reference), en préférant une source sur disque (ogr) à une
        couche mémoire volatile.
        """
        # 1. L'id en cache pointe-t-il encore vers une couche du projet ?
        if self._new_layer_id is not None:
            cached = QgsProject.instance().mapLayer(self._new_layer_id)
            if cached is not None:
                return cached
            self._new_layer_id = None  # couche supprimée → cache obsolète

        # 2. Chercher dans le projet par nom ou par schéma.
        candidates = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            if lyr.geometryType() != QgsWkbTypes.PointGeometry:
                continue
            fields = set(lyr.fields().names())
            if lyr.name() == self._CAVITES_LAYER_NAME or \
                    {"name", "type", "reference"} <= fields:
                candidates.append(lyr)
        if not candidates:
            return None
        # Priorité au stockage sur disque (ogr) sur la mémoire.
        candidates.sort(key=lambda l: 0 if l.dataProvider().name() == "ogr" else 1)
        self._new_layer_id = candidates[0].id()
        return candidates[0]

    def _create_persistent_cavites_layer(self):
        """Crée une couche cavités PERSISTANTE (GeoPackage sur disque), pas en mémoire.

        Le fichier est créé dans le dossier du projet s'il est enregistré, sinon
        l'utilisateur choisit l'emplacement. Renvoie la couche ogr chargée, ou
        None si l'utilisateur annule.
        """
        proj_dir = QgsProject.instance().absolutePath()
        if proj_dir:
            path = os.path.join(proj_dir, f"{self._CAVITES_LAYER_NAME}.gpkg")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Enregistrer la couche cavités",
                f"{self._CAVITES_LAYER_NAME}.gpkg", "GeoPackage (*.gpkg)")
            if not path:
                return None

        # Si le fichier existe déjà, le charger plutôt que l'écraser.
        if not os.path.isfile(path):
            crs = self.canvas.mapSettings().destinationCrs()
            mem = QgsVectorLayer(f"Point?crs={crs.authid()}",
                                 self._CAVITES_LAYER_NAME, "memory")
            mem.dataProvider().addAttributes(self._cavites_field_defs())
            mem.updateFields()
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = self._CAVITES_LAYER_NAME
            ctx = QgsProject.instance().transformContext()
            try:
                res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, ctx, options)
            except AttributeError:
                res = QgsVectorFileWriter.writeAsVectorFormatV2(mem, path, ctx, options)
            if res[0] != QgsVectorFileWriter.NoError:
                QMessageBox.warning(self, "Création impossible",
                                    f"Impossible de créer le GeoPackage :\n{res[1]}")
                return None

        layer = QgsVectorLayer(path, self._CAVITES_LAYER_NAME, "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Couche invalide",
                                f"Le GeoPackage créé est illisible :\n{path}")
            return None
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _offer_schema_upgrade(self, layer, pr):
        """Propose d'ajouter à la couche les colonnes du schéma qui lui manquent.

        Demandé une seule fois par couche et par session (refus mémorisé).
        En cas de refus, le comportement reste l'ancien : seuls les champs
        présents sont écrits, les autres valeurs sont ignorées.
        """
        try:
            existing = set(layer.fields().names())
            expected = self._cavites_field_defs()
            missing = [f for f in expected if f.name() not in existing]
            if not missing:
                return
            asked = getattr(self, "_schema_upgrade_asked", None)
            if asked is None:
                asked = set()
                self._schema_upgrade_asked = asked
            if layer.id() in asked:
                return
            asked.add(layer.id())
            names = ", ".join(f.name() for f in missing)
            reply = QMessageBox.question(
                self, "Schéma incomplet",
                f"La couche « {layer.name()} » n'a pas les colonnes :\n"
                f"{names}\n\n"
                "Sans elles, ces valeurs ne seront PAS enregistrées "
                "(commune, codes…).\nAjouter les colonnes manquantes ?",
                _MsgYes | _MsgNo, _MsgYes)
            if reply == _MsgYes:
                pr.addAttributes(missing)
                layer.updateFields()
        except Exception:
            # Jamais bloquant : en cas de pépin on retombe sur l'ancien
            # comportement (écriture des seuls champs présents).
            pass

    def _reproject_entry_xy(self, entry, layer_crs):
        """Coordonnées de l'entrée exprimées dans le CRS de la couche.

        Reprojette depuis le CRS de capture si nécessaire. Retourne (None, None)
        si l'entrée n'a pas de coordonnées.
        """
        x, y = entry.get("x"), entry.get("y")
        if x is None or y is None:
            return None, None
        src = entry.get("src_crs")
        if src is not None and src.isValid() and src != layer_crs:
            try:
                tr = QgsCoordinateTransform(src, layer_crs, QgsProject.instance())
                p = tr.transform(QgsPointXY(x, y))
                return p.x(), p.y()
            except Exception:
                return x, y
        return x, y

    def _load_existing_points(self, layer):
        """Liste {ref, name, x, y} des entités de la couche (coords en CRS couche)."""
        names = set(layer.fields().names())
        has_name = "name" in names
        has_ref = "reference" in names
        out = []
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom and not geom.isEmpty():
                pt = geom.asPoint()
                x, y = pt.x(), pt.y()
            else:
                x, y = None, None
            out.append({
                "ref":  str(feat["reference"]) if has_ref else "",
                "name": str(feat["name"]) if has_name else "",
                "x": x, "y": y,
            })
        return out

    def _confirm_skip_duplicates(self, layer, layer_crs, entry_xy):
        """Repère les points en doublon de position (< 2 m) et demande quoi faire.

        Marque entry["_is_dup"] = True pour chaque entrée coïncidant avec une
        entité existante de la couche OU une entrée précédente de la file.
        Retourne True si l'utilisateur choisit d'ignorer les doublons.
        Position seule (pas le nom) : « mêmes coordonnées » = doublon.
        """
        tol = self._COORD_TOLERANCE
        try:
            seen = [e for e in self._load_existing_points(layer)
                    if e["x"] is not None]
        except Exception:
            return False
        dist = self._metric_distance_fn(layer_crs)

        def is_close(x, y):
            for e in seen:
                if dist is not None:
                    if dist(x, y, e["x"], e["y"]) < tol:
                        return True
                elif self._coords_close(x, y, e["x"], e["y"], tol):
                    return True
            return False

        dups = 0
        for entry, (x, y) in zip(self._queue, entry_xy):
            entry["_is_dup"] = False
            if x is None:
                continue
            if is_close(x, y):
                entry["_is_dup"] = True
                dups += 1
            else:
                seen.append({"x": x, "y": y})

        if not dups:
            return False
        reply = QMessageBox.question(
            self, "Doublons détectés",
            f"{dups} point(s) coïncident avec une entité existante ou un autre "
            f"point de la file (< {int(tol)} m).\n\n"
            "Les ajouter quand même ?\n"
            "• Oui : tout ajouter (doublons inclus)\n"
            "• Non : ignorer les doublons",
            _MsgYes | _MsgNo, _MsgNo)
        return reply == _MsgNo

    def _flush_queue_to_layer(self):
        """Write all queued entries to a persistent on-disk cavités layer."""
        if not self._queue:
            return

        layer = self._resolve_cavites_layer()
        if layer is None:
            layer = self._create_persistent_cavites_layer()
        if layer is None:
            return  # création annulée par l'utilisateur
        self._new_layer_id = layer.id()
        pr = layer.dataProvider()

        # Couche au schéma incomplet (créée par une version antérieure du
        # plugin, repackagée par QField…) : proposer UNE fois d'ajouter les
        # colonnes manquantes. Sans cela les valeurs (ex. commune) seraient
        # silencieusement perdues à l'écriture.
        self._offer_schema_upgrade(layer, pr)

        # Champs réellement présents dans la couche cible : on n'écrit que
        # ceux-là, pour rester compatible avec un GPKG au schéma différent
        # (ex. couche repackagée par QField, champs renommés/absents).
        layer_field_names = set(layer.fields().names())
        # Dossier de la couche (pour copier les photos à côté → portable).
        layer_dir = self._layer_dir(layer)

        layer_crs = layer.crs()

        # Coordonnées de chaque entrée dans le CRS de la couche (reprojetées).
        entry_xy = [self._reproject_entry_xy(e, layer_crs) for e in self._queue]

        # Dédoublonnage : repérer les points qui coïncident (< 2 m) avec une
        # entité existante OU une entrée déjà vue dans cette file. Si on en
        # trouve, demander une fois quoi faire (jamais silencieux, jamais bloquant).
        skip_dups = self._confirm_skip_duplicates(layer, layer_crs, entry_xy)

        added = skipped = 0
        for entry, (x, y) in zip(self._queue, entry_xy):
            if skip_dups and entry.get("_is_dup"):
                skipped += 1
                continue
            feat = QgsFeature(layer.fields())
            if x is not None:
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            # photos écrites après coup (dépendent de la référence)
            values = {
                "name":      entry["name"],
                "type":      entry["type"],
                "date_disc": entry["date_disc"],
                "date_expl": entry["date_expl"],
                "prot_id":   entry["prot_id"],
                "explorers": entry["explorers"],
                "comment":   entry["comment"],
                "commune":     entry.get("commune", ""),
                "code_insee":  entry.get("code_insee", ""),
                "code_postal": entry.get("code_postal", ""),
                "departement": entry.get("departement", ""),
                "code_dept":   entry.get("code_dept", ""),
                "x":         x if x is not None else QVariant(),
                "y":         y if y is not None else QVariant(),
            }
            for field_name, value in values.items():
                if field_name in layer_field_names:
                    feat.setAttribute(field_name, value)
            pr.addFeature(feat)

            fid = feat.id()
            ref_idx = layer.fields().indexOf("reference")
            ref = _build_reference(fid, x or 0, y or 0)
            # Copie des photos à côté de la couche → chemins relatifs portables
            # (repli absolu si couche mémoire). Nom de dossier = référence.
            photos_idx = layer.fields().indexOf("photos")
            photos_val = self._store_photos_beside_layer(
                entry["photos"], layer_dir, ref)

            if ref_idx != -1 or photos_idx != -1:
                layer.startEditing()
                if ref_idx != -1:
                    layer.changeAttributeValue(fid, ref_idx, ref)
                if photos_idx != -1:
                    layer.changeAttributeValue(fid, photos_idx, photos_val)
                layer.commitChanges()
            added += 1

        layer.updateExtents()
        self._queue.clear()

        if not QgsProject.instance().mapLayer(layer.id()):
            QgsProject.instance().addMapLayer(layer)

        self._update_queue_counter()
        QMessageBox.information(
            self, "Ajouté dans QGIS",
            f"{added} entrée(s) ajoutée(s) à la couche « Inventaire Cavités »."
            + (f"\n{skipped} doublon(s) ignoré(s)." if skipped else "")
        )

    def _update_queue_counter(self):
        """Met à jour le compteur de file d'attente : rouge si non vide, vert si vide."""
        count = len(self._queue)
        self._lbl_queue.setText(f"File d'attente : {count} point(s)")
        self._lbl_queue.setStyleSheet(
            "font-weight: bold; color: #c0392b;" if count > 0 else "font-weight: bold; color: #27ae60;"
        )

    def _clear_new_form(self):
        """Remet tous les champs du formulaire "Nouvelle saisie" à leur état initial."""
        self._f_name.clear()
        self._f_type.setCurrentIndex(0)
        self._f_date_disc.setDate(QDate.currentDate())
        self._f_date_expl.setDate(QDate.currentDate())
        self._f_prot_id.clear()
        self._f_explorers.clear()
        self._f_comment.setPlainText("")
        self._f_commune.clear()
        self._f_code_postal.clear()
        self._f_departement.clear()
        self._f_code_insee.clear()
        self._f_code_dept.clear()
        self._photo_paths.clear()
        self._photo_list.clear()

    def _export_csv(self):
        layer = self._resolve_cavites_layer()
        if layer is None or layer.featureCount() == 0:
            QMessageBox.information(self, "Vide",
                                    "Aucune donnée à exporter.\n"
                                    "Utilisez d'abord « Ajouter dans QGIS » pour valider les entrées.")
            return
        self._new_layer_id = layer.id()

        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter en CSV", "", "CSV (*.csv)"
        )
        if not path:
            return

        export_dir = os.path.dirname(path)
        fields = [f.name() for f in layer.fields()]

        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for feat in layer.getFeatures():
                row = {f: feat[f] for f in fields}
                # Copy photos stored as paths if they exist on disk
                if row.get("photos"):
                    ref = row.get("reference", "no_ref")
                    copied = []
                    for src in row["photos"].split(";"):
                        src = src.strip()
                        if src and os.path.isfile(src):
                            dest_folder = os.path.join(export_dir, ref)
                            os.makedirs(dest_folder, exist_ok=True)
                            dst = os.path.join(dest_folder, os.path.basename(src))
                            shutil.copy2(src, dst)
                            copied.append(os.path.join(ref, os.path.basename(src)))
                    row["photos"] = ";".join(copied)
                writer.writerow(row)

        QMessageBox.information(self, "Export réussi",
                                f"CSV sauvegardé :\n{path}\n\n"
                                f"Photos copiées dans les sous-dossiers <référence>/.")

    def _add_layer_to_qgis(self):
        layer = self._resolve_cavites_layer()
        if layer is None or layer.featureCount() == 0:
            QMessageBox.information(self, "Vide", "Aucune entité à ajouter.")
            return
        self._new_layer_id = layer.id()
        if not QgsProject.instance().mapLayer(layer.id()):
            QgsProject.instance().addMapLayer(layer)
        else:
            QMessageBox.information(self, "Info", "La couche est déjà présente dans le projet.")

    # ---------------------------------------------------------------- Delete tab --

    def _build_delete_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Layer selector
        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Couche :"))
        self._del_layer_combo = QComboBox()
        self._del_layer_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._del_layer_combo.currentIndexChanged.connect(self._del_on_layer_changed)
        layer_row.addWidget(self._del_layer_combo)
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(32)
        btn_refresh.clicked.connect(self._populate_delete_layer_combo)
        layer_row.addWidget(btn_refresh)
        layout.addLayout(layer_row)

        # Recherche + filtre par type
        filter_row = QHBoxLayout()
        self._del_search = QLineEdit()
        self._del_search.setPlaceholderText("🔎 Rechercher…")
        self._del_search.setClearButtonEnabled(True)
        self._del_search.textChanged.connect(self._refresh_delete_table)
        filter_row.addWidget(self._del_search, 2)
        self._del_type = QComboBox()
        self._del_type.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._del_type.currentIndexChanged.connect(self._refresh_delete_table)
        filter_row.addWidget(self._del_type, 1)
        layout.addLayout(filter_row)

        # Feature table
        self._del_table = QTableWidget()
        self._del_table.setSelectionBehavior(_SelectRows)
        self._del_table.setSelectionMode(_ExtendedSel)
        self._del_table.setEditTriggers(_NoEditTriggers)
        self._del_table.horizontalHeader().setSectionResizeMode(_HeaderStretch)
        self._del_table.setAlternatingRowColors(True)
        layout.addWidget(self._del_table)

        # Options
        self._lbl_photo_dir = QLabel("")
        self._lbl_photo_dir.setStyleSheet("color: #666; font-size: 10px;")
        self._lbl_photo_dir.setWordWrap(True)
        layout.addWidget(self._lbl_photo_dir)

        btn_del = QPushButton("🗑 Supprimer la sélection")
        btn_del.setStyleSheet("color: white; background-color: #c0392b;")
        btn_del.clicked.connect(self._delete_selected_features)
        layout.addWidget(btn_del)

        self._populate_delete_layer_combo()
        return tab

    def _populate_delete_layer_combo(self):
        """Peuple le sélecteur avec toutes les couches vecteur Point et LineString."""
        self._del_layer_combo.blockSignals(True)
        self._del_layer_combo.clear()
        _accepted = (QgsWkbTypes.PointGeometry, QgsWkbTypes.LineGeometry)
        for layer in QgsProject.instance().mapLayers().values():
            try:
                if layer.geometryType() in _accepted:
                    self._del_layer_combo.addItem(layer.name(), layer.id())
            except AttributeError:
                pass  # couche non vecteur (raster, etc.)
        self._del_layer_combo.blockSignals(False)
        self._del_on_layer_changed()

    def _del_current_layer(self):
        layer_id = self._del_layer_combo.currentData()
        return QgsProject.instance().mapLayer(layer_id) if layer_id else None

    def _del_on_layer_changed(self):
        """Couche changée : rebâtir le filtre de type puis le tableau."""
        combo = getattr(self, "_del_type", None)
        if combo is not None:
            self._populate_type_filter(combo, self._del_current_layer())
        self._refresh_delete_table()

    def _refresh_delete_table(self):
        self._del_table.clear()
        self._del_table.setRowCount(0)
        self._lbl_photo_dir.setText("")

        layer = self._del_current_layer()
        if layer is None:
            return

        # Colonnes affichées : priorité aux colonnes connues, selon le type de couche
        field_names = [f.name() for f in layer.fields()]
        display_cols = [f for f in ("reference", "name", "type", "date_disc") if f in field_names]
        if not display_cols:
            # Couche de traçages ou autre couche non-standard
            display_cols = [f for f in ("point_injection", "point_sortie", "colorant", "date_injection")
                            if f in field_names]
        if not display_cols:
            display_cols = field_names[:4]

        self._del_table.setColumnCount(len(display_cols))
        self._del_table.setHorizontalHeaderLabels(display_cols)

        # Recherche (sur les colonnes affichées, insensible casse/accents) +
        # filtre par type (si la couche a un champ « type »).
        search = self._fold(self._del_search.text().strip()) \
            if hasattr(self, "_del_search") else ""
        type_f = (self._del_type.currentData() or "") \
            if hasattr(self, "_del_type") else ""

        def _passes(feat):
            if type_f and "type" in field_names and str(feat["type"] or "") != type_f:
                return False
            if search:
                hay = self._fold(" ".join(
                    str(feat[c]) for c in display_cols
                    if c in field_names and feat[c]))
                if search not in hay:
                    return False
            return True

        features = [f for f in layer.getFeatures() if _passes(f)]
        self._del_table.setRowCount(len(features))
        self._del_fid_map = {}  # row → feature id

        for row, feat in enumerate(features):
            self._del_fid_map[row] = feat.id()
            for col, fname in enumerate(display_cols):
                val = feat[fname] if fname in field_names else ""
                self._del_table.setItem(row, col, QTableWidgetItem(str(val) if val else ""))

        # Indicateur dossier photos — uniquement pertinent pour les couches Point
        if layer.geometryType() == QgsWkbTypes.PointGeometry:
            source = layer.dataProvider().dataSourceUri().split("|")[0]
            if os.path.isfile(source):
                folder = os.path.dirname(source)
                self._lbl_photo_dir.setText(f"Dossier photos recherché : {folder}")
            else:
                self._lbl_photo_dir.setText("Couche en mémoire — suppression des photos non disponible.")
        else:
            self._lbl_photo_dir.setText("")

    def _delete_selected_features(self):
        layer = self._del_current_layer()
        if layer is None:
            return

        selected_rows = list({idx.row() for idx in self._del_table.selectedIndexes()})
        if not selected_rows:
            QMessageBox.information(self, "Aucune sélection", "Sélectionnez au moins une entrée.")
            return

        fids = [self._del_fid_map[r] for r in selected_rows]
        field_names = [f.name() for f in layer.fields()]

        # Gather references for photo deletion
        refs_to_delete = []
        if "reference" in field_names:
            for feat in layer.getFeatures():
                if feat.id() in fids:
                    ref = feat["reference"]
                    if ref:
                        refs_to_delete.append(str(ref))

        n = len(fids)
        is_point = layer.geometryType() == QgsWkbTypes.PointGeometry
        photo_note = "\nLes dossiers photos associés seront également supprimés." \
                     if refs_to_delete and is_point else ""
        reply = QMessageBox.question(
            self, "Confirmer la suppression",
            f"Supprimer {n} entrée(s) ?{photo_note}",
            _MsgYes | _MsgNo,
            _MsgNo
        )
        if reply == _MsgNo:
            return

        # Delete features from layer
        layer.startEditing()
        layer.deleteFeatures(fids)
        layer.commitChanges()

        # Delete photo folders (Point layers only)
        if refs_to_delete and is_point:
            source = layer.dataProvider().dataSourceUri().split("|")[0]
            if os.path.isfile(source):
                base_dir = os.path.dirname(source)
                deleted_dirs, missing_dirs = [], []
                for ref in refs_to_delete:
                    photo_dir = os.path.join(base_dir, ref)
                    if os.path.isdir(photo_dir):
                        shutil.rmtree(photo_dir)
                        deleted_dirs.append(ref)
                    else:
                        missing_dirs.append(ref)
                detail = ""
                if deleted_dirs:
                    detail += f"\nDossiers supprimés : {', '.join(deleted_dirs)}"
                if missing_dirs:
                    detail += f"\nDossiers introuvables : {', '.join(missing_dirs)}"
                QMessageBox.information(self, "Suppression effectuée",
                                        f"{n} entrée(s) supprimée(s).{detail}")
            else:
                QMessageBox.information(self, "Suppression effectuée",
                                        f"{n} entrée(s) supprimée(s).\n"
                                        "Photos non supprimées : couche en mémoire sans chemin de fichier.")
        else:
            QMessageBox.information(self, "Suppression effectuée", f"{n} entrée(s) supprimée(s).")

        self._refresh_delete_table()

    # ---------------------------------------------------------------- Info tab --

    def _build_info_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setAlignment(_AlignTop)

        plugin_dir = os.path.dirname(os.path.abspath(__file__))

        logo_path = os.path.join(plugin_dir, "brand",
                                 "karstentry-pastille-ronde-ocre-clair-512.png")
        title = QLabel()
        title.setAlignment(_AlignCenter)
        if os.path.isfile(logo_path):
            title.setPixmap(QPixmap(logo_path).scaledToWidth(160, _Smooth))
        else:
            title.setText("Karst Entry")
            title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        version = QLabel("Version 1.0  —  Plugin QGIS de saisie de phénomènes karstiques")
        version.setStyleSheet("color: white;")
        layout.addWidget(version)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #ccc; margin: 8px 0;")
        layout.addWidget(sep)

        license_box = QGroupBox("Licence")
        lb = QVBoxLayout(license_box)
        lbl_lic = QLabel(
            "© 2026 Julien Tournois\n"
            "Usage non-commercial uniquement (PolyForm Noncommercial 1.0).\n"
            "Toute utilisation commerciale est interdite sans autorisation écrite.\n"
            "Contact : julien.tournois@gmail.com"
        )
        lbl_lic.setWordWrap(True)
        lb.addWidget(lbl_lic)
        layout.addWidget(license_box)

        guide_box = QGroupBox("Guide utilisateur")
        gb = QVBoxLayout(guide_box)
        lbl_guide = QLabel(
            'Le guide utilisateur illustré est disponible dans le fichier '
            '<b>KarstEntry_Documentation.pdf</b> du répertoire du plugin.'
        )
        lbl_guide.setWordWrap(True)
        gb.addWidget(lbl_guide)

        btn_open = QPushButton("📖 Ouvrir le guide utilisateur")
        guide_pdf = os.path.join(plugin_dir, "KarstEntry_Documentation.pdf")
        guide_md = os.path.join(plugin_dir, "INSTALL.md")
        guide_path = guide_pdf if os.path.isfile(guide_pdf) else guide_md
        btn_open.clicked.connect(lambda: self._open_file(guide_path))
        gb.addWidget(btn_open)
        layout.addWidget(guide_box)

        layout.addStretch()
        return tab

    def _open_file(self, path):
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Fichier introuvable", path)
            return
        import subprocess, sys
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])

    # ---------------------------------------------------------------- Fiche tab --

    def _build_fiche_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Layer selector
        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Couche :"))
        self._fiche_layer_combo = QComboBox()
        self._fiche_layer_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._fiche_layer_combo.currentIndexChanged.connect(self._fiche_on_layer_changed)
        layer_row.addWidget(self._fiche_layer_combo)
        btn_refresh_fiche = QPushButton("↻")
        btn_refresh_fiche.setFixedWidth(32)
        btn_refresh_fiche.clicked.connect(self._fiche_populate_layer_combo)
        layer_row.addWidget(btn_refresh_fiche)
        layout.addLayout(layer_row)

        # Recherche (référence/nom) + filtre par type
        filter_row = QHBoxLayout()
        self._fiche_search = QLineEdit()
        self._fiche_search.setPlaceholderText("🔎 Rechercher (référence, nom)…")
        self._fiche_search.setClearButtonEnabled(True)
        self._fiche_search.textChanged.connect(self._fiche_populate_features)
        filter_row.addWidget(self._fiche_search, 2)
        self._fiche_type_filter = QComboBox()
        self._fiche_type_filter.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._fiche_type_filter.currentIndexChanged.connect(self._fiche_populate_features)
        filter_row.addWidget(self._fiche_type_filter, 1)
        layout.addLayout(filter_row)

        # Feature selector
        feat_row = QHBoxLayout()
        feat_row.addWidget(QLabel("Phénomène :"))
        self._fiche_feat_combo = QComboBox()
        self._fiche_feat_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._fiche_feat_combo.currentIndexChanged.connect(self._fiche_show)
        feat_row.addWidget(self._fiche_feat_combo)
        layout.addLayout(feat_row)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._fiche_content = QWidget()
        self._fiche_layout = QVBoxLayout(self._fiche_content)
        self._fiche_layout.setAlignment(_AlignTop)
        scroll.setWidget(self._fiche_content)
        layout.addWidget(scroll)

        # Connect to QGIS selection changes
        self._fiche_layer_conn = None
        self._tabs.currentChanged.connect(self._fiche_on_tab_activated)

        self._fiche_populate_layer_combo()
        return tab

    def _fiche_on_tab_activated(self, index):
        if self._tabs.tabText(index) == "🔍 Fiche":
            self._fiche_populate_layer_combo()

    def _fiche_populate_layer_combo(self):
        self._fiche_layer_combo.blockSignals(True)
        self._fiche_layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and \
                    layer.geometryType() == QgsWkbTypes.PointGeometry:
                self._fiche_layer_combo.addItem(layer.name(), layer.id())
        self._fiche_layer_combo.blockSignals(False)
        self._fiche_on_layer_changed()

    def _fiche_on_layer_changed(self):
        # Disconnect previous selection signal
        if self._fiche_layer_conn is not None:
            try:
                self._fiche_layer_conn
            except Exception:
                pass
            self._fiche_layer_conn = None

        layer = self._fiche_current_layer()
        combo = getattr(self, "_fiche_type_filter", None)
        if combo is not None:
            self._populate_type_filter(combo, layer)

        self._fiche_populate_features()

        # Auto-select when feature selected on canvas
        if layer is not None:
            self._fiche_layer_conn = layer.selectionChanged.connect(
                self._fiche_sync_selection)

    def _fiche_populate_features(self):
        """Remplit la liste des phénomènes selon la recherche et le filtre type."""
        self._fiche_feat_combo.blockSignals(True)
        self._fiche_feat_combo.clear()
        layer = self._fiche_current_layer()
        if layer is not None:
            fields = [f.name() for f in layer.fields()]
            search = (self._fiche_search.text().strip().lower()
                      if hasattr(self, "_fiche_search") else "")
            type_f = (self._fiche_type_filter.currentData() or ""
                      if hasattr(self, "_fiche_type_filter") else "")
            for feat in layer.getFeatures():
                if not self._feature_matches(feat, fields, search, type_f):
                    continue
                self._fiche_feat_combo.addItem(
                    self._fiche_feat_label(feat, layer), feat.id())
        self._fiche_feat_combo.blockSignals(False)
        self._fiche_show()

    def _fiche_feat_label(self, feat, layer):
        fields = [f.name() for f in layer.fields()]
        name = feat["name"] if "name" in fields else ""
        ref = feat["reference"] if "reference" in fields else str(feat.id())
        label = f"{ref} — {name}" if name else ref
        return label or f"ID {feat.id()}"

    def _fiche_sync_selection(self):
        layer = self._fiche_current_layer()
        if layer is None:
            return
        selected = layer.selectedFeatureIds()
        if not selected:
            return
        fid = selected[0]
        for i in range(self._fiche_feat_combo.count()):
            if self._fiche_feat_combo.itemData(i) == fid:
                self._fiche_feat_combo.setCurrentIndex(i)
                # Switch to fiche tab automatically
                for t in range(self._tabs.count()):
                    if self._tabs.tabText(t) == "🔍 Fiche":
                        self._tabs.setCurrentIndex(t)
                        break
                break

    def _fiche_current_layer(self):
        layer_id = self._fiche_layer_combo.currentData()
        if not layer_id:
            return None
        return QgsProject.instance().mapLayer(layer_id)

    @staticmethod
    def _is_blank(val):
        """True si une valeur d'attribut est vide : None, chaîne vide, ou NULL QGIS."""
        if val is None:
            return True
        s = str(val).strip()
        return s == "" or s.upper() == "NULL"

    def _fiche_show(self):
        # Clear previous content
        while self._fiche_layout.count():
            item = self._fiche_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        layer = self._fiche_current_layer()
        fid = self._fiche_feat_combo.currentData()
        if layer is None or fid is None:
            return

        feat = next(layer.getFeatures([fid]), None)
        if feat is None:
            return

        fields = [f.name() for f in layer.fields()]
        photo_field = "photos" if "photos" in fields else None

        # Attribute table — on masque les champs vides (None, "", NULL QGIS).
        SKIP = {"photos"}
        for field in layer.fields():
            fname = field.name()
            if fname in SKIP:
                continue
            val = feat[fname]
            if self._is_blank(val):
                continue
            row = QHBoxLayout()
            lbl_key = QLabel(f"<b>{fname}</b>")
            lbl_key.setFixedWidth(120)
            lbl_key.setAlignment(_AlignTop | _AlignLeft)
            lbl_val = QLabel(str(val) if val is not None else "")
            lbl_val.setWordWrap(True)
            lbl_val.setTextInteractionFlags(_TextSelect)
            row.addWidget(lbl_key)
            row.addWidget(lbl_val, 1)
            container = QWidget()
            container.setLayout(row)
            self._fiche_layout.addWidget(container)

        # Separator
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #ccc;")
        self._fiche_layout.addWidget(sep)

        # Photos
        if photo_field:
            raw = feat[photo_field] or ""
            paths = [p.strip() for p in str(raw).split(";") if p.strip()]
            if paths:
                lbl_photos = QLabel("<b>Photos</b>")
                self._fiche_layout.addWidget(lbl_photos)

                # Try to resolve paths relative to the layer source
                layer_dir = ""
                src = layer.dataProvider().dataSourceUri().split("|")[0]
                if os.path.isfile(src):
                    layer_dir = os.path.dirname(src)

                photo_row = QHBoxLayout()
                photo_row.setAlignment(_AlignLeft)
                for raw_path in paths:
                    abs_path = raw_path
                    if not os.path.isabs(raw_path) and layer_dir:
                        abs_path = os.path.join(layer_dir, raw_path)

                    thumb = QLabel()
                    thumb.setFixedSize(160, 160)
                    thumb.setAlignment(_AlignCenter)
                    thumb.setStyleSheet("border: 1px solid #aaa; background: #f5f5f5;")
                    thumb.setToolTip(abs_path)

                    if os.path.isfile(abs_path):
                        pix = QPixmap(abs_path).scaled(
                            156, 156, _KeepRatio, _Smooth)
                        thumb.setPixmap(pix)
                    else:
                        thumb.setText("⚠ introuvable")
                        thumb.setStyleSheet(
                            "border: 1px solid #e74c3c; color: #e74c3c; font-size: 10px;")

                    photo_row.addWidget(thumb)

                pw = QWidget()
                pw.setLayout(photo_row)
                self._fiche_layout.addWidget(pw)

    # ---------------------------------------------------------------- Traçage tab --

    def _build_tracage_tab(self):
        """Construit l'onglet de saisie des traçages hydrogéologiques.

        Un traçage relie une perte (source) à une résurgence (destination).
        La géométrie ligne est créée automatiquement depuis les coordonnées
        des deux entités sélectionnées.
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        scroll.setWidget(form_widget)

        # --- Source (perte) ---
        src_group = QGroupBox("Point d'injection du colorant")
        src_layout = QFormLayout(src_group)

        self._tr_src_layer = QComboBox()
        self._tr_src_layer.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        src_layer_row = QHBoxLayout()
        src_layer_row.addWidget(self._tr_src_layer)
        btn_refresh_src = QPushButton("↻")
        btn_refresh_src.setFixedWidth(32)
        btn_refresh_src.clicked.connect(self._tr_refresh_src)
        src_layer_row.addWidget(btn_refresh_src)
        src_layout.addRow("Couche :", src_layer_row)

        # Recherche + filtre par type
        self._tr_src_search = QLineEdit()
        self._tr_src_search.setPlaceholderText("🔎 Rechercher (référence, nom)…")
        self._tr_src_search.setClearButtonEnabled(True)
        self._tr_src_search.textChanged.connect(self._tr_features_src)
        src_layout.addRow("Recherche :", self._tr_src_search)
        self._tr_src_type = QComboBox()
        self._tr_src_type.currentIndexChanged.connect(self._tr_features_src)
        src_layout.addRow("Type :", self._tr_src_type)

        self._tr_src_feat = QComboBox()
        self._tr_src_feat.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        src_layout.addRow("Entité :", self._tr_src_feat)

        self._tr_src_layer.currentIndexChanged.connect(self._tr_on_layer_changed_src)
        form.addRow(src_group)

        # --- Destination (résurgence) ---
        dst_group = QGroupBox("Sortie du colorant")
        dst_layout = QFormLayout(dst_group)

        self._tr_dst_layer = QComboBox()
        self._tr_dst_layer.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        dst_layer_row = QHBoxLayout()
        dst_layer_row.addWidget(self._tr_dst_layer)
        btn_refresh_dst = QPushButton("↻")
        btn_refresh_dst.setFixedWidth(32)
        btn_refresh_dst.clicked.connect(self._tr_refresh_dst)
        dst_layer_row.addWidget(btn_refresh_dst)
        dst_layout.addRow("Couche :", dst_layer_row)

        # Recherche + filtre par type
        self._tr_dst_search = QLineEdit()
        self._tr_dst_search.setPlaceholderText("🔎 Rechercher (référence, nom)…")
        self._tr_dst_search.setClearButtonEnabled(True)
        self._tr_dst_search.textChanged.connect(self._tr_features_dst)
        dst_layout.addRow("Recherche :", self._tr_dst_search)
        self._tr_dst_type = QComboBox()
        self._tr_dst_type.currentIndexChanged.connect(self._tr_features_dst)
        dst_layout.addRow("Type :", self._tr_dst_type)

        self._tr_dst_feat = QComboBox()
        self._tr_dst_feat.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        dst_layout.addRow("Entité :", self._tr_dst_feat)

        self._tr_dst_layer.currentIndexChanged.connect(self._tr_on_layer_changed_dst)
        form.addRow(dst_group)

        # --- Métadonnées ---
        cfg = _load_config().get("tracage", {})
        colorants = cfg.get("colorants", _DEFAULT_COLORANTS)
        resultats  = cfg.get("resultats",  _DEFAULT_RESULTATS)

        self._tr_colorant = QComboBox()
        self._tr_colorant.addItems(colorants)
        self._tr_colorant.setEditable(True)
        form.addRow("Colorant", self._tr_colorant)

        self._tr_resultat = QComboBox()
        self._tr_resultat.addItems(resultats)
        form.addRow("Résultat", self._tr_resultat)

        self._tr_date_inj = QDateEdit()
        self._tr_date_inj.setCalendarPopup(True)
        self._tr_date_inj.setDate(QDate.currentDate())
        form.addRow("Date d'injection", self._tr_date_inj)

        self._tr_date_det = QDateEdit()
        self._tr_date_det.setCalendarPopup(True)
        self._tr_date_det.setDate(QDate.currentDate())
        form.addRow("Date de détection", self._tr_date_det)

        self._tr_temps = QLineEdit()
        self._tr_temps.setPlaceholderText("En heures")
        form.addRow("Temps de transit", self._tr_temps)

        self._tr_operateurs = QLineEdit()
        self._tr_operateurs.setPlaceholderText("Nom1, Nom2, …")
        form.addRow("Opérateurs", self._tr_operateurs)

        self._tr_comment = QTextEdit()
        self._tr_comment.setFixedHeight(70)
        self._tr_comment.setTabChangesFocus(True)
        form.addRow("Commentaire", self._tr_comment)

        layout.addWidget(scroll)

        # Compteur file d'attente traçages
        self._tr_lbl_queue = QLabel("File d'attente : 0 traçage(s)")
        self._tr_lbl_queue.setStyleSheet("font-weight: bold; color: #27ae60;")
        layout.addWidget(self._tr_lbl_queue)

        btn_row = QHBoxLayout()

        btn_qgis = QPushButton("🗺 Ajouter dans QGIS")
        btn_qgis.setToolTip("Envoie tous les traçages en attente dans la couche QGIS")
        btn_qgis.clicked.connect(self._tr_save_to_qgis)
        btn_row.addWidget(btn_qgis)

        btn_queue = QPushButton("➕ Ajouter à la file d'attente")
        btn_queue.setToolTip("Met le traçage en attente sans toucher QGIS")
        btn_queue.clicked.connect(self._tr_add_to_queue)
        btn_queue.setDefault(True)
        btn_queue.setAutoDefault(True)
        btn_row.addWidget(btn_queue)

        layout.addLayout(btn_row)

        # Peupler les couches au démarrage
        self._tr_refresh_src()
        self._tr_refresh_dst()
        return tab

    # Wrappers par côté (source / destination) pour router les signaux.
    def _tr_refresh_src(self):
        self._tr_populate_layers(self._tr_src_layer, self._tr_src_feat,
                                 self._tr_src_search, self._tr_src_type)

    def _tr_refresh_dst(self):
        self._tr_populate_layers(self._tr_dst_layer, self._tr_dst_feat,
                                 self._tr_dst_search, self._tr_dst_type)

    def _tr_on_layer_changed_src(self):
        self._populate_type_filter(
            self._tr_src_type, self._tr_layer_of(self._tr_src_layer))
        self._tr_features_src()

    def _tr_on_layer_changed_dst(self):
        self._populate_type_filter(
            self._tr_dst_type, self._tr_layer_of(self._tr_dst_layer))
        self._tr_features_dst()

    def _tr_features_src(self):
        self._tr_populate_features(self._tr_src_layer, self._tr_src_feat,
                                   self._tr_src_search, self._tr_src_type)

    def _tr_features_dst(self):
        self._tr_populate_features(self._tr_dst_layer, self._tr_dst_feat,
                                   self._tr_dst_search, self._tr_dst_type)

    @staticmethod
    def _tr_layer_of(layer_combo):
        lid = layer_combo.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    def _tr_populate_layers(self, layer_combo, feat_combo, search_w=None, type_w=None):
        """Peuple un sélecteur de couche avec toutes les couches point du projet."""
        layer_combo.blockSignals(True)
        layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if (isinstance(layer, QgsVectorLayer)
                    and layer.geometryType() == QgsWkbTypes.PointGeometry):
                layer_combo.addItem(layer.name(), layer.id())
        layer_combo.blockSignals(False)
        if type_w is not None:
            self._populate_type_filter(type_w, self._tr_layer_of(layer_combo))
        self._tr_populate_features(layer_combo, feat_combo, search_w, type_w)

    def _tr_populate_features(self, layer_combo, feat_combo, search_w=None, type_w=None):
        """Peuple un sélecteur d'entités depuis la couche, filtré recherche/type."""
        feat_combo.blockSignals(True)
        feat_combo.clear()
        layer_id = layer_combo.currentData()
        if layer_id:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                fields = [f.name() for f in layer.fields()]
                search = (search_w.text().strip().lower()
                          if search_w is not None else "")
                type_f = (type_w.currentData() or "" if type_w is not None else "")
                for feat in layer.getFeatures():
                    if not self._feature_matches(feat, fields, search, type_f):
                        continue
                    ref  = str(feat["reference"]) if "reference" in fields else ""
                    name = str(feat["name"])       if "name"      in fields else ""
                    label = f"{ref} — {name}" if ref and name else (ref or name or f"ID {feat.id()}")
                    feat_combo.addItem(label, feat.id())
        feat_combo.blockSignals(False)

    def _tr_get_point(self, layer_combo, feat_combo):
        """Retourne (QgsPointXY dans CRS projet, ref, nom) pour l'entité sélectionnée.

        Reprojette automatiquement si le CRS de la couche diffère du projet.
        Retourne (None, '', '') si aucune entité valide.
        """
        layer_id = layer_combo.currentData()
        fid      = feat_combo.currentData()
        if layer_id is None or fid is None:
            return None, "", ""

        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return None, "", ""

        feat = next(layer.getFeatures([fid]), None)
        if feat is None or feat.geometry().isNull():
            return None, "", ""

        pt = feat.geometry().asPoint()

        # Reprojection vers CRS projet si nécessaire
        proj_crs = self.canvas.mapSettings().destinationCrs()
        if layer.crs() != proj_crs:
            tr = QgsCoordinateTransform(layer.crs(), proj_crs, QgsProject.instance())
            pt = tr.transform(pt)

        fields = [f.name() for f in layer.fields()]
        ref  = str(feat["reference"]) if "reference" in fields else ""
        name = str(feat["name"])       if "name"      in fields else ""
        return QgsPointXY(pt.x(), pt.y()), ref, name

    def _tr_read_form(self):
        """Lit le formulaire traçage et retourne un dict, ou None si invalide."""
        pt_src, ref_src, nom_src = self._tr_get_point(self._tr_src_layer, self._tr_src_feat)
        pt_dst, ref_dst, nom_dst = self._tr_get_point(self._tr_dst_layer, self._tr_dst_feat)

        if pt_src is None:
            QMessageBox.warning(self, "Source manquante",
                                "Sélectionnez une entité source valide avec géométrie.")
            return None
        if pt_dst is None:
            QMessageBox.warning(self, "Destination manquante",
                                "Sélectionnez une entité destination valide avec géométrie.")
            return None
        if pt_src.x() == pt_dst.x() and pt_src.y() == pt_dst.y():
            QMessageBox.warning(self, "Points identiques",
                                "La source et la destination sont au même endroit.")
            return None

        # Calcul géodésique de la distance (ellipsoïde du projet)
        proj_crs = self.canvas.mapSettings().destinationCrs()
        da = QgsDistanceArea()
        da.setSourceCrs(proj_crs, QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid())
        distance_m = round(da.measureLine(pt_src, pt_dst), 1)

        return {
            "pt_src":          pt_src,
            "pt_dst":          pt_dst,
            "point_injection": nom_src,
            "point_sortie":    nom_dst,
            "colorant":      self._tr_colorant.currentText(),
            "resultat":      self._tr_resultat.currentText(),
            "date_injection": self._tr_date_inj.date().toString(_ISODate),
            "date_detection": self._tr_date_det.date().toString(_ISODate),
            "temps_transit": self._tr_temps.text().strip(),
            "distance_m":    distance_m,
            "operateurs":    self._tr_operateurs.text().strip(),
            "commentaire":   self._tr_comment.toPlainText().strip(),
        }

    def _tr_add_to_queue(self):
        """Ajoute le traçage courant à la file d'attente sans toucher QGIS."""
        entry = self._tr_read_form()
        if entry is None:
            return
        self._tracage_queue.append(entry)
        self._tr_update_counter()
        self._tr_clear_form()

    def _tr_save_to_qgis(self):
        """Lit le formulaire courant (si rempli) puis vide toute la file dans QGIS.

        Même comportement que l'onglet Nouvelle saisie : si une source et une
        destination sont sélectionnées, le traçage courant est ajouté à la file
        avant le flush. Si le formulaire est vide et la file vide, avertissement.
        """
        if self._tr_src_layer.currentData() is not None \
                and self._tr_dst_layer.currentData() is not None:
            entry = self._tr_read_form()
            if entry is None:
                return          # validation échouée, message déjà affiché
            self._tracage_queue.append(entry)
            self._tr_clear_form()
        elif not self._tracage_queue:
            QMessageBox.warning(self, "Rien à ajouter",
                                "Le formulaire est vide et la file d'attente est vide.")
            return
        self._tr_flush_queue()

    _TRACAGES_LAYER_NAME = "Inventaire Traçages"

    @staticmethod
    def _tracages_field_defs():
        """Champs de la couche traçages, chargés depuis karst_schema.json."""
        return _qgs_fields(
            _schema_fields("tracages", _FALLBACK_TRACAGES_FIELDS))

    def _resolve_tracages_layer(self):
        """Retrouve une couche traçages existante dans le projet, sans en créer.

        Cherche une couche ligne « Inventaire Traçages » (ou portant le schéma
        traçage), en préférant une source sur disque (ogr) à la mémoire.
        """
        if self._tracage_layer_id is not None:
            cached = QgsProject.instance().mapLayer(self._tracage_layer_id)
            if cached is not None:
                return cached
            self._tracage_layer_id = None  # couche supprimée → cache obsolète

        candidates = []
        for lyr in QgsProject.instance().mapLayers().values():
            if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
                continue
            if lyr.geometryType() != QgsWkbTypes.LineGeometry:
                continue
            fields = set(lyr.fields().names())
            if lyr.name() == self._TRACAGES_LAYER_NAME or \
                    {"point_injection", "point_sortie", "colorant"} <= fields:
                candidates.append(lyr)
        if not candidates:
            return None
        candidates.sort(key=lambda l: 0 if l.dataProvider().name() == "ogr" else 1)
        self._tracage_layer_id = candidates[0].id()
        return candidates[0]

    def _create_persistent_tracages_layer(self):
        """Crée une couche traçages PERSISTANTE (GeoPackage sur disque), pas en mémoire."""
        proj_dir = QgsProject.instance().absolutePath()
        if proj_dir:
            path = os.path.join(proj_dir, f"{self._TRACAGES_LAYER_NAME}.gpkg")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Enregistrer la couche traçages",
                f"{self._TRACAGES_LAYER_NAME}.gpkg", "GeoPackage (*.gpkg)")
            if not path:
                return None

        if not os.path.isfile(path):
            proj_crs = self.canvas.mapSettings().destinationCrs()
            mem = QgsVectorLayer(f"LineString?crs={proj_crs.authid()}",
                                 self._TRACAGES_LAYER_NAME, "memory")
            mem.dataProvider().addAttributes(self._tracages_field_defs())
            mem.updateFields()
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = self._TRACAGES_LAYER_NAME
            ctx = QgsProject.instance().transformContext()
            try:
                res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, ctx, options)
            except AttributeError:
                res = QgsVectorFileWriter.writeAsVectorFormatV2(mem, path, ctx, options)
            if res[0] != QgsVectorFileWriter.NoError:
                QMessageBox.warning(self, "Création impossible",
                                    f"Impossible de créer le GeoPackage :\n{res[1]}")
                return None

        layer = QgsVectorLayer(path, self._TRACAGES_LAYER_NAME, "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Couche invalide",
                                f"Le GeoPackage créé est illisible :\n{path}")
            return None
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _tr_flush_queue(self):
        """Écrit tous les traçages de la file dans une couche persistante sur disque."""
        if not self._tracage_queue:
            return

        layer = self._resolve_tracages_layer()
        if layer is None:
            layer = self._create_persistent_tracages_layer()
        if layer is None:
            return  # création annulée par l'utilisateur
        self._tracage_layer_id = layer.id()
        pr    = layer.dataProvider()
        added = 0

        for entry in self._tracage_queue:
            feat = QgsFeature(layer.fields())
            # Ligne entre les deux points
            feat.setGeometry(QgsGeometry.fromPolylineXY([entry["pt_src"], entry["pt_dst"]]))
            for field in ("point_injection", "point_sortie",
                          "colorant", "resultat", "date_injection", "date_detection",
                          "temps_transit", "distance_m", "operateurs", "commentaire"):
                feat.setAttribute(field, entry[field])
            pr.addFeature(feat)
            added += 1

        layer.updateExtents()
        self._tracage_queue.clear()

        if not QgsProject.instance().mapLayer(layer.id()):
            QgsProject.instance().addMapLayer(layer)

        self._tr_update_counter()
        QMessageBox.information(
            self, "Traçages ajoutés",
            f"{added} traçage(s) ajouté(s) à la couche « Inventaire Traçages »."
        )

    def _tr_update_counter(self):
        """Met à jour le compteur de file d'attente des traçages."""
        count = len(self._tracage_queue)
        self._tr_lbl_queue.setText(f"File d'attente : {count} traçage(s)")
        self._tr_lbl_queue.setStyleSheet(
            "font-weight: bold; color: #c0392b;" if count > 0
            else "font-weight: bold; color: #27ae60;"
        )

    def _tr_clear_form(self):
        """Remet les champs métadonnées du formulaire traçage à leur état initial."""
        self._tr_colorant.setCurrentIndex(0)
        self._tr_resultat.setCurrentIndex(0)
        self._tr_date_inj.setDate(QDate.currentDate())
        self._tr_date_det.setDate(QDate.currentDate())
        self._tr_temps.clear()
        self._tr_operateurs.clear()
        self._tr_comment.setPlainText("")

    # ------------------------------------------------------------- Vues par champ tab --

    def _build_views_tab(self):
        """Onglet « Vues » : génère des couches filtrées vivantes par valeur d'un
        champ (commune par défaut). Chaque vue pointe sur le MÊME GeoPackage —
        pas de copie — et reflète donc le source en direct."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        intro = QLabel(
            "Crée une couche filtrée par valeur de champ (ex. une couche par "
            "<b>commune</b>), regroupée dans le panneau des couches. Les vues "
            "pointent sur la même couche source : elles se mettent à jour "
            "automatiquement quand tu modifies les données. Aucune copie de fichier."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Couche :"))
        self._views_layer_combo = QComboBox()
        self._views_layer_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._views_layer_combo.currentIndexChanged.connect(self._views_populate_fields)
        layer_row.addWidget(self._views_layer_combo)
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(32)
        btn_refresh.clicked.connect(self._views_populate_layers)
        layer_row.addWidget(btn_refresh)
        layout.addLayout(layer_row)

        field_row = QHBoxLayout()
        field_row.addWidget(QLabel("Champ :"))
        self._views_field_combo = QComboBox()
        self._views_field_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        field_row.addWidget(self._views_field_combo)
        layout.addLayout(field_row)

        btn_gen = QPushButton("🗂 Générer les vues par champ")
        btn_gen.clicked.connect(self._views_generate)
        layout.addWidget(btn_gen)

        self._views_info = QLabel("")
        self._views_info.setWordWrap(True)
        layout.addWidget(self._views_info)

        layout.addStretch()
        self._views_populate_layers()
        return tab

    def _views_populate_layers(self):
        self._views_layer_combo.blockSignals(True)
        self._views_layer_combo.clear()
        default_index = -1
        for layer in QgsProject.instance().mapLayers().values():
            if not (hasattr(layer, "getFeatures") and hasattr(layer, "fields")):
                continue
            try:
                if not layer.isValid():
                    continue
                name, lid = layer.name(), layer.id()
            except (AttributeError, RuntimeError):
                continue
            self._views_layer_combo.addItem(name, lid)
            if name == self._CAVITES_LAYER_NAME:
                default_index = self._views_layer_combo.count() - 1
        if default_index != -1:
            self._views_layer_combo.setCurrentIndex(default_index)
        self._views_layer_combo.blockSignals(False)
        self._views_populate_fields()

    def _views_current_layer(self):
        lid = self._views_layer_combo.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    def _views_populate_fields(self):
        self._views_field_combo.blockSignals(True)
        self._views_field_combo.clear()
        layer = self._views_current_layer()
        if layer is not None:
            try:
                names = [f.name() for f in layer.fields()]
            except (AttributeError, RuntimeError):
                names = []
            default_index = -1
            for i, n in enumerate(names):
                self._views_field_combo.addItem(n)
                if n == "commune":
                    default_index = i
            if default_index != -1:
                self._views_field_combo.setCurrentIndex(default_index)
        self._views_field_combo.blockSignals(False)

    def _views_generate(self):
        """Génère une couche filtrée vivante par valeur distincte du champ."""
        layer = self._views_current_layer()
        if layer is None:
            QMessageBox.warning(self, "Aucune couche", "Sélectionne une couche.")
            return
        field = self._views_field_combo.currentText().strip()
        if not field:
            QMessageBox.warning(self, "Aucun champ", "Sélectionne un champ.")
            return

        # Valeurs distinctes non vides du champ.
        values = set()
        try:
            for feat in layer.getFeatures():
                v = feat[field]
                if v is not None and str(v).strip():
                    values.add(str(v).strip())
        except (KeyError, AttributeError, RuntimeError):
            QMessageBox.warning(self, "Champ illisible",
                                f"Impossible de lire le champ « {field} ».")
            return
        values = sorted(values)
        if not values:
            QMessageBox.information(self, "Rien à générer",
                                    f"Aucune valeur dans le champ « {field} ».")
            return
        if len(values) > 50:
            ok = QMessageBox.question(
                self, "Beaucoup de valeurs",
                f"Le champ « {field} » a {len(values)} valeurs distinctes : "
                f"cela créera {len(values)} couches. Continuer ?")
            if ok != QMessageBox.Yes:
                return

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group_name = f"{layer.name()} — par {field}"

        # Repartir propre : retirer un groupe homonyme existant.
        existing = root.findGroup(group_name)
        if existing is not None:
            root.removeChildNode(existing)
        group = root.insertGroup(0, group_name)

        created = 0
        for v in values:
            sub = layer.clone()
            sub.setName(f"{layer.name()} - {v}")
            sub.setSubsetString(f'"{field}" = \'' + v.replace("'", "''") + "'")
            project.addMapLayer(sub, False)   # False : ne pas ajouter à la racine
            group.addLayer(sub)
            created += 1

        self._views_info.setText(
            f"✓ {created} vue(s) générée(s) dans le groupe « {group_name} ». "
            f"Elles reflètent la couche source en direct.")

    # ---------------------------------------------------------------- Import CSV tab --

    def _build_import_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Step 1 — CSV file selection
        file_group = QGroupBox("1. Fichier CSV source")
        file_layout = QHBoxLayout(file_group)
        self._imp_path = QLineEdit()
        self._imp_path.setPlaceholderText("Chemin vers le fichier CSV…")
        self._imp_path.setReadOnly(True)
        btn_browse = QPushButton("Parcourir…")
        btn_browse.clicked.connect(self._imp_browse)
        file_layout.addWidget(self._imp_path)
        file_layout.addWidget(btn_browse)
        layout.addWidget(file_group)

        # Helper — format CSV attendu
        help_box = QGroupBox("Format attendu")
        help_layout = QVBoxLayout(help_box)
        help_lbl = QLabel(
            "Fichier <b>CSV</b> avec une ligne d'en-tête. Séparateur "
            "<code>;</code>, <code>,</code> ou tabulation (détecté automatiquement). "
            "Encodage UTF-8.<br><br>"
            "<b>Colonnes reconnues</b> (toutes optionnelles ; le mapping par nom "
            "identique est automatique) :<br>"
            "• <b>Position</b> : <code>x</code>/<code>X</code>/<code>longitude</code> "
            "et <code>y</code>/<code>Y</code>/<code>latitude</code><br>"
            "• <b>Identité</b> : <code>name</code> (nom), <code>type</code>, "
            "<code>reference</code><br>"
            "• <b>Dates</b> : <code>date_disc</code>, <code>date_expl</code> "
            "(format <code>AAAA-MM-JJ</code>)<br>"
            "• <b>Détails</b> : <code>prot_id</code>, <code>explorers</code>, "
            "<code>comment</code><br>"
            "• <b>Localisation</b> : <code>commune</code>, <code>code_insee</code>, "
            "<code>code_postal</code>, <code>departement</code>, <code>code_dept</code><br>"
            "• <b>Photos</b> : <code>photos</code> (chemins séparés par "
            "<code>;</code>, relatifs au dossier du CSV ou absolus)<br><br>"
            "Les colonnes inconnues peuvent être mappées à la main (couche "
            "existante) ou conservées telles quelles (nouvelle couche). "
            "Sans <code>reference</code>, une référence est générée "
            "automatiquement.<br><br>"
            "<b>Exemple :</b><br>"
            "<code>name;type;x;y;date_disc;commune</code><br>"
            "<code>Gouffre du Diable;Gouffre;6.02;47.05;2026-06-04;Malans</code>"
        )
        help_lbl.setWordWrap(True)
        help_lbl.setTextInteractionFlags(_TextSelect)
        help_lbl.setStyleSheet("font-size: 10px;")
        help_layout.addWidget(help_lbl)
        layout.addWidget(help_box)

        # Step 2 — Destination
        dest_group = QGroupBox("2. Destination")
        dest_layout = QVBoxLayout(dest_group)
        self._imp_radio_new      = QRadioButton("Créer une nouvelle couche (mémoire)")
        self._imp_radio_existing = QRadioButton("Importer dans une couche existante")
        self._imp_radio_new.setChecked(True)
        dest_layout.addWidget(self._imp_radio_new)
        dest_layout.addWidget(self._imp_radio_existing)

        self._imp_layer_combo = QComboBox()
        self._imp_layer_combo.setEnabled(False)
        self._imp_layer_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        existing_row = QHBoxLayout()
        existing_row.addWidget(QLabel("Couche :"))
        existing_row.addWidget(self._imp_layer_combo)
        btn_refresh_imp = QPushButton("↻")
        btn_refresh_imp.setFixedWidth(32)
        btn_refresh_imp.setToolTip("Rafraîchir les couches du projet")
        btn_refresh_imp.clicked.connect(self._imp_populate_layers)
        existing_row.addWidget(btn_refresh_imp)
        btn_from_file = QPushButton("📁")
        btn_from_file.setFixedWidth(32)
        btn_from_file.setToolTip("Choisir une couche dans un fichier (GeoPackage, Shapefile…)")
        btn_from_file.clicked.connect(self._imp_add_file_layer)
        existing_row.addWidget(btn_from_file)
        dest_layout.addLayout(existing_row)
        layout.addWidget(dest_group)

        self._imp_radio_new.toggled.connect(
            lambda checked: (self._imp_layer_combo.setEnabled(not checked),
                             self._imp_refresh_mapping()))

        # Step 3 — Column config (shown after CSV is loaded)
        self._imp_config_group = QGroupBox("3. Configuration des colonnes")
        config_layout = QVBoxLayout(self._imp_config_group)

        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("Colonne de référence (dédoublonnage) :"))
        self._imp_ref_combo = QComboBox()
        ref_row.addWidget(self._imp_ref_combo)
        config_layout.addLayout(ref_row)

        crs_row = QHBoxLayout()
        crs_row.addWidget(QLabel("CRS des coordonnées source :"))
        self._imp_crs_edit = QLineEdit()
        self._imp_crs_edit.setReadOnly(True)
        self._imp_crs_edit.setPlaceholderText("Détecté automatiquement…")
        crs_row.addWidget(self._imp_crs_edit)
        btn_crs = QPushButton("📐 Changer…")
        btn_crs.clicked.connect(self._imp_select_crs)
        crs_row.addWidget(btn_crs)
        config_layout.addLayout(crs_row)
        self._imp_crs_id = None  # authid retenu, ex: "EPSG:4326"

        config_layout.addWidget(QLabel("Mapping source → destination (ignoré si nouvelle couche) :"))
        self._imp_mapping_table = QTableWidget(0, 2)
        self._imp_mapping_table.setHorizontalHeaderLabels(["Colonne CSV source", "Champ destination"])
        self._imp_mapping_table.horizontalHeader().setSectionResizeMode(_HeaderStretch)
        self._imp_mapping_table.setEditTriggers(_NoEditTriggers)
        self._imp_mapping_table.setFixedHeight(160)
        config_layout.addWidget(self._imp_mapping_table)
        self._imp_config_group.setVisible(False)
        layout.addWidget(self._imp_config_group)

        # Step 4 — Preview / info
        self._imp_info = QLabel("")
        self._imp_info.setStyleSheet("font-size: 10px;")
        self._imp_info.setWordWrap(True)
        layout.addWidget(self._imp_info)

        layout.addStretch()

        btn_import = QPushButton("📥 Lancer l'import")
        btn_import.clicked.connect(self._imp_run)
        layout.addWidget(btn_import)

        self._imp_csv_headers = []
        self._imp_populate_layers()
        return tab

    def _imp_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir un CSV", "", "CSV (*.csv *.txt)"
        )
        if not path:
            return
        self._imp_path.setText(path)
        try:
            delim = self._detect_delimiter(path)
            with open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh, delimiter=delim)
                sample_rows = [row for _, row in zip(range(5), reader)]
                headers = list(sample_rows[0].keys()) if sample_rows else []
            self._imp_csv_headers = [h.strip() for h in headers]
            self._imp_delimiter = delim

            # Détection automatique du CRS
            detected = self._detect_crs(sample_rows)
            self._imp_set_crs(detected)
            crs_note = "EPSG:4326 détecté automatiquement" if detected else "CRS du projet utilisé"

            self._imp_ref_combo.clear()
            self._imp_ref_combo.addItems(self._imp_csv_headers)
            if "reference" in self._imp_csv_headers:
                self._imp_ref_combo.setCurrentText("reference")
            self._imp_refresh_mapping()
            self._imp_config_group.setVisible(True)
            sep_label = {";": "point-virgule", ",": "virgule", "\t": "tabulation"}.get(delim, delim)
            self._imp_info.setText(
                f"Séparateur : {sep_label} | {len(self._imp_csv_headers)} colonne(s) | {crs_note}"
            )
        except Exception as e:
            QMessageBox.warning(self, "Erreur lecture CSV", str(e))

    def _imp_populate_layers(self):
        """Peuple le combo avec toutes les couches vecteur du projet (tous types de géométrie)."""
        self._imp_layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                self._imp_layer_combo.addItem(layer.name(), layer.id())
        self._imp_refresh_mapping()

    def _imp_refresh_mapping(self):
        """Rebuild the mapping table: CSV columns → destination layer fields."""
        self._imp_mapping_table.setRowCount(0)
        if not self._imp_csv_headers:
            return

        dest_fields = self._imp_dest_field_names()

        for src_col in self._imp_csv_headers:
            row = self._imp_mapping_table.rowCount()
            self._imp_mapping_table.insertRow(row)
            self._imp_mapping_table.setItem(row, 0, QTableWidgetItem(src_col))
            combo = QComboBox()
            combo.addItem("— ignorer —")
            combo.addItems(dest_fields)
            # Auto-match by name
            if src_col in dest_fields:
                combo.setCurrentText(src_col)
            self._imp_mapping_table.setCellWidget(row, 1, combo)

        self._imp_layer_combo.currentIndexChanged.connect(
            lambda: self._imp_refresh_mapping()
        )

    def _imp_dest_field_names(self):
        """Noms des champs de la couche de destination, ou liste vide si nouvelle couche."""
        if not self._imp_radio_existing.isChecked():
            return []
        data = self._imp_layer_combo.currentData()
        if not data:
            return []
        # data est soit un ID de couche projet, soit un URI OGR (couche fichier)
        layer = QgsProject.instance().mapLayer(data)
        if layer:
            return [f.name() for f in layer.fields()]
        # URI fichier (couche non chargée dans le projet)
        tmp = QgsVectorLayer(data, "_tmp", "ogr")
        return [f.name() for f in tmp.fields()] if tmp.isValid() else []

    def _imp_add_file_layer(self):
        """Ouvre un fichier et ajoute ses couches vecteur au sélecteur de destination."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir un fichier de données", "",
            "Tous les formats (*.gpkg *.shp *.geojson *.json *.sqlite)"
            ";;GeoPackage (*.gpkg)"
            ";;Shapefile (*.shp)"
            ";;GeoJSON (*.geojson *.json)"
        )
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()
        if ext == ".gpkg":
            # Lister toutes les couches vecteur du GeoPackage via OGR
            info_layer = QgsVectorLayer(path, "_info", "ogr")
            sub_layers = info_layer.dataProvider().subLayers() \
                if info_layer.isValid() else []
            # subLayers() : "id!!name!!count!!geomtype!!..."
            added = 0
            for sub in sub_layers:
                parts = sub.split("!!")
                if len(parts) < 2:
                    continue
                lname = parts[1]
                uri   = f"{path}|layername={lname}"
                label = f"{lname}  [{os.path.basename(path)}]"
                if self._imp_layer_combo.findData(uri) == -1:
                    self._imp_layer_combo.addItem(label, uri)
                    added += 1
            if added == 0:
                QMessageBox.information(
                    self, "Aucune couche",
                    "Le fichier ne contient aucune couche vecteur.")
        else:
            # Shapefile ou GeoJSON : une seule couche
            tmp = QgsVectorLayer(path, "_tmp", "ogr")
            if not tmp.isValid():
                QMessageBox.warning(self, "Fichier invalide",
                                    "Impossible de lire ce fichier.")
                return
            label = os.path.basename(path)
            if self._imp_layer_combo.findData(path) == -1:
                self._imp_layer_combo.addItem(label, path)

        # Activer la radio "Couche existante" et sélectionner la dernière entrée ajoutée
        self._imp_radio_existing.setChecked(True)
        self._imp_layer_combo.setEnabled(True)
        self._imp_layer_combo.setCurrentIndex(self._imp_layer_combo.count() - 1)
        self._imp_refresh_mapping()

    def _imp_get_mapping(self):
        """Return dict {src_col: dest_field} from the mapping table (ignoring '— ignorer —')."""
        mapping = {}
        for row in range(self._imp_mapping_table.rowCount()):
            src = self._imp_mapping_table.item(row, 0).text()
            combo = self._imp_mapping_table.cellWidget(row, 1)
            dest = combo.currentText() if combo else ""
            if dest and dest != "— ignorer —":
                mapping[src] = dest
        return mapping

    def _imp_run(self):
        path = self._imp_path.text()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Fichier manquant", "Sélectionnez un fichier CSV valide.")
            return
        if not self._imp_csv_headers:
            QMessageBox.warning(self, "CSV non chargé", "Cliquez sur Parcourir pour charger le CSV.")
            return

        ref_col = self._imp_ref_combo.currentText()

        try:
            delim = getattr(self, "_imp_delimiter", None) or self._detect_delimiter(path)
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh, delimiter=delim))
        except Exception as e:
            QMessageBox.warning(self, "Erreur lecture", str(e))
            return

        if not rows:
            QMessageBox.information(self, "CSV vide", "Le fichier CSV ne contient aucune donnée.")
            return

        csv_dir = os.path.dirname(os.path.abspath(path))
        if self._imp_radio_new.isChecked():
            self._imp_to_new_layer(rows, ref_col, csv_dir)
        else:
            self._imp_to_existing_layer(rows, ref_col, csv_dir)

    @staticmethod
    def _layer_dir(layer):
        """Dossier du fichier source d'une couche, ou '' si couche mémoire."""
        try:
            src = layer.dataProvider().dataSourceUri().split("|")[0]
        except (AttributeError, RuntimeError):
            return ""
        return os.path.dirname(src) if os.path.isfile(src) else ""

    @staticmethod
    def _absolutize_photos(rows, csv_dir, column="photos"):
        """Convertit les chemins relatifs de la colonne photos en chemins absolus.

        Repli pour les couches mémoire (sans dossier) : sans base de résolution
        sur disque, seuls des chemins absolus s'affichent dans la Fiche.
        Modifie les dicts `rows` en place.
        """
        for row in rows:
            raw = row.get(column)
            if not raw:
                continue
            resolved = []
            for p in str(raw).split(";"):
                p = p.strip()
                if not p:
                    continue
                if os.path.isabs(p):
                    resolved.append(p)
                else:
                    resolved.append(os.path.normpath(os.path.join(csv_dir, p)))
            row[column] = ";".join(resolved)

    @staticmethod
    def _relativize_photos(rows, csv_dir, layer_dir, ref_col, column="photos"):
        """Copie les images à côté de la couche et stocke des chemins relatifs.

        Pour chaque photo : la source est résolue (absolue, ou relative au
        dossier du CSV), puis copiée sous layer_dir/<référence>/<fichier> ;
        la colonne reçoit le chemin relatif « <référence>/<fichier> » (avec
        séparateurs « / », portables). Retourne le nombre d'images copiées.
        Modifie les dicts `rows` en place.
        """
        copied = 0
        for row in rows:
            raw = row.get(column)
            if not raw:
                continue
            ref = (row.get(ref_col) or "").strip() or "_photos"
            out = []
            for p in str(raw).split(";"):
                p = p.strip()
                if not p:
                    continue
                src = p if os.path.isabs(p) else os.path.normpath(os.path.join(csv_dir, p))
                # Chemin relatif cible : réutilise le relatif existant, sinon <ref>/<base>
                rel = p if not os.path.isabs(p) else os.path.join(ref, os.path.basename(p))
                rel = rel.replace("\\", "/")
                if os.path.isfile(src):
                    dst = os.path.join(layer_dir, *rel.split("/"))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.abspath(src) != os.path.abspath(dst):
                        shutil.copy2(src, dst)
                    copied += 1
                out.append(rel)
            row[column] = ";".join(out)
        return copied

    # Tolérance de distance pour la comparaison de coordonnées (en unités de la couche).
    # 2 mètres pour une couche projetée, ~0.00002° pour WGS84 (≈ 2 m).
    _COORD_TOLERANCE = 2.0

    @staticmethod
    def _norm_name(val):
        """Normalise un nom pour la comparaison : strip + minuscules."""
        return str(val).strip().lower() if val else ""

    @staticmethod
    def _coords_close(x1, y1, x2, y2, tol):
        """True si les deux points sont à moins de tol unités l'un de l'autre
        (distance planaire, dans les unités du CRS des coordonnées)."""
        if None in (x1, y1, x2, y2):
            return False
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 < tol

    def _metric_distance_fn(self, crs):
        """Retourne f(x1,y1,x2,y2) -> mètres pour des coordonnées dans `crs`.

        Utilise QgsDistanceArea (mesure ellipsoïdale) : correct quel que soit le
        CRS (Lambert-93, WGS84 en degrés, Web Mercator…). Repli sur None en cas
        d'indisponibilité → comparaison planaire dans les unités du CRS.
        """
        try:
            from qgis.core import QgsUnitTypes
            da = QgsDistanceArea()
            da.setSourceCrs(crs, QgsProject.instance().transformContext())
            da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")

            def dist(x1, y1, x2, y2):
                d = da.measureLine(QgsPointXY(x1, y1), QgsPointXY(x2, y2))
                return da.convertLengthMeasurement(d, QgsUnitTypes.DistanceMeters)
            return dist
        except Exception:
            return None

    def _is_duplicate(self, src_ref, src_name, src_x, src_y, existing, dist=None):
        """
        existing : liste de dicts {ref, name, x, y} déjà dans la couche.
        dist     : optionnel, f(x1,y1,x2,y2) -> mètres. Si fourni, la tolérance
                   (_COORD_TOLERANCE = 2 m) est interprétée en mètres ; sinon
                   comparaison planaire dans les unités du CRS (repli historique).

        IMPORTANT : src_(x,y) et existing[].(x,y) doivent être dans le MÊME CRS.

        Règle 1 — référence non vide :
          Cherche la même référence. Si trouvée, compare name+coords.
          Identiques → doublon. Différents → entrée distincte (import).

        Règle 2 — référence vide :
          Compare name (insensible à la casse) + coords (tolérance 2 m).
          Match complet → doublon.
        """
        tol = self._COORD_TOLERANCE
        norm_src = self._norm_name(src_name)

        def close(x1, y1, x2, y2):
            if None in (x1, y1, x2, y2):
                return False
            if dist is not None:
                return dist(x1, y1, x2, y2) < tol
            return self._coords_close(x1, y1, x2, y2, tol)

        if src_ref:
            for e in existing:
                if e["ref"] == src_ref:
                    same_name = self._norm_name(e["name"]) == norm_src
                    if e["x"] is not None and src_x is not None:
                        same_pos = close(src_x, src_y, e["x"], e["y"])
                    else:
                        same_pos = (e["x"] is None and src_x is None)
                    return same_name and same_pos
            return False

        # Pas de référence → comparaison name + position
        for e in existing:
            same_name = self._norm_name(e["name"]) == norm_src and norm_src != ""
            if e["x"] is not None and src_x is not None:
                same_pos = close(src_x, src_y, e["x"], e["y"])
            else:
                same_pos = (e["x"] is None and src_x is None)
            if same_name and same_pos:
                return True
        return False

    @staticmethod
    def _extract_xy(row):
        """Lit x/y depuis un dict CSV en testant plusieurs noms de colonnes."""
        def _f(val):
            try:
                return float(val) if val not in (None, "") else None
            except (ValueError, TypeError):
                return None
        x = _f(row.get("x") or row.get("X") or row.get("longitude") or row.get("Longitude"))
        y = _f(row.get("y") or row.get("Y") or row.get("latitude") or row.get("Latitude"))
        return x, y

    def _imp_to_new_layer(self, rows, ref_col, csv_dir=""):
        """Create a new memory layer from all CSV rows."""
        # Couche mémoire : pas de dossier sur disque → on ne peut pas stocker de
        # chemins relatifs. Repli : chemins absolus pour que les photos s'affichent.
        if csv_dir and any(r.get("photos") for r in rows):
            self._absolutize_photos(rows, csv_dir)
        src_crs_id = getattr(self, "_imp_crs_id", None) \
            or self.canvas.mapSettings().destinationCrs().authid()
        layer = QgsVectorLayer(f"Point?crs={src_crs_id}", "Inventaire Cavités", "memory")
        pr = layer.dataProvider()
        for col in self._imp_csv_headers:
            pr.addAttributes([QgsField(col, QVariant.String)])
        layer.updateFields()

        existing = []  # accumule les entrées déjà ajoutées dans cette session d'import
        added = skipped = 0
        # Distance métrique dans le CRS source (les coords comparées y sont).
        dist = self._metric_distance_fn(QgsCoordinateReferenceSystem(src_crs_id))

        for row in rows:
            ref  = row.get(ref_col, "").strip()
            name = row.get("name") or row.get("Name") or row.get("nom") or ""
            x, y = self._extract_xy(row)

            if self._is_duplicate(ref, name, x, y, existing, dist):
                skipped += 1
                continue

            feat = QgsFeature(layer.fields())
            if x is not None and y is not None:
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))

            # Génère une référence si absente
            if not ref:
                ref = _build_reference(added + 1, x or 0, y or 0)
                if ref_col in self._imp_csv_headers:
                    row = dict(row)
                    row[ref_col] = ref

            for col in self._imp_csv_headers:
                feat.setAttribute(col, row.get(col, ""))
            pr.addFeature(feat)
            existing.append({"ref": ref, "name": name, "x": x, "y": y})
            added += 1

        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        QMessageBox.information(
            self, "Import terminé",
            f"{added} entité(s) importée(s) dans une nouvelle couche.\n"
            + (f"{skipped} doublon(s) ignoré(s)." if skipped else "")
        )

    def _imp_to_existing_layer(self, rows, ref_col, csv_dir=""):
        """Import CSV rows into an existing layer (project or file) with mapping and dedup."""
        data = self._imp_layer_combo.currentData()
        if not data:
            QMessageBox.warning(self, "Couche manquante", "Sélectionnez une couche de destination.")
            return
        # Résoudre la couche : ID projet ou URI OGR (fichier)
        layer = QgsProject.instance().mapLayer(data)
        if not layer:
            layer = QgsVectorLayer(data, "_dest", "ogr")
            if not layer.isValid():
                QMessageBox.warning(self, "Couche introuvable",
                                    "La couche sélectionnée est introuvable.")
                return

        # Photos : si la couche est sur disque, on copie les images à côté et on
        # stocke des chemins relatifs (portables). Sinon (mémoire), repli absolu.
        photo_note = ""
        if csv_dir and any(r.get("photos") for r in rows):
            layer_dir = self._layer_dir(layer)
            if layer_dir:
                n = self._relativize_photos(rows, csv_dir, layer_dir, ref_col)
                photo_note = f"\n{n} photo(s) copiée(s) à côté de la couche."
            else:
                self._absolutize_photos(rows, csv_dir)
                photo_note = ("\n⚠ Couche en mémoire : photos non copiées "
                              "(chemins absolus utilisés).")

        dest_fields = [f.name() for f in layer.fields()]
        mapping = self._imp_get_mapping()
        if not mapping:
            QMessageBox.warning(self, "Mapping vide",
                                "Aucun champ source n'est mappé vers la destination.")
            return

        dest_ref_field = mapping.get(ref_col, "reference" if "reference" in dest_fields else "")
        dest_name_field = next((mapping.get(c) for c in ("name","Name","nom")
                                if mapping.get(c) in dest_fields), None) or \
                          next((f for f in ("name", "nom") if f in dest_fields), None)

        # Transformateur CRS source → CRS de la couche de destination
        src_crs_id = getattr(self, "_imp_crs_id", None) \
            or self.canvas.mapSettings().destinationCrs().authid()
        src_crs  = QgsCoordinateReferenceSystem(src_crs_id)
        dest_crs = layer.crs()
        transform = None
        if src_crs.isValid() and dest_crs.isValid() and src_crs != dest_crs:
            transform = QgsCoordinateTransform(
                src_crs, dest_crs, QgsProject.instance())

        # Charge les entrées existantes de la couche de destination
        existing = []
        for feat in layer.getFeatures():
            ref_v  = str(feat[dest_ref_field]).strip() if dest_ref_field and dest_ref_field in dest_fields else ""
            name_v = str(feat[dest_name_field]).strip() if dest_name_field and dest_name_field in dest_fields else ""
            geom = feat.geometry()
            if geom and not geom.isEmpty():
                pt = geom.asPoint()
                ex, ey = pt.x(), pt.y()
            else:
                ex, ey = None, None
            existing.append({"ref": ref_v, "name": name_v, "x": ex, "y": ey})

        pr = layer.dataProvider()
        added = skipped = 0
        # Distance métrique dans le CRS de destination : c'est là que sont
        # exprimées les coords de `existing` ET les coords source transformées.
        dist = self._metric_distance_fn(dest_crs)

        for row in rows:
            ref  = row.get(ref_col, "").strip()
            name = row.get("name") or row.get("Name") or row.get("nom") or ""
            x, y = self._extract_xy(row)

            # Transformer AVANT le dédoublonnage : `existing` est en CRS dest,
            # comparer des coords source brutes (autre CRS) serait faux.
            dx, dy = x, y
            if x is not None and y is not None and transform:
                tp = transform.transform(QgsPointXY(x, y))
                dx, dy = tp.x(), tp.y()

            if self._is_duplicate(ref, name, dx, dy, existing, dist):
                skipped += 1
                continue

            feat = QgsFeature(layer.fields())
            if dx is not None and dy is not None:
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(dx, dy)))

            # Génère une référence si absente et que le champ existe
            if not ref and dest_ref_field and dest_ref_field in dest_fields:
                ref = _build_reference(layer.featureCount() + added + 1, x or 0, y or 0)

            for src_col, dst_field in mapping.items():
                if dst_field in dest_fields:
                    feat.setAttribute(dst_field, row.get(src_col, ""))
            if dest_ref_field and dest_ref_field in dest_fields and not feat[dest_ref_field]:
                feat.setAttribute(dest_ref_field, ref)

            pr.addFeature(feat)
            # Coords en CRS dest (dx, dy) pour rester cohérent avec `existing`.
            existing.append({"ref": ref, "name": name, "x": dx, "y": dy})
            added += 1

        layer.updateExtents()
        layer.triggerRepaint()
        QMessageBox.information(
            self, "Import terminé",
            f"{added} entité(s) importée(s) dans « {layer.name()} ».\n"
            + (f"{skipped} doublon(s) ignoré(s)." if skipped else "")
            + photo_note
        )

    @staticmethod
    def _detect_crs(sample_rows):
        """Heuristique : si x ∈ [-180,180] et y ∈ [-90,90] → EPSG:4326, sinon None.

        Retourne un authid str ('EPSG:4326') ou None (= utiliser le CRS du projet).
        """
        for row in sample_rows[:5]:
            x_val = (row.get("x") or row.get("X")
                     or row.get("longitude") or row.get("Longitude"))
            y_val = (row.get("y") or row.get("Y")
                     or row.get("latitude") or row.get("Latitude"))
            if x_val and y_val:
                try:
                    xf, yf = float(x_val), float(y_val)
                    if -180.0 <= xf <= 180.0 and -90.0 <= yf <= 90.0:
                        return "EPSG:4326"
                    else:
                        return None  # clairement projeté
                except (ValueError, TypeError):
                    pass
        return None  # pas de coordonnées trouvées

    def _imp_set_crs(self, authid):
        """Enregistre l'authid CRS et met à jour le champ affiché."""
        self._imp_crs_id = authid
        if authid:
            crs = QgsCoordinateReferenceSystem(authid)
            self._imp_crs_edit.setText(f"{authid} — {crs.description()}")
        else:
            proj_crs = self.canvas.mapSettings().destinationCrs()
            self._imp_crs_id = proj_crs.authid()
            self._imp_crs_edit.setText(
                f"{proj_crs.authid()} — {proj_crs.description()} (CRS du projet)")

    def _imp_select_crs(self):
        """Ouvre le sélecteur de projection QGIS."""
        dlg = QgsProjectionSelectionDialog(self)
        if self._imp_crs_id:
            dlg.setCrs(QgsCoordinateReferenceSystem(self._imp_crs_id))
        if dlg.exec_():
            self._imp_set_crs(dlg.crs().authid())

    @staticmethod
    def _detect_delimiter(path):
        """Sniff the CSV delimiter; fall back to semicolon then comma."""
        with open(path, newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(4096)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return ";" if ";" in sample else ","

    @staticmethod
    def _try_float(val):
        try:
            return float(val) if val not in (None, "") else None
        except (ValueError, TypeError):
            return None

    # ---------------------------------------------------------------- Tools --

    # ---------------------------------------------------------------- Capture outil carte --

    def _toggle_capture(self, checked):
        """Active ou désactive l'outil de capture de point sur la carte."""
        if checked:
            self._prev_tool = self.canvas.mapTool()
            self._map_tool  = PointCaptureTool(self.canvas)
            self._map_tool.pointCaptured.connect(self._on_point_captured)
            self.canvas.setMapTool(self._map_tool)
            self._btn_capture.setText("🔴 En attente du clic…")
        else:
            self._reset_capture()

    def _on_point_captured(self, point):
        """Reçoit le point capturé, met à jour les champs X/Y et restaure l'outil précédent."""
        self._captured_point = point
        self._captured_crs = self.canvas.mapSettings().destinationCrs()
        self._lbl_x.setText(f"{point.x():.6f}")
        self._lbl_y.setText(f"{point.y():.6f}")
        self._reset_capture()
        self._autofill_admin(point)
        self.raise_()          # remet la fenêtre au premier plan après le clic carte
        self.activateWindow()

    def _autofill_admin(self, point):
        """Renseigne commune / code postal / département depuis les coordonnées.

        Optimisation réseau : les contours communaux déjà récupérés sont mis en
        cache ; on teste d'abord le point localement (point-dans-polygone) avant
        tout appel. Résultat : un seul appel par commune distincte, pas par
        cavité — et c'est exact (vrai PIP, pas un arrondi de coordonnées).

        L'appel réseau s'exécute dans un thread : l'interface ne gèle jamais,
        même hors-ligne. En cas d'échec un indicateur discret est affiché sous
        les champs (jamais de dialogue bloquant), la saisie reste possible.
        """
        try:
            proj_crs = self.canvas.mapSettings().destinationCrs()
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            pt = point
            if proj_crs != wgs84:
                tr = QgsCoordinateTransform(proj_crs, wgs84, QgsProject.instance())
                pt = tr.transform(point)
            lon, lat = pt.x(), pt.y()

            self._ensure_commune_cache_loaded()

            # 1. Cache local (point-dans-polygone) : réponse immédiate, sans réseau.
            for polygons, cached_info in self._commune_cache:
                if self._point_in_polygons(lon, lat, polygons):
                    self._apply_admin_info(cached_info)
                    self._set_admin_status("")
                    return

            # 2. Sinon, appel réseau DANS UN THREAD : la saisie ne gèle jamais,
            #    même hors-ligne avec un long timeout. Le résultat revient sur le
            #    thread UI via le signal _admin_fetched (connexion queued).
            self._admin_gen += 1
            gen = self._admin_gen
            self._set_admin_status("Recherche de la commune…")

            def _worker():
                result = self._reverse_geocode(lat, lon)
                try:
                    self._admin_fetched.emit(result, gen)
                except RuntimeError:
                    pass  # dialogue fermé pendant la requête

            threading.Thread(target=_worker, daemon=True).start()
        except Exception:
            return

    def _on_admin_fetched(self, result, gen):
        """Reçoit (thread UI) le résultat du géocodage asynchrone."""
        if gen != self._admin_gen:
            return  # réponse obsolète : un nouveau point a été capturé depuis
        if not result:
            # Échec signalé mais jamais bloquant : champs laissés en l'état.
            self._set_admin_status(
                "⚠ Commune non renseignée (hors ligne ou point hors France). "
                "Saisie possible à la main.")
            return
        polygons = self._parse_geojson_polygons(result.pop("_contour", None))
        if polygons:
            self._commune_cache.append((polygons, result))
            self._save_commune_cache()
        self._apply_admin_info(result)
        self._set_admin_status("")

    # ---- Cache communal persistant (un fichier JSON dans le dossier projet) --

    def _commune_cache_dir(self):
        """Dossier du projet QGIS courant, ou '' si le projet n'est pas enregistré."""
        try:
            return QgsProject.instance().absolutePath() or ""
        except Exception:
            return ""

    def _ensure_commune_cache_loaded(self):
        """Charge une seule fois le cache communal depuis le disque (best effort)."""
        if getattr(self, "_commune_cache_loaded", False):
            return
        self._commune_cache_loaded = True
        try:
            loaded = geocode_utils.load_commune_cache(self._commune_cache_dir())
            known = {info.get("code_insee") for _, info in self._commune_cache}
            for polygons, info in loaded:
                if info.get("code_insee") not in known:
                    self._commune_cache.append((polygons, info))
        except Exception:
            pass

    def _save_commune_cache(self):
        """Écrit le cache communal sur disque (best effort, jamais bloquant)."""
        try:
            geocode_utils.save_commune_cache(
                self._commune_cache_dir(), self._commune_cache)
        except Exception:
            pass

    def _apply_admin_info(self, info):
        """Remplit les champs commune / CP / département depuis un dict info."""
        self._f_commune.setText(info.get("commune", ""))
        self._f_code_postal.setText(info.get("code_postal", ""))
        self._f_departement.setText(info.get("departement", ""))
        self._f_code_insee.setText(info.get("code_insee", ""))
        self._f_code_dept.setText(info.get("code_dept", ""))

    def _set_admin_status(self, text):
        """Met à jour l'indicateur d'état du géocodage (silencieux si absent)."""
        try:
            self._admin_status.setText(text)
        except (AttributeError, RuntimeError):
            pass

    # Délégations vers geocode_utils (fonctions pures, testables, thread-safe).
    # Conservées comme méthodes pour la compatibilité (tests, surcharge).
    _reverse_geocode = staticmethod(geocode_utils.reverse_geocode)
    _parse_geojson_polygons = staticmethod(geocode_utils.parse_geojson_polygons)
    _point_in_ring = staticmethod(geocode_utils.point_in_ring)
    _point_in_polygons = staticmethod(geocode_utils.point_in_polygons)

    def _reset_capture(self):
        """Restaure l'outil carte précédent et remet le bouton capture à l'état initial."""
        if self._map_tool:
            self.canvas.unsetMapTool(self._map_tool)
            if self._prev_tool:
                self.canvas.setMapTool(self._prev_tool)
            self._map_tool = None
        self._btn_capture.setChecked(False)
        self._btn_capture.setText("📍 Capturer un point")

    # ---------------------------------------------------------------- Utilitaires --

    def _get_xy(self):
        """Lit les champs X et Y et les convertit en float.

        Retourne (None, None) si les champs sont vides ou non numériques.
        Ne lève jamais d'exception.
        """
        try:
            x = float(self._lbl_x.text())
            y = float(self._lbl_y.text())
            return x, y
        except ValueError:
            return None, None

    def _crs_text(self, crs):
        """Formate le texte d'affichage du CRS courant."""
        return f"Projection : {crs.authid()} — {crs.description()}"

    def _on_crs_changed(self):
        """Slot appelé quand la projection du canevas QGIS change."""
        self._lbl_crs.setText(self._crs_text(self.canvas.mapSettings().destinationCrs()))

    def closeEvent(self, event):
        count = len(self._queue)
        if count > 0:
            reply = QMessageBox.question(
                self, "Fermer sans sauvegarder ?",
                f"Il y a {count} point(s) en file d'attente non exportés.\n"
                "Fermer quand même ? Les données seront perdues.",
                _MsgYes | _MsgNo,
                _MsgNo
            )
            if reply == _MsgNo:
                event.ignore()
                return
        try:
            self.canvas.destinationCrsChanged.disconnect(self._on_crs_changed)
        except (TypeError, RuntimeError):
            pass
        self._reset_capture()
        super().closeEvent(event)
