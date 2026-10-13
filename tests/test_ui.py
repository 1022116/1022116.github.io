"""ブラウザー試験（同期仕様ID PV-SPEC-2026-10-v5 / 版 5.0）

Playwright（Chromium）でスマートフォン画面をエミュレートし、試験画像から生成したサイトを
GitHub Pages と同じ「リポジトリ名のサブパス」で配信して確認する。
分類: 回帰（一覧・年タブ・スライド・遅延読込・エラー表示・アクセシビリティ）/ 操作（連打・スワイプ途中で閉じる）/
      保存（年選択・スクロール位置・再読込）/ 競合（タイマー）/ 境界（0件・1件）/ 異常（JSON・画像）/ シナリオ
注意: これは実機ではなくエミュレーション。iPhone Safari・Android Chrome の実機確認は別途必要。
"""
from __future__ import annotations

import contextlib
import functools
import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pvtest_helpers import (
    find_powershell,
    in_venv,
    init_git_project,
    load_photos,
    make_image,
    make_project,
    run_build,
    run_publish,
    run_validate,
)

playwright_api = pytest.importorskip("playwright.sync_api", reason="Playwright 未導入（UI試験をスキップ）")

PALETTE = [(222, 94, 84), (240, 170, 70), (90, 160, 120), (70, 120, 200), (150, 100, 190), (60, 60, 70)]
YEARS = ((2026, 7), (2025, 40), (2024, 30), (2023, 2))  # 合計79枚（初回表示60枚＋追加表示）


# ---------------------------------------------------------------------------
# 配信・ブラウザー
# ---------------------------------------------------------------------------
class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # ブラウザーが意図的に中断した接続（画像の読込取消など）は記録しない


@contextlib.contextmanager
def serve(directory: Path):
    srv = _QuietServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(directory)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def build_gallery(project: Path, plan) -> dict:
    n = 0
    for year, count in plan:
        for k in range(count):
            n += 1
            month, day = 1 + (k * 5) % 12, 1 + (k * 7) % 28
            make_image(
                project / "source_photos" / str(year) / f"IMG_{n:04d}.jpg",
                [(640, 480), (480, 640), (560, 560)][n % 3],
                PALETTE[n % len(PALETTE)],
                exif_dt=f"{year}:{month:02d}:{day:02d} {n % 24:02d}:{(n * 7) % 60:02d}:00",
                label=str(n),
            )
    r = run_build(project)
    assert r.returncode == 0, r.stdout
    assert run_validate(project).returncode == 0
    return load_photos(project)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    main = make_project(root / "main" / "photo-viewer")
    empty = make_project(root / "empty" / "photo-viewer")
    single = make_project(root / "single" / "photo-viewer")
    doc = build_gallery(main, YEARS)
    build_gallery(empty, ())
    build_gallery(single, ((2025, 1),))
    with serve(root) as base:
        yield {
            "base": base,
            "main": f"{base}/main/photo-viewer/",
            "empty": f"{base}/empty/photo-viewer/",
            "single": f"{base}/single/photo-viewer/",
            "doc": doc,
            "ids": [p["id"] for p in doc["photos"]],
        }


@pytest.fixture(scope="module")
def pw():
    p = playwright_api.sync_playwright().start()
    try:
        browser = p.chromium.launch()
    except Exception as exc:  # ブラウザー未インストール
        p.stop()
        pytest.skip(f"Chromium を起動できません（python -m playwright install chromium）: {exc}")
    yield p, browser
    browser.close()
    p.stop()


class Watch:
    def __init__(self, page):
        self.page_errors, self.console_errors, self.requests = [], [], []
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        page.on("console", lambda m: self.console_errors.append(m.text) if m.type == "error" else None)
        page.on("request", lambda r: self.requests.append(r.url))

    def assert_clean(self):
        assert self.page_errors == [], self.page_errors
        assert self.console_errors == [], self.console_errors


@pytest.fixture
def phone(pw):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"])
    yield ctx
    ctx.close()


@pytest.fixture
def desktop(pw):
    _p, browser = pw
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    yield ctx
    ctx.close()


def open_page(ctx, url, state="ready"):
    page = ctx.new_page()
    w = Watch(page)
    page.goto(url)
    page.wait_for_selector(f"html[data-pv={state}]")
    return page, w


def tile_ids(page):
    return page.eval_on_selector_all(".tile", "els => els.map(e => e.dataset.id)")


def counter(page):
    return page.inner_text("#viewer-counter")


def viewer_open(page):
    return page.evaluate("document.getElementById('viewer').open")


def tab_texts(page):
    return page.eval_on_selector_all(".year-tab", "els => els.map(e => e.textContent)")


def selected_tab(page):
    return page.eval_on_selector(".year-tab[aria-selected=true]", "e => e.textContent")


def scroll_y(page):
    return page.evaluate("Math.round(window.pageYOffset)")


def anchor_id(page):
    """年の行の下に見えている最初の写真のID（アプリのスクロール保存と同じ規則）。"""
    return page.evaluate(
        """() => { const top = document.querySelector('.app-header').getBoundingClientRect().bottom;
                   const li = [...document.querySelectorAll('#grid > li')].find(e => e.getBoundingClientRect().bottom > top + 1);
                   return li ? li.querySelector('.tile').dataset.id : null; }"""
    )


