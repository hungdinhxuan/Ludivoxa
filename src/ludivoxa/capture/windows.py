"""Windows Graphics Capture with a window-only GDI path for Remote Desktop."""

import ctypes
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

import numpy as np
from numpy.typing import NDArray

from .convert import bgra_to_rgb_pil

# Fixed capture interval (4 FPS) - throttle Windows Graphics Capture API
CAPTURE_INTERVAL = 0.25
GRAPHICS_START_TIMEOUT = 5.0
GRAPHICS_RETRY_INTERVAL = 10.0

# Windows build number requirements for Graphics Capture API features
# - Build 18362 (Win10 1903): Basic Windows Graphics Capture API
# - Build 22000 (Win11): IsBorderRequired property to disable yellow border
MIN_BUILD_GRAPHICS_CAPTURE = 18362
MIN_BUILD_BORDER_TOGGLE = 22000


def get_content_offset(window_id: int) -> tuple[int, int]:
    """Get the offset of the content area within a window.

    On Windows, the overlay is positioned at the client area and the capture
    also covers the client area (after cropping title bar). So overlay and
    capture are aligned - no offset needed.

    Args:
        window_id: The window handle (HWND).

    Returns:
        Tuple of (x_offset, y_offset) in pixels. Always (0, 0) on Windows.
    """
    return (0, 0)


def get_windows_build() -> int:
    """Get the Windows build number.

    Returns:
        Windows build number (e.g., 22000 for Windows 11, 19045 for Windows 10 22H2).
        Returns 0 if detection fails or not on Windows.
    """
    if sys.platform != "win32":
        return 0

    try:
        # Use RtlGetVersion for accurate version (not subject to compatibility shims)
        # GetVersionEx can return wrong values due to app manifest compatibility
        ntdll = ctypes.windll.ntdll

        class OSVERSIONINFOEXW(ctypes.Structure):
            _fields_ = [
                ("dwOSVersionInfoSize", wintypes.DWORD),
                ("dwMajorVersion", wintypes.DWORD),
                ("dwMinorVersion", wintypes.DWORD),
                ("dwBuildNumber", wintypes.DWORD),
                ("dwPlatformId", wintypes.DWORD),
                ("szCSDVersion", wintypes.WCHAR * 128),
                ("wServicePackMajor", wintypes.WORD),
                ("wServicePackMinor", wintypes.WORD),
                ("wSuiteMask", wintypes.WORD),
                ("wProductType", wintypes.BYTE),
                ("wReserved", wintypes.BYTE),
            ]

        version_info = OSVERSIONINFOEXW()
        version_info.dwOSVersionInfoSize = ctypes.sizeof(OSVERSIONINFOEXW)
        ntdll.RtlGetVersion(ctypes.byref(version_info))
        return version_info.dwBuildNumber
    except Exception:
        return 0


def get_windows_version_string() -> str:
    """Get a human-readable Windows version string.

    Returns:
        Version string like 'Windows 11 (build 22000)' or 'Windows 10 (build 19045)'.
    """
    build = get_windows_build()
    if build == 0:
        return "Unknown"

    # Windows 11 starts at build 22000
    if build >= 22000:
        return f"Windows 11 (build {build})"
    else:
        return f"Windows 10 (build {build})"


def get_window_style_flags(hwnd: int) -> str:
    """Get window style flags for diagnostic logging.

    Args:
        hwnd: The window handle (HWND).

    Returns:
        String describing the window style flags (e.g., "POPUP+TOPMOST" or "CAPTION").
    """
    user32 = ctypes.windll.user32

    GWL_STYLE = -16
    GWL_EXSTYLE = -20

    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

    # Key style flags that affect capture
    flags = []
    if style & 0x80000000:  # WS_POPUP
        flags.append("POPUP")
    if style & 0x00C00000:  # WS_CAPTION
        flags.append("CAPTION")
    if exstyle & 0x00000008:  # WS_EX_TOPMOST
        flags.append("TOPMOST")
    if exstyle & 0x08000000:  # WS_EX_NOREDIRECTIONBITMAP
        flags.append("NOREDIRECT")

    return "+".join(flags) if flags else "NORMAL"


# Windows-specific imports
try:
    import pygetwindow as gw

    WINDOWS_AVAILABLE = True
except ImportError:
    WINDOWS_AVAILABLE = False

# Win32 API constants
DIB_RGB_COLORS = 0
BI_RGB = 0
PW_RENDERFULLCONTENT = 2  # Capture even if window is layered/composited
PW_CLIENTONLY = 1

