"""
Vsako besedilo, ki gre v kodi skozi tr()/_tr(), mora imeti vnos z angleškim
prevodom v translations.json (ta datoteka je vir za prevode v druge jezike).
Test ne potrebuje QGIS-a.
"""

import ast
import glob
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def literal_tr_strings():
    found = {}
    for path in sorted(glob.glob(os.path.join(ROOT, '*.py'))):
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
            arg = node.args[0]
            if name in ('tr', '_tr') and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(arg.value, f'{os.path.basename(path)}:{node.lineno}')
    return found


def indirect_tr_strings():
    """Besedila, ki gredo v tr() prek spremenljivk (oznake, sporočila o napakah …)."""
    with open(os.path.join(ROOT, 'q_narcis.py'), encoding='utf-8') as f:
        plugin = f.read()
    with open(os.path.join(ROOT, 'profile_settings_dialog.py'), encoding='utf-8') as f:
        dialog = f.read()
    with open(os.path.join(ROOT, 'narcis_map_template.py'), encoding='utf-8') as f:
        template = f.read()
    result = re.findall(r'^CATALOG_ERR_\w+ = "([^"]+)"', plugin, re.M)
    labels = re.search(r'_PROFILE_SERVICE_LABELS = \((.*?)\n    \)', plugin, re.S)
    result += [label for _name, label in ast.literal_eval('(' + labels.group(1) + ')')]
    for name in ('_GROUP_LABELS', '_PATH_LABELS'):
        block = re.search(name + r' = (\{.*?\n    \})', dialog, re.S)
        result += list(ast.literal_eval(block.group(1)).values())
    result += re.findall(r"\(BASIC, '([^']+)'\), \(ADVANCED, '([^']+)'\)", dialog)[0]
    result += re.findall(r"^LAYOUT_BASE_NAME = '([^']+)'", template, re.M)
    result += ['Nameščanje novega kataloga slojev', 'Pripravljanje kataloga slojev']
    return result


class TranslationTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'translations.json'), encoding='utf-8') as f:
            self.translations = json.load(f)

    def test_every_tr_string_has_english(self):
        missing = {text: where for text, where in literal_tr_strings().items()
                   if 'en' not in self.translations.get(text, {})}
        self.assertEqual(missing, {}, 'Dodajte vnose v translations.json')

    def test_indirect_strings_have_english(self):
        missing = [text for text in indirect_tr_strings()
                   if 'en' not in self.translations.get(text, {})]
        self.assertEqual(missing, [])

    def test_placeholders_are_kept(self):
        for source, table in self.translations.items():
            expected = sorted(re.findall(r'\{[^}]*\}', source))
            for lang, text in table.items():
                with self.subTest(source=source, lang=lang):
                    self.assertEqual(sorted(re.findall(r'\{[^}]*\}', text)), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