def stage_center(page):
    b = page.locator("#viewer-stage").bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


class Touch:
    """CDP のタッチ入力（実際のスワイプと同じく pointer イベントが発生する）。"""

    def __init__(self, page):
        self.cdp = page.context.new_cdp_session(page)

    def start(self, x, y):
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 1}]})

    def move(self, x, y):
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y, "id": 1}]})

    def end(self):
        self.cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    def swipe(self, x, y, dx, dy=0, steps=10):
        self.start(x, y)
        for i in range(1, steps + 1):
            self.move(x + dx * i / steps, y + dy * i / steps)
        self.end()


# ===========================================================================
# 回帰: 一覧・年タブ
# ===========================================================================
@pytest.mark.parametrize("device", ["iPhone 13", "iPhone SE", "Pixel 5"])
def test_portfolio_grid_on_phones(pw, site, device):
    """S-05（v5）: 先頭の1枚を元の縦横比で大きく、以降は正方形の2列。各写真に番号と撮影日。"""
    p, browser = pw
    ctx = browser.new_context(**p.devices[device])
    try:
        page, w = open_page(ctx, site["main"])
        vw = page.evaluate("document.documentElement.clientWidth")
        grid = page.locator("#grid").bounding_box()
        assert 12 <= grid["x"] <= 24 and abs((grid["x"] + grid["width"]) - (vw - grid["x"])) < 1  # 左右に同じ余白
        page.wait_for_function("document.querySelector('#grid > li img').naturalWidth > 0")
        hero = page.evaluate(
            """(() => { const li = document.querySelector('#grid > li'); const img = li.querySelector('img');
               const r = img.getBoundingClientRect(); return { hero: li.classList.contains('is-hero'),
               usesDetail: img.getAttribute('src').startsWith('photos/'), ratio: r.width / r.height,
               natural: img.naturalWidth / img.naturalHeight, liW: li.getBoundingClientRect().width }; })()"""
        )
        assert hero["hero"] and hero["usesDetail"]
        assert abs(hero["liW"] - grid["width"]) < 1  # 横幅いっぱいの行を使う
        assert abs(hero["ratio"] - hero["natural"]) < 0.02  # 元の縦横比
        boxes = page.eval_on_selector_all(
            "#grid > li:not(.is-hero) .tile", "els => els.slice(0, 3).map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })"
        )
        (x1, y1, w1, h1), (x2, y2, w2, _), (x3, y3, _, _) = boxes
        assert y1 == y2 and y3 > y1 and x3 == x1  # 2列で折り返す
        assert abs(w1 - h1) < 0.6 and abs(w1 - w2) < 0.6  # 正方形・同じ大きさ
        assert 8 <= x2 - (x1 + w1) <= 24  # 写真の間に余白
        caps = page.eval_on_selector_all("#grid > li .tile-cap", "els => els.slice(0, 3).map(e => [e.querySelector('.tile-no').textContent, e.querySelector('.tile-date').textContent])")
        n = len(site["ids"])
        assert [c[0] for c in caps] == [str(n), str(n - 1), str(n - 2)]  # 目録番号（新しいほど大きい）
        assert all(re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", c[1]) for c in caps)
        assert caps[0][1] == site["doc"]["photos"][0]["date"][:10].replace("-", ".")
        w.assert_clean()
    finally:
        ctx.close()


def test_desktop_uses_three_columns_with_wide_gaps(desktop, site):
    page, w = open_page(desktop, site["main"])
    tops = page.eval_on_selector_all("#grid > li:not(.is-hero) .tile", "els => els.slice(0, 4).map(e => e.getBoundingClientRect().top)")
    assert tops[0] == tops[1] == tops[2] < tops[3]
    gap = page.evaluate("(() => { const t = document.querySelectorAll('#grid > li:not(.is-hero) .tile'); return t[1].getBoundingClientRect().left - t[0].getBoundingClientRect().right; })()")
    assert gap >= 16
    w.assert_clean()


def test_hero_space_is_reserved_before_image_loads(phone, site):
    """先頭の大きな写真は読み込み前から場所を確保し、読み込み後に一覧がずれない。"""
    page = phone.new_page()
    w = Watch(page)
    held = []
    first_src = site["doc"]["photos"][0]["src"]
    page.route(f"**/{first_src}", lambda route: held.append(route))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    before = page.evaluate("document.querySelector('#grid > li:nth-child(2)').getBoundingClientRect().top")
    hero_h = page.evaluate("document.querySelector('#grid > li.is-hero .tile').getBoundingClientRect().height")
    assert hero_h > 100
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(50)
    held[0].continue_()
    page.wait_for_function("document.querySelector('#grid > li.is-hero img').naturalWidth > 0")
    after = page.evaluate("document.querySelector('#grid > li:nth-child(2)').getBoundingClientRect().top")
    assert abs(after - before) < 1
    w.assert_clean()


def test_cover_title_meta_and_catalogue_numbers(phone, site):
    page, w = open_page(phone, site["main"])
    assert page.inner_text("#app-title") == "写真"
    assert page.evaluate("document.getElementById('app-title').classList.contains('is-ja')")
    years = site["doc"]["years"]
    assert page.inner_text("#photo-range") == f"{years[-1]}–{years[0]}"
    assert page.get_attribute("#tab-2025", "data-count") == "40"
    # 年で絞り込んでも目録番号は変わらない（通し番号）
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_function("document.querySelectorAll('#grid > li').length === 79")
    all_nos = dict(zip(tile_ids(page), page.eval_on_selector_all("#grid > li .tile-no", "els => els.map(e => e.textContent)")))
    page.click("#tab-2024")
    ids24 = tile_ids(page)
    nos24 = page.eval_on_selector_all("#grid > li .tile-no", "els => els.map(e => e.textContent)")
    assert [all_nos[i] for i in ids24] == nos24
    assert page.locator("#grid > li.is-hero").count() == 1  # 絞り込み後も先頭は大きく
    page.click(".tile[data-index='0']")
    assert page.inner_text("#viewer-no") == "No. " + nos24[0]
    w.assert_clean()


def test_latin_title_uses_bundled_font(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"])
    try:
        page = ctx.new_page()
        w = Watch(page)

        def handler(route):
            resp = route.fetch()
            doc = resp.json()
            doc["title"] = "Photographs"
            route.fulfill(response=resp, json=doc)

        page.route("**/data/photos.json", handler)
        page.goto(site["main"])
        page.wait_for_selector("html[data-pv=ready]")
        assert page.inner_text("#app-title") == "Photographs"
        assert not page.evaluate("document.getElementById('app-title').classList.contains('is-ja')")
        assert page.evaluate("document.fonts.check('200 64px \"Albert Sans\"')")
        assert any(u == site["main"] + "fonts/albert-sans-latin-wght-normal.woff2" for u in w.requests)
        assert page.title() == "Photographs"
        lines = page.evaluate("Math.round(document.getElementById('app-title').getBoundingClientRect().height / parseFloat(getComputedStyle(document.getElementById('app-title')).lineHeight))")
        assert lines == 1  # 画面幅いっぱいでも1行に収まる
        w.assert_clean()
    finally:
        ctx.close()


def test_year_tabs_are_generated_newest_first(phone, site):
    page, w = open_page(phone, site["main"])
    assert tab_texts(page) == ["すべて", "2026", "2025", "2024", "2023"]
    assert selected_tab(page) == "すべて"
    assert page.inner_text("#photo-count") == "79枚"
    page.click("#tab-2025")
    expected = [p["id"] for p in site["doc"]["photos"] if p["year"] == 2025]
    assert tile_ids(page) == expected and len(expected) == 40
    assert page.inner_text("#photo-count") == "2025年の40枚"
    w.assert_clean()


def test_order_matches_json_newest_first(phone, site):
    page, w = open_page(phone, site["main"])
    assert tile_ids(page) == site["ids"][:60]
    dates = [p["date"] for p in site["doc"]["photos"]]
    assert dates == sorted(dates, reverse=True)
    w.assert_clean()


def test_lazy_loading_and_incremental_rendering(phone, site):
    page, w = open_page(phone, site["main"])
    page.wait_for_timeout(500)
    assert page.locator(".tile").count() == 60  # 最初は60枚だけDOMに追加
    assert page.eval_on_selector_all(".tile img", "els => els.every(e => e.loading === 'lazy')")
    first = {u for u in w.requests if "/thumbs/" in u}
    assert len(first) < 79
    for _ in range(30):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(120)
        if page.locator(".tile").count() == 79:
            break
    assert page.locator(".tile").count() == 79
    page.wait_for_timeout(800)
    assert len({u for u in w.requests if "/thumbs/" in u}) > len(first)
    assert len(set(tile_ids(page))) == 79
    w.assert_clean()


# ===========================================================================
# 回帰: 全画面スライド
# ===========================================================================
def test_viewer_navigation_with_buttons_and_keys(phone, site):
    page, w = open_page(phone, site["main"])
    ids = site["ids"]
    page.click(".tile[data-index='2']")
    assert viewer_open(page) and counter(page) == "3 / 79"
    assert page.get_attribute("#viewer-img", "data-id") == ids[2]
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    page.click("#viewer-next")
    assert counter(page) == "4 / 79"
    page.click("#viewer-prev")
    page.click("#viewer-prev")
    assert counter(page) == "2 / 79"
    page.keyboard.press("ArrowRight")
    assert counter(page) == "3 / 79"
    page.keyboard.press("ArrowLeft")
    assert counter(page) == "2 / 79"
    page.keyboard.press("Home")
    assert counter(page) == "1 / 79" and page.is_disabled("#viewer-prev")
    page.keyboard.press("ArrowLeft")
    assert counter(page) == "1 / 79"
    page.keyboard.press("End")
    assert counter(page) == "79 / 79" and page.is_disabled("#viewer-next")
    assert page.get_attribute("#viewer-img", "data-id") == ids[78]
    page.keyboard.press("Escape")
    assert not viewer_open(page)
    assert page.evaluate("document.documentElement.classList.contains('viewer-open')") is False
    assert page.evaluate("document.activeElement.dataset.index") == "78"  # 最後に見た写真へフォーカスが戻る
    w.assert_clean()


def test_detail_image_keeps_aspect_ratio(phone, site):
    page, w = open_page(phone, site["main"])
    for i in range(3):
        page.click(f".tile[data-index='{i}']")
        page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
        r = page.evaluate(
            "(() => { const i = document.getElementById('viewer-img'); const b = i.getBoundingClientRect();"
            " return [b.width / b.height, i.naturalWidth / i.naturalHeight]; })()"
        )
        assert abs(r[0] - r[1]) < 0.02
        page.click("#viewer-close")
    w.assert_clean()


def test_swipe_left_and_right(phone, site):
    page, w = open_page(phone, site["main"])
    page.click(".tile[data-index='0']")
    t = Touch(page)
    x, y = stage_center(page)
    t.swipe(x + 100, y, -220)
    assert counter(page) == "2 / 79"
    t.swipe(x - 100, y, 220)
    assert counter(page) == "1 / 79"
    t.swipe(x - 100, y, 220)  # 先頭ではそれ以上戻らない
    assert counter(page) == "1 / 79"
    t.swipe(x, y, -20)  # 小さな動きはスワイプとみなさない
    assert counter(page) == "1 / 79"
    t.swipe(x, y + 100, 0, -250)  # 縦方向は無視
    assert counter(page) == "1 / 79"
    page.wait_for_timeout(300)
    assert page.evaluate("document.getElementById('viewer-img').style.transform") == ""
    w.assert_clean()


# ===========================================================================
# 操作: 連打・スワイプ途中で閉じる
# ===========================================================================
def test_rapid_next_clicks(phone, site):
    page, w = open_page(phone, site["main"])
    page.click(".tile[data-index='0']")
    for _ in range(12):
        page.locator("#viewer-next").click(delay=0)
    assert counter(page) == "13 / 79"
    page.evaluate("() => { for (let i = 0; i < 300; i++) document.getElementById('viewer-next').click(); }")
    assert counter(page) == "79 / 79"
    assert page.get_attribute("#viewer-img", "data-id") == site["ids"][78]
    assert page.evaluate("document.activeElement.id") in ("viewer-prev", "viewer-close")  # 無効化されたボタンにフォーカスを残さない
    page.evaluate("() => { for (let i = 0; i < 300; i++) document.getElementById('viewer-prev').click(); }")
    assert counter(page) == "1 / 79"
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    assert not page.evaluate("document.getElementById('viewer-stage').classList.contains('is-loading')")
    w.assert_clean()


def test_rapid_year_tab_clicks(phone, site):
    page, w = open_page(phone, site["main"])
    page.evaluate("() => { const t = document.querySelectorAll('.year-tab'); for (let i = 0; i < 49; i++) t[i % t.length].click(); }")
    assert selected_tab(page) == "2024"  # 48 % 5 = 3
    for _ in range(5):
        page.click("#tab-2023")
        page.click("#tab-all")
    page.click("#tab-2025")
    expected = [p["id"] for p in site["doc"]["photos"] if p["year"] == 2025]
    assert tile_ids(page) == expected
    page.focus("#tab-2025")
    for _ in range(12):  # キー連打（2025 の位置 2 から 12 回 → (2 + 12) % 5 = 4 → 2023）
        page.keyboard.press("ArrowRight")
    assert selected_tab(page) == "2023" and len(tile_ids(page)) == 2
    assert page.locator(".year-tab[aria-selected=true]").count() == 1
    w.assert_clean()


def test_rapid_close_and_reopen(phone, site):
    page, w = open_page(phone, site["main"])
    page.click(".tile[data-index='4']")
    page.evaluate("() => { for (let i = 0; i < 10; i++) document.getElementById('viewer-close').click(); }")
    assert not viewer_open(page)
    for _ in range(3):
        page.keyboard.press("Escape")
    page.evaluate(
        "() => { const t = document.querySelector('.tile[data-index=\"1\"]'); const c = document.getElementById('viewer-close');"
        " for (let i = 0; i < 20; i++) { t.click(); c.click(); } }"
    )
    assert not viewer_open(page)
    page.click(".tile[data-index='5']")
    assert viewer_open(page) and counter(page) == "6 / 79"
    page.click("#viewer-close")
    assert not viewer_open(page)
    w.assert_clean()


@pytest.mark.parametrize("how", ["escape", "close_button"])
def test_close_during_swipe(phone, site, how):
    page, w = open_page(phone, site["main"])
    page.click(".tile[data-index='0']")
    t = Touch(page)
    x, y = stage_center(page)
    t.start(x + 100, y)
    for i in range(1, 6):
        t.move(x + 100 - 30 * i, y)
    assert "translate3d(-" in page.evaluate("document.getElementById('viewer-img').style.transform")
    if how == "escape":
        page.keyboard.press("Escape")
    else:
        page.evaluate("document.getElementById('viewer-close').click()")
    assert not viewer_open(page)
    t.move(x - 200, y)
    t.end()  # 閉じた後に指を離しても次へ進まない
    page.wait_for_timeout(200)
    assert not viewer_open(page)
    page.click(".tile[data-index='0']")
    assert counter(page) == "1 / 79"
    assert page.evaluate("document.getElementById('viewer-img').style.transform") == ""
    assert not page.evaluate("document.getElementById('viewer-stage').classList.contains('is-dragging')")
    t.swipe(x + 100, y, -220)  # 再度開いた後のスワイプは正常
    assert counter(page) == "2 / 79"
    w.assert_clean()


# ===========================================================================
# 保存: 年選択・スクロール位置・再読込
# ===========================================================================
def test_year_and_scroll_position_restored_after_reload(phone, site):
    page, w = open_page(phone, site["main"])
    page.click("#tab-2025")
    page.evaluate("window.scrollTo(0, 900)")
    page.wait_for_timeout(500)
    y, anchor = scroll_y(page), anchor_id(page)
    assert y == 900 and anchor
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert selected_tab(page) == "2025"
    assert abs(scroll_y(page) - y) <= 2 and anchor_id(page) == anchor
    w.assert_clean()


def test_deep_scroll_beyond_first_batch_is_restored(phone, site):
    page, w = open_page(phone, site["main"])
    for _ in range(30):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(100)
    page.wait_for_timeout(500)
    y, anchor = scroll_y(page), anchor_id(page)
    assert page.locator(".tile").count() == 79 and site["ids"].index(anchor) >= 60
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert abs(scroll_y(page) - y) <= 2 and anchor_id(page) == anchor
    w.assert_clean()


def test_new_tab_restores_year_but_starts_at_top(phone, site):
    page, _ = open_page(phone, site["main"])
    page.click("#tab-2024")
    page.evaluate("window.scrollTo(0, 400)")
    page.wait_for_timeout(500)
    page2, w2 = open_page(phone, site["main"])  # 同じブラウザーの新しいタブ
    assert selected_tab(page2) == "2024"
    assert scroll_y(page2) == 0
    w2.assert_clean()


def test_tab_switch_cancels_pending_scroll_save(phone, site):
    """スクロール保存タイマーと年タブ切替の競合: 古い位置が新しい年として保存されない。"""
    page, w = open_page(phone, site["main"])
    page.evaluate("() => { window.scrollTo(0, 1500); document.getElementById('tab-2025').click(); }")
    page.wait_for_timeout(600)
    saved = json.loads(page.evaluate("sessionStorage.getItem('pv:scroll')"))
    assert saved["year"] == "2025" and saved["y"] == 0
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert selected_tab(page) == "2025" and scroll_y(page) == 0
    w.assert_clean()


def test_removed_year_and_broken_storage_do_not_come_back(phone, site):
    page, w = open_page(phone, site["main"])
    # 離脱時の保存（pagehide）より後、app.js より前に書き込むため init script で注入する
    page.add_init_script(
        "localStorage.setItem('pv:selectedYear', '2019');"
        "sessionStorage.setItem('pv:scroll', JSON.stringify({v: 1, year: 'all', anchorId: 'deadbeefdeadbeef', delta: 0, y: 500}));"
    )
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert selected_tab(page) == "すべて" and "2019" not in tab_texts(page)
    assert page.evaluate("localStorage.getItem('pv:selectedYear')") == "all"
    assert abs(scroll_y(page) - 500) <= 2  # 基準写真が消えていれば保存位置付近へ
    page.add_init_script("sessionStorage.setItem('pv:scroll', '{broken');")
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    assert scroll_y(page) == 0
    w.assert_clean()


def test_works_without_storage(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"])
    ctx.add_init_script(
        "Object.defineProperty(window, 'localStorage', {get() { throw new Error('denied'); }});"
        "Object.defineProperty(window, 'sessionStorage', {get() { throw new Error('denied'); }});"
    )
    try:
        page, w = open_page(ctx, site["main"])
        page.click("#tab-2024")
        page.evaluate("window.scrollTo(0, 300)")
        page.wait_for_timeout(400)
        page.click(".tile[data-index='0']")
        assert counter(page) == "1 / 30"
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 境界: 0件・1件
# ===========================================================================
def test_empty_gallery(phone, site):
    page, w = open_page(phone, site["empty"], state="empty")
    assert page.inner_text("#status-text") == "写真はまだありません。"
    assert tab_texts(page) == ["すべて"] and page.locator(".tile").count() == 0
    assert page.is_hidden("#status-retry")
    w.assert_clean()


def test_single_photo(phone, site):
    page, w = open_page(phone, site["single"])
    assert tab_texts(page) == ["すべて", "2025"] and page.locator(".tile").count() == 1
    page.click(".tile")
    assert counter(page) == "1 / 1"
    assert page.is_disabled("#viewer-prev") and page.is_disabled("#viewer-next")
    page.keyboard.press("ArrowRight")
    x, y = stage_center(page)
    Touch(page).swipe(x + 100, y, -220)
    assert counter(page) == "1 / 1"
    page.click("#viewer-close")
    assert not viewer_open(page)
    w.assert_clean()


# ===========================================================================
# 異常: JSON失敗・旧形式・読込中・画像失敗
# ===========================================================================
@pytest.mark.parametrize(
    "kind,expect",
    [("404", "HTTP 404"), ("broken", "JSONを解析できません"), ("abort", "通信状態"), ("shape", "形式が正しくありません")],
)
def test_json_failure_is_shown_and_retry_recovers(phone, site, kind, expect):
    page = phone.new_page()
    w = Watch(page)

    def handler(route):
        if kind == "404":
            route.fulfill(status=404, body="not found")
        elif kind == "broken":
            route.fulfill(status=200, content_type="application/json", body="{ broken")
        elif kind == "shape":
            route.fulfill(status=200, content_type="application/json", body='{"items": 1}')
        else:
            route.abort()

    page.route("**/data/photos.json", handler)
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=error]")
    assert expect in page.inner_text("#status-text")
    assert page.is_visible("#status-retry") and page.locator(".tile").count() == 0
    page.unroute("**/data/photos.json")
    page.click("#status-retry")
    page.wait_for_selector("html[data-pv=ready]")
    assert page.locator(".tile").count() == 60
    assert w.page_errors == []


def test_legacy_fiscal_year_json_stops_clearly(phone, site):
    page = phone.new_page()
    w = Watch(page)
    legacy = [{"id": "abc", "src": "photos/2025/a.webp", "thumb": "thumbs/2025/a.webp", "date": "2025-03-31T10:00:00+09:00", "fiscalYear": 2024}]
    page.route("**/data/photos.json", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"photos": legacy})))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=error]")
    assert "旧形式（fiscalYear）" in page.inner_text("#status-text")
    assert page.locator(".tile").count() == 0 and page.locator(".year-tab").count() == 0
    assert w.page_errors == []


