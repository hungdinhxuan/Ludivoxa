"""History launcher geometry and single-page ownership across its two views."""

from types import SimpleNamespace

from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ludivoxa.config import Config
from ludivoxa.gui.history_overlay import HistoryOverlay, _visible_game_area
from ludivoxa.gui.history_page import HistoryPage
from ludivoxa.gui.workers import DialogueEntry


def test_game_anchor_uses_the_visible_monitor_across_dpi_and_screen_origins():
    first = QRect(0, 0, 1920, 1080)
    second = QRect(-2560, -100, 1280, 720)
    screens = [
        SimpleNamespace(geometry=lambda: first, availableGeometry=lambda: first, devicePixelRatio=lambda: 1.0),
        SimpleNamespace(geometry=lambda: second, availableGeometry=lambda: second, devicePixelRatio=lambda: 2.0),
    ]
    assert _visible_game_area({"x": -2400, "y": 100, "width": 1200, "height": 800}, screens) == (
        QRect(-2480, 0, 600, 400),
        second,
    )
    assert _visible_game_area({"x": -80, "y": 20, "width": 380, "height": 210}, screens)[0] == QRect(0, 20, 300, 210)


def test_history_launcher_requires_target_focus_and_restores_same_page_on_close(monkeypatch):
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    layout = QVBoxLayout(host)
    history = HistoryPage()
    layout.addWidget(history)
    overlay = HistoryOverlay(history, host)
    monkeypatch.setattr(overlay._button, "ensure_above", lambda _window_id: None)
    entry = DialogueEntry(("Welcome.",), "Chào mừng.", "English", "Vietnamese", None, ("", 7, 1.0, ""))
    history.append(entry)
    screen = app.primaryScreen().availableGeometry()
    scale = app.primaryScreen().devicePixelRatio()
    bounds = {"x": screen.x(), "y": screen.y(), "width": round(screen.width() * scale), "height": 400}
    try:
        overlay.sync(bounds, False, 1)
        assert not overlay._button.isVisible()
        overlay.sync(bounds, True, 1)
        assert overlay._button.isVisible()
        assert overlay._button.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert not overlay._button.windowFlags() & Qt.WindowType.WindowTransparentForInput
        overlay._button.click()
        assert overlay.panel_visible and history.parentWidget() is overlay._panel
        assert history._selected_entry() is entry
        assert not overlay._button.isVisible()

        QTest.keyClick(overlay._panel, Qt.Key.Key_Escape)
        assert not overlay.panel_visible
        assert history.parentWidget() is host and history._selected_entry() is entry

        overlay.sync(bounds, True, 1)
        overlay._button.click()
        monkeypatch.setattr(overlay._panel, "isActiveWindow", lambda: True)
        overlay.sync(bounds, False, 1)
        assert overlay.panel_visible
        monkeypatch.setattr(overlay._panel, "isActiveWindow", lambda: False)
        overlay.sync(bounds, True, 1)
        assert not overlay.panel_visible and overlay._button.isVisible()
        assert history.parentWidget() is host
        overlay._button.click()
        overlay.hide()
        assert not overlay.panel_visible and not overlay._button.isVisible()
        assert history.parentWidget() is host
        overlay.sync({**bounds, "width": 20}, True, 1)
        assert not overlay._button.isVisible()
    finally:
        overlay.close()
        host.close()


def test_history_launcher_setting_defaults_on_and_can_be_turned_off(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("window_title: Game\n", encoding="utf-8")
    config = Config.load(str(path))
    assert config.history_overlay_enabled
    config.history_overlay_enabled = False
    config.save()
    assert not Config.load(str(path)).history_overlay_enabled
