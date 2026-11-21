from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QWidget

from interpreter.config import Config
from interpreter.gui import diagnostics


def test_diagnostics_copies_live_stages_without_device_names_or_error_details(monkeypatch):
    app = QApplication.instance() or QApplication([])
    device = SimpleNamespace(isNull=lambda: False, description=lambda: "Private headphones")
    monkeypatch.setattr(diagnostics, "QMediaDevices", SimpleNamespace(defaultAudioOutput=lambda: device))
    copies = []
    monkeypatch.setattr(
        diagnostics,
        "QApplication",
        SimpleNamespace(clipboard=lambda: SimpleNamespace(setText=copies.append)),
    )
    window = QWidget()
    window.capture_diagnostics = lambda: ("Receiving frames", "PrintWindow", "800 × 600")
    window._config = Config()
    window._capturing = True
    window._capture_generation = 2
    window._ocr_status_label = window._translation_status_label = SimpleNamespace(text=lambda: "Ready")
    window._process_worker = SimpleNamespace(
        ocr_diagnostics=(2, 42, 4, 1, "English"),
        translation_diagnostics=(1, 300, False, True),
    )
    window._speech = SimpleNamespace(
        diagnostic_state=lambda: (
            "Idle",
            {"error": r"Private driver path [PaErrorCode -9998]", "output_device_name": "Private speakers"},
        )
    )
    dialog = diagnostics.DiagnosticsDialog(window)
    try:
        dialog.show()
        assert dialog._timer.isActive()
        assert "Private headphones" in dialog._summary.toPlainText()
        assert "Private speakers" in dialog._summary.toPlainText()
        dialog._copy()
        report = copies[0]
        assert "PrintWindow" in report
        assert "42 ms · 4 regions · 1 ignored" in report
        assert "PortAudio -9998" in report
        assert "Private" not in report
        assert "Last translation" not in report
        dialog.close()
        assert not dialog._timer.isActive()
    finally:
        dialog.close()
        window.close()
        app.processEvents()
