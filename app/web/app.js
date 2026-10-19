/* ============================================================
   听力训练 · 前端
   无构建步骤：原生 JS + 模块级状态，便于直接部署与排错。
   ============================================================ */
'use strict';

// ---------------------------------------------------------------- 状态
const S = {
  view: 'library',
  lessons: [],
  search: '',          // 素材库搜索关键词
  filter: 'all',       // 素材库筛选：all / bilibili / local / video / unfinished
  lesson: null,        // 当前课程数据
  sentences: [],
  idx: 0,              // 当前句下标（0-based）
  playing: false,
  loop: false,
  autoAdvance: true,
  hideText: true,
  showCn: false,
  rate: 1.0,
  audio: new Audio(),
  video: null,
  useVideo: false,
  saveTimer: null,
};

const $  = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ---------------------------------------------------------------- 工具
function toast(msg, ms = 2200) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), ms);
}

function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (res.status === 401) {
    // 会话过期或未登录：回到登录层，而不是弹一串红色报错
    showAuth();
    throw new Error('请先登录');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

// ---------------------------------------------------------------- 导航
const VIEWS = ['library', 'study', 'progress', 'settings'];

function switchView(name) {
  if (!VIEWS.includes(name)) return;
  // 没登录就别让人在空壳界面里逛，直接弹登录层。
  // 否则会出现"未登录"却能翻到设置页这种自相矛盾的画面。
  if (!AUTH.user) { showAuth(); return; }
  // 学习页必须有课程；没有就自动回到素材库
  if (name === 'study' && !S.lesson) {
    if (S.lessons.length) { openLesson(S.lessons[0].id); return; }
    toast('先导入一个素材吧');
    name = 'library';
  }
  S.view = name;
  VIEWS.forEach(v => $(`view-${v}`).classList.toggle('active', v === name));
  $$('#tabbar .tab').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  if (name === 'progress') loadProgress();
  if (name === 'study') requestAnimationFrame(scrollToCurrent);
}

$$('#tabbar .tab').forEach(btn =>
  btn.addEventListener('click', () => switchView(btn.dataset.view)));

// ---------------------------------------------------------------- 素材库
async function loadLessons() {
  if (!S.lessons.length) renderSkeleton(4);
  try {
    const data = await api('/api/lessons');
    S.lessons = data.lessons || [];
  } catch (e) {
    S.lessons = [];
    toast('读取素材库失败：' + e.message);
  }
  renderLibrary();
}

// ---- 搜索与筛选 ----
$('search-input').addEventListener('input', (e) => {
  S.search = e.target.value;
  $('search-clear').classList.toggle('show', !!S.search);
  renderLibrary();
});
$('search-clear').addEventListener('click', () => {
  S.search = '';
  $('search-input').value = '';
  $('search-clear').classList.remove('show');
  renderLibrary();
});
$$('#filter-row .chip-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    S.filter = btn.dataset.filter;
    $$('#filter-row .chip-btn').forEach(b =>
      b.classList.toggle('active', b === btn));
    renderLibrary();
  });
});
// 空状态里的按钮：有筛选条件时变成「清除筛选」
// （"开始导入" 的行为由原有的 data-open-import 监听负责，这里只管清除）
$('library-empty').querySelector('.primary-btn').addEventListener('click', (e) => {
  if (e.target.dataset.action !== 'clear-filter') return;
  e.stopPropagation();
  S.search = '';
  S.filter = 'all';
  $('search-input').value = '';
  $('search-clear').classList.remove('show');
  $$('#filter-row .chip-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.filter === 'all'));
  renderLibrary();
});

function sourceChip(l) {
  if (l.platform === 'bilibili') return '<span class="chip bili">B站</span>';
  if (l.platform === 'youtube')  return '<span class="chip">YouTube</span>';
  if (l.source === 'local')      return '<span class="chip local">本地</span>';
  return `<span class="chip">${l.source || '链接'}</span>`;
}

/** 骨架屏：加载数据时先占位，避免"空白 → 突然出现"的跳动 */
function renderSkeleton(count = 4) {
  $('library-empty').classList.add('hidden');
  $('lesson-list').innerHTML = Array.from({ length: count }, () => `
    <div class="skeleton">
      <div class="sk-thumb"></div>
      <div class="sk-lines">
        <div class="sk-line w1"></div>
        <div class="sk-line w2"></div>
      </div>
    </div>`).join('');
}

/** 当前筛选条件命中的课程 */
function filteredLessons() {
  const q = (S.search || '').trim().toLowerCase();
  const f = S.filter || 'all';
  return S.lessons.filter(l => {
    if (q && !String(l.title || '').toLowerCase().includes(q)) return false;
    if (f === 'bilibili') return l.platform === 'bilibili';
    if (f === 'local') return l.source === 'local';
    if (f === 'video') return !!l.hasVideo;
    if (f === 'unfinished') return (l.progress || 0) < 0.999;
    return true;
  });
}

/** 平台 → 封面用的 class 名 */
function platClass(l) {
  if (l.platform === 'bilibili') return 'plat-bilibili';
  if (l.platform === 'youtube')  return 'plat-youtube';
  if (l.platform === 'douyin')   return 'plat-douyin';
  if (l.source === 'local')      return 'plat-local';
  return '';
}

/**
 * 顶部「继续学习」主视觉卡。
 * 选谁作为"继续"对象：优先未学完、且进度最高的那门——
 * 因为那才是用户最可能接着学下去的。
 */
function renderHero() {
  const wrap = $('hero-wrap');
  // 只看全部素材，不受当前筛选影响（筛选是"找东西"，不该改变主入口）
  const candidates = S.lessons
    .filter(l => (l.sentenceCount || 0) > 0)
    .map(l => ({ ...l, _p: l.progress || 0 }))
    .sort((a, b) => {
      const aDone = a._p >= 0.999, bDone = b._p >= 0.999;
      if (aDone !== bDone) return aDone ? 1 : -1;      // 学完的排后面
      if (b._p !== a._p) return b._p - a._p;           // 进度高的优先
      return (b.lastStudied || 0) - (a.lastStudied || 0);
    });

  const l = candidates[0];
  if (!l) { wrap.classList.add('hidden'); wrap.innerHTML = ''; return; }

  const pct = Math.round((l.progress || 0) * 100);
  const isNew = pct === 0;
  wrap.classList.remove('hidden');
  wrap.innerHTML = `
    <div class="hero" data-id="${l.id}">
      <div class="hero-label">
        <span class="dot"></span>${isNew ? '开始学习' : pct >= 100 ? '已完成' : '继续学习'}
      </div>
      <div class="hero-title">${escapeHtml(l.title)}</div>
      <div class="hero-meta">
        ${sourceChip(l)}
        <span>${l.sentenceCount} 句</span>
        <span>${fmtTime(l.duration)}</span>
      </div>
      <div class="hero-bar"><i style="width:${pct}%"></i></div>
      <div class="hero-foot">
        <div class="hero-pct">${pct}<small>%</small></div>
        <div class="hero-go">${isNew ? '▶ 开始' : pct >= 100 ? '↻ 复习' : '▶ 继续'}</div>
      </div>
    </div>`;
  wrap.querySelector('.hero')
    .addEventListener('click', () => openLesson(l.id));
}

/** 数据概览：课程数 / 总句数 / 总体完成度 */
function renderStatStrip() {
  const strip = $('stat-strip');
  const ls = S.lessons;
  if (!ls.length) { strip.classList.add('hidden'); strip.innerHTML = ''; return; }

  const totalSent = ls.reduce((s, l) => s + (l.sentenceCount || 0), 0);
  // 总体完成度按"句子"加权，而不是按课程数——这样长课程不会和小课程等权
  const doneSent = ls.reduce(
    (s, l) => s + Math.round((l.progress || 0) * (l.sentenceCount || 0)), 0);
  const overall = totalSent ? Math.round((doneSent / totalSent) * 100) : 0;
  const finished = ls.filter(l => (l.progress || 0) >= 0.999).length;

  strip.classList.remove('hidden');
  strip.innerHTML = `
    <div class="stat-pill"><div class="v">${ls.length}<em>个</em></div><div class="k">课程</div></div>
    <div class="stat-pill"><div class="v">${totalSent}<em>句</em></div><div class="k">总句数</div></div>
    <div class="stat-pill accent"><div class="v">${overall}<em>%</em></div><div class="k">总完成度</div></div>
    <div class="stat-pill"><div class="v">${finished}<em>个</em></div><div class="k">已学完</div></div>`;
}

function renderLibrary() {
  const list = $('lesson-list');
  const empty = $('library-empty');

  if (!S.lessons.length) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    renderHero();
    renderStatStrip();
    $('section-head').classList.add('hidden');
    return;
  }

  const rows = filteredLessons();
  if (!rows.length) {
    // 有素材但被筛选空了：这和"完全没有素材"是两回事，文案要区分
    list.innerHTML = '';
    empty.classList.remove('hidden');
    empty.querySelector('.ei-icon').textContent = '🔍';
    empty.querySelector('.empty-title').textContent = '没有匹配的素材';
    empty.querySelector('.empty-sub').innerHTML =
      '换个关键词，或点下面的按钮看全部';
    empty.querySelector('.primary-btn').textContent = '清除筛选';
    empty.querySelector('.primary-btn').dataset.action = 'clear-filter';
    renderHero();
    renderStatStrip();
    return;
  }

  // 恢复默认空状态文案
  empty.querySelector('.ei-icon').textContent = '🎧';
  empty.querySelector('.empty-title').textContent = '还没有素材';
  empty.querySelector('.empty-sub').innerHTML =
    '粘贴一个 B 站链接，或导入本地视频<br>软件会自动切好每一句';
  const btn = empty.querySelector('.primary-btn');
  btn.textContent = '开始导入';
  delete btn.dataset.action;
  empty.classList.add('hidden');

  renderHero();
  renderStatStrip();

  const head = $('section-head');
  head.classList.toggle('hidden', S.filter === 'all' && !S.search);
  $('section-count').textContent = `${rows.length} 个`;

  list.innerHTML = rows.map(l => {
    const pct = Math.round((l.progress || 0) * 100);
    return `
      <div class="card" data-id="${l.id}">
        <div class="card-thumb ${platClass(l)}">${l.hasVideo ? '🎬' : '🎧'}</div>
        <div class="card-main">
          <div class="card-title">${escapeHtml(l.title)}</div>
          <div class="card-sub">
            ${sourceChip(l)}
            <span>${l.sentenceCount} 句</span>
            <span>${fmtTime(l.duration)}</span>
            ${pct > 0 ? `<span>已学 ${pct}%</span>` : ''}
          </div>
          <div class="card-bar"><div class="card-bar-fill" style="width:${pct}%"></div></div>
        </div>
      </div>`;
  }).join('');

  $$('#lesson-list .card').forEach(card =>
    card.addEventListener('click', () => openLesson(card.dataset.id)));
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------- 学习页
async function openLesson(id) {
  try {
    S.lesson = await api(`/api/lessons/${id}`);
  } catch (e) {
    toast('打开失败：' + e.message);
    return;
  }
  S.sentences = S.lesson.sentences || [];
  S.idx = Math.min(S.lesson.lastSentence || 0, Math.max(0, S.sentences.length - 1));

  $('study-title-text').textContent = S.lesson.title;
  const src = S.lesson.meta?.sentenceSource || '';
  const srcLabel = src.startsWith('caption') ? '平台字幕'
                 : src.startsWith('asr') ? '本地识别' : src;
  $('study-meta').textContent =
    `${S.sentences.length} 句 · ${fmtTime(S.lesson.duration)} · ${srcLabel}`;

  setupPlayer();
  renderSentences();
  renderSeekMarks();
  updateCurrent({ scroll: true, seek: true });
  switchView('study');
}

function setupPlayer() {
  const video = $('video');
  S.video = video;
  S.useVideo = !!S.lesson.hasVideo;

  if (S.useVideo) {
    video.src = S.lesson.media;
    video.classList.remove('hidden');
    $('player-placeholder').classList.add('hidden');
    if (!video._bound) {
      video._bound = true;
      // 视频播放时按句子边界约束、并同步高亮
      video.addEventListener('timeupdate', onVideoTime);
      video.addEventListener('play', () => setPlayingUI(true));
      video.addEventListener('pause', () => setPlayingUI(false));
      video.addEventListener('ended', () => { setPlayingUI(false); onSegmentEnd(); });
    }
  } else {
    video.pause();
    video.removeAttribute('src');
    video.classList.add('hidden');
    $('player-placeholder').classList.remove('hidden');
    $('player-placeholder-text').textContent = '音频模式';
  }

  const audio = S.audio;
  if (!audio._bound) {
    audio._bound = true;
    audio.addEventListener('ended', onSegmentEnd);
    audio.addEventListener('play', () => setPlayingUI(true));
    audio.addEventListener('pause', () => setPlayingUI(false));
    audio.addEventListener('timeupdate', () => {
      if (!S.useVideo) {
        const dur = audio.duration || curSentence()?.duration || 1;
        $('seek').value = Math.round((audio.currentTime / dur) * 1000);
        $('time-cur').textContent = fmtTime((curSentence()?.start || 0) + audio.currentTime);
      }
    });
  }
  setRate(S.rate);
}

function curSentence() { return S.sentences[S.idx]; }

function renderSentences() {
  const list = $('sentence-list');
  list.innerHTML = S.sentences.map((s, i) => `
    <div class="sent" data-i="${i}">
      <div class="sent-no">${String(i + 1).padStart(2, '0')}</div>
      <div class="sent-body">
        <div class="sent-text">${escapeHtml(s.text)}</div>
        ${s.translation ? `<div class="sent-trans">${escapeHtml(s.translation)}</div>` : ''}
        <div class="sent-sub">
          <span>${fmtTime(s.start)} - ${fmtTime(s.end)}</span>
          <span>${(s.duration || 0).toFixed(1)}s</span>
          <div class="sent-actions">
            <button class="sent-act" data-act="reveal" title="显示 / 隐藏原文">👁</button>
            <button class="sent-act" data-act="tts" title="朗读这一句">🔊</button>
            <button class="sent-act" data-act="slow" title="0.75 倍速重播">🐢</button>
            <button class="sent-act" data-act="loop" title="单句循环">🔁</button>
          </div>
        </div>
      </div>
    </div>`).join('');

  $$('#sentence-list .sent').forEach(el => {
    el.addEventListener('click', (ev) => {
      const act = ev.target.dataset?.act;
      const i = Number(el.dataset.i);
      if (act) { ev.stopPropagation(); runAction(act, i); return; }

      // 隐藏模式下，点「文字本身」是"我要看原文"，点其他位置是"播放这一句"。
      // 这样既保留"先听再看"的练习方式，又不用去找小按钮。
      if (S.hideText && !el.classList.contains('revealed')
          && ev.target.classList.contains('sent-text')) {
        ev.stopPropagation();
        el.classList.add('revealed');
        return;
      }
      playIndex(i);
    });
  });
  applyTextVisibility();
}

function runAction(act, i) {
  // 眼睛按钮：只切换这一句的显示状态，不播放。
  // 练听力的正确顺序是「先听，实在听不出来再看」，所以默认全模糊。
  if (act === 'reveal') {
    const el = document.querySelector(
      `#sentence-list .sent[data-i="${i}"]`);
    if (el) el.classList.toggle('revealed');
    return;
  }
  if (act === 'tts')  return speakSentence(i);
  if (act === 'slow') { playIndex(i, { rate: 0.75, temporary: true }); return; }
  if (act === 'loop') {
    S.loop = !S.loop;
    $('btn-loop').classList.toggle('on', S.loop);
    toast(S.loop ? '单句循环：开' : '单句循环：关');
    return;
  }
}

// ---------------------------------------------------------------- 播放控制
function playIndex(i, opts = {}) {
  if (i < 0 || i >= S.sentences.length) return;
  S.idx = i;
  const s = curSentence();
  updateCurrent({ scroll: true });

  const rate = opts.rate ?? S.rate;
  if (opts.temporary) setRate(rate);

  if (S.useVideo && S.video) {
    S.video.currentTime = s.start;
    S.video.playbackRate = rate;
    S.video.play().catch(() => {});
  } else {
    if (s.audioUrl) {
      S.audio.src = s.audioUrl;
      S.audio.playbackRate = rate;
      S.audio.play().catch(err => {
        // 浏览器自动播放策略：首次需要用户手势
        setPlayingUI(false);
        if (err && err.name === 'NotAllowedError') toast('再点一次即可播放');
      });
    } else {
      toast('这一句没有音频片段');
    }
  }
  reportPlay(i);
  saveSoon();
}

function playPause() {
  if (S.useVideo && S.video) {
    if (S.video.paused) { S.video.play().catch(() => {}); }
    else S.video.pause();
    return;
  }
  if (S.audio.paused) {
    if (!S.audio.src) { playIndex(S.idx); return; }
    S.audio.play().catch(() => {});
  } else {
    S.audio.pause();
  }
}

function setPlayingUI(playing) {
  S.playing = playing;
  $('btn-play').textContent = playing ? '❚❚' : '▶';
}

function onSegmentEnd() {
  if (S.loop) { playIndex(S.idx); return; }
  if (S.autoAdvance && S.idx < S.sentences.length - 1) {
    playIndex(S.idx + 1);
  } else {
    setPlayingUI(false);
    saveSoon();
  }
}

/** 视频模式：到句子结尾就交给同一套推进逻辑 */
function onVideoTime() {
  const s = curSentence();
  if (!s) return;
  const v = S.video;
  $('time-cur').textContent = fmtTime(v.currentTime);
  const total = S.lesson?.duration || v.duration || 1;
  $('seek').value = Math.round((v.currentTime / total) * 1000);

  if (v.currentTime >= s.end - 0.03) {
    if (S.loop) { v.currentTime = s.start; v.play().catch(() => {}); return; }
    if (S.autoAdvance && S.idx < S.sentences.length - 1) {
      // 直接跳到下一句起点，避免句间静音被播出来
      S.idx += 1;
      updateCurrent({ scroll: true });
      v.currentTime = curSentence().start;
      if (S.video.paused) setPlayingUI(false);
      reportPlay(S.idx);
      saveSoon();
    } else {
      v.pause();
    }
    return;
  }
  // 用户手动拖动到别的句子时，同步高亮
  if (v.currentTime < s.start - 0.25 || v.currentTime > s.end + 0.25) {
    const near = nearestIndex(v.currentTime);
    if (near !== S.idx) { S.idx = near; updateCurrent({ scroll: true }); }
  }
}

function nearestIndex(t) {
  let best = 0, bestD = Infinity;
  S.sentences.forEach((s, i) => {
    const d = Math.abs((s.start + s.end) / 2 - t);
    if (d < bestD) { bestD = d; best = i; }
  });
  return best;
}

function updateCurrent(opts = {}) {
  $$('#sentence-list .sent').forEach((el, i) => {
    el.classList.toggle('current', i === S.idx);
    el.classList.toggle('done', !!(S.sentences[i]?.completed));
  });
  const s = curSentence();
  if (s) {
    $('time-cur').textContent = fmtTime(s.start);
    $('time-total').textContent = fmtTime(S.lesson?.duration || s.end);
    if (opts.seek) {
      const total = S.lesson?.duration || s.end || 1;
      $('seek').value = Math.round((s.start / total) * 1000);
      if (S.useVideo && S.video) S.video.currentTime = s.start;
    }
  }
  $('sentence-counter').textContent =
    `${S.idx + 1} / ${S.sentences.length}`;
  updateSentProgress();
  if (opts.scroll) scrollToCurrent();
}

/** 整篇的学习进度：按"已播完的句子数"算，比按时间更贴近真实掌握度 */
function updateSentProgress() {
  const total = S.sentences.length;
  const done = S.sentences.filter(s => s.completed).length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const fill = $('sent-progress-fill');
  const txt = $('sent-progress-text');
  if (fill) fill.style.width = pct + '%';
  if (txt) txt.textContent = `已完成 ${pct}% · ${done}/${total} 句`;
}

function scrollToCurrent() {
  const el = $(`#sentence-list .sent[data-i="${S.idx}"]`);
  if (!el) return;
  const box = $('sentence-list');
  const target = el.offsetTop - box.clientHeight / 2 + el.offsetHeight / 2;
  box.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
}

function renderSeekMarks() {
  const total = S.lesson?.duration || 0;
  const marks = $('seek-marks');
  if (!total) { marks.innerHTML = ''; return; }
  marks.innerHTML = S.sentences.map(s =>
    `<div class="seek-mark" style="left:${(s.start / total) * 100}%"></div>`).join('');
}

function setRate(r) {
  S.rate = r;
  $('btn-rate').textContent = r.toFixed(2).replace(/0$/, '') + 'x';
  if (S.audio) S.audio.playbackRate = r;
  if (S.video) S.video.playbackRate = r;
}

function applyTextVisibility() {
  const list = $('sentence-list');
  list.classList.toggle('hide-mode', S.hideText);
  $$('#sentence-list .sent').forEach(el => {
    el.classList.toggle('hide-text', S.hideText);
    // 当前句在"隐藏字幕"模式下也保持模糊，点击才显示（听力训练的关键）
    el.classList.remove('revealed');
  });
}

// ---------------------------------------------------------------- 服务端记账
function reportPlay(i) {
  if (!S.lesson) return;
  api(`/api/lessons/${S.lesson.id}/play`,
      { method: 'POST', body: JSON.stringify({ index: i + 1, looped: S.loop }) })
    .then(() => {
      const s = S.sentences[i];
      if (s) { s.completed = true; const el = $(`#sentence-list .sent[data-i="${i}"]`); if (el) el.classList.add('done'); }
    })
    .catch(() => {});
}

function saveSoon() {
  if (!S.lesson) return;
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(() => {
    const pos = S.useVideo && S.video ? S.video.currentTime
              : (curSentence()?.start || 0);
    api(`/api/lessons/${S.lesson.id}/state`, {
      method: 'POST',
      body: JSON.stringify({ lastSentence: S.idx, position: pos }),
    }).catch(() => {});
  }, 900);
}

// ---------------------------------------------------------------- TTS 朗读
async function speakSentence(i) {
  const s = S.sentences[i];
  if (!s) return;
  const voice = $('set-voice').value;
  const engine = $('set-engine') ? $('set-engine').value : 'offline';
  try {
    const url = `/api/tts?text=${encodeURIComponent(s.text)}` +
                `&voice=${encodeURIComponent(voice)}&engine=${encodeURIComponent(engine)}`;
    const res = await fetch(url);
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    const used = res.headers.get('X-TTS-Engine') || engine;
    const blob = await res.blob();
    const objUrl = URL.createObjectURL(blob);
    const a = $('tts-audio');
    a.src = objUrl;
    a.playbackRate = S.rate;
    await a.play();
    a.onended = () => URL.revokeObjectURL(objUrl);
    if (engine === 'online' && used !== 'online') {
      toast('在线朗读不可用，已用离线语音代替');
    }
  } catch (e) {
    toast('朗读失败：' + e.message, 3200);
  }
}

// ---------------------------------------------------------------- 传输控制条
$('btn-play').addEventListener('click', playPause);
$('btn-prev').addEventListener('click', () => playIndex(Math.max(0, S.idx - 1)));
$('btn-next').addEventListener('click', () =>
  playIndex(Math.min(S.sentences.length - 1, S.idx + 1)));
$('btn-back').addEventListener('click', () => switchView('library'));

$('btn-rate').addEventListener('click', () => {
  const rates = [0.5, 0.75, 1, 1.25, 1.5];
  const next = rates[(rates.indexOf(S.rate) + 1) % rates.length];
  setRate(next);
  toast(`播放速度 ${next}x`);
});

$('btn-loop').addEventListener('click', () => {
  S.loop = !S.loop;
  $('btn-loop').classList.toggle('on', S.loop);
  toast(S.loop ? '单句循环：开' : '单句循环：关');
});

$('seek').addEventListener('input', (e) => {
  const total = S.lesson?.duration || 0;
  if (!total) return;
  const t = (Number(e.target.value) / 1000) * total;
  if (S.useVideo && S.video) { S.video.currentTime = t; }
  else { S.idx = nearestIndex(t); updateCurrent({ scroll: true }); }
  $('time-cur').textContent = fmtTime(t);
});

$('opt-auto').addEventListener('change', (e) => { S.autoAdvance = e.target.checked; });
$('opt-hide').addEventListener('change', (e) => {
  S.hideText = e.target.checked;
  applyTextVisibility();
  if (S.hideText) toast('字幕已隐藏，点句子可显示');
});
$('opt-cn').addEventListener('change', (e) => {
  S.showCn = e.target.checked;
  toast(S.showCn ? '中文翻译功能开发中' : '已关闭中文');
});

// 键盘快捷键（电脑端）
document.addEventListener('keydown', (e) => {
  if (S.view !== 'study') return;
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
  switch (e.key) {
    case ' ':      e.preventDefault(); playPause(); break;
    case 'ArrowDown': case 'j': e.preventDefault();
      playIndex(Math.min(S.sentences.length - 1, S.idx + 1)); break;
    case 'ArrowUp': case 'k': e.preventDefault();
      playIndex(Math.max(0, S.idx - 1)); break;
    case 'r': case 'R': playIndex(S.idx); break;
    case 'l': case 'L': $('btn-loop').click(); break;
    case 'Escape': switchView('library'); break;
  }
});

// ---------------------------------------------------------------- 导入
$('btn-add').addEventListener('click', openImport);
$$('[data-open-import]').forEach(b => b.addEventListener('click', openImport));
$$('[data-close-import]').forEach(b =>
  b.addEventListener('click', () => $('import-sheet').classList.add('hidden')));

let importMode = 'url';
function openImport() {
  $('import-sheet').classList.remove('hidden');
  $('import-progress').classList.add('hidden');
  $('btn-import-go').disabled = false;
  setTimeout(() => $(importMode === 'url' ? 'input-url' : 'input-path').focus(), 240);
}

$$('#import-seg .seg-item').forEach(btn => btn.addEventListener('click', () => {
  importMode = btn.dataset.mode;
  $$('#import-seg .seg-item').forEach(b => b.classList.toggle('active', b === btn));
  $('pane-url').classList.toggle('hidden', importMode !== 'url');
  $('pane-local').classList.toggle('hidden', importMode !== 'local');
}));

async function browse(path) {
  const box = $('browser');
  box.classList.remove('hidden');
  box.innerHTML = '<div class="browse-path">读取中…</div>';
  try {
    const data = await api('/api/browse' + (path ? `?path=${encodeURIComponent(path)}` : ''));
    const rows = [];
    rows.push(`<div class="browse-path">${escapeHtml(data.path)}</div>`);
    if (data.parent) rows.push(`<div class="browse-item" data-dir="${escapeHtml(data.parent)}">📁 ..</div>`);
    data.dirs.forEach(d => rows.push(
      `<div class="browse-item" data-dir="${escapeHtml(d.path)}">📁 ${escapeHtml(d.name)}</div>`));
    data.media.forEach(m => rows.push(
      `<div class="browse-item" data-file="${escapeHtml(m.path)}">🎬 ${escapeHtml(m.name)}
        <span class="browse-path" style="margin-left:auto;padding:0">${(m.size / 1048576).toFixed(0)}MB</span></div>`));
    box.innerHTML = rows.join('');
    $$('.browse-item', box).forEach(el => el.addEventListener('click', () => {
      if (el.dataset.dir) browse(el.dataset.dir);
      else { $('input-path').value = el.dataset.file; box.classList.add('hidden'); }
    }));
  } catch (e) {
    box.innerHTML = `<div class="browse-path">无法读取：${escapeHtml(e.message)}</div>`;
  }
}
$('btn-browse').addEventListener('click', () => browse($('input-path').value.trim() || null));

$('btn-import-go').addEventListener('click', async () => {
  const isUrl = importMode === 'url';
  const value = (isUrl ? $('input-url').value : $('input-path').value).trim();
  if (!value) { toast(isUrl ? '请粘贴链接' : '请选择文件'); return; }

  $('btn-import-go').disabled = true;
  $('import-progress').classList.remove('hidden');
  setProgress(0.01, '提交任务…');

  let jobId;
  try {
    const body = isUrl ? { url: value } : { path: value };
    const r = await api(isUrl ? '/api/build/url' : '/api/build/local', {
      method: 'POST', body: JSON.stringify(body),
    });
    jobId = r.jobId;
  } catch (e) {
    setProgress(0, '失败：' + e.message);
    $('btn-import-go').disabled = false;
    return;
  }

  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  es.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (_) { return; }
    if (d.type === 'progress') {
      setProgress(d.frac, d.msg);
    } else if (d.type === 'done') {
      es.close();
      setProgress(1, `完成：${d.result.sentences} 句`);
      $('btn-import-go').disabled = false;
      toast(`已导入「${d.result.title}」`);
      $('import-sheet').classList.add('hidden');
      $('input-url').value = '';
      loadLessons().then(() => openLesson(d.result.id));
    } else if (d.type === 'error') {
      es.close();
      setProgress(0, '失败：' + d.message);
      $('btn-import-go').disabled = false;
      toast('导入失败：' + d.message, 4000);
    }
  };
  es.onerror = () => {
    es.close();
    setProgress(0, '连接中断（任务可能仍在后台运行）');
    $('btn-import-go').disabled = false;
    loadLessons();
  };
});