def test_loading_state_is_shown(phone, site):
    page = phone.new_page()
    w = Watch(page)
    held = []
    page.route("**/data/photos.json", lambda route: held.append(route))
    page.goto(site["main"], wait_until="domcontentloaded")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(30)
    assert held
    assert page.get_attribute("#status", "data-state") == "loading"
    assert page.inner_text("#status-text") == "読み込み中…"
    assert page.get_attribute("#status", "aria-busy") == "true" and page.get_attribute("#grid", "aria-busy") == "true"
    assert page.is_visible("#status .spinner")
    held[0].continue_()
    page.wait_for_selector("html[data-pv=ready]")
    assert page.is_hidden("#status") and page.get_attribute("#grid", "aria-busy") == "false"
    w.assert_clean()


def test_broken_images_are_shown_and_do_not_block(phone, site):
    page = phone.new_page()
    w = Watch(page)
    p0 = site["doc"]["photos"][0]
    page.route(f"**/{p0['thumb']}", lambda r: r.fulfill(status=404, body=""))
    page.route(f"**/{p0['src']}", lambda r: r.fulfill(status=404, body=""))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_selector(".tile[data-index='0'].is-broken")
    assert page.is_visible(".tile[data-index='0'] .tile-fallback")
    page.click(".tile[data-index='0']")
    page.wait_for_selector("#viewer-error:not([hidden])")
    assert page.inner_text("#viewer-error") == "画像を読み込めませんでした"
    page.click("#viewer-next")
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    assert page.is_hidden("#viewer-error") and counter(page) == "2 / 79"
    assert w.page_errors == []


