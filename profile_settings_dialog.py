# -*- coding: utf-8 -*-
"""
Urejevalnik uporabniških sprememb enega profila infrastrukture.

Zavihek »Osnovno« vsebuje identiteto profila in API endpointe posameznih
funkcionalnosti (z vklopom/izklopom), zavihek »Napredno« pa tehnične
nastavitve (preslikave polj, vloge, CRS, sloje, branding, prevode …).
Spremembe se shranijo kot prepisi nad izvornim JSON-om (ta ostane nespremenjen).
"""

import copy
import json

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import country_profile as cprof


def _enum(owner, legacy, scope, scoped):
    value = getattr(owner, legacy, None)
    return value if value is not None else getattr(getattr(owner, scope), scoped)


_ITEM_EDITABLE = _enum(Qt, 'ItemIsEditable', 'ItemFlag', 'ItemIsEditable')
_USER_ROLE = _enum(Qt, 'UserRole', 'ItemDataRole', 'UserRole')
_NO_EDIT_TRIGGERS = _enum(QAbstractItemView, 'NoEditTriggers', 'EditTrigger', 'NoEditTriggers')
_DIALOG_ACCEPTED = _enum(QDialog, 'Accepted', 'DialogCode', 'Accepted')
_MSG_YES = _enum(QMessageBox, 'Yes', 'StandardButton', 'Yes')

BASIC = 'basic'
ADVANCED = 'advanced'

# Osnovne nastavitve izven storitev.
_BASIC_TOP_PATHS = ('name', 'organization', 'language', 'catalog_code', 'base_url')


def _exec(dialog):
    method = getattr(dialog, 'exec', None)
    return method() if callable(method) else dialog.exec_()


def _flatten(node, prefix=''):
    """Vrne urejevalne liste/skalarje; prazni objekti niso samostojne vrednosti."""
    result = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f'{prefix}.{key}' if prefix else key
            if isinstance(value, dict):
                result.update(_flatten(value, path))
            else:
                result[path] = copy.deepcopy(value)
    return result


def _format_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _parse_value(text, original):
    text = str(text).strip()
    if isinstance(original, bool):
        if text.lower() in ('true', '1', 'yes', 'da'):
            return True
        if text.lower() in ('false', '0', 'no', 'ne'):
            return False
        raise ValueError('Vnesite true ali false.')
    if isinstance(original, (list, dict)):
        value = json.loads(text)
        if not isinstance(value, type(original)):
            raise ValueError('Vrednost nima pričakovane vrste JSON.')
        return value
    if original is None:
        return None if text in ('', 'null') else text
    if isinstance(original, int):
        return int(text)
    if isinstance(original, float):
        return float(text)
    return text


def setting_level(path):
    """BASIC za identiteto profila, vklop storitev in njihove URL-je; sicer ADVANCED."""
    if path in _BASIC_TOP_PATHS:
        return BASIC
    parts = path.split('.')
    if parts[0] == 'services' and len(parts) == 3:
        key = parts[2]
        if key == 'enabled' or key == 'url' or key.endswith('_url'):
            return BASIC
    return ADVANCED


def editable_values(source):
    """
    {pot: izvorna vrednost} za vse nastavitve, ki jih urejevalnik prikaže.
    Vključuje tudi znane storitve, ki jih izvorni profil izpušča (da jih je
    mogoče vklopiti), in privzete preslikave polj prisotnih storitev.
    """
    base = cprof.build_profile_from_data(source)
    values = _flatten(base.data)
    for path in list(values):
        if path.split('.', 1)[0] in ('schema_version', 'id'):
            values.pop(path)
    for path in _BASIC_TOP_PATHS:
        values.setdefault(path, '')
    services = base.data.get('services') or {}
    for name, required in cprof.SERVICE_REQUIRED_URLS.items():
        values.setdefault(f'services.{name}.enabled', base.enabled(name))
        for key in required:
            values.setdefault(f'services.{name}.{key}', '')
        if name in services:
            for field, default in cprof.DEFAULT_FIELDS.get(name, {}).items():
                values.setdefault(f'services.{name}.fields.{field}', default)
    return values