function setProgress(frac, msg) {
  $('progress-fill').style.width = `${Math.round(frac * 100)}%`;
  $('progress-msg').textContent = msg;
}

// ---------------------------------------------------------------- 进度页
async function loadProgress() {
  let stats = {};
  try { stats = await api('/api/stats'); } catch (_) {}
  const cards = [
    ['素材数',   stats.lessons ?? 0],
    ['句子总数', stats.totalSentences ?? 0],
    ['已学句子', stats.sentencesDone ?? 0],
    ['播放次数', stats.totalPlays ?? 0],
    ['练习时长', (stats.estimatedMinutes ?? 0) + ' 分'],
    ['学习天数', (stats.activeDays || []).length],
  ];
  $('stats-grid').innerHTML = cards.map(([label, num]) => `
    <div class="stat"><div class="stat-num">${num}</div>
    <div class="stat-label">${label}</div></div>`).join('');

  const days = new Set(stats.activeDays || []);
  const cells = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    cells.push(`<div class="hm-day ${days.has(key) ? 'on' : ''}" title="${key}"></div>`);
  }
  $('heatmap').innerHTML = cells.join('');

  const wrap = $('progress-list');
  if (!S.lessons.length) { wrap.innerHTML = '<p class="hint">还没有素材</p>'; return; }
  wrap.innerHTML = S.lessons.map(l => {
    const pct = Math.round((l.progress || 0) * 100);
    return `<div class="card" data-id="${l.id}">
      <div class="card-thumb">${l.hasVideo ? '🎬' : '🎧'}</div>
      <div class="card-main">
        <div class="card-title">${escapeHtml(l.title)}</div>
        <div class="card-sub"><span>${l.sentencesDone} / ${l.sentenceCount} 句</span>
          <span>${pct}%</span></div>
        <div class="card-bar"><div class="card-bar-fill" style="width:${pct}%"></div></div>
      </div></div>`;
  }).join('');
  $$('#progress-list .card').forEach(c =>
    c.addEventListener('click', () => openLesson(c.dataset.id)));
}

