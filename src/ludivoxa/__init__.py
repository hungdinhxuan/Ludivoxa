"""Ludivoxa - Offline game OCR, local translation and Vietnamese speech.

This application captures text from a target window, performs OCR using
the existing OCR backends, translates locally, and displays subtitles or
reads Vietnamese speech.
"""

import faulthandler
import signal

# Enable faulthandler to dump thread stacks on SIGUSR1 (Unix only)
# Usage: kill -USR1 <pid>  (find pid with: pgrep -f ludivoxa)
if hasattr(signal, "SIGUSR1"):
    faulthandler.register(signal.SIGUSR1, all_threads=True)

from importlib.metadata import version

__version__ = version("ludivoxa")


def main():
    """Entry point for the application."""
    from . import paths

    # Must precede any import of huggingface_hub (pulled in by the GUI stack).
    paths.apply_environment()

    from .gui import run

    return run()


__all__ = ["__version__", "main"]
