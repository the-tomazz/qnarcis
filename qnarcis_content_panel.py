# -*- coding: utf-8 -*-

import html
import json
import os
import re
import base64
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlparse

from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QPixmap
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QLabel,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from qgis.core import Qgis, QgsBlockingNetworkRequest, QgsMessageLog
from qgis.gui import QgsDockWidget


class QNarcisContentPanel:
    """Unified content panel for QNarcIS Obvestila and Pomoč tabs."""

    FORUM_URL = "https://narcis.gov.si/ords/narcis/hr/qgisforum"
    MAX_IMAGE_CACHE_ITEMS = 50
    NETWORK_TIMEOUT_MS = 5000
    PARSER_SMOKE_SAMPLES = (
        "_**Spremenjeno delovanje orodja »Pošlji poligon«**_",
        "Vnesite [**NarcIS uporabniški elektronski naslov in geslo**](https://gis.arso.gov.si/related/qnarcis/delovanje/prijava_portal_poligon.png)",
        "[**odstraniti predhodni zapis uporabnika QNarcIS vtičnika**](https://gis.arso.gov.si/related/qnarcis/delovanje/odstranitev_auth.png)",
    )

    def __init__(self, iface, mode, country_code="SI", translations=None):
        self.iface = iface
        self.mode = mode  # "objave" or "pomoc"
        self.country_code = country_code
        self.translations = translations or {}

        self.plugin_dir = os.path.dirname(__file__)
        self.help_dir = os.path.join(self.plugin_dir, "help")

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)

        self.dockwidget = None
        self.pinned_message = None
        self.content_scroll_area = None
        self.content_container = None
        self.link_section_label = None
        self.links_container = None
        self._image_data_uri_cache = OrderedDict()

    def tr(self, message):
        if self.country_code == "SI":
            return message

        if message in self.translations:
            if self.country_code in self.translations[message]:
                return self.translations[message][self.country_code]
            if "en" in self.translations[message]:
                return self.translations[message]["en"]

        return message

    def run(self):
        if self.dockwidget is None:
            self._build_ui()

        if not self.dockwidget.isUserVisible():
            self.iface.addTabifiedDockWidget(Qt.RightDockWidgetArea, self.dockwidget, raiseTab=True)
            self.dockwidget.show()

        self.refresh()

    def refresh(self):
        if self.mode == "objave":
            self._render_objave()
        else:
            self._render_pomoc()

    def stop(self):
        self.timer.stop()

    def _build_ui(self):
        self.dockwidget = QgsDockWidget()
        self.dockwidget.setObjectName("news" if self.mode == "objave" else "help")
        self.dockwidget.setWindowTitle("QNarcIS - objave" if self.mode == "objave" else self.tr("QNarcIS - pomoč"))

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)

        self.pinned_message = QLabel()
        self.pinned_message.setWordWrap(True)
        self.pinned_message.setVisible(False)
        self.pinned_message.setMinimumHeight(40)
        self._set_pinned_message("", "low")

        self.content_scroll_area = QScrollArea()
        self.content_scroll_area.setWidgetResizable(True)
        self.content_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.content_container = QWidget()
        self.content_scroll_area.setWidget(self.content_container)

        self.link_section_label = QLabel(self.tr("Povezave"))

        self.links_container = QWidget()

        root_layout.addWidget(self.pinned_message)
        root_layout.addWidget(self.content_scroll_area)
        root_layout.addWidget(self.link_section_label)
        root_layout.addWidget(self.links_container)

        self.dockwidget.setWidget(root)

    def _render_objave(self):
        self.dockwidget.setWindowTitle("QNarcIS - obvestila")
        payload = self._fetch_json(self.FORUM_URL)

        if payload is None:
            self._set_pinned_message(self.tr("Napaka: vsebina na spletu ni dostopna."), "high")
            self._set_html_content(self._wrap_html(self.tr("<p>Obvestila trenutno niso dosegljiva.</p>")))
        else:
            self._set_pinned_message("", "low")
            items = payload.get("items", [])
            items = sorted(items, key=self._article_sort_key, reverse=True)
            self._set_html_content(self._build_forum_html(items))

        self._set_links(
            [
                ("https://narcis.gov.si/ords/r/narcis/narcis/viri-financiranja", "Viri financiranja"),
                ("https://www.facebook.com/lifenarcis.si", "NarcIS facebook"),
                ("https://www.arso.gov.si", "Agencija RS za okolje"),
            ]
        )

        if not self.timer.isActive():
            self.timer.start(60 * 60000)

    def _render_pomoc(self):
        self.timer.stop()
        self.dockwidget.setWindowTitle(self.tr("QNarcIS - pomoč"))
        self._set_pinned_message("", "medium")

        lang = "si" if self.country_code == "SI" else "en"
        help_path = os.path.join(self.help_dir, lang, "index.html")

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.anchorClicked.connect(QDesktopServices.openUrl)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        browser.setSource(QUrl.fromLocalFile(help_path))

        self._replace_layout_content(self.content_container, [browser])

        links = [("https://narcis.gov.si/ords/r/narcis/narcis", "Spletni portal NarcIS")] if self.country_code == "SI" else []
        self._set_links(links)

    def _set_html_content(self, html_content):
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(True)
        browser.setHtml(html_content)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._replace_layout_content(self.content_container, [browser])

    def _set_links(self, links):
        self.link_section_label.setVisible(bool(links))

        layout = self.links_container.layout()
        if layout is None:
            layout = QHBoxLayout(self.links_container)
            layout.setContentsMargins(0, 0, 0, 0)
        else:
            self._clear_layout(layout)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        for url, title in links:
            label = QLabel(f'<a href="{html.escape(url)}">{html.escape(title)}</a>')
            label.setTextFormat(Qt.RichText)
            label.setOpenExternalLinks(True)
            column.addWidget(label)

        layout.addLayout(column)

        # Keep legacy-like visual layout for "objave": links on the left, logo on the right.
        if links and self.mode == "objave":
            logo_path = os.path.join(self.plugin_dir, "icons", "NarcIS-logo-RB-cropped.png")
            if os.path.exists(logo_path):
                logo_label = QLabel()
                logo_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                logo_label.setPixmap(QPixmap(logo_path))
                layout.addStretch(1)
                layout.addWidget(logo_label)

        self.links_container.setVisible(bool(links))

    def _replace_layout_content(self, container, widgets):
        layout = container.layout()
        if layout is None:
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
        else:
            self._clear_layout(layout)

        for widget in widgets:
            layout.addWidget(widget)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)

    def _set_pinned_message(self, text, importance):
        self.pinned_message.setText(text)
        self.pinned_message.setVisible(bool(text))

        color_by_level = {
            "low": "rgb(154, 229, 114)",
            "medium": "rgb(255, 206, 58)",
            "high": "rgb(255, 85, 0)",
        }
        color = color_by_level.get(importance, "rgb(173,216,230)")
        self.pinned_message.setStyleSheet(f"background:{color};padding:8px;")

    def _fetch_json(self, url):
        try:
            request = QNetworkRequest(QUrl(url))
            try:
                request.setAttribute(QNetworkRequest.TransferTimeoutAttribute, self.NETWORK_TIMEOUT_MS)
            except Exception:
                pass
            blocking_request = QgsBlockingNetworkRequest()
            result = blocking_request.get(request)
            if result != QgsBlockingNetworkRequest.NoError:
                raise RuntimeError(blocking_request.errorMessage())

            reply = blocking_request.reply()
            if reply.error() != QNetworkReply.NoError:
                raise RuntimeError(reply.errorString())

            return json.loads(str(reply.content(), "utf-8"))
        except Exception as exc:
            self.iface.messageBar().pushMessage(
                u"QNarcis",
                self.tr(u"Vsebina na spletu ni dostopna.") + self.tr(u"Več informacij v QGIS message logu."),
                level=Qgis.Critical,
            )
            QgsMessageLog.logMessage(f"Error loading forum content: {exc}", "QNarcIS Content")
            return None

    def _build_forum_html(self, items):
        articles = []
        total = len(items)
        for idx, item in enumerate(items):
            title = html.escape(item.get("naslov", ""))
            date_value = html.escape(item.get("datum", ""))
            body_html = self._markdownish_to_html(item.get("vsebina", ""))
            separator_html = '<hr class="article-separator" />' if idx < total - 1 else ''
            articles.append(
                f"""
                <article class="entry">
                    <h2>{title}</h2>
                    <div class="date">{date_value}</div>
                    <div class="body">{body_html}</div>
                </article>
                {separator_html}
                """
            )
        return self._wrap_html("\n".join(articles))

    def _parse_article_date(self, date_value):
        raw = (date_value or "").strip()
        if not raw:
            return None

        try:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def _article_sort_key(self, item):
        raw_date = item.get("datum", "")
        parsed = self._parse_article_date(raw_date)
        # Parsed dates first; fallback keeps deterministic lexical ordering for invalid dates.
        return (1, parsed) if parsed else (0, str(raw_date or ""))

    def _wrap_html(self, body_html):
        return f"""
        <html>
            <head>
                <meta charset="utf-8" />
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        font-size: 12px;
                        color: #202124;
                        margin: 0;
                        padding: 10px;
                    }}
                    .entry {{
                        padding: 0;
                        margin: 0;
                    }}
                    .article-separator {{
                        border: none;
                        border-top: 1px solid #d8dee6;
                        margin: 14px 0;
                    }}
                    h2 {{ margin: 0 0 4px 0; font-size: 15px; }}
                    .date {{ color: #6b7280; margin-bottom: 8px; font-size: 11px; }}
                    p {{ margin: 6px 0; }}
                    ul, ol {{ margin: 6px 0 6px 22px; }}
                    li {{ margin: 2px 0; }}
                    img {{ max-width: 100%; height: auto; border-radius: 4px; display: block; margin: 0; }}
                    a {{ color: #0b61a4; text-decoration: none; }}
                    a:hover {{ text-decoration: underline; }}
                    blockquote {{
                        border-left: 3px solid #c8d4e3;
                        margin: 8px 0;
                        padding: 2px 0 2px 10px;
                        color: #374151;
                    }}
                </style>
            </head>
            <body>
                {body_html}
            </body>
        </html>
        """

    def _markdownish_to_html(self, text):
        if not text:
            return ""

        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").strip()
        text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>])", r"\1", text)

        lines = text.split("\n")
        out = []
        in_ul = False
        in_ol = False

        def close_lists():
            nonlocal in_ul, in_ol
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if in_ol:
                out.append("</ol>")
                in_ol = False

        for line in lines:
            raw = line.strip()
            if not raw:
                close_lists()
                continue

            heading = re.match(r"^(#{1,6})\s+(.+)$", raw)
            if heading:
                close_lists()
                level = len(heading.group(1))
                out.append(f"<h{level}>{self._format_inline(heading.group(2))}</h{level}>")
                continue

            ordered = re.match(r"^\d+\.\s+(.+)$", raw)
            if ordered:
                if in_ul:
                    out.append("</ul>")
                    in_ul = False
                if not in_ol:
                    out.append("<ol>")
                    in_ol = True
                out.append(f"<li>{self._format_inline(ordered.group(1))}</li>")
                continue

            unordered = re.match(r"^(?:[-*]|[·•])\s+(.+)$", raw)
            if unordered:
                if in_ol:
                    out.append("</ol>")
                    in_ol = False
                if not in_ul:
                    out.append("<ul>")
                    in_ul = True
                out.append(f"<li>{self._format_inline(unordered.group(1))}</li>")
                continue

            if raw.startswith(">"):
                close_lists()
                out.append(f"<blockquote>{self._format_inline(raw[1:].strip())}</blockquote>")
                continue

            close_lists()
            out.append(f"<p>{self._format_inline(raw)}</p>")

        close_lists()
        return "\n".join(out)

    def _is_image_url(self, url):
        if not url:
            return False
        path = url.lower().split("?")[0]
        return path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"))

    def _is_allowed_inline_image(self, url):
        try:
            parsed = urlparse(url)
            return parsed.scheme in ("http", "https") and parsed.netloc.lower() == "gis.arso.gov.si"
        except Exception:
            return False

    def _guess_mime_type(self, url):
        path = (url or "").lower().split("?")[0]
        if path.endswith(".png"):
            return "image/png"
        if path.endswith(".jpg") or path.endswith(".jpeg"):
            return "image/jpeg"
        if path.endswith(".gif"):
            return "image/gif"
        if path.endswith(".svg"):
            return "image/svg+xml"
        if path.endswith(".webp"):
            return "image/webp"
        return "application/octet-stream"

    def _inline_image_source(self, url):
        if url in self._image_data_uri_cache:
            cached = self._image_data_uri_cache.pop(url)
            self._image_data_uri_cache[url] = cached
            return cached

        source = html.escape(url, quote=True)
        try:
            request = QNetworkRequest(QUrl(url))
            try:
                request.setAttribute(QNetworkRequest.TransferTimeoutAttribute, self.NETWORK_TIMEOUT_MS)
            except Exception:
                pass
            blocking_request = QgsBlockingNetworkRequest()
            result = blocking_request.get(request)
            if result == QgsBlockingNetworkRequest.NoError:
                reply = blocking_request.reply()
                if reply.error() == QNetworkReply.NoError:
                    content = bytes(reply.content())
                    if content:
                        mime_type = self._guess_mime_type(url)
                        encoded = base64.b64encode(content).decode("ascii")
                        source = f"data:{mime_type};base64,{encoded}"
        except Exception:
            pass

        self._image_data_uri_cache[url] = source
        while len(self._image_data_uri_cache) > self.MAX_IMAGE_CACHE_ITEMS:
            self._image_data_uri_cache.popitem(last=False)
        return source

    def parser_smoke_check(self):
        """Lightweight internal parser regression hook (manual/debug usage)."""
        try:
            for sample in self.PARSER_SMOKE_SAMPLES:
                _ = self._markdownish_to_html(sample)
            return True
        except Exception:
            return False

    def _format_inline(self, text):
        placeholders = {}

        def stash(value):
            key = f"@@TOKEN{len(placeholders)}@@"
            placeholders[key] = value
            return key

        def image_repl(match):
            alt_raw = (match.group(1) or "").strip()
            alt = html.escape(alt_raw)
            url_raw = (match.group(2) or "").strip()
            url = html.escape(url_raw, quote=True)

            if self._is_allowed_inline_image(url_raw):
                img_src = self._inline_image_source(url_raw)
                return stash(f'<br/><a href="{url}"><img src="{img_src}" alt="{alt or "image"}" /></a><br/>')

            return stash(f'<a href="{url}">{alt if alt_raw else url}</a>')

        def link_repl(match):
            title_raw = (match.group(1) or "").strip()
            title = self._format_inline_emphasis_only(title_raw)
            url_raw = (match.group(2) or "").strip()
            url = html.escape(url_raw, quote=True)

            if self._is_image_url(url_raw):
                if self._is_allowed_inline_image(url_raw):
                    img_src = self._inline_image_source(url_raw)
                    title_part = title if title_raw else ""
                    return stash(
                        f'{title_part}<br/><a href="{url}"><img src="{img_src}" alt="{title or "image"}" /></a><br/>'
                    )

                return stash(f'<a href="{url}">{title if title_raw else url}</a>')

            return stash(f'<a href="{url}">{title if title_raw else url}</a>')

        def bare_url_repl(match):
            whole = match.group(0)
            url_raw = whole
            trailing = ""

            while url_raw and url_raw[-1] in ".,;:!?)\\]}":
                trailing = url_raw[-1] + trailing
                url_raw = url_raw[:-1]

            if not url_raw:
                return whole

            safe_url = html.escape(url_raw, quote=True)
            if self._is_image_url(url_raw):
                if self._is_allowed_inline_image(url_raw):
                    img_src = self._inline_image_source(url_raw)
                    replacement = f'<br/><a href="{safe_url}"><img src="{img_src}" alt="image" /></a><br/>'
                else:
                    replacement = f'<a href="{safe_url}">{html.escape(url_raw)}</a>'
            else:
                replacement = f'<a href="{safe_url}">{html.escape(url_raw)}</a>'

            return stash(replacement) + html.escape(trailing)

        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image_repl, text)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, text)
        text = re.sub(r"https?://[^\s<>'\"]+", bare_url_repl, text)

        escaped = html.escape(text)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"_(.+?)_", r"<em>\1</em>", escaped)
        escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)

        for token, value in placeholders.items():
            escaped = escaped.replace(html.escape(token), value)

        return escaped

    def _format_inline_emphasis_only(self, text):
        escaped = html.escape(text)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"_(.+?)_", r"<em>\1</em>", escaped)
        escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
        return escaped
