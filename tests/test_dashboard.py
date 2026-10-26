"""ダッシュボードの試験（同期仕様ID PV-SPEC-2026-10-v18 / 版 18.0）

PC の中だけで見るページ dashboard/index.html を、GoatCounter の代わり（FakeGoatCounter）と試験用の公開記録から作り、
見に来た人・サイトの中での見られ方・サイトの状態が出ること、API キーがページに入らないこと、
GoatCounter に届かないときも作れること（前回の値を使う）を確かめる。
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import make_dashboard as D
import pv_common as C
from pvtest_helpers import FakeGoatCounter, load_photos, make_image, run_build

TOKEN = "gc-secret-token-0123456789abcdef"
NOW = datetime(2026, 10, 25, 21, 30, tzinfo=C.JST)

PUBLISHED = """2026-10-24 17:45:21 [INFO] 写真ビューア公開 同期仕様ID PV-SPEC-2026-10-v18 / 版 18.0
2026-10-24 17:45:24 [INFO] 対象ファイル: 3件
2026-10-24 17:48:14 [INFO] 生成完了: 3件（新規変換 2 / 再利用 1）
167 passed, 3 skipped in 502.87s (0:08:22)
2026-10-24 17:56:45 [OK] 6/8 試験に合格しました
2026-10-24 17:56:56 [OK] commit しました: 61c8240 写真ビューアを更新: 3枚 (2026-10-24 17:56)
2026-10-24 17:57:06 [OK] 8/8 push しました（origin/main）。GitHub Pages への反映には数分かかります。
"""
STOPPED = """2026-10-25 03:29:42 [INFO] 生成完了: 3件（新規変換 0 / 再利用 3）
167 passed, 3 skipped in 251.53s (0:04:11)
2026-10-25 03:34:03 [ERROR] push に失敗しました。commit は PC 内に残っており、次回の publish.ps1 実行時に再送します。
"""
UNCHANGED = """2026-10-25 11:49:12 [INFO] 生成完了: 2件（新規変換 0 / 再利用 2）
167 passed, 3 skipped in 262.03s (0:04:22)
2026-10-25 11:53:46 [OK] 8/8 公開内容に変更はありません。commit / push は行いません。
"""


@pytest.fixture(autouse=True)
def no_api_pause(monkeypatch):
    monkeypatch.setattr(D, "API_PAUSE", 0)  # 本物の GoatCounter は1秒に4回まで。試験の代わりの受付先では待たない


@pytest.fixture
def site(project):
    """写真3枚のサイト（1枚は撮影情報なし＝ファイルの更新日時）と、3回分の公開記録。"""
    s = project / "source_photos"
    make_image(s / "a.jpg", exif_dt="2026:10:20 10:00:00")
    make_image(s / "b.jpg", exif_dt="2026:09:05 10:00:00")
    make_image(s / "noexif.jpg")
    assert run_build(project).returncode == 0
    logs = project / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "publish_20261024_174521_3908.log").write_text(PUBLISHED, encoding="utf-8")
    (logs / "publish_20261025_032932_6288.log").write_text(STOPPED, encoding="utf-8")
    (logs / "publish_20261025_114859_1816.log").write_text(UNCHANGED, encoding="utf-8")
    return project


def config(project: Path, **kw) -> None:
    cfg = {"siteTitle": "Daily"}
    cfg.update(kw)
    (project / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def page_of(project: Path) -> str:
    return (project / "dashboard" / "index.html").read_text(encoding="utf-8")


def visible_text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def test_dashboard_shows_visitors_views_and_site_status(site):
    config(site, goatcounter="daily-photos", goatcounterToken=TOKEN)
    ids = [p["id"] for p in load_photos(site)["photos"]]
    with FakeGoatCounter(ids, token=TOKEN) as fake:
        target, notes = D.build(site, api_base=fake.base, now=NOW)
    assert notes == [] and target == site / "dashboard" / "index.html"
    html = page_of(site)
    text = visible_text(html)
    # 1. 見に来た人（3つの期間。30日が最初に選ばれている）
    assert html.count('class="period p-') == 3 and '<body data-period="30">' in html
    for n in (7, 30, 90):
        assert f"直近{n}日" in text
    assert "日ごとの訪問者" in text and "見られている時間帯" in text
    assert "instagram.com" in text and "（直接・ブックマーク・LINE など）" in text
    assert "スマホ（幅600以下）" in text and "iOS" in text and "Safari" in text and "Japan" in text
    # 地域などの名前はそのまま書かない（ページに埋め込まれない）
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # 2. サイトの中での見られ方
    for name in ("散らばる", "並べる", "揺れる", "動かない一覧"):
        assert name in text
    assert "全画面でよく開かれた写真" in text and "../thumbs/" in html and "今はない写真" in html
    assert "全部の写真" in text and "10枚以上" not in text  # 写真が3枚なので「10枚以上」の段はない
    # 3. サイトの状態
    assert "写真の枚数" in text and re.search(r'class="hero">3<', html)
    assert "止まりました" in text and "push に失敗しました" in text and "変更なし" in text and "公開しました" in text
    assert "日付があやしい写真" in text and "ファイルの更新日時" in text
    assert "公開している容量" in text and "上限 1GB" in text
    # API キーはページにもキャッシュ以外にも書かない。問い合わせには付ける
    assert TOKEN not in html
    assert all(auth == f"Bearer {TOKEN}" for _, _, auth in fake.requests)
    assert len(fake.requests) == 21  # 3つの期間 × (今と前の期間の hits ＋ リンク元・OS・ブラウザー・画面・地域)
    # リンク元・OS などは、ページを開いた分（path_id 1 の "/"）だけ。番号で絞る（名前で探させない）
    detail = [q for path, q, _ in fake.requests if path.endswith("/stats/systems")]
    assert detail and all(q.get("include_paths") == "1" and "path_by_name" not in q for q in detail)
    cache = json.loads((site / "dashboard" / "cache.json").read_text(encoding="utf-8"))
    assert cache["code"] == "daily-photos" and TOKEN not in json.dumps(cache)


def test_charts_render_and_period_switch_in_a_browser(site):
    pw = pytest.importorskip("playwright.sync_api", reason="Playwright 未導入（ブラウザーでの確認をスキップ）")
    config(site, goatcounter="daily-photos", goatcounterToken=TOKEN)
    ids = [p["id"] for p in load_photos(site)["photos"]]
    with FakeGoatCounter(ids, token=TOKEN) as fake:
        D.build(site, api_base=fake.base, now=NOW)
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium を起動できません: {exc}")
        try:
            for kw in ({"viewport": {"width": 1280, "height": 900}}, dict(**p.devices["iPhone 13"])):
                ctx = browser.new_context(**kw)
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto((site / "dashboard" / "index.html").resolve().as_uri())
                page.wait_for_selector(".p-30 .chart svg")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")  # 横にはみ出さない
                bars = page.locator(".p-30 .chart").first.locator(".bar").count()
                assert bars >= 20  # 30日分の棒（訪問者のいない日は描かない）
                sizes = page.evaluate("[...document.querySelectorAll('.p-30 .chart .tick')].map(t => parseFloat(getComputedStyle(t).fontSize))")
                assert sizes and min(sizes) >= 10.5  # 目盛りの文字はスマホでも小さくならない
                assert page.is_hidden(".p-7") and page.is_visible(".p-30")
                page.click(".filters button[data-p='7']")
                assert page.is_visible(".p-7") and page.is_hidden(".p-30")
                assert page.locator(".p-7 .chart").first.locator(".bar").count() >= 5
                page.hover(".p-7 .chart .hit >> nth=2")
                assert page.is_visible("#tip") and "人" in page.inner_text("#tip")
                page.reload()
                assert page.get_attribute(".filters button[data-p='7']", "aria-pressed") == "true"  # 選んだ期間を覚える
                assert errors == []
                ctx.close()
        finally:
            browser.close()


def test_unreachable_goatcounter_uses_the_last_values(site):
    config(site, goatcounter="daily-photos", goatcounterToken=TOKEN)
    ids = [p["id"] for p in load_photos(site)["photos"]]
    with FakeGoatCounter(ids, token=TOKEN) as fake:
        D.build(site, api_base=fake.base, now=NOW)
        good = page_of(site)
    with FakeGoatCounter(ids, token=TOKEN, status=500) as fake:
        _target, notes = D.build(site, api_base=fake.base, now=NOW)
    html = page_of(site)
    assert notes and "HTTP 500" in notes[0]
    assert "アクセス数を読めませんでした" in html and "に読んだ値です" in html
    assert "instagram.com" in html and html.count('class="period p-') == 3  # 前回の値で出す
    assert "instagram.com" in good


def test_one_list_failing_keeps_the_rest(site):
    """リンク元などの一覧の1つが GoatCounter でエラーになっても、訪問者の数やほかの一覧は出す。どれが読めなかったかを書く。"""
    config(site, goatcounter="daily-photos", goatcounterToken=TOKEN)
    ids = [p["id"] for p in load_photos(site)["photos"]]
    with FakeGoatCounter(ids, token=TOKEN, detail_status={"toprefs": 404, "locations": 500}) as fake:
        _target, notes = D.build(site, api_base=fake.base, now=NOW)
    html = page_of(site)
    text = visible_text(html)
    assert len(notes) == 1 and notes[0].startswith("一部の一覧を読めませんでした")
    assert "リンク元: GoatCounter が「見つかりません」と答えました（/stats/toprefs）" in notes[0]
    assert "地域: GoatCounter からエラーが返りました（HTTP 500、/stats/locations" in notes[0]
    assert "コードが見つかりません" not in html  # 404 はコードの間違いではない
    assert "一部の一覧を読めませんでした" in text and "ほかの数は読めています" in text
    assert text.count("読めませんでした（上のお知らせ）") == 6  # 2つの一覧 × 3つの期間
    assert "instagram.com" not in html and "Japan" not in html
    assert "iOS" in text and "Safari" in text and "スマホ（幅600以下）" in text and "散らばる" in text
    assert re.search(r'class="hero">\d', html)
    cache = json.loads((site / "dashboard" / "cache.json").read_text(encoding="utf-8"))
    assert cache["periods"]["7"]["refs"] is None and cache["failed"]


def test_wrong_code_is_explained(site):
    config(site, goatcounter="nosuch", goatcounterToken=TOKEN)
    with FakeGoatCounter([], token=TOKEN, no_site=True) as fake:
        _target, notes = D.build(site, api_base=fake.base, now=NOW)
    assert notes and "GoatCounter のコードが見つかりません" in notes[0]
    assert len(fake.requests) == 1  # 続けて問い合わせない


def test_hits_without_path_ids_use_path_names(site):
    config(site, goatcounter="daily-photos", goatcounterToken=TOKEN)
    ids = [p["id"] for p in load_photos(site)["photos"]]
    with FakeGoatCounter(ids, token=TOKEN, path_ids=False) as fake:
        _target, notes = D.build(site, api_base=fake.base, now=NOW)
    assert notes == []
    detail = [q for path, q, _ in fake.requests if path.endswith("/stats/toprefs")]
    assert detail and all(q.get("path_by_name") == "true" and q.get("include_paths") == "/" for q in detail)
    assert "instagram.com" in page_of(site)


@pytest.mark.parametrize("status,words", [(401, "API キーが正しくありません"), (403, "Read statistics")])
def test_wrong_key_is_explained(site, status, words):
    config(site, goatcounter="daily-photos", goatcounterToken=TOKEN)
    with FakeGoatCounter([], token=TOKEN if status != 401 else "another-token-000000", status=status if status != 401 else 200) as fake:
        _target, notes = D.build(site, api_base=fake.base, now=NOW)
    assert notes and words in notes[0]
    html = page_of(site)
    assert words in html and "サイトの状態" in html  # 3 は出す


def test_without_goatcounter_the_page_explains_the_setup(site):
    config(site)
    _target, notes = D.build(site, now=NOW)
    html = page_of(site)
    assert notes == [] and "GoatCounter の設定が必要です" in html and "サイトの状態" in html
    config(site, goatcounter="daily-photos")
    D.build(site, now=NOW)
    assert "API キーがまだありません" in page_of(site)


def test_command_line_never_fails_the_publish_on_network_errors(site):
    config(site, goatcounter="daily-photos", goatcounterToken=TOKEN)
    with socket.socket() as s:  # 誰も受け付けていない番号
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = subprocess.run(
        [sys.executable, str(site / "tools" / "make_dashboard.py"), "--root", str(site), "--api-base", f"http://127.0.0.1:{port}/api/v0"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "注意: GoatCounter に接続できませんでした" in r.stdout and "ダッシュボードを作りました" in r.stdout
    assert (site / "dashboard" / "index.html").is_file()


def test_publish_logs_are_read_in_order(site):
    pubs = D.read_publishes(site)
    assert [p["result"] for p in pubs] == ["published", "stopped", "unchanged"]
    assert pubs[0]["count"] == 3 and pubs[0]["new"] == 2 and pubs[0]["tests"] == 167
    assert pubs[1]["message"].startswith("push に失敗しました")
    assert pubs[0]["time"].startswith("2026-10-24T17:45:21")


def test_period_ranges_are_whole_days_in_japan_time():
    start, end = D.period_range(7, NOW)
    assert start.isoformat() == "2026-10-19T00:00:00+09:00" and end <= NOW.replace(hour=22, minute=0)
    pstart, pend = D.period_range(7, NOW, back=1)
    assert pstart.isoformat() == "2026-10-12T00:00:00+09:00" and pend == start
    assert D._iso_utc(start) == "2026-10-18T15:00:00Z"
