import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import country_profile as cprof


class DictSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        prefix = key.rstrip('/') + '/'
        for candidate in list(self.values):
            if candidate == key or candidate.startswith(prefix):
                del self.values[candidate]

    def allKeys(self):
        return list(self.values)


def imported_profile():
    return {
        'schema_version': 1,
        'id': 'partner',
        'name': 'Partner',
        'catalog_code': 'MK',
        'base_url': 'https://partner.example.org',
        'services': {
            'catalog': {'index_url': '{base_url}/catalog'},
            'login': {'url': '{base_url}/login'},
        },
    }


class BuiltinProfileTests(unittest.TestCase):
    def setUp(self):
        self.settings = DictSettings()

    def test_builtin_profiles_regression(self):
        expected = {
            'arso-narcis-si': ('SI', 'sl', {
                'catalog', 'login', 'send_polygon', 'send_layer', 'taxa_search',
                'parcels', 'parcel_ownership', 'news', 'help', 'map_template'},
                'Pregledna topografska karta'),
            'arso-narcis-mk': ('MK', 'en', {'catalog', 'help', 'map_template'}, 'OpenStreetMap'),
            'arso-narcis-xk': ('XK', 'en', {'catalog', 'help', 'map_template'}, 'OpenStreetMap'),
        }
        self.assertEqual(list(cprof.builtin_profiles()), list(expected))
        for pid, (code, language, services, first_layer) in expected.items():
            with self.subTest(profile=pid):
                cprof.validate_profile(cprof.load_builtin(pid), allow_builtin=True)
                cprof.set_active_profile_id(pid, self.settings)
                profile, warning = cprof.load_active(self.settings)
                self.assertIsNone(warning)
                self.assertTrue(profile.is_builtin)
                self.assertEqual(profile.id, pid)
                self.assertEqual(profile.catalog_code, code)
                self.assertEqual(profile.language, language)
                self.assertEqual(set(profile.enabled_services()), services)
                self.assertEqual(profile.default_layers()[0][0], first_layer)
                self.assertEqual(profile.service('catalog').url('index_url'),
                                 'https://narcis.gov.si/ords/narcis/hr/qgz')

    def test_default_profile_is_slovenian(self):
        profile, warning = cprof.load_active(self.settings)
        self.assertIsNone(warning)
        self.assertEqual(profile.id, 'arso-narcis-si')

    def test_import_cannot_use_builtin_or_legacy_id(self):
        for pid in list(cprof.BUILTIN_PROFILES) + [cprof.LEGACY_BUILTIN_ID]:
            data = imported_profile()
            data['id'] = pid
            with self.subTest(pid=pid), self.assertRaises(cprof.ProfileError):
                cprof.validate_profile(data)

    def test_builtin_profile_cannot_be_removed(self):
        with self.assertRaises(cprof.ProfileError):
            cprof.remove_profile(cprof.BUILTIN_ID, self.settings)


