# Copyright (c) 2026 Julien Tournois
# Licence : PolyForm Noncommercial License 1.0.0
# Usage commercial interdit sans autorisation écrite — julien.tournois@gmail.com

"""
map_tool.py
===========
Outil de capture de point sur le canevas QGIS.

PointCaptureTool émet le signal `pointCaptured(QgsPointXY)` au relâchement
du bouton de la souris, puis le dialogue reprend la main.
"""

from qgis.gui import QgsMapTool
from qgis.core import QgsPointXY
from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QCursor

# Compatibilité PyQt5/PyQt6 pour l'enum du curseur
try:
    _CrossCursor = Qt.CursorShape.CrossCursor
except AttributeError:
    _CrossCursor = Qt.CrossCursor


class PointCaptureTool(QgsMapTool):
    """Outil QGIS qui capture un seul clic sur la carte et émet pointCaptured.

    Utilisation typique :
        tool = PointCaptureTool(canvas)
        tool.pointCaptured.connect(my_slot)
        canvas.setMapTool(tool)
    """

    # Signal émis avec les coordonnées carte du clic (dans le CRS du canevas).
    pointCaptured = pyqtSignal(QgsPointXY)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(QCursor(_CrossCursor))

    def canvasReleaseEvent(self, event):
        """Convertit le clic en coordonnées carte et émet le signal."""
        point = self.toMapCoordinates(event.pos())
        self.pointCaptured.emit(point)
