# Copyright (c) 2026 Julien Tournois — PolyForm Noncommercial 1.0
"""Construction des onglets de KarstDialog — mixin séparé pour alléger
karst_dialog.py. Les méthodes restent des méthodes d'instance (self) : aucun
signal/slot ni handler n'est déplacé, seul l'emplacement du code de mise en page
change. KarstDialog hérite de TabBuildersMixin.
"""
import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDateEdit, QPushButton, QTabWidget, QWidget, QMessageBox,
    QFileDialog, QScrollArea, QGroupBox, QSizePolicy, QListWidget,
    QListWidgetItem, QAbstractItemView, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QRadioButton, QProgressDialog
)
from qgis.PyQt.QtCore import Qt, QDate, QSize, QVariant, pyqtSignal
from qgis.PyQt.QtGui import QPixmap, QIcon
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsWkbTypes, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsDistanceArea, QgsVectorFileWriter,
)
from .ui_constants import *  # noqa: F401,F403  (constantes + compat enums)


class TabBuildersMixin:
    """Méthodes _build_*_tab de KarstDialog (mise en page des onglets)."""

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

        self._f_altitude = QLineEdit()
        self._f_altitude.setPlaceholderText("mètres (optionnel)")
        form.addRow("Altitude (m)", self._f_altitude)

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

        # Couche active (ajout / export) — par défaut « Inventaire Cavités ».
        dest_group = QGroupBox("Couche active (ajout / export)")
        dest_layout = QHBoxLayout(dest_group)
        dest_layout.addWidget(QLabel("Couche :"))
        self._new_target_combo = QComboBox()
        self._new_target_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._new_target_combo.currentIndexChanged.connect(self._new_target_changed)
        dest_layout.addWidget(self._new_target_combo, 1)
        btn_dest_refresh = QPushButton("↻")
        btn_dest_refresh.setFixedWidth(32)
        btn_dest_refresh.clicked.connect(self._populate_new_targets)
        dest_layout.addWidget(btn_dest_refresh)
        self._new_name_edit = QLineEdit()
        self._new_name_edit.setPlaceholderText("nom de la nouvelle couche")
        self._new_name_edit.setText(self._CAVITES_LAYER_NAME)
        dest_layout.addWidget(self._new_name_edit, 1)
        layout.addWidget(dest_group)

        # Queue counter
        self._lbl_queue = QLabel("File d'attente : 0 point(s)")
        self._lbl_queue.setStyleSheet("font-weight: bold; color: #555;")
        layout.addWidget(self._lbl_queue)

        self._populate_new_targets()

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

        btn_gpx = QPushButton("🛰 Exporter en GPX")
        btn_gpx.setToolTip("Waypoints GPS (WGS84) à recharger sur un GPS de terrain")
        btn_gpx.clicked.connect(self._export_gpx)
        btn_row.addWidget(btn_gpx)

        btn_zip = QPushButton("🗜 Exporter en ZIP")
        btn_zip.setToolTip("Archive unique : CSV + photos (portable)")
        btn_zip.clicked.connect(self._export_zip)
        btn_row.addWidget(btn_zip)

        layout.addLayout(btn_row)
        return tab

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

        version = QLabel("Version 1.4  —  Plugin QGIS de saisie de phénomènes karstiques")
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

        # Couche de destination (optionnel) — par défaut « Inventaire Traçages ».
        tdest_group = QGroupBox("Couche de destination (optionnel)")
        tdest_layout = QHBoxLayout(tdest_group)
        tdest_layout.addWidget(QLabel("Couche :"))
        self._tr_target_combo = QComboBox()
        self._tr_target_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._tr_target_combo.currentIndexChanged.connect(self._tr_target_changed)
        tdest_layout.addWidget(self._tr_target_combo, 1)
        btn_tdest_refresh = QPushButton("↻")
        btn_tdest_refresh.setFixedWidth(32)
        btn_tdest_refresh.clicked.connect(self._populate_tr_targets)
        tdest_layout.addWidget(btn_tdest_refresh)
        self._tr_name_edit = QLineEdit()
        self._tr_name_edit.setPlaceholderText("nom de la nouvelle couche")
        self._tr_name_edit.setText(self._TRACAGES_LAYER_NAME)
        tdest_layout.addWidget(self._tr_name_edit, 1)
        layout.addWidget(tdest_group)

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
        self._populate_tr_targets()
        return tab

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

    def _build_stats_tab(self):
        """Onglet Stats : agrégats par commune (nombre, types, développement)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        intro = QLabel("Statistiques de l'inventaire, regroupées par commune.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Couche :"))
        self._stats_layer_combo = QComboBox()
        self._stats_layer_combo.setSizePolicy(_SizePolicyExpanding, _SizePolicyPreferred)
        self._stats_layer_combo.currentIndexChanged.connect(self._stats_refresh)
        layer_row.addWidget(self._stats_layer_combo)
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(32)
        btn_refresh.clicked.connect(self._stats_populate_layers)
        layer_row.addWidget(btn_refresh)
        layout.addLayout(layer_row)

        self._stats_table = QTableWidget()
        self._stats_table.setEditTriggers(_NoEditTriggers)
        self._stats_table.setAlternatingRowColors(True)
        self._stats_table.horizontalHeader().setSectionResizeMode(_HeaderStretch)
        layout.addWidget(self._stats_table)

        self._stats_summary = QLabel("")
        self._stats_summary.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._stats_summary)

        btn_export = QPushButton("📤 Exporter le récapitulatif (CSV)")
        btn_export.clicked.connect(self._stats_export_csv)
        layout.addWidget(btn_export)

        self._stats_fill_btn = QPushButton("🏛 Remplir les communes manquantes")
        self._stats_fill_btn.setToolTip(
            "Géocode (geo.api.gouv.fr) les entités sans commune et remplit "
            "commune / code postal / département. Asynchrone, ne touche que les "
            "champs vides.")
        self._stats_fill_btn.clicked.connect(self._stats_fill_communes)
        layout.addWidget(self._stats_fill_btn)

        self._stats_populate_layers()
        return tab

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
            "<b>Colonnes reconnues</b> (toutes optionnelles ; mapping automatique "
            "par synonymes, insensible à la casse/accents/espaces) :<br>"
            "• <b>Position</b> : <code>x</code>/<code>lon</code>/<code>longitude</code> "
            "et <code>y</code>/<code>lat</code>/<code>latitude</code> ; "
            "<code>altitude</code>/<code>alt</code><br>"
            "• <b>Identité</b> : <code>name</code>/<code>nom</code>, <code>type</code>, "
            "<code>reference</code>/<code>numero</code><br>"
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
        self._imp_radio_new      = QRadioButton("Créer une nouvelle couche")
        self._imp_radio_existing = QRadioButton("Importer dans une couche existante")
        self._imp_radio_new.setChecked(True)
        dest_layout.addWidget(self._imp_radio_new)

        # Nom de la nouvelle couche (mode « nouvelle couche »).
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nom de la couche :"))
        self._imp_name_edit = QLineEdit()
        self._imp_name_edit.setText(self._CAVITES_LAYER_NAME)
        self._imp_name_edit.setPlaceholderText(self._CAVITES_LAYER_NAME)
        name_row.addWidget(self._imp_name_edit)
        dest_layout.addLayout(name_row)

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
                             self._imp_name_edit.setEnabled(checked),
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