class LegacyMigrationTests(unittest.TestCase):
    def test_released_country_selection_maps_to_profile(self):
        for code, pid in cprof.LEGACY_COUNTRY_PROFILES.items():
            with self.subTest(code=code):
                settings = DictSettings({
                    'q_narcis/country_code': code,
                    'q_narcis/qgz_version': '42',
                    'q_narcis/default_layer_enabled': True,
                    'q_narcis/default_layer_names': '["OpenStreetMap"]',
                    f'q_narcis/default_extent/{code}': '{"xmin": 1}',
                })
                self.assertEqual(cprof.migrate_legacy_profile_selection(settings), pid)
                self.assertEqual(cprof.active_profile_id(settings), pid)
                key = lambda name: cprof.profile_settings_key(pid, name)
                self.assertEqual(settings.value(key('qgz_version')), '42')
                self.assertEqual(settings.value(key('default_layer_enabled')), True)
                self.assertEqual(settings.value(key('default_layer_names')), '["OpenStreetMap"]')
                self.assertEqual(settings.value(key('default_extent/' + code)), '{"xmin": 1}')
                self.assertEqual(settings.value(cprof.SETTINGS_LAST_PROFILE), pid)
                # stari ključi ostanejo
                self.assertEqual(settings.value('q_narcis/country_code'), code)
                profile, warning = cprof.load_active(settings)
                self.assertIsNone(warning)
                self.assertEqual(profile.catalog_code, code)

    def test_fresh_install_uses_default_profile(self):
        settings = DictSettings()
        self.assertEqual(cprof.migrate_legacy_profile_selection(settings), cprof.BUILTIN_ID)
        self.assertEqual(cprof.active_profile_id(settings), cprof.BUILTIN_ID)

    def test_existing_selection_is_left_alone(self):
        settings = DictSettings({
            cprof.SETTINGS_ACTIVE_PROFILE: 'arso-narcis-xk',
            'q_narcis/country_code': 'MK',
            'q_narcis/qgz_version': '42',
        })
        self.assertEqual(cprof.migrate_legacy_profile_selection(settings), 'arso-narcis-xk')
        self.assertIsNone(settings.value(cprof.profile_settings_key('arso-narcis-xk', 'qgz_version')))

    def test_unreleased_single_builtin_id_is_mapped(self):
        settings = DictSettings({
            cprof.SETTINGS_ACTIVE_PROFILE: cprof.LEGACY_BUILTIN_ID,
            cprof.SETTINGS_LAST_PROFILE: cprof.LEGACY_BUILTIN_ID,
            'q_narcis/country_code': 'MK',
        })
        self.assertEqual(cprof.migrate_legacy_profile_selection(settings), 'arso-narcis-mk')
        self.assertEqual(settings.value(cprof.SETTINGS_LAST_PROFILE), 'arso-narcis-mk')

    def test_migration_does_not_overwrite_profile_data(self):
        pid = 'arso-narcis-mk'
        settings = DictSettings({
            'q_narcis/country_code': 'MK',
            'q_narcis/qgz_version': '42',
            cprof.profile_settings_key(pid, 'qgz_version'): '50',
        })
        cprof.migrate_legacy_profile_selection(settings)
        self.assertEqual(settings.value(cprof.profile_settings_key(pid, 'qgz_version')), '50')