# ===========================================================================
# 回帰: アクセシビリティ・reduced-motion・相対URL
# ===========================================================================
def test_accessibility_basics(desktop, site):
    page, w = open_page(desktop, site["main"])
    assert page.get_attribute("html", "lang") == "ja"
    assert page.locator("[role=tablist]").count() == 1
    tabs = page.locator("[role=tab]")
    assert tabs.count() == 5
    assert page.locator("[role=tab][aria-selected=true]").count() == 1
    assert page.locator("[role=tab][tabindex='0']").count() == 1
    assert page.eval_on_selector_all("[role=tab]", "els => els.every(e => e.getAttribute('aria-controls') === 'grid-panel')")
    assert page.get_attribute("#grid-panel", "aria-labelledby") == "tab-all"
    assert page.get_attribute("#tab-2025", "aria-label") == "2025年（40枚）"
    # キーボードでタブ切替
    page.focus("#tab-all")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.id") == "tab-2026" and selected_tab(page) == "2026"
    page.keyboard.press("End")
    assert selected_tab(page) == "2023"
    page.keyboard.press("Home")
    assert selected_tab(page) == "すべて"
    # 写真ボタンの名前（alt）
    assert page.eval_on_selector_all(".tile img", "els => els.every(e => e.alt.trim().length > 0)")
    assert page.get_by_role("button", name=re.compile("撮影の写真")).count() == 60
    # キーボードで開閉・フォーカス管理
    page.focus(".tile[data-index='3']")
    page.keyboard.press("Enter")
    assert viewer_open(page) and page.evaluate("document.activeElement.id") == "viewer-close"
    assert page.get_attribute("#viewer", "aria-label") == "写真の全画面表示"
    for sel in ("#viewer-close", "#viewer-prev", "#viewer-next"):
        assert page.get_attribute(sel, "aria-label")
    assert page.get_attribute("#viewer-img", "alt") == site["doc"]["photos"][3]["alt"]
    seen = set()
    for _ in range(6):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('viewer').contains(document.activeElement)")
        seen.add(page.evaluate("document.activeElement.id"))
    assert seen == {"viewer-close", "viewer-prev", "viewer-next"}
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.getElementById('viewer').contains(document.activeElement)")
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.dataset.index") == "3"
    assert page.get_attribute("#status", "role") == "status"
    assert page.locator(".skip-link").count() == 1
    w.assert_clean()


