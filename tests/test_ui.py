"""ブラウザー試験（同期仕様ID PV-SPEC-2026-10-v18 / 版 18.0）

Playwright（Chromium）でスマートフォン画面をエミュレートし、試験画像から生成したサイトを
GitHub Pages と同じ「リポジトリ名のサブパス」で配信して確認する。
分類: 回帰（一覧・スライド・遅延読込・エラー表示・アクセシビリティ）/ 操作（連打・スワイプ途中で閉じる）/
      保存（スクロール位置・再読込・古い保存値）/ 境界（0件・1件・2件）/ 異常（JSON・画像）/ シナリオ
仕様 v13: 年で分けない。試験用データは4年分（2026〜2023）あり、全部の写真が新しい順に1つの一覧に並ぶ。
仕様 v17: 動く見せ方では部品を画面の近くの写真だけ作る。並び全体は画面を送りながら集めて確かめ、
         写真が増えたときの速さ・部品の数は、同じ画像を使い回した3,000枚のデータ（site["big"]）で確かめる。
注意: これは実機ではなくエミュレーション。iPhone Safari・Android Chrome の実機確認は別途必要。
"""
from __future__ import annotations

import contextlib
import functools
import json
import re
import time
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
YEARS = ((2026, 72), (2025, 12), (2024, 6), (2023, 2))  # 合計92枚（動かない一覧は初回表示60枚＋追加表示）
N_ALL = 92  # 一覧の写真（v13 から年で分けず、全部の写真を1つの一覧に）


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


N_BIG = 3000  # 仕様 v17: 写真が増えたときの試験用（同じ画像を使い回した3,000枚のデータ）


def make_big_gallery(main: Path, dest: Path, n: int = N_BIG) -> dict:
    """生成済みのサイトを複製し、同じ画像を使い回して n 枚分の photos.json を作る（画面の速さ・部品の数の試験用）。
    撮影日は新しい日から1〜13日おきに、1日に1〜25枚（揺れるの紐の長さもばらばらにする）。"""
    import random
    import shutil
    from datetime import date, timedelta

    shutil.copytree(main, dest, ignore=shutil.ignore_patterns("source_photos", "logs", ".build", "tools"))
    doc = json.loads((main / "data" / "photos.json").read_text(encoding="utf-8"))
    src = doc["photos"]
    rng = random.Random(17)
    photos, day = [], date(2026, 10, 20)
    while len(photos) < n:
        k = rng.choices([1, 2, 3, 5, 9, 25], [50, 25, 10, 8, 5, 2])[0]
        for j in range(min(k, n - len(photos))):
            p = dict(src[len(photos) % len(src)])
            p["id"] = "%016x" % rng.getrandbits(64)
            p["date"] = f"{day.isoformat()}T{18 - j % 10:02d}:{(j * 7) % 60:02d}:00+09:00"
            p["year"] = day.year
            p["alt"] = f"{day.year}年{day.month}月{day.day}日 撮影の写真"
            p.pop("story", None)
            photos.append(p)
        day -= timedelta(days=rng.choice([1, 2, 3, 5, 8, 13]))
    big = dict(doc, photos=photos, count=n, years=sorted({p["year"] for p in photos}, reverse=True))
    (dest / "data" / "photos.json").write_text(json.dumps(big, ensure_ascii=False), encoding="utf-8")
    return big


COUNT_URL = "https://pvtest.goatcounter.com/count"