# System metrics constants
SM_CYCAPTION = 4  # Title bar height
SM_CYFRAME = 33  # Window frame height
SM_CXPADDEDBORDER = 92  # Padded border width (Vista+)


def get_title_bar_height() -> int:
    """Get the system title bar height in screen pixels.

    Uses GetSystemMetrics to get the actual title bar height for the current
    Windows theme and DPI setting.

    Returns:
        Title bar height in screen pixels.
    """
    user32 = ctypes.windll.user32
    # SM_CYCAPTION gives the title bar height
    caption_height = user32.GetSystemMetrics(SM_CYCAPTION)
    # SM_CYFRAME gives the window frame thickness
    frame_height = user32.GetSystemMetrics(SM_CYFRAME)
    # SM_CXPADDEDBORDER gives extra padding (Windows Vista+)
    padded_border = user32.GetSystemMetrics(SM_CXPADDEDBORDER)

    # Total non-client area at top = caption + frame + padded border
    return caption_height + frame_height + padded_border


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def _is_window_cloaked(hwnd: int) -> bool:
    """Check if a window is cloaked by DWM.

    UWP and modern apps leave behind hidden 'ghost' top-level windows that
    still enumerate via EnumWindows but are invisible to the user.
    """
    DWMWA_CLOAKED = 14
    cloaked = wintypes.DWORD(0)
    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_uint(DWMWA_CLOAKED),
        ctypes.byref(cloaked),
        ctypes.sizeof(cloaked),
    )
    return hr == 0 and cloaked.value != 0


def _get_window_process_name(hwnd: int) -> str | None:
    """Get the owning process's executable basename (no extension).

    Used to give nameless windows a more identifiable label — e.g.
    RPGMaker games typically ship as 'Game.exe' so the user sees
    'Game' instead of an opaque HWND. Returns None on failure.
    """
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == 0:
        return None

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        name = buf.value.rsplit("\\", 1)[-1]
        if name.lower().endswith(".exe"):
            name = name[:-4]
        return name or None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def get_window_list() -> list[dict]:
    """Get list of all top-level capturable windows.

    Nameless windows (e.g. some RPGMaker games that leave Game.ini Title=
    empty) are labeled with the owning process name and HWND so users can
    still identify and select them. Invisible, cloaked, and zero-size
    windows are filtered out.

    Returns:
        List of window dictionaries with keys: id, title, owner, bounds
    """
    if not WINDOWS_AVAILABLE:
        return []

    user32 = ctypes.windll.user32
    shell_hwnd = user32.GetShellWindow()

    windows = []
    for win in gw.getAllWindows():
        hwnd = win._hWnd
        if hwnd == shell_hwnd:
            continue
        if not user32.IsWindowVisible(hwnd):
            continue
        if win.width <= 0 or win.height <= 0:
            continue
        if _is_window_cloaked(hwnd):
            continue
        title = win.title
        if not title:
            proc = _get_window_process_name(hwnd)
            title = f"{proc} ({hex(hwnd)})" if proc else f"[Untitled window {hex(hwnd)}]"
        windows.append(
            {
                "id": hwnd,
                "title": title,
                "owner": "",
                "bounds": {
                    "x": win.left,
                    "y": win.top,
                    "width": win.width,
                    "height": win.height,
                },
            }
        )
    return windows


def find_window_by_title(title_substring: str) -> dict | None:
    """Find a window by partial title match.

    Args:
        title_substring: Substring to search for in window titles.

    Returns:
        Window dictionary if found, None otherwise.
    """
    title_lower = title_substring.lower()
    windows = get_window_list()

    for window in windows:
        if title_lower in window["title"].lower():
            return window

    return None


class RECT(ctypes.Structure):
    """Windows RECT structure."""

    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    """Windows POINT structure."""

    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


def _get_window_bounds(window_id: int) -> dict | None:
    """Get the current bounds of a window (full window including chrome).

    Args:
        window_id: The window handle (HWND).

    Returns:
        Dictionary with x, y, width, height or None if failed.
    """
    rect = RECT()
    user32 = ctypes.windll.user32
    if user32.GetWindowRect(window_id, ctypes.byref(rect)):
        return {
            "x": rect.left,
            "y": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        }
    return None


