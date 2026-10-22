/*
 * 写真ビューア（同期仕様ID PV-SPEC-2026-10-v12 / 版 12.0）
 * HTML / CSS / Vanilla JavaScript のみ。外部CDN・ライブラリなし。URLはすべて相対（GitHub Pages のサブパスで動作）。
 *
 * ■ 状態保存と再読込の仕様
 *   - 年タブ … 写真のある年だけを新しい順に並べる（v7 で「すべて」を廃止）。初めて開いたときはいちばん新しい年。
 *   - 選択中の年タブ … localStorage「pv:selectedYear」に保存。再読込・再訪問で復元する。
 *       保存した年がデータに存在しない場合（旧版の「all」を含む）はいちばん新しい年を表示し、保存値は消す。
 *   - スクロール位置 … sessionStorage「pv:scroll」に保存（表示中の年について1つだけ）。
 *       同じタブでの再読込・戻る操作では復元し、新しいタブ・別の日の再訪問では先頭から表示する。
 *       位置は「画面上端の写真ID＋ずれ(px)」で保存するため、画面幅や写真の追加で多少変わっても同じ写真付近へ戻る。
 *       年タブを切り替えたときは、その年の一覧の先頭（表紙の下）を上部バーのすぐ下に表示する
 *       （保存位置も新しい年の先頭で上書き）。矢印キーで年を移動しているときは画面を動かさない。
 *   - 全画面表示中の写真 … 保存しない。再読込すると一覧（保存した年・位置）に戻る。
 *   - 写真データ … data/photos.json を毎回再検証付き（cache: no-cache）で取得する。
 *   - 保存はスクロール停止後 200ms のタイマーと、ページを離れる時（pagehide / 非表示化）に行う。
 *       年タブ切替時は保留中のタイマーを取り消し、古い年の位置が新しい年として保存される競合を防ぐ。
 *   - プライベートブラウズ等でストレージを使えない場合は保存せずに動作する。
 *
 * ■ 演出（v8）
 *   - 現像 … 一覧と表紙の写真は「読み込み済み」かつ「画面に入った」ときに is-developed を付け、
 *       白くぼやけた像から色と輪郭が浮かぶように表示する（css/style.css）。視差効果を減らす設定では動かさない。
 *   （v8 のサイト名の書き順は v11 で廃止。サイト名は画面に出さず、読み上げとブラウザーのタブの名前にだけ使う）
 *   （v8 の小話は v9 で廃止。photos.json に story があっても表示しない）
 *
 * ■ 一覧の2つの見せ方（v10）
 *   - 動く一覧（data-mode="drift"。ふだんはこちら）… 選んだ年の写真を、元の縦横比のまま大小ばらばらに
 *       終わりのない平面へ散らす。スクロール（ホイール・トラックパッド）・ドラッグ・スワイプで上下左右に動かすと、
 *       写真ごとに少しずつ違う速さで追いかけ（各写真に 0.5〜1 の係数）、止まると元の並びに戻る。
 *       端まで行くと反対側から同じ並びが続く（写真が少ないときは同じ並びを複製して画面を埋める。複製は読み上げない）。
 *       開いた直後・年を切り替えたときは、画面の中央に重なった写真が自分の場所へ広がっていく。
 *       マウスのある端末では、マウスの位置に合わせて写真が大きさに応じて少しずれる（奥行き）。
 *       番号と撮影日は、写真が画面に入るたびに1行ずつ下から現れる。画像は中間画像（mids/、長辺800px）を使う。
 *       位置は sessionStorage「pv:drift」に（年と、並び1周に対する割合で）保存し、同じタブの再読込・戻る操作で復元する
 *       （復元したときは広がる動きをしない）。
 *   - 動かない一覧（data-mode="still"）… 端末の「視差効果を減らす」設定がオンのとき。表紙と正方形の一覧を
 *       縦にスクロールする（v9 までと同じ）。現像も動かさない。設定を途中で変えると、その場で切り替わる。
 *
 * ■ 年（v11）
 *   - 動く一覧では、年の文字（2026・2025 …）も写真と同じように平面へ散らし、写真と同じ動きをさせる。
 *       タップするとその年に切り替わる。選んでいる年には下線。だいたい1画面に1つ見える数を置く（年の数より少なくはしない）。
 *       上部のバー（年のタブ）は見えないようにし、キーボードでフォーカスしたときだけ出す（Tab で年を選べるように）。
 *       散らした年の文字は読み上げ・Tab の対象にしない（同じ内容を上部のバーの年のタブで読み上げる）。
 *   - 動かない一覧では、上部のバーの右側に年を並べる（v10 までと同じ）。
 *
 * ■ 見せ方のパターン（v12）
 *   - 開いたときに「表示を選ぶ」画面で選ぶ。パターン1 = 散らばる（動く一覧、data-mode="drift"）、
 *       パターン2 = 列（data-mode="elastic"。Codrops の作例「Elastic Grid Scroll」の動きの考え方を参考に書いたもの）。
 *       選んだパターンは sessionStorage「pv:pattern」に保存し、同じタブの再読込・戻る操作では選ぶ画面を出さない。
 *       前回選んだものは localStorage「pv:patternLast」に覚えておき、選ぶ画面で最初に選ばれた状態にする。
 *       画面の「表示を選ぶ」からいつでも選び直せる。視差効果を減らす設定のときは選ぶ画面を出さず、動かない一覧にする。
 *   - パターン2 … 写真を 4:5 の枠で列に並べる（幅 910px 未満は3列、以上は7列）。ページは普通にスクロールし、
 *       各列はスクロールに遅れてついてくる（中央の列 0.5 秒、外側へ1列ごとに 0.1 秒ずつ遅い）。止まるとそろう。
 *       年・枚数・「表示を選ぶ」は一覧の上に並べる。スクロール位置は sessionStorage「pv:elastic」に保存する。
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
    year: 'pv:selectedYear', scroll: 'pv:scroll', drift: 'pv:drift',
    pattern: 'pv:pattern', patternLast: 'pv:patternLast', elastic: 'pv:elastic'
  });
  var PATTERNS = ['1', '2']; // 1 = 散らばる（drift）、2 = 列（elastic）
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
    yearW: 72,         // 散らした年の文字の大きさ（押しやすい大きさ）
    yearH: 44
  });
  var PATH_RE = /^(photos|thumbs|mids)\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.webp$/;
  var ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
  var JST_OFFSET_MS = 9 * 60 * 60 * 1000;

  function $(id) { return document.getElementById(id); }

  var el = {
    header: document.querySelector('.app-header'), // スクロール中も上に残るバー（名前と年）
    title: $('app-title'),
    count: $('photo-count'),
    range: $('photo-range'),
    cover: $('cover'),
    coverBtn: $('cover-btn'),
    coverImg: $('cover-img'),
    meta: document.querySelector('.site-meta'), // 一覧の先頭（枚数の行）
    tabs: $('year-tabs'),
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
    driftYears: $('drift-years'),
    elastic: $('elastic'),
    chooser: $('chooser'),
    patternSwitch: $('pattern-switch')
  };

  var state = {
    photos: [],
    years: [],
    year: null,       // 表示中の年（写真が0件のときは null）
    list: [],
    viewList: [],     // 全画面表示で前後に送る写真（一覧から開いたら一覧、表紙から開いたら表紙の写真の年）
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
    resizeTimer: 0,
    saveTimer: 0
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
    el.grid.textContent = '';
    el.tabs.textContent = '';
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

    // 初めて開いたときはいちばん新しい年。保存した年がなくなっていれば（旧版の「all」も）保存値を消す
    var stored = storageGet('localStorage', KEYS.year);
    var year = years.length ? years[0] : null;
    if (stored !== null) {
      var n = /^\d{4}$/.test(stored) ? Number(stored) : NaN;
      if (years.indexOf(n) >= 0) year = n;
      else storageRemove('localStorage', KEYS.year); // 削除された年は復活させない
    }
    state.year = year;

    renderTabs();
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

  // ---------------------------------------------------------------- 年タブ（S-06）
  function countFor(year) {
    var n = 0;
    for (var i = 0; i < state.photos.length; i++) if (state.photos[i].year === year) n++;
    return n;
  }

  function renderTabs() {
    el.tabs.textContent = '';
    state.years.forEach(function (v) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'year-tab bar-year'; // 上部バー右側の小さな年（スクロールしても上に残る）
      b.id = 'tab-' + v;
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-controls', 'grid-panel');
      b.setAttribute('data-year', String(v));
      b.setAttribute('aria-label', v + '年（' + countFor(v) + '枚）');
      b.setAttribute('data-count', String(countFor(v)));
      b.textContent = String(v);
      el.tabs.appendChild(b);
    });
    syncTabs();
  }

  function syncTabs() {
    var current = String(state.year);
    var active = null;
    Array.prototype.forEach.call(el.tabs.children, function (b) {
      var on = b.getAttribute('data-year') === current;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
      if (on) active = b;
    });
    if (active) {
      el.panel.setAttribute('aria-labelledby', active.id);
      var left = active.offsetLeft;
      var right = left + active.offsetWidth;
      if (left < el.tabs.scrollLeft) el.tabs.scrollLeft = left;
      else if (right > el.tabs.scrollLeft + el.tabs.clientWidth) el.tabs.scrollLeft = right - el.tabs.clientWidth;
    }
  }

  // 一覧の先頭（枚数の行）が上部バーのすぐ下に来るスクロール位置
  function listTop() {
    var y = window.pageYOffset || document.documentElement.scrollTop || 0;
    var bar = el.header ? el.header.getBoundingClientRect().height : 0;
    return Math.max(0, Math.round(y + el.meta.getBoundingClientRect().top - bar));
  }

  function selectYear(value, opts) {
    var year = Number(value);
    if (state.years.indexOf(year) < 0) return;
    cancelPendingSave();
    closeViewer({ restoreFocus: false });
    var changed = year !== state.year;
    state.year = year;
    storageSet('localStorage', KEYS.year, String(year));
    syncTabs();
    if (state.mode === 'drift') {
      // 動く一覧: 新しい年の写真が中央から広がる（同じ年を選び直したときはそのまま）
      if (changed) renderList({ restore: false });
      saveScrollNow();
      return;
    }
    if (state.mode === 'elastic') {
      // パターン2: 年は一覧の上にあるので、新しい年の一覧を上から見せる
      if (changed) {
        renderList({ restore: false });
        if (!(opts && opts.keepScroll)) window.scrollTo(0, 0);
      }
      saveScrollNow();
      return;
    }
    if (changed) renderList();
    // 選んだ年の写真がすぐ見えるよう、表紙の下の一覧の先頭へ（矢印キーで年を移動中は動かさない）
    if (!(opts && opts.keepScroll)) window.scrollTo(0, listTop());
    saveScrollNow();
  }

  el.tabs.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.year-tab') : null;
    if (b) selectYear(b.getAttribute('data-year'));
  });

  el.tabs.addEventListener('keydown', function (e) {
    var tabs = Array.prototype.slice.call(el.tabs.children);
    var i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    var j = null;
    if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
    else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === 'Home') j = 0;
    else if (e.key === 'End') j = tabs.length - 1;
    if (j === null) return;
    e.preventDefault();
    tabs[j].focus();
    selectYear(tabs[j].getAttribute('data-year'), { keepScroll: true });
  });

  // ---------------------------------------------------------------- ポートフォリオ型一覧（S-05）
  function updateCount() {
    var n = state.list.length + '枚';
    var text = state.photos.length ? state.year + '年の' + n : '';
    var ys = state.years;
    el.range.textContent = !ys.length ? '' : (ys.length === 1 ? String(ys[0]) : ys[ys.length - 1] + '–' + ys[0]);
    if (state.invalid) text += '（表示できないデータ ' + state.invalid + '件）';
    el.count.textContent = text;
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

  // 表紙（上部バーの下に、どの年を選んでいても同じ1枚を大きく表示する）
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
  // 表紙をタップすると、表紙の写真の年の中のその位置から全画面表示（前後はその年の写真）
  el.coverBtn.addEventListener('click', function () {
    var p = coverPhoto();
    if (!p) return;
    var list = state.photos.filter(function (q) { return q.year === p.year; });
    var i = list.indexOf(p);
    if (i >= 0) openViewer(i, { list: list, from: 'cover' });
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

  // 選んだ年の一覧を描き直す（動く一覧／動かない一覧）
  function renderList(opts) {
    state.list = state.photos.filter(function (p) { return p.year === state.year; });
    updateCount();
    if (state.mode === 'drift' || state.mode === 'elastic' || state.mode === 'choose') {
      clearGrid();
      el.cover.hidden = true; // 動く見せ方では表紙を出さない（v10）
      el.sentinel.hidden = true;
      if (state.observer) state.observer.disconnect();
      if (state.mode === 'drift') { elasticClear(); driftBuild({ restore: !!(opts && opts.restore) }); }
      else if (state.mode === 'elastic') { driftClear(); elasticBuild({ restore: !!(opts && opts.restore) }); }
      else { driftClear(); elasticClear(); } // 選ぶ画面の間は一覧を作らない
    } else {
      driftClear();
      elasticClear();
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
  function headerBottom() {
    return el.header ? Math.max(0, el.header.getBoundingClientRect().bottom) : 0;
  }

  function captureScroll() {
    var y = window.pageYOffset || document.documentElement.scrollTop || 0;
    var base = { v: 1, year: String(state.year), anchorId: null, delta: 0, y: 0 };
    if (y <= 0 || !state.rendered) return base;
    base.y = Math.round(y);
    var top = headerBottom();
    var items = el.grid.children;
    // 一覧は上から順に並ぶので、下端が年の行より下にある最初の写真を二分探索で求める
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
    if (state.mode === 'drift') { driftSaveNow(); return; }
    if (state.mode === 'elastic') { elasticSaveNow(); return; }
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
    if (!s || s.v !== 1 || s.year !== String(state.year) || !state.list.length) return;
    state.restoring = true;
    try {
      var idx = s.anchorId ? state.list.findIndex(function (p) { return p.id === s.anchorId; }) : -1;
      if (idx >= 0) {
        ensureRendered(idx + CONFIG.batchSize);
        var li = el.grid.children[idx];
        // 見出しはスクロールで流れ、年の行だけが上に残るため、
        // スクロール後の位置で年の行の下端が変わる。ずれがなくなるまで補正する（最大3回）。
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
  // 写真IDから毎回同じ乱数（0〜1）を作る。並び・大きさ・速さの係数が再読込しても変わらないように
  function seeded(id, salt) {
    var h = (2166136261 ^ salt) >>> 0;
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
    if (el.driftYears) el.driftYears.textContent = '';
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
    ['No. ' + formatNo(p.no), formatDot(p.ms)].forEach(function (text, line) {
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
      copy: copy, year: 0, near: false, visible: false, intro: null };
  }

  // 散らす年の文字（v11）。押すとその年に切り替わる。読み上げ・Tab は上部のバーの年のタブで行う
  function makeDriftYear(year, ease) {
    var item = document.createElement('div');
    item.className = 'drift-item drift-year-item';
    item.style.width = DRIFT.yearW + 'px';
    item.style.transform = 'translate3d(-10000px,-10000px,0)';
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'drift-year' + (year === state.year ? ' is-current' : '');
    b.tabIndex = -1;
    b.setAttribute('data-year', String(year));
    b.textContent = String(year);
    item.appendChild(b);
    return { el: item, btn: b, x: 0, y: 0, w: DRIFT.yearW, h: DRIFT.yearH, cap: 0, ease: ease, index: -1,
      copy: true, year: year, near: false, visible: false, intro: null };
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
    // 写真と年の文字の置き場所（v11）。並び1周を「画面くらいの大きさの区画」に分け、
    // 最初に見える区画には全部の年を、ほかの区画には年を1つずつ（新しい年から順に）置く。残りの枠に写真を新しい順に入れる
    var years = state.years;
    var nPhotos = list.length;
    var visCols = Math.max(1, Math.floor(vw / cw));
    var visRows = Math.max(1, Math.floor(vh / ch));
    var winCols = visCols;
    var winRows = visRows + 1;
    // 最初に見える区画の枠のうち、いちばん新しい写真の枠（左上）と、ほかの写真の枠（半分ほど）を残して年を置く
    var firstCells = visCols * visRows;
    var firstPhotos = Math.min(Math.max(0, nPhotos - 1), Math.floor((firstCells - 1) / 2));
    var firstCap = Math.max(0, Math.min(years.length, firstCells - 1 - firstPhotos));
    var minCols = Math.ceil(vw * 1.15 / cw);
    var nLabels = years.length;
    var cols = 1, rows = 1;
    for (var pass = 0; pass < 4; pass++) {
      var n = nPhotos + nLabels;
      cols = Math.max(1, Math.min(n, Math.max(minCols, Math.round(Math.sqrt(n * (vw / vh) * (ch / cw))))));
      rows = Math.ceil(n / cols);
      var windows = Math.ceil(cols / winCols) * Math.ceil(rows / winRows);
      var want = Math.max(years.length, firstCap + windows - 1);
      if (want === nLabels) break;
      nLabels = want;
    }
    rows = Math.max(rows, Math.ceil((nPhotos + nLabels) / cols));
    var yearCell = {};
    var placed = 0;
    var cycle = 0;
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
            if (yearCell[rr * cols + cc] === undefined) cand.push(rr * cols + cc);
          }
        }
        cand.sort(function (a, b) { return seeded('w' + a, 11) - seeded('w' + b, 11); }); // 区画の中の場所は毎回同じ乱数で
        var take = first ? firstCap : 1;
        for (var t = 0; t < take && t < cand.length && placed < nLabels; t++) {
          yearCell[cand[t]] = years[cycle % years.length];
          cycle++;
          placed++;
        }
      }
    }
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
    // 写真を入れる枠の順番: 最初に見える区画の枠（左上から）→ ほかの枠（左上から行ごと）。新しい写真ほど最初の画面に入る
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
      if (yearCell[k] !== undefined) {
        var key = 'y' + yearCell[k] + '-' + k;
        base.push({
          year: yearCell[k], w: DRIFT.yearW, h: DRIFT.yearH,
          x: c * cw + pad + Math.round(seeded(key, 2) * (availW - DRIFT.yearW)),
          y: r * ch + pad + Math.round(seeded(key, 3) * (ch - pad * 2 - DRIFT.yearH)) + lift,
          ease: 0.5 + seeded(key, 5) * 0.5
        });
        return;
      }
      if (photoIndex >= nPhotos) return; // 余った枠は空けておく
      var p = list[photoIndex];
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
        p: p, index: photoIndex, w: w, h: h,
        x: c * cw + pad + Math.round(seeded(p.id, 2) * (availW - w)),
        y: r * ch + pad + Math.round(seeded(p.id, 3) * (availH - h)) + lift
      });
      photoIndex++;
    });
    var frag = document.createDocumentFragment();
    var copyFrag = document.createDocumentFragment();
    var yearFrag = document.createDocumentFragment();
    for (var iy = 0; iy < ny; iy++) {
      for (var ix = 0; ix < nx; ix++) {
        var copy = ix > 0 || iy > 0;
        base.forEach(function (b) {
          var it = b.p ? makeDriftItem(b.p, b.index, copy, b.w, b.h) : makeDriftYear(b.year, b.ease);
          it.x = b.x + ix * bw;
          it.y = b.y + iy * bh;
          drift.items.push(it);
          (b.p ? (copy ? copyFrag : frag) : yearFrag).appendChild(it.el);
        });
      }
    }
    el.driftList.setAttribute('aria-label', state.year + '年の写真');
    el.driftList.appendChild(frag);
    el.driftCopies.appendChild(copyFrag);
    el.driftYears.appendChild(yearFrag);
    var firstPhoto = null;
    for (var q = 0; q < base.length && !firstPhoto; q++) if (base[q].p) firstPhoto = base[q];

    // 位置: 同じタブの再読込・戻る操作では保存した位置へ（広がる動きはしない）。それ以外は初めの位置から広がる
    var saved = opts && opts.restore ? driftLoadSaved() : null;
    if (saved) {
      drift.cx = drift.tx = saved.fx * bw;
      drift.cy = drift.ty = saved.fy * bh;
    } else {
      // 左端を少し切って「横にも続く」ことを見せつつ、いちばん新しい写真は全部見えるようにする
      drift.cx = drift.tx = Math.max(-Math.round(cw * 0.12), 8 - firstPhoto.x);
      drift.cy = drift.ty = Math.round(drift.top) + 14 - pad;
    }
    drift.lx = drift.cx;
    drift.ly = drift.cy;
    if (!saved && !(opts && opts.keepPlace)) driftStartIntro();
    driftKick();
  }

  // 開いた直後・年の切り替え: 画面に入る写真を中央に重ね、順に自分の場所へ広げる
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
        if (visible && !it.year && it.btn.getAttribute('data-seen') !== '1') {
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
      driftScheduleSave();
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

  // ---- 位置の保存・復元（同じタブの再読込・戻る操作用） ----
  function driftSaveNow() {
    if (drift.saveTimer) clearTimeout(drift.saveTimer);
    drift.saveTimer = 0;
    if (state.mode !== 'drift' || !drift.items.length || !drift.block.w) return;
    var fx = ((drift.tx / drift.block.w) % 1 + 1) % 1;
    var fy = ((drift.ty / drift.block.h) % 1 + 1) % 1;
    storageSet('sessionStorage', KEYS.drift, JSON.stringify({ v: 1, year: String(state.year), fx: +fx.toFixed(5), fy: +fy.toFixed(5) }));
  }

  function driftScheduleSave() {
    if (drift.saveTimer) clearTimeout(drift.saveTimer);
    drift.saveTimer = setTimeout(driftSaveNow, CONFIG.saveDelay);
  }

  function driftLoadSaved() {
    var raw = storageGet('sessionStorage', KEYS.drift);
    if (!raw) return null;
    var s;
    try { s = JSON.parse(raw); } catch (e) { storageRemove('sessionStorage', KEYS.drift); return null; }
    if (!s || s.v !== 1 || s.year !== String(state.year) || !isFinite(s.fx) || !isFinite(s.fy)) return null;
    return { fx: clamp(+s.fx, 0, 1), fy: clamp(+s.fy, 0, 1) };
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
      var yb = e.target.closest ? e.target.closest('.drift-year') : null;
      if (yb && state.mode === 'drift') {
        selectYear(yb.getAttribute('data-year')); // 散らした年の文字: その年に切り替える
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
    if (t && t.closest && t.closest('#year-tabs, input, textarea, select')) return;
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
    cap.textContent = 'No. ' + formatNo(p.no) + ' - ' + formatDot(p.ms);
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
    el.elastic.setAttribute('aria-label', state.year + '年の写真');
    el.elastic.appendChild(wrap);
    elastic.count = n;
    if (opts && opts.restore) elasticRestore();
    elastic.cols.forEach(function (c) { c.y = window.pageYOffset || 0; });
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
  });

  function elasticSaveNow() {
    if (elastic.saveTimer) clearTimeout(elastic.saveTimer);
    elastic.saveTimer = 0;
    if (state.mode !== 'elastic' || !state.ready || elastic.restoring) return;
    storageSet('sessionStorage', KEYS.elastic, JSON.stringify({ v: 1, year: String(state.year), y: Math.round(window.pageYOffset || 0) }));
  }

  function elasticRestore() {
    var raw = storageGet('sessionStorage', KEYS.elastic);
    if (!raw) return;
    var s;
    try { s = JSON.parse(raw); } catch (e) { storageRemove('sessionStorage', KEYS.elastic); return; }
    if (!s || s.v !== 1 || s.year !== String(state.year) || !(s.y > 0)) return;
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

  // ---------------------------------------------------------------- 見せ方の切り替え（パターン・視差効果を減らす設定）
  function wantedMode() {
    if (motionQuery && motionQuery.matches) return 'still';
    if (!state.pattern) return 'choose';
    return state.pattern === '2' ? 'elastic' : 'drift';
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
    if (el.patternSwitch) el.patternSwitch.hidden = !(mode === 'drift' || mode === 'elastic');
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
    storageRemove('sessionStorage', KEYS.drift); // 選び直したときは最初の位置から（広がる動きも見せる）
    storageRemove('sessionStorage', KEYS.elastic);
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
  if (el.patternSwitch) {
    el.patternSwitch.addEventListener('click', function () {
      if (state.mode !== 'drift' && state.mode !== 'elastic') return;
      saveScrollNow();
      state.pattern = null;
      storageRemove('sessionStorage', KEYS.pattern);
      applyMode();
    });
  }

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
    el.no.textContent = 'No. ' + formatNo(p.no);
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
