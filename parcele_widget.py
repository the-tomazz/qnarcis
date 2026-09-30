# -*- coding: utf-8 -*-

import re
from urllib.parse import urlencode

from qgis.PyQt.QtCore import QModelIndex, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QCompleter, QLabel, QLineEdit, QVBoxLayout, QWidget
from qgis.core import QgsApplication, QgsFeedback, QgsTask

from .qgs_requests import requests

try:
    from qgis.PyQt import sip
except ImportError:
    import sip


_QT_CASE_INSENSITIVE = getattr(Qt, "CaseInsensitive", None)
if _QT_CASE_INSENSITIVE is None:
    _QT_CASE_INSENSITIVE = Qt.CaseSensitivity.CaseInsensitive

_QT_USER_ROLE = getattr(Qt, "UserRole", None)
if _QT_USER_ROLE is None:
    _QT_USER_ROLE = Qt.ItemDataRole.UserRole

_QCOMPLETER_UNFILTERED_POPUP = getattr(QCompleter, "UnfilteredPopupCompletion", None)
if _QCOMPLETER_UNFILTERED_POPUP is None:
    _QCOMPLETER_UNFILTERED_POPUP = QCompleter.CompletionMode.UnfilteredPopupCompletion

_KEY_DOWN = getattr(Qt, "Key_Down", None)
if _KEY_DOWN is None:
    _KEY_DOWN = Qt.Key.Key_Down

_QGSTASK_CAN_CANCEL = getattr(QgsTask, "CanCancel", None)
if _QGSTASK_CAN_CANCEL is None:
    _QGSTASK_CAN_CANCEL = QgsTask.Flag.CanCancel

_QGSTASK_SILENT = getattr(QgsTask, "Silent", None)
if _QGSTASK_SILENT is None:
    _QGSTASK_SILENT = QgsTask.Flag.Silent

_QGSTASK_HIDDEN = getattr(QgsTask, "Hidden", None)
if _QGSTASK_HIDDEN is None:
    _QGSTASK_HIDDEN = QgsTask.Flag.Hidden


class WfsJsonTask(QgsTask):
    """Fetch a small WFS JSON response without blocking the QGIS UI."""

    def __init__(self, url):
        super().__init__("", _QGSTASK_CAN_CANCEL | _QGSTASK_SILENT | _QGSTASK_HIDDEN)
        self.url = url
        self.data = None
        self.exception = None
        self.feedback = QgsFeedback()
        self.cancel_requested = False

    def run(self):
        try:
            response = requests.get(self.url, timeout=30, feedback=self.feedback)
            if self.isCanceled():
                return False
            response.raise_for_status()
            self.data = response.json()
            return True
        except Exception as exc:
            self.exception = exc
            return False

    def cancel(self):
        self.cancel_requested = True
        self.feedback.cancel()
        super().cancel()


