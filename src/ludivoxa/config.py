"""Configuration management for Ludivoxa."""

import math
import os
import unicodedata
from dataclasses import asdict, dataclass, fields
from dataclasses import field as dataclass_field
from enum import Enum
from pathlib import Path

import yaml

from . import log
from .languages import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    SOURCE_LANGUAGES,
    is_vietnamese_target,
    normalize_source_language,
)
from .local_tts import LocalCloneVoice, clone_language, local_clone_url
from .paths import get_config_dir, get_legacy_config_dir


class OverlayMode(str, Enum):
    """Overlay display mode."""

    BANNER = "banner"
    INPLACE = "inplace"


class OutputMode(str, Enum):
    OVERLAY = "overlay"
    TTS = "tts"
    BOTH = "both"


class TranslationBackend(str, Enum):
    """Which engine translates the OCR output."""

    SUGOI = "sugoi"  # Built-in offline Sugoi V4 (CTranslate2)
    LLM = "llm"  # Ollama or any OpenAI-compatible chat endpoint


LLM_PROVIDERS = ("ollama", "openai")


class OCRBackend(str, Enum):
    """Which engine extracts text from captured frames."""

    MEIKI = "meiki"  # Built-in offline MeikiOCR
    OWOCR = "owocr"  # External owocr process reached over a websocket
    PADDLE = "paddle"  # Optional local PaddleOCR 3 inference models


@dataclass
class PaddleSettings:
    """Local inference directories; one recognition model selected for the game."""

    detection_model_dir: str = ""
    recognition_model_dir: str = ""
    device: str = "cpu"
    cpu_threads: int = 2
    max_side: int = 960

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        values = {}
        for key in ("detection_model_dir", "recognition_model_dir", "device"):
            if isinstance(data.get(key), str):
                values[key] = data[key]
        for key in ("cpu_threads", "max_side"):
            try:
                value = int(data.get(key, getattr(defaults, key)))
                values[key] = value if value > 0 else getattr(defaults, key)
            except (ValueError, TypeError, OverflowError):
                pass
        return cls(**values)

    def to_dict(self):
        return asdict(self)


@dataclass
class OwocrSettings:
    """Connection settings for the owocr websocket OCR backend."""

    url: str = "ws://127.0.0.1:7331"
    timeout: float = 10.0  # Seconds to wait for each frame's result

    @classmethod
    def from_dict(cls, data: dict | None) -> "OwocrSettings":
        """Build settings from a YAML mapping, ignoring unknown keys and bad types."""
        defaults = cls()
        if not isinstance(data, dict):
            return defaults
        kwargs = {}
        for field in fields(cls):
            if field.name not in data:
                continue
            value = data[field.name]
            default = getattr(defaults, field.name)
            try:
                if isinstance(default, float):
                    kwargs[field.name] = float(value)
                else:
                    kwargs[field.name] = str(value) if value is not None else default
            except (TypeError, ValueError):
                logger.warning("invalid owocr setting, using default", key=field.name, value=value)

        if "timeout" in kwargs and not (0 < kwargs["timeout"] < float("inf")):
            logger.warning("owocr timeout must be a positive number, using default", timeout=kwargs["timeout"])
            del kwargs["timeout"]
        return cls(**kwargs)

    def to_dict(self) -> dict:
        """Plain-type mapping for YAML output."""
        return asdict(self)


