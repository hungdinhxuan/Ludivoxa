"""Release checks, download progress and explicit install/restart in the Qt UI."""

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..updates import RELEASES_URL, DeltaUnavailable, GitHubUpdates, UpdateCancelled, launch_update
from .i18n import tr
from .theme import ERROR, OK


def size_label(size):
    return f"{size / 1024**3:.2f} GB" if size >= 1024**3 else f"{size / 1024**2:.1f} MB"


class UpdatesPage(QWidget):
    update_available = Signal(str)
    _completed = Signal(int, str, object)
    _progress = Signal(int, object, object)

    def __init__(self, config, version, parent=None):
        super().__init__(parent)
        self.config, self.version = config, version
        self.release = self.downloaded = None
        self._busy, self._closed, self._generation = False, False, 0
        self._cancel = threading.Event()
        self._completed.connect(self._done)
        self._progress.connect(self._show_progress)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 16, 12)
        layout.setSpacing(12)
        card = QGroupBox(tr("Application updates"))
        body = QVBoxLayout(card)
        body.addWidget(QLabel(tr("Installed version: {0}", version)))
        self.status = QLabel(tr("Check GitHub for a newer stable Ludivoxa release."))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.status)
        self.automatic = QCheckBox(tr("Check for updates when Ludivoxa starts"))
        self.automatic.setChecked(config.update_check_on_startup)
        self.automatic.toggled.connect(self._save_automatic)
        body.addWidget(self.automatic)
        hint = QLabel(tr("Update checks use GitHub. Offline translation does not require this option."))
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        body.addWidget(hint)
        row = QHBoxLayout()
        self.check_button = QPushButton(tr("Check for updates"))
        self.download_button = QPushButton(tr("Download update"))
        self.install_button = QPushButton(tr("Install and restart"))
        self.cancel_button = QPushButton(tr("Cancel"))
        for button in (self.download_button, self.install_button, self.cancel_button):
            row.addWidget(button)
        self.check_button.clicked.connect(self.check)
        self.download_button.clicked.connect(self.download)
        self.install_button.clicked.connect(self.install)
        self.cancel_button.clicked.connect(self.cancel)
        body.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setAccessibleName(tr("Update download progress"))
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.hide()
        body.addWidget(self.progress)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName(tr("Release notes"))
        self.notes.setPlaceholderText(tr("Release notes appear here when an update is available."))
        self.notes.setMaximumHeight(160)
        body.addWidget(self.notes)
        releases = QPushButton(tr("View releases on GitHub"))
        releases.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        body.addWidget(releases)
        layout.addWidget(card)
        layout.addStretch()
        self._buttons()
        if config.update_check_on_startup:
            QTimer.singleShot(1500, self.check)

    def _save_automatic(self, enabled):
        self.config.update_check_on_startup = enabled
        self.config.save()

    def _buttons(self):
        self.check_button.setEnabled(not self._busy)
        self.download_button.setEnabled(not self._busy and self.release is not None and self.downloaded is None)
        self.install_button.setEnabled(
            not self._busy and self.downloaded is not None and os.name == "nt" and bool(getattr(sys, "frozen", False))
        )
        self.cancel_button.setEnabled(self._busy)
        for button in (self.check_button, self.download_button, self.install_button, self.cancel_button):
            button.setVisible(button.isEnabled())
        if self.release:
            label = "Download smaller update" if self.release.delta else "Download update"
            self.download_button.setText(f"{tr(label)} ({size_label(self.release.download_size)})")

    def _task(self, action, operation):
        if self._busy or self._closed:
            return
        self._generation += 1
        generation = self._generation
        self._cancel = threading.Event()
        self._busy = True
        self._buttons()
        self.progress.setRange(0, 0)
        self.progress.setFormat(tr("Working…"))
        self.progress.show()

        def run():
            try:
                result = operation(self._cancel, generation)
                if self._cancel.is_set() and action != "install":
                    result = UpdateCancelled("Update cancelled.")
            except Exception as exc:
                result = exc
            if not self._closed:
                try:
                    self._completed.emit(generation, action, result)
                except RuntimeError:
                    pass

        threading.Thread(target=run, daemon=True, name="ludivoxa-update").start()

    def check(self):
        if self._busy or self._closed:
            return
        self.status.setText(tr("Checking GitHub…"))
        self.status.setStyleSheet("")

        def operation(cancel, _generation):
            client = GitHubUpdates(cancel=cancel)
            try:
                return client.check(self.version)
            finally:
                client.close()

        self._task("check", operation)

    def download(self):
        if self._busy or self.release is None:
            return
        release = self.release
        self.status.setText(
            tr("Checking installed files before downloading the smaller update…")
            if release.delta
            else tr("Downloading Ludivoxa {0}…", release.version)
        )

        def operation(cancel, generation):
            client = GitHubUpdates(cancel=cancel)
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.1 or done == total:
                    last = now
                    if not self._closed:
                        try:
                            self._progress.emit(generation, done, total)
                        except RuntimeError:
                            pass

            try:
                return client.download(release, Path.home() / ".ludivoxa/updates", progress)
            finally:
                client.close()

        self._task("download", operation)

    def install(self):
        if self._busy or self.downloaded is None or not self.install_button.isEnabled():
            return
        update = self.downloaded
        self.status.setText(tr("Verifying update files before installation…"))
        self._task("install", lambda cancel, _generation: launch_update(update, cancel=cancel))

    def _show_progress(self, generation, done, total):
        if generation == self._generation and self._busy and not self._closed:
            if self.progress.maximum() == 0 and self.release and self.release.delta and not self._cancel.is_set():
                self.status.setText(tr("Downloading Ludivoxa {0}…", self.release.version))
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(done * 1000 / max(total, 1))))
            self.progress.setFormat(f"{done / max(total, 1):.0%} — {size_label(done)} / {size_label(total)}")

    def _done(self, generation, action, result):
        if generation != self._generation or self._closed:
            return
        self._busy = False
        self.progress.hide()
        if isinstance(result, DeltaUnavailable) and action == "download":
            self.release = self.release.standard()
            self.status.setText(tr("{0} Select Download update to get the standard installer.", tr(str(result))))
            self.status.setStyleSheet("")
        elif isinstance(result, Exception):
            self.status.setText(tr(str(result)))
            self.status.setStyleSheet(f"color: {ERROR};" if not isinstance(result, UpdateCancelled) else "")
        elif action == "check":
            self.release, self.downloaded = result, None
            self.notes.setPlainText(result.notes if result else "")
            if result:
                kind = (
                    "App-only update; existing models and settings are kept."
                    if result.app_only
                    else "Full offline installer download."
                )
                if result.delta:
                    kind = "A smaller update is available if the installed files match."
                self.status.setText(tr("Ludivoxa {0} is available. {1}", result.version, tr(kind)))
                self.update_available.emit(result.version)
            else:
                self.status.setText(tr("Ludivoxa {0} is up to date.", self.version))
            self.status.setStyleSheet(f"color: {OK};")
        elif action == "download":
            self.downloaded = result
            message = "Download verified. Install and restart when you are ready."
            if os.name != "nt" or not getattr(sys, "frozen", False):
                message = tr("Download verified in {0}. Run the Windows installer manually.", result.directory)
            self.status.setText(tr(message))
            self.status.setStyleSheet(f"color: {OK};")
        elif action == "install":
            QApplication.instance().quit()
        self._buttons()

    def cancel(self):
        if self._busy:
            self._cancel.set()
            self.status.setText(tr("Cancelling update operation…"))

    def shutdown(self):
        self._closed = True
        self._cancel.set()