// ---------------------------------------------------------------- 设置
async function loadSettings() {
  let s = {};
  try { s = await api('/api/settings'); } catch (_) {}
  $('set-auto').checked = s.autoAdvance !== false;
  $('set-loop').checked = !!s.loopCurrent;
  $('set-rate').value = String(s.playbackRate ?? 1);
  if (s.ttsVoice) $('set-voice').value = s.ttsVoice;
  if (s.ttsEngine && $('set-engine')) $('set-engine').value = s.ttsEngine;
  S.autoAdvance = s.autoAdvance !== false;
  S.loop = !!s.loopCurrent;
  $('opt-auto').checked = S.autoAdvance;
  $('btn-loop').classList.toggle('on', S.loop);
  setRate(Number(s.playbackRate ?? 1));
}

function bindSetting(id, key, get) {
  const el = $(id);
  if (!el) return;
  el.addEventListener('change', async () => {
    const value = get();
    try { await api('/api/settings', { method: 'POST', body: JSON.stringify({ [key]: value }) }); }
    catch (e) { toast('保存失败：' + e.message); }
  });
}
bindSetting('set-auto', 'autoAdvance', () => $('set-auto').checked);
bindSetting('set-loop', 'loopCurrent', () => $('set-loop').checked);
bindSetting('set-rate', 'playbackRate', () => Number($('set-rate').value));
bindSetting('set-voice', 'ttsVoice', () => $('set-voice').value);
bindSetting('set-engine', 'ttsEngine', () => $('set-engine').value);

