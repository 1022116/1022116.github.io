"""ブラウザー試験（同期仕様ID PV-SPEC-2026-10-v10 / 版 10.0）

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
YEARS = ((2026, 72), (2025, 12), (2024, 6), (2023, 2))  # 合計92枚。最初に開く2026年は72枚（初回表示60枚＋追加表示）
N_NEWEST = 72  # 最初に開く年（いちばん新しい年）の枚数


# ---------------------------------------------------------------------------
# 配信・ブラウザー
# ---------------------------------------------------------------------------
class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        # 標準のサーバーは更新日時を秒単位で比べるため、同じ秒に作り直したファイルを「変更なし(304)」と答えてしまう。
        # 試験では常に中身を返す（GitHub Pages は内容で比較するので実際のサイトには影響しない）
        del self.headers["If-Modified-Since"]
        return super().send_head()


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


# 仕様 v10: ふだんは「動く一覧」、端末の「視差効果を減らす」設定がオンのときは「動かない一覧」（v9 までの一覧）。
# v9 までの一覧・表紙・スクロール位置の試験は、動かない一覧（reduced_motion="reduce"）で行う。
STILL = {"reduced_motion": "reduce"}
MOTION = {"reduced_motion": "no-preference"}


@pytest.fixture
def phone(pw):
    """動かない一覧（視差効果を減らす設定）のスマホ。"""
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], **STILL)
    yield ctx
    ctx.close()


@pytest.fixture
def desktop(pw):
    """動かない一覧（視差効果を減らす設定）の PC。"""
    _p, browser = pw
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, **STILL)
    yield ctx
    ctx.close()


@pytest.fixture
def phone_drift(pw):
    """動く一覧（ふだんの設定）のスマホ。"""
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], **MOTION)
    yield ctx
    ctx.close()


@pytest.fixture
def desktop_drift(pw):
    """動く一覧（ふだんの設定）の PC（マウスあり）。"""
    _p, browser = pw
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, **MOTION)
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
    """S-05（v7）: 最初はいちばん新しい年。表紙の写真を元の縦横比で大きく、その下に正方形の2列。各写真に番号と撮影日。"""
    p, browser = pw
    ctx = browser.new_context(**p.devices[device], **STILL)
    try:
        page, w = open_page(ctx, site["main"])
        vw = page.evaluate("document.documentElement.clientWidth")
        grid = page.locator("#grid").bounding_box()
        assert 12 <= grid["x"] <= 24 and abs((grid["x"] + grid["width"]) - (vw - grid["x"])) < 1  # 左右に同じ余白
        page.wait_for_function("document.getElementById('cover-img').naturalWidth > 0")
        cover = page.evaluate(
            """(() => { const img = document.getElementById('cover-img'); const r = img.getBoundingClientRect();
               return { visible: !document.getElementById('cover').hidden, id: img.dataset.id,
               usesDetail: img.getAttribute('src').startsWith('photos/'), ratio: r.width / r.height,
               natural: img.naturalWidth / img.naturalHeight, above: r.bottom <= document.getElementById('grid').getBoundingClientRect().top }; })()"""
        )
        assert cover["visible"] and cover["usesDetail"] and cover["above"]
        assert cover["id"] == site["ids"][0]  # cover 未設定 → いちばん新しい写真
        assert abs(cover["ratio"] - cover["natural"]) < 0.02  # 元の縦横比
        boxes = page.eval_on_selector_all(
            "#grid > li .tile", "els => els.slice(0, 3).map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })"
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
    tops = page.eval_on_selector_all("#grid > li .tile", "els => els.slice(0, 4).map(e => e.getBoundingClientRect().top)")
    assert tops[0] == tops[1] == tops[2] < tops[3]
    gap = page.evaluate("(() => { const t = document.querySelectorAll('#grid > li .tile'); return t[1].getBoundingClientRect().left - t[0].getBoundingClientRect().right; })()")
    assert gap >= 16
    w.assert_clean()


def test_cover_space_is_reserved_before_image_loads(phone, site):
    """表紙の大きな写真は読み込み前から場所を確保し、読み込み後に一覧がずれない。"""
    page = phone.new_page()
    w = Watch(page)
    held = []
    first_src = site["doc"]["photos"][0]["src"]
    page.route(f"**/{first_src}", lambda route: held.append(route))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    before = page.evaluate("document.querySelector('#grid > li').getBoundingClientRect().top")
    assert page.evaluate("document.getElementById('cover-btn').getBoundingClientRect().height") > 100
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(50)
    held[0].continue_()
    page.wait_for_function("document.getElementById('cover-img').naturalWidth > 0")
    after = page.evaluate("document.querySelector('#grid > li').getBoundingClientRect().top")
    assert abs(after - before) < 1
    w.assert_clean()


def test_cover_title_meta_and_catalogue_numbers(phone, site):
    page, w = open_page(phone, site["main"])
    assert page.inner_text("#app-title") == "写真"
    assert page.evaluate("document.getElementById('app-title').classList.contains('is-ja')")
    years = site["doc"]["years"]
    assert page.inner_text("#photo-range") == f"{years[-1]}–{years[0]}"
    assert page.get_attribute("#tab-2025", "data-count") == "12"
    # 年で絞り込んでも目録番号は全写真の通し番号（いちばん古い写真が 01）
    total = len(site["ids"])
    page.click("#tab-2024")
    ids24 = tile_ids(page)
    nos24 = page.eval_on_selector_all("#grid > li .tile-no", "els => els.map(e => e.textContent)")
    assert nos24 == [f"{total - site['ids'].index(i):02d}" for i in ids24] and len(ids24) == 6
    assert page.is_visible("#cover")  # 表紙はどの年でも表示
    page.click(".tile[data-index='0']")
    assert page.inner_text("#viewer-no") == "No. " + nos24[0]
    w.assert_clean()


def test_title_uses_bundled_handwriting_font(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], **STILL)
    try:
        page = ctx.new_page()
        w = Watch(page)

        def handler(route):
            resp = route.fetch()
            doc = resp.json()
            doc["title"] = "Daily"
            route.fulfill(response=resp, json=doc)

        page.route("**/data/photos.json", handler)
        page.goto(site["main"])
        page.wait_for_selector("html[data-pv=ready]")
        assert page.inner_text("#app-title") == "Daily"
        assert not page.evaluate("document.getElementById('app-title').classList.contains('is-ja')")
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.fonts.check('16px \"Klee One UI\"', 'Daily 2026年の16枚')")
        assert page.evaluate("[...document.fonts].some(f => f.family.replace(/\"/g, '') === 'Klee One UI' && f.status === 'loaded')")
        assert any(u == site["main"] + "fonts/klee-one-subset.woff2" for u in w.requests)
        fams = page.evaluate("['#app-title', '#tab-2026', '#photo-count', '.tile-date'].map(s => getComputedStyle(document.querySelector(s)).fontFamily)")
        assert all(f.startswith('"Klee One UI", "Hiragino Maru Gothic ProN"') for f in fams), fams  # v9: 全文字版なし
        assert page.title() == "Daily"
        lines = page.evaluate("Math.round(document.getElementById('app-title').getBoundingClientRect().height / parseFloat(getComputedStyle(document.getElementById('app-title')).lineHeight))")
        assert lines == 1  # 画面幅いっぱいでも1行に収まる
        w.assert_clean()
    finally:
        ctx.close()


def test_year_tabs_are_generated_newest_first(phone, site):
    page, w = open_page(phone, site["main"])
    assert tab_texts(page) == ["2026", "2025", "2024", "2023"]  # 「すべて」はない（v7）
    assert page.locator("#tab-all").count() == 0
    assert selected_tab(page) == "2026"  # 最初はいちばん新しい年
    assert page.inner_text("#photo-count") == f"2026年の{N_NEWEST}枚"
    assert tile_ids(page) == site["ids"][:60]
    assert scroll_y(page) == 0 and page.is_visible("#cover")
    page.click("#tab-2025")
    expected = [p["id"] for p in site["doc"]["photos"] if p["year"] == 2025]
    assert tile_ids(page) == expected and len(expected) == 12
    assert page.inner_text("#photo-count") == "2025年の12枚"
    # 選んだ年の一覧の先頭（枚数の行）が上部バーのすぐ下に来る
    gap = page.evaluate("document.querySelector('.site-meta').getBoundingClientRect().top - document.querySelector('.site-bar').getBoundingClientRect().bottom")
    assert abs(gap) <= 1 and scroll_y(page) > 0
    assert anchor_id(page) == expected[0]
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
    assert len(first) < N_NEWEST
    for _ in range(30):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(120)
        if page.locator(".tile").count() == N_NEWEST:
            break
    assert page.locator(".tile").count() == N_NEWEST
    page.wait_for_timeout(800)
    assert len({u for u in w.requests if "/thumbs/" in u}) > len(first)
    assert len(set(tile_ids(page))) == N_NEWEST
    w.assert_clean()


# ===========================================================================
# 回帰: 全画面スライド
# ===========================================================================
def test_viewer_navigation_with_buttons_and_keys(phone, site):
    page, w = open_page(phone, site["main"])
    ids = site["ids"]
    page.click(".tile[data-index='2']")
    assert viewer_open(page) and counter(page) == "3 / 72"
    assert page.get_attribute("#viewer-img", "data-id") == ids[2]
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    page.click("#viewer-next")
    assert counter(page) == "4 / 72"
    page.click("#viewer-prev")
    page.click("#viewer-prev")
    assert counter(page) == "2 / 72"
    page.keyboard.press("ArrowRight")
    assert counter(page) == "3 / 72"
    page.keyboard.press("ArrowLeft")
    assert counter(page) == "2 / 72"
    page.keyboard.press("Home")
    assert counter(page) == "1 / 72" and page.is_disabled("#viewer-prev")
    page.keyboard.press("ArrowLeft")
    assert counter(page) == "1 / 72"
    page.keyboard.press("End")
    assert counter(page) == "72 / 72" and page.is_disabled("#viewer-next")
    assert page.get_attribute("#viewer-img", "data-id") == ids[71]
    page.keyboard.press("Escape")
    assert not viewer_open(page)
    assert page.evaluate("document.documentElement.classList.contains('viewer-open')") is False
    assert page.evaluate("document.activeElement.dataset.index") == "71"  # 最後に見た写真へフォーカスが戻る
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
    assert counter(page) == "2 / 72"
    t.swipe(x - 100, y, 220)
    assert counter(page) == "1 / 72"
    t.swipe(x - 100, y, 220)  # 先頭ではそれ以上戻らない
    assert counter(page) == "1 / 72"
    t.swipe(x, y, -20)  # 小さな動きはスワイプとみなさない
    assert counter(page) == "1 / 72"
    t.swipe(x, y + 100, 0, -250)  # 縦方向は無視
    assert counter(page) == "1 / 72"
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
    assert counter(page) == "13 / 72"
    page.evaluate("() => { for (let i = 0; i < 300; i++) document.getElementById('viewer-next').click(); }")
    assert counter(page) == "72 / 72"
    assert page.get_attribute("#viewer-img", "data-id") == site["ids"][71]
    assert page.evaluate("document.activeElement.id") in ("viewer-prev", "viewer-close")  # 無効化されたボタンにフォーカスを残さない
    page.evaluate("() => { for (let i = 0; i < 300; i++) document.getElementById('viewer-prev').click(); }")
    assert counter(page) == "1 / 72"
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    assert not page.evaluate("document.getElementById('viewer-stage').classList.contains('is-loading')")
    w.assert_clean()


def test_rapid_year_tab_clicks(phone, site):
    page, w = open_page(phone, site["main"])
    page.evaluate("() => { const t = document.querySelectorAll('.year-tab'); for (let i = 0; i < 49; i++) t[i % t.length].click(); }")
    assert selected_tab(page) == "2026"  # 48 % 4 = 0
    for _ in range(5):
        page.click("#tab-2023")
        page.click("#tab-2026")
    page.click("#tab-2025")
    expected = [p["id"] for p in site["doc"]["photos"] if p["year"] == 2025]
    assert tile_ids(page) == expected
    page.focus("#tab-2025")
    for _ in range(14):  # キー連打（2025 の位置 1 から 14 回 → (1 + 14) % 4 = 3 → 2023）
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
    assert viewer_open(page) and counter(page) == "6 / 72"
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
    assert counter(page) == "1 / 72"
    assert page.evaluate("document.getElementById('viewer-img').style.transform") == ""
    assert not page.evaluate("document.getElementById('viewer-stage').classList.contains('is-dragging')")
    t.swipe(x + 100, y, -220)  # 再度開いた後のスワイプは正常
    assert counter(page) == "2 / 72"
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
    assert page.locator(".tile").count() == N_NEWEST and site["ids"].index(anchor) >= 60
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
    first25 = [p["id"] for p in site["doc"]["photos"] if p["year"] == 2025][0]
    assert saved["year"] == "2025" and saved["anchorId"] == first25 and saved["y"] == scroll_y(page) < 1500
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert selected_tab(page) == "2025" and anchor_id(page) == first25 and abs(scroll_y(page) - saved["y"]) <= 2
    w.assert_clean()


def test_removed_year_and_broken_storage_do_not_come_back(phone, site):
    page, w = open_page(phone, site["main"])
    # 離脱時の保存（pagehide）より後、app.js より前に書き込むため init script で注入する
    page.add_init_script(  # v6 以前に「すべて」を選んでいた人
        "localStorage.setItem('pv:selectedYear', 'all');"
        "sessionStorage.setItem('pv:scroll', JSON.stringify({v: 1, year: '2026', anchorId: 'deadbeefdeadbeef', delta: 0, y: 500}));"
    )
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert selected_tab(page) == "2026" and "すべて" not in tab_texts(page)  # いちばん新しい年
    assert page.evaluate("localStorage.getItem('pv:selectedYear')") is None  # 保存値は消す
    assert abs(scroll_y(page) - 500) <= 2  # 基準写真が消えていれば保存位置付近へ
    page.add_init_script(
        "localStorage.setItem('pv:selectedYear', '2019');"
        "sessionStorage.setItem('pv:scroll', '{broken');"
    )
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    assert selected_tab(page) == "2026" and "2019" not in tab_texts(page)
    assert page.evaluate("localStorage.getItem('pv:selectedYear')") is None
    assert scroll_y(page) == 0
    w.assert_clean()


def test_works_without_storage(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], **STILL)
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
        assert counter(page) == "1 / 6"
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 境界: 0件・1件
# ===========================================================================
def test_empty_gallery(phone, site):
    page, w = open_page(phone, site["empty"], state="empty")
    assert page.inner_text("#status-text") == "写真はまだありません。"
    assert tab_texts(page) == [] and page.locator(".tile").count() == 0
    assert page.inner_text("#photo-count") == "" and page.inner_text("#app-title") == "写真"
    assert page.is_hidden("#status-retry")
    w.assert_clean()


def test_single_photo(phone, site):
    page, w = open_page(phone, site["single"])
    assert tab_texts(page) == ["2025"] and selected_tab(page) == "2025" and page.locator(".tile").count() == 1
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
    assert page.inner_text("#app-title") == ""  # 読込前に仮のサイト名（写真）を出さない
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
    assert page.is_hidden("#viewer-error") and counter(page) == "2 / 72"
    assert w.page_errors == []


# ===========================================================================
# 回帰: アクセシビリティ・reduced-motion・相対URL
# ===========================================================================
def test_accessibility_basics(desktop, site):
    page, w = open_page(desktop, site["main"])
    assert page.get_attribute("html", "lang") == "ja"
    assert page.locator("[role=tablist]").count() == 1
    tabs = page.locator("[role=tab]")
    assert tabs.count() == 4
    assert page.locator("[role=tab][aria-selected=true]").count() == 1
    assert page.locator("[role=tab][tabindex='0']").count() == 1
    assert page.eval_on_selector_all("[role=tab]", "els => els.every(e => e.getAttribute('aria-controls') === 'grid-panel')")
    assert page.get_attribute("#grid-panel", "aria-labelledby") == "tab-2026"
    assert page.get_attribute("#tab-2025", "aria-label") == "2025年（12枚）"
    # キーボードでタブ切替（索引を移動している間は画面を動かさない）
    page.focus("#tab-2026")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.id") == "tab-2025" and selected_tab(page) == "2025"
    page.keyboard.press("End")
    assert selected_tab(page) == "2023"
    page.keyboard.press("Home")
    assert selected_tab(page) == "2026" and scroll_y(page) == 0
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
        # 現像はせず、最初から仕上がった写真を見せる
        page.click("#viewer-close")
        page.wait_for_selector(".tile[data-index='0'].is-developed")
        assert page.evaluate("getComputedStyle(document.querySelector('.tile[data-index=\"0\"] img')).filter") == "none"
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
        ctx = browser.new_context(**p.devices["iPhone 13"], **STILL)
        try:
            page, w = open_page(ctx, f"{base}/photo-viewer/")
            assert selected_tab(page) == "2025" and page.locator(".tile").count() == 1  # いちばん新しい年
            page.click("#tab-2024")
            assert page.locator(".tile").count() == 1
            # 写真を追加して再生成・公開準備
            make_image(s / "2026" / "c.jpg", (800, 600), exif_dt="2026:01:01 00:00:00", label="C")
            make_image(s / "2025" / "d.jpg", (800, 600), exif_dt="2025:12:31 23:59:59", label="D")
            prepare()
            page.reload()
            page.wait_for_selector("html[data-pv=ready]")
            assert selected_tab(page) == "2024" and page.locator(".tile").count() == 1  # 年選択を保持
            assert tab_texts(page) == ["2026", "2025", "2024"]
            page.click("#tab-2025")
            doc = load_photos(project)
            assert tile_ids(page) == [x["id"] for x in doc["photos"] if x["year"] == 2025]
            assert [x["date"][:10] for x in doc["photos"]] == ["2026-01-01", "2025-12-31", "2025-05-01", "2024-05-01"]
            page.click(".tile[data-index='0']")
            page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
            assert counter(page) == "1 / 2"
            w.assert_clean()
        finally:
            ctx.close()


# ===========================================================================
# 手順書 8章「スマートフォン受入確認」に対応する試験（エミュレーション）
# ===========================================================================
def test_landscape_and_rotation(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13 landscape"], **STILL)
    try:
        page, w = open_page(ctx, site["main"])
        vw = page.evaluate("document.documentElement.clientWidth")
        boxes = page.eval_on_selector_all("#grid > li .tile", "els => els.slice(0, 4).map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width]; })")
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
        assert viewer_open(page) and counter(page) == "2 / 72" and image_fits()
        page.set_viewport_size({"width": 844, "height": 340})  # 再び横向きへ
        page.wait_for_timeout(300)
        assert image_fits()
        page.click("#viewer-close")
        sel = "#grid > li .tile"
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
    ctx = browser.new_context(**p.devices["Pixel 5"], **STILL)
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
        assert counter(page) == "1 / 72"
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 見出しは流れ、年の行だけが残る／全画面表示の操作は写真に重ならない
# ===========================================================================
def test_only_top_bar_stays_on_scroll(phone, site):
    """上部のバー（名前と小さな年）だけが残り、表紙はスクロールで流れる。"""
    page, w = open_page(phone, site["main"])
    assert page.is_visible("#app-title") and page.is_visible("#tab-2026")
    page.evaluate("window.scrollTo(0, 1400)")
    page.wait_for_timeout(200)
    bar = page.evaluate("document.querySelector('.site-bar').getBoundingClientRect().top")
    cover_bottom = page.evaluate("document.getElementById('cover').getBoundingClientRect().bottom")
    assert abs(bar) < 1 and cover_bottom <= 0
    assert page.is_visible("#tab-2025")  # 年はスクロールしても押せる
    assert page.evaluate("getComputedStyle(document.querySelector('.site-bar')).borderBottomWidth") == "0px"
    assert page.evaluate("document.querySelector('.site-bar').getBoundingClientRect().height") <= 64  # 写真の表示領域を広く保つ
    w.assert_clean()


def test_top_bar_year_links_switch_year(phone, site):
    page, w = open_page(phone, site["main"])
    links = page.eval_on_selector_all(".bar-year", "els => els.map(e => e.textContent)")
    assert links == ["2026", "2025", "2024", "2023"]
    assert page.get_attribute(".bar-year[data-year='2026']", "aria-selected") == "true"
    page.evaluate("window.scrollTo(0, 1400)")
    page.wait_for_timeout(200)
    page.click(".bar-year[data-year='2025']")  # スクロールした先からでも年を切り替えられる
    assert selected_tab(page) == "2025"
    assert page.get_attribute(".bar-year[data-year='2025']", "aria-selected") == "true"
    assert page.get_attribute(".bar-year[data-year='2026']", "aria-selected") == "false"
    expected = [p["id"] for p in site["doc"]["photos"] if p["year"] == 2025]
    assert tile_ids(page) == expected
    assert anchor_id(page) == expected[0]  # その年の先頭が上部バーのすぐ下に
    assert page.is_visible("#cover")  # 表紙は上へ戻れば見える
    page.click(".bar-year[data-year='2026']")
    assert selected_tab(page) == "2026" and tile_ids(page)[0] == site["ids"][0]
    w.assert_clean()


def test_years_are_only_in_the_top_bar(pw, site):
    """年は上部バーの右側だけに並べる（名前の下の大きな年の一覧はなし）。表紙はバーのすぐ下から。"""
    p, browser = pw
    for device in ("iPhone 13", "desktop"):
        ctx = browser.new_context(**p.devices["iPhone 13"], **STILL) if device != "desktop" else browser.new_context(viewport={"width": 1440, "height": 900}, **STILL)
        try:
            page, w = open_page(ctx, site["main"])
            assert page.locator(".year-index").count() == 0
            assert page.locator("[role=tablist]").count() == 1 and page.locator(".site-bar [role=tablist]").count() == 1
            r = page.evaluate(
                """(() => { const box = s => document.querySelector(s).getBoundingClientRect();
                   const bar = box('.site-bar'), title = box('#app-title'), first = box('#tab-2026'), last = box('#tab-2023'), cover = box('#cover');
                   return { inBar: first.top >= bar.top && first.bottom <= bar.bottom, rightOfTitle: first.left > title.right,
                            sameRow: Math.abs((first.top + first.bottom) / 2 - (title.top + title.bottom) / 2) < 8,
                            rightEdge: box('.bar-inner').right - last.right, coverGap: cover.top - bar.bottom }; })()"""
            )
            assert r["inBar"] and r["rightOfTitle"] and r["sameRow"], (device, r)
            assert r["rightEdge"] < 60  # 内容の右端（余白の内側）に寄せる
            assert 0 <= r["coverGap"] <= 60, (device, r)  # 表紙がバーのすぐ下に来る
            w.assert_clean()
        finally:
            ctx.close()


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
    assert r["bg"] == "rgb(255, 255, 255)"  # 白い壁
    w.assert_clean()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_always_white_background_and_black_text(pw, site, scheme):
    """スマホがダークモードでも、白い背景に黒い文字。選んでいる年は色ではなく下線で示す。"""
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], color_scheme=scheme, **STILL)
    try:
        page, w = open_page(ctx, site["main"])
        colors = page.evaluate(
            """(() => { const c = s => getComputedStyle(document.querySelector(s)).color;
               return { body: getComputedStyle(document.body).backgroundColor,
                        bar: getComputedStyle(document.querySelector('.site-bar')).backgroundColor,
                        text: ['#app-title', '#tab-2026', '#tab-2025', '.bar-year[data-year="2024"]', '#photo-count',
                               '#photo-range', '.tile-no', '.tile-date'].map(c) }; })()"""
        )
        assert colors["body"] == "rgb(255, 255, 255)"
        assert colors["bar"].startswith("rgba(255, 255, 255") or colors["bar"] == "rgb(255, 255, 255)"
        assert page.get_attribute("meta[name=theme-color]", "content") == "#ffffff"
        assert set(colors["text"]) == {"rgb(0, 0, 0)"}, colors["text"]
        deco = lambda s: page.evaluate("s => getComputedStyle(document.querySelector(s)).textDecorationLine", s)
        assert deco("#tab-2026") == "underline" and deco("#tab-2025") == "none"
        assert deco(".bar-year[data-year='2026']") == "underline" and deco(".bar-year[data-year='2025']") == "none"
        page.click("#tab-2025")
        assert deco("#tab-2025") == "underline" and deco("#tab-2026") == "none"
        page.click(".tile[data-index='0']")
        page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
        v = page.evaluate(
            """(() => ({ bg: getComputedStyle(document.getElementById('viewer')).backgroundColor,
                         text: ['#viewer-no', '#viewer-counter', '#viewer-caption'].map(s => getComputedStyle(document.querySelector(s)).color) }))()"""
        )
        assert v["bg"] == "rgb(255, 255, 255)" and set(v["text"]) == {"rgb(0, 0, 0)"}
        w.assert_clean()
    finally:
        ctx.close()



# ===========================================================================
# 仕様 v6・v7: トップ（表紙）の写真を選べる。どの年でも表示
# ===========================================================================
def _with_cover(page, cover):
    def handler(route):
        resp = route.fetch()
        doc = resp.json()
        doc["cover"] = cover
        route.fulfill(response=resp, json=doc)

    page.route("**/data/photos.json", handler)


def test_chosen_cover_is_shown_and_opens_within_its_year(phone, site):
    chosen = site["doc"]["photos"][75]  # 2025年の4枚目（最初に開く2026年とは別の年）
    assert chosen["year"] == 2025
    page = phone.new_page()
    w = Watch(page)
    _with_cover(page, chosen["id"])
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    assert page.get_attribute("#cover-img", "data-id") == chosen["id"]
    assert page.get_attribute("#cover-img", "src") == chosen["src"]
    # 表紙は一覧の写真の数に含めない（一覧は従来どおり新しい順）
    assert tile_ids(page)[:3] == site["ids"][:3]
    page.click("#cover-btn")
    assert viewer_open(page) and counter(page) == "4 / 12"  # 表紙の写真の年（2025年）の中の位置で開く
    assert page.get_attribute("#viewer-img", "data-id") == chosen["id"]
    page.click("#viewer-next")
    assert page.get_attribute("#viewer-img", "data-id") == site["ids"][76]
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.id") == "cover-btn"  # 表紙へフォーカスが戻る
    assert selected_tab(page) == "2026"  # 表紙を見ても選択中の年は変わらない
    # どの年を選んでも同じ表紙
    for tab in ("#tab-2024", "#tab-2023", "#tab-2026"):
        page.click(tab)
        assert page.is_visible("#cover") and page.get_attribute("#cover-img", "data-id") == chosen["id"]
    w.assert_clean()


@pytest.mark.parametrize("bad", ["deadbeefdeadbeef", None, 123])
def test_unknown_cover_falls_back_to_newest(phone, site, bad):
    page = phone.new_page()
    w = Watch(page)
    _with_cover(page, bad)
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    assert page.get_attribute("#cover-img", "data-id") == site["ids"][0]
    w.assert_clean()


def test_cover_hidden_when_no_photos(phone, site):
    page, w = open_page(phone, site["empty"], state="empty")
    assert page.is_hidden("#cover")
    w.assert_clean()


def test_cover_button_has_its_own_name(phone, site):
    page, w = open_page(phone, site["main"])
    assert page.get_attribute("#cover-btn", "aria-label") == "表紙の写真を全画面で見る"
    assert page.get_by_role("button", name=re.compile("撮影の写真")).count() == 60  # 一覧の写真だけ
    w.assert_clean()


# ===========================================================================
# 仕様 v8: 演出（現像・サイト名の書き順）／仕様 v9: 小話の廃止
# ===========================================================================
def _patch_doc(page, fn):
    def handler(route):
        resp = route.fetch()
        doc = resp.json()
        fn(doc)
        route.fulfill(response=resp, json=doc)

    page.route("**/data/photos.json", handler)


def test_photos_develop_when_they_come_into_view(phone, site):
    page, w = open_page(phone, site["main"])
    page.wait_for_selector("#cover-btn.is-developed")
    page.wait_for_selector(".tile[data-index='0'].is-developed")
    far = ".tile[data-index='50']"
    assert page.locator(far).count() == 1
    assert not page.evaluate(f"document.querySelector(\"{far}\").classList.contains('is-developed')")  # 画面の外はまだ
    assert page.evaluate(f"getComputedStyle(document.querySelector(\"{far} img\")).opacity") == "0"
    page.evaluate(f"document.querySelector(\"{far}\").scrollIntoView({{block: 'center'}})")
    page.wait_for_selector(f"{far}.is-developed")
    page.wait_for_timeout(2600)  # 現像し終わるまで
    st = page.evaluate(f"(() => {{ const s = getComputedStyle(document.querySelector(\"{far} img\")); return [s.opacity, s.filter]; }})()")
    assert st == ["1", "none"]
    w.assert_clean()


def test_develop_does_not_break_year_switch_or_broken_images(phone, site):
    page = phone.new_page()
    w = Watch(page)
    p0 = site["doc"]["photos"][0]
    page.route(f"**/{p0['thumb']}", lambda r: r.fulfill(status=404, body=""))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_selector(".tile[data-index='0'].is-broken.is-developed")
    assert page.is_visible(".tile[data-index='0'] .tile-fallback")
    page.click("#tab-2025")
    page.wait_for_selector(".tile[data-index='0'].is-developed")
    assert w.page_errors == []


def test_title_daily_is_drawn_in_stroke_order_once_per_tab(phone_drift, site):
    page = phone_drift.new_page()
    w = Watch(page)
    _patch_doc(page, lambda d: d.update(title="Daily"))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    assert page.evaluate("document.getElementById('app-title').classList.contains('has-art')")
    assert page.inner_text("#app-title .visually-hidden") == "Daily" and page.title() == "Daily"
    assert page.get_attribute("#app-title svg", "aria-hidden") == "true"
    assert page.locator("#app-title svg .stroke").count() == 9  # D(2) a(2) i(2) l(1) y(2)
    assert page.evaluate("document.querySelector('#app-title svg').classList.contains('is-drawing')")
    delays = page.eval_on_selector_all("#app-title svg .stroke", "els => els.map(e => parseFloat(getComputedStyle(e).animationDelay))")
    assert delays == sorted(delays) and delays[0] > 0  # 書き順どおりに1画ずつ
    page.wait_for_timeout(500)
    first = page.evaluate("parseFloat(getComputedStyle(document.querySelector('#app-title svg .stroke')).strokeDashoffset)")
    last = page.evaluate("parseFloat(getComputedStyle([...document.querySelectorAll('#app-title svg .stroke')].pop()).strokeDashoffset)")
    assert last > 0  # まだ書いていない画が残っている
    page.wait_for_timeout(3500)
    done = page.eval_on_selector_all("#app-title svg .stroke", "els => els.map(e => parseFloat(getComputedStyle(e).strokeDashoffset))")
    assert all(abs(x) < 0.5 for x in done), done
    assert page.evaluate("getComputedStyle(document.querySelector('#app-title svg .ink-full')).opacity") == "1"
    page.reload()  # 同じタブでは描き終わった状態で表示
    page.wait_for_selector("html[data-pv=ready]")
    assert page.evaluate("document.querySelector('#app-title svg').classList.contains('is-drawn')")
    assert page.evaluate("getComputedStyle(document.querySelector('#app-title svg .ink-full')).opacity") == "1"
    assert first < 2000
    w.assert_clean()


def test_other_titles_are_plain_text(phone, site):
    page = phone.new_page()
    w = Watch(page)
    _patch_doc(page, lambda d: d.update(title="Notes"))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    assert page.inner_text("#app-title") == "Notes" and page.locator("#app-title svg").count() == 0
    w.assert_clean()


def test_title_is_not_animated_with_reduced_motion(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], reduced_motion="reduce")
    try:
        page = ctx.new_page()
        w = Watch(page)
        _patch_doc(page, lambda d: d.update(title="Daily"))
        page.goto(site["main"])
        page.wait_for_selector("html[data-pv=ready]")
        assert page.evaluate("document.querySelector('#app-title svg').classList.contains('is-drawn')")
        assert page.evaluate("getComputedStyle(document.querySelector('#app-title svg .ink-full')).opacity") == "1"
        w.assert_clean()
    finally:
        ctx.close()


def test_story_in_old_data_is_not_shown(phone, site):
    """仕様 v9: 小話は廃止。古い photos.json に story が残っていても表示せず、全文字版フォントも読み込まない。"""
    p0 = site["doc"]["photos"][0]
    page = phone.new_page()
    w = Watch(page)
    _patch_doc(page, lambda d: [ph.update(story="海へ行った日。") for ph in d["photos"] if ph["id"] == p0["id"]])
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    assert page.get_attribute(".tile[data-index='0']", "data-id") == p0["id"]
    assert page.locator(".tile-story").count() == 0
    assert "小話" not in page.get_attribute(".tile[data-index='0'] img", "alt")
    page.click(".tile[data-index='0']")
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    assert page.locator("#viewer-story").count() == 0
    assert "海へ行った日" not in page.inner_text("#viewer")
    assert page.get_attribute("#viewer", "aria-describedby") == "viewer-caption"
    page.wait_for_timeout(300)
    assert not [u for u in w.requests if "klee-one.css" in u or "/fonts/klee-one/" in u]
    w.assert_clean()


# ===========================================================================
# 仕様 v10: 動く一覧（終わりのない平面に散らした写真。視差効果を減らす設定でないとき）
# ===========================================================================
DRIFT_BOXES = """sel => [...document.querySelectorAll(sel)].map(e => {
    const b = e.querySelector('.drift-tile'); const t = b.getBoundingClientRect(); const r = e.getBoundingClientRect();
    return { i: +b.dataset.index, id: b.dataset.id, x: t.left, y: t.top, w: t.width, h: t.height, bottom: r.bottom,
             inView: e.classList.contains('is-in') }; })"""


def drift_settled(page, timeout=12000):
    page.wait_for_selector("#drift:not([data-intro])", timeout=timeout)
    page.wait_for_selector("#drift:not([data-moving])", timeout=timeout)


def open_drift(ctx, url):
    page, w = open_page(ctx, url)
    drift_settled(page)
    return page, w


def drift_boxes(page, sel="#drift-list > li"):
    return page.evaluate(DRIFT_BOXES, sel)


def on_screen(page, boxes):
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    return [b for b in boxes if b["x"] >= 0 and b["y"] >= 60 and b["x"] + b["w"] <= vw and b["y"] + b["h"] <= vh]


def test_drift_scatters_the_years_photos_in_their_own_shapes(phone_drift, site):
    """動く一覧: 選んだ年の写真を元の縦横比のまま大小ばらばらに散らし、中間画像（mids/）を使う。表紙は出さない。"""
    page, w = open_drift(phone_drift, site["main"])
    assert page.evaluate("document.documentElement.dataset.mode") == "drift"
    assert page.locator("#grid > li").count() == 0 and page.is_hidden("#cover")
    boxes = drift_boxes(page)
    assert len(boxes) == N_NEWEST and [b["i"] for b in boxes] == list(range(N_NEWEST))
    doc = {p["id"]: p for p in site["doc"]["photos"]}
    newest = [p for p in site["doc"]["photos"] if p["year"] == 2026]
    assert [b["id"] for b in boxes] == [p["id"] for p in newest]  # 新しい順（番号の大きい順）
    for b in boxes:
        p = doc[b["id"]]
        assert abs(b["w"] / b["h"] - p["width"] / p["height"]) < 0.03  # 元の縦横比
    widths = {round(b["w"]) for b in boxes if doc[b["id"]]["width"] > doc[b["id"]]["height"]}
    assert len(widths) >= 2  # 大きさがばらばら
    srcs = page.eval_on_selector_all("#drift-list img", "els => els.map(e => e.getAttribute('src'))")
    assert all(s.startswith("mids/2026/") for s in srcs)
    assert not [u for u in w.requests if "/thumbs/" in u or "/photos/" in u]  # 一覧では中間画像だけ読む
    caps = page.eval_on_selector_all("#drift-list > li:first-child .drift-line", "els => els.map(e => e.textContent)")
    assert caps == [f"No. {len(site['ids'])}", newest[0]["date"][:10].replace("-", ".")]
    # 止まっているときは写真どうし（番号・日付を含む）が重ならない
    shown = [b for b in boxes if b["inView"]]
    assert len(shown) >= 3
    for i, a in enumerate(shown):
        for b in shown[i + 1:]:
            overlap_x = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            overlap_y = min(a["bottom"], b["bottom"]) - max(a["y"], b["y"])
            assert overlap_x <= 1 or overlap_y <= 1, (a, b)
    # ページ自体はスクロールしない（一覧の中で動かす）
    assert page.evaluate("document.scrollingElement.scrollHeight <= innerHeight + 1")
    assert page.inner_text("#photo-count") == f"2026年の{N_NEWEST}枚"
    w.assert_clean()


def test_drift_intro_spreads_photos_from_the_center(phone_drift, site):
    page = phone_drift.new_page()
    w = Watch(page)
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    assert page.get_attribute("#drift", "data-intro") == "1"
    vw, vh = page.evaluate("[innerWidth, innerHeight]")

    def spread():
        bs = [b for b in drift_boxes(page) if -200 < b["x"] < vw + 200 and -200 < b["y"] < vh + 200]
        cx = [b["x"] + b["w"] / 2 for b in bs]
        return max(cx) - min(cx)

    early = spread()
    drift_settled(page)
    late = spread()
    assert late > vw * 0.35 and early < late * 0.6, (early, late)  # 中央に重なった写真が、自分の場所へ広がる
    w.assert_clean()


def test_drift_wheel_moves_each_photo_at_its_own_speed_then_settles(desktop_drift, site):
    page, w = open_drift(desktop_drift, site["main"])
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    page.mouse.move(vw / 2, vh / 2)  # マウスは中央（マウスによるずれなし）
    drift_settled(page)
    before = {b["i"]: b for b in on_screen(page, drift_boxes(page))}
    assert len(before) >= 4
    for _ in range(3):
        page.mouse.wheel(0, 80)
        page.wait_for_timeout(16)
    spreads = []
    for _ in range(12):  # 動いている間、何度か測る（遅い PC でも確かめられるように）
        page.wait_for_timeout(40)
        mid = {b["i"]: b for b in drift_boxes(page)}
        moved = [before[i]["y"] - mid[i]["y"] for i in before]
        if min(moved) > 0:  # 下へスクロールすると写真は上へ
            spreads.append(max(moved) - min(moved))
    assert spreads and max(spreads) > 4, spreads  # 写真ごとに違う速さ（係数 0.5〜1）で追いかける
    drift_settled(page)
    after = {b["i"]: b for b in drift_boxes(page)}
    final = [round(before[i]["y"] - after[i]["y"], 1) for i in before]
    assert max(final) - min(final) < 1  # 止まるとそろって元の並びに戻る
    assert abs(final[0] - 120) < 2  # 80 × 3 × 0.5（ホイールの倍率）
    assert all(abs(before[i]["x"] - after[i]["x"]) < 1 for i in before)
    assert page.evaluate("Math.round(window.pageYOffset)") == 0  # ページはスクロールしない
    w.assert_clean()


def test_drift_is_endless_in_every_direction(desktop_drift, site):
    page, w = open_drift(desktop_drift, site["main"])
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    page.mouse.move(vw / 2, vh / 2)
    for dx, dy in ((0, 400), (400, 0), (0, -400), (-400, 0)):
        for _ in range(25):
            page.mouse.wheel(dx, dy)
        drift_settled(page)
        shown = [b for b in drift_boxes(page) if b["x"] < vw and b["x"] + b["w"] > 0 and b["y"] < vh and b["y"] + b["h"] > 60]
        assert len(shown) >= 4, (dx, dy, len(shown))  # どこまで行っても写真が続く
    w.assert_clean()


def test_drift_drag_moves_and_tap_opens_the_photo(phone_drift, site):
    page, w = open_drift(phone_drift, site["main"])
    t = Touch(page)
    before = {b["i"]: b for b in drift_boxes(page)}
    t.swipe(200, 500, -60, -180, steps=12)  # 指で左上へ動かす
    page.wait_for_timeout(50)
    assert not viewer_open(page)  # ドラッグでは開かない
    drift_settled(page)
    after = {b["i"]: b for b in drift_boxes(page)}
    i0 = next(i for i, b in before.items() if b["inView"])
    assert after[i0]["y"] < before[i0]["y"] - 150 and after[i0]["x"] < before[i0]["x"] - 40
    target = on_screen(page, drift_boxes(page))[0]
    page.touchscreen.tap(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
    page.wait_for_function("document.getElementById('viewer').open")
    assert page.get_attribute("#viewer-img", "data-id") == target["id"]
    assert counter(page) == f"{target['i'] + 1} / {N_NEWEST}"
    page.click("#viewer-close")
    assert page.evaluate("document.activeElement.dataset.index") == str(target["i"])
    drift_settled(page)
    again = {b["i"]: b for b in drift_boxes(page)}
    assert abs(again[target["i"]]["y"] - target["y"]) < 1  # 閉じても一覧は動かない
    w.assert_clean()


def test_drift_keyboard_tab_reveals_photos_and_opens_them(desktop_drift, site):
    page, w = open_drift(desktop_drift, site["main"])
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    page.mouse.move(vw / 2, vh / 2)
    page.focus("#tab-2026")
    for k in range(12):
        page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('drift-tile')")
    idx = int(page.evaluate("document.activeElement.dataset.index"))
    assert idx == 11
    drift_settled(page)
    r = page.evaluate("(() => { const r = document.activeElement.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
    assert r[0] >= 0 and r[2] <= vw and r[1] >= 56 and r[3] <= vh  # フォーカスした写真が見える所まで動く
    page.keyboard.press("Enter")
    page.wait_for_function("document.getElementById('viewer').open")
    assert counter(page) == f"12 / {N_NEWEST}"
    page.keyboard.press("ArrowRight")
    assert counter(page) == f"13 / {N_NEWEST}"
    page.keyboard.press("Escape")
    assert not viewer_open(page)
    assert page.evaluate("document.activeElement.dataset.index") == "12"
    drift_settled(page)
    r = page.evaluate("(() => { const r = document.activeElement.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
    assert r[0] >= 0 and r[2] <= vw and r[1] >= 56 and r[3] <= vh  # 送った先の写真が見える所へ
    # 矢印キーで一覧を動かす
    b0 = drift_boxes(page)[12]
    page.keyboard.press("ArrowDown")
    drift_settled(page)
    assert abs((b0["y"] - drift_boxes(page)[12]["y"]) - 140) < 2
    w.assert_clean()


def test_drift_year_switch_replays_the_spread(phone_drift, site):
    page, w = open_drift(phone_drift, site["main"])
    page.click("#tab-2025")
    assert page.get_attribute("#drift", "data-intro") == "1"
    assert page.get_attribute("#drift-list", "aria-label") == "2025年の写真"
    drift_settled(page)
    ids = [b["id"] for b in drift_boxes(page)]
    assert ids == [p["id"] for p in site["doc"]["photos"] if p["year"] == 2025]
    assert page.inner_text("#photo-count") == "2025年の12枚"
    assert selected_tab(page) == "2025"
    w.assert_clean()


def test_drift_fills_the_screen_with_copies_when_few_photos(phone_drift, site):
    """写真が少ない年は並びを複製して画面を埋める。複製は読み上げ・Tab の対象にしない。"""
    page, w = open_drift(phone_drift, site["main"])
    page.click("#tab-2023")
    drift_settled(page)
    assert page.locator("#drift-list > li").count() == 2
    copies = page.locator("#drift-copies .drift-item")
    assert copies.count() >= 2
    assert page.get_attribute("#drift-copies", "aria-hidden") == "true"
    assert page.eval_on_selector_all("#drift-copies .drift-tile", "els => els.every(e => e.tabIndex === -1)")
    assert page.eval_on_selector_all("#drift-copies img", "els => els.every(e => e.alt === '')")
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    every = drift_boxes(page, "#drift .drift-item")
    shown = [b for b in every if b["x"] < vw and b["x"] + b["w"] > 0 and b["y"] < vh and b["y"] + b["h"] > 60]
    assert len(shown) >= 3
    # 複製をタップしても、その写真が開く
    c = next(b for b in drift_boxes(page, "#drift-copies .drift-item") if 0 <= b["x"] and b["x"] + b["w"] <= vw and 60 <= b["y"] and b["y"] + b["h"] <= vh)
    page.touchscreen.tap(c["x"] + c["w"] / 2, c["y"] + c["h"] / 2)
    page.wait_for_function("document.getElementById('viewer').open")
    assert page.get_attribute("#viewer-img", "data-id") == c["id"] and counter(page) == f"{c['i'] + 1} / 2"
    before = drift_boxes(page, "#drift .drift-item")
    page.click("#viewer-close")
    page.wait_for_timeout(300)
    drift_settled(page)
    after = drift_boxes(page, "#drift .drift-item")
    assert [(round(b["x"]), round(b["y"])) for b in after] == [(round(b["x"]), round(b["y"])) for b in before]  # 閉じても動かない
    w.assert_clean()


def test_drift_position_is_restored_after_reload_without_the_spread(phone_drift, site):
    page, w = open_drift(phone_drift, site["main"])
    Touch(page).swipe(200, 500, -40, -260, steps=12)
    drift_settled(page)
    page.wait_for_timeout(300)  # 位置の保存（止まってから 200ms）
    before = {b["i"]: (round(b["x"]), round(b["y"])) for b in drift_boxes(page) if b["inView"]}
    assert len(before) >= 3
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    assert page.get_attribute("#drift", "data-intro") is None  # 復元したときは広がる動きをしない
    drift_settled(page)
    after = {b["i"]: (round(b["x"]), round(b["y"])) for b in drift_boxes(page)}
    assert all(abs(before[i][0] - after[i][0]) <= 1 and abs(before[i][1] - after[i][1]) <= 1 for i in before)
    # 新しいタブでは最初から（広がる動きあり）
    other = phone_drift.new_page()
    other.goto(site["main"])
    other.wait_for_selector("html[data-pv=ready]")
    assert other.get_attribute("#drift", "data-intro") == "1"
    w.assert_clean()


def test_drift_mouse_shifts_photos_by_their_size(desktop_drift, site):
    page, w = open_drift(desktop_drift, site["main"])
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    page.mouse.move(vw / 2, vh / 2)
    drift_settled(page)
    before = {b["i"]: b for b in on_screen(page, drift_boxes(page))}
    page.mouse.move(vw - 1, vh / 2, steps=8)
    drift_settled(page)
    after = {b["i"]: b for b in drift_boxes(page)}
    ratios = [(after[i]["x"] - before[i]["x"]) / before[i]["w"] for i in before]
    assert all(abs(r - 0.25) < 0.03 for r in ratios), ratios  # 端で写真の幅の 1/4（大きい写真ほど大きくずれる）
    assert all(abs(after[i]["y"] - before[i]["y"]) < 1 for i in before)
    w.assert_clean()


def test_drift_develops_photos_and_shows_captions_as_they_appear(phone_drift, site):
    page, w = open_drift(phone_drift, site["main"])
    page.wait_for_timeout(1200)  # 番号・日付が出そろうまで
    shown = page.eval_on_selector_all(
        "#drift-list > li.is-in", "els => els.map(e => [e.querySelector('.drift-tile').classList.contains('is-developed') || !e.querySelector('img').complete, getComputedStyle(e.querySelector('.drift-line > span')).transform])"
    )
    assert shown and all(d for d, _ in shown)
    assert all(t in ("none", "matrix(1, 0, 0, 1, 0, 0)") for _, t in shown)
    hidden = page.eval_on_selector_all(
        "#drift-list > li:not(.is-in)", "els => els.map(e => [e.querySelector('.drift-tile').dataset.seen || '', getComputedStyle(e.querySelector('.drift-line > span')).transform])"
    )
    far = [h for h in hidden if h[0] != "1"]
    assert far and all(t != "none" for _, t in far)  # まだ画面に入っていない写真は番号・日付も隠れている
    # Tab で遠くの写真へ移ると、見える所まで動いて現像される
    page.evaluate("document.querySelector('#drift-list .drift-tile[data-index=\"40\"]').focus()")
    page.wait_for_selector("#drift-list .drift-tile[data-index='40'].is-developed", timeout=10000)
    page.wait_for_timeout(2600)
    st = page.evaluate("(() => { const s = getComputedStyle(document.querySelector('#drift-list .drift-tile[data-index=\"40\"] img')); return [s.opacity, s.filter]; })()")
    assert st == ["1", "none"]
    w.assert_clean()


def test_drift_broken_image_is_shown_and_year_switch_still_works(phone_drift, site):
    page = phone_drift.new_page()
    w = Watch(page)
    p0 = site["doc"]["photos"][0]
    page.route(f"**/{p0['mid']}", lambda r: r.fulfill(status=404, body=""))
    page.goto(site["main"])
    page.wait_for_selector("#drift-list .drift-tile[data-index='0'].is-broken.is-developed", timeout=10000)
    assert page.is_visible("#drift-list .drift-tile[data-index='0'] .tile-fallback")
    page.click("#tab-2025")
    drift_settled(page)
    assert page.locator("#drift-list > li").count() == 12
    assert w.page_errors == []


def test_reduced_motion_setting_switches_between_the_two_lists(phone_drift, site):
    """視差効果を減らす設定にすると、その場で動かない一覧（表紙＋正方形）に切り替わり、戻すと動く一覧に戻る。"""
    page, w = open_drift(phone_drift, site["main"])
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_function("document.documentElement.dataset.mode === 'still'")
    assert page.locator("#grid > li").count() == 60 and page.locator("#drift .drift-item").count() == 0
    assert page.is_visible("#cover") and page.is_visible("#grid")
    assert page.evaluate("document.scrollingElement.scrollHeight > innerHeight * 2")  # 縦にスクロールする一覧
    page.click(".tile[data-index='3']")
    assert counter(page) == f"4 / {N_NEWEST}"
    page.click("#viewer-close")
    page.emulate_media(reduced_motion="no-preference")
    page.wait_for_function("document.documentElement.dataset.mode === 'drift'")
    drift_settled(page)
    assert page.locator("#drift-list > li").count() == N_NEWEST and page.locator("#grid > li").count() == 0
    assert page.is_hidden("#cover")
    w.assert_clean()


def test_drift_empty_and_single_photo(phone_drift, site):
    page, w = open_page(phone_drift, site["empty"], state="empty")
    assert "写真はまだありません" in page.inner_text("#status")
    assert page.locator("#drift .drift-item").count() == 0
    w.assert_clean()
    page, w = open_drift(phone_drift, site["single"])
    assert page.locator("#drift-list > li").count() == 1
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    every = drift_boxes(page, "#drift .drift-item")
    assert len([b for b in every if b["x"] < vw and b["x"] + b["w"] > 0 and b["y"] < vh and b["y"] + b["h"] > 60]) >= 2
    w.assert_clean()


def test_drift_relative_urls_and_no_external_requests(phone_drift, site):
    page, w = open_drift(phone_drift, site["main"])
    Touch(page).swipe(200, 500, 0, -400, steps=10)
    drift_settled(page)
    assert w.requests and all(u.startswith(site["main"]) for u in w.requests), [u for u in w.requests if not u.startswith(site["main"])]
    w.assert_clean()
