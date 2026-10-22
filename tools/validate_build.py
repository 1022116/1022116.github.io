#!/usr/bin/env python3
"""公開出力一式の動的整合性検証（同期仕様ID PV-SPEC-2026-10-v11 / 版 11.0）

検証内容:
  JSON構文（UTF-8・BOMなし）/ 同期仕様ID / 必須キーと型 / 旧fiscalYearの混入 /
  date（ISO 8601・+09:00）と year の一致（1月1日始まり）/ 並び順（新しい順・同日時は相対パス昇順）/
  ID・パスの重複 / src・thumb のPOSIX相対パスと実在 / 画像の実読込 /
  サムネイル 480×480 / 詳細画像の最大辺 1920 以下と width・height の一致 /
  中間画像 mid（任意・仕様 v10）の長辺 800 以下と縦横比の一致 /
  公開画像のメタデータ（EXIF・XMP・ICC）なし / 参照されていない公開画像（孤立ファイル）なし / 表紙の写真が一覧にあること /
  サイトファイルの存在・外部URL依存なし・ルート相対パスなし /
  設定ファイル（thumbSize=480・detailMaxEdge=1920 の仕様固定値、値の型）

使い方:
  python tools/validate_build.py                       # プロジェクト直下を検証（config.json があれば設定も確認）
  python tools/validate_build.py --config config.json  # 設定ファイルを指定（ユーザー手順書 5章のコマンド）
  python tools/validate_build.py --root DIR            # 任意のディレクトリを検証
終了コード: 0=合格 / 1=不合格
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv_common as C  # noqa: E402

from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = 250_000_000

SITE_FILES = ("index.html", "css/style.css", "js/app.js", ".nojekyll")
CSS_URL_RE = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")
FORBIDDEN_WEBP_CHUNKS = {b"EXIF": "EXIF", b"XMP ": "XMP", b"ICCP": "ICCプロファイル"}
EXTERNAL_URL_RE = re.compile(r"""https?://|(?:src|href)\s*=\s*["']//|url\(\s*["']?//""", re.I)
ROOT_RELATIVE_RE = re.compile(r"""(?:src|href)\s*=\s*["']/(?!/)""", re.I)


@dataclass
class Result:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def read_webp_chunks(path: Path):
    """RIFF/WebP のチャンク一覧と VP8X フラグを返す（Pillowに依存しない直接解析）。"""
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("WebP(RIFF)形式ではありません")
    chunks = []
    vp8x_flags = None
    pos = 12
    while pos + 8 <= len(data):
        fourcc = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        chunks.append(fourcc)
        if fourcc == b"VP8X" and size >= 1:
            vp8x_flags = data[pos + 8]
        pos += 8 + size + (size & 1)
    return chunks, vp8x_flags


def webp_metadata_problems(path: Path) -> list:
    chunks, flags = read_webp_chunks(path)
    found = [name for code, name in FORBIDDEN_WEBP_CHUNKS.items() if code in chunks]
    if flags is not None:
        if flags & 0x20 and "ICCプロファイル" not in found:
            found.append("ICCプロファイル(フラグ)")
        if flags & 0x08 and "EXIF" not in found:
            found.append("EXIF(フラグ)")
        if flags & 0x04 and "XMP" not in found:
            found.append("XMP(フラグ)")
    return found


