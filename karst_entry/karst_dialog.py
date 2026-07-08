# Copyright (c) 2026 Julien Tournois
# Licence : PolyForm Noncommercial License 1.0.0
# Usage commercial interdit sans autorisation écrite — julien.tournois@gmail.com

"""
karst_dialog.py
===============
Dialogue principal du plugin KarstEntry.

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
import zipfile

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QDateEdit, QTabWidget, QWidget, QMessageBox,
    QFileDialog, QListWidgetItem, QTableWidgetItem, QProgressDialog
)
from qgis.PyQt.QtCore import Qt, QDate, QVariant, pyqtSignal
from qgis.PyQt.QtGui import QPixmap, QIcon

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsWkbTypes, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsDistanceArea,
)
try:
    from qgis.core import QgsExifTools  # géotag des photos (QGIS ≥ 3.6)
except ImportError:  # pragma: no cover
    QgsExifTools = None
from qgis.gui import QgsProjectionSelectionDialog

from .map_tool import PointCaptureTool
from . import geocode_utils
from . import feature_utils
from . import csv_io
from .ui_tabs import TabBuildersMixin
from .layers import LayersMixin

# Compat enums Qt (PyQt5/6) + constantes de thème : déplacés dans ui_constants
# pour être partagés avec ui_tabs sans import circulaire. Le contrat de schéma
# (schema.py) n'est plus référencé ici directement : seul LayersMixin (layers.py)
# en a besoin depuis le split _cavites_field_defs/_tracages_field_defs.
from .ui_constants import (
    _qenum,
    _WStaysOnTop, _AlignTop, _AlignLeft, _AlignCenter,
    _KeepRatio, _Smooth, _TextSelect, _ISODate, _UserRole, _WindowModal,
    _MsgYes, _MsgNo, PHOTO_THUMB_SIZE,
)

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


class KarstDialog(TabBuildersMixin, LayersMixin, QDialog):
    """Fenêtre principale du plugin KarstEntry.

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

    # Géocodage par lot (onglet Stats) : (results_dict, filled, failed, layer_id).
    _geofill_done = pyqtSignal(object, int, int, str)
    _geofill_progress = pyqtSignal(int, int)

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
        self._geofill_busy = False
        self._geofill_done.connect(self._on_geofill_done)
        self._geofill_progress.connect(self._on_geofill_progress)

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
        self._tabs.addTab(self._build_stats_tab(), "📊 Stats")
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


    # -------- Tab: modification --------


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
    # Délégations vers feature_utils (fonctions pures, testables sans QGIS).
    _feature_label = staticmethod(feature_utils.feature_label)
    _fold = staticmethod(feature_utils.fold)
    _feature_matches = staticmethod(feature_utils.feature_matches)

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
        # Si aucune position n'est encore saisie, proposer de la déduire d'une
        # photo géolocalisée (EXIF GPS).
        if paths and self._captured_point is None:
            self._offer_geotag_from_photos(paths)

    @staticmethod
    def _photo_geotag(path):
        """Coordonnées (lon, lat) WGS84 du géotag EXIF d'une photo, ou None."""
        if QgsExifTools is None:
            return None
        try:
            if not QgsExifTools.hasGeoTag(path):
                return None
            pt = QgsExifTools.getGeoTag(path)
            # getGeoTag renvoie un QgsPoint(X,Y[,Z]) en WGS84.
            return (pt.x(), pt.y())
        except Exception:
            return None

    def _offer_geotag_from_photos(self, paths):
        """Propose de placer le point depuis la 1re photo géolocalisée trouvée."""
        for path in paths:
            lonlat = self._photo_geotag(path)
            if not lonlat:
                continue
            lon, lat = lonlat
            reply = QMessageBox.question(
                self, "Photo géolocalisée",
                f"« {os.path.basename(path)} » contient une position GPS.\n"
                "Placer le point de la cavité à cet emplacement ?",
                _MsgYes | _MsgNo, _MsgYes)
            if reply != _MsgYes:
                return
            try:
                proj_crs = self.canvas.mapSettings().destinationCrs()
                wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                pt = QgsPointXY(lon, lat)
                if proj_crs != wgs84:
                    tr = QgsCoordinateTransform(wgs84, proj_crs,
                                                QgsProject.instance())
                    pt = tr.transform(pt)
                self._apply_captured_point(pt)
            except Exception:
                QMessageBox.warning(self, "Erreur",
                                    "Impossible de reprojeter la position de la photo.")
            return

    def _remove_selected_photo(self):
        for item in self._photo_list.selectedItems():
            path = item.data(_UserRole)
            if path in self._photo_paths:
                self._photo_paths.remove(path)
            self._photo_list.takeItem(self._photo_list.row(item))

    _safe_dirname = staticmethod(feature_utils.safe_dirname)

    @staticmethod
    def _store_photos_beside_layer(photo_paths, layer_dir, reference, subdir=""):
        """Copie les photos sous layer_dir/[<subdir>/]<reference>/ et renvoie les
        chemins relatifs (« [<subdir>/]<référence>/<fichier> », séparés par « ; »).

        - subdir : dossier intermédiaire (nom de la couche) pour ne pas mélanger
          les photos de plusieurs couches d'un même dossier.
        - layer_dir vide (couche mémoire) → renvoie les chemins absolus tels
          quels (repli non portable, mais affichable).
        - fichier source absent → le token est conservé sans copie.
        """
        if not photo_paths:
            return ""
        if not layer_dir:
            return ";".join(p for p in photo_paths if p)
        rel_base = f"{subdir}/{reference}" if subdir else reference
        out = []
        for src in photo_paths:
            if not src:
                continue
            if os.path.isfile(src):
                base = os.path.basename(src)
                dst = os.path.join(layer_dir, *rel_base.split("/"), base)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copy2(src, dst)
                out.append(f"{rel_base}/{base}")
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
            "altitude":  self._try_float(self._f_altitude.text()),
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


    # ---- Sélection de la couche de destination (Saisie) ---------------------

    def _populate_new_targets(self):
        """Peuple le sélecteur de couche active : « Nouvelle couche » + couches
        de points (cavités) ET de lignes (traçages), pour pouvoir exporter l'une
        ou l'autre. La saisie de cavités valide le schéma et refuse une ligne."""
        combo = self._new_target_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("➕ Nouvelle couche", "__new__")
        default_index = 0
        for layer in QgsProject.instance().mapLayers().values():
            if not (hasattr(layer, "getFeatures") and hasattr(layer, "fields")):
                continue
            try:
                if not layer.isValid() or layer.geometryType() not in (
                        QgsWkbTypes.PointGeometry, QgsWkbTypes.LineGeometry):
                    continue
                name, lid = layer.name(), layer.id()
            except (AttributeError, RuntimeError):
                continue
            combo.addItem(name, lid)
            if name == self._CAVITES_LAYER_NAME:
                default_index = combo.count() - 1
        combo.setCurrentIndex(default_index)
        combo.blockSignals(False)
        self._new_target_changed()

    def _new_target_changed(self):
        """Le champ « nom » n'est actif que pour la création d'une nouvelle couche."""
        is_new = self._new_target_combo.currentData() == "__new__"
        self._new_name_edit.setEnabled(is_new)



    # ---- Symbologie automatique (catégorisée par type / résultat) -----------

    @staticmethod
    def _save_style_to_gpkg(layer):
        """Enregistre le style courant comme style par défaut dans le GPKG.

        Permet à QField et aux réouvertures de retrouver la symbologie.
        Best effort : silencieux si la couche n'est pas sur disque.
        """
        try:
            layer.saveStyleToDatabase(layer.name(), "Style KarstEntry", True, "")
        except Exception:
            pass

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

        # Couche cible selon le sélecteur « Couche de destination » :
        # couche existante (validée, migration proposée si champs manquants)
        # ou nouvelle couche au nom choisi.
        layer = self._target_cavites_layer()
        if layer is None:
            return  # annulé / couche refusée
        self._new_layer_id = layer.id()
        pr = layer.dataProvider()

        # Filet supplémentaire : couche réutilisée au schéma incomplet
        # (sécurité ; pour une couche choisie c'est déjà géré en amont).
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
                "altitude":  entry.get("altitude") if entry.get("altitude") is not None
                             else QVariant(),
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
            # Copie des photos sous <couche>/<référence>/ → chemins relatifs
            # portables (repli absolu si couche mémoire).
            photos_idx = layer.fields().indexOf("photos")
            photos_val = self._store_photos_beside_layer(
                entry["photos"], layer_dir, ref, self._safe_dirname(layer.name()))

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
        self._f_altitude.clear()
        self._photo_paths.clear()
        self._photo_list.clear()

    def _make_progress(self, label, total):
        """Crée une barre de progression modale (s'affiche si l'opération dure).

        `setValue` traite les événements Qt → l'interface reste réactive et le
        bouton Annuler fonctionne. Ne s'affiche qu'au-delà de ~0,4 s.
        """
        dlg = QProgressDialog(label, "Annuler", 0, max(int(total), 1), self)
        dlg.setWindowTitle("KarstEntry")
        # Modal : Qt n'appelle QApplication.processEvents() depuis setValue() que
        # si le dialog est modal. Sans ça → fenêtre blanche figée pendant l'export.
        dlg.setWindowModality(_WindowModal)
        # Sinon Qt ferme/réinitialise le dialog dès que la valeur atteint le max,
        # alors qu'il reste du travail (dernière entité, fermeture fichier, zip).
        # On le ferme nous-mêmes dans le finally de chaque export/import.
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumDuration(0)  # visible dès le départ
        dlg.setValue(0)
        return dlg

    @staticmethod
    def _progress_cancelled(progress):
        """True seulement si l'utilisateur a cliqué Annuler (robuste aux mocks)."""
        return progress is not None and progress.wasCanceled() is True

    def _write_export(self, layer, csv_path, progress=None):
        """Écrit la couche en CSV dans csv_path et copie les photos dans des
        sous-dossiers <référence>/ à côté. Résout les chemins relatifs depuis le
        dossier de la couche."""
        layer_dir = self._layer_dir(layer)
        out_dir = os.path.dirname(csv_path)
        sub = self._safe_dirname(layer.name())  # dossier au nom de la couche
        # On exclut « fid » (clé primaire interne du GeoPackage) : il n'a pas de
        # sens hors de la couche et fait échouer la réimport (champ réservé OGR).
        fields = [f.name() for f in layer.fields() if f.name().strip().lower() != "fid"]
        # Couches non ponctuelles (traçages = lignes) : la géométrie n'est pas
        # reconstructible depuis des attributs → on ajoute une colonne WKT pour
        # un roundtrip sans perte (réimportée par _imp_tracages_to_new_layer).
        is_point = layer.geometryType() == QgsWkbTypes.PointGeometry
        out_fields = fields if is_point else fields + ["wkt"]
        # utf-8-sig : BOM pour qu'Excel (Windows) détecte l'UTF-8 et n'affiche
        # plus de mojibake type "NÂ°" (sinon il lit le CSV en cp1252).
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=out_fields)
            writer.writeheader()
            for i, feat in enumerate(layer.getFeatures(), 1):
                if progress is not None:
                    progress.setValue(i)
                    if self._progress_cancelled(progress):
                        break
                row = {f: feat[f] for f in fields}
                if not is_point:
                    geom = feat.geometry()
                    row["wkt"] = geom.asWkt() if geom and not geom.isEmpty() else ""
                if row.get("photos"):
                    ref = str(row.get("reference") or "no_ref")
                    copied = []
                    for raw in str(row["photos"]).split(";"):
                        raw = raw.strip()
                        if not raw:
                            continue
                        src = raw if os.path.isabs(raw) else \
                            os.path.join(layer_dir or "", *raw.split("/"))
                        if os.path.isfile(src):
                            dest = os.path.join(out_dir, sub, ref)
                            dest_file = os.path.join(dest, os.path.basename(src))
                            # Exporter dans le dossier de la couche : src et dest
                            # peuvent être le MÊME fichier → ne pas se copier sur
                            # soi (WinError 32). Toute erreur de copie (fichier
                            # verrouillé…) ne doit pas interrompre l'export.
                            try:
                                if os.path.abspath(src) != os.path.abspath(dest_file):
                                    os.makedirs(dest, exist_ok=True)
                                    shutil.copy2(src, dest_file)
                            except (OSError, shutil.SameFileError):
                                pass
                            copied.append(f"{sub}/{ref}/{os.path.basename(src)}")
                        else:
                            copied.append(raw)  # introuvable : on garde le jeton
                    row["photos"] = ";".join(copied)
                writer.writerow(row)

    def _export_csv(self):
        layer = self._selected_export_layer()
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
        progress = self._make_progress("Export CSV en cours…", layer.featureCount())
        try:
            self._write_export(layer, path, progress)
            # Capturer l'état AVANT close() : close() positionne wasCanceled()=True.
            cancelled = self._progress_cancelled(progress)
        finally:
            progress.close()
        if cancelled:
            QMessageBox.information(self, "Export annulé", "Export interrompu.")
            return
        QMessageBox.information(self, "Export réussi",
                                f"CSV sauvegardé :\n{path}\n\n"
                                f"Photos copiées dans les sous-dossiers <référence>/.")

    def _export_zip(self):
        """Exporte CSV + photos dans une archive ZIP unique (portable)."""
        layer = self._selected_export_layer()
        if layer is None or layer.featureCount() == 0:
            QMessageBox.information(self, "Vide", "Aucune donnée à exporter.")
            return
        self._new_layer_id = layer.id()
        zip_path, _ = QFileDialog.getSaveFileName(
            self, "Exporter en ZIP", "", "Archive ZIP (*.zip)")
        if not zip_path:
            return
        if not zip_path.lower().endswith(".zip"):
            zip_path += ".zip"
        import tempfile
        progress = self._make_progress("Export ZIP en cours…", layer.featureCount())
        try:
            with tempfile.TemporaryDirectory() as tmp:
                csv_path = os.path.join(tmp, f"{layer.name()}.csv")
                self._write_export(layer, csv_path, progress)
                if self._progress_cancelled(progress):
                    progress.close()
                    QMessageBox.information(self, "Export annulé", "Export interrompu.")
                    return
                # Phase compression : réutilise la barre fichier par fichier
                # (setValue pompe les événements → pas de gel pendant le zip).
                all_files = [os.path.join(r, fn)
                             for r, _d, fns in os.walk(tmp) for fn in fns]
                progress.setLabelText("Compression de l'archive…")
                progress.setRange(0, max(len(all_files), 1))
                progress.setValue(0)
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for n, full in enumerate(all_files, 1):
                        zf.write(full, os.path.relpath(full, tmp))
                        progress.setValue(n)
                        if self._progress_cancelled(progress):
                            break
        except OSError as exc:
            QMessageBox.warning(self, "Erreur", f"Écriture impossible :\n{exc}")
            return
        finally:
            progress.close()
        QMessageBox.information(self, "Export réussi",
                                f"Archive ZIP (CSV + photos) sauvegardée :\n{zip_path}")

    _gpx_document = staticmethod(csv_io.gpx_document)

    def _export_gpx(self):
        """Exporte les cavités en waypoints GPX (WGS84)."""
        layer = self._selected_export_layer()
        if layer is None or layer.featureCount() == 0:
            QMessageBox.information(self, "Vide",
                                    "Aucune donnée à exporter.\n"
                                    "Utilisez d'abord « Ajouter dans QGIS ».")
            return
        self._new_layer_id = layer.id()

        path, _ = QFileDialog.getSaveFileName(self, "Exporter en GPX", "", "GPX (*.gpx)")
        if not path:
            return
        if not path.lower().endswith(".gpx"):
            path += ".gpx"

        fields = [f.name() for f in layer.fields()]
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        tr = None
        if layer.crs() != wgs84:
            tr = QgsCoordinateTransform(layer.crs(), wgs84, QgsProject.instance())

        progress = self._make_progress("Export GPX en cours…", layer.featureCount())
        waypoints = []
        cancelled = False
        for i, feat in enumerate(layer.getFeatures(), 1):
            progress.setValue(i)
            if self._progress_cancelled(progress):
                cancelled = True
                break
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue
            pt = geom.asPoint()
            if tr is not None:
                pt = tr.transform(pt)
            name = ""
            if "reference" in fields and feat["reference"]:
                name = str(feat["reference"])
            if "name" in fields and feat["name"]:
                name = f"{name} — {feat['name']}" if name else str(feat["name"])
            desc_parts = [str(feat[f]) for f in ("type", "commune")
                          if f in fields and feat[f]]
            ele = feat["altitude"] if "altitude" in fields and feat["altitude"] else None
            waypoints.append({"lat": pt.y(), "lon": pt.x(), "name": name,
                              "desc": " — ".join(desc_parts), "ele": ele})
        progress.close()
        if cancelled:
            QMessageBox.information(self, "Export annulé", "Export interrompu.")
            return

        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._gpx_document(waypoints))
        except OSError as exc:
            QMessageBox.warning(self, "Erreur", f"Écriture impossible :\n{exc}")
            return
        QMessageBox.information(self, "Export réussi",
                                f"{len(waypoints)} waypoint(s) GPX sauvegardé(s) :\n{path}")

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

    _is_blank = staticmethod(feature_utils.is_blank)
    _clean_html = staticmethod(feature_utils.clean_html)

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


    def _populate_tr_targets(self):
        """Peuple le sélecteur de couche cible des traçages (lignes)."""
        combo = self._tr_target_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("➕ Nouvelle couche", "__new__")
        default_index = 0
        for layer in QgsProject.instance().mapLayers().values():
            if not (hasattr(layer, "getFeatures") and hasattr(layer, "fields")):
                continue
            try:
                if not layer.isValid() or layer.geometryType() != QgsWkbTypes.LineGeometry:
                    continue
                name, lid = layer.name(), layer.id()
            except (AttributeError, RuntimeError):
                continue
            combo.addItem(name, lid)
            if name == self._TRACAGES_LAYER_NAME:
                default_index = combo.count() - 1
        combo.setCurrentIndex(default_index)
        combo.blockSignals(False)
        self._tr_target_changed()

    def _tr_target_changed(self):
        is_new = self._tr_target_combo.currentData() == "__new__"
        self._tr_name_edit.setEnabled(is_new)


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

    def _tr_flush_queue(self):
        """Écrit tous les traçages de la file dans une couche persistante sur disque."""
        if not self._tracage_queue:
            return

        layer = self._target_tracages_layer()
        if layer is None:
            return  # annulé / couche refusée
        self._tracage_layer_id = layer.id()
        pr    = layer.dataProvider()
        added = 0

        # pt_src/pt_dst sont dans le CRS du PROJET (cf. _tr_get_point) : il faut
        # les reprojeter vers le CRS de la couche cible si celui-ci diffère,
        # sinon la géométrie est écrite avec les mauvaises unités (mélange
        # degrés/mètres) et la couche s'affiche vide ou hors champ.
        proj_crs = self.canvas.mapSettings().destinationCrs()
        layer_crs = layer.crs()
        transform = None
        if layer_crs.isValid() and proj_crs != layer_crs:
            transform = QgsCoordinateTransform(proj_crs, layer_crs, QgsProject.instance())

        for entry in self._tracage_queue:
            feat = QgsFeature(layer.fields())
            pt_src, pt_dst = entry["pt_src"], entry["pt_dst"]
            if transform is not None:
                try:
                    pt_src = transform.transform(pt_src)
                    pt_dst = transform.transform(pt_dst)
                except Exception:
                    pass
            # Ligne entre les deux points
            feat.setGeometry(QgsGeometry.fromPolylineXY([pt_src, pt_dst]))
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

    # ------------------------------------------------------------------ Stats tab --


    _ADMIN_FIELDS = ("commune", "code_insee", "code_postal",
                     "departement", "code_dept")

    def _stats_fill_communes(self):
        """Géocode par lot les entités de la couche dont la commune est vide."""
        if getattr(self, "_geofill_busy", False):
            return
        layer = self._stats_current_layer()
        if layer is None:
            return
        fields = layer.fields().names()
        if "commune" not in fields:
            QMessageBox.warning(self, "Champ absent",
                                "Cette couche n'a pas de champ « commune ».")
            return
        # Colonnes administratives manquantes : proposer de les ajouter.
        missing = [QgsField(n, QVariant.String)
                   for n in self._ADMIN_FIELDS if n not in fields]
        if missing:
            names = ", ".join(f.name() for f in missing)
            if QMessageBox.question(
                    self, "Colonnes manquantes",
                    f"Pour stocker la localisation, ajouter : {names} ?",
                    _MsgYes | _MsgNo, _MsgYes) != _MsgYes:
                return
            try:
                layer.dataProvider().addAttributes(missing)
                layer.updateFields()
            except Exception:
                QMessageBox.warning(self, "Échec", "Ajout des colonnes impossible.")
                return

        # Collecte des entités sans commune, reprojetées en WGS84.
        try:
            proj_crs = layer.crs()
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            tr = None
            if proj_crs != wgs84:
                tr = QgsCoordinateTransform(proj_crs, wgs84, QgsProject.instance())
            todo = []
            for feat in layer.getFeatures():
                if str(feat["commune"] or "").strip():
                    continue
                geom = feat.geometry()
                if not geom or geom.isEmpty():
                    continue
                pt = geom.asPoint()
                if tr is not None:
                    pt = tr.transform(pt)
                todo.append((feat.id(), pt.x(), pt.y()))
        except Exception:
            QMessageBox.warning(self, "Erreur", "Lecture de la couche impossible.")
            return

        if not todo:
            QMessageBox.information(self, "Rien à faire",
                                   "Toutes les entités ont déjà une commune.")
            return

        self._geofill_busy = True
        self._stats_fill_btn.setEnabled(False)
        layer_id = layer.id()

        def _worker():
            results = {}
            filled = failed = 0
            for i, (fid, lon, lat) in enumerate(todo, 1):
                info = None
                for polygons, cached in self._commune_cache:
                    if self._point_in_polygons(lon, lat, polygons):
                        info = cached
                        break
                if info is None:
                    res = self._reverse_geocode(lat, lon)
                    if res:
                        polys = self._parse_geojson_polygons(res.pop("_contour", None))
                        info = res
                        if polys:
                            self._commune_cache.append((polys, info))
                if info:
                    results[fid] = info
                    filled += 1
                else:
                    failed += 1
                self._geofill_progress.emit(i, len(todo))
            try:
                self._geofill_done.emit(results, filled, failed, layer_id)
            except RuntimeError:
                pass  # dialogue fermé

        threading.Thread(target=_worker, daemon=True).start()

    def _on_geofill_progress(self, done, total):
        self._stats_summary.setText(f"Géocodage… {done}/{total}")

    @staticmethod
    def _apply_geofill_results(layer, results, admin_fields):
        """Écrit les infos admin par fid dans la couche. Retourne le nb appliqué."""
        idx = {n: layer.fields().indexOf(n) for n in admin_fields}
        layer.startEditing()
        applied = 0
        for fid, info in results.items():
            for name, col in idx.items():
                if col != -1:
                    layer.changeAttributeValue(fid, col, info.get(name, ""))
            applied += 1
        layer.commitChanges()
        return applied

    def _on_geofill_done(self, results, filled, failed, layer_id):
        self._geofill_busy = False
        self._stats_fill_btn.setEnabled(True)
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is not None and results:
            try:
                self._apply_geofill_results(layer, results, self._ADMIN_FIELDS)
                layer.triggerRepaint()
            except Exception:
                QMessageBox.warning(self, "Erreur", "Écriture des communes impossible.")
        self._stats_refresh()
        QMessageBox.information(
            self, "Géocodage terminé",
            f"{filled} commune(s) renseignée(s)."
            + (f"\n{failed} non trouvée(s) (hors France / hors ligne)." if failed else ""))

    def _stats_populate_layers(self):
        self._stats_layer_combo.blockSignals(True)
        self._stats_layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if not (hasattr(layer, "getFeatures") and hasattr(layer, "fields")):
                continue
            try:
                if layer.geometryType() != QgsWkbTypes.PointGeometry:
                    continue
                self._stats_layer_combo.addItem(layer.name(), layer.id())
            except (AttributeError, RuntimeError):
                continue
        self._stats_layer_combo.blockSignals(False)
        self._stats_refresh()

    def _stats_current_layer(self):
        lid = self._stats_layer_combo.currentData()
        return QgsProject.instance().mapLayer(lid) if lid else None

    @staticmethod
    def _compute_commune_stats(entries):
        """Agrège des entrées en stats par commune.

        entries : itérable de dicts {commune, type, developpement (float|None)}.
        Retourne une liste triée (nb décroissant, puis commune) de dicts :
        {commune, count, developpement_total, by_type:{type:count}}.
        Fonction pure → testable sans QGIS.
        """
        acc = {}
        for e in entries:
            com = (e.get("commune") or "").strip() or "(non renseignée)"
            a = acc.setdefault(
                com, {"commune": com, "count": 0,
                      "developpement_total": 0.0, "by_type": {}})
            a["count"] += 1
            t = (e.get("type") or "").strip() or "(non renseigné)"
            a["by_type"][t] = a["by_type"].get(t, 0) + 1
            dev = e.get("developpement")
            try:
                if dev not in (None, ""):
                    a["developpement_total"] += float(dev)
            except (ValueError, TypeError):
                pass
        return sorted(acc.values(), key=lambda r: (-r["count"], r["commune"]))

    def _stats_refresh(self):
        self._stats_table.clear()
        self._stats_table.setRowCount(0)
        self._stats_summary.setText("")
        layer = self._stats_current_layer()
        if layer is None:
            return
        fields = [f.name() for f in layer.fields()]
        if "commune" not in fields:
            self._stats_summary.setText(
                "Cette couche n'a pas de champ « commune ».")
            return
        has_dev = "developpement_estime" in fields
        has_type = "type" in fields
        entries = []
        for feat in layer.getFeatures():
            entries.append({
                "commune": feat["commune"] if "commune" in fields else "",
                "type": feat["type"] if has_type else "",
                "developpement": feat["developpement_estime"] if has_dev else None,
            })
        stats = self._compute_commune_stats(entries)
        self._stats_rows = stats  # mémorisé pour l'export CSV

        self._stats_table.setColumnCount(4)
        self._stats_table.setHorizontalHeaderLabels(
            ["Commune", "Nombre", "Dévelop. cumulé (m)", "Détail par type"])
        self._stats_table.setRowCount(len(stats))
        for row, s in enumerate(stats):
            by_type = ", ".join(f"{t}×{n}" for t, n in
                                sorted(s["by_type"].items(), key=lambda kv: -kv[1]))
            dev = f"{s['developpement_total']:.0f}" if has_dev else "—"
            for col, val in enumerate((s["commune"], str(s["count"]), dev, by_type)):
                self._stats_table.setItem(row, col, QTableWidgetItem(val))

        total = sum(s["count"] for s in stats)
        self._stats_summary.setText(
            f"{total} entité(s) · {len(stats)} commune(s)")

    def _stats_export_csv(self):
        rows = getattr(self, "_stats_rows", None)
        if not rows:
            QMessageBox.information(self, "Vide", "Aucune statistique à exporter.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter le récapitulatif", "", "CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["commune", "nombre", "developpement_cumule_m",
                                 "detail_types"])
                for s in rows:
                    by_type = "; ".join(f"{t}x{n}" for t, n in
                                        sorted(s["by_type"].items(), key=lambda kv: -kv[1]))
                    writer.writerow([s["commune"], s["count"],
                                     f"{s['developpement_total']:.0f}", by_type])
        except OSError as exc:
            QMessageBox.warning(self, "Erreur", f"Écriture impossible :\n{exc}")
            return
        QMessageBox.information(self, "Export réussi", f"Récapitulatif sauvegardé :\n{path}")

    # ---------------------------------------------------------------- Import CSV tab --


    def _imp_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir un CSV", "", "CSV (*.csv *.txt)"
        )
        if not path:
            return
        self._imp_path.setText(path)
        try:
            enc = self._detect_encoding(path)
            delim = self._detect_delimiter(path)
            with open(path, newline="", encoding=enc) as fh:
                reader = csv.DictReader(fh, delimiter=delim)
                sample_rows = [row for _, row in zip(range(5), reader)]
                headers = list(sample_rows[0].keys()) if sample_rows else []
            self._imp_csv_headers = [h.strip() for h in headers]
            self._imp_delimiter = delim
            self._imp_encoding = enc

            # Détection automatique du CRS
            detected = self._detect_crs(sample_rows)
            self._imp_set_crs(detected)
            crs_note = "EPSG:4326 détecté automatiquement" if detected else "CRS du projet utilisé"

            self._imp_ref_combo.clear()
            self._imp_ref_combo.addItems(self._imp_csv_headers)
            # Devine la colonne « référence » (reference, numero, id, code…).
            ref_guess = next(
                (h for h in self._imp_csv_headers
                 if feature_utils.guess_dest_field(h, ["reference"]) == "reference"),
                None)
            if ref_guess:
                self._imp_ref_combo.setCurrentText(ref_guess)
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
            # Auto-match universel : casse/accents/espaces ignorés + synonymes
            # (numero→reference, nom→name, lon→x, alt→altitude…).
            guess = feature_utils.guess_dest_field(src_col, dest_fields)
            if guess:
                combo.setCurrentText(guess)
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
            enc = getattr(self, "_imp_encoding", None) or self._detect_encoding(path)
            with open(path, newline="", encoding=enc) as fh:
                rows = [{(k.strip() if isinstance(k, str) else k): v
                         for k, v in r.items()}
                        for r in csv.DictReader(fh, delimiter=delim)]
        except Exception as e:
            QMessageBox.warning(self, "Erreur lecture", str(e))
            return

        if not rows:
            QMessageBox.information(self, "CSV vide", "Le fichier CSV ne contient aucune donnée.")
            return

        csv_dir = os.path.dirname(os.path.abspath(path))
        # Auto-détection d'un CSV de traçages (lignes) : présence d'une colonne
        # WKT et/ou des champs spécifiques aux traçages. On crée alors une couche
        # de lignes — aucun bouton dédié, tout passe par l'import existant.
        headers_low = {h.strip().lower() for h in self._imp_csv_headers}
        is_tracage = ("wkt" in headers_low) or \
            ({"point_injection", "point_sortie"} <= headers_low)
        if is_tracage:
            self._imp_tracages_to_new_layer(rows, csv_dir)
        elif self._imp_radio_new.isChecked():
            self._imp_to_new_layer(rows, ref_col, csv_dir)
        else:
            self._imp_to_existing_layer(rows, ref_col, csv_dir)

    def _imp_tracages_to_new_layer(self, rows, csv_dir=""):
        """Crée une couche de traçages (lignes) depuis un CSV exporté par
        KarstEntry : géométrie reconstruite depuis la colonne WKT, attributs
        mappés par nom. Enregistre toujours un GeoPackage réel."""
        src_crs_id = getattr(self, "_imp_crs_id", None) \
            or self.canvas.mapSettings().destinationCrs().authid()
        layer_name = (self._imp_name_edit.text().strip()
                      if hasattr(self, "_imp_name_edit") else "") \
            or self._TRACAGES_LAYER_NAME
        layer = QgsVectorLayer(f"LineString?crs={src_crs_id}", layer_name, "memory")
        pr = layer.dataProvider()
        pr.addAttributes(self._tracages_field_defs())
        layer.updateFields()
        field_names = set(layer.fields().names())
        # Colonne WKT (insensible à la casse).
        wkt_col = next((h for h in self._imp_csv_headers
                        if h.strip().lower() == "wkt"), None)

        progress = self._make_progress("Import des traçages…", len(rows))
        feats = []
        added = 0
        for i, row in enumerate(rows, 1):
            progress.setValue(i)
            if self._progress_cancelled(progress):
                break
            feat = QgsFeature(layer.fields())
            wkt = (row.get(wkt_col) or "").strip() if wkt_col else ""
            if wkt:
                geom = QgsGeometry.fromWkt(wkt)
                if geom and not geom.isEmpty():
                    feat.setGeometry(geom)
            for col, val in row.items():
                # Ignorer les valeurs vides : "" dans un champ numérique
                # (distance_m) fait échouer l'écriture GeoPackage.
                if col in field_names and val not in (None, ""):
                    if col == "commentaire":
                        val = self._clean_html(val)
                    feat.setAttribute(col, val)
            feats.append(feat)
            added += 1
        if feats:
            pr.addFeatures(feats)
        progress.close()
        layer.updateExtents()

        # Persistance : toujours une vraie couche GeoPackage (cf. _imp_to_new_layer).
        layer, path, persisted = self._persist_new_layer_as_gpkg(
            layer, layer_name, csv_dir, "Enregistrer la couche de traçages")
        self._apply_tracages_style(layer)
        QgsProject.instance().addMapLayer(layer)
        where = f"\nEnregistrée : {path}" if persisted else \
            "\n⚠ Échec de l'enregistrement : couche en mémoire (non sauvegardée)."
        QMessageBox.information(
            self, "Import terminé",
            f"{added} traçage(s) importé(s) dans « {layer_name} »." + where)


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
    def _relativize_photos(rows, csv_dir, layer_dir, ref_col, column="photos",
                           subdir=""):
        """Copie les images à côté de la couche et stocke des chemins relatifs.

        Pour chaque photo : la source est résolue (absolue, ou relative au
        dossier du CSV), puis copiée sous layer_dir/[<subdir>/]<référence>/<fichier> ;
        la colonne reçoit le chemin relatif correspondant (séparateurs « / »).
        `subdir` = nom de la couche, pour grouper les photos par couche.
        Retourne le nombre d'images copiées. Modifie les dicts `rows` en place.
        """
        prefix = (subdir + "/") if subdir else ""
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
                base_rel = (p if not os.path.isabs(p)
                            else os.path.join(ref, os.path.basename(p))).replace("\\", "/")
                rel = prefix + base_rel
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

    _norm_name = staticmethod(feature_utils.norm_name)
    _coords_close = staticmethod(feature_utils.coords_close)


    def _is_duplicate(self, src_ref, src_name, src_x, src_y, existing, dist=None):
        """Délègue à feature_utils.is_duplicate avec la tolérance du plugin.

        src_(x,y) et existing[].(x,y) doivent être dans le MÊME CRS. Si `dist`
        (f(x1,y1,x2,y2)->m) est fourni, la tolérance est en mètres.
        """
        return feature_utils.is_duplicate(
            src_ref, src_name, src_x, src_y, existing,
            self._COORD_TOLERANCE, dist)

    _extract_xy = staticmethod(feature_utils.extract_xy)


    def _imp_to_new_layer(self, rows, ref_col, csv_dir=""):
        """Crée une couche depuis le CSV : mémoire, puis persistée sur disque
        (GeoPackage) si l'utilisateur choisit un emplacement."""
        # Photos en chemins absolus (résolus depuis le dossier du CSV) : valables
        # que la couche finisse en mémoire ou sur disque.
        if csv_dir and any(r.get("photos") for r in rows):
            self._absolutize_photos(rows, csv_dir)
        src_crs_id = getattr(self, "_imp_crs_id", None) \
            or self.canvas.mapSettings().destinationCrs().authid()
        layer_name = (self._imp_name_edit.text().strip()
                      if hasattr(self, "_imp_name_edit") else "") or self._CAVITES_LAYER_NAME
        layer = QgsVectorLayer(f"Point?crs={src_crs_id}", layer_name, "memory")
        pr = layer.dataProvider()
        # « fid » est réservé par le GeoPackage (clé primaire entière) : le
        # recréer en texte fait échouer l'écriture OGR. On l'ignore — c'est un
        # identifiant interne, pas une donnée (présent dans les CSV exportés).
        for col in self._imp_csv_headers:
            if col.strip().lower() == "fid":
                continue
            pr.addAttributes([QgsField(col, QVariant.String)])
        layer.updateFields()

        existing = []  # accumule les entrées déjà ajoutées dans cette session d'import
        added = skipped = 0
        # Distance métrique dans le CRS source (les coords comparées y sont).
        dist = self._metric_distance_fn(QgsCoordinateReferenceSystem(src_crs_id))

        progress = self._make_progress("Import en cours…", len(rows))
        feats = []  # accumulés puis ajoutés en un seul addFeatures (perf)
        for i, row in enumerate(rows, 1):
            progress.setValue(i)
            if self._progress_cancelled(progress):
                break
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
                if col.strip().lower() == "fid":
                    continue  # champ réservé non créé (cf. plus haut)
                val = row.get(col, "")
                if col == "comment":
                    val = self._clean_html(val)  # retire le HTML MS Office
                feat.setAttribute(col, val)
            feats.append(feat)
            existing.append({"ref": ref, "name": name, "x": x, "y": y})
            added += 1
        # Ajout en lot : bien plus rapide que addFeature un-par-un sur gros CSV.
        if feats:
            pr.addFeatures(feats)
        progress.close()

        layer.updateExtents()

        # Persistance sur disque : l'import produit TOUJOURS une vraie couche
        # GeoPackage (jamais une couche mémoire volatile).
        layer, path, persisted = self._persist_new_layer_as_gpkg(
            layer, layer_name, csv_dir, "Enregistrer la nouvelle couche")

        # Symbologie auto si la couche a un champ « type » (catégorisation).
        if "type" in self._imp_csv_headers:
            self._apply_cavites_style(layer)
        QgsProject.instance().addMapLayer(layer)
        where = f"\nEnregistrée : {path}" if persisted else \
            "\n⚠ Échec de l'enregistrement : couche en mémoire (non sauvegardée)."
        QMessageBox.information(
            self, "Import terminé",
            f"{added} entité(s) importée(s) dans « {layer_name} »."
            + (f"\n{skipped} doublon(s) ignoré(s)." if skipped else "")
            + where
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

        # Sauvegarde du GeoPackage AVANT toute modification (schéma ou import) :
        # une copie datée du jour dans le même dossier, filet de sécurité en cas
        # d'import malencontreux. Sans effet sur une couche mémoire.
        backup_path = self._backup_gpkg(self._layer_file_path(layer))

        # Vérifier que la couche choisie correspond au schéma cavités attendu
        # (géométrie + champs ; migration proposée si des colonnes manquent).
        if not self._ensure_layer_schema(layer, "cavites"):
            return

        # Photos : si la couche est sur disque, on copie les images à côté et on
        # stocke des chemins relatifs (portables). Sinon (mémoire), repli absolu.
        photo_note = ""
        if csv_dir and any(r.get("photos") for r in rows):
            layer_dir = self._layer_dir(layer)
            if layer_dir:
                n = self._relativize_photos(rows, csv_dir, layer_dir, ref_col,
                                            subdir=self._safe_dirname(layer.name()))
                photo_note = f"\n{n} photo(s) copiée(s) dans « {self._safe_dirname(layer.name())}/ »."
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

        progress = self._make_progress("Import en cours…", len(rows))
        feats = []  # accumulés puis ajoutés en un seul addFeatures (perf)
        for i, row in enumerate(rows, 1):
            progress.setValue(i)
            if self._progress_cancelled(progress):
                break
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
                    val = row.get(src_col, "")
                    if dst_field == "comment":
                        val = self._clean_html(val)  # retire le HTML MS Office
                    feat.setAttribute(dst_field, val)
            if dest_ref_field and dest_ref_field in dest_fields and not feat[dest_ref_field]:
                feat.setAttribute(dest_ref_field, ref)

            feats.append(feat)
            # Coords en CRS dest (dx, dy) pour rester cohérent avec `existing`.
            existing.append({"ref": ref, "name": name, "x": dx, "y": dy})
            added += 1
        # Ajout en lot : bien plus rapide que addFeature un-par-un sur gros CSV.
        if feats:
            pr.addFeatures(feats)
        progress.close()

        layer.updateExtents()
        layer.triggerRepaint()
        backup_note = f"\nSauvegarde avant import : {os.path.basename(backup_path)}" \
            if backup_path else ""
        QMessageBox.information(
            self, "Import terminé",
            f"{added} entité(s) importée(s) dans « {layer.name()} ».\n"
            + (f"{skipped} doublon(s) ignoré(s)." if skipped else "")
            + photo_note + backup_note
        )

    # Délégations vers csv_io (fonctions pures, testables sans QGIS).
    _detect_crs = staticmethod(csv_io.detect_crs)

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
        if dlg.exec():
            self._imp_set_crs(dlg.crs().authid())

    _detect_encoding = staticmethod(csv_io.detect_encoding)
    _detect_delimiter = staticmethod(csv_io.detect_delimiter)
    _try_float = staticmethod(csv_io.try_float)

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
        """Reçoit le point capturé (CRS du canevas), restaure l'outil précédent."""
        self._apply_captured_point(point)
        self._reset_capture()
        self.raise_()          # remet la fenêtre au premier plan après le clic carte
        self.activateWindow()

    def _apply_captured_point(self, point):
        """Enregistre un point (exprimé dans le CRS du canevas) : labels + autofill."""
        self._captured_point = point
        self._captured_crs = self.canvas.mapSettings().destinationCrs()
        self._lbl_x.setText(f"{point.x():.6f}")
        self._lbl_y.setText(f"{point.y():.6f}")
        self._autofill_admin(point)

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
