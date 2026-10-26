#!/usr/bin/env python3
"""ダッシュボードを作る（同期仕様ID PV-SPEC-2026-10-v18 / 版 18.0）

PC の中だけで見るページ dashboard/index.html を作る（公開しない。.gitignore の対象）。
  1. 見に来た人 … GoatCounter から読む（日ごとの訪問者・時間帯・リンク元・端末・画面の大きさ・地域）
  2. サイトの中での見られ方 … GoatCounter のイベント（見せ方・全画面で開いた写真・見た写真の枚数）
  3. サイトの状態 … PC の中の記録から（写真の枚数・撮影月・公開の履歴と結果・容量・日付があやしい写真）
1・2 は直近7日・30日・90日を切り替えて見られる。GoatCounter に届かないときは、前回読んだ値を出す。

設定（config.json）:
  goatcounter      … GoatCounter のコード（https://<コード>.goatcounter.com の <コード>）
  goatcounterToken … GoatCounter の API キー（権限は Read statistics だけ）。公開データには書かない

使い方:
  python tools/make_dashboard.py            … 作る（公開する.bat の最後と tools\\dashboard.bat からも呼ばれる）
  python tools/make_dashboard.py --open     … 作ってブラウザーで開く
  python tools/make_dashboard.py --no-fetch … GoatCounter に問い合わせず、前回読んだ値で作る
終了コード: 0=作った（GoatCounter に届かなくても作る）/ 1=作れなかった
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv_common as C  # noqa: E402

PERIODS = (7, 30, 90)
MB = 1024 * 1024
GB = 1024 * MB
SITE_LIMIT = 1 * GB  # GitHub Pages: 公開サイトは最大 1GB。リポジトリも 1GB 未満が推奨
SEEN_STEPS = (10, 30, 60, 100, 200, 500, 1000)
VIEWS = (("view/1", "散らばる"), ("view/2", "並べる"), ("view/3", "揺れる"), ("view/still", "動かない一覧"))
SIZE_NAMES = {
    "phone": "スマホ（幅600以下）",
    "tablet": "タブレット・大きいスマホ",
    "desktop": "パソコン",
    "desktophd": "大きな画面",
    "unknown": "不明",
}
API_PAUSE = 0.3  # GoatCounter の API は 1秒に4回まで


class GCError(Exception):
    """GoatCounter から読めなかった（理由を画面に出す）。

    fatal: キー・コード・接続の問題など、続けて問い合わせても無駄なもの。
    そうでないもの（ある一覧だけ 404 / 500 など）は、その一覧だけ「読めませんでした」にして残りを出す。
    """

    def __init__(self, message: str, fatal: bool = True):
        super().__init__(message)
        self.fatal = fatal


DETAIL_PAGES = (("toprefs", "リンク元"), ("systems", "OS"), ("browsers", "ブラウザー"), ("sizes", "画面の大きさ"), ("locations", "地域"))


def _server_said(body: str) -> str:
    """GoatCounter のエラー応答（{"error": "..."}）から、理由を短く取り出す（画面に出すときはエスケープする）。"""
    try:
        obj = json.loads(body)
    except ValueError:
        return ""
    said = obj.get("error") if isinstance(obj, dict) else None
    if not said and isinstance(obj, dict) and isinstance(obj.get("errors"), dict):
        said = "; ".join(f"{k}: {v}" for k, v in obj["errors"].items())
    said = " ".join(str(said or "").split())
    return said[:120]


# ---------------------------------------------------------------------------
# GoatCounter
# ---------------------------------------------------------------------------
def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")


def period_range(days: int, now: datetime, back: int = 0) -> tuple:
    """直近 days 日（今日を含む。日本時間の0時から）。back=1 でその前の同じ長さ。"""
    today0 = now.astimezone(C.JST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today0 - timedelta(days=days * (back + 1) - 1)
    end = (today0 + timedelta(days=1)) - timedelta(days=days * back)
    if back == 0:
        end = min(end, (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
    return start, end


class GoatCounterAPI:
    def __init__(self, base: str, token: str, timeout: float = 15.0):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.calls = 0

    def get(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{self.base}{path}?{query}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "photo-viewer-dashboard",
        })
        for attempt in range(3):
            if self.calls:
                time.sleep(API_PAUSE)
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:  # 回数の上限: 少し待ってもう一度
                    wait = exc.headers.get("X-Rate-Limit-Reset") if exc.headers else None
                    time.sleep(min(10.0, float(wait)) if wait and wait.replace(".", "", 1).isdigit() else 2.0)
                    continue
                try:
                    body = exc.read(4000).decode("utf-8", "replace")
                except Exception:
                    body = ""
                said = _server_said(body)
                if exc.code == 401:
                    raise GCError("API キーが正しくありません（config.json の goatcounterToken を確認してください）")
                if exc.code == 403:
                    raise GCError("API キーに「Read statistics（統計の読み取り）」の権限がありません"
                                  + (f"（{said}）" if said and "permission" not in said else ""))
                if exc.code == 400 and "no site at this domain" in body:
                    raise GCError("GoatCounter のコードが見つかりません（config.json の goatcounter を確認してください）")
                if exc.code == 404:
                    # 宛先は合っていて、GoatCounter の中で何かが見つからなかった（コードの間違いは 400 で返る）
                    raise GCError(f"GoatCounter が「見つかりません」と答えました（{path}）", fatal=False)
                raise GCError(f"GoatCounter からエラーが返りました（HTTP {exc.code}、{path}"
                              + (f"：{said}" if said else "") + "）", fatal=False)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise GCError(f"GoatCounter に接続できませんでした（{getattr(exc, 'reason', exc)}）")
            except ValueError:
                raise GCError("GoatCounter の応答を読めませんでした")
        raise GCError("GoatCounter の回数の上限に達しました。少し時間をおいてから作り直してください")


def _stats_list(data: dict) -> list:
    out = []
    for s in (data or {}).get("stats") or []:
        name = s.get("name") or s.get("id") or ""
        out.append([str(name), int(s.get("count") or 0)])
    return out


def fetch_period(api: GoatCounterAPI, days: int, now: datetime) -> dict:
    start, end = period_range(days, now)
    pstart, pend = period_range(days, now, back=1)
    rng = {"start": _iso_utc(start), "end": _iso_utc(end)}
    hits = api.get("/stats/hits", dict(rng, limit=100)).get("hits") or []
    prev = api.get("/stats/hits", {"start": _iso_utc(pstart), "end": _iso_utc(pend), "limit": 100}).get("hits") or []

    pages = [h for h in hits if not h.get("event")]
    events = {str(h.get("path")): h for h in hits if h.get("event")}
    # 日ごと・時間帯（ページを開いた人。イベントは含めない）
    by_day: dict = {}
    hours = [0] * 24
    for h in pages:
        for st in h.get("stats") or []:
            day = str(st.get("day") or "")[:10]
            by_day[day] = by_day.get(day, 0) + int(st.get("daily") or 0)
            for i, v in enumerate((st.get("hourly") or [])[:24]):
                hours[i] += int(v or 0)
    days_list = []
    d = start.astimezone(C.JST).date()
    last = (end - timedelta(seconds=1)).astimezone(C.JST).date()
    while d <= last:
        key = d.isoformat()
        days_list.append([key, by_day.get(key, 0)])
        d += timedelta(days=1)

    # リンク元・OS などは、ページを開いた分だけ（イベントの分は数えない）。ページの番号（path_id）で絞る。
    # 番号がない応答のときだけ、名前で絞る（path_by_name）
    ids = [h.get("path_id") for h in pages]
    if ids and all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        only_pages = {"include_paths": ",".join(str(i) for i in ids)}
    else:
        only_pages = {"path_by_name": "true", "include_paths": ",".join(str(h.get("path")) for h in pages if h.get("path"))}
    detail, failed = {}, []
    for page, label in DETAIL_PAGES:
        if not pages:
            detail[page] = []
            continue
        try:
            detail[page] = _stats_list(api.get(f"/stats/{page}", dict(rng, limit=20, **only_pages)))
        except GCError as exc:
            if exc.fatal:
                raise
            detail[page] = None  # この一覧だけ「読めませんでした」
            failed.append(f"{label}: {exc}")

    photos = sorted(
        ([p[len("photo/"):], int(h.get("count") or 0)] for p, h in events.items() if p.startswith("photo/")),
        key=lambda x: (-x[1], x[0]),
    )
    return {
        "days": days_list,
        "hours": hours,
        "visitors": sum(int(h.get("count") or 0) for h in pages),
        "prev": sum(int(h.get("count") or 0) for h in prev if not h.get("event")),
        "refs": detail["toprefs"],
        "systems": detail["systems"],
        "browsers": detail["browsers"],
        "sizes": detail["sizes"],
        "locations": detail["locations"],
        "views": {k: int((events.get(k) or {}).get("count") or 0) for k, _ in VIEWS},
        "seen": {f"seen/{n}": int((events.get(f"seen/{n}") or {}).get("count") or 0) for n in SEEN_STEPS}
        | {"seen/all": int((events.get("seen/all") or {}).get("count") or 0)},
        "photos": photos[:24],
        "failed": failed,
    }


def fetch_access(code: str, token: str, api_base: str | None, now: datetime) -> dict:
    base = api_base or f"https://{code}.goatcounter.com/api/v0"
    api = GoatCounterAPI(base, token)
    periods = {str(n): fetch_period(api, n, now) for n in PERIODS}
    failed = []
    for n in PERIODS:
        for f in periods[str(n)].get("failed") or []:
            if f not in failed:
                failed.append(f)
    return {"fetchedAt": C.to_iso_jst(now), "code": code, "periods": periods, "failed": failed}


# ---------------------------------------------------------------------------
# PC の中の記録
# ---------------------------------------------------------------------------
LOG_NAME_RE = re.compile(r"^publish_(\d{8})_(\d{6})_\d+\.log$")
LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(\w+)\] (.*)$")


def parse_publish_log(path: Path) -> dict | None:
    m = LOG_NAME_RE.match(path.name)
    if not m:
        return None
    when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=C.JST)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rec = {"time": C.to_iso_jst(when), "result": "running", "count": None, "new": None, "tests": None, "message": ""}
    errors = []
    for raw in text.splitlines():
        line = raw.strip()
        if (g := re.search(r"生成完了: (\d+)件（新規変換 (\d+)", line)):
            rec["count"], rec["new"] = int(g.group(1)), int(g.group(2))
        if (g := re.search(r"(\d+) passed", line)) and "試験" not in line:
            rec["tests"] = int(g.group(1))
        lm = LINE_RE.match(line)
        if not lm:
            continue
        level, msg = lm.groups()
        if level == "OK" and msg.startswith("8/8 push しました"):
            rec["result"] = "published"
        elif level == "OK" and msg.startswith("8/8 公開内容に変更はありません"):
            rec["result"] = "unchanged"
        elif level == "OK" and msg.startswith("8/8") and ("DryRun" in msg or "NoPush" in msg):
            rec["result"] = "dryrun"
        elif level == "ERROR":
            errors.append(msg)
        elif level == "OK" and msg.startswith("commit しました:"):
            rec["commit"] = msg.split(":", 1)[1].strip()
    if errors and rec["result"] not in ("published", "unchanged"):
        rec["result"] = "stopped"
        rec["message"] = errors[0]
    return rec


def read_publishes(root: Path, limit: int = 60) -> list:
    logs = sorted((root / "logs").glob("publish_*.log"))
    out = [r for r in (parse_publish_log(p) for p in logs[-limit:]) if r]
    return out


def folder_bytes(path: Path) -> int:
    total = 0
    if path.is_file():
        return path.stat().st_size
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
    return total


def git_history_bytes(root: Path) -> int | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "count-objects", "-v"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    kb = 0
    for line in r.stdout.splitlines():
        k, _, v = line.partition(":")
        if k.strip() in ("size", "size-pack"):
            try:
                kb += int(v.strip())
            except ValueError:
                pass
    return kb * 1024


def local_status(root: Path) -> dict:
    photos, err = [], None
    try:
        doc = json.loads((root / "data" / "photos.json").read_text(encoding="utf-8"))
        photos = [p for p in doc.get("photos", []) if isinstance(p, dict)]
    except (OSError, ValueError, AttributeError) as exc:
        err = f"data/photos.json を読めません（{exc}）"
    n = len(photos)
    info = {}
    months: dict = {}
    sources: dict = {}
    doubtful = []
    for k, p in enumerate(photos):
        date = str(p.get("date", ""))
        info[str(p.get("id"))] = {"thumb": p.get("thumb", ""), "date": date[:10].replace("-", "."), "no": n - k}
        months[date[:7]] = months.get(date[:7], 0) + 1
        src = str(p.get("dateSource", ""))
        sources[src] = sources.get(src, 0) + 1
        if src in ("mtime", "legacy"):  # 撮影情報もファイル名の日時もなく、ファイルの更新日時などで決めた
            doubtful.append({"id": p.get("id"), "date": date[:16].replace("T", " "), "source": src, "thumb": p.get("thumb", "")})
    sizes = {name: folder_bytes(root / name) for name in ("photos", "mids", "thumbs")}
    sizes["other"] = sum(folder_bytes(root / name) for name in ("index.html", "css", "js", "fonts", "data"))
    return {
        "count": n,
        "error": err,
        "months": sorted(months.items()),
        "sources": sources,
        "doubtful": doubtful,
        "photoInfo": info,
        "publishes": read_publishes(root),
        "sizes": sizes,
        "site": sum(sizes.values()),
        "history": git_history_bytes(root),
    }


# ---------------------------------------------------------------------------
# ページ
# ---------------------------------------------------------------------------
def esc(v) -> str:
    return html.escape(str(v), quote=True)


def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_mb(b: int | None) -> str:
    if b is None:
        return "—"
    if b >= GB:
        return f"{b / GB:.2f} GB"
    return f"{b / MB:.1f} MB"


def fmt_time(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso).astimezone(C.JST)
    except ValueError:
        return iso
    return f"{d.year}/{d.month}/{d.day} {d.hour:02d}:{d.minute:02d}"


def nice_max(v: int) -> int:
    """目盛りの上端（切りのよい偶数。半分の目盛りも整数になる）。"""
    for e in range(0, 9):
        for k in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8):
            m = k * 10 ** e
            if m == int(m) and int(m) % 2 == 0 and m >= max(v, 2):
                return int(m)
    return v


def chart(kind: str, values: list, tips: list, axis_labels: list, title: str) -> str:
    """グラフ（1系列）。形はページの中の JavaScript が、画面の幅に合わせて描く（文字の大きさを保つため）。
    kind: "col"（縦の棒）/ "line"（折れ線）。値はツールチップと「表で見る」でも読める。"""
    data = {"kind": kind, "values": values, "tips": tips, "axis": axis_labels, "top": nice_max(max(values) if values else 0)}
    return (f'<div class="chart" role="img" aria-label="{esc(title)}" data-chart="{esc(json.dumps(data, ensure_ascii=False))}">'
            "</div>")


def hbars(rows: list, unit: str, empty: str = "まだありません", total: int | None = None) -> str:
    """横の棒（名前・棒・数）。数は棒の先に。"""
    rows = [r for r in rows if r[1] > 0]
    if not rows:
        return f'<p class="empty">{esc(empty)}</p>'
    top = max(r[1] for r in rows)
    out = ['<ul class="hbars">']
    for name, v in rows:
        share = f"（{v / total * 100:.0f}%）" if total else ""
        tip = f"{name}: {fmt_int(v)}{unit}{share}"
        out.append(
            f'<li tabindex="0" data-tip="{esc(tip)}"><span class="hb-name">{esc(name)}</span>'
            f'<span class="hb-track"><span class="hb-bar" style="width:{max(1.5, v / top * 100):.1f}%"></span></span>'
            f'<span class="hb-val">{fmt_int(v)}<small>{esc(unit)}{esc(share)}</small></span></li>'
        )
    out.append("</ul>")
    return "".join(out)


def table(headers: list, rows: list) -> str:
    out = ['<details class="tv"><summary>表で見る</summary><div class="tv-wrap"><table><thead><tr>']
    out += [f"<th>{esc(h)}</th>" for h in headers]
    out.append("</tr></thead><tbody>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table></div></details>")
    return "".join(out)


def tile(label: str, value: str, sub: str = "", cls: str = "") -> str:
    return (f'<div class="tile {cls}"><p class="tile-label">{esc(label)}</p><p class="tile-value">{value}</p>'
            + (f'<p class="tile-sub">{sub}</p>' if sub else "") + "</div>")


def delta(cur: int, prev: int, days: int) -> str:
    if prev <= 0:
        return f"前の{days}日は 0 人" if cur else ""
    d = (cur - prev) / prev * 100
    sign = "+" if d >= 0 else "−"
    cls = "up" if d >= 0 else "down"
    return f'<span class="delta {cls}">{sign}{abs(d):.0f}%</span> 前の{days}日（{fmt_int(prev)}人）より'


def top_rows(rows: list, k: int = 8) -> list:
    rows = [list(r) for r in rows if r[1] > 0]
    if len(rows) <= k:
        return rows
    return rows[:k - 1] + [["その他", sum(r[1] for r in rows[k - 1:])]]


def render_period(days: int, p: dict, local: dict) -> str:
    """1・2 の中身（期間ごと）。"""
    dl = p["days"]
    vals = [v for _, v in dl]

    def md(s):
        return f"{int(s[5:7])}/{int(s[8:10])}"

    tips = [f"{md(d)}　{fmt_int(v)}人" for d, v in dl]
    step = max(1, len(dl) // 4)
    axis = [(i, md(dl[i][0])) for i in range(0, len(dl), step)]
    if dl and axis[-1][0] != len(dl) - 1:
        if len(dl) - 1 - axis[-1][0] < step / 2:
            axis.pop()
        axis.append((len(dl) - 1, md(dl[-1][0])))
    daily = chart("col", vals, tips, axis, f"日ごとの訪問者（直近{days}日）")
    hours = p["hours"]
    htips = [f"{h}時台　{fmt_int(v)}人" for h, v in enumerate(hours)]
    hourly = chart("col", hours, htips, [(0, "0時"), (6, "6時"), (12, "12時"), (18, "18時"), (23, "23時")], "時間帯ごとの訪問者")
    today = vals[-1] if vals else 0
    best = max(dl, key=lambda x: x[1]) if dl else None
    out = [f'<div class="period p-{days}">']
    out.append('<section class="block"><h2>見に来た人</h2><div class="tiles">')
    out.append(tile(f"訪問者（直近{days}日）", f'<span class="hero">{fmt_int(p["visitors"])}</span><small>人</small>',
                    delta(p["visitors"], p["prev"], days), "wide"))
    out.append(tile("今日", f"{fmt_int(today)}<small>人</small>"))
    if best and best[1]:
        out.append(tile("いちばん多かった日", f"{fmt_int(best[1])}<small>人</small>", esc(md(best[0]))))
    out.append("</div>")
    out.append(f'<figure class="card"><figcaption>日ごとの訪問者</figcaption>{daily}'
               + table(["日付", "訪問者"], [[d, v] for d, v in dl]) + "</figure>")
    out.append(f'<figure class="card"><figcaption>見られている時間帯（日本時間）</figcaption>{hourly}'
               + table(["時間", "訪問者"], [[f"{h}時台", v] for h, v in enumerate(hours)]) + "</figure>")
    out.append('<div class="cards">')

    def card(title: str, head: str, rows, k: int = 8, total: bool = False) -> str:
        if rows is None:  # GoatCounter から、この一覧だけ読めなかった
            return f'<figure class="card"><figcaption>{esc(title)}</figcaption><p class="empty">読めませんでした（上のお知らせ）</p></figure>'
        bars = hbars(rows if total else top_rows(rows, k), "人", total=sum(v for _, v in rows) if total else None)
        return f'<figure class="card"><figcaption>{esc(title)}</figcaption>{bars}' + table([head, "訪問者"], rows) + "</figure>"

    refs = p.get("refs")
    if refs is not None:
        refs = [[("（直接・ブックマーク・LINE など）" if not n or n == "(no data)" else n), v] for n, v in refs]
    sizes = p.get("sizes")
    if sizes is not None:
        sizes = [[SIZE_NAMES.get(n, n), v] for n, v in sizes]
    out.append(card("どこから来たか（リンク元）", "リンク元", refs, k=10))
    out.append(card("画面の大きさ", "画面", sizes, total=True))
    out.append(card("OS", "OS", p.get("systems")))
    out.append(card("ブラウザー", "ブラウザー", p.get("browsers")))
    out.append(card("地域", "地域", p.get("locations")))
    out.append("</div></section>")

    # 2. サイトの中での見られ方
    out.append('<section class="block"><h2>サイトの中での見られ方</h2><div class="cards">')
    views = [[name, p["views"].get(k, 0)] for k, name in VIEWS]
    vt = sum(v for _, v in views)
    out.append(f'<figure class="card"><figcaption>選ばれた見せ方</figcaption>'
               f'{hbars(views, "人", "まだ選ばれていません", total=vt or None)}'
               f'<p class="note">同じ人が見せ方を変えると、それぞれに数えます。</p>' + table(["見せ方", "人数"], views) + "</figure>")
    seen = [[f"{n}枚以上", p["seen"].get(f"seen/{n}", 0)] for n in SEEN_STEPS if n < max(10, local["count"])]
    seen.append(["全部の写真", p["seen"].get("seen/all", 0)])
    base = p["visitors"]
    out.append(f'<figure class="card"><figcaption>見た写真の枚数（画面に入った写真）</figcaption>'
               f'{hbars(seen, "人", "まだ記録がありません", total=base or None)}'
               f'<p class="note">割合は、この期間の訪問者に対してです。</p>' + table(["枚数", "人数"], seen) + "</figure>")
    out.append("</div>")
    out.append(render_popular(p["photos"], local))
    out.append("</section></div>")
    return "".join(out)


def render_popular(photos: list, local: dict) -> str:
    info = local["photoInfo"]
    rows = [(pid, n) for pid, n in photos if n > 0][:12]
    out = ['<figure class="card"><figcaption>全画面でよく開かれた写真</figcaption>']
    if not rows:
        out.append('<p class="empty">まだ記録がありません</p></figure>')
        return "".join(out)
    out.append('<ol class="popular">')
    for rank, (pid, n) in enumerate(rows, start=1):
        meta = info.get(pid)
        if meta and meta.get("thumb"):
            img = f'<img src="../{esc(meta["thumb"])}" alt="" loading="lazy" width="120" height="120">'
            cap = f'{esc(meta["no"])}　{esc(meta["date"])}'
        else:
            img = '<span class="gone">今はない写真</span>'
            cap = esc(pid)
        out.append(f'<li tabindex="0" data-tip="{esc(f"{rank}位　{n}人")}"><span class="rank">{rank}</span>{img}'
                   f'<span class="pop-cap">{cap}</span><span class="pop-n">{fmt_int(n)}<small>人</small></span></li>')
    out.append("</ol>")
    out.append(table(["順位", "写真（番号・撮影日）", "開いた人"],
                     [[i, (f'{info[pid]["no"]} {info[pid]["date"]}' if pid in info else pid), n]
                      for i, (pid, n) in enumerate(rows, start=1)]))
    out.append("</figure>")
    return "".join(out)


RESULT_LABEL = {
    "published": ("ok", "✓", "公開しました"),
    "unchanged": ("ok", "✓", "変更なし"),
    "dryrun": ("info", "・", "確認のみ"),
    "stopped": ("ng", "!", "止まりました"),
    "running": ("info", "…", "途中・不明"),
}


def render_status(local: dict) -> str:
    out = ['<section class="block"><h2>サイトの状態</h2>']
    if local["error"]:
        out.append(f'<p class="alert">{esc(local["error"])}</p>')
    pubs = local["publishes"]
    last = pubs[-1] if pubs else None
    out.append('<div class="tiles">')
    counts = [p for p in pubs if p["count"] is not None]
    sub = ""
    if len(counts) >= 2:  # いちばん新しい公開と、その前の公開の枚数の差
        diff = counts[-1]["count"] - counts[-2]["count"]
        sub = f"前の公開から {'+' if diff >= 0 else '−'}{abs(diff)}枚" if diff else "前の公開と同じ枚数"
    out.append(tile("写真の枚数", f'<span class="hero">{fmt_int(local["count"])}</span><small>枚</small>', sub, "wide"))
    if last:
        cls, icon, label = RESULT_LABEL.get(last["result"], RESULT_LABEL["running"])
        out.append(tile("最後の公開", f'<span class="status {cls}"><span class="icon" aria-hidden="true">{icon}</span>{esc(label)}</span>',
                        esc(fmt_time(last["time"])) + (f'<br>{esc(last["message"])}' if last["message"] else "")))
    else:
        out.append(tile("最後の公開", "—", "公開の記録（logs）がありません"))
    site = local["site"]
    pct = site / SITE_LIMIT * 100
    out.append(tile("公開している容量", f"{fmt_mb(site)}",
                    f'<span class="meter" role="img" aria-label="上限1GBの{pct:.0f}%"><span style="width:{min(100, pct):.1f}%"></span></span>'
                    f'上限 1GB の {pct:.0f}%'))
    doubtful = len(local["doubtful"])
    srcs = local["sources"]
    how = "・".join(f"{name} {srcs[k]}枚" for k, name in (("exif", "撮影情報"), ("filename", "ファイル名")) if srcs.get(k))
    out.append(tile("日付があやしい写真", f"{doubtful}<small>枚</small>",
                    ("ファイルの更新日時などで日付を決めた写真" if doubtful else "どの写真も撮影日がはっきりしています")
                    + (f"<br>日付の決め方: {esc(how)}" if how else "")))
    out.append("</div>")

    if counts:
        pts = [p["count"] for p in counts]
        tips = [f'{fmt_time(p["time"])}　{p["count"]}枚' for p in counts]
        n = len(counts)
        axis = [(0, fmt_time(counts[0]["time"]).split(" ")[0])]
        if n > 1:
            axis.append((n - 1, fmt_time(counts[-1]["time"]).split(" ")[0]))
        out.append(f'<figure class="card"><figcaption>公開ごとの写真の枚数</figcaption>{chart("line", pts, tips, axis, "公開ごとの写真の枚数")}'
                   + table(["公開した日時", "枚数"], [[fmt_time(p["time"]), p["count"]] for p in counts]) + "</figure>")
    months = local["months"]
    if months:
        vals = [v for _, v in months]
        labels = [f"{m[:4]}/{int(m[5:7])}" for m, _ in months]
        tips = [f"{lab}　{v}枚" for lab, v in zip(labels, vals)]
        step = max(1, len(months) // 5)
        axis = [(i, labels[i]) for i in range(0, len(months), step)]
        if axis[-1][0] != len(months) - 1:
            if len(months) - 1 - axis[-1][0] < step / 2 and len(axis) > 1:
                axis.pop()
            axis.append((len(months) - 1, labels[-1]))
        out.append(f'<figure class="card"><figcaption>撮影した月ごとの枚数</figcaption>{chart("col", vals, tips, axis, "撮影した月ごとの枚数")}'
                   + table(["撮影した月", "枚数"], [[lab, v] for lab, v in zip(labels, vals)]) + "</figure>")

    out.append('<div class="cards">')
    sizes = local["sizes"]
    size_rows = [["全画面用の写真（photos）", sizes["photos"]], ["一覧用の中くらいの写真（mids）", sizes["mids"]],
                 ["小さい写真（thumbs）", sizes["thumbs"]], ["ページ・文字・データ", sizes["other"]]]
    out.append('<figure class="card"><figcaption>容量の内訳</figcaption><ul class="hbars">')
    top = max(1, max(v for _, v in size_rows))
    for name, v in size_rows:
        out.append(f'<li tabindex="0" data-tip="{esc(f"{name}: {fmt_mb(v)}")}"><span class="hb-name">{esc(name)}</span>'
                   f'<span class="hb-track"><span class="hb-bar" style="width:{max(1.5, v / top * 100):.1f}%"></span></span>'
                   f'<span class="hb-val">{esc(fmt_mb(v))}</span></li>')
    out.append("</ul>")
    hist = local["history"]
    if hist is not None:
        out.append(f'<p class="note">Git の履歴（消した写真も残る）: {esc(fmt_mb(hist))}。GitHub はリポジトリを 1GB 未満にするよう勧めています。</p>')
    out.append("</figure>")
    if local["doubtful"]:
        out.append('<figure class="card"><figcaption>日付があやしい写真</figcaption><ul class="doubt">')
        for d in local["doubtful"][:20]:
            why = {"filename": "ファイル名の日時", "mtime": "ファイルの更新日時", "legacy": "古いデータ"}.get(d["source"], d["source"])
            img = f'<img src="../{esc(d["thumb"])}" alt="" loading="lazy" width="56" height="56">' if d["thumb"] else ""
            out.append(f'<li>{img}<span>{esc(d["date"])}<br><small>{esc(why)}から決めた日付</small></span></li>')
        out.append("</ul></figure>")
    out.append("</div>")

    rows = []
    for p in reversed(pubs[-15:]):
        cls, icon, label = RESULT_LABEL.get(p["result"], RESULT_LABEL["running"])
        rows.append(
            f'<tr><td>{esc(fmt_time(p["time"]))}</td><td><span class="status {cls}"><span class="icon" aria-hidden="true">{icon}</span>{esc(label)}</span>'
            + (f'<br><small>{esc(p["message"])}</small>' if p["message"] else "")
            + f'</td><td class="num">{esc("—" if p["count"] is None else p["count"])}</td>'
            f'<td class="num">{esc("—" if p["new"] is None else p["new"])}</td>'
            f'<td class="num">{esc("—" if p["tests"] is None else p["tests"])}</td></tr>'
        )
    out.append('<figure class="card"><figcaption>公開の履歴（新しい順）</figcaption>')
    if rows:
        out.append('<div class="tv-wrap"><table class="log"><thead><tr><th>日時</th><th>結果</th><th class="num">枚数</th><th class="num">新しく変換</th><th class="num">合格した試験</th></tr></thead><tbody>'
                   + "".join(rows) + "</tbody></table></div>")
    else:
        out.append('<p class="empty">公開の記録（logs）がありません</p>')
    out.append("</figure></section>")
    return "".join(out)


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--base:#c3c2b7;
--border:rgba(11,11,11,.10);--s1:#2a78d6;--track:#e1e0d9;--good:#0ca30c;--good-text:#006300;--critical:#d03b3b;--info:#898781}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--base:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--track:#2c2c2a;--good-text:#0ca30c}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.6 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin-bottom:16px}
h1{font-size:20px;font-weight:600;margin:0}
.updated{color:var(--ink2);font-size:12px;margin:0}
h2{font-size:16px;font-weight:600;margin:28px 0 12px}
.filters{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 0;background:var(--page);border-bottom:1px solid var(--grid)}
.filters span{color:var(--ink2);font-size:12px;margin-right:4px}
.filters button{font:inherit;font-size:13px;padding:5px 12px;border:1px solid var(--border);border-radius:999px;background:var(--surface);color:var(--ink);cursor:pointer}
.filters button[aria-pressed="true"]{border-color:var(--ink);font-weight:600}
.period{display:none}
body[data-period="7"] .p-7,body[data-period="30"] .p-30,body[data-period="90"] .p-90{display:block}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:12px}
.tile,.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;margin:0}
.tile.wide{grid-column:span 2}
@media (max-width:520px){.tile.wide{grid-column:auto}}
.tile-label{margin:0;color:var(--ink2);font-size:12px}
.tile-value{margin:2px 0 0;font-size:26px;font-weight:600;line-height:1.25}
.tile-value .hero{font-size:48px;font-weight:600;letter-spacing:-.01em}
.tile-value small,.hb-val small,.pop-n small{font-size:12px;font-weight:400;color:var(--ink2);margin-left:2px}
.tile-sub{margin:4px 0 0;color:var(--ink2);font-size:12px}
.delta.up{color:var(--good-text);font-weight:600}.delta.down{color:var(--critical);font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-top:12px}
.card{margin-top:12px}.cards .card{margin-top:0}
figcaption{font-weight:600;font-size:13px;margin-bottom:8px}
.chart{display:block;width:100%;min-height:180px}
.chart svg{display:block;overflow:visible}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .base{stroke:var(--base);stroke-width:1}
.chart .tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.chart .bar{fill:var(--s1)}.chart .hit{fill:transparent;outline:none}
.chart .hit:hover,.chart .hit:focus-visible{fill:var(--grid);fill-opacity:.55}
.chart .line{fill:none;stroke:var(--s1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.chart .dot{fill:var(--s1);stroke:var(--surface);stroke-width:2}.chart .end{fill:var(--ink);font-size:12px;font-weight:600}
.hbars{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.hbars li{display:grid;grid-template-columns:minmax(90px,38%) 1fr auto;align-items:center;gap:8px;outline:none;border-radius:6px;padding:1px 2px}
.hbars li:hover,.hbars li:focus-visible{background:var(--grid)}
.hb-name{overflow-wrap:anywhere;font-size:13px}
.hb-track{height:10px}.hb-bar{display:block;height:10px;background:var(--s1);border-radius:0 4px 4px 0}
.hb-val{font-variant-numeric:tabular-nums;font-size:13px;text-align:right;white-space:nowrap}
.note{color:var(--ink2);font-size:12px;margin:8px 0 0}
.empty{color:var(--muted);margin:6px 0}
.alert{border:1px solid var(--critical);border-radius:8px;padding:10px 12px;background:var(--surface)}
.alert b{color:var(--critical)}
.popular{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:12px}
.popular li{position:relative;display:grid;gap:2px;outline:none}
.popular li:focus-visible{box-shadow:0 0 0 2px var(--ink);border-radius:8px}
.popular img,.popular .gone{width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;background:var(--grid);display:block}
.popular .gone{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12px}
.rank{position:absolute;top:6px;left:6px;min-width:22px;height:22px;padding:0 6px;border-radius:999px;background:var(--surface);color:var(--ink);font-size:12px;font-weight:600;display:flex;align-items:center;justify-content:center}
.pop-cap{font-size:12px;color:var(--ink2)}.pop-n{font-weight:600;font-variant-numeric:tabular-nums}
.status{display:inline-flex;align-items:center;gap:6px;font-weight:600}
.status .icon{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;font-size:12px;color:#fff}
.status.ok .icon{background:var(--good)}.status.ng .icon{background:var(--critical)}.status.info .icon{background:var(--info)}
.meter{display:block;height:8px;border-radius:4px;background:var(--track);margin:6px 0 4px;overflow:hidden}
.meter span{display:block;height:100%;background:var(--s1);border-radius:0 4px 4px 0}
details.tv{margin-top:8px}details.tv summary{cursor:pointer;color:var(--ink2);font-size:12px}
.tv-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;margin-top:6px;font-size:13px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{white-space:nowrap;font-weight:600;color:var(--ink2);font-size:12px}
table.log td:first-child{white-space:nowrap}
.tile-value .status{font-size:18px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.doubt{list-style:none;margin:0;padding:0;display:grid;gap:8px}.doubt li{display:flex;gap:10px;align-items:center}
.doubt img{border-radius:6px;object-fit:cover}
.setup{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 18px}
.setup ol{margin:6px 0 0;padding-left:20px}
code{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:var(--grid);padding:1px 4px;border-radius:4px}
#tip{position:fixed;z-index:20;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);
border-radius:8px;padding:6px 10px;font-size:13px;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;max-width:280px}
footer{margin-top:32px;color:var(--muted);font-size:12px}
"""

