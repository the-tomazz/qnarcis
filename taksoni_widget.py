from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QLineEdit, QTreeWidget, 
                                QTreeWidgetItem, QProgressBar)
from qgis.PyQt.QtCore import Qt
from qgis.utils import iface
from qgis.core import (QgsRasterLayer, QgsVectorLayer, QgsProject, 
                      QgsApplication, QgsTask, QgsLayerTreeGroup, Qgis)
from .qgs_requests import requests
import json
import os
import hashlib

import time
from urllib.parse import quote

from qgis.PyQt.QtCore import pyqtSignal

from .gsrv_utils import findGeoserverAuthConfig, build_url_with_params

_QT_USER_ROLE = getattr(Qt, "UserRole", None)
if _QT_USER_ROLE is None:
    _QT_USER_ROLE = Qt.ItemDataRole.UserRole

def _to_message_level(level):
    if not isinstance(level, int):
        return level

    success = getattr(Qgis, "Success", getattr(Qgis, "Info", 0))
    warning = getattr(Qgis, "Warning", getattr(Qgis, "Info", 0))
    critical = getattr(Qgis, "Critical", warning)

    return {
        0: success,
        1: warning,
        2: critical,
    }.get(level, getattr(Qgis, "Info", 0))

def _push_message(title, text, level=0, duration=3):
    msgbar = iface.messageBar()
    level_value = _to_message_level(level)
    full_text = f"{title}: {text}" if title else str(text)

    try:
        msgbar.pushMessage(title, text, level=level_value, duration=duration)
        return
    except TypeError:
        pass

    try:
        msgbar.pushMessage(title, text, level_value, duration)
        return
    except TypeError:
        pass

    try:
        msgbar.pushMessage(full_text, level=level_value, duration=duration)
        return
    except TypeError:
        pass

    try:
        msgbar.pushMessage(full_text, level_value, duration)
        return
    except TypeError:
        pass

    msgbar.pushMessage(full_text)

class DownloadTask(QgsTask):
    """QgsTask for downloading JSON data in the background"""
    def __init__(self, json_file):
        super().__init__("", QgsTask.CanCancel | QgsTask.Silent | QgsTask.Hidden)
        self.json_file = json_file
        self.url = "https://narcis.gov.si/ords/narcis/hr/katalog-vrst-v4"
        self.exception = None
        self.new_data = None
        self.changed = False

    def run(self):
        try:
            # Download new data
            response = requests.get(self.url)
            response.raise_for_status()
            self.new_data = response.json()
            
            # Check if file exists and compare content
            file_exists = os.path.exists(self.json_file)
            self.changed = True
            
            if file_exists:
                # Calculate hash of existing file
                with open(self.json_file, 'r', encoding='utf-8') as f:
                    existing_content = json.dumps(json.load(f), sort_keys=True).encode('utf-8')
                    existing_hash = hashlib.md5(existing_content, usedforsecurity=False).hexdigest()
                
                # Calculate hash of new data
                new_content = json.dumps(self.new_data, sort_keys=True).encode('utf-8')
                new_hash = hashlib.md5(new_content, usedforsecurity=False).hexdigest()
                
                if existing_hash == new_hash:
                    self.changed = False
            
            # Save new data if changed
            if self.changed:
                with open(self.json_file, 'w', encoding='utf-8') as f:
                    json.dump(self.new_data, f, ensure_ascii=False, indent=2)
            
            return True
            
        except Exception as e:
            self.exception = e
            return False

    def finished(self, result):
        if result:
            if self.changed:
                _push_message("QNarcIS", "Katalog vrst je uspešno naložen.", level=0, duration=3)
        else:
            _push_message("Napaka", f"Neuspešno pridobivanje kataloga vrst: {str(self.exception)}", level=2, duration=5)

