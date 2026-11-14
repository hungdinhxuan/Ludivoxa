"""Cheap offline English/Japanese identification for recognized game text."""

from .languages import contains_japanese


def detect_language(text: str) -> str:
    """Han or kana indicates Japanese; otherwise use English. Manual selection wins."""
    return "Japanese" if contains_japanese(text) else "English"