def find_keys(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(find_keys(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(find_keys(v, key) for v in obj)
    return False


def load_json_strict(path: Path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM 付きです（BOMなしUTF-8が必要）")
    text = raw.decode("utf-8")  # UnicodeDecodeError は呼び出し側で処理
    return json.loads(text)


def _check_image(root: Path, photo: dict):
    """1件分の画像検証。エラーメッセージのリストを返す。"""
    errs = []
    pid = photo.get("id")
    for kind in ("src", "thumb", "mid"):
        if kind == "mid" and "mid" not in photo:
            continue  # 中間画像は任意（旧データの移行など）。生成スクリプトは必ず作る
        rel = photo[kind]
        p = root / rel
        if not p.is_file():
            errs.append(f"[{pid}] {kind} の参照先がありません: {rel}")
            continue
        try:
            with Image.open(p) as im:
                fmt = im.format
                im.load()
                size = im.size
        except Exception as exc:  # 破損・形式不正
            errs.append(f"[{pid}] {kind} の画像を読み込めません: {rel} ({exc.__class__.__name__}: {exc})")
            continue
        if fmt != "WEBP":
            errs.append(f"[{pid}] {kind} が WebP ではありません: {rel} ({fmt})")
        try:
            meta = webp_metadata_problems(p)
        except ValueError as exc:
            errs.append(f"[{pid}] {kind} の構造が不正です: {rel} ({exc})")
            meta = []
        if meta:
            errs.append(f"[{pid}] {kind} にメタデータが残っています（S-08違反）: {rel} {meta}")
        w, h = size
        if kind == "thumb":
            if (w, h) != (C.THUMB_SIZE, C.THUMB_SIZE):
                errs.append(f"[{pid}] サムネイルが {C.THUMB_SIZE}×{C.THUMB_SIZE} ではありません: {w}×{h}")
        elif kind == "mid":
            pw, ph = photo.get("width"), photo.get("height")
            if max(w, h) > C.MID_MAX_SIDE:
                errs.append(f"[{pid}] 中間画像の長辺が {C.MID_MAX_SIDE}px を超えています: {w}×{h}")
            if isinstance(pw, int) and isinstance(ph, int) and pw > 0 and ph > 0:
                if w > pw or h > ph:
                    errs.append(f"[{pid}] 中間画像が詳細画像より大きくなっています: {w}×{h} / 詳細 {pw}×{ph}")
                elif abs(w * ph - h * pw) > max(pw, ph):  # 丸めによる1px程度の差は許容
                    errs.append(f"[{pid}] 中間画像の縦横比が詳細画像と一致しません: {w}×{h} / 詳細 {pw}×{ph}")
        else:
            if max(w, h) > C.DETAIL_MAX_SIDE:
                errs.append(f"[{pid}] 詳細画像の長辺が {C.DETAIL_MAX_SIDE}px を超えています: {w}×{h}")
            if (w, h) != (photo.get("width"), photo.get("height")):
                errs.append(
                    f"[{pid}] width/height がファイルと一致しません: JSON {photo.get('width')}×{photo.get('height')} / 実際 {w}×{h}"
                )
    return errs


def _valid_public_path(value, prefix: str) -> str | None:
    if not isinstance(value, str):
        return "文字列ではありません"
    if "\\" in value:
        return "バックスラッシュを含みます（POSIX形式が必要）"
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or "://" in value:
        return "絶対パス・URLは使えません（相対パスが必要）"
    if ".." in value.split("/"):
        return "'..' を含みます"
    if not value.startswith(prefix + "/"):
        return f"'{prefix}/' 配下ではありません"
    if not C.PUBLIC_PATH_RE.match(value):
        return "使用できない文字を含むか .webp ではありません"
    return None


def validate(
    root: Path,
    *,
    json_path: Path | None = None,
    manifest_path: Path | None = None,
    check_site_files: bool = True,
    check_orphans: bool = True,
    check_images: bool = True,
) -> Result:
    root = Path(root)
    json_path = Path(json_path) if json_path else root / "data" / "photos.json"
    res = Result()

    # --- サイトファイル ---------------------------------------------------------
    if check_site_files:
        for rel in SITE_FILES:
            if not (root / rel).is_file():
                res.error(f"サイトファイルがありません: {rel}")
        for rel in ("index.html", "css/style.css", "js/app.js"):
            p = root / rel
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if EXTERNAL_URL_RE.search(text):
                res.error(f"外部URLへの依存があります（外部CDN禁止）: {rel}")
            if ROOT_RELATIVE_RE.search(text):
                res.error(f"ルート相対パス(/...)があります（リポジトリのサブパスで壊れます）: {rel}")
            if rel.endswith(".css"):
                for ref in CSS_URL_RE.findall(text):
                    if ref.startswith(("data:", "#")) or "://" in ref:
                        continue
                    if ref.startswith("/"):
                        res.error(f"CSS にルート相対パスがあります: {rel} → {ref}")
                    elif not (p.parent / ref).resolve().is_file():
                        res.error(f"CSS が参照するファイルがありません: {rel} → {ref}")

    # --- JSON構文 ---------------------------------------------------------------
    if not json_path.is_file():
        res.error(f"JSONがありません: {json_path}")
        return res
    try:
        data = load_json_strict(json_path)
    except UnicodeDecodeError as exc:
        res.error(f"JSONがUTF-8ではありません: {exc}")
        return res
    except (ValueError, json.JSONDecodeError) as exc:
        res.error(f"JSON構文エラー: {exc}")
        return res

    if find_keys(data, "fiscalYear"):
        res.error(
            "旧形式の fiscalYear が含まれています。1月1日始まりの year へ移行してください"
            "（python tools/build_gallery.py で再生成、または --migrate-legacy）"
        )
    if not isinstance(data, dict) or not isinstance(data.get("photos"), list):
        res.error("JSONのトップレベルは photos 配列を持つオブジェクトである必要があります")
        return res
    if data.get("specId") != C.SPEC_ID:
        res.error(f"同期仕様IDが一致しません: {data.get('specId')!r}（期待値 {C.SPEC_ID}）")
    if str(data.get("specVersion")) != C.SPEC_VERSION:
        res.error(f"版番号が一致しません: {data.get('specVersion')!r}（期待値 {C.SPEC_VERSION}）")

    photos = data["photos"]
    res.stats["count"] = len(photos)

    # --- 各エントリの必須キー・型・日付 -------------------------------------------
    valid = []
    seen_ids, seen_src, seen_thumb, seen_mid = {}, {}, {}, {}
    for i, p in enumerate(photos):
        tag = f"photos[{i}]"
        if not isinstance(p, dict):
            res.error(f"{tag} がオブジェクトではありません")
            continue
        missing = [k for k in C.REQUIRED_KEYS if k not in p]
        if missing:
            res.error(f"{tag} 必須キー不足: {missing}")
            continue
        pid = p["id"]
        tag = f"{tag}[{pid}]"
        ok = True
        if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-f]{8,64}", pid):
            res.error(f"{tag} id の形式が不正です")
            ok = False
        for kind, prefix in (("src", "photos"), ("thumb", "thumbs"), ("mid", "mids")):
            if kind == "mid" and "mid" not in p:
                continue
            problem = _valid_public_path(p[kind], prefix)
            if problem:
                res.error(f"{tag} {kind}={p[kind]!r}: {problem}")
                ok = False
        for k in ("width", "height", "year"):
            if not isinstance(p[k], int) or isinstance(p[k], bool) or p[k] <= 0:
                res.error(f"{tag} {k} は正の整数である必要があります: {p[k]!r}")
                ok = False
        if not isinstance(p["alt"], str) or not p["alt"].strip():
            res.error(f"{tag} alt が空です")
            ok = False
        if p["dateSource"] not in C.DATE_SOURCES:
            res.error(f"{tag} dateSource が不正です: {p['dateSource']!r}")
            ok = False
        if "story" in p:  # v8 の小話。v9 で廃止したので、古いデータが公開されないよう止める
            res.error(f"{tag} story は使われなくなりました（v9 で小話を廃止）。build_gallery.py を再実行してください")
            ok = False
        dt = None
        if not isinstance(p["date"], str) or not C.ISO_JST_RE.match(p["date"]):
            res.error(f"{tag} date は Asia/Tokyo(+09:00) の ISO 8601 形式が必要です: {p['date']!r}")
            ok = False
        else:
            try:
                dt = C.parse_iso(p["date"])
            except ValueError as exc:
                res.error(f"{tag} date を解釈できません: {exc}")
                ok = False
        if dt is not None and isinstance(p["year"], int) and C.calendar_year(dt) != p["year"]:
            res.error(f"{tag} year({p['year']}) が date の西暦年({C.calendar_year(dt)})と一致しません（S-01）")
            ok = False
        for seen, key in ((seen_ids, "id"), (seen_src, "src"), (seen_thumb, "thumb"), (seen_mid, "mid")):
            v = p.get(key)
            if isinstance(v, str):
                if v in seen:
                    res.error(f"{tag} {key} が重複しています: {v}（photos[{seen[v]}] と同じ）")
                    ok = False
                else:
                    seen[v] = i
        if ok:
            valid.append((i, p, dt))

    # --- 並び順 -----------------------------------------------------------------
    order_source = None
    if manifest_path and Path(manifest_path).is_file():
        try:
            man = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            order = man.get("order", [])
            man_ids = sorted(str(o.get("id")) for o in order)
            json_ids = sorted(str(p.get("id")) for p in photos if isinstance(p, dict))
            if man_ids == json_ids:
                order_source = {o["id"]: o.get("source", "") for o in order}
            else:
                res.warn("ビルド記録(manifest)とJSONの内容が一致しないため、同日時の相対パス順は検証しません")
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            res.warn("ビルド記録(manifest)を読めないため、同日時の相対パス順は検証しません")
    for (i1, p1, d1), (i2, p2, d2) in zip(valid, valid[1:]):
        if i2 != i1 + 1:
            continue
        if d1 < d2:
            res.error(f"並び順が新しい順ではありません: photos[{i1}] {p1['date']} < photos[{i2}] {p2['date']}（S-02）")
        elif d1 == d2 and order_source is not None:
            s1, s2 = order_source.get(p1["id"], ""), order_source.get(p2["id"], "")
            if s1 > s2:
                res.error(f"同日時の並びが相対パス昇順ではありません: photos[{i1}] と photos[{i2}]（S-02）")

    # --- years / count -----------------------------------------------------------
    if "cover" in data:
        cover = data["cover"]
        ids = {p.get("id") for p in photos if isinstance(p, dict)}
        if cover is None:
            if photos:
                res.warn("表紙の写真（cover）が未設定です。画面ではいちばん新しい写真を表紙にします")
        elif not isinstance(cover, str) or cover not in ids:
            res.error(f"表紙の写真（cover）が一覧にない写真を指しています: {cover!r}")
    expected_years = sorted({p["year"] for _, p, _ in valid}, reverse=True)
    if "years" in data and data["years"] != expected_years:
        res.error(f"years が写真の年と一致しません: {data['years']} / 期待値 {expected_years}")
    if "count" in data and data["count"] != len(photos):
        res.error(f"count({data['count']}) が写真数({len(photos)})と一致しません")
    res.stats["years"] = expected_years

    # --- 画像の実読込 -------------------------------------------------------------
    if check_images and valid:
        workers = min(8, (os.cpu_count() or 2))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for errs in ex.map(lambda item: _check_image(root, item[1]), valid):
                for e in errs:
                    res.error(e)

    # --- 孤立ファイル（参照されていない公開画像） ----------------------------------
    if check_orphans:
        referenced = set()
        for p in photos:
            if isinstance(p, dict):
                for k in ("src", "thumb", "mid"):
                    if isinstance(p.get(k), str):
                        referenced.add(p[k])
        uses_mid = any(isinstance(p, dict) and "mid" in p for p in photos)
        orphans = []
        for top in ("photos", "thumbs", "mids"):
            base = root / top
            if not base.is_dir():
                if top != "mids" or uses_mid:
                    res.error(f"出力ディレクトリがありません: {top}/")
                continue
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    rel = Path(dirpath, name).relative_to(root).as_posix()
                    if name == ".gitkeep" and Path(dirpath) == base:
                        continue
                    if rel not in referenced:
                        orphans.append(rel)
        if orphans:
            sample = ", ".join(sorted(orphans)[:5])
            res.error(f"参照されていないファイルが公開ディレクトリにあります（{len(orphans)}件）: {sample}")
    return res


def print_result(res: Result, label: str = "検証") -> None:
    for w in res.warnings:
        print(f"[警告] {w}")
    for e in res.errors[:200]:
        print(f"[エラー] {e}")
    if len(res.errors) > 200:
        print(f"[エラー] ほか {len(res.errors) - 200} 件")
    n = res.stats.get("count", "?")
    if res.ok:
        print(f"{label}: 合格（{n}件, 年: {res.stats.get('years', [])}）")
    else:
        print(f"{label}: 不合格（エラー {len(res.errors)}件）")


def check_config(root: Path, config_path: Path | None, res: Result) -> None:
    """設定ファイルを build と同じ規則で確認する（仕様固定値の変更はエラー）。"""
    try:
        cfg, used, warnings = C.load_config(root, config_path)
    except C.ConfigError as exc:
        res.error(f"設定ファイル: {exc}")
        return
    for w in warnings:
        res.warn(f"設定ファイル: {w}")
    res.stats["config"] = str(used) if used else None
    if used is None:
        return
    try:
        data = load_json_strict(root / "data" / "photos.json")
        title = data.get("title") if isinstance(data, dict) else None
    except Exception:
        return
    if title is not None and title != cfg["siteTitle"]:
        res.warn(f"siteTitle（{cfg['siteTitle']}）が公開データの title（{title}）と異なります。build_gallery.py を再実行すると反映されます")


def main(argv=None) -> int:
    C.configure_stdio()
    ap = argparse.ArgumentParser(description="写真ビューア出力の整合性検証")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--config", type=Path, default=None, help="設定ファイル（既定: ROOT/config.json があれば確認）")
    ap.add_argument("--json", type=Path, default=None, help="検証するJSON（既定: ROOT/data/photos.json）")
    ap.add_argument("--no-site-files", action="store_true", help="index.html等の確認を省略")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    manifest = root / ".build" / "manifest.json"
    res = validate(
        root,
        json_path=args.json,
        manifest_path=manifest if manifest.is_file() else None,
        check_site_files=not args.no_site_files,
    )
    check_config(root, args.config, res)
    print(f"同期仕様ID {C.SPEC_ID} / 版 {C.SPEC_VERSION}")
    if res.stats.get("config"):
        print(f"設定ファイル: {res.stats['config']}")
    print_result(res)
    return C.EXIT_OK if res.ok else C.EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
