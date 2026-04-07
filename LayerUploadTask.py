# -*- coding: utf-8 -*-
import json, math, uuid

from qgis.core import (
    QgsTask, Qgis, QgsProject, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsApplication,
    QgsGeometry, QgsMessageLog
)
from qgis.PyQt.QtCore import (
    QObject, QUrl, QByteArray, pyqtSignal, pyqtSlot, Qt, QThread
)
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply

_QT_ISO_DATE = getattr(Qt, 'ISODate', None)
if _QT_ISO_DATE is None:
    _QT_ISO_DATE = Qt.DateFormat.ISODate

_QT_QUEUED_CONNECTION = getattr(Qt, 'QueuedConnection', None)
if _QT_QUEUED_CONNECTION is None:
    _QT_QUEUED_CONNECTION = Qt.ConnectionType.QueuedConnection

_QNETWORKREPLY_NOERROR = getattr(QNetworkReply, 'NoError', None)
if _QNETWORKREPLY_NOERROR is None:
    _QNETWORKREPLY_NOERROR = QNetworkReply.NetworkError.NoError


# ----------------------------- small helpers -----------------------------
def _log(level, msg):
    # Always log to the QNarcIS tab in the Log Messages panel
    try:
        QgsMessageLog.logMessage(msg, 'QNarcIS', level)
    except Exception:
        pass

def _parts_in(g, geom_type):
    if g is None or g.isEmpty():
        return 0
    try:
        if QgsWkbTypes.isMultiType(g.wkbType()):
            if geom_type == QgsWkbTypes.PointGeometry:
                return len(g.asMultiPoint())
            elif geom_type == QgsWkbTypes.LineGeometry:
                try:
                    return len(g.asMultiPolyline())
                except Exception:
                    return len(g.asMultiLineString())
            elif geom_type == QgsWkbTypes.PolygonGeometry:
                return len(g.asMultiPolygon())
        return 1
    except Exception:
        return 1

def _bbox_array(layer, xform):
    rect = layer.extent()
    try:
        if xform is not None:
            rect = xform.transformBoundingBox(rect)
    except Exception:
        pass
    return [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]

def _to_jsonable(v):
    from qgis.PyQt.QtCore import QDateTime, QDate, QTime, QByteArray as QtBA, QVariant
    from qgis.PyQt.QtGui import QColor
    try:
        if isinstance(v, QVariant):
            v = v.value()
    except Exception:
        pass
    if v is None or isinstance(v, (bool, int, float, str)): return v
    if isinstance(v, (QDateTime, QDate, QTime)): return v.toString(_QT_ISO_DATE)
    if isinstance(v, QtBA):
        try: return bytes(v).decode("utf-8")
        except Exception: return bytes(v).hex()
    if isinstance(v, QColor): return v.name()
    if isinstance(v, (list, tuple)): return [_to_jsonable(x) for x in v]
    if isinstance(v, dict): return {str(k): _to_jsonable(val) for k, val in v.items()}
    try:
        from datetime import datetime, date, time as pytime
        if isinstance(v, (datetime, date, pytime)): return v.isoformat()
    except Exception:
        pass
    return str(v)


