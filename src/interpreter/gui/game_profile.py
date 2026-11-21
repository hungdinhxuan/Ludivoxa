"""Edit the language, prompt and ignored OCR phrases for a capture source."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from ..config import GameProfile
from ..languages import OCR_SOURCE_LANGUAGES
from .theme import set_role


class GameProfileDialog(QDialog):
    def __init__(self, title, profile, default_language, validate_language, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Game profile")
        self.resize(460, 395)
        self._validate_language = validate_language
        layout = QVBoxLayout(self)

        source = QLabel(title)
        source.setTextFormat(Qt.TextFormat.PlainText)
        source.setWordWrap(True)
        layout.addWidget(source)
        self.language = QComboBox()
        self.language.addItem(f"Use Engines setting ({default_language})", "")
        for language in dict.fromkeys((*OCR_SOURCE_LANGUAGES, profile.source_language)):
            if language:
                self.language.addItem(language, language)
        self.language.setCurrentIndex(self.language.findData(profile.source_language))
        self.prompt = QPlainTextEdit(profile.system_prompt)
        self.prompt.setPlaceholderText("Leave blank to use the prompt from Engines.")
        self.ignored = QPlainTextEdit("\n".join(profile.ignore_phrases))
        self.ignored.setPlaceholderText("Enter one complete phrase per line.")

        for text, field in (
            ("Source language", self.language),
            ("LLM system prompt", self.prompt),
            ("Ignore OCR phrases", self.ignored),
        ):
            label = QLabel(text)
            label.setBuddy(field)
            layout.addWidget(label)
            layout.addWidget(field)
        self._error = QLabel()
        self._error.setWordWrap(True)
        set_role(self._error, "status-error")
        self._error.hide()
        layout.addWidget(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def profile(self):
        return GameProfile.from_dict(
            {
                "source_language": self.language.currentData(),
                "system_prompt": self.prompt.toPlainText(),
                "ignore_phrases": self.ignored.toPlainText().splitlines(),
            }
        )

    def accept(self):
        message = self._validate_language(self.language.currentData())
        if message:
            self._error.setText(message)
            self._error.show()
            self.language.setFocus()
            return
        super().accept()