def test_reduced_motion_disables_transitions(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], reduced_motion="reduce")
    try:
        page, w = open_page(ctx, site["main"])
        page.click(".tile[data-index='0']")
        dur = page.evaluate("getComputedStyle(document.getElementById('viewer-img')).transitionDuration")
        assert all(float(d.strip().rstrip("s")) <= 0.0001 for d in dur.split(","))
        assert page.evaluate("getComputedStyle(document.querySelector('.year-tab')).transitionDuration").startswith("1e-05")
        w.assert_clean()
    finally:
        ctx.close()


def test_relative_urls_under_repository_subpath_and_no_external_requests(phone, site):
    page, w = open_page(phone, site["main"])
    page.click(".tile[data-index='0']")
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    prefix = site["main"]
    assert w.requests and all(u.startswith(prefix) for u in w.requests), [u for u in w.requests if not u.startswith(prefix)]
    for suffix in ("css/style.css", "js/app.js", "data/photos.json"):
        assert prefix + suffix in w.requests
    w.assert_clean()


# ===========================================================================
# シナリオ: 写真追加 → 生成 → 検証 → 公開準備 → ブラウザー再読込
# ===========================================================================
def test_scenario_add_photos_publish_and_reload(pw, tmp_path):
    p, browser = pw
    project = make_project(tmp_path / "photo-viewer")
    use_publish = find_powershell() is not None and in_venv()
    if use_publish:
        init_git_project(project, tmp_path / "remote.git")
    s = project / "source_photos"
    make_image(s / "2025" / "a.jpg", (800, 600), exif_dt="2025:05:01 10:00:00", label="A")
    make_image(s / "2024" / "b.jpg", (600, 800), exif_dt="2024:05:01 10:00:00", label="B")

    def prepare():
        if use_publish:
            r = run_publish(project, "-NoPush")  # 生成→検証→試験→Git安全確認→commit（公開準備）
            assert r.returncode == 0, r.stdout + r.stderr
        else:
            assert run_build(project).returncode == 0
            assert run_validate(project).returncode == 0

    prepare()
    with serve(tmp_path) as base:
        ctx = browser.new_context(**p.devices["iPhone 13"])
        try:
            page, w = open_page(ctx, f"{base}/photo-viewer/")
            assert page.locator(".tile").count() == 2
            page.click("#tab-2025")
            assert page.locator(".tile").count() == 1
            # 写真を追加して再生成・公開準備
            make_image(s / "2026" / "c.jpg", (800, 600), exif_dt="2026:01:01 00:00:00", label="C")
            make_image(s / "2025" / "d.jpg", (800, 600), exif_dt="2025:12:31 23:59:59", label="D")
            prepare()
            page.reload()
            page.wait_for_selector("html[data-pv=ready]")
            assert selected_tab(page) == "2025" and page.locator(".tile").count() == 2  # 年選択を保持
            assert tab_texts(page) == ["すべて", "2026", "2025", "2024"]
            page.click("#tab-all")
            doc = load_photos(project)
            assert tile_ids(page) == [x["id"] for x in doc["photos"]]
            assert [x["date"][:10] for x in doc["photos"]] == ["2026-01-01", "2025-12-31", "2025-05-01", "2024-05-01"]
            page.click(".tile[data-index='0']")
            page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
            assert counter(page) == "1 / 4"
            w.assert_clean()
        finally:
            ctx.close()