$('set-auto').addEventListener('change', () => {
  S.autoAdvance = $('set-auto').checked;
  $('opt-auto').checked = S.autoAdvance;
});
$('set-rate').addEventListener('change', () => setRate(Number($('set-rate').value)));
$('btn-reload').addEventListener('click', () => {
  loadLessons(); loadSettings(); loadEnv(); toast('已刷新');
});

async function loadEnv() {
  try {
    const h = await api('/api/health');
    $('env-info').innerHTML = `
      ffmpeg：<code>${h.ffmpeg ? '可用' : '缺失'}</code><br>
      识别模型：<code>${escapeHtml(h.model)}</code><br>
      数据目录：<code>${escapeHtml(h.dataDir)}</code>`;
  } catch (_) {}
  try {
    const t = await api('/api/tts/status');
    const off = t.offline || {};
    $('tts-hint').innerHTML = off.available
      ? `离线语音 <code>${escapeHtml(off.model)}</code> 已就绪（${escapeHtml(off.license)} 许可，完全本地运行，不联网）。` +
        `<br>在线语音走 edge-tts 的非官方接口，<b>实测返回 403</b>，选它时会自动回退到离线语音。`
      : '离线语音模型缺失，朗读功能不可用。';
  } catch (_) {
    $('tts-hint').textContent = '朗读状态读取失败';
  }
}

