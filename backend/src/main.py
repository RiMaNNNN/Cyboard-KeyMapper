"""Entry point wiring for the KeyMapper backend.

Startup chain: resolve paths from ``.env`` → ingest and validate ``config.yaml`` →
start the logger → attach the keyboard (or the fake device in demo mode) → serve
the API and web UI until shutdown. All steps live in :func:`services.run_server`;
this module exists so ``python -m src`` and ``python src/main.py`` both work.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sibling modules importable when executed as a script (not as a package).
_SRC_DIR = str(Path(__file__).resolve().parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from services import run_server  # noqa: E402


def _open_browser_when_up() -> None:
    """Open the UI in the default browser once the server answers.

    Runs on a daemon thread so a hung startup never blocks shutdown. The
    port is read from ``config.yaml`` the same way the server reads it,
    falling back to the default.
    """
    import re
    import time
    import urllib.request
    import webbrowser

    port = 8756
    config = Path(__file__).resolve().parent.parent / "data" / "configuration" / "config.yaml"
    try:
        match = re.search(r"^ *port: *(\d+)", config.read_text("utf-8"), re.M)
        if match:
            port = int(match.group(1))
    except OSError:
        pass
    url = f"http://127.0.0.1:{port}"
    for _ in range(180):
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(1)


def main() -> None:
    """Run the KeyMapper backend until it exits.

    ``--open`` additionally opens the default browser at the UI once the
    server is up — the OS-independent equivalent of the Windows launcher.
    """
    if "--open" in sys.argv:
        import threading

        threading.Thread(target=_open_browser_when_up, daemon=True).start()
    run_server()


if __name__ == "__main__":
    main()