# ---------------------- main-thread poster (queued) ----------------------
class _Poster(QObject):
    """
    Runs in the main (GUI) thread. Receives post requests via a queued signal,
    uses QgsNetworkAccessManager.instance(), and enforces max_concurrent.
    """
    batchFinished = pyqtSignal(int, int, bool, dict)  # (batch_idx, n_feats, ok, resp)

    def __init__(self, api_url, username, password, max_concurrent=1, parent=None):
        super().__init__(parent)
        from qgis.core import QgsNetworkAccessManager
        self._nam = QgsNetworkAccessManager.instance()
        self._original_timeout = self._nam.timeout()
        self._nam.setTimeout(300000)
        self._api_url = api_url
        self._username = username
        self._password = password
        self._max_concurrent = max(1, int(max_concurrent))
        self._inflight = {}          # reply -> (batch_idx, n_feats)
        self._queue = []             # list of (batch_idx, n_feats, QByteArray)
        self._popup_shown = False    # show the first “error” popup only once
        self._canceled = False       # NEW: stop starting new and suppress UI popups after cancel
    
    def restore_timeout(self):
        if hasattr(self, '_original_timeout'):
            self._nam.setTimeout(self._original_timeout)

    def _open_qnarcis_log(self):
        # Try to show the built-in Log Messages panel focused on the "QNarcIS" tab
        try:
            from qgis.utils import iface
            from qgis.PyQt.QtWidgets import QDockWidget, QTabWidget
            mw = iface.mainWindow()
            dock = mw.findChild(QDockWidget, 'MessageLog')
            if dock is None:
                try:
                    for act in mw.findChildren(type(mw.menuBar().defaultAction())):
                        if hasattr(act, 'objectName') and act.objectName() == 'mActionToggleLogMessagesPanel':
                            act.trigger()
                            break
                    dock = mw.findChild(QDockWidget, 'MessageLog')
                except Exception:
                    pass
            if dock:
                dock.show()
                dock.raise_()
                tabs = dock.findChild(QTabWidget)
                if tabs:
                    for i in range(tabs.count()):
                        if tabs.tabText(i).strip().lower() == 'qnarcis':
                            tabs.setCurrentIndex(i)
                            break
        except Exception:
            pass

    def cancel_all(self):
        """Prevent any new requests and abort all in-flight ones."""
        self._canceled = True
        # drop anything queued
        try:
            self._queue[:] = []
        except Exception:
            self._queue = []
        # abort in-flight replies
        for reply in list(self._inflight.keys()):
            try:
                reply.abort()
            except Exception:
                pass

    @pyqtSlot(int, int, QByteArray)
    def onPostRequested(self, batch_idx, n_feats, payload):
        """Called from the task thread (QueuedConnection)."""
        if self._canceled:
            return
        self._queue.append((batch_idx, n_feats, payload))
        self._pump()

    def _pump(self):
        if self._canceled:
            return
        # Start as many queued requests as capacity allows
        while self._queue and (not self._canceled) and len(self._inflight) < self._max_concurrent:
            batch_idx, n_feats, payload = self._queue.pop(0)
            req = QNetworkRequest(QUrl(self._api_url))
            req.setRawHeader(b"uporabnik", self._username.encode("utf-8"))
            req.setRawHeader(b"key", self._password.encode("utf-8"))
            req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
            reply = self._nam.post(req, payload)
            self._inflight[reply] = (batch_idx, n_feats)
            reply.finished.connect(lambda r=reply: self._onReply(r))

    def _onReply(self, reply):
        from qgis.utils import iface
        from qgis.PyQt.QtWidgets import QPushButton
        batch_idx, n_feats = self._inflight.pop(reply, (-1, 0))
        ok = (reply.error() == _QNETWORKREPLY_NOERROR)
        data = reply.readAll().data()  # bytes
        try:
            resp = json.loads((data or b"{}").decode("utf-8", errors="ignore"))
        except Exception:
            resp = {}
        reply.deleteLater()

        # If we were canceled, just propagate result (as error if aborted) and do nothing else.
        if self._canceled:
            self.batchFinished.emit(batch_idx, n_feats, False, resp)
            return

        if not ok:
            _log(Qgis.Critical, f"Pošiljanje paketa {batch_idx} je spodletelo: {resp}")
            if not self._popup_shown:
                try:
                    msgw = iface.messageBar().createMessage(
                        "QNarcIS",
                        "Pri pošiljanju je prišlo do napake. Za podrobnosti odprite Panel dnevnika → QNarcIS."
                    )
                    btn = QPushButton("Odpri dnevnik")
                    btn.clicked.connect(self._open_qnarcis_log)
                    msgw.layout().addWidget(btn)
                    iface.messageBar().pushWidget(msgw, Qgis.Critical)
                except Exception:
                    pass
                self._popup_shown = True

        self.batchFinished.emit(batch_idx, n_feats, ok, resp)
        self._pump()


