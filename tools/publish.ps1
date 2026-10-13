<#
.SYNOPSIS
  写真ビューアを生成・検証・試験してから GitHub Pages へ公開します。
  同期仕様ID PV-SPEC-2026-10-v4 / 版 4.0

.DESCRIPTION
  次の順に実行し、どこかで失敗したら commit / push は行いません（S-10）。
    1. 排他ロック（公開・生成の多重実行を防止）
    2. 仮想環境の確認（.venv と必要パッケージ）
    3. Git の事前確認（リポジトリ・ブランチ・ステージ済み変更なし）
    4. build   : tools/build_gallery.py
    5. validate: tools/validate_build.py
    6. pytest  : tests/
    7. Git 対象の安全確認（原本・秘密情報・未許可ファイルを含まないこと）
    8. 差分がある時だけ commit / push

.PARAMETER DryRun
  commit / push をせず、公開される予定のファイルだけを表示します。

.PARAMETER NoPush
  commit までで止めます（公開準備）。後で git push すると公開されます。

.PARAMETER AllowEmpty
  原本が0件のとき、公開中の写真をすべて削除することを許可します。

.PARAMETER AllowShrink
  写真数が大きく減る場合（既定: 半分未満）の停止を解除します。

.PARAMETER Unattended
  タスク スケジューラーなど無人で実行する場合に指定します。GitHub の認証入力を待たずに失敗として終了し、
  ログに記録します（認証切れ・通信断の確認用）。push できなかった commit は次回の実行時に再送します。

.PARAMETER Python
  使用する Python を指定します（既定: プロジェクトの .venv）。仮想環境の Python である必要があります。

.EXAMPLE
  PowerShell -ExecutionPolicy Bypass -File .\tools\publish.ps1

.EXAMPLE
  PowerShell -NoProfile -ExecutionPolicy Bypass -File C:\PhotoViewer\photo-viewer\tools\publish.ps1 -Unattended
  （タスク スケジューラーに登録する場合）

.NOTES
  終了コード: 0=公開完了または変更なし / 1=失敗 / 2=安全装置で停止 / 75=実行中のため停止
  Windows PowerShell 5.1 で日本語を正しく読めるよう、このファイルは BOM 付き UTF-8 で保存しています。
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoPush,
    [switch]$AllowEmpty,
    [switch]$AllowShrink,
    [switch]$Unattended,
    [string]$Python = '',
    [string]$Remote = 'origin'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$SpecId = 'PV-SPEC-2026-10-v4'
$SpecVersion = '4.0'
$ExitFailed = 1
$ExitGuard = 2
$ExitBusy = 75

# 公開対象（これ以外のファイルは publish.ps1 では commit しない）
$PublishPaths = @('index.html', 'css', 'js', 'data', 'photos', 'thumbs', '.nojekyll')
$ForbiddenExtensions = @('.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif', '.tif', '.tiff', '.bmp', '.dng', '.raw',
    '.cr2', '.nef', '.arw', '.mov', '.mp4', '.m4v', '.avi', '.zip', '.pem', '.key', '.pfx', '.env')
$MaxFileBytes = 25MB

try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if ($Unattended) {
    $env:GIT_TERMINAL_PROMPT = '0'   # 認証の入力待ちで止まらないようにする
    $env:GCM_INTERACTIVE = 'never'   # Git Credential Manager の画面も出さない
}

$Root = Split-Path -Parent $PSScriptRoot
$OnWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
$script:ExitCode = 0
$script:StopCode = $ExitFailed
$script:Committed = $false
$script:PushFailed = $false

$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ('publish_{0}_{1}.log' -f (Get-Date -Format 'yyyyMMdd_HHmmss'), $PID)

function Write-Log {
    param([string]$Level, [string]$Message)
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    $color = 'Gray'
    if ($Level -eq 'ERROR') { $color = 'Red' }
    elseif ($Level -eq 'WARN') { $color = 'Yellow' }
    elseif ($Level -eq 'OK') { $color = 'Green' }
    Write-Host $line -ForegroundColor $color
    try { [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false)) } catch { }
}

function Stop-Publish {
    param([string]$Message, [int]$Code = 1)
    $script:StopCode = $Code
    throw "PV_STOP: $Message"
}

