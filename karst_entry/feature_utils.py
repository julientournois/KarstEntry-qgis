# Copyright (c) 2026 Julien Tournois
# Licence : PolyForm Noncommercial License 1.0.0
# Usage commercial interdit sans autorisation écrite — julien.tournois@gmail.com

"""
feature_utils.py
================
Fonctions pures de recherche, dédoublonnage et affichage d'entités.

Aucune dépendance Qt/QGIS : les « features » sont manipulées par duck-typing
(`feat[name]`, `feat.id()`), ce qui rend ces fonctions testables sans QGIS.
Le dialogue (karst_dialog) ne garde que de minces délégations.
"""
from __future__ import annotations

import html as _html
import re
import unicodedata

# Balises HTML/MS-Office (et commentaires conditionnels <!...>) à retirer.
_TAG_RE = re.compile(r"</?[a-zA-Z!][^>]*>", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WS_RE = re.compile(r"\s+")


def clean_html(text):
    """Retire le boilerplate HTML/Microsoft Office d'un texte (champ commentaire).

    Supprime les balises et commentaires HTML, décode les entités (&nbsp;,
    &quot;, &amp;…) et normalise les espaces. Le texte hors balises est conservé.
    Tolérant : « h < 5m » (pas une balise) reste intact.
    """
    if text is None:
        return text
    s = str(text)
    if "<" not in s and "&" not in s and "mso-" not in s:
        return s.strip()
    s = _HTML_COMMENT_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def fold(text):
    """Normalise pour comparaison : minuscules + suppression des accents.

    « Résurgence » → « resurgence », « Vallée Sèche » → « vallee seche ».
    Permet une recherche insensible à la casse ET aux accents.
    """
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def fold_key(header):
    """Clé de comparaison d'un nom de colonne : minuscules, sans accents,
    seulement les caractères alphanumériques. « Nom de la cavité » → « nomdelacavite »."""
    return "".join(c for c in fold(header) if c.isalnum())


# Synonymes par champ canonique (matching d'import universel).
FIELD_SYNONYMS = {
    "reference":   ["reference", "ref", "numero", "num", "no", "id",
                    "code", "identifiant"],
    "name":        ["name", "nom", "libelle", "intitule", "appellation",
                    "nomcavite", "nomdelacavite"],
    "type":        ["type", "categorie", "nature"],
    "x":           ["x", "lon", "lng", "long", "longitude", "est", "easting", "e"],
    "y":           ["y", "lat", "latitude", "nord", "northing", "n"],
    "altitude":    ["altitude", "alt", "z", "elevation", "ele"],
    "comment":     ["comment", "commentaire", "remarque", "remarques",
                    "note", "notes", "description", "desc"],
    "commune":     ["commune", "ville"],
    "code_postal": ["codepostal", "cp"],
    "code_insee":  ["codeinsee", "insee"],
    "departement": ["departement", "dept", "departementnom"],
    "code_dept":   ["codedept", "codedepartement"],
    "explorers":   ["explorers", "explorateurs", "inventeurs", "decouvreurs"],
    "date_disc":   ["datedisc", "datedecouverte", "datedecouv", "decouverte"],
    "date_expl":   ["dateexpl", "dateexploration", "exploration"],
    "prot_id":     ["protid", "idprotection", "idzone"],
}

_SYN_TO_CANON = {fold_key(s): canon
                 for canon, syns in FIELD_SYNONYMS.items() for s in syns}


def guess_dest_field(src_col, dest_fields):
    """Devine le champ destination correspondant à une colonne CSV.

    1) correspondance exacte (normalisée : casse/accents/espaces ignorés) ;
    2) sinon via les synonymes → champ canonique présent dans la destination.
    Retourne "" si rien ne correspond.
    """
    key = fold_key(src_col)
    if not key:
        return ""
    for d in dest_fields:
        if fold_key(d) == key:
            return d
    canon = _SYN_TO_CANON.get(key)
    if canon:
        ck = fold_key(canon)
        for d in dest_fields:
            if fold_key(d) == ck:
                return d
    return ""


def extract_xy(row):
    """Lit x/y d'une ligne CSV via les synonymes (insensible casse/accents/espaces)."""
    folded = {}
    for k, v in row.items():
        folded.setdefault(fold_key(k), v)

    def num(syns):
        for s in syns:
            v = folded.get(fold_key(s))
            if v not in (None, ""):
                try:
                    return float(v)
                except (ValueError, TypeError):
                    pass
        return None
    return num(FIELD_SYNONYMS["x"]), num(FIELD_SYNONYMS["y"])


def is_blank(val):
    """True si une valeur d'attribut est vide : None, chaîne vide, ou NULL QGIS."""
    if val is None:
        return True
    s = str(val).strip()
    return s == "" or s.upper() == "NULL"


def norm_name(val):
    """Normalise un nom pour la comparaison : strip + minuscules."""
    return str(val).strip().lower() if val else ""


def coords_close(x1, y1, x2, y2, tol):
    """True si les deux points sont à moins de tol unités l'un de l'autre
    (distance planaire, dans les unités du CRS des coordonnées)."""
    if None in (x1, y1, x2, y2):
        return False
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 < tol


def feature_label(feat, fields):
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


def feature_matches(feat, fields, search, type_filter):
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
        if fold(search) not in fold(" ".join(parts)):
            return False
    return True


def is_duplicate(src_ref, src_name, src_x, src_y, existing, tol, dist=None):
    """Détecte un doublon d'entité.

    existing : liste de dicts {ref, name, x, y} (mêmes CRS que src_x/src_y).
    tol      : tolérance de position. Si `dist` est fourni (f(x1,y1,x2,y2)->m),
               elle est en mètres ; sinon comparaison planaire (unités du CRS).

    Règle 1 — référence non vide : même référence + même nom + même position.
    Règle 2 — référence vide : même nom (insensible à la casse) + même position.
    """
    norm_src = norm_name(src_name)

    def close(x1, y1, x2, y2):
        if None in (x1, y1, x2, y2):
            return False
        if dist is not None:
            return dist(x1, y1, x2, y2) < tol
        return coords_close(x1, y1, x2, y2, tol)

    if src_ref:
        for e in existing:
            if e["ref"] == src_ref:
                same_name = norm_name(e["name"]) == norm_src
                if e["x"] is not None and src_x is not None:
                    same_pos = close(src_x, src_y, e["x"], e["y"])
                else:
                    same_pos = (e["x"] is None and src_x is None)
                return same_name and same_pos
        return False

    for e in existing:
        same_name = norm_name(e["name"]) == norm_src and norm_src != ""
        if e["x"] is not None and src_x is not None:
            same_pos = close(src_x, src_y, e["x"], e["y"])
        else:
            same_pos = (e["x"] is None and src_x is None)
        if same_name and same_pos:
            return True
    return False
