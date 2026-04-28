from qgis.PyQt import QtWidgets, QtGui
from qgis.PyQt.QtCore import QRegularExpression

_QSIZEPOLICY_MINIMUM = getattr(QtWidgets.QSizePolicy, "Minimum", None)
if _QSIZEPOLICY_MINIMUM is None:
    _QSIZEPOLICY_MINIMUM = QtWidgets.QSizePolicy.Policy.Minimum

_QSIZEPOLICY_EXPANDING = getattr(QtWidgets.QSizePolicy, "Expanding", None)
if _QSIZEPOLICY_EXPANDING is None:
    _QSIZEPOLICY_EXPANDING = QtWidgets.QSizePolicy.Policy.Expanding

_QLINEEDIT_PASSWORD = getattr(QtWidgets.QLineEdit, "Password", None)
if _QLINEEDIT_PASSWORD is None:
    _QLINEEDIT_PASSWORD = QtWidgets.QLineEdit.EchoMode.Password

_QLINEEDIT_NORMAL = getattr(QtWidgets.QLineEdit, "Normal", None)
if _QLINEEDIT_NORMAL is None:
    _QLINEEDIT_NORMAL = QtWidgets.QLineEdit.EchoMode.Normal

_QLINEEDIT_TRAILING_POSITION = getattr(QtWidgets.QLineEdit, "TrailingPosition", None)
if _QLINEEDIT_TRAILING_POSITION is None:
    _QLINEEDIT_TRAILING_POSITION = QtWidgets.QLineEdit.ActionPosition.TrailingPosition

class PasswordEdit(QtWidgets.QLineEdit):
    """
    A LineEdit with icons to show/hide password entries inside the text box.
    """
    CSS = '''QLineEdit {
        border-radius: 5px;
        height: 30px;
        padding-right: 25px;  # Ensure space for the toggle icon
    }
    '''
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Set styles
        self.setStyleSheet(self.CSS)
        
        # Load icons for visibility toggle
        self.visibleIcon = QtGui.QIcon(":/plugins/q_narcis/icons/eye-svgrepo-com.svg")       # Show password
        self.hiddenIcon = QtGui.QIcon(":/plugins/q_narcis/icons/eye-slash-svgrepo-com.svg")    # Hide password
        
        # Set initial state to password hidden
        self.setEchoMode(_QLINEEDIT_PASSWORD)
        self.password_shown = False
        
        # Add the eye icon inside the text box
        self.togglePasswordAction = self.addAction(self.hiddenIcon, _QLINEEDIT_TRAILING_POSITION)
        self.togglePasswordAction.triggered.connect(self.on_toggle_password_Action)

    def on_toggle_password_Action(self):
        """Toggle password visibility on icon click."""
        if not self.password_shown:
            self.setEchoMode(_QLINEEDIT_NORMAL)
            self.password_shown = True
            self.togglePasswordAction.setIcon(self.visibleIcon)
        else:
            self.setEchoMode(_QLINEEDIT_PASSWORD)
            self.password_shown = False
            self.togglePasswordAction.setIcon(self.hiddenIcon)

class CustomCredentialsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("NarcIS - prijava")
        
        # Create the layout
        layout = QtWidgets.QFormLayout(self)
        
        # Username field with custom label
        self.username_label = QtWidgets.QLabel("Uporabniški elektronski naslov:")
        self.username_input = QtWidgets.QLineEdit(self)
        layout.addRow(self.username_label, self.username_input)
        
        # Password field with custom label using PasswordEdit class
        self.password_label = QtWidgets.QLabel("Geslo:")
        self.password_input = PasswordEdit(self)
        layout.addRow(self.password_label, self.password_input)

        custom_text = """<br>Vpisati je potrebno (samo pri prvi uporabi) vaš <b>uporabniški elektronski naslov (email) in geslo za portal NarcIS (https://narcis.gov.si).</b>
<br><br>
<b>Bodite pozorni</b>, da vpišete pravilni uporabniški elektronski naslov in geslo - če se vtičnik ne bo mogel prijaviti na sloj za shranjevanje poligonov,
vas bo ob naslednji uporabi sloja spet vprašal za <b>NarcIS uporabniški elektronski naslov in geslo.</b>
<br><br>
<b>Če se želite prijaviti z drugim elektronskim naslovom in geslom</b> je potrebno v nastavitvah QGIS-a (Settings/Options/Authentication),
<b>zbrisati konfiguracijo z imenom qnarcis_oauth</b> in vtičnik vam bo spet ponudil možnost za vpis gesla.
<br><br>
Vpisani prijavni podatki v NarcIS portal so v enkriptirani obliki shranjeni v lokalni QGIS bazi prijavnih podatkov,
zaščiteni z geslom, ki v splošnem ni enako kot geslo za NarcIS portal <b>(master authentication password)</b>.
<br><br>
Če gesla za QGIS bazo prijavnih podatkov (master authentication password) še nimate nastavljenega, vam bo QGIS v naslednjem koraku ponudil tudi nastavitev le-tega."""

        self.custom_text = QtWidgets.QLabel(custom_text)
        self.custom_text.setWordWrap(True)
        layout.addRow(self.custom_text)

        spacer = QtWidgets.QSpacerItem(20, 10, _QSIZEPOLICY_MINIMUM, _QSIZEPOLICY_EXPANDING)
        layout.addItem(spacer)
        
        # OK and Cancel buttons
        self.ok_button = QtWidgets.QPushButton("OK", self)
        self.cancel_button = QtWidgets.QPushButton("Cancel", self)
        
        # Add buttons to the layout
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addRow(button_layout)
        
        # Connect buttons
        self.cancel_button.clicked.connect(self.reject)

        # Connect the OK button to the custom validation logic
        self.ok_button.clicked.connect(self.validate_and_accept)

        self.setMinimumWidth(600)
        self.adjustSize()
        self.setFixedSize(self.width(), self.height())

    def validate_and_accept(self):
        # Get the text from the username input
        email = self.username_input.text()
        
        # Define a basic email validation regex pattern
        email_regex = QRegularExpression(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        
        # Check if the email is valid
        if not email_regex.match(email).hasMatch():
            # Show an error message if the email is not valid
            QtWidgets.QMessageBox.warning(self, "NAPAKA: vnos elektronskega naslova", "Prosim vnesite veljaven elektronski naslov.")
        else:
            # If the email is valid, accept the dialog
            self.accept()


    def get_credentials(self):
        """Retrieve the entered username and password."""
        return self.username_input.text(), self.password_input.text()
