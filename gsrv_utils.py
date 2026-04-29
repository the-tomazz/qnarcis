from .qgs_requests import requests
from lxml import etree

from qgis.core import QgsApplication, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urlunsplit, urlsplit, quote

_QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE = getattr(QNetworkRequest, 'HttpStatusCodeAttribute', None)
if _QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE is None:
    _QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE = QNetworkRequest.Attribute.HttpStatusCodeAttribute

class _QgsNetworkResponse:
    def __init__(self, status_code, content, reason="", error_code=None):
        self.status_code = int(status_code or 0)
        self.content = content or b""
        self.text = self.content.decode("utf-8", errors="replace")
        self.reason = reason or ("HTTP {0}".format(self.status_code) if self.status_code else "")
        self._error_code = error_code

    @property
    def ok(self):
        return 200 <= (self.status_code or 0) < 400

    def raise_for_status(self):
        try:
            error_code = int(self._error_code)
        except Exception:
            error_code = int(getattr(self._error_code, 'value', 0) or 0)
        if error_code:
            raise Exception(self.reason or "Network error")
        if 400 <= (self.status_code or 0):
            raise Exception(self.reason or "HTTP {0}".format(self.status_code))

def _qgs_auth_get(url, authcfg):
    req = QNetworkRequest(QUrl(url))
    reply = QgsNetworkAccessManager.blockingGet(req, authcfg or '', True)
    try:
        status = reply.attribute(_QNETWORKREQUEST_HTTP_STATUS_CODE_ATTRIBUTE)
    except Exception:
        status = 0
    try:
        content = bytes(reply.content())
    except Exception:
        content = b""
    try:
        reason = reply.errorString()
    except Exception:
        reason = ""
    try:
        error_code = reply.error()
    except Exception:
        error_code = None
    return _QgsNetworkResponse(status, content, reason, error_code)

def build_url_with_params(base_url, extra):
    """
    Append/override query params on base_url and return the new URL.
    Keeps existing params if present.
    """
    scheme, netloc, path, query, frag = urlsplit(base_url)
    q = dict(parse_qsl(query, keep_blank_values=True))
    q.update(extra)
    # keep commas and parentheses readable for CQL
    new_query = urlencode(q, doseq=True, safe=',()', quote_via=quote)
    return urlunsplit((scheme, netloc, path, new_query, frag))

def findGeoserverAuthConfig():
    am = QgsApplication.authManager()
    configs = am.availableAuthMethodConfigs()
    for auth_id, config in configs.items():
        # config might be a string (name) or QgsAuthMethodConfig object
        if hasattr(config, 'name'):
            config_name = config.name()
        else:
            config_name = config
        if isinstance(config_name, str) and config_name.startswith('narcis_gsrv_'):
            ok, _ = am.loadAuthenticationConfig(auth_id, config, True)
            if ok:
                cmap = config.configMap()
                uname = (cmap.get('username') or '').strip()
                return auth_id, config_name, uname
    return None, None, None

def build_getcap_url(source, provider_key, version = None):
    """
    Build a GetCapabilities URL from a QGIS layer 'source' string and providerKey ("WMS"/"WFS").
    - Picks the first url='...' without a query if available; otherwise the first one.
    - Preserves existing query params but forces SERVICE and REQUEST (and VERSION if provided).
    """
    urls = re.findall(r"url='([^']+)'", source)
    if not urls:
        raise ValueError("No url='...' found in source")

    # Prefer a URL without query; otherwise take the first
    chosen = next((u for u in urls if not urlparse(u).query), urls[0])

    svc = provider_key.upper().strip()
    if svc not in ("WMS", "WFS"):
        raise ValueError(f"Unsupported providerKey: {provider_key}")

    p = urlparse(chosen)
    q = dict(parse_qsl(p.query, keep_blank_values=True))

    q["SERVICE"] = svc
    q["REQUEST"] = "GetCapabilities"
    if version:  # e.g., "1.3.0" for WMS or "2.0.0" for WFS if you want to pin it
        q["VERSION"] = version

    new_query = urlencode(q, doseq=True)
    return urlunparse(p._replace(query=new_query))


def get_layers_from_capabilities(xml_text, provider_key):
    """
    Return a set of layer/feature-type names from WMS or WFS GetCapabilities.
    Uses explicit namespaces: WMS 1.1/1.3 and WFS 2.0 (GeoServer).
    """
    if not xml_text or not xml_text.strip():
        raise ValueError("Empty GetCapabilities response")

    try:
        root = etree.fromstring(xml_text.encode('utf-8'))
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Invalid GetCapabilities XML.") from exc

    namespaces = {
        'wms': 'http://www.opengis.net/wms',
        'wfs': 'http://www.opengis.net/wfs/2.0',
    }

    svc = provider_key.upper().strip()
    if svc == "WMS":
        layers = root.xpath(
            '//wms:Layer/wms:Layer/wms:Name',
            namespaces=namespaces
        )
    elif svc == "WFS":
        layers = root.xpath(
            '//wfs:FeatureTypeList/wfs:FeatureType/wfs:Name',
            namespaces=namespaces
        )
    else:
        raise ValueError(f"Unsupported providerKey: {provider_key}")

    return {layer.text.strip() for layer in layers if layer is not None and layer.text}


def get_wms_layers_difference(source, provider_key, authcfg):

    url = build_getcap_url(source, provider_key)

    # Step 1: No-auth request
    response_no_auth = requests.get(url)
    try:
        response_no_auth.raise_for_status()
    except Exception as exc:
        raise Exception(f"Unauthenticated request failed: {exc}") from exc
    layers_no_auth = get_layers_from_capabilities(response_no_auth.text, provider_key)

    # Step 2: Authenticated request through QGIS auth manager.
    response_auth = _qgs_auth_get(url, authcfg)
    try:
        response_auth.raise_for_status()
    except Exception as exc:
        raise Exception(f"Authenticated request failed: {exc}") from exc
    layers_auth = get_layers_from_capabilities(response_auth.text, provider_key)

    return layers_auth - layers_no_auth
