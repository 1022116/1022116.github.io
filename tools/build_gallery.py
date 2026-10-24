#!/usr/bin/env python3
"""写真ビューア 生成スクリプト（同期仕様ID PV-SPEC-2026-10-v16 / 版 16.0）

原本フォルダー（既定: source_photos/）を再帰走査し、公開用の
  photos/<年>/<id>.webp   … 詳細画像（元の縦横比・長辺最大1920px・メタデータなし）
  thumbs/<年>/<id>.webp   … サムネイル（EXIF回転補正後・中央切り抜き 480×480・メタデータなし）
  mids/<年>/<id>.webp     … 中間画像（元の縦横比・長辺最大800px・メタデータなし。動く一覧で使う。仕様 v10）
  data/photos.json        … 一覧データ（新しい撮影日時順・同日時は相対パス昇順）
を生成する。

安全設計:
  - 原本は読み取りのみ（削除・上書き・移動をしない）
  - 表紙の写真: config.json の coverPhoto（未指定ならいちばん新しい写真）。JSON には写真IDだけを書く
  - 撮影日時: EXIF DateTimeOriginal → ファイル名の日時（例 2026-09-05_14-45-52）→ 更新日時 の順（仕様 v4）
  - .build/ 内の一時ディレクトリへ全量生成 → validate_build で検証 → 合格時だけ置換
  - 置換中に失敗したら元に戻す（ジャーナル記録。強制終了後は次回起動時に自動復旧）
  - 生成・公開の多重実行をOSのファイルロックで防止
  - 原本0件・大幅減少時は停止（--allow-empty / --allow-shrink で明示的に許可）

使い方:
  python tools/build_gallery.py                 # 生成
  python tools/build_gallery.py --migrate-legacy # 旧fiscalYear形式のJSONを year 形式へ移行
終了コード: 0=成功 / 1=失敗 / 2=安全装置で停止 / 3=旧形式を移行できず停止 / 75=実行中のため停止
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv_common as C  # noqa: E402
import validate_build as V  # noqa: E402

from PIL import Image, ImageOps  # noqa: E402

try:
    from PIL import ImageCms  # noqa: E402
except ImportError:  # littlecms 非搭載のPillow
    ImageCms = None

Image.MAX_IMAGE_PIXELS = 250_000_000
log = logging.getLogger("build_gallery")

BUILD_ALGO_VERSION = 1  # 変換処理を変えたら上げる（キャッシュ無効化）
SWAP_TARGETS = ("photos", "thumbs", "mids", "data/photos.json")
IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "icon\r"}
IGNORED_DIRS = {"@eadir", "__macosx", "$recycle.bin", "system volume information"}

DEFAULT_CONFIG = C.DEFAULT_CONFIG

HEIF_AVAILABLE = False


def _register_heif() -> None:
    """pillow-heif が導入されていれば HEIC/HEIF を読めるようにする（なければスキップ扱い）。"""
    global HEIF_AVAILABLE
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        HEIF_AVAILABLE = True
    except Exception:
        HEIF_AVAILABLE = False


_register_heif()


class BuildError(RuntimeError):
    def __init__(self, message: str, code: int = C.EXIT_FAILED):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
def load_config(root: Path, config_path: Path | None) -> dict:
    try:
        cfg, used, warnings = C.load_config(root, config_path)
    except C.ConfigError as exc:
        raise BuildError(str(exc))
    for w in warnings:
        log.warning("%s", w)
    if used:
        log.info("設定ファイル: %s", used)
    else:
        log.info("config.json がないため既定値を使います（原本: source_photos/）")
    return cfg


def settings_key(cfg: dict) -> str:
    payload = json.dumps(
        {
            "algo": BUILD_ALGO_VERSION,
            "thumb": C.THUMB_SIZE,
            "detail": C.DETAIL_MAX_SIDE,
            "mid": C.MID_MAX_SIDE,
            "tq": cfg["thumbQuality"],
            "dq": cfg["detailQuality"],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def resolve_source_dir(root: Path, cfg: dict) -> Path:
    src = Path(os.path.expanduser(cfg["sourceDir"]))
    if not src.is_absolute():
        src = root / src
    src = src.resolve()
    root_r = root.resolve()
    if not src.is_dir():
        raise BuildError(f"原本フォルダーがありません: {src}")
    if src == root_r or src in root_r.parents:
        raise BuildError(f"原本フォルダーにプロジェクト全体を含めることはできません: {src}")
    for name in ("photos", "thumbs", "mids", "data", ".build"):
        out = (root_r / name).resolve()
        if src == out or out in src.parents:
            raise BuildError(f"原本フォルダーを出力先（{name}/）の中に置くことはできません: {src}")
    return src


# ---------------------------------------------------------------------------
# 日時
# ---------------------------------------------------------------------------
EXIF_DT_RE = re.compile(r"^\s*(\d{4})[:\-/](\d{1,2})[:\-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?")
OFFSET_RE = re.compile(r"^\s*([+-])(\d{2}):?(\d{2})\s*$")


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    return str(value).replace("\x00", "").strip() or None


def parse_exif_datetime(value, offset=None) -> datetime | None:
    """EXIF の 'YYYY:MM:DD HH:MM:SS'（＋OffsetTimeOriginal）を Asia/Tokyo の datetime にする。"""
    text = _as_text(value)
    if not text:
        return None
    m = EXIF_DT_RE.match(text)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:  # 0000:00:00 など
        return None
    if not 1900 <= y <= 2100:
        return None
    off = _as_text(offset)
    om = OFFSET_RE.match(off) if off else None
    if om:
        from datetime import timedelta, timezone

        sign = 1 if om.group(1) == "+" else -1
        minutes = sign * (int(om.group(2)) * 60 + int(om.group(3)))
        if abs(minutes) <= 14 * 60:
            return dt.replace(tzinfo=timezone(timedelta(minutes=minutes))).astimezone(C.JST)
    # オフセットがない EXIF は撮影地を問わず Asia/Tokyo の現地時刻とみなす（README に明記）
    return dt.replace(tzinfo=C.JST)


def read_exif_date(im: Image.Image) -> datetime | None:
    try:
        exif = im.getexif()
        ifd = exif.get_ifd(0x8769)
    except Exception:
        return None
    return parse_exif_datetime(ifd.get(36867), ifd.get(36881))  # DateTimeOriginal / OffsetTimeOriginal


def file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(os.stat(path).st_mtime, C.JST).replace(microsecond=0)


def fallback_date(rel: str, path: Path) -> tuple:
    """EXIF DateTimeOriginal がない写真の日時: ファイル名の日時 → 更新日時（仕様 v4）。"""
    named = C.parse_filename_datetime(Path(rel).name)
    if named is not None:
        return named, "filename"
    return file_mtime(path), "mtime"


# ---------------------------------------------------------------------------
# 画像変換（ワーカー）
# ---------------------------------------------------------------------------
def _orientation(im: Image.Image) -> int:
    try:
        return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def _to_srgb(img: Image.Image) -> Image.Image:
    icc = img.info.get("icc_profile")
    if not icc or ImageCms is None:
        return img
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        if img.mode == "CMYK":
            return ImageCms.profileToProfile(img, src, dst, outputMode="RGB")
        if img.mode in ("RGB", "RGBA"):
            return ImageCms.profileToProfile(img, src, dst, outputMode=img.mode)
    except Exception:
        pass  # 不正なプロファイルは無視（色は近似）
    return img


def _normalize_mode(img: Image.Image) -> Image.Image:
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        img = img.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        img = img.convert("RGBA")
        if img.getextrema()[3] == (255, 255):
            img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _save_webp(img: Image.Image, path: Path, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.info = {}  # EXIF・XMP・ICC を一切書き込まない（S-08）
    img.save(path, "WEBP", quality=quality, method=4)


def fit_size(w: int, h: int, max_side: int) -> tuple:
    """元の縦横比のまま長辺を max_side 以下にした大きさ（拡大はしない）。"""
    scale = min(1.0, max_side / max(w, h))
    tw = max(1, min(max_side, round(w * scale)))
    th = max(1, min(max_side, round(h * scale)))
    return tw, th


def detail_size(w: int, h: int) -> tuple:
    return fit_size(w, h, C.DETAIL_MAX_SIDE)


def mid_size(w: int, h: int) -> tuple:
    return fit_size(w, h, C.MID_MAX_SIDE)


def process_one(task: dict) -> dict:
    """1枚を変換して staging に書き出す。入力不良はスキップ、出力失敗は致命的エラーとして返す。"""
    src = Path(task["abs"])
    try:
        with Image.open(src) as im:
            exif_dt = read_exif_date(im)
            orient = _orientation(im)
            rw, rh = im.size
            ow, oh = (rh, rw) if orient in (5, 6, 7, 8) else (rw, rh)
            tw, th = detail_size(ow, oh)
            if im.format == "JPEG" and tw * 2 < ow:
                # DCT縮小で読込を高速化（目標の2倍以上・サムネイル用に短辺960px以上を確保）
                s = max(tw * 2 / ow, min(1.0, 960 / min(ow, oh)))
                im.draft("RGB", (math.ceil(rw * s), math.ceil(rh * s)))
            im.load()
            img = ImageOps.exif_transpose(im)
        img = _normalize_mode(_to_srgb(img))
        detail = img if img.size == (tw, th) else img.resize((tw, th), Image.LANCZOS)
        mw, mh = mid_size(ow, oh)
        mid = detail if detail.size == (mw, mh) else detail.resize((mw, mh), Image.LANCZOS)  # 詳細画像から縮小（速い）
        thumb = ImageOps.fit(img, (C.THUMB_SIZE, C.THUMB_SIZE), Image.LANCZOS, centering=(0.5, 0.5))
        if exif_dt is not None:
            date, source = exif_dt, "exif"
        else:
            date, source = fallback_date(task["rel"], src)
    except Exception as exc:
        return {"ok": False, "fatal": False, "rel": task["rel"], "error": f"{exc.__class__.__name__}: {exc}"}

    year = C.calendar_year(date)
    rel_detail = f"photos/{year}/{task['id']}.webp"
    rel_thumb = f"thumbs/{year}/{task['id']}.webp"
    rel_mid = f"mids/{year}/{task['id']}.webp"
    staging = Path(task["staging"])
    try:
        _save_webp(detail, staging / rel_detail, task["dq"])
        _save_webp(thumb, staging / rel_thumb, task["tq"])
        _save_webp(mid, staging / rel_mid, task["tq"])
    except Exception as exc:
        return {"ok": False, "fatal": True, "rel": task["rel"], "error": f"書込み失敗 {exc.__class__.__name__}: {exc}"}
    return {
        "ok": True,
        "rel": task["rel"],
        "sha": task["sha"],
        "id": task["id"],
        "src": rel_detail,
        "thumb": rel_thumb,
        "mid": rel_mid,
        "width": detail.size[0],
        "height": detail.size[1],
        "date": C.to_iso_jst(date),
        "dateSource": source,
        "exifDate": C.to_iso_jst(exif_dt) if exif_dt else None,
        "reused": False,
    }


# ---------------------------------------------------------------------------
# 表紙の写真（仕様 v6）
# ---------------------------------------------------------------------------
def resolve_cover(cfg: dict, src_dir: Path, results: list) -> str | None:
    """config.json の coverPhoto（原本フォルダー内のファイル）を公開用の写真IDにする。

    ・空なら、いちばん新しい写真（results の先頭）
    ・「source_photos/〜」のようにフォルダー名から書いても、Windows の \\ 区切りや絶対パスでもよい
    ・見つからない／原本フォルダーの外／変換できなかった場合は警告して、いちばん新しい写真にする
    公開JSONには写真IDだけを書き、PC内のファイル名やパスは書かない。
    """
    newest = results[0]["id"] if results else None
    raw = (cfg.get("coverPhoto") or "").strip()
    if not raw:
        return newest
    root = src_dir.resolve()
    cand = Path(os.path.expanduser(raw.replace("\\", "/")))
    tries = [cand] if cand.is_absolute() else [root / cand]
    if not cand.is_absolute() and cand.parts and cand.parts[0] == root.name:
        tries.append(root.joinpath(*cand.parts[1:]))
    path = None
    for t in tries:
        t = t.resolve()
        if t.is_file():
            if t != root and root not in t.parents:
                log.warning("表紙に指定した写真が原本フォルダーの外にあります: %s（いちばん新しい写真を表紙にします）", raw)
                return newest
            path = t
            break
    if path is None:
        log.warning("表紙に指定した写真が見つかりません: %s（いちばん新しい写真を表紙にします）", raw)
        return newest
    try:
        digest = sha256_file(path)
    except OSError as exc:
        log.warning("表紙に指定した写真を読めません: %s (%s)（いちばん新しい写真を表紙にします）", raw, exc)
        return newest
    for r in results:
        if r["sha"] == digest:  # 同じ内容の写真が別名で採用されていても一致する
            log.info("表紙の写真: %s", raw)
            return r["id"]
    log.warning("表紙に指定した写真は変換できなかったため使えません: %s（いちばん新しい写真を表紙にします）", raw)
    return newest


# ---------------------------------------------------------------------------
# 走査・ハッシュ
# ---------------------------------------------------------------------------
def scan_sources(src_dir: Path, allowed: list) -> tuple:
    candidates, skipped = [], []

    def onerror(err: OSError) -> None:
        skipped.append((str(err.filename), "unreadable", f"フォルダーを読めません: {err}"))

    for dirpath, dirnames, filenames in os.walk(src_dir, onerror=onerror):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in IGNORED_DIRS)
        for name in sorted(filenames):
            if name.startswith(".") or name.lower() in IGNORED_NAMES:
                continue
            p = Path(dirpath, name)
            rel = p.relative_to(src_dir).as_posix()
            ext = p.suffix.lower()
            if ext not in allowed:
                skipped.append((rel, "extension", f"対象外の拡張子のためスキップ: {rel}"))
            elif ext in (".heic", ".heif") and not HEIF_AVAILABLE:
                skipped.append((rel, "heif", f"HEIC/HEIF は pillow-heif 未導入のためスキップ: {rel}"))
            else:
                candidates.append((rel, p))
    candidates.sort(key=lambda c: c[0])
    return candidates, skipped


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# ファイル操作（置換・ロールバック）
# ---------------------------------------------------------------------------
def _move(src: Path, dst: Path) -> None:
    """os.replace（同一ボリューム内の原子的な名前変更）。Windowsの一時的なロックは再試行。"""
    for attempt in range(6):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.4)


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _rmtree(path: Path) -> None:
    def _onerror(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_onerror)
    else:
        shutil.rmtree(path, onerror=_onerror)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def _journal_path(root: Path) -> Path:
    return root / ".build" / "swap-journal.json"


def rollback(root: Path, journal: dict) -> None:
    staging = root / journal["staging"]
    backup = root / journal["backup"]
    for t in reversed(journal.get("installed", [])):
        cur = root / t
        if cur.exists():
            (staging / t).parent.mkdir(parents=True, exist_ok=True)
            if (staging / t).exists():
                _rmtree(staging / t) if (staging / t).is_dir() else (staging / t).unlink()
            _move(cur, staging / t)
    for t in reversed(journal.get("moved_old", [])):
        if (backup / t).exists():
            cur = root / t
            if cur.exists():
                _rmtree(cur) if cur.is_dir() else cur.unlink()
            cur.parent.mkdir(parents=True, exist_ok=True)
            _move(backup / t, cur)


def recover_interrupted_swap(root: Path) -> None:
    jp = _journal_path(root)
    if not jp.is_file():
        return
    log.warning("前回の置換処理が中断されています。直前の正常版へ戻します。")
    journal = json.loads(jp.read_text(encoding="utf-8"))
    rollback(root, journal)
    jp.unlink()
    log.warning("復旧しました。")


def swap_into_place(root: Path, staging: Path, stamp: str) -> None:
    build_dir = root / ".build"
    backup = build_dir / f"backup-{stamp}"
    journal = {
        "staging": staging.relative_to(root).as_posix(),
        "backup": backup.relative_to(root).as_posix(),
        "moved_old": [],
        "installed": [],
    }
    jp = _journal_path(root)

    def save_journal() -> None:
        _write_text(jp, json.dumps(journal, ensure_ascii=False))

    save_journal()
    try:
        (backup / "data").mkdir(parents=True, exist_ok=True)
        (root / "data").mkdir(exist_ok=True)
        for t in SWAP_TARGETS:
            cur = root / t
            if cur.exists():
                _move(cur, backup / t)
                journal["moved_old"].append(t)
                save_journal()
            _move(staging / t, cur)
            journal["installed"].append(t)
            save_journal()
    except BaseException:
        log.error("置換中に失敗したため、直前の正常版へ戻します。")
        try:
            rollback(root, journal)
            jp.unlink()
        except Exception as exc:  # 復旧も失敗（ジャーナルを残し、次回起動時に再試行）
            log.critical("自動復旧に失敗しました。次回の build 実行時に再試行します: %s", exc)
        raise
    jp.unlink()
    _rmtree(backup)


def cleanup_leftovers(root: Path) -> None:
    build_dir = root / ".build"
    if not build_dir.is_dir():
        return
    for p in build_dir.iterdir():
        if p.is_dir() and p.name.startswith(("staging-", "backup-", "migrate-")):
            _rmtree(p)


# ---------------------------------------------------------------------------
# 既存出力の情報
# ---------------------------------------------------------------------------
def previous_state(root: Path) -> dict:
    info = {"count": 0, "legacy": False, "readable": False}
    jp = root / "data" / "photos.json"
    if jp.is_file():
        try:
            data = V.load_json_strict(jp)
            photos = data.get("photos") if isinstance(data, dict) else data
            if isinstance(photos, list):
                info["count"] = len(photos)
                info["readable"] = True
            info["legacy"] = V.find_keys(data, "fiscalYear")
        except Exception:
            pass
    thumbs = root / "thumbs"
    if thumbs.is_dir():
        n = sum(1 for _ in thumbs.rglob("*.webp"))
        info["count"] = max(info["count"], n)
    return info


def load_cache(root: Path, key: str) -> dict:
    mp = root / ".build" / "manifest.json"
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
        if man.get("settingsKey") == key and isinstance(man.get("entries"), dict):
            return man["entries"]
    except (OSError, ValueError, AttributeError):
        pass
    return {}


# ---------------------------------------------------------------------------
# 生成本体
# ---------------------------------------------------------------------------
def build(
    root: Path,
    *,
    config_path: Path | None = None,
    allow_empty: bool = False,
    allow_shrink: bool = False,
    strict: bool = False,
    jobs: int | None = None,
) -> int:
    t0 = time.monotonic()
    root = root.resolve()
    cfg = load_config(root, config_path)
    src_dir = resolve_source_dir(root, cfg)
    key = settings_key(cfg)
    log.info("原本フォルダー: %s", src_dir)
    log.info("HEIC/HEIF 対応: %s", "あり（pillow-heif）" if HEIF_AVAILABLE else "なし（pillow-heif 未導入）")

    recover_interrupted_swap(root)
    cleanup_leftovers(root)

    prev = previous_state(root)
    if prev["legacy"]:
        log.warning("既存の data/photos.json は旧形式（fiscalYear・4月始まり）です。原本から year（1月1日始まり）形式で再生成して置き換えます。")

    candidates, skipped = scan_sources(src_dir, cfg["allowedExtensions"])
    for _rel, _kind, msg in skipped:
        log.warning(msg)
    log.info("対象ファイル: %d件", len(candidates))

    if not candidates and prev["count"] > 0 and not allow_empty:
        raise BuildError(
            f"原本が0件です（公開中は{prev['count']}件）。原本フォルダーの接続・パスを確認してください。"
            " すべて非公開にする意図なら --allow-empty を付けて実行してください。既存の公開データは変更していません。",
            C.EXIT_GUARD,
        )

    # 内容ハッシュによる重複排除（相対パス昇順で先に見つかったものを採用）
    seen: dict = {}
    unique = []
    dup_count = 0
    unreadable = 0
    for rel, path in candidates:
        try:
            digest = sha256_file(path)
        except OSError as exc:
            log.warning("読めないためスキップ: %s (%s)", rel, exc)
            unreadable += 1
            continue
        if digest in seen:
            log.info("内容が同一の写真のためスキップ: %s（%s と同一）", rel, seen[digest])
            dup_count += 1
            continue
        seen[digest] = rel
        unique.append((rel, path, digest))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{os.getpid()}"
    staging = root / ".build" / f"staging-{stamp}"
    try:
        staging.mkdir(parents=True)
        for d in ("photos", "thumbs", "mids", "data"):
            (staging / d).mkdir()
        for d in ("photos", "thumbs", "mids"):
            (staging / d / ".gitkeep").touch()
    except OSError as exc:
        raise BuildError(f"一時ディレクトリを作成できません（書込み不可）: {staging} ({exc})")

    try:
        results, corrupt = _convert_all(root, staging, unique, cfg, key, jobs)
        if corrupt and strict:
            raise BuildError(f"破損などで変換できない写真が{len(corrupt)}件あります（--strict）")

        # 新しい撮影日時順・同日時は相対パス昇順（安定ソート）
        results.sort(key=lambda r: r["rel"])
        results.sort(key=lambda r: C.parse_iso(r["date"]), reverse=True)

        n = len(results)
        if n == 0 and prev["count"] > 0 and not allow_empty:
            raise BuildError(
                f"変換できた写真が0件です（公開中は{prev['count']}件）。既存の公開データは変更していません。"
                " 意図的に空にする場合は --allow-empty を付けてください。",
                C.EXIT_GUARD,
            )
        ratio = cfg["shrinkGuardRatio"]
        if prev["count"] >= 10 and n < prev["count"] * ratio and not allow_shrink:
            raise BuildError(
                f"写真数が大きく減ります（{prev['count']}件 → {n}件）。原本フォルダーを確認してください。"
                " 意図した削除なら --allow-shrink を付けて実行してください。既存の公開データは変更していません。",
                C.EXIT_GUARD,
            )

        photos = [
            {
                "id": r["id"],
                "src": r["src"],
                "thumb": r["thumb"],
                "mid": r["mid"],
                "date": r["date"],
                "year": C.calendar_year(C.parse_iso(r["date"])),
                "width": r["width"],
                "height": r["height"],
                "alt": C.format_alt(C.parse_iso(r["date"]), r["dateSource"]),
                "dateSource": r["dateSource"],
            }
            for r in results
        ]
        years = sorted({p["year"] for p in photos}, reverse=True)
        cover_id = resolve_cover(cfg, src_dir, results)
        doc = {
            "specId": C.SPEC_ID,
            "specVersion": C.SPEC_VERSION,
            "schemaVersion": C.SCHEMA_VERSION,
            "title": cfg["siteTitle"],
            "timezone": C.TIMEZONE_NAME,
            "count": len(photos),
            "years": years,
            "cover": cover_id,
            "photos": photos,
        }
        try:
            _write_text(staging / "data" / "photos.json", json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            raise BuildError(f"JSONを書き込めません: {exc}")
        manifest = {
            "specId": C.SPEC_ID,
            "settingsKey": key,
            "generatedAt": C.to_iso_jst(datetime.now(C.JST)),
            "sourceDir": str(src_dir),
            "order": [{"id": r["id"], "source": r["rel"]} for r in results],
            "entries": {
                r["sha"]: {
                    "id": r["id"],
                    "src": r["src"],
                    "thumb": r["thumb"],
                    "mid": r["mid"],
                    "width": r["width"],
                    "height": r["height"],
                    "exifDate": r["exifDate"],
                }
                for r in results
            },
        }
        _write_text(staging / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))

        # S-10: 検証に合格した場合だけ置換する
        res = V.validate(staging, manifest_path=staging / "manifest.json", check_site_files=False)
        for w in res.warnings:
            log.warning("検証: %s", w)
        if not res.ok:
            for e in res.errors[:50]:
                log.error("検証: %s", e)
            raise BuildError(f"生成結果が検証に不合格です（{len(res.errors)}件）。既存の公開データは変更していません。")

        swap_into_place(root, staging, stamp)
        _move(staging / "manifest.json", root / ".build" / "manifest.json")
    finally:
        _rmtree(staging)

    reused = sum(1 for r in results if r["reused"])
    log.info("生成完了: %d件（新規変換 %d / 再利用 %d）", len(results), len(results) - reused, reused)
    by_kind = {}
    for _rel, kind, _m in skipped:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    log.info(
        "スキップ: 破損・読込不可 %d / 対象外拡張子 %d / HEIC未対応 %d / 重複 %d / 読めないファイル %d",
        len(corrupt),
        by_kind.get("extension", 0),
        by_kind.get("heif", 0),
        dup_count,
        unreadable + by_kind.get("unreadable", 0),
    )
    counts = {}
    for p in photos:
        counts[p["year"]] = counts.get(p["year"], 0) + 1
    log.info("年別: %s", ", ".join(f"{y}年 {counts[y]}件" for y in years) or "なし")
    log.info("所要時間: %.1f秒", time.monotonic() - t0)
    return C.EXIT_OK


def _convert_all(root: Path, staging: Path, unique: list, cfg: dict, key: str, jobs: int | None):
    cache = load_cache(root, key)
    results, corrupt, tasks = [], [], []
    for rel, path, digest in unique:
        pid = digest[:16]
        hit = cache.get(digest)
        if (
            hit
            and hit.get("id") == pid
            and all(isinstance(hit.get(k), str) and (root / hit[k]).is_file() for k in ("src", "thumb", "mid"))
        ):
            try:
                if hit.get("exifDate"):
                    date, source = C.parse_iso(hit["exifDate"]), "exif"
                else:
                    date, source = fallback_date(rel, path)
                year = C.calendar_year(date)
                rel_detail, rel_thumb = f"photos/{year}/{pid}.webp", f"thumbs/{year}/{pid}.webp"
                rel_mid = f"mids/{year}/{pid}.webp"
                _link_or_copy(root / hit["src"], staging / rel_detail)
                _link_or_copy(root / hit["thumb"], staging / rel_thumb)
                _link_or_copy(root / hit["mid"], staging / rel_mid)
                results.append(
                    {
                        "ok": True,
                        "rel": rel,
                        "sha": digest,
                        "id": pid,
                        "src": rel_detail,
                        "thumb": rel_thumb,
                        "mid": rel_mid,
                        "width": hit["width"],
                        "height": hit["height"],
                        "date": C.to_iso_jst(date),
                        "dateSource": source,
                        "exifDate": hit.get("exifDate"),
                        "reused": True,
                    }
                )
                continue
            except (OSError, ValueError, KeyError) as exc:
                log.info("再利用できないため再変換します: %s (%s)", rel, exc)
        tasks.append(
            {
                "rel": rel,
                "abs": str(path),
                "sha": digest,
                "id": pid,
                "staging": str(staging),
                "tq": cfg["thumbQuality"],
                "dq": cfg["detailQuality"],
            }
        )

    n_jobs = jobs if jobs is not None else (cfg["jobs"] or min(4, os.cpu_count() or 1))
    if tasks:
        log.info("変換: %d件（並列数 %d）", len(tasks), n_jobs if len(tasks) > 1 else 1)
    if n_jobs <= 1 or len(tasks) <= 1:
        outcomes = map(process_one, tasks)
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=n_jobs)
        outcomes = executor.map(process_one, tasks, chunksize=2)
    try:
        for i, out in enumerate(outcomes, 1):
            if out["ok"]:
                results.append(out)
            elif out["fatal"]:
                raise BuildError(f"出力を書き込めません（書込み不可・容量不足など）: {out['rel']} {out['error']}")
            else:
                log.warning("破損または読み込めない画像のためスキップ: %s (%s)", out["rel"], out["error"])
                corrupt.append(out["rel"])
            if i % 50 == 0:
                log.info("  変換中… %d / %d", i, len(tasks))
    finally:
        if executor is not None:
            executor.shutdown(cancel_futures=True)
    return results, corrupt


# ---------------------------------------------------------------------------
# 旧形式（fiscalYear）の移行
# ---------------------------------------------------------------------------
def migrate_legacy(root: Path) -> int:
    jp = root / "data" / "photos.json"
    if not jp.is_file():
        log.error("data/photos.json がありません")
        return C.EXIT_FAILED
    try:
        data = V.load_json_strict(jp)
    except Exception as exc:
        log.error("data/photos.json を読めません: %s", exc)
        return C.EXIT_FAILED
    if not V.find_keys(data, "fiscalYear"):
        log.info("旧形式（fiscalYear）ではないため、移行は不要です。")
        return C.EXIT_OK
    entries = data if isinstance(data, list) else data.get("photos") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        log.error("旧形式のJSON構造を解釈できません。原本から build_gallery.py で再生成してください。")
        return C.EXIT_LEGACY
    title = data.get("title", DEFAULT_CONFIG["siteTitle"]) if isinstance(data, dict) else DEFAULT_CONFIG["siteTitle"]

    migrated = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            log.error("photos[%d] がオブジェクトではないため移行できません", i)
            return C.EXIT_LEGACY
        try:
            dt = C.parse_iso(str(e["date"]))
        except (KeyError, ValueError):
            log.error("photos[%d] に有効な date がないため、1月1日始まりの year を決められません。移行を停止します。", i)
            return C.EXIT_LEGACY
        src, thumb = e.get("src"), e.get("thumb")
        w, h = e.get("width"), e.get("height")
        if not (isinstance(w, int) and isinstance(h, int)) and isinstance(src, str) and (root / src).is_file():
            try:
                with Image.open(root / src) as im:
                    w, h = im.size
            except Exception:
                pass
        source = e.get("dateSource") if e.get("dateSource") in C.DATE_SOURCES else "legacy"
        pid = e.get("id")
        if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-f]{8,64}", pid or ""):
            pid = hashlib.sha256(str(src).encode()).hexdigest()[:16]
        migrated.append(
            {
                "id": pid,
                "src": src,
                "thumb": thumb,
                "date": C.to_iso_jst(dt),
                "year": C.calendar_year(dt),
                "width": w,
                "height": h,
                "alt": e.get("alt") if isinstance(e.get("alt"), str) and e.get("alt").strip() else C.format_alt(dt, source),
                "dateSource": source,
            }
        )
    migrated.sort(key=lambda p: C.parse_iso(p["date"]), reverse=True)  # 安定ソート（同日時は元の順序）
    doc = {
        "specId": C.SPEC_ID,
        "specVersion": C.SPEC_VERSION,
        "schemaVersion": C.SCHEMA_VERSION,
        "title": title,
        "timezone": C.TIMEZONE_NAME,
        "count": len(migrated),
        "years": sorted({p["year"] for p in migrated}, reverse=True),
        "photos": migrated,
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = root / ".build" / f"migrate-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        tmp = work / "photos.json"
        _write_text(tmp, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        res = V.validate(root, json_path=tmp, check_site_files=False)
        if not res.ok:
            for e in res.errors[:30]:
                log.error("移行後の検証: %s", e)
            log.error(
                "旧データの画像が新仕様（480×480 WebP・長辺1920px・メタデータなし等）を満たさないため、移行を停止しました。"
                " data/photos.json は変更していません。原本を source_photos/ に置き、python tools/build_gallery.py で再生成してください。"
            )
            return C.EXIT_LEGACY
        backup = root / ".build" / f"legacy-photos-{stamp}.json"
        shutil.copy2(jp, backup)
        _move(tmp, jp)
        log.info("旧形式から year 形式へ移行しました（%d件）。旧JSONの控え: %s", len(migrated), backup)
        return C.EXIT_OK
    finally:
        _rmtree(work)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def setup_logging(root: Path, verbose: bool) -> Path | None:
    C.configure_stdio()
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    log.addHandler(console)
    logs = root / "logs"
    try:
        logs.mkdir(exist_ok=True)
        path = logs / f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
        old = sorted(logs.glob("build_*.log"))
        for p in old[:-30]:  # 直近30件を保持
            try:
                p.unlink()
            except OSError:
                pass
        return path
    except OSError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="写真ビューアの公開データを生成します")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent, help="プロジェクトのフォルダー")
    ap.add_argument("--config", type=Path, default=None, help="設定ファイル（既定: ROOT/config.json）")
    ap.add_argument("--allow-empty", action="store_true", help="原本0件のとき公開データを空にすることを許可")
    ap.add_argument("--allow-shrink", action="store_true", help="写真数の大幅減少を許可")
    ap.add_argument("--strict", action="store_true", help="破損画像が1件でもあれば失敗にする")
    ap.add_argument("--jobs", type=int, default=None, help="並列変換数（既定: 設定値または自動）")
    ap.add_argument("--migrate-legacy", action="store_true", help="旧fiscalYear形式のJSONを year 形式へ移行")
    ap.add_argument("--under-publish", action="store_true", help=argparse.SUPPRESS)  # publish.ps1 専用
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    root = args.root.resolve()

    log_path = setup_logging(root, args.verbose)
    log.info("写真ビューア生成 同期仕様ID %s / 版 %s", C.SPEC_ID, C.SPEC_VERSION)
    if log_path:
        log.info("ログ: %s", log_path)

    paths = C.lock_paths(root)
    locks = []
    try:
        try:
            if not args.under_publish:
                locks.append(C.FileLock(paths["publish"], "publish").acquire())
            locks.append(C.FileLock(paths["build"], "build").acquire())
        except C.LockBusyError as exc:
            what = "公開処理（publish.ps1）" if exc.args and exc.args[0] == "publish" else "別の生成処理"
            log.error("%sが実行中のため、生成を中止しました。終了後に再実行してください。", what)
            return C.EXIT_BUSY
        pause = os.environ.get("PV_DEBUG_PAUSE_AFTER_LOCK")  # 競合試験用
        if pause:
            time.sleep(float(pause))
        if args.migrate_legacy:
            return migrate_legacy(root)
        return build(
            root,
            config_path=args.config,
            allow_empty=args.allow_empty,
            allow_shrink=args.allow_shrink,
            strict=args.strict,
            jobs=args.jobs,
        )
    except BuildError as exc:
        log.error("%s", exc)
        return exc.code
    except KeyboardInterrupt:
        log.error("中断されました。既存の公開データは変更していません。")
        return 130
    except Exception as exc:  # 想定外
        log.exception("想定外のエラーで停止しました: %s", exc)
        return C.EXIT_FAILED
    finally:
        for lk in reversed(locks):
            lk.release()


if __name__ == "__main__":
    sys.exit(main())
