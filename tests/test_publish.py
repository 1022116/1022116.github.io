"""publish.ps1 の試験（同期仕様ID PV-SPEC-2026-10-v4 / 版 4.0）

一時ディレクトリに Git リポジトリとベアリポジトリ（origin の代わり）を作り、
ロック → 仮想環境確認 → build → validate → pytest → Git 安全確認 → commit/push の流れと停止条件を確認する。
PowerShell（pwsh / powershell）がない環境、または仮想環境外で実行した場合はスキップする。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import pv_common as C
from pvtest_helpers import (
    find_powershell,
    git,
    in_venv,
    init_git_project,
    load_photos,
    make_image,
    run_publish,
)

pytestmark = [
    pytest.mark.skipif(find_powershell() is None, reason="PowerShell がありません"),
    pytest.mark.skipif(not in_venv(), reason="仮想環境の Python で実行してください（publish.ps1 が仮想環境を確認するため）"),
    pytest.mark.skipif(os.environ.get("PV_SKIP_PUBLISH_TESTS") == "1", reason="PV_SKIP_PUBLISH_TESTS=1"),
]


@pytest.fixture
def repo(project, tmp_path):
    init_git_project(project, tmp_path / "remote.git")
    for i in range(3):
        make_image(project / "source_photos" / f"IMG_{i}.jpg", (900, 600), exif_dt=f"2025:0{i + 1}:15 12:00:00", gps=True)
    return project


def remote_head(project: Path) -> str:
    return git(project, "ls-remote", "origin", "refs/heads/main").stdout.split()[0]


def local_head(project: Path) -> str:
    return git(project, "rev-parse", "HEAD").stdout.strip()


def staged(project: Path) -> list:
    return [x for x in git(project, "diff", "--cached", "--name-only").stdout.splitlines() if x]


def out(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


def test_publish_commits_and_pushes_only_public_files(repo):
    before = remote_head(repo)
    r = run_publish(repo)
    assert r.returncode == 0, out(r)
    assert "8/8 push しました" in r.stdout
    after = remote_head(repo)
    assert after != before and after == local_head(repo)
    msg = git(repo, "log", "-1", "--format=%s").stdout
    assert "写真ビューアを更新: 3枚" in msg
    changed = git(repo, "diff", "--name-only", before, after).stdout.split()
    assert "data/photos.json" in changed
    assert all(f.startswith(("data/", "photos/", "thumbs/")) for f in changed), changed
    tracked = git(repo, "ls-files").stdout.split()
    assert not [f for f in tracked if f.startswith("source_photos/") and f != "source_photos/.gitkeep"]
    assert not [f for f in tracked if f.startswith((".build/", "logs/", ".locks/")) or f == "config.json"]
    assert sum(1 for f in tracked if f.startswith("photos/") and f.endswith(".webp")) == 3
    assert staged(repo) == []


def test_publish_without_changes_does_not_commit(repo):
    assert run_publish(repo).returncode == 0
    head = remote_head(repo)
    r = run_publish(repo)
    assert r.returncode == 0, out(r)
    assert "変更はありません" in r.stdout
    assert remote_head(repo) == head == local_head(repo)


def test_publish_dry_run_changes_nothing_in_git(repo):
    head = local_head(repo)
    r = run_publish(repo, "-DryRun")
    assert r.returncode == 0, out(r)
    assert "公開予定のファイル" in r.stdout and "data/photos.json" in r.stdout
    assert local_head(repo) == head and staged(repo) == []


def test_publish_no_push_prepares_commit_only(repo):
    remote = remote_head(repo)
    r = run_publish(repo, "-NoPush")
    assert r.returncode == 0, out(r)
    assert "公開準備完了" in r.stdout
    assert local_head(repo) != remote and remote_head(repo) == remote


def test_build_failure_blocks_commit(repo):
    (repo / "config.json").write_text('{"sourceDir": "no_such_folder"}', encoding="utf-8")
    head = local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 1 and "生成に失敗しました" in r.stdout
    assert local_head(repo) == head and remote_head(repo) == head and staged(repo) == []


def test_pytest_failure_blocks_commit(repo):
    (repo / "tests" / "test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    head = local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 1 and "試験に失敗しました" in r.stdout
    assert local_head(repo) == head and staged(repo) == []


def test_validation_failure_blocks_commit(repo):
    html = (repo / "index.html").read_text(encoding="utf-8")
    (repo / "index.html").write_text(html.replace("</head>", '<script src="https://cdn.example.com/a.js"></script></head>'), encoding="utf-8")
    head = local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 1 and "検証に不合格" in r.stdout
    assert local_head(repo) == head and staged(repo) == []


def test_tracked_original_blocks_publish(repo):
    git(repo, "add", "-f", "source_photos/IMG_0.jpg")
    git(repo, "commit", "-q", "-m", "mistake")
    head = local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 1 and "原本が Git の管理対象" in r.stdout
    assert local_head(repo) == head


def test_forbidden_file_in_public_dir_blocks_publish(repo):
    make_image(repo / "data" / "leak.jpg", (100, 100))
    head = local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 1 and "公開してはいけないファイル" in r.stdout and "data/leak.jpg" in r.stdout
    assert local_head(repo) == head and staged(repo) == []


def test_prestaged_changes_block_publish(repo):
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    r = run_publish(repo)
    assert r.returncode == 1 and "ステージ済みの変更" in r.stdout
    assert staged(repo) == ["README.md"]  # 利用者のステージ内容には触れない


def test_gitignore_without_source_photos_blocks_publish(repo):
    (repo / ".gitignore").write_text("logs/\n.build/\n.locks/\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "break gitignore")
    head = local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 1 and "source_photos" in r.stdout
    assert local_head(repo) == head


def test_non_venv_python_is_rejected(repo):
    base = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    if not base.exists():
        pytest.skip("基底 Python が見つかりません")
    ps = find_powershell()
    r = subprocess.run(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "tools" / "publish.ps1"), "-Python", str(base)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    assert r.returncode == 1 and "仮想環境の Python ではありません" in r.stdout


def test_missing_venv_is_reported(repo):
    ps = find_powershell()
    r = subprocess.run(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "tools" / "publish.ps1")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    assert r.returncode == 1 and "仮想環境の Python が見つかりません" in r.stdout


def test_publish_lock_busy(repo):
    head = local_head(repo)
    with C.FileLock(C.lock_paths(repo)["publish"], "publish"):
        r = run_publish(repo)
    assert r.returncode == 75 and "実行中" in r.stdout
    assert local_head(repo) == head


def test_publish_stops_when_build_lock_busy(repo):
    with C.FileLock(C.lock_paths(repo)["build"], "build"):
        r = run_publish(repo)
    assert r.returncode == 75, out(r)
    assert staged(repo) == []


def test_two_publishes_started_together_run_once(repo):
    ps = find_powershell()
    env = dict(os.environ, PYTHONUTF8="1", PV_DEBUG_PAUSE_AFTER_LOCK="4")
    first = subprocess.Popen(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(repo / "tools" / "publish.ps1"), "-Python", sys.executable],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
    )
    time.sleep(2.0)
    second = run_publish(repo)  # タスクスケジューラー等による同時起動
    log1, _ = first.communicate(timeout=600)
    assert second.returncode == 75, out(second)
    assert first.returncode == 0, log1.decode("utf-8", "replace")
    assert git(repo, "log", "--oneline").stdout.count("写真ビューアを更新") == 1


def test_publish_reflects_added_and_removed_photos(repo):
    assert run_publish(repo).returncode == 0
    make_image(repo / "source_photos" / "new" / "IMG_9.jpg", (700, 900), exif_dt="2026:01:01 00:00:00")
    r = run_publish(repo)
    assert r.returncode == 0, out(r)
    doc = load_photos(repo)
    assert doc["count"] == 4 and doc["years"] == [2026, 2025]
    assert "4枚" in git(repo, "log", "-1", "--format=%s").stdout
    (repo / "source_photos" / "IMG_0.jpg").unlink()
    assert run_publish(repo).returncode == 0
    tracked = git(repo, "ls-tree", "-r", "--name-only", remote_head(repo)).stdout.split()
    assert sum(1 for f in tracked if f.startswith("thumbs/") and f.endswith(".webp")) == 3


# ---------------------------------------------------------------------------
# 手順書 9・10章: 通信断・認証切れ後の再送、無人実行
# ---------------------------------------------------------------------------
def bare_head(bare: Path) -> str:
    return subprocess.run(["git", "ls-remote", str(bare), "refs/heads/main"], capture_output=True, text=True).stdout.split()[0]


def test_push_failure_is_retried_on_next_run(repo):
    bare = repo.parent / "remote.git"
    before = bare_head(bare)
    git(repo, "remote", "set-url", "origin", str(repo.parent / "unreachable.git"))  # 通信断・認証切れの代わり
    r = run_publish(repo)
    assert r.returncode == 1 and "push に失敗しました" in r.stdout and "次回の publish.ps1 実行時に再送" in r.stdout
    assert "写真ビューアを更新" in git(repo, "log", "-1", "--format=%s").stdout  # commit は PC 内に残る
    assert bare_head(bare) == before
    git(repo, "remote", "set-url", "origin", str(bare))  # 回復
    r = run_publish(repo)
    assert r.returncode == 0, out(r)
    assert "push できなかった commit があります（1 件）" in r.stdout and "8/8 push しました" in r.stdout
    assert bare_head(bare) == local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 0 and "変更はありません" in r.stdout


def test_no_push_commit_is_sent_by_next_publish(repo):
    assert run_publish(repo, "-NoPush").returncode == 0
    assert remote_head(repo) != local_head(repo)
    r = run_publish(repo, "-DryRun")
    assert r.returncode == 0 and "まだ push していない commit があります" in r.stdout
    assert remote_head(repo) != local_head(repo)
    r = run_publish(repo)
    assert r.returncode == 0 and "8/8 push しました" in r.stdout
    assert remote_head(repo) == local_head(repo)


def test_unattended_mode_for_task_scheduler(repo):
    r = run_publish(repo, "-Unattended")
    assert r.returncode == 0, out(r)
    assert "無人実行モード" in r.stdout and "8/8 push しました" in r.stdout
    git(repo, "remote", "set-url", "origin", "https://127.0.0.1:9/unreachable/photo-viewer.git")
    make_image(repo / "source_photos" / "later.jpg", exif_dt="2026:03:03 03:03:03")
    r = run_publish(repo, "-Unattended", timeout=180)  # 認証入力を待たずに失敗として終了する
    assert r.returncode == 1 and "push に失敗しました" in r.stdout


def test_publish_log_shows_tests_passed_before_push(repo):
    assert run_publish(repo).returncode == 0
    logs = sorted((repo / "logs").glob("publish_*.log"))
    text = logs[-1].read_text(encoding="utf-8")
    assert text.index("6/8 試験に合格しました") < text.index("8/8 push しました")
