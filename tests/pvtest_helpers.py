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
SITE_FILES = ("index.html", "css/style.css", "js/app.js", ".nojekyll", "config.example.json", "requirements.txt", ".gitignore", ".gitattributes", "pytest.ini", "fonts/dotgothic16-subset.woff2", "fonts/OFL.txt")


def make_project(dest: Path) -> Path:
    """本物のプロジェクトのコードだけを一時ディレクトリへ複製する（写真・出力は複製しない）。"""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TOOLS, dest / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for rel in SITE_FILES:
        src = PROJECT_ROOT / rel
        if src.exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
    for d in ("source_photos", "photos", "thumbs", "mids", "data"):
        (dest / d).mkdir(exist_ok=True)
    (dest / "source_photos" / ".gitkeep").touch()
    (dest / "photos" / ".gitkeep").touch()
    (dest / "thumbs" / ".gitkeep").touch()
    (dest / "mids" / ".gitkeep").touch()
    # 実プロジェクトの公開データに依存しないよう、常に空のギャラリーから始める
    empty = {
        "specId": "PV-SPEC-2026-10-v18",
        "specVersion": "18.0",
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


class FakeGoatCounter:
    """GoatCounter の API の代わり（仕様 v18 のダッシュボードの試験用）。決まった数を返し、受けた問い合わせを記録する。

    status を 401 / 403 / 500 にするとそのエラーを返す。token が違えば 401。
    """

    VIEWS = {"view/1": 21, "view/2": 9, "view/3": 14, "view/still": 2}
    SEEN = {"seen/10": 30, "seen/30": 18, "seen/60": 9, "seen/100": 4, "seen/all": 1}

    def __init__(self, photo_ids, token: str = "fake-token-0123456789", status: int = 200):
        self.photo_ids = list(photo_ids)
        self.token = token
        self.status = status
        self.requests = []
        self._srv = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_port}/api/v0"

    def __enter__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                from urllib.parse import parse_qs, urlsplit

                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                fake.requests.append((u.path, q, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != f"Bearer {fake.token}":
                    return self._send(401, {"error": "unauthorized"})
                if fake.status != 200:
                    return self._send(fake.status, {"error": "fake error"})
                if u.path == "/api/v0/stats/hits":
                    return self._send(200, fake.hits(q))
                if u.path.startswith("/api/v0/stats/"):
                    return self._send(200, fake.stats(u.path.rsplit("/", 1)[1]))
                return self._send(404, {"error": "not found"})

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()

    @staticmethod
    def _days(q):
        from datetime import date, datetime, timedelta

        start = datetime.fromisoformat(q["start"].replace("Z", "+00:00")) + timedelta(hours=9)
        end = datetime.fromisoformat(q["end"].replace("Z", "+00:00")) + timedelta(hours=9) - timedelta(seconds=1)
        d, out = start.date(), []
        while d <= end.date():
            out.append(d)
            d += timedelta(days=1)
        return out

    def hits(self, q):
        days = self._days(q)
        stats = []
        for k, d in enumerate(days):
            daily = (d.toordinal() * 7) % 9 + 1  # 1〜9人（日によって違う）
            hourly = [0] * 24
            for i in range(daily):
                hourly[(9 + i * 5) % 24] += 1
            stats.append({"day": d.isoformat(), "daily": daily, "hourly": hourly})
        total = sum(s["daily"] for s in stats)
        hits = [{"path": "/", "path_id": 1, "event": False, "count": total, "title": "写真", "stats": stats}]
        scale = max(1, len(days) // 7)
        for i, (p, n) in enumerate(list(self.VIEWS.items()) + list(self.SEEN.items())):
            hits.append({"path": p, "path_id": 10 + i, "event": True, "count": n * scale, "stats": []})
        for i, pid in enumerate(self.photo_ids[:15]):
            hits.append({"path": f"photo/{pid}", "path_id": 100 + i, "event": True, "count": (15 - i) * scale, "stats": []})
        hits.append({"path": "photo/0000deadbeef0000", "path_id": 99, "event": True, "count": 1, "stats": []})  # 今はない写真
        hits.sort(key=lambda h: -h["count"])
        return {"hits": hits, "total": total, "more": False}

    def stats(self, page):
        data = {
            "toprefs": [("instagram.com", 12), ("", 9), ("t.co", 3), ("example.org", 1)],
            "systems": [("iOS", 18), ("Android", 7), ("Windows", 4), ("macOS", 2)],
            "browsers": [("Safari", 17), ("Chrome", 11), ("Edge", 3)],
            "sizes": [("phone", 24), ("tablet", 2), ("desktop", 5), ("desktophd", 0), ("unknown", 0)],
            "locations": [("Japan", 29), ("Taiwan", 1), ("<script>alert(1)</script>", 1)],
        }.get(page, [])
        return {"stats": [{"id": n or "", "name": n, "count": c} for n, c in data], "more": False}
