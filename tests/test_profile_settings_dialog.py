"""Offscreen PyQGIS tests for the infrastructure profile editor."""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]

from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import QDialog, QPushButton, QVBoxLayout


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


class DictSettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)

    def allKeys(self):
        return list(self.values)


class ProfileSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile_dir = tempfile.TemporaryDirectory(prefix='qnarcis-profile-ui-')
        for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
                    'QGIS_CUSTOM_CONFIG_PATH'):
            os.environ[key] = cls.profile_dir.name
        cls.app = QgsApplication([], True, cls.profile_dir.name)
        cls.app.initQgis()
        package = load_package()
        cls.cprof = __import__(package + '.country_profile', fromlist=['x'])
        cls.module = __import__(package + '.profile_settings_dialog', fromlist=['x'])
        cls.plugin_module = __import__(package + '.q_narcis', fromlist=['x'])

    @classmethod
    def tearDownClass(cls):
        cls.app.exitQgis()
        cls.profile_dir.cleanup()

    def open_dialog(self, pid='arso-narcis-mk'):
        self.settings = DictSettings()
        self.dialog = self.module.ProfileSettingsDialog(
            None, pid, self.cprof.load_builtin(pid), self.settings, lambda text: text)
        return self.dialog

    def tearDown(self):
        dialog = getattr(self, 'dialog', None)
        if dialog is not None:
            dialog.close()
            sip.delete(dialog)
            self.dialog = None
        self.app.processEvents()

    def _tree_paths(self, level):
        tree = self.dialog._trees[level]
        paths = set()
        for g in range(tree.topLevelItemCount()):
            group = tree.topLevelItem(g)
            for c in range(group.childCount()):
                paths.add(group.child(c).data(0, self.module._USER_ROLE))
        return paths

    def test_no_country_selector_and_two_tabs(self):
        self.open_dialog()
        self.assertFalse(hasattr(self.dialog, 'country_combo'))
        self.assertEqual([self.dialog.tabs.tabText(i) for i in range(self.dialog.tabs.count())],
                         ['Osnovno', 'Napredno'])

    def test_basic_and_advanced_split(self):
        self.open_dialog('arso-narcis-si')
        basic = self._tree_paths(self.module.BASIC)
        advanced = self._tree_paths(self.module.ADVANCED)
        for path in ('catalog_code', 'language', 'base_url',
                     'services.catalog.index_url', 'services.login.url',
                     'services.send_layer.result_url', 'services.news.enabled'):
            self.assertIn(path, basic)
        for path in ('services.login.roles.INTERNA_RABA.suffix',
                     'services.send_layer.fields.layer_name',
                     'services.taxa_search.crs_wfs', 'default_layers',
                     'branding.portal_name'):
            self.assertIn(path, advanced)
        self.assertFalse(basic & advanced)
        tree = self.dialog._trees[self.module.ADVANCED]
        groups = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        self.assertIn('Portal in logotip', groups)
        self.assertNotIn('branding', groups)

    def test_missing_service_can_be_enabled(self):
        self.open_dialog('arso-narcis-mk')
        self.assertIn('services.login.url', self.dialog._items)
        self.assertEqual(self.dialog._items['services.login.enabled'].text(1), 'false')
        self.dialog._items['services.login.url'].setText(1, 'https://mk.example.org/login')
        self.assertTrue(self.dialog._save_button.isEnabled())
        profile = self.cprof.build_profile_from_data(
            self.cprof.load_builtin('arso-narcis-mk'), self.dialog.overrides())
        self.assertTrue(profile.enabled('login'))

    def test_change_is_marked(self):
        self.open_dialog()
        path = 'services.catalog.index_url'
        item = self.dialog._items[path]
        item.setText(1, 'https://mk.example.org/index.json')
        self.assertEqual(item.text(2), 'Spremenjeno')
        self.assertEqual(self.dialog.overrides(), {path: 'https://mk.example.org/index.json'})

    def test_restore_selected_returns_source_value(self):
        self.open_dialog()
        path = 'base_url'
        item = self.dialog._items[path]
        original = item.text(3)
        item.setText(1, 'https://changed.example.org')
        self.dialog.tabs.setCurrentIndex(0)
        self.dialog._trees[self.module.BASIC].setCurrentItem(item)
        self.dialog._reset_selected()
        self.assertEqual(item.text(1), original)
        self.assertNotIn(path, self.dialog.overrides())

    def test_invalid_url_disables_save(self):
        self.open_dialog()
        self.dialog._items['services.catalog.index_url'].setText(1, 'http://insecure.example.org/catalog')
        self.assertFalse(self.dialog._save_button.isEnabled())
        self.assertIn('https://', self.dialog.error_label.text())

    def test_empty_catalog_code_disables_save(self):
        self.open_dialog()
        self.dialog._items['catalog_code'].setText(1, '')
        self.assertFalse(self.dialog._save_button.isEnabled())

    def test_restore_entire_profile(self):
        self.open_dialog()
        self.dialog._items['language'].setText(1, 'mk')
        original_question = self.module.QMessageBox.question
        self.module.QMessageBox.question = lambda *args, **kwargs: self.module._MSG_YES
        try:
            self.dialog._reset_profile()
        finally:
            self.module.QMessageBox.question = original_question
        self.assertEqual(self.dialog.overrides(), {})
        self.assertEqual(self.dialog._items['language'].text(1), 'en')

    def _visible_paths(self):
        paths = set()
        for tree in self.dialog._trees.values():
            for g in range(tree.topLevelItemCount()):
                group = tree.topLevelItem(g)
                for c in range(group.childCount()):
                    child = group.child(c)
                    if not child.isHidden() and not group.isHidden():
                        paths.add(child.data(0, self.module._USER_ROLE))
        return paths

    def test_search_covers_all_columns(self):
        self.open_dialog('arso-narcis-si')
        item = self.dialog._items['services.catalog.index_url']
        source_value = item.text(3)
        item.setText(1, 'https://drugi.example.org/indeks')

        self.dialog.search.setText('drugi.example')  # vrednost
        self.assertEqual(self._visible_paths(), {'services.catalog.index_url'})
        self.assertNotEqual(item.background(1).color(), item.background(0).color())

        self.dialog.search.setText(source_value)  # izvorna vrednost
        self.assertIn('services.catalog.index_url', self._visible_paths())
        self.assertEqual(item.background(3).color(), self.module.ProfileSettingsDialog._HIGHLIGHT)

        self.dialog.search.setText('spremenjeno')  # stanje
        self.assertEqual(self._visible_paths(), {'services.catalog.index_url'})

        self.dialog.search.setText('drugi.example')
        self.assertEqual([self.dialog.tabs.tabText(i) for i in range(2)], ['Osnovno (1)', 'Napredno (0)'])
        self.assertFalse(self.dialog.tabs.tabIcon(0).isNull())

        self.dialog.search.setText('portal in logotip')  # ime skupine
        visible = self._visible_paths()
        self.assertIn('branding.portal_name', visible)
        self.assertTrue(all(path.startswith('branding.') for path in visible))

        self.dialog.search.setText('')
        self.assertIn('base_url', self._visible_paths())
        self.assertEqual([self.dialog.tabs.tabText(i) for i in range(2)], ['Osnovno', 'Napredno'])
        self.assertTrue(self.dialog.tabs.tabIcon(1).isNull())
        self.dialog.only_changed.setChecked(True)  # tudi »samo spremenjene« je filter
        self.assertEqual(self.dialog.tabs.tabText(0), 'Osnovno (1)')
        self.assertEqual(item.background(1).style(), self.module.Qt.BrushStyle.NoBrush
                         if hasattr(self.module.Qt, 'BrushStyle') else self.module.Qt.NoBrush)

    def test_dialog_does_not_write_settings(self):
        self.open_dialog()
        self.dialog._items['language'].setText(1, 'mk')
        self.assertEqual(self.settings.values, {})

    def _plugin(self, active='arso-narcis-si'):
        plugin = self.plugin_module.QNarcis.__new__(self.plugin_module.QNarcis)
        plugin.tr = lambda text: text
        plugin.profile_id = active
        return plugin

    def test_plugin_profile_section_lists_builtin_profiles(self):
        plugin = self._plugin('arso-narcis-mk')
        parent = QDialog()
        layout = QVBoxLayout(parent)
        combo = plugin._build_profile_section(parent, layout)
        labels = [button.text() for button in parent.findChildren(QPushButton)]
        self.assertIn('Nastavitve profila…', labels)
        ids = [combo.itemData(i) for i in range(combo.count())]
        self.assertEqual(ids[:3], ['arso-narcis-si', 'arso-narcis-mk', 'arso-narcis-xk'])
        self.assertEqual(combo.currentData(), 'arso-narcis-mk')
        self.assertEqual(combo._profile_override_drafts, {})
        self.assertEqual(combo._preview_profile('arso-narcis-xk').catalog_code, 'XK')
        remove_button = next(b for b in parent.findChildren(QPushButton) if b.text() == 'Odstrani')
        self.assertFalse(remove_button.isEnabled())
        parent.close()
        sip.delete(parent)

    def test_preview_uses_unsaved_drafts(self):
        plugin = self._plugin()
        parent = QDialog()
        combo = plugin._build_profile_section(parent, QVBoxLayout(parent))
        combo._profile_override_drafts['arso-narcis-mk'] = {
            'services.catalog.index_url': 'https://mk.example.org/index.json'}
        preview = combo._preview_profile('arso-narcis-mk')
        self.assertEqual(preview.service('catalog').url('index_url'),
                         'https://mk.example.org/index.json')
        parent.close()
        sip.delete(parent)

    def test_profile_removal_is_staged_until_main_dialog_accepts(self):
        settings = QgsSettings()
        source = {
            'schema_version': 1, 'id': 'staged-remove', 'name': 'Staged remove',
            'catalog_code': 'MK',
            'services': {'catalog': {'index_url': 'https://example.org/index'}},
        }
        self.cprof.store_profile(source, settings)
        plugin = self._plugin()
        parent = QDialog()
        combo = plugin._build_profile_section(parent, QVBoxLayout(parent))
        combo.setCurrentIndex(combo.findData('staged-remove'))
        remove_button = next(button for button in parent.findChildren(QPushButton)
                             if button.text() == 'Odstrani')
        self.assertTrue(remove_button.isEnabled())
        original_question = self.plugin_module.QMessageBox.question
        yes = getattr(self.plugin_module.QMessageBox, 'Yes', None)
        if yes is None:
            yes = self.plugin_module.QMessageBox.StandardButton.Yes
        self.plugin_module.QMessageBox.question = lambda *args, **kwargs: yes
        try:
            remove_button.click()
        finally:
            self.plugin_module.QMessageBox.question = original_question
        self.assertIn('staged-remove', self.cprof.list_profiles(settings))
        self.assertIn('staged-remove', combo._profiles_to_remove)
        self.assertEqual(combo.findData('staged-remove'), -1)
        parent.close()
        sip.delete(parent)
        self.cprof.remove_profile('staged-remove', settings)

    def test_help_folder_follows_profile_language(self):
        plugin = self._plugin()
        plugin.plugin_dir = tempfile.mkdtemp(dir=self.profile_dir.name)
        for folder in ('si', 'en', 'mk'):
            os.makedirs(os.path.join(plugin.plugin_dir, 'docs', folder))
            open(os.path.join(plugin.plugin_dir, 'docs', folder, 'index.html'), 'w').close()
        plugin.catalog_code = 'MK'
        cases = {'sl': 'si', 'en': 'en', 'mk': 'mk', 'sq': 'en', '../en': 'en', None: 'en'}
        for language, expected in cases.items():
            with self.subTest(language=language):
                data = self.cprof.load_builtin('arso-narcis-mk')
                data['language'] = language
                plugin.profile = self.cprof.Profile(data, True)
                self.assertEqual(plugin._help_lang(), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