# --------------------------- QgsTask implementation ---------------------------
class LayerUploadTask(QgsTask):
    """
    Builds batches in a background thread and hands them to the Poster in the GUI thread
    via a queued signal. Deterministic progress: 0–100 % by number of features (replies).
    """

    DEFAULT_API_URL = "https://narcis.gov.si/ords/narcis/hr/qnarcis/poslji-sloj"
    DEFAULT_BATCH_SIZE = 10              # fixed NUMBER of features per batch
    DEFAULT_MAX_CONCURRENT = 1           # max parallel POST requests
    DEFAULT_TARGET_CRS_AUTHID = None
    DEFAULT_STOP_AFTER = None

    postRequested = pyqtSignal(int, int, QByteArray)

    def __init__(
        self,
        layer,
        username,
        password,
        api_url=DEFAULT_API_URL,
        batch_size_parts=DEFAULT_BATCH_SIZE,   # kept name for backward compatibility
        max_concurrent=DEFAULT_MAX_CONCURRENT,
        target_crs_authid=DEFAULT_TARGET_CRS_AUTHID,
        stop_after_batches=DEFAULT_STOP_AFTER,
        description="NARCIS upload",
        selected_only=False,
        max_units_limit=None   # None = unlimited. Unit = singlepart(1) or multipart(#parts)
    ):
        super().__init__(description, QgsTask.CanCancel)
        if not username or not password:
            raise ValueError("username and password are required")

        self._layer = layer
        self._username = username
        self._password = password

        self._api_url = f"{api_url}/"
        self._batch_size = max(1, int(batch_size_parts))
        self._max_concurrent = int(max_concurrent)
        self._target_authid = target_crs_authid
        self._stop_after = int(stop_after_batches) if stop_after_batches else None
        self._selected_only = bool(selected_only)
        self._max_units_limit = max_units_limit if (max_units_limit is None or isinstance(max_units_limit, int)) else 1000

        self.session_uid = str(uuid.uuid4())

        self._geom_type = QgsWkbTypes.geometryType(layer.wkbType())

        self._layer_id = None

        # runtime
        self._xform = None
        self._bbox = None
        self._field_names = []

        # totals for UI and final summary
        self._total_batches = 0
        self._total_features = 0
        self._target_features = 0

        # live counters
        self._posted_batches = 0
        self._replied_batches = 0
        self._ok_features = 0
        self._err_features = 0
        self._invalid_features = 0
        self._units_count = 0
        self._hit_units_limit = False

        # batch buffer
        self._prefix = b""
        self._suffix = b"]}"
        self._comma = b","
        self._buf_features = []
        self._buf_len_count = 0
        self._batch_idx = 0

        # Poster in MAIN thread + wiring
        self._poster = _Poster(self._api_url, self._username, self._password, self._max_concurrent, parent=QgsApplication.instance())
        self.postRequested.connect(self._poster.onPostRequested, _QT_QUEUED_CONNECTION)
        self._poster.batchFinished.connect(self._on_batch_replied, _QT_QUEUED_CONNECTION)

        # Permanent “task started” message (closed on finish)
        try:
            from qgis.utils import iface
            self._start_msg_item = iface.messageBar().createMessage(
                "QNarcIS", "Pošiljanje sloja se je začelo …"
            )
            iface.messageBar().pushWidget(self._start_msg_item, Qgis.Info)
        except Exception:
            self._start_msg_item = None

    # NEW: ensure UI cancel aborts network immediately
    def cancel(self):
        try:
            super().cancel()
        except Exception:
            try:
                QgsTask.cancel(self)
            except Exception:
                pass
        try:
            self._poster.cancel_all()
        except Exception:
            pass

        self._poster.restore_timeout()

    def run(self):
        try:
            # Optional CRS transform
            self._xform = None
            if self._target_authid:
                trg = QgsCoordinateReferenceSystem(self._target_authid)
                if self._layer.crs().isValid() and trg.isValid() and self._layer.crs() != trg:
                    self._xform = QgsCoordinateTransform(self._layer.crs(), trg, QgsProject.instance())

            self._bbox = _bbox_array(self._layer, self._xform)
            self._field_names = [f.name() for f in self._layer.fields()]

            # Deterministic feature count for progress
            try:
                if self._selected_only:
                    self._total_features = len(self._layer.selectedFeatureIds())
                else:
                    self._total_features = int(self._layer.featureCount())
            except Exception:
                self._total_features = 0

            self._total_batches = math.ceil(self._total_features / float(self._batch_size)) if self._total_features else 0

            self.setProgress(0.0)
            self._start_new_batch()

            it = (iter(self._layer.selectedFeatures()) if self._selected_only else self._layer.getFeatures())
            while True:
                if self.isCanceled():
                    # hard stop: also abort any network activity
                    try:
                        self._poster.cancel_all()
                    except Exception:
                        pass
                    return False

                try:
                    feat = next(it)
                except StopIteration:
                    if self._buf_features:
                        self._flush_batch()
                    break

                g = feat.geometry()

                # Invalid geometries: log to QNarcIS tab and skip
                try:
                    problems = g.validateGeometry(QgsGeometry.ValidatorGeos)
                except Exception:
                    problems = []
                if problems:
                    self._invalid_features += 1
                    try:
                        what = ", ".join([e.what() for e in problems])
                    except Exception:
                        what = "Neveljavna geometrija"
                    _log(Qgis.Warning, f"Preskočena neveljavna geometrija (feature id={feat.id()}): {what}")
                    continue

                # Enforce max_units_limit
                parts = _parts_in(g, self._geom_type)
                if self._max_units_limit is not None and (self._units_count + parts) > int(self._max_units_limit):
                    if self._buf_features:
                        self._flush_batch()
                    self._hit_units_limit = True
                    break

                self._units_count += parts

                # Build GeoJSON feature
                geom = feat.geometry()
                if self._xform is not None and not geom.isEmpty():
                    geom.transform(self._xform)
                geom_dict = json.loads(geom.asJson())

                props = {}
                for name in self._field_names:
                    try: props[name] = _to_jsonable(feat[name])
                    except Exception: props[name] = None

                fid_name = getattr(self, "fid_field_name", None)
                if fid_name and fid_name in props and props[fid_name] is not None:
                    try:
                        props["fid"] = int(props[fid_name])
                    except Exception:
                        props["fid"] = int(feat.id())
                else:
                    props["fid"] = int(feat.id())

                fb = json.dumps({"type":"Feature","geometry":geom_dict,"properties":props}, ensure_ascii=False).encode("utf-8")

                # Add to batch; limit is NUMBER of features per batch
                if self._buf_features:
                    self._buf_features.append(self._comma)
                self._buf_features.append(fb)
                self._buf_len_count += 1

                if self._buf_len_count >= self._batch_size:
                    self._flush_batch()

                if self._stop_after and self._batch_idx >= self._stop_after:
                    break

                QThread.msleep(0)

            # Wait for all replies (unless canceled)
            while self._posted_batches > self._replied_batches:
                if self.isCanceled():
                    try:
                        self._poster.cancel_all()
                    except Exception:
                        pass
                    return False
                QThread.msleep(20)

            return True

        except Exception as e:
            _log(Qgis.Critical, f"Napaka opravila: {e}")
            try:
                self._poster.cancel_all()
            except Exception:
                pass
            return False
        
        finally:
            try:
                self._poster.restore_timeout()
            except Exception:
                pass

    def finished(self, result):
        from qgis.utils import iface

        # Close the “started” sticky message, if present
        try:
            if getattr(self, '_start_msg_item', None) is not None:
                iface.messageBar().popWidget(self._start_msg_item)
        except Exception:
            pass

        # Compose final sticky message (UI) + ensure details are in QNarcIS log
        total_feats = self._total_features if self._total_features else self._target_features
        ok = self._ok_features
        err = self._err_features
        skipped = self._invalid_features
        title = "QNarcIS"

        if total_feats <= 0:
            text = "Nič za poslati."
            level = Qgis.Warning
        else:
            text = f"{ok} od {total_feats} objektov je bilo uspešno prenesenih."
            if err:
                text += " Podrobnosti poglejte v dnevniku napak."
            if skipped:
                text += f" {skipped} neveljavnih geometrij je bilo preskočenih."
            if self._hit_units_limit and self._max_units_limit is not None:
                text += f" Dosežen je bil trenutno dovoljen limit {int(self._max_units_limit)} objektov; pošiljanje je bilo ustavljeno."
            level = Qgis.Success if err == 0 and result and not self._hit_units_limit else Qgis.Warning
            if not result:
                level = Qgis.Warning

        # Log final summary to QNarcIS tab as well
        _log(level, text)

        # NEW: If at least one feature succeeded and we have a layer_id, show a link button
        if ok > 0 and self._layer_id is not None:
            try:
                from qgis.PyQt.QtWidgets import QPushButton
                from qgis.PyQt.QtGui import QDesktopServices
                url = QUrl(f"https://narcis.gov.si/ords/r/narcis/narcis/spletni-gis1?p257_izbrani_sloj_id={int(self._layer_id)}&p257_zoom=Y")

                msgw = iface.messageBar().createMessage(title, text)
                btn = QPushButton("Povezava do naloženega sloja.")
                btn.clicked.connect(lambda: QDesktopServices.openUrl(url))
                msgw.layout().addWidget(btn)

                iface.messageBar().pushWidget(msgw, level)
            except Exception:
                # Fallback to plain message if button cannot be shown
                try:
                    iface.messageBar().pushMessage(title, text, level=level, duration=0)
                except Exception:
                    pass
        else:
            # Original behavior when there’s nothing to link to
            try:
                iface.messageBar().pushMessage(title, text, level=level, duration=0)
            except Exception:
                pass

        if not result:
            _log(Qgis.Warning, "Naloga pošiljanja je bila preklicana ali neuspešna.")

        self._poster.restore_timeout()

    # -------------------- helpers --------------------
    def _start_new_batch(self):
        props = {
            "ime_sloja": self._layer.name(),
            "opomba": "Poslano iz QGIS",
            "uname": self._username,
            "bbox": self._bbox,
            "uid": self.session_uid,                   # SAME for all batches in this session
            "batch_index": self._batch_idx + 1,
            "batch_count": self._total_batches or None,
            "feature_count": 0,
            "fid_source": getattr(self, "fid_field_name", None) or "qgis_feature_id"
        }
        props_b = json.dumps(props, ensure_ascii=False).encode("utf-8")
        self._prefix = b'{"type":"FeatureCollection","properties":' + props_b + b',"features":['
        self._buf_features = []         # [fb, ',', fb, ',', ...]
        self._buf_len_count = 0

    def _flush_batch(self):
        self._batch_idx += 1
        n_feats = max(0, (len(self._buf_features) + 1) // 2) if self._buf_features else 0

        # Patch feature_count + batch_index
        props = json.loads(self._prefix.split(b'"features":',1)[0].split(b'"properties":',1)[1].rstrip(b','))  # quick hack
        props["feature_count"] = n_feats
        props["batch_index"] = self._batch_idx
        new_prefix = b'{"type":"FeatureCollection","properties":' + json.dumps(props, ensure_ascii=False).encode("utf-8") + b',"features":['

        if self._buf_features:
            body = new_prefix + b"".join(self._buf_features) + self._suffix
        else:
            body = new_prefix + self._suffix

        payload = QByteArray(body)

        # Emit to poster (queued to GUI thread)
        self.postRequested.emit(self._batch_idx, n_feats, payload)
        self._posted_batches += 1
        self._target_features += n_feats

        # Prepare next
        self._start_new_batch()

    @pyqtSlot(int, int, bool, dict)
    def _on_batch_replied(self, batch_idx, n_feats, ok, resp):
        self._replied_batches += 1
        if ok:
            self._ok_features += n_feats
            # remember layer_id if provided (same for the whole session)
            try:
                lid = resp.get("layer_id", None)
                if lid is not None and self._layer_id is None:
                    self._layer_id = int(lid)
            except Exception:
                pass
        else:
            self._err_features += n_feats

        # Deterministic progress: by number of features replied (ok + err)
        sent_or_failed = self._ok_features + self._err_features
        denom = max(1, self._total_features)
        self.setProgress(100.0 * float(sent_or_failed) / float(denom))
