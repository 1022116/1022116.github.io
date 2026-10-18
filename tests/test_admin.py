"""管理ページと小話の試験（同期仕様ID PV-SPEC-2026-10-v8 / 版 8.0）

分類: 正常（ログイン・保存・生成への反映）/ 安全（Host・Origin・専用ヘッダー・Cookie・試行制限・パス）/
      境界（空・最大文字数）/ 異常（壊れた stories.json・不正な story）
実際の写真・設定・パスワードには触れない（一時ディレクトリで試験する）。
"""
from __future__ import annotations

import http.client
import io
import json
import socket
import threading
from pathlib import Path

import pytest

import admin as A
import pv_common as C
import validate_build as V
from pvtest_helpers import load_photos, make_image, run_build, run_validate

PASSWORD = "test-only-pass-1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def built(project):
    s = project / "source_photos"
    make_image(s / "2026" / "a.jpg", exif_dt="2026:05:01 10:00:00", label="A")
    make_image(s / "2026" / "b.jpg", exif_dt="2026:06:01 10:00:00", label="B")
    make_image(s / "2025" / "c.jpg", exif_dt="2025:03:01 10:00:00", label="C")
    r = run_build(project)
    assert r.returncode == 0, r.stdout
    (project / "config.json").write_text((project / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    return project


@pytest.fixture
def server(built, monkeypatch):
    monkeypatch.setattr(A.time, "sleep", lambda s: None)  # 失敗時の待ち時間を省く
    monkeypatch.setattr(C, "PASSWORD_ITERATIONS", 1000)  # 試験を速くする（本番は60万回）
    monkeypatch.setattr(A.sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert A.main(["--set-password", "--user", "tester", "--password-stdin", "--root", str(built)]) == 0
    port = _free_port()
    app = A.AdminApp(built, None, port)
    srv = A.make_server(app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield {"root": built, "port": port, "app": app}
    srv.shutdown()
    srv.server_close()


def req(srv, method, path, body=None, *, host=None, origin="same", cookie=None, headers=None):
    port = srv["port"]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}"}
    if origin == "same":
        h["Origin"] = f"http://127.0.0.1:{port}"
    elif origin:
        h["Origin"] = origin
    if cookie:
        h["Cookie"] = f"{A.COOKIE}={cookie}"
    if isinstance(body, dict):
        body = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    elif isinstance(body, str):
        body = body.encode("utf-8")
        h["Content-Type"] = "application/x-www-form-urlencoded"
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r, data


def login(srv, user="tester", password=PASSWORD):
    from urllib.parse import urlencode

    r, _ = req(srv, "POST", "/login", urlencode({"user": user, "password": password}))
    if r.status != 303:
        return r, None
    cookie = r.getheader("Set-Cookie")
    return r, cookie.split(";", 1)[0].split("=", 1)[1]


def api_save(srv, cookie, pid, text, **kw):
    r, data = req(srv, "POST", "/api/story", {"id": pid, "text": text}, cookie=cookie,
                  headers=kw.pop("headers", {"X-PV-Admin": "1"}), **kw)
    is_json = (r.getheader("Content-Type") or "").startswith("application/json")
    return r, (json.loads(data) if is_json and data else {})


# ===========================================================================
# パスワード
# ===========================================================================
class TestPassword:
    def test_hash_is_salted_and_verifiable(self):
        a = C.hash_password("secret-123", iterations=1000)
        b = C.hash_password("secret-123", iterations=1000)
        assert a != b and a.startswith("pbkdf2_sha256$1000$")  # 同じパスワードでも塩が違う
        assert C.verify_password("secret-123", a) and not C.verify_password("secret-124", a)
        assert not C.verify_password("secret-123", "broken") and not C.verify_password("x", "md5$1$a$b")

    def test_default_iterations_are_strong(self):
        assert C.PASSWORD_ITERATIONS >= 600_000

    def test_set_password_stores_only_the_hash(self, built, monkeypatch):
        monkeypatch.setattr(C, "PASSWORD_ITERATIONS", 1000)
        monkeypatch.setattr(A.sys, "stdin", io.StringIO(PASSWORD + "\n"))
        assert A.main(["--set-password", "--user", "tester", "--password-stdin", "--root", str(built)]) == 0
        text = (built / "config.json").read_text(encoding="utf-8")
        assert PASSWORD not in text
        cfg, _u, warnings = C.load_config(built)
        assert warnings == [] and cfg["admin"]["user"] == "tester"
        assert C.verify_password(PASSWORD, cfg["admin"]["passwordHash"])
        assert json.loads(text)["sourceDir"] == "source_photos"  # ほかの設定はそのまま

    @pytest.mark.parametrize("user,pw", [("tester", "short"), ("bad user", PASSWORD), ("", PASSWORD)])
    def test_set_password_rejects_weak_input(self, built, monkeypatch, user, pw):
        monkeypatch.setattr(A.sys, "stdin", io.StringIO(pw + "\n"))
        before = (built / "config.json").read_text(encoding="utf-8")
        assert A.main(["--set-password", "--user", user, "--password-stdin", "--root", str(built)]) == 1
        assert (built / "config.json").read_text(encoding="utf-8") == before

    def test_server_refuses_to_start_without_password(self, built, capsys):
        assert A.main(["--root", str(built), "--no-browser", "--port", str(_free_port())]) == 1
        assert "--set-password" in capsys.readouterr().err

    def test_broken_admin_config_is_rejected(self, built):
        cfg = json.loads((built / "config.json").read_text(encoding="utf-8"))
        cfg["admin"] = {"user": "tester", "passwordHash": "plain-text"}
        (built / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with pytest.raises(C.ConfigError):
            C.load_config(built)

    def test_config_json_is_not_published(self):
        root = Path(__file__).resolve().parent.parent
        assert "config.json" in (root / ".gitignore").read_text(encoding="utf-8").split()
        assert "'config.json'" not in (root / "tools" / "publish.ps1").read_text(encoding="utf-8-sig")


# ===========================================================================
# ログインと安全装置
# ===========================================================================
class TestLogin:
    def test_login_page_and_success(self, server):
        r, body = req(server, "GET", "/")
        assert r.status == 200 and "ログイン" in body.decode("utf-8")
        assert "nonce-" in r.getheader("Content-Security-Policy") and r.getheader("Cache-Control") == "no-store"
        assert r.getheader("X-Frame-Options") == "DENY"
        r, cookie = login(server)
        assert r.status == 303 and cookie
        sc = r.getheader("Set-Cookie")
        assert "HttpOnly" in sc and "SameSite=Strict" in sc
        r, body = req(server, "GET", "/", cookie=cookie)
        page = body.decode("utf-8")
        assert r.status == 200 and "<textarea" in page and "No. 03" in page and "2026/b.jpg" in page

    @pytest.mark.parametrize("user,pw", [("tester", "wrong-password"), ("other", PASSWORD), ("", "")])
    def test_wrong_credentials(self, server, user, pw):
        r, cookie = login(server, user, pw)
        assert r.status == 401 and cookie is None

    def test_lockout_after_repeated_failures(self, server):
        for _ in range(A.MAX_FAILURES):
            assert login(server, "tester", "nope-nope")[0].status == 401
        r, cookie = login(server)  # 正しくても一定時間は受け付けない
        assert r.status == 429 and cookie is None

    def test_wrong_host_is_refused(self, server):
        r, _ = req(server, "GET", "/", host="evil.example")
        assert r.status == 400
        r, _ = req(server, "POST", "/login", "user=tester&password=x", host=f"evil.example:{server['port']}")
        assert r.status == 400

    @pytest.mark.parametrize("origin", [None, "http://evil.example", "null"])
    def test_cross_site_post_is_refused(self, server, origin):
        r, _ = req(server, "POST", "/login", f"user=tester&password={PASSWORD}", origin=origin)
        assert r.status == 403

    @pytest.mark.parametrize("path,origin", [("/login", None), ("/login", "http://evil.example"), ("/api/story", None)])
    def test_refusal_is_sent_after_the_whole_request(self, server, path, origin):
        """断るときも本文を受け取ってから応答する（読まずに閉じると Windows では接続がリセットされ、応答が届かない）。"""
        import select

        port = server["port"]
        body = b"user=tester&password=" + b"x" * 3000
        head = f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/x-www-form-urlencoded\r\n"
        if origin:
            head += f"Origin: {origin}\r\n"
        head += f"Content-Length: {len(body)}\r\n\r\n"
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
            s.sendall(head.encode("ascii"))
            ready, _, _ = select.select([s], [], [], 0.5)
            assert not ready  # 本文が届く前に応答しない
            s.sendall(body)
            data = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        assert data.split(b"\r\n", 1)[0].endswith(b"403 Forbidden"), data[:80]

    def test_oversized_body_gets_a_clean_answer(self, server):
        port = server["port"]
        body = b"x" * (200 * 1024)
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
            s.sendall(f"POST /api/story HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            data = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        assert b" 413 " in data.split(b"\r\n", 1)[0]

    def test_logout_ends_session(self, server):
        _r, cookie = login(server)
        req(server, "POST", "/logout", "", cookie=cookie)
        r, _ = api_save(server, cookie, "x" * 16, "a")
        assert r.status == 401

    def test_forged_cookie_is_rejected(self, server):
        r, body = req(server, "GET", "/", cookie="forged-token")
        assert "<textarea" not in body.decode("utf-8")

    def test_thumbnails_need_login_and_cannot_escape(self, server):
        p = load_photos(server["root"])["photos"][0]
        _r, cookie = login(server)
        assert req(server, "GET", "/" + p["thumb"])[0].status == 404
        r, data = req(server, "GET", "/" + p["thumb"], cookie=cookie)
        assert r.status == 200 and r.getheader("Content-Type") == "image/webp" and data[:4] == b"RIFF"
        for bad in ("/thumbs/../config.json", "/thumbs/2026/..%2f..%2fconfig.json", "/config.json", "/stories.json"):
            assert req(server, "GET", bad, cookie=cookie)[0].status == 404

    def test_server_listens_only_on_this_pc(self, server):
        app = server["app"]
        srv = A.make_server(A.AdminApp(server["root"], None, _free_port()))
        try:
            assert srv.server_address[0] == "127.0.0.1"
        finally:
            srv.server_close()
        assert app.port == server["port"]


# ===========================================================================
# 小話の保存
# ===========================================================================
class TestStories:
    def test_save_update_and_delete(self, server):
        root = server["root"]
        pid = load_photos(root)["photos"][0]["id"]
        _r, cookie = login(server)
        r, body = api_save(server, cookie, pid, "  朝の散歩。\r\n\r\n川沿いで。  \n")
        assert r.status == 200 and body["text"] == "朝の散歩。\n\n川沿いで。"
        saved = json.loads((root / "stories.json").read_text(encoding="utf-8"))
        assert saved["stories"][pid]["text"] == "朝の散歩。\n\n川沿いで。" and saved["stories"][pid]["updated"].endswith("+09:00")
        assert C.load_stories(root) == {pid: "朝の散歩。\n\n川沿いで。"}
        r, body = api_save(server, cookie, pid, "書き直し")
        assert C.load_stories(root) == {pid: "書き直し"}
        r, body = api_save(server, cookie, pid, "   ")  # 空で保存すると削除
        assert r.status == 200 and C.load_stories(root) == {}
        assert not list(root.glob(".stories.json.*.tmp"))  # 一時ファイルが残らない

    def test_api_requires_session_and_header(self, server):
        pid = load_photos(server["root"])["photos"][0]["id"]
        assert api_save(server, None, pid, "a")[0].status == 401
        _r, cookie = login(server)
        assert api_save(server, cookie, pid, "a", headers={})[0].status == 403
        assert api_save(server, cookie, pid, "a", origin="http://evil.example")[0].status == 403
        assert not (server["root"] / "stories.json").exists()

    @pytest.mark.parametrize("pid,text,expect", [
        ("deadbeefdeadbeef", "a", "写真が見つかりません"),
        (None, "x" * (C.STORY_MAX_CHARS + 1), "1000 文字まで"),
        (None, "bell\x07", "制御文字"),
    ])
    def test_invalid_story_is_rejected(self, server, pid, text, expect):
        pid = pid or load_photos(server["root"])["photos"][0]["id"]
        _r, cookie = login(server)
        r, body = api_save(server, cookie, pid, text)
        assert r.status == 400 and expect in body["error"]
        assert not (server["root"] / "stories.json").exists()

    def test_max_length_story_is_accepted(self, server):
        pid = load_photos(server["root"])["photos"][0]["id"]
        _r, cookie = login(server)
        r, body = api_save(server, cookie, pid, "あ" * C.STORY_MAX_CHARS)
        assert r.status == 200 and len(body["text"]) == C.STORY_MAX_CHARS

    def test_page_escapes_story_text(self, server):
        pid = load_photos(server["root"])["photos"][0]["id"]
        _r, cookie = login(server)
        api_save(server, cookie, pid, "</textarea><script>alert(1)</script>")
        _r, body = req(server, "GET", "/", cookie=cookie)
        page = body.decode("utf-8")
        assert "<script>alert(1)" not in page and "&lt;/textarea&gt;&lt;script&gt;" in page


# ===========================================================================
# 小話の生成・検証
# ===========================================================================
class TestStoriesInBuild:
    def test_build_puts_stories_into_photos_json(self, built):
        doc = load_photos(built)
        a, b = doc["photos"][0]["id"], doc["photos"][2]["id"]
        C.save_stories(built, {a: "一枚目の話", b: "三枚目\nの話", "0123456789abcdef": "消えた写真の話"})
        r = run_build(built)
        assert r.returncode == 0, r.stdout
        assert "小話: 2件（写真が見つからない小話 1件）" in r.stdout
        doc = load_photos(built)
        assert doc["photos"][0]["story"] == "一枚目の話" and doc["photos"][2]["story"] == "三枚目\nの話"
        assert "story" not in doc["photos"][1]
        assert run_validate(built).returncode == 0

    def test_broken_stories_file_stops_build_without_changes(self, built):
        before = (built / "data" / "photos.json").read_bytes()
        (built / "stories.json").write_text("{ broken", encoding="utf-8")
        r = run_build(built)
        assert r.returncode == 1 and "stories.json" in r.stdout
        assert (built / "data" / "photos.json").read_bytes() == before

    @pytest.mark.parametrize("story", ["", " 前に空白", "x" * (C.STORY_MAX_CHARS + 1), "nul\x00", 5])
    def test_validator_rejects_bad_story(self, built, story):
        path = built / "data" / "photos.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["photos"][0]["story"] = story
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        assert any("story" in e for e in V.validate(built).errors)

    def test_publish_script_commits_stories(self):
        root = Path(__file__).resolve().parent.parent
        text = (root / "tools" / "publish.ps1").read_text(encoding="utf-8-sig")
        assert "'stories.json'" in text and "$OptionalPublishPaths" in text


# ===========================================================================
# 実際のブラウザーでの操作（Playwright がある場合）
# ===========================================================================
def test_login_and_save_in_a_real_browser(server):
    sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright 未導入（ブラウザー試験をスキップ）")
    root = server["root"]
    pid = load_photos(root)["photos"][1]["id"]
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium を起動できません: {exc}")
        try:
            page = browser.new_page()
            errors, thumbs = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("response", lambda r: thumbs.append(r.status) if "/thumbs/" in r.url else None)
            page.goto(f"http://127.0.0.1:{server['port']}/")
            page.fill("#user", "tester")
            page.fill("#password", "wrong-password")
            page.click("button[type=submit]")
            assert "ユーザー名またはパスワードが違います" in page.inner_text("body")
            page.fill("#user", "tester")
            page.fill("#password", PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_selector("textarea")
            form = page.locator(f"form.story[data-id='{pid}']")
            form.locator("textarea").fill("友だちと海へ。\n風が強かった。")
            assert "未保存" in form.locator(".status").inner_text()
            form.locator("button").click()
            form.locator(".status.saved").wait_for()  # このページは eval を禁止しているので、要素の出現で待つ
            assert form.locator(".status").inner_text() == "保存しました"
            assert C.load_stories(root) == {pid: "友だちと海へ。\n風が強かった。"}
            assert thumbs and set(thumbs) == {200}  # サムネイルが表示される
            page.click("text=ログアウト")
            page.wait_for_selector("#password")
            assert errors == []
        finally:
            browser.close()
