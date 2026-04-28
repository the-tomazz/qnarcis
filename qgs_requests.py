# qgs_requests.py
# Minimal 'requests.get' compatible shim using QGIS network stack
# (proxy/auth settings handled by QgsNetworkAccessManager via QgsBlockingNetworkRequest)

import json

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

_QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE = getattr(QNetworkRequest, 'HttpStatusCodeAttribute', None)
if _QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE is None:
    _QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE = QNetworkRequest.Attribute.HttpStatusCodeAttribute


class _QgsResponse:
    """
    Minimal 'requests.Response'-like wrapper around QgsNetworkReplyContent.

    Supports:
      - .status_code
      - .text
      - .content
      - .json()
      - .raise_for_status()
      - .ok
    """

    def __init__(self, reply_content, error_code=None, error_message=""):
        self._reply = reply_content
        self._error_code = error_code
        self._error_message = error_message or ""
        self.headers = {}
        self.reason = ""

        status = None
        try:
            status = self._reply.attribute(_QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE)
        except Exception:
            status = None

        try:
            self.status_code = int(status) if status is not None else 0
        except Exception:
            self.status_code = 0

        try:
            self.content = bytes(self._reply.content())
        except Exception:
            self.content = b""

        try:
            self.text = self.content.decode("utf-8")
        except Exception:
            self.text = self.content.decode("utf-8", errors="replace")

        if self._error_message:
            self.reason = self._error_message.strip()
        elif self.status_code:
            self.reason = "HTTP {0}".format(self.status_code)

    def json(self):
        # requests raises JSONDecodeError; keep it simple and let exceptions bubble up
        return json.loads(self.text if self.text is not None else "")

    def raise_for_status(self):
        if (self._error_code and int(self._error_code) != 0):
            msg = self._error_message.strip() if self._error_message else "Network error"
            raise Exception(msg)
        if 400 <= (self.status_code or 0):
            # Prefer QGIS-provided error message; otherwise generic HTTP status
            msg = self._error_message.strip() if self._error_message else ""
            if not msg:
                msg = "HTTP {0}".format(self.status_code)
            raise Exception(msg)

    @property
    def ok(self):
        return 200 <= (self.status_code or 0) < 400


def _apply_headers(req, headers):
    if not headers:
        return
    for k, v in headers.items():
        if v is None:
            continue
        try:
            req.setRawHeader(str(k).encode("utf-8"), str(v).encode("utf-8"))
        except Exception:
            # ignore a single bad header
            pass


def get(url, headers=None, timeout=None, allow_redirects=True):
    """
    Drop-in-ish replacement for requests.get(url, headers=..., timeout=..., allow_redirects=...)
    Note: allow_redirects is effectively handled by QGIS/Qt; kept for API compatibility.
    """
    req = QNetworkRequest(QUrl(url))

    # Best-effort per-request timeout (Qt supports setTransferTimeout in newer versions)
    if timeout is not None:
        try:
            req.setTransferTimeout(int(float(timeout) * 1000))
        except Exception:
            pass

    _apply_headers(req, headers)

    b = QgsBlockingNetworkRequest()
    err = b.get(req)
    reply = b.reply()

    err_msg = ""
    try:
        err_msg = b.errorMessage()
    except Exception:
        err_msg = ""

    return _QgsResponse(reply, error_code=err, error_message=err_msg)


class _RequestsCompat:
    @staticmethod
    def get(url, headers=None, timeout=None, allow_redirects=True):
        return get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)


# Import this as: from .qgs_requests import requests
requests = _RequestsCompat()
