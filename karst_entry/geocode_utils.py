# Copyright (c) 2026 Julien Tournois
# Licence : PolyForm Noncommercial License 1.0.0
# Usage commercial interdit sans autorisation écrite — julien.tournois@gmail.com

"""
geocode_utils.py
================
Géocodage inverse communal (geo.api.gouv.fr) et point-dans-polygone local.

Fonctions pures, sans dépendance Qt/QGIS : utilisables depuis un thread de
travail comme depuis les tests. Le dialogue (karst_dialog) ne garde que de
minces délégations.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request


def reverse_geocode(lat, lon, timeout=3.0, retries=2, backoff=0.5):
    """Géocodage inverse via geo.api.gouv.fr (point-dans-polygone communal).

    Retourne un dict commune/code_insee/code_postal/departement/code_dept,
    plus « _contour » (géométrie GeoJSON de la commune, pour mise en cache),
    ou None en cas d'échec (réseau, hors France, réponse vide). Ne lève
    jamais d'exception.

    Résilience terrain (réseau instable) : en cas d'erreur réseau/timeout, on
    réessaie `retries` fois avec un délai exponentiel (`backoff`·2ⁿ). Une réponse
    valide mais vide (point hors France) n'est PAS réessayée — c'est définitif.
    """
    params = urllib.parse.urlencode({
        "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
        "fields": "nom,code,codesPostaux,departement,contour",
        "format": "json", "geometry": "contour",
    })
    url = f"https://geo.api.gouv.fr/communes?{params}"
    data = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break  # réponse réseau obtenue (même vide) : pas de réessai
        except Exception:
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            return None
    if not data:
        return None
    c = data[0]
    cps = c.get("codesPostaux") or []
    dep = c.get("departement") or {}
    return {
        "commune":     c.get("nom", ""),
        "code_insee":  c.get("code", ""),
        "code_postal": cps[0] if cps else "",
        "departement": dep.get("nom", ""),
        "code_dept":   dep.get("code", ""),
        "_contour":    c.get("contour"),
    }


def parse_geojson_polygons(geom):
    """Normalise une géométrie GeoJSON en liste de polygones.

    Chaque polygone = liste d'anneaux ; chaque anneau = liste de (lon, lat).
    Le 1er anneau est l'extérieur, les suivants des trous. Retourne [] si la
    géométrie est absente ou non polygonale.
    """
    if not geom:
        return []
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return []
    if gtype == "Polygon":
        return [coords]
    if gtype == "MultiPolygon":
        return list(coords)
    return []


def point_in_ring(lon, lat, ring):
    """Ray casting : True si (lon, lat) est à l'intérieur de l'anneau."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and \
                (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def point_in_polygons(lon, lat, polygons):
    """True si le point est dans l'un des polygones (extérieur, hors trous)."""
    for polygon in polygons:
        if not polygon:
            continue
        if point_in_ring(lon, lat, polygon[0]) and \
                not any(point_in_ring(lon, lat, hole)
                        for hole in polygon[1:]):
            return True
    return False


# ── Persistance du cache communal ─────────────────────────────────────────────
# Format sur disque : [{"polygons": [...], "info": {...}}, ...]
# En mémoire : liste de (polygons, info), comme avant.

CACHE_FILENAME = "karst_commune_cache.json"


def load_commune_cache(directory):
    """Charge le cache communal depuis <directory>/karst_commune_cache.json.

    Retourne une liste de (polygons, info). Toute erreur (fichier absent,
    JSON invalide, droits) renvoie simplement une liste vide.
    """
    if not directory:
        return []
    path = os.path.join(directory, CACHE_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return [(e["polygons"], e["info"]) for e in raw
                if isinstance(e, dict) and "polygons" in e and "info" in e]
    except Exception:
        return []


def save_commune_cache(directory, cache):
    """Écrit le cache communal dans <directory>/karst_commune_cache.json.

    Silencieux en cas d'échec (dossier en lecture seule, etc.) : le cache
    disque est un confort, jamais un point de blocage de la saisie.
    """
    if not directory:
        return False
    path = os.path.join(directory, CACHE_FILENAME)
    try:
        data = [{"polygons": polygons, "info": info}
                for polygons, info in cache]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return True
    except Exception:
        return False
