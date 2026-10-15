/*
 * 写真ビューア（同期仕様ID PV-SPEC-2026-10-v7 / 版 7.0）
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
 *       （保存位置も新しい年の先頭で上書き）。矢印キーで索引を移動しているときは画面を動かさない。
 *   - 全画面表示中の写真 … 保存しない。再読込すると一覧（保存した年・位置）に戻る。
 *   - 写真データ … data/photos.json を毎回再検証付き（cache: no-cache）で取得する。
 *   - 保存はスクロール停止後 200ms のタイマーと、ページを離れる時（pagehide / 非表示化）に行う。
 *       年タブ切替時は保留中のタイマーを取り消し、古い年の位置が新しい年として保存される競合を防ぐ。
 *   - プライベートブラウズ等でストレージを使えない場合は保存せずに動作する。
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
  var KEYS = Object.freeze({ year: 'pv:selectedYear', scroll: 'pv:scroll' });
  var PATH_RE = /^(photos|thumbs)\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.webp$/;
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
    barYears: $('bar-years'),
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
    caption: $('viewer-caption')
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
    observer: null
  };

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
    el.grid.textContent = '';
    el.tabs.textContent = '';
    el.barYears.textContent = '';
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
    el.title.textContent = parsed.title;
    el.title.classList.toggle('is-ja', /[\u3000-\u9fff\uff00-\uffef]/.test(parsed.title));
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
    renderGrid();
    state.ready = true;
    if (!state.photos.length) {
      setStatus('empty', '写真はまだありません。');
    } else {
      setStatus('ready', '');
    }
    var token = state.loadToken;
    whenLayoutStable(function () {
      if (token !== state.loadToken) return;
      restoreScroll();
      document.documentElement.setAttribute('data-pv', state.photos.length ? 'ready' : 'empty');
    });
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
      b.className = 'year-tab';
      b.id = 'tab-' + v;
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-controls', 'grid-panel');
      b.setAttribute('data-year', String(v));
      b.setAttribute('aria-label', v + '年（' + countFor(v) + '枚）');
      b.setAttribute('data-count', String(countFor(v)));
      b.textContent = String(v);
      el.tabs.appendChild(b);
    });
    // 上部バーの小さな年リンク（スクロールしても年を切り替えられるように。索引と同じ動作）
    el.barYears.textContent = '';
    state.years.forEach(function (v) {
      var a = document.createElement('button');
      a.type = 'button';
      a.className = 'bar-year';
      a.setAttribute('data-year', String(v));
      a.setAttribute('aria-label', v + '年を表示');
      a.textContent = String(v);
      el.barYears.appendChild(a);
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
    Array.prototype.forEach.call(el.barYears.children, function (a) {
      if (a.getAttribute('data-year') === current) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
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
    if (changed) renderGrid();
    // 選んだ年の写真がすぐ見えるよう、表紙の下の一覧の先頭へ（矢印キーで索引を移動中は動かさない）
    if (!(opts && opts.keepScroll)) window.scrollTo(0, listTop());
    saveScrollNow();
  }

  el.tabs.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.year-tab') : null;
    if (b) selectYear(b.getAttribute('data-year'));
  });
  el.barYears.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('.bar-year') : null;
    if (a) selectYear(a.getAttribute('data-year'));
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
    img.addEventListener('error', function () { b.classList.add('is-broken'); });
    img.src = p.thumb;
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

  // 表紙（年の索引の下に、どの年を選んでいても同じ1枚を大きく表示する）
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
    el.coverBtn.classList.remove('is-broken');
    el.coverImg.width = rw;
    el.coverImg.height = rh;
    el.coverImg.alt = p.alt;
    el.coverImg.setAttribute('data-id', p.id);
    el.coverImg.src = p.src;
  }

  el.coverImg.addEventListener('error', function () { el.coverBtn.classList.add('is-broken'); });
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

  function renderGrid() {
    state.list = state.photos.filter(function (p) { return p.year === state.year; });
    el.grid.textContent = '';
    state.rendered = 0;
    renderMore(CONFIG.batchSize);
    renderCover();
    updateCount();
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
    storageSet('sessionStorage', KEYS.scroll, JSON.stringify(captureScroll()));
  }

  function cancelPendingSave() {
    if (state.saveTimer) clearTimeout(state.saveTimer);
    state.saveTimer = 0;
  }

  function scheduleSave() {
    if (!state.ready || state.restoring || state.open) return;
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
    if (!opts || opts.restoreFocus !== false) {
      if (state.viewFrom === 'cover' && !el.cover.hidden) el.coverBtn.focus({ preventScroll: true });
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
  load();
})();
