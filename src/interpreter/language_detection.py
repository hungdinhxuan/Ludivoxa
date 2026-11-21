"""Cheap offline script identification for recognized game text."""

from .languages import contains_hangul, contains_japanese, contains_kana


def detect_language(text: str) -> str:
    """Preserve the Japanese default for ambiguous Han. Manual selection wins."""
    if contains_hangul(text) and not contains_kana(text):
        return "Korean"
    return "Japanese" if contains_japanese(text) else "English"
