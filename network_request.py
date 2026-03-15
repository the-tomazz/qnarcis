import json
from qgis.core import QgsGeometry, QgsPointXY, QgsCoordinateReferenceSystem, QgsCoordinateTransform
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QUrl, QByteArray, QUrlQuery, QEventLoop

class NetworkRequest():
    def __init__(self, onSuccess = None, onError = None):
        super().__init__()
        self.manager = QgsNetworkAccessManager.instance()
        self.manager.finished.connect(self.handle_response)
        self.onSuccess = onSuccess
        self.onError = onError
        self.url = None

    def handle_response(self, reply):
        if reply.error() == QNetworkReply.NoError:
            # Successful response
            rdata = reply.readAll().data()
            response_data = '{}'

            if rdata:
                response_data = rdata.decode('utf-8')
                response_json = json.loads(response_data)
                if self.onSuccess:
                    self.onSuccess(reply, response_json)
        else:
            # Handle error
            if self.onError:
                self.onError(reply, reply.errorString())

        self.url = None

    def send_request(self, username, password, payload):
        self.url = url = "https://narcis.gov.si/ords/narcis/qnarcis-protected/send-polygon/"
        
        # Prepare the request
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"uporabnik", f"{username}".encode('utf-8'))
        request.setRawHeader(b"key", f"{password}".encode('utf-8'))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        
        # Send the POST request with the WKT polygon
        self.manager.post(request, QByteArray(payload.encode('utf-8')))