"""写真ビューア 管理ページ（同期仕様ID PV-SPEC-2026-10-v8 / 版 8.0）

写真ごとに「小話」を書くためのページを、このPCの中だけで開きます（GitHub Pages には載りません）。
書いた小話は stories.json に保存され、publish.ps1（生成）で data/photos.json の story に入り、サイトに表示されます。

使い方（プロジェクトのフォルダーで、仮想環境の Python を使う）:
  python tools/admin.py --set-password   # 最初に1回: ユーザー名とパスワードを設定（config.json にハッシュだけを保存）
  python tools/admin.py                  # 管理ページを起動してブラウザーで開く（終了は Ctrl + C）

安全のための作り:
  - 127.0.0.1（このPC）だけで待ち受け、別の名前（Host）で来た要求は断る
  - パスワードは PBKDF2-HMAC-SHA256（60万回）のハッシュだけを config.json（Git 対象外）に保存
  - ログインはセッション Cookie（HttpOnly・SameSite=Strict）。保存の要求は Origin と専用ヘッダーも確認する
  - 5回続けて失敗すると60秒間ログインを受け付けない
"""
from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv_common as C  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8765
SESSION_SECONDS = 8 * 60 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
MIN_PASSWORD_LENGTH = 8
COOKIE = "pv_admin"
THUMB_RE = re.compile(r"^/thumbs/(\d{4})/([0-9a-f]{8,64})\.webp$")


class AdminError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# パスワードの設定（config.json の admin）
# ---------------------------------------------------------------------------
def _config_path(root: Path, config: str | None) -> Path:
    path = Path(config) if config else root / "config.json"
    return path if path.is_absolute() else (root / path)


def set_password(root: Path, config: str | None, user: str | None, password: str | None) -> None:
    path = _config_path(root, config)
    if not path.is_file():
        raise AdminError(f"設定ファイルがありません: {path}（先に Copy-Item config.example.json config.json）")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise AdminError("config.json の形式が正しくありません")
    current = data.get("admin") if isinstance(data.get("admin"), dict) else {}
    if user is None:
        default = current.get("user", "")
        user = input(f"ユーザー名{f'（Enter で {default}）' if default else ''}: ").strip() or default
    if not user or not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", user):
        raise AdminError("ユーザー名は半角英数字と . _ @ - で、1～64文字にしてください")
    if password is None:
        password = getpass.getpass("パスワード（入力しても表示されません）: ")
        again = getpass.getpass("もう一度入力: ")
        if password != again:
            raise AdminError("2回の入力が一致しません。もう一度やり直してください")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminError(f"パスワードは {MIN_PASSWORD_LENGTH} 文字以上にしてください")
    data["admin"] = {"user": user, "passwordHash": C.hash_password(password)}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 管理ページのサーバー
# ---------------------------------------------------------------------------
class AdminApp:
    def __init__(self, root: Path, config: str | None, port: int):
        self.root = root
        self.port = port
        cfg, _used, _warnings = C.load_config(root, _config_path(root, config))
        if not cfg.get("admin"):
            raise AdminError("ログイン情報が未設定です。先に python tools/admin.py --set-password を実行してください")
        self.user = cfg["admin"]["user"]
        self.password_hash = cfg["admin"]["passwordHash"]
        self.sessions: dict = {}
        self.failures = 0
        self.locked_until = 0.0
        self.lock = threading.Lock()

    # --- 認証 -------------------------------------------------------------
    def login(self, user: str, password: str) -> str | None:
        with self.lock:
            if time.time() < self.locked_until:
                return None
        ok = secrets.compare_digest(user.encode("utf-8"), self.user.encode("utf-8"))
        ok = C.verify_password(password, self.password_hash) and ok
        with self.lock:
            if not ok:
                self.failures += 1
                if self.failures >= MAX_FAILURES:
                    self.locked_until = time.time() + LOCKOUT_SECONDS
                    self.failures = 0
                return None
            self.failures = 0
            token = secrets.token_urlsafe(32)
            self.sessions[token] = time.time() + SESSION_SECONDS
            return token

    def locked(self) -> bool:
        return time.time() < self.locked_until

    def valid_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            exp = self.sessions.get(token)
            if exp is None:
                return False
            if exp < time.time():
                del self.sessions[token]
                return False
            return True

    def logout(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)

    # --- データ -----------------------------------------------------------
    def photos(self) -> list:
        path = self.root / "data" / "photos.json"
        if not path.is_file():
            return []
        doc = json.loads(path.read_text(encoding="utf-8"))
        photos = doc.get("photos", []) if isinstance(doc, dict) else []
        sources = {}
        man = self.root / ".build" / "manifest.json"
        if man.is_file():
            try:
                sources = {o["id"]: o["source"] for o in json.loads(man.read_text(encoding="utf-8")).get("order", [])}
            except (ValueError, KeyError, TypeError):
                sources = {}
        n = len(photos)
        return [
            {"id": p["id"], "no": n - i, "date": p["date"], "year": p["year"], "thumb": p["thumb"],
             "source": sources.get(p["id"], "")}
            for i, p in enumerate(photos)
            if isinstance(p, dict) and "id" in p
        ]

    def stories_with_dates(self) -> tuple:
        texts = C.load_stories(self.root)
        updated = {}
        path = self.root / C.STORIES_FILE
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8-sig")).get("stories", {})
            updated = {k: v.get("updated", "") for k, v in raw.items() if isinstance(v, dict)}
        return texts, updated

    def save_story(self, pid: str, text: str) -> tuple:
        ids = {p["id"] for p in self.photos()}
        if pid not in ids:
            raise C.StoriesError("写真が見つかりません（生成し直した後なら、ページを再読み込みしてください）")
        text = C.normalize_story(text)
        with self.lock:
            texts, updated = self.stories_with_dates()
            stamp = C.to_iso_jst(datetime.now(C.JST))
            if text:
                texts[pid] = text
                updated[pid] = stamp
            else:
                texts.pop(pid, None)
                updated.pop(pid, None)
            C.save_stories(self.root, texts, updated)
        return text, stamp


def _page(title: str, body: str, nonce: str, script: str = "") -> bytes:
    css = """
:root { --bg:#ffffff; --ink:#000; --line:#d6d6d6; --tile:#f0f0ef; --focus:#2f6feb; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font: 15px/1.7 system-ui, -apple-system, "Hiragino Sans", "Yu Gothic UI", Meiryo, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 80px; }
h1 { font-size: 22px; font-weight: 500; margin: 0 0 4px; }
.lead { margin: 0 0 24px; font-size: 13px; }
.bar { display:flex; justify-content:space-between; align-items:baseline; gap:16px; flex-wrap:wrap; }
button, input, textarea { font: inherit; color: inherit; }
button { min-height: 40px; padding: 6px 18px; border: 1px solid var(--ink); border-radius: 999px; background: none; cursor: pointer; }
button:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.login { max-width: 360px; margin: 12vh auto 0; }
.login label { display:block; margin: 14px 0 4px; font-size: 13px; }
.login input { width:100%; padding: 8px 10px; border:1px solid var(--line); border-radius: 6px; background:#fff; }
.login button { margin-top: 20px; width: 100%; }
.error { color: #a01818; font-size: 13px; margin: 12px 0 0; }
h2 { font-size: 18px; font-weight: 500; margin: 36px 0 8px; }
.item { display:grid; grid-template-columns: 120px minmax(0,1fr); gap: 16px; padding: 18px 0; border-top: 1px solid var(--line); }
.item img { width:120px; height:120px; object-fit:cover; background:var(--tile); display:block; }
.meta { font-size: 12px; margin: 0 0 6px; display:flex; gap: 12px; flex-wrap: wrap; }
.meta .src { overflow-wrap:anywhere; opacity: .75; }
textarea { width:100%; min-height: 96px; padding: 8px 10px; border:1px solid var(--line); border-radius: 6px; background:#fff; resize: vertical; line-height: 1.7; }
.row { display:flex; align-items:center; gap: 12px; margin-top: 8px; font-size: 12px; }
.count { margin-left: auto; }
.status.saved { color: #1b6e2a; } .status.error { color: #a01818; }
.visually-hidden { position:absolute; width:1px; height:1px; margin:-1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
@media (max-width: 520px) { .item { grid-template-columns: 1fr; } .item img { width: 100%; height: auto; aspect-ratio: 1; } }
"""
    doc = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{html.escape(title)}</title>
<style nonce="{nonce}">{css}</style></head>
<body><main>{body}</main>{f'<script nonce="{nonce}">{script}</script>' if script else ''}</body></html>"""
    return doc.encode("utf-8")


ADMIN_JS = r"""
(function () {
  'use strict';
  var dirty = 0;
  function setStatus(form, text, kind) {
    var s = form.querySelector('.status');
    s.textContent = text;
    s.className = 'status' + (kind ? ' ' + kind : '');
  }
  Array.prototype.forEach.call(document.querySelectorAll('form.story'), function (form) {
    var ta = form.querySelector('textarea');
    var count = form.querySelector('.count');
    var saved = ta.value;
    function update() {
      count.textContent = ta.value.length + ' / ' + ta.maxLength;
      var isDirty = ta.value !== saved;
      if (isDirty !== form.classList.contains('is-dirty')) {
        form.classList.toggle('is-dirty', isDirty);
        dirty += isDirty ? 1 : -1;
      }
      if (isDirty) setStatus(form, '未保存', '');
    }
    ta.addEventListener('input', update);
    update();
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var btn = form.querySelector('button');
      btn.disabled = true;
      setStatus(form, '保存中…', '');
      fetch('api/story', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-PV-Admin': '1' },
        body: JSON.stringify({ id: form.getAttribute('data-id'), text: ta.value })
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) { return { res: res, body: body }; });
      }).then(function (r) {
        if (r.res.status === 401) { setStatus(form, 'ログインが切れました。ページを再読み込みしてください', 'error'); return; }
        if (!r.res.ok) { setStatus(form, r.body.error || '保存できませんでした', 'error'); return; }
        ta.value = r.body.text;
        saved = ta.value;
        update();
        setStatus(form, r.body.text ? '保存しました' : '削除しました', 'saved');
      }).catch(function () {
        setStatus(form, '保存できませんでした（管理ページが終了していないか確認してください）', 'error');
      }).then(function () { btn.disabled = false; });
    });
  });
  window.addEventListener('beforeunload', function (e) {
    if (dirty > 0) { e.preventDefault(); e.returnValue = ''; }
  });
})();
"""


def make_handler(app: AdminApp):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "PhotoViewerAdmin"
        sys_version = ""

        def log_message(self, fmt, *args):  # 要求の記録は出さない（入力内容を残さないため）
            pass

        # --- 共通 ---------------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None, nonce: str | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            # same-origin: 外部へは参照元を送らない。no-referrer にするとブラウザーが自分の送信の Origin を null にして確認できなくなる
            self.send_header("Referrer-Policy", "same-origin")
            csp = "default-src 'none'; img-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            if nonce:
                csp += f"; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'"
            self.send_header("Content-Security-Policy", csp)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj: dict):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(HTTPStatus.BAD_REQUEST, b"bad host", "text/plain; charset=utf-8")
                return False
            return True

        def _origin_ok(self) -> bool:
            if self.headers.get("Origin", "") not in allowed_origins:
                self._send(HTTPStatus.FORBIDDEN, "別のページからの送信は受け付けません".encode("utf-8"), "text/plain; charset=utf-8")
                return False
            return True

        def _token(self) -> str | None:
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie", ""))
            except Exception:  # 壊れた Cookie は無視
                return None
            return c[COOKIE].value if COOKIE in c else None

        def _body(self, limit: int = 64 * 1024) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("too large")
            return self.rfile.read(n) if n > 0 else b""

        # --- 画面 ---------------------------------------------------------
        def _login_page(self, status: int = 200, message: str = ""):
            nonce = secrets.token_urlsafe(16)
            err = f'<p class="error" role="alert">{html.escape(message)}</p>' if message else ""
            body = f"""<div class="login">
<h1>Daily 管理ページ</h1>
<p class="lead">写真ごとの小話を書くページです（このPCの中だけで開いています）。</p>
<form method="post" action="login">
<label for="user">ユーザー名</label><input id="user" name="user" autocomplete="username" required autofocus>
<label for="password">パスワード</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">ログイン</button>{err}
</form></div>"""
            self._send(status, _page("ログイン｜管理ページ", body, nonce), "text/html; charset=utf-8", nonce=nonce)

        def _admin_page(self):
            nonce = secrets.token_urlsafe(16)
            photos = app.photos()
            try:
                texts, updated = app.stories_with_dates()
                problem = ""
            except C.StoriesError as exc:
                texts, updated, problem = {}, {}, str(exc)
            parts = [f"""<div class="bar"><h1>小話</h1>
<form method="post" action="logout"><button type="submit">ログアウト</button></form></div>
<p class="lead">写真ごとに小話を書いて「保存」してください（空にして保存すると削除）。<br>
保存した小話は、<code>publish.ps1</code> を実行するとサイトの全画面表示に載ります。</p>"""]
            if problem:
                parts.append(f'<p class="error" role="alert">{html.escape(problem)}</p>')
            if not photos:
                parts.append('<p class="error">写真の一覧（data/photos.json）がありません。先に生成してください。</p>')
            year = None
            for p in photos:
                if p["year"] != year:
                    year = p["year"]
                    parts.append(f"<h2>{year}</h2>")
                text = texts.get(p["id"], "")
                when = updated.get(p["id"], "")
                date = p["date"][:16].replace("T", " ")
                parts.append(f"""<section class="item">
<img src="{html.escape(p['thumb'])}" alt="No. {p['no']:02d}（{html.escape(date)}）" width="120" height="120" loading="lazy">
<form class="story" data-id="{html.escape(p['id'])}">
<p class="meta"><span>No. {p['no']:02d}</span><span>{html.escape(date)}</span><span class="src">{html.escape(p['source'])}</span></p>
<label class="visually-hidden" for="t-{html.escape(p['id'])}">No. {p['no']:02d} の小話</label>
<textarea id="t-{html.escape(p['id'])}" maxlength="{C.STORY_MAX_CHARS}" placeholder="この写真の小話">{html.escape(text)}</textarea>
<div class="row"><button type="submit">保存</button><span class="status" role="status">{'保存済み ' + html.escape(when[:16].replace('T', ' ')) if when else ''}</span><span class="count"></span></div>
</form></section>""")
            body = "\n".join(parts)
            self._send(200, _page("小話｜管理ページ", body, nonce, ADMIN_JS), "text/html; charset=utf-8", nonce=nonce)

        # --- 要求 ---------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return
            path = self.path.split("?", 1)[0]
            logged_in = app.valid_session(self._token())
            if path == "/":
                return self._admin_page() if logged_in else self._login_page()
            m = THUMB_RE.match(path)
            if m and logged_in:
                f = (app.root / "thumbs" / m.group(1) / f"{m.group(2)}.webp").resolve()
                if f.is_file() and (app.root / "thumbs").resolve() in f.parents:
                    return self._send(200, f.read_bytes(), "image/webp")
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._host_ok() or not self._origin_ok():
                return
            path = self.path.split("?", 1)[0]
            try:
                raw = self._body()
            except ValueError:
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"too large", "text/plain; charset=utf-8")
            if path == "/login":
                if app.locked():
                    return self._login_page(429, f"失敗が続いたため、{LOCKOUT_SECONDS}秒ほど待ってからログインしてください")
                form = parse_qs(raw.decode("utf-8", "replace"))
                token = app.login(form.get("user", [""])[0], form.get("password", [""])[0])
                if not token:
                    time.sleep(1)  # 総当たりを遅らせる
                    return self._login_page(401, "ユーザー名またはパスワードが違います")
                cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"
                return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
            if path == "/logout":
                app.logout(self._token())
                cookie = f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
            if path == "/api/story":
                if not app.valid_session(self._token()):
                    return self._json(HTTPStatus.UNAUTHORIZED, {"error": "ログインしてください"})
                if self.headers.get("X-PV-Admin") != "1":
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "受け付けられない送信です"})
                try:
                    data = json.loads(raw.decode("utf-8"))
                    pid, text = data["id"], data["text"]
                    if not isinstance(pid, str) or not isinstance(text, str):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "送信内容が正しくありません"})
                try:
                    text, stamp = app.save_story(pid, text)
                except C.StoriesError as exc:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except OSError as exc:
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"保存できませんでした: {exc}"})
                return self._json(200, {"ok": True, "id": pid, "text": text, "updated": stamp})
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

    return Handler


def make_server(app: AdminApp) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", app.port), make_handler(app))
    srv.daemon_threads = True
    return srv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="写真ビューア 管理ページ（小話の編集）")
    ap.add_argument("--set-password", action="store_true", help="ログインのユーザー名とパスワードを設定する")
    ap.add_argument("--user", help="--set-password で使うユーザー名（省略時は入力）")
    ap.add_argument("--password-stdin", action="store_true", help="--set-password のパスワードを標準入力の1行目から読む")
    ap.add_argument("--config", default=None, help="設定ファイル（既定: config.json）")
    ap.add_argument("--root", default=str(PROJECT_ROOT), help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"待ち受けるポート（既定: {DEFAULT_PORT}）")
    ap.add_argument("--no-browser", action="store_true", help="ブラウザーを自動で開かない")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.set_password:
            pw = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else None
            set_password(root, args.config, args.user, pw)
            print("ログイン情報を設定しました（config.json にはパスワードのハッシュだけを保存しています）。")
            print("管理ページの起動: python tools/admin.py")
            return 0
        app = AdminApp(root, args.config, args.port)
        srv = make_server(app)
    except (AdminError, C.ConfigError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[ERROR] 管理ページを起動できません（ポート {args.port} が使用中の可能性）: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{app.port}/"
    print(f"管理ページ: {url}")
    print("このPCの中だけで開いています。終了するときは、このウィンドウで Ctrl + C を押してください。")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n管理ページを終了しました。")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