class Taksoni(QWidget):
    loginRequested = pyqtSignal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.json_file = os.path.join(os.path.dirname(__file__), "katalog-vrst-v4.json")
        self.data = None
        self.current_filter = ""
        self.loading_item = None
        self.loading_bar = None
        self.setup_ui()
        
        # Start with cached data if available
        if os.path.exists(self.json_file):
            try:
                with open(self.json_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                    self.populate_tree(self.current_filter)
            except Exception as e:
                _push_message("Napaka", f"Napaka pri nalaganju cache kataloga vrst: {e}", level=1, duration=5)
        
        # Start background download
        self.start_download()

    def setup_ui(self):
        # Layout
        vbox = QVBoxLayout()
        self.setLayout(vbox)

        # Search bar
        self.searchbar = QLineEdit()
        self.searchbar.setClearButtonEnabled(True)
        self.searchbar.setPlaceholderText("Iskanje po taksonih...")
        vbox.addWidget(self.searchbar)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Ime", "Rang"])
        self.tree.setColumnWidth(0, 400)
        vbox.addWidget(self.tree)

        # Connect signals
        self.searchbar.textChanged.connect(self.on_search)
        self.tree.setToolTip("Dvakrat klikni na ime vrste, če želiš dodati WMS sloje z lokacijami.")
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)

    def start_download(self):
        """Start background download of JSON data using QgsTask"""
        if not os.path.exists(self.json_file):
            self.show_busy_row()  # show spinner immediately
        task = DownloadTask(self.json_file)
        task.taskCompleted.connect(lambda: self.on_download_complete(task.new_data, task.changed))
        task.taskTerminated.connect(self.on_download_failed)
        QgsApplication.taskManager().addTask(task)

    def on_download_complete(self, new_data, changed):
        """Handle completion of background download"""
        self.hide_busy_row()  # remove spinner
        if new_data:
            self.data = new_data
            if changed:
                self.populate_tree(self.current_filter)

    def on_download_failed(self):
        self.hide_busy_row()
        if not self.data:
            _push_message(
                "QNarcIS",
                "Katalog vrst ni na voljo (ni povezave in ni lokalnega predpomnilnika), zato je seznam prazen.",
                level=1,
                duration=8,
            )

    def populate_tree(self, filter_text=None):
        """Populate the tree with all data or filtered data"""
        self.tree.clear()
        
        if not self.data:
            return
            
        if filter_text and len(filter_text) >= 2:
            filter_text_lower = filter_text.lower()
            matches = []
            self.find_matches(self.data, filter_text_lower, matches)
            
            for match in matches:
                self.add_item(None, match, filter_text_lower)
        else:
            for node in self.data:
                self.add_item(None, node)

    def on_search(self, text):
        self.current_filter = text
        self.populate_tree(text)

    def find_matches(self, nodes, search_text, matches):
        for node in nodes:
            title = node.get('title', '').lower()
            slovenskoimetax = node.get('slovenskoimetax', '').lower()

            if search_text in title or search_text in slovenskoimetax:
                matches.append(node)
            if 'children' in node:
                self.find_matches(node['children'], search_text, matches)

    def add_item(self, parent, node, search_text=""):
        item = QTreeWidgetItem()
        
        if 'key' in node:
            item.setData(0, _QT_USER_ROLE, node['key'])
        
        title = node.get('title', '')
        slovenskoimetax = node.get('slovenskoimetax', '')
        
        display_text = title
        if slovenskoimetax:
            display_text = f"{title} | {slovenskoimetax}"
        
        item.setText(0, display_text)
        item.setText(1, node.get('rang', ''))

        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)

        for child in node.get('children', []):
            self.add_item(item, child, search_text)

        return item

    def on_item_double_clicked(self, item):
        keys = self.get_item_keys(item)
        if not keys:
            _push_message("QNarcIS", "Za izbrano vrsto ne najdem identifikatorja.", level=1, duration=5)
            return

        self.loginRequested.emit(True)
        
        layer_base_name = item.text(0).split(" | ")[0]

        # Find/create top-level group
        root = QgsProject.instance().layerTreeRoot()
        parent_group = root.findGroup("Podatki o naravi")
        if parent_group is None:
            parent_group = QgsLayerTreeGroup("Podatki o naravi")
            root.insertChildNode(0, parent_group)

        # Find/create subgroup
        subgroup = parent_group.findGroup("Vrste")
        if subgroup is None:
            subgroup = parent_group.addGroup("Vrste")
        
        # Create species group
        species_group_name = f"{layer_base_name} [{len(keys)} vrst]"
        species_group = subgroup.findGroup(species_group_name)
        if species_group is None:
            species_group = subgroup.addGroup(species_group_name)

        # Now add all layers
        layers = {
            "SI.CKFF:NARCIS_TAX_LOK_POLIGONI": "poligoni",
            "SI.CKFF:NARCIS_TAX_LOK_LINIJE": "linije",
            "SI.CKFF:NARCIS_TAX_LOK_TOCKE": "tocke"
        }

        service = 'WMS'
        MAX_URL_LENGTH = 8000  # GET request limit

        for gsrv_layer_name in layers:
            layer_type = layers[gsrv_layer_name]
            
            # Try to create URI with all keys first
            wms_uri, locked = self.create_ows_uri(
                service=service, 
                layer_name=gsrv_layer_name, 
                keys=keys
            )
            
            # Check if URI is too long
            if len(wms_uri) <= MAX_URL_LENGTH:
                # URL fits - add single layer directly to species group
                full_layer_name = f"{layer_base_name} ({layer_type})"
                if locked:
                    full_layer_name = full_layer_name + ' 🔒'
                self.add_ows_layer(service, wms_uri, full_layer_name, species_group)
            else:
                # URL too long - need to split
                # Create geometry type subgroup
                geom_group_name = f"{layer_type}"
                geom_group = species_group.findGroup(geom_group_name)
                if geom_group is None:
                    geom_group = species_group.addGroup(geom_group_name)
                
                # Binary search for optimal chunk size
                chunk_size = len(keys)
                while chunk_size > 1:
                    # Try with current chunk size
                    test_chunks = [keys[i:i + chunk_size] 
                                for i in range(0, len(keys), chunk_size)]
                    
                    # Test first chunk
                    test_uri, _ = self.create_ows_uri(
                        service=service,
                        layer_name=gsrv_layer_name,
                        keys=test_chunks[0]
                    )
                    
                    if len(test_uri) <= MAX_URL_LENGTH:
                        # This chunk size works
                        break
                    else:
                        # Reduce chunk size
                        chunk_size = chunk_size // 2
                
                # Split into final chunks
                chunks = [keys[i:i + chunk_size] 
                        for i in range(0, len(keys), chunk_size)]
                
                # Update group name with chunk count
                if len(chunks) > 1:
                    geom_group.setName(f"{layer_type} ({len(chunks)} delov)")
                
                # Add each chunk as separate layer
                for i, chunk_keys in enumerate(chunks):
                    chunk_uri, locked = self.create_ows_uri(
                        service=service, 
                        layer_name=gsrv_layer_name, 
                        keys=chunk_keys
                    )
                    
                    if len(chunks) == 1:
                        # Only one chunk needed - add directly to species group
                        full_layer_name = f"{layer_base_name} ({layer_type})"
                    else:
                        # Multiple chunks - simple name
                        full_layer_name = f"Del {i+1}"
                    
                    if locked:
                        full_layer_name = full_layer_name + ' 🔒'
                    
                    self.add_ows_layer(service, chunk_uri, full_layer_name, 
                                    geom_group if len(chunks) > 1 else species_group)
        
        # Expand the species group
        species_group.setExpanded(True)
        
    def get_item_keys(self, item):
        keys = []
        clicked_key = item.data(0, _QT_USER_ROLE)
        if clicked_key:
            keys.append(clicked_key)
        self.collect_child_keys(item, keys)
        return keys

    def collect_child_keys(self, parent_item, keys_list):
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            child_key = child.data(0, _QT_USER_ROLE)
            if child_key:
                keys_list.append(child_key)
            self.collect_child_keys(child, keys_list)

    def create_ows_uri(
        self,
        service,                 # "WMS" or "WFS"
        layer_name,              # WMS: layers=..., WFS: typename=...
        *,
        keys=None,                    # list/iterable for TAX_ID IN (...)
        cql = None,       # explicit CQL_FILTER; overrides keys if both given
        base_url = "https://narcis.gov.si/ows",   # OWS root; we'll add /wms or /wfs
        crs_wms = "EPSG:3857",
        crs_wfs = "EPSG:3794",
        version_wms = "1.3.0",
        version_wfs = "auto",    # or "2.0.0"
        image_format = "image/png",
        transparent = True,
        # WFS provider tuning (match your previous defaults)
        wfs_paging_enabled = "true",
        wfs_prefer_xy_t11 = "false",
        wfs_restrict_to_bbox = "1",
        # WMS tiling hints (keep from your snippet)
        wms_max_width = "256",
        wms_max_height = "256",
        extra_top_level = None,  # add any extra provider params at top level
    ):
        """
        Build a QGIS data source string for WMS or WFS:
        - WMS: unchanged (CQL + cache-buster in inner url, &-joined)
        - WFS: space-separated key='value', CQL in url, restrictToRequestBBOX='0'
        Returns (uri, locked_flag).
        """

        service = service.strip().upper()
        if service not in ("WMS", "WFS"):
            raise ValueError("service must be 'WMS' or 'WFS'")

        # auth profile (your function)
        auth_id, _config_name, user_name = findGeoserverAuthConfig()

        layer_name_postposition = ''
        if (auth_id):
            if 'INTERNA_RABA' in user_name:
                layer_name_postposition = '_INTERNO'
            elif 'STROKOVNA_GEN' in user_name:
                layer_name_postposition = '_STROK'
            elif 'REGISTRIRANA_GEN' in user_name:
                layer_name_postposition = '_REG'

        layer_name = layer_name + layer_name_postposition

        # CQL from keys if given
        if keys and not cql:
            keys_str = ",".join(f"{k}" for k in keys)
            cql = f"TAX_ID IN ({keys_str})"

        # inner base url
        inner_base = f"{base_url.rstrip('/')}/{service.lower()}"

        # put cache-buster INSIDE inner url, along with CQL_FILTER
        # IMPORTANT: QGIS may forward ONLY the first non-standard param from inner url
        # So add CQL_FILTER *first*, then _cb (which doesn’t need to reach the server).
        inner_url = inner_base
        if cql:
            inner_url = build_url_with_params(inner_url, {"CQL_FILTER": cql})  # first
        inner_url = build_url_with_params(inner_url, {"_cb": str(int(time.time()))})  # second

        # top-level provider params
        params = {"url": inner_url}

        if service == "WMS":
            # ----- WMS: UNCHANGED -----
            params.update({
                "IgnoreGetMapUrl": "1",
                "layers": layer_name,
                "styles": "",
                "format": image_format,
                "transparent": "true" if transparent else "false",
                "crs": crs_wms,
                "version": version_wms,
                "maxWidth": wms_max_width,
                "maxHeight": wms_max_height,
            })
            if extra_top_level:
                params.update({str(k): str(v) for k, v in extra_top_level.items()})
            # authcfg applied after branch
            uri_str = "&".join(f"{k}={v}" for k, v in params.items())
        else:
            # ----- WFS: SPACE-SEP key='value', CQL in url, disable BBOX -----
            wfs_version = "2.0.0" if version_wfs == "auto" else version_wfs

            wfs_base = f"{base_url.rstrip('/')}/wfs"
            if cql:
                wfs_url = f"{wfs_base}?CQL_FILTER={quote(cql, safe='(),= <>:_')}&_cb={int(time.time())}"
            else:
                wfs_url = f"{wfs_base}?_cb={int(time.time())}"

            # Use URN for SRS to match WFS 2.0 style (works with GeoServer)
            if crs_wfs.upper().startswith("EPSG:"):
                srs_urn = f"urn:ogc:def:crs:EPSG::{crs_wfs.split(':', 1)[1]}"
            else:
                srs_urn = crs_wfs

            pieces = [
                f"url='{wfs_url}'",
                f"typename='{layer_name}'",
                f"srsname='{srs_urn}'",
                f"version='{wfs_version}'",
                f"pagingEnabled='{wfs_paging_enabled}'",
                f"preferCoordinatesForWfsT11='{wfs_prefer_xy_t11}'",
                "restrictToRequestBBOX='0'",  # avoid BBOX+CQL conflict
            ]
            if extra_top_level:
                for k, v in extra_top_level.items():
                    pieces.append(f"{k}='{v}'")
            # authcfg appended after branch
            uri_str = " ".join(pieces)

        # Apply authcfg for BOTH services here
        if auth_id:
            if service == "WMS":
                uri_str += f"&authcfg={auth_id}"
            else:
                uri_str += f" authcfg='{auth_id}'"

        return uri_str, bool(layer_name_postposition)

    def add_ows_layer(self, service, uri, layer_name, target_group):
        """
        Add a WMS (raster) or WFS (vector) layer to the project and target group.
        service: "WMS" or "WFS"
        Returns the created QgsMapLayer on success, else None.
        """
        svc = service.strip().upper()
        if svc not in ("WMS", "WFS"):
            raise ValueError("service must be 'WMS' or 'WFS'")

        # Pick the right layer class and provider key
        if svc == "WMS":
            provider_key = "wms"
            layer = QgsRasterLayer(uri, layer_name, provider_key)
        else:  # WFS
            provider_key = "WFS"
            layer = QgsVectorLayer(uri, layer_name, provider_key)

        try:
            if layer.isValid():
                layer.setScaleBasedVisibility(True)
                layer.setMaximumScale(1)
                layer.setMinimumScale(2750000)
                
                QgsProject.instance().addMapLayer(layer, False)
                target_group.addLayer(layer)

                node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
                if node:
                    node.setExpanded(False)

                _push_message(
                    "QNarciIS",
                    f"Sloj '{layer_name}' ({svc}) je bil uspešno dodan.",
                    level=0,
                    duration=3,
                )
                return layer
            else:
                _push_message(
                    "Napaka",
                    f"Napaka pri nalaganju {svc} sloja. Naloženi sloj ni veljaven.",
                    level=2,
                    duration=5,
                )
                return None
        except Exception as e:
            _push_message(
                "Napaka",
                f"Napaka pri nalaganju {svc} sloja: {str(e)}",
                level=2,
                duration=5,
            )
            return None


    def show_busy_row(self):
        """Show an indeterminate progress row at the top of the tree."""
        if self.loading_item is not None:
            return  # already shown

        self.loading_item = QTreeWidgetItem()
        self.loading_item.setFirstColumnSpanned(False)
        self.loading_item.setText(0, "Nalagam katalog vrst …")
        self.loading_item.setText(1, "")  # keep column 1 free for the bar

        # Indeterminate progress bar
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)          # <-- endless/spinning
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setMaximumHeight(12)

        # Insert on top and place the bar in column 1
        self.tree.insertTopLevelItem(0, self.loading_item)
        self.tree.setItemWidget(self.loading_item, 1, self.loading_bar)

    def hide_busy_row(self):
        """Remove the busy row if present."""
        if self.loading_item is None:
            return
        idx = self.tree.indexOfTopLevelItem(self.loading_item)
        if idx != -1:
            self.tree.takeTopLevelItem(idx)
        self.loading_item = None
        self.loading_bar = None