@dataclass
class LLMSettings:
    """Connection and prompt settings for the LLM endpoint translation backend."""

    provider: str = "ollama"  # "ollama" (native API) or "openai" (OpenAI-compatible /v1)
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""
    api_key: str = ""  # Stored in plain text in config.yml; only needed for hosted services
    target_language: str = DEFAULT_TARGET_LANGUAGE
    system_prompt: str | None = None  # None = built-in default; may contain {target_language}
    context_lines: int = 3  # Previous lines replayed as conversation history
    timeout: float = 30.0  # Seconds per request
    # Extra JSON fields merged into every chat request, for provider/model-specific
    # controls the app does not know about (e.g. {"reasoning_effort": "none"} on an
    # OpenAI-compatible server, or {"options": {"num_ctx": 2048}} on Ollama).
    request_options: dict = dataclass_field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "LLMSettings":
        """Build settings from a YAML mapping, ignoring unknown keys and bad types."""
        defaults = cls()
        if not isinstance(data, dict):
            return defaults
        kwargs = {}
        for field in fields(cls):
            if field.name not in data:
                continue
            value = data[field.name]
            default = getattr(defaults, field.name)
            try:
                if field.name == "system_prompt":
                    kwargs[field.name] = str(value) if value else None
                elif field.name == "request_options":
                    if value is None:
                        continue
                    if not isinstance(value, dict):
                        raise TypeError("request_options must be a mapping")
                    kwargs[field.name] = {str(k): v for k, v in value.items()}
                elif isinstance(default, bool):
                    kwargs[field.name] = bool(value)
                elif isinstance(default, int):
                    kwargs[field.name] = int(value)
                elif isinstance(default, float):
                    kwargs[field.name] = float(value)
                else:
                    kwargs[field.name] = str(value) if value is not None else default
            except (TypeError, ValueError):
                logger.warning("invalid llm setting, using default", key=field.name, value=value)

        # Values that parse but would break the HTTP client or route to the wrong API
        if kwargs.get("provider") not in (None, *LLM_PROVIDERS):
            logger.warning("unsupported llm provider, using default", provider=kwargs["provider"])
            del kwargs["provider"]
        if "timeout" in kwargs and not (0 < kwargs["timeout"] < float("inf")):
            logger.warning("llm timeout must be a positive number, using default", timeout=kwargs["timeout"])
            del kwargs["timeout"]
        if "context_lines" in kwargs and kwargs["context_lines"] < 0:
            logger.warning("llm context_lines cannot be negative, using default", value=kwargs["context_lines"])
            del kwargs["context_lines"]
        return cls(**kwargs)

    def to_dict(self) -> dict:
        """Plain-type mapping for YAML output (omits the prompt when it is the default)."""
        data = asdict(self)
        if self.system_prompt is None:
            del data["system_prompt"]
        if not self.request_options:
            del data["request_options"]
        return data


logger = log.get_logger()


