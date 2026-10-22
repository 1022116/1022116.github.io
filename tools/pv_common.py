"""写真ビューア 共通定義（同期仕様ID PV-SPEC-2026-10-v10 / 版 10.0）

build_gallery.py と validate_build.py が共有する定数・日時処理・排他ロック。
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC_ID = "PV-SPEC-2026-10-v10"
SPEC_VERSION = "10.0"
SCHEMA_VERSION = 3

# S-03 / S-04（仕様で固定。設定ファイルからは変更できない）
THUMB_SIZE = 480
DETAIL_MAX_SIDE = 1920
# 動く一覧（仕様 v10）で使う中間画像。元の縦横比・長辺最大800px（小さい写真は拡大しない）
MID_MAX_SIDE = 800

# Asia/Tokyo は 1951 年以降 夏時間がなく +09:00 固定。
# Windows では zoneinfo に tzdata パッケージが必要になるため、固定オフセットで扱う。
JST = timezone(timedelta(hours=9), "Asia/Tokyo")
TIMEZONE_NAME = "Asia/Tokyo"

REQUIRED_KEYS = ("id", "src", "thumb", "date", "year", "width", "height", "alt", "dateSource")
DATE_SOURCES = ("exif", "filename", "mtime", "legacy")

# 公開JSON内のパス（POSIX相対形式・WebPのみ）
PUBLIC_PATH_RE = re.compile(r"^(photos|thumbs|mids)/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.webp$")
# YYYY-MM-DDTHH:MM:SS+09:00（秒まで・Asia/Tokyoのオフセット付き）
ISO_JST_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")

DEFAULT_ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")

# 設定ファイル（config.json）の既定値。thumbSize / detailMaxEdge は仕様固定値で、変更するとエラーにする。
DEFAULT_CONFIG = {
    "sourceDir": "source_photos",
    "allowedExtensions": list(DEFAULT_ALLOWED_EXTENSIONS),
    "siteTitle": "写真",
    "coverPhoto": "",
    "thumbSize": THUMB_SIZE,
    "detailMaxEdge": DETAIL_MAX_SIDE,
    "thumbQuality": 80,
    "detailQuality": 85,
    "shrinkGuardRatio": 0.5,
    "jobs": 0,
}

# 以前の版で使っていた設定（読み込んでも使わない。config.json に残っていても動くが、消してよいことを知らせる）
RETIRED_CONFIG_KEYS = {
    "admin": "小話の管理ページのログイン情報。v9 で小話をやめたため使いません",
}

# 終了コード
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_GUARD = 2  # 安全装置による停止（0件・大幅減少）
EXIT_LEGACY = 3  # 旧形式データの移行不能による停止
EXIT_BUSY = 75  # 別プロセスが実行中


def to_iso_jst(dt: datetime) -> str:
    """aware datetime を Asia/Tokyo の秒精度 ISO 8601 文字列にする。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    """ISO 8601 文字列を aware datetime にする（オフセットなしは Asia/Tokyo とみなす）。"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def calendar_year(dt: datetime) -> int:
    """S-01: 年は 1月1日〜12月31日（Asia/Tokyo）。4月始まりの年度は使わない。"""
    return dt.astimezone(JST).year


#   2026-09-05_14-45-52_000.jpeg（Amazon Photos）/ 2026-09-05 14.45.52.jpg / 2026-09-05T14_45_52
_FILENAME_DT_SEP = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[-_.:](\d{2})[-_.:](\d{2})")
#   IMG_20260905_144552.jpg / PXL_20260905_144552123.jpg / Screenshot_20260905-144552.png
_FILENAME_DT_COMPACT = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})")


def parse_filename_datetime(name: str) -> datetime | None:
    """ファイル名に含まれる撮影日時（秒まで）を Asia/Tokyo の現地時刻として読む。

    仕様 PV-SPEC-2026-10-v4 以降: EXIF DateTimeOriginal がない場合に使う（EXIF → ファイル名 → 更新日時）。
    ファイル名の中で最初に現れる日時だけを使う（Amazon Photos が重複時に付ける
    「 (2026-10-12T16_03_05.577)」のようなダウンロード日時の後置きは使わない）。
    実在しない日時・1900〜2100年以外は無視する。日付だけ（時刻なし）のファイル名は使わない。
    """
    stem = Path(name).stem
    found = []
    for rx in (_FILENAME_DT_SEP, _FILENAME_DT_COMPACT):
        m = rx.search(stem)
        if m:
            found.append(m)
    if not found:
        return None
    m = min(found, key=lambda x: x.start())
    y, mo, d, h, mi, s = (int(v) for v in m.groups())
    if not 1900 <= y <= 2100:
        return None
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=JST)
    except ValueError:
        return None


def format_alt(dt: datetime, date_source: str) -> str:
    d = dt.astimezone(JST)
    base = f"{d.year}年{d.month}月{d.day}日 {d.hour:02d}:{d.minute:02d}"
    if date_source in ("exif", "filename"):
        return f"{base} 撮影の写真"
    if date_source == "mtime":
        return f"{base} の写真（ファイル更新日時）"
    return f"{base} の写真"


def configure_stdio() -> None:
    """Windows の cp932 コンソールでも文字化け例外で止まらないようにする。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


