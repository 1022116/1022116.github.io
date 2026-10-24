/*
 * 写真ビューア（同期仕様ID PV-SPEC-2026-10-v14 / 版 14.0）
 * HTML / CSS / Vanilla JavaScript のみ。外部CDN・ライブラリなし。URLはすべて相対（GitHub Pages のサブパスで動作）。
 *
 * ■ 一覧（v13）
 *   - 年で分けない。全部の写真を新しい順に1つの一覧にし、各写真に目録番号（いちばん古い写真が 01 の通し番号。
 *       「No.」は付けない）と撮影日を添える。v12 までの年のタブ・散らした年の文字・「〜年の〜枚」は廃止。
 *       （photos.json の year は生成側の S-01 として残るが、画面では使わない）
 *   - 撮影年の範囲（例: 2025–2026）だけを小さく表示する。表示できないデータがあるときはその件数も出す。
 *
 * ■ 状態保存と再読込の仕様
 *   - スクロール位置（動かない一覧）… sessionStorage「pv:scroll」に保存。
 *       同じタブでの再読込・戻る操作では復元し、新しいタブ・別の日の再訪問では先頭から表示する。
 *       位置は「画面上端の写真ID＋ずれ(px)」で保存するため、画面幅や写真の追加で多少変わっても同じ写真付近へ戻る。
 *   - 全画面表示中の写真 … 保存しない。再読込すると一覧（保存した位置）に戻る。
 *   - 写真データ … data/photos.json を毎回再検証付き（cache: no-cache）で取得する。
 *   - 保存はスクロール停止後 200ms のタイマーと、ページを離れる時（pagehide / 非表示化）に行う。
 *   - プライベートブラウズ等でストレージを使えない場合は保存せずに動作する。
 *   - v12 までの「pv:selectedYear」（選んだ年）と「pv:drift」（パターン1の位置）は使わない。残っていれば消す。
 *
 * ■ 演出（v8）
 *   - 現像 … 一覧と表紙の写真は「読み込み済み」かつ「画面に入った」ときに is-developed を付け、
 *       白くぼやけた像から色と輪郭が浮かぶように表示する（css/style.css）。視差効果を減らす設定では動かさない。
 *   （v8 のサイト名の書き順は v11 で廃止。サイト名は画面に出さず、読み上げとブラウザーのタブの名前にだけ使う）
 *   （v8 の小話は v9 で廃止。photos.json に story があっても表示しない）
 *
 * ■ パターン1 = 散らばる（data-mode="drift"。v10）
 *   - 写真を、元の縦横比のまま大小ばらばらに終わりのない平面へ散らす。スクロール（ホイール・トラックパッド）・
 *       ドラッグ・スワイプで上下左右に動かすと、写真ごとに少しずつ違う速さで追いかけ（各写真に 0.5〜1 の係数）、
 *       止まると元の並びに戻る。端まで行くと反対側から同じ並びが続く
 *       （写真が少ないときは同じ並びを複製して画面を埋める。複製は読み上げない）。
 *   - 散らし方（どの写真がどこに・どの大きさで）は読み込むたびに変わる（v13。再読込でも変わる）。
 *       同じ読み込みの間（画面の回転・パターンの選び直し）は同じ散らし方。位置の保存・復元はしない。
 *   - 開いた直後は、画面の中央に重なった写真が自分の場所へ広がっていく。
 *   - マウスのある端末では、マウスの位置に合わせて写真が大きさに応じて少しずれる（奥行き）。
 *   - 番号と撮影日は、写真が画面に入るたびに1行ずつ下から現れる。画像は中間画像（mids/、長辺800px）を使う。
 *   - 「Change view」（見せ方を選び直す）の文字も写真と同じように散らし、写真と同じ動きをさせる（v13。v11 の年の文字の代わり）。
 *       最初に開いた画面に1つ、ほかはだいたい1画面に1つ。散らした文字は読み上げ・Tab の対象にせず、
 *       同じ働きの #pattern-switch（画面の右下。キーボードで移ったときだけ見える）で読み上げる。
 *
 * ■ 動かない一覧（data-mode="still"）… 端末の「視差効果を減らす」設定がオンのとき。表紙と正方形の一覧を
 *       縦にスクロールする。現像も動かさない。設定を途中で変えると、その場で切り替わる。
 *
 * ■ 見せ方のパターン（v12）
 *   - 開いたときに選ぶ画面（Choose a view）で選ぶ。パターン1 = 散らばる、
 *       パターン2 = 並べる（data-mode="elastic"。Codrops の作例「Elastic Grid Scroll」の動きの考え方を参考に書いたもの。
 *       v13 までの名前は「列」）、パターン3 = 揺れる（data-mode="sway"。v14。このサイト用に考えた見せ方）。
 *       選んだパターンは sessionStorage「pv:pattern」に保存し、同じタブの再読込・戻る操作では選ぶ画面を出さない。
 *       前回選んだものは localStorage「pv:patternLast」に覚えておき、選ぶ画面で最初に選ばれた状態にする。
 *       画面の「Change view」からいつでも選び直せる。視差効果を減らす設定のときは選ぶ画面を出さず、動かない一覧にする。
 *   - パターン2 … 写真を 4:5 の枠で列に並べる（幅 910px 未満は3列、以上は7列）。ページは普通にスクロールし、
 *       各列はスクロールに遅れてついてくる（中央の列 0.5 秒、外側へ1列ごとに 0.1 秒ずつ遅い）。止まるとそろう。
 *       撮影年の範囲・「Change view」は一覧の上に並べる。スクロール位置は sessionStorage「pv:elastic」に保存する。
 *   - パターン3（v14）… 暗室で現像した写真を干すように、傾きの違う紐を上から下へ何本も張り（交差はしない）、
 *       写真（ふちなし・元の縦横比）を上の真ん中のクリップで吊るし、右横に札（番号・年・月日）を下げる。
 *       1本に吊るす枚数はスマホ1〜2枚・タブレット2〜4枚・PC 3〜6枚。最初の紐には撮影年の範囲と「Change view」の札。
 *       ページは普通に縦へスクロールする。写真と札は振り子のように揺れ（大きい写真ほどゆっくり）、
 *       スクロールの勢いで傾き、止まると数秒で収まる。ときどき左から右へそよ風が通り抜け、少しだけ揺らす。
 *       並びは写真ごとに決まっていて再読込しても同じ。スクロール位置は sessionStorage「pv:sway」に保存する。
 */
