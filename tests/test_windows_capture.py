"""Windows session changes must recover without freezing capture or Qt."""

import ctypes
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

if sys.platform != "win32":
    pytest.skip("Win32 capture", allow_module_level=True)

from interpreter.capture import windows as capture


@pytest.fixture
def desktop(monkeypatch):
    clock = [0.0]
    user32 = SimpleNamespace(IsWindow=Mock(return_value=True), IsIconic=Mock(return_value=False))
    monkeypatch.setattr(capture, "ctypes", SimpleNamespace(windll=SimpleNamespace(user32=user32)))
    monkeypatch.setattr(capture, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    remote = Mock(return_value=False)
    monkeypatch.setattr(capture, "is_remote_session", remote)
    gdi = Mock(side_effect=lambda *args, **kwargs: np.full((2, 3, 4), 20, np.uint8))
    monkeypatch.setattr(capture, "capture_window", gdi)
    return SimpleNamespace(clock=clock, user32=user32, remote=remote, gdi=gdi)


def pump(stream, desktop, count):
    observations = []

    def wait(timeout):
        frame = stream.get_frame()
        observations.append(None if frame is None else int(frame[0, 0, 0]))
        desktop.clock[0] += timeout
        if len(observations) == count:
            stream._stop_event.set()

    stream._stop_event.wait = wait
    stream._running = True
    stream._run()
    return observations


def native_sessions(stream, monkeypatch, *, frames=True, finished=False):
    controls = []

    def start():
        control = Mock()
        control.is_finished.return_value = finished
        controls.append(control)
        stream._control = control
        if frames:
            stream._latest_frame = np.full((2, 3, 4), len(controls), np.uint8)

    monkeypatch.setattr(stream, "_start_graphics_capture", start)
    return controls


def test_console_rdp_console_switch_discards_cached_frames_and_stops_native_session(desktop, monkeypatch):
    stream = capture.WindowsCaptureStream(42)
    controls = native_sessions(stream, monkeypatch)
    desktop.remote.side_effect = [False, True, True, False]
    assert pump(stream, desktop, 4) == [1, 20, 20, 2]
    assert len(controls) == 2
    assert all(c.stop.call_count == 1 for c in controls)
    assert desktop.gdi.call_count == 2
    desktop.gdi.assert_called_with(42, render_full_content=True)
    assert stream.get_frame() is None and not stream._running


def test_starting_inside_rdp_never_initializes_graphics_capture(desktop, monkeypatch):
    stream = capture.WindowsCaptureStream(42)
    desktop.remote.return_value = True
    native = Mock(side_effect=AssertionError("WGC initialized during RDP"))
    monkeypatch.setattr(stream, "_start_graphics_capture", native)
    assert pump(stream, desktop, 3) == [20, 20, 20]
    native.assert_not_called()


@pytest.mark.parametrize("failure", ["closed", "no_first_frame", "start_error"])
def test_native_failure_uses_window_rendering_then_retries(desktop, monkeypatch, failure):
    stream = capture.WindowsCaptureStream(42)
    controls = native_sessions(stream, monkeypatch, frames=False, finished=failure == "closed")
    if failure == "start_error":
        monkeypatch.setattr(stream, "_start_graphics_capture", Mock(side_effect=RuntimeError("device lost")))
    monkeypatch.setattr(capture, "GRAPHICS_START_TIMEOUT", 0.5)
    monkeypatch.setattr(capture, "GRAPHICS_RETRY_INTERVAL", 1.0)
    frames = pump(stream, desktop, 13)
    assert 20 in frames and None in frames
    if failure != "start_error":
        assert len(controls) >= 2
        assert all(c.stop.call_count == 1 for c in controls)
    assert not stream.window_invalid


def test_static_native_frame_does_not_trigger_periodic_restarts(desktop, monkeypatch):
    stream = capture.WindowsCaptureStream(42)
    controls = native_sessions(stream, monkeypatch)
    assert pump(stream, desktop, 60) == [1] * 60
    assert len(controls) == 1
    desktop.gdi.assert_not_called()


def test_missing_window_marks_invalid_and_never_captures(desktop, monkeypatch):
    stream = capture.WindowsCaptureStream(42)
    stream._latest_frame = np.ones((2, 3, 4), np.uint8)
    native = Mock()
    monkeypatch.setattr(stream, "_start_graphics_capture", native)
    desktop.user32.IsWindow.return_value = False
    stream._run()
    assert stream.window_invalid and stream.get_frame() is None
    native.assert_not_called()
    desktop.gdi.assert_not_called()


def test_minimize_or_failed_remote_render_never_returns_stale_image(desktop):
    stream = capture.WindowsCaptureStream(42)
    stream._latest_frame = np.ones((2, 3, 4), np.uint8)
    desktop.remote.return_value = True
    desktop.user32.IsIconic.side_effect = [False, True, False, False]
    desktop.gdi.side_effect = [np.full((2, 3, 4), 20, np.uint8), None, np.full((2, 3, 4), 30, np.uint8)]
    assert pump(stream, desktop, 4) == [20, None, None, 30]
    assert stream.error is None and not stream.window_invalid


def test_remote_capture_exception_can_recover_without_stop_start(desktop):
    stream = capture.WindowsCaptureStream(42)
    desktop.remote.return_value = True
    desktop.gdi.side_effect = [OSError("desktop unavailable"), np.full((2, 3, 4), 30, np.uint8)]
    assert pump(stream, desktop, 2) == [None, 30]
    assert stream.error is None and not stream.window_invalid


def test_start_and_stop_do_not_wait_for_a_synchronous_capture_call(monkeypatch):
    stream = capture.WindowsCaptureStream(42)
    entered, release = threading.Event(), threading.Event()

    def capture_loop():
        entered.set()
        release.wait(5)

    monkeypatch.setattr(stream, "_run", capture_loop)
    try:
        stream.start()
        assert entered.wait(2)
        assert not release.is_set() and stream._thread.is_alive()
        stream.stop()
        assert stream._stop_event.is_set() and stream.get_frame() is None
        # stop returned while the native capture operation is still blocked.
        assert stream._thread.is_alive()
    finally:
        release.set()
        stream._thread.join(3)
    assert not stream._thread.is_alive()


def test_callbacks_from_closed_graphics_session_cannot_stop_or_overwrite_replacement(monkeypatch):
    captures = []

    class Native:
        def __init__(self, **kwargs):
            self.handlers = {}
            self.control = Mock()
            captures.append(self)

        def event(self, function):
            self.handlers[function.__name__] = function
            return function

        def start_free_threaded(self):
            return self.control

    monkeypatch.setitem(
        sys.modules,
        "windows_capture",
        SimpleNamespace(WindowsCapture=Native, Frame=object, InternalCaptureControl=object),
    )
    monkeypatch.setattr(capture, "get_windows_build", lambda: 22631)
    monkeypatch.setattr(capture, "get_window_style_flags", lambda hwnd: "NORMAL")
    monkeypatch.setattr(capture, "_get_monitor_size_for_window", lambda hwnd: (30, 20))
    monkeypatch.setattr(
        capture, "get_title_bar_height", Mock(side_effect=AssertionError("borderless has no title bar"))
    )
    stream = capture.WindowsCaptureStream(42)
    stream._running = True
    stream._start_graphics_capture()
    old = captures[-1]
    stream._stop_graphics()
    stream._start_graphics_capture()
    new = captures[-1]
    frame = SimpleNamespace(width=3, height=2, frame_buffer=np.full((2, 3, 4), 55, np.uint8))
    new.handlers["on_frame_arrived"](frame, new.control)
    old.handlers["on_closed"]()
    old.handlers["on_frame_arrived"](frame, old.control)
    assert stream._running and not stream._graphics_closed
    assert np.all(stream.get_frame() == 55)
    new.control.stop.assert_not_called()
    new.handlers["on_closed"]()
    assert stream._graphics_closed and stream.get_frame() is None
    stream._stop_graphics()
    new.control.stop.assert_called_once()


@pytest.mark.parametrize("protocol", [0, 2])
def test_remote_protocol_detection_works_when_remote_system_metric_is_false(monkeypatch, protocol):
    value = ctypes.c_ushort(protocol)

    def query(server, session, kind, output, size):
        assert server is None and session == 0xFFFFFFFF and kind == 16
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(ctypes.pointer(value), ctypes.c_void_p)
        ctypes.cast(size, ctypes.POINTER(ctypes.wintypes.DWORD))[0] = ctypes.sizeof(value)
        return True

    wts = SimpleNamespace(WTSQuerySessionInformationW=Mock(side_effect=query), WTSFreeMemory=Mock())
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: wts)
    monkeypatch.setattr(ctypes.windll.user32, "GetSystemMetrics", Mock(return_value=0))
    assert capture.is_remote_session() == bool(protocol)
    wts.WTSFreeMemory.assert_called_once()


