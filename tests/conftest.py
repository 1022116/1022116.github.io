"""pytest 共通設定。試験はすべて一時ディレクトリ上の複製で行い、実データに触れない。"""
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))

from pvtest_helpers import make_project  # noqa: E402

TEST_TEMP_DIR = Path(tempfile.gettempdir()) / "photo-viewer-pytest"


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """試験用の一時フォルダーを %TEMP%\\photo-viewer-pytest に固定する（毎回空にしてから使う）。

    Windows では、pytest 標準の一時フォルダー（%TEMP%\\pytest-of-ユーザー名）に残った古いリンクのせいで、
    試験がすべて合格しても終了時に PermissionError で失敗することがある。固定の場所を指定すると、
    pytest はそのリンクを作らず、片付けもしない。
    プロジェクトの中に置かないのは、試験で作る Git リポジトリが本物のリポジトリの中に入らないようにするため。
    --basetemp を指定して実行した場合はそちらを使う。
    """
    if not config.option.basetemp:
        config.option.basetemp = str(TEST_TEMP_DIR)


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path / "photo-viewer")