def make_counted_gallery(main: Path, dest: Path) -> dict:
    """生成済みのサイトを複製し、アクセス解析（GoatCounter の受付先）を設定したデータにする（仕様 v18 の試験用）。"""
    import shutil

    shutil.copytree(main, dest, ignore=shutil.ignore_patterns("source_photos", "logs", ".build", "tools"))
    doc = json.loads((dest / "data" / "photos.json").read_text(encoding="utf-8"))
    doc["analytics"] = {"goatcounter": COUNT_URL}
    (dest / "data" / "photos.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    main = make_project(root / "main" / "photo-viewer")
    empty = make_project(root / "empty" / "photo-viewer")
    single = make_project(root / "single" / "photo-viewer")
    few = make_project(root / "few" / "photo-viewer")
    doc = build_gallery(main, YEARS)
    build_gallery(empty, ())
    build_gallery(single, ((2025, 1),))
    build_gallery(few, ((2023, 2),))
    big = make_big_gallery(main, root / "big" / "photo-viewer")
    counted = make_counted_gallery(main, root / "counted" / "photo-viewer")
    with serve(root) as base:
        yield {
            "base": base,
            "main": f"{base}/main/photo-viewer/",
            "empty": f"{base}/empty/photo-viewer/",
            "single": f"{base}/single/photo-viewer/",
            "few": f"{base}/few/photo-viewer/",
            "big": f"{base}/big/photo-viewer/",
            "counted": f"{base}/counted/photo-viewer/",
            "counted_doc": counted,
            "doc": doc,
            "big_doc": big,
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
# 仕様 v12: 動く見せ方では最初に見せ方を選ぶ画面（Choose a view）が出る。パターンの試験では、同じタブで選び済みの状態から始める
PICK_1 = "try { sessionStorage.setItem('pv:pattern', '1') } catch (e) {}"
PICK_2 = "try { sessionStorage.setItem('pv:pattern', '2') } catch (e) {}"


def motion_context(browser, pattern="1", **kw):
    """動く見せ方のブラウザー（pattern=None なら見せ方を選ぶ画面から始まる）。"""
    ctx = browser.new_context(**kw, **MOTION)
    if pattern:
        ctx.add_init_script(f"try {{ sessionStorage.setItem('pv:pattern', '{pattern}') }} catch (e) {{}}")
    return ctx


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
    ctx = motion_context(browser, "1", **p.devices["iPhone 13"])
    yield ctx
    ctx.close()


@pytest.fixture
def desktop_drift(pw):
    """動く一覧（ふだんの設定）の PC（マウスあり）。"""
    _p, browser = pw
    ctx = motion_context(browser, "1", viewport={"width": 1440, "height": 900})
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


def scroll_y(page):
    return page.evaluate("Math.round(window.pageYOffset)")


def anchor_id(page):
    """画面の上端より下に見えている最初の写真のID（アプリのスクロール保存と同じ規則。v13 から上に残るバーはない）。"""
    return page.evaluate(
        """() => { const li = [...document.querySelectorAll('#grid > li')].find(e => e.getBoundingClientRect().bottom > 1);
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
# 回帰: 一覧（v13: 年で分けない）
# ===========================================================================
@pytest.mark.parametrize("device", ["iPhone 13", "iPhone SE", "Pixel 5"])
def test_portfolio_grid_on_phones(pw, site, device):
    """S-05: 表紙の写真を元の縦横比で大きく、その下に全部の写真の正方形の2列。各写真に番号（「No.」なし）と撮影日。"""
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
    years = site["doc"]["years"]
    assert page.inner_text("#photo-range") == f"{years[-1]}–{years[0]}"  # 撮影年の範囲だけ
    assert page.inner_text("#photo-count") == "" and page.is_hidden("#photo-count")  # 「〜年の〜枚」は出さない（v13）
    # 目録番号は全写真の通し番号（いちばん古い写真が 01）。「No.」は付けない
    total = len(site["ids"])
    nos = page.eval_on_selector_all("#grid > li .tile-no", "els => els.map(e => e.textContent)")
    assert nos[:3] == [str(total), str(total - 1), str(total - 2)]
    assert all(re.fullmatch(r"\d{2,}", n) for n in nos)
    page.click(".tile[data-index='0']")
    assert page.inner_text("#viewer-no") == nos[0]
    w.assert_clean()


def test_text_uses_bundled_angular_font(pw, site):
    """仕様 v12: 文字はカクカクした DotGothic16（画面の文言だけの小さな版）。手書き風の Klee One は使わない。"""
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
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.fonts.check('12px \"DotGothic16 UI\"', '47 2025–2026 Change view Choose a view')")
        assert page.evaluate("[...document.fonts].some(f => f.family.replace(/\"/g, '') === 'DotGothic16 UI' && f.status === 'loaded')")
        assert any(u == site["main"] + "fonts/dotgothic16-subset.woff2" for u in w.requests)
        assert not [u for u in w.requests if "klee" in u]
        fams = page.evaluate("['#photo-range', '.tile-no', '.tile-date', 'body'].map(s => getComputedStyle(document.querySelector(s)).fontFamily)")
        assert all(f.startswith('"DotGothic16 UI", "Hiragino Sans"') for f in fams), fams
        sizes = page.evaluate("['#photo-range', '.tile-no', '.tile-date', 'body'].map(s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize))")
        assert max(sizes) <= 12, sizes  # 文字は小さめ
        assert page.title() == "Daily"
        w.assert_clean()
    finally:
        ctx.close()


def test_all_photos_in_one_list_without_year_tabs(phone, site):
    """仕様 v13: 年で分けない。年のタブ・上部のバーはなく、全部の写真（4年分）が新しい順に1つの一覧に並ぶ。"""
    page, w = open_page(phone, site["main"])
    assert page.locator(".year-tab, #year-tabs, [role=tab], [role=tablist], .site-bar, .bar-year").count() == 0
    assert tile_ids(page) == site["ids"][:60]
    assert scroll_y(page) == 0 and page.is_visible("#cover")
    for _ in range(30):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(100)
        if page.locator(".tile").count() == N_ALL:
            break
    assert tile_ids(page) == site["ids"]  # 2026 → 2025 → 2024 → 2023 と続く
    years = [p["year"] for p in site["doc"]["photos"]]
    assert years == sorted(years, reverse=True) and set(years) == {2026, 2025, 2024, 2023}
    assert page.evaluate("localStorage.getItem('pv:selectedYear')") is None
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
    assert len(first) < N_ALL
    for _ in range(30):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(120)
        if page.locator(".tile").count() == N_ALL:
            break
    assert page.locator(".tile").count() == N_ALL
    page.wait_for_timeout(800)
    assert len({u for u in w.requests if "/thumbs/" in u}) > len(first)
    assert len(set(tile_ids(page))) == N_ALL
    w.assert_clean()


# ===========================================================================
# 回帰: 全画面スライド
# ===========================================================================
def test_viewer_navigation_with_buttons_and_keys(phone, site):
    page, w = open_page(phone, site["main"])
    ids = site["ids"]
    page.click(".tile[data-index='2']")
    assert viewer_open(page) and counter(page) == "3 / 92"  # 前後は全部の写真（年をまたぐ）
    assert page.get_attribute("#viewer-img", "data-id") == ids[2]
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    page.click("#viewer-next")
    assert counter(page) == "4 / 92"
    page.click("#viewer-prev")
    page.click("#viewer-prev")
    assert counter(page) == "2 / 92"
    page.keyboard.press("ArrowRight")
    assert counter(page) == "3 / 92"
    page.keyboard.press("ArrowLeft")
    assert counter(page) == "2 / 92"
    page.keyboard.press("Home")
    assert counter(page) == "1 / 92" and page.is_disabled("#viewer-prev")
    page.keyboard.press("ArrowLeft")
    assert counter(page) == "1 / 92"
    page.keyboard.press("End")
    assert counter(page) == "92 / 92" and page.is_disabled("#viewer-next")
    assert page.get_attribute("#viewer-img", "data-id") == ids[91]
    page.keyboard.press("Escape")
    assert not viewer_open(page)
    assert page.evaluate("document.documentElement.classList.contains('viewer-open')") is False
    assert page.evaluate("document.activeElement.dataset.index") == "91"  # 最後に見た写真へフォーカスが戻る（追加表示される）
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
    assert counter(page) == "2 / 92"
    t.swipe(x - 100, y, 220)
    assert counter(page) == "1 / 92"
    t.swipe(x - 100, y, 220)  # 先頭ではそれ以上戻らない
    assert counter(page) == "1 / 92"
    t.swipe(x, y, -20)  # 小さな動きはスワイプとみなさない
    assert counter(page) == "1 / 92"
    t.swipe(x, y + 100, 0, -250)  # 縦方向は無視
    assert counter(page) == "1 / 92"
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
    assert counter(page) == "13 / 92"
    page.evaluate("() => { for (let i = 0; i < 300; i++) document.getElementById('viewer-next').click(); }")
    assert counter(page) == "92 / 92"
    assert page.get_attribute("#viewer-img", "data-id") == site["ids"][91]
    assert page.evaluate("document.activeElement.id") in ("viewer-prev", "viewer-close")  # 無効化されたボタンにフォーカスを残さない
    page.evaluate("() => { for (let i = 0; i < 300; i++) document.getElementById('viewer-prev').click(); }")
    assert counter(page) == "1 / 92"
    page.wait_for_function("document.getElementById('viewer-img').naturalWidth > 0")
    assert not page.evaluate("document.getElementById('viewer-stage').classList.contains('is-loading')")
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
    assert viewer_open(page) and counter(page) == "6 / 92"
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
    assert counter(page) == "1 / 92"
    assert page.evaluate("document.getElementById('viewer-img').style.transform") == ""
    assert not page.evaluate("document.getElementById('viewer-stage').classList.contains('is-dragging')")
    t.swipe(x + 100, y, -220)  # 再度開いた後のスワイプは正常
    assert counter(page) == "2 / 92"
    w.assert_clean()


# ===========================================================================
# 保存: スクロール位置・再読込・古い保存値（v13 で年の保存は廃止）
# ===========================================================================
def test_scroll_position_restored_after_reload(phone, site):
    page, w = open_page(phone, site["main"])
    page.evaluate("window.scrollTo(0, 900)")
    page.wait_for_timeout(500)
    y, anchor = scroll_y(page), anchor_id(page)
    assert y == 900 and anchor
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert abs(scroll_y(page) - y) <= 2 and anchor_id(page) == anchor
    w.assert_clean()


def test_deep_scroll_beyond_first_batch_is_restored(phone, site):
    page, w = open_page(phone, site["main"])
    for _ in range(30):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(100)
    page.wait_for_timeout(500)
    y, anchor = scroll_y(page), anchor_id(page)
    assert page.locator(".tile").count() == N_ALL and site["ids"].index(anchor) >= 60
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert abs(scroll_y(page) - y) <= 2 and anchor_id(page) == anchor
    w.assert_clean()


def test_new_tab_starts_at_top(phone, site):
    page, _ = open_page(phone, site["main"])
    page.evaluate("window.scrollTo(0, 400)")
    page.wait_for_timeout(500)
    page2, w2 = open_page(phone, site["main"])  # 同じブラウザーの新しいタブ
    assert scroll_y(page2) == 0 and tile_ids(page2) == site["ids"][:60]
    w2.assert_clean()


def test_old_year_and_position_storage_is_ignored_and_cleared(phone, site):
    """v12 までの保存値（選んだ年・年ごとのスクロール位置・パターン1の位置）は使わず、見つけたら消す。"""
    page, w = open_page(phone, site["main"])
    page.add_init_script(
        "localStorage.setItem('pv:selectedYear', '2024');"
        "sessionStorage.setItem('pv:drift', JSON.stringify({v: 1, year: '2024', fx: 0.5, fy: 0.5}));"
        "sessionStorage.setItem('pv:scroll', JSON.stringify({v: 1, year: '2024', anchorId: null, delta: 0, y: 600}));"
    )
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert tile_ids(page) == site["ids"][:60]  # 年で絞り込まない
    assert scroll_y(page) == 0  # v1（年ごと）の位置は使わない
    assert page.evaluate("localStorage.getItem('pv:selectedYear')") is None
    assert page.evaluate("sessionStorage.getItem('pv:drift')") is None
    page.evaluate("window.scrollTo(0, 700)")
    page.wait_for_timeout(500)
    saved = json.loads(page.evaluate("sessionStorage.getItem('pv:scroll')"))
    assert saved["v"] == 2 and "year" not in saved and saved["anchorId"] == anchor_id(page)
    w.assert_clean()


def test_missing_anchor_and_broken_storage(phone, site):
    page, w = open_page(phone, site["main"])
    # 離脱時の保存（pagehide）より後、app.js より前に書き込むため init script で注入する
    page.add_init_script(
        "sessionStorage.setItem('pv:scroll', JSON.stringify({v: 2, anchorId: 'deadbeefdeadbeef', delta: 0, y: 500}));"
    )
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert abs(scroll_y(page) - 500) <= 2  # 基準写真が消えていれば保存位置付近へ
    page.add_init_script("sessionStorage.setItem('pv:scroll', '{broken');")
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
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
        page.evaluate("window.scrollTo(0, 300)")
        page.wait_for_timeout(400)
        page.click(".tile[data-index='0']")
        assert counter(page) == "1 / 92"
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 境界: 0件・1件
# ===========================================================================
def test_empty_gallery(phone, site):
    page, w = open_page(phone, site["empty"], state="empty")
    assert page.inner_text("#status-text") == "写真はまだありません。"
    assert page.locator(".tile").count() == 0
    assert page.inner_text("#photo-count") == "" and page.inner_text("#photo-range") == "" and page.inner_text("#app-title") == "写真"
    assert page.is_hidden("#status-retry")
    w.assert_clean()


def test_single_photo(phone, site):
    page, w = open_page(phone, site["single"])
    assert page.locator(".tile").count() == 1 and page.inner_text("#photo-range") == "2025"
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
    assert page.locator(".tile").count() == 0
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
    assert page.is_hidden("#viewer-error") and counter(page) == "2 / 92"
    assert w.page_errors == []


# ===========================================================================
# 回帰: アクセシビリティ・reduced-motion・相対URL
# ===========================================================================
def test_accessibility_basics(desktop, site):
    page, w = open_page(desktop, site["main"])
    assert page.get_attribute("html", "lang") == "ja"
    assert page.locator("[role=tablist], [role=tab], [role=tabpanel]").count() == 0  # 年のタブは v13 で廃止
    assert page.get_attribute("#grid-panel", "aria-label") == "写真一覧"
    # キーボード: スキップリンク → 表紙 → 一覧の写真
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip-link"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "cover-btn"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.dataset.index") == "0"
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
        assert page.evaluate("getComputedStyle(document.querySelector('.tile img')).transitionDuration").startswith("1e-05")
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
            assert page.locator(".tile").count() == 2  # 2025年と2024年の写真が1つの一覧に
            assert page.inner_text("#photo-range") == "2024–2025"
            # 写真を追加して再生成・公開準備
            make_image(s / "2026" / "c.jpg", (800, 600), exif_dt="2026:01:01 00:00:00", label="C")
            make_image(s / "2025" / "d.jpg", (800, 600), exif_dt="2025:12:31 23:59:59", label="D")
            prepare()
            page.reload()
            page.wait_for_selector("html[data-pv=ready]")
            doc = load_photos(project)
            assert tile_ids(page) == [x["id"] for x in doc["photos"]]
            assert [x["date"][:10] for x in doc["photos"]] == ["2026-01-01", "2025-12-31", "2025-05-01", "2024-05-01"]
            nos = page.eval_on_selector_all("#grid > li .tile-no", "els => els.map(e => e.textContent)")
            assert nos == ["04", "03", "02", "01"]  # 追加した写真も通し番号
            assert page.inner_text("#photo-range") == "2024–2026"
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
        assert viewer_open(page) and counter(page) == "2 / 92" and image_fits()
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


def test_back_navigation_restores_scroll(phone, site):
    page, w = open_page(phone, site["main"])
    page.evaluate("window.scrollTo(0, 700)")
    page.wait_for_timeout(500)
    y, anchor = scroll_y(page), anchor_id(page)
    page.goto(site["base"] + "/")  # 別のページへ移動してから「戻る」
    page.go_back()
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_timeout(300)
    assert abs(scroll_y(page) - y) <= 2 and anchor_id(page) == anchor
    w.assert_clean()


def test_rapid_swipes_never_go_out_of_range(phone, site):
    page, w = open_page(phone, site["few"])  # 2枚だけの一覧
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
    assert page.get_attribute("#viewer-img", "data-id") in tile_ids(page)
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
        assert counter(page) == "1 / 92"
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 上に残るバーはない（v13）／全画面表示の操作は写真に重ならない
# ===========================================================================
def test_no_top_bar_and_cover_starts_at_the_top(pw, site):
    """仕様 v13: 上部のバー（年のタブ）はなく、表紙が画面の上から始まる。スクロールすると全部流れる。"""
    p, browser = pw
    for device in ("iPhone 13", "desktop"):
        ctx = browser.new_context(**p.devices["iPhone 13"], **STILL) if device != "desktop" else browser.new_context(viewport={"width": 1440, "height": 900}, **STILL)
        try:
            page, w = open_page(ctx, site["main"])
            assert page.locator(".site-bar, .app-header, .bar-year, .year-index").count() == 0
            top = page.evaluate("document.getElementById('cover').getBoundingClientRect().top")
            assert top == 0
            pad = page.evaluate("document.getElementById('cover-btn').getBoundingClientRect().top")
            assert 20 <= pad <= 60, (device, pad)
            page.evaluate("window.scrollTo(0, 1400)")
            page.wait_for_timeout(200)
            assert page.evaluate("document.getElementById('cover').getBoundingClientRect().bottom") <= 0
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
    """スマホがダークモードでも、白い背景に黒い文字。"""
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], color_scheme=scheme, **STILL)
    try:
        page, w = open_page(ctx, site["main"])
        colors = page.evaluate(
            """(() => { const c = s => getComputedStyle(document.querySelector(s)).color;
               return { body: getComputedStyle(document.body).backgroundColor,
                        text: ['#photo-range', '.tile-no', '.tile-date'].map(c) }; })()"""
        )
        assert colors["body"] == "rgb(255, 255, 255)"
        assert page.get_attribute("meta[name=theme-color]", "content") == "#ffffff"
        assert set(colors["text"]) == {"rgb(0, 0, 0)"}, colors["text"]
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
# 仕様 v6: トップ（表紙）の写真を選べる（動かない一覧の先頭）
# ===========================================================================
def _with_cover(page, cover):
    def handler(route):
        resp = route.fetch()
        doc = resp.json()
        doc["cover"] = cover
        route.fulfill(response=resp, json=doc)

    page.route("**/data/photos.json", handler)


def test_chosen_cover_is_shown_and_opens_at_its_place_in_the_list(phone, site):
    chosen = site["doc"]["photos"][75]  # 2025年の写真（いちばん新しい写真とは別）
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
    assert viewer_open(page) and counter(page) == f"76 / {N_ALL}"  # 一覧の中のその写真の位置で開く（v13）
    assert page.get_attribute("#viewer-img", "data-id") == chosen["id"]
    page.click("#viewer-next")
    assert page.get_attribute("#viewer-img", "data-id") == site["ids"][76]
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.id") == "cover-btn"  # 表紙へフォーカスが戻る
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
# 仕様 v8: 演出（現像）／仕様 v9: 小話の廃止
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


def test_develop_does_not_break_broken_images(phone, site):
    page = phone.new_page()
    w = Watch(page)
    p0 = site["doc"]["photos"][0]
    page.route(f"**/{p0['thumb']}", lambda r: r.fulfill(status=404, body=""))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    page.wait_for_selector(".tile[data-index='0'].is-broken.is-developed")
    assert page.is_visible(".tile[data-index='0'] .tile-fallback")
    page.wait_for_selector(".tile[data-index='1'].is-developed:not(.is-broken)")
    assert w.page_errors == []


def test_site_name_is_not_shown_but_named(pw, site):
    """仕様 v11: サイト名（Daily）は画面に出さない。読み上げ用の見出しとブラウザーのタブの名前にだけ使う。"""
    p, browser = pw
    for opts in (STILL, MOTION):
        ctx = browser.new_context(**p.devices["iPhone 13"], **opts)
        ctx.add_init_script(PICK_1)
        try:
            page = ctx.new_page()
            w = Watch(page)
            _patch_doc(page, lambda d: d.update(title="Daily"))
            page.goto(site["main"])
            page.wait_for_selector("html[data-pv=ready]")
            assert page.title() == "Daily"
            assert page.inner_text("h1#app-title") == "Daily"  # 読み上げでは見出しとして読む
            r = page.evaluate("(() => { const r = document.getElementById('app-title').getBoundingClientRect(); return [r.width, r.height]; })()")
            assert r[0] <= 1 and r[1] <= 1  # 画面には出ない
            assert page.locator("#app-title svg, template#title-art").count() == 0  # v8 の書き順の図形は廃止
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
# 仕様 v10: 動く一覧（パターン1。終わりのない平面に散らした写真。視差効果を減らす設定でないとき）
# 仕様 v13: 散らし方は読み込むたびに変わる。「Change view」も写真と一緒に散らす
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


def drift_boxes_all(page):
    """平面を縦横に送りながら、部品を作った写真を集める（v17: 部品は画面の周りの写真だけ）。位置は送った先のもの。"""
    vw, vh, dpr = page.evaluate("[innerWidth, innerHeight, devicePixelRatio]")
    seen = {b["i"]: b for b in drift_boxes(page)}

    def watch():  # 動いている途中も何度か集める（通り過ぎた写真も拾う）
        for _ in range(5):
            page.wait_for_timeout(80)
            seen.update({b["i"]: b for b in drift_boxes(page) if b["i"] not in seen})

    # スマホの画面では、ホイールの量が画面の細かさ（devicePixelRatio）で割られて届くので掛けておく
    for _ in range(4):
        for _ in range(4):
            page.mouse.wheel(vw * 2 * dpr, 0)  # 画面の幅だけ（ホイールの倍率 0.5）
            watch()
        page.mouse.wheel(0, vh * 2.6 * dpr)  # 画面の高さの 1.3 倍
        watch()
    drift_settled(page)
    return [seen[i] for i in sorted(seen)]


def on_screen(page, boxes):
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    return [b for b in boxes if b["x"] >= 0 and b["y"] >= 0 and b["x"] + b["w"] <= vw and b["y"] + b["h"] <= vh]


def test_drift_scatters_all_photos_in_their_own_shapes(phone_drift, site):
    """パターン1: 全部の写真を元の縦横比のまま大小ばらばらに散らし、中間画像（mids/）を使う。表紙は出さない。"""
    page, w = open_drift(phone_drift, site["main"])
    assert page.evaluate("document.documentElement.dataset.mode") == "drift"
    assert page.locator("#grid > li").count() == 0 and page.is_hidden("#cover")
    boxes = drift_boxes(page)
    idx = [b["i"] for b in boxes]
    assert idx == sorted(idx)  # 読み上げ・Tab は新しい順
    # v17: 部品は画面の周りの写真だけ。いちばん新しい写真といちばん古い写真はいつもある（Tab で一覧に入ったときの行き先）
    assert len(boxes) < N_ALL and idx[0] == 0 and idx[-1] == N_ALL - 1
    assert page.eval_on_selector("#drift-list > li", "e => [e.getAttribute('aria-setsize'), e.getAttribute('aria-posinset')]") == [str(N_ALL), "1"]
    doc = {p["id"]: p for p in site["doc"]["photos"]}
    assert [b["id"] for b in boxes] == [site["ids"][i] for i in idx]  # 年で分けない（4年分）
    for b in boxes:
        p = doc[b["id"]]
        assert abs(b["w"] / b["h"] - p["width"] / p["height"]) < 0.03  # 元の縦横比
    many = drift_boxes_all(page)  # v17: 部品は画面の周りの写真だけなので、平面を送りながら集める
    assert len(many) >= N_ALL * 0.6
    widths = {round(b["w"]) for b in many if doc[b["id"]]["width"] > doc[b["id"]]["height"]}
    assert len(widths) >= 2  # 大きさがばらばら
    srcs = page.eval_on_selector_all("#drift-list img", "els => els.map(e => e.getAttribute('src'))")
    assert all(s.startswith("mids/") for s in srcs)
    assert not [u for u in w.requests if "/thumbs/" in u or "/photos/" in u]  # 一覧では中間画像だけ読む
    caps = page.eval_on_selector_all("#drift-list > li:first-child .drift-line", "els => els.map(e => e.textContent)")
    assert caps == [str(len(site["ids"])), site["doc"]["photos"][0]["date"][:10].replace("-", ".")]  # 「No.」は付けない
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
    assert page.inner_text("#photo-count") == "" and page.locator(".drift-year, #drift-years").count() == 0
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
    i0 = next(i for i, b in before.items() if b["inView"] and b["y"] > 250 and b["x"] > 80)  # 反対側へ回らない写真
    assert after[i0]["y"] < before[i0]["y"] - 150 and after[i0]["x"] < before[i0]["x"] - 40
    target = on_screen(page, drift_boxes(page))[0]
    page.touchscreen.tap(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
    page.wait_for_function("document.getElementById('viewer').open")
    assert page.get_attribute("#viewer-img", "data-id") == target["id"]
    assert counter(page) == f"{target['i'] + 1} / {N_ALL}"
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
    page.focus("#pattern-switch")
    for k in range(12):  # 「Change view」のあと、写真を12枚目まで（散らし方によらず新しい順）
        page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('drift-tile')")
    idx = int(page.evaluate("document.activeElement.dataset.index"))
    assert idx == 11
    drift_settled(page)
    r = page.evaluate("(() => { const r = document.activeElement.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
    assert r[0] >= 0 and r[2] <= vw and r[1] >= 0 and r[3] <= vh  # フォーカスした写真が見える所まで動く
    page.keyboard.press("Enter")
    page.wait_for_function("document.getElementById('viewer').open")
    assert counter(page) == f"12 / {N_ALL}"
    page.keyboard.press("ArrowRight")
    assert counter(page) == f"13 / {N_ALL}"
    page.keyboard.press("Escape")
    assert not viewer_open(page)
    assert page.evaluate("document.activeElement.dataset.index") == "12"
    drift_settled(page)
    r = page.evaluate("(() => { const r = document.activeElement.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
    assert r[0] >= 0 and r[2] <= vw and r[1] >= 0 and r[3] <= vh  # 送った先の写真が見える所へ
    # 矢印キーで一覧を動かす
    b0 = {b["i"]: b for b in drift_boxes(page)}[12]
    page.keyboard.press("ArrowDown")
    drift_settled(page)
    assert abs((b0["y"] - {b["i"]: b for b in drift_boxes(page)}[12]["y"]) - 140) < 2
    w.assert_clean()


def test_drift_scatter_changes_on_every_load(phone_drift, site):
    """仕様 v13: 散らし方（どの写真がどこに・どの大きさで）は読み込むたびに変わる。位置は保存せず、毎回広がる。"""
    page, w = open_drift(phone_drift, site["main"])

    def first_screen():
        return {b["id"]: (round(b["x"]), round(b["y"])) for b in drift_boxes(page) if b["inView"]}

    def sizes():  # v17: 部品は画面の周りの写真だけなので、平面を送りながら集める
        return {b["id"]: round(b["w"]) for b in drift_boxes_all(page)}

    screens, sizes_ = [first_screen()], [sizes()]
    for _ in range(2):
        page.reload()
        page.wait_for_selector("html[data-pv=ready]")
        assert page.get_attribute("#drift", "data-intro") == "1"  # 再読込でも中央から広がる（復元しない）
        drift_settled(page)
        screens.append(first_screen())
        sizes_.append(sizes())
    for a, b in zip(screens, screens[1:]):
        same = sum(1 for k in a if k in b and a[k] == b[k])
        assert same <= len(a) * 0.3, (same, len(a))  # 最初に見える写真と場所が変わる
    for a, b in zip(sizes_, sizes_[1:]):
        common = [k for k in a if k in b]
        assert len(common) >= N_ALL * 0.6, len(common)
        resized = sum(1 for k in common if a[k] != b[k])
        assert resized >= len(common) * 0.3, resized  # 大きさ（大・中・小）も変わる
    assert len({tuple(sorted(s)) for s in screens}) >= 2  # 最初に見える写真も変わる
    assert page.evaluate("sessionStorage.getItem('pv:drift')") is None  # 位置は保存しない
    # 番号は写真ごとに決まっていて変わらない
    nos = page.evaluate("Object.fromEntries([...document.querySelectorAll('#drift-list > li')].map(li => [li.querySelector('.drift-tile').dataset.id, li.querySelector('.drift-line').textContent]))")
    total = len(site["ids"])
    assert len(nos) >= 5 and all(nos[i] == f"{total - k:02d}" for k, i in enumerate(site["ids"]) if i in nos)
    w.assert_clean()


def test_drift_fills_the_screen_with_copies_when_few_photos(phone_drift, site):
    """写真が少ないときは並びを複製して画面を埋める。複製は読み上げ・Tab の対象にしない。"""
    page, w = open_drift(phone_drift, site["few"])
    assert page.locator("#drift-list > li").count() == 2
    copies = page.locator("#drift-copies .drift-item")
    assert copies.count() >= 2
    assert page.get_attribute("#drift-copies", "aria-hidden") == "true"
    assert page.eval_on_selector_all("#drift-copies .drift-tile", "els => els.every(e => e.tabIndex === -1)")
    assert page.eval_on_selector_all("#drift-copies img", "els => els.every(e => e.alt === '')")
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    every = drift_boxes(page, "#drift .drift-item:not(.drift-label-item)")
    shown = [b for b in every if b["x"] < vw and b["x"] + b["w"] > 0 and b["y"] < vh and b["y"] + b["h"] > 0]
    assert len(shown) >= 2 and len({b["id"] for b in shown}) == 2  # 2枚の写真が複製で画面を埋める
    # 複製をタップしても、その写真が開く
    c = None
    for _ in range(6):
        c = next((b for b in drift_boxes(page, "#drift-copies .drift-item") if 0 <= b["x"] and b["x"] + b["w"] <= vw and 0 <= b["y"] and b["y"] + b["h"] <= vh), None)
        if c:
            break
        page.mouse.wheel(0, 300)
        drift_settled(page)
    assert c, "画面に複製が見つかりません"
    page.touchscreen.tap(c["x"] + c["w"] / 2, c["y"] + c["h"] / 2)
    page.wait_for_function("document.getElementById('viewer').open")
    assert page.get_attribute("#viewer-img", "data-id") == c["id"] and counter(page) == f"{c['i'] + 1} / 2"
    before = drift_boxes(page, "#drift .drift-item:not(.drift-label-item)")
    page.click("#viewer-close")
    page.wait_for_timeout(300)
    drift_settled(page)
    after = drift_boxes(page, "#drift .drift-item:not(.drift-label-item)")
    assert [(round(b["x"]), round(b["y"])) for b in after] == [(round(b["x"]), round(b["y"])) for b in before]  # 閉じても動かない
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
        "#drift-list > li:not(.is-in)", "els => els.map(e => [e.querySelector('.drift-tile').dataset.seen || '', getComputedStyle(e.querySelector('.drift-line > span')).transform, e.querySelector('.drift-tile').dataset.index])"
    )
    far = [h for h in hidden if h[0] != "1"]
    assert far and all(t != "none" for t in [h[1] for h in far])  # まだ画面に入っていない写真は番号・日付も隠れている
    # Tab で遠くの写真へ移ると、見える所まで動いて現像される
    k = far[0][2]
    page.evaluate(f"document.querySelector('#drift-list .drift-tile[data-index=\"{k}\"]').focus()")
    page.wait_for_selector(f"#drift-list .drift-tile[data-index='{k}'].is-developed", timeout=10000)
    page.wait_for_timeout(2600)
    st = page.evaluate(f"(() => {{ const s = getComputedStyle(document.querySelector('#drift-list .drift-tile[data-index=\"{k}\"] img')); return [s.opacity, s.filter]; }})()")
    assert st == ["1", "none"]
    w.assert_clean()


def test_drift_broken_image_is_shown_and_others_still_work(phone_drift, site):
    page = phone_drift.new_page()
    w = Watch(page)
    p0 = site["doc"]["photos"][0]
    page.route(f"**/{p0['mid']}", lambda r: r.fulfill(status=404, body=""))
    page.goto(site["main"])
    page.wait_for_selector("html[data-pv=ready]")
    page.evaluate("document.querySelector('#drift-list .drift-tile[data-index=\"0\"]').focus()")  # 見える所まで動かす
    page.wait_for_selector("#drift-list .drift-tile[data-index='0'].is-broken.is-developed", timeout=10000)
    assert page.is_visible("#drift-list .drift-tile[data-index='0'] .tile-fallback")
    drift_settled(page)
    assert 2 <= page.locator("#drift-list > li").count() < N_ALL  # v17: 画面の周りの写真だけ
    assert page.locator("#drift-list .drift-tile.is-developed:not(.is-broken)").count() >= 2
    assert w.page_errors == []


def test_reduced_motion_setting_switches_between_the_two_lists(phone_drift, site):
    """視差効果を減らす設定にすると、その場で動かない一覧（表紙＋正方形）に切り替わり、戻すと動く一覧に戻る。"""
    page, w = open_drift(phone_drift, site["main"])
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_function("document.documentElement.dataset.mode === 'still'")
    assert page.locator("#grid > li").count() == 60 and page.locator("#drift .drift-item:not(.drift-label-item)").count() == 0
    assert page.is_visible("#cover") and page.is_visible("#grid")
    assert page.evaluate("document.scrollingElement.scrollHeight > innerHeight * 2")  # 縦にスクロールする一覧
    page.click(".tile[data-index='3']")
    assert counter(page) == f"4 / {N_ALL}"
    page.click("#viewer-close")
    page.emulate_media(reduced_motion="no-preference")
    page.wait_for_function("document.documentElement.dataset.mode === 'drift'")
    drift_settled(page)
    assert 0 < page.locator("#drift-list > li").count() < N_ALL and page.locator("#grid > li").count() == 0
    assert page.is_hidden("#cover")
    w.assert_clean()


def test_drift_empty_and_single_photo(phone_drift, site):
    page, w = open_page(phone_drift, site["empty"], state="empty")
    assert "写真はまだありません" in page.inner_text("#status")
    assert page.locator("#drift .drift-item:not(.drift-label-item)").count() == 0
    w.assert_clean()
    page, w = open_drift(phone_drift, site["single"])
    assert page.locator("#drift-list > li").count() == 1
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    every = drift_boxes(page, "#drift .drift-item:not(.drift-label-item)")
    assert len([b for b in every if b["x"] < vw and b["x"] + b["w"] > 0 and b["y"] < vh and b["y"] + b["h"] > 60]) >= 2
    w.assert_clean()


def test_drift_relative_urls_and_no_external_requests(phone_drift, site):
    page, w = open_drift(phone_drift, site["main"])
    Touch(page).swipe(200, 500, 0, -400, steps=10)
    drift_settled(page)
    assert w.requests and all(u.startswith(site["main"]) for u in w.requests), [u for u in w.requests if not u.startswith(site["main"])]
    w.assert_clean()


# ===========================================================================
# 仕様 v13: 「Change view」を写真と同じように散らす（v11 の年の文字の代わり）／仕様 v11: 選んだものの印「」
# ===========================================================================
LABELS = """() => [...document.querySelectorAll('#drift-labels .drift-label')].map(b => { const r = b.getBoundingClientRect();
    return { key: b.dataset.key, text: b.textContent, lang: b.lang, x: r.left, y: r.top, w: r.width, h: r.height,
             deco: getComputedStyle(b).textDecorationLine, tab: b.tabIndex }; })"""


def drift_labels(page):
    return page.evaluate(LABELS)


def test_drift_change_view_is_scattered_among_the_photos(phone_drift, site):
    page, w = open_drift(phone_drift, site["main"])
    labels = drift_labels(page)
    assert labels and all(l["text"] == "Change view" and l["lang"] == "en" for l in labels)
    assert all(l["deco"] == "underline" for l in labels)
    assert page.get_attribute("#drift-labels", "aria-hidden") == "true" and all(l["tab"] == -1 for l in labels)
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    first = [l for l in labels if l["x"] >= 0 and l["x"] + l["w"] <= vw and l["y"] >= 0 and l["y"] + l["h"] <= vh]
    assert len(first) >= 1  # 開いた画面に1つ見える
    # 写真と重ならない
    photos = [b for b in drift_boxes(page) if b["inView"]]
    for l in first:
        for b in photos:
            ox = min(l["x"] + l["w"], b["x"] + b["w"]) - max(l["x"], b["x"])
            oy = min(l["y"] + l["h"], b["bottom"]) - max(l["y"], b["y"])
            assert ox <= 1 or oy <= 1, (l, b)
    # 動かしていっても、だいたい1画面に1つ見えてくる
    # （PageDown は画面の高さの 0.7 倍ずつ動かす。1区画は画面1つ分＋1行なので、文字どうしは最大で2区画近く離れるが、
    #   3回続けて見えないことはない。並び1周より長く動かして確かめる）
    seen = []
    for _ in range(10):
        page.keyboard.press("PageDown")
        drift_settled(page)
        seen.append(any(l["x"] + l["w"] > 0 and l["x"] < vw and l["y"] + l["h"] > 0 and l["y"] < vh for l in drift_labels(page)))
    assert any(seen) and not any(not any(seen[k:k + 3]) for k in range(len(seen) - 2)), seen
    # 押すと見せ方を選ぶ画面が出る（開いた画面の文字を押す）
    page.reload()
    page.wait_for_selector("html[data-pv=ready]")
    drift_settled(page)
    target = next(l for l in drift_labels(page) if l["x"] >= 0 and l["x"] + l["w"] <= vw and l["y"] >= 0 and l["y"] + l["h"] <= vh)
    page.touchscreen.tap(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
    page.wait_for_function("document.getElementById('chooser').open")
    assert page.evaluate("document.documentElement.dataset.mode") == "choose"
    page.click(".chooser-opt[data-pattern='2']")
    assert page.evaluate("document.documentElement.dataset.mode") == "elastic"
    w.assert_clean()


def test_drift_change_view_moves_like_photos(desktop_drift, site):
    page, w = open_drift(desktop_drift, site["main"])
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    page.mouse.move(vw / 2, vh / 2)
    drift_settled(page)
    before = drift_labels(page)
    photos_before = {b["i"]: b for b in on_screen(page, drift_boxes(page))}
    for _ in range(3):
        page.mouse.wheel(0, -80)  # 上へ戻す向き（画面の中の写真・文字は下へ動き、反対側へ回らない）
        page.wait_for_timeout(16)
    drift_settled(page)
    after = drift_labels(page)
    photos_after = {b["i"]: b for b in drift_boxes(page)}
    shifts = sorted(photos_after[i]["y"] - photos_before[i]["y"] for i in photos_before)
    shift = shifts[len(shifts) // 2]
    assert abs(shift - 120) < 2  # 80 × 3 × 0.5（ホイールの倍率）
    on = [l["key"] for l in before if 0 <= l["y"] and l["y"] + l["h"] <= vh and 0 <= l["x"] and l["x"] + l["w"] <= vw]
    assert on  # 開いた画面に1つある
    b_, a_ = {l["key"]: l for l in before}, {l["key"]: l for l in after}
    assert all(abs((a_[k]["y"] - b_[k]["y"]) - shift) < 1.5 for k in on)  # 写真と同じだけ動いて止まる
    w.assert_clean()


def test_drift_pattern_switch_button_appears_only_for_keyboard(desktop_drift, site):
    """パターン1: 散らした文字とは別に、同じ働きのボタン（読み上げ・Tab 用）がある。キーボードで移ったときだけ見える。"""
    page, w = open_drift(desktop_drift, site["main"])
    assert page.locator(".site-bar, #year-tabs").count() == 0  # 上部のバー（年）はない
    sw = "getComputedStyle(document.getElementById('pattern-switch')).opacity"
    assert page.evaluate(sw) == "0" and page.inner_text("#pattern-switch") == "Change view"
    assert page.get_attribute("#pattern-switch", "lang") == "en"
    page.keyboard.press("Tab")  # スキップリンク
    page.keyboard.press("Tab")  # Change view
    assert page.evaluate("document.activeElement.id") == "pattern-switch"
    assert page.evaluate(sw) == "1"
    page.keyboard.press("Tab")  # 写真へ
    assert page.evaluate("document.activeElement.classList.contains('drift-tile')")
    assert page.evaluate(sw) == "0"
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Enter")
    page.wait_for_function("document.getElementById('chooser').open")
    page.keyboard.press("Escape")  # 前回のパターン（1）に戻る
    assert page.evaluate("document.documentElement.dataset.mode") == "drift"
    w.assert_clean()


MARK = """el => { const b = getComputedStyle(el, '::before'), a = getComputedStyle(el, '::after'), s = getComputedStyle(el);
    return { outline: s.outlineStyle, before: [b.opacity, b.borderTopWidth, b.borderLeftWidth, b.borderBottomWidth, b.borderTopColor],
             after: [a.opacity, a.borderBottomWidth, a.borderRightWidth, a.borderTopWidth, a.borderBottomColor] }; }"""


def assert_mark(page, shown):
    page.wait_for_timeout(250)
    m = page.evaluate(f"({MARK})(document.activeElement)")
    assert m["outline"] == "none", m  # 青い枠は出さない
    if shown:  # 左上に「、右下に」
        line = lambda v: v not in ("0px", "")  # 線の太さ（画面の細かさで 1〜1.5px）
        assert m["before"][0] == "1" and line(m["before"][1]) and line(m["before"][2]) and m["before"][3] == "0px", m
        assert m["after"][0] == "1" and line(m["after"][1]) and line(m["after"][2]) and m["after"][3] == "0px", m
        assert m["before"][4] == m["after"][4] == "rgb(0, 0, 0)", m
    else:
        assert m["before"][0] == "0" and m["after"][0] == "0", m


@pytest.mark.parametrize("opts", ["drift", "still"])
def test_selection_mark_is_corner_brackets_not_a_blue_frame(pw, site, opts):
    p, browser = pw
    ctx = motion_context(browser, "1", viewport={"width": 1280, "height": 900}) if opts == "drift" else browser.new_context(viewport={"width": 1280, "height": 900}, **STILL)
    try:
        page, w = open_page(ctx, site["main"])
        if opts == "drift":
            drift_settled(page)
        page.keyboard.press("Tab")  # スキップリンク
        page.keyboard.press("Tab")
        if opts == "still":
            assert page.evaluate("document.activeElement.id") == "cover-btn"
            assert_mark(page, True)  # 表紙の写真
            page.keyboard.press("Tab")
        else:
            assert page.evaluate("document.activeElement.id") == "pattern-switch"
            assert_mark(page, True)  # 「Change view」
            page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.dataset.index") == "0"
        if opts == "drift":
            drift_settled(page)
        assert_mark(page, True)  # 一覧の写真
        page.keyboard.press("Enter")
        page.wait_for_function("document.getElementById('viewer').open")
        assert page.evaluate("document.activeElement.id") == "viewer-close"
        assert_mark(page, True)  # 全画面表示のボタン
        page.keyboard.press("Escape")
        assert_mark(page, True)  # 戻った写真
        # 指やマウスで選んだときは印を出さない（閉じて戻ったときも）
        sel = ".drift-tile" if opts == "drift" else ".tile"
        box = page.evaluate(f"(() => {{ const r = [...document.querySelectorAll('{sel}')].find(e => {{ const q = e.getBoundingClientRect(); return e !== document.activeElement && q.top > 80 && q.bottom < innerHeight && q.left > 0 && q.right < innerWidth; }}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")
        page.mouse.click(*box)
        page.wait_for_function("document.getElementById('viewer').open")
        assert_mark(page, False)
        page.click("#viewer-close")
        assert_mark(page, False)
        css = Path(__file__).resolve().parent.parent.joinpath("css", "style.css").read_text(encoding="utf-8")
        assert "#2f6feb" not in css and "var(--focus)" not in css
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 仕様 v12: 最初に見せ方（パターン）を選ぶ／パターン2（列ごとに遅れてついてくる）。v13 から英語の「Change view」
# ===========================================================================
def chooser_open(page):
    return page.evaluate("document.getElementById('chooser').open")


def test_pattern_chooser_comes_first_and_is_remembered(pw, site):
    p, browser = pw
    ctx = motion_context(browser, None, **p.devices["iPhone 13"])
    try:
        page = ctx.new_page()
        w = Watch(page)
        page.goto(site["main"])
        page.wait_for_function("document.getElementById('chooser').open")
        assert page.evaluate("document.documentElement.dataset.mode") == "choose"
        assert page.inner_text("#chooser-title") == "Choose a view" and page.get_attribute("#chooser-title", "lang") == "en"
        opts = page.eval_on_selector_all(".chooser-opt", "els => els.map(e => [e.dataset.pattern, e.querySelector('.chooser-name').textContent])")
        assert opts == [["1", "散らばる"], ["2", "並べる"], ["3", "揺れる"]]  # v14: 「列」→「並べる」、3つ目を追加
        assert page.evaluate("document.activeElement.dataset.pattern") == "1"  # 初めては1が選ばれた状態
        page.wait_for_selector("html[data-pv=ready]")
        assert page.locator("#drift .drift-item, #elastic .eg-item, #grid > li").count() == 0  # 選ぶまで一覧は作らない
        page.click(".chooser-opt[data-pattern='2']")
        assert not chooser_open(page)
        assert page.evaluate("document.documentElement.dataset.mode") == "elastic"
        page.wait_for_selector("#elastic .eg-item")
        # 同じタブの再読込では選ぶ画面を出さない
        page.reload()
        page.wait_for_selector("html[data-pv=ready]")
        assert not chooser_open(page) and page.evaluate("document.documentElement.dataset.mode") == "elastic"
        # 新しいタブでは選ぶ画面から。前回選んだ2が選ばれた状態
        other = ctx.new_page()
        other.goto(site["main"])
        other.wait_for_function("document.getElementById('chooser').open")
        assert other.evaluate("document.activeElement.dataset.pattern") == "2"
        assert other.eval_on_selector(".chooser-opt[data-pattern='2']", "e => e.classList.contains('is-last')")
        other.keyboard.press("Escape")  # Escape は前回のパターンで見る
        assert other.evaluate("document.documentElement.dataset.mode") == "elastic"
        # 「Change view」で選び直す
        assert page.inner_text("#pattern-switch") == "Change view"
        page.click("#pattern-switch")
        assert chooser_open(page) and page.evaluate("document.documentElement.dataset.mode") == "choose"
        page.click(".chooser-opt[data-pattern='1']")
        assert page.evaluate("document.documentElement.dataset.mode") == "drift"
        assert page.get_attribute("#drift", "data-intro") == "1"  # 選び直したときも広がる
        drift_settled(page)
        assert page.locator("#drift-labels .drift-label").count() >= 1  # パターン1では写真と一緒に散らす
        w.assert_clean()
    finally:
        ctx.close()


def test_no_chooser_with_reduced_motion(phone, site):
    page, w = open_page(phone, site["main"])
    assert not chooser_open(page)
    assert page.evaluate("document.documentElement.dataset.mode") == "still"
    assert page.is_hidden("#pattern-switch")
    assert page.locator("#grid > li").count() == 60
    w.assert_clean()


# v17: 写真は1つのリスト（新しい順）に入り、列（data-col）と段の位置は app.js が置く。部品は画面の近くの写真だけ。
# 列ごとに、部品のある写真を上から並べて返す
EG_BOXES = """() => { const n = +document.querySelector('#elastic .eg-grid').dataset.cols; const cols = Array.from({ length: n }, () => []);
    [...document.querySelectorAll('#elastic .eg-item')].forEach(li => { const b = li.querySelector('.eg-tile'); const r = b.getBoundingClientRect();
      cols[+li.dataset.col].push({ col: +li.dataset.col, i: +b.dataset.index, id: b.dataset.id, x: r.left, y: r.top, w: r.width, h: r.height,
             cap: li.querySelector('.eg-cap').textContent, src: b.querySelector('img').getAttribute('src') }); });
    cols.forEach(c => c.sort((a, b) => a.i - b.i)); return cols; }"""


def eg_settled(page, timeout=8000):
    page.wait_for_selector("#elastic:not([data-moving])", timeout=timeout)


@pytest.mark.parametrize("device,cols", [("phone", 3), ("desktop", 7)])
def test_pattern2_lays_photos_in_columns(pw, site, device, cols):
    p, browser = pw
    kw = dict(**p.devices["iPhone 13"]) if device == "phone" else {"viewport": {"width": 1440, "height": 900}}
    ctx = motion_context(browser, "2", **kw)
    try:
        page, w = open_page(ctx, site["main"])
        assert page.evaluate("document.documentElement.dataset.mode") == "elastic"
        columns = page.evaluate(EG_BOXES)
        assert len(columns) == cols
        flat = sorted((b for c in columns for b in c), key=lambda b: b["i"])
        assert all(b["col"] == b["i"] % cols for b in flat)  # 横に読むと新しい順
        assert [b["id"] for b in flat] == [site["ids"][b["i"]] for b in flat]  # 年で分けない（v13）
        # v17: 部品は画面の近くの写真だけ（上から続けて）。いちばん古い写真はいつもある（Tab で一覧に入ったときの行き先）
        idx = [b["i"] for b in flat]
        assert len(idx) < N_ALL and idx[:-1] == list(range(len(idx) - 1)) and idx[-1] == N_ALL - 1
        assert page.evaluate("[...document.querySelectorAll('#elastic .eg-item')].map(e => e.getAttribute('aria-posinset')).slice(0, 3)") == ["1", "2", "3"]
        assert page.get_attribute("#elastic .eg-item", "aria-setsize") == str(N_ALL)
        dom = page.evaluate("[...document.querySelectorAll('#elastic .eg-tile')].map(b => +b.dataset.index)")
        assert dom == sorted(dom)  # 読み上げ・Tab は新しい順（v17。v16 までは列ごと）
        assert all(abs(b["w"] / b["h"] - 128 / 160) < 0.01 for b in flat)  # 4:5 の枠
        assert all(b["src"].startswith("mids/") for b in flat)
        assert flat[0]["cap"] == f"{len(site['ids'])} - " + site["doc"]["photos"][0]["date"][:10].replace("-", ".")  # 「No.」なし
        first_row = [c[0] for c in columns]
        assert max(b["y"] for b in first_row) - min(b["y"] for b in first_row) < 1  # 止まっているときは行がそろう
        # 撮影年の範囲・「Change view」は一覧の上の左側
        r = page.evaluate("(() => { const m = document.getElementById('photo-range').getBoundingClientRect(), s = document.getElementById('pattern-switch').getBoundingClientRect(), g = document.querySelector('#elastic .eg-tile').getBoundingClientRect(); return [m.bottom, s.bottom, g.top, m.left, g.left]; })()")
        assert max(r[:2]) < r[2] and abs(r[3] - r[4]) < 1  # 左端は写真とそろう
        assert page.inner_text("#photo-count") == "" and page.locator("[role=tab], .bar-year").count() == 0
        assert page.inner_text("#pattern-switch") == "Change view"
        assert page.is_hidden("#cover")
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern2_outer_columns_lag_behind_then_line_up(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "2", viewport={"width": 1440, "height": 900})
    try:
        page, w = open_page(ctx, site["main"])
        page.mouse.move(720, 450)
        before = [c[1]["y"] for c in page.evaluate(EG_BOXES)]
        for _ in range(4):
            page.mouse.wheel(0, 150)
            page.wait_for_timeout(16)
        page.wait_for_timeout(150)
        mid = [c[1]["y"] for c in page.evaluate(EG_BOXES)]
        scrolled = page.evaluate("window.pageYOffset")
        assert scrolled > 300
        lagging = [m - (b - scrolled) for b, m in zip(before, mid)]  # 普通にスクロールした位置からの遅れ
        assert all(x > 1 for x in lagging), lagging  # どの列も遅れてついてくる
        assert lagging[3] < lagging[0] and lagging[3] < lagging[6]  # 中央の列がいちばん早く、外側ほど遅い
        assert abs(lagging[0] - lagging[6]) < 2 and lagging[1] < lagging[0]  # 左右対称
        eg_settled(page)
        after = [c[1]["y"] for c in page.evaluate(EG_BOXES)]
        assert max(after) - min(after) < 1  # 止まると行がそろう
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern2_open_photo_and_restore(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "2", **p.devices["iPhone 13"])
    try:
        page, w = open_page(ctx, site["main"])
        page.click(".eg-tile[data-index='4']")
        page.wait_for_function("document.getElementById('viewer').open")
        assert counter(page) == f"5 / {N_ALL}"
        assert page.get_attribute("#viewer-img", "data-id") == site["doc"]["photos"][4]["id"]
        page.click("#viewer-close")
        assert page.evaluate("document.activeElement.dataset.index") == "4"
        # スクロール位置は同じタブの再読込で戻る
        page.evaluate("window.scrollTo(0, 1500)")
        page.wait_for_timeout(400)
        saved = json.loads(page.evaluate("sessionStorage.getItem('pv:elastic')"))
        assert saved == {"v": 2, "y": 1500}
        page.reload()
        page.wait_for_selector("html[data-pv=ready]")
        page.wait_for_timeout(300)
        assert abs(page.evaluate("window.pageYOffset") - 1500) < 2
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern2_keyboard_and_resize(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "2", viewport={"width": 1440, "height": 900})
    try:
        page, w = open_page(ctx, site["main"])
        page.keyboard.press("Tab")  # スキップリンク
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "pattern-switch"
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.dataset.index") == "0"
        page.keyboard.press("Enter")
        page.wait_for_function("document.getElementById('viewer').open")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Escape")
        assert page.evaluate("document.activeElement.dataset.index") == "1"
        assert len(page.evaluate(EG_BOXES)) == 7
        page.keyboard.press("Tab")  # Tab は新しい順（横へ）
        assert page.evaluate("document.activeElement.dataset.index") == "2"
        page.set_viewport_size({"width": 800, "height": 900})  # 幅が狭くなると3列に並べ直す
        page.wait_for_function("document.querySelector('#elastic .eg-grid').dataset.cols === '3'")
        eg_settled(page)
        columns = page.evaluate(EG_BOXES)
        assert len(columns) == 3 and all(b["col"] == b["i"] % 3 for c in columns for b in c)
        assert page.evaluate("document.activeElement.dataset.index") == "2"  # 並べ直してもフォーカスはそのまま（v17）
        first_row = [c[0]["y"] for c in columns]
        assert max(first_row) - min(first_row) < 1
        w.assert_clean()
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [320, 375, 390, 910, 1000, 1440])
def test_pattern2_captions_are_never_cut_off(pw, site, width):
    """番号と日付が「…」で切れない。1行に収まらない幅では、全部の写真が番号と日付の2行になる（行はそろう）"""
    p, browser = pw
    kw = dict(**p.devices["iPhone 13"]) if width < 910 else {}
    kw["viewport"] = {"width": width, "height": 860}
    ctx = motion_context(browser, "2", **kw)
    try:
        page, w = open_page(ctx, site["main"])
        page.evaluate("document.fonts.ready")
        caps = page.evaluate("""[...document.querySelectorAll('#elastic .eg-cap')].map(c => ({
            over: [c, ...c.children].some(e => e.scrollWidth > e.clientWidth), h: c.getBoundingClientRect().height,
            text: c.textContent, lines: Math.round(c.getBoundingClientRect().height / parseFloat(getComputedStyle(c).lineHeight)) }))""")
        assert caps and not [c["text"] for c in caps if c["over"]]
        assert len({c["lines"] for c in caps}) == 1  # どの写真も同じ行数
        stacked = page.evaluate("document.querySelector('.eg-grid').classList.contains('is-stacked')")
        assert caps[0]["lines"] == (2 if stacked else 1)
        if width in (375, 390, 1440):
            assert not stacked  # 「No.」をやめて短くなったので、スマホでも1行に収まる
        if width == 910:
            assert stacked  # 7列のいちばん狭い幅では2行
        assert all(re.fullmatch(r"\d{2,} - \d{4}\.\d{2}\.\d{2}", c["text"]) for c in caps)
        columns = page.evaluate(EG_BOXES)
        first_row = [c[0]["y"] for c in columns]
        second_row = [c[1]["y"] for c in columns]
        assert max(first_row) - min(first_row) < 1 and max(second_row) - min(second_row) < 1
        w.assert_clean()
    finally:
        ctx.close()



# ===========================================================================
# 仕様 v14〜v16: パターン3「揺れる」（1日＝1本の紐に写真と日付の札を吊るし、スクロール・スライドの勢いとそよ風で揺らす）
# ===========================================================================
SW_STATE = """() => {
    const box = e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top + scrollY, w: r.width, h: r.height }; };
    const angle = e => { const t = getComputedStyle(e).transform; if (!t || t === 'none') return 0;
        const m = t.match(/matrix\\(([^,]+), ([^,]+)/); return m ? Math.atan2(+m[2], +m[1]) * 180 / Math.PI : 0; };
    return {
      lines: [...document.querySelectorAll('#sway-lines .sw-line')].map(l => ({ row: +l.dataset.row, deg: angle(l),
          left: parseFloat(l.style.left), top: parseFloat(l.style.top) })),
      bands: [...document.querySelectorAll('#sway-bands .sw-band')].map(b => ({ row: +b.dataset.row, max: +b.dataset.max, ...box(b) })),
      photos: [...document.querySelectorAll('#sway-list .sw-item')].map(li => { const b = li.querySelector('.sw-tile');
          return { row: +li.dataset.row, i: +b.dataset.index, id: b.dataset.id, ...box(b), deg: angle(li),
                   src: b.querySelector('img').getAttribute('src'), left: parseFloat(li.style.left), top: parseFloat(li.style.top) }; }),
      tags: [...document.querySelectorAll('#sway-tags .sw-tag')].map(t => ({ row: +t.dataset.row, kind: t.className.replace('sw-tag sw-tag-', ''),
          lines: [...t.querySelectorAll('.sw-card > span')].map(s => s.textContent), label: (t.querySelector('.sw-label') || {}).textContent || null,
          ...box(t.querySelector('.sw-card')), deg: angle(t) })),
      moving: document.getElementById('sway').hasAttribute('data-moving'),
      vw: document.documentElement.clientWidth, sw: document.documentElement.scrollWidth, sh: document.documentElement.scrollHeight,
      vh: innerHeight, sy: scrollY };
}"""


def sway_state(page):
    return page.evaluate(SW_STATE)


# v17: 部品は画面の近くの紐だけなので、並び全体はページを上から下まで送って集める。
# 位置は style の値（揺れ・スライドに左右されない。紐の傾きは rotate の値）
SW_LAYOUT = """() => ({
    lines: [...document.querySelectorAll('#sway-lines .sw-line')].map(l => ({ row: +l.dataset.row, left: parseFloat(l.style.left),
        top: parseFloat(l.style.top), deg: parseFloat(l.style.transform.replace('rotate(', '')) })),
    bands: [...document.querySelectorAll('#sway-bands .sw-band')].map(b => ({ row: +b.dataset.row, max: +b.dataset.max })),
    photos: [...document.querySelectorAll('#sway-list .sw-item')].map(li => { const b = li.querySelector('.sw-tile');
        return { row: +li.dataset.row, i: +b.dataset.index, id: b.dataset.id, left: parseFloat(li.style.left), top: parseFloat(li.style.top),
                 w: parseFloat(li.style.width), h: parseFloat(b.style.height), src: b.querySelector('img').getAttribute('src'),
                 size: li.getAttribute('aria-setsize'), pos: li.getAttribute('aria-posinset') }; }),
    tags: [...document.querySelectorAll('#sway-tags .sw-tag')].map(t => ({ row: +t.dataset.row, kind: t.className.replace('sw-tag sw-tag-', ''),
        lines: [...t.querySelectorAll('.sw-card > span')].map(s => s.textContent), label: (t.querySelector('.sw-label') || {}).textContent || null,
        left: parseFloat(t.style.left), w: parseFloat(t.style.width) })),
    dom: [...document.querySelectorAll('#sway-list .sw-tile')].map(b => +b.dataset.index) })"""


def next_frames(page):
    """2コマ進むのを待つ（コマが進まない状態でも2秒で戻る）。"""
    page.evaluate("new Promise(r => { requestAnimationFrame(() => requestAnimationFrame(r)); setTimeout(r, 2000); })")


def sway_layout(page):
    """ページを上から下まで画面1つ分ずつ送り、全部の紐・写真・札を集める。終わったら一番上へ戻す。"""
    vh = page.evaluate("innerHeight")
    acc = {"lines": {}, "bands": {}, "photos": {}, "tags": {}}
    y = 0
    while True:
        page.evaluate(f"window.scrollTo(0, {y})")
        next_frames(page)
        got = page.evaluate(SW_LAYOUT)
        assert got["dom"] == sorted(got["dom"])  # 写真の部品はいつも番号の順（読み上げ・Tab の順）
        for l in got["lines"]:
            acc["lines"][l["row"]] = l
        for b in got["bands"]:
            acc["bands"][b["row"]] = b
        for ph in got["photos"]:
            acc["photos"][ph["i"]] = ph
        for t in got["tags"]:
            acc["tags"][(t["row"], t["kind"])] = t
        if y + vh >= page.evaluate("document.documentElement.scrollHeight"):
            break
        y += vh
    page.evaluate("window.scrollTo(0, 0)")
    next_frames(page)
    return {k: [v[x] for x in sorted(v)] for k, v in acc.items()}


def sway_settled(page, timeout=15000):
    page.wait_for_selector("#sway:not([data-moving])", state="attached", timeout=timeout)


def overlap(a, b, pad=0):
    return min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) > pad and min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) > pad


def days_of(doc):
    """撮影日ごとの写真ID（新しい日から。日の中も新しい順）。"""
    days = {}
    for p in doc["photos"]:
        days.setdefault(p["date"][:10], []).append(p["id"])
    return list(days.items())


def line_y(l, x):
    import math
    return l["top"] + math.tan(math.radians(l["deg"])) * (x - l["left"])


def row_photos(st, row):
    return [ph for ph in st["photos"] if ph["row"] == row]


@pytest.mark.parametrize("device", ["phone", "desktop"])
def test_pattern3_hangs_one_day_per_string(pw, site, device):
    p, browser = pw
    kw = dict(**p.devices["iPhone 13"]) if device == "phone" else {"viewport": {"width": 1440, "height": 900}}
    ctx = motion_context(browser, "3", **kw)
    try:
        page, w = open_page(ctx, site["main"])
        assert page.evaluate("document.documentElement.dataset.mode") == "sway"
        assert page.is_hidden("#cover") and page.locator("#grid > li, #drift .drift-item, #elastic .eg-item").count() == 0
        sway_settled(page)
        st = sway_state(page)
        # v17: 部品は画面の近くの紐だけ（いちばん古い写真の紐はいつもある）
        assert len(st["photos"]) < N_ALL and max(ph["i"] for ph in st["photos"]) == N_ALL - 1
        lay = sway_layout(page)
        days = days_of(site["doc"])
        # 紐: いちばん上の短い紐（範囲と Change view）＋ 撮影日ごとに1本。傾きは2〜7度で、画面の中で交差しない
        assert [l["row"] for l in lay["lines"]] == list(range(len(days) + 1)) and len(lay["bands"]) == len(days) + 1
        assert all(2 - 0.01 <= abs(l["deg"]) <= 7 + 0.01 for l in lay["lines"])
        for a, b in zip(lay["lines"], lay["lines"][1:]):
            assert all(line_y(b, x) - line_y(a, x) > 30 for x in (0, st["vw"]))
        # 写真: 1本の紐にその日の写真だけを新しい順に。全体も新しい順（読み上げ・Tab の順）
        photos = lay["photos"]
        assert [ph["i"] for ph in photos] == list(range(N_ALL)) and [ph["id"] for ph in photos] == site["ids"]
        assert all(ph["size"] == str(N_ALL) and ph["pos"] == str(ph["i"] + 1) for ph in photos)  # 読み上げ: 何枚のうち何枚目
        for r, (day, ids) in enumerate(days, start=1):
            assert [ph["id"] for ph in row_photos(lay, r)] == ids, day
        doc = {x["id"]: x for x in site["doc"]["photos"]}
        assert all(abs(ph["w"] / ph["h"] - doc[ph["id"]]["width"] / doc[ph["id"]]["height"]) < 0.03 for ph in photos)
        assert all(ph["src"].startswith("mids/") for ph in photos) and all(ph["deg"] == 0 for ph in st["photos"])
        # クリップ（写真の上の真ん中）は自分の日の紐の上
        lines = {l["row"]: l for l in lay["lines"]}
        for ph in photos:
            assert abs(line_y(lines[ph["row"]], ph["left"] + ph["w"] / 2) - (ph["top"] + 7)) < 1.5, ph
        # 札: 紐の左端に日付（年・月日）だけ（写真の横の番号の札は v16 で廃止）。いちばん上の紐に範囲と Change view
        for r, (day, ids) in enumerate(days, start=1):
            tags = [t for t in lay["tags"] if t["row"] == r]
            assert len(tags) == 1 and tags[0]["kind"] == "date" and tags[0]["lines"] == [day[:4], day[5:].replace("-", ".")]
            assert tags[0]["left"] < min(ph["left"] for ph in row_photos(lay, r))  # 日付の札がいちばん左
        assert page.locator(".sw-tag-num").count() == 0
        top = [t for t in lay["tags"] if t["row"] == 0]
        years = site["doc"]["years"]
        assert [t["kind"] for t in top] == ["range", "switch"]
        assert top[0]["lines"] == [str(years[-1]), "–", str(years[0])] and top[1]["label"] == "Change view"
        # 画面に収まらない日（6枚の日）はスライドでき、続きの写真は画面の外。1枚の日はスライドしない
        bands = {b["row"]: b for b in lay["bands"]}
        for r, (day, ids) in enumerate(days, start=1):
            if len(ids) == 1:
                assert bands[r]["max"] == 0
        long_rows = [r for r, (day, ids) in enumerate(days, start=1) if len(ids) == 6]
        if device == "phone":
            assert long_rows and all(bands[r]["max"] > 0 for r in long_rows)
            for r in long_rows:
                assert max(ph["left"] for ph in row_photos(lay, r)) > st["vw"]
        sway_settled(page)
        st = sway_state(page)
        photos = st["photos"]
        # 止まっているときは、画面の中の写真どうし・写真と札が重ならない
        inside = lambda e: e["x"] >= 0 and e["x"] + e["w"] <= st["vw"]
        shown = [ph for ph in photos if inside(ph)]
        tags_in = [t for t in st["tags"] if inside(t)]
        for k, a in enumerate(shown):
            for b in shown[k + 1:]:
                assert not overlap(a, b, 1), (a, b)
            for t in tags_in:
                assert not overlap(a, t, 1), (a, t)
        # ページは縦にスクロールし、横にははみ出さない（画面の外の写真は切れている）
        assert st["sh"] > st["vh"] * 3 and st["sw"] <= st["vw"]
        for layer in ("#sway-tags", "#sway-lines", "#sway-bands"):
            assert page.get_attribute(layer, "aria-hidden") == "true"
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern3_slides_a_long_day_by_touch(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "3", **p.devices["iPhone 13"])
    try:
        page, w = open_page(ctx, site["main"])
        sway_settled(page)
        st = sway_state(page)
        band = next(b for b in st["bands"] if b["max"] > 0 and b["y"] > 0 and b["y"] + b["h"] < st["vh"])
        r = band["row"]
        line = next(l for l in st["lines"] if l["row"] == r)
        before = row_photos(st, r)
        others = [ph for ph in st["photos"] if ph["row"] != r]
        y = line_y(line, 330) + 24  # 紐のすぐ下（写真か帯の上）
        t = Touch(page)
        t.start(330, y)
        for k in range(1, 13):
            t.move(330 - k * 20, y + k * 0.4)
            page.wait_for_timeout(16)
        mid = sway_state(page)
        assert max(abs(ph["deg"]) for ph in row_photos(mid, r)) > 1  # スライドの勢いで揺れる
        t.end()
        sway_settled(page)
        st = sway_state(page)
        after = row_photos(st, r)
        moved = [b["x"] - a["x"] for a, b in zip(before, after)]
        assert all(m < -150 for m in moved), moved  # 指を離したあとも勢いで少し進む
        import math
        dy = [(b["y"] - a["y"]) for a, b in zip(before, after)]
        assert all(abs(d - math.tan(math.radians(line["deg"])) * m) < 2 for d, m in zip(dy, moved))  # 紐に沿って動く
        assert all(ph["deg"] == 0 for ph in after)
        assert [(round(ph["x"]), round(ph["y"])) for ph in st["photos"] if ph["row"] != r] == [(round(ph["x"]), round(ph["y"])) for ph in others]
        assert st["sy"] == 0  # ページは縦に動かない
        saved = json.loads(page.evaluate("sessionStorage.getItem('pv:sway')"))
        assert saved["v"] == 2 and list(saved["rows"].values())[0] > 150
        # いちばん右まで送ると、最後の写真が右端（余白の内側）にそろって止まる
        for _ in range(6):
            t.swipe(340, y, -300, 0, steps=8)
            page.wait_for_timeout(50)
        sway_settled(page)
        st = sway_state(page)
        last = row_photos(st, r)[-1]
        side = max(16, round(st["vw"] * 0.04))
        assert abs(last["x"] + last["w"] - (st["vw"] - side)) < 3
        # 左へ戻すと最初の位置。端の先へ引っぱっても戻る
        for _ in range(8):
            t.swipe(40, y, 300, 0, steps=8)
            page.wait_for_timeout(50)
        sway_settled(page)
        st = sway_state(page)
        assert [round(ph["x"]) for ph in row_photos(st, r)] == [round(ph["x"]) for ph in before]
        # スライドの終わりでは開かない。タップすると開く
        target = row_photos(st, r)[0]
        page.touchscreen.tap(target["x"] + target["w"] / 2, target["y"] - st["sy"] + target["h"] / 2)
        page.wait_for_function("document.getElementById('viewer').open")
        assert page.get_attribute("#viewer-img", "data-id") == target["id"]
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern3_slides_with_mouse_and_horizontal_wheel(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "3", viewport={"width": 900, "height": 900})
    try:
        page, w = open_page(ctx, site["main"])
        sway_settled(page)
        st = sway_state(page)
        band = next(b for b in st["bands"] if b["max"] > 0 and b["y"] > 0 and b["y"] + b["h"] < st["vh"])
        r = band["row"]
        x0 = [ph["x"] for ph in row_photos(st, r)]
        y = line_y(next(l for l in st["lines"] if l["row"] == r), 450) + 24  # 紐のすぐ下
        # マウスのドラッグ（写真の上から始めても、動かしたら開かない）
        ph0 = row_photos(st, r)[1]
        page.mouse.move(ph0["x"] + ph0["w"] / 2, ph0["y"] + ph0["h"] / 2)
        page.mouse.down()
        page.mouse.move(ph0["x"] + ph0["w"] / 2 - 120, ph0["y"] + ph0["h"] / 2, steps=10)
        page.mouse.up()
        page.wait_for_timeout(100)
        assert not viewer_open(page)
        sway_settled(page)
        x1 = [ph["x"] for ph in row_photos(sway_state(page), r)]
        assert all(b - a < -100 for a, b in zip(x0, x1))
        # 縦のホイールはページのスクロール（紐は動かない）
        page.mouse.move(450, y)
        page.mouse.wheel(0, 200)
        page.wait_for_timeout(300)
        assert page.evaluate("scrollY") > 0
        page.evaluate("window.scrollTo(0, 0)")
        sway_settled(page)
        assert [round(ph["x"]) for ph in row_photos(sway_state(page), r)] == [round(v) for v in x1]
        # 横のホイール（トラックパッド）と Shift＋ホイールで紐をスライド
        page.mouse.move(450, y)
        page.mouse.wheel(150, 0)
        page.wait_for_timeout(100)  # ホイールの処理が始まるまで
        sway_settled(page)
        x2 = [ph["x"] for ph in row_photos(sway_state(page), r)]
        assert all(abs((b - a) + 150) < 2 for a, b in zip(x1, x2))
        page.keyboard.down("Shift")
        page.mouse.wheel(0, -100)
        page.keyboard.up("Shift")
        page.wait_for_timeout(100)
        sway_settled(page)
        x3 = [ph["x"] for ph in row_photos(sway_state(page), r)]
        assert all(abs((b - a) - 100) < 2 for a, b in zip(x2, x3))
        assert page.evaluate("scrollY") == 0
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern3_swings_with_scroll_then_settles(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "3", viewport={"width": 1440, "height": 900})
    try:
        page, w = open_page(ctx, site["main"])
        sway_settled(page)
        page.mouse.move(720, 450)
        for _ in range(6):
            page.mouse.wheel(0, 180)
            page.wait_for_timeout(16)
        page.wait_for_timeout(120)
        st = sway_state(page)
        top = st["sy"]
        assert top > 400
        near = [ph for ph in st["photos"] if top - 200 < ph["y"] < top + st["vh"]]
        assert near and max(abs(ph["deg"]) for ph in near) > 1.5  # スクロールの勢いで傾く
        assert max(abs(ph["deg"]) for ph in st["photos"]) <= 20.1  # 傾きすぎない
        tags = [t for t in st["tags"] if top - 200 < t["y"] < top + st["vh"]]
        assert max(abs(t["deg"]) for t in tags) > 1.5  # 札も揺れる
        sway_settled(page, timeout=15000)  # 止まると収まる
        st = sway_state(page)
        assert all(ph["deg"] == 0 for ph in st["photos"]) and all(t["deg"] == 0 for t in st["tags"])
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern3_breeze_passes_from_left_to_right(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "3", **p.devices["iPhone 13"])
    try:
        page, w = open_page(ctx, site["main"])
        # 何もしなくても、しばらくするとそよ風が吹いて少し揺れる（スクロールはしない）
        page.wait_for_selector("#sway[data-moving]", state="attached", timeout=9000)
        seen = 0
        for _ in range(40):
            st = sway_state(page)
            angles = [abs(x["deg"]) for x in st["photos"] + st["tags"] if x["y"] < st["vh"]]
            seen = max(seen, max(angles))
            page.wait_for_timeout(50)
        assert 0.2 < seen < 6, seen  # ほんの少し（数度まで）
        assert page.evaluate("scrollY") == 0
        sway_settled(page, timeout=15000)
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern3_keyboard_reveals_photos_and_restores_after_reload(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "3", **p.devices["iPhone 13"])
    try:
        page, w = open_page(ctx, site["main"])
        sway_settled(page)
        days = days_of(site["doc"])
        assert len(days[0][1]) == 6  # 最初の日は6枚（画面に収まらない）
        # キーボード: スキップリンク → Change view（右下。移ったときだけ見える）→ 写真（新しい順）
        assert page.evaluate("getComputedStyle(document.getElementById('pattern-switch')).opacity") == "0"
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.id") == "pattern-switch"
        assert page.evaluate("getComputedStyle(document.getElementById('pattern-switch')).opacity") == "1"
        for k in range(4):
            page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.dataset.index") == "3"
        sway_settled(page)
        rect = page.evaluate("(() => { const r = document.activeElement.getBoundingClientRect(); return [r.left, r.right]; })()")
        assert rect[0] >= 0 and rect[1] <= page.evaluate("innerWidth")  # 画面の外の写真へ移ると、紐がスライドして見える
        page.keyboard.press("Enter")
        page.wait_for_function("document.getElementById('viewer').open")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        assert counter(page) == f"6 / {N_ALL}"
        page.keyboard.press("Escape")
        assert page.evaluate("document.activeElement.dataset.index") == "5"
        sway_settled(page)
        rect = page.evaluate("(() => { const r = document.activeElement.getBoundingClientRect(); return [r.left, r.right]; })()")
        assert rect[0] >= 0 and rect[1] <= page.evaluate("innerWidth")  # 送った先の写真が見えるところまでスライド
        # スクロール位置と、紐ごとのスライド位置は再読込で戻る（並びも同じ）
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_timeout(400)
        sway_settled(page, timeout=15000)  # スクロールで揺れた写真が止まってから
        before = {ph["id"]: (round(ph["x"]), round(ph["y"])) for ph in sway_state(page)["photos"]}
        saved = json.loads(page.evaluate("sessionStorage.getItem('pv:sway')"))
        assert saved["v"] == 2 and saved["y"] == 600 and saved["rows"]
        page.reload()
        page.wait_for_selector("html[data-pv=ready]")
        page.wait_for_timeout(300)
        assert abs(page.evaluate("scrollY") - 600) < 2
        sway_settled(page)
        after = {ph["id"]: (round(ph["x"]), round(ph["y"])) for ph in sway_state(page)["photos"]}
        common = [k for k in before if k in after]
        assert len(common) >= 10 and all(after[k] == before[k] for k in common)
        # 札の Change view を押すと、見せ方を選ぶ画面
        page.evaluate("window.scrollTo(0, 0)")
        sway_settled(page)
        page.click(".sw-label")
        page.wait_for_function("document.getElementById('chooser').open")
        page.click(".chooser-opt[data-pattern='2']")
        assert page.evaluate("document.documentElement.dataset.mode") == "elastic"
        assert page.locator("#sway .sw-item, #sway .sw-band").count() == 0  # 揺れるの一覧は片づける
        w.assert_clean()
    finally:
        ctx.close()


def test_pattern3_relayouts_on_width_change_and_handles_few_photos(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "3", viewport={"width": 1440, "height": 900})
    try:
        page, w = open_page(ctx, site["main"])
        wide = sway_state(page)
        page.set_viewport_size({"width": 700, "height": 900})
        page.wait_for_function(f"document.documentElement.clientWidth === 700 && [...document.querySelectorAll('.sw-band')].some(b => +b.dataset.max > {max(b['max'] for b in wide['bands'])})")
        st = sway_state(page)
        assert st["sw"] <= st["vw"]
        lay = sway_layout(page)
        assert len(lay["photos"]) == N_ALL and len(lay["lines"]) == len(days_of(site["doc"])) + 1  # 紐の数（日の数）は変わらない
        w.assert_clean()
        page, w = open_page(ctx, site["few"])
        st = sway_state(page)
        assert len(st["photos"]) == 2 and len(st["lines"]) == 3  # 2枚・2日 → 上の紐＋2本
        assert all(b["max"] == 0 for b in st["bands"])
        w.assert_clean()
        page, w = open_page(ctx, site["empty"], state="empty")
        assert page.locator("#sway .sw-item, #sway .sw-line").count() == 0
        assert "写真はまだありません" in page.inner_text("#status")
        w.assert_clean()
    finally:
        ctx.close()


# ===========================================================================
# 仕様 v17: 画面の近くだけ部品を作る（写真が増えても軽いまま）。3,000枚のデータで確かめる
# ===========================================================================
PARTS = "document.getElementsByTagName('*').length"
TILES = "#drift-list .drift-tile, #elastic .eg-tile, #sway-list .sw-tile"
ON_SCREEN = """sel => [...document.querySelectorAll(sel)].filter(b => { const r = b.getBoundingClientRect();
    return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; }).map(b => +b.dataset.index)"""


def settled(page, pattern, timeout=15000):
    sel = {"1": "#drift:not([data-moving])", "2": "#elastic:not([data-moving])", "3": "#sway:not([data-moving])"}[pattern]
    page.wait_for_selector(sel, state="attached", timeout=timeout)


def active_index(page):
    return page.evaluate("document.activeElement.dataset.index")


def active_on_screen(page):
    return page.evaluate("(() => { const r = document.activeElement.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; })()")


@pytest.mark.parametrize("pattern", ["1", "2", "3"])
def test_v17_large_gallery_opens_quickly_with_few_parts(pw, site, pattern):
    """3,000枚でも、開くまでの時間と部品の数は写真の数によらず小さい（処理の速さを4分の1に落とした iPhone 相当）。
    v16 では「揺れる」が開くまで約20秒、部品は約2万3千個だった。"""
    p, browser = pw
    ctx = motion_context(browser, pattern, **p.devices["iPhone 13"])
    cdp = None
    try:
        page = ctx.new_page()
        w = Watch(page)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        t0 = time.time()
        page.goto(site["big"])
        page.wait_for_selector("html[data-pv=ready]", timeout=60000)
        took = time.time() - t0
        assert took < 5, took
        parts = page.evaluate(PARTS)
        assert parts < 1500, parts
        items = page.evaluate("[...document.querySelectorAll('#drift-list > li, #elastic .eg-item, #sway-list > li')].map(li => [li.getAttribute('aria-setsize'), +li.getAttribute('aria-posinset')])")
        assert 3 <= len(items) < 200 and all(s == str(N_BIG) for s, _ in items), len(items)
        pos = [k for _, k in items]
        assert pos == sorted(pos) and pos[0] == 1 and pos[-1] == N_BIG  # 最初と最後の写真はいつもある（新しい順）
        assert len(page.evaluate(ON_SCREEN, TILES)) >= 2
        w.assert_clean()
    finally:
        if cdp:  # 遅くした処理の速さを戻す（ブラウザーが同じ処理を次の試験に使い回しても遅くならないように）
            with contextlib.suppress(Exception):
                cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
        ctx.close()


@pytest.mark.parametrize("pattern", ["2", "3"])
def test_v17_scrolling_far_shows_photos_and_cleans_up(pw, site, pattern):
    """遠くまで一気にスクロールしても画面に写真があり、離れた部品は片付く。いちばん下の写真も開ける。"""
    p, browser = pw
    ctx = motion_context(browser, pattern, **p.devices["iPhone 13"])
    try:
        page, w = open_page(ctx, site["big"])
        for frac in (0.5, 0.97, 0.2, 0.75):
            page.evaluate(f"window.scrollTo(0, Math.round(document.documentElement.scrollHeight * {frac}))")
            next_frames(page)
            assert len(page.evaluate(ON_SCREEN, TILES)) >= 2, frac
            assert page.evaluate(PARTS) < 1500
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        next_frames(page)
        settled(page, pattern)
        sel = f".eg-tile[data-index='{N_BIG - 1}'], .sw-tile[data-index='{N_BIG - 1}']"
        if pattern == "3":  # いちばん古い写真が紐の外なら、紐をスライドして見せる
            page.focus(sel)
            settled(page, pattern)
        box = page.locator(sel).bounding_box()
        page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 40))
        page.wait_for_function("document.getElementById('viewer').open")
        assert counter(page) == f"{N_BIG} / {N_BIG}"
        page.click("#viewer-close")
        assert active_index(page) == str(N_BIG - 1)
        w.assert_clean()
    finally:
        ctx.close()


@pytest.mark.parametrize("pattern", ["1", "2", "3"])
def test_v17_tab_goes_through_photos_in_order(pw, site, pattern):
    """キーボードの Tab で、写真を新しい順に1枚ずつたどれる（部品が画面の近くにしかなくても途切れない）。"""
    p, browser = pw
    ctx = motion_context(browser, pattern, viewport={"width": 1280, "height": 860})
    try:
        page, w = open_page(ctx, site["big"])
        page.focus("#pattern-switch")
        seq = []
        for _ in range(30):
            page.keyboard.press("Tab")
            seq.append(active_index(page))
        assert seq == [str(i) for i in range(30)]
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Shift+Tab")
        assert active_index(page) == "27"
        settled(page, pattern)
        assert active_on_screen(page)  # フォーカスした写真が見える所まで動く
        assert page.evaluate(PARTS) < 1500
        w.assert_clean()
    finally:
        ctx.close()


@pytest.mark.parametrize("pattern", ["1", "2", "3"])
def test_v17_closing_the_viewer_on_a_far_photo_returns_there(pw, site, pattern):
    """全画面表示でいちばん古い写真まで送って閉じると、その写真が見える所へ移ってフォーカスが戻る。"""
    p, browser = pw
    ctx = motion_context(browser, pattern, viewport={"width": 1280, "height": 860})
    try:
        page, w = open_page(ctx, site["big"])
        page.focus("#pattern-switch")
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        page.wait_for_function("document.getElementById('viewer').open")
        page.keyboard.press("End")
        assert counter(page) == f"{N_BIG} / {N_BIG}"
        page.keyboard.press("Escape")
        assert active_index(page) == str(N_BIG - 1)
        settled(page, pattern)
        assert active_on_screen(page)
        page.keyboard.press("Shift+Tab")  # 前の写真へも続けて移れる
        assert active_index(page) == str(N_BIG - 2)
        assert page.evaluate(PARTS) < 1500
        w.assert_clean()
    finally:
        ctx.close()


def test_v17_photos_come_back_developed_and_position_is_restored(pw, site):
    """離れて片付けた写真は、戻ってきたとき現像し直さない。再読込するとスクロール位置と写真が戻る（揺れる）。"""
    p, browser = pw
    ctx = motion_context(browser, "3", **p.devices["iPhone 13"])
    try:
        page, w = open_page(ctx, site["big"])
        k = len(days_of(site["big_doc"])[0][1])  # 2日目の最初の写真（いちばん新しい写真の紐はいつもあるので、その次の紐）
        b = f"#sway-list .sw-tile[data-index='{k}']"
        page.wait_for_selector(b + ".is-developed", timeout=10000)
        page.evaluate("window.scrollTo(0, innerHeight * 6)")
        next_frames(page)
        assert page.locator(b).count() == 0  # 離れた紐は片付いた
        page.evaluate("window.scrollTo(0, 0)")
        next_frames(page)
        assert page.eval_on_selector(b, "e => e.classList.contains('is-developed')")  # 作り直したときは現像した状態
        assert page.eval_on_selector(b + " img", "e => getComputedStyle(e).opacity") == "1"
        y = page.evaluate("Math.round(document.documentElement.scrollHeight * 0.4)")
        page.evaluate(f"window.scrollTo(0, {y})")
        page.wait_for_timeout(400)
        settled(page, "3")
        before = sorted(page.evaluate(ON_SCREEN, TILES))
        page.reload()
        page.wait_for_selector("html[data-pv=ready]")
        page.wait_for_timeout(300)
        assert abs(page.evaluate("scrollY") - y) < 2
        settled(page, "3")
        assert sorted(page.evaluate(ON_SCREEN, TILES)) == before and before
        w.assert_clean()
    finally:
        ctx.close()



# ===========================================================================
# 仕様 v18: アクセス解析（GoatCounter に直接知らせる。外部のスクリプトは読まない。#nocount で自分の端末を数えない）
# ===========================================================================
class Counts:
    """GoatCounter の受付先への知らせを横取りして記録する（実際には送らない）。"""

    def __init__(self, ctx):
        from urllib.parse import parse_qsl, urlsplit

        self.hits = []

        def handle(route):
            self.hits.append(dict(parse_qsl(urlsplit(route.request.url).query)))
            route.fulfill(status=200, content_type="image/gif", body=b"GIF89a")

        ctx.route("https://pvtest.goatcounter.com/**", handle)

    def paths(self):
        return [h["p"] for h in self.hits]

    def events(self):
        return [h["p"] for h in self.hits if h.get("e") == "true"]


def test_v18_counts_page_view_chosen_view_opened_photo_and_seen(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "2", **p.devices["iPhone 13"])
    try:
        counts = Counts(ctx)
        page = ctx.new_page()
        w = Watch(page)
        page.goto(site["counted"] + "?from=test")
        page.wait_for_selector("html[data-pv=ready]")
        page.wait_for_timeout(300)
        views = [h for h in counts.hits if h.get("e") != "true"]
        assert len(views) == 1 and views[0]["p"].endswith("/counted/photo-viewer/") and views[0]["q"] == "?from=test"
        assert views[0]["t"] == site["counted_doc"]["title"] and views[0]["s"] == "390"
        assert counts.events()[0] == "view/2" and counts.hits[1]["t"] == "並べる"
        # 送るのは決まった項目だけ（クッキーや写真の場所などは送らない）
        assert all(set(h) <= {"p", "t", "r", "q", "e", "s", "b", "rnd"} for h in counts.hits)
        # スクロールして写真を見ると、見た枚数（10枚・30枚…）
        for _ in range(12):
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(120)
        page.wait_for_timeout(500)
        seen = [e for e in counts.events() if e.startswith("seen/")]
        assert seen[:2] == ["seen/10", "seen/30"] and len(seen) == len(set(seen))
        # 全画面で開いた写真（前後に送った写真は数えない）
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        page.click(".eg-tile[data-index='3']")
        page.wait_for_function("document.getElementById('viewer').open")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Escape")
        photos = [h for h in counts.hits if h["p"].startswith("photo/")]
        assert [h["p"] for h in photos] == ["photo/" + site["ids"][3]] and photos[0]["t"] == site["doc"]["photos"][3]["date"][:10].replace("-", ".")
        # 見せ方を選び直すと知らせる（同じ見せ方は1回だけ）
        page.click("#pattern-switch")
        page.click(".chooser-opt[data-pattern='3']")
        page.evaluate("document.getElementById('pattern-switch').click()")  # 揺れるでは、このボタンはキーボードで移ったときだけ見える
        page.click(".chooser-opt[data-pattern='2']")
        assert [e for e in counts.events() if e.startswith("view/")] == ["view/2", "view/3"]
        # 送り先は設定した受付先だけ（外部のスクリプトは読まない）
        assert all(u.startswith(site["counted"]) or u.startswith(COUNT_URL) for u in w.requests), [u for u in w.requests if "goatcounter" not in u and not u.startswith(site["counted"])]
        w.assert_clean()
    finally:
        ctx.close()


def test_v18_still_and_drift_views_are_counted(pw, site):
    p, browser = pw
    ctx = browser.new_context(**p.devices["iPhone 13"], **STILL)
    try:
        counts = Counts(ctx)
        page, w = open_page(ctx, site["counted"])
        page.wait_for_timeout(300)
        for _ in range(8):
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(100)
        page.wait_for_timeout(400)
        ev = counts.events()
        assert ev[0] == "view/still" and "seen/10" in ev
        w.assert_clean()
    finally:
        ctx.close()
    ctx = motion_context(browser, "1", **p.devices["iPhone 13"])
    try:
        counts = Counts(ctx)
        page, w = open_drift(ctx, site["counted"])
        for _ in range(6):
            page.keyboard.press("PageDown")
            page.wait_for_timeout(250)
        drift_settled(page)
        ev = counts.events()
        assert ev[0] == "view/1" and "seen/10" in ev
        w.assert_clean()
    finally:
        ctx.close()


def test_v18_nocount_turns_counting_off_for_this_browser(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "2", **p.devices["iPhone 13"])
    try:
        counts = Counts(ctx)
        page = ctx.new_page()
        w = Watch(page)
        messages = []
        page.on("dialog", lambda d: (messages.append(d.message), d.accept()))
        page.goto(site["counted"] + "#nocount")
        page.wait_for_selector("html[data-pv=ready]")
        page.wait_for_timeout(300)
        assert messages and "数えない" in messages[0]
        assert page.evaluate("location.hash") == ""  # 印はアドレスから消す
        assert page.evaluate("localStorage.getItem('pv:nocount')") == "1"
        page.click(".eg-tile[data-index='1']")
        page.keyboard.press("Escape")
        page.reload()
        page.wait_for_selector("html[data-pv=ready]")
        page.wait_for_timeout(300)
        assert counts.hits == []  # この端末の分は数えない（再読込しても）
        # 開いているページのアドレスに #count を付け足しても（読み込み直されない）元に戻る
        page.evaluate("location.hash = '#count'")
        page.wait_for_function("location.hash === ''")
        assert "戻しました" in messages[-1] and page.evaluate("localStorage.getItem('pv:nocount')") is None
        page.reload()
        page.wait_for_selector("html[data-pv=ready]")
        page.wait_for_timeout(300)
        assert [h["p"] for h in counts.hits][1] == "view/2" and len(counts.hits) >= 2  # また数える
        w.assert_clean()
    finally:
        ctx.close()


def test_v18_sites_without_analytics_send_nothing(pw, site):
    p, browser = pw
    ctx = motion_context(browser, "3", **p.devices["iPhone 13"])
    try:
        page, w = open_page(ctx, site["main"])
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(500)
        assert not [u for u in w.requests if "goatcounter" in u]
        assert "analytics" not in site["doc"]
        w.assert_clean()
    finally:
        ctx.close()
