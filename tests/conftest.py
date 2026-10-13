"""pytest 共通設定。試験はすべて一時ディレクトリ上の複製で行い、実データに触れない。"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))

from pvtest_helpers import make_project  # noqa: E402


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path / "photo-viewer")
