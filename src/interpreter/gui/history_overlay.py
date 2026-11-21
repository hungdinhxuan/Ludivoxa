"""Windows History launcher and floating view of the session's existing page."""

from PySide6.QtCore import QObject, QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout

from ..overlay.windows import _WindowsOverlayMixin

MARGIN = 8


def _visible_game_area(bounds, screens):
    """Return the largest visible part of a physical game rectangle in Qt coordinates."""
    game = QRect(bounds["x"], bounds["y"], bounds["width"], bounds["height"])
    largest, available = QRect(), QRect()
    for screen in screens:
        area = screen.geometry()
        scale = screen.devicePixelRatio()
        # On Windows, Qt scales screen sizes and keeps their native origins.
        native = QRect(area.x(), area.y(), round(area.width() * scale), round(area.height() * scale))
        visible = native.intersected(game)
        logical = QRect(
            area.x() + round((visible.x() - area.x()) / scale),
            area.y() + round((visible.y() - area.y()) / scale),
            round(visible.width() / scale),
            round(visible.height() / scale),
        ).intersected(screen.availableGeometry())
        if logical.width() * logical.height() > largest.width() * largest.height():
            largest, available = logical, screen.availableGeometry()
    return largest, available


class _HistoryButton(_WindowsOverlayMixin, QPushButton):
    def __init__(self):
        super().__init__("History")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(0.78)
        self.setToolTip("Open dialogue history")
        self.adjustSize()


class HistoryOverlay(QObject):
    def __init__(self, history, host):
        super().__init__(host)
        self._history = history
        self._host = host
        self._screen_area = QRect()
        self._button = _HistoryButton()
        self._button.clicked.connect(self._open_panel)
        # A parentless tool window stays available when the main app is minimized.
        self._panel = QDialog(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self._panel.setWindowTitle("Ludivoxa History")
        self._panel_layout = QVBoxLayout(self._panel)
        self._panel_layout.setContentsMargins(0, 0, 0, 0)
        self._panel.finished.connect(self._restore_history)

    @property
    def panel_visible(self):
        return self._panel.isVisible()

    def sync(self, bounds, foreground, window_id):
        if self.panel_visible:
            if self._panel.isActiveWindow():
                return
            self.hide()
        if not foreground or not bounds or not window_id:
            self._button.hide()
            return
        game, self._screen_area = _visible_game_area(bounds, QApplication.screens())
        width, height = self._button.width(), self._button.height()
        if game.width() < width + 2 * MARGIN or game.height() < height + 2 * MARGIN:
            self._button.hide()
            return
        position = QPoint(game.right() - width - MARGIN + 1, game.top() + MARGIN)
        if position != self._button.pos():
            self._button.move(position)
        if not self._button.isVisible():
            self._button.show()
        self._button.ensure_above(window_id)

    def _open_panel(self):
        if not self._button.isVisible() or self.panel_visible:
            return
        area = self._screen_area.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        anchor = self._button.geometry().topRight()
        self._button.hide()
        self._panel_layout.addWidget(self._history)
        self._history.show()
        self._panel.resize(min(480, area.width()), min(500, area.height() - 24))
        position = QPoint(anchor.x() - self._panel.width() + 1, anchor.y())
        position.setX(max(area.left(), min(position.x(), area.right() - self._panel.width() + 1)))
        position.setY(max(area.top(), min(position.y(), area.bottom() - self._panel.height() + 1)))
        self._panel.move(position)
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()
        self._history.entries.setFocus()

    def _restore_history(self):
        self._host.layout().addWidget(self._history)
        self._history.show()

    def hide(self):
        self._button.hide()
        if self.panel_visible:
            self._panel.reject()

    def close(self):
        self.hide()
        self._button.close()
        self._panel.close()
