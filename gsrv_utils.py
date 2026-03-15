import base64
from .qgs_requests import requests
from lxml import etree

from qgis.core import QgsApplication

import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urlunsplit, urlsplit, quote

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


def get_wms_layers_difference(source, provider_key, username, password):

    url = build_getcap_url(source, provider_key)
    
    credentials = f"{username}:{password}"
    b64_auth = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    
    # Step 1: No-auth request
    response_no_auth = requests.get(url)
    try:
        response_no_auth.raise_for_status()
    except Exception as exc:
        raise Exception(f"Unauthenticated request failed: {exc}") from exc
    layers_no_auth = get_layers_from_capabilities(response_no_auth.text, provider_key)

    # Step 2: Authenticated request with raw base64 header
    headers_with_auth = {'Authorization': f"Basic {b64_auth}"}

    response_auth = requests.get(url, headers=headers_with_auth)
    try:
        response_auth.raise_for_status()
    except Exception as exc:
        raise Exception(f"Authenticated request failed: {exc}") from exc
    layers_auth = get_layers_from_capabilities(response_auth.text, provider_key)

    return layers_auth - layers_no_auth