JS = """
(function(){
  var NS='http://www.w3.org/2000/svg';
  function mk(tag,attrs,parent){var e=document.createElementNS(NS,tag);for(var k in attrs)e.setAttribute(k,attrs[k]);if(parent)parent.appendChild(e);return e}
  function txt(parent,x,y,s,anchor,cls){var t=mk('text',{x:x,y:y,'text-anchor':anchor||'start','class':cls||'tick'},parent);t.textContent=s;return t}
  function fmt(n){return Number(n).toLocaleString('ja-JP')}
  function draw(el){
    var W=el.clientWidth;if(!W)return;var d=JSON.parse(el.getAttribute('data-chart'));
    if(el.getAttribute('data-w')===String(W))return;el.setAttribute('data-w',W);el.textContent='';
    var line=d.kind==='line',H=line?170:180,L=36,R=line?44:6,B=24,T=line?16:10,n=Math.max(1,d.values.length),top=d.top||1;
    var pw=W-L-R,ph=H-B-T,svg=mk('svg',{width:W,height:H,viewBox:'0 0 '+W+' '+H,'aria-hidden':'true'},el);
    [0,0.5,1].forEach(function(f){var y=T+ph*(1-f);mk('line',{'class':f?'grid':'base',x1:L,x2:L+pw,y1:y,y2:y},svg);txt(svg,L-6,y+4,fmt(Math.round(top*f)),'end')});
    var band=pw/n,bw=Math.min(24,Math.max(2,band*0.62)),xs=[];
    d.values.forEach(function(v,i){
      var h=ph*(v/top),y=T+ph-h,cx=line?(n>1?L+pw*i/(n-1):L+pw/2):L+band*i+band/2;xs.push([cx,T+ph*(1-v/top)]);
      if(!line&&h>0){var x=cx-bw/2,r=Math.min(4,bw/2,h);
        mk('path',{'class':'bar',d:'M'+x+','+(T+ph)+'V'+(y+r)+'Q'+x+','+y+' '+(x+r)+','+y+'H'+(x+bw-r)+'Q'+(x+bw)+','+y+' '+(x+bw)+','+(y+r)+'V'+(T+ph)+'Z'},svg)}
    });
    if(line&&n>1)mk('path',{'class':'line',d:'M'+xs.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1)}).join(' L')},svg);
    d.values.forEach(function(v,i){
      var w=line?Math.max(24,n>1?pw/(n-1):24):Math.max(band,1),x=xs[i][0]-w/2;
      var hit=mk('rect',{'class':'hit',x:x,y:T,width:w,height:ph,tabindex:0,'data-tip':d.tips[i]},svg);
      mk('title',{},hit).textContent=d.tips[i];
    });
    if(line&&n){var last=xs[n-1];mk('circle',{'class':'dot',cx:last[0],cy:last[1],r:4},svg);txt(svg,last[0]+9,last[1]+4,fmt(d.values[n-1]),'start','end')}
    d.axis.forEach(function(a){var i=a[0],x=xs[i]?xs[i][0]:L,anchor='middle';
      if(i===0){anchor='start';x=line?xs[0][0]:L+band*0+(band-bw)/2}else if(i===n-1){anchor='end';x=line?xs[i][0]:L+band*i+(band+bw)/2}
      txt(svg,x,H-6,a[1],anchor)});
  }
  function drawAll(){var c=document.querySelectorAll('.chart[data-chart]');for(var i=0;i<c.length;i++)draw(c[i])}
  var rt=0;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(drawAll,120)});
  var body=document.body,btns=document.querySelectorAll('.filters button');
  function setP(p){body.setAttribute('data-period',p);for(var i=0;i<btns.length;i++)btns[i].setAttribute('aria-pressed',btns[i].getAttribute('data-p')===p?'true':'false');
    try{localStorage.setItem('pvdash:period',p)}catch(e){}drawAll()}
  for(var i=0;i<btns.length;i++)btns[i].addEventListener('click',function(){setP(this.getAttribute('data-p'))});
  try{var s=localStorage.getItem('pvdash:period');if(s&&document.querySelector('.p-'+s))setP(s)}catch(e){}
  drawAll();
  var tip=document.getElementById('tip');
  function show(el,x,y){tip.textContent=el.getAttribute('data-tip');tip.style.display='block';
    var w=tip.offsetWidth,h=tip.offsetHeight;tip.style.left=Math.min(innerWidth-w-8,Math.max(8,x-w/2))+'px';tip.style.top=Math.max(8,y-h-12)+'px'}
  function hide(){tip.style.display='none'}
  document.addEventListener('pointermove',function(e){var el=e.target.closest&&e.target.closest('[data-tip]');if(el)show(el,e.clientX,e.clientY);else hide()});
  document.addEventListener('focusin',function(e){var el=e.target.closest&&e.target.closest('[data-tip]');if(!el)return hide();var r=el.getBoundingClientRect();show(el,r.left+r.width/2,r.top)});
  document.addEventListener('focusout',hide);
})();
"""


