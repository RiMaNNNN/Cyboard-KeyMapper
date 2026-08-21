"""Package entry point: ``python -m src`` starts the KeyMapper backend."""

from __future__ import annotations

import sys
from pathlib import Path

# Sibling modules use flat imports; ensure the src directory is importable
# before pulling in main, for both `python -m src` and frozen launchers.
_SRC_DIR = str(Path(__file__).resolve().parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from main import main  # noqa: E402

main()