class AutocompleteEdit(QLineEdit):
    """Passive web-like autocomplete: typing never commits a suggestion.

    A suggestion is committed only by explicit activation (mouse click or
    keyboard navigation followed by Enter). The widget never rewrites the
    typed text and never pre-selects the first popup row, so slow typing,
    backspace corrections and partial input are never hijacked.
    """

    suggestionActivated = pyqtSignal(object)

    def __init__(self, placeholder, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(_QT_CASE_INSENSITIVE)
        self._completer.setCompletionMode(_QCOMPLETER_UNFILTERED_POPUP)
        self._completer.setMaxVisibleItems(20)
        self.setCompleter(self._completer)

        try:
            self._completer.activated[QModelIndex].connect(self._activate_suggestion)
        except (AttributeError, KeyError, TypeError):
            self._completer.activated.connect(self._activate_suggestion)

    def keyPressEvent(self, event):
        # Down with a hidden popup reopens the last valid suggestions instead
        # of starting anything new. All other keys (including backspace) keep
        # their default line-edit behavior.
        try:
            if event.key() == int(_KEY_DOWN):
                popup = self._completer.popup()
                if not popup.isVisible() and self._model.rowCount() > 0:
                    self.open_popup()
                    event.accept()
                    return
        except (AttributeError, TypeError, ValueError):
            pass
        super().keyPressEvent(event)

    def has_explicit_highlight(self):
        """True only when the user highlighted a popup row themselves."""
        try:
            popup = self._completer.popup()
            return bool(popup.isVisible() and popup.currentIndex().isValid())
        except (AttributeError, TypeError):
            return False

    def open_popup(self):
        if self._model.rowCount() > 0 and self.hasFocus() and self.text().strip():
            self._completer.setCompletionPrefix("")
            self._completer.complete()

    def set_suggestions(self, suggestions, open_popup=True):
        self._model.clear()
        for label, record in suggestions:
            item = QStandardItem(label)
            item.setData(record, _QT_USER_ROLE)
            self._model.appendRow(item)

        # Deliberately no setCurrentIndex(): Enter without explicit navigation
        # must not confirm the first row.
        if open_popup and suggestions and self.hasFocus() and self.text().strip():
            self._completer.setCompletionPrefix("")
            self._completer.complete()
        else:
            self._completer.popup().hide()

    def clear_suggestions(self):
        self.set_suggestions([], open_popup=False)

    def _activate_suggestion(self, value):
        if isinstance(value, QModelIndex):
            index = value
        else:
            matches = self._model.findItems(str(value))
            index = matches[0].index() if matches else QModelIndex()

        if not index.isValid():
            return
        record = index.data(_QT_USER_ROLE)
        if record is None:
            return
        self.setText(str(index.data()))
        self._completer.popup().hide()
        self.suggestionActivated.emit(record)


class Parcele(QWidget):
    parcelSelected = pyqtSignal(dict)
    searchChanged = pyqtSignal()

    WFS_URL = "https://ipi.eprostor.gov.si/wfs-si-gurs-kn/wfs"
    KO_TYPENAME = "SI.GURS.KN:KATASTRSKE_OBCINE"
    PARCEL_TYPENAME = "SI.GURS.KN:PARCELE"
    RESULT_LIMIT = 50
    KO_RESULT_LIMIT = 50
    KO_QUERY_CACHE_LIMIT = 100
    # Shared by both searcher instances (Parcele, Lastništvo parcel): KO data
    # comes from the same public GURS WFS, so a query run in one tab benefits
    # the other. Maps normalized query -> (records, complete), where complete
    # means the server returned fewer than KO_RESULT_LIMIT rows, i.e. nothing
    # was truncated and longer queries may be derived locally.
    _ko_query_cache = {}

    def __init__(self, parent=None, searcher_id="parcele"):
        super().__init__(parent)
        self._searcher_id = searcher_id
        self._ko_task = None
        self._parcel_task = None
        self._geometry_task = None
        self._ko_request_id = 0
        self._parcel_request_id = 0
        self._geometry_request_id = 0
        self._selected_ko = None
        self._selected_ko_label = None
        self._disposed = False

        self._ko_search_timer = QTimer(self)
        self._ko_search_timer.setSingleShot(True)
        self._ko_search_timer.setInterval(400)
        self._ko_search_timer.timeout.connect(self._search_kos)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(600)
        self._search_timer.timeout.connect(self._search_parcels)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Katastrska občina"))
        self.ko_edit = AutocompleteEdit("Vnesi šifro ali ime KO ...")
        self.ko_edit.setEnabled(True)
        self.ko_edit.textEdited.connect(self._on_ko_text_edited)
        self.ko_edit.textChanged.connect(self._on_ko_text_changed)
        self.ko_edit.suggestionActivated.connect(self._on_ko_selected)
        layout.addWidget(self.ko_edit)

        layout.addWidget(QLabel("Parcelna številka"))
        self.parcel_edit = AutocompleteEdit("Najprej izberi KO ...")
        self.parcel_edit.setEnabled(False)
        self.parcel_edit.textEdited.connect(self._on_parcel_text_edited)
        self.parcel_edit.textChanged.connect(self._on_parcel_text_changed)
        self.parcel_edit.suggestionActivated.connect(self._on_parcel_selected)
        layout.addWidget(self.parcel_edit)

        self.status_label = QLabel("Vnesi šifro ali ime katastrske občine.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

    def _build_wfs_url(self, typename, properties, count, cql_filter=None):
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": typename,
            "outputFormat": "application/json",
            "propertyName": ",".join(properties),
            "count": str(count),
        }
        if cql_filter:
            params["CQL_FILTER"] = cql_filter
        return self.WFS_URL + "?" + urlencode(params)

    def _start_task(self, attribute, url, completed, failed):
        if self._disposed:
            return None
        task = WfsJsonTask(url)
        task.taskCompleted.connect(lambda: completed(task))
        task.taskTerminated.connect(lambda: failed(task))
        setattr(self, attribute, task)
        QgsApplication.taskManager().addTask(task)
        return task

    def _cancel_task(self, attribute):
        task = getattr(self, attribute, None)
        setattr(self, attribute, None)
        if task is None:
            return
        try:
            if sip.isdeleted(task):
                return
        except (AttributeError, TypeError):
            pass
        try:
            task.cancel()
        except RuntimeError:
            # QGIS can delete a completed task before Python releases its wrapper.
            return

    @staticmethod
    def _escape_cql_like(text):
        return (text.replace("\\", "\\\\").replace("'", "''")
                .replace("%", "\\%").replace("_", "\\_"))

    @classmethod
    def _store_ko_query(cls, query, records):
        key = query.strip().casefold()
        cache = cls._ko_query_cache
        cache.pop(key, None)
        cache[key] = (list(records), len(records) < cls.KO_RESULT_LIMIT)
        while len(cache) > cls.KO_QUERY_CACHE_LIMIT:
            cache.pop(next(iter(cache)))

    def _search_kos(self):
        if self._disposed:
            return
        query = self.ko_edit.text().strip()
        if not query:
            return

        key = query.casefold()
        cache = self.__class__._ko_query_cache
        entry = cache.get(key)
        if entry is not None:
            self._show_ko_suggestions(entry[0], query)
            return
        # Prefix derivation: the server searches by prefix, so results for a
        # longer query are a subset of a COMPLETE cached shorter one. Only
        # within the same branch (code vs. name predicates differ).
        for length in range(len(key) - 1, 0, -1):
            prefix = key[:length]
            prefix_entry = cache.get(prefix)
            if prefix_entry is None:
                continue
            prefix_records, complete = prefix_entry
            if not complete or prefix.isdigit() != key.isdigit():
                continue
            if key.isdigit():
                filtered = [record for record in prefix_records
                            if str(record["sifko"]).startswith(key)]
            else:
                filtered = [record for record in prefix_records
                            if record["name"].casefold().startswith(key)]
            self._show_ko_suggestions(filtered, query)
            return

        self._ko_request_id += 1
        request_id = self._ko_request_id
        self._cancel_task("_ko_task")

        # Server-side prefix search: the WFS rejects bulk downloads, so the
        # KO list is never fetched in full. Digits search the KO code,
        # anything else searches the (uppercase) KO name.
        if query.isdigit():
            cql_filter = "SIFKO LIKE '{0}%'".format(self._escape_cql_like(query))
        else:
            cql_filter = "strToUpperCase(NAZIV) LIKE '{0}%'".format(
                self._escape_cql_like(query.upper())
            )
        url = self._build_wfs_url(
            self.KO_TYPENAME,
            ("KO_ID", "NAZIV", "SIFKO"),
            self.KO_RESULT_LIMIT,
            cql_filter,
        )
        self.status_label.setText("Iščem katastrske občine ...")
        self._start_task(
            "_ko_task",
            url,
            lambda task: self._kos_loaded(task, request_id, query),
            lambda task: self._kos_failed(task, request_id),
        )

    def _kos_loaded(self, task, request_id, query):
        if self._disposed or request_id != self._ko_request_id or task is not self._ko_task:
            return
        self._ko_task = None
        records = []
        for feature in (task.data or {}).get("features", []):
            properties = feature.get("properties") or {}
            ko_id = properties.get("KO_ID")
            sifko = properties.get("SIFKO")
            name = str(properties.get("NAZIV") or "").strip()
            if ko_id is None or sifko is None or not name:
                continue
            records.append({"ko_id": int(ko_id), "sifko": int(sifko), "name": name})

        if self.ko_edit.text().strip() != query:
            return

        self._store_ko_query(query, records)
        self._show_ko_suggestions(records, query)

    def _show_ko_suggestions(self, records, query):
        if self._disposed or self.ko_edit.text().strip() != query:
            return
        query_folded = query.strip().casefold()
        ordered = sorted(records, key=lambda record: self._ko_suggestion_key(record, query_folded))
        suggestions = [
            (f"{record['sifko']} - {record['name']}", record)
            for record in ordered[:self.KO_RESULT_LIMIT]
        ]
        self.ko_edit.set_suggestions(suggestions)
        if suggestions:
            self.status_label.setText("Izberi katastrsko občino s seznama predlogov.")
        else:
            self.status_label.setText("Za vneseno iskanje ni katastrskih občin.")

    def _kos_failed(self, task, request_id):
        if self._disposed or request_id != self._ko_request_id or task is not self._ko_task:
            return
        self._ko_task = None
        if task.cancel_requested:
            return
        self.status_label.setText("Katastrskih občin ni bilo mogoče pridobiti. Preveri omrežno povezavo.")

    def _on_ko_text_changed(self, text):
        # The clear (x) button and programmatic clears emit textChanged, not
        # textEdited. An emptied KO field is a full reset: drop the selection,
        # cancel everything and clear the parcel field and the map highlight
        # (via searchChanged). Idempotent: re-entered by clears issued below.
        if self._disposed or text.strip():
            return
        self._reset_ko_selection()

    def _reset_ko_selection(self):
        self.searchChanged.emit()
        self._ko_search_timer.stop()
        self._search_timer.stop()
        self._ko_request_id += 1
        self._parcel_request_id += 1
        self._geometry_request_id += 1
        self._cancel_task("_ko_task")
        self._cancel_task("_parcel_task")
        self._cancel_task("_geometry_task")
        self._selected_ko = None
        self._selected_ko_label = None
        self.ko_edit.clear_suggestions()
        self._clear_parcels()
        self.status_label.setText("Izberi katastrsko občino.")

    def _on_ko_text_edited(self, text):
        if self._disposed:
            return
        # Guard against accidental invalidation: edits that leave the text
        # identical to the selected label keep the selection.
        if self._selected_ko is not None and text == self._selected_ko_label:
            return

        self.searchChanged.emit()
        self._ko_search_timer.stop()
        self._search_timer.stop()
        self._ko_request_id += 1
        self._parcel_request_id += 1
        self._geometry_request_id += 1
        self._cancel_task("_ko_task")
        self._cancel_task("_parcel_task")
        self._cancel_task("_geometry_task")
        self._selected_ko = None
        self._clear_parcels()
        self.ko_edit.clear_suggestions()

        if not text.strip():
            self.status_label.setText("Izberi katastrsko občino.")
            return

        self.status_label.setText("Vnesi šifro ali ime katastrske občine.")
        self._ko_search_timer.start()

    @staticmethod
    def _ko_suggestion_key(record, query):
        code = str(record["sifko"]).casefold()
        name = record["name"].strip().casefold()
        label = f"{code} - {name}"
        words = re.split(r"[^0-9a-zčšž]+", name)
        if query in (code, name, label):
            rank = 0
        elif code.startswith(query) or name.startswith(query):
            rank = 1
        elif any(word.startswith(query) for word in words):
            rank = 2
        else:
            rank = 3
        return rank, len(name), name, int(record["sifko"])

    def _on_ko_selected(self, record):
        if self._disposed:
            return
        self.searchChanged.emit()
        self._selected_ko = record
        label = f"{record['sifko']} - {record['name']}"
        self._selected_ko_label = label
        self.ko_edit.setText(label)
        self.ko_edit.clear_suggestions()
        # The field stays enabled: any further edit that changes the text
        # invalidates the selection (see _on_ko_text_edited).
        self._clear_parcels()
        self.status_label.setText("Vnesi parcelno številko.")
        self.parcel_edit.setFocus()

    def _clear_parcels(self, text=""):
        self._search_timer.stop()
        self.parcel_edit.clear_suggestions()
        self.parcel_edit.setText(text)
        has_ko = self._selected_ko is not None
        self.parcel_edit.setEnabled(has_ko)
        self.parcel_edit.setPlaceholderText(
            "Vnesi parcelno številko ..." if has_ko else "Najprej izberi KO ..."
        )

    def _on_parcel_text_changed(self, text):
        # The clear (x) button emits textChanged, not textEdited. An emptied
        # parcel field cancels pending requests and clears the map highlight
        # (via searchChanged). Idempotent: re-entered by clears issued below.
        if self._disposed or text.strip():
            return
        self.searchChanged.emit()
        self._search_timer.stop()
        self._parcel_request_id += 1
        self._geometry_request_id += 1
        self._cancel_task("_parcel_task")
        self._cancel_task("_geometry_task")
        self.parcel_edit.clear_suggestions()
        if self._selected_ko is not None:
            self.status_label.setText("Vnesi parcelno številko.")

    def _on_parcel_text_edited(self, text):
        if self._disposed or not self._selected_ko:
            return

        self.searchChanged.emit()
        self._parcel_request_id += 1
        self._cancel_task("_parcel_task")
        self.parcel_edit.clear_suggestions()

        text = text.strip()
        self._search_timer.stop()
        if not text:
            self.status_label.setText("Vnesi parcelno številko.")
            return
        if not re.fullmatch(r"[0-9A-Za-z./*-]+", text):
            self.status_label.setText("Parcelna številka vsebuje nedovoljene znake.")
            return
        self.status_label.setText("Vnesi parcelno številko in izberi predlog s seznama.")
        self._search_timer.start()

    def _search_parcels(self):
        if self._disposed or not self._selected_ko:
            return
        prefix = self.parcel_edit.text().strip()
        if not prefix:
            return
        if not re.fullmatch(r"[0-9A-Za-z./*-]+", prefix):
            self.status_label.setText("Parcelna številka vsebuje nedovoljene znake.")
            return
        self.status_label.setText("Iščem parcele ...")

        self._parcel_request_id += 1
        request_id = self._parcel_request_id
        self._cancel_task("_parcel_task")

        safe_prefix = self._escape_cql_like(prefix)
        cql_filter = "KO_ID={0} AND ST_PARCELE LIKE '{1}%'".format(
            self._selected_ko["ko_id"], safe_prefix
        )
        url = self._build_wfs_url(
            self.PARCEL_TYPENAME,
            ("EID_PARCELA", "KO_ID", "ST_PARCELE"),
            self.RESULT_LIMIT,
            cql_filter,
        )
        self._start_task(
            "_parcel_task",
            url,
            lambda task: self._parcels_loaded(task, request_id, prefix),
            lambda task: self._parcels_failed(task, request_id),
        )

    def _parcels_loaded(self, task, request_id, prefix):
        if self._disposed or request_id != self._parcel_request_id or task is not self._parcel_task:
            return
        self._parcel_task = None

        records = []
        for feature in (task.data or {}).get("features", []):
            properties = feature.get("properties") or {}
            eid = str(properties.get("EID_PARCELA") or "").strip()
            number = str(properties.get("ST_PARCELE") or "").strip()
            if eid and number:
                records.append({"eid_parcela": eid, "number": number})

        if self.parcel_edit.text().strip() != prefix:
            return

        records.sort(key=lambda record: (
            record["number"].casefold() != prefix.casefold(),
            self._parcel_sort_key(record),
        ))
        suggestions = [(record["number"], record) for record in records]
        self.parcel_edit.set_suggestions(suggestions)

        if not records:
            self.status_label.setText("Za vneseno številko ni zadetkov.")
        elif len(records) >= self.RESULT_LIMIT:
            self.status_label.setText("Prikazanih je prvih 50 zadetkov. Vnesi več znakov.")
        else:
            self.status_label.setText("Izberi parcelo s seznama predlogov.")

    def _parcels_failed(self, task, request_id):
        if self._disposed or request_id != self._parcel_request_id or task is not self._parcel_task:
            return
        self._parcel_task = None
        if task.cancel_requested:
            return
        self.status_label.setText("Parcel ni bilo mogoče pridobiti. Preveri omrežno povezavo.")

    @staticmethod
    def _parcel_sort_key(record):
        parts = re.split(r"(\d+)", record["number"])
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in parts
        )

    def _on_parcel_selected(self, parcel):
        if self._disposed or not self._selected_ko or not parcel:
            return
        self._search_timer.stop()
        self._parcel_request_id += 1
        self._cancel_task("_parcel_task")
        self.parcel_edit.clear_suggestions()
        self._load_parcel_extent(parcel)

    def _load_parcel_extent(self, parcel):
        if self._disposed:
            return
        self._geometry_request_id += 1
        request_id = self._geometry_request_id
        self._cancel_task("_geometry_task")

        eid = parcel["eid_parcela"].replace("'", "''")
        url = self._build_wfs_url(
            self.PARCEL_TYPENAME,
            ("EID_PARCELA", "GEOM"),
            1,
            "EID_PARCELA='{}'".format(eid),
        )
        self.status_label.setText("Pripravljam parcelo ...")
        self.parcel_edit.setEnabled(False)
        self._start_task(
            "_geometry_task",
            url,
            lambda task: self._parcel_extent_loaded(task, request_id, parcel),
            lambda task: self._parcel_extent_failed(task, request_id),
        )

    def _parcel_extent_loaded(self, task, request_id, parcel):
        if self._disposed or request_id != self._geometry_request_id or task is not self._geometry_task:
            return
        self._geometry_task = None
        self.parcel_edit.setEnabled(True)
        features = (task.data or {}).get("features", [])
        bbox = features[0].get("bbox") if features else None
        if not bbox or len(bbox) < 4:
            self.status_label.setText("Za izbrano parcelo ni mogoče določiti lokacije.")
            return

        geometry = features[0].get("geometry")
        if not isinstance(geometry, dict) or not geometry.get("coordinates"):
            geometry = None

        if self._searcher_id == "lastnistvo":
            self.status_label.setText("Odpiram sloj lastništva parcel ...")
        else:
            self.status_label.setText("Odpiram sloj parcel ...")
        self.parcelSelected.emit({
            "searcher": self._searcher_id,
            "eid_parcela": parcel["eid_parcela"],
            "number": parcel["number"],
            "sifko": self._selected_ko["sifko"],
            "bbox": [float(value) for value in bbox[:4]],
            "crs": "EPSG:3794",
            "geometry": geometry,
        })

    def _parcel_extent_failed(self, task, request_id):
        if self._disposed or request_id != self._geometry_request_id or task is not self._geometry_task:
            return
        self._geometry_task = None
        self.parcel_edit.setEnabled(self._selected_ko is not None)
        if task.cancel_requested:
            return
        self.status_label.setText("Lokacije parcele ni bilo mogoče pridobiti.")

    def shutdown(self):
        if self._disposed:
            return
        self._disposed = True
        self.__class__._ko_query_cache.clear()
        self._ko_search_timer.stop()
        self._search_timer.stop()
        self._ko_request_id += 1
        self._parcel_request_id += 1
        self._geometry_request_id += 1
        self._cancel_task("_ko_task")
        self._cancel_task("_parcel_task")
        self._cancel_task("_geometry_task")

    def set_result_message(self, message):
        if self._disposed:
            return
        self.status_label.setText(message)
