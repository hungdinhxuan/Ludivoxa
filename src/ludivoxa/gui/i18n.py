"""Desktop interface strings. Keep engine identifiers and content in their original language."""

from ._vi import VIETNAMESE

_language = "en"


def set_interface_language(language: str) -> None:
    global _language
    _language = language


def tr(text: str, *values) -> str:
    if _language == "vi":
        text = VIETNAMESE.get(text, text)
    return text.format(*values) if values else text
