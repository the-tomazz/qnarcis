from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QFormLayout, QComboBox, QFrame
)

_PREFERRED_NAMES = ["fid", "id", "gid", "oid", "eid", "object_id", "featureid"]

_HELPER_TEXT_COMBO = (
    "Ob prenosu sloja se kot enolični identifikator posameznih objektov privzeto "
    "uporabi atribut z imenom <b>fid</b>. Izbrani sloj tega atributa nima, zato "
    "izberite <b>številski</b> atribut, ki naj bo uporabljen kot enolični identifikator. "
    "Če ne izberete ničesar, se uporabi <b>QGIS ID objekta</b>"
)

_INFO_TEXT_FID_EXISTS = (
    "Kot enolični identifikator bo ob prenosu sloja uporabljen atribut <b>fid</b>."
)

_INFO_TEXT_NO_INT = (
    "V izbranem sloju ni številskih atributov, ki bi lahko bili uporabljeni kot "
    "enolični identifikator - zato bo ob pošiljanju sloja uporabljen <b>QGIS ID objekta</b>."
)

class SendSelectionDialog(QDialog):
    def __init__(self, layer, selected_count, parent=None):
        super().__init__(parent)
        layer_name = layer.name().strip()
        self.setWindowTitle(f"QNarcIS - pošiljanje sloja: {layer_name}")
        self._layer = layer
        self._sel_count = int(selected_count)
        self._fid_field_name = None
        self._selected_only = True

        vbox = QVBoxLayout(self)
        vbox.setSpacing(6)
        vbox.setContentsMargins(12, 10, 12, 12)

        field_names = [f.name() for f in layer.fields()]
        lower_map = {nm.lower(): nm for nm in field_names}
        has_fid = "fid" in lower_map
        resolved_fid_name = lower_map.get("fid")

        int_field_names = []
        if not has_fid:
            for f in layer.fields():
                tn = (f.typeName() or "").lower()
                if tn in ("integer", "int", "int4", "int8", "bigint", "smallint",
                          "uint", "uint8", "uint16", "uint32", "uint64") or f.type() in (2, 4):
                    int_field_names.append(f.name())

        if has_fid:
            helper = QLabel(_INFO_TEXT_FID_EXISTS)
        else:
            if int_field_names:
                helper = QLabel(_HELPER_TEXT_COMBO)
            else:
                helper = QLabel(_INFO_TEXT_NO_INT)
        helper.setWordWrap(True)
        helper.setStyleSheet("color: #374151; margin-bottom: 6px;")
        vbox.addWidget(helper)

        if (not has_fid) and int_field_names:
            form = QFormLayout()
            form.setHorizontalSpacing(8)
            form.setVerticalSpacing(4)

            self.cmb = QComboBox()
            self.cmb.setObjectName("cmbFidField")
            self.cmb.setEditable(False)
            self.cmb.addItem("— uporabi QGIS ID objekta —", userData=None)
            self.cmb.insertSeparator(1)
            for name in int_field_names:
                self.cmb.addItem(name, userData=name)
            preferred_lower = [s.lower() for s in _PREFERRED_NAMES]
            default_index = 0
            for i in range(2, self.cmb.count()):
                nm = self.cmb.itemData(i)
                if nm and nm.lower() in preferred_lower:
                    default_index = i
                    break
            self.cmb.setCurrentIndex(default_index)

            form.addRow("ID atribut:", self.cmb)
            vbox.addLayout(form)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        line.setStyleSheet("color: #000000; background: #000000; max-height: 1px; margin-top: 8px; margin-bottom: 6px;")
        vbox.addWidget(line)

        if self._sel_count > 0:
            question = f"V sloju je izbranih {self._sel_count} objektov.\nAli želite poslati te izbrane objekte?"
        else:
            question = "Sloj za pošiljanje nima izbranih objektov.\nAli želite poslati celoten sloj (vse objekte)?"
        lbl_question = QLabel(question)
        lbl_question.setWordWrap(True)
        lbl_question.setStyleSheet("margin-bottom: 8px;")
        vbox.addWidget(lbl_question)

        btns = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No, Qt.Horizontal, self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        vbox.addWidget(btns)

        self.setMinimumWidth(500)

        if has_fid:
            self._fid_field_name = resolved_fid_name

    @property
    def fid_field_name(self):
        return self._fid_field_name

    @property
    def selected_only(self):
        return self._selected_only

    def accept(self):
        if self._fid_field_name is None:
            cmb = self.findChild(QComboBox, "cmbFidField")
            if cmb is not None:
                data = cmb.currentData()
                self._fid_field_name = data if (isinstance(data, str) and data.strip()) else None

        self._selected_only = (self._sel_count > 0)
        super().accept()