function Invoke-Tool {
    # 外部コマンドを実行し、出力を画面とログへ流す。終了コードを返す。
    param([string]$File, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $File @Arguments 2>&1 | ForEach-Object {
            $s = "$_"
            Write-Host $s
            try { [System.IO.File]::AppendAllText($LogFile, $s + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false)) } catch { }
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-Capture {
    # 外部コマンドを実行して出力行を返す（標準エラーは捨てる）。
    param([string]$File, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $File @Arguments 2>$null
        return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = @($out | ForEach-Object { "$_" }) }
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-Git {
    param([string[]]$Arguments)
    return Invoke-Capture 'git' (@('-C', $Root, '-c', 'core.quotepath=false') + $Arguments)
}

function Undo-Stage {
    $null = Invoke-Git (@('reset', '-q', '--') + $PublishPaths)
}

function Get-PendingPushCount {
    # まだ push していない commit 数。追跡ブランチがなく判定できない場合は -1。
    param([string]$Branch)
    $tracking = "refs/remotes/$Remote/$Branch"
    $r = Invoke-Git @('rev-parse', '--verify', '-q', $tracking)
    if ($r.Code -ne 0) { return -1 }
    $c = Invoke-Git @('rev-list', '--count', "$tracking..HEAD")
    if ($c.Code -ne 0 -or -not $c.Lines) { return -1 }
    return [int]$c.Lines[0]
}

function Push-Branch {
    param([string]$Branch)
    $code = Invoke-Tool 'git' @('-C', $Root, 'push', $Remote, $Branch)
    if ($code -ne 0) {
        $script:PushFailed = $true
        Stop-Publish 'push に失敗しました。commit は PC 内に残っており、次回の publish.ps1 実行時に再送します。通信状態・GitHub の認証、またはリモート側の更新（git pull --rebase）を確認してください。'
    }
    Write-Log 'OK' "8/8 push しました（$Remote/$Branch）。GitHub Pages への反映には数分かかります。"
}

$script:LockStream = $null
$script:Staged = $false

function Invoke-Publish {
    Write-Log 'INFO' "写真ビューア公開 同期仕様ID $SpecId / 版 $SpecVersion"
    Write-Log 'INFO' "プロジェクト: $Root"
    Write-Log 'INFO' "ログ: $LogFile"
    if ($Unattended) { Write-Log 'INFO' '無人実行モード（認証の入力待ちをせずに終了します）' }

    # ---- 1. 排他ロック -------------------------------------------------------------
    $lockDir = Join-Path $Root '.locks'
    New-Item -ItemType Directory -Force -Path $lockDir | Out-Null
    $lockPath = Join-Path $lockDir 'publish.lock'
    try {
        $script:LockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    } catch {
        Stop-Publish '別の公開処理または生成処理が実行中です。終了してから再実行してください。' $ExitBusy
    }
    Write-Log 'OK' '1/8 排他ロックを取得しました'

    # ---- 2. 仮想環境 ---------------------------------------------------------------
    if ($Python) { $py = $Python }
    elseif ($OnWindows) { $py = Join-Path $Root '.venv\Scripts\python.exe' }
    else { $py = Join-Path $Root '.venv/bin/python' }
    if (-not (Test-Path -LiteralPath $py)) {
        Stop-Publish "仮想環境の Python が見つかりません: $py （README の「初回セットアップ」で .venv を作成してください）"
    }
    $r = Invoke-Capture $py @('-c', 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 3)')
    if ($r.Code -ne 0) { Stop-Publish "仮想環境の Python ではありません: $py" }
    $r = Invoke-Capture $py @('-c', 'import PIL, pytest')
    if ($r.Code -ne 0) {
        Stop-Publish '必要なパッケージがありません。仮想環境で pip install -r requirements.txt を実行してください。'
    }
    Write-Log 'OK' "2/8 仮想環境を確認しました: $py"

    # ---- 3. Git 事前確認 ------------------------------------------------------------
    $r = Invoke-Capture 'git' @('--version')
    if ($r.Code -ne 0) { Stop-Publish 'git コマンドが見つかりません。Git をインストールしてください。' }
    $r = Invoke-Git @('rev-parse', '--show-toplevel')
    if ($r.Code -ne 0) { Stop-Publish 'このフォルダーは Git リポジトリではありません（git init / clone が必要です）。' }
    $top = [System.IO.Path]::GetFullPath(($r.Lines[0] -replace '/', [System.IO.Path]::DirectorySeparatorChar)).TrimEnd('\', '/')
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if ($top -ne $rootFull) {
        Stop-Publish "photo-viewer フォルダーがリポジトリの最上位ではありません（リポジトリ: $top）。GitHub Pages はリポジトリ直下を公開します。"
    }
    $r = Invoke-Git @('symbolic-ref', '--short', '-q', 'HEAD')
    if ($r.Code -ne 0 -or -not $r.Lines) { Stop-Publish 'ブランチがチェックアウトされていません（detached HEAD）。' }
    $branch = $r.Lines[0]
    foreach ($marker in @('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD')) {
        $p = (Invoke-Git @('rev-parse', '--git-path', $marker)).Lines[0]
        if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $Root $p }
        if (Test-Path -LiteralPath $p) { Stop-Publish "Git のマージ／リベース処理が途中です（$marker）。完了または中止してから再実行してください。" }
    }
    $pre = Invoke-Git @('diff', '--cached', '--name-only')
    if ($pre.Lines.Count -gt 0) {
        Stop-Publish ("公開処理の前にステージ済みの変更があります: {0} … commit するか git restore --staged で取り消してから再実行してください。" -f (($pre.Lines | Select-Object -First 5) -join ', '))
    }
    if (-not $DryRun -and -not $NoPush) {
        $r = Invoke-Git @('remote', 'get-url', $Remote)
        if ($r.Code -ne 0) { Stop-Publish "リモート '$Remote' が設定されていません（git remote add $Remote <URL>）。" }
    }
    Write-Log 'OK' "3/8 Git を確認しました（ブランチ: $branch）"

    # ---- 4. build -----------------------------------------------------------------
    $buildArgs = @((Join-Path $Root 'tools/build_gallery.py'), '--under-publish')
    if ($AllowEmpty) { $buildArgs += '--allow-empty' }
    if ($AllowShrink) { $buildArgs += '--allow-shrink' }
    $code = Invoke-Tool $py $buildArgs
    if ($code -eq $ExitBusy) { Stop-Publish '別の生成処理が実行中です。' $ExitBusy }
    if ($code -eq $ExitGuard) { Stop-Publish '生成の安全装置で停止しました（上のメッセージを確認してください）。既存の公開データは変更していません。' $ExitGuard }
    if ($code -ne 0) { Stop-Publish "生成に失敗しました（終了コード $code）。既存の公開データは変更していません。" }
    Write-Log 'OK' '4/8 生成が完了しました'

    # ---- 5. validate --------------------------------------------------------------
    $code = Invoke-Tool $py @((Join-Path $Root 'tools/validate_build.py'), '--root', $Root)
    if ($code -ne 0) { Stop-Publish "出力の検証に不合格です（終了コード $code）。" }
    Write-Log 'OK' '5/8 検証に合格しました'

    # ---- 6. pytest ----------------------------------------------------------------
    Push-Location $Root
    try {
        $code = Invoke-Tool $py @('-m', 'pytest', '-q', 'tests')
    } finally {
        Pop-Location
    }
    if ($code -eq 5) { Stop-Publish '試験が見つかりません（tests/ を確認してください）。' }
    if ($code -ne 0) { Stop-Publish "試験に失敗しました（終了コード $code）。" }
    Write-Log 'OK' '6/8 試験に合格しました'

    # ---- 7. Git 対象の安全確認 -------------------------------------------------------
    $r = Invoke-Git @('check-ignore', '-q', '--no-index', 'source_photos/__pv_probe__.jpg')
    if ($r.Code -ne 0) { Stop-Publish '.gitignore で source_photos/ が除外されていません（S-09）。' }
    $tracked = @((Invoke-Git @('ls-files', '--', 'source_photos')).Lines | Where-Object { $_ -and $_ -ne 'source_photos/.gitkeep' })
    if ($tracked.Count -gt 0) {
        Stop-Publish ("原本が Git の管理対象になっています: {0} … git rm --cached で管理対象から外してください（S-09）。" -f (($tracked | Select-Object -First 5) -join ', '))
    }
    $r = Invoke-Git (@('add', '-A', '--') + $PublishPaths)
    if ($r.Code -ne 0) { Stop-Publish 'git add に失敗しました。' }
    $script:Staged = $true
    $files = @((Invoke-Git @('diff', '--cached', '--name-only', '--diff-filter=ACMRT')).Lines | Where-Object { $_ })
    $problems = @()
    foreach ($f in $files) {
        $ext = [System.IO.Path]::GetExtension($f).ToLowerInvariant()
        $name = [System.IO.Path]::GetFileName($f)
        if ($f -like 'source_photos/*') { $problems += "$f （原本フォルダー）" ; continue }
        if ($ForbiddenExtensions -contains $ext) { $problems += "$f （公開禁止の拡張子 $ext）" ; continue }
        if (($f -like 'photos/*' -or $f -like 'thumbs/*') -and $ext -ne '.webp' -and $name -ne '.gitkeep') {
            $problems += "$f （公開画像は .webp のみ）" ; continue
        }
        $full = Join-Path $Root $f
        if ((Test-Path -LiteralPath $full) -and ((Get-Item -LiteralPath $full).Length -gt $MaxFileBytes)) {
            $problems += "$f （25MB を超えるファイル）"
        }
    }
    $json = Invoke-Git @('show', ':data/photos.json')
    $jsonText = $json.Lines -join "`n"
    if ($jsonText -match 'fiscalYear') { $problems += 'data/photos.json （旧形式 fiscalYear を含む）' }
    if ($jsonText -match '[A-Za-z]:\\\\|"/(Users|home)/|source_photos') {
        $problems += 'data/photos.json （PC内のパスを含む）'
    }
    if ($problems.Count -gt 0) {
        Undo-Stage
        $script:Staged = $false
        Stop-Publish ("公開してはいけないファイルが含まれています: {0}" -f ($problems -join ' / '))
    }
    Write-Log 'OK' ("7/8 Git 対象を確認しました（変更 {0} ファイル）" -f $files.Count)

    # ---- 8. commit / push -----------------------------------------------------------
    $r = Invoke-Git @('diff', '--cached', '--quiet')
    if ($r.Code -eq 0) {
        Undo-Stage
        $script:Staged = $false
        $pending = Get-PendingPushCount $branch
        if ($pending -eq 0) {
            Write-Log 'OK' '8/8 公開内容に変更はありません。commit / push は行いません。'
            return
        }
        $what = if ($pending -lt 0) { 'リモートとの差を確認できません' } else { "$pending 件" }
        if ($DryRun -or $NoPush) {
            Write-Log 'WARN' "公開内容に変更はありませんが、まだ push していない commit があります（$what）。"
            Write-Log 'OK' '8/8 DryRun / NoPush のため push は行いませんでした。'
            return
        }
        Write-Log 'WARN' "前回までに push できなかった commit があります（$what）。push します。"
        Push-Branch $branch
        return
    }
    $countOut = Invoke-Capture $py @('-c', "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['count'])", (Join-Path $Root 'data/photos.json'))
    $count = if ($countOut.Lines) { $countOut.Lines[0] } else { '?' }
    if ($DryRun) {
        Write-Log 'INFO' '公開予定のファイル（DryRun のため commit しません）:'
        $files | Select-Object -First 50 | ForEach-Object { Write-Log 'INFO' "  $_" }
        if ($files.Count -gt 50) { Write-Log 'INFO' ("  ほか {0} ファイル" -f ($files.Count - 50)) }
        Undo-Stage
        $script:Staged = $false
        Write-Log 'OK' '8/8 DryRun のため commit / push は行いませんでした。'
        return
    }
    $message = '写真ビューアを更新: {0}枚 ({1})' -f $count, (Get-Date -Format 'yyyy-MM-dd HH:mm')
    $r = Invoke-Git @('commit', '-q', '-m', $message)
    if ($r.Code -ne 0) {
        Stop-Publish 'commit に失敗しました。git config user.name / user.email の設定を確認してください。'
    }
    $script:Staged = $false
    $script:Committed = $true
    $head = (Invoke-Git @('rev-parse', '--short', 'HEAD')).Lines[0]
    Write-Log 'OK' "commit しました: $head $message"
    if ($NoPush) {
        Write-Log 'OK' '8/8 NoPush のため push は行いませんでした（公開準備完了）。git push で公開されます。'
        return
    }
    Push-Branch $branch
}

try {
    Invoke-Publish
} catch {
    $msg = "$($_.Exception.Message)"
    if ($msg.StartsWith('PV_STOP: ')) {
        Write-Log 'ERROR' $msg.Substring(9)
        $script:ExitCode = $script:StopCode
    } else {
        Write-Log 'ERROR' "想定外のエラー: $msg"
        $script:ExitCode = $ExitFailed
    }
    if ($script:Staged) { Undo-Stage }
    if ($script:ExitCode -ne 0 -and -not $script:PushFailed) {
        if ($script:Committed) { Write-Log 'ERROR' 'push は行っていません（commit は PC 内に残っています）。' }
        else { Write-Log 'ERROR' 'commit / push は行っていません。' }
    }
} finally {
    if ($script:LockStream) { $script:LockStream.Dispose() }
}
exit $script:ExitCode