def normalize_ocr_region(value):
    """Validate and clip a normalized rectangle to the capture frame."""
    if not isinstance(value, dict):
        return None
    try:
        x, y, width, height = (float(value[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0:
        return None
    right, bottom = min(1.0, x + width), min(1.0, y + height)
    x, y = max(0.0, x), max(0.0, y)
    if right <= x or bottom <= y:
        return None
    return {"x": x, "y": y, "width": right - x, "height": bottom - y}


def normalize_ocr_regions(value) -> list[dict]:
    """Accept both legacy rectangles and lists of selected boxes."""
    entries = value if isinstance(value, list) else [value]
    return [region for entry in entries if (region := normalize_ocr_region(entry)) is not None]


def ignore_phrase_key(text: str) -> str:
    """Match a complete OCR phrase, allowing for case and OCR spacing differences."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class GameProfile:
    """Overrides for one capture source. Bounds, confidence and voices use their existing maps."""

    source_language: str = ""
    system_prompt: str = ""
    ignore_phrases: tuple[str, ...] = ()
    prompt_mode: str = "append"
    ignore_keys: frozenset[str] = dataclass_field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "ignore_keys", frozenset(map(ignore_phrase_key, self.ignore_phrases)))

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return cls()
        language = data.get("source_language")
        key = language.strip().casefold() if isinstance(language, str) else ""
        source = next((name for name in SOURCE_LANGUAGES if name.casefold() == key), "")
        prompt = data.get("system_prompt")
        prompt = prompt.strip() if isinstance(prompt, str) else ""
        prompt_mode = data.get("prompt_mode")
        if prompt_mode not in ("append", "override"):
            # Saved prompts without a mode used to replace the global prompt.
            prompt_mode = "override" if prompt else "append"
        phrases = data.get("ignore_phrases")
        return cls(
            source,
            prompt,
            tuple(dict.fromkeys(phrase.strip() for phrase in phrases if isinstance(phrase, str) and phrase.strip()))
            if isinstance(phrases, list)
            else (),
            prompt_mode,
        )

    def to_dict(self):
        return {
            key: value
            for key, value in (
                ("source_language", self.source_language),
                ("system_prompt", self.system_prompt),
                ("prompt_mode", self.prompt_mode if self.system_prompt or self.prompt_mode == "override" else ""),
                ("ignore_phrases", list(self.ignore_phrases)),
            )
            if value
        }


CHARACTER_GENDERS = ("male", "female", "nonbinary", "unknown")


def character_name(text: str) -> str:
    """Normalize a name label without guessing identity or gender."""
    return " ".join(unicodedata.normalize("NFKC", str(text)).split()).strip(' :：「」『』【】[]"“”')[:64]


@dataclass
class CharacterVoices:
    """Optional name-area OCR and explicit preset assignments for one game."""

    enabled: bool = False
    name_region: dict | None = None
    model_path: str = ""
    voices: dict[str, int] = dataclass_field(default_factory=dict)
    genders: dict[str, str] = dataclass_field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return cls()
        voices = {}
        for name, value in (data.get("voices", {}) if isinstance(data.get("voices"), dict) else {}).items():
            if not isinstance(name, str) or isinstance(value, bool):
                continue
            try:
                index = int(value)
            except (ValueError, TypeError, OverflowError):
                continue
            if character_name(name) and index >= 0 and str(index) == str(value):
                voices[character_name(name)] = index
        genders = data.get("genders")
        return cls(
            enabled=data.get("enabled") is True,
            name_region=normalize_ocr_region(data.get("name_region")),
            model_path=data.get("model_path", "") if isinstance(data.get("model_path"), str) else "",
            voices=voices,
            genders={
                character_name(name): value
                for name, value in genders.items()
                if isinstance(name, str) and character_name(name) and value in CHARACTER_GENDERS
            }
            if isinstance(genders, dict)
            else {},
        )

    def to_dict(self):
        return asdict(self)

    def matches_model(self, model_path):
        if not self.model_path:
            return False
        if self.model_path == model_path:
            return True
        try:
            return os.path.normcase(str(Path(self.model_path).expanduser().resolve())) == os.path.normcase(
                str(Path(model_path).expanduser().resolve())
            )
        except (OSError, ValueError, RuntimeError):
            return False

    def speaker_for(self, name, model_path):
        if not self.matches_model(model_path):
            return None
        key = character_name(name).casefold()
        return next((index for label, index in self.voices.items() if label.casefold() == key), None)

    def gender_for(self, name):
        key = character_name(name).casefold()
        return next((value for label, value in self.genders.items() if label.casefold() == key), None)


class Config:
    """Application configuration."""

    DEFAULT_WINDOW_TITLE = "Snes9x"

    # Default hotkeys
    DEFAULT_HOTKEYS = {
        "toggle_overlay": "f8",
        "switch_mode": "m",
        "increase_font": "=",
        "decrease_font": "-",
        "quit": "q",
    }

    # Default OCR confidence threshold
    DEFAULT_OCR_CONFIDENCE = 0.6

    def __init__(
        self,
        window_title: str = "Snes9x",
        ocr_confidence: float = 0.6,
        overlay_mode: OverlayMode = OverlayMode.BANNER,
        font_family: str | None = None,
        font_size: int = 26,
        font_color: str = "#FFFFFF",
        background_color: str = "#404040",
        background_opacity: float = 0.8,
        hotkeys: dict | None = None,
        config_path: str | None = None,
        banner_x: int | None = None,
        banner_y: int | None = None,
        exclusion_zones: dict | None = None,
        ocr_confidence_per_window: dict | None = None,
        translation_backend: TranslationBackend = TranslationBackend.SUGOI,
        llm: LLMSettings | None = None,
        ocr_backend: OCRBackend = OCRBackend.MEIKI,
        owocr: OwocrSettings | None = None,
        source_language: str = DEFAULT_SOURCE_LANGUAGE,
        video_device: str = "",
        video_device_id: str = "",
        paddle: PaddleSettings | None = None,
        tts_enabled: bool = True,
        tts_model_path: str = "",
        output_mode: str | OutputMode | None = None,
        tts_speaker_id: int = 0,
        tts_rate: float = 1.0,
        tts_reference_audio: str = "",
        ocr_regions: dict | None = None,
        update_check_on_startup: bool = False,
        character_voices: dict | None = None,
        history_overlay_enabled: bool = True,
        game_profiles: dict | None = None,
        hide_stale_overlay: bool = False,
        tts_volume: int = 100,
        tts_clone_url: str = "",
        interface_language: str = "en",
    ):
        self.window_title = window_title
        self.interface_language = interface_language if interface_language in ("en", "vi") else "en"
        self.update_check_on_startup = update_check_on_startup is True
        self.history_overlay_enabled = history_overlay_enabled is True
        self.hide_stale_overlay = hide_stale_overlay is True
        # Video device (capture card, webcam) captured last; empty means a window was captured last.
        # The name is what the user sees and what keys exclusion zones; the id tells two devices
        # of the same model apart (it is matched first, the name is the fallback).
        self.video_device = video_device
        self.video_device_id = video_device_id
        self.translation_backend = translation_backend
        self.llm = llm if llm is not None else LLMSettings()
        self.ocr_backend = ocr_backend
        self.owocr = owocr if owocr is not None else OwocrSettings()
        self.paddle = paddle if paddle is not None else PaddleSettings()
        self.tts_enabled = tts_enabled
        self.tts_model_path = tts_model_path
        self.tts_reference_audio = tts_reference_audio
        self.tts_clone_url = tts_clone_url
        try:
            self.output_mode = (
                OutputMode(output_mode) if output_mode else (OutputMode.BOTH if tts_enabled else OutputMode.OVERLAY)
            )
        except ValueError:
            self.output_mode = OutputMode.BOTH if tts_enabled else OutputMode.OVERLAY
        self.tts_enabled = self.output_mode != OutputMode.OVERLAY
        try:
            self.tts_speaker_id = max(0, int(tts_speaker_id))
            self.tts_rate = min(2.0, max(0.75, float(tts_rate)))
        except (ValueError, TypeError, OverflowError):
            self.tts_speaker_id, self.tts_rate = 0, 1.0
        try:
            self.tts_volume = min(100, max(0, int(tts_volume)))
        except (ValueError, TypeError, OverflowError):
            self.tts_volume = 100
        self.source_language = source_language  # language of the game's text, see languages.SOURCE_LANGUAGES
        self.ocr_confidence = ocr_confidence  # Global default
        self.overlay_mode = overlay_mode
        self.font_family = font_family  # None = system default
        self.font_size = font_size
        self.font_color = font_color
        self.background_color = background_color
        self.background_opacity = background_opacity
        self.hotkeys = hotkeys if hotkeys is not None else self.DEFAULT_HOTKEYS.copy()
        self.config_path = config_path
        self.banner_x = banner_x
        self.banner_y = banner_y
        # Exclusion zones per window title: {"window_title": [{"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1}, ...]}
        self.exclusion_zones = exclusion_zones if exclusion_zones is not None else {}
        # A selected box overrides exclusions for that window; absent = legacy full-window mode.
        self.ocr_regions = {}
        if isinstance(ocr_regions, dict):
            for title, value in ocr_regions.items():
                boxes = normalize_ocr_regions(value)
                if boxes:
                    self.ocr_regions[str(title)] = boxes[0] if len(boxes) == 1 else boxes
        # Per-window OCR confidence: {"window_title": 0.55, ...}
        self.ocr_confidence_per_window = ocr_confidence_per_window if ocr_confidence_per_window is not None else {}
        self.character_voices = {
            str(title): CharacterVoices.from_dict(value)
            for title, value in (character_voices.items() if isinstance(character_voices, dict) else [])
        }
        self.game_profiles = {
            title: profile
            for title, value in (game_profiles.items() if isinstance(game_profiles, dict) else [])
            if isinstance(title, str) and title and (profile := GameProfile.from_dict(value)).to_dict()
        }

    @property
    def clone_voice(self) -> LocalCloneVoice | None:
        if self.translation_backend == TranslationBackend.LLM and self.tts_clone_url and self.tts_reference_audio:
            language = clone_language(self.llm.target_language)
            endpoint = local_clone_url(self.tts_clone_url)
            if endpoint and language:
                return LocalCloneVoice(endpoint, *language)
        return None

    @property
    def speech_voice_settings(self) -> tuple[str | LocalCloneVoice, int, float, str]:
        if voice := self.clone_voice:
            return voice, 0, 1.0, self.tts_reference_audio
        return self.tts_model_path, self.tts_speaker_id, self.tts_rate, self.tts_reference_audio

    @property
    def target_supports_speech(self) -> bool:
        return (
            self.translation_backend != TranslationBackend.LLM
            or is_vietnamese_target(self.llm.target_language)
            or self.clone_voice is not None
        )

    @property
    def effective_output_mode(self) -> OutputMode:
        """Use the overlay for LLM targets outside the bundled voices' language."""
        return self.output_mode if self.target_supports_speech else OutputMode.OVERLAY

    @property
    def live_speech_enabled(self) -> bool:
        return self.tts_enabled and self.effective_output_mode != OutputMode.OVERLAY

    @classmethod
    def load(cls, config_path: str | None = None) -> "Config":
        """Load configuration from YAML file.

        Args:
            config_path: Path to config file. If None, looks for config.yml
                        in common locations.

        Returns:
            Config instance with loaded values.
        """
        migrated_path = None
        if config_path is None:
            # Look for config in common locations
            search_paths = [
                Path("config.yml"),
                Path(__file__).parent.parent / "config.yml",
                get_config_dir() / "config.yml",
            ]
            for path in search_paths:
                if path.exists():
                    config_path = str(path)
                    break
            if config_path is None:
                legacy = get_legacy_config_dir() / "config.yml"
                if legacy.is_file():
                    config_path = str(legacy)
                    migrated_path = str(get_config_dir() / legacy.name)

        if config_path and os.path.exists(config_path):
            # Resolve to absolute path for clear display
            config_path = str(Path(config_path).resolve())
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # Load hotkeys with defaults for any missing keys
            hotkeys_data = data.get("hotkeys", {})
            hotkeys = cls.DEFAULT_HOTKEYS.copy()
            hotkeys.update(hotkeys_data)

            # Parse overlay_mode from string to enum
            mode_str = data.get("overlay_mode", "banner")
            try:
                overlay_mode = OverlayMode(mode_str)
            except ValueError:
                logger.warning("invalid overlay_mode, using banner", mode=mode_str)
                overlay_mode = OverlayMode.BANNER

            backend_str = data.get("translation_backend", TranslationBackend.SUGOI.value)
            try:
                translation_backend = TranslationBackend(backend_str)
            except ValueError:
                logger.warning("invalid translation_backend, using sugoi", backend=backend_str)
                translation_backend = TranslationBackend.SUGOI

            ocr_backend_str = data.get("ocr_backend", OCRBackend.MEIKI.value)
            try:
                ocr_backend = OCRBackend(ocr_backend_str)
            except ValueError:
                logger.warning("invalid ocr_backend, using meiki", backend=ocr_backend_str)
                ocr_backend = OCRBackend.MEIKI

            source_language_str = data.get("source_language", DEFAULT_SOURCE_LANGUAGE)
            source_language = normalize_source_language(source_language_str)
            if source_language.lower() != str(source_language_str).strip().lower():
                logger.warning(
                    "unsupported source_language, using default",
                    value=source_language_str,
                    supported=", ".join(SOURCE_LANGUAGES),
                )

            return cls(
                window_title=data.get("window_title", cls.DEFAULT_WINDOW_TITLE),
                interface_language=data.get("interface_language", "en"),
                update_check_on_startup=data.get("update_check_on_startup", False) is True,
                history_overlay_enabled=data.get("history_overlay_enabled", True) is True,
                hide_stale_overlay=data.get("hide_stale_overlay", False) is True,
                ocr_confidence=float(data.get("ocr_confidence", cls.DEFAULT_OCR_CONFIDENCE)),
                overlay_mode=overlay_mode,
                font_family=data.get("font_family"),  # None = system default
                font_size=int(data.get("font_size", 26)),
                font_color=data.get("font_color", "#FFFFFF"),
                background_color=data.get("background_color", "#404040"),
                background_opacity=float(data.get("background_opacity", 0.8)),
                hotkeys=hotkeys,
                config_path=migrated_path or config_path,
                banner_x=data.get("banner_x"),
                banner_y=data.get("banner_y"),
                exclusion_zones=data.get("exclusion_zones", {}),
                ocr_regions=data.get("ocr_regions"),
                character_voices=data.get("character_voices"),
                game_profiles=data.get("game_profiles"),
                ocr_confidence_per_window=data.get("ocr_confidence_per_window", {}),
                translation_backend=translation_backend,
                llm=LLMSettings.from_dict(data.get("llm")),
                ocr_backend=ocr_backend,
                owocr=OwocrSettings.from_dict(data.get("owocr")),
                paddle=PaddleSettings.from_dict(data.get("paddle")),
                tts_enabled=data.get("tts_enabled", True) is True,
                tts_model_path=str(data.get("tts_model_path") or ""),
                tts_reference_audio=str(data.get("tts_reference_audio") or ""),
                tts_clone_url=str(data.get("tts_clone_url") or ""),
                output_mode=data.get("output_mode"),
                tts_speaker_id=data.get("tts_speaker_id", 0),
                tts_rate=data.get("tts_rate", 1.0),
                tts_volume=data.get("tts_volume", 100),
                source_language=source_language,
                video_device=str(data.get("video_device") or ""),
                video_device_id=str(data.get("video_device_id") or ""),
            )

        # No config file found - create default in home directory
        config = cls()
        config._create_default_config()
        return config

    def _create_default_config(self) -> None:
        """Create a default config file in the user's home directory."""
        config_dir = get_config_dir()
        config_path = config_dir / "config.yml"

        # Don't overwrite if it already exists
        if config_path.exists():
            return

        # Create directory if needed
        config_dir.mkdir(parents=True, exist_ok=True)

        # Write default config with comments
        default_config = """# Window to capture (partial title match)
window_title: "Snes9x"

# Video device to capture instead of a window (capture card or webcam, by the name shown in
# the source list). Banner mode only. Leave empty to capture a window.
# video_device: ""

# OCR confidence threshold (0.0-1.0)
# Filters out low-confidence text detection
ocr_confidence: 0.6

# Overlay mode: "banner" (subtitle bar) or "inplace" (over game text)
overlay_mode: banner

# Subtitle appearance
font_size: 26
font_color: "#FFFFFF"
background_color: "#404040"
background_opacity: 0.8  # 0.0 (transparent) to 1.0 (opaque)

# Translation engine: "sugoi" (built-in, offline) or "llm" (Ollama / OpenAI-compatible endpoint)
# The llm block is only used when translation_backend is "llm". Configure it from the app's
# Translation panel, or by hand:
# translation_backend: sugoi
# llm:
#   provider: ollama            # "ollama" or "openai"
#   base_url: http://127.0.0.1:11434
#   model: ""                   # e.g. "gemma3:4b" (run "ollama pull gemma3:4b" first)
#   api_key: ""                 # only for hosted services
#   target_language: Vietnamese
#   context_lines: 3            # previous lines sent as context
#   timeout: 30                 # seconds per request
#   request_options: {}         # extra JSON fields for every request, e.g. {reasoning_effort: none}

# OCR engine: "meiki", "paddle" (local offline models), or "owocr" (external process, see README)
# The owocr block is only used when ocr_backend is "owocr". Configure it from the app's
# OCR panel, or by hand:
# ocr_backend: meiki
# owocr:
#   url: ws://127.0.0.1:7331
#   timeout: 10                 # seconds to wait for each frame's result

# Language of the game's text. MeikiOCR and Sugoi V4 only handle Japanese; other languages
# English needs PaddleOCR/owocr and the LLM translation engine.
# Auto detects English/Japanese after OCR. Legacy source-language values remain compatible.
source_language: Japanese

# Hotkeys - single characters or special key names
# Special keys: f1-f12, escape, space, enter, tab, backspace, delete,
#               insert, home, end, page_up, page_down, up, down, left, right
hotkeys:
  toggle_overlay: "f8"
  switch_mode: "m"
  increase_font: "="
  decrease_font: "-"
  quit: "q"
"""
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(default_config)

        logger.info("created default config", path=str(config_path))

    def get_exclusion_zones(self, window_title: str) -> list[dict]:
        """Get exclusion zones for a specific window title.

        Args:
            window_title: The window title to get zones for.

        Returns:
            List of zone dicts with x, y, width, height as floats (0.0-1.0).
        """
        return self.exclusion_zones.get(window_title, [])

    def get_ocr_region(self, window_title: str) -> dict | list[dict] | None:
        """Selected translation boxes, or None for full-window/exclusion mode."""
        region = self.ocr_regions.get(window_title)
        if isinstance(region, list):
            return [box.copy() for box in region]
        return region.copy() if region else None

    def get_character_voices(self, window_title: str) -> CharacterVoices:
        profile = self.character_voices.get(window_title)
        return CharacterVoices.from_dict(profile.to_dict()) if profile else CharacterVoices()

    def set_game_profile(self, title: str, profile: GameProfile):
        if profile.to_dict():
            self.game_profiles[title] = profile
        else:
            self.game_profiles.pop(title, None)

    def set_character_voices(self, window_title: str, profile: CharacterVoices):
        self.character_voices[window_title] = CharacterVoices.from_dict(profile.to_dict())

    def set_ocr_region(self, window_title: str, region: dict | list[dict] | None) -> None:
        if region is None:
            self.ocr_regions.pop(window_title, None)
            return
        boxes = normalize_ocr_regions(region)
        if not boxes:
            raise ValueError("Select a non-empty OCR box inside the image.")
        self.ocr_regions[window_title] = boxes[0] if len(boxes) == 1 else boxes

    def set_exclusion_zones(self, window_title: str, zones: list[dict]) -> None:
        """Set exclusion zones for a specific window title.

        Args:
            window_title: The window title to set zones for.
            zones: List of zone dicts with x, y, width, height as floats (0.0-1.0).
        """
        if zones:
            self.exclusion_zones[window_title] = zones
        elif window_title in self.exclusion_zones:
            del self.exclusion_zones[window_title]

    def get_ocr_confidence(self, window_title: str | None = None) -> float:
        """Get OCR confidence for a specific window, or global default.

        Args:
            window_title: The window title to get confidence for. If None, returns global default.

        Returns:
            OCR confidence threshold (0.0-1.0).
        """
        if window_title and window_title in self.ocr_confidence_per_window:
            return self.ocr_confidence_per_window[window_title]
        return self.ocr_confidence

    def set_ocr_confidence(self, window_title: str, confidence: float) -> None:
        """Set OCR confidence for a specific window.

        Args:
            window_title: The window title to set confidence for.
            confidence: OCR confidence threshold (0.0-1.0).
        """
        # Only store if different from global default
        if abs(confidence - self.ocr_confidence) < 0.001:
            # Same as global, remove per-window override
            if window_title in self.ocr_confidence_per_window:
                del self.ocr_confidence_per_window[window_title]
        else:
            self.ocr_confidence_per_window[window_title] = confidence

    def hex_to_rgb(self, hex_color: str) -> tuple[int, int, int]:
        """Convert hex color string to RGB tuple."""
        hex_color = hex_color.lstrip("#")
        if len(hex_color) != 6 or not all(c in "0123456789abcdefABCDEF" for c in hex_color):
            logger.warning("invalid hex color, using white", color=hex_color)
            return (255, 255, 255)
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))

    def save(self, config_path: str | None = None) -> None:
        """Save configuration to YAML file.

        Args:
            config_path: Path to save to. If None, uses the path the config was loaded from,
                        or creates in ~/.ludivoxa/config.yml
        """
        if config_path is None:
            config_path = self.config_path or str(get_config_dir() / "config.yml")
            Path(config_path).parent.mkdir(parents=True, exist_ok=True)

        # Explicitly convert to basic types to avoid Python-specific YAML tags
        data = {
            "window_title": str(self.window_title) if self.window_title else "",
            "ocr_confidence": float(self.ocr_confidence),
            "overlay_mode": self.overlay_mode.value,
            "font_size": int(self.font_size),
            "font_color": str(self.font_color),
            "background_color": str(self.background_color),
            "background_opacity": float(self.background_opacity),
            "hotkeys": {str(k): str(v) for k, v in self.hotkeys.items()},
        }
        # Translation backend; the llm block is only written once the user has touched it
        data["translation_backend"] = self.translation_backend.value
        if self.translation_backend == TranslationBackend.LLM or self.llm != LLMSettings():
            data["llm"] = self.llm.to_dict()
        # OCR backend; same rule for the owocr block
        data["ocr_backend"] = self.ocr_backend.value
        if self.ocr_backend == OCRBackend.PADDLE or self.paddle != PaddleSettings():
            data["paddle"] = self.paddle.to_dict()
        if self.ocr_backend == OCRBackend.OWOCR or self.owocr != OwocrSettings():
            data["owocr"] = self.owocr.to_dict()
        data["source_language"] = str(self.source_language)
        data["interface_language"] = self.interface_language
        data["update_check_on_startup"] = self.update_check_on_startup
        data["history_overlay_enabled"] = self.history_overlay_enabled
        data["hide_stale_overlay"] = self.hide_stale_overlay
        data["tts_enabled"] = self.tts_enabled
        data["tts_model_path"] = self.tts_model_path
        data["tts_reference_audio"] = self.tts_reference_audio
        if self.tts_clone_url:
            data["tts_clone_url"] = self.tts_clone_url
        data["output_mode"] = self.output_mode.value
        data["tts_speaker_id"] = self.tts_speaker_id
        data["tts_rate"] = self.tts_rate
        data["tts_volume"] = self.tts_volume
        # Only written while a video device is the selected source
        if self.video_device:
            data["video_device"] = str(self.video_device)
            if self.video_device_id:
                data["video_device_id"] = str(self.video_device_id)
        # Only save font_family if user has chosen one (None = system default)
        if self.font_family is not None:
            data["font_family"] = str(self.font_family)
        # Only save banner position if it was set (user has moved the banner)
        if self.banner_x is not None:
            data["banner_x"] = int(self.banner_x)
        if self.banner_y is not None:
            data["banner_y"] = int(self.banner_y)
        # Save exclusion zones if any are defined
        # Convert keys to plain strings to avoid Python-specific YAML tags
        if self.exclusion_zones:
            data["exclusion_zones"] = {
                str(k): [{str(zk): float(zv) for zk, zv in zone.items()} for zone in zones]
                for k, zones in self.exclusion_zones.items()
            }
        # Save per-window OCR confidence if any are defined
        if self.ocr_regions:
            data["ocr_regions"] = {str(title): region.copy() for title, region in self.ocr_regions.items()}
        if self.character_voices:
            data["character_voices"] = {title: profile.to_dict() for title, profile in self.character_voices.items()}
        if self.game_profiles:
            data["game_profiles"] = {title: profile.to_dict() for title, profile in self.game_profiles.items()}
        if self.ocr_confidence_per_window:
            data["ocr_confidence_per_window"] = {str(k): float(v) for k, v in self.ocr_confidence_per_window.items()}

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        self.config_path = config_path
        logger.info("config saved", path=config_path)
