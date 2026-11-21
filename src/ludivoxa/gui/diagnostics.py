"""Read-only snapshot of the running capture and audio pipeline."""

import re
import sys

from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from .. import __version__


def diagnostic_report(window):
    """Build a shareable report without dialogue, window titles, paths, or endpoint settings."""
    state, backend, geometry = window.capture_diagnostics()
    rows = {
        "Version": __version__,
        "Capture": f"{state} · {backend} · {geometry}",
    }
    if sys.platform == "win32":
        from ..capture.windows import is_remote_session, is_session_active

        session = "Remote Desktop" if is_remote_session() else "Local desktop"
        rows["Windows session"] = f"{session} · {'active' if is_session_active() else 'disconnected'}"

    worker = window._process_worker
    generation = window._capture_generation
    rows["OCR"] = f"{window._config.ocr_backend.value} · {window._ocr_status_label.text()}"
    ocr = worker.ocr_diagnostics
    if window._capturing and ocr and ocr[0] == generation:
        _, ms, count, ignored, stage = ocr
        rows["Last OCR"] = stage if ms is None else f"{ms} ms · {count} regions · {ignored} ignored · {stage}"

    rows["Translation"] = f"{window._config.translation_backend.value} · {window._translation_status_label.text()}"
    translation = worker.translation_diagnostics
    if window._capturing and translation and translation[0] == generation:
        _, ms, cached, received = translation
        result = "cached" if cached else "received" if received else "empty or failed"
        rows["Last translation"] = f"{ms} ms · {result}"

    speech, audio = window._speech.diagnostic_state()
    rows["Speech"] = speech
    default = QMediaDevices.defaultAudioOutput()
    rows["System audio output"] = "Available" if not default.isNull() else "Unavailable"
    if audio.get("error"):
        code = re.search(r"\[PaErrorCode (-?\d+)\]", audio["error"])
        rows["Last audio playback"] = f"Failed · PortAudio {code[1]}" if code else "Failed"
    elif audio.get("host_api"):
        rows["Last audio playback"] = (
            f"{audio['host_api']} · {audio['output_channels']} ch · {audio['sample_rate']} Hz"
            f" · {audio['underruns']} underruns"
        )
    report = "\n".join(f"{label}: {value}" for label, value in rows.items())
    return report, default.description(), audio.get("output_device_name", "")


class DiagnosticsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("Diagnostics")
        self.resize(550, 365)
        layout = QVBoxLayout(self)
        self._summary = QPlainTextEdit()
        self._summary.setAccessibleName("Live diagnostic report")
        self._summary.setReadOnly(True)
        layout.addWidget(self._summary)
        self._feedback = QLabel()
        layout.addWidget(self._feedback)
        actions = QHBoxLayout()
        copy = QPushButton("Copy report")
        copy.clicked.connect(self._copy)
        actions.addWidget(copy)
        actions.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._refresh)
        self._report = ""

    def showEvent(self, event):
        super().showEvent(event)
        self._feedback.clear()
        self._refresh()
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _refresh(self):
        self._report, default_name, last_name = diagnostic_report(self.parent())
        view = self._report
        if default_name:
            view += f"\nCurrent system device: {default_name}"
        if last_name:
            view += f"\nDevice used for last playback: {last_name}"
        if view != self._summary.toPlainText():
            self._summary.setPlainText(view)

    def _copy(self):
        QApplication.clipboard().setText(self._report)
        self._feedback.setText("Report copied")