def is_window_foreground(window_id: int) -> bool:
    """Check if the specified window is the foreground window.

    Args:
        window_id: The window handle (HWND).

    Returns:
        True if the window is in foreground, False otherwise.
    """
    user32 = ctypes.windll.user32
    foreground_hwnd = user32.GetForegroundWindow()
    return foreground_hwnd == window_id


def _get_client_bounds(window_id: int) -> dict | None:
    """Get the client area bounds (content without title bar/borders).

    Args:
        window_id: The window handle (HWND).

    Returns:
        Dictionary with x, y, width, height in screen coordinates, or None if failed.
    """
    user32 = ctypes.windll.user32

    # Get client rect (in client coordinates, so left/top are 0)
    client_rect = RECT()
    if not user32.GetClientRect(window_id, ctypes.byref(client_rect)):
        return None

    # Convert client (0,0) to screen coordinates
    point = POINT(0, 0)
    if not user32.ClientToScreen(window_id, ctypes.byref(point)):
        return None

    return {
        "x": point.x,
        "y": point.y,
        "width": client_rect.right,  # client_rect.left is always 0
        "height": client_rect.bottom,  # client_rect.top is always 0
    }


def _session_value(kind, value_type):
    """Read current-session WTS information and always release its buffer."""
    try:
        wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        query = wts.WTSQuerySessionInformationW
        query.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        query.restype = wintypes.BOOL
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        wts.WTSFreeMemory.restype = None
        buffer = ctypes.c_void_p()
        size = wintypes.DWORD()
        # WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION.
        if query(None, 0xFFFFFFFF, kind, ctypes.byref(buffer), ctypes.byref(size)):
            try:
                if buffer.value and size.value >= ctypes.sizeof(value_type):
                    return ctypes.cast(buffer, ctypes.POINTER(value_type)).contents.value
            finally:
                wts.WTSFreeMemory(buffer)
    except (OSError, AttributeError):
        pass
    return None


def is_remote_session() -> bool:
    """Query the current session's protocol, including RemoteFX sessions."""
    protocol = _session_value(16, ctypes.c_ushort)  # WTSClientProtocolType
    if protocol is not None:
        return protocol != 0
    return bool(ctypes.windll.user32.GetSystemMetrics(0x1000))  # SM_REMOTESESSION


def is_session_active() -> bool:
    # Starting WGC on a disconnected desktop can block the native start call
    # until the user reconnects. Wait without holding a native session instead.
    state = _session_value(8, ctypes.c_int)  # WTSConnectState; WTSActive == 0
    return state is None or state == 0


def capture_window(window_id: int, *, render_full_content: bool = False) -> NDArray[np.uint8] | None:
    """Capture a screenshot of a specific window's client area.

    Remote Desktop uses PrintWindow to render this HWND alone. Capturing the
    desktop would also read our translated overlay back into OCR. PrintWindow
    is synchronous and must only be used on the background capture thread.

    Args:
        window_id: The window handle (HWND).
        render_full_content: Ask the app to render its client area with PrintWindow.

    Returns:
        Numpy array (H, W, 4) in BGRA format, or None if capture failed.
    """
    # Get client area dimensions
    client_bounds = _get_client_bounds(window_id)
    if not client_bounds:
        return None

    width = client_bounds["width"]
    height = client_bounds["height"]

    if width <= 0 or height <= 0:
        return None

    # Get API functions
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    # GDI handles are pointer-sized on 64-bit Windows; ctypes defaults to int.
    user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = [wintypes.HWND, wintypes.HDC], ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        *([ctypes.c_int] * 4),
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = [wintypes.HANDLE], wintypes.BOOL
    gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = [wintypes.HDC], wintypes.BOOL

    # Get the window's client area DC (not screen DC)
    # GetDC with hwnd returns DC for client area only
    hwnd_dc = user32.GetDC(window_id)
    if not hwnd_dc:
        return None

    try:
        # Create compatible DC and bitmap
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        if not mem_dc:
            return None

        try:
            bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
            if not bitmap:
                return None

            try:
                # Select bitmap into memory DC
                old_bitmap = gdi32.SelectObject(mem_dc, bitmap)

                # BitBlt from window DC (copies from client area origin 0,0)
                SRCCOPY = 0x00CC0020
                if render_full_content:
                    result = user32.PrintWindow(window_id, mem_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
                else:
                    result = gdi32.BitBlt(mem_dc, 0, 0, width, height, hwnd_dc, 0, 0, SRCCOPY)
                gdi32.SelectObject(mem_dc, old_bitmap)

                if not result:
                    return None

                # Prepare bitmap info for GetDIBits
                bmi = BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = width
                bmi.bmiHeader.biHeight = -height  # Negative for top-down
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = BI_RGB

                # Create buffer for pixel data
                buffer_size = width * height * 4
                buffer = ctypes.create_string_buffer(buffer_size)

                # Get bitmap bits
                result = gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(bmi), DIB_RGB_COLORS)

                if result == 0:
                    return None

                # Return raw BGRA numpy array (consumers convert on demand)
                arr = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width, 4)).copy()
                return arr

            finally:
                gdi32.DeleteObject(bitmap)
        finally:
            gdi32.DeleteDC(mem_dc)
    finally:
        user32.ReleaseDC(window_id, hwnd_dc)