class ProfileSettingsDialog(QDialog):
    """Uredi prepise enega profila (osnovne in napredne nastavitve)."""

    # Napisi skupin (JSON ključi ostanejo nespremenjeni).
    _GROUP_LABELS = {
        'profile': 'Profil',
        'branding': 'Portal in logotip',
    }
    _PATH_LABELS = {
        'name': 'Ime profila',
        'organization': 'Organizacija',
        'profile_version': 'Verzija profila',
        'contact': 'Kontakt',
        'base_url': 'Osnovni URL',
        'language': 'Jezik',
        'catalog_code': 'Koda kataloga',
        'default_layers': 'Privzeti sloji',
        'branding.portal_name': 'Ime portala',
        'branding.portal_url': 'URL portala',
        'branding.login_portal_url': 'URL portala za prijavo',
        'branding.logo': 'Logotip',
        'branding.map_footer': 'Noga karte',
    }

    def __init__(self, parent, profile_id, source_data, settings, tr=None,
                 initial_overrides=None):
        super().__init__(parent)
        self._tr = tr or (lambda text: text)
        self._profile_id = profile_id or cprof.BUILTIN_ID
        self._source = copy.deepcopy(source_data)
        self._settings = settings
        if initial_overrides is None:
            initial_overrides = cprof.read_profile_overrides(self._profile_id, settings)
        self._draft = copy.deepcopy(initial_overrides)
        self._baseline = {}
        self._items = {}
        self._trees = {}
        self._updating = False
        self._valid = True

        self.setWindowTitle(self._tr('Nastavitve profila') + ': '
                            + str(self._source.get('name') or self._profile_id))
        self.resize(900, 620)
        layout = QVBoxLayout(self)

        notice = QLabel(self._tr(
            'Spremembe veljajo po ponovnem zagonu QGIS-a. Izvorni JSON ostane nespremenjen.'))
        notice.setWordWrap(True)
        layout.addWidget(notice)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(self._tr('Išči')))
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        filter_row.addWidget(self.search, 1)
        self.only_changed = QCheckBox(self._tr('Prikaži samo spremenjene'))
        filter_row.addWidget(self.only_changed)
        layout.addLayout(filter_row)

        self.tabs = QTabWidget()
        self._tab_titles = {}
        for level, title in ((BASIC, 'Osnovno'), (ADVANCED, 'Napredno')):
            tree = self._make_tree()
            self._trees[level] = tree
            self._tab_titles[level] = self._tr(title)
            self.tabs.addTab(tree, self._tr(title))
        layout.addWidget(self.tabs, 1)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color: #b00020;')
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.reset_selected = QPushButton(self._tr('Povrni izbrano'))
        self.reset_profile = QPushButton(self._tr('Povrni celoten profil na izvorni JSON'))
        actions.addWidget(self.reset_selected)
        actions.addWidget(self.reset_profile)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox()
        save_button = _enum(QDialogButtonBox, 'Save', 'StandardButton', 'Save')
        cancel_button = _enum(QDialogButtonBox, 'Cancel', 'StandardButton', 'Cancel')
        buttons.setStandardButtons(save_button | cancel_button)
        self._save_button = buttons.button(save_button)
        self._save_button.setText(self._tr('Shrani'))
        buttons.button(cancel_button).setText(self._tr('Prekliči'))
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.search.textChanged.connect(self._apply_filter)
        self.only_changed.toggled.connect(self._apply_filter)
        self.reset_selected.clicked.connect(self._reset_selected)
        self.reset_profile.clicked.connect(self._reset_profile)
        self._rebuild()

    def _make_tree(self):
        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels([
            self._tr('Nastavitev'), self._tr('Vrednost'),
            self._tr('Stanje'), self._tr('Izvorna vrednost'),
        ])
        tree.setAlternatingRowColors(True)
        tree.setEditTriggers(_NO_EDIT_TRIGGERS)
        tree.setColumnWidth(0, 300)
        tree.setColumnWidth(1, 260)
        tree.setColumnWidth(2, 110)
        tree.itemChanged.connect(self._item_changed)
        tree.itemDoubleClicked.connect(
            lambda item, column, t=tree: t.editItem(item, 1)
            if column == 1 and item.data(0, _USER_ROLE) else None)
        return tree

    def overrides(self):
        return copy.deepcopy(self._draft)

    def _rebuild(self):
        values = editable_values(self._source)
        self._baseline = values
        self._updating = True
        self._items = {}
        for level, tree in self._trees.items():
            tree.clear()
            groups = {}
            for path in sorted(p for p in set(values) | set(self._draft)
                               if setting_level(p) == level):
                parts = path.split('.')
                group_key = '.'.join(parts[:2]) if parts[0] == 'services' and len(parts) > 2 else (
                    'profile' if len(parts) == 1 else parts[0])
                group = groups.get(group_key)
                if group is None:
                    label = parts[1] if group_key.startswith('services.') else self._tr(
                        self._GROUP_LABELS.get(group_key, group_key))
                    group = QTreeWidgetItem([label])
                    tree.addTopLevelItem(group)
                    groups[group_key] = group
                original = values.get(path)
                value = self._draft.get(path, original)
                item = QTreeWidgetItem([
                    self._path_label(path), _format_value(value), '', _format_value(original),
                ])
                item.setData(0, _USER_ROLE, path)
                item.setFlags(item.flags() | _ITEM_EDITABLE)
                group.addChild(item)
                self._items[path] = item
                self._mark_item(item, path in self._draft)
            tree.expandAll()
        self._updating = False
        self._validate()
        self._apply_filter()

    def _path_label(self, path):
        label = self._PATH_LABELS.get(path)
        if label:
            return self._tr(label) + f' ({path})'
        parts = path.split('.')
        if parts[0] == 'services' and len(parts) >= 3:
            return '.'.join(parts[2:])
        return path

    def _mark_item(self, item, changed):
        item.setText(2, self._tr('Spremenjeno') if changed else self._tr('Privzeto'))
        item.setForeground(2, QBrush(QColor('#9a4d00') if changed else QColor('#555555')))

    def _item_changed(self, item, column):
        if self._updating or column != 1:
            return
        path = item.data(0, _USER_ROLE)
        if not path:
            return
        original = self._baseline.get(path)
        try:
            value = _parse_value(item.text(1), original)
        except (ValueError, TypeError) as exc:
            item.setText(2, self._tr('Neveljavno'))
            item.setForeground(2, QBrush(QColor('#b00020')))
            self.error_label.setText(f'{path}: {exc}')
            self._valid = False
            self._save_button.setEnabled(False)
            return
        if value == original:
            self._draft.pop(path, None)
        else:
            self._draft[path] = value
        self._mark_item(item, path in self._draft)
        self._validate()
        self._apply_filter()

    def _validation_error(self):
        overrides = copy.deepcopy(self._draft)
        overrides.update(cprof.read_legacy_overrides(self._settings))
        try:
            profile = cprof.build_profile_from_data(self._source, overrides)
            cprof.validate_profile(profile.data, cprof.allow_http(self._settings),
                                   allow_builtin=True)
        except (cprof.ProfileError, ValueError, TypeError) as exc:
            return exc
        return None

    def _validate(self):
        error = self._validation_error()
        self._valid = error is None
        if self._valid:
            self.error_label.clear()
        else:
            self.error_label.setText(self._tr('Profil ni veljaven') + ': ' + str(error))
        self._save_button.setEnabled(self._valid)

    # Kot iskalnik po katalogu: iskanje po vseh stolpcih, ujemajoče celice so označene.
    _HIGHLIGHT = QColor(255, 236, 140)

    @staticmethod
    def _filter_icon():
        try:
            from qgis.core import QgsApplication
            return QgsApplication.getThemeIcon('/mActionFilter2.svg')
        except Exception:
            return QIcon()

    def _apply_filter(self, *_args):
        query = self.search.text().strip().casefold()
        changed_only = self.only_changed.isChecked()
        filtering = bool(query) or changed_only
        highlight, plain = QBrush(self._HIGHLIGHT), QBrush()
        filter_icon = self._filter_icon() if filtering else QIcon()
        for level, tree in self._trees.items():
            columns = tree.columnCount()
            tree_matches = 0
            for group_index in range(tree.topLevelItemCount()):
                group = tree.topLevelItem(group_index)
                group_matches = bool(query) and query in group.text(0).casefold()
                group.setBackground(0, highlight if group_matches else plain)
                visible_children = 0
                for child_index in range(group.childCount()):
                    child = group.child(child_index)
                    path = child.data(0, _USER_ROLE)
                    matched_columns = [c for c in range(columns)
                                       if query and query in child.text(c).casefold()]
                    for column in range(columns):
                        child.setBackground(column, highlight if column in matched_columns else plain)
                    matches = (not query or group_matches or bool(matched_columns)
                               or query in str(path).casefold())
                    visible = matches and (not changed_only or path in self._draft)
                    child.setHidden(not visible)
                    visible_children += int(visible)
                group.setHidden(visible_children == 0)
                tree_matches += visible_children
            # med iskanjem: ikona filtra in število zadetkov v imenu zavihka
            index = self.tabs.indexOf(tree)
            title = self._tab_titles[level]
            self.tabs.setTabText(index, f'{title} ({tree_matches})' if filtering else title)
            self.tabs.setTabIcon(index, filter_icon)

    def _reset_selected(self):
        tree = self.tabs.currentWidget()
        item = tree.currentItem() if tree is not None else None
        path = item.data(0, _USER_ROLE) if item is not None else None
        if not path:
            return
        self._draft.pop(path, None)
        self._updating = True
        item.setText(1, _format_value(self._baseline.get(path)))
        self._mark_item(item, False)
        self._updating = False
        self._validate()
        self._apply_filter()

    def _reset_profile(self):
        answer = QMessageBox.question(
            self, 'QNarcIS', self._tr('Povrnem celoten profil na izvorni JSON?'))
        if answer != _MSG_YES:
            return
        self._draft = {}
        self._rebuild()

    def _accept_if_valid(self):
        self._validate()
        if self._valid:
            self.accept()


def edit_profile_settings(parent, profile_id, source_data, settings, tr=None,
                          initial_overrides=None):
    """Odpre urejevalnik; vrne nove prepise profila ali None ob preklicu."""
    dialog = ProfileSettingsDialog(parent, profile_id, source_data, settings, tr,
                                   initial_overrides)
    if _exec(dialog) == _DIALOG_ACCEPTED:
        return dialog.overrides()
    return None
