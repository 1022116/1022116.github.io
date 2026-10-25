"""生成・検証の試験（同期仕様ID PV-SPEC-2026-10-v18 / 版 18.0）

試験画像を一時ディレクトリに生成し、build → JSON → 検証 → 再生成までの状態遷移とファイル更新を確認する。
分類: 正常 / 境界 / 異常 / 競合 / 移行 / 回帰
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageCms

import build_gallery as B
import pv_common as C
import validate_build as V
from pvtest_helpers import (
    PROJECT_ROOT,
    load_photos,
    make_image,
    run_build,
    run_validate,
    snapshot,
)

RED = (220, 30, 30)
BLUE = (30, 30, 220)
GREEN = (30, 200, 30)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------
def src(project: Path) -> Path:
    return project / "source_photos"


def build_ok(project: Path, *args: str) -> subprocess.CompletedProcess:
    r = run_build(project, *args)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def by_source(project: Path) -> dict:
    """原本の相対パス → JSONエントリ（非公開の manifest で対応付け）。"""
    man = json.loads((project / ".build" / "manifest.json").read_text(encoding="utf-8"))
    order = {o["id"]: o["source"] for o in man["order"]}
    return {order[p["id"]]: p for p in load_photos(project)["photos"]}


def output_snapshot(project: Path) -> dict:
    out = {}
    for d in ("photos", "thumbs", "mids", "data"):
        for k, v in snapshot(project / d).items():
            out[f"{d}/{k}"] = v[0]
    return out


def near(c1, c2, tol=40) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(c1[:3], c2[:3]))


def in_process_build(project: Path, **kw) -> int:
    return B.build(project, jobs=1, **kw)


def write_json(project: Path, doc) -> None:
    (project / "data" / "photos.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_errors(project: Path) -> list:
    return V.validate(project, manifest_path=project / ".build" / "manifest.json").errors


# ===========================================================================
# 正常
# ===========================================================================
class TestNormal:
    def test_portrait_landscape_square_sizes(self, project):
        s = src(project)
        cases = {
            "portrait.jpg": ((3000, 4000), (1440, 1920)),
            "landscape.jpg": ((4000, 3000), (1920, 1440)),
            "square.jpg": ((2500, 2500), (1920, 1920)),
            "panorama.jpg": ((6000, 1200), (1920, 384)),
            "small.jpg": ((800, 600), (800, 600)),
        }
        for i, (name, (size, _)) in enumerate(cases.items()):
            make_image(s / name, size, exif_dt=f"2025:06:0{i + 1} 12:00:00")
        build_ok(project)
        m = by_source(project)
        for name, (size, expected) in cases.items():
            e = m[name]
            assert (e["width"], e["height"]) == expected, name
            with Image.open(project / e["src"]) as im:
                assert im.size == expected and im.format == "WEBP"
                assert abs(im.size[0] / im.size[1] - size[0] / size[1]) < 0.01  # 元の縦横比
            with Image.open(project / e["thumb"]) as im:
                assert im.size == (480, 480) and im.format == "WEBP"
        assert run_validate(project).returncode == 0

    def test_exif_orientation_is_corrected(self, project):
        # 保存上は横長(左=赤・右=青)、Orientation=6（90°回転して表示）→ 表示は縦長で上=赤・下=青
        img = Image.new("RGB", (600, 400), RED)
        img.paste(BLUE, (300, 0, 600, 400))
        path = src(project) / "rotated.jpg"
        from pvtest_helpers import exif_bytes

        img.save(path, "JPEG", exif=exif_bytes("2024:05:01 10:00:00", orientation=6), quality=95)
        build_ok(project)
        e = by_source(project)["rotated.jpg"]
        assert (e["width"], e["height"]) == (400, 600)
        with Image.open(project / e["src"]) as im:
            rgb = im.convert("RGB")
            assert near(rgb.getpixel((200, 60)), RED)
            assert near(rgb.getpixel((200, 540)), BLUE)
        with Image.open(project / e["thumb"]) as im:
            rgb = im.convert("RGB")
            assert near(rgb.getpixel((240, 10)), RED) and near(rgb.getpixel((240, 470)), BLUE)

    def test_thumbnail_is_center_crop(self, project):
        make_image(src(project) / "wide.jpg", (1200, 400), bands=(BLUE, RED, BLUE), exif_dt="2024:01:02 03:04:05")
        tall = Image.new("RGB", (400, 1200), GREEN)
        tall.paste(RED, (0, 400, 400, 800))
        tall.save(src(project) / "tall.png")
        build_ok(project)
        m = by_source(project)
        for name in ("wide.jpg", "tall.png"):
            with Image.open(project / m[name]["thumb"]) as im:
                rgb = im.convert("RGB")
                for pt in ((240, 240), (8, 240), (472, 240), (240, 8), (240, 472)):
                    assert near(rgb.getpixel(pt), RED, 60), (name, pt, rgb.getpixel(pt))

    def test_exif_date_preferred_and_mtime_fallback(self, project):
        make_image(src(project) / "exif.jpg", exif_dt="2024:07:08 09:10:11", mtime=datetime(2020, 1, 1))
        make_image(src(project) / "noexif.png", mtime=datetime(2023, 4, 1, 9, 0, 0, tzinfo=C.JST))
        build_ok(project)
        m = by_source(project)
        assert m["exif.jpg"]["date"] == "2024-07-08T09:10:11+09:00"
        assert m["exif.jpg"]["dateSource"] == "exif" and m["exif.jpg"]["year"] == 2024
        assert m["noexif.png"]["date"] == "2023-04-01T09:00:00+09:00"
        assert m["noexif.png"]["dateSource"] == "mtime" and m["noexif.png"]["year"] == 2023

    def test_gps_exif_and_icc_removed_from_public_images(self, project):
        path = src(project) / "gps.jpg"
        make_image(path, (1000, 800), exif_dt="2025:08:01 12:00:00", gps=True)
        with Image.open(path) as im:  # 前提: 原本には GPS がある
            assert im.getexif().get_ifd(0x8825)
        icc_path = src(project) / "icc.jpg"
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGB", (500, 500), GREEN).save(icc_path, "JPEG", icc_profile=icc, exif=Image.Exif())
        build_ok(project)
        for e in load_photos(project)["photos"]:
            for k in ("src", "thumb"):
                p = project / e[k]
                assert V.webp_metadata_problems(p) == []
                raw = p.read_bytes()
                assert b"EXIF" not in raw and b"ICCP" not in raw and b"XMP " not in raw
                with Image.open(p) as im:
                    assert len(im.getexif()) == 0
                    assert "icc_profile" not in im.info

    def test_originals_are_never_modified(self, project):
        s = src(project)
        make_image(s / "a.jpg", (2400, 1800), exif_dt="2025:01:01 10:00:00", orientation=6, gps=True)
        make_image(s / "sub/b.png", (500, 400))
        make_image(s / "c.heic", (900, 600), exif_dt="2025:02:02 10:00:00") if B.HEIF_AVAILABLE else None
        (s / "broken.jpg").write_bytes(b"\xff\xd8\xff\xe0broken")
        before = snapshot(s)
        build_ok(project)
        build_ok(project)
        assert snapshot(s) == before

    def test_json_schema_encoding_and_paths(self, project):
        make_image(src(project) / "家族/2025 夏/写真 1.jpg", exif_dt="2025:08:15 18:30:00")
        build_ok(project)
        raw = (project / "data" / "photos.json").read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
        doc = json.loads(raw.decode("utf-8"))
        assert doc["specId"] == "PV-SPEC-2026-10-v18" and doc["specVersion"] == "18.0"
        assert doc["count"] == 1 and doc["years"] == [2025] and doc["timezone"] == "Asia/Tokyo"
        e = doc["photos"][0]
        assert set(e) == set(C.REQUIRED_KEYS) | {"mid"}  # mid: 動く一覧の中間画像（仕様 v10）
        assert "fiscalYear" not in raw.decode("utf-8")
        assert C.ISO_JST_RE.match(e["date"])
        for k in ("src", "thumb", "mid"):
            assert C.PUBLIC_PATH_RE.match(e[k]) and "\\" not in e[k] and not e[k].startswith("/")
        assert e["mid"] == f"mids/2025/{e['id']}.webp"
        assert "家族" not in e["src"] and "写真 1" not in raw.decode("utf-8")  # 原本のパス名を公開しない
        assert "撮影の写真" in e["alt"] and "撮影の写真".encode("utf-8") in raw  # ensure_ascii=False

    def test_generate_add_remove_and_reload(self, project, caplog):
        s = src(project)
        make_image(s / "a.jpg", exif_dt="2024:01:01 10:00:00", label="A")
        make_image(s / "b.jpg", exif_dt="2025:01:01 10:00:00", label="B")
        build_ok(project)
        first = load_photos(project)
        assert first["count"] == 2
        # 追加 → 再生成（既存は再利用）
        make_image(s / "c.jpg", exif_dt="2026:01:01 10:00:00", label="C")
        r = build_ok(project)
        assert "新規変換 1 / 再利用 2" in r.stdout
        doc = load_photos(project)
        assert doc["count"] == 3 and doc["years"] == [2026, 2025, 2024]
        assert [p["year"] for p in doc["photos"]] == [2026, 2025, 2024]
        # 削除 → 再生成（公開ディレクトリに古いファイルを残さない）
        removed = by_source(project)["a.jpg"]
        (s / "a.jpg").unlink()
        build_ok(project)
        doc = load_photos(project)
        assert doc["count"] == 2 and doc["years"] == [2026, 2025]
        assert not (project / removed["src"]).exists() and not (project / removed["thumb"]).exists()
        assert run_validate(project).returncode == 0

    def test_rebuild_is_deterministic(self, project):
        for i in range(3):
            make_image(src(project) / f"p{i}.jpg", exif_dt=f"2025:03:0{i + 1} 10:00:00")
        build_ok(project)
        snap1 = output_snapshot(project)
        build_ok(project)
        assert output_snapshot(project) == snap1  # 変更がなければ公開ファイルは同一（差分なし→commitなし）

    @pytest.mark.skipif(not B.HEIF_AVAILABLE, reason="pillow-heif 未導入")
    def test_heic_converted_when_available(self, project):
        make_image(src(project) / "iphone.heic", (2000, 1500), exif_dt="2025:09:09 09:09:09")
        build_ok(project)
        e = by_source(project)["iphone.heic"]
        assert e["dateSource"] == "exif" and (e["width"], e["height"]) == (1920, 1440)

    def test_heic_skipped_when_plugin_unavailable(self, project, monkeypatch, caplog):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 00:00:00")
        (src(project) / "b.heic").write_bytes(b"dummy")
        monkeypatch.setattr(B, "HEIF_AVAILABLE", False)
        caplog.set_level(logging.INFO, logger="build_gallery")
        assert in_process_build(project) == 0
        assert load_photos(project)["count"] == 1
        assert "pillow-heif 未導入のためスキップ: b.heic" in caplog.text

    def test_alpha_png_cmyk_and_16bit_images(self, project):
        s = src(project)
        rgba = Image.new("RGBA", (600, 400), (255, 0, 0, 0))
        rgba.paste((0, 0, 255, 255), (0, 0, 300, 400))
        rgba.save(s / "alpha.png")
        Image.new("CMYK", (500, 500), (0, 200, 200, 0)).save(s / "cmyk.jpg", "JPEG")
        Image.new("I;16", (300, 300), 40000).save(s / "deep.png")
        Image.new("L", (300, 200), 128).save(s / "gray.jpg")
        Image.new("P", (200, 200), 3).save(s / "pal.png")
        build_ok(project)
        assert load_photos(project)["count"] == 5
        assert run_validate(project).returncode == 0

    def test_parallel_conversion_matches_sequential(self, project, tmp_path):
        from pvtest_helpers import make_project

        other = make_project(tmp_path / "parallel")
        for root in (project, other):
            for i in range(6):
                make_image(src(root) / f"{i}.jpg", (900 + i * 50, 700), exif_dt=f"2025:02:0{i + 1} 10:00:00", label=str(i))
        build_ok(project)  # --jobs 1
        r = subprocess.run(
            [sys.executable, str(other / "tools" / "build_gallery.py"), "--root", str(other), "--jobs", "3"],
            capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"),
        )
        assert r.returncode == 0 and "並列数 3" in r.stdout, r.stdout
        assert load_photos(project) == load_photos(other)
        assert output_snapshot(project) == output_snapshot(other)

    def test_content_duplicates_are_merged(self, project, caplog):
        s = src(project)
        make_image(s / "b/original.jpg", exif_dt="2025:05:05 05:05:05")
        (s / "a").mkdir()
        shutil.copy2(s / "b/original.jpg", s / "a/copy.jpg")
        r = build_ok(project)
        assert load_photos(project)["count"] == 1
        assert "内容が同一の写真のためスキップ: b/original.jpg（a/copy.jpg と同一）" in r.stdout


# ===========================================================================
# 境界
# ===========================================================================
class TestBoundary:
    def test_zero_photos_on_fresh_project(self, project):
        build_ok(project)
        doc = load_photos(project)
        assert doc["count"] == 0 and doc["photos"] == [] and doc["years"] == []
        assert run_validate(project).returncode == 0

    def test_single_photo(self, project):
        make_image(src(project) / "only.jpg", exif_dt="2025:05:05 12:00:00")
        build_ok(project)
        doc = load_photos(project)
        assert doc["count"] == 1 and doc["years"] == [2025]
        assert run_validate(project).returncode == 0

    def test_calendar_year_boundaries_not_fiscal_year(self, project):
        s = src(project)
        make_image(s / "dec31.jpg", exif_dt="2025:12:31 23:59:59")
        make_image(s / "jan01.jpg", exif_dt="2026:01:01 00:00:00")
        make_image(s / "mar31.jpg", exif_dt="2025:03:31 23:59:59")  # 4月始まりなら2024年度
        make_image(s / "apr01.jpg", exif_dt="2025:04:01 00:00:00")
        make_image(s / "jan01_2025.jpg", exif_dt="2025:01:01 00:00:00")
        build_ok(project)
        m = by_source(project)
        assert m["dec31.jpg"]["year"] == 2025 and m["dec31.jpg"]["date"] == "2025-12-31T23:59:59+09:00"
        assert m["jan01.jpg"]["year"] == 2026 and m["jan01.jpg"]["date"] == "2026-01-01T00:00:00+09:00"
        assert m["mar31.jpg"]["year"] == 2025 and m["apr01.jpg"]["year"] == 2025
        assert m["jan01_2025.jpg"]["year"] == 2025
        doc = load_photos(project)
        assert doc["years"] == [2026, 2025]
        assert [p["date"][:10] for p in doc["photos"]] == ["2026-01-01", "2025-12-31", "2025-04-01", "2025-03-31", "2025-01-01"]
        assert all(p["src"].startswith(f"photos/{p['year']}/") for p in doc["photos"])

    def test_exif_offset_converted_to_tokyo(self, project):
        s = src(project)
        make_image(s / "ny.jpg", exif_dt="2025:12:31 20:00:00", offset="-05:00")  # = 2026-01-01 10:00 JST
        make_image(s / "jp.jpg", exif_dt="2025:12:31 20:00:00", offset="+09:00")
        make_image(s / "utc.jpg", exif_dt="2025:12:31 14:59:59", offset="+00:00")  # = 23:59:59 JST
        build_ok(project)
        m = by_source(project)
        assert m["ny.jpg"]["date"] == "2026-01-01T10:00:00+09:00" and m["ny.jpg"]["year"] == 2026
        assert m["jp.jpg"]["date"] == "2025-12-31T20:00:00+09:00" and m["jp.jpg"]["year"] == 2025
        assert m["utc.jpg"]["date"] == "2025-12-31T23:59:59+09:00" and m["utc.jpg"]["year"] == 2025

    def test_same_datetime_sorted_by_relative_path(self, project):
        s = src(project)
        for rel in ("b/2.jpg", "a/10.jpg", "a/1.jpg", "c.jpg"):
            make_image(s / rel, exif_dt="2025:07:07 07:07:07", label=rel)
        make_image(s / "z_newer.jpg", exif_dt="2025:07:07 07:07:08")
        build_ok(project)
        man = json.loads((project / ".build" / "manifest.json").read_text(encoding="utf-8"))
        assert [o["source"] for o in man["order"]] == ["z_newer.jpg", "a/1.jpg", "a/10.jpg", "b/2.jpg", "c.jpg"]
        assert run_validate(project).returncode == 0
        # 同日時の順序を崩すと検証で検出される
        doc = load_photos(project)
        doc["photos"][1], doc["photos"][2] = doc["photos"][2], doc["photos"][1]
        write_json(project, doc)
        assert any("相対パス昇順" in e for e in validate_errors(project))

    def test_invalid_exif_date_falls_back_to_mtime(self, project):
        make_image(src(project) / "zero.jpg", exif_dt="0000:00:00 00:00:00", mtime=datetime(2022, 2, 2, 2, 2, 2, tzinfo=C.JST))
        make_image(src(project) / "blank.jpg", exif_dt="    :  :     :  :  ", mtime=datetime(2021, 1, 1, tzinfo=C.JST))
        build_ok(project)
        m = by_source(project)
        assert m["zero.jpg"]["dateSource"] == "mtime" and m["zero.jpg"]["date"] == "2022-02-02T02:02:02+09:00"
        assert m["blank.jpg"]["dateSource"] == "mtime"

    def test_tiny_image_not_upscaled_but_thumbnail_480(self, project):
        make_image(src(project) / "tiny.png", (100, 50))
        build_ok(project)
        e = by_source(project)["tiny.png"]
        assert (e["width"], e["height"]) == (100, 50)
        with Image.open(project / e["thumb"]) as im:
            assert im.size == (480, 480)

    def test_exactly_1920_and_1921(self, project):
        make_image(src(project) / "w1920.jpg", (1920, 1080), exif_dt="2025:01:01 01:00:00")
        make_image(src(project) / "w1921.jpg", (1921, 1081), exif_dt="2025:01:01 02:00:00")
        build_ok(project)
        m = by_source(project)
        assert (m["w1920.jpg"]["width"], m["w1920.jpg"]["height"]) == (1920, 1080)
        assert m["w1921.jpg"]["width"] == 1920 and m["w1921.jpg"]["height"] == 1080


# ===========================================================================
# 異常
# ===========================================================================
class TestAbnormal:
    def test_corrupt_files_are_skipped(self, project):
        s = src(project)
        make_image(s / "good.jpg", exif_dt="2025:01:01 10:00:00")
        good = (s / "good.jpg").read_bytes()
        (s / "truncated.jpg").write_bytes(good[: len(good) // 3])
        (s / "garbage.jpg").write_bytes(os.urandom(2048))
        (s / "empty.png").write_bytes(b"")
        (s / "text.webp").write_text("not an image", encoding="utf-8")
        r = build_ok(project)
        assert load_photos(project)["count"] == 1
        for name in ("truncated.jpg", "garbage.jpg", "empty.png", "text.webp"):
            assert f"スキップ: {name}" in r.stdout
        assert "破損・読込不可 4" in r.stdout
        assert run_validate(project).returncode == 0

    def test_strict_mode_fails_and_keeps_previous(self, project):
        s = src(project)
        make_image(s / "good.jpg", exif_dt="2025:01:01 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        (s / "broken.jpg").write_bytes(b"\xff\xd8\xff\xe0broken")
        make_image(s / "new.jpg", exif_dt="2025:02:01 10:00:00")
        r = run_build(project, "--strict")
        assert r.returncode == 1
        assert output_snapshot(project) == before

    def test_unknown_extensions_are_skipped(self, project):
        s = src(project)
        make_image(s / "ok.jpg", exif_dt="2025:01:01 10:00:00")
        (s / "memo.txt").write_text("x", encoding="utf-8")
        (s / "movie.mov").write_bytes(b"\x00" * 10)
        (s / "weird.xyz").write_bytes(b"\x00")
        (s / "noext").write_bytes(b"\x00")
        (s / "Thumbs.db").write_bytes(b"\x00")
        (s / ".hidden.jpg").write_bytes(b"\x00")
        r = build_ok(project)
        assert load_photos(project)["count"] == 1
        for name in ("memo.txt", "movie.mov", "weird.xyz", "noext"):
            assert f"対象外の拡張子のためスキップ: {name}" in r.stdout
        assert "Thumbs.db" not in r.stdout

    def test_validator_detects_broken_json(self, project):
        build_ok(project)
        (project / "data" / "photos.json").write_text("{ broken", encoding="utf-8")
        r = run_validate(project)
        assert r.returncode == 1 and "JSON構文エラー" in r.stdout
        (project / "data" / "photos.json").write_bytes(b"\xef\xbb\xbf{}")
        r = run_validate(project)
        assert r.returncode == 1 and "BOM" in r.stdout
        (project / "data" / "photos.json").write_bytes('{"a": "日本"}'.encode("cp932"))
        r = run_validate(project)
        assert r.returncode == 1 and "UTF-8" in r.stdout

    def test_validator_detects_missing_reference(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        build_ok(project)
        e = load_photos(project)["photos"][0]
        (project / e["thumb"]).unlink()
        r = run_validate(project)
        assert r.returncode == 1 and "参照先がありません" in r.stdout

    @pytest.mark.parametrize(
        "case",
        [
            "year_mismatch",
            "unsorted",
            "duplicate_id",
            "thumb_size",
            "detail_too_large",
            "exif_in_webp",
            "absolute_path",
            "backslash_path",
            "orphan_file",
            "missing_key",
            "fiscal_year",
            "spec_id",
            "jpeg_as_webp",
            "width_mismatch",
            "external_url",
            "root_relative_url",
            "mid_too_large",
            "mid_aspect",
            "mid_missing_file",
            "mid_wrong_folder",
            "mid_orphan",
            "mid_metadata",
        ],
    )
    def test_validator_detects_inconsistency(self, project, case):
        s = src(project)
        make_image(s / "a.jpg", (1000, 800), exif_dt="2025:03:01 10:00:00")
        make_image(s / "b.jpg", (800, 1000), exif_dt="2024:03:01 10:00:00")
        build_ok(project)
        assert validate_errors(project) == []
        doc = load_photos(project)
        p0, p1 = doc["photos"]
        expect = None
        if case == "year_mismatch":
            p0["year"] = 2024
            expect = "一致しません"
        elif case == "unsorted":
            doc["photos"] = [p1, p0]
            expect = "新しい順ではありません"
        elif case == "duplicate_id":
            p1["id"] = p0["id"]
            expect = "重複"
        elif case == "thumb_size":
            Image.new("RGB", (400, 400)).save(project / p0["thumb"], "WEBP")
            expect = "480×480"
        elif case == "detail_too_large":
            Image.new("RGB", (2000, 1600)).save(project / p0["src"], "WEBP")
            p0["width"], p0["height"] = 2000, 1600
            expect = "1920"
        elif case == "exif_in_webp":
            from pvtest_helpers import exif_bytes

            Image.new("RGB", (480, 480)).save(project / p0["thumb"], "WEBP", exif=exif_bytes(gps=True))
            expect = "メタデータ"
        elif case == "absolute_path":
            p0["src"] = "/photos/2025/" + p0["id"] + ".webp"
            expect = "絶対パス"
        elif case == "backslash_path":
            p0["src"] = p0["src"].replace("/", "\\")
            expect = "バックスラッシュ"
        elif case == "orphan_file":
            shutil.copy2(project / p0["src"], project / "photos" / "2025" / "stray.webp")
            expect = "参照されていないファイル"
        elif case == "missing_key":
            del p0["alt"]
            expect = "必須キー不足"
        elif case == "fiscal_year":
            p0["fiscalYear"] = 2024
            expect = "fiscalYear"
        elif case == "spec_id":
            doc["specId"] = "PV-SPEC-2026-09-v2"
            expect = "同期仕様ID"
        elif case == "jpeg_as_webp":
            Image.new("RGB", (480, 480)).save(project / p0["thumb"], "JPEG")
            expect = "WebP ではありません"
        elif case == "width_mismatch":
            p0["width"] += 1
            expect = "width/height"
        elif case == "mid_too_large":
            Image.new("RGB", (1000, 800)).save(project / p0["mid"], "WEBP")
            expect = "中間画像の長辺が 800px"
        elif case == "mid_aspect":
            Image.new("RGB", (800, 800)).save(project / p0["mid"], "WEBP")
            expect = "中間画像の縦横比"
        elif case == "mid_missing_file":
            (project / p0["mid"]).unlink()
            expect = "mid の参照先がありません"
        elif case == "mid_wrong_folder":
            p0["mid"] = p0["thumb"]
            expect = "'mids/' 配下ではありません"
        elif case == "mid_orphan":
            shutil.copy2(project / p0["mid"], project / "mids" / "2025" / "stray.webp")
            expect = "参照されていないファイル"
        elif case == "mid_metadata":
            from pvtest_helpers import exif_bytes

            Image.new("RGB", (800, 640)).save(project / p0["mid"], "WEBP", exif=exif_bytes(gps=True))
            expect = "mid にメタデータ"
        elif case == "external_url":
            html = (project / "index.html").read_text(encoding="utf-8")
            (project / "index.html").write_text(
                html.replace("</head>", '<script src="https://cdn.example.com/x.js"></script></head>'), encoding="utf-8"
            )
            expect = "外部URL"
        elif case == "root_relative_url":
            html = (project / "index.html").read_text(encoding="utf-8")
            (project / "index.html").write_text(html.replace('href="css/style.css"', 'href="/css/style.css"'), encoding="utf-8")
            expect = "ルート相対"
        write_json(project, doc)
        errors = V.validate(project).errors
        assert any(expect in e for e in errors), errors

    def test_unwritable_output_keeps_previous(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        make_image(src(project) / "b.jpg", exif_dt="2025:02:01 10:00:00")
        shutil.rmtree(project / ".build")
        (project / ".build").write_text("書込み不可を再現するため、ディレクトリの位置にファイルを置く", encoding="utf-8")
        r = run_build(project)
        assert r.returncode == 1 and "書込み不可" in r.stdout
        assert output_snapshot(project) == before
        assert run_validate(project).returncode == 0

    def test_disk_full_during_conversion_keeps_previous(self, project, monkeypatch):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        make_image(src(project) / "b.jpg", exif_dt="2025:02:01 10:00:00")

        def full(*_a, **_k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(B, "_save_webp", full)
        with pytest.raises(B.BuildError, match="書き込めません"):
            in_process_build(project)
        assert output_snapshot(project) == before
        assert not list((project / ".build").glob("staging-*"))

    @pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5, 6, 7, 8])
    def test_swap_failure_rolls_back_to_previous(self, project, monkeypatch, fail_at):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        make_image(src(project) / "b.jpg", exif_dt="2026:02:01 10:00:00")
        real_move = B._move
        calls = {"n": 0}

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] == fail_at:
                raise PermissionError(13, "ファイルが別のプログラムで使用中です")
            real_move(a, b)

        monkeypatch.setattr(B, "_move", flaky)
        with pytest.raises(PermissionError):
            in_process_build(project)
        monkeypatch.setattr(B, "_move", real_move)
        assert output_snapshot(project) == before
        assert not (project / ".build" / "swap-journal.json").exists()
        assert run_validate(project).returncode == 0

    def test_interrupted_swap_is_recovered_on_next_run(self, project, monkeypatch):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        make_image(src(project) / "b.jpg", exif_dt="2026:02:01 10:00:00")
        real_move = B._move
        calls = {"n": 0}

        def crash_midway(a, b):
            calls["n"] += 1
            if calls["n"] == 4:  # thumbs を退避した直後に強制終了したのと同じ状態
                raise KeyboardInterrupt
            real_move(a, b)

        monkeypatch.setattr(B, "_move", crash_midway)
        monkeypatch.setattr(B, "rollback", lambda *_a: (_ for _ in ()).throw(RuntimeError("電源断")))
        monkeypatch.setattr(B, "_rmtree", lambda *_a: None)  # 強制終了なので後片付けも行われない
        with pytest.raises(KeyboardInterrupt):
            in_process_build(project)
        monkeypatch.undo()
        assert (project / ".build" / "swap-journal.json").exists()
        assert not (project / "thumbs").exists()  # 中途半端な状態
        B.recover_interrupted_swap(project)
        assert output_snapshot(project) == before  # 直前の正常版に戻る
        build_ok(project)
        assert load_photos(project)["count"] == 2
        assert run_validate(project).returncode == 0

    def test_empty_source_guard(self, project):
        s = src(project)
        for i in range(3):
            make_image(s / f"{i}.jpg", exif_dt=f"2025:01:0{i + 1} 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        for p in s.glob("*.jpg"):
            p.unlink()
        r = run_build(project)
        assert r.returncode == 2 and "--allow-empty" in r.stdout
        assert output_snapshot(project) == before
        r = run_build(project, "--allow-empty")
        assert r.returncode == 0
        assert load_photos(project)["count"] == 0
        assert [p.name for p in (project / "photos").rglob("*") if p.is_file()] == [".gitkeep"]
        assert run_validate(project).returncode == 0

    def test_all_sources_corrupt_guard(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        (src(project) / "a.jpg").write_bytes(b"\xff\xd8broken")
        r = run_build(project)
        assert r.returncode == 2 and output_snapshot(project) == before

    def test_large_shrink_guard(self, project):
        s = src(project)
        for i in range(12):
            make_image(s / f"{i:02d}.jpg", (300, 200), exif_dt=f"2025:01:{i + 1:02d} 10:00:00")
        build_ok(project)
        before = output_snapshot(project)
        for i in range(8):
            (s / f"{i:02d}.jpg").unlink()
        r = run_build(project)
        assert r.returncode == 2 and "--allow-shrink" in r.stdout
        assert output_snapshot(project) == before
        assert run_build(project, "--allow-shrink").returncode == 0
        assert load_photos(project)["count"] == 4

    def test_missing_source_dir(self, project):
        (project / "config.json").write_text(json.dumps({"sourceDir": "Z:/no/such/folder"}), encoding="utf-8")
        r = run_build(project)
        assert r.returncode == 1 and "原本フォルダーがありません" in r.stdout

    @pytest.mark.parametrize("bad", ["photos", ".", "data/sub"])
    def test_source_dir_overlapping_outputs_rejected(self, project, bad):
        (project / "data" / "sub").mkdir(parents=True, exist_ok=True)
        (project / "config.json").write_text(json.dumps({"sourceDir": bad}), encoding="utf-8")
        r = run_build(project)
        assert r.returncode == 1 and "原本フォルダー" in r.stdout

    def test_invalid_config_values(self, project):
        (project / "config.json").write_text(json.dumps({"thumbQuality": 500}), encoding="utf-8")
        r = run_build(project)
        assert r.returncode == 1 and "thumbQuality" in r.stdout
        (project / "config.json").write_text("{ not json", encoding="utf-8")
        assert run_build(project).returncode == 1

    def test_config_with_absolute_source_outside_project(self, project, tmp_path):
        outside = tmp_path / "AmazonPhotos"
        make_image(outside / "x.jpg", exif_dt="2025:05:05 05:05:05")
        (project / "config.json").write_text(json.dumps({"sourceDir": str(outside), "siteTitle": "家族の写真"}), encoding="utf-8")
        build_ok(project)
        doc = load_photos(project)
        assert doc["count"] == 1 and doc["title"] == "家族の写真"
        assert str(outside) not in (project / "data" / "photos.json").read_text(encoding="utf-8")


# ===========================================================================
# 競合
# ===========================================================================
class TestConcurrency:
    def test_build_lock_blocks_second_build(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        with C.FileLock(C.lock_paths(project)["build"], "build"):
            r = run_build(project)
        assert r.returncode == 75 and "別の生成処理が実行中" in r.stdout
        assert load_photos(project)["count"] == 0  # 何も変更されていない

    def test_publish_lock_blocks_standalone_build(self, project):
        with C.FileLock(C.lock_paths(project)["publish"], "publish"):
            r = run_build(project)
        assert r.returncode == 75 and "公開処理" in r.stdout

    def test_under_publish_build_still_uses_build_lock(self, project):
        with C.FileLock(C.lock_paths(project)["publish"], "publish"):
            assert run_build(project, "--under-publish").returncode == 0  # publish から呼ばれた生成は実行できる
            with C.FileLock(C.lock_paths(project)["build"], "build"):
                assert run_build(project, "--under-publish").returncode == 75

    def test_two_real_builds_run_one_at_a_time(self, project):
        for i in range(4):
            make_image(src(project) / f"{i}.jpg", exif_dt=f"2025:01:0{i + 1} 10:00:00")
        env = dict(os.environ, PYTHONUTF8="1", PV_DEBUG_PAUSE_AFTER_LOCK="3")
        first = subprocess.Popen(
            [sys.executable, str(project / "tools" / "build_gallery.py"), "--root", str(project), "--jobs", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        time.sleep(1.2)
        second = run_build(project)  # タイマー等で同時に起動された2つ目
        out1, _ = first.communicate(timeout=120)
        assert second.returncode == 75
        assert first.returncode == 0, out1.decode("utf-8", "replace")
        assert load_photos(project)["count"] == 4
        assert run_validate(project).returncode == 0

    def test_lock_is_released_after_crash(self, project):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import os, pv_common as C;"
            "C.FileLock(C.lock_paths(sys.argv[2])['build'], 'build').acquire(); os._exit(9)"
        )
        r = subprocess.run([sys.executable, "-c", code, str(project / "tools"), str(project)])
        assert r.returncode == 9
        assert (project / ".locks" / "build.lock").exists()  # ロックファイルは残っても
        assert run_build(project).returncode == 0  # 次の実行は妨げられない


# ===========================================================================
# 移行（旧 fiscalYear）
# ===========================================================================
def fiscal_year(date: str) -> int:
    d = C.parse_iso(date)
    return d.year if d.month >= 4 else d.year - 1


def to_legacy(doc: dict) -> dict:
    legacy = []
    for p in doc["photos"]:
        q = dict(p)
        q.pop("year")
        q["fiscalYear"] = fiscal_year(q["date"])  # 旧仕様: 4月始まりの年度
        q.pop("dateSource", None)
        q.pop("mid", None)  # 旧仕様の時代には中間画像（v10）はない
        legacy.append(q)
    return {"photos": legacy, "fiscalYears": sorted({q["fiscalYear"] for q in legacy}, reverse=True)}


class TestMigration:
    def test_build_replaces_legacy_json(self, project):
        make_image(src(project) / "mar.jpg", exif_dt="2025:03:31 12:00:00")
        write_json(project, {"photos": [{"id": "x", "src": "photos/old.jpg", "fiscalYear": 2024, "date": "2025-03-31T12:00:00+09:00"}]})
        r = build_ok(project)
        assert "旧形式（fiscalYear" in r.stdout
        text = (project / "data" / "photos.json").read_text(encoding="utf-8")
        assert "fiscalYear" not in text
        assert load_photos(project)["photos"][0]["year"] == 2025
        assert run_validate(project).returncode == 0

    def test_migrate_legacy_converts_to_calendar_year(self, project):
        s = src(project)
        make_image(s / "a.jpg", exif_dt="2025:03:31 23:59:59")
        make_image(s / "b.jpg", exif_dt="2025:04:01 00:00:00")
        make_image(s / "c.jpg", exif_dt="2026:01:01 00:00:00")
        build_ok(project)
        modern = load_photos(project)
        write_json(project, to_legacy(modern))
        shutil.rmtree(project / "mids")  # 旧仕様の時代の公開フォルダーには mids/ がない
        assert run_validate(project).returncode == 1  # 旧形式は不合格
        r = run_build(project, "--migrate-legacy")
        assert r.returncode == 0, r.stdout
        doc = load_photos(project)
        assert "fiscalYear" not in json.dumps(doc)
        assert [p["year"] for p in doc["photos"]] == [2026, 2025, 2025]
        assert doc["years"] == [2026, 2025]
        assert {p["id"] for p in doc["photos"]} == {p["id"] for p in modern["photos"]}
        assert run_validate(project).returncode == 0
        assert list((project / ".build").glob("legacy-photos-*.json"))  # 旧JSONの控え

    def test_migrate_legacy_stops_without_date(self, project):
        write_json(project, {"photos": [{"id": "abcdef0123456789", "src": "photos/a.webp", "thumb": "thumbs/a.webp", "fiscalYear": 2024}]})
        before = (project / "data" / "photos.json").read_bytes()
        r = run_build(project, "--migrate-legacy")
        assert r.returncode == 3 and "移行を停止" in r.stdout
        assert (project / "data" / "photos.json").read_bytes() == before

    def test_migrate_legacy_stops_when_old_images_do_not_meet_spec(self, project):
        Image.new("RGB", (3000, 2000)).save(project / "photos" / "IMG_0001.jpg")
        write_json(
            project,
            [{"src": "photos/IMG_0001.jpg", "thumb": "photos/IMG_0001.jpg", "date": "2025-03-01T10:00:00+09:00", "fiscalYear": 2024}],
        )
        before = (project / "data" / "photos.json").read_bytes()
        r = run_build(project, "--migrate-legacy")
        assert r.returncode == 3 and "移行を停止" in r.stdout
        assert (project / "data" / "photos.json").read_bytes() == before

    def test_migrate_not_needed_for_current_format(self, project):
        r = run_build(project, "--migrate-legacy")
        assert r.returncode == 0 and "移行は不要" in r.stdout


# ===========================================================================
# 回帰・同期チェック
# ===========================================================================
class TestRegression:
    def test_full_output_passes_validation_including_site_files(self, project):
        for i in range(5):
            make_image(src(project) / f"{i}.jpg", exif_dt=f"202{i}:06:01 10:00:00")
        build_ok(project)
        r = run_validate(project)
        assert r.returncode == 0, r.stdout
        assert "合格（5件" in r.stdout

    def test_real_project_site_files_are_self_contained(self):
        res = V.validate(PROJECT_ROOT, check_orphans=False, check_images=False)
        site_errors = [e for e in res.errors if "外部URL" in e or "ルート相対" in e or "サイトファイル" in e]
        assert site_errors == []

    def test_spec_id_and_version_are_in_sync(self):
        files = ["README.md", "index.html", "js/app.js", "css/style.css", "tools/build_gallery.py",
                 "tools/validate_build.py", "tools/publish.ps1", "tools/publish.bat", "tools/pv_common.py",
                 "tools/make_dashboard.py", "tools/dashboard.bat", "tests/test_build.py", "tests/test_dashboard.py"]
        for rel in files:
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8-sig")
            assert "PV-SPEC-2026-10-v18" in text, rel
            assert "18.0" in text, rel
        assert C.SPEC_ID == "PV-SPEC-2026-10-v18" and C.SPEC_VERSION == "18.0"
        assert json.loads((PROJECT_ROOT / "data" / "photos.json").read_text(encoding="utf-8"))["specId"] == C.SPEC_ID

    def test_test_temp_folder_is_outside_the_project(self, tmp_path_factory):
        """試験で作る一時プロジェクト（Git リポジトリを含む）が、本物のリポジトリの中に作られないこと。"""
        base = tmp_path_factory.getbasetemp().resolve()
        assert PROJECT_ROOT.resolve() not in base.parents and base != PROJECT_ROOT.resolve()
        assert "pytest-of-" not in str(base)  # Windows で終了時に失敗する標準の一時フォルダーを使わない

    def test_publish_script_is_utf8_with_bom(self):
        raw = (PROJECT_ROOT / "tools" / "publish.ps1").read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 で日本語を読むには BOM 付き UTF-8 が必要"
        raw.decode("utf-8-sig")

    def test_publish_launcher_runs_publish_script_and_waits(self):
        """デスクトップのショートカット用 publish.bat: どこから開いても publish.ps1 を実行し、結果を見るまで閉じない。"""
        raw = (PROJECT_ROOT / "tools" / "publish.bat").read_bytes()
        # publish.ps1 が画面を UTF-8 に切り替えた後も cmd.exe が読み違えないよう、英数字だけで書く
        raw.decode("ascii")
        assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"), "バッチファイルは CRLF"
        text = raw.decode("ascii")
        lines = [ln.strip() for ln in text.splitlines()]
        assert lines[0].lower() == "@echo off"
        assert 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1" %*' in lines
        assert "-Unattended" not in text  # 手動実行なので GitHub の認証画面を出せるようにする
        assert ":\\" not in text  # PC の場所に依存しない（%~dp0 = このファイルのフォルダー）
        assert "pause" in lines and lines[-1] == "exit /b %PV_EXIT%"
        assert lines.index('set "PV_EXIT=%ERRORLEVEL%"') == lines.index(
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1" %*') + 1
        for code, label in (("0", ":done"), ("2", ":stopped"), ("75", ":busy")):
            assert f'if "%PV_EXIT%"=="{code}" goto {label[1:]}' in lines and label in lines
        attrs = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
        assert "*.bat text eol=crlf" in attrs.splitlines()

    @pytest.mark.skipif(shutil.which("git") is None, reason="git がありません")
    def test_gitignore_excludes_originals_and_private_files(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        shutil.copy2(PROJECT_ROOT / ".gitignore", repo / ".gitignore")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        for rel in ("source_photos/IMG_0001.HEIC", "source_photos/sub/a.jpg", "config.json", "logs/build.log",
                    ".build/manifest.json", ".locks/build.lock", ".venv/bin/python"):
            r = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=repo)
            assert r.returncode == 0, f"{rel} が除外されていません"
        for rel in ("source_photos/.gitkeep", "photos/2025/a.webp", "data/photos.json", "index.html"):
            r = subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=repo)
            assert r.returncode == 1, f"{rel} が除外されています"

    def test_required_files_exist(self):
        for rel in ("index.html", "css/style.css", "js/app.js", "data/photos.json", "photos", "thumbs",
                    "source_photos/.gitkeep", "tools/build_gallery.py", "tools/validate_build.py", "tools/publish.ps1",
                    "tests/test_build.py", "config.example.json", "requirements.txt", "README.md", ".gitignore", ".gitattributes", ".nojekyll", "pytest.ini"):
            assert (PROJECT_ROOT / rel).exists(), rel


# ===========================================================================
# 同期チェック: ユーザー向けセットアップ・運用手順書のコマンドと設定
# ===========================================================================
def run_tool(project: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """手順書どおり、プロジェクトのフォルダーをカレントにして相対パスで実行する。"""
    return subprocess.run(
        [sys.executable, f"tools/{script}", *args],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
        timeout=300,
    )


class TestGuideSync:
    def test_section5_commands_work_with_copied_config(self, project):
        shutil.copy2(project / "config.example.json", project / "config.json")  # 手順書 3.4 Copy-Item
        s = src(project)
        make_image(s / "dec31.jpg", exif_dt="2025:12:31 23:59:59")
        make_image(s / "jan01.jpg", exif_dt="2026:01:01 00:00:00")
        r = run_tool(project, "build_gallery.py", "--config", "config.json")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "設定ファイル" in r.stdout and "未知の設定キー" not in r.stdout
        r = run_tool(project, "validate_build.py", "--config", "config.json")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "設定ファイル: config.json" in r.stdout and "合格（2件" in r.stdout
        assert load_photos(project)["years"] == [2026, 2025]  # 12月31日と1月1日が別の年

    @pytest.mark.parametrize("key,value", [("thumbSize", 400), ("detailMaxEdge", 2048), ("thumbSize", "480"), ("detailMaxEdge", True)])
    def test_fixed_spec_values_cannot_be_changed(self, project, key, value):
        make_image(src(project) / "a.jpg", exif_dt="2025:01:01 10:00:00")
        shutil.copy2(project / "config.example.json", project / "config.json")
        assert run_tool(project, "build_gallery.py", "--config", "config.json").returncode == 0
        before = output_snapshot(project)
        cfg = json.loads((project / "config.json").read_text(encoding="utf-8"))
        cfg[key] = value
        (project / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        make_image(src(project) / "b.jpg", exif_dt="2025:02:01 10:00:00")
        r = run_tool(project, "build_gallery.py", "--config", "config.json")
        assert r.returncode == 1 and f"{key} は仕様" in r.stdout
        r = run_tool(project, "validate_build.py", "--config", "config.json")
        assert r.returncode == 1 and f"{key} は仕様" in r.stdout
        assert output_snapshot(project) == before

    def test_validate_reports_missing_config(self, project):
        r = run_tool(project, "validate_build.py", "--config", "no-such-config.json")
        assert r.returncode == 1 and "設定ファイルがありません" in r.stdout

    def test_config_example_matches_guide(self):
        cfg = json.loads((PROJECT_ROOT / "config.example.json").read_text(encoding="utf-8"))
        assert cfg["sourceDir"] == "source_photos"
        assert cfg["thumbSize"] == 480 and cfg["detailMaxEdge"] == 1920
        loaded, _used, warnings = C.load_config(PROJECT_ROOT, PROJECT_ROOT / "config.example.json")
        assert warnings == [] and loaded["thumbSize"] == 480

    def test_readme_uses_the_guide_commands(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for cmd in (
            "PV-SPEC-2026-10-v18 / version 18.0",
            "Python 3.11",
            "python -m venv .venv",
            r".\.venv\Scripts\Activate.ps1",
            "python -m pip install --upgrade pip",
            "pip install -r requirements.txt",
            "Copy-Item config.example.json config.json",
            "python tools/build_gallery.py --config config.json",
            "python tools/validate_build.py --config config.json",
            "python -m pytest -q",
            "python -m http.server 8000",
            "git push -u origin main",
            r"PowerShell -ExecutionPolicy Bypass -File .\tools\publish.ps1",
            "新しいインスタンスを開始しない",
        ):
            assert cmd in readme, cmd

    @pytest.mark.skipif(shutil.which("git") is None, reason="git がありません")
    def test_gitattributes_keeps_line_endings_stable(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        shutil.copy2(PROJECT_ROOT / ".gitattributes", repo / ".gitattributes")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        def attr(path, name):
            out = subprocess.run(["git", "check-attr", name, "--", path], cwd=repo, capture_output=True, text=True).stdout
            return out.strip().rsplit(": ", 1)[-1]
        assert attr("data/photos.json", "eol") == "lf"
        assert attr("tools/publish.ps1", "eol") == "crlf"
        assert attr("photos/2025/a.webp", "text") == "unset"  # binary

    def test_bare_pytest_command_targets_tests_folder(self):
        ini = (PROJECT_ROOT / "pytest.ini").read_text(encoding="utf-8")
        assert "testpaths = tests" in ini


# ===========================================================================
# 仕様 v4: EXIF がない写真はファイル名の日時を使う（EXIF → ファイル名 → 更新日時）
# ===========================================================================
TODAY_COPY = datetime(2026, 10, 13, 22, 7, 55, tzinfo=C.JST)  # 原本をコピーした時刻（更新日時）の代わり


class TestFilenameDate:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("2026-09-05_14-45-52_000.jpeg", "2026-09-05T14:45:52+09:00"),  # Amazon Photos
            ("2026-09-05_14-27-05_000 (2026-10-12T16_03_05.577).jpeg", "2026-09-05T14:27:05+09:00"),  # 重複時の後置き
            ("IMG_20231231_235959.jpg", "2023-12-31T23:59:59+09:00"),  # Android
            ("PXL_20260101_000000123.jpg", "2026-01-01T00:00:00+09:00"),  # Pixel（ミリ秒付き）
            ("Screenshot_20250331-120000.png", "2025-03-31T12:00:00+09:00"),
            ("2025-04-01 09.30.15.jpg", "2025-04-01T09:30:15+09:00"),
            ("2026-13-45_25-61-61_000.jpg", None),  # 実在しない日時
            ("2026-09-05.jpg", None),  # 時刻がない
            ("IMG_0001.jpg", None),
            ("12345678_123456789.jpg", None),  # 1234年は範囲外
        ],
    )
    def test_parse_filename_datetime(self, name, expected):
        dt = C.parse_filename_datetime(name)
        assert (C.to_iso_jst(dt) if dt else None) == expected

    def test_filename_date_used_when_exif_missing(self, project):
        s = src(project)
        make_image(s / "2026-09-05_14-45-52_000.jpeg", label="1", mtime=TODAY_COPY)
        make_image(s / "2026-09-05_14-27-05_000 (2026-10-12T16_03_05.577).jpeg", (700, 500), label="2", mtime=TODAY_COPY)
        make_image(s / "2026-07-19_07-17-03_000.jpeg", (500, 700), exif_dt="2026:07:19 07:17:03", mtime=TODAY_COPY)
        make_image(s / "no_date_in_name.jpg", (600, 600), mtime=TODAY_COPY)
        build_ok(project)
        m = by_source(project)
        e = m["2026-09-05_14-45-52_000.jpeg"]
        assert e["date"] == "2026-09-05T14:45:52+09:00" and e["dateSource"] == "filename"
        assert e["alt"] == "2026年9月5日 14:45 撮影の写真"
        assert m["2026-09-05_14-27-05_000 (2026-10-12T16_03_05.577).jpeg"]["date"] == "2026-09-05T14:27:05+09:00"
        assert m["2026-07-19_07-17-03_000.jpeg"]["dateSource"] == "exif"
        n = m["no_date_in_name.jpg"]
        assert n["dateSource"] == "mtime" and n["date"] == "2026-10-13T22:07:55+09:00"
        doc = load_photos(project)
        assert [p["date"][:10] for p in doc["photos"]] == ["2026-10-13", "2026-09-05", "2026-09-05", "2026-07-19"]
        assert run_validate(project).returncode == 0

    def test_exif_wins_over_filename(self, project):
        make_image(src(project) / "2026-01-01_00-00-00_000.jpg", exif_dt="2025:12:31 23:59:59", mtime=TODAY_COPY)
        build_ok(project)
        e = load_photos(project)["photos"][0]
        assert e["dateSource"] == "exif" and e["date"] == "2025-12-31T23:59:59+09:00" and e["year"] == 2025

    def test_filename_date_decides_year_not_copy_time(self, project):
        s = src(project)
        make_image(s / "IMG_20231231_235959.jpg", label="A", mtime=TODAY_COPY)
        make_image(s / "IMG_20240101_000000.jpg", label="B", mtime=TODAY_COPY)
        build_ok(project)
        doc = load_photos(project)
        assert doc["years"] == [2024, 2023]  # コピーした 2026 年ではなく、撮影した年
        m = by_source(project)
        assert m["IMG_20231231_235959.jpg"]["src"].startswith("photos/2023/")
        assert m["IMG_20240101_000000.jpg"]["src"].startswith("photos/2024/")

    def test_filename_date_kept_when_conversion_is_reused(self, project):
        make_image(src(project) / "2026-09-05_14-45-52_000.jpeg", mtime=TODAY_COPY)
        build_ok(project)
        os.utime(src(project) / "2026-09-05_14-45-52_000.jpeg", None)  # 更新日時だけ変わっても
        r = build_ok(project)
        assert "再利用 1" in r.stdout
        e = load_photos(project)["photos"][0]
        assert e["dateSource"] == "filename" and e["date"] == "2026-09-05T14:45:52+09:00"

    def test_validator_accepts_filename_source_only_from_known_list(self, project):
        make_image(src(project) / "2026-09-05_14-45-52_000.jpeg", mtime=TODAY_COPY)
        build_ok(project)
        assert validate_errors(project) == []
        doc = load_photos(project)
        doc["photos"][0]["dateSource"] = "guess"
        write_json(project, doc)
        assert any("dateSource" in e for e in validate_errors(project))

    def test_mtime_used_with_existing_v3_data_is_replaced(self, project):
        """v3 で生成済みの公開データ（コピー時刻のまま）を、再生成で v4 の日時に置き換える。"""
        make_image(src(project) / "2026-09-05_14-45-52_000.jpeg", mtime=TODAY_COPY)
        build_ok(project)
        doc = load_photos(project)
        doc["specId"], doc["specVersion"] = "PV-SPEC-2026-10-v3", "3.0"
        doc["photos"][0].update(date="2026-10-13T22:07:55+09:00", dateSource="mtime")
        write_json(project, doc)
        assert any("同期仕様ID" in e for e in V.validate(project).errors)
        build_ok(project)
        doc = load_photos(project)
        assert doc["specId"] == "PV-SPEC-2026-10-v18" and doc["photos"][0]["dateSource"] == "filename"
        assert run_validate(project).returncode == 0


# ===========================================================================
# 同梱フォント（v12: カクカクした DotGothic16。画面に出る文字だけを取り出したもの）
# ===========================================================================
FONT = PROJECT_ROOT / "fonts" / "dotgothic16-subset.woff2"


def _ui_texts() -> str:
    """画面に出る文言（app.js の文字列と index.html の本文）。コメントは含めない。"""
    js = (PROJECT_ROOT / "js" / "app.js").read_text(encoding="utf-8")
    html = re.sub(r"<!--.*?-->", "", (PROJECT_ROOT / "index.html").read_text(encoding="utf-8"), flags=re.S)
    lits = re.findall(r"'((?:[^'\\\n]|\\.)*)'", js) + re.findall(r">([^<]+)<", html)
    return "".join(lits)


class TestBundledFont:
    def test_font_and_license_are_bundled(self):
        assert FONT.is_file() and FONT.read_bytes()[:4] == b"wOF2"
        assert FONT.stat().st_size < 200_000  # 必要な文字だけなので軽い（スマホ回線でもすぐ読める）
        lic = (PROJECT_ROOT / "fonts" / "OFL.txt").read_text(encoding="utf-8")
        assert "SIL Open Font License" in lic and "DotGothic16 Project Authors" in lic
        assert not (PROJECT_ROOT / "fonts" / "klee-one-subset.woff2").exists()  # v11 までの手書き風フォントは廃止

    def test_css_and_html_point_to_the_font(self):
        css = (PROJECT_ROOT / "css" / "style.css").read_text(encoding="utf-8")
        html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
        assert 'url("../fonts/dotgothic16-subset.woff2")' in css and 'font-family: "DotGothic16 UI"' in css
        assert 'href="fonts/dotgothic16-subset.woff2"' in html
        assert "Klee One" not in css

    def test_font_subset_tool_matches_the_css(self):
        """tools/make_font_subset.py が選ぶ文字と、css の unicode-range が一致する（文言を足したら作り直す）。"""
        import importlib

        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        tool = importlib.import_module("make_font_subset")
        css = (PROJECT_ROOT / "css" / "style.css").read_text(encoding="utf-8")
        m = re.search(r'font-family: "DotGothic16 UI";[^}]*unicode-range: ([^;]+);', css)
        assert m, "DotGothic16 UI の unicode-range がありません"
        ttlib = pytest.importorskip("fontTools.ttLib", reason="fontTools 未導入（フォントの文字確認をスキップ）")
        pytest.importorskip("brotli", reason="brotli 未導入（WOFF2 を読めないためスキップ）")
        cps = sorted(ttlib.TTFont(FONT).getBestCmap())
        assert m.group(1).strip() == tool.unicode_range([c for c in cps if c >= 0x20])

    def test_font_covers_every_character_on_screen(self):
        """画面の文言・日付・かな・英数字がすべてフォントに入っている（欠けると一部だけ別の書体になる）。"""
        ttlib = pytest.importorskip("fontTools.ttLib", reason="fontTools 未導入（フォントの文字確認をスキップ）")
        pytest.importorskip("brotli", reason="brotli 未導入（WOFF2 を読めないためスキップ）")
        cmap = ttlib.TTFont(FONT).getBestCmap()
        need = set(_ui_texts()) | {chr(c) for c in range(0x20, 0x7F)} | set("年月日枚–…")
        need |= {chr(c) for c in range(0x3041, 0x3097)} | {chr(c) for c in range(0x30A1, 0x30FB)}
        missing = sorted(c for c in need if c.isprintable() and ord(c) not in cmap)
        assert missing == [], f"フォントに入っていない文字: {''.join(missing)}"

    def test_validator_detects_missing_css_asset(self, project):
        build_ok(project)
        assert V.validate(project).ok
        (project / "fonts" / "dotgothic16-subset.woff2").unlink()
        errors = V.validate(project).errors
        assert any("CSS が参照するファイルがありません" in e for e in errors), errors

    def test_publish_script_includes_fonts(self):
        text = (PROJECT_ROOT / "tools" / "publish.ps1").read_text(encoding="utf-8-sig")
        assert "'fonts'" in text


# ===========================================================================
# 仕様 v6: 表紙の写真（config.json の coverPhoto）
# ===========================================================================
class TestCoverPhoto:
    def _setup(self, project, cover=None):
        s = src(project)
        make_image(s / "2026" / "new.jpg", label="N", exif_dt="2026:05:01 10:00:00")
        make_image(s / "2025" / "old.jpg", label="O", exif_dt="2025:03:01 10:00:00")
        make_image(s / "2024" / "older.jpg", label="R", exif_dt="2024:01:01 10:00:00")
        cfg = {"sourceDir": "source_photos"}
        if cover is not None:
            cfg["coverPhoto"] = cover
        (project / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def test_default_cover_is_newest(self, project):
        self._setup(project)
        build_ok(project)
        doc = load_photos(project)
        assert doc["cover"] == doc["photos"][0]["id"]

    @pytest.mark.parametrize("value", ["2025/old.jpg", "2025\\old.jpg", "source_photos/2025/old.jpg", "source_photos\\2025\\old.jpg", " 2025/old.jpg "])
    def test_chosen_cover_by_relative_path(self, project, value):
        self._setup(project, value)
        r = build_ok(project)
        doc = load_photos(project)
        assert doc["cover"] == by_source(project)["2025/old.jpg"]["id"]
        assert "表紙の写真:" in r.stdout
        text = (project / "data" / "photos.json").read_text(encoding="utf-8")
        assert "old.jpg" not in text and "source_photos" not in text  # PC内のファイル名は公開しない
        assert run_validate(project).returncode == 0

    def test_chosen_cover_by_absolute_path_inside_source(self, project):
        self._setup(project, str((project / "source_photos" / "2024" / "older.jpg").resolve()))
        build_ok(project)
        assert load_photos(project)["cover"] == by_source(project)["2024/older.jpg"]["id"]

    @pytest.mark.parametrize("value", ["2025/nothing.jpg", "../config.json"])
    def test_missing_or_outside_cover_falls_back_with_warning(self, project, value):
        self._setup(project, value)
        r = build_ok(project)
        doc = load_photos(project)
        assert doc["cover"] == doc["photos"][0]["id"]
        assert "表紙に指定した写真" in r.stdout and "WARNING" in r.stdout

    def test_cover_pointing_to_duplicate_content(self, project):
        self._setup(project, "dup/copy_of_old.jpg")
        (src(project) / "dup").mkdir()
        shutil.copy2(src(project) / "2025" / "old.jpg", src(project) / "dup" / "copy_of_old.jpg")
        build_ok(project)
        doc = load_photos(project)
        assert doc["count"] == 3  # 重複は1枚にまとまる
        assert doc["cover"] == by_source(project)["2025/old.jpg"]["id"]

    def test_corrupt_cover_falls_back(self, project):
        self._setup(project, "broken.jpg")
        (src(project) / "broken.jpg").write_bytes(b"\xff\xd8broken")
        r = build_ok(project)
        assert "変換できなかった" in r.stdout
        doc = load_photos(project)
        assert doc["cover"] == doc["photos"][0]["id"]

    def test_cover_type_is_validated_in_config(self, project):
        self._setup(project)
        (project / "config.json").write_text(json.dumps({"coverPhoto": 3}), encoding="utf-8")
        r = run_build(project)
        assert r.returncode == 1 and "coverPhoto" in r.stdout

    def test_validator_rejects_cover_not_in_list(self, project):
        self._setup(project)
        build_ok(project)
        doc = load_photos(project)
        doc["cover"] = "0123456789abcdef"
        write_json(project, doc)
        assert any("表紙の写真（cover）" in e for e in V.validate(project).errors)

    def test_cover_reused_from_cache_keeps_choice(self, project):
        self._setup(project, "2025/old.jpg")
        build_ok(project)
        r = build_ok(project)
        assert "再利用 3" in r.stdout
        assert load_photos(project)["cover"] == by_source(project)["2025/old.jpg"]["id"]

    def test_config_example_documents_cover(self):
        cfg = json.loads((PROJECT_ROOT / "config.example.json").read_text(encoding="utf-8"))
        assert "coverPhoto" in cfg and cfg["coverPhoto"] == ""


# ===========================================================================
# 仕様 v9: 小話（v8）の廃止
# ===========================================================================
# 公開されたり試験が失敗したりするため、プロジェクトに残っていてはいけないファイル
RETIRED_FILES = ("tools/admin.py", "tests/test_admin.py", "css/klee-one.css", "fonts/klee-one")


def _without_comments(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


class TestStoriesRetired:
    def test_retired_files_are_gone(self):
        """管理ページと、小話用の全文字版フォントが残っていない（フォントは残っていると公開され続ける）。"""
        left = [rel for rel in RETIRED_FILES if (PROJECT_ROOT / rel).exists()]
        assert left == [], f"v9 で廃止したファイルが残っています。git rm -r で削除してください: {left}"

    def test_site_no_longer_shows_stories_or_loads_the_full_font(self):
        html = _without_comments((PROJECT_ROOT / "index.html").read_text(encoding="utf-8"))
        js = _without_comments((PROJECT_ROOT / "js" / "app.js").read_text(encoding="utf-8"))
        css = _without_comments((PROJECT_ROOT / "css" / "style.css").read_text(encoding="utf-8"))
        assert "viewer-story" not in html + js + css and "tile-story" not in js + css
        assert not re.search(r"\bstory\b", js) and "小話" not in js
        assert "klee-one.css" not in html + js + css and "fonts/klee-one/" not in css
        assert '"Klee One",' not in css  # 文字の並びは「手書き風（画面の文言だけ）→ 端末の書体」
        assert 'aria-describedby="viewer-caption"' in html

    def test_old_admin_login_in_config_does_not_stop_the_build(self, project):
        """v8 で config.json に書いた管理ページのログイン情報（admin）が残っていても動き、消してよいと知らせる。"""
        make_image(src(project) / "a.jpg", exif_dt="2025:05:05 05:05:05")
        old = {"siteTitle": "Daily", "admin": {"user": "someone", "passwordHash": "pbkdf2_sha256$600000$c2FsdA==$aGFzaA=="}}
        (project / "config.json").write_text(json.dumps(old), encoding="utf-8")
        r = build_ok(project)
        assert "admin は使われなくなりました" in r.stdout and "未知の設定キー" not in r.stdout
        assert "passwordHash" not in (project / "data" / "photos.json").read_text(encoding="utf-8")
        cfg, _used, warnings = C.load_config(project)
        assert "admin" not in cfg and len(warnings) == 1
        assert run_validate(project).returncode == 0

    def test_leftover_stories_file_is_ignored(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2025:05:05 05:05:05")
        build_ok(project)
        pid = load_photos(project)["photos"][0]["id"]
        (project / "stories.json").write_text(
            json.dumps({"version": 1, "stories": {pid: {"text": "海へ行った日。"}}}, ensure_ascii=False), encoding="utf-8")
        build_ok(project)
        assert "海へ行った日" not in (project / "data" / "photos.json").read_text(encoding="utf-8")
        assert run_validate(project).returncode == 0

    def test_v8_data_with_a_story_is_rebuilt_without_it(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2025:05:05 05:05:05")
        build_ok(project)
        doc = load_photos(project)
        doc["specId"], doc["specVersion"] = "PV-SPEC-2026-10-v8", "8.0"
        doc["photos"][0]["story"] = "海へ行った日。"
        write_json(project, doc)
        assert any("story は使われなくなりました" in e for e in V.validate(project).errors)  # このままでは公開しない
        build_ok(project)
        doc = load_photos(project)
        assert doc["specId"] == C.SPEC_ID and "story" not in doc["photos"][0]
        assert run_validate(project).returncode == 0


# ===========================================================================
# 仕様 v10: 動く一覧の中間画像（mids/、元の縦横比・長辺最大800px）
# ===========================================================================
class TestMidImages:
    def test_mid_keeps_aspect_ratio_and_long_side_800(self, project):
        s = src(project)
        cases = {
            "landscape.jpg": ((3000, 2000), (800, 533)),
            "portrait.jpg": ((1500, 2250), (533, 800)),
            "panorama.jpg": ((6000, 1200), (800, 160)),
            "small.jpg": ((600, 400), (600, 400)),  # 小さい写真は拡大しない
        }
        for i, (name, (size, _)) in enumerate(cases.items()):
            make_image(s / name, size, exif_dt=f"2025:06:0{i + 1} 12:00:00", gps=True)
        build_ok(project)
        m = by_source(project)
        for name, (_size, expected) in cases.items():
            e = m[name]
            assert e["mid"] == f"mids/{e['year']}/{e['id']}.webp"
            with Image.open(project / e["mid"]) as im:
                assert im.size == expected and im.format == "WEBP", name
            assert V.webp_metadata_problems(project / e["mid"]) == []  # GPS などを残さない（S-08）
        assert run_validate(project).returncode == 0

    def test_mid_follows_exif_orientation(self, project):
        img = Image.new("RGB", (1200, 800), RED)
        img.paste(BLUE, (600, 0, 1200, 800))
        from pvtest_helpers import exif_bytes

        img.save(src(project) / "rotated.jpg", "JPEG", exif=exif_bytes("2024:05:01 10:00:00", orientation=6), quality=95)
        build_ok(project)
        e = by_source(project)["rotated.jpg"]
        with Image.open(project / e["mid"]) as im:
            assert im.size == (533, 800)
            rgb = im.convert("RGB")
            assert near(rgb.getpixel((266, 60)), RED) and near(rgb.getpixel((266, 740)), BLUE)

    def test_mid_is_reused_and_rebuilt_when_missing(self, project):
        s = src(project)
        make_image(s / "a.jpg", (1600, 1200), exif_dt="2025:01:01 10:00:00")
        make_image(s / "b.jpg", (1200, 1600), exif_dt="2025:02:01 10:00:00")
        build_ok(project)
        assert "新規変換 0 / 再利用 2" in build_ok(project).stdout
        lost = by_source(project)["a.jpg"]["mid"]
        (project / lost).unlink()
        r = build_ok(project)
        assert "新規変換 1 / 再利用 1" in r.stdout and (project / lost).is_file()
        assert run_validate(project).returncode == 0

    def test_upgrade_from_v9_creates_mids_for_every_photo(self, project):
        """v9 までの公開データ（mids/ も mid もない）から生成し直すと、全写真の中間画像ができる。"""
        s = src(project)
        for i in range(3):
            make_image(s / f"p{i}.jpg", (1400, 1000), exif_dt=f"2025:0{i + 1}:01 10:00:00")
        build_ok(project)
        doc = load_photos(project)
        for ph in doc["photos"]:
            ph.pop("mid")
        doc["specId"], doc["specVersion"] = "PV-SPEC-2026-10-v9", "9.0"
        write_json(project, doc)
        shutil.rmtree(project / "mids")
        man_path = project / ".build" / "manifest.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        man["settingsKey"] = "v9-settings"
        for ent in man["entries"].values():
            ent.pop("mid")
        man_path.write_text(json.dumps(man), encoding="utf-8")
        r = build_ok(project)
        assert "新規変換 3 / 再利用 0" in r.stdout
        doc = load_photos(project)
        assert doc["specId"] == C.SPEC_ID and all((project / ph["mid"]).is_file() for ph in doc["photos"])
        assert (project / "mids" / ".gitkeep").is_file()
        assert run_validate(project).returncode == 0

    def test_validator_accepts_data_without_mid(self, project):
        """中間画像は任意（旧データの移行など）。画面は詳細画像で代わりに表示する。"""
        make_image(src(project) / "a.jpg", (1000, 800), exif_dt="2025:03:01 10:00:00")
        build_ok(project)
        doc = load_photos(project)
        doc["photos"][0].pop("mid")
        write_json(project, doc)
        shutil.rmtree(project / "mids")
        assert validate_errors(project) == []

    def test_source_folder_inside_mids_is_rejected(self, project):
        (project / "mids" / "sub").mkdir(parents=True, exist_ok=True)
        (project / "config.json").write_text(json.dumps({"sourceDir": "mids/sub"}), encoding="utf-8")
        r = run_build(project)
        assert r.returncode == 1 and "出力先（mids/）" in r.stdout

    def test_publish_script_includes_mids(self):
        text = (PROJECT_ROOT / "tools" / "publish.ps1").read_text(encoding="utf-8-sig")
        assert "'mids'" in text and "$f -like 'mids/*'" in text


# ===========================================================================
# 仕様 v18: アクセス解析（GoatCounter）。config.json のコードから受付先を photos.json に書く。API キーは公開しない
# ===========================================================================
TOKEN = "gc-secret-token-0123456789abcdef"


def write_config(project: Path, **kw) -> None:
    cfg = json.loads((project / "config.example.json").read_text(encoding="utf-8"))
    cfg.update(kw)
    (project / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


class TestAnalyticsV18:
    @pytest.mark.parametrize("value", ["daily-photos", "https://daily-photos.goatcounter.com", "https://Daily-Photos.goatcounter.com/count", " daily-photos "])
    def test_code_becomes_the_count_endpoint(self, project, value):
        make_image(src(project) / "a.jpg", exif_dt="2026:01:01 10:00:00")
        write_config(project, goatcounter=value, goatcounterToken=TOKEN)
        build_ok(project, "--config", "config.json")
        doc = load_photos(project)
        assert doc["analytics"] == {"goatcounter": "https://daily-photos.goatcounter.com/count"}
        assert run_validate(project, "--config", "config.json").returncode == 0

    def test_no_code_means_no_counting(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2026:01:01 10:00:00")
        write_config(project)
        build_ok(project, "--config", "config.json")
        assert "analytics" not in load_photos(project)

    @pytest.mark.parametrize("value", ["Daily Photos", "-abc", "abc_def", 123, "https://example.com/count"])
    def test_bad_code_stops_the_build(self, project, value):
        make_image(src(project) / "a.jpg", exif_dt="2026:01:01 10:00:00")
        write_config(project, goatcounter=value)
        r = run_build(project, "--config", "config.json")
        assert r.returncode == 1 and "goatcounter" in r.stdout, r.stdout

    def test_api_key_never_reaches_public_files(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2026:01:01 10:00:00")
        make_image(src(project) / "b.jpg", exif_dt="2026:01:02 10:00:00")
        write_config(project, goatcounter="daily-photos", goatcounterToken=TOKEN)
        build_ok(project, "--config", "config.json")
        for d in ("data", "photos", "thumbs", "mids", "js", "css", "fonts"):
            for f in (project / d).rglob("*"):
                if f.is_file():
                    assert TOKEN.encode() not in f.read_bytes(), f
        assert TOKEN not in (project / "index.html").read_text(encoding="utf-8")
        # 万一 photos.json に入っていたら、検証で止まる
        doc = load_photos(project)
        doc["note"] = TOKEN
        write_json(project, doc)
        r = run_validate(project, "--config", "config.json")
        assert r.returncode == 1 and "API キー" in r.stdout

    @pytest.mark.parametrize("an", [
        {"goatcounter": "https://evil.example.com/count"},
        {"goatcounter": "http://daily.goatcounter.com/count"},
        {"goatcounter": "https://daily.goatcounter.com/count", "token": "x"},
        "https://daily.goatcounter.com/count",
    ])
    def test_validator_rejects_other_analytics(self, project, an):
        make_image(src(project) / "a.jpg", exif_dt="2026:01:01 10:00:00")
        build_ok(project)
        doc = load_photos(project)
        doc["analytics"] = an
        write_json(project, doc)
        assert any("analytics" in e for e in validate_errors(project))

    def test_settings_change_does_not_reconvert_images(self, project):
        make_image(src(project) / "a.jpg", exif_dt="2026:01:01 10:00:00")
        write_config(project)
        build_ok(project, "--config", "config.json")
        before = {k: v for k, v in output_snapshot(project).items() if not k.startswith("data/")}
        write_config(project, goatcounter="daily-photos", goatcounterToken=TOKEN)
        r = build_ok(project, "--config", "config.json")
        assert "新規変換 0" in r.stdout
        assert {k: v for k, v in output_snapshot(project).items() if not k.startswith("data/")} == before

    def test_dashboard_folder_is_never_published(self):
        ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert re.search(r"^dashboard/$", ignore, re.M)
        ps1 = (PROJECT_ROOT / "tools" / "publish.ps1").read_text(encoding="utf-8-sig")
        published = re.search(r"\$PublishPaths = @\(([^)]*)\)", ps1).group(1)
        assert "dashboard" not in published and "config" not in published and "logs" not in published
        assert "Update-Dashboard" in ps1