def _get_screen_size() -> tuple[int, int]:
    """Get the primary screen size in pixels."""
    user32 = ctypes.windll.user32
    # Get virtual screen size (accounts for DPI)
    width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
    height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
    return width, height


class MONITORINFO(ctypes.Structure):
    """Windows MONITORINFO structure."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def _get_monitor_size_for_window(window_id: int) -> tuple[int, int]:
    """Get the monitor size for the monitor containing the specified window.

    Args:
        window_id: The window handle (HWND).

    Returns:
        Tuple of (width, height) of the monitor, or primary screen size as fallback.
    """
    user32 = ctypes.windll.user32

    # Get the monitor that contains the window
    MONITOR_DEFAULTTONEAREST = 2
    hmonitor = user32.MonitorFromWindow(window_id, MONITOR_DEFAULTTONEAREST)

    if hmonitor:
        # Get monitor info
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(mi)):
            width = mi.rcMonitor.right - mi.rcMonitor.left
            height = mi.rcMonitor.bottom - mi.rcMonitor.top
            return width, height

    # Fallback to primary screen
    return _get_screen_size()


class WindowsCaptureStream:
    """Continuous window capture using Windows Graphics Capture API.

    Uses the windows-capture library which wraps the Windows Graphics Capture API.
    This can capture DirectX/OpenGL content even when the window is in the background.
    Throttled to 4 FPS to avoid wasteful high-frequency capture.
    """

    def __init__(self, window_id: int, on_frame_ready: Callable[[], None] | None = None):
        """Initialize the capture stream.

        Args:
            window_id: The window handle (HWND) to capture.
            on_frame_ready: Optional callback for a newly available frame.
        """
        self._window_id = window_id
        self._latest_frame: NDArray[np.uint8] | None = None
        self._captured_at: float | None = None
        self.backend = ""
        self._on_frame_ready = on_frame_ready
        self._frame_notification_pending = False
        self._frame_lock = threading.Lock()
        self._capture = None
        self._control = None
        self._thread = None
        self._stop_event = threading.Event()
        self._graphics_generation = 0
        self._graphics_closed = False
        self._window_invalid = False
        self.error: str | None = None
        self._running = False
        self._last_frame_time: float = 0.0  # For 4 FPS throttling

    def start(self):
        """Start asynchronously; display/session recovery never blocks Qt."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="ludivoxa-capture", daemon=True)
        self._thread.start()

    @property
    def window_invalid(self) -> bool:
        return self._window_invalid

    def _clear_frame(self):
        with self._frame_lock:
            self._latest_frame = None
            self._captured_at = None
            self.backend = ""

    def _publish_frame(self, frame: NDArray[np.uint8], captured_at: float, graphics_generation: int | None = None):
        with self._frame_lock:
            if not self._running or (
                graphics_generation is not None and graphics_generation != self._graphics_generation
            ):
                return
            self._latest_frame = frame
            self._captured_at = captured_at
            self.backend = "Windows Graphics Capture" if graphics_generation is not None else "PrintWindow"
            self.error = None
            # Hold just one notification while the consumer is busy.
            notify = self._on_frame_ready is not None and not self._frame_notification_pending
            if notify:
                self._frame_notification_pending = True
        if notify:
            self._on_frame_ready()

    def _stop_graphics(self):
        # Invalidate callbacks before stopping: an old on_closed must not stop
        # the replacement session, nor may it publish its last cached frame.
        self._graphics_generation += 1
        self._clear_frame()
        control, self._control = self._control, None
        capture, self._capture = self._capture, None
        if control is not None:
            try:
                control.stop()
            except Exception as exc:
                from .. import log

                log.get_logger().warning("failed to stop graphics capture", error=str(exc))
        del capture

    def _run(self):
        from .. import log

        logger = log.get_logger()
        remote = None
        retry_at = 0.0
        graphics_started = 0.0
        last_error_log = float("-inf")
        try:
            while not self._stop_event.is_set():
                tick = time.monotonic()
                try:
                    if not ctypes.windll.user32.IsWindow(self._window_id):
                        self._window_invalid = True
                        break
                    if not is_session_active():
                        self._stop_graphics()
                        self.error = "Waiting for an active Windows session."
                        remote, retry_at = None, 0.0
                        self._stop_event.wait(CAPTURE_INTERVAL)
                        continue
                    current_remote = is_remote_session()
                    if current_remote != remote:
                        self._stop_graphics()
                        self._clear_frame()
                        remote, retry_at = current_remote, 0.0
                        logger.info(
                            "capture session changed",
                            remote=remote,
                            backend="PrintWindow" if remote else "Windows Graphics Capture",
                        )
                    if ctypes.windll.user32.IsIconic(self._window_id):
                        self._clear_frame()
                    elif remote or tick < retry_at:
                        # This renders only the target HWND, so inplace overlay
                        # text cannot feed back into OCR as with desktop capture.
                        frame = capture_window(self._window_id, render_full_content=True)
                        if frame is not None:
                            self._publish_frame(frame, tick)
                        else:
                            self._clear_frame()
                            self.error = "Waiting for window frames. Keep the game visible."
                    else:
                        if self._control is None:
                            self._graphics_closed = False
                            try:
                                self._start_graphics_capture()
                                graphics_started = time.monotonic()
                            except Exception:
                                self._stop_graphics()
                                self._clear_frame()
                                retry_at = tick + GRAPHICS_RETRY_INTERVAL
                                raise
                        if (
                            self._graphics_closed
                            or self._control.is_finished()
                            or (self.get_frame() is None and tick - graphics_started >= GRAPHICS_START_TIMEOUT)
                        ):
                            logger.warning("graphics capture interrupted, using window rendering until retry")
                            self._stop_graphics()
                            self._clear_frame()
                            retry_at = tick + GRAPHICS_RETRY_INTERVAL
                except Exception as exc:
                    self.error = str(exc)
                    self._clear_frame()
                    if tick - last_error_log >= GRAPHICS_RETRY_INTERVAL:
                        logger.warning("waiting for window capture", error=self.error)
                        last_error_log = tick
                self._stop_event.wait(max(0.01, CAPTURE_INTERVAL - (time.monotonic() - tick)))
        finally:
            self._running = False
            self._stop_graphics()
            self._clear_frame()

    def _start_graphics_capture(self):
        """Create one native session, called only by the capture thread."""
        from windows_capture import Frame, InternalCaptureControl, WindowsCapture

        # Check Windows version for Graphics Capture API support
        build = get_windows_build()
        if build < MIN_BUILD_GRAPHICS_CAPTURE:
            raise RuntimeError(
                f"Windows Graphics Capture API requires Windows 10 version 1903 or later "
                f"(build {MIN_BUILD_GRAPHICS_CAPTURE}+). Your system is build {build}. "
                f"Please update Windows to use this application."
            )

        # Determine border setting based on Windows version
        # - Windows 11 (build 22000+): Can disable border with draw_border=False
        # - Windows 10 1903-21H2: Must use draw_border=None (yellow border will appear)
        if build >= MIN_BUILD_BORDER_TOGGLE:
            draw_border = False  # Disable yellow capture border
        else:
            draw_border = None  # Skip border config (yellow border will appear)

        self._last_frame_time = 0.0  # Reset throttling
        self._first_frame_logged = False  # Track if we've logged first frame
        self._graphics_generation += 1
        generation = self._graphics_generation

        # Log capture configuration
        from .. import log

        logger = log.get_logger()
        window_style = get_window_style_flags(self._window_id)
        logger.info(
            "capture started (hwnd)",
            hwnd=hex(self._window_id),
            draw_border=draw_border,
            window_style=window_style,
        )

        # Create capture instance using HWND (more reliable than window_name for dynamic titles)
        self._capture = WindowsCapture(
            cursor_capture=False,
            draw_border=draw_border,
            window_hwnd=self._window_id,
        )

        # Reference to self for use in callback
        stream = self

        # Set up frame handler
        @self._capture.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
            if not stream._running or generation != stream._graphics_generation:
                capture_control.stop()
                return

            # Throttle to 4 FPS - skip frames that arrive too quickly
            now = time.monotonic()
            if now - stream._last_frame_time < CAPTURE_INTERVAL:
                return
            stream._last_frame_time = now

            try:
                # Detect fullscreen: if frame fills the monitor, don't crop title bar
                # Use the monitor the window is on, not just the primary screen
                if stream._window_id:
                    screen_w, screen_h = _get_monitor_size_for_window(stream._window_id)
                else:
                    screen_w, screen_h = _get_screen_size()
                is_fullscreen = frame.width >= screen_w and frame.height >= screen_h

                # Log first frame info
                if not stream._first_frame_logged:
                    stream._first_frame_logged = True
                    from .. import log

                    log.get_logger().info(
                        "first frame received",
                        resolution=f"{frame.width}x{frame.height}",
                        fullscreen=is_fullscreen,
                    )

                # Borderless games (and RDP windows one pixel shorter than the
                # monitor) have no title bar to crop. Keep their client pixels
                # aligned with PrintWindow when switching session backends.
                if not is_fullscreen and "CAPTION" in get_window_style_flags(stream._window_id):
                    # Get title bar height in screen pixels
                    title_bar_screen = get_title_bar_height()
                    # Get window bounds to calculate the actual scale
                    # (frame size vs window size, not screen size)
                    window_bounds = _get_window_bounds(stream._window_id) if stream._window_id else None
                    if window_bounds and window_bounds["width"] > 0:
                        scale = frame.width / window_bounds["width"]
                    else:
                        scale = 1.0
                    # Convert to capture pixels
                    title_bar_px = int(title_bar_screen * scale)
                    if frame.height > title_bar_px:
                        frame = frame.crop(0, title_bar_px, frame.width, frame.height)

                # Get frame buffer (numpy array, shape: height x width x 4, BGRA format)
                arr = frame.frame_buffer

                # Save first frame for debugging (only once, only in debug mode)
                if not hasattr(stream, "_debug_frame_saved"):
                    stream._debug_frame_saved = True
                    from .. import log

                    if log.is_debug_enabled():
                        try:
                            debug_img = bgra_to_rgb_pil(arr)
                            debug_path = "debug_capture.png"
                            debug_img.save(debug_path)
                            log.get_logger().info("saved debug frame", path=debug_path)
                        except Exception as e:
                            log.get_logger().warning("failed to save debug frame", error=str(e))

                # Store raw BGRA numpy array (consumers convert on demand)
                stream._publish_frame(arr.copy(), now, generation)
            except Exception as e:
                from .. import log

                log.get_logger().error("frame processing failed", error=str(e))

        @self._capture.event
        def on_closed():
            if generation == stream._graphics_generation:
                stream._graphics_closed = True
                stream._clear_frame()

        # Start capture in free-threaded mode (non-blocking)
        try:
            self._control = self._capture.start_free_threaded()
        except Exception as e:
            error_msg = str(e).strip()
            if not error_msg:
                # windows-capture sometimes throws empty exceptions
                raise RuntimeError(
                    f"Failed to start screen capture for window {hex(self._window_id)}. "
                    f"This can happen if the window uses exclusive fullscreen or a video driver "
                    f"that bypasses Windows Desktop Window Manager (DWM).\n\n"
                    f"Troubleshooting tips:\n"
                    f"  - Try running the application in windowed or borderless windowed mode\n"
                    f"  - For emulators: switch video driver to 'gl', 'glcore', or 'd3d11' (not 'vulkan' exclusive)\n"
                    f"  - Make sure the window is visible and not minimized\n"
                    f"  - Try running ludivoxa as Administrator"
                ) from e
            else:
                raise RuntimeError(
                    f"Failed to start screen capture for window {hex(self._window_id)}: {error_msg}"
                ) from e

    def get_frame(self) -> NDArray[np.uint8] | None:
        """Get the latest captured frame.

        Returns:
            Numpy array (H, W, 4) in BGRA format, or None if no frame available.
        """
        with self._frame_lock:
            return self._latest_frame

    def get_frame_with_timestamp(self) -> tuple[NDArray[np.uint8] | None, float | None]:
        """Consume the notification and read the most recent frame atomically."""
        with self._frame_lock:
            self._frame_notification_pending = False
            return self._latest_frame, self._captured_at

    def stop(self):
        """Stop the capture stream."""
        self._running = False
        self._stop_event.set()
        self._clear_frame()
