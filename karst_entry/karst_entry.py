# Copyright (c) 2026 Julien Tournois
# Licence : PolyForm Noncommercial License 1.0.0
# Usage commercial interdit sans autorisation écrite — julien.tournois@gmail.com

"""
karst_entry.py
==============
Point d'entrée du plugin QGIS Karst Entry.

KarstEntryPlugin est instancié par classFactory() dans __init__.py.
Il ajoute une action dans la barre d'outils et le menu QGIS, et
ouvre KarstDialog à la demande (instance unique réutilisée).
"""

import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt

# Compatibilité PyQt5/PyQt6 pour supprimer le bouton "?" de la barre de titre
try:
    _WNoHelpBtn = Qt.WindowType.WindowContextHelpButtonHint
except AttributeError:
    _WNoHelpBtn = Qt.WindowContextHelpButtonHint

from .karst_dialog import KarstDialog


class KarstEntryPlugin:
    """Gestionnaire de cycle de vie du plugin (initGui / unload)."""
    def __init__(self, iface):
        self.iface = iface
        self._action = None
        self._dialog = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self._action = QAction(icon, "Saisie Karstique", self.iface.mainWindow())
        self._action.setToolTip("Ouvrir le formulaire de saisie karstique")
        self._action.triggered.connect(self._open_dialog)

        self.iface.addToolBarIcon(self._action)
        self.iface.addPluginToMenu("&Karst Entry", self._action)

    def unload(self):
        self.iface.removePluginMenu("&Karst Entry", self._action)
        self.iface.removeToolBarIcon(self._action)
        if self._dialog:
            self._dialog.close()
            self._dialog = None

    def _open_dialog(self):
        if self._dialog is None:
            self._dialog = KarstDialog(self.iface, self.iface.mainWindow())
            self._dialog.setWindowFlags(
                self._dialog.windowFlags() & ~_WNoHelpBtn
            )
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
