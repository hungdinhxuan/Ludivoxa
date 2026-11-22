"""Main application window with settings and controls."""

import math
import sys
import threading
import time

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QImage, QKeySequence, QPixmap
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFontDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, log
from ..capture import Capture, WindowCapture, _is_wayland_session
from ..capture.convert import bgra_to_rgb_pil
from ..capture.video_device import VideoDeviceCapture, device_id, find_video_device, list_video_devices
from ..config import (
    Config,
    GameProfile,
    LLMSettings,
    OCRBackend,
    OutputMode,
    OverlayMode,
    OwocrSettings,
    PaddleSettings,
    TranslationBackend,
    normalize_ocr_regions,
)
from ..languages import (
    DEFAULT_SOURCE_LANGUAGE,
    OCR_SOURCE_LANGUAGES,
    TARGET_LANGUAGE_PRESETS,
    is_vietnamese_target,
    target_language_or_default,
)
from ..llm_translate import (
    DEFAULT_BASE_URLS,
    DEFAULT_SYSTEM_PROMPT,
    PROVIDER_LABELS,
    PROVIDERS,
    check_endpoint,
    describe_request_error,
    list_models,
    normalize_base_url,
    provider_label,
)
from ..local_tts import clone_language, local_clone_url
from ..ocr import PADDLE_LANGUAGE_MODELS
from ..overlay import BannerOverlay, InplaceOverlay
from ..owocr_ocr import DEFAULT_URL as OWOCR_DEFAULT_URL
from ..owocr_ocr import SERVER_COMMAND as OWOCR_SERVER_COMMAND
from ..owocr_ocr import check_endpoint as check_owocr_endpoint
from ..owocr_ocr import normalize_url as normalize_owocr_url
from ..owocr_ocr import remote_warning as owocr_remote_warning
from ..permissions import (
    check_accessibility,
    check_screen_recording,
    is_macos,
    open_accessibility_settings,
    open_screen_recording_settings,
    request_accessibility,
    request_screen_recording,
)
from ..speech import DEFAULT_VOICE_PATH, SpeechController, SpeechStream, available_voices, voice_details, voice_label
from ..translate import SUGOI_ONLY_JAPANESE, SUGOI_SOURCE_LANGUAGE
from . import keyboard
from .character_voices import CharacterVoicesDialog
from .diagnostics import DiagnosticsDialog
from .game_profile import GameProfileDialog
from .history_page import HistoryPage
from .i18n import set_interface_language, tr
from .ocr_config import OCRConfigDialog
from .theme import ERROR, OK, set_role
from .updates_page import UpdatesPage
from .workers import ProcessWorker

logger = log.get_logger()

# Font settings
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 72

# Poll promptly; the worker retains only the latest frame and handles inference
# off the UI thread. Preview painting keeps its original, lower refresh rate.
PROCESS_INTERVAL_MS = 150
PREVIEW_INTERVAL_MS = 500
CAPTURE_IDLE_SECONDS = 0.8
NO_SIGNAL_NOTICE_MS = 3000  # video device session without frames before the status bar says so


