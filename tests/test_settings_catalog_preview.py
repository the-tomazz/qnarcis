"""
Offscreen PyQGIS test: v Nastavitvah se ob izbiri drugega profila naloži njegov
katalog slojev (lokalni HTTP strežnik z indeksom in arhivom), brez sprememb
nastavitev in nameščenega kataloga. Preveri tudi statusna sporočila ob napakah.
"""

import functools
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]

from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtWidgets import QComboBox


def load_package():
    name = '_qnarcis_profile_tests'
    if name in sys.modules:
        return name
    spec = importlib.util.spec_from_file_location(
        name, str(ROOT / '__init__.py'), submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return name


def write_catalog(folder, layer_title):
    os.makedirs(os.path.join(folder, 'qml'), exist_ok=True)
    with open(os.path.join(folder, 'vsi_sloji.xml'), 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<qgis><layer-tree-group>'
                f'<layer-tree-layer name="{layer_title}" id="L1" source="type=xyz&amp;'
                'url=https://tile.example.org/%7Bz%7D/%7Bx%7D/%7By%7D.png" '
                'providerKey="wms"/></layer-tree-group></qgis>\n')
    with open(os.path.join(folder, 'additional_layer_data.json'), 'w', encoding='utf-8') as f:
        json.dump({}, f)


def tree_titles(model):
    titles = []

    def walk(item):
        for r in range(item.rowCount()):
            child = item.child(r, 0)
            if child.text():
                titles.append(child.text())
            walk(child)
    walk(model.invisibleRootItem())
    return titles


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    requests_seen = []

    def _maintenance(self, body):
        data = b'<!DOCTYPE html><html><body>Vzdrzevanje</body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def do_HEAD(self):
        self.requests_seen.append(('HEAD', self.path))
        if self.path.startswith('/maint'):
            return self._maintenance(False)
        if self.path.startswith('/hang'):
            time.sleep(4)
        return super().do_HEAD()

    def do_GET(self):
        self.requests_seen.append(('GET', self.path))
        if self.path.startswith('/maint'):
            return self._maintenance(True)
        if self.path.startswith('/hang'):
            time.sleep(4)
        if self.path.startswith('/slow'):
            time.sleep(2)  # počasen strežnik
            self.path = '/index.json'
        return super().do_GET()


class SettingsCatalogPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='qnarcis-preview-')
        base = cls.tmp.name
        for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'QGIS_CUSTOM_CONFIG_PATH'):
            os.environ[key] = base
        cls.app = QgsApplication([], True, base)
        cls.app.initQgis()
        package = load_package()
        cls.m = __import__(package + '.q_narcis', fromlist=['x'])
        cls.cprof = __import__(package + '.country_profile', fromlist=['x'])

        # strežnik: index.json, MK_5.tar.xz (veljaven), maintenance.tar.xz (HTML)
        cls.www = os.path.join(base, 'www')
        os.makedirs(cls.www)
        src = os.path.join(base, 'mk_catalog')
        write_catalog(src, 'MK podlaga')
        with tarfile.open(os.path.join(cls.www, 'MK_5.tar.xz'), 'w:xz') as tar:
            for item in ('vsi_sloji.xml', 'additional_layer_data.json', 'qml'):
                tar.add(os.path.join(src, item), arcname=item)
        with open(os.path.join(cls.www, 'maintenance.tar.xz'), 'w') as f:
            f.write('<!DOCTYPE html><html><body>Vzdrževanje</body></html>')
        handler = functools.partial(_QuietHandler, directory=cls.www)
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.base_url = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        for name, archive in (('index.json', 'MK_5.tar.xz'), ('index_html.json', 'maintenance.tar.xz'),
                              ('index_maint.json', 'maint.tar.xz'), ('index_hang.json', 'hang.tar.xz')):
            with open(os.path.join(cls.www, name), 'w') as f:
                json.dump({'items': [
                    {'id': 5, 'drzava': 'MK', 'povezava': f'{cls.base_url}/{archive}'},
                    {'id': 9, 'drzava': 'SI', 'povezava': f'{cls.base_url}/none.tar.xz'},
                ]}, f)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.app.exitQgis()
        cls.tmp.cleanup()

    def make_plugin(self):
        plugin_dir = tempfile.mkdtemp(dir=self.tmp.name)
        write_catalog(plugin_dir, 'SI podlaga')  # nameščeni katalog aktivnega profila
        p = self.m.QNarcis.__new__(self.m.QNarcis)
        p.translations = {}
        p.plugin_dir = plugin_dir
        p.iface = type('Iface', (), {'messageBar': lambda self: type(
            'Bar', (), {'pushMessage': lambda *a, **k: None})()})()
        p.profile, _w = self.cprof.load_active(QgsSettings())
        p.profile_id = p.profile.id
        p.catalog_code = p.profile.catalog_code
        p.layers = {}
        p.layers_by_identity = {}
        p.layerTreeIndexByTreeItemId = {}
        p.locked_layers = {}
        p.locked_layers_by_id = {}
        p._xml_group_by_element = {}
        p.default_layer_name = ''
        p.default_layer_names = []
        p.default_layer_enabled = True
        p.deleteLoginConfiguration = lambda: None
        return p

    def wait_loaded(self, dlg):
        deadline = time.time() + 30
        while dlg.catalog_progress.isVisibleTo(dlg) and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.02)

    def restore_settings(self, snapshot):
        s = QgsSettings()
        for key in s.allKeys():
            if key not in snapshot:
                s.remove(key)
        for key, value in snapshot.items():
            s.setValue(key, value)

    def run_preview(self, index_url, switch_back_early=False, prepare=None, local_file=None, accept=False):
        """
        Odpre Nastavitve, izbere MK z danim indeksom, počaka na rezultat in prekliče.
        local_file: nato še »Naloži katalog iz datoteke…« s to datoteko;
        accept: potrdi z OK (nastavitve se po testu obnovijo z restore_settings).
        """
        observed = {}
        settings_before = {k: QgsSettings().value(k) for k in QgsSettings().allKeys()}
        observed['settings_snapshot'] = settings_before
        plugin = self.make_plugin()
        if prepare:
            prepare(plugin.plugin_dir)

        def fake_exec(dlg):
            if isinstance(dlg, self.m.QMessageBox):
                return 0
            combo = dlg.findChildren(QComboBox)[0]
            combo._profile_override_drafts['arso-narcis-mk'] = {
                'services.catalog.index_url': index_url}
            combo.setCurrentIndex(combo.findData('arso-narcis-mk'))
            observed['loading'] = dlg.catalog_progress.isVisibleTo(dlg)
            observed['combo_enabled'] = combo.isEnabled()
            if switch_back_early:
                combo.setCurrentIndex(combo.findData('arso-narcis-si'))
                observed['early_titles'] = tree_titles(dlg.tree.data_model)
                observed['early_progress'] = dlg.catalog_progress.isVisibleTo(dlg)
                deadline = time.time() + 10
                while dlg._catalog_task_running and time.time() < deadline:
                    self.app.processEvents()
                    time.sleep(0.02)
            self.wait_loaded(dlg)
            observed['status'] = dlg.catalog_status.text()
            observed['tree_enabled'] = dlg.tree.isEnabled()
            observed['tree_titles'] = tree_titles(dlg.tree.data_model)
            if local_file is not None:
                original_open = self.m.QFileDialog.getOpenFileName
                self.m.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (local_file, ''))
                try:
                    dlg.local_catalog_button.click()
                finally:
                    self.m.QFileDialog.getOpenFileName = original_open
                observed['local_loading'] = dlg.catalog_progress.isVisibleTo(dlg)
                self.wait_loaded(dlg)
                observed['local_status'] = dlg.catalog_status.text()
                observed['local_tree_enabled'] = dlg.tree.isEnabled()
                observed['local_tree_titles'] = tree_titles(dlg.tree.data_model)
                observed['local_catalog'] = dlg._local_catalog
                if accept:
                    return 1
            # nazaj na aktivni profil: drevo spet prikazuje nameščeni katalog
            combo.setCurrentIndex(combo.findData('arso-narcis-si'))
            observed['back_titles'] = tree_titles(dlg.tree.data_model)
            observed['back_status_visible'] = dlg.catalog_status.isVisibleTo(dlg)
            return 0  # preklic: nič se ne shrani

        original = self.m._exec_dialog
        self.m._exec_dialog = fake_exec
        try:
            plugin.runSettings()
        finally:
            self.m._exec_dialog = original
        observed['accepted'] = accept
        observed['settings_unchanged'] = settings_before == {
            k: QgsSettings().value(k) for k in QgsSettings().allKeys()}
        observed['plugin_dir'] = plugin.plugin_dir
        return observed

    def assert_common(self, observed):
        self.assertTrue(observed['loading'])
        self.assertTrue(observed['combo_enabled'])  # med nalaganjem se lahko premisli
        self.assertEqual(observed['back_titles'], ['SI podlaga'])
        self.assertFalse(observed['back_status_visible'])
        self.assertTrue(observed['settings_unchanged'])
        with open(os.path.join(observed['plugin_dir'], 'vsi_sloji.xml'), encoding='utf-8') as f:
            self.assertIn('SI podlaga', f.read())  # nameščeni katalog nedotaknjen

    def test_other_profile_catalog_is_shown(self):
        observed = self.run_preview(self.base_url + '/index.json')
        self.assert_common(observed)
        self.assertEqual(observed['tree_titles'], ['MK podlaga'])
        self.assertTrue(observed['tree_enabled'])
        self.assertIn('Prikazan je katalog slojev izbranega profila (MK)', observed['status'])

    def test_maintenance_page_instead_of_archive(self):
        observed = self.run_preview(self.base_url + '/index_html.json')
        self.assert_common(observed)
        self.assertEqual(observed['tree_titles'], [])
        self.assertFalse(observed['tree_enabled'])
        self.assertIn('ni mogoče naložiti', observed['status'])
        self.assertIn('vzdrževanju', observed['status'])
        self.assertIn('privzeti sloji profila', observed['status'])

    def test_unreachable_index(self):
        observed = self.run_preview(self.base_url + '/missing.json')
        self.assert_common(observed)
        self.assertFalse(observed['tree_enabled'])
        self.assertIn('Napaka pri povezavi na', observed['status'])

    def test_catalog_code_missing_from_index(self):
        with open(os.path.join(self.www, 'index_xk.json'), 'w') as f:
            json.dump({'items': [{'id': 1, 'drzava': 'SI', 'povezava': 'x'}]}, f)
        observed = self.run_preview(self.base_url + '/index_xk.json')
        self.assert_common(observed)
        self.assertIn('ne vsebuje kataloga »MK«', observed['status'])

    def test_switch_back_while_loading(self):
        observed = self.run_preview(self.base_url + '/slow.json', switch_back_early=True)
        self.assertTrue(observed['loading'])
        self.assertEqual(observed['early_titles'], ['SI podlaga'])
        self.assertFalse(observed['early_progress'])
        # pozen rezultat MK ne povozi prikaza aktivnega profila
        self.assertEqual(observed['tree_titles'], ['SI podlaga'])
        self.assertTrue(observed['settings_unchanged'])

    def test_timeout_shows_message(self):
        original = self.m.CATALOG_PREVIEW_TIMEOUT_MS
        self.m.CATALOG_PREVIEW_TIMEOUT_MS = 300
        try:
            # unikaten URL, da ga ne postreže omrežni predpomnilnik QGIS-a
            observed = self.run_preview(self.base_url + '/slow.json?timeout')
        finally:
            self.m.CATALOG_PREVIEW_TIMEOUT_MS = original
        self.assertTrue(observed['combo_enabled'])
        self.assertIn('Strežnik se ne odziva', observed['status'])
        self.assertFalse(observed['tree_enabled'])
        self.assertTrue(observed['settings_unchanged'])

    def test_maintenance_detected_without_download(self):
        _QuietHandler.requests_seen.clear()
        started = time.time()
        observed = self.run_preview(self.base_url + '/index_maint.json')
        self.assertLess(time.time() - started, 5)
        self.assertIn('vzdrževanju', observed['status'])
        self.assertIn(('HEAD', '/maint.tar.xz'), _QuietHandler.requests_seen)
        self.assertNotIn(('GET', '/maint.tar.xz'), _QuietHandler.requests_seen)

    def test_unresponsive_archive_link_fails_fast(self):
        original = self.m.CATALOG_PREVIEW_CHECK_TIMEOUT
        self.m.CATALOG_PREVIEW_CHECK_TIMEOUT = 1
        try:
            started = time.time()
            observed = self.run_preview(self.base_url + '/index_hang.json')
            elapsed = time.time() - started
        finally:
            self.m.CATALOG_PREVIEW_CHECK_TIMEOUT = original
        self.assertLess(elapsed, 3.5)
        self.assertIn('ni bilo mogoče prenesti', observed['status'])

    def test_manually_copied_archive_with_legacy_name(self):
        """Uporabnik arhiv ročno skopira v _cached_qgz pod imenom iz starejših izdaj."""
        def copy_archive(plugin_dir):
            os.makedirs(os.path.join(plugin_dir, '_cached_qgz'))
            shutil.copy(os.path.join(self.www, 'MK_5.tar.xz'),
                        os.path.join(plugin_dir, '_cached_qgz', 'MK_5.tar.xz'))
        _QuietHandler.requests_seen.clear()
        observed = self.run_preview(self.base_url + '/index_maint.json?legacy', prepare=copy_archive)
        self.assertEqual(observed['tree_titles'], ['MK podlaga'])
        self.assertNotIn(('HEAD', '/maint.tar.xz'), _QuietHandler.requests_seen)
        self.assertNotIn(('GET', '/maint.tar.xz'), _QuietHandler.requests_seen)
        self.assertTrue(os.path.exists(
            os.path.join(observed['plugin_dir'], '_cached_qgz', 'MK_5.tar.xz')))

    # --- Naloži katalog iz datoteke… -------------------------------------

    def local_archive(self):
        """Arhiv zunaj mape vtičnika (npr. v Prenosih)."""
        folder = tempfile.mkdtemp(dir=self.tmp.name)
        path = os.path.join(folder, 'narcis_mk.tar.xz')
        shutil.copy(os.path.join(self.www, 'MK_5.tar.xz'), path)
        return path

    def test_local_file_when_archive_link_is_broken(self):
        """Indeks dela, povezava do arhiva ne: arhiv se ob OK shrani v predpomnilnik kot katalog 5."""
        archive = self.local_archive()
        observed = self.run_preview(self.base_url + '/index_maint.json?local', local_file=archive, accept=True)
        try:
            self.assertIn('vzdrževanju', observed['status'])  # najprej strežnik ne deluje
            self.assertTrue(observed['local_loading'])
            self.assertEqual(observed['local_tree_titles'], ['MK podlaga'])
            self.assertTrue(observed['local_tree_enabled'])
            self.assertIn('narcis_mk.tar.xz', observed['local_status'])
            self.assertNotIn('ni dosegljiv', observed['local_status'])
            self.assertEqual(observed['local_catalog']['id'], '5')

            plugin_dir = observed['plugin_dir']
            s = QgsSettings()
            self.assertEqual(s.value('q_narcis/active_profile'), 'arso-narcis-mk')
            self.assertIsNone(s.value(self.cprof.profile_settings_key('arso-narcis-mk', 'qgz_version')))
            cached = os.listdir(os.path.join(plugin_dir, '_cached_qgz'))
            self.assertEqual([n for n in cached if n.startswith('arso-narcis-mk__MK_5_')], [n for n in cached
                             if n.endswith('.tar.xz') and n.startswith('arso-narcis-mk__')])
            self.assertTrue(any(n.startswith('arso-narcis-mk__MK_5_') for n in cached))
            with open(os.path.join(plugin_dir, 'vsi_sloji.xml'), encoding='utf-8') as f:
                self.assertIn('SI podlaga', f.read())  # namesti se šele ob zagonu
            self.assertTrue(os.path.exists(archive))  # izvirna datoteka ostane

            # zagon QGIS-a: običajna namestitev (kot onFinishedCheckForQgzVersion) brez prenosa
            _QuietHandler.requests_seen.clear()
            result = self.m.installQgz('5', self.base_url + '/maint.tar.xz', plugin_dir, 'MK', None, '',
                                       self.cprof.profile_settings_key('arso-narcis-mk', ''), 'arso-narcis-mk__')
            self.assertNotIn('error', result)
            self.assertEqual(_QuietHandler.requests_seen, [])
            with open(os.path.join(plugin_dir, 'vsi_sloji.xml'), encoding='utf-8') as f:
                self.assertIn('MK podlaga', f.read())
            self.assertEqual(s.value(self.cprof.profile_settings_key('arso-narcis-mk', 'qgz_version')), '5')
        finally:
            self.restore_settings(observed['settings_snapshot'])

    def test_local_file_when_index_is_unreachable(self):
        """Indeks ni dosegljiv: katalog se ob OK namesti takoj, brez številke različice."""
        archive = self.local_archive()
        observed = self.run_preview(self.base_url + '/missing.json?local', local_file=archive, accept=True)
        try:
            self.assertEqual(observed['local_tree_titles'], ['MK podlaga'])
            self.assertIn('Indeks katalogov ni dosegljiv', observed['local_status'])
            self.assertIsNone(observed['local_catalog']['id'])
            plugin_dir = observed['plugin_dir']
            with open(os.path.join(plugin_dir, 'vsi_sloji.xml'), encoding='utf-8') as f:
                self.assertIn('MK podlaga', f.read())
            self.assertIsNone(QgsSettings().value(
                self.cprof.profile_settings_key('arso-narcis-mk', 'qgz_version')))
            self.assertEqual(QgsSettings().value('q_narcis/active_profile'), 'arso-narcis-mk')
        finally:
            self.restore_settings(observed['settings_snapshot'])

    def test_invalid_local_file(self):
        bad = os.path.join(tempfile.mkdtemp(dir=self.tmp.name), 'stran.tar.xz')
        shutil.copy(os.path.join(self.www, 'maintenance.tar.xz'), bad)
        observed = self.run_preview(self.base_url + '/missing.json?bad', local_file=bad)
        self.assertIn('ni veljaven arhiv', observed['local_status'])
        self.assertFalse(observed['local_tree_enabled'])
        self.assertIsNone(observed['local_catalog'])
        self.assertTrue(observed['settings_unchanged'])
        self.assertTrue(os.path.exists(bad))  # uporabnikove datoteke ne brišemo
        with open(os.path.join(observed['plugin_dir'], 'vsi_sloji.xml'), encoding='utf-8') as f:
            self.assertIn('SI podlaga', f.read())

    def test_local_file_cancelled_changes_nothing(self):
        observed = self.run_preview(self.base_url + '/index_maint.json?cancel', local_file=self.local_archive())
        self.assertEqual(observed['local_tree_titles'], ['MK podlaga'])
        self.assertTrue(observed['settings_unchanged'])
        cache = os.path.join(observed['plugin_dir'], '_cached_qgz')
        self.assertFalse(os.path.exists(cache) and any(
            n.startswith('arso-narcis-mk__') for n in os.listdir(cache)))

    # --- obstoječa namestitev s strežnika (regresija) --------------------

    def test_server_install_downloads_caches_and_reuses(self):
        plugin_dir = tempfile.mkdtemp(dir=self.tmp.name)
        write_catalog(plugin_dir, 'SI podlaga')
        prefix = self.cprof.profile_settings_key('arso-narcis-mk', '')
        snapshot = {k: QgsSettings().value(k) for k in QgsSettings().allKeys()}
        url = self.base_url + '/MK_5.tar.xz?server'
        try:
            _QuietHandler.requests_seen.clear()
            result = self.m.installQgz(5, url, plugin_dir, 'MK', None, '', prefix, 'arso-narcis-mk__')
            self.assertEqual(result['id'], '5')
            self.assertIn(('GET', '/MK_5.tar.xz?server'), _QuietHandler.requests_seen)
            with open(os.path.join(plugin_dir, 'vsi_sloji.xml'), encoding='utf-8') as f:
                self.assertIn('MK podlaga', f.read())
            self.assertEqual(QgsSettings().value(prefix + 'qgz_version'), '5')
            cached = os.listdir(os.path.join(plugin_dir, '_cached_qgz'))
            self.assertEqual(len(cached), 1)
            self.assertTrue(cached[0].startswith('arso-narcis-mk__MK_5_'))

            write_catalog(plugin_dir, 'SI podlaga')  # npr. po menjavi profila
            _QuietHandler.requests_seen.clear()
            result = self.m.installQgz(5, url, plugin_dir, 'MK', None, '', prefix, 'arso-narcis-mk__')
            self.assertNotIn('error', result)
            self.assertEqual(_QuietHandler.requests_seen, [])  # iz predpomnilnika
            with open(os.path.join(plugin_dir, 'vsi_sloji.xml'), encoding='utf-8') as f:
                self.assertIn('MK podlaga', f.read())
        finally:
            self.restore_settings(snapshot)

    def test_server_maintenance_page_keeps_installed_catalog(self):
        plugin_dir = tempfile.mkdtemp(dir=self.tmp.name)
        write_catalog(plugin_dir, 'SI podlaga')
        snapshot = {k: QgsSettings().value(k) for k in QgsSettings().allKeys()}
        try:
            result = self.m.installQgz(7, self.base_url + '/maintenance.tar.xz?server', plugin_dir, 'MK',
                                       None, '', self.cprof.profile_settings_key('arso-narcis-mk', ''),
                                       'arso-narcis-mk__')
            self.assertEqual(result['error'], self.m.CATALOG_ERR_HTML)
            with open(os.path.join(plugin_dir, 'vsi_sloji.xml'), encoding='utf-8') as f:
                self.assertIn('SI podlaga', f.read())
            self.assertEqual([n for n in os.listdir(os.path.join(plugin_dir, '_cached_qgz'))
                              if n.endswith('.tar.xz')], [])
        finally:
            self.restore_settings(snapshot)

    def test_invalid_id_without_local_archive_is_rejected(self):
        plugin_dir = tempfile.mkdtemp(dir=self.tmp.name)
        result = self.m.installQgz(None, self.base_url + '/MK_5.tar.xz', plugin_dir, 'MK', None, '', None)
        self.assertIn('error', result)
        self.assertFalse(os.path.exists(os.path.join(plugin_dir, 'vsi_sloji.xml')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