// ================================================================
//  认证 / 个人中心 / 背景
// ================================================================

const AUTH = {
  user: null,
  mode: 'login',        // 'login' | 'register'
};

/** 后端返回 401 时统一跳到登录层，而不是抛一堆红字 */
async function apiAuthed(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (res.status === 401) {
    showAuth();
    throw new Error('请先登录');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function showAuth(mode = 'login') {
  AUTH.mode = mode;
  const overlay = $('auth-overlay');
  overlay.classList.remove('hidden');
  applyAuthMode();
}

function hideAuth() {
  $('auth-overlay').classList.add('hidden');
}

function applyAuthMode() {
  const isRegister = AUTH.mode === 'register';
  $('auth-title').textContent = isRegister ? '创建账号' : '欢迎回来';
  $('auth-sub').textContent = isRegister
    ? '注册后你的素材和学习进度只属于你'
    : '登录后继续你的听力训练';
  $('auth-submit').textContent = isRegister ? '注册并进入' : '登录';
  $('auth-switch-text').textContent = isRegister ? '已经有账号？' : '还没有账号？';
  $('auth-switch-link').textContent = isRegister ? '去登录' : '立即注册';
  $('auth-email-field').style.display = isRegister ? 'block' : 'none';
  $('auth-password').setAttribute(
    'autocomplete', isRegister ? 'new-password' : 'current-password');
  $('auth-error').classList.remove('show');
}

function authError(msg) {
  const el = $('auth-error');
  el.textContent = msg;
  el.classList.add('show');
}

async function submitAuth() {
  const username = $('auth-username').value.trim();
  const password = $('auth-password').value;
  const email = $('auth-email').value.trim();

  if (!username) return authError('请输入用户名');
  if (AUTH.mode === 'register' && password.length < 6) {
    return authError('密码至少 6 位');
  }
  if (!password) return authError('请输入密码');

  const btn = $('auth-submit');
  btn.disabled = true;
  btn.textContent = AUTH.mode === 'register' ? '注册中…' : '登录中…';

  try {
    const path = AUTH.mode === 'register'
      ? '/api/auth/register' : '/api/auth/login';
    const body = AUTH.mode === 'register'
      ? { username, password, email: email || null }
      : { username, password };
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || '操作失败');

    AUTH.user = data.user;
    hideAuth();
    await enterApp();
  } catch (e) {
    authError(e.message);
  } finally {
    btn.disabled = false;
    applyAuthMode();
  }
}

async function logout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (_) {}
  AUTH.user = null;
  S.lessons = [];
  S.lesson = null;
  $('lesson-list').innerHTML = '';
  $('library-empty').classList.remove('hidden');
  renderProfile();          // 统一由 renderProfile 处理未登录态，避免文案不一致
  showAuth();
}

