#!/usr/bin/env python3
"""画面用フォント（DotGothic16）の小さな版を作る開発用スクリプト（同期仕様ID PV-SPEC-2026-10-v12 / 版 12.0）

画面に出る文言（js/app.js の文字列と index.html の本文）・英数字・かな・全角記号だけを取り出して
fonts/dotgothic16-subset.woff2 を作り、css/style.css に書く unicode-range を表示する。
画面の文言に新しい漢字を足したときに実行する（試験 test_font_covers_every_character_on_screen が知らせる）。

使い方（fontTools と brotli が必要: pip install fonttools brotli）:
  python tools/make_font_subset.py DotGothic16-Regular.ttf
元のフォント: https://github.com/fontworks-fonts/DotGothic16 （SIL Open Font License 1.1。fonts/OFL.txt）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fonts" / "dotgothic16-subset.woff2"
EXTRA = "年月日枚–…・「」、。（）：ー゛゜"


def ui_texts() -> str:
    js = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    html = re.sub(r"<!--.*?-->", "", (ROOT / "index.html").read_text(encoding="utf-8"), flags=re.S)
    lits = re.findall(r"'((?:[^'\\\n]|\\.)*)'", js) + re.findall(r">([^<]+)<", html)
    return "".join(lits)


def wanted() -> set:
    need = set(ui_texts()) | set(EXTRA) | {chr(c) for c in range(0x20, 0x7F)}
    need |= {chr(c) for c in range(0x3041, 0x3097)} | {chr(c) for c in range(0x30A1, 0x30FB)}
    need |= {chr(c) for c in range(0xFF01, 0xFF5F)}
    return {c for c in need if c.isprintable()}


def unicode_range(cps: list) -> str:
    out, start, prev = [], None, None
    for c in cps:
        if start is None:
            start = prev = c
        elif c == prev + 1:
            prev = c
        else:
            out.append((start, prev))
            start = prev = c
    if start is not None:
        out.append((start, prev))
    return ",".join(f"U+{a:X}" if a == b else f"U+{a:X}-{b:X}" for a, b in out)


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1
    from fontTools import subset
    from fontTools.ttLib import TTFont

    src = Path(argv[1])
    cmap = TTFont(src).getBestCmap()
    need = wanted()
    missing = sorted(c for c in need if ord(c) not in cmap)
    if missing:
        print("フォントにない文字（端末の書体で表示されます）:", "".join(missing))
    cps = sorted(ord(c) for c in need if ord(c) in cmap)
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = []
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.drop_tables += ["vhea", "vmtx", "VORG"]
    font = TTFont(src)
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=cps)
    sub.subset(font)
    font.flavor = "woff2"
    font.save(OUT)
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size} バイト（{len(cps)} 文字）")
    print("css/style.css の @font-face の unicode-range:")
    print(unicode_range(cps))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
