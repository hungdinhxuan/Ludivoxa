"""Read-only snapshot of the running capture and audio pipeline."""

import re
import sys

from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from .. import __version__
from .i18n import tr


def diagnostic_report(window):
    """Build a shareable report without dialogue, window titles, paths, or endpoint settings."""
    state, backend, geometry = window.capture_diagnostics()
    rows = {
        "Version": __version__,
        "Capture": f"{tr(state)} · {tr(backend)} · {geometry}",
    }
    if sys.platform == "win32":
        from ..capture.windows import is_remote_session, is_session_active

        session = "Remote Desktop" if is_remote_session() else "Local desktop"
        rows["Windows session"] = f"{tr(session)} · {tr('active' if is_session_active() else 'disconnected')}"

    worker = window._process_worker
    generation = window._capture_generation
    rows["OCR"] = f"{window._config.ocr_backend.value} · {window._ocr_status_label.text()}"
    ocr = worker.ocr_diagnostics
    if window._capturing and ocr and ocr[0] == generation:
        _, ms, count, ignored, stage = ocr
        rows["Last OCR"] = (
            tr(stage) if ms is None else tr("{0} ms · {1} regions · {2} ignored · {3}", ms, count, ignored, tr(stage))
        )

    rows["Translation"] = f"{window._config.translation_backend.value} · {window._translation_status_label.text()}"
    translation = worker.translation_diagnostics
    if window._capturing and translation and translation[0] == generation:
        _, ms, cached, received = translation
        result = "cached" if cached else "received" if received else "empty or failed"
        rows["Last translation"] = f"{ms} ms · {tr(result)}"

    speech, audio = window._speech.diagnostic_state()
    rows["Speech"] = tr(speech)
    default = QMediaDevices.defaultAudioOutput()
    rows["System audio output"] = tr("Available" if not default.isNull() else "Unavailable")
    if audio.get("error"):
        code = re.search(r"\[PaErrorCode (-?\d+)\]", audio["error"])
        rows["Last audio playback"] = tr("Failed · PortAudio {0}", code[1]) if code else tr("Failed")
    elif audio.get("host_api"):
        rows["Last audio playback"] = tr(
            "{0} · {1} ch · {2} Hz · {3} underruns",
            audio["host_api"],
            audio["output_channels"],
            audio["sample_rate"],
            audio["underruns"],
        )
    report = "\n".join(f"{tr(label)}: {value}" for label, value in rows.items())
    return report, default.description(), audio.get("output_device_name", "")


class DiagnosticsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle(tr("Diagnostics"))
        self.resize(550, 365)
        layout = QVBoxLayout(self)
        self._summary = QPlainTextEdit()
        self._summary.setAccessibleName(tr("Live diagnostic report"))
        self._summary.setReadOnly(True)
        layout.addWidget(self._summary)
        self._feedback = QLabel()
        layout.addWidget(self._feedback)
        actions = QHBoxLayout()
        copy = QPushButton(tr("Copy report"))
        copy.clicked.connect(self._copy)
        actions.addWidget(copy)
        actions.addStretch()
        close = QPushButton(tr("Close"))
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
            view += tr("\nCurrent system device: {0}", default_name)
        if last_name:
            view += tr("\nDevice used for last playback: {0}", last_name)
        if view != self._summary.toPlainText():
            self._summary.setPlainText(view)

    def _copy(self):
        QApplication.clipboard().setText(self._report)
        self._feedback.setText(tr("Report copied"))