/** 登录成功（或已有会话）后进入主界面 */
async function enterApp() {
  hideAuth();
  renderProfile();
  await loadSettings();
  await loadLessons();
  loadEnv();
}

// ---------------------------------------------------------------- 输入对话框
/**
 * 页面内的文本输入框，替代 prompt()。
 *
 * Android WebView 默认不支持 prompt（需要原生 onJsPrompt 回调），
 * 直接用会让"改昵称"变成一个点了没反应的死按钮。
 * 这里用 DOM 自己实现，任何环境都能用。
 *
 * @returns {Promise<string|null>} 确认返回文本，取消返回 null
 */
function askText(title, value = '', placeholder = '') {
  return new Promise((resolve) => {
    const wrap = document.createElement('div');
    wrap.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:200',
      'display:flex', 'align-items:center', 'justify-content:center',
      'background:rgba(0,0,0,.62)', 'backdrop-filter:blur(6px)',
      'padding:22px',
    ].join(';');

    const card = document.createElement('div');
    card.style.cssText = [
      'width:100%', 'max-width:380px',
      'background:rgba(20,24,33,.98)',
      'border:1px solid rgba(255,255,255,.14)',
      'border-radius:22px', 'padding:22px',
      'box-shadow:0 24px 70px rgba(0,0,0,.7)',
    ].join(';');

    const h = document.createElement('div');
    h.textContent = title;
    h.style.cssText = 'font-size:17px;font-weight:700;color:#f2f5fa;margin-bottom:14px';

    const input = document.createElement('input');
    input.type = 'text';
    input.value = value;
    input.placeholder = placeholder;
    input.maxLength = 32;
    input.style.cssText = [
      'width:100%', 'box-sizing:border-box',
      'padding:13px 15px', 'border-radius:14px',
      'background:rgba(255,255,255,.07)',
      'border:1px solid rgba(255,255,255,.16)',
      'color:#f2f5fa', 'font-size:16px', 'outline:none',
    ].join(';');

    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:10px;margin-top:18px';

    const cancel = document.createElement('button');
    cancel.textContent = '取消';
    cancel.style.cssText = [
      'flex:1', 'padding:13px', 'border-radius:999px', 'border:none',
      'background:rgba(255,255,255,.09)', 'color:#c8cfdc',
      'font-size:15px', 'cursor:pointer',
    ].join(';');

    const ok = document.createElement('button');
    ok.textContent = '确定';
    ok.style.cssText = [
      'flex:1', 'padding:13px', 'border-radius:999px', 'border:none',
      'background:linear-gradient(135deg,#4f8cff,#7c5cff)', 'color:#fff',
      'font-size:15px', 'font-weight:650', 'cursor:pointer',
    ].join(';');

    const close = (v) => { wrap.remove(); resolve(v); };
    cancel.onclick = () => close(null);
    ok.onclick = () => close(input.value.trim());
    wrap.addEventListener('click', (e) => { if (e.target === wrap) close(null); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); close(input.value.trim()); }
      if (e.key === 'Escape') { e.preventDefault(); close(null); }
    });

    row.append(cancel, ok);
    card.append(h, input, row);
    wrap.append(card);
    document.body.append(wrap);
    // 移动端要等一帧才能聚焦并弹出键盘
    setTimeout(() => { input.focus(); input.select(); }, 60);
  });
}