def test_failed_remote_protocol_query_falls_back_to_system_metric(monkeypatch):
    wts = SimpleNamespace(WTSQuerySessionInformationW=Mock(return_value=False), WTSFreeMemory=Mock())
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: wts)
    monkeypatch.setattr(ctypes.windll.user32, "GetSystemMetrics", Mock(return_value=1))
    assert capture.is_remote_session()
    wts.WTSFreeMemory.assert_not_called()


@pytest.mark.parametrize("render_ok", [True, False])
def test_remote_printwindow_uses_only_client_area_and_releases_gdi_resources(monkeypatch, render_ok):
    monkeypatch.setattr(capture, "_get_client_bounds", lambda hwnd: {"width": 3, "height": 2})
    user32, gdi32 = Mock(), Mock()
    user32.GetDC.return_value = 101
    user32.PrintWindow.return_value = render_ok
    gdi32.CreateCompatibleDC.return_value = 102
    gdi32.CreateCompatibleBitmap.return_value = 103
    gdi32.SelectObject.return_value = 104

    def read_bitmap(dc, bitmap, start, height, buffer, info, flags):
        assert gdi32.SelectObject.call_args.args == (102, 104), "bitmap must be deselected before GetDIBits"
        ctypes.memset(buffer, 77, 3 * 2 * 4)
        return height

    gdi32.GetDIBits.side_effect = read_bitmap
    monkeypatch.setattr(ctypes.windll, "user32", user32)
    monkeypatch.setattr(ctypes.windll, "gdi32", gdi32)
    frame = capture.capture_window(42, render_full_content=True)
    user32.PrintWindow.assert_called_once_with(42, 102, 3)
    gdi32.BitBlt.assert_not_called()
    if render_ok:
        assert frame.shape == (2, 3, 4) and np.all(frame == 77)
    else:
        assert frame is None
        gdi32.GetDIBits.assert_not_called()
    gdi32.DeleteObject.assert_called_once_with(103)
    gdi32.DeleteDC.assert_called_once_with(102)
    user32.ReleaseDC.assert_called_once_with(42, 101)
    assert user32.GetDC.restype is ctypes.wintypes.HDC
