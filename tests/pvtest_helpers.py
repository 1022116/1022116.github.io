"""試験用の画像生成・プロジェクト複製ヘルパー（実データには触れない）。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS = PROJECT_ROOT / "tools"
SITE_FILES = ("index.html", "css/style.css", "js/app.js", ".nojekyll", "config.example.json", "requirements.txt", ".gitignore", ".gitattributes", "pytest.ini")


def make_project(dest: Path) -> Path:
    """本物のプロジェクトのコードだけを一時ディレクトリへ複製する（写真・出力は複製しない）。"""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TOOLS, dest / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for rel in SITE_FILES:
        src = PROJECT_ROOT / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
    for d in ("source_photos", "photos", "thumbs", "data"):
        (dest / d).mkdir(exist_ok=True)
    (dest / "source_photos" / ".gitkeep").touch()
    (dest / "photos" / ".gitkeep").touch()
    (dest / "thumbs" / ".gitkeep").touch()
    # 実プロジェクトの公開データに依存しないよう、常に空のギャラリーから始める
    empty = {
        "specId": "PV-SPEC-2026-10-v4",
        "specVersion": "4.0",
        "schemaVersion": 3,
        "title": "写真",
        "timezone": "Asia/Tokyo",
        "count": 0,
        "years": [],
        "photos": [],
    }
    (dest / "data" / "photos.json").write_text(json.dumps(empty, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def exif_bytes(dt: str | None = None, offset: str | None = None, orientation: int | None = None, gps: bool = False):
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    if orientation:
        exif[0x0112] = orientation
    if dt or offset:
        ifd = exif.get_ifd(0x8769)
        if dt:
            ifd[36867] = dt
        if offset:
            ifd[36881] = offset
    if gps:
        g = exif.get_ifd(0x8825)
        g[1] = "N"
        g[2] = (35.0, 39.0, 29.0)
        g[3] = "E"
        g[4] = (139.0, 44.0, 28.0)
    return exif


def make_image(
    path: Path,
    size=(800, 600),
    color=(200, 60, 60),
    *,
    fmt: str | None = None,
    exif_dt: str | None = None,
    offset: str | None = None,
    orientation: int | None = None,
    gps: bool = False,
    label: str | None = None,
    mtime: datetime | None = None,
    bands: tuple | None = None,
) -> Path:
    """試験画像を作る。bands=(左, 中央, 右) の色で縦3分割（中央切り抜き確認用）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    if bands:
        w = size[0]
        for i, c in enumerate(bands):
            d.rectangle([i * w // 3, 0, (i + 1) * w // 3, size[1]], fill=c)
    if label:
        s = max(12, min(size) // 6)
        d.rectangle([0, 0, s * len(label) // 2 + s, s + s // 2], fill=(255, 255, 255))
        d.text((s // 4, s // 6), label, fill=(0, 0, 0), font_size=s)
    fmt = fmt or {".png": "PNG", ".webp": "WEBP", ".heic": "HEIF", ".heif": "HEIF"}.get(path.suffix.lower(), "JPEG")
    kwargs = {}
    if exif_dt or orientation or gps or offset:
        kwargs["exif"] = exif_bytes(exif_dt, offset, orientation, gps)
    if fmt == "HEIF":
        import pillow_heif  # noqa: F401

        pillow_heif.register_heif_opener()
    img.save(path, fmt, **kwargs)
    if mtime is not None:
        ts = mtime.timestamp()
        os.utime(path, (ts, ts))
    return path


def snapshot(directory: Path) -> dict:
    """ディレクトリ内の全ファイルの (sha256, mtime_ns, size)。原本不変の確認用。"""
    out = {}
    for p in sorted(Path(directory).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(directory).as_posix()] = (
                hashlib.sha256(p.read_bytes()).hexdigest(),
                st.st_mtime_ns,
                st.st_size,
            )
    return out


def run_build(root: Path, *args: str, env: dict | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    e = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(root / "tools" / "build_gallery.py"), "--root", str(root), "--jobs", "1", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=e,
        timeout=timeout,
    )


def run_validate(root: Path, *args: str) -> subprocess.CompletedProcess:
    e = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(root / "tools" / "validate_build.py"), "--root", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=e,
        timeout=300,
    )


def load_photos(root: Path) -> dict:
    return json.loads((Path(root) / "data" / "photos.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# publish.ps1 試験用（一時 Git リポジトリ）
# ---------------------------------------------------------------------------
STUB_TEST = "def test_stub_ok():\n    assert True\n"


def find_powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def init_git_project(project: Path, remote_dir: Path) -> Path:
    """一時プロジェクトを Git 管理下にし、ベアリポジトリを origin として登録する。"""
    tests = project / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_stub.py").write_text(STUB_TEST, encoding="utf-8")  # 再帰実行を避ける軽量な試験
    shutil.copy2(PROJECT_ROOT / "README.md", project / "README.md")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote_dir)], check=True)
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.name", "PV Test")
    git(project, "config", "user.email", "pv-test@example.invalid")
    git(project, "config", "core.autocrlf", "false")
    git(project, "remote", "add", "origin", str(remote_dir))
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "initial")
    git(project, "push", "-q", "origin", "main")
    return project


def run_publish(project: Path, *args: str, env: dict | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    ps = find_powershell()
    e = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    e.pop("PV_DEBUG_PAUSE_AFTER_LOCK", None)
    if env:
        e.update(env)
    return subprocess.run(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(project / "tools" / "publish.ps1"), "-Python", sys.executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=e,
        timeout=timeout,
    )