function renderProfile() {
  const u = AUTH.user;

  // 未登录状态必须显式处理：否则页面会停在 HTML 的默认文案上，
  // 出现"未登录"却又有"退出登录 / 改昵称"按钮这种自相矛盾的画面。
  if (!u) {
    $('profile-name').textContent = '未登录';
    $('profile-meta').textContent = '点头像登录';
    $('profile-avatar').textContent = '我';
    $('profile-avatar').style.backgroundImage = '';
    $('btn-avatar').textContent = '我';
    $('btn-avatar').style.backgroundImage = '';
    $('btn-edit-name').style.display = 'none';
    $('btn-logout').style.display = 'none';
    return;
  }

  const initial = (u.displayName || u.username || '我').slice(0, 1);
  const name = u.displayName || u.username;

  $('btn-edit-name').style.display = '';
  $('btn-logout').style.display = '';

  // 顶栏头像
  const av = $('btn-avatar');
  av.textContent = initial;
  if (u.avatar) av.style.backgroundImage = `url("${u.avatar}")`; else av.style.backgroundImage = '';

  // 个人中心
  $('profile-avatar').textContent = initial;
  if (u.avatar) $('profile-avatar').style.backgroundImage = `url("${u.avatar}")`;
  else $('profile-avatar').style.backgroundImage = '';
  $('profile-name').textContent = name;
  $('profile-meta').textContent =
    `${u.username}${u.role === 'admin' ? ' · 管理员' : ''}`;

  applyBackground(u.background);
  markActiveSwatch(u.background);
}

