"""Small per-game editor for name OCR and existing voice presets."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import CharacterVoices, character_name
from ..speech import voice_details, voice_label
from .ocr_config import OCRConfigDialog


class CharacterVoicesDialog(QDialog):
    preview_requested = Signal(int)

    def __init__(self, parent, title, profile, model_path, frame=None, detected_name=""):
        super().__init__(parent)
        self.setWindowTitle("Character voices (experimental)")
        bounds = self.screen().availableGeometry().adjusted(12, 36, -12, -12)
        self.setMinimumSize(min(480, bounds.width()), min(320, bounds.height()))
        self.resize(min(780, bounds.width()), min(600, bounds.height()))
        self._profile = CharacterVoices.from_dict(profile.to_dict())
        self._model_path, self._frame = model_path, frame
        self._model_matches = not profile.model_path or profile.matches_model(model_path)
        try:
            self._speakers = voice_details(model_path)
        except (OSError, ValueError, TypeError, KeyError):
            self._speakers = []

        outer = QVBoxLayout(self)
        actions = QHBoxLayout()
        actions.addStretch()
        self._ok = QPushButton("OK")
        self._ok.setDefault(True)
        self._ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for button in (self._ok, cancel):
            button.setMinimumHeight(44)
            actions.addWidget(button)
        outer.addLayout(actions)
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #ff6b6b")
        self._error.hide()
        outer.addWidget(self._error)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        game = QLabel(f"Capture source: {title}")
        game.setTextFormat(Qt.TextFormat.PlainText)
        game.setWordWrap(True)
        body.addWidget(game)
        self._enabled = QCheckBox("Automatically select a voice from the speaker name")
        self._enabled.setChecked(profile.enabled)
        body.addWidget(self._enabled)
        help_text = QLabel(
            "Select the speaker-name label, then assign each name a preset. "
            "Unknown names and narration use your default voice or clone. "
            "Gender defaults to the preset label. You can override it for translation."
        )
        help_text.setWordWrap(True)
        body.addWidget(help_text)
        self._region_button = QPushButton("Select name area…")
        self._region_button.setMinimumHeight(44)
        self._region_button.setEnabled(frame is not None)
        self._region_button.clicked.connect(self._select_region)
        body.addWidget(self._region_button)
        self._region_status = QLabel()
        self._region_status.setWordWrap(True)
        body.addWidget(self._region_status)
        self._refresh_region_status()
        self._detected = QLabel()
        self._detected.setWordWrap(True)
        body.addWidget(self._detected)

        fields = QFormLayout()
        self._name = QLineEdit()
        self._name.setMaxLength(64)
        self._name.setAccessibleName("Character name")
        fields.addRow("Character name:", self._name)
        self._preset = self._preset_combo()
        self._preset.setAccessibleName("Character voice preset")
        fields.addRow("Voice preset:", self._preset)
        self._gender = self._gender_combo()
        fields.addRow("Character gender:", self._gender)
        body.addLayout(fields)
        add_row = QHBoxLayout()
        add = QPushButton("Add / update")
        add.clicked.connect(self._add_name)
        test = QPushButton("Test selected voice")
        test.setEnabled(self._preset.currentData() is not None)
        self._preset.currentIndexChanged.connect(lambda: test.setEnabled(self._preset.currentData() is not None))
        test.clicked.connect(self._preview)
        for button in (add, test):
            button.setMinimumHeight(44)
            add_row.addWidget(button)
        body.addLayout(add_row)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Character name", "Voice preset", "Character gender"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(150)
        self._table.setAccessibleName("Character voice assignments")
        self._table.itemSelectionChanged.connect(self._edit_selected)
        body.addWidget(self._table)
        remove = QPushButton("Remove selected")
        remove.setMinimumHeight(44)
        remove.clicked.connect(self._remove_selected)
        body.addWidget(remove)
        for name, index in profile.voices.items():
            self._append_row(name, index if self._model_matches else None, profile.gender_for(name))
        assigned = {name.casefold() for name in profile.voices}
        for name, gender in profile.genders.items():
            if name.casefold() not in assigned:
                self._append_row(name, None, gender)
        if not self._model_matches and profile.voices:
            self._error.setText("Saved assignments use another voice model. Choose current presets before enabling.")
            self._error.show()
        self.set_detected_name(detected_name)
        if detected_name:
            self._name.setText(detected_name)
            self._preset.setCurrentIndex(max(0, self._preset.findData(profile.speaker_for(detected_name, model_path))))
            self._gender.setCurrentIndex(max(0, self._gender.findData(profile.gender_for(detected_name))))

    def _preset_combo(self, index=None):
        combo = QComboBox()
        combo.addItem("Default voice", None)
        for name, speaker_id in self._speakers:
            combo.addItem(voice_label(self._model_path, name), speaker_id)
        combo.setCurrentIndex(max(0, combo.findData(index)))
        return combo

    @staticmethod
    def _gender_combo(value=None):
        combo = QComboBox()
        combo.setAccessibleName("Character gender")
        for label, data in (
            ("From preset", None),
            ("Male", "male"),
            ("Female", "female"),
            ("Nonbinary", "nonbinary"),
            ("Unspecified", "unknown"),
        ):
            combo.addItem(label, data)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        return combo

    def _append_row(self, name, index, gender=None):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(name))
        combo = self._preset_combo(index)
        combo.setAccessibleName(f"Voice for {name}")
        self._table.setCellWidget(row, 1, combo)
        self._table.setCellWidget(row, 2, self._gender_combo(gender))
        self._table.setRowHeight(row, 44)

    def _add_name(self):
        name, index = character_name(self._name.text()), self._preset.currentData()
        gender = self._gender.currentData()
        if not name or (index is None and gender is None):
            self._error.setText("Enter a name and choose a voice preset or a character gender.")
            self._error.show()
            return
        for row in range(self._table.rowCount()):
            if character_name(self._table.item(row, 0).text()).casefold() == name.casefold():
                self._table.item(row, 0).setText(name)
                combo = self._table.cellWidget(row, 1)
                combo.setCurrentIndex(combo.findData(index))
                gender_combo = self._table.cellWidget(row, 2)
                gender_combo.setCurrentIndex(gender_combo.findData(gender))
                break
        else:
            self._append_row(name, index, gender)
        self._error.clear()
        self._error.hide()

    def _edit_selected(self):
        row = self._table.currentRow()
        if row >= 0 and self._table.item(row, 0) is not None:
            self._name.setText(self._table.item(row, 0).text())
            combo = self._table.cellWidget(row, 1)
            if combo is not None:
                self._preset.setCurrentIndex(max(0, self._preset.findData(combo.currentData())))
                self._gender.setCurrentIndex(
                    max(0, self._gender.findData(self._table.cellWidget(row, 2).currentData()))
                )

    def _remove_selected(self):
        for row in sorted({item.row() for item in self._table.selectedItems()}, reverse=True):
            self._table.removeRow(row)

    def _preview(self):
        index = self._preset.currentData()
        if index is not None:
            self.preview_requested.emit(index)

    def set_detected_name(self, name):
        self._detected.setText(f"Detected name: {name}" if name else "Detected name: none (default voice)")

    def _refresh_region_status(self):
        status = "Speaker-name area selected." if self._profile.name_region else "No name area selected."
        if self._frame is None:
            status += " Start capture on this source to select or change the area."
        self._region_status.setText(status)

    def _select_region(self):
        editor = OCRConfigDialog(self, initial_region=self._profile.name_region, selection_only=True)
        editor.setWindowTitle("Select speaker name area")
        editor.update_frame(self._frame)
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._profile.name_region = editor.get_scan_region()
            self._refresh_region_status()

    def get_profile(self):
        profile = CharacterVoices.from_dict(self._profile.to_dict())
        profile.enabled = self._enabled.isChecked()
        assignments, genders, seen = {}, {}, set()
        for row in range(self._table.rowCount()):
            name = character_name(self._table.item(row, 0).text())
            index = self._table.cellWidget(row, 1).currentData()
            gender = self._table.cellWidget(row, 2).currentData()
            if not name or (index is None and gender is None):
                if not profile.enabled and not self._model_matches:
                    return profile
                raise ValueError("Every assignment needs a name and a voice preset or character gender.")
            if name.casefold() in seen:
                raise ValueError("Each character name can have only one assignment.")
            seen.add(name.casefold())
            if index is not None:
                assignments[name] = index
            if gender:
                genders[name] = gender
        profile.model_path, profile.voices, profile.genders = self._model_path, assignments, genders
        if profile.enabled and not profile.name_region:
            raise ValueError("Select the speaker-name area before enabling automatic voices.")
        return profile

    def accept(self):
        try:
            self.get_profile()
        except ValueError as exc:
            self._error.setText(str(exc))
            self._error.show()
            return
        super().accept()
