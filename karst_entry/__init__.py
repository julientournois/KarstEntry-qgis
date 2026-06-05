# Point d'entrée QGIS — appelé automatiquement au chargement du plugin.
def classFactory(iface):
    """Instancie et retourne le plugin principal.

    Paramètres
    ----------
    iface : QgisInterface
        Interface QGIS fournie par le gestionnaire de plugins.
    """
    from .karst_entry import KarstEntryPlugin
    return KarstEntryPlugin(iface)
