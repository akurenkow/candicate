"""Pytest configuration for telegram-bot tests."""

import sys
from pathlib import Path

_service_root = Path(__file__).resolve().parent.parent
_s = str(_service_root)
# Ensure telegram-bot root is first so main's "from db import" resolves correctly
while _s in sys.path:
    sys.path.remove(_s)
sys.path.insert(0, _s)
