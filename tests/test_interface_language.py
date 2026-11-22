"""The desktop language is saved independently of the translation pair."""

from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QToolButton

from ludivoxa.config import Config, GameProfile, LLMSettings, OCRBackend, TranslationBackend
from ludivoxa.gui.game_profile import GameProfileDialog
from ludivoxa.gui.i18n import set_interface_language
from ludivoxa.gui.main_window import MainWindow
from ludivoxa.gui.ocr_config import OCRConfigDialog


def test_switch_interface_language_on_reopen(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "config.yml"
    config = Config(
        config_path=str(path),
        source_language="English",
        ocr_backend=OCRBackend.PADDLE,
        translation_backend=TranslationBackend.LLM,
        llm=LLMSettings(model="local", target_language="Korean"),
    )
    config.save()
    monkeypatch.setattr(MainWindow, "_setup_hotkeys", lambda win: setattr(win, "_keyboard_listener", MagicMock()))
    monkeypatch.setattr(MainWindow, "_refresh_sources", lambda win: None)
    monkeypatch.setattr(MainWindow, "_load_models", lambda win: None)
    english, vietnamese, ocr, profile = None, None, None, None
    try:
        english = MainWindow(Config.load(str(path)))
        picker = next(combo for combo in english.findChildren(QComboBox) if combo.findData("vi") >= 0)
        assert english._nav.item(4).text() == "History"
        assert picker.currentData() == "en"
        picker.setCurrentIndex(picker.findData("vi"))
        assert english._nav.item(4).text() == "History"  # Applies only on the next launch.

        restored = Config.load(str(path))
        assert (restored.interface_language, restored.source_language, restored.llm.target_language) == (
            "vi",
            "English",
            "Korean",
        )
        vietnamese = MainWindow(restored)
        assert [vietnamese._nav.item(index).text() for index in range(5)] == [
            "Chơi",
            "Phụ đề",
            "Bộ xử lý",
            "Cập nhật",
            "Lịch sử",
        ]
        assert vietnamese._start_btn.text() == "Bắt đầu chụp"
        assert vietnamese._tts_clone_model_label.text().startswith("Model giọng đọc: Qwen3-TTS Base")
        assert vietnamese._updates_page.check_button.text() == "Kiểm tra cập nhật"
        vietnamese._on_ocr_status("error")
        assert vietnamese._ocr_status_label.text() == "Lỗi"
        assert not vietnamese._fix_models_btn.isHidden()
        assert vietnamese._source_language_combo.currentText() == "Tiếng Anh"
        assert vietnamese._target_language_combo.currentText() == "Tiếng Hàn"
        assert vietnamese._llm_settings_from_ui().target_language == "Korean"
        vietnamese._target_language_combo.setEditText("French")
        assert vietnamese._llm_settings_from_ui().target_language == "French"

        vietnamese.resize(680, 480)
        vietnamese.show()
        app.processEvents()
        play = vietnamese._pages.currentWidget()
        voice = play.findChild(QToolButton)
        assert vietnamese._ocr_config_btn.isVisible()
        assert vietnamese._tts_clone_model_label.isVisible()
        assert not vietnamese._tts_clone_url_edit.isVisible()
        voice.setFocus()
        QTest.keyClick(voice, Qt.Key.Key_Space)
        assert voice.isChecked() and vietnamese._tts_clone_url_edit.isVisible()

        vietnamese._nav.setCurrentRow(2)
        app.processEvents()
        engines = vietnamese._pages.currentWidget()
        scroll = engines.findChild(QScrollArea)
        bar = scroll.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        apply = vietnamese._apply_engines_btn
        assert apply.isVisible() and not scroll.isAncestorOf(apply)

        ocr = OCRConfigDialog(window_title="Test Window")
        assert ocr.windowTitle() == "Cấu hình OCR: Test Window"
        ocr._scan_mode.setCurrentIndex(1)
        assert "Kéo trên ảnh xem trước" in ocr._instructions.text()
        profile = GameProfileDialog("Test Window", GameProfile(), "English", lambda _: "")
        assert profile.windowTitle() == "Hồ sơ game"
        assert profile.language.itemText(1) == "Tự động"
        assert profile.profile() == GameProfile()
    finally:
        for dialog in (profile, ocr):
            if dialog is not None:
                dialog.close()
        for window in (vietnamese, english):
            if window is not None:
                window.cleanup()
                window.close()
        set_interface_language("en")
        app.processEvents()