(function () {
  'use strict';

  var CONFIG = Object.freeze({
    dataUrl: 'data/photos.json',
    batchSize: 60,
    saveDelay: 200,
    swipeMinPx: 40,
    swipeRatio: 0.18,
    swipeVelocity: 0.45,
    spinnerDelay: 150,
    coverMaxHeight: 'min(64vh, 820px)', // css/style.css の .cover-btn と同じ値
    fontWait: 1500
  });
  var KEYS = Object.freeze({
    scroll: 'pv:scroll', pattern: 'pv:pattern', patternLast: 'pv:patternLast', elastic: 'pv:elastic', sway: 'pv:sway'
  });
  // v12 までの保存値（選んだ年・パターン1の位置）。もう使わないので見つけたら消す
  var RETIRED_KEYS = [['localStorage', 'pv:selectedYear'], ['sessionStorage', 'pv:drift']];
  var PATTERNS = ['1', '2', '3']; // 1 = 散らばる（drift）、2 = 並べる（elastic）、3 = 揺れる（sway。v14）
  // パターン3の動き方（v14）
  var SWAY = Object.freeze({
    tagW: 40,          // 札の幅（番号・年・月日の3行）
    tagH: 52,          // 札の高さ
    labelW: 54,        // 「Change view」の札の幅（2行）
    labelH: 40,        // 「Change view」の札の高さ
    tagGap: 8,         // 写真と札のあいだ
    itemGap: 14,       // 札と次の写真のあいだ（いちばん狭いとき）
    lineGap: 34,       // 吊るしたものの下端と次の紐のあいだ
    clip: 14,          // クリップの高さ（紐がクリップの真ん中を通る）
    period: 1700,      // 高さ150pxの写真が1往復する時間（ms）。大きい写真ほどゆっくり
    lenRef: 115,       // そのときの振り子の長さ（写真の高さ × 0.7 ＋ 10）
    decay: 1100,       // 揺れが 1/e に収まるまでの時間（ms）
    scroll: 0.07,      // スクロールの速さ 1px/ms あたりの傾き（rad）
    maxTilt: 0.35,     // いちばん大きい傾き（rad。約20度）
    gustEvery: [6000, 14000], // そよ風の間隔（ms）
    gustSpeed: 0.9,    // そよ風が横切る速さ（px/ms）
    gustWidth: 320,    // そよ風の幅（px）
    gustTilt: 0.035,   // そよ風で傾く大きさ（rad。約2度）
    saveDelay: 200
  });
  // パターン2の動き方（v12）
  var ELASTIC = Object.freeze({
    baseLag: 0.5,   // 中央の列が追いつくまでの秒数
    lagScale: 0.1,  // 外側へ1列ごとに足す秒数
    wide: 910,      // この幅から7列（それより狭いと3列）
    saveDelay: 200
  });
  // 動く一覧の動き方（v10）
  var DRIFT = Object.freeze({
    ease: 0.075,       // 1フレーム（60fps 換算）で目標の位置に近づく割合。小さいほどゆっくり滑らか
    wheel: 0.5,        // ホイール・トラックパッドの移動量に掛ける倍率
    parallax: 5,       // 動いている間のずれ = 1フレームの移動量 × この値 × 写真ごとの係数（0.5〜1）
    mouse: 0.5,        // マウスの位置によるずれ（写真の大きさに対する割合。画面の中央で0、端で ±半分）
    fling: 200,        // 指を離したときの勢い（直前の速さ × この ms 分だけ先へ進む）
    tap: 8,            // これより動いたらドラッグとみなす（タップで開かない）
    introDur: 2000,    // 中央から広がる時間
    introStagger: 50,  // 写真ごとの開始のずれ
    caption: 40,       // 写真の下の番号・日付の高さ（並べるときに空ける）
    labelW: 104,       // 散らした「Change view」の大きさ（押しやすい大きさ）
    labelH: 44
  });
  var PATH_RE = /^(photos|thumbs|mids)\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.webp$/;
  var ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
  var JST_OFFSET_MS = 9 * 60 * 60 * 1000;

  function $(id) { return document.getElementById(id); }

  var el = {
    title: $('app-title'),
    count: $('photo-count'),
    range: $('photo-range'),
    cover: $('cover'),
    coverBtn: $('cover-btn'),
    coverImg: $('cover-img'),
    status: $('status'),
    statusText: $('status-text'),
    retry: $('status-retry'),
    panel: $('grid-panel'),
    grid: $('grid'),
    sentinel: $('grid-sentinel'),
    viewer: $('viewer'),
    counter: $('viewer-counter'),
    no: $('viewer-no'),
    close: $('viewer-close'),
    stage: $('viewer-stage'),
    img: $('viewer-img'),
    spinner: $('viewer-spinner'),
    error: $('viewer-error'),
    prev: $('viewer-prev'),
    next: $('viewer-next'),
    caption: $('viewer-caption'),
    drift: $('drift'),
    driftList: $('drift-list'),
    driftCopies: $('drift-copies'),
    driftLabels: $('drift-labels'),
    elastic: $('elastic'),
    sway: $('sway'),
    swayLines: $('sway-lines'),
    swayList: $('sway-list'),
    swayTags: $('sway-tags'),
    chooser: $('chooser'),
    patternSwitch: $('pattern-switch')
  };

  var state = {
    photos: [],
    years: [],        // 写真のある年（撮影年の範囲の表示だけに使う）
    list: [],         // 一覧の写真（全部の写真。新しい順）
    viewList: [],     // 全画面表示で前後に送る写真
    viewFrom: 'grid', // 'grid' | 'cover'（閉じたときにフォーカスを戻す先）
    coverId: null,
    rendered: 0,
    invalid: 0,
    ready: false,
    loading: false,
    loadToken: 0,
    open: false,
    index: -1,
    slideToken: 0,
    spinnerTimer: 0,
    preload: [],
    swipe: null,
    saveTimer: 0,
    restoring: false,
    observer: null,
    developObserver: null,
    mode: null,          // 'drift'（パターン1）| 'elastic'（パターン2）| 'still'（動かない一覧）| 'choose'（選ぶ画面）
    pattern: null,       // 選んだパターン '1' | '2'（未選択は null）
    viewOpened: -1,      // 全画面表示を開いたときの写真の番号（動く一覧で、閉じたときに画面を動かすかの判断に使う）
    viewByKey: false     // キーボードで開いたか
  };

  // 動く一覧の状態
  var drift = {
    items: [],                 // 画面に置いた写真（元の写真と複製）
    cell: { w: 0, h: 0 },
    block: { w: 0, h: 0 },     // 写真をすべて並べた1周分の大きさ
    period: { w: 0, h: 0 },    // 複製を含めた1周分（画面より大きい）
    vw: 0, vh: 0, top: 0,      // 画面の幅・高さ・上部バーの下端
    cx: 0, cy: 0,              // 今の位置
    tx: 0, ty: 0,              // 目標の位置（スクロール・ドラッグで動かす）
    lx: 0, ly: 0,              // 前のフレームの位置
    mx: 0.5, my: 0.5, mcx: 0.5, mcy: 0.5, mouse: false,
    raf: 0, last: 0,
    drag: null,
    suppressClick: false,
    quietFocus: false,
    resizeTimer: 0
  };
  var motionQuery = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  var hoverQuery = window.matchMedia ? window.matchMedia('(hover: hover) and (pointer: fine)') : null;

  // ---------------------------------------------------------------- ユーティリティ
  function DataError(kind, detail) {
    this.name = 'DataError';
    this.kind = kind;
    this.detail = detail;
    this.message = kind;
  }
  DataError.prototype = Object.create(Error.prototype);

  function storageGet(area, key) {
    try { return window[area] ? window[area].getItem(key) : null; } catch (e) { return null; }
  }
  function storageSet(area, key, value) {
    try { if (window[area]) window[area].setItem(key, value); } catch (e) { /* 保存不可でも動作継続 */ }
  }
  function storageRemove(area, key) {
    try { if (window[area]) window[area].removeItem(key); } catch (e) { /* noop */ }
  }

  function jstParts(ms) {
    var d = new Date(ms + JST_OFFSET_MS);
    return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(), h: d.getUTCHours(), mi: d.getUTCMinutes() };
  }
  function jstYear(ms) { return jstParts(ms).y; }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function formatDot(ms) {
    var p = jstParts(ms);
    return p.y + '.' + pad2(p.m) + '.' + pad2(p.d);
  }
  function formatNo(n) { return n < 10 ? '0' + n : String(n); }
  function formatDateTime(ms) {
    var p = jstParts(ms);
    return p.y + '年' + p.m + '月' + p.d + '日 ' + pad2(p.h) + ':' + pad2(p.mi);
  }
  function posInt(v) { return (typeof v === 'number' && v > 0 && Math.floor(v) === v) ? v : 0; }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  // ---------------------------------------------------------------- データ読込
  function parseData(data) {
    var entries;
    var title = '写真';
    if (data && typeof data === 'object' && !Array.isArray(data) && Array.isArray(data.photos)) {
      entries = data.photos;
      if (typeof data.title === 'string' && data.title.trim()) title = data.title.trim();
    } else if (Array.isArray(data)) {
      entries = data;
    } else {
      throw new DataError('format');
    }
    var legacy = entries.some(function (e) {
      return e && typeof e === 'object' && Object.prototype.hasOwnProperty.call(e, 'fiscalYear');
    });
    if (legacy) throw new DataError('legacy');

    var photos = [];
    var seen = Object.create(null);
    var invalid = 0;
    entries.forEach(function (e, i) {
      if (!e || typeof e !== 'object') { invalid++; return; }
      var ok = typeof e.id === 'string' && e.id &&
        typeof e.src === 'string' && PATH_RE.test(e.src) && e.src.indexOf('photos/') === 0 &&
        typeof e.thumb === 'string' && PATH_RE.test(e.thumb) && e.thumb.indexOf('thumbs/') === 0 &&
        typeof e.date === 'string' && ISO_RE.test(e.date);
      if (!ok || seen[e.id]) { invalid++; return; }
      var ms = Date.parse(e.date);
      if (!isFinite(ms)) { invalid++; return; }
      var y = jstYear(ms);
      if (e.year !== undefined && e.year !== y) { invalid++; return; } // S-01: year は date の西暦年
      seen[e.id] = true;
      photos.push({
        id: e.id,
        src: e.src,
        thumb: e.thumb,
        // 中間画像（v10。動く一覧で使う）。ない・形式が違うときは詳細画像で代わりに表示する
        mid: (typeof e.mid === 'string' && PATH_RE.test(e.mid) && e.mid.indexOf('mids/') === 0) ? e.mid : '',
        ms: ms,
        year: y,
        width: posInt(e.width),
        height: posInt(e.height),
        alt: (typeof e.alt === 'string' && e.alt.trim()) ? e.alt : formatDateTime(ms) + ' の写真',
        dateSource: e.dateSource,
        order: i
      });
    });
    // S-02: 新しい順。同日時は JSON の順序（＝生成時の相対パス昇順）を保つ安定ソート
    photos.sort(function (a, b) { return (b.ms - a.ms) || (a.order - b.order); });
    // 目録番号: いちばん古い写真が 1。年で絞り込んでも番号は変わらない
    for (var k = 0; k < photos.length; k++) photos[k].no = photos.length - k;
    var cover = (data && typeof data === 'object' && !Array.isArray(data) && typeof data.cover === 'string') ? data.cover : null;
    return { photos: photos, title: title, invalid: invalid, cover: cover };
  }

  function errorMessage(err) {
    var kind = err && err.kind;
    if (kind === 'legacy') {
      return '旧形式（fiscalYear）の写真データのため表示を停止しました。\n管理者は tools/build_gallery.py で再生成してください。';
    }
    if (kind === 'http') return '写真データを読み込めませんでした（HTTP ' + err.detail + '）。';
    if (kind === 'json') return '写真データの形式が正しくありません（JSONを解析できません）。';
    if (kind === 'format') return '写真データの形式が正しくありません。';
    return '写真データを読み込めませんでした。\n通信状態を確認して、再読み込みしてください。';
  }

  function setStatus(kind, text) {
    el.status.setAttribute('data-state', kind);
    el.status.setAttribute('aria-busy', kind === 'loading' ? 'true' : 'false');
    el.statusText.textContent = text || '';
    el.retry.hidden = kind !== 'error';
  }

  function load() {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    state.ready = false;
    var token = ++state.loadToken;
    setStatus('loading', '読み込み中…');
    el.grid.setAttribute('aria-busy', 'true');
    return fetch(CONFIG.dataUrl, { cache: 'no-cache', credentials: 'same-origin' })
      .catch(function () { throw new DataError('network'); })
      .then(function (res) {
        if (!res.ok) throw new DataError('http', res.status);
        return res.json().catch(function () { throw new DataError('json'); });
      })
      .then(function (data) {
        if (token !== state.loadToken) return;
        applyData(parseData(data));
      })
      .catch(function (err) {
        if (token !== state.loadToken) return;
        showLoadError(err);
      })
      .then(function () {
        state.loading = false;
        el.grid.setAttribute('aria-busy', 'false');
      });
  }

  function showLoadError(err) {
    if (window.console) console.error('[photo-viewer]', err);
    closeViewer({ restoreFocus: false });
    state.photos = [];
    state.list = [];
    state.years = [];
    state.rendered = 0;
    driftClear();
    elasticClear();
    swayClear();
    el.grid.textContent = '';
    el.count.textContent = '';
    el.range.textContent = '';
    if (!el.title.textContent) el.title.textContent = '写真';
    el.cover.hidden = true;
    el.sentinel.hidden = true;
    setStatus('error', errorMessage(err));
    document.documentElement.setAttribute('data-pv', 'error');
  }

  function applyData(parsed) {
    state.photos = parsed.photos;
    state.invalid = parsed.invalid;
    // 表紙: photos.json の cover（config.json の coverPhoto）。見つからなければいちばん新しい写真
    state.coverId = parsed.photos.some(function (p) { return p.id === parsed.cover; })
      ? parsed.cover
      : (parsed.photos[0] ? parsed.photos[0].id : null);
    if (parsed.invalid && window.console) console.warn('[photo-viewer] 表示できないデータ: ' + parsed.invalid + '件');
    renderTitle(parsed.title);
    document.title = parsed.title;

    var years = [];
    state.photos.forEach(function (p) { if (years.indexOf(p.year) < 0) years.push(p.year); });
    years.sort(function (a, b) { return b - a; });
    state.years = years;

    if (!state.photos.length) {
      setStatus('empty', '写真はまだありません。');
    } else {
      setStatus('ready', '');
    }
    renderList({ restore: true });
    state.ready = true;
    var token = state.loadToken;
    whenLayoutStable(function () {
      if (token !== state.loadToken) return;
      if (state.mode === 'still') restoreScroll();
      document.documentElement.setAttribute('data-pv', state.photos.length ? 'ready' : 'empty');
    });
  }

  // ---------------------------------------------------------------- サイト名（画面には出さない。読み上げ用・v11）
  function renderTitle(title) {
    el.title.textContent = title;
  }

  function whenLayoutStable(fn) {
    var done = false;
    var run = function () { if (!done) { done = true; fn(); } };
    if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === 'function') {
      document.fonts.ready.then(run, run);
      setTimeout(run, CONFIG.fontWait);
    } else {
      run();
    }
  }

  // ---------------------------------------------------------------- ポートフォリオ型一覧（S-05）
  // 撮影年の範囲（v13 で「〜年の〜枚」は廃止。表示できないデータがあるときだけ件数を出す）
  function updateCount() {
    var ys = state.years;
    el.range.textContent = !ys.length ? '' : (ys.length === 1 ? String(ys[0]) : ys[ys.length - 1] + '–' + ys[0]);
    el.count.textContent = state.invalid ? '表示できないデータ ' + state.invalid + '件' : '';
  }

  function makeTile(p, i) {
    var li = document.createElement('li');
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'tile';
    b.setAttribute('data-index', String(i));
    b.setAttribute('data-id', p.id);
    var img = document.createElement('img');
    img.loading = 'lazy'; // src より先に設定（遅延読込を確実に効かせる）
    img.decoding = 'async';
    img.width = 480;
    img.height = 480;
    img.alt = p.alt;
    img.addEventListener('load', function () { markLoaded(b); });
    img.addEventListener('error', function () { b.classList.add('is-broken'); markLoaded(b); });
    img.src = p.thumb;
    watchDevelop(b);
    var fb = document.createElement('span');
    fb.className = 'tile-fallback';
    fb.setAttribute('aria-hidden', 'true');
    fb.textContent = '表示できません';
    b.appendChild(img);
    b.appendChild(fb);
    var cap = document.createElement('p');
    cap.className = 'tile-cap';
    cap.setAttribute('aria-hidden', 'true'); // 読み上げは写真の代替テキスト（撮影日時）で行う
    var no = document.createElement('span');
    no.className = 'tile-no';
    no.textContent = formatNo(p.no);
    var date = document.createElement('span');
    date.className = 'tile-date';
    date.textContent = formatDot(p.ms);
    cap.appendChild(no);
    cap.appendChild(date);
    li.appendChild(b);
    li.appendChild(cap);
    return li;
  }

  // ---------------------------------------------------------------- 現像（読み込み済みで、画面に入った写真から）
  function tryDevelop(target) {
    if (target.getAttribute('data-loaded') === '1' && target.getAttribute('data-seen') === '1') {
      target.classList.add('is-developed');
    }
  }
  function markLoaded(target) {
    target.setAttribute('data-loaded', '1');
    tryDevelop(target);
  }
  function watchDevelop(target) {
    if (!('IntersectionObserver' in window)) {
      target.setAttribute('data-seen', '1');
      return;
    }
    if (!state.developObserver) {
      state.developObserver = new IntersectionObserver(function (entries) {
        var k = 0;
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          var t = en.target;
          state.developObserver.unobserve(t);
          // 同時に画面に入った写真は少しずつずらして現像する（最大 0.4 秒）
          t.style.setProperty('--develop-delay', Math.min(k++ * 80, 400) + 'ms');
          t.setAttribute('data-seen', '1');
          tryDevelop(t);
        });
      }, { threshold: 0.15 });
    }
    state.developObserver.observe(target);
  }

  // 表紙（動かない一覧の先頭に、1枚を大きく表示する）
  function coverPhoto() {
    for (var i = 0; i < state.photos.length; i++) if (state.photos[i].id === state.coverId) return state.photos[i];
    return null;
  }

  function renderCover() {
    var p = coverPhoto();
    var show = !!p;
    el.cover.hidden = !show;
    if (!show) return;
    if (el.coverImg.getAttribute('data-id') === p.id) return; // 同じ写真なら読み込み直さない
    var rw = p.width || 4, rh = p.height || 3;
    // 読み込み前から元の縦横比の場所を確保する（読み込み後に一覧がずれてスクロール位置が狂わないように）
    el.coverBtn.style.aspectRatio = rw + ' / ' + rh;
    el.coverBtn.style.width = 'min(100%, calc(' + CONFIG.coverMaxHeight + ' * ' + (rw / rh).toFixed(4) + '))';
    el.coverBtn.classList.remove('is-broken', 'is-developed');
    el.coverBtn.removeAttribute('data-loaded');
    el.coverBtn.removeAttribute('data-seen');
    watchDevelop(el.coverBtn);
    el.coverImg.width = rw;
    el.coverImg.height = rh;
    el.coverImg.alt = p.alt;
    el.coverImg.setAttribute('data-id', p.id);
    el.coverImg.src = p.src;
  }

  el.coverImg.addEventListener('load', function () { markLoaded(el.coverBtn); });
  el.coverImg.addEventListener('error', function () { el.coverBtn.classList.add('is-broken'); markLoaded(el.coverBtn); });
  // 表紙をタップすると、一覧の中のその写真の位置から全画面表示（前後は一覧の写真）
  el.coverBtn.addEventListener('click', function () {
    var p = coverPhoto();
    if (!p) return;
    var i = state.list.indexOf(p);
    if (i >= 0) openViewer(i, { from: 'cover' });
  });

  function renderMore(n) {
    var end = Math.min(state.list.length, state.rendered + n);
    if (end <= state.rendered) return;
    var frag = document.createDocumentFragment();
    for (var i = state.rendered; i < end; i++) frag.appendChild(makeTile(state.list[i], i));
    el.grid.appendChild(frag);
    state.rendered = end;
  }

  function ensureRendered(index) {
    while (state.rendered <= index && state.rendered < state.list.length) renderMore(CONFIG.batchSize);
    observeSentinel();
  }

  // 一覧を描き直す（全部の写真。新しい順）
  function renderList(opts) {
    state.list = state.photos.slice();
    updateCount();
    if (state.mode === 'drift' || state.mode === 'elastic' || state.mode === 'sway' || state.mode === 'choose') {
      clearGrid();
      el.cover.hidden = true; // 動く見せ方では表紙を出さない（v10）
      el.sentinel.hidden = true;
      if (state.observer) state.observer.disconnect();
      if (state.mode === 'drift') { elasticClear(); swayClear(); driftBuild(); }
      else if (state.mode === 'elastic') { driftClear(); swayClear(); elasticBuild({ restore: !!(opts && opts.restore) }); }
      else if (state.mode === 'sway') { driftClear(); elasticClear(); swayBuild({ restore: !!(opts && opts.restore) }); }
      else { driftClear(); elasticClear(); swayClear(); } // 選ぶ画面の間は一覧を作らない
    } else {
      driftClear();
      elasticClear();
      swayClear();
      renderGrid();
    }
  }

  function clearGrid() {
    if (state.developObserver) {
      Array.prototype.forEach.call(el.grid.querySelectorAll('.tile:not(.is-developed)'), function (b) {
        state.developObserver.unobserve(b);
      });
    }
    el.grid.textContent = '';
    state.rendered = 0;
  }

  function renderGrid() {
    clearGrid();
    renderMore(CONFIG.batchSize);
    renderCover();
    observeSentinel();
  }

  function observeSentinel() {
    var done = state.rendered >= state.list.length;
    el.sentinel.hidden = done;
    if (!('IntersectionObserver' in window)) {
      if (!done) renderMore(state.list.length);
      el.sentinel.hidden = true;
      return;
    }
    if (!state.observer) {
      state.observer = new IntersectionObserver(function (entries) {
        if (!entries.some(function (en) { return en.isIntersecting; })) return;
        renderMore(CONFIG.batchSize);
        observeSentinel(); // 再監視（まだ見えていれば続けて追加される）
      }, { rootMargin: '800px 0px' });
    }
    state.observer.disconnect();
    if (!done) state.observer.observe(el.sentinel);
  }

  el.grid.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.tile') : null;
    if (b) openViewer(Number(b.getAttribute('data-index')));
  });

  // ---------------------------------------------------------------- スクロール位置の保存・復元
  // 上に残るバーはない（v13 で年のタブとともに廃止）ので、画面の上端が基準
  function headerBottom() {
    return 0;
  }

  function captureScroll() {
    var y = window.pageYOffset || document.documentElement.scrollTop || 0;
    var base = { v: 2, anchorId: null, delta: 0, y: 0 };
    if (y <= 0 || !state.rendered) return base;
    base.y = Math.round(y);
    var top = headerBottom();
    var items = el.grid.children;
    // 一覧は上から順に並ぶので、下端が画面の上端より下にある最初の写真を二分探索で求める
    var lo = 0, hi = Math.min(items.length, state.rendered) - 1, idx = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (items[mid].getBoundingClientRect().bottom > top + 1) { idx = mid; hi = mid - 1; } else { lo = mid + 1; }
    }
    if (idx < 0 || !state.list[idx]) return base;
    base.anchorId = state.list[idx].id;
    base.delta = Math.round(items[idx].getBoundingClientRect().top - top);
    return base;
  }

  function saveScrollNow() {
    cancelPendingSave();
    if (!state.ready || state.restoring) return;
    if (state.mode === 'elastic') { elasticSaveNow(); return; }
    if (state.mode === 'sway') { swaySaveNow(); return; }
    if (state.mode !== 'still') return;
    storageSet('sessionStorage', KEYS.scroll, JSON.stringify(captureScroll()));
  }

  function cancelPendingSave() {
    if (state.saveTimer) clearTimeout(state.saveTimer);
    state.saveTimer = 0;
  }

  function scheduleSave() {
    if (!state.ready || state.restoring || state.open || state.mode !== 'still') return;
    cancelPendingSave();
    state.saveTimer = setTimeout(saveScrollNow, CONFIG.saveDelay);
  }

  function restoreScroll() {
    var raw = storageGet('sessionStorage', KEYS.scroll);
    if (!raw) return;
    var s;
    try { s = JSON.parse(raw); } catch (e) { storageRemove('sessionStorage', KEYS.scroll); return; }
    if (!s || s.v !== 2 || !state.list.length) return; // v1（v12 までの年ごとの位置）は使わない
    state.restoring = true;
    try {
      var idx = s.anchorId ? state.list.findIndex(function (p) { return p.id === s.anchorId; }) : -1;
      if (idx >= 0) {
        ensureRendered(idx + CONFIG.batchSize);
        var li = el.grid.children[idx];
        // 表紙などの読み込みで位置がずれることがあるので、ずれがなくなるまで補正する（最大3回）。
        for (var k = 0; k < 3; k++) {
          var diff = li.getBoundingClientRect().top - headerBottom() - (s.delta || 0);
          if (Math.abs(diff) < 1) break;
          window.scrollTo(0, Math.max(0, Math.round((window.pageYOffset || 0) + diff)));
        }
      } else if (s.y > 0) {
        // 基準の写真が削除された場合は保存したピクセル位置付近へ
        var last = el.grid.children[state.rendered - 1];
        var perItem = last ? Math.max(1, (last.getBoundingClientRect().bottom + (window.pageYOffset || 0)) / state.rendered) : 60;
        ensureRendered(Math.ceil(s.y / perItem) + CONFIG.batchSize);
        window.scrollTo(0, s.y);
      }
    } finally {
      var release = function () { state.restoring = false; };
      if (window.requestAnimationFrame) requestAnimationFrame(function () { requestAnimationFrame(release); });
      else setTimeout(release, 50);
    }
  }

  window.addEventListener('scroll', scheduleSave, { passive: true });
  window.addEventListener('pagehide', saveScrollNow);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') saveScrollNow();
  });
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

  // ---------------------------------------------------------------- 動く一覧（v10）
  // 散らし方の種（v13）。読み込むたびに変わるので、並び・大きさ・速さの係数も読み込むたびに変わる。
  // 同じ読み込みの間は同じ値（画面を回して並べ直したときや、パターンを選び直したときも同じ散らし方）
  var LOAD_SEED = (function () {
    try {
      if (window.crypto && window.crypto.getRandomValues) return window.crypto.getRandomValues(new Uint32Array(1))[0];
    } catch (e) { /* noop */ }
    return Math.floor(Math.random() * 4294967296) >>> 0;
  })();

  // 写真ID（など）と種から乱数（0〜1）を作る。seeded は読み込むたびに変わり、stable はいつも同じ（v14）
  function seeded(id, salt) { return hashRand(id, salt, LOAD_SEED); }
  function stable(id, salt) { return hashRand(id, salt, 0); }
  function hashRand(id, salt, seed) {
    var h = (2166136261 ^ salt ^ seed) >>> 0;
    for (var i = 0; i < id.length; i++) {
      h ^= id.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    h ^= h >>> 13;
    h = Math.imul(h, 0x5bd1e995);
    h ^= h >>> 15;
    return (h >>> 0) / 4294967296;
  }

  function expoInOut(t) {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    return t < 0.5 ? Math.pow(2, 20 * t - 10) / 2 : (2 - Math.pow(2, -20 * t + 10)) / 2;
  }

  // v を [-size, period - size) に収める（画面の外へ出た写真を反対側へ回す）
  function wrapPos(v, size, period) {
    var r = (v + size) % period;
    if (r < 0) r += period;
    return r - size;
  }

  function driftClear() {
    if (drift.raf) cancelAnimationFrame(drift.raf);
    drift.raf = 0;
    drift.last = 0;
    drift.items = [];
    drift.drag = null;
    if (el.driftList) el.driftList.textContent = '';
    if (el.driftCopies) el.driftCopies.textContent = '';
    if (el.driftLabels) el.driftLabels.textContent = '';
    if (el.drift) {
      el.drift.classList.remove('is-dragging');
      el.drift.removeAttribute('data-intro');
      el.drift.removeAttribute('data-moving');
    }
  }

  function makeDriftItem(p, index, copy, w, h) {
    var item = document.createElement(copy ? 'div' : 'li');
    item.className = 'drift-item';
    item.style.width = w + 'px';
    item.style.transform = 'translate3d(-10000px,-10000px,0)'; // 位置が決まるまで画面の外（遅延読込も始めない）
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'drift-tile';
    b.style.height = h + 'px';
    b.setAttribute('data-index', String(index));
    b.setAttribute('data-id', p.id);
    if (copy) b.tabIndex = -1; // 複製は読み上げ・フォーカスの対象にしない（親に aria-hidden）
    b.style.setProperty('--develop-delay', Math.round(seeded(p.id, 7) * 300) + 'ms');
    var img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.draggable = false;
    img.width = Math.round(w);
    img.height = Math.round(h);
    img.alt = copy ? '' : p.alt;
    img.addEventListener('load', function () { markLoaded(b); });
    img.addEventListener('error', function () { b.classList.add('is-broken'); markLoaded(b); });
    img.src = p.mid || p.src;
    var fb = document.createElement('span');
    fb.className = 'tile-fallback';
    fb.setAttribute('aria-hidden', 'true');
    fb.textContent = '表示できません';
    b.appendChild(img);
    b.appendChild(fb);
    var cap = document.createElement('p');
    cap.className = 'drift-cap';
    cap.setAttribute('aria-hidden', 'true'); // 読み上げは写真の代替テキスト（撮影日時）で行う
    [formatNo(p.no), formatDot(p.ms)].forEach(function (text, line) { // 番号（「No.」は付けない。v13）と撮影日
      var mask = document.createElement('span');
      mask.className = 'drift-line';
      var inner = document.createElement('span');
      inner.style.setProperty('--line', String(line));
      inner.textContent = text;
      mask.appendChild(inner);
      cap.appendChild(mask);
    });
    item.appendChild(b);
    item.appendChild(cap);
    return { el: item, btn: b, x: 0, y: 0, w: w, h: h, cap: DRIFT.caption, ease: 0.5 + seeded(p.id, 5) * 0.5, index: index,
      copy: copy, label: false, near: false, visible: false, intro: null };
  }

  // 散らす「Change view」（v13。v11 の年の文字の代わり）。押すと見せ方を選ぶ画面を出す。
  // 読み上げ・Tab は同じ働きの #pattern-switch で行う
  function makeDriftLabel(ease) {
    var item = document.createElement('div');
    item.className = 'drift-item drift-label-item';
    item.style.width = DRIFT.labelW + 'px';
    item.style.transform = 'translate3d(-10000px,-10000px,0)';
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'drift-label';
    b.tabIndex = -1;
    b.lang = 'en';
    b.textContent = el.patternSwitch ? el.patternSwitch.textContent : 'Change view';
    item.appendChild(b);
    return { el: item, btn: b, x: 0, y: 0, w: DRIFT.labelW, h: DRIFT.labelH, cap: 0, ease: ease, index: -1,
      copy: true, label: true, near: false, visible: false, intro: null };
  }

  // 写真を並べる。1周分（block）は画面より少し広く、写真が少ないときは複製して画面より大きくする
  function driftBuild(opts) {
    driftClear();
    var list = state.list;
    var vw = Math.max(1, window.innerWidth);
    var vh = Math.max(1, window.innerHeight);
    drift.vw = vw;
    drift.vh = vh;
    drift.top = 0; // 動く一覧では上部のバーを出さない（v11）ので、画面の上端まで使う
    if (!list.length || !el.drift) return;
    var compact = vw < 640;
    var cw = Math.round(compact ? vw * 0.5 : clamp(vw / 4.2, 230, 360));
    var ch = Math.round(cw * (compact ? 1.28 : 1.22));
    // 写真と「Change view」の置き場所（v13）。並び1周を「画面くらいの大きさの区画」に分け、
    // 最初に見える区画とほかの区画に「Change view」を1つずつ置く。残りの枠に写真を入れる
    var nPhotos = list.length;
    var visCols = Math.max(1, Math.floor(vw / cw));
    var visRows = Math.max(1, Math.floor(vh / ch));
    var winCols = visCols;
    var winRows = visRows + 1;
    // 最初に見える区画には、左上の写真の枠と、ほかの写真の枠（半分ほど）を残せるときだけ置く
    var firstCells = visCols * visRows;
    var firstPhotos = Math.min(Math.max(0, nPhotos - 1), Math.floor((firstCells - 1) / 2));
    var firstCap = Math.max(0, Math.min(1, firstCells - 1 - firstPhotos));
    var minCols = Math.ceil(vw * 1.15 / cw);
    var nLabels = 1;
    var cols = 1, rows = 1;
    for (var pass = 0; pass < 4; pass++) {
      var n = nPhotos + nLabels;
      cols = Math.max(1, Math.min(n, Math.max(minCols, Math.round(Math.sqrt(n * (vw / vh) * (ch / cw))))));
      rows = Math.ceil(n / cols);
      var windows = Math.ceil(cols / winCols) * Math.ceil(rows / winRows);
      var want = Math.max(1, firstCap + windows - 1);
      if (want === nLabels) break;
      nLabels = want;
    }
    rows = Math.max(rows, Math.ceil((nPhotos + nLabels) / cols));
    var labelCell = {};
    var placed = 0;
    var winX = Math.ceil(cols / winCols);
    var winY = Math.ceil(rows / winRows);
    for (var wy = 0; wy < winY && placed < nLabels; wy++) {
      for (var wx = 0; wx < winX && placed < nLabels; wx++) {
        var first = wx === 0 && wy === 0;
        var cand = [];
        // 最初の区画は、最初に画面に見える範囲（visCols × visRows）から選ぶ
        var rEnd = Math.min(rows, wy * winRows + (first ? visRows : winRows));
        for (var rr = wy * winRows; rr < rEnd; rr++) {
          for (var cc = wx * winCols; cc < Math.min(cols, (wx + 1) * winCols); cc++) {
            if (rr === 0 && cc === 0) continue;
            if (!labelCell[rr * cols + cc]) cand.push(rr * cols + cc);
          }
        }
        cand.sort(function (a, b) { return seeded('w' + a, 11) - seeded('w' + b, 11); }); // 区画の中の場所も読み込むたびに変わる
        var take = first ? firstCap : 1;
        for (var t = 0; t < take && t < cand.length && placed < nLabels; t++) {
          labelCell[cand[t]] = true;
          placed++;
        }
      }
    }
    // どの写真をどの枠に入れるかも読み込むたびに変える（番号は写真ごとに決まっていて変わらない）
    var seq = list.map(function (p, i) { return { i: i, r: seeded(p.id, 13) }; });
    seq.sort(function (a, b) { return (a.r - b.r) || (a.i - b.i); });
    var bw = cols * cw;
    var bh = rows * ch;
    var nx = Math.max(1, Math.ceil((vw + cw) / bw));
    var ny = Math.max(1, Math.ceil((vh + ch) / bh));
    drift.cell = { w: cw, h: ch };
    drift.block = { w: bw, h: bh };
    drift.period = { w: bw * nx, h: bh * ny };

    var pad = Math.round(cw * 0.04);
    var availW = cw - pad * 2;
    var availH = ch - pad * 2 - DRIFT.caption;
    var base = [];
    // 写真を入れる枠の順番: 最初に見える区画の枠（左上から）→ ほかの枠（左上から行ごと）
    var order = [];
    var inFirst = {};
    for (var fr = 0; fr < Math.min(visRows, rows); fr++) {
      for (var fc = 0; fc < Math.min(visCols, cols); fc++) {
        order.push(fr * cols + fc);
        inFirst[fr * cols + fc] = true;
      }
    }
    for (var k = 0; k < cols * rows; k++) if (!inFirst[k]) order.push(k);
    var photoIndex = 0;
    order.forEach(function (k) {
      var c = k % cols;
      var r = Math.floor(k / cols);
      var lift = c % 2 ? Math.round(ch * 0.32) : 0; // 奇数列は少し下げて、格子に見えないようにする
      if (labelCell[k]) {
        var key = 'l' + k;
        base.push({
          label: true, first: !!inFirst[k], w: DRIFT.labelW, h: DRIFT.labelH,
          x: c * cw + pad + Math.round(seeded(key, 2) * Math.max(0, availW - DRIFT.labelW)),
          y: r * ch + pad + Math.round(seeded(key, 3) * Math.max(0, ch - pad * 2 - DRIFT.labelH)) + lift,
          ease: 0.5 + seeded(key, 5) * 0.5
        });
        return;
      }
      if (photoIndex >= nPhotos) return; // 余った枠は空けておく
      var listIndex = seq[photoIndex].i;
      var p = list[listIndex];
      var ar = (p.width && p.height) ? p.width / p.height : 1;
      var pick = seeded(p.id, 1);
      var size = pick < 0.3 ? 0.7 : (pick < 0.7 ? 0.85 : 1); // 小・中・大
      // 横長は幅、縦長は高さを基準に（どちらも枠の中に収める）
      var w = ar >= 1 ? availW * size : availH * size * ar;
      var h = ar >= 1 ? w / ar : availH * size;
      var fit = Math.min(1, availW / w, availH / h);
      w = Math.max(24, Math.round(w * fit));
      h = Math.max(24, Math.round(h * fit));
      base.push({
        p: p, index: listIndex, w: w, h: h,
        x: c * cw + pad + Math.round(seeded(p.id, 2) * (availW - w)),
        y: r * ch + pad + Math.round(seeded(p.id, 3) * (availH - h)) + lift
      });
      photoIndex++;
    });
    var firstPhoto = null;
    for (var q = 0; q < base.length && !firstPhoto; q++) if (base[q].p) firstPhoto = base[q];
    // 初めの位置: 左端を少し切って「横にも続く」ことを見せつつ、左上の写真は全部見えるようにする
    var startX = Math.max(-Math.round(cw * 0.12), 8 - firstPhoto.x);
    var startY = Math.round(drift.top) + 14 - pad;
    // 最初の区画の「Change view」は、開いた画面の中に全部見えるように寄せる（枠の中で動かすだけなので写真とは重ならない）
    base.forEach(function (b) {
      if (!b.label || !b.first) return;
      b.x = clamp(b.x, 8 - startX, vw - b.w - 8 - startX);
      b.y = clamp(b.y, Math.round(drift.top) + 8 - startY, vh - b.h - 8 - startY);
    });
    var frag = document.createDocumentFragment();
    var copyFrag = document.createDocumentFragment();
    var labelFrag = document.createDocumentFragment();
    var originals = [];
    for (var iy = 0; iy < ny; iy++) {
      for (var ix = 0; ix < nx; ix++) {
        var copy = ix > 0 || iy > 0;
        base.forEach(function (b) {
          var it = b.p ? makeDriftItem(b.p, b.index, copy, b.w, b.h) : makeDriftLabel(b.ease);
          it.x = b.x + ix * bw;
          it.y = b.y + iy * bh;
          drift.items.push(it);
          if (b.p && !copy) originals.push(it);
          else (b.p ? copyFrag : labelFrag).appendChild(it.el);
        });
      }
    }
    // 読み上げ・Tab の順は、散らし方にかかわらず新しい順（番号の大きい順）
    originals.sort(function (a, b) { return a.index - b.index; });
    originals.forEach(function (it) { frag.appendChild(it.el); });
    el.driftList.setAttribute('aria-label', '写真');
    el.driftList.appendChild(frag);
    el.driftCopies.appendChild(copyFrag);
    el.driftLabels.appendChild(labelFrag);

    // 位置: 散らし方が読み込むたびに変わるので、保存・復元はしない（v13）。いつも初めの位置から広がる
    drift.cx = drift.tx = startX;
    drift.cy = drift.ty = startY;
    drift.lx = drift.cx;
    drift.ly = drift.cy;
    if (!(opts && opts.keepPlace)) driftStartIntro();
    driftKick();
  }

  // 開いた直後: 画面に入る写真を中央に重ね、順に自分の場所へ広げる
  function driftStartIntro() {
    var now = performance.now();
    var vw = drift.vw, vh = drift.vh;
    var midY = (drift.top + vh) / 2;
    var shown = [];
    drift.items.forEach(function (it) {
      var x = wrapPos(it.x + drift.cx, it.w, drift.period.w);
      var y = wrapPos(it.y + drift.cy, it.h + it.cap, drift.period.h);
      if (x < vw + drift.cell.w && x + it.w > -drift.cell.w && y < vh + drift.cell.h && y + it.h > -drift.cell.h) {
        it.intro = { dx: vw / 2 - it.w / 2 - x, dy: midY - it.h / 2 - y, start: 0 };
        shown.push(it);
      }
    });
    // 後ろの写真から順に（最後に動き出す写真がいちばん上に重なっている）
    shown.reverse().forEach(function (it, k) { it.intro.start = now + k * DRIFT.introStagger; });
    if (shown.length) el.drift.setAttribute('data-intro', '1');
  }

  function driftKick() {
    if (!drift.raf && drift.items.length) {
      el.drift.setAttribute('data-moving', '1');
      drift.raf = requestAnimationFrame(driftFrame);
    }
  }

  function driftFrame(now) {
    drift.raf = 0;
    var dt = drift.last ? Math.min(64, Math.max(1, now - drift.last)) : 16.67;
    drift.last = now;
    var f = dt / 16.67;
    var k = 1 - Math.pow(1 - DRIFT.ease, f);
    drift.cx += (drift.tx - drift.cx) * k;
    drift.cy += (drift.ty - drift.cy) * k;
    if (Math.abs(drift.tx - drift.cx) < 0.05) drift.cx = drift.tx;
    if (Math.abs(drift.ty - drift.cy) < 0.05) drift.cy = drift.ty;
    var vx = (drift.cx - drift.lx) / f; // 1フレーム（60fps 換算）あたりの移動量
    var vy = (drift.cy - drift.ly) / f;
    drift.lx = drift.cx;
    drift.ly = drift.cy;
    var km = 1 - Math.pow(1 - 0.08, f);
    drift.mcx += (drift.mx - drift.mcx) * km;
    drift.mcy += (drift.my - drift.mcy) * km;
    if (Math.abs(drift.mx - drift.mcx) < 0.0005) drift.mcx = drift.mx;
    if (Math.abs(drift.my - drift.mcy) < 0.0005) drift.mcy = drift.my;
    var mox = drift.mouse ? drift.mcx - 0.5 : 0;
    var moy = drift.mouse ? drift.mcy - 0.5 : 0;

    var vw = drift.vw, vh = drift.vh, pw = drift.period.w, ph = drift.period.h;
    var marginX = drift.cell.w, marginY = drift.cell.h;
    var introOn = false;
    for (var i = 0; i < drift.items.length; i++) {
      var it = drift.items[i];
      var x = wrapPos(it.x + drift.cx + vx * DRIFT.parallax * it.ease + mox * it.w * DRIFT.mouse, it.w, pw);
      var y = wrapPos(it.y + drift.cy + vy * DRIFT.parallax * it.ease + moy * it.h * DRIFT.mouse, it.h + it.cap, ph);
      if (it.intro) {
        var e = expoInOut((now - it.intro.start) / DRIFT.introDur);
        if (e >= 1) it.intro = null;
        else {
          introOn = true;
          x += it.intro.dx * (1 - e);
          y += it.intro.dy * (1 - e);
        }
      }
      var near = x < vw + marginX && x + it.w > -marginX && y < vh + marginY && y + it.h + it.cap > -marginY;
      if (near || it.near) it.el.style.transform = 'translate3d(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px,0)';
      it.near = near;
      var visible = x < vw && x + it.w > 0 && y < vh && y + it.h > drift.top;
      if (visible !== it.visible) {
        it.visible = visible;
        it.el.classList.toggle('is-in', visible); // 番号と日付を1行ずつ出す（画面を出たら戻し、次に入ったときにもう一度）
        if (visible && !it.label && it.btn.getAttribute('data-seen') !== '1') {
          it.btn.setAttribute('data-seen', '1');
          tryDevelop(it.btn);
        }
      }
    }
    if (!introOn) el.drift.removeAttribute('data-intro');
    var moving = introOn || !!drift.drag || drift.cx !== drift.tx || drift.cy !== drift.ty ||
      Math.abs(vx) > 0.01 || Math.abs(vy) > 0.01 || drift.mcx !== drift.mx || drift.mcy !== drift.my;
    if (moving) {
      drift.raf = requestAnimationFrame(driftFrame);
    } else {
      drift.last = 0;
      el.drift.removeAttribute('data-moving');
    }
  }

  // 元の写真（複製でない方）の、今の画面上の位置（動きによるずれを除く）
  function driftRect(it) {
    return {
      x: wrapPos(it.x + drift.tx, it.w, drift.period.w),
      y: wrapPos(it.y + drift.ty, it.h + it.cap, drift.period.h)
    };
  }

  function driftOriginal(index) {
    for (var i = 0; i < drift.items.length; i++) {
      var it = drift.items[i];
      if (!it.copy && it.index === index) return it;
    }
    return null;
  }

  // 写真が画面に収まっていなければ、画面の中央へ来るよう動かす
  function driftReveal(it) {
    if (!it) return;
    var r = driftRect(it);
    var top = drift.top + 8;
    var inside = r.x >= 8 && r.x + it.w <= drift.vw - 8 && r.y >= top && r.y + it.h + it.cap <= drift.vh - 8;
    if (inside) return;
    drift.tx += Math.round(drift.vw / 2 - (r.x + it.w / 2));
    drift.ty += Math.round((top + drift.vh) / 2 - (r.y + (it.h + it.cap) / 2));
    driftKick();
  }

  function driftFocus(index, reveal) {
    var it = driftOriginal(index);
    if (!it) return;
    drift.quietFocus = true; // このフォーカスでは focusin による移動をしない
    try { it.btn.focus({ preventScroll: true }); } finally { drift.quietFocus = false; }
    if (reveal) driftReveal(it);
  }

  function driftMove(dx, dy) {
    drift.tx += dx;
    drift.ty += dy;
    driftKick();
  }

  // ---- 操作（ホイール・ドラッグ・スワイプ・マウス・キー） ----
  function driftActive() {
    return state.mode === 'drift' && !state.open && drift.items.length > 0;
  }

  window.addEventListener('wheel', function (e) {
    if (!driftActive() || e.ctrlKey) return; // Ctrl＋ホイール（拡大）はブラウザーに任せる
    e.preventDefault();
    var unit = e.deltaMode === 1 ? 16 : (e.deltaMode === 2 ? drift.vh : 1);
    var dx = e.deltaX * unit;
    var dy = e.deltaY * unit;
    if (e.shiftKey && !dx) { dx = dy; dy = 0; } // Shift＋ホイールで横
    driftMove(-dx * DRIFT.wheel, -dy * DRIFT.wheel);
  }, { passive: false });

  if (el.drift) {
    el.drift.addEventListener('pointerdown', function (e) {
      if (!driftActive() || drift.drag) return;
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      drift.drag = { id: e.pointerId, type: e.pointerType, sx: e.clientX, sy: e.clientY, tx: drift.tx, ty: drift.ty,
        moved: false, samples: [{ t: e.timeStamp, x: e.clientX, y: e.clientY }] };
    });

    el.drift.addEventListener('pointermove', function (e) {
      if (state.mode !== 'drift') return;
      if (e.pointerType === 'mouse' && hoverQuery && hoverQuery.matches) {
        drift.mouse = true;
        drift.mx = clamp(e.clientX / drift.vw, 0, 1);
        drift.my = clamp(e.clientY / drift.vh, 0, 1);
        driftKick();
      }
      var d = drift.drag;
      if (!d || e.pointerId !== d.id) return;
      var dx = e.clientX - d.sx;
      var dy = e.clientY - d.sy;
      if (!d.moved) {
        if (Math.abs(dx) < DRIFT.tap && Math.abs(dy) < DRIFT.tap) return;
        d.moved = true;
        el.drift.classList.add('is-dragging');
        try { el.drift.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
      }
      drift.tx = d.tx + dx;
      drift.ty = d.ty + dy;
      d.samples.push({ t: e.timeStamp, x: e.clientX, y: e.clientY });
      while (d.samples.length > 2 && e.timeStamp - d.samples[0].t > 100) d.samples.shift();
      driftKick();
    });

    var endDrag = function (e) {
      var d = drift.drag;
      if (!d || e.pointerId !== d.id) return;
      drift.drag = null;
      el.drift.classList.remove('is-dragging');
      try { el.drift.releasePointerCapture(e.pointerId); } catch (err) { /* noop */ }
      if (!d.moved) return;
      drift.suppressClick = true; // ドラッグの終わりのクリックで写真を開かない
      setTimeout(function () { drift.suppressClick = false; }, 0);
      var a = d.samples[0];
      var b = d.samples[d.samples.length - 1];
      var span = b.t - a.t;
      if (e.type === 'pointerup' && d.type !== 'mouse' && span > 0 && e.timeStamp - b.t < 80) {
        // 指を離したときの勢いで少し先へ（画面の高さまで）
        var limit = drift.vh;
        drift.tx += clamp((b.x - a.x) / span * DRIFT.fling, -limit, limit);
        drift.ty += clamp((b.y - a.y) / span * DRIFT.fling, -limit, limit);
      }
      driftKick();
    };
    el.drift.addEventListener('pointerup', endDrag);
    el.drift.addEventListener('pointercancel', endDrag);
    el.drift.addEventListener('pointerleave', function (e) {
      if (e.pointerType === 'mouse' && drift.mouse) {
        drift.mx = 0.5;
        drift.my = 0.5;
        driftKick();
      }
    });
    el.drift.addEventListener('dragstart', function (e) { e.preventDefault(); });

    el.drift.addEventListener('click', function (e) {
      if (drift.suppressClick) {
        drift.suppressClick = false;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      var lb = e.target.closest ? e.target.closest('.drift-label') : null;
      if (lb && state.mode === 'drift') {
        reopenChooser(); // 散らした「Change view」: 見せ方を選び直す
        return;
      }
      var b = e.target.closest ? e.target.closest('.drift-tile') : null;
      if (!b || state.mode !== 'drift') return;
      openViewer(Number(b.getAttribute('data-index')), { from: 'drift', byKey: e.detail === 0 });
    }, true);

    // Tab キーで写真へ移ったら、その写真が見えるところまで動かす
    // （クリック・タップで付いたフォーカスや、全画面表示を閉じたときに戻るフォーカスでは動かさない）
    el.drift.addEventListener('focusin', function (e) {
      var b = e.target.closest ? e.target.closest('#drift-list .drift-tile') : null;
      var byKey = true;
      try { byKey = !!b && b.matches(':focus-visible'); } catch (err) { /* :focus-visible 非対応 */ }
      if (b && byKey && state.mode === 'drift' && !state.open && !drift.quietFocus) {
        driftReveal(driftOriginal(Number(b.getAttribute('data-index'))));
      }
      el.drift.scrollTop = 0; // フォーカスでブラウザーが中身をスクロールした場合は戻す（位置は transform で決める）
      el.drift.scrollLeft = 0;
    });
    el.drift.addEventListener('scroll', function () {
      el.drift.scrollTop = 0;
      el.drift.scrollLeft = 0;
    });
  }

  document.addEventListener('keydown', function (e) {
    if (!driftActive() || e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target;
    if (t && t.closest && t.closest('input, textarea, select')) return;
    var step = 140;
    var page = Math.round(drift.vh * 0.7);
    var map = {
      ArrowDown: [0, -step], ArrowUp: [0, step], ArrowRight: [-step, 0], ArrowLeft: [step, 0],
      PageDown: [0, -page], PageUp: [0, page]
    };
    if (e.key === ' ' && !(t && t.closest && t.closest('button, a'))) map[' '] = [0, e.shiftKey ? page : -page];
    var m = map[e.key];
    if (!m) return;
    e.preventDefault();
    driftMove(m[0], m[1]);
  });

  window.addEventListener('resize', function () {
    if (state.mode !== 'drift' || !drift.items.length) return;
    clearTimeout(drift.resizeTimer);
    drift.resizeTimer = setTimeout(function () {
      var vw = window.innerWidth, vh = window.innerHeight;
      // スマホのアドレスバーの出し入れ（高さだけ少し変わる）では並べ直さない
      if (vw === drift.vw && Math.abs(vh - drift.vh) < 120) {
        drift.vh = vh;
        driftKick();
        return;
      }
      var fx = drift.block.w ? drift.tx / drift.block.w : 0;
      var fy = drift.block.h ? drift.ty / drift.block.h : 0;
      driftBuild({ keepPlace: true });
      drift.cx = drift.tx = drift.lx = fx * drift.block.w;
      drift.cy = drift.ty = drift.ly = fy * drift.block.h;
      driftKick();
    }, 150);
  });

  // ---------------------------------------------------------------- パターン2: 列（v12）
  var elastic = { cols: [], raf: 0, last: 0, count: 0, saveTimer: 0, restoring: false };

  function elasticColumnCount() {
    return window.innerWidth >= ELASTIC.wide ? 7 : 3;
  }

  function elasticClear() {
    if (elastic.raf) cancelAnimationFrame(elastic.raf);
    elastic.raf = 0;
    elastic.last = 0;
    elastic.cols = [];
    elastic.count = 0;
    if (elastic.saveTimer) clearTimeout(elastic.saveTimer);
    elastic.saveTimer = 0;
    if (el.elastic) {
      if (state.developObserver) {
        Array.prototype.forEach.call(el.elastic.querySelectorAll('.eg-tile:not(.is-developed)'), function (b) {
          state.developObserver.unobserve(b);
        });
      }
      el.elastic.textContent = '';
      el.elastic.removeAttribute('data-moving');
    }
  }

  function makeElasticItem(p, i) {
    var fig = document.createElement('figure');
    fig.className = 'eg-item';
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'eg-tile';
    b.setAttribute('data-index', String(i));
    b.setAttribute('data-id', p.id);
    var img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.draggable = false;
    img.alt = p.alt;
    img.addEventListener('load', function () { markLoaded(b); });
    img.addEventListener('error', function () { b.classList.add('is-broken'); markLoaded(b); });
    img.src = p.mid || p.src;
    watchDevelop(b);
    var fb = document.createElement('span');
    fb.className = 'tile-fallback';
    fb.setAttribute('aria-hidden', 'true');
    fb.textContent = '表示できません';
    b.appendChild(img);
    b.appendChild(fb);
    var cap = document.createElement('figcaption');
    cap.className = 'eg-cap';
    cap.setAttribute('aria-hidden', 'true'); // 読み上げは写真の代替テキスト（撮影日時）で行う
    // 「47 - 2026.10.11」（v13 から「No.」は付けない）。列が細くて1行に収まらないときは番号と日付の2行にする（elasticFitCaptions）
    [['eg-no', formatNo(p.no)], ['eg-sep', ' - '], ['eg-date', formatDot(p.ms)]].forEach(function (part) {
      var s = document.createElement('span');
      s.className = part[0];
      s.textContent = part[1];
      cap.appendChild(s);
    });
    fig.appendChild(b);
    fig.appendChild(cap);
    return fig;
  }

  // 写真を列に分けて並べる（i 番目の写真は i % 列数 の列へ。横に読むと新しい順）
  function elasticBuild(opts) {
    elasticClear();
    if (!el.elastic || !state.list.length) return;
    var n = elasticColumnCount();
    var mid = (n - 1) / 2;
    var y = window.pageYOffset || 0;
    var wrap = document.createElement('div');
    wrap.className = 'eg-grid';
    wrap.style.setProperty('--eg-cols', String(n));
    for (var i = 0; i < n; i++) {
      var col = document.createElement('div');
      col.className = 'eg-col';
      // 中央の列ほど早く、外側の列ほど遅れてついてくる
      elastic.cols.push({ el: col, lag: ELASTIC.baseLag + Math.abs(i - mid) * ELASTIC.lagScale, y: y, shown: 0 });
      wrap.appendChild(col);
    }
    state.list.forEach(function (p, k) { elastic.cols[k % n].el.appendChild(makeElasticItem(p, k)); });
    el.elastic.setAttribute('aria-label', '写真');
    el.elastic.appendChild(wrap);
    elastic.count = n;
    elasticFitCaptions();
    if (opts && opts.restore) elasticRestore();
    elastic.cols.forEach(function (c) { c.y = window.pageYOffset || 0; });
  }

  // 番号と日付が1行に収まらない幅（スマホの3列・狭いPCの7列）では、全部の写真を2行にする。
  // 写真ごとに行数が違うと、止まったときに行がそろわなくなるため、1枚でもはみ出せば全部2行
  function elasticFitCaptions() {
    var grid = el.elastic ? el.elastic.querySelector('.eg-grid') : null;
    if (!grid) return;
    grid.classList.remove('is-stacked');
    var caps = grid.querySelectorAll('.eg-cap');
    var over = false;
    for (var i = 0; i < caps.length && !over; i++) over = caps[i].scrollWidth > caps[i].clientWidth;
    if (over) grid.classList.add('is-stacked');
  }

  function elasticKick() {
    if (!elastic.raf && elastic.cols.length) {
      el.elastic.setAttribute('data-moving', '1');
      elastic.raf = requestAnimationFrame(elasticFrame);
    }
  }

  function elasticFrame(now) {
    elastic.raf = 0;
    var dt = elastic.last ? Math.min(64, Math.max(1, now - elastic.last)) : 16.67;
    elastic.last = now;
    var y = window.pageYOffset || 0;
    var moving = false;
    elastic.cols.forEach(function (c) {
      // 列は lag 秒ほどかけて、いまのスクロール位置に追いつく
      c.y += (y - c.y) * (1 - Math.exp(-dt / (c.lag * 1000 / 3)));
      if (Math.abs(y - c.y) < 0.3) c.y = y;
      else moving = true;
      var off = Math.round((y - c.y) * 10) / 10;
      if (off !== c.shown) {
        c.el.style.transform = off ? 'translate3d(0,' + off + 'px,0)' : '';
        c.shown = off;
      }
    });
    if (moving) {
      elastic.raf = requestAnimationFrame(elasticFrame);
    } else {
      elastic.last = 0;
      el.elastic.removeAttribute('data-moving');
    }
  }

  window.addEventListener('scroll', function () {
    if (state.mode !== 'elastic' || !elastic.cols.length) return;
    elasticKick();
    if (state.open || elastic.restoring) return;
    if (elastic.saveTimer) clearTimeout(elastic.saveTimer);
    elastic.saveTimer = setTimeout(elasticSaveNow, ELASTIC.saveDelay);
  }, { passive: true });

  window.addEventListener('resize', function () {
    if (state.mode !== 'elastic' || !elastic.cols.length) return;
    if (elasticColumnCount() !== elastic.count) elasticBuild({ restore: false }); // 列の数が変わったときだけ並べ直す
    else elasticFitCaptions();
  });
  // 文字（DotGothic16）が読み込まれると幅が変わるので、もう一度確かめる
  if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === 'function') {
    document.fonts.ready.then(function () { if (state.mode === 'elastic') elasticFitCaptions(); }, function () {});
  }

  function elasticSaveNow() {
    if (elastic.saveTimer) clearTimeout(elastic.saveTimer);
    elastic.saveTimer = 0;
    if (state.mode !== 'elastic' || !state.ready || elastic.restoring) return;
    storageSet('sessionStorage', KEYS.elastic, JSON.stringify({ v: 2, y: Math.round(window.pageYOffset || 0) }));
  }

  function elasticRestore() {
    var raw = storageGet('sessionStorage', KEYS.elastic);
    if (!raw) return;
    var s;
    try { s = JSON.parse(raw); } catch (e) { storageRemove('sessionStorage', KEYS.elastic); return; }
    if (!s || s.v !== 2 || !(s.y > 0)) return; // v1（v12 までの年ごとの位置）は使わない
    elastic.restoring = true;
    window.scrollTo(0, s.y);
    requestAnimationFrame(function () { requestAnimationFrame(function () { elastic.restoring = false; }); });
  }

  function elasticFocus(index) {
    var b = el.elastic ? el.elastic.querySelector('.eg-tile[data-index="' + index + '"]') : null;
    if (!b) return;
    b.focus({ preventScroll: true });
    var r = b.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) b.scrollIntoView({ block: 'center' });
  }

  if (el.elastic) {
    el.elastic.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('.eg-tile') : null;
      if (b && state.mode === 'elastic') openViewer(Number(b.getAttribute('data-index')), { from: 'elastic' });
    });
  }

  // ---------------------------------------------------------------- パターン3: 揺れる（v14）
  // 斜めに張った紐に写真を吊るし（ふちなし・上の真ん中をクリップで留める）、写真の右横に札（番号と撮影日）を下げる。
  // ページは普通に縦へスクロールする。写真と札は振り子のように揺れる（大きい写真ほどゆっくり）。
  // スクロールの勢いで傾き、止まると数秒で収まる。ときどき左から右へそよ風が通り抜け、少しだけ揺らす。
  // 並び（紐の傾き・1本に吊るす枚数・大きさ）は写真ごとに決まっていて、再読込しても同じ（スクロール位置を戻すため）。
  var sway = { lines: [], width: 0, raf: 0, last: 0, lastY: 0, vel: 0, gust: null, gustTimer: 0, saveTimer: 0, restoring: false };

  function swayClear() {
    if (sway.raf) cancelAnimationFrame(sway.raf);
    sway.raf = 0;
    sway.last = 0;
    sway.lines = [];
    sway.vel = 0;
    sway.gust = null;
    clearTimeout(sway.gustTimer);
    sway.gustTimer = 0;
    clearTimeout(sway.saveTimer);
    sway.saveTimer = 0;
    if (state.developObserver && el.swayList) {
      Array.prototype.forEach.call(el.swayList.querySelectorAll('.sw-tile:not(.is-developed)'), function (b) {
        state.developObserver.unobserve(b);
      });
    }
    if (el.swayLines) el.swayLines.textContent = '';
    if (el.swayList) el.swayList.textContent = '';
    if (el.swayTags) el.swayTags.textContent = '';
    if (el.sway) {
      el.sway.style.height = '';
      el.sway.removeAttribute('data-moving');
    }
  }

  // 画面の幅で決まる大きさ（スマホは1本に1〜2枚、タブレットは2〜4枚、PC は3〜6枚）
  function swayMetrics(W, vh) {
    var phone = W < 640, wide = W >= 1000;
    return {
      side: Math.max(16, Math.round(W * 0.04)),
      kMin: phone ? 1 : (wide ? 3 : 2),
      kMax: phone ? 2 : (wide ? 6 : 4),
      uMin: phone ? 96 : 140,
      uMax: phone ? W * 0.52 : clamp(W * 0.2, 200, 320),
      hMax: Math.max(160, vh * 0.6),
      top: phone ? 40 : 56
    };
  }

  function makeSwayTag(kind, lines, x, ay, w, thread, extra) {
    var t = document.createElement('div');
    t.className = 'sw-tag sw-tag-' + kind;
    t.style.left = Math.round(x - w / 2) + 'px';
    t.style.top = Math.round(ay) + 'px';
    t.style.width = w + 'px';
    var th = document.createElement('span');
    th.className = 'sw-thread';
    th.style.height = thread + 'px';
    var card = document.createElement('span');
    card.className = 'sw-card';
    if (extra) card.appendChild(extra);
    lines.forEach(function (text) {
      var s = document.createElement('span');
      s.textContent = text;
      card.appendChild(s);
    });
    t.appendChild(th);
    t.appendChild(card);
    return t;
  }

  function makeSwayItem(p, index, w, h, ax, ay) {
    var li = document.createElement('li');
    li.className = 'sw-item';
    li.style.left = Math.round(ax - w / 2) + 'px';
    li.style.top = Math.round(ay - SWAY.clip / 2) + 'px';
    li.style.width = w + 'px';
    var clipEl = document.createElement('span');
    clipEl.className = 'sw-clip';
    clipEl.setAttribute('aria-hidden', 'true');
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'sw-tile';
    b.style.height = h + 'px';
    b.setAttribute('data-index', String(index));
    b.setAttribute('data-id', p.id);
    var img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.draggable = false;
    img.width = w;
    img.height = h;
    img.alt = p.alt;
    img.addEventListener('load', function () { markLoaded(b); });
    img.addEventListener('error', function () { b.classList.add('is-broken'); markLoaded(b); });
    img.src = p.mid || p.src;
    watchDevelop(b);
    var fb = document.createElement('span');
    fb.className = 'tile-fallback';
    fb.setAttribute('aria-hidden', 'true');
    fb.textContent = '表示できません';
    b.appendChild(img);
    b.appendChild(fb);
    li.appendChild(clipEl);
    li.appendChild(b);
    return li;
  }

  function swayBuild(opts) {
    swayClear();
    if (!el.sway || !state.list.length) return;
    // 先に高さを取って縦のスクロールバーが出た状態で幅を測る（PC でスクロールバーの分だけ右が切れないように）
    el.sway.style.height = Math.round((window.innerHeight || 600) * 2) + 'px';
    var W = el.sway.clientWidth || document.documentElement.clientWidth || window.innerWidth;
    var vh = window.innerHeight || 600;
    var m = swayMetrics(W, vh);
    var avail = W - m.side * 2;
    var list = state.list;
    var slot = SWAY.tagGap + SWAY.tagW + SWAY.itemGap; // 写真1枚ごとに札とすき間が要る幅
    var ys = state.years;
    var rangeLines = !ys.length ? [] : (ys.length === 1 ? [String(ys[0])] : [String(ys[ys.length - 1]), '–', String(ys[0])]);
    var lineFrag = document.createDocumentFragment();
    var listFrag = document.createDocumentFragment();
    var tagFrag = document.createDocumentFragment();
    var prev = null;
    var next = 0;
    var height = 0;
    for (var li = 0; next < list.length; li++) {
      var key = 'sway-line-' + li;
      // 紐の傾き: 2〜7度。上下の紐で向きを変えることが多い
      var sign = (li % 2 ? 1 : -1) * (stable(key, 2) < 0.8 ? 1 : -1);
      var deg = (2 + stable(key, 1) * 5) * sign;
      var slope = Math.tan(deg * Math.PI / 180);
      // 最初の紐には、撮影年の範囲と「Change view」の札を左から吊るす
      var lead = li === 0 ? [{ kind: 'range', w: SWAY.tagW }, { kind: 'switch', w: SWAY.labelW }] : [];
      var leadW = lead.reduce(function (sum, t) { return sum + t.w + SWAY.itemGap; }, 0);
      var k = Math.min(list.length - next, m.kMin + Math.floor(stable(key, 3) * (m.kMax - m.kMin + 1)));
      var photos, u;
      for (;;) { // 写真が小さくなりすぎるときは、1本に吊るす枚数を減らす
        photos = [];
        var sumF = 0;
        for (var j = 0; j < k; j++) {
          var p = list[next + j];
          var ar = (p.width && p.height) ? p.width / p.height : 1;
          var f = 0.82 + stable(p.id, 4) * 0.28;
          photos.push({ p: p, index: next + j, ar: ar, f: f });
          sumF += f * Math.sqrt(ar);
        }
        u = Math.min(m.uMax, (avail - leadW - k * slot) / sumF);
        if (u >= m.uMin || k <= 1) break;
        k--;
      }
      u = Math.max(u, 40);
      var used = leadW;
      photos.forEach(function (ph) {
        var w = u * ph.f * Math.sqrt(ph.ar), h = u * ph.f / Math.sqrt(ph.ar);
        if (h > m.hMax) { w *= m.hMax / h; h = m.hMax; }
        if (w > avail - leadW - slot) { h *= (avail - leadW - slot) / w; w = avail - leadW - slot; }
        ph.w = Math.max(32, Math.round(w));
        ph.h = Math.max(32, Math.round(h));
        used += ph.w + slot;
      });
      // 余った幅は、ばらばらのすき間として配る（両端までそろえない）
      var spare = Math.max(0, avail - used);
      var nParts = lead.length + photos.length + 1;
      var weights = [], wsum = 0;
      for (var q = 0; q < nParts; q++) { weights.push(0.2 + stable(key, 10 + q)); wsum += weights[q]; }
      var things = [];
      var x = m.side + spare * weights[0] / wsum;
      var part = 1;
      lead.forEach(function (t) {
        things.push({ kind: t.kind, w: t.w, x: x + t.w / 2, x0: x, x1: x + t.w });
        x += t.w + SWAY.itemGap + spare * weights[part++] / wsum;
      });
      photos.forEach(function (ph) {
        things.push({ kind: 'photo', ph: ph, x: x + ph.w / 2, x0: x, x1: x + ph.w });
        var tx = x + ph.w + SWAY.tagGap + SWAY.tagW / 2;
        things.push({ kind: 'tag', ph: ph, w: SWAY.tagW, x: tx, x0: tx - SWAY.tagW / 2, x1: tx + SWAY.tagW / 2 });
        x += ph.w + slot + spare * weights[part++] / wsum;
      });
      // 札の糸の長さ（写真ごとに少し違う）
      things.forEach(function (t, n) {
        t.thread = t.kind === 'photo' ? 0 : Math.round(8 + stable(t.ph ? t.ph.p.id : key + t.kind, 5 + n) * 20);
        t.drop = t.kind === 'photo' ? SWAY.clip / 2 - 4 + t.ph.h : t.thread + (t.kind === 'switch' ? SWAY.labelH : SWAY.tagH);
      });
      // 紐の高さ: 1本目は上に余白を空け、2本目からは前の紐に吊るしたものの下に間を空ける（紐どうしは交差しない）
      var yMid;
      if (!prev) {
        yMid = m.top + Math.abs(slope) * W / 2 + SWAY.clip;
      } else {
        yMid = -Infinity;
        prev.things.forEach(function (t) {
          var bottom = prev.y(t.x) + t.drop + SWAY.lineGap;
          var reach = t.kind === 'photo' ? t.ph.h * 0.25 : 8; // 揺れて横へはみ出す分
          [t.x0 - reach, t.x1 + reach].forEach(function (ex) {
            yMid = Math.max(yMid, bottom - slope * (ex - W / 2));
          });
        });
        [0, W].forEach(function (ex) { yMid = Math.max(yMid, prev.y(ex) + 48 - slope * (ex - W / 2)); });
      }
      var line = {
        slope: slope, yMid: yMid, things: things, bodies: [], top: Infinity, bottom: -Infinity,
        y: (function (s, ym) { return function (px) { return ym + s * (px - W / 2); }; })(slope, yMid)
      };
      // 紐（画面の端より少し外まで）
      var lx0 = -40, lx1 = W + 40;
      var seg = document.createElement('div');
      seg.className = 'sw-line';
      seg.style.left = lx0 + 'px';
      seg.style.top = line.y(lx0).toFixed(1) + 'px';
      seg.style.width = Math.ceil((lx1 - lx0) / Math.cos(Math.atan(slope))) + 'px';
      seg.style.transform = 'rotate(' + deg.toFixed(3) + 'deg)';
      lineFrag.appendChild(seg);
      things.forEach(function (t) {
        var ay = line.y(t.x);
        var node, len, id;
        if (t.kind === 'photo') {
          node = makeSwayItem(t.ph.p, t.ph.index, t.ph.w, t.ph.h, t.x, ay);
          listFrag.appendChild(node);
          len = t.ph.h * 0.7 + 10;
          id = t.ph.p.id;
        } else {
          var extra = null, lines = [];
          if (t.kind === 'tag') {
            var dot = formatDot(t.ph.p.ms);
            lines = [formatNo(t.ph.p.no), dot.slice(0, 4), dot.slice(5)];
            id = t.ph.p.id + '-tag';
          } else if (t.kind === 'range') {
            lines = rangeLines;
            id = 'range';
          } else {
            extra = document.createElement('button');
            extra.type = 'button';
            extra.className = 'sw-label';
            extra.tabIndex = -1;
            extra.lang = 'en';
            extra.textContent = el.patternSwitch ? el.patternSwitch.textContent : 'Change view';
            id = 'switch';
          }
          node = makeSwayTag(t.kind, lines, t.x, ay, t.w, t.thread, extra);
          tagFrag.appendChild(node);
          len = t.thread + SWAY.tagH / 2 + 6;
        }
        var w0 = 2 * Math.PI / (SWAY.period * Math.sqrt(len / SWAY.lenRef));
        line.bodies.push({
          el: node, x: t.x, w0: w0, th: 0, om: 0, shown: 0,
          dir: (slope >= 0 ? 1 : -1) * (0.6 + stable(id, 6) * 0.8),
          wind: 0.7 + stable(id, 7) * 0.6
        });
        line.top = Math.min(line.top, ay - SWAY.clip);
        line.bottom = Math.max(line.bottom, ay + t.drop);
      });
      height = Math.max(height, line.bottom, line.y(0), line.y(W));
      sway.lines.push(line);
      prev = line;
      next += photos.length;
    }
    sway.width = W;
    el.swayLines.appendChild(lineFrag);
    el.swayList.setAttribute('aria-label', '写真');
    el.swayList.appendChild(listFrag);
    el.swayTags.appendChild(tagFrag);
    el.sway.style.height = Math.ceil(height + 140) + 'px';
    sway.lastY = window.pageYOffset || 0;
    if (opts && opts.restore) swayRestore();
    else if (opts && opts.ratio) window.scrollTo(0, Math.round(opts.ratio * el.sway.offsetHeight));
    sway.lastY = window.pageYOffset || 0;
    swayScheduleGust(2500 + Math.random() * 2500);
  }

  function swayKick() {
    if (!sway.raf && sway.lines.length) {
      el.sway.setAttribute('data-moving', '1');
      sway.raf = requestAnimationFrame(swayFrame);
    }
  }

  // そよ風: 間をあけて、左から右へ通り抜ける
  function swayScheduleGust(delay) {
    clearTimeout(sway.gustTimer);
    sway.gustTimer = setTimeout(function () {
      sway.gustTimer = 0;
      if (state.mode !== 'sway' || !sway.lines.length) return;
      if (state.open || document.visibilityState === 'hidden') { swayScheduleGust(SWAY.gustEvery[0]); return; }
      sway.gust = { start: performance.now() };
      swayKick();
    }, delay !== undefined ? delay : SWAY.gustEvery[0] + Math.random() * (SWAY.gustEvery[1] - SWAY.gustEvery[0]));
  }

  function swayFrame(now) {
    sway.raf = 0;
    var dt = sway.last ? Math.min(48, Math.max(1, now - sway.last)) : 16.67;
    sway.last = now;
    var y = window.pageYOffset || 0;
    var v = state.open ? 0 : (y - sway.lastY) / dt; // スクロールの速さ（px/ms）
    sway.lastY = y;
    sway.vel += (v - sway.vel) * Math.min(1, dt / 50);
    if (Math.abs(sway.vel) < 0.002) sway.vel = 0;
    var top = el.sway.getBoundingClientRect().top;
    var vh = window.innerHeight;
    var lo = -top - 200, hi = -top + vh + 200; // 画面の近くの紐だけ動かす
    var gx = null;
    if (sway.gust) {
      gx = (now - sway.gust.start) * SWAY.gustSpeed - SWAY.gustWidth;
      if (gx > sway.width + SWAY.gustWidth) {
        sway.gust = null;
        gx = null;
        swayScheduleGust();
      }
    }
    var active = sway.vel !== 0 || gx !== null;
    var damp = 2 / SWAY.decay;
    var steps = Math.ceil(dt / 8), h = dt / steps;
    for (var i = 0; i < sway.lines.length; i++) {
      var line = sway.lines[i];
      if (line.bottom < lo || line.top > hi) {
        // 画面から離れた紐は、揺れを止めてまっすぐに戻しておく（次に見えたときは静かな状態から）
        if (line.near) {
          line.near = false;
          line.bodies.forEach(function (b) { b.th = 0; b.om = 0; if (b.shown) { b.el.style.transform = ''; b.shown = 0; } });
        }
        continue;
      }
      line.near = true;
      for (var k = 0; k < line.bodies.length; k++) {
        var b = line.bodies[k];
        var w2 = b.w0 * b.w0;
        var force = SWAY.scroll * sway.vel * b.dir * w2; // 速さに比例して傾く（止まると戻って揺れる）
        if (gx !== null) {
          var d = gx - b.x;
          if (Math.abs(d) < SWAY.gustWidth / 2) force += SWAY.gustTilt * w2 * b.wind * Math.cos(Math.PI * d / SWAY.gustWidth);
        }
        for (var s = 0; s < steps; s++) {
          b.om += (-w2 * b.th - damp * b.om + force) * h;
          b.th += b.om * h;
        }
        if (b.th > SWAY.maxTilt) { b.th = SWAY.maxTilt; b.om = Math.min(0, b.om); }
        else if (b.th < -SWAY.maxTilt) { b.th = -SWAY.maxTilt; b.om = Math.max(0, b.om); }
        if (!force && Math.abs(b.th) < 0.0004 && Math.abs(b.om) < 0.00002) { b.th = 0; b.om = 0; }
        else active = true;
        var deg = Math.round(b.th * 5729.58) / 100;
        if (deg !== b.shown) {
          b.el.style.transform = deg ? 'rotate(' + deg + 'deg)' : '';
          b.shown = deg;
        }
      }
    }
    if (active) {
      sway.raf = requestAnimationFrame(swayFrame);
    } else {
      sway.last = 0;
      el.sway.removeAttribute('data-moving');
    }
  }

  window.addEventListener('scroll', function () {
    if (state.mode !== 'sway' || !sway.lines.length) return;
    swayKick();
    if (state.open || sway.restoring) return;
    clearTimeout(sway.saveTimer);
    sway.saveTimer = setTimeout(swaySaveNow, SWAY.saveDelay);
  }, { passive: true });

  window.addEventListener('resize', function () {
    if (state.mode !== 'sway' || !sway.lines.length) return;
    var W = el.sway.clientWidth;
    if (W === sway.width) return; // 高さだけ変わった（スマホのアドレスバーの出し入れ）ときは並べ直さない
    var ratio = (window.pageYOffset || 0) / Math.max(1, el.sway.offsetHeight);
    swayBuild({ ratio: ratio });
  });

  document.addEventListener('visibilitychange', function () {
    if (state.mode === 'sway' && document.visibilityState === 'visible' && sway.lines.length && !sway.gustTimer && !sway.gust) swayScheduleGust();
  });

  function swaySaveNow() {
    clearTimeout(sway.saveTimer);
    sway.saveTimer = 0;
    if (state.mode !== 'sway' || !state.ready || sway.restoring) return;
    storageSet('sessionStorage', KEYS.sway, JSON.stringify({ v: 1, y: Math.round(window.pageYOffset || 0) }));
  }

  function swayRestore() {
    var raw = storageGet('sessionStorage', KEYS.sway);
    if (!raw) return;
    var s;
    try { s = JSON.parse(raw); } catch (e) { storageRemove('sessionStorage', KEYS.sway); return; }
    if (!s || s.v !== 1 || !(s.y > 0)) return;
    sway.restoring = true;
    window.scrollTo(0, s.y);
    requestAnimationFrame(function () { requestAnimationFrame(function () { sway.restoring = false; }); });
  }

  function swayFocus(index) {
    var b = el.swayList ? el.swayList.querySelector('.sw-tile[data-index="' + index + '"]') : null;
    if (!b) return;
    b.focus({ preventScroll: true });
    var r = b.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) b.scrollIntoView({ block: 'center' });
  }

  if (el.sway) {
    el.sway.addEventListener('click', function (e) {
      if (state.mode !== 'sway') return;
      var lb = e.target.closest ? e.target.closest('.sw-label') : null;
      if (lb) { reopenChooser(); return; } // 札の「Change view」: 見せ方を選び直す
      var b = e.target.closest ? e.target.closest('.sw-tile') : null;
      if (b) openViewer(Number(b.getAttribute('data-index')), { from: 'sway' });
    });
  }

  // ---------------------------------------------------------------- 見せ方の切り替え（パターン・視差効果を減らす設定）
  function wantedMode() {
    if (motionQuery && motionQuery.matches) return 'still';
    if (!state.pattern) return 'choose';
    return state.pattern === '3' ? 'sway' : (state.pattern === '2' ? 'elastic' : 'drift');
  }

  function applyMode() {
    var mode = wantedMode();
    if (mode === state.mode) return;
    var first = state.mode === null;
    if (!first) {
      closeViewer({ restoreFocus: false });
      saveScrollNow();
    }
    state.mode = mode;
    document.documentElement.setAttribute('data-mode', mode);
    if (el.patternSwitch) el.patternSwitch.hidden = !(mode === 'drift' || mode === 'elastic' || mode === 'sway');
    if (mode === 'choose') openChooser();
    else closeChooser();
    if (!first && state.ready) {
      window.scrollTo(0, 0);
      renderList({ restore: true });
    }
  }

  function openChooser() {
    var d = el.chooser;
    if (!d || d.open) return;
    var last = storageGet('localStorage', KEYS.patternLast);
    var opts = d.querySelectorAll('.chooser-opt');
    Array.prototype.forEach.call(opts, function (b) {
      b.classList.toggle('is-last', b.getAttribute('data-pattern') === last);
    });
    try {
      if (typeof d.showModal === 'function') d.showModal();
      else d.setAttribute('open', '');
    } catch (e) {
      d.setAttribute('open', '');
    }
    var focus = d.querySelector('.chooser-opt.is-last') || opts[0];
    if (focus) focus.focus({ preventScroll: true });
  }

  function closeChooser() {
    var d = el.chooser;
    if (!d || !(d.open || d.hasAttribute('open'))) return;
    try {
      if (typeof d.close === 'function') d.close();
      else d.removeAttribute('open');
    } catch (e) {
      d.removeAttribute('open');
    }
  }

  function choosePattern(p) {
    if (PATTERNS.indexOf(p) < 0) return;
    state.pattern = p;
    storageSet('sessionStorage', KEYS.pattern, p);
    storageSet('localStorage', KEYS.patternLast, p);
    storageRemove('sessionStorage', KEYS.elastic); // 選び直したときは最初の位置から
    storageRemove('sessionStorage', KEYS.sway);
    applyMode();
  }

  if (el.chooser) {
    el.chooser.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('.chooser-opt') : null;
      if (b) choosePattern(b.getAttribute('data-pattern'));
    });
    var chooseDefault = function () { // 前回のパターン（初めてなら1）で見る
      var last = storageGet('localStorage', KEYS.patternLast);
      choosePattern(PATTERNS.indexOf(last) >= 0 ? last : '1');
    };
    el.chooser.addEventListener('cancel', function (e) { // Escape
      e.preventDefault();
      chooseDefault();
    });
    el.chooser.addEventListener('close', function () { // ほかの経路で閉じられたときも一覧が空のままにならないように
      if (state.mode === 'choose') chooseDefault();
    });
  }
  // 「Change view」: 見せ方を選ぶ画面をもう一度出す
  function reopenChooser() {
    if (state.mode !== 'drift' && state.mode !== 'elastic' && state.mode !== 'sway') return;
    saveScrollNow();
    state.pattern = null;
    storageRemove('sessionStorage', KEYS.pattern);
    applyMode();
  }
  if (el.patternSwitch) el.patternSwitch.addEventListener('click', reopenChooser);

  // ---------------------------------------------------------------- 全画面スライド（S-07）
  function openViewer(index, opts) {
    var list = (opts && opts.list) || state.list;
    if (!list.length) return;
    index = clamp(index | 0, 0, list.length - 1);
    state.viewList = list;
    state.viewFrom = (opts && opts.from) || 'grid';
    cancelSwipe();
    cancelPendingSave();
    if (!state.open) {
      saveScrollNow();
      state.open = true;
      state.viewOpened = index;
      state.viewByKey = !!(opts && opts.byKey);
      document.documentElement.classList.add('viewer-open');
      try {
        if (typeof el.viewer.showModal === 'function') el.viewer.showModal();
        else el.viewer.setAttribute('open', '');
      } catch (e) {
        el.viewer.setAttribute('open', '');
      }
      el.close.focus({ preventScroll: true });
    }
    state.index = index;
    showSlide();
  }

  function closeViewer(opts) {
    if (!state.open) return; // 連打しても1回だけ処理
    state.open = false;
    drift.quietFocus = true; // 閉じたときにブラウザーが戻すフォーカスで一覧を動かさない（動かすかは下で決める）
    cancelSwipe();
    state.slideToken++; // 遅れて届く読込イベントを無視
    clearTimeout(state.spinnerTimer);
    if (el.viewer.open || el.viewer.hasAttribute('open')) {
      try {
        if (typeof el.viewer.close === 'function') el.viewer.close();
        else el.viewer.removeAttribute('open');
      } catch (e) {
        el.viewer.removeAttribute('open');
      }
    }
    document.documentElement.classList.remove('viewer-open');
    el.img.onload = null;
    el.img.onerror = null;
    el.img.removeAttribute('src');
    el.stage.classList.remove('is-loading', 'is-error', 'is-dragging');
    el.spinner.hidden = true;
    el.error.hidden = true;
    state.preload = [];
    drift.quietFocus = false;
    if (!opts || opts.restoreFocus !== false) {
      if (state.viewFrom === 'cover' && !el.cover.hidden) el.coverBtn.focus({ preventScroll: true });
      // 動く一覧: 開いた写真のまま閉じたときは画面を動かさない（タップした複製の位置のまま）。
      // キーボードで開いた・別の写真へ送ったときは、その写真が見える所まで動かす
      else if (state.mode === 'drift') driftFocus(state.index, state.viewByKey || state.index !== state.viewOpened);
      else if (state.mode === 'elastic') elasticFocus(state.index);
      else if (state.mode === 'sway') swayFocus(state.index);
      else focusTile(state.index);
    }
  }

  function focusTile(index) {
    if (index < 0 || index >= state.list.length) return;
    ensureRendered(index);
    var b = el.grid.querySelector('.tile[data-index="' + index + '"]');
    if (!b) return;
    b.focus({ preventScroll: true });
    var r = b.getBoundingClientRect();
    if (r.top < headerBottom() || r.bottom > window.innerHeight) b.scrollIntoView({ block: 'center' });
  }

  function slideDone(token, failed) {
    if (token !== state.slideToken) return;
    clearTimeout(state.spinnerTimer);
    el.spinner.hidden = true;
    el.stage.classList.remove('is-loading');
    if (failed) {
      el.stage.classList.add('is-error');
      el.error.hidden = false;
    }
  }

  function showSlide() {
    var p = state.viewList[state.index];
    if (!p) return;
    var token = ++state.slideToken;
    var n = state.viewList.length;
    el.stage.classList.add('is-loading');
    el.stage.classList.remove('is-error');
    el.error.hidden = true;
    clearTimeout(state.spinnerTimer);
    state.spinnerTimer = setTimeout(function () {
      if (token === state.slideToken && el.stage.classList.contains('is-loading')) el.spinner.hidden = false;
    }, CONFIG.spinnerDelay);

    el.img.onload = function () { slideDone(token, false); };
    el.img.onerror = function () { slideDone(token, true); };
    el.img.style.transform = '';
    el.img.alt = p.alt;
    if (p.width && p.height) {
      el.img.width = p.width;
      el.img.height = p.height;
    } else {
      el.img.removeAttribute('width');
      el.img.removeAttribute('height');
    }
    el.img.setAttribute('data-id', p.id);
    el.img.src = p.src;
    if (el.img.complete && el.img.naturalWidth) slideDone(token, false);

    el.counter.textContent = (state.index + 1) + ' / ' + n;
    el.no.textContent = formatNo(p.no); // 目録番号（v13 から「No.」は付けない）
    el.caption.textContent = formatDateTime(p.ms) + (p.dateSource === 'mtime' ? '（ファイル更新日時）' : '');
    var focused = document.activeElement;
    el.prev.disabled = state.index === 0;
    el.next.disabled = state.index === n - 1;
    if (focused && focused.disabled) {
      var alt = focused === el.prev ? el.next : el.prev;
      (alt.disabled ? el.close : alt).focus({ preventScroll: true });
    }
    // 前後の画像を先読み
    state.preload = [state.index - 1, state.index + 1].filter(function (i) { return i >= 0 && i < n; }).map(function (i) {
      var im = new Image();
      im.decoding = 'async';
      im.src = state.viewList[i].src;
      return im;
    });
  }

  function go(delta) {
    if (!state.open) return false;
    var n = state.index + delta;
    if (n < 0 || n >= state.viewList.length) return false;
    state.index = n;
    showSlide();
    return true;
  }

  el.prev.addEventListener('click', function () { go(-1); });
  el.next.addEventListener('click', function () { go(1); });
  el.close.addEventListener('click', function () { closeViewer(); });

  el.viewer.addEventListener('cancel', function (e) { // Escape
    e.preventDefault();
    closeViewer();
  });
  el.viewer.addEventListener('close', function () { // 他の経路で閉じられた場合も状態を同期
    if (state.open) closeViewer();
  });

  document.addEventListener('keydown', function (e) {
    if (!state.open) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
    else if (e.key === 'Home') { e.preventDefault(); go(-state.index); }
    else if (e.key === 'End') { e.preventDefault(); go(state.viewList.length - 1 - state.index); }
    else if (e.key === 'Escape' || e.key === 'Esc') { e.preventDefault(); closeViewer(); }
    else if (e.key === 'Tab') {
      // フォーカスを全画面表示の中で循環させる（ブラウザーのUIへ抜けないようにする）
      var items = [el.close, el.prev, el.next].filter(function (b) { return !b.disabled; });
      var i = items.indexOf(document.activeElement);
      e.preventDefault();
      var j = i < 0 ? 0 : (i + (e.shiftKey ? -1 : 1) + items.length) % items.length;
      items[j].focus();
    }
  });

  // ---------------------------------------------------------------- スワイプ
  function cancelSwipe() {
    var s = state.swipe;
    state.swipe = null;
    el.stage.classList.remove('is-dragging');
    el.img.style.transform = '';
    if (s && el.stage.releasePointerCapture) {
      try { el.stage.releasePointerCapture(s.id); } catch (e) { /* noop */ }
    }
  }

  el.stage.addEventListener('pointerdown', function (e) {
    if (!state.open || state.swipe) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (e.target.closest && e.target.closest('.viewer-nav')) return;
    state.swipe = { id: e.pointerId, x: e.clientX, y: e.clientY, t: Date.now(), dx: 0, axis: null };
    try { el.stage.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
  });

  el.stage.addEventListener('pointermove', function (e) {
    var s = state.swipe;
    if (!s || e.pointerId !== s.id || !state.open) return;
    var dx = e.clientX - s.x;
    var dy = e.clientY - s.y;
    if (!s.axis) {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
      s.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
      if (s.axis === 'x') el.stage.classList.add('is-dragging');
    }
    if (s.axis !== 'x') return;
    e.preventDefault();
    s.dx = dx;
    var atEdge = (dx > 0 && state.index === 0) || (dx < 0 && state.index === state.viewList.length - 1);
    el.img.style.transform = 'translate3d(' + (atEdge ? dx / 3 : dx) + 'px,0,0)';
  });

  function endSwipe(e) {
    var s = state.swipe;
    if (!s || e.pointerId !== s.id) return;
    state.swipe = null;
    try { el.stage.releasePointerCapture(s.id); } catch (err) { /* noop */ }
    if (state.open && s.axis === 'x') {
      var width = el.stage.clientWidth || window.innerWidth;
      var dt = Math.max(1, Date.now() - s.t);
      var far = Math.abs(s.dx) > Math.max(CONFIG.swipeMinPx, width * CONFIG.swipeRatio);
      var fast = Math.abs(s.dx) > 30 && Math.abs(s.dx) / dt > CONFIG.swipeVelocity;
      if ((far || fast) && go(s.dx < 0 ? 1 : -1)) {
        // 新しい写真は位置リセットをアニメーションさせない
        el.img.style.transform = '';
        requestAnimationFrame(function () { el.stage.classList.remove('is-dragging'); });
        return;
      }
    }
    el.stage.classList.remove('is-dragging');
    el.img.style.transform = '';
  }

  el.stage.addEventListener('pointerup', endSwipe);
  el.stage.addEventListener('pointercancel', function (e) {
    if (state.swipe && e.pointerId === state.swipe.id) cancelSwipe();
  });

  // ---------------------------------------------------------------- 起動
  el.retry.addEventListener('click', function () { load(); });
  RETIRED_KEYS.forEach(function (k) { storageRemove(k[0], k[1]); }); // v12 までの保存値（年・パターン1の位置）
  (function () {
    var saved = storageGet('sessionStorage', KEYS.pattern); // 同じタブでは選んだパターンのまま
    if (PATTERNS.indexOf(saved) >= 0) state.pattern = saved;
  })();
  applyMode();
  if (motionQuery) {
    if (motionQuery.addEventListener) motionQuery.addEventListener('change', applyMode);
    else if (motionQuery.addListener) motionQuery.addListener(applyMode); // 古い Safari
  }
  load();
})();