def render(access: dict | None, access_note: str, local: dict, code: str, now: datetime) -> str:
    out = ['<!doctype html><html lang="ja"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">',
           f"<title>写真ビューア ダッシュボード</title><style>{CSS}</style></head>",
           '<body data-period="30"><div class="wrap">',
           '<header><h1>写真ビューア ダッシュボード</h1>',
           f'<p class="updated">作った日時: {esc(fmt_time(C.to_iso_jst(now)))}'
           + (f'　・　アクセス数: GoatCounter（{esc(code)}）{esc(fmt_time(access["fetchedAt"]))} に読んだ値' if access else "")
           + "</p></header>"]
    if access_note:
        out.append(f'<p class="alert">{access_note}</p>')
    if access:
        out.append('<div class="filters" role="group" aria-label="期間"><span>期間（見に来た人・見られ方）</span>'
                   + "".join(f'<button type="button" data-p="{n}" aria-pressed="{"true" if n == 30 else "false"}">直近{n}日</button>'
                             for n in PERIODS) + "</div>")
        for n in PERIODS:
            out.append(render_period(n, access["periods"][str(n)], local))
    else:
        out.append(
            '<section class="block"><h2>見に来た人・サイトの中での見られ方</h2><div class="setup">'
            "<p>アクセス数を出すには、GoatCounter の設定が必要です。</p><ol>"
            "<li>GoatCounter のアカウントを作り、管理画面の右上のメニュー → API で、権限「Read statistics」だけのキーを作る</li>"
            "<li>PC の <code>config.json</code> に <code>\"goatcounter\": \"コード\"</code> と <code>\"goatcounterToken\": \"キー\"</code> を書く</li>"
            "<li>「公開する」を実行する（サイトが数え始めます）。このページは公開のたびと、tools\\dashboard.bat で作り直されます</li>"
            "</ol></div></section>")
    out.append(render_status(local))
    out.append('<footer>このページは PC の中だけで見るものです（公開されません）。'
               '人数は GoatCounter の「訪問者」で、同じ人が続けて何度開いても1人と数えます。'
               '自分の端末の分は、サイトのアドレスの最後に #nocount を付けて一度開くと数えなくなります。</footer>')
    out.append(f'</div><div id="tip" role="tooltip"></div><script>{JS}</script></body></html>')
    return "".join(out)


