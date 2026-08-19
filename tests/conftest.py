"""Test muhiti: har seans uchun toza vaqtinchalik baza va API kalit.

Muhit o'zgaruvchilari modul import bo'lishidan OLDIN o'rnatilishi shart —
core.db import paytida NIGOH_DATA'ni o'qiydi.
"""
import os
import sys
import tempfile
from pathlib import Path

_data_dir = tempfile.mkdtemp(prefix="nigoh-test-")
os.environ["NIGOH_DATA"] = _data_dir
os.environ.setdefault("NIGOH_API_KEY", "test-kalit")
os.environ.setdefault("ENABLE_UI", "0")

# Loyiha ildizi import yo'lida bo'lsin (pytest'ni istalgan joydan yuritish uchun).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()
