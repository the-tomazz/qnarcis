# -*- coding: utf-8 -*-
"""
Profili infrastrukture za vtičnik QNarcIS.

Profil opisuje, s katero infrastrukturo (katalog, prijava, pošiljanje,
iskalnik …) vtičnik deluje. Vsak profil je samostojna konfiguracija za eno
okolje oziroma skupino uporabnikov (en jezik, en katalog). Vgrajeni profili
(profiles/arso_narcis_*.json) opisujejo današnje ARSO delovanje za SI, MK in
XK. Uporabnik lahko uvozi dodatne profile (*.qnarcis.json), ki se shranijo v
QgsSettings.

Zasnova: _private/profili_infrastrukture.md

Modul namenoma ne uvaža QGIS-a na vrhu, da je preizkusljiv brez QGIS-a.
Funkcije za shranjevanje sprejmejo objekt `settings` (QgsSettings ali
združljiv objekt z value/setValue/remove/allKeys).
"""

import copy
import json
import os
import re
from urllib.parse import urlparse


def _json_or_none(text):
    """JSON iz nastavitev; poškodovana vrednost se preskoči (vrne None)."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None

SCHEMA_VERSION = 1
# Vgrajeni profili (id -> datoteka v mapi profiles/), v vrstnem redu prikaza.
BUILTIN_PROFILES = {
    'arso-narcis-si': 'arso_narcis_si.json',
    'arso-narcis-mk': 'arso_narcis_mk.json',
    'arso-narcis-xk': 'arso_narcis_xk.json',
}
BUILTIN_ID = 'arso-narcis-si'  # privzeti profil
# ID enotnega vgrajenega profila iz neizdane razvojne različice.
LEGACY_BUILTIN_ID = 'arso-narcis'
# Izdane različice (<= 3.4.1) so shranjevale izbrano državo v q_narcis/country_code.
LEGACY_COUNTRY_PROFILES = {
    'SI': 'arso-narcis-si',
    'MK': 'arso-narcis-mk',
    'XK': 'arso-narcis-xk',
}
_RESERVED_IDS = set(BUILTIN_PROFILES) | {LEGACY_BUILTIN_ID}
PROFILE_FILE_SUFFIX = '.qnarcis.json'

SETTINGS_PROFILES_PREFIX = 'q_narcis/profiles/'
SETTINGS_ACTIVE_PROFILE = 'q_narcis/active_profile'
SETTINGS_LAST_PROFILE = 'q_narcis/last_profile_id'
SETTINGS_OVERRIDE_PREFIX = 'q_narcis/override/'
SETTINGS_PROFILE_OVERRIDE_PREFIX = 'q_narcis/profile_overrides/'
SETTINGS_PROFILE_DATA_PREFIX = 'q_narcis/profile_data/'

_PROFILES_DIR = os.path.join(os.path.dirname(__file__), 'profiles')

# Obvezni URL-ji po storitvah. Storitev brez vseh obveznih URL-jev je izklopljena.
SERVICE_REQUIRED_URLS = {
    'catalog': ('index_url',),
    'login': ('url',),
    'send_polygon': ('url',),
    'send_layer': ('url',),
    'taxa_search': ('catalog_url', 'ows_url'),
    'parcels': ('wfs_url',),
    'parcel_ownership': (),
    'news': ('url',),
    'help': (),
    'map_template': (),
}

# Storitve, ki delujejo tudi, če jih profil ne navede (ne potrebujejo strežnika).
SERVICES_ALWAYS_ON = ('help', 'map_template')

# Storitve, ki zahtevajo druge storitve.
SERVICE_DEPENDENCIES = {
    'send_polygon': ('login',),
    'send_layer': ('login',),
    'parcel_ownership': ('parcels', 'login'),
}

# Privzeta (današnja) imena polj. Profil lahko posamezno ime preslika v
# services.<storitev>.fields.<logično ime>.
DEFAULT_FIELDS = {
    'login': {
        'user': 'uporabnik',
        'password': 'key',  # nosec B105 - ime polja v API klicu, ne geslo  # pragma: allowlist secret
        'gsrv_user': 'uporabnik',
        'gsrv_key': 'kljuc',
    },
    'send_polygon': {
        'user': 'uporabnik',
        'password': 'key',  # nosec B105 - ime polja v API klicu, ne geslo  # pragma: allowlist secret
    },
    'send_layer': {
        'user': 'uporabnik',
        'password': 'key',  # nosec B105 - ime polja v API klicu, ne geslo  # pragma: allowlist secret
        'layer_name': 'ime_sloja',
        'note': 'opomba',
        'username': 'uname',
        'bbox': 'bbox',
        'session_id': 'uid',
        'batch_index': 'batch_index',
        'batch_count': 'batch_count',
        'feature_count': 'feature_count',
        'fid_source': 'fid_source',
        'status': 'Status',
        'message': 'Sporocilo',
        'layer_id': 'layer_id',
    },
    'taxa_search': {
        'key': 'key',
        'title': 'title',
        'local_name': 'slovenskoimetax',
        'rank': 'rang',
        'children': 'children',
        'taxon_id': 'TAX_ID',
    },
    'parcels': {
        'ko_id': 'KO_ID',
        'ko_name': 'NAZIV',
        'ko_code': 'SIFKO',
        'parcel_id': 'EID_PARCELA',
        'parcel_number': 'ST_PARCELE',
        'geometry': 'GEOM',
    },
}

# Storitve, ki strežniku pošljejo uporabnikovo geslo.
PASSWORD_SERVICES = ('login', 'send_polygon', 'send_layer')

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
_ALLOWED_LOGO_PREFIXES = ('data:image/png;base64,', 'data:image/svg+xml;base64,')
_KNOWN_TOP_KEYS = {
    'schema_version', 'id', 'name', 'organization', 'profile_version', 'contact',
    'base_url', 'branding', 'services', 'translations', 'default_layers',
    'language', 'catalog_code',
}


class ProfileError(Exception):
    """Neveljaven profil."""


# ---------------------------------------------------------------------------
# pomožne funkcije
# ---------------------------------------------------------------------------

def deep_merge(base, override):
    """Vrne nov slovar: override globoko združen v base (seznami se zamenjajo)."""
    result = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _replace_placeholders(value, replacements):
    if isinstance(value, str):
        for key, repl in replacements.items():
            value = value.replace('{' + key + '}', repl)
        return value
    if isinstance(value, dict):
        return {k: _replace_placeholders(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(v, replacements) for v in value]
    return value


def _iter_urls(value, path=''):
    """Vrne (pot, url) za vse vrednosti, ki so URL-ji (ključ *url ali links[].url)."""
    if isinstance(value, dict):
        for key, sub in value.items():
            sub_path = f'{path}.{key}' if path else key
            if isinstance(sub, str) and (key == 'url' or key.endswith('_url')):
                yield sub_path, sub
            else:
                yield from _iter_urls(sub, sub_path)
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            yield from _iter_urls(sub, f'{path}[{i}]')


def url_host(url):
    try:
        return (urlparse(url).hostname or '').lower()
    except Exception:
        return ''


def is_builtin_profile_id(profile_id):
    return profile_id in BUILTIN_PROFILES


def load_builtin(profile_id=BUILTIN_ID):
    """Izvorni JSON vgrajenega profila."""
    filename = BUILTIN_PROFILES.get(profile_id or BUILTIN_ID)
    if filename is None:
        raise ProfileError(f'Vgrajen profil »{profile_id}« ne obstaja.')
    with open(os.path.join(_PROFILES_DIR, filename), 'r', encoding='utf-8') as f:
        return json.load(f)


def builtin_profiles():
    """{id: data} za vse vgrajene profile v vrstnem redu prikaza."""
    return {pid: load_builtin(pid) for pid in BUILTIN_PROFILES}


# ---------------------------------------------------------------------------
# validacija
# ---------------------------------------------------------------------------

def validate_profile(data, allow_http=False, allow_builtin=False):
    """
    Preveri uvožen profil. Vrne seznam opozoril; ob napaki sproži ProfileError.
    """
    warnings = []
    if not isinstance(data, dict):
        raise ProfileError('Profil mora biti JSON objekt.')

    version = data.get('schema_version')
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProfileError('Manjka ali neveljavna »schema_version«.')
    if version > SCHEMA_VERSION:
        raise ProfileError(
            f'Profil zahteva novejšo različico vtičnika (schema_version {version}, '
            f'podprta je {SCHEMA_VERSION}).')

    pid = data.get('id')
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        raise ProfileError('Neveljaven »id« (dovoljene so male črke, številke, ».«, »_«, »-«).')
    if pid in _RESERVED_IDS and not allow_builtin:
        raise ProfileError(f'»id« ne sme biti »{pid}« (vgrajen profil).')

    if not isinstance(data.get('name'), str) or not data['name'].strip():
        raise ProfileError('Manjka »name«.')

    for key in data:
        if key not in _KNOWN_TOP_KEYS:
            warnings.append(f'Neznan ključ »{key}« je prezrt.')

    for key in ('branding', 'services', 'translations'):
        if key in data and not isinstance(data[key], dict):
            raise ProfileError(f'»{key}« mora biti objekt.')
    if 'base_url' in data and not isinstance(data['base_url'], str):
        raise ProfileError('»base_url« mora biti niz.')
    if 'language' in data and not isinstance(data['language'], str):
        raise ProfileError('»language« mora biti niz.')
    if 'catalog_code' in data and (
            not isinstance(data['catalog_code'], str) or not data['catalog_code'].strip()):
        raise ProfileError('»catalog_code« mora biti neprazen niz.')
    if 'default_layers' in data and not isinstance(data['default_layers'], list):
        raise ProfileError('»default_layers« mora biti seznam.')

    def _check_services(services, where):
        if not isinstance(services, dict):
            raise ProfileError(f'»{where}« mora biti objekt.')
        for name, section in services.items():
            if name not in SERVICE_REQUIRED_URLS:
                warnings.append(f'Neznana storitev »{where}.{name}« je prezrta.')
                continue
            if not isinstance(section, dict):
                raise ProfileError(f'»{where}.{name}« mora biti objekt.')
            if 'enabled' in section and not isinstance(section['enabled'], bool):
                raise ProfileError(f'»{where}.{name}.enabled« mora biti true ali false.')
            fields = section.get('fields')
            if fields is not None:
                if not isinstance(fields, dict) or not all(
                        isinstance(k, str) and isinstance(v, str) and v for k, v in fields.items()):
                    raise ProfileError(f'»{where}.{name}.fields« mora biti objekt nizov.')
                for k in fields:
                    if k not in DEFAULT_FIELDS.get(name, {}):
                        warnings.append(f'Neznano polje »{where}.{name}.fields.{k}« je prezrto.')
        roles = (services.get('login') or {}).get('roles')
        if roles is not None:
            if not isinstance(roles, dict):
                raise ProfileError(f'»{where}.login.roles« mora biti objekt.')
            for role, cfg in roles.items():
                if not isinstance(cfg, dict) or not isinstance(cfg.get('suffix', ''), str):
                    raise ProfileError(f'»{where}.login.roles.{role}« mora imeti niz »suffix«.')

    if 'services' in data:
        _check_services(data['services'], 'services')
        catalog = data['services'].get('catalog')
        if (isinstance(catalog, dict) and catalog.get('enabled') is not False
                and catalog.get('index_url') and not data.get('catalog_code')):
            raise ProfileError('Profil s katalogom mora imeti »catalog_code« '
                               '(vrednost polja »drzava« v indeksu katalogov).')

    for lang, table in (data.get('translations') or {}).items():
        if not isinstance(table, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in table.items()):
            raise ProfileError(f'»translations.{lang}« mora biti objekt nizov.')

    base_url = data.get('base_url') or ''
    resolved = _replace_placeholders(data, {'base_url': base_url})
    for path, url in _iter_urls(resolved):
        if path.startswith('translations'):
            continue
        scheme = (urlparse(url).scheme or '').lower()
        if scheme == 'https' or (allow_http and scheme == 'http'):
            if not url_host(url):
                raise ProfileError(f'Neveljaven URL v »{path}«: {url}')
            continue
        raise ProfileError(f'Dovoljeni so samo https:// naslovi (»{path}«: {url}).')

    logo = (data.get('branding') or {}).get('logo')
    if logo is not None:
        if not isinstance(logo, str) or not (
                logo.startswith(_ALLOWED_LOGO_PREFIXES) or logo.lower().startswith('https://')):
            raise ProfileError('»branding.logo« mora biti data:image/png ali data:image/svg+xml '
                               '(base64) ali https:// naslov.')
    return warnings


# ---------------------------------------------------------------------------
# razreševanje
# ---------------------------------------------------------------------------

class Service:
    def __init__(self, name, section, enabled):
        self.name = name
        self.section = section or {}
        self.enabled = bool(enabled)

    def url(self, key='url'):
        value = self.section.get(key)
        return value if isinstance(value, str) and value else None

    def get(self, key, default=None):
        return self.section.get(key, default)

    def field(self, logical_name):
        mapped = (self.section.get('fields') or {}).get(logical_name)
        if mapped:
            return mapped
        return DEFAULT_FIELDS[self.name][logical_name]

    def fields(self):
        result = dict(DEFAULT_FIELDS.get(self.name, {}))
        for key, value in (self.section.get('fields') or {}).items():
            if key in result and value:
                result[key] = value
        return result

    def __bool__(self):
        return self.enabled

    def __repr__(self):
        return f'Service({self.name!r}, enabled={self.enabled})'


class Profile:
    """Razrešen profil (izvorni JSON + uporabniški prepisi)."""

    def __init__(self, data, is_builtin):
        self.data = data
        self.is_builtin = is_builtin
        self.id = data.get('id') or BUILTIN_ID
        self.name = data.get('name') or self.id
        self._services = {}
        for name in SERVICE_REQUIRED_URLS:
            self._services[name] = self._build_service(name)
        # odvisnosti (v več prehodih, da se izklop razširi)
        changed = True
        while changed:
            changed = False
            for name, deps in SERVICE_DEPENDENCIES.items():
                svc = self._services[name]
                if svc.enabled and not all(self._services[d].enabled for d in deps):
                    svc.enabled = False
                    changed = True

    def _build_service(self, name):
        services = self.data.get('services') or {}
        present = name in services
        section = services.get(name) if present else None
        if not present and name not in SERVICES_ALWAYS_ON:
            return Service(name, {}, False)
        section = section if isinstance(section, dict) else {}
        if section.get('enabled') is False:
            return Service(name, section, False)
        for key in SERVICE_REQUIRED_URLS[name]:
            value = section.get(key)
            if not isinstance(value, str) or not value:
                return Service(name, section, False)
        return Service(name, section, True)

    def service(self, name):
        return self._services[name]

    def enabled(self, name):
        return self._services[name].enabled

    def enabled_services(self):
        return [name for name, svc in self._services.items() if svc.enabled]

    def get(self, path, default=None):
        node = self.data
        for part in path.split('.'):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def catalog_code(self):
        """Koda kataloga (polje »drzava« v indeksu katalogov) ali None."""
        code = self.data.get('catalog_code')
        return code.strip() if isinstance(code, str) and code.strip() else None

    @property
    def language(self):
        """Jezik vmesnika ali None (= dosedanje vedenje: SI slovensko, sicer angleško)."""
        lang = self.data.get('language')
        return lang if isinstance(lang, str) and lang else None

    def default_layers(self):
        """Seznam skupin privzetih podlag (vsaka skupina = seznam alternativnih imen)."""
        groups = self.data.get('default_layers') or []
        result = []
        for group in groups:
            if isinstance(group, str):
                group = [group]
            if isinstance(group, list):
                names = [n for n in group if isinstance(n, str) and n]
                if names:
                    result.append(names)
        return result

    def roles(self):
        roles = self.service('login').get('roles') or {}
        return roles if isinstance(roles, dict) else {}

    def role_suffix(self, gsrv_user):
        """Pripona sloja za GeoServer uporabnika (isti vrstni red in podniz kot doslej)."""
        if not gsrv_user:
            return ''
        for role, cfg in self.roles().items():
            if role in gsrv_user:
                return (cfg or {}).get('suffix', '') or ''
        return ''

    def role_label(self, gsrv_user):
        if not gsrv_user:
            return ''
        for role, cfg in self.roles().items():
            if role in gsrv_user:
                return (cfg or {}).get('label', '') or ''
        return ''

    def translate(self, text, lang):
        table = (self.data.get('translations') or {}).get(lang) if lang else None
        if isinstance(table, dict):
            value = table.get(text)
            if isinstance(value, str):
                return value
        return None

    def domains(self):
        """{host: [storitve]} za vse omogočene storitve in {host} za storitve z geslom."""
        hosts = {}
        password_hosts = set()
        for name, svc in self._services.items():
            for _path, url in _iter_urls(svc.section):
                host = url_host(url)
                if not host:
                    continue
                hosts.setdefault(host, [])
                if name not in hosts[host]:
                    hosts[host].append(name)
                if name in PASSWORD_SERVICES and svc.enabled and (_path == 'url'):
                    password_hosts.add(host)
        return hosts, password_hosts


def profile_domains(data):
    """
    Za potrditveno okno ob uvozu: {host: [storitve]} in množica hostov, ki
    prejmejo geslo.
    """
    resolved = _replace_placeholders(
        data, {'base_url': data.get('base_url') or ''})
    services = resolved.get('services') or {}
    hosts = {}
    password_hosts = set()
    if not isinstance(services, dict):
        return hosts, password_hosts
    for name, section in services.items():
        if name not in SERVICE_REQUIRED_URLS or not isinstance(section, dict):
            continue
        for path, url in _iter_urls(section):
            host = url_host(url)
            if not host:
                continue
            hosts.setdefault(host, [])
            if name not in hosts[host]:
                hosts[host].append(name)
            if name in PASSWORD_SERVICES and path == 'url':
                password_hosts.add(host)
    return hosts, password_hosts


def source_profile_data(profile_id, settings=None):
    """Izvorni JSON profila brez uporabniških sprememb ali None."""
    settings = _default_settings() if settings is None else settings
    profile_id = profile_id or BUILTIN_ID
    if is_builtin_profile_id(profile_id):
        return copy.deepcopy(load_builtin(profile_id))
    data = list_profiles(settings).get(profile_id)
    return copy.deepcopy(data) if data is not None else None


def build_profile_from_data(source, overrides=None, is_builtin=False):
    """Razreši podani izvorni JSON (brez validacije); uporablja ga tudi osnutek v nastavitvah."""
    data = resolve_profile_data(source, overrides)
    return Profile(data, is_builtin)


def build_profile(profile_id, settings=None):
    """Razrešen Profile za poljuben shranjen profil (za prikaz v nastavitvah)."""
    settings = _default_settings() if settings is None else settings
    profile_id = profile_id or BUILTIN_ID
    source = source_profile_data(profile_id, settings)
    if source is None:
        raise ProfileError(f'Profil »{profile_id}« ne obstaja.')
    builtin = is_builtin_profile_id(profile_id)
    validate_profile(source, allow_http=allow_http(settings), allow_builtin=builtin)
    overrides = read_overrides(settings, profile_id)
    data = resolve_profile_data(source, overrides)
    try:
        validate_profile(data, allow_http=allow_http(settings), allow_builtin=True)
    except ProfileError:
        data = resolve_profile_data(source)
    return Profile(data, builtin)


def resolve_profile_data(source, overrides=None):
    """Uporabi uporabniške prepise na samostojnem izvornem profilu."""
    data = copy.deepcopy(source)

    for path, value in (overrides or {}).items():
        node = data
        parts = path.split('.')
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    return _replace_placeholders(data, {'base_url': data.get('base_url') or ''})


# ---------------------------------------------------------------------------
# shranjevanje v QgsSettings
# ---------------------------------------------------------------------------

def _default_settings():
    from qgis.core import QgsSettings
    return QgsSettings()


def _all_keys(settings):
    try:
        return list(settings.allKeys())
    except Exception:
        return []


def list_profiles(settings=None):
    """Vrne {id: data} za vse uvožene profile (neveljavni se preskočijo)."""
    settings = _default_settings() if settings is None else settings
    result = {}
    for key in _all_keys(settings):
        if not key.startswith(SETTINGS_PROFILES_PREFIX):
            continue
        pid = key[len(SETTINGS_PROFILES_PREFIX):]
        if '/' in pid:
            continue
        data = _json_or_none(settings.value(key, '') or '')
        if isinstance(data, dict) and data.get('id') == pid:
            result[pid] = data
    return result


def store_profile(data, settings=None):
    settings = _default_settings() if settings is None else settings
    settings.setValue(SETTINGS_PROFILES_PREFIX + data['id'], json.dumps(data, ensure_ascii=False))


def remove_profile(pid, settings=None):
    settings = _default_settings() if settings is None else settings
    if is_builtin_profile_id(pid):
        raise ProfileError('Vgrajenega profila ni mogoče odstraniti.')
    settings.remove(SETTINGS_PROFILES_PREFIX + pid)
    settings.remove(SETTINGS_PROFILE_OVERRIDE_PREFIX + pid)
    settings.remove(SETTINGS_PROFILE_DATA_PREFIX + pid)
    if active_profile_id(settings) == pid:
        set_active_profile_id(None, settings)


def active_profile_id(settings=None):
    settings = _default_settings() if settings is None else settings
    value = settings.value(SETTINGS_ACTIVE_PROFILE, '') or ''
    return str(value) or None


def set_active_profile_id(pid, settings=None):
    settings = _default_settings() if settings is None else settings
    settings.setValue(SETTINGS_ACTIVE_PROFILE, pid or BUILTIN_ID)


def profile_override_key(profile_id):
    """Ključ za uporabniške spremembe samostojnega profila."""
    pid = profile_id or BUILTIN_ID
    return f'{SETTINGS_PROFILE_OVERRIDE_PREFIX}{pid}'


def read_profile_overrides(profile_id, settings=None):
    settings = _default_settings() if settings is None else settings
    raw = settings.value(profile_override_key(profile_id), '') or ''
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def store_profile_overrides(profile_id, overrides, settings=None):
    settings = _default_settings() if settings is None else settings
    key = profile_override_key(profile_id)
    clean = {str(path): copy.deepcopy(value) for path, value in (overrides or {}).items() if path}
    if clean:
        settings.setValue(key, json.dumps(clean, ensure_ascii=False))
    else:
        settings.remove(key)


def read_legacy_overrides(settings=None):
    """Stari globalni razvojni prepisi; ohranjeni zaradi združljivosti."""
    settings = _default_settings() if settings is None else settings
    overrides = {}
    for key in _all_keys(settings):
        if key.startswith(SETTINGS_OVERRIDE_PREFIX):
            path = key[len(SETTINGS_OVERRIDE_PREFIX):]
            if path and path != 'allow_http':
                overrides[path] = settings.value(key)
    return overrides


def read_overrides(settings=None, profile_id=None):
    """Učinkoviti prepisi profila in nazadnje stari razvojni prepisi."""
    settings = _default_settings() if settings is None else settings
    overrides = {}
    if profile_id is not None:
        overrides.update(read_profile_overrides(profile_id, settings))
    overrides.update(read_legacy_overrides(settings))
    return overrides


def allow_http(settings=None):
    settings = _default_settings() if settings is None else settings
    value = settings.value(SETTINGS_OVERRIDE_PREFIX + 'allow_http', False)
    return str(value).lower() in ('1', 'true', 'yes')


def read_profile_file(path, allow_http_urls=False):
    """Prebere in preveri datoteko profila. Vrne (data, warnings)."""
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ProfileError(f'Datoteke ni mogoče prebrati kot JSON: {exc}')
    warnings = validate_profile(data, allow_http=allow_http_urls)
    return data, warnings


def load_active(settings=None):
    """
    Vrne (Profile, opozorilo|None). Če aktivni profil ne obstaja več ali ni
    veljaven, se uporabi vgrajeni.
    """
    settings = _default_settings() if settings is None else settings
    warning = None
    pid = active_profile_id(settings) or BUILTIN_ID
    source = source_profile_data(pid, settings)
    if source is None:
        warning = f'Aktivni profil »{pid}« ne obstaja več; uporabljen je privzeti profil.'
        pid = BUILTIN_ID
        source = load_builtin(pid)
    builtin = is_builtin_profile_id(pid)
    try:
        validate_profile(source, allow_http=allow_http(settings), allow_builtin=builtin)
    except ProfileError as exc:
        warning = f'Profil »{pid}« ni veljaven ({exc}); uporabljen je privzeti profil.'
        pid = BUILTIN_ID
        source = load_builtin(pid)
        builtin = True
    overrides = read_overrides(settings, pid)
    data = resolve_profile_data(source, overrides)
    try:
        validate_profile(data, allow_http=allow_http(settings), allow_builtin=True)
    except ProfileError as exc:
        warning = f'Uporabniške spremembe profila »{pid}« niso veljavne ({exc}); uporabljene so izvorne nastavitve.'
        data = resolve_profile_data(source)
    return Profile(data, builtin), warning


def profile_settings_key(profile_id, name):
    """
    Ključ za nastavitve, ki so vezane na profil (verzija kataloga, privzeti
    sloji, obseg …).
    """
    return f'{SETTINGS_PROFILE_DATA_PREFIX}{profile_id or BUILTIN_ID}/{name}'


# Nastavitve izdane različice (<= 3.4.1), ki se ob nadgradnji prenesejo v profil.
_LEGACY_PROFILE_DATA = ('qgz_version', 'default_layer_enabled', 'default_layer_name',
                        'default_layer_names')


def migrate_legacy_profile_selection(settings=None):
    """
    Združljivost z izdano različico: izbrana država (q_narcis/country_code)
    se ob prvem zagonu preslika na ustrezni vgrajeni profil; nastavitve
    kataloga in privzetih slojev se prenesejo v prostor tega profila.
    Stari ključi ostanejo nespremenjeni. Vrne ID aktivnega profila.
    """
    settings = _default_settings() if settings is None else settings
    current = active_profile_id(settings)
    if current and current != LEGACY_BUILTIN_ID:
        return current

    legacy_code = str(settings.value('q_narcis/country_code', 'SI') or 'SI').strip().upper()
    profile_id = LEGACY_COUNTRY_PROFILES.get(legacy_code, BUILTIN_ID)
    set_active_profile_id(profile_id, settings)

    names = list(_LEGACY_PROFILE_DATA) + ['default_extent/' + legacy_code]
    for name in names:
        value = settings.value('q_narcis/' + name, None)
        target = profile_settings_key(profile_id, name)
        if value is not None and settings.value(target, None) is None:
            settings.setValue(target, value)

    # Nameščeni katalog pripada tej državi: ne štej tega kot menjavo profila.
    last = settings.value(SETTINGS_LAST_PROFILE, None)
    if last is None or str(last) == LEGACY_BUILTIN_ID:
        settings.setValue(SETTINGS_LAST_PROFILE, profile_id)
    return profile_id
