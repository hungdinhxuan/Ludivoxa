"""Updater UI never blocks Qt or installs without the explicit install action."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox, QLineEdit

from interpreter.config import Config
from interpreter.gui import updates_page as ui
from interpreter.updates import Asset, Release, UpdateError


@pytest.fixture
def page(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(ui, "GitHubUpdates", factory)
    config = Config(config_path=str(tmp_path / "config.yml"))
    widget = ui.UpdatesPage(config, "1.0.0")
    yield widget, client, factory, app
    widget.shutdown()
    widget.close()


def until(predicate):
    limit = time.monotonic() + 3
    while not predicate() and time.monotonic() < limit:
        QTest.qWait(5)
        time.sleep(0.002)
    assert predicate()


def release():
    return Release("v1.0.1", "New release notes", (Asset("Ludivoxa-1.0.1-App-Update.exe", 1, 12),), True)


def test_offline_default_does_not_check_and_opt_in_is_saved(page):
    widget, _client, factory, _app = page
    QTest.qWait(30)
    factory.assert_not_called()
    widget.automatic.setChecked(True)
    assert Config.load(widget.config.config_path).update_check_on_startup
    assert not widget.findChildren(QLineEdit)
    assert [card.title() for card in widget.findChildren(QGroupBox)] == ["Application updates"]


def test_slow_check_runs_off_ui_thread_and_reports_update(page):
    widget, client, factory, _app = page
    started, finish = threading.Event(), threading.Event()
    caller = threading.get_ident()

    def slow(_version):
        assert threading.get_ident() != caller
        started.set()
        finish.wait(2)
        return release()

    client.check.side_effect = slow
    widget.check()
    until(started.is_set)
    assert not factory.call_args.args  # Public checks require no credentials.
    assert widget._busy and not widget.check_button.isEnabled()
    assert widget.cancel_button.isEnabled()
    finish.set()
    until(lambda: not widget._busy)
    assert widget.download_button.isEnabled() and not widget.install_button.isEnabled()
    assert widget.notes.toPlainText() == "New release notes"
    assert "1.0.1" in widget.status.text()


def test_missing_public_release_stays_an_error_not_up_to_date(page):
    widget, client, *_ = page
    client.check.side_effect = UpdateError("Release unavailable. No published release or update file was found.")
    widget.check()
    until(lambda: not widget._busy)
    assert "Release unavailable" in widget.status.text()
    assert "up to date" not in widget.status.text()
    assert not widget.download_button.isEnabled()


def test_cancelled_check_cannot_overwrite_status_with_up_to_date(page):
    widget, client, *_ = page
    complete = threading.Event()
    client.check.side_effect = lambda _: (complete.wait(2) and None)
    widget.check()
    widget.cancel()
    complete.set()
    until(lambda: not widget._busy)
    assert "cancelled" in widget.status.text().lower()
    assert widget.release is None


def test_multigigabyte_progress_and_verified_download_never_auto_install(page, monkeypatch):
    widget, client, *_ = page
    widget.release = release()
    downloaded = SimpleNamespace(directory="updates/v1.0.1")

    def download(_release, _root, progress):
        progress(4_000_000_000, 5_000_000_000)
        return downloaded

    client.download.side_effect = download
    launcher = MagicMock()
    monkeypatch.setattr(ui, "launch_update", launcher)
    widget.download()
    until(lambda: not widget._busy)
    assert widget.downloaded is downloaded
    assert widget.progress.value() == 800
    launcher.assert_not_called()


def test_closed_page_discards_background_results(page):
    widget, client, *_ = page
    finish = threading.Event()
    client.check.side_effect = lambda _: (finish.wait(2) and release())
    widget.check()
    widget.shutdown()
    finish.set()
    QTest.qWait(20)
    assert widget.release is None
