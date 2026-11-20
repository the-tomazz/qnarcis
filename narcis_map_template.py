# -*- coding: utf-8 -*-
"""
NarcIS predloga karte (Print Layout).

Ustvari QGIS Print Layout (A4 ležeče) z NarcIS logotipom, karto, legendo,
merilom, puščico za sever in samodejno izpolnjenim seznamom virov
(»Skrbnik sloja« iz kataloga QNarcIS).

Postavitev je zgrajena v kodi (ne iz .qpt) in se prilagodi izseku karte.
Uporabnik lahko layout v Layout Designerju shrani kot .qpt predlogo
(Layout → Save as Template).

Besedila se prevajajo prek funkcije `tr`, ki jo poda vtičnik.

ID-ji elementov (za .qpt predlogo in programsko posodabljanje):
    narcis_naslov, narcis_logo, narcis_karta, narcis_legenda,
    narcis_merilo, narcis_sever, narcis_viri, narcis_opomba
"""

import os

from qgis.core import (
    QgsProject,
    QgsMapSettings,
    QgsRectangle,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsSymbolLayerUtils,
    QgsTextFormat,
    QgsMessageLog,
    Qgis,
)
from qgis.PyQt.QtGui import QFont, QColor, QImageReader

_QGIS_INFO = getattr(Qgis, 'Info', None)
if _QGIS_INFO is None:  # QGIS 4: samo Qgis.MessageLevel
    _QGIS_INFO = Qgis.MessageLevel.Info

LAYOUT_BASE_NAME = 'NarcIS karta'

# A4 ležeče, mm
PAGE_W = 297.0
PAGE_H = 210.0
MARGIN = 8.0
HEADER_H = 16.0
FOOTER_H = 16.0
GAP = 4.0
LEGEND_W = 55.0      # stalna širina stolpca z legendo


def _text_format(size, bold=False, color=None):
    fmt = QgsTextFormat()
    font = QFont('Arial')
    font.setBold(bold)
    fmt.setFont(font)
    fmt.setSize(size)
    if color is not None:
        fmt.setColor(QColor(color))
    return fmt


def _add_label(layout, item_id, text, x, y, w, h, size, bold=False, color=None):
    label = QgsLayoutItemLabel(layout)
    label.setId(item_id)
    label.setText(text)
    label.setTextFormat(_text_format(size, bold, color))
    layout.addLayoutItem(label)
    label.attemptMove(QgsLayoutPoint(x, y))
    label.attemptResize(QgsLayoutSize(w, h))
    return label


def _unique_layout_name(manager, base_name):
    if manager.layoutByName(base_name) is None:
        return base_name
    i = 2
    while manager.layoutByName(f'{base_name} {i}') is not None:
        i += 1
    return f'{base_name} {i}'


def _identity(text):
    return text


def snapshot_canvas_settings(canvas):
    """Posnetek izrisa, vključno s slogi aktivne teme zaslona."""
    settings = QgsMapSettings(canvas.mapSettings())
    theme = canvas.theme()
    themes = QgsProject.instance().mapThemeCollection()
    if theme and themes.hasMapTheme(theme):
        # QGIS sloge teme nastavi šele ob izrisu; mapSettings() jih pred
        # tem ne vsebuje nujno (npr. ob zaustavljenem osveževanju zaslona).
        settings.setLayerStyleOverrides(themes.mapThemeStyleOverrides(theme))
    return settings


def format_sources_text(sources, tr=_identity):
    """
    sources: dict {skrbnik: [imena slojev]} za sloje iz kataloga
             + ključ None za sloje, ki niso v katalogu NarcIS.
    """
    lines = []
    for owner in sorted(k for k in sources if k):
        names = ', '.join(sorted(set(sources[owner])))
        lines.append(f'{owner} ({names})')

    text = tr('Viri podatkov') + ': ' + ('; '.join(lines) if lines else '–')

    other = sources.get(None)
    if other:
        text += '\n' + tr('Drugi sloji (niso iz kataloga NarcIS)') + ': ' + ', '.join(sorted(set(other)))
    return text