class ConfigError(ValueError):
    pass


def load_config(root: Path, config_path: Path | None = None) -> tuple:
    """config.json を読み、検証済みの設定と実際に読んだパス（なければ None）、警告の一覧を返す。

    config_path を指定してファイルがない場合はエラー。未指定なら ROOT/config.json を探し、なければ既定値。
    """
    import json

    cfg = dict(DEFAULT_CONFIG)
    warnings = []
    path = Path(config_path) if config_path else Path(root) / "config.json"
    if config_path and not path.is_absolute() and not path.exists():
        path = Path(root) / path  # プロジェクト外のカレントから実行された場合
    used = None
    if path.is_file():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"設定ファイルを読めません: {path} ({exc})")
        if not isinstance(user, dict):
            raise ConfigError(f"設定ファイルはJSONオブジェクトである必要があります: {path}")
        for k, v in user.items():
            if k.startswith("_"):
                continue
            if k in RETIRED_CONFIG_KEYS:
                warnings.append(f"{k} は使われなくなりました（{RETIRED_CONFIG_KEYS[k]}）。config.json から消して構いません")
                continue
            if k not in DEFAULT_CONFIG:
                warnings.append(f"未知の設定キーを無視します: {k}")
                continue
            cfg[k] = v
        used = path
    elif config_path:
        raise ConfigError(f"指定された設定ファイルがありません: {config_path}")

    if cfg["thumbSize"] != THUMB_SIZE or isinstance(cfg["thumbSize"], bool):
        raise ConfigError(f"thumbSize は仕様（S-03）で {THUMB_SIZE} に固定です。config.json の thumbSize を {THUMB_SIZE} に戻してください")
    if cfg["detailMaxEdge"] != DETAIL_MAX_SIDE or isinstance(cfg["detailMaxEdge"], bool):
        raise ConfigError(
            f"detailMaxEdge は仕様（S-04）で {DETAIL_MAX_SIDE} に固定です。config.json の detailMaxEdge を {DETAIL_MAX_SIDE} に戻してください"
        )
    exts = cfg["allowedExtensions"]
    if not isinstance(exts, list) or not exts or not all(isinstance(e, str) and e.startswith(".") for e in exts):
        raise ConfigError("allowedExtensions は '.jpg' 形式の文字列リストである必要があります")
    cfg["allowedExtensions"] = sorted({e.lower() for e in exts})
    for k in ("thumbQuality", "detailQuality"):
        if not isinstance(cfg[k], int) or isinstance(cfg[k], bool) or not 1 <= cfg[k] <= 100:
            raise ConfigError(f"{k} は 1〜100 の整数である必要があります")
    r = cfg["shrinkGuardRatio"]
    if not isinstance(r, (int, float)) or isinstance(r, bool) or not 0 <= r <= 1:
        raise ConfigError("shrinkGuardRatio は 0〜1 の数値である必要があります")
    if not isinstance(cfg["sourceDir"], str) or not cfg["sourceDir"].strip():
        raise ConfigError("sourceDir が空です")
    if not isinstance(cfg["coverPhoto"], str):
        raise ConfigError("coverPhoto は原本フォルダー内の写真のファイル名（文字列）を指定してください（空なら最新の写真）")
    if not isinstance(cfg["siteTitle"], str):
        raise ConfigError("siteTitle は文字列である必要があります")
    if not isinstance(cfg["jobs"], int) or isinstance(cfg["jobs"], bool) or cfg["jobs"] < 0:
        raise ConfigError("jobs は 0以上の整数である必要があります（0=自動）")
    return cfg, used, warnings


class LockBusyError(RuntimeError):
    pass


class FileLock:
    """OS のファイルロックによる排他。

    ロックはファイルハンドルに紐づくため、プロセスが異常終了しても OS が自動で解放する
    （古いロックファイルが残って二度と実行できなくなる問題が起きない）。
    Windows: msvcrt.locking / それ以外: fcntl.flock。
    publish.ps1 側の .NET FileShare.None とも相互に排他される。
    """

    def __init__(self, path: Path, label: str):
        self.path = Path(path)
        self.label = label
        self._fh = None

    def acquire(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = open(self.path, "a+b")
        except PermissionError as exc:  # Windows: 他プロセスが共有なしで開いている
            raise LockBusyError(self.label) from exc
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise LockBusyError(self.label) from exc
        self._fh = fh
        if os.name != "nt":  # Windows はロック範囲への書込みを避ける
            try:
                fh.seek(0)
                fh.truncate()
                fh.write(f"pid={os.getpid()} at={datetime.now(JST).isoformat()}\n".encode())
                fh.flush()
            except OSError:
                pass
        return self

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def lock_paths(root: Path) -> dict:
    locks = Path(root) / ".locks"
    return {"build": locks / "build.lock", "publish": locks / "publish.lock"}
