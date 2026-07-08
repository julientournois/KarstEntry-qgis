# Copyright (c) 2026 Julien Tournois — PolyForm Noncommercial 1.0
"""Schéma des couches KarstEntry : chargement de karst_schema.json, replis et
construction de listes de QgsField.

Contrat partagé avec **KarstPro** (copie locale par projet, cf. karst_schema.json) :
source de vérité des noms/types de champs. Isolé de `karst_dialog` pour être
partagé avec `layers` sans import circulaire.
"""
import os
import json
import warnings

from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsField

__all__ = [
    "_SCHEMA_PATH", "_QVARIANT_BY_TYPE", "_FALLBACK_CAVITES_FIELDS",
    "_FALLBACK_TRACAGES_FIELDS", "_load_schema", "_SCHEMA",
    "_schema_fields", "_qgs_fields",
]

# Contrat de schéma partagé avec KarstPro (copie locale par projet).
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
    "developpement_estime": "float", "altitude": "float",
    "topographiable": "int64", "lien_topo": "str",
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
        warnings.warn(f"karst_schema.json illisible : {exc}")
        return {}


_SCHEMA = _load_schema()


def _schema_fields(layer_key, fallback):
    """Retourne le dict {nom: type} des champs d'une couche du schéma, ou le repli."""
    fields = (_SCHEMA.get("layers", {}).get(layer_key, {}).get("fields"))
    return fields if fields else fallback


def _qgs_fields(fields_def, extra=()):
    """Construit une liste de QgsField depuis un dict {nom: type_json}.

    `extra` : champs supplémentaires (nom, QVariant) propres à KarstEntry,
    ajoutés s'ils ne sont pas déjà dans le schéma (ex. x/y, miroir géométrie).
    """
    defs = [QgsField(name, _QVARIANT_BY_TYPE.get(t, QVariant.String))
            for name, t in fields_def.items()]
    for name, qvar in extra:
        if name not in fields_def:
            defs.append(QgsField(name, qvar))
    return defs
