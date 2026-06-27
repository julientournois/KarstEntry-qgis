# Copyright (c) 2026 Julien Tournois — PolyForm Noncommercial 1.0
"""Entrées/sorties CSV et GPX — fonctions **pures** (aucune dépendance QGIS/Qt).

Regroupe la détection d'encodage, de délimiteur et de CRS à la lecture des CSV,
ainsi que la sérialisation GPX. Isolé de `karst_dialog` pour rester testable sans
QGIS et alléger le dialogue. Voir aussi `feature_utils` (mapping de colonnes,
nettoyage) et `geocode_utils`.
"""
import csv
from xml.sax.saxutils import escape


def detect_encoding(path):
    """Devine l'encodage d'un CSV : UTF-8 (avec/sans BOM) sinon Windows-1252.

    Beaucoup de CSV produits sous Windows/Excel (FR) sont en cp1252/latin-1
    (« é » = 0xe9), ce qui fait échouer une lecture utf-8 stricte. latin-1
    mappe les 256 octets et ne lève jamais : c'est le repli ultime.
    """
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                fh.read()
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(path):
    """Sniff the CSV delimiter; fall back to semicolon then comma."""
    enc = detect_encoding(path)
    with open(path, newline="", encoding=enc) as fh:
        sample = fh.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ";" if ";" in sample else ","


def detect_crs(sample_rows):
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


def try_float(val):
    """Convertit en float, ou None si vide/invalide (ne lève jamais)."""
    try:
        return float(val) if val not in (None, "") else None
    except (ValueError, TypeError):
        return None


def gpx_document(waypoints):
    """Construit un document GPX (chaîne XML) depuis une liste de waypoints.

    waypoints : liste de dicts {lat, lon, name, desc?, ele?} en WGS84.
    Fonction pure → testable sans QGIS.
    """
    def esc(v):
        return escape(str(v)) if v is not None else ""

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Karst Entry" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
    ]
    for wp in waypoints:
        lat, lon = wp.get("lat"), wp.get("lon")
        if lat is None or lon is None:
            continue
        lines.append(f'  <wpt lat="{lat:.8f}" lon="{lon:.8f}">')
        if wp.get("ele") is not None:
            lines.append(f"    <ele>{wp['ele']}</ele>")
        if wp.get("name"):
            lines.append(f"    <name>{esc(wp['name'])}</name>")
        if wp.get("desc"):
            lines.append(f"    <desc>{esc(wp['desc'])}</desc>")
        lines.append("  </wpt>")
    lines.append("</gpx>")
    return "\n".join(lines) + "\n"
