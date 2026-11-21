"""Windows executable entry point; application behavior stays in ludivoxa.main."""

import os
import sys
from multiprocessing import freeze_support
from pathlib import Path


def run():
    freeze_support()
    runtime = None
    # Windowed executables have no standard streams. Native OCR dependencies and
    # structlog need writable streams, so keep a UTF-8 log instead of a console.
    report = None
    if "--self-test" in sys.argv:
        report = Path(sys.argv[sys.argv.index("--self-test") + 1]).resolve()
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).parent)
        if sys.stdout is None or sys.stderr is None:
            log_dir = Path.home() / ".ludivoxa" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stream = (log_dir / "windows.log").open("a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = stream
        from local_runtime import ensure_bundled_ollama

        runtime = ensure_bundled_ollama(Path(sys.executable).parent)
    try:
        if report:
            from frozen_smoke import run_smoke

            return run_smoke(report)
        from ludivoxa import main

        return main()
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(run())