# ---------------------------------------------------------------------------
def build(root: Path, fetch: bool = True, api_base: str | None = None, now: datetime | None = None) -> tuple:
    """dashboard/index.html を作る。（出力パス, お知らせの一覧）を返す。"""
    now = now or datetime.now(C.JST)
    notes = []
    try:
        cfg, _used, _warnings = C.load_config(root)
    except C.ConfigError as exc:
        cfg = dict(C.DEFAULT_CONFIG)
        notes.append(f"config.json を読めません: {exc}")
    code, token = cfg.get("goatcounter") or "", cfg.get("goatcounterToken") or ""
    out_dir = root / "dashboard"
    out_dir.mkdir(exist_ok=True)
    cache_path = out_dir / "cache.json"
    access, access_note = None, ""
    if code and token and fetch:
        try:
            access = fetch_access(code, token, api_base, now)
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(access, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cache_path)
            if access.get("failed"):  # 訪問者の数などは読めて、一部の一覧だけ読めなかった
                notes.append("一部の一覧を読めませんでした: " + " / ".join(access["failed"]))
                access_note = ("<b>一部の一覧を読めませんでした。</b>"
                               + "<br>".join(esc(f) for f in access["failed"]) + "<br>ほかの数は読めています。")
        except GCError as exc:
            notes.append(str(exc))
            access_note = f"<b>アクセス数を読めませんでした。</b>{esc(exc)}"
    elif code and not token:
        access_note = "<b>GoatCounter の API キーがまだありません。</b>config.json の goatcounterToken に、権限「Read statistics」だけのキーを書いてください。"
    if access is None and code and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("code") == code and isinstance(cached.get("periods"), dict):
                access = cached
                if access_note:
                    access_note += f"（下は {esc(fmt_time(cached.get('fetchedAt', '')))} に読んだ値です）"
        except (OSError, ValueError):
            pass
    local = local_status(root)
    page = render(access, access_note, local, code, now)
    if token and len(token) >= 8 and token in page:  # 念のため（キーをページに書かない）
        raise RuntimeError("ダッシュボードに API キーが含まれてしまいます")
    target = out_dir / "index.html"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, target)
    return target, notes


def main(argv=None) -> int:
    C.configure_stdio()
    ap = argparse.ArgumentParser(description="写真ビューアのダッシュボード（PC の中だけで見るページ）を作る")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--no-fetch", action="store_true", help="GoatCounter に問い合わせず、前回読んだ値で作る")
    ap.add_argument("--api-base", default=None, help=argparse.SUPPRESS)  # 試験用（GoatCounter の代わりの受付先）
    ap.add_argument("--open", action="store_true", help="作ったあとブラウザーで開く")
    args = ap.parse_args(argv)
    try:
        target, notes = build(args.root.resolve(), fetch=not args.no_fetch, api_base=args.api_base)
    except Exception as exc:  # ダッシュボードが作れなくても公開には影響させない
        print(f"ダッシュボードを作れませんでした: {exc}")
        return 1
    for n in notes:
        print(f"注意: {n}")
    print(f"ダッシュボードを作りました: {target}")
    if args.open:
        webbrowser.open(target.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