/** 应用背景：内置渐变 id 或 data:image */
function applyBackground(value) {
  const layer = $('bg-layer');
  const builtin = ['aurora', 'ocean', 'sunset', 'forest', 'mono'];
  layer.className = '';
  layer.style.backgroundImage = '';

  if (!value) {
    layer.classList.add('bg-aurora');
    return;
  }
  if (value.startsWith('data:image/')) {
    layer.classList.add('image');
    layer.style.backgroundImage = `url("${value}")`;
    return;
  }
  layer.classList.add(builtin.includes(value) ? `bg-${value}` : 'bg-aurora');
}

function markActiveSwatch(value) {
  $$('#bg-grid .bg-swatch').forEach(el => {
    el.classList.toggle('active', !!value && el.dataset.bg === value);
  });
}

async function saveBackground(value) {
  try {
    const data = await apiAuthed('/api/auth/profile', {
      method: 'POST',
      body: JSON.stringify({ background: value }),
    });
    AUTH.user = data.user;
    applyBackground(data.user.background);
    markActiveSwatch(data.user.background);
  } catch (e) {
    toast('背景保存失败：' + e.message);
  }
}

// ---- 绑定事件 ----
$('auth-submit').addEventListener('click', submitAuth);
$('auth-switch-link').addEventListener('click', () => {
  AUTH.mode = AUTH.mode === 'login' ? 'register' : 'login';
  applyAuthMode();
});
['auth-username', 'auth-password', 'auth-email'].forEach(id => {
  $(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitAuth();
  });
});

// ---------------------------------------------------------------- 头像上传
/**
 * 把用户选的图压到 256×256 再上传。
 *
 * 手机上拍一张照片就是 3-8 MB，直接存 base64 会把数据库撑大、
 * 每次登录还要把它整个传下来。所以这里统一缩放 + JPEG 压缩。
 */
function compressImage(file, size = 256, quality = 0.85) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const s = Math.min(size, Math.max(img.width, img.height));
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext('2d');
      // 正方形居中裁剪，避免头像被拉变形
      const side = Math.min(img.width, img.height);
      const sx = (img.width - side) / 2;
      const sy = (img.height - side) / 2;
      ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
      resolve(canvas.toDataURL('image/jpeg', quality));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('图片读取失败'));
    };
    img.src = url;
  });
}

async function uploadAvatar(file) {
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    toast('请选一张图片');
    return;
  }
  try {
    toast('正在处理图片…');
    const dataUrl = await compressImage(file);
    const data = await api('/api/auth/profile', {
      method: 'POST',
      body: JSON.stringify({ avatar: dataUrl }),
    });
    AUTH.user = data.user;
    renderProfile();
    toast('头像已更新');
  } catch (e) {
    toast('上传失败：' + e.message);
  }
}

$('avatar-file').addEventListener('change', (e) => {
  const f = e.target.files && e.target.files[0];
  uploadAvatar(f);
  e.target.value = '';
});

$('profile-avatar').addEventListener('click', () => {
  if (!AUTH.user) { showAuth(); return; }
  $('avatar-file').click();
});

$('btn-avatar').addEventListener('click', () =>
  AUTH.user ? switchView('settings') : showAuth());
$('btn-logout').addEventListener('click', logout);

$('btn-edit-name').addEventListener('click', async () => {
  const cur = AUTH.user?.displayName || AUTH.user?.username || '';
  const next = await askText('修改昵称', cur, '最多 32 个字');
  if (next === null) return;
  try {
    const data = await apiAuthed('/api/auth/profile', {
      method: 'POST',
      body: JSON.stringify({ displayName: next.trim() }),
    });
    AUTH.user = data.user;
    renderProfile();
    toast('昵称已更新');
  } catch (e) {
    toast('修改失败：' + e.message);
  }
});

$$('#bg-grid .bg-swatch[data-bg]').forEach(el => {
  el.addEventListener('click', () => saveBackground(el.dataset.bg));
});

$('bg-upload').addEventListener('click', () => $('bg-file').click());
$('bg-file').addEventListener('change', (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    toast('图片太大了（上限 2 MB），换一张小的');
    e.target.value = '';
    return;
  }
  const reader = new FileReader();
  reader.onload = () => saveBackground(String(reader.result));
  reader.readAsDataURL(file);
  e.target.value = '';
});

// ---------------------------------------------------------------- 启动
(async function init() {
  let status = null;
  try {
    const res = await fetch('/api/auth/status');
    status = await res.json();
  } catch (_) {
    status = { authenticated: false, needsSetup: false };
  }

  if (!status.authenticated) {
    // 第一个用户直接进注册页，省掉一次点击
    showAuth(status.needsSetup ? 'register' : 'login');
    if (status.needsSetup) {
      $('auth-sub').textContent = '这是第一次使用，先创建一个账号吧（你会成为管理员）';
    }
    return;
  }

  AUTH.user = status.user;
  await enterApp();
  if (location.hash === '#study' && S.lessons.length) switchView('study');
})();
