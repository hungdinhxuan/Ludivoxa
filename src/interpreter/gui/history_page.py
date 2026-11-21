"""Session dialogue history with explicit replay and retranslation actions."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .workers import DialogueEntry

HISTORY_LIMIT = 100
ENTRY_ROLE = Qt.ItemDataRole.UserRole


class HistoryPage(QWidget):
    replay_requested = Signal(object)
    retranslation_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending_entry = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 16, 12)
        layout.setSpacing(8)
        self.entries = QListWidget()
        self.entries.setProperty("role", "feed")
        self.entries.setAccessibleName("Dialogue history")
        self.entries.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addWidget(self.entries, 2)
        self.source = self._add_text(layout, "Original")
        self.translation = self._add_text(layout, "Translation")

        buttons = QHBoxLayout()
        self.replay = QPushButton("Replay")
        self.copy = QPushButton("Copy translation")
        self.retranslate = QPushButton("Retranslate")
        for button in (self.replay, self.copy, self.retranslate):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QLabel("Completed dialogue will appear here during capture.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setProperty("role", "hint")
        layout.addWidget(self.status)

        self.entries.currentItemChanged.connect(self._show_entry)
        self.replay.clicked.connect(lambda: self.replay_requested.emit(self._selected_entry()))
        self.copy.clicked.connect(self._copy_selected)
        self.retranslate.clicked.connect(self._retranslate_selected)
        self._show_entry(None)

    @staticmethod
    def _add_text(layout, name):
        label = QLabel(name)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setAccessibleName(name)
        label.setBuddy(text)
        layout.addWidget(label)
        layout.addWidget(text, 1)
        return text

    @staticmethod
    def _summary(entry):
        translation = " ".join(entry.translation.split())
        return f"{entry.speaker[0]} — {translation}" if entry.speaker else translation

    def _selected_entry(self):
        item = self.entries.currentItem()
        return item.data(ENTRY_ROLE) if item else None

    def _show_entry(self, item):
        entry = item.data(ENTRY_ROLE) if item else None
        self.source.setPlainText("\n".join(entry.source) if entry else "")
        self.translation.setPlainText(entry.translation if entry else "")
        self.replay.setEnabled(bool(entry and entry.can_replay))
        self.copy.setEnabled(entry is not None)
        self.retranslate.setEnabled(entry is not None and self._pending_entry is None)

    def append(self, entry):
        count = self.entries.count()
        follow_latest = not count or (self.entries.currentRow() == count - 1 and self._pending_entry is None)
        item = QListWidgetItem(self._summary(entry))
        item.setData(ENTRY_ROLE, entry)
        self.entries.addItem(item)
        if self.entries.count() > HISTORY_LIMIT:
            self.entries.takeItem(0)
        if follow_latest:
            self.entries.setCurrentItem(item)
            self.entries.scrollToItem(item)
        if not count:
            self.status.clear()

    def _copy_selected(self):
        entry = self._selected_entry()
        if entry:
            QApplication.clipboard().setText(entry.translation)
            self.status.setText("Translation copied.")

    def _retranslate_selected(self):
        entry = self._selected_entry()
        if entry is None or self._pending_entry is not None:
            return
        self._pending_entry = entry
        self.retranslate.setEnabled(False)
        self.status.setText("Retranslating…")
        self.retranslation_requested.emit(entry)

    def finish_retranslation(self, entry, result):
        if entry is not self._pending_entry:
            return
        self._pending_entry = None
        message = result
        if isinstance(result, DialogueEntry):
            message = "That dialogue is no longer in history."
            for index in range(self.entries.count()):
                item = self.entries.item(index)
                if item.data(ENTRY_ROLE) is entry:
                    item.setData(ENTRY_ROLE, result)
                    item.setText(self._summary(result))
                    message = "History translation updated."
                    break
        self.status.setText(message)
        self._show_entry(self.entries.currentItem())

    def cancel_retranslation(self):
        if self._pending_entry is not None:
            self.finish_retranslation(self._pending_entry, "Translation engine restarted. Try again.")