class MainWindow(QMainWindow):
    """Main application window."""

    # Signals for thread-safe hotkey handling
    hotkey_pressed = Signal()
    mode_switch_pressed = Signal()
    _capture_frame_ready = Signal(object)

    # Results of background endpoint queries (list of models, or (translation, ms)); str = error
    _llm_models_result = Signal(object)
    _llm_test_result = Signal(object)
    # Result of the owocr Test button (round-trip ms); str = error
    _owocr_test_result = Signal(object)

    PREVIEW_WIDTH = 960  # rendered once at this width, then scaled down to the card

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        set_interface_language(config.interface_language)
        self._speech = SpeechController(self)
        self._speech.set_enabled(config.live_speech_enabled)
        self._speech.set_volume(config.tts_volume)
        self._last_ocr_results: list = []
        self._preview_pixmap: QPixmap | None = None

        self.setWindowTitle("Ludivoxa")

        # State
        self._capturing = False
        self._capture_generation = 0
        self._last_ocr_results = []
        self._mode = config.overlay_mode
        self._windows_list: list[dict] = []
        self._paused = False
        self._current_window_title: str = ""  # For exclusion zone lookup
        self._ocr_config_dialog = None  # Reference to open OCR config dialog
        self._character_dialog = None
        self._detected_character = ""
        self._history_overlay = None
        self._diagnostics_dialog = None
        # Wayland session detection (from capture module, uses D-Bus portal check)
        self._is_wayland_session = _is_wayland_session
        self._wayland_portal = None  # WaylandPortalCapture instance (for managing portal session lifecycle)
        self._wayland_selecting = False  # Guard against re-entry during portal flow
        # Video device sessions are banner only; the user's mode is restored afterwards
        self._banner_only = False
        self._mode_before_video: OverlayMode | None = None
        # Consecutive processing ticks without a frame (a capture card with the console off)
        self._frames_missing = 0
        self._waiting_for_signal = False
        self._fixing_ocr = False  # Track if we're re-downloading OCR model
        self._fixing_translation = False  # Track if we're re-downloading translation model

        # Components - unified capture interface (WindowCapture or WaylandCaptureStream)
        self._capture: Capture | None = None

        # Worker for OCR/translation (uses Python threading internally)
        self._process_worker = self._create_worker()
        self._llm_models_result.connect(self._on_llm_models_listed)
        self._llm_test_result.connect(self._on_llm_test_done)
        self._owocr_test_result.connect(self._on_owocr_test_done)
        # Settings snapshots of the in-flight Refresh / Test requests (stale results are dropped)
        self._llm_refresh_request: LLMSettings | None = None
        self._llm_test_request: LLMSettings | None = None
        self._llm_test_language: str = self._config.source_language
        self._owocr_test_request: OwocrSettings | None = None

        # Overlays
        self._banner_overlay = BannerOverlay(
            font_family=config.font_family,
            font_size=config.font_size,
            font_color=config.font_color,
            background_color=config.background_color,
            background_opacity=config.background_opacity,
        )
        self._inplace_overlay = InplaceOverlay(
            font_family=config.font_family,
            font_size=config.font_size,
            font_color=config.font_color,
            background_color=config.background_color,
            background_opacity=config.background_opacity,
        )

        # Apply saved banner position if available
        if config.banner_x is not None and config.banner_y is not None:
            logger.debug("restoring banner position", x=config.banner_x, y=config.banner_y)
            self._banner_overlay.set_position(config.banner_x, config.banner_y)
            # Ensure banner is visible (handles monitor changes, resolution changes, etc.)
            self._banner_overlay.clamp_to_visible_area()

        self._setup_capture_timers()
        self._last_frame = None
        self._last_bounds = {}
        self._preview_updated_at = float("-inf")

        # Re-list the sources when a capture card or webcam is plugged in or removed
        self._media_devices = QMediaDevices(self)
        self._media_devices.videoInputsChanged.connect(self._on_video_devices_changed)

        self._setup_ui()
        available = self.screen().availableGeometry()
        self.resize(min(840, available.width() - 24), min(660, available.height() - 48))
        self._refresh_sources()
        self._load_models()

    def _setup_capture_timers(self):
        # Windows publishes frames as they arrive. Polling also handles source
        # recovery, preview/overlay position and captures without notifications.
        self._process_timer = QTimer(self)
        self._process_timer.timeout.connect(self._capture_and_process)
        self._capture_frame_ready.connect(self._on_capture_frame_ready, Qt.ConnectionType.QueuedConnection)
        self._capture_idle_timer = QTimer(self)
        self._capture_idle_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._capture_idle_timer.setSingleShot(True)
        self._capture_idle_timer.timeout.connect(self._capture_and_process)
        self._last_ocr_capture = None
        self._last_ocr_capture_seen_at = 0.0

    def _create_worker(self) -> ProcessWorker:
        """Create the OCR/translation worker and wire its signals."""
        worker = ProcessWorker(self._config)
        worker.text_ready.connect(self._on_text_ready)
        worker.regions_ready.connect(self._on_regions_ready)
        worker.speech_ready.connect(self._on_speech_ready)
        worker.speech_stream_ready.connect(self._on_speech_ready)
        worker.speech_scene_reset.connect(self._on_speech_scene_reset)
        worker.character_detected.connect(self._on_character_detected)
        worker.dialogue_translated.connect(self._on_dialogue_translated)
        worker.dialogue_retranslated.connect(self._on_dialogue_retranslated)
        worker.ocr_results_ready.connect(self._on_ocr_results_ready)
        worker.models_ready.connect(self._on_models_ready)
        worker.models_failed.connect(self._on_models_failed)
        worker.ocr_status.connect(self._on_ocr_status)
        worker.translation_status.connect(self._on_translation_status)
        return worker

    def _setup_ui(self):
        """Set up the main UI."""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Build every widget first (the builders own the widget references), then arrange
        capture_group = self._build_capture_group()
        appearance_group = self._build_appearance_group()
        pair_card = self._build_language_pair_card()
        ocr_group = self._build_ocr_group()
        translation_group = self._build_translation_group()
        header = self._build_header()
        self._setup_hotkeys()
        self._target_language_combo.setEnabled(self._config.translation_backend == TranslationBackend.LLM)
        self._update_pair_hint()

        layout.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Sidebar navigation
        self._nav = QListWidget()
        self._nav.setProperty("role", "nav")
        self._nav.setFixedWidth(132)
        for name in ("Play", "Overlay", "Engines", "Updates", "History"):
            self._nav.addItem(tr(name))
        nav_box = QWidget()
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setContentsMargins(8, 8, 4, 8)
        nav_layout.addWidget(self._nav)
        interface_language = self._config.interface_language
        interface_combo = QComboBox()
        interface_combo.addItem("English", "en")
        interface_combo.addItem("Tiếng Việt", "vi")
        interface_combo.setCurrentIndex(interface_combo.findData(interface_language))
        interface_label = QLabel(tr("Interface language"))
        interface_label.setWordWrap(True)
        interface_label.setBuddy(interface_combo)
        interface_hint = QLabel(tr("Reopen Ludivoxa to apply the language."))
        interface_hint.setWordWrap(True)
        set_role(interface_hint, "hint")
        interface_hint.hide()

        def save_interface_language():
            self._config.interface_language = interface_combo.currentData()
            self._config.save()
            interface_hint.setVisible(self._config.interface_language != interface_language)

        interface_combo.currentIndexChanged.connect(save_interface_language)
        nav_layout.addWidget(interface_label)
        nav_layout.addWidget(interface_combo)
        nav_layout.addWidget(interface_hint)
        version = QLabel(f"v{__version__}")
        version.setProperty("role", "hint")
        nav_layout.addWidget(version)
        body.addWidget(nav_box)

        # Pages
        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_play_page(capture_group))
        self._pages.addWidget(self._scroll_page(self._page(appearance_group)))
        self._pages.addWidget(self._build_engines_page(pair_card, ocr_group, translation_group))
        self._updates_page = UpdatesPage(self._config, __version__, self)
        self._updates_page.update_available.connect(self._on_update_available)
        self._pages.addWidget(self._scroll_page(self._updates_page))
        self._history = HistoryPage(self)
        self._history.replay_requested.connect(self._replay_history)
        self._history.retranslation_requested.connect(self._retranslate_history)
        history_host = QWidget()
        history_layout = QVBoxLayout(history_host)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.addWidget(self._history)
        self._pages.addWidget(history_host)
        if sys.platform == "win32":
            from .history_overlay import HistoryOverlay

            self._history_overlay = HistoryOverlay(self._history, history_host)
            self._process_timer.timeout.connect(self._sync_history_overlay)
        self._nav.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._nav.setCurrentRow(0)
        body.addWidget(self._pages, 1)
        layout.addLayout(body, 1)

        # Status bar
        self.statusBar().showMessage(tr("Idle"))

    @staticmethod
    def _page(*widgets) -> QWidget:
        """A settings page: the given cards stacked, then empty space."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(8, 8, 12, 8)
        page_layout.setSpacing(8)
        for widget in widgets:
            page_layout.addWidget(widget)
        page_layout.addStretch()
        return page

    @staticmethod
    def _scroll_page(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _on_update_available(self, version):
        self._nav.item(3).setText(tr("Updates •"))
        self.statusBar().showMessage(tr("Ludivoxa {0} is available. Open Updates to download it.", version))

    def _build_play_page(self, capture_group: QGroupBox) -> QScrollArea:
        """Everything used during a session: window, preview, mode, hide, and the feed."""
        session = QGroupBox(tr("Session"))
        session_layout = QVBoxLayout(session)
        session_layout.addWidget(self._mode_row_widget)
        output_row = QHBoxLayout()
        output_label = QLabel(tr("Output:"))
        self._output_combo = QComboBox()
        for label, value in (
            ("Overlay only", OutputMode.OVERLAY),
            ("TTS only", OutputMode.TTS),
            ("Overlay + TTS", OutputMode.BOTH),
        ):
            self._output_combo.addItem(tr(label), value.value)
        self._sync_output_selection()
        self._output_combo.currentIndexChanged.connect(self._on_output_changed)
        output_label.setBuddy(self._output_combo)
        output_row.addWidget(output_label)
        output_row.addWidget(self._output_combo, 1)
        stop_speech = QPushButton(tr("Stop reading"))
        stop_speech.clicked.connect(lambda: self._speech.stop())
        output_row.addWidget(stop_speech)
        session_layout.addLayout(output_row)

        voice_toggle = QToolButton()
        voice_toggle.setText(tr("Voice settings"))
        voice_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        voice_toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        voice_toggle.setCheckable(True)
        voice_toggle.setArrowType(Qt.ArrowType.RightArrow)
        voice_options = QHBoxLayout()
        voice_options.addWidget(voice_toggle, alignment=Qt.AlignmentFlag.AlignTop)
        self._tts_clone_model_label = QLabel()
        self._tts_clone_model_label.setWordWrap(True)
        voice_options.addWidget(self._tts_clone_model_label, 1)
        voice_options.addStretch()
        session_layout.addLayout(voice_options)

        voice_settings = QWidget()
        voice_layout = QVBoxLayout(voice_settings)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        voice_settings.hide()
        session_layout.addWidget(voice_settings)

        self._tts_preset_panel = QWidget()
        preset_layout = QVBoxLayout(self._tts_preset_panel)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        voice_form = QFormLayout()
        self._tts_voice_combo = QComboBox()
        self._tts_voice_combo.setToolTip(
            tr("VAIS is recommended for Vietnamese pronunciation. Use Test voice to compare presets.")
        )
        self._tts_speaker_combo = QComboBox()
        for label, widget in (("Voice model:", self._tts_voice_combo), ("Speaker:", self._tts_speaker_combo)):
            voice_form.addRow(tr(label), widget)
        preset_layout.addLayout(voice_form)
        tts_row = QHBoxLayout()
        voice_label = QLabel(tr("Local voice model:"))
        self._tts_model_edit = QLineEdit(self._config.tts_model_path)
        self._tts_model_edit.setPlaceholderText(DEFAULT_VOICE_PATH)
        self._tts_model_edit.setToolTip(
            tr("Piper .onnx file, or a local VieNeu Nano folder. Models are never downloaded by the app.")
        )
        voice_label.setBuddy(self._tts_model_edit)
        self._tts_model_edit.editingFinished.connect(self._on_tts_model_changed)
        tts_row.addWidget(voice_label)
        tts_row.addWidget(self._tts_model_edit, 1)
        browse_voice = QPushButton(tr("Browse..."))
        browse_voice.clicked.connect(self._browse_tts_voice)
        tts_row.addWidget(browse_voice)
        preset_layout.addLayout(tts_row)
        voice_layout.addWidget(self._tts_preset_panel)

        self._clone_server_row = QWidget()
        server_layout = QHBoxLayout(self._clone_server_row)
        server_layout.setContentsMargins(0, 0, 0, 0)
        server_label = QLabel(tr("Local clone server:"))
        self._tts_clone_url_edit = QLineEdit(self._config.tts_clone_url)
        self._tts_clone_url_edit.setPlaceholderText("http://127.0.0.1:17867")
        self._tts_clone_url_edit.setToolTip(tr("Loopback URL of llama.cpp running Qwen3-TTS Base."))
        self._tts_clone_url_edit.editingFinished.connect(self._on_clone_url_changed)
        server_label.setBuddy(self._tts_clone_url_edit)
        server_layout.addWidget(server_label)
        server_layout.addWidget(self._tts_clone_url_edit, 1)
        voice_layout.addWidget(self._clone_server_row)
        self._clone_row = QWidget()
        clone_layout = QHBoxLayout(self._clone_row)
        clone_layout.setContentsMargins(0, 0, 0, 0)
        self._tts_reference_label = QLabel()
        self._tts_reference_edit = QLineEdit(self._config.tts_reference_audio)
        self._tts_reference_label.setBuddy(self._tts_reference_edit)
        self._tts_reference_edit.editingFinished.connect(self._on_reference_audio_changed)
        clone_layout.addWidget(self._tts_reference_label)
        clone_layout.addWidget(self._tts_reference_edit, 1)
        browse_reference = QPushButton(tr("Browse audio..."))
        browse_reference.clicked.connect(self._browse_reference_audio)
        clone_layout.addWidget(browse_reference)
        voice_layout.addWidget(self._clone_row)

        self._character_status = QLabel(tr("Character voices off"))
        self._character_status.setWordWrap(True)
        voice_layout.addWidget(self._character_status)
        rate_row = QHBoxLayout()
        rate_label = QLabel(tr("Speaking speed:"))
        self._tts_rate_spin = QDoubleSpinBox()
        self._tts_rate_spin.setRange(0.75, 2.0)
        self._tts_rate_spin.setSingleStep(0.05)
        self._tts_rate_spin.setSuffix("x")
        self._tts_rate_spin.setValue(self._config.tts_rate)
        self._tts_rate_spin.valueChanged.connect(self._on_speech_settings_changed)
        rate_label.setBuddy(self._tts_rate_spin)
        rate_row.addWidget(rate_label)
        rate_row.addWidget(self._tts_rate_spin)
        self._preview_voice_btn = QPushButton(tr("Test voice"))
        self._preview_voice_btn.clicked.connect(lambda: self._speech.preview(*self._config.speech_voice_settings))
        rate_row.addWidget(self._preview_voice_btn)
        rate_row.addStretch()
        voice_layout.addLayout(rate_row)
        volume_row = QHBoxLayout()
        volume_label = QLabel(tr("Speech volume:"))
        volume_slider = QSlider(Qt.Orientation.Horizontal)
        volume_slider.setAccessibleName(tr("Speech volume"))
        volume_slider.setRange(0, 100)
        volume_slider.setPageStep(10)
        volume_slider.setTracking(False)
        volume_slider.setValue(self._config.tts_volume)
        volume_slider.setToolTip(tr("Applies to the next line, including Test voice and History replay."))
        volume_label.setBuddy(volume_slider)
        volume_value = QLabel(f"{self._config.tts_volume}%")
        volume_slider.sliderMoved.connect(lambda percent: volume_value.setText(f"{percent}%"))
        volume_slider.valueChanged.connect(lambda percent: volume_value.setText(f"{percent}%"))
        volume_slider.valueChanged.connect(self._on_speech_volume_changed)
        volume_row.addWidget(volume_label)
        volume_row.addWidget(volume_slider, 1)
        volume_row.addWidget(volume_value)
        voice_layout.addLayout(volume_row)
        self._refresh_voice_models()
        self._tts_voice_combo.currentIndexChanged.connect(self._on_voice_model_changed)
        self._tts_speaker_combo.currentIndexChanged.connect(self._on_speech_settings_changed)
        self._tts_status = QLabel()
        self._tts_status.setWordWrap(True)
        self._tts_status.hide()
        self._speech.status_changed.connect(self._show_speech_status)
        session_layout.addWidget(self._tts_status)

        page = self._page(capture_group, session)
        page.layout().setStretch(0, 1)  # the capture card absorbs extra height, not a spacer
        if is_macos():
            permissions = QGridLayout()
            self._setup_permissions_ui(permissions)
            page.layout().insertLayout(page.layout().count() - 1, permissions)
        scroll = self._scroll_page(page)

        def toggle_voice_settings(opened):
            voice_toggle.setArrowType(Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow)
            voice_settings.setVisible(opened)
            if opened:
                QTimer.singleShot(0, scroll, lambda: scroll.ensureWidgetVisible(voice_settings, 0, 8))

        voice_toggle.toggled.connect(toggle_voice_settings)
        return scroll

    def _build_header(self) -> QWidget:
        """Keep capture tools above the scrollable pages."""
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 12, 4)
        title = QLabel("Ludivoxa")
        title.setProperty("role", "app-title")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self._ocr_status_label = QLabel(tr("Loading..."))
        self._translation_status_label = QLabel(tr("Loading..."))
        self._ocr_status_pill = self._pill("OCR", self._ocr_engine_name(), self._ocr_status_label)
        self._translation_status_pill = self._pill(
            tr("Translation"), self._translation_engine_name(), self._translation_status_label
        )
        self.statusBar().addPermanentWidget(self._ocr_status_pill)
        self.statusBar().addPermanentWidget(self._translation_status_pill)

        self._fix_models_btn = QPushButton(tr("Fix Models"))
        self._fix_models_btn.clicked.connect(self._on_fix_models)
        self._fix_models_btn.setVisible(False)
        header_layout.addWidget(self._fix_models_btn)
        header_layout.addWidget(self._ocr_config_btn)
        tools_button = QPushButton(tr("Tools"))
        tools_button.setMinimumHeight(44)
        menu = QMenu(tools_button)
        menu.addAction(tr("Game profile…"), self._open_game_profile)
        menu.addAction(tr("Character voices…"), self._open_character_voices)
        menu.addAction(tr("Diagnostics"), self._open_diagnostics)
        tools_button.setMenu(menu)
        header_layout.addWidget(tools_button)
        return header

    @staticmethod
    def _pill(kind: str, engine_name: str, status_label: QLabel) -> QFrame:
        pill = QFrame()
        pill.setProperty("role", "pill")
        pill.setToolTip(engine_name)
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(6, 2, 6, 2)
        pill_layout.setSpacing(6)
        kind_label = QLabel(kind)
        kind_label.setProperty("role", "hint")
        pill_layout.addWidget(kind_label)
        pill_layout.addWidget(status_label)
        return pill

    def _build_engines_page(self, *cards) -> QWidget:
        """Language pair, OCR engine and translation engine, saved together by one Apply."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(4)
        layout.addWidget(self._scroll_page(self._page(*cards)), 1)
        actions = QHBoxLayout()
        actions.setContentsMargins(8, 0, 12, 0)
        actions.addStretch()
        self._apply_engines_btn = QPushButton(tr("Apply"))
        self._apply_engines_btn.setProperty("primary", True)
        self._apply_engines_btn.setToolTip(tr("Save the language pair and both engines, and reload what changed"))
        self._apply_engines_btn.clicked.connect(self._apply_engines)
        actions.addWidget(self._apply_engines_btn)
        layout.addLayout(actions)
        return page

    def _build_language_pair_card(self) -> QGroupBox:
        """Pick the language pair first; the engines below then explain what they can do."""
        card = QGroupBox(tr("Languages"))
        grid = QGridLayout(card)
        source_label = QLabel(tr("Game text:"))
        grid.addWidget(source_label, 0, 0)
        self._source_language_combo = QComboBox()
        for language in dict.fromkeys((*OCR_SOURCE_LANGUAGES, self._config.source_language)):
            self._source_language_combo.addItem(tr(language), language)
        self._source_language_combo.setCurrentIndex(
            max(0, self._source_language_combo.findData(self._config.source_language))
        )
        self._source_language_combo.setToolTip(
            tr("Auto is intended for English/Japanese. Select Chinese or Korean to load its offline recognition model.")
        )
        self._source_language_combo.currentIndexChanged.connect(lambda _i: self._update_pair_hint())
        grid.addWidget(self._source_language_combo, 0, 1)
        source_label.setBuddy(self._source_language_combo)
        target_label = QLabel(tr("Translate to:"))
        grid.addWidget(target_label, 1, 0)
        self._target_language_combo = QComboBox()
        self._target_language_combo.setEditable(True)
        self._target_language_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for language in TARGET_LANGUAGE_PRESETS:
            self._target_language_combo.addItem(tr(language), language)
        target = target_language_or_default(self._config.llm.target_language)
        target_index = self._target_language_combo.findData(target)
        self._target_language_combo.setCurrentIndex(target_index)
        if target_index < 0:
            self._target_language_combo.setEditText(target)
        self._target_language_combo.setToolTip(
            tr("Pick a target language or type a language supported by your LLM model.")
        )
        self._target_language_combo.currentTextChanged.connect(self._update_pair_hint)
        target_label.setBuddy(self._target_language_combo)
        grid.addWidget(self._target_language_combo, 1, 1)
        self._pair_hint = QLabel("")
        self._pair_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._pair_hint.setWordWrap(True)
        grid.addWidget(self._pair_hint, 2, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        return card

    def _update_pair_hint(self):
        """Explain, in one line, whether the selected engines can serve the selected pair."""
        if not hasattr(self, "_pair_hint") or not hasattr(self, "_engine_combo"):
            return
        source = self._selected_source_language()
        chosen = self._source_language_combo.currentData()
        problems = []
        if self._selected_ocr_backend() == OCRBackend.MEIKI and chosen != DEFAULT_SOURCE_LANGUAGE:
            problems.append(
                tr("MeikiOCR reads Japanese only: pick the owocr engine to read {0}.", tr(chosen or source))
            )
        if self._selected_backend() == TranslationBackend.SUGOI and source != SUGOI_SOURCE_LANGUAGE:
            problems.append(tr("Sugoi V4 translates Japanese to English only: pick the LLM endpoint engine."))
        if problems:
            set_role(self._pair_hint, "hint-warn")
            self._pair_hint.setText(" ".join(problems))
        else:
            set_role(self._pair_hint, "hint")
            target = self._selected_target_language()
            hint = ""
            if self._selected_backend() == TranslationBackend.SUGOI:
                target = "English"
                hint = " · Sugoi V4"
            elif not is_vietnamese_target(target):
                if clone_language(target):
                    ready = local_clone_url(self._config.tts_clone_url) and self._config.tts_reference_audio
                    hint = (
                        tr(" · Local voice cloning")
                        if ready
                        else tr(" · Overlay only until a clone server and recording are configured in Session.")
                    )
                else:
                    hint = tr(" · Overlay only. The bundled voices read Vietnamese.")
            self._pair_hint.setText(f"{tr(source)} \u2192 {tr(target)}{hint}")

    def _build_capture_group(self) -> QGroupBox:
        """Source selection, capture controls and the preview."""
        # ==================== CAPTURE ====================
        # Source selection + Preview in one logical group
        capture_group = QGroupBox(tr("Capture"))
        capture_layout = QVBoxLayout(capture_group)

        # Source row: windows (or the Wayland portal picker) and video devices in one list
        source_row = QHBoxLayout()

        self._window_combo = QComboBox()
        self._window_combo.setAccessibleName(tr("Capture source"))
        self._window_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._window_combo.setMinimumContentsLength(12)
        self._window_combo.currentTextChanged.connect(self._window_combo.setToolTip)
        source_row.addWidget(self._window_combo, 1)

        self._start_btn = QPushButton(tr("Start Capture"))
        self._start_btn.setProperty("primary", True)
        self._start_btn.setEnabled(False)  # Disabled until models are loaded
        self._start_btn.clicked.connect(self._toggle_capture)
        source_row.addWidget(self._start_btn)

        refresh_btn = QPushButton(tr("Refresh"))
        refresh_btn.clicked.connect(self._refresh_sources)
        source_row.addWidget(refresh_btn)

        self._ocr_config_btn = QPushButton(tr("Configure OCR"))
        self._ocr_config_btn.setMinimumHeight(44)
        self._ocr_config_btn.setEnabled(False)  # Disabled until capturing
        self._ocr_config_btn.clicked.connect(self._open_ocr_config)

        capture_layout.addLayout(source_row)

        # Preview (centered, aspect ratio preserved)
        self._preview_label = QLabel()
        self._preview_label.setMinimumSize(240, 120)
        self._preview_label.setFrameStyle(QFrame.Shape.NoFrame)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setText(tr("No preview"))
        self._preview_label.setProperty("role", "preview")
        # Ignored horizontally: a QLabel's size hint is its pixmap, which would make the window
        # grow to fit the scaled preview and then scale the preview to the wider window again.
        self._preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        capture_layout.addWidget(self._preview_label, 1)

        return capture_group

    def _build_appearance_group(self) -> QGroupBox:
        """Overlay mode, hotkeys and the visual settings."""
        # ==================== APPEARANCE ====================
        # Overlay mode + all visual settings
        appearance_group = QGroupBox(tr("Overlay style"))
        appearance_layout = QVBoxLayout(appearance_group)

        # The session page owns these live controls.
        self._mode_row_widget = QWidget()
        mode_row = QHBoxLayout(self._mode_row_widget)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.addWidget(QLabel(tr("Mode:")))

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        self._banner_btn = QPushButton(tr("Banner"))
        self._banner_btn.setCheckable(True)
        self._banner_btn.setChecked(self._mode == OverlayMode.BANNER)
        self._mode_group.addButton(self._banner_btn, 0)

        self._inplace_btn = QPushButton(tr("Inplace"))
        self._inplace_btn.setCheckable(True)
        self._inplace_btn.setChecked(self._mode == OverlayMode.INPLACE)
        self._mode_group.addButton(self._inplace_btn, 1)

        # Styled as a segmented control by the theme (see theme.py)
        self._banner_btn.setProperty("segment", "left")
        self._inplace_btn.setProperty("segment", "right")

        self._mode_group.idClicked.connect(self._on_mode_changed)

        # Container to keep buttons connected (no spacing)
        mode_container = QWidget()
        mode_btn_layout = QHBoxLayout(mode_container)
        mode_btn_layout.setContentsMargins(0, 0, 0, 0)
        mode_btn_layout.setSpacing(0)
        mode_btn_layout.addWidget(self._banner_btn)
        mode_btn_layout.addWidget(self._inplace_btn)
        mode_row.addWidget(mode_container)

        # Mode switch hotkey picker
        mode_switch_str = self._config.hotkeys.get("switch_mode", "m")
        self._mode_switch_str = mode_switch_str
        self._mode_hotkey = QKeySequenceEdit(self._hotkey_str_to_qkeysequence(mode_switch_str))
        self._mode_hotkey.setFixedWidth(80)
        self._mode_hotkey.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._mode_hotkey.keySequenceChanged.connect(self._on_mode_hotkey_changed)

        # Wayland limitation warning (only shown on Wayland)
        if self._is_wayland_session:
            wayland_warning = QLabel(tr("<a href='#' style='color: #ffa500;'>⚠️ Wayland limitations</a>"))
            wayland_warning.setToolTip(tr("Click for details about inplace mode on Wayland"))
            wayland_warning.linkActivated.connect(self._show_wayland_warning)
            appearance_layout.addWidget(wayland_warning)

        mode_row.addStretch()

        # Hide/Show button with hotkey
        self._pause_btn = QPushButton(tr("Hide"))
        self._pause_btn.clicked.connect(self._toggle_pause)
        self._pause_btn.setEnabled(False)
        mode_row.addWidget(self._pause_btn)

        hotkey_str = self._config.hotkeys.get("toggle_overlay", "f8")
        self._hotkey_str = hotkey_str
        self._pause_hotkey = QKeySequenceEdit(self._hotkey_str_to_qkeysequence(hotkey_str))
        self._pause_hotkey.setFixedWidth(80)
        self._pause_hotkey.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._pause_hotkey.keySequenceChanged.connect(self._on_pause_hotkey_changed)
        hotkeys = QFormLayout()
        hotkeys.addRow(tr("Switch overlay mode:"), self._mode_hotkey)
        hotkeys.addRow(tr("Show or hide overlay:"), self._pause_hotkey)
        appearance_layout.addLayout(hotkeys)
        hide_stale = QCheckBox(tr("Clear translation when dialogue changes"))
        hide_stale.setChecked(self._config.hide_stale_overlay)
        hide_stale.setToolTip(tr("Clear the overlay while the new dialogue is being translated."))
        hide_stale.toggled.connect(self._set_hide_stale_overlay)
        appearance_layout.addWidget(hide_stale)
        if sys.platform == "win32":
            history_overlay = QCheckBox(tr("Show History button over the game"))
            history_overlay.setChecked(self._config.history_overlay_enabled)
            history_overlay.toggled.connect(self._set_history_overlay_enabled)
            appearance_layout.addWidget(history_overlay)

        # Visual settings grid
        visual_grid = QGridLayout()

        # Font family
        visual_grid.addWidget(QLabel(tr("Font:")), 0, 0)
        self._font_family_btn = QPushButton(self._config.font_family or tr("System Default"))
        self._font_family_btn.clicked.connect(self._pick_font_family)
        visual_grid.addWidget(self._font_family_btn, 0, 1)

        # Font size
        visual_grid.addWidget(QLabel(tr("Size:")), 0, 2)
        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._font_slider.setValue(self._config.font_size)
        self._font_slider.valueChanged.connect(self._on_font_size_changed)
        visual_grid.addWidget(self._font_slider, 0, 3)
        self._font_label = QLabel(f"{self._config.font_size}pt")
        visual_grid.addWidget(self._font_label, 0, 4)

        # Font color
        visual_grid.addWidget(QLabel(tr("Text Color:")), 1, 0)
        self._font_color_btn = QPushButton()
        self._font_color_btn.setStyleSheet(f"background-color: {self._config.font_color};")
        self._font_color_btn.clicked.connect(self._pick_font_color)
        visual_grid.addWidget(self._font_color_btn, 1, 1)

        # Background color
        visual_grid.addWidget(QLabel(tr("Background:")), 1, 2)
        self._bg_color_btn = QPushButton()
        self._bg_color_btn.setStyleSheet(f"background-color: {self._config.background_color};")
        self._bg_color_btn.clicked.connect(self._pick_bg_color)
        visual_grid.addWidget(self._bg_color_btn, 1, 3)

        # Opacity
        visual_grid.addWidget(QLabel(tr("Opacity:")), 2, 0)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(int(self._config.background_opacity * 100))
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        visual_grid.addWidget(self._opacity_slider, 2, 1, 1, 3)
        self._opacity_label = QLabel(f"{int(self._config.background_opacity * 100)}%")
        visual_grid.addWidget(self._opacity_label, 2, 4)

        appearance_layout.addLayout(visual_grid)

        # Live sample: exactly what a subtitle looks like with these settings
        sample_row = QHBoxLayout()
        sample_caption = QLabel(tr("Sample:"))
        sample_row.addWidget(sample_caption)
        sample_box = QWidget()
        sample_box.setProperty("role", "sample-stage")
        sample_box.setMinimumHeight(96)
        sample_layout = QVBoxLayout(sample_box)
        sample_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._style_sample = QLabel(tr("Brave hero, you must defeat the demon king."))
        self._style_sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._style_sample.setWordWrap(True)
        # The sample must never dictate the window width (a big font would)
        sample_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sample_box.setMinimumWidth(320)
        sample_layout.addWidget(self._style_sample, 0, Qt.AlignmentFlag.AlignCenter)
        sample_row.addWidget(sample_box, 1)
        appearance_layout.addLayout(sample_row)
        self._update_style_sample()

        return appearance_group

    def _update_style_sample(self):
        """Render the sample subtitle with the current font, colours and opacity."""
        font = QFont(self._config.font_family) if self._config.font_family else QFont()
        font.setPointSize(self._config.font_size)
        font.setBold(True)
        self._style_sample.setFont(font)
        bg = self._config.background_color.lstrip("#")
        r, g, b = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
        family = f"font-family: '{self._config.font_family}';" if self._config.font_family else ""
        self._style_sample.setStyleSheet(
            f"color: {self._config.font_color}; background-color: rgba({r}, {g}, {b}, "
            f"{self._config.background_opacity:.2f}); padding: 6px 12px; border-radius: 6px; "
            f"font-size: {self._config.font_size}pt; font-weight: 700; {family}"
        )

    def _setup_hotkeys(self):
        """Global hotkey listener, from the strings read by the appearance builder."""
        # Global hotkey listener - load from config
        self._current_hotkey = self._qt_key_to_key(self._hotkey_str)
        self._keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
        self._keyboard_listener.start()
        self.hotkey_pressed.connect(self._toggle_pause)

        # Mode switch hotkey
        self._mode_switch_hotkey = self._qt_key_to_key(self._mode_switch_str)
        self.mode_switch_pressed.connect(self._toggle_mode)

    # ==================== OCR SETTINGS ====================

    def _build_ocr_group(self) -> QGroupBox:
        """Build the OCR group: engine selector plus the owocr server panel."""
        group = QGroupBox("OCR")
        group_layout = QVBoxLayout(group)

        # Engine row
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel(tr("Engine:")))
        self._ocr_engine_combo = QComboBox()
        self._ocr_engine_combo.addItem(tr("MeikiOCR (built-in, offline)"), OCRBackend.MEIKI.value)
        self._ocr_engine_combo.addItem(tr("owocr (external process, websocket)"), OCRBackend.OWOCR.value)
        self._ocr_engine_combo.addItem(tr("PaddleOCR (local models, offline)"), OCRBackend.PADDLE.value)
        self._ocr_engine_combo.setCurrentIndex(self._ocr_engine_combo.findData(self._config.ocr_backend.value))
        self._ocr_engine_combo.currentIndexChanged.connect(self._on_ocr_engine_changed)
        engine_row.addWidget(self._ocr_engine_combo, 1)
        group_layout.addLayout(engine_row)

        # The source language lives in the language pair card (built first)
        self._sync_source_language_combo()

        self._paddle_panel = QWidget()
        paddle_grid = QGridLayout(self._paddle_panel)
        self._paddle_detection_edit = QLineEdit(self._config.paddle.detection_model_dir)
        self._paddle_recognition_edit = QLineEdit(self._config.paddle.recognition_model_dir)
        for row, (label, edit) in enumerate(
            (
                ("Detection model directory:", self._paddle_detection_edit),
                ("Recognition model directory:", self._paddle_recognition_edit),
            )
        ):
            widget = QLabel(tr(label))
            widget.setBuddy(edit)
            paddle_grid.addWidget(widget, row, 0)
            paddle_grid.addWidget(edit, row, 1)
        hint = QLabel(
            tr(
                "Chinese and Korean select a matching local recognition model next to the detector. Auto detects text language after OCR; it does not switch recognition models. Models are never downloaded. CPU, two threads by default."
            )
        )
        hint.setWordWrap(True)
        paddle_grid.addWidget(hint, 2, 0, 1, 2)
        group_layout.addWidget(self._paddle_panel)
        self._paddle_panel.setVisible(self._config.ocr_backend == OCRBackend.PADDLE)

        # owocr panel (only visible for the owocr engine)
        self._owocr_panel = QWidget()
        owocr_grid = QGridLayout(self._owocr_panel)
        owocr_grid.setContentsMargins(0, 0, 0, 0)

        owocr_grid.addWidget(QLabel(tr("Server URL:")), 0, 0)
        self._owocr_url_edit = QLineEdit(self._config.owocr.url)
        self._owocr_url_edit.setToolTip(
            tr(
                "Start owocr as: {0}\nDefault address: {1}. Prefer 127.0.0.1 over localhost: on Windows, localhost adds ~2 s per request.",
                OWOCR_SERVER_COMMAND,
                OWOCR_DEFAULT_URL,
            )
        )
        owocr_grid.addWidget(self._owocr_url_edit, 0, 1, 1, 2)

        self._owocr_test_btn = QPushButton(tr("Test"))
        self._owocr_test_btn.setToolTip(tr("Send a blank image to the server and measure the round trip"))
        self._owocr_test_btn.clicked.connect(self._test_owocr_endpoint)
        owocr_grid.addWidget(self._owocr_test_btn, 1, 0)
        self._owocr_result_label = QLabel("")
        self._owocr_result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Reserve one text line even while empty so the row never collapses
        self._owocr_result_label.setMinimumHeight(self._owocr_test_btn.sizeHint().height())
        owocr_grid.addWidget(self._owocr_result_label, 1, 1, 1, 2)

        note = QLabel(
            tr("owocr returns no confidence scores; the OCR confidence slider has no effect with this engine.")
        )
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        owocr_grid.addWidget(note, 2, 0, 1, 3)

        owocr_grid.setColumnStretch(1, 1)
        group_layout.addWidget(self._owocr_panel)
        self._owocr_panel.setVisible(self._config.ocr_backend == OCRBackend.OWOCR)

        return group

    def _ocr_engine_name(self) -> str:
        """Engine name shown in the Status group."""
        return {OCRBackend.OWOCR: "owocr", OCRBackend.PADDLE: "PaddleOCR"}.get(self._config.ocr_backend, "MeikiOCR")

    def _selected_ocr_backend(self) -> OCRBackend:
        return OCRBackend(self._ocr_engine_combo.currentData())

    def _owocr_settings_from_ui(self) -> OwocrSettings:
        """Read the owocr panel into an OwocrSettings value (without touching the config)."""
        return OwocrSettings(
            url=normalize_owocr_url(self._owocr_url_edit.text()),
            # Config-only field (no widget), carried over so Apply never drops it
            timeout=self._config.owocr.timeout,
        )

    def _on_ocr_engine_changed(self, _index: int):
        """Show the owocr panel only when the owocr engine is selected."""
        self._owocr_panel.setVisible(self._selected_ocr_backend() == OCRBackend.OWOCR)
        if hasattr(self, "_paddle_panel"):
            self._paddle_panel.setVisible(self._selected_ocr_backend() == OCRBackend.PADDLE)
        self._sync_source_language_combo()
        self._update_pair_hint()

    def _sync_source_language_combo(self):
        """Pin the source language to Japanese while MeikiOCR is selected; free it for owocr."""
        pluggable = self._selected_ocr_backend() != OCRBackend.MEIKI
        if not pluggable:
            self._source_language_combo.setCurrentIndex(self._source_language_combo.findData(DEFAULT_SOURCE_LANGUAGE))
        self._source_language_combo.setEnabled(pluggable)

    def _selected_source_language(self) -> str:
        """The source language the OCR group would apply (Japanese whenever MeikiOCR is selected)."""
        if self._selected_ocr_backend() == OCRBackend.MEIKI:
            return DEFAULT_SOURCE_LANGUAGE
        return self._source_language_combo.currentData() or DEFAULT_SOURCE_LANGUAGE

    def _selected_target_language(self) -> str:
        combo = self._target_language_combo
        text = combo.currentText()
        return target_language_or_default(combo.itemData(combo.findText(text)) or text)

    def _set_owocr_result(self, text: str, error: bool = False):
        self._set_result_label(self._owocr_result_label, text, error)

    def _test_owocr_endpoint(self):
        """Run the probe round trip with the current panel settings on a background thread."""
        settings = self._owocr_settings_from_ui()
        self._owocr_test_request = settings
        self._owocr_test_btn.setEnabled(False)
        self._set_owocr_result(tr("Testing..."))

        def run():
            try:
                self._owocr_test_result.emit((settings, check_owocr_endpoint(settings)))
            except Exception as e:
                self._owocr_test_result.emit((settings, str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _on_owocr_test_done(self, payload):
        settings, result = payload
        if settings is not self._owocr_test_request:
            return  # superseded by a newer Test
        self._owocr_test_request = None
        self._owocr_test_btn.setEnabled(True)
        if settings != self._owocr_settings_from_ui():
            self._set_owocr_result(tr("Settings changed during the test; click Test again."), error=True)
            return
        if isinstance(result, str):
            self._set_owocr_result(result, error=True)
            return
        warning = owocr_remote_warning(settings.url)
        self._set_owocr_result(
            tr("OK in {0} ms. {1}", result, warning) if warning else tr("OK in {0} ms", result), error=bool(warning)
        )

    def _apply_engines(self):
        """Save the Engines page as one unit: the language pair, the OCR engine and the translation engine.

        Validating the pending pair against the pending engines here (instead of per group) means the
        visible source-to-target pair is what gets saved, never half of it.
        """
        config = self._config
        ocr_backend = self._selected_ocr_backend()
        translation_backend = self._selected_backend()
        source_language = self._selected_source_language()
        if translation_backend == TranslationBackend.SUGOI and source_language != SUGOI_SOURCE_LANGUAGE:
            self._set_pair_error(tr(SUGOI_ONLY_JAPANESE))
            return
        if self._capturing:
            error = self._profile_language_error(
                self._current_window_title,
                default_language=source_language,
                backend=translation_backend,
                ocr_backend=ocr_backend,
            )
            if error:
                self._set_pair_error(error)
                return
        llm = self._llm_settings_from_ui()  # carries the target language from the pair card
        if translation_backend == TranslationBackend.LLM and not llm.model:
            self._set_llm_result("Pick a model before applying.", error=True)
            self._set_pair_error(tr("Pick an LLM model before applying."))
            return
        owocr = self._owocr_settings_from_ui() if ocr_backend == OCRBackend.OWOCR else config.owocr
        paddle = config.paddle
        if ocr_backend == OCRBackend.PADDLE:
            paddle = PaddleSettings(
                detection_model_dir=self._paddle_detection_edit.text().strip(),
                recognition_model_dir=self._paddle_recognition_edit.text().strip(),
                device=config.paddle.device,
                cpu_threads=config.paddle.cpu_threads,
                max_side=config.paddle.max_side,
            )

        ocr_changed = (
            ocr_backend != config.ocr_backend
            or owocr != config.owocr
            or paddle != config.paddle
            or (
                ocr_backend == OCRBackend.PADDLE
                and PADDLE_LANGUAGE_MODELS.get(source_language) != PADDLE_LANGUAGE_MODELS.get(config.source_language)
            )
        )
        target_changed = translation_backend != config.translation_backend or (
            translation_backend == TranslationBackend.LLM and llm.target_language != config.llm.target_language
        )
        translation_changed = (
            target_changed
            or llm != config.llm
            or source_language != config.source_language  # the LLM prompt names the source language
        )
        config.ocr_backend = ocr_backend
        config.owocr = owocr
        config.paddle = paddle
        config.translation_backend = translation_backend
        config.llm = llm
        config.source_language = source_language
        config.save()

        if ocr_backend == OCRBackend.OWOCR:
            self._owocr_url_edit.setText(owocr.url)
            warning = owocr_remote_warning(owocr.url)
            self._set_owocr_result(warning or "", error=bool(warning))
        self._ocr_status_pill.setToolTip(self._ocr_engine_name())
        self._translation_status_pill.setToolTip(self._translation_engine_name())
        self._update_pair_hint()
        if not (ocr_changed or translation_changed):
            self.statusBar().showMessage(tr("Engines unchanged"))
            return
        self._fix_models_btn.setVisible(False)
        if ocr_changed:
            self._ocr_status_label.setToolTip("")
            self._process_worker.reload_ocr()
        if translation_changed:
            self._translation_status_label.setToolTip("")
            self._process_worker.reload_translation()
        if target_changed:
            self._sync_translation_output()
        self.statusBar().showMessage(tr("Reloading engines..."))

    def _set_pair_error(self, message: str):
        """Show why the Engines page could not be applied, next to the language pair."""
        set_role(self._pair_hint, "hint-warn")
        self._pair_hint.setText(message)
        self.statusBar().showMessage(message)

    # ==================== TRANSLATION SETTINGS ====================

    def _build_translation_group(self) -> QGroupBox:
        """Build the Translation group: engine selector plus the LLM endpoint panel."""
        group = QGroupBox(tr("Translation"))
        group_layout = QVBoxLayout(group)

        # Engine row
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel(tr("Engine:")))
        self._engine_combo = QComboBox()
        self._engine_combo.addItem(tr("Sugoi V4 (built-in, offline)"), TranslationBackend.SUGOI.value)
        self._engine_combo.addItem(tr("LLM endpoint (Ollama / OpenAI-compatible)"), TranslationBackend.LLM.value)
        self._engine_combo.setCurrentIndex(self._engine_combo.findData(self._config.translation_backend.value))
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        engine_row.addWidget(self._engine_combo, 1)
        group_layout.addLayout(engine_row)

        # LLM panel (only visible for the LLM engine)
        self._llm_panel = QWidget()
        llm_grid = QGridLayout(self._llm_panel)
        llm_grid.setContentsMargins(0, 0, 0, 0)
        settings = self._config.llm

        llm_grid.addWidget(QLabel(tr("Provider:")), 0, 0)
        self._llm_provider_combo = QComboBox()
        for provider in PROVIDERS:
            self._llm_provider_combo.addItem(PROVIDER_LABELS[provider], provider)
        self._llm_provider_combo.setCurrentIndex(max(0, self._llm_provider_combo.findData(settings.provider)))
        self._llm_provider_combo.currentIndexChanged.connect(self._on_llm_provider_changed)
        llm_grid.addWidget(self._llm_provider_combo, 0, 1, 1, 2)

        llm_grid.addWidget(QLabel(tr("Server URL:")), 1, 0)
        self._llm_url_edit = QLineEdit(settings.base_url)
        self._llm_url_edit.setToolTip(
            tr(
                "Ollama: http://127.0.0.1:11434 - LM Studio: http://127.0.0.1:1234/v1 - OpenAI: https://api.openai.com/v1\nPrefer 127.0.0.1 over localhost: on Windows, localhost adds ~2 s per request."
            )
        )
        llm_grid.addWidget(self._llm_url_edit, 1, 1, 1, 2)

        llm_grid.addWidget(QLabel(tr("Model:")), 2, 0)
        self._llm_model_combo = QComboBox()
        self._llm_model_combo.setEditable(True)
        self._llm_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._llm_model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._llm_model_combo.setMinimumContentsLength(12)
        if settings.model:
            self._llm_model_combo.addItem(settings.model)
        self._llm_model_combo.setCurrentText(settings.model)
        self._llm_model_combo.setToolTip(tr("Type a model name or click Refresh to list the models on the server"))
        llm_grid.addWidget(self._llm_model_combo, 2, 1)
        self._llm_refresh_btn = QPushButton(tr("Refresh"))
        self._llm_refresh_btn.setToolTip(tr("Ask the server which models it has"))
        self._llm_refresh_btn.clicked.connect(self._refresh_llm_models)
        llm_grid.addWidget(self._llm_refresh_btn, 2, 2)

        # Target language lives in the language pair card

        llm_grid.addWidget(QLabel(tr("API key:")), 3, 0)
        self._llm_api_key_edit = QLineEdit(settings.api_key)
        self._llm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_api_key_edit.setPlaceholderText(tr("Optional - only for hosted services (stored in config.yml)"))
        llm_grid.addWidget(self._llm_api_key_edit, 3, 1, 1, 2)

        llm_grid.addWidget(QLabel(tr("Prompt:")), 4, 0, Qt.AlignmentFlag.AlignTop)
        self._llm_prompt_edit = QPlainTextEdit(settings.system_prompt or DEFAULT_SYSTEM_PROMPT)
        self._llm_prompt_edit.setFixedHeight(64)
        self._llm_prompt_edit.setToolTip(
            tr("System prompt sent to the model. {source_language} and {target_language} are replaced.")
        )
        llm_grid.addWidget(self._llm_prompt_edit, 4, 1)
        reset_prompt_btn = QPushButton(tr("Reset"))
        reset_prompt_btn.setToolTip(tr("Restore the default prompt"))
        reset_prompt_btn.clicked.connect(lambda: self._llm_prompt_edit.setPlainText(DEFAULT_SYSTEM_PROMPT))
        llm_grid.addWidget(reset_prompt_btn, 4, 2, Qt.AlignmentFlag.AlignTop)

        self._llm_test_btn = QPushButton(tr("Test"))
        self._llm_test_btn.setToolTip(tr("Translate a sample line with these settings and measure the round trip"))
        self._llm_test_btn.clicked.connect(self._test_llm_endpoint)
        llm_grid.addWidget(self._llm_test_btn, 5, 2)
        self._llm_result_label = QLabel("")
        self._llm_result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._llm_result_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Reserve one text line even while empty so the row never collapses
        self._llm_result_label.setMinimumHeight(self._llm_test_btn.sizeHint().height())
        llm_grid.addWidget(self._llm_result_label, 5, 0, 1, 2)

        llm_grid.setColumnStretch(1, 1)
        group_layout.addWidget(self._llm_panel)
        self._llm_panel.setVisible(self._config.translation_backend == TranslationBackend.LLM)

        return group

    def _translation_engine_name(self) -> str:
        """Engine name shown in the Status group."""
        if self._config.translation_backend == TranslationBackend.LLM:
            llm = self._config.llm
            return f"{provider_label(llm.provider)} \u00b7 {llm.model or 'no model'}"
        return "Sugoi V4"

    def _selected_backend(self) -> TranslationBackend:
        return TranslationBackend(self._engine_combo.currentData())

    def _selected_provider(self) -> str:
        return self._llm_provider_combo.currentData()

    def _llm_settings_from_ui(self) -> LLMSettings:
        """Read the LLM panel into an LLMSettings value (without touching the config)."""
        prompt = self._llm_prompt_edit.toPlainText().strip()
        return LLMSettings(
            provider=self._selected_provider(),
            base_url=self._llm_url_edit.text().strip() or DEFAULT_BASE_URLS[self._selected_provider()],
            model=self._llm_model_combo.currentText().strip(),
            api_key=self._llm_api_key_edit.text().strip(),
            target_language=self._selected_target_language(),
            system_prompt=None if not prompt or prompt == DEFAULT_SYSTEM_PROMPT else prompt,
            # Config-only fields (no widgets) are carried over so Apply never drops them
            context_lines=self._config.llm.context_lines,
            timeout=self._config.llm.timeout,
            request_options=dict(self._config.llm.request_options),
        )

    def _on_engine_changed(self, _index: int):
        """Show the LLM panel only when the LLM engine is selected."""
        is_llm = self._selected_backend() == TranslationBackend.LLM
        self._llm_panel.setVisible(is_llm)
        self._target_language_combo.setEnabled(is_llm)  # Sugoi V4 always outputs English
        self._update_pair_hint()

    def _on_llm_provider_changed(self, _index: int):
        """Swap the default URL when the provider changes, unless the user typed their own."""
        provider = self._selected_provider()
        current = self._llm_url_edit.text().strip()
        if not current or current in DEFAULT_BASE_URLS.values():
            self._llm_url_edit.setText(DEFAULT_BASE_URLS[provider])
        self._llm_result_label.setText("")

    def _set_llm_result(self, text: str, error: bool = False):
        self._set_result_label(self._llm_result_label, text, error)

    @staticmethod
    def _set_result_label(label: QLabel, text: str, error: bool = False):
        """Show a one-line result under a settings panel; the full text is in the tooltip."""
        label.setStyleSheet(f"color: {ERROR};" if error else f"color: {OK};")
        label.setToolTip(text)
        # Keep transient messages on a single line. The full text stays in the tooltip.
        width = max(label.width() - 4, 200)
        label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))

    @staticmethod
    def _endpoint_identity(settings: LLMSettings) -> tuple[str, str, str]:
        """The parts of the settings that decide which server a model list came from."""
        return (settings.provider, normalize_base_url(settings.provider, settings.base_url), settings.api_key)

    def _refresh_llm_models(self):
        """List the server's models on a background thread.

        The request carries the settings it was made with; a result is dropped if a
        newer request superseded it or the endpoint fields changed meanwhile, so a slow
        old server can never overwrite the model list of the one now selected.
        """
        settings = self._llm_settings_from_ui()
        self._llm_refresh_request = settings
        self._llm_refresh_btn.setEnabled(False)
        self._set_llm_result(tr("Listing models..."))

        def run():
            try:
                self._llm_models_result.emit((settings, list_models(settings)))
            except Exception as e:
                self._llm_models_result.emit((settings, describe_request_error(e, settings)))

        threading.Thread(target=run, daemon=True).start()

    def _on_llm_models_listed(self, payload):
        settings, result = payload
        if settings is not self._llm_refresh_request:
            return  # superseded by a newer Refresh; that one re-enables the button
        self._llm_refresh_request = None
        self._llm_refresh_btn.setEnabled(True)
        if self._endpoint_identity(settings) != self._endpoint_identity(self._llm_settings_from_ui()):
            self._set_llm_result(tr("Endpoint changed while listing models; click Refresh again."), error=True)
            return
        if isinstance(result, str):
            self._set_llm_result(result, error=True)
            return
        current = self._llm_model_combo.currentText().strip()
        self._llm_model_combo.clear()
        self._llm_model_combo.addItems(result)
        if current in result:
            self._llm_model_combo.setCurrentIndex(result.index(current))
        elif current:
            self._llm_model_combo.setCurrentText(current)
        if not result:
            hint = tr(" Run 'ollama pull <model>' first.") if self._selected_provider() == "ollama" else ""
            self._set_llm_result(tr("The server has no models.{0}", hint), error=True)
        else:
            self._set_llm_result(tr("{0} model(s) found", len(result)))

    def _test_llm_endpoint(self):
        """Translate a sample line with the current panel settings on a background thread."""
        settings = self._llm_settings_from_ui()
        if not settings.model:
            self._set_llm_result(tr("Pick a model first."), error=True)
            return
        # The source language the OCR group would apply, not the saved one: the Test must
        # validate what the user is about to Apply, like every other pending field
        source_language = self._selected_source_language()
        self._llm_test_request = settings
        self._llm_test_language = source_language
        self._llm_test_btn.setEnabled(False)
        self._set_llm_result(
            tr("Testing with a {0} sample (loading the model may take a moment)...", tr(source_language))
        )

        def run():
            try:
                self._llm_test_result.emit((settings, check_endpoint(settings, source_language)))
            except Exception as e:
                self._llm_test_result.emit((settings, str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _on_llm_test_done(self, payload):
        settings, result = payload
        if settings is not self._llm_test_request:
            return  # superseded by a newer Test
        self._llm_test_request = None
        self._llm_test_btn.setEnabled(True)
        if settings != self._llm_settings_from_ui() or self._llm_test_language != self._selected_source_language():
            self._set_llm_result(tr("Settings changed during the test; click Test again."), error=True)
            return
        if isinstance(result, str):
            self._set_llm_result(result, error=True)
            return
        translation, ms = result
        self._set_llm_result(tr("OK in {0} ms: {1}", ms, translation))

    def _setup_permissions_ui(self, status_layout: QGridLayout):
        """Set up macOS permissions in the status section."""
        # Get current row count to add after models
        row = status_layout.rowCount()

        # Screen Recording row
        status_layout.addWidget(QLabel(tr("Screen Recording:")), row, 0)
        self._screen_recording_status = QLabel()
        status_layout.addWidget(self._screen_recording_status, row, 1)
        self._screen_recording_btn = QPushButton(tr("Grant"))
        self._screen_recording_btn.setFixedWidth(80)
        self._screen_recording_btn.clicked.connect(self._on_request_screen_recording)
        status_layout.addWidget(self._screen_recording_btn, row, 2)

        # Accessibility row (required for global hotkeys)
        row += 1
        status_layout.addWidget(QLabel(tr("Accessibility:")), row, 0)
        self._accessibility_status = QLabel()
        status_layout.addWidget(self._accessibility_status, row, 1)
        self._accessibility_btn = QPushButton(tr("Grant"))
        self._accessibility_btn.setFixedWidth(80)
        self._accessibility_btn.clicked.connect(self._on_request_accessibility)
        status_layout.addWidget(self._accessibility_btn, row, 2)

        # Initial permission check
        self._update_permissions_status()

    def _update_permissions_status(self):
        """Update the permission status indicators."""
        if not is_macos():
            return

        # Screen Recording
        if check_screen_recording():
            self._screen_recording_status.setText(tr("✓ Granted"))
            set_role(self._screen_recording_status, "status-ok")
            self._screen_recording_btn.setVisible(False)
        else:
            self._screen_recording_status.setText(tr("✗ Required"))
            set_role(self._screen_recording_status, "status-error")
            self._screen_recording_btn.setVisible(True)

        # Accessibility
        if check_accessibility():
            self._accessibility_status.setText(tr("✓ Granted"))
            set_role(self._accessibility_status, "status-ok")
            self._accessibility_btn.setVisible(False)
        else:
            self._accessibility_status.setText(tr("✗ Required"))
            set_role(self._accessibility_status, "status-error")
            self._accessibility_btn.setVisible(True)

    def _on_request_screen_recording(self):
        """Handle Screen Recording grant button click."""
        # Try to request permission (triggers system dialog if first time)
        if not request_screen_recording():
            # Already denied, open System Settings
            open_screen_recording_settings()

        # Update status after a short delay (permission may take a moment to register)
        QTimer.singleShot(500, self._update_permissions_status)

    def _on_request_accessibility(self):
        """Handle Accessibility grant button click."""
        # Try to request permission (triggers system dialog)
        if not request_accessibility():
            # Already denied, open System Settings
            open_accessibility_settings()

        # Update status after a short delay
        QTimer.singleShot(500, self._update_permissions_status)

    def _load_models(self):
        """Start worker thread and load OCR/translation models."""
        self.statusBar().showMessage(tr("Loading models..."))
        self._process_worker.set_mode(self._mode)
        self._process_worker.start(self._config.ocr_confidence)

    def _on_models_ready(self):
        """Handle models loaded signal from worker thread."""
        self._start_btn.setEnabled(True)
        self.statusBar().showMessage(tr("Ready"))
        logger.debug("models loaded")
        if self._config.live_speech_enabled and hasattr(self, "_speech"):
            self._speech.prepare(self._config.speech_voice_settings[0])

    def _on_models_failed(self, error: str):
        """Handle model loading failure from worker thread."""
        self._start_btn.setEnabled(self._capturing)
        self._show_fix_models_button()
        self.statusBar().showMessage(tr("Model loading failed: {0}", error[:120]))
        # Full message on hover, since the status bar truncates
        failed = self._process_worker.get_failed_models()
        if "translation" in failed:
            self._translation_status_label.setToolTip(error)
            if self._config.translation_backend == TranslationBackend.LLM:
                self._set_llm_result(error, error=True)
        if "ocr" in failed:
            self._ocr_status_label.setToolTip(error)
            if self._config.ocr_backend == OCRBackend.OWOCR:
                self._set_owocr_result(error, error=True)
        logger.error("model loading failed", error=error)

    def _on_ocr_status(self, status: str):
        """Handle OCR model status change."""
        # Show "Downloading..." instead of "Loading..." when fixing
        if status == "loading" and self._fixing_ocr:
            status = "downloading"
        if status == "ready":
            self._fixing_ocr = False
            self._ocr_status_label.setToolTip("")
            # A recovered endpoint must not keep showing its old connection error
            if self._config.ocr_backend == OCRBackend.OWOCR and ERROR in self._owocr_result_label.styleSheet():
                warning = owocr_remote_warning(self._config.owocr.url)
                self._set_owocr_result(warning or "", error=bool(warning))
        self._update_status_label(self._ocr_status_label, status)
        self._update_fix_button_visibility()

    def _on_translation_status(self, status: str):
        """Handle translation model status change."""
        # Show "Downloading..." instead of "Loading..." when fixing
        if status == "loading" and self._fixing_translation:
            status = "downloading"
        if status == "ready":
            self._fixing_translation = False
            self._translation_status_label.setToolTip("")
        self._update_status_label(self._translation_status_label, status)
        self._update_fix_button_visibility()

    def _update_status_label(self, label: QLabel, status: str):
        """Update a model status label with appropriate text and style."""
        if status == "loading":
            label.setText(tr("Loading..."))
            set_role(label, "status-busy")
        elif status == "downloading":
            label.setText(tr("Downloading..."))
            set_role(label, "status-busy")
        elif status == "ready":
            label.setText(tr("Ready"))
            set_role(label, "status-ok")
        elif status == "error":
            label.setText(tr("Error"))
            set_role(label, "status-error")

    def _update_fix_button_visibility(self):
        """Show/hide the Fix Models button based on model status."""
        has_error = any(
            label.property("role") == "status-error"
            for label in (self._ocr_status_label, self._translation_status_label)
        )
        if has_error:
            self._show_fix_models_button()
        else:
            self._fix_models_btn.setVisible(False)

    def _show_fix_models_button(self):
        self._fix_models_btn.show()

    def _on_fix_models(self):
        """Handle Fix Models button click."""
        from ..models import delete_model_cache

        # Track which models we're fixing (to show "Downloading..." instead of "Loading...")
        self._fixing_ocr = False
        self._fixing_translation = False

        # Delete caches for failed built-in models; endpoint-backed engines have nothing to download
        failed = self._process_worker.get_failed_models()
        if "ocr" in failed and self._config.ocr_backend == OCRBackend.MEIKI:
            delete_model_cache("rtr46/meiki.text.detect.v0")
            delete_model_cache("rtr46/meiki.txt.recognition.v0")
            self._fixing_ocr = True
        if (
            "translation" in failed
            and self._config.translation_backend == TranslationBackend.SUGOI
            # A source-language conflict is a settings problem, not a corrupt download
            and self._config.source_language == SUGOI_SOURCE_LANGUAGE
        ):
            delete_model_cache("entai2965/sugoi-v4-ja-en-ctranslate2")
            self._fixing_translation = True

        # Hide the button while fixing
        self._fix_models_btn.setVisible(False)

        if not (self._fixing_ocr or self._fixing_translation):
            # Nothing to download: retry the endpoints with the current settings
            self.statusBar().showMessage(tr("Reconnecting..."))
            if "ocr" in failed:
                self._process_worker.reload_ocr()
            if "translation" in failed:
                self._process_worker.reload_translation()
            return

        self.statusBar().showMessage(tr("Downloading models..."))

        # Stop and restart the worker to reload models
        self._history.cancel_retranslation()
        self._process_worker.stop()
        self._process_worker = self._create_worker()
        self._process_worker.set_capture_source(self._current_window_title if self._capturing else "")
        self._process_worker.set_mode(self._mode)
        self._process_worker.start(
            self._config.get_ocr_confidence(self._current_window_title if self._capturing else None)
        )

    def _refresh_sources(self):
        """Refresh the source list: windows (or the Wayland picker) followed by video devices.

        Each item carries a (kind, value) tuple: ("portal", None), ("window", index into
        self._windows_list) or ("device", (device id, device name)).

        A source the user already picked stays selected when it is still there; otherwise
        the source captured last (from the config) is selected.
        """
        previous = self._source_key(self._window_combo.currentData())
        self._window_combo.clear()
        selected_idx = -1

        if self._is_wayland_session:
            # Wayland has no window list; the portal shows the system picker on start
            self._windows_list = []
            self._window_combo.addItem(tr("Window (system picker)"), ("portal", None))
            selected_idx = 0
        else:
            self._windows_list = WindowCapture.list_windows()
            for i, win in enumerate(self._windows_list):
                title = win.get("title", "Unknown")
                bounds = win.get("bounds", {})
                width = bounds.get("width", 0)
                height = bounds.get("height", 0)
                if len(title) > 50:
                    title = title[:50] + "..."
                self._window_combo.addItem(f"{title} ({width}x{height})", ("window", i))

                # Auto-select if matches config
                if self._config.window_title and self._config.window_title.lower() in win.get("title", "").lower():
                    selected_idx = self._window_combo.count() - 1

        devices = list_video_devices()
        if devices:
            if self._window_combo.count():
                self._window_combo.insertSeparator(self._window_combo.count())
            names_seen: dict[str, int] = {}
            id_match = name_match = -1
            for device in devices:
                name = device.description()
                id_text = device_id(device)
                # Two cards of the same model: number the display text, the id tells them apart
                names_seen[name] = names_seen.get(name, 0) + 1
                label = f"Video device: {name}" + (f" ({names_seen[name]})" if names_seen[name] > 1 else "")
                self._window_combo.addItem(label, ("device", (id_text, name)))
                if self._config.video_device_id and self._config.video_device_id == id_text:
                    id_match = self._window_combo.count() - 1
                elif name_match < 0 and self._config.video_device and self._config.video_device == name:
                    name_match = self._window_combo.count() - 1
            # A video device captured last wins over the window title match: by id, else by name
            device_match = id_match if id_match >= 0 else name_match
            if device_match >= 0:
                selected_idx = device_match

        # Keep what the user had picked, if it is still there
        if previous is not None:
            for i in range(self._window_combo.count()):
                if self._source_key(self._window_combo.itemData(i)) == previous:
                    selected_idx = i
                    break

        if selected_idx >= 0:
            self._window_combo.setCurrentIndex(selected_idx)

    def _source_key(self, data) -> tuple | None:
        """Stable identity of a source item, independent of its position in the list."""
        if not data:
            return None
        kind, value = data
        if kind == "window":
            if value >= len(self._windows_list):
                return None
            return ("window", self._windows_list[value].get("id"))
        if kind == "device":
            return ("device", value[0])
        return (kind,)

    def _on_video_devices_changed(self):
        """A video device was plugged in or removed; re-list unless a capture is running."""
        if not self._capturing:
            self._refresh_sources()

    def _toggle_capture(self):
        """Start or stop capture."""
        if self._capturing:
            self._stop_capture()
        else:
            self._start_capture()

    def _start_capture(self):
        """Start capturing the selected source."""
        kind, value = self._window_combo.currentData() or (None, None)
        if kind == "portal":
            self._start_wayland_capture()
            return
        if kind == "device":
            id_text, name = value
            self._start_video_capture(name, id_text)
            return
        if kind != "window" or value >= len(self._windows_list):
            self.statusBar().showMessage(tr("No source selected"))
            return

        window = self._windows_list[value]
        title = window.get("title", "")
        if error := self._profile_language_error(title):
            self.statusBar().showMessage(error)
            return
        window_id = window.get("id")
        bounds = window.get("bounds")

        try:
            self._capture = WindowCapture(
                title, window_id=window_id, bounds=bounds, on_frame_ready=self._queue_capture_frame
            )
            if not self._capture.find_window():
                self._stop_capture()
                self.statusBar().showMessage(tr("Window not found"))
                return
            if not self._capture.start_stream():
                self._stop_capture()
                self.statusBar().showMessage(tr("Failed to start stream"))
                return
        except Exception as e:
            error_msg = str(e)
            log.get_logger().error("capture failed", error=error_msg)
            # Show truncated message in status bar (full message in logs)
            display_msg = error_msg.split("\n")[0][:100]  # First line, max 100 chars
            self._stop_capture()
            self.statusBar().showMessage(tr("Capture failed: {0}", display_msg))
            return

        self._process_timer.setInterval(PROCESS_INTERVAL_MS)
        self._process_timer.start()

        self._capturing = True

        self._last_ocr_results = []  # boxes from a previous session must not outlive it
        self._paused = False
        self._start_btn.setText(tr("Stop Capture"))
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText(tr("Hide"))
        self._ocr_config_btn.setEnabled(True)
        self.statusBar().showMessage(tr("Capturing '{0}...'", title[:40]))

        # Update config with selected window
        self._config.window_title = title
        self._config.video_device = ""
        self._config.video_device_id = ""
        self._current_window_title = title  # Store for exclusion zone lookup
        self._process_worker.set_capture_source(title)

        # Set per-window OCR confidence
        confidence = self._config.get_ocr_confidence(title)
        self._process_worker.set_confidence_threshold(confidence)

        # Show overlay
        self._show_overlay()

    def _start_video_capture(self, name: str, id_text: str = ""):
        """Start capturing a video device (capture card, webcam), by id when known, else by name."""
        if error := self._profile_language_error(name):
            self.statusBar().showMessage(error)
            return
        device = find_video_device(name, id_text)
        if device is None:
            self.statusBar().showMessage(tr("Video device not found: {0}", name[:40]))
            self._refresh_sources()
            return

        try:
            self._capture = VideoDeviceCapture(device, self)
            self._capture.start()
        except Exception as e:
            logger.error("video device capture failed", device=name, error=str(e))
            first_line = (str(e).splitlines() or [""])[0]
            self._stop_capture()
            self.statusBar().showMessage(tr("Capture failed: {0}", first_line[:100]))
            return

        self._process_timer.setInterval(PROCESS_INTERVAL_MS)
        self._process_timer.start()

        self._capturing = True
        self._frames_missing = 0
        self._waiting_for_signal = False
        self._preview_updated_at = float("-inf")

        self._last_ocr_results = []  # boxes from a previous session must not outlive it
        self._paused = False
        self._start_btn.setText(tr("Stop Capture"))
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText(tr("Hide"))
        self._ocr_config_btn.setEnabled(True)
        self.statusBar().showMessage(tr("Capturing video device '{0}'", name[:40]))

        # Remember the device; exclusion zones and OCR confidence are keyed by its name
        self._config.video_device = name
        self._config.video_device_id = device_id(device)
        self._current_window_title = name
        self._process_worker.set_capture_source(name)
        self._process_worker.set_confidence_threshold(self._config.get_ocr_confidence(name))

        # Nothing on screen to draw over: banner mode for the whole session
        self._set_banner_only(True)
        self._show_overlay()

    def _set_banner_only(self, banner_only: bool):
        """Lock the overlay to banner mode (video device sessions) or lift the lock again.

        The user's chosen mode is restored when the lock is lifted and is never written to
        the config by the forced switch.
        """
        self._banner_only = banner_only
        if banner_only:
            self._inplace_btn.setEnabled(False)
            self._inplace_btn.setToolTip(tr("Video devices have no window on screen to draw over: banner mode only"))
            if self._mode == OverlayMode.INPLACE:
                self._mode_before_video = self._mode
                self._banner_btn.setChecked(True)
                self._apply_mode(OverlayMode.BANNER, persist=False)
        else:
            self._inplace_btn.setEnabled(True)
            self._inplace_btn.setToolTip("")
            if self._mode_before_video is not None:
                restored, self._mode_before_video = self._mode_before_video, None
                self._inplace_btn.setChecked(restored == OverlayMode.INPLACE)
                self._banner_btn.setChecked(restored == OverlayMode.BANNER)
                self._apply_mode(restored, persist=False)

    def _start_wayland_capture(self):
        """Start Wayland capture using xdg-desktop-portal."""
        from ..capture.linux_wayland import WaylandCaptureStream, WaylandPortalCapture

        # Guard against duplicate selection requests.
        if self._wayland_selecting:
            return
        if error := self._profile_language_error("Wayland Capture"):
            self.statusBar().showMessage(error)
            return
        self._wayland_selecting = True

        self.statusBar().showMessage(tr("Select a window..."))
        self._start_btn.setEnabled(False)

        try:
            self._wayland_portal = WaylandPortalCapture()

            # Synchronous API (pipewire-capture 0.2.0) - returns stream info directly
            stream_info = self._wayland_portal.select_window()
            self._wayland_selecting = False

            if not stream_info:
                self.statusBar().showMessage(tr("Window selection cancelled"))
                self._start_btn.setEnabled(True)
                self._wayland_portal.close()
                self._wayland_portal = None
                return

            fd, node_id, width, height = stream_info

            # Use unified capture interface
            self._capture = WaylandCaptureStream(fd, node_id, width, height)
            self._capture.start()

            # Start processing timer
            self._process_timer.setInterval(PROCESS_INTERVAL_MS)
            self._process_timer.start()

            self._capturing = True

            self._last_ocr_results = []  # boxes from a previous session must not outlive it
            self._paused = False
            self._pause_btn.setEnabled(True)
            self._pause_btn.setText(tr("Hide"))
            self._ocr_config_btn.setEnabled(True)
            self.statusBar().showMessage(tr("Capturing Wayland window..."))

            # Update button to show stop action
            self._start_btn.setText(tr("Stop Capture"))
            self._start_btn.setEnabled(True)

            # Store window title for exclusion zone lookup (Wayland doesn't have window titles)
            self._current_window_title = "Wayland Capture"
            self._process_worker.set_capture_source(self._current_window_title)
            self._config.video_device = ""
            self._config.video_device_id = ""

            # Set per-window OCR confidence
            confidence = self._config.get_ocr_confidence(self._current_window_title)
            self._process_worker.set_confidence_threshold(confidence)

            # Show overlay
            self._show_overlay()

        except Exception as e:
            logger.error("failed to start wayland capture", error=str(e))
            self._wayland_selecting = False
            self._stop_capture()
            self.statusBar().showMessage(tr("Wayland capture failed"))
            message = QMessageBox(self)
            message.setIcon(QMessageBox.Icon.Warning)
            message.setWindowTitle(tr("Wayland capture failed"))
            message.setTextFormat(Qt.TextFormat.PlainText)
            message.setText(tr("Could not start screen capture."))
            message.setInformativeText(str(e).split("\n", 1)[0])
            message.setDetailedText(str(e))
            message.exec()

    def _stop_capture(self):
        """Stop capturing."""
        if hasattr(self, "_speech"):
            self._speech.stop(reset=True, reason="capture stopped")
        if getattr(self, "_history_overlay", None) is not None:
            self._history_overlay.hide()
        self._process_timer.stop()
        self._capture_idle_timer.stop()
        self._last_ocr_capture = None
        self._capturing = False
        self._capture_generation += 1
        self._process_worker.set_capture_source("")
        self._process_worker.discard_pending_frames()

        # Stop capture using unified interface
        capture, self._capture = self._capture, None
        if capture:
            try:
                capture.stop()
            except Exception as e:
                logger.warning("failed to stop capture", error=str(e))

        # No-signal notice belongs to the video device session that just ended
        self._frames_missing = 0
        self._waiting_for_signal = False
        self._preview_updated_at = float("-inf")

        # Close Wayland portal session if active
        portal, self._wayland_portal = self._wayland_portal, None
        if portal:
            try:
                portal.close()
            except Exception as e:
                logger.warning("failed to close capture portal", error=str(e))

        self._last_frame = None
        self._last_bounds = {}
        self._last_ocr_results = []
        self._detected_character = ""
        if hasattr(self, "_character_status"):
            self._character_status.setText(tr("Character voices off"))
        self._paused = False
        self._pause_btn.setEnabled(False)
        self._ocr_config_btn.setEnabled(False)
        self.statusBar().showMessage(tr("Ready"))

        self._start_btn.setText(tr("Start Capture"))
        self._start_btn.setEnabled(True)

        # Clear preview
        self._preview_pixmap = None
        self._preview_label.clear()
        self._preview_label.setText(tr("No preview"))

        # Hide overlays and clear inplace labels
        self._banner_overlay.hide()
        self._banner_overlay.set_text("")
        self._inplace_overlay.clear_regions()
        self._inplace_overlay.hide()

        # Give the inplace mode back after a video device session
        self._set_banner_only(False)

    def _toggle_pause(self):
        """Toggle pause state."""
        if not self._capturing:
            return
        self._paused = not self._paused
        logger.debug("overlay visibility toggled", hidden=self._paused, tts_continues=self._config.live_speech_enabled)
        if self._paused:
            self._pause_btn.setText(tr("Show"))
            self._banner_overlay.hide()
            self._inplace_overlay.clear_regions()
            self._inplace_overlay.hide()
            if getattr(self, "_history_overlay", None) is not None:
                self._history_overlay.hide()
        else:
            self._pause_btn.setText(tr("Hide"))
            self._show_overlay()

    def _toggle_mode(self):
        """Toggle between banner and inplace mode via hotkey."""
        if not self._inplace_btn.isEnabled():
            return  # video device session: banner only
        new_mode_id = 0 if self._mode == OverlayMode.INPLACE else 1
        [self._banner_btn, self._inplace_btn][new_mode_id].setChecked(True)
        self._on_mode_changed(new_mode_id)

    def _on_key_press(self, key):
        """Handle global key press (called from keyboard listener thread)."""
        # Compare the pressed key with our hotkeys
        if key == self._current_hotkey:
            self.hotkey_pressed.emit()
        elif key == self._mode_switch_hotkey:
            self.mode_switch_pressed.emit()

    def _on_pause_hotkey_changed(self, key_sequence: QKeySequence):
        """Update pause hotkey when changed in UI."""
        if key_sequence.isEmpty():
            return
        # Convert Qt key sequence to keyboard key
        key_str = key_sequence.toString().lower()
        self._current_hotkey = self._qt_key_to_key(key_str)
        # Save to config
        self._config.hotkeys["toggle_overlay"] = key_str
        # Clear focus so it stops capturing keys
        self._pause_hotkey.clearFocus()

    def _on_mode_hotkey_changed(self, key_sequence: QKeySequence):
        """Update mode switch hotkey when changed in UI."""
        if key_sequence.isEmpty():
            return
        key_str = key_sequence.toString().lower()
        self._mode_switch_hotkey = self._qt_key_to_key(key_str)
        self._config.hotkeys["switch_mode"] = key_str
        self._mode_hotkey.clearFocus()

    def _qt_key_to_key(self, key_str: str):
        """Convert Qt key string to keyboard module key."""
        # Map common keys
        key_map = {
            "space": keyboard.Key.space,
            "esc": keyboard.Key.esc,
            "escape": keyboard.Key.esc,
            "tab": keyboard.Key.tab,
            "return": keyboard.Key.enter,
            "enter": keyboard.Key.enter,
            "backspace": keyboard.Key.backspace,
            "delete": keyboard.Key.delete,
            "home": keyboard.Key.home,
            "end": keyboard.Key.end,
            "pgup": keyboard.Key.page_up,
            "pgdown": keyboard.Key.page_down,
            "up": keyboard.Key.up,
            "down": keyboard.Key.down,
            "left": keyboard.Key.left,
            "right": keyboard.Key.right,
            "f1": keyboard.Key.f1,
            "f2": keyboard.Key.f2,
            "f3": keyboard.Key.f3,
            "f4": keyboard.Key.f4,
            "f5": keyboard.Key.f5,
            "f6": keyboard.Key.f6,
            "f7": keyboard.Key.f7,
            "f8": keyboard.Key.f8,
            "f9": keyboard.Key.f9,
            "f10": keyboard.Key.f10,
            "f11": keyboard.Key.f11,
            "f12": keyboard.Key.f12,
        }
        if key_str in key_map:
            return key_map[key_str]
        # For single characters, use KeyCode
        if len(key_str) == 1:
            return keyboard.KeyCode.from_char(key_str)
        return keyboard.Key.space  # fallback

    def _hotkey_str_to_qkeysequence(self, key_str: str) -> QKeySequence:
        """Convert config hotkey string to QKeySequence for the picker widget."""
        # Map config strings to Qt key names
        key_map = {
            "space": "Space",
            "esc": "Escape",
            "escape": "Escape",
            "tab": "Tab",
            "return": "Return",
            "enter": "Return",
            "backspace": "Backspace",
            "delete": "Delete",
            "home": "Home",
            "end": "End",
            "pgup": "PgUp",
            "pgdown": "PgDown",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
        }
        # Check special keys first
        if key_str.lower() in key_map:
            return QKeySequence(key_map[key_str.lower()])
        # F-keys (f1-f12)
        if key_str.lower().startswith("f") and key_str[1:].isdigit():
            return QKeySequence(key_str.upper())
        # Single character
        if len(key_str) == 1:
            return QKeySequence(key_str.upper())
        # Fallback to space
        return QKeySequence(Qt.Key.Key_Space)

    def _on_mode_changed(self, button_id: int):
        """Handle mode selection change."""
        # A click on Banner during a video device session must not overwrite the saved choice
        mode = OverlayMode.BANNER if button_id == 0 else OverlayMode.INPLACE
        self._apply_mode(mode, persist=not self._banner_only)

    def _apply_mode(self, mode: OverlayMode, persist: bool):
        """Switch the overlay mode; persist=False for the temporary switch of a video device session."""
        self._mode = mode

        self._process_worker.set_mode(self._mode)
        if persist:
            self._config.overlay_mode = self._mode

        if self._capturing and not self._paused:
            self._show_overlay()

    def _show_wayland_warning(self):
        """Show warning about inplace mode limitations on Wayland."""
        QMessageBox.information(
            self,
            tr("Inplace Mode on Wayland"),
            tr(
                "Inplace mode only works correctly with fullscreen games on Wayland.\n\nFor windowed games, the text labels may appear in the wrong position because Wayland doesn't expose window coordinates.\n\nThis is a Wayland security limitation, not a bug."
            ),
        )

    def _show_overlay(self):
        """Show the appropriate overlay."""
        if self._config.effective_output_mode == OutputMode.TTS:
            self._banner_overlay.hide()
            self._inplace_overlay.hide()
            return
        if self._mode == OverlayMode.BANNER:
            self._inplace_overlay.hide()
            self._banner_overlay.show()
        else:
            self._banner_overlay.hide()
            if self._last_bounds:
                self._inplace_overlay.position_over_window(self._last_bounds)
            self._inplace_overlay.show()

    def _queue_capture_frame(self, capture):
        try:
            self._capture_frame_ready.emit(capture)
        except RuntimeError:
            pass  # Capture notification raced with Qt teardown.

    @Slot(bool)
    def _set_history_overlay_enabled(self, enabled):
        self._config.history_overlay_enabled = enabled
        self._sync_history_overlay()
        self._config.save()

    def _set_hide_stale_overlay(self, enabled):
        self._config.hide_stale_overlay = enabled
        self._config.save()

    @Slot()
    def _sync_history_overlay(self):
        overlay, capture = self._history_overlay, self._capture
        if overlay is None:
            return
        if (
            not self._capturing
            or self._paused
            or not self._config.history_overlay_enabled
            or not isinstance(capture, WindowCapture)
        ):
            overlay.hide()
            return
        overlay.sync(capture.bounds, capture.is_foreground(), capture.window_id)

    @Slot(object)
    def _on_capture_frame_ready(self, capture):
        if self._capturing and capture is self._capture:
            self._capture_and_process()

    def _capture_and_process(self):
        """Capture a frame and process it through OCR and translation."""
        if not self._capture:
            return

        # Get frame using unified capture interface
        try:
            if isinstance(self._capture, WindowCapture):
                frame, captured_at = self._capture.get_frame_with_timestamp()
            else:
                frame, captured_at = self._capture.get_frame(), None
        except Exception as e:
            logger.error("capture frame failed", error=str(e))
            self._stop_capture()
            first_line = str(e).split("\n", 1)[0]
            self.statusBar().showMessage(tr("Capture stopped: {0}", first_line[:100]))
            return

        # Check if the window was closed or the video device went away
        if self._capture.window_invalid:
            error = getattr(self._capture, "error", None)
            logger.info("capture source gone, stopping capture", error=error)
            self._stop_capture()
            if error:
                self.statusBar().showMessage(tr("Capture stopped: {0}", error[:100]))
            return

        # Get bounds (None on Wayland, dict on X11/Windows/macOS)
        bounds = self._capture.bounds or {}

        # Keep the overlay above the game even after the game takes focus
        # (a focused topmost fullscreen window otherwise covers it)
        if not self._paused:
            overlay = self._inplace_overlay if self._mode == OverlayMode.INPLACE else self._banner_overlay
            overlay.ensure_above(getattr(self._capture, "window_id", None))

        if frame is None:
            if self._last_ocr_capture is not None:
                self._capture_idle_timer.stop()
                self._last_ocr_capture = None
                self._process_worker.discard_pending_frames()
            # Display/session changes can interrupt window capture too. Show a
            # notice rather than leaving an unexplained frozen preview/overlay.
            self._frames_missing += 1
            if self._frames_missing * PROCESS_INTERVAL_MS >= NO_SIGNAL_NOTICE_MS and not self._waiting_for_signal:
                self._waiting_for_signal = True
                if isinstance(self._capture, VideoDeviceCapture):
                    self.statusBar().showMessage(
                        tr("Waiting for a video signal from '{0}'...", self._capture.name[:40])
                    )
                else:
                    self.statusBar().showMessage(
                        tr("Waiting for window frames. Keep the game visible; capture will retry.")
                    )
            return

        if self._waiting_for_signal:
            if isinstance(self._capture, VideoDeviceCapture):
                self.statusBar().showMessage(tr("Capturing video device '{0}'", self._capture.name[:40]))
            else:
                self.statusBar().showMessage(tr("Capturing '{0}'", self._current_window_title[:40]))
        self._waiting_for_signal = False
        self._frames_missing = 0

        self._last_frame = frame
        self._last_bounds = bounds

        now = time.monotonic()
        if now - self._preview_updated_at >= PREVIEW_INTERVAL_MS / 1000:
            self._preview_updated_at = now
            self._update_capture_preview(frame)

        # Update inplace overlay position if window moved (X11 only - Wayland doesn't have bounds)
        if self._mode == OverlayMode.INPLACE and bounds and not self._paused:
            self._inplace_overlay.position_over_window(bounds)

        if self._paused and not (self._config.live_speech_enabled or self._ocr_config_dialog):
            return

        if captured_at is not None:
            frame_key = (self._capture_generation, captured_at, id(frame))
            new_frame = frame_key != self._last_ocr_capture
            if new_frame:
                self._last_ocr_capture_seen_at = now
            remaining = CAPTURE_IDLE_SECONDS - (now - self._last_ocr_capture_seen_at)
            if remaining > 0:
                if new_frame or not self._capture_idle_timer.isActive():
                    self._capture_idle_timer.start(max(10, math.ceil(remaining * 1000)))
                if not new_frame and not self._ocr_config_dialog:
                    return
            else:
                # A static window can stop publishing. Re-read the valid frame
                # via the normal settle rules so static dialogue still speaks.
                captured_at = None
            self._last_ocr_capture = frame_key

        # Apply exclusion zones before OCR
        frame_for_ocr, origin = self._prepare_ocr_frame(frame)
        if frame_for_ocr is None:
            return
        frame_for_ocr, speaker_context = self._prepare_character_ocr(frame, frame_for_ocr, origin)
        options = {}
        if origin != (0, 0) or self._ocr_config_dialog or speaker_context is not None:
            options.update(origin=origin, preview_only=bool(self._ocr_config_dialog))
        if speaker_context is not None:
            options["speaker_context"] = speaker_context
        if captured_at is not None:
            options["captured_at"] = captured_at
        self._process_worker.submit_frame(frame_for_ocr, self._capture_generation, **options)

    def _update_capture_preview(self, frame):
        """Paint the preview/editor independently of the OCR polling rate."""
        # Shrink the preview before copying/reordering every pixel. The original
        # full-resolution frame still goes to OCR and the exclusion editor.
        frame_h, frame_w = frame.shape[:2]

        # Scale to max 320 width while preserving aspect ratio
        max_preview_width = min(self.PREVIEW_WIDTH, frame_w)  # render once at good quality, fit later
        scale = max_preview_width / frame_w
        preview_w = int(frame_w * scale)
        preview_h = int(frame_h * scale)
        step = max(1, frame_w // max_preview_width)
        preview = bgra_to_rgb_pil(frame[::step, ::step])
        preview = preview.resize((preview_w, preview_h), Image.Resampling.BILINEAR)

        # Draw exclusion zones on preview
        region = self._selected_ocr_region()
        zones = self._config.get_exclusion_zones(self._current_window_title) if region is None else []
        if region:
            draw = ImageDraw.Draw(preview, "RGBA")
            for box in normalize_ocr_regions(region):
                x, y = int(box["x"] * preview_w), int(box["y"] * preview_h)
                right = int((box["x"] + box["width"]) * preview_w)
                bottom = int((box["y"] + box["height"]) * preview_h)
                draw.rectangle([x, y, right, bottom], outline=(79, 140, 255, 255), width=3)
                draw.text((x + 4, y + 4), "Translation box", fill=(79, 140, 255, 255))
        if zones:
            draw = ImageDraw.Draw(preview, "RGBA")
            for zone in zones:
                x = int(zone.get("x", 0) * preview_w)
                y = int(zone.get("y", 0) * preview_h)
                w = int(zone.get("width", 0) * preview_w)
                h = int(zone.get("height", 0) * preview_h)
                # Semi-transparent red fill with red outline
                draw.rectangle([x, y, x + w, y + h], fill=(255, 0, 0, 60), outline=(255, 0, 0, 180))

        # Live OCR boxes: what the engine read on the last processed frame
        if self._last_ocr_results:
            draw = ImageDraw.Draw(preview, "RGBA")
            for result in self._last_ocr_results:
                box = result.bbox
                if not box:
                    continue
                x, y = int(box["x"] * scale), int(box["y"] * scale)
                w, h = int(box["width"] * scale), int(box["height"] * scale)
                draw.rectangle([x, y, x + w, y + h], outline=(79, 140, 255, 220), width=2)

        data = preview.tobytes("raw", "RGB")
        qimg = QImage(data, preview_w, preview_h, preview_w * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        # Resize label to match preview aspect ratio
        self._preview_pixmap = pixmap
        self._fit_preview()

        # Update exclusion editor dialog if open
        if self._ocr_config_dialog:
            self._ocr_config_dialog.update_frame(frame)

    def _fit_preview(self):
        """Scale the rendered preview to the card's current width, keeping the aspect ratio."""
        if self._preview_pixmap is None:
            return
        scaled = self._preview_pixmap.scaled(
            self._preview_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self._preview_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_preview()

    @Slot(object, int)
    def _on_dialogue_translated(self, entry, generation):
        if self.sender() is self._process_worker and self._capturing and generation == self._capture_generation:
            self._history.append(entry)

    def _replay_history(self, entry):
        if entry is None or not entry.can_replay:
            return
        if self._speech.replay(entry.translation, *entry.voice):
            self._history.status.setText(tr("Replay requested."))
        else:
            self._history.status.setText(tr("Replay unavailable. Wait for current speech to finish and try again."))

    def _retranslate_history(self, entry):
        if not self._process_worker.request_retranslation(entry):
            self._history.finish_retranslation(entry, "Translation engine is busy or unavailable. Try again.")

    @Slot(object, object)
    def _on_dialogue_retranslated(self, entry, result):
        if self.sender() is self._process_worker:
            self._history.finish_retranslation(entry, result)

    def _on_text_ready(self, translated: str, generation: int):
        """Handle translated text (banner mode)."""
        if (
            self._capturing
            and generation == self._capture_generation
            and not self._paused
            and self._config.effective_output_mode != OutputMode.TTS
        ):
            self._banner_overlay.set_text(translated)

    def _on_speech_scene_reset(self, generation: int):
        if self._capturing and generation == self._capture_generation and hasattr(self, "_speech"):
            self._speech.reset_repeat_guard()

    def _on_speech_ready(self, translated: str, generation: int):
        if (
            self._capturing
            and generation == self._capture_generation
            and self._config.live_speech_enabled
            and hasattr(self, "_speech")
        ):
            if self._config.translation_backend == TranslationBackend.LLM:
                current_voice = self._config.speech_voice_settings
                model, preset, rate, reference = current_voice
                if isinstance(translated, SpeechStream):
                    if translated.voice_settings is not None and translated.voice_settings != current_voice:
                        translated.cancel()
                        return
                    if translated.speaker_id is not None:
                        preset = translated.speaker_id
                    if translated.reference_audio is not None:
                        reference = translated.reference_audio
                self._speech.submit(
                    translated,
                    model,
                    preset,
                    rate,
                    reference_audio=reference,
                )
                return
        if isinstance(translated, SpeechStream):
            translated.cancel()

    def _on_character_detected(self, name, mapped, generation):
        if not self._capturing or generation != self._capture_generation:
            return
        self._detected_character = name
        if hasattr(self, "_character_status"):
            self._character_status.setText(
                tr("Character: {0} ({1})", name, tr("assigned preset" if mapped else "default voice"))
                if name
                else tr("Narration / no name: default voice")
            )
        if getattr(self, "_character_dialog", None) is not None:
            self._character_dialog.set_detected_name(name)

    def _profile_source_title(self):
        if self._capturing:
            return self._current_window_title
        kind, value = self._window_combo.currentData() or (None, None)
        if kind == "window" and 0 <= value < len(self._windows_list):
            return self._windows_list[value].get("title", "")
        if kind == "device":
            return value[1]
        if kind == "portal":
            return "Wayland Capture"
        return ""

    def capture_diagnostics(self):
        if not self._capturing or self._capture is None:
            return "Idle", "—", "—"
        if isinstance(self._capture, WindowCapture):
            backend = self._capture.backend
        elif isinstance(self._capture, VideoDeviceCapture):
            backend = "Video device"
        else:
            backend = "Wayland portal"
        waiting = self._last_frame is None or self._frames_missing > 0
        size = "—" if self._last_frame is None else f"{self._last_frame.shape[1]} × {self._last_frame.shape[0]}"
        return "Waiting for frames" if waiting else "Receiving frames", backend, size

    def _open_diagnostics(self):
        if self._diagnostics_dialog is None:
            self._diagnostics_dialog = DiagnosticsDialog(self)
        self._diagnostics_dialog.show()
        self._diagnostics_dialog.raise_()
        self._diagnostics_dialog.activateWindow()

    def _profile_language_error(self, title, language=None, *, default_language=None, backend=None, ocr_backend=None):
        config = self._config
        if language is None:
            profile = config.game_profiles.get(title)
            language = profile.source_language if profile else ""
        if (ocr_backend or config.ocr_backend) == OCRBackend.MEIKI and language and language != "Japanese":
            return tr("MeikiOCR only reads Japanese. Select PaddleOCR or owocr in Engines for this game profile.")
        language = language or default_language or config.source_language
        if (backend or config.translation_backend) == TranslationBackend.SUGOI and language != SUGOI_SOURCE_LANGUAGE:
            return tr("The game profile requires an LLM endpoint for this source language. ") + tr(SUGOI_ONLY_JAPANESE)
        return ""

    def _open_game_profile(self):
        title = self._profile_source_title()
        if not title:
            self.statusBar().showMessage(tr("Select a capture source first."))
            return
        profile = self._config.game_profiles.get(title) or GameProfile()
        dialog = GameProfileDialog(
            title,
            profile,
            self._config.source_language,
            lambda language: self._profile_language_error(title, language),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.profile()
        if updated != profile:
            self._config.set_game_profile(title, updated)
            self._config.save()
            if self._capturing and title == self._current_window_title:
                self._capture_generation += 1
                self._last_ocr_capture = None
                self._process_worker.discard_pending_frames()
                self._speech.stop(reset=True, reason="game profile changed")
                self._banner_overlay.set_text("")
                self._inplace_overlay.clear_regions()
        self.statusBar().showMessage(tr("Game profile saved"))

    def _open_character_voices(self):
        title = self._profile_source_title()
        if not title:
            self.statusBar().showMessage(
                tr("Select a capture source first. On Wayland, start capture to choose a window.")
            )
            return
        dialog = CharacterVoicesDialog(
            self,
            title,
            self._config.get_character_voices(title),
            self._config.tts_model_path,
            frame=self._last_frame if self._capturing else None,
            detected_name=self._detected_character if self._capturing else "",
        )
        self._character_dialog = dialog
        dialog.preview_requested.connect(
            lambda index: self._speech.preview(self._config.tts_model_path, index, self._config.tts_rate)
        )
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                profile = dialog.get_profile()
                self._config.set_character_voices(title, profile)
                self._config.save()
                if self._capturing:
                    self._character_status.setText(
                        tr("Waiting for speaker name…") if profile.enabled else tr("Character voices off")
                    )
        finally:
            self._character_dialog = None

    def _sync_output_selection(self):
        mode = self._config.effective_output_mode
        speech_supported = self._config.target_supports_speech
        with QSignalBlocker(self._output_combo):
            self._output_combo.setCurrentIndex(self._output_combo.findData(mode.value))
        self._output_combo.setEnabled(speech_supported)
        hint = tr("The bundled voices read Vietnamese.")
        if clone_language(self._config.llm.target_language):
            hint = tr("Set a local clone server and choose a recording to enable speech for this target.")
        self._output_combo.setToolTip("" if speech_supported else hint)

    def _sync_translation_output(self):
        self._sync_output_selection()
        self._refresh_reference_controls()
        self._speech.stop(reset=True, reason="translation target changed")
        self._speech.set_enabled(self._config.live_speech_enabled)
        if self._capturing:
            self._capture_generation += 1
            self._last_ocr_capture = None
            self._process_worker.discard_pending_frames()
            self._banner_overlay.set_text("")
            self._inplace_overlay.clear_regions()
            if not self._paused:
                self._show_overlay()

    def _on_output_changed(self):
        self._config.output_mode = OutputMode(self._output_combo.currentData())
        self._config.tts_enabled = self._config.output_mode != OutputMode.OVERLAY
        self._speech.stop(reset=True, reason="output mode changed")
        self._speech.set_enabled(self._config.live_speech_enabled)
        if self._config.live_speech_enabled:
            self._speech.prepare(self._config.speech_voice_settings[0])
        if self._capturing and not self._paused:
            self._show_overlay()
        self._config.save()

    def _on_tts_model_changed(self):
        self._config.tts_model_path = self._tts_model_edit.text().strip()
        self._config.tts_speaker_id = 0
        self._refresh_voice_models()
        self._on_speech_settings_changed()

    def _browse_tts_voice(self):
        from pathlib import Path

        if Path(self._config.tts_model_path).expanduser().is_dir():
            path = QFileDialog.getExistingDirectory(self, tr("Select a local VieNeu Nano model folder"))
            if path:
                self._tts_model_edit.setText(path)
                self._on_tts_model_changed()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select an offline Vietnamese Piper voice"), "", tr("Piper voice (*.onnx)")
        )
        if path:
            self._tts_model_edit.setText(path)
            self._on_tts_model_changed()

    def _refresh_voice_models(self):
        from pathlib import Path

        selected = self._config.tts_model_path
        self._tts_voice_combo.blockSignals(True)
        self._tts_voice_combo.clear()
        for label, path in available_voices(selected):
            self._tts_voice_combo.addItem(tr(label), path)
        index = self._tts_voice_combo.findData(str(Path(selected).expanduser().resolve())) if selected else -1
        if index < 0:
            self._tts_voice_combo.addItem(tr("Custom / select local model"), selected)
            index = self._tts_voice_combo.count() - 1
        self._tts_voice_combo.setCurrentIndex(index)
        self._tts_voice_combo.blockSignals(False)
        self._refresh_speakers()

    def _refresh_speakers(self):
        self._tts_speaker_combo.blockSignals(True)
        self._tts_speaker_combo.clear()
        try:
            speakers = voice_details(self._config.tts_model_path)
        except (OSError, ValueError, TypeError, KeyError):
            speakers = [("Default speaker", 0)]
        for label, index in speakers:
            self._tts_speaker_combo.addItem(tr(voice_label(self._config.tts_model_path, label)), index)
        index = self._tts_speaker_combo.findData(self._config.tts_speaker_id)
        self._tts_speaker_combo.setCurrentIndex(max(0, index))
        self._tts_speaker_combo.blockSignals(False)
        self._refresh_reference_controls()

    def _refresh_reference_controls(self):
        from pathlib import Path

        if not hasattr(self, "_clone_row"):
            return
        multilingual = self._config.translation_backend == TranslationBackend.LLM and bool(
            clone_language(self._config.llm.target_language)
        )
        clone = Path(self._config.tts_model_path).expanduser().is_dir()
        clone_voice = self._config.clone_voice
        self._tts_preset_panel.setVisible(not multilingual)
        self._tts_clone_model_label.setVisible(multilingual)
        if multilingual:
            model_text = tr("Voice model: Qwen3-TTS Base (local clone)")
            if clone_voice is None:
                model_text += tr("\nSet the local clone server and select a recording to enable speech.")
            self._tts_clone_model_label.setText(model_text)
        self._clone_server_row.setVisible(multilingual)
        self._clone_row.setVisible(multilingual or clone)
        self._tts_reference_label.setText(
            tr("Clone from audio:") if multilingual else tr("Clone from audio (optional):")
        )
        self._tts_reference_edit.setPlaceholderText(
            tr("Select a WAV or MP3 recording") if multilingual else tr("No recording: use the selected Speaker preset")
        )
        self._tts_reference_edit.setToolTip(
            tr("Use a clean WAV or MP3 recording of about 3–10 seconds.")
            if multilingual
            else tr("Clear this field to use a preset. No transcript is required.")
        )
        self._tts_speaker_combo.setEnabled(not (clone and self._config.tts_reference_audio))
        self._tts_rate_spin.setEnabled(not multilingual)
        self._preview_voice_btn.setEnabled(not multilingual or clone_voice is not None)

    def _browse_reference_audio(self):
        formats = "Audio (*.wav *.mp3)" if not self._clone_server_row.isHidden() else "Audio (*.wav *.flac *.mp3 *.ogg)"
        path, _ = QFileDialog.getOpenFileName(self, tr("Select a local voice reference"), "", formats)
        if path:
            self._tts_reference_edit.setText(path)
            self._on_reference_audio_changed()

    def _on_reference_audio_changed(self):
        self._config.tts_reference_audio = self._tts_reference_edit.text().strip()
        self._on_speech_settings_changed()

    def _on_clone_url_changed(self):
        value = self._tts_clone_url_edit.text().strip()
        self._config.tts_clone_url = local_clone_url(value)
        self._on_speech_settings_changed()
        if value and not self._config.tts_clone_url:
            self._show_speech_status("Use a loopback address such as http://127.0.0.1:17867.")

    def _show_speech_status(self, message: str):
        self._tts_status.setText(tr(message))
        self._tts_status.setVisible(bool(message))

    def _on_voice_model_changed(self):
        self._config.tts_model_path = self._tts_voice_combo.currentData() or ""
        self._config.tts_speaker_id = 0
        self._tts_model_edit.setText(self._config.tts_model_path)
        self._refresh_speakers()
        self._on_speech_settings_changed()

    def _on_speech_settings_changed(self):
        self._config.tts_speaker_id = self._tts_speaker_combo.currentData() or 0
        self._config.tts_rate = self._tts_rate_spin.value()
        self._speech.stop(reset=True, reason="voice settings changed")
        self._sync_output_selection()
        self._refresh_reference_controls()
        self._update_pair_hint()
        self._speech.set_enabled(self._config.live_speech_enabled)
        if self._config.live_speech_enabled:
            self._speech.prepare(self._config.speech_voice_settings[0])
        if self._capturing and not self._paused:
            self._show_overlay()
        self._config.save()

    def _on_speech_volume_changed(self, percent):
        self._config.tts_volume = percent
        self._speech.set_volume(percent)
        self._config.save()

    def _on_regions_ready(self, regions: list, generation: int):
        """Handle translated regions (inplace mode)."""
        if (
            self._capturing
            and generation == self._capture_generation
            and not self._paused
            and self._config.effective_output_mode != OutputMode.TTS
        ):
            # Get content offset from capture (accounts for window decorations)
            content_offset = (0, 0)
            if self._capture:
                content_offset = self._capture.get_content_offset()
            self._inplace_overlay.set_regions(regions, content_offset)

    def _on_ocr_results_ready(self, results: list, generation: int):
        """Handle raw OCR results (preview boxes and the OCR config dialog)."""
        if not self._capturing or generation != self._capture_generation:
            return
        self._last_ocr_results = results
        if self._ocr_config_dialog:
            self._ocr_config_dialog.update_ocr_results(results)

    # Settings handlers
    def _on_font_size_changed(self, value: int):
        self._config.font_size = value
        self._font_label.setText(f"{value}pt")
        self._banner_overlay.set_font_size(value)
        self._inplace_overlay.set_font_size(value)
        self._update_style_sample()

    def _on_opacity_changed(self, value: int):
        opacity = value / 100.0
        self._config.background_opacity = opacity
        self._opacity_label.setText(f"{value}%")
        self._banner_overlay.set_opacity(opacity)
        self._inplace_overlay.set_opacity(opacity)
        self._update_style_sample()

    def _pick_font_family(self):
        # Initialize dialog with current font
        if self._config.font_family:
            initial_font = QFont(self._config.font_family)
        else:
            initial_font = QFont()
        initial_font.setPointSize(self._config.font_size)

        ok, font = QFontDialog.getFont(initial_font, self)
        if ok:
            font_family = font.family()
            self._config.font_family = font_family
            self._font_family_btn.setText(font_family)
            self._banner_overlay.set_font_family(font_family)
            self._inplace_overlay.set_font_family(font_family)
            self._update_style_sample()

    def _pick_font_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            hex_color = color.name()
            self._config.font_color = hex_color
            self._font_color_btn.setStyleSheet(f"background-color: {hex_color};")
            self._banner_overlay.set_colors(hex_color, self._config.background_color)
            self._inplace_overlay.set_colors(hex_color, self._config.background_color)
            self._update_style_sample()

    def _pick_bg_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            hex_color = color.name()
            self._config.background_color = hex_color
            self._bg_color_btn.setStyleSheet(f"background-color: {hex_color};")
            self._banner_overlay.set_colors(self._config.font_color, hex_color)
            self._inplace_overlay.set_colors(self._config.font_color, hex_color)
            self._update_style_sample()

    def _selected_ocr_region(self):
        if self._ocr_config_dialog:
            return self._ocr_config_dialog.get_scan_region()
        return self._config.get_ocr_region(self._current_window_title)

    def _prepare_ocr_frame(self, frame):
        """Crop before inference; keep original capture coordinates for overlays."""
        region = self._selected_ocr_region()
        if region is None:
            return self._apply_exclusion_zones(frame), (0, 0)
        regions = normalize_ocr_regions(region)
        if not regions:
            return None, (0, 0)  # Selected-box mode with no box never scans outside it.
        height, width = frame.shape[:2]
        boxes = []
        for box in regions:
            left, top = int(box["x"] * width), int(box["y"] * height)
            right = min(width, round((box["x"] + box["width"]) * width))
            bottom = min(height, round((box["y"] + box["height"]) * height))
            if right > left and bottom > top:
                boxes.append((left, top, right, bottom))
        if not boxes:
            return None, (0, 0)
        left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
        right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
        cropped = frame[top:bottom, left:right]
        if len(boxes) == 1:
            return cropped, (left, top)
        # Preserve reading order and overlay coordinates. Mask the spaces
        # between boxes and scan overlapping pixels only once.
        masked = np.zeros_like(cropped)
        for x1, y1, x2, y2 in boxes:
            selection = np.s_[y1 - top : y2 - top, x1 - left : x2 - left]
            masked[selection] = cropped[selection]
        return masked, (left, top)

    def _prepare_character_ocr(self, full_frame, dialogue_frame, origin):
        """Separate name OCR from dialogue, including outside the selected box."""
        if self._ocr_config_dialog:
            return dialogue_frame, None
        profile = self._config.get_character_voices(self._current_window_title)
        if not profile.enabled or not profile.name_region:
            self._detected_character = ""
            if hasattr(self, "_character_status") and self._character_status.text() != tr("Character voices off"):
                self._character_status.setText(tr("Character voices off"))
            return dialogue_frame, None
        region = profile.name_region
        height, width = full_frame.shape[:2]
        left, top = int(region["x"] * width), int(region["y"] * height)
        right = min(width, round((region["x"] + region["width"]) * width))
        bottom = min(height, round((region["y"] + region["height"]) * height))
        name_frame = full_frame[top:bottom, left:right].copy()
        x1, y1 = max(0, left - origin[0]), max(0, top - origin[1])
        x2 = min(dialogue_frame.shape[1], right - origin[0])
        y2 = min(dialogue_frame.shape[0], bottom - origin[1])
        if x2 > x1 and y2 > y1:
            dialogue_frame = dialogue_frame.copy()
            dialogue_frame[y1:y2, x1:x2] = 0
        if self._config.translation_backend != TranslationBackend.LLM and (
            not self._config.tts_enabled or self._config.output_mode == OutputMode.OVERLAY
        ):
            return dialogue_frame, None
        return dialogue_frame, (self._current_window_title, profile, name_frame)

    def _apply_exclusion_zones(self, frame):
        """Apply exclusion zones to frame by masking out excluded regions.

        Args:
            frame: BGRA numpy array (H, W, 4)

        Returns:
            Frame with exclusion zones blacked out.
        """
        if not self._current_window_title:
            return frame

        # Use dialog's zones if open (for live editing), otherwise use config
        if self._ocr_config_dialog:
            zones = self._ocr_config_dialog.get_zones()
        else:
            zones = self._config.get_exclusion_zones(self._current_window_title)

        if not zones:
            return frame

        # Make a copy to avoid modifying the original
        masked_frame = frame.copy()
        h, w = frame.shape[:2]

        for zone in zones:
            # Convert normalized coordinates to pixel coordinates
            x = int(zone.get("x", 0) * w)
            y = int(zone.get("y", 0) * h)
            zone_w = int(zone.get("width", 0) * w)
            zone_h = int(zone.get("height", 0) * h)

            # Clamp to frame bounds
            x = max(0, min(x, w))
            y = max(0, min(y, h))
            x2 = max(0, min(x + zone_w, w))
            y2 = max(0, min(y + zone_h, h))

            # Black out the region
            masked_frame[y:y2, x:x2] = 0

        return masked_frame

    def _open_ocr_config(self):
        """Open the OCR configuration dialog."""
        if not self._capturing or self._last_frame is None:
            return

        # Get current settings for this window
        current_zones = self._config.get_exclusion_zones(self._current_window_title)
        current_confidence = self._config.get_ocr_confidence(self._current_window_title)
        current_region = self._config.get_ocr_region(self._current_window_title)
        interrupted_speech = bool(self._speech._busy)

        # Create and show dialog
        dialog = OCRConfigDialog(
            self,
            window_title=self._current_window_title,
            initial_confidence=current_confidence,
            initial_zones=current_zones,
            initial_region=current_region,
        )

        # Connect confidence changes to worker (for live preview)
        dialog.confidence_changed.connect(self._process_worker.set_confidence_threshold)

        # Set up live frame updates
        self._ocr_config_dialog = dialog
        self._capture_generation += 1
        self._process_worker.discard_pending_frames()
        self._speech.stop(reset=interrupted_speech, reason="OCR region editing")

        # Update with current frame
        dialog.update_frame(self._last_frame)
        dialog.apply_pending_zones()

        # Show dialog (modal)
        accepted = dialog.exec()

        if accepted:
            # Save zones and confidence to config
            new_zones = dialog.get_zones()
            new_confidence = dialog.get_confidence()
            new_region = dialog.get_scan_region()
            if not interrupted_speech and (
                new_zones != current_zones or new_confidence != current_confidence or new_region != current_region
            ):
                self._speech.stop(reset=True, reason="OCR settings changed")

            self._config.set_exclusion_zones(self._current_window_title, new_zones)
            self._config.set_ocr_region(self._current_window_title, new_region)
            self._config.set_ocr_confidence(self._current_window_title, new_confidence)
            self._config.save()

            logger.debug(
                "OCR config updated",
                window=self._current_window_title,
                zones=len(new_zones),
                confidence=f"{new_confidence:.0%}",
            )
        else:
            # User cancelled - restore original confidence
            self._process_worker.set_confidence_threshold(current_confidence)

        self._ocr_config_dialog = None
        # Reject in-flight preview work. Unchanged settings retain the completed
        # TTS guard; changed settings or an interrupted line can be read anew.
        self._capture_generation += 1
        self._process_worker.discard_pending_frames()
        self._last_ocr_results = []

    def render_api_speech(self, text, voice_settings, cancelled):
        return self._speech.render_for_api(text, voice_settings, cancelled)

    def is_capturing(self) -> bool:
        return self._capturing

    def get_banner_position(self) -> tuple[int, int]:
        """Get current banner overlay position."""
        return self._banner_overlay.get_position()

    def cleanup(self):
        """Clean up resources before closing."""
        if hasattr(self, "_updates_page"):
            self._updates_page.shutdown()
        self._stop_capture()
        self._speech.close()
        self._keyboard_listener.stop()
        self._process_worker.stop()
        self._banner_overlay.close()
        self._inplace_overlay.close()
        if self._history_overlay is not None:
            self._history_overlay.close()
