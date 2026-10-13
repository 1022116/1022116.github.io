# 写真ビューア（photo-viewer）

同期仕様ID / 版: **PV-SPEC-2026-10-v4 / version 4.0**

PC に取り込んだ Amazon Photos 由来の写真から、スマートフォンで閲覧できる静的な写真ビューアを生成し、GitHub Pages で公開します。
操作の流れは別冊「ユーザー向けセットアップ・運用手順書」（同じ同期仕様ID・版）に従ってください。本 README は手順書と同じコマンドを使い、各コマンドの動作と仕様を補足します。

> **重要:** GitHub Pages は誰でも閲覧できる公開 Web サイトです。URL を知っていれば誰でも見られます。
> `noindex` で検索エンジンへの登録を抑止していますが、アクセス制限ではありません。人に見せたくない写真は置かないでください。

## 手順書との対応

| 手順書 | 内容 | README |
|---|---|---|
| 3章 | 初回セットアップ（Python 環境・設定ファイル） | [初回セットアップ](#初回セットアップ) |
| 4章 | 写真を準備する | [写真を置く](#写真を置く) |
| 5章 | 生成と検証 | [生成と検証](#生成と検証) |
| 6章 | ローカル表示 | [ローカル表示](#ローカル表示) |
| 7章 | GitHub へ公開 | [GitHub へ公開](#github-へ公開) |
| 8章 | スマートフォン受入確認 | [画面の動作](#画面の動作) |
| 9章 | 写真追加と更新 | [写真追加と更新](#写真追加と更新publishps1) |
| 10章 | 自動更新 | [自動更新](#自動更新タスク-スケジューラー) |

## 共通仕様（S-01～S-10）

| ID | 仕様 | 実装 |
|---|---|---|
| S-01 | 年は1月1日～12月31日 | `year` = `date`（Asia/Tokyo）の西暦年。4月始まりの年度（fiscalYear）は使わない |
| S-02 | 新しい撮影日時順・同日時は相対パス昇順 | 生成時に並べ替え、画面側は JSON の順序を保つ安定ソート |
| S-03 | 480×480 WebP サムネイル | EXIF の回転を補正してから中央を正方形に切り抜き |
| S-04 | 詳細画像は長辺最大1920px | 元の縦横比を維持。1920px 以下の写真は拡大しない |
| S-05 | Instagram 風 3列一覧 | スマホ幅で3列・隙間2px（広い画面でも3列、最大幅 935px） |
| S-06 | 年タブ自動生成 | 「すべて」＋写真のある年を新しい順に |
| S-07 | 全画面スライド | 左右スワイプ・前後ボタン・閉じる・Escape・矢印キー |
| S-08 | 公開画像の EXIF 削除 | GPS を含む EXIF・XMP・ICC を書き込まない（色は sRGB に変換） |
| S-09 | 原本は Git 対象外 | `.gitignore` で `source_photos/` を除外。publish.ps1 でも確認 |
| S-10 | 検証成功後だけ公開 | 一時フォルダーで生成→検証→合格時だけ置換。失敗時は既存の正常版を維持 |

## フォルダー構成

```
photo-viewer/
  index.html            画面の骨格（読込中表示・全画面表示）
  css/style.css         3列一覧・年タブ・全画面・レスポンシブ・reduced-motion
  js/app.js             データ取得・安定ソート・タブ・スライド・スワイプ・状態保存
  data/photos.json      一覧データ（生成物）
  photos/<年>/*.webp    詳細画像（生成物）
  thumbs/<年>/*.webp    サムネイル（生成物）
  source_photos/        原本を置く場所（Git 対象外。空の .gitkeep だけ管理）
  tools/build_gallery.py   生成
  tools/validate_build.py  出力一式と設定ファイルの検証
  tools/publish.ps1        生成→検証→試験→安全確認→commit/push
  tools/pv_common.py       共通定義（仕様ID・サイズ・日時・設定・ロック）
  tests/                   試験（test_build.py / test_publish.py / test_ui.py）
  config.example.json   設定の見本（config.json にコピーして使う）
  requirements.txt      必要な Python パッケージ
  pytest.ini            「python -m pytest -q」で tests/ を実行する設定
  .gitignore / .gitattributes（改行コードの統一） / .nojekyll
```

`.build/`（生成作業用）、`logs/`（ログ）、`.locks/`（排他ロック）、`.venv/`、`config.json` は自動で作られ、Git の対象外です。

## 必要なもの

- Windows 11 PC（publish.ps1 は Windows PowerShell 5.1 / PowerShell 7 で動作）
- Python 3.11 以上
- Git
- GitHub アカウント

## 初回セットアップ

例として `C:\PhotoViewer\photo-viewer` に配置した場合です（手順書 3.2～3.4）。

```powershell
cd C:\PhotoViewer\photo-viewer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item config.example.json config.json
```

- `Activate.ps1` で「このシステムではスクリプトの実行が無効になっている」と表示された場合は、その PowerShell ウィンドウだけ許可してから再実行します:
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
- `pillow-heif` をインストールできない場合は requirements.txt からその行を削除してください（HEIC はスキップされ、ログに記録されます）。
- 以降のコマンドは、仮想環境を有効にした（プロンプトの先頭に `(.venv)` が出ている）状態で実行します。

### 設定ファイル（config.json）

| キー | 既定値 | 説明 |
|---|---|---|
| `sourceDir` | `source_photos` | 原本フォルダー。手順書どおり `source_photos` のままにします（絶対パスも可。出力フォルダーの中は不可） |
| `thumbSize` | `480` | **固定値（S-03）。変更しないでください**（480 以外にすると生成・検証がエラーで停止） |
| `detailMaxEdge` | `1920` | **固定値（S-04）。変更しないでください**（1920 以外にすると生成・検証がエラーで停止） |
| `allowedExtensions` | `.jpg .jpeg .png .webp .heic .heif` | 対象にする拡張子。それ以外（動画など）はスキップしてログに記録 |
| `siteTitle` | `写真` | 画面上部に表示する名前 |
| `thumbQuality` / `detailQuality` | 80 / 85 | WebP の画質（1～100） |
| `shrinkGuardRatio` | 0.5 | 公開中の写真数（10枚以上）に対して、この割合未満へ減る場合は停止 |
| `jobs` | 0 | 並列変換数（0 = 自動、最大4） |

`config.json` は `.gitignore` 対象のため GitHub へは送られません（確認: `git check-ignore -v config.json` で `.gitignore` の行が表示されれば除外されています）。

## 写真を置く

原本を `source_photos\` にコピーします（サブフォルダーも再帰的に読み込みます）。原本は読み取るだけで、削除・上書き・移動はしません。

- `source_photos` が Git の対象外であることの確認: `git check-ignore -v source_photos\写真のファイル名.jpg`
- 手順書 9章のとおり、コピー完了（ファイルサイズ・更新時刻が変化しないこと）を確認してから生成します。コピー途中のファイルを読み込むと、壊れた画像としてスキップされるか、一部が欠けた画像になることがあります（コピー完了後に生成し直すと正しい画像に置き換わります）。

## 生成と検証

手順書 5章のコマンドです。

```powershell
python tools/build_gallery.py --config config.json
python tools/validate_build.py --config config.json
python -m pytest -q
```

**build_gallery.py**
- `photos\<年>\` に詳細画像、`thumbs\<年>\` に 480×480 のサムネイル、`data\photos.json` を作ります。
- 撮影日時は次の順で決めます（`dateSource` に記録）。
  1. EXIF の DateTimeOriginal（`exif`）。タイムゾーン（OffsetTimeOriginal）があれば Asia/Tokyo に換算し、なければ Asia/Tokyo の時刻とみなします。
  2. ファイル名の日時（`filename`）。例: `2026-09-05_14-45-52_000.jpeg`（Amazon Photos）、`IMG_20260905_144552.jpg`、`Screenshot_20260905-144552.png`。
     ファイル名の最初に現れる「日付＋時刻（秒まで）」を Asia/Tokyo の時刻として使います。重複ダウンロード時の `(2026-10-12T16_03_05.577)` のような後置きは使いません。
  3. ファイルの更新日時（`mtime`）。コピーやダウンロードをした時刻になっていることが多いため、一覧では「（ファイル更新日時）」と表示します。
- 内容が同じ写真（SHA-256 が一致）は1枚にまとめます（相対パス順で先のものを採用）。
- 破損した画像・対象外の拡張子・pillow-heif 未導入時の HEIC はスキップし、件数をログに出します（`--strict` で1件でもあれば失敗）。
- 2回目以降は、変わっていない写真の変換結果を再利用します。ログは `logs\build_*.log`（直近30件）。

**validate_build.py**
JSON 構文、必須キー、year と date の一致（12月31日と1月1日が別の年になること）、並び順、ID 重複、参照先の存在、画像の実読込、サイズ、メタデータの有無、参照されていない公開画像、外部 URL 依存、設定ファイルの仕様固定値を確認します。

**pytest**
試験画像を一時フォルダーに作って生成・検証・公開処理・画面を試験します（実際の写真と出力には触れません）。

検証や試験が1つでも失敗した場合は公開へ進まないでください（publish.ps1 は自動で停止します）。

## ローカル表示

```powershell
python -m http.server 8000
```

PC のブラウザーで `http://localhost:8000/` を開きます。`index.html` をダブルクリックで開くとデータを読み込めないため使いません。終了は `Ctrl + C`。

## GitHub へ公開

手順書 7章のコマンドです（初回のみ）。

```powershell
git init
git branch -M main
git add .
git status
git commit -m "Initial photo viewer"
git remote add origin https://github.com/YOUR_NAME/photo-viewer.git
git push -u origin main
```

- `git status` に `source_photos/.gitkeep`・`photos/.gitkeep`・`thumbs/.gitkeep`（中身が空のフォルダー保持用ファイル）が出るのは正常です。
  それ以外の `source_photos/` のファイル、`config.json`、`logs/`、`.build/`、`.venv/`、`.jpg` などの元写真が出ていれば commit しないでください。
- GitHub の Settings → Pages で Source を「Deploy from a branch」、ブランチを `main`・フォルダーを `/(root)` にして Save します。
- URL は `https://YOUR_NAME.github.io/photo-viewer/` です。ページ内のパスはすべて相対 URL のため、このサブパスでそのまま動きます。

## 写真追加と更新（publish.ps1）

手順書 9章のコマンドです。

```powershell
PowerShell -ExecutionPolicy Bypass -File .\tools\publish.ps1
```

次の順に実行し、どこかで失敗した場合は commit / push を行いません。

1. 排他ロック 2. 仮想環境（`.venv`）の確認 3. Git の事前確認 4. 生成 5. 検証 6. 試験（pytest） 7. Git 対象の安全確認 8. 差分がある時だけ commit / push

**テスト成功時だけ push されたことの確認:** `logs\publish_日時.log` を開き、`6/8 試験に合格しました` の後に `8/8 push しました` があることを確認します。
変更がなければ `8/8 公開内容に変更はありません` と記録され、commit / push は行いません。

| オプション | 動作 |
|---|---|
| `-DryRun` | commit / push せず、公開予定のファイルを表示 |
| `-NoPush` | commit まで（公開準備）。あとで `git push` すると公開 |
| `-Unattended` | 無人実行用。GitHub の認証入力を待たずに失敗として終了し、ログに記録 |
| `-AllowEmpty` | 原本0件のとき、公開中の写真をすべて消すことを許可 |
| `-AllowShrink` | 写真数の大幅減少を許可 |

- push に失敗した場合（通信断・認証切れなど）、commit は PC 内に残り、**次回の publish.ps1 実行時に自動で再送**します。
- publish.ps1 が commit するのは `index.html css js data photos thumbs .nojekyll` だけです。tools・tests・README などを変更した場合は通常どおり自分で commit してください。

## 自動更新（タスク スケジューラー）

手順書 10章のとおり、手動更新が安定してから登録します。

| 項目 | 設定値 |
|---|---|
| 操作 | プログラムの開始 |
| プログラム/スクリプト | `powershell.exe` |
| 引数の追加 | `-NoProfile -ExecutionPolicy Bypass -File "C:\PhotoViewer\photo-viewer\tools\publish.ps1" -Unattended` |
| 開始（オプション） | `C:\PhotoViewer\photo-viewer` |
| 設定タブ「タスクが既に実行中の場合に適用される規則」 | 新しいインスタンスを開始しない |

- publish.ps1 自体も排他ロックを持つため、手動実行と重なった場合は後から起動した方が終了コード 75 で何もせず終了します。
- 結果はタスクの「前回の実行結果」（終了コード）と `logs\publish_*.log` で確認します。通信断・GitHub 認証切れは「push に失敗しました」、PC のスリープで中断された場合はログが途中で終わります（次回実行時に未送信分を再送）。
- 自動化後も、公開対象の写真を定期的に目視確認してください。

## 画面の動作

- 年タブ: 「すべて」と写真のある年（新しい順）。矢印キー・Home・End でも切り替え可能。
- 一覧: 3列・隙間2px（縦・横どちらの向きでも3列）。60枚ずつ追加表示し、サムネイルは画面に近づいてから読み込みます。
- 全画面: 写真をタップ。左右スワイプ／「‹ ›」ボタン／矢印キーで前後、×ボタン／Escape で閉じます。端では止まります（ループしません）。
- 読込中・写真0件・JSON の読込失敗（再読み込みボタン付き）・旧形式データ・画像の読込失敗をそれぞれ表示します。
- 想定ブラウザー: iOS 15.4 以降の Safari、最新の Android Chrome、PC の最新ブラウザー（実機での確認は手順書 8章）。

### 状態保存と再読込の仕様

| 項目 | 保存先 | 再読込・戻る操作をしたとき |
|---|---|---|
| 選択中の年タブ | localStorage `pv:selectedYear` | 復元（再訪問時も）。その年が無くなっていれば「すべて」に戻し、保存値も置き換える |
| スクロール位置 | sessionStorage `pv:scroll` | 同じタブでの再読込・戻る操作で復元。新しいタブや再訪問では先頭から |
| 全画面表示中の写真 | 保存しない | 一覧（保存した年・位置）に戻る |

- スクロール位置は「画面上端の写真＋ずれ」で保存するため、写真が追加されても同じ写真の付近に戻ります。
- 年タブを切り替えると、その年の先頭から表示します（保存中のタイマーは取り消し、古い位置を新しい年として保存しません）。
- `data/photos.json` は毎回サーバーへ更新確認して取得します。公開後に再読込すると新しい写真が表示されます（GitHub Pages の反映には数分かかることがあります）。

## 安全装置

- **排他:** 生成と公開は OS のファイルロックで1つずつしか動きません。実行中に起動すると終了コード 75 で停止します。異常終了してもロックは自動で解放されます。
- **原子的な置換:** `.build\` 内で全量を作り、検証に合格したときだけ `photos\`・`thumbs\`・`data\photos.json` を入れ替えます。入れ替え中の失敗は自動で元に戻し、強制終了した場合も次回起動時に復旧します。
- **0件・大幅減少:** 原本フォルダーが見つからない／空のときに公開中の写真を消さないよう停止します（終了コード 2）。意図的な場合は `--allow-empty` / `--allow-shrink`（publish.ps1 では `-AllowEmpty` / `-AllowShrink`）。
- **公開前チェック:** 原本・元形式の画像・動画・鍵ファイル・25MB 超のファイル・PC 内のパスを含む JSON が commit 対象にあれば停止します。

出力の初期化（公開データの一括削除）機能はありません。写真を減らす場合は原本を減らして再生成します。

## 旧形式（fiscalYear）からの移行

- `build_gallery.py` を実行すると、旧形式の `data/photos.json` を検出してログに記録し、原本から `year` 形式で作り直します。
- 原本がない場合は `python tools/build_gallery.py --migrate-legacy` で、各写真の `date` から1月1日始まりの `year` を求めて変換します。
  `date` がない、または旧画像が新仕様（480×480 WebP など）を満たさない場合は、ファイルを変更せずに停止します（終了コード 3）。
- 画面側は旧形式のデータを受け取ると、表示を停止して再生成を促すメッセージを出します。
- `validate_build.py` は `fiscalYear` を含むデータを不合格にします。

## 仕様変更履歴

| 同期仕様ID / 版 | 日付 | 変更 |
|---|---|---|
| PV-SPEC-2026-10-v4 / 4.0 | 2026-10-13 | 撮影日時の決め方を「EXIF DateTimeOriginal → 更新日時」から「EXIF DateTimeOriginal → ファイル名の日時 → 更新日時」に変更。`dateSource` に `filename` を追加。理由: Amazon Photos から取得した写真の一部に EXIF がなく、更新日時（コピーした時刻）で並んでしまうため。S-01～S-10 の文言は変更なし |
| PV-SPEC-2026-10-v3 / 3.0 | 2026-10-02 | 初版（実装指示書・ユーザー向け手順書） |

v4 に合わせて、2つの文書を次のように更新してください（手順書のルール「仕様変更時は両文書の同期仕様IDと版番号を同時に更新」）。

- 両文書: 同期仕様ID `PV-SPEC-2026-10-v3` → `PV-SPEC-2026-10-v4`、版 `3.0` → `4.0`（表紙・各ページのヘッダー・同期チェック）
- ユーザー向け手順書 3.2: `PV-SPEC-2026-10-v3 / version 3.0` → `PV-SPEC-2026-10-v4 / version 4.0`
- 実装指示書「絶対仕様」: 「EXIF DateTimeOriginalを優先し、なければ更新日時。」→「EXIF DateTimeOriginalを優先し、なければファイル名の日時、それもなければ更新日時。」
- 実装指示書「photos.json」: dateSource の値として exif / filename / mtime（旧データ移行時は legacy）を明記

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功（publish では「変更なし」も含む） |
| 1 | 失敗（既存の公開データは変更しない） |
| 2 | 安全装置で停止（0件・大幅減少） |
| 3 | 旧形式を移行できず停止 |
| 75 | 別の生成・公開が実行中のため停止 |

## 試験の内訳

`python -m pytest -q` で次を実行します（`pytest.ini` により `tests\` だけが対象）。

- `tests/test_build.py`: 生成・検証（正常・境界・異常・移行・競合・書込不可・手順書コマンド）
- `tests/test_publish.py`: publish.ps1（一時 Git リポジトリで commit/push・停止条件・排他・push 失敗後の再送）。PowerShell がなければスキップ
- `tests/test_ui.py`: ブラウザー試験（一覧・年タブ・スライド・連打・保存・戻る操作・縦横回転・低速回線・エラー表示・アクセシビリティ）。Playwright がなければスキップ
  使う場合: `pip install playwright` → `python -m playwright install chromium`

## 前提・未確認事項

- iPhone Safari・Android Chrome の実機確認は、手順書 8章に従って対象端末で行ってください（自動試験は Chromium によるスマホ画面のエミュレーションです）。
- 写真総数、動画対応、家族共有、認証要否は未確定です。動画は現在スキップされます。
- Amazon Photos から PC への取り込み方法は環境によって異なります（手順書 4章）。
- EXIF もファイル名の日時もない写真は、ファイルの更新日時（＝コピー・ダウンロードした日時の可能性）で分類されます。