def map_frame_size(extent_w, extent_h, content_w, content_h):
    """Okvir v razpoložljivem prostoru (mm), brez spremembe razmerja izseka."""
    max_map_w = content_w - GAP - LEGEND_W
    if extent_w > 0 and extent_h > 0:
        factor = min(max_map_w / extent_w, content_h / extent_h)
        return extent_w * factor, extent_h * factor
    return max_map_w, content_h


DEFAULT_LOGO_ASPECT = 259.0 / 100.0  # NarcIS-logo-RB-cropped.png


def _logo_aspect(logo_path, default=DEFAULT_LOGO_ASPECT):
    """Razmerje širina/višina slike; za neznane ali oddaljene slike privzeto."""
    try:
        if logo_path and os.path.exists(logo_path):
            size = QImageReader(logo_path).size()
            if size.isValid() and size.height() > 0:
                return size.width() / float(size.height())
    except Exception as exc:
        QgsMessageLog.logMessage(f"NarcIS karta: velikosti logotipa ni mogoče prebrati: {exc}",
                                 "QNarcIS", _QGIS_INFO)
    return default


def create_narcis_layout(iface, plugin_dir, layers, sources, tr=_identity,
                         logo_path=None, footer_text=None, map_settings=None):
    """
    layers:       seznam slojev zaslona (v vrstnem redu izrisa)
    sources:      glej format_sources_text
    tr:           funkcija za prevajanje besedil
    logo_path:    pot ali https URL logotipa (profil); None = NarcIS logotip
    footer_text:  besedilo noge (profil); None = privzeto QNarcIS besedilo
    map_settings: posnetek nastavitev zaslona; None = trenutne nastavitve
    Vrne ustvarjen QgsPrintLayout in ga odpre v Layout Designerju.
    """
    settings = (QgsMapSettings(map_settings) if map_settings is not None
                else snapshot_canvas_settings(iface.mapCanvas()))
    if not settings.hasValidSettings():
        raise ValueError(tr('Izsek karte ni veljaven.'))

    # canvas.extent() je pri rotaciji OVOJNICA vidnega poligona. Okvir in
    # nerotiran izsek sestavimo iz velikosti zaslona in ločljivosti, nato
    # rotacijo prenesemo posebej. Tako ostanejo vsi štirje vogali isti.
    output_size = settings.outputSize()
    mupp = settings.mapUnitsPerPixel()
    extent = QgsRectangle.fromCenterAndSize(
        settings.visibleExtent().center(),
        output_size.width() * mupp, output_size.height() * mupp)

    project = QgsProject.instance()
    manager = project.layoutManager()

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()  # ena stran A4 ležeče
    layout.setName(_unique_layout_name(manager, tr(LAYOUT_BASE_NAME)))

    # --- okvir ohrani razmerje zaslona; legenda ima svoj prostor tudi pri
    #     zelo širokem in nizkem izseku ---
    content_top = MARGIN + HEADER_H
    content_h = PAGE_H - content_top - FOOTER_H - MARGIN
    content_w = PAGE_W - 2 * MARGIN
    map_w, map_h = map_frame_size(extent.width(), extent.height(), content_w, content_h)
    column_h = max(map_h, content_h * 0.6)
    legend_x = MARGIN + map_w + GAP
    legend_w = LEGEND_W

    # --- glava: naslov + logotip ---
    default_logo = os.path.join(plugin_dir, 'icons', 'NarcIS-logo-RB-cropped.png')
    if not logo_path:
        logo_path = default_logo
    logo_h = HEADER_H - 3
    logo_w = logo_h * (DEFAULT_LOGO_ASPECT if logo_path == default_logo else _logo_aspect(logo_path))
    _add_label(layout, 'narcis_naslov', tr('Naslov karte'),
               MARGIN, MARGIN + 1, content_w - logo_w - GAP, logo_h, 18, bold=True)

    logo = QgsLayoutItemPicture(layout)
    logo.setId('narcis_logo')
    logo.setPicturePath(logo_path)
    layout.addLayoutItem(logo)
    logo.attemptMove(QgsLayoutPoint(PAGE_W - MARGIN - logo_w, MARGIN))
    logo.attemptResize(QgsLayoutSize(logo_w, logo_h))

    # --- karta ---
    # Najprej določimo velikost, nato točen izsek (zoomToExtent bi ga
    # pri drugačnem razmerju okvirja razširil).
    map_item = QgsLayoutItemMap(layout)
    map_item.setId('narcis_karta')
    layout.addLayoutItem(map_item)
    map_item.attemptResize(QgsLayoutSize(map_w, map_h))
    map_item.attemptMove(QgsLayoutPoint(MARGIN, content_top))
    map_item.setFrameEnabled(True)
    map_item.setCrs(settings.destinationCrs())
    map_item.setLayers(layers)
    map_item.setKeepLayerSet(True)
    # Shranimo tudi običajne sloge; tema lahko prepiše samo del slojev.
    map_item.storeCurrentLayerStyles()
    styles = map_item.layerStyleOverrides()
    styles.update(settings.layerStyleOverrides())
    map_item.setLayerStyleOverrides(styles)
    map_item.setKeepLayerStyles(True)
    map_item.setBackgroundColor(settings.backgroundColor())
    map_item.setExtent(extent)
    map_item.setMapRotation(settings.rotation())
    map_item.refresh()

    # --- legenda (samo vidni sloji) ---
    legend = QgsLayoutItemLegend(layout)
    legend.setId('narcis_legenda')
    legend.setTitle(tr('Legenda'))
    legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.removeAllChildren()
    for layer in layers:
        root.addLayer(layer)
    layout.addLayoutItem(legend)
    legend.attemptMove(QgsLayoutPoint(legend_x, content_top))
    legend.setResizeToContents(False)
    legend.attemptResize(QgsLayoutSize(legend_w, column_h - 28))
    legend.updateLegend()

    # --- puščica za sever (spodaj desno v stolpcu legende) ---
    north = QgsLayoutItemPicture(layout)
    north.setId('narcis_sever')
    try:
        north_path = QgsSymbolLayerUtils.svgSymbolNameToPath(
            'arrows/NorthArrow_02.svg', project.pathResolver())
        north.setPicturePath(north_path)
    except Exception as exc:
        QgsMessageLog.logMessage(f"NarcIS karta: puščice proti severu ni mogoče naložiti: {exc}",
                                 "QNarcIS", _QGIS_INFO)
    north.setLinkedMap(map_item)
    layout.addLayoutItem(north)
    north.attemptMove(QgsLayoutPoint(legend_x, content_top + column_h - 24))
    north.attemptResize(QgsLayoutSize(14, 24))

    # --- merilo (pod karto) ---
    scalebar = QgsLayoutItemScaleBar(layout)
    scalebar.setId('narcis_merilo')
    scalebar.setStyle('Single Box')
    scalebar.setLinkedMap(map_item)
    scalebar.applyDefaultSize()
    layout.addLayoutItem(scalebar)
    footer_top = content_top + column_h + 2
    scalebar.attemptMove(QgsLayoutPoint(MARGIN, footer_top))

    # --- noga: viri + opomba ---
    footer_x = MARGIN + 75
    _add_label(layout, 'narcis_viri', format_sources_text(sources, tr),
               footer_x, footer_top, PAGE_W - MARGIN - footer_x, FOOTER_H - 7, 7)

    # [% %] se ovrednoti ob vsakem izrisu/izvozu
    _add_label(layout, 'narcis_opomba',
               (footer_text or tr("Karta izdelana z vtičnikom QNarcIS (https://narcis.gov.si)"))
               + ", [% format_date(now(), 'dd.MM.yyyy') %]",
               footer_x, footer_top + FOOTER_H - 5, PAGE_W - MARGIN - footer_x, 4, 7, color='#555555')

    manager.addLayout(layout)
    layout.refresh()
    iface.openLayoutDesigner(layout)
    return layout
