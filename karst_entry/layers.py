# Copyright (c) 2026 Julien Tournois — PolyForm Noncommercial 1.0
"""Gestion des couches GeoPackage de KarstEntry — mixin séparé pour alléger
karst_dialog.py : résolution/création/persistance des couches cavités et
traçages, validation/migration de schéma, symbologie et distance métrique.

Méthodes d'instance (self) regroupées dans LayersMixin, dont hérite KarstDialog.
Aucun signal/slot déplacé.
"""
import os
import shutil
import datetime

from qgis.PyQt.QtWidgets import QMessageBox, QFileDialog
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes,
    QgsDistanceArea, QgsVectorFileWriter,
    QgsCategorizedSymbolRenderer, QgsRendererCategory,
    QgsMarkerSymbol, QgsLineSymbol, QgsPointXY, QgsUnitTypes,
)
from .ui_constants import *  # noqa: F401,F403
from .schema import (
    _schema_fields, _qgs_fields,
    _FALLBACK_CAVITES_FIELDS, _FALLBACK_TRACAGES_FIELDS,
)


class LayersMixin:
    """Méthodes de gestion des couches (schéma, création, persistance, style)."""

    @staticmethod
    def _cavites_field_defs():
        """Champs de la couche cavités, chargés depuis karst_schema.json.

        x/y (miroir de la géométrie) sont propres à KarstEntry, ajoutés
        en plus des champs du contrat partagé.
        """
        return _qgs_fields(
            _schema_fields("cavites_connues", _FALLBACK_CAVITES_FIELDS),
            extra=[("x", QVariant.Double), ("y", QVariant.Double)])

    def _ensure_layer_schema(self, layer, kind):
        """Vérifie qu'une couche choisie correspond au schéma attendu.

        Géométrie incompatible → échec. Champs manquants → propose de les
        ajouter (migration). Retourne True si la couche est utilisable.
        """
        if kind == "cavites":
            expected = self._cavites_field_defs()
            want_geom, glabel = QgsWkbTypes.PointGeometry, "points (cavités)"
        else:
            expected = self._tracages_field_defs()
            want_geom, glabel = QgsWkbTypes.LineGeometry, "lignes (traçages)"
        try:
            if layer.geometryType() != want_geom:
                QMessageBox.warning(
                    self, "Couche incompatible",
                    f"« {layer.name()} » n'est pas une couche de {glabel}.\n"
                    "Choisissez une autre couche.")
                return False
            present = set(layer.fields().names())
        except (AttributeError, RuntimeError):
            QMessageBox.warning(self, "Couche illisible", "Couche inutilisable.")
            return False
        missing = [f for f in expected if f.name() not in present]
        if missing:
            names = ", ".join(f.name() for f in missing)
            reply = QMessageBox.question(
                self, "Schéma incomplet",
                f"« {layer.name()} » n'a pas les colonnes du schéma :\n{names}\n\n"
                "Les ajouter à la couche ?", _MsgYes | _MsgNo, _MsgYes)
            if reply != _MsgYes:
                return False
            try:
                layer.dataProvider().addAttributes(missing)
                layer.updateFields()
            except Exception:
                QMessageBox.warning(self, "Échec",
                                    "Impossible d'ajouter les colonnes.")
                return False
        return True

    def _target_cavites_layer(self):
        """Couche cible des cavités selon le sélecteur (existante validée ou nouvelle)."""
        data = self._new_target_combo.currentData() \
            if hasattr(self, "_new_target_combo") else "__new__"
        if data and data != "__new__":
            layer = QgsProject.instance().mapLayer(data)
            if layer is None:
                QMessageBox.warning(self, "Couche introuvable",
                                    "La couche choisie n'existe plus.")
                return None
            if not self._ensure_layer_schema(layer, "cavites"):
                return None
            return layer
        name = (self._new_name_edit.text().strip()
                if hasattr(self, "_new_name_edit") else "") or self._CAVITES_LAYER_NAME
        return self._create_persistent_cavites_layer(name)

    def _selected_export_layer(self):
        """Couche pour l'export.

        Si la couche active de QGIS (panneau Couches) est une couche de LIGNES
        valide, on l'exporte : c'est le moyen — sans nouveau bouton — d'exporter
        une couche de traçages. Sinon comportement cavités habituel : couche
        choisie dans « Couche active », ou couche cavités résolue.
        """
        # Priorité au choix explicite dans « Couche active » (qui liste
        # désormais cavités ET traçages).
        data = self._new_target_combo.currentData() \
            if hasattr(self, "_new_target_combo") else None
        if data and data != "__new__":
            layer = QgsProject.instance().mapLayer(data)
            if layer is not None:
                return layer
        # Repli : couche active de QGIS si c'est une couche de lignes (traçages).
        active = getattr(self.iface, "activeLayer", lambda: None)()
        if active is not None and hasattr(active, "geometryType"):
            try:
                if active.isValid() \
                        and active.geometryType() == QgsWkbTypes.LineGeometry:
                    return active
            except (AttributeError, RuntimeError):
                pass
        return self._resolve_cavites_layer()

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

    def _create_persistent_cavites_layer(self, name=None):
        """Crée une couche cavités PERSISTANTE (GeoPackage sur disque), pas en mémoire.

        `name` : nom de la couche (défaut « Inventaire Cavités »). Le fichier est
        créé dans le dossier du projet s'il est enregistré, sinon l'utilisateur
        choisit l'emplacement. Renvoie la couche ogr chargée, ou None si annulé.
        """
        name = name or self._CAVITES_LAYER_NAME
        proj_dir = QgsProject.instance().absolutePath()
        if proj_dir:
            path = os.path.join(proj_dir, f"{name}.gpkg")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Enregistrer la couche cavités",
                f"{name}.gpkg", "GeoPackage (*.gpkg)")
            if not path:
                return None

        # Si le fichier existe déjà, le charger plutôt que l'écraser.
        created = not os.path.isfile(path)
        if created:
            crs = self.canvas.mapSettings().destinationCrs()
            mem = QgsVectorLayer(f"Point?crs={crs.authid()}", name, "memory")
            mem.dataProvider().addAttributes(self._cavites_field_defs())
            mem.updateFields()
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = name
            ctx = QgsProject.instance().transformContext()
            try:
                res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, ctx, options)
            except AttributeError:
                res = QgsVectorFileWriter.writeAsVectorFormatV2(mem, path, ctx, options)
            if res[0] != QgsVectorFileWriter.NoError:
                QMessageBox.warning(self, "Création impossible",
                                    f"Impossible de créer le GeoPackage :\n{res[1]}")
                return None

        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Couche invalide",
                                f"Le GeoPackage créé est illisible :\n{path}")
            return None
        if created:
            self._apply_cavites_style(layer)
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _apply_cavites_style(self, layer):
        """Applique une symbologie catégorisée par `type` à la couche cavités."""
        try:
            cats = []
            for t in KARST_TYPES:
                color = _TYPE_COLORS.get(t, _TYPE_COLOR_DEFAULT)
                marker = _TYPE_MARKERS.get(t, _TYPE_MARKER_DEFAULT)
                sym = QgsMarkerSymbol.createSimple(
                    {"name": marker, "color": color, "size": _TYPE_MARKER_SIZE,
                     "outline_color": "#2C2620", "outline_width": "0.3"})
                cats.append(QgsRendererCategory(t, sym, t))
            # Catégorie par défaut (valeurs hors liste / vides).
            default_sym = QgsMarkerSymbol.createSimple(
                {"name": _TYPE_MARKER_DEFAULT, "color": _TYPE_COLOR_DEFAULT,
                 "size": _TYPE_MARKER_SIZE,
                 "outline_color": "#2C2620", "outline_width": "0.3"})
            cats.append(QgsRendererCategory("", default_sym, "Autre / non renseigné"))
            layer.setRenderer(QgsCategorizedSymbolRenderer("type", cats))
            layer.triggerRepaint()
            self._save_style_to_gpkg(layer)
        except Exception:
            pass  # symbologie = confort, jamais bloquant

    def _apply_tracages_style(self, layer):
        """Applique une symbologie catégorisée par `resultat` aux traçages."""
        try:
            cats = []
            for r, color in _RESULT_COLORS.items():
                sym = QgsLineSymbol.createSimple({"color": color, "width": "0.7"})
                cats.append(QgsRendererCategory(r, sym, r))
            default_sym = QgsLineSymbol.createSimple(
                {"color": _RESULT_COLOR_DEFAULT, "width": "0.7"})
            cats.append(QgsRendererCategory("", default_sym, "Non renseigné"))
            layer.setRenderer(QgsCategorizedSymbolRenderer("resultat", cats))
            layer.triggerRepaint()
            self._save_style_to_gpkg(layer)
        except Exception:
            pass

    def _target_tracages_layer(self):
        """Couche cible des traçages selon le sélecteur (existante validée ou nouvelle)."""
        data = self._tr_target_combo.currentData() \
            if hasattr(self, "_tr_target_combo") else "__new__"
        if data and data != "__new__":
            layer = QgsProject.instance().mapLayer(data)
            if layer is None:
                QMessageBox.warning(self, "Couche introuvable",
                                    "La couche choisie n'existe plus.")
                return None
            if not self._ensure_layer_schema(layer, "tracages"):
                return None
            return layer
        name = (self._tr_name_edit.text().strip()
                if hasattr(self, "_tr_name_edit") else "") or self._TRACAGES_LAYER_NAME
        return self._create_persistent_tracages_layer(name)

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

    def _create_persistent_tracages_layer(self, name=None):
        """Crée une couche traçages PERSISTANTE (GeoPackage sur disque), pas en mémoire."""
        name = name or self._TRACAGES_LAYER_NAME
        proj_dir = QgsProject.instance().absolutePath()
        if proj_dir:
            path = os.path.join(proj_dir, f"{name}.gpkg")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Enregistrer la couche traçages",
                f"{name}.gpkg", "GeoPackage (*.gpkg)")
            if not path:
                return None

        created = not os.path.isfile(path)
        if created:
            proj_crs = self.canvas.mapSettings().destinationCrs()
            mem = QgsVectorLayer(f"LineString?crs={proj_crs.authid()}", name, "memory")
            mem.dataProvider().addAttributes(self._tracages_field_defs())
            mem.updateFields()
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = name
            ctx = QgsProject.instance().transformContext()
            try:
                res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, ctx, options)
            except AttributeError:
                res = QgsVectorFileWriter.writeAsVectorFormatV2(mem, path, ctx, options)
            if res[0] != QgsVectorFileWriter.NoError:
                QMessageBox.warning(self, "Création impossible",
                                    f"Impossible de créer le GeoPackage :\n{res[1]}")
                return None

        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Couche invalide",
                                f"Le GeoPackage créé est illisible :\n{path}")
            return None
        if created:
            self._apply_tracages_style(layer)
        QgsProject.instance().addMapLayer(layer)
        return layer

    @staticmethod
    def _layer_dir(layer):
        """Dossier du fichier source d'une couche, ou '' si couche mémoire."""
        try:
            src = layer.dataProvider().dataSourceUri().split("|")[0]
        except (AttributeError, RuntimeError):
            return ""
        return os.path.dirname(src) if os.path.isfile(src) else ""

    @staticmethod
    def _layer_file_path(layer):
        """Chemin complet du fichier GeoPackage source d'une couche, ou '' si
        couche mémoire (ou source introuvable sur disque)."""
        try:
            src = layer.dataProvider().dataSourceUri().split("|")[0]
        except (AttributeError, RuntimeError):
            return ""
        return src if os.path.isfile(src) else ""

    @staticmethod
    def _backup_gpkg(path):
        """Sauvegarde un GeoPackage avant modification, dans le même dossier,
        avec la date du jour dans le nom (ex. « Inventaire.backup-2026-07-03.gpkg »).

        Une seule sauvegarde par jour est conservée : si un backup du jour
        existe déjà, on ne l'écrase pas (c'est l'état le plus utile — celui
        d'avant tout import de la journée). Silencieux en cas d'échec : la
        sauvegarde est un filet de sécurité, jamais un blocage de l'import.
        Retourne le chemin du backup, ou None si non applicable/échec.
        """
        if not path or not os.path.isfile(path):
            return None
        folder = os.path.dirname(path)
        base, ext = os.path.splitext(os.path.basename(path))
        date_str = datetime.date.today().isoformat()
        backup_path = os.path.join(folder, f"{base}.backup-{date_str}{ext}")
        if os.path.exists(backup_path):
            return backup_path
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            return None
        return backup_path

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

    def _save_memory_layer_as_gpkg(self, mem, path, name):
        """Écrit une couche mémoire dans un GeoPackage sur disque et la recharge.

        Retourne la couche ogr chargée, ou None en cas d'échec.
        """
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = name
        ctx = QgsProject.instance().transformContext()
        try:
            res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, ctx, options)
        except AttributeError:
            res = QgsVectorFileWriter.writeAsVectorFormatV2(mem, path, ctx, options)
        if res[0] != QgsVectorFileWriter.NoError:
            QMessageBox.warning(self, "Enregistrement impossible",
                                f"Impossible d'écrire le GeoPackage :\n{res[1]}")
            return None
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            QMessageBox.warning(self, "Couche invalide",
                                f"Le GeoPackage créé est illisible :\n{path}")
            return None
        return layer

    def _persist_new_layer_as_gpkg(self, layer, layer_name, csv_dir, dialog_title):
        """Propose un emplacement GeoPackage pour une couche mémoire nouvellement
        importée et l'y enregistre (factorise le import cavités/traçages).

        Emplacement pré-rempli : dossier du projet, sinon celui du CSV importé.
        Annuler la boîte de dialogue utilise cet emplacement par défaut plutôt
        que de laisser une couche mémoire volatile (cf. _imp_to_new_layer /
        _imp_tracages_to_new_layer).

        Retourne (layer, path, persisted) : `layer` est la couche persistée en
        cas de succès, sinon la couche mémoire d'origine ; `persisted` est
        False si l'écriture GeoPackage a échoué.
        """
        proj_dir = QgsProject.instance().absolutePath() or csv_dir or ""
        default = os.path.join(proj_dir, f"{layer_name}.gpkg") if proj_dir \
            else f"{layer_name}.gpkg"
        path, _ = QFileDialog.getSaveFileName(
            self, dialog_title, default, "GeoPackage (*.gpkg)")
        if not path:
            path = default  # annulation → emplacement par défaut (couche réelle)
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"
        saved = self._save_memory_layer_as_gpkg(layer, path, layer_name)
        persisted = saved is not None
        if persisted:
            layer = saved
        return layer, path, persisted
