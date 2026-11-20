"""Real PyQGIS layout regression tests; run with a QGIS-enabled Python.

Uses a temporary profile and memory layers, never the user's QGIS project.
QT_QPA_PLATFORM=offscreen python3 tests/test_narcis_map_template.py
"""

import os
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsFillSymbol,
    QgsGeometry, QgsLayoutExporter, QgsLayerTreeModel, QgsMapLayerStyle,
    QgsMapSettings, QgsMapThemeCollection, QgsProject, QgsRectangle, QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor

from narcis_map_template import create_narcis_layout


PLUGIN_DIR = str(Path(__file__).resolve().parents[1])


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory(prefix='qnarcis-layout-test-')
        for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
                    'QGIS_CUSTOM_CONFIG_PATH'):
            os.environ[key] = cls.profile.name
        cls.app = QgsApplication([], True, cls.profile.name)
        cls.app.initQgis()

    @classmethod
    def tearDownClass(cls):
        QgsProject.instance().clear()
        cls.app.exitQgis()
        cls.profile.cleanup()

    def setUp(self):
        self.project = QgsProject.instance()
        self.project.clear()
        self.layer = self.add_polygon('Canvas layer', '#2244cc')
        self.canvas = QgsMapCanvas()
        self.canvas.setRenderFlag(False)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        self.canvas.setLayers([self.layer])
        self.opened = []
        self.set_view(1000, 1000)

    def tearDown(self):
        self.canvas.close()
        sip.delete(self.canvas)
        self.opened.clear()
        self.project.clear()
        self.app.processEvents()

    def add_polygon(self, name, color):
        layer = QgsVectorLayer('Polygon?crs=EPSG:3857', name, 'memory')
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(
            'POLYGON((0 0, 1000 0, 1000 1000, 0 1000, 0 0))'))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({
            'color': color, 'outline_style': 'no',
        }))
        self.project.addMapLayer(layer)
        return layer

    def set_view(self, width, height, rotation=0):
        self.canvas.resize(width, height)
        self.canvas.show()
        self.app.processEvents()
        self.canvas.setRotation(0)
        self.canvas.setExtent(QgsRectangle(-100, -100, 1100, 1100))
        self.canvas.setRotation(rotation)

    # Minimal iface: canvas and designer entry point are real API boundaries.
    def mapCanvas(self):
        return self.canvas

    def openLayoutDesigner(self, layout):
        self.opened.append(layout.name())

    def create_layout(self, **kwargs):
        return create_narcis_layout(
            self, PLUGIN_DIR, self.canvas.layers(),
            {None: [layer.name() for layer in self.canvas.layers()]}, **kwargs)

    def assert_same_footprint(self, map_item, settings):
        # Keep the QPolygonF owners alive while using their PyQt point wrappers.
        expected_polygon = settings.visiblePolygon()
        actual_polygon = map_item.visibleExtentPolygon()
        expected = list(expected_polygon)[:4]
        actual = list(actual_polygon)[:4]
        self.assertEqual(len(actual), 4)
        # Polygon starting corner/winding may differ; compare geographical corners.
        tolerance = settings.mapUnitsPerPixel() * 1e-6
        for point in expected:
            self.assertTrue(any(
                abs(point.x() - other.x()) < tolerance and
                abs(point.y() - other.y()) < tolerance for other in actual),
                f'Canvas corner {point} missing from layout {actual}')

    def test_viewport_footprint_and_page_bounds(self):
        for width, height in ((1600, 900), (1200, 900), (1000, 1000),
                              (2400, 800), (800, 1200), (2400, 200)):
            for rotation in (0, 30, 45, 90, -25):
                with self.subTest(size=(width, height), rotation=rotation):
                    self.set_view(width, height, rotation)
                    layout = self.create_layout()
                    item = layout.itemById('narcis_karta')
                    self.assert_same_footprint(item, self.canvas.mapSettings())
                    self.assertEqual(item.crs(), self.canvas.mapSettings().destinationCrs())
                    self.assertAlmostEqual(item.mapRotation(), rotation)
                    for item_id in ('narcis_karta', 'narcis_legenda', 'narcis_sever',
                                    'narcis_viri', 'narcis_opomba'):
                        rect = layout.itemById(item_id).sceneBoundingRect()
                        self.assertGreater(rect.width(), 0)
                        self.assertGreater(rect.height(), 0)
                        self.assertGreaterEqual(rect.top(), 0)
                        self.assertLessEqual(rect.right(), 297)
                        self.assertLessEqual(rect.bottom(), 210)
                    self.project.layoutManager().removeLayout(layout)

    def test_canvas_layers_order_styles_and_rendering(self):
        decoy = self.add_polygon('Checked but absent from canvas', '#cc2222')
        self.canvas.setLayers([self.layer])
        original_style = QgsMapLayerStyle()
        original_style.readFromLayer(self.layer)
        self.layer.renderer().symbol().setColor(QColor('#22cc44'))
        canvas_style = QgsMapLayerStyle()
        canvas_style.readFromLayer(self.layer)
        original_style.writeToLayer(self.layer)
        self.canvas.setLayerStyleOverrides({self.layer.id(): canvas_style.xmlData()})
        layout = self.create_layout()
        item = layout.itemById('narcis_karta')
        self.assertEqual(item.layers(), [self.layer])
        self.assertTrue(item.keepLayerSet())
        self.assertTrue(item.keepLayerStyles())
        self.assertEqual(item.layerStyleOverrides(), self.canvas.layerStyleOverrides())
        self.assertEqual([node.layer() for node in layout.itemById('narcis_legenda')
                          .model().rootGroup().findLayers()], [self.layer])

        # Render through the layout exporter, not just the map-settings API.
        image = QgsLayoutExporter(layout).renderPageToImage(0, QSize(1188, 840))
        self.assertFalse(image.isNull())
        center = item.sceneBoundingRect().center()
        self.assertEqual(image.pixelColor(round(center.x() * 4), round(center.y() * 4)).name(),
                         '#22cc44')
        self.assertEqual(self.layer.renderer().symbol().color().name(), '#2244cc')
        self.assertTrue(self.project.layerTreeRoot().findLayer(decoy.id()).isVisible())

        # A generated map remains a snapshot when the live canvas changes.
        self.canvas.setLayers([decoy, self.layer])
        second = self.create_layout()
        self.assertEqual(second.itemById('narcis_karta').layers(), [decoy, self.layer])
        self.assertEqual(item.layers(), [self.layer])

    def test_project_round_trip(self):
        self.set_view(1600, 900, 30)
        layout = self.create_layout(footer_text='Custom footer')
        settings = QgsMapSettings(self.canvas.mapSettings())
        with tempfile.TemporaryDirectory(dir=self.profile.name) as folder:
            path = os.path.join(folder, 'layout.qgs')
            self.assertTrue(self.project.write(path))
            restored = QgsProject()
            self.assertTrue(restored.read(path))
            saved = restored.layoutManager().layoutByName(layout.name())
            self.assertIsNotNone(saved)
            item = saved.itemById('narcis_karta')
            self.assert_same_footprint(item, settings)
            self.assertEqual([layer.id() for layer in item.layers()], [self.layer.id()])
            self.assertTrue(item.keepLayerSet())
            self.assertEqual(
                ET.canonicalize(item.layerStyleOverrides()[self.layer.id()], strip_text=True),
                ET.canonicalize(layout.itemById('narcis_karta').layerStyleOverrides()[self.layer.id()],
                                strip_text=True))
            self.assertTrue(saved.itemById('narcis_opomba').text().startswith('Custom footer'))
            restored.clear()

    def test_map_theme_and_ordinary_style_snapshot(self):
        root = self.project.layerTreeRoot()
        decoy = self.add_polygon('Outside theme', '#cc2222')
        root.findLayer(decoy.id()).setItemVisibilityChecked(False)
        self.layer.styleManager().addStyleFromLayer('blue')
        self.layer.renderer().symbol().setColor(QColor('#22cc44'))
        self.layer.styleManager().addStyleFromLayer('green')
        self.layer.styleManager().setCurrentStyle('green')
        model = QgsLayerTreeModel(root)
        theme = QgsMapThemeCollection.createThemeFromCurrentState(root, model)
        self.project.mapThemeCollection().insert('Canvas theme', theme)
        self.layer.styleManager().setCurrentStyle('blue')
        root.findLayer(decoy.id()).setItemVisibilityChecked(True)
        self.canvas.setTheme('Canvas theme')
        self.assertEqual(self.canvas.layers(), [self.layer])
        layout = self.create_layout()
        item = layout.itemById('narcis_karta')
        self.assertEqual(item.layers(), [self.layer])
        self.assertEqual(item.layerStyleOverrides(),
                         self.project.mapThemeCollection().mapThemeStyleOverrides('Canvas theme'))
        image = QgsLayoutExporter(layout).renderPageToImage(0, QSize(1188, 840))
        center = item.sceneBoundingRect().center()
        self.assertEqual(image.pixelColor(round(center.x() * 4), round(center.y() * 4)).name(),
                         '#22cc44')
        self.assertEqual(self.layer.styleManager().currentStyle(), 'blue')

        # Without a theme, later edits must not change a previously made map.
        self.canvas.setTheme('')
        self.canvas.setLayers([self.layer])
        ordinary = self.create_layout().itemById('narcis_karta')
        saved_style = ordinary.layerStyleOverrides()[self.layer.id()]
        self.layer.renderer().symbol().setColor(QColor('#cc2222'))
        self.assertEqual(ordinary.layerStyleOverrides()[self.layer.id()], saved_style)

    def test_snapshot_and_invalid_view(self):
        self.canvas.setMagnificationFactor(1.5)
        self.set_view(1200, 900, 45)
        settings = QgsMapSettings(self.canvas.mapSettings())
        self.set_view(900, 1200, -25)
        layout = self.create_layout(map_settings=settings)
        self.assert_same_footprint(layout.itemById('narcis_karta'), settings)
        count = len(self.project.layoutManager().layouts())
        with self.assertRaises(ValueError):
            self.create_layout(map_settings=QgsMapSettings())
        self.assertEqual(len(self.project.layoutManager().layouts()), count)
        self.assertEqual(len(self.opened), count)

    def test_plugin_entry_point_uses_canvas_for_sources(self):
        # Load the actual plugin as a package without constructing its UI or
        # starting catalog/network tasks. Exercise the real action handler.
        spec = importlib.util.spec_from_file_location(
            '_qnarcis_layout_tests', os.path.join(PLUGIN_DIR, '__init__.py'),
            submodule_search_locations=[PLUGIN_DIR])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        from _qnarcis_layout_tests.q_narcis import QNarcis

        self.add_polygon('Not on canvas', '#cc2222')
        self.canvas.setLayers([self.layer])
        self.layer.setScaleBasedVisibility(True)
        self.layer.setMinimumScale(1000000)
        self.layer.setMaximumScale(1)
        plugin = QNarcis.__new__(QNarcis)
        plugin.iface = self
        plugin.plugin_dir = PLUGIN_DIR
        plugin.profile = None
        plugin.hasDataModel = lambda: True
        plugin.tr = lambda text: text
        plugin._normalize_identity_layer_name = lambda name: name
        plugin._catalog_definition_for_project_layer = lambda layer: None
        plugin._branding = lambda key: None
        plugin._profile_logo_path = lambda: None
        plugin.runMapTemplate()

        layout = self.project.layoutManager().layoutByName('NarcIS karta')
        self.assertIsNotNone(layout)
        self.assertEqual(layout.itemById('narcis_karta').layers(), [self.layer])
        sources = layout.itemById('narcis_viri').text()
        self.assertIn('Canvas layer', sources)
        self.assertNotIn('Not on canvas', sources)
        self.assertTrue(self.layer.hasScaleBasedVisibility())
        self.assertEqual(self.layer.minimumScale(), 1000000)
        self.assertEqual(self.layer.maximumScale(), 1)

    def test_unique_names_branding_and_linked_items(self):
        first = self.create_layout(footer_text='Custom footer')
        second = self.create_layout()
        self.assertEqual(first.name(), 'NarcIS karta')
        self.assertEqual(second.name(), 'NarcIS karta 2')
        self.assertEqual(self.opened, [first.name(), second.name()])
        item = first.itemById('narcis_karta')
        for item_id in ('narcis_legenda', 'narcis_sever', 'narcis_merilo'):
            self.assertEqual(first.itemById(item_id).linkedMap(), item)
        self.assertFalse(first.itemById('narcis_logo').isMissingImage())
        self.assertFalse(first.itemById('narcis_sever').isMissingImage())
        self.assertIn('Canvas layer', first.itemById('narcis_viri').text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