class ImportedProfileTests(unittest.TestCase):
    def setUp(self):
        self.settings = DictSettings()

    def test_overrides_are_isolated_by_profile(self):
        source = imported_profile()
        cprof.store_profile(source, self.settings)
        other = copy.deepcopy(source)
        other.update(id='other', name='Other')
        cprof.store_profile(other, self.settings)
        cprof.store_profile_overrides('partner', {
            'base_url': 'https://edited.example.org',
            'services.login.enabled': False,
        }, self.settings)

        partner = cprof.build_profile('partner', self.settings)
        untouched = cprof.build_profile('other', self.settings)
        self.assertEqual(partner.service('catalog').url('index_url'),
                         'https://edited.example.org/catalog')
        self.assertFalse(partner.enabled('login'))
        self.assertEqual(untouched.service('catalog').url('index_url'),
                         'https://partner.example.org/catalog')
        self.assertTrue(untouched.enabled('login'))

    def test_builtin_overrides_are_isolated(self):
        cprof.store_profile_overrides('arso-narcis-mk', {
            'services.catalog.index_url': 'https://mk.example.org/index.json',
        }, self.settings)
        mk = cprof.build_profile('arso-narcis-mk', self.settings)
        xk = cprof.build_profile('arso-narcis-xk', self.settings)
        self.assertEqual(mk.service('catalog').url('index_url'), 'https://mk.example.org/index.json')
        self.assertEqual(xk.service('catalog').url('index_url'),
                         'https://narcis.gov.si/ords/narcis/hr/qgz')

    def test_load_active_and_preview_use_same_overrides(self):
        cprof.store_profile(imported_profile(), self.settings)
        cprof.set_active_profile_id('partner', self.settings)
        cprof.store_profile_overrides('partner', {
            'services.catalog.index_url': 'https://override.example.org/index',
        }, self.settings)
        active, warning = cprof.load_active(self.settings)
        preview = cprof.build_profile('partner', self.settings)
        self.assertIsNone(warning)
        self.assertEqual(active.service('catalog').url('index_url'),
                         'https://override.example.org/index')
        self.assertEqual(active.service('catalog').url('index_url'),
                         preview.service('catalog').url('index_url'))

    def test_empty_overrides_remove_settings_key(self):
        cprof.store_profile_overrides('partner', {'language': 'mk'}, self.settings)
        key = cprof.profile_override_key('partner')
        self.assertIn(key, self.settings.values)
        cprof.store_profile_overrides('partner', {}, self.settings)
        self.assertNotIn(key, self.settings.values)

    def test_remove_profile_removes_scoped_data(self):
        cprof.store_profile(imported_profile(), self.settings)
        cprof.set_active_profile_id('partner', self.settings)
        cprof.store_profile_overrides('partner', {'language': 'mk'}, self.settings)
        self.settings.setValue(cprof.profile_settings_key('partner', 'qgz_version'), '3')
        cprof.remove_profile('partner', self.settings)
        self.assertFalse(any('/partner/' in key or key.endswith('/partner')
                             for key in self.settings.allKeys()))
        self.assertEqual(cprof.active_profile_id(self.settings), cprof.BUILTIN_ID)

    def test_missing_active_profile_falls_back_to_default(self):
        cprof.set_active_profile_id('gone', self.settings)
        profile, warning = cprof.load_active(self.settings)
        self.assertEqual(profile.id, cprof.BUILTIN_ID)
        self.assertIn('gone', warning)

    def test_validation(self):
        bad = imported_profile()
        bad['services']['catalog']['enabled'] = 'false'
        with self.assertRaises(cprof.ProfileError):
            cprof.validate_profile(bad)
        bad = imported_profile()
        bad['language'] = 1
        with self.assertRaises(cprof.ProfileError):
            cprof.validate_profile(bad)
        bad = imported_profile()
        del bad['catalog_code']
        with self.assertRaisesRegex(cprof.ProfileError, 'catalog_code'):
            cprof.validate_profile(bad)
        bad['services']['catalog']['enabled'] = False
        cprof.validate_profile(bad)  # brez kataloga koda ni potrebna
        old = imported_profile()
        old['countries'] = {'MK': {}}
        self.assertTrue(any('countries' in w for w in cprof.validate_profile(old)))

    def test_legacy_override_remains_supported(self):
        cprof.store_profile(imported_profile(), self.settings)
        self.settings.setValue(cprof.SETTINGS_OVERRIDE_PREFIX + 'services.catalog.index_url',
                               'https://legacy.example.org/index')
        profile = cprof.build_profile('partner', self.settings)
        self.assertEqual(profile.service('catalog').url('index_url'),
                         'https://legacy.example.org/index')

    def test_invalid_persisted_override_falls_back_to_source(self):
        cprof.store_profile(imported_profile(), self.settings)
        cprof.set_active_profile_id('partner', self.settings)
        cprof.store_profile_overrides('partner', {
            'services.catalog.index_url': 'http://insecure.example.org/index',
        }, self.settings)
        profile, warning = cprof.load_active(self.settings)
        self.assertIn('niso veljavne', warning)
        self.assertEqual(profile.service('catalog').url('index_url'),
                         'https://partner.example.org/catalog')

    def test_profile_runtime_keys_are_scoped(self):
        self.assertEqual(cprof.profile_settings_key('arso-narcis-mk', 'qgz_version'),
                         'q_narcis/profile_data/arso-narcis-mk/qgz_version')
        self.assertEqual(cprof.profile_settings_key('partner', 'qgz_version'),
                         'q_narcis/profile_data/partner/qgz_version')

    def test_import_domains(self):
        hosts, password_hosts = cprof.profile_domains(imported_profile())
        self.assertEqual(set(hosts), {'partner.example.org'})
        self.assertEqual(password_hosts, {'partner.example.org'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
