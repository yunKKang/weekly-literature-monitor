"""Compatibility helpers for reusing the legacy src modules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CONFIG_DIR = ROOT / "config"
WEB_DIR = ROOT / "web"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