# ===========================================================================
# 手順書 8章「スマートフォン受入確認」に対応する試験（エミュレーション）
# ===========================================================================
def test_landscape_and_rotation(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13 landscape"])
    try:
        page, w = open_page(ctx, site["main"])
        vw = page.evaluate("document.documentElement.clientWidth")
        boxes = page.eval_on_selector_all("#grid > li:not(.is-hero) .tile", "els => els.slice(0, 4).map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width]; })")
        assert boxes[0][1] == boxes[1][1] == boxes[2][1] < boxes[3][1]  # 横向き（広い画面）では3列
        assert boxes[1][0] - (boxes[0][0] + boxes[0][2]) >= 8
        assert boxes[0][0] >= 0 and boxes[2][0] + boxes[2][2] <= vw + 0.6

        def image_fits():
            return page.evaluate(
                "(() => { const i = document.getElementById('viewer-img').getBoundingClientRect();"
                " const s = document.getElementById('viewer-stage').getBoundingClientRect();"
                " return i.width > 0 && i.left >= s.left - 1 && i.right <= s.right + 1 && i.top >= s.top - 1 && i.bottom <= s.bottom + 1; })()"
            )

        page.click(".tile[data-index='1']")
        page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
        assert image_fits()
        page.set_viewport_size({"width": 390, "height": 664})  # 縦向きへ回転
        page.wait_for_timeout(300)
        assert viewer_open(page) and counter(page) == "2 / 79" and image_fits()
        page.set_viewport_size({"width": 844, "height": 340})  # 再び横向きへ
        page.wait_for_timeout(300)
        assert image_fits()
        page.click("#viewer-close")
        sel = "#grid > li:not(.is-hero) .tile"
        tops = page.eval_on_selector_all(sel, "els => els.slice(0, 4).map(e => e.getBoundingClientRect().top)")
        assert tops[0] == tops[1] == tops[2] < tops[3]  # 横向きでは3列
        page.set_viewport_size({"width": 390, "height": 664})
        page.wait_for_timeout(200)
        tops = page.eval_on_selector_all(sel, "els => els.slice(0, 4).map(e => e.getBoundingClientRect().top)")
        assert tops[0] == tops[1] < tops[2]  # 縦向きでは2列
        w.assert_clean()
    finally:
        ctx.close()


def test_back_navigation_restores_year_and_scroll(phone, site):
    page, w = open_page(phone, site["main"])
    page.click("#tab-2025")
    page.evaluate("window.scrollTo(0, 700)")
    page.wait_for_timeout(500)
    y, anchor = scroll_y(page), anchor_id(page)
    page.goto(site["base"] + "/")  # 別のページへ移動してから「戻る」
    page.go_back()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert selected_tab(page) == "2025"
    assert abs(scroll_y(page) - y) <= 2 and anchor_id(page) == anchor
    w.assert_clean()


def test_rapid_swipes_never_go_out_of_range(phone, site):
    page, w = open_page(phone, site["main"])
    page.click("#tab-2023")  # 2枚だけの年
    page.click(".tile[data-index='0']")
    t = Touch(page)
    x, y = stage_center(page)
    for _ in range(15):
        t.swipe(x + 100, y, -220, steps=3)
    assert counter(page) == "2 / 2"
    for _ in range(15):
        t.swipe(x - 100, y, 220, steps=3)
    assert counter(page) == "1 / 2"
    for i in range(20):  # 左右交互の連打
        t.swipe(x, y, -220 if i % 2 == 0 else 220, steps=2)
    assert counter(page) in ("1 / 2", "2 / 2")
    assert page.get_attribute("#viewer-img", "data-id") in [p["id"] for p in site["doc"]["photos"] if p["year"] == 2023]
    page.wait_for_timeout(300)
    assert page.evaluate("document.getElementById('viewer-img').style.transform") == ""
    w.assert_clean()


def test_slow_mobile_network_shows_loading_then_photos(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["Pixel 5"])
    try:
        page = ctx.new_page()
        w = Watch(page)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send(
            "Network.emulateNetworkConditions",
            {"offline": False, "latency": 600, "downloadThroughput": 50 * 1024, "uploadThroughput": 20 * 1024},
        )  # 低速なモバイル回線相当
        page.goto(site["main"], wait_until="domcontentloaded")
        assert page.get_attribute("#status", "data-state") == "loading"
        assert page.inner_text("#status-text") == "読み込み中…"
        page.wait_for_selector("html[data-pv=ready]", timeout=60000)
        assert page.locator(".tile").count() == 60
        page.click(".tile[data-index='0']")
        page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0", timeout=60000)
        assert counter(page) == "1 / 79"
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 見出しは流れ、年の行だけが残る／全画面表示の操作は写真に重ならない
# ===========================================================================
def test_only_year_row_stays_on_scroll(phone, site):
    page, w = open_page(phone, site["main"])
    head0 = page.locator(".site-head").bounding_box()
    assert head0["y"] >= 0 and page.is_visible("#app-title")
    page.evaluate("window.scrollTo(0, 900)")
    page.wait_for_timeout(200)
    bar = page.evaluate("document.querySelector('.tabs-bar').getBoundingClientRect().top")
    head = page.evaluate("document.querySelector('.site-head').getBoundingClientRect().bottom")
    assert abs(bar) < 1  # 年の行は上端に残る
    assert head <= 0  # 見出しは流れて画面外へ
    assert page.evaluate("getComputedStyle(document.querySelector('.tabs-bar')).borderBottomWidth") == "0px"
    # 年の行の高さは 60px 以下（写真の表示領域を広く保つ）
    assert page.evaluate("document.querySelector('.tabs-bar').getBoundingClientRect().height") <= 60
    w.assert_clean()


def test_viewer_controls_do_not_cover_photo(phone, site):
    page, w = open_page(phone, site["main"])
    page.click(".tile[data-index='1']")
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    r = page.evaluate(
        """(() => { const box = id => document.getElementById(id).getBoundingClientRect();
           const img = box('viewer-img'), p = box('viewer-prev'), n = box('viewer-next'), c = box('viewer-close');
           const overlap = (a, b) => !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top);
           return { prev: overlap(img, p), next: overlap(img, n), close: overlap(img, c),
                    navBelow: p.top >= img.bottom - 1 && n.top >= img.bottom - 1,
                    bg: getComputedStyle(document.getElementById('viewer')).backgroundColor }; })()"""
    )
    assert r["prev"] is False and r["next"] is False and r["close"] is False and r["navBelow"]
    assert r["bg"] == "rgb(255, 255, 255)"  # 明るい壁（ライトモード）
    w.assert_clean()
