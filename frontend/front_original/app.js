/* NeoNote 视觉重设计原型交互脚本
 * 纯前端演示：示例数据、简易合成音与模拟进度，不连接后端、不调用模型。 */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const noteName = m => NAMES[m % 12] + (Math.floor(m / 12) - 1);
  const isBlack = m => [1, 3, 6, 8, 10].includes(m % 12);
  const typing = () => { const el = document.activeElement; return el && (el.matches('input:not([type=range]):not([type=checkbox]):not([type=file]), textarea, select') || el.isContentEditable); };
  const fmt = (s, dec = false) => { const m = Math.floor(s / 60), r = s - m * 60; return `${String(m).padStart(2, '0')}:${dec ? r.toFixed(1).padStart(4, '0') : String(Math.floor(r)).padStart(2, '0')}`; };
  let seed = 7; const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

  function toast(text) {
    const t = document.createElement('div');
    t.className = 'toast'; t.innerHTML = `<span class="dot"></span><span>${text}</span>`;
    $('#toasts').append(t);
    setTimeout(() => { t.style.transition = 'opacity .3s, transform .3s'; t.style.opacity = '0'; t.style.transform = 'translateX(20px)'; setTimeout(() => t.remove(), 300); }, 2600);
  }

  /* ---------------- 简易合成音（原型用，不代表 SoundFont 音色） ---------------- */
  let actx = null;
  const ac = () => { if (!actx) actx = new (window.AudioContext || window.webkitAudioContext)(); if (actx.state === 'suspended') actx.resume(); return actx; };
  const freq = m => 440 * Math.pow(2, (m - 69) / 12);
  const INST = {
    keyboard: { type: 'triangle', hint: 'Acoustic Grand Piano · 电脑按键固定力度', shift: 0, decay: 1.4 },
    guitar: { type: 'sawtooth', hint: 'Nylon String Guitar · 电脑按键固定力度', shift: 0, decay: .9, lp: 1800 },
    bass: { type: 'sine', hint: 'Fingered Bass · 低两个八度发声', shift: -24, decay: 1.2 },
    drums: { hint: 'A 底鼓 · S 军鼓 · D 闭踩镲 · 其余为踩镲' }
  };
  const live = [];
  function voice(m, inst = 'keyboard', when = 0, dur = null, vol = .16) {
    const c = ac(), cfg = INST[inst] || INST.keyboard, t = c.currentTime + when;
    const o = c.createOscillator(), g = c.createGain();
    o.type = cfg.type; o.frequency.value = freq(m + cfg.shift);
    let out = g;
    if (cfg.lp) { const f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = cfg.lp; o.connect(f); f.connect(g); } else o.connect(g);
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + .012);
    g.gain.exponentialRampToValueAtTime(vol * .35, t + .25);
    out.connect(c.destination); o.start(t);
    const v = { o, g, release(at = ac().currentTime) { g.gain.cancelScheduledValues(at); g.gain.setValueAtTime(Math.max(g.gain.value, .0001), at); g.gain.exponentialRampToValueAtTime(.0001, at + .25); o.stop(at + .3); } };
    if (dur != null) v.release(t + Math.max(.08, dur));
    live.push(v); o.onended = () => { const i = live.indexOf(v); if (i >= 0) live.splice(i, 1); };
    return v;
  }
  function drum(m, when = 0, vol = .5) {
    const c = ac(), t = c.currentTime + when, g = c.createGain(); g.connect(c.destination);
    const kind = m === 36 ? 'kick' : m === 38 ? 'snare' : 'hat';
    if (kind === 'kick') {
      const o = c.createOscillator(); o.frequency.setValueAtTime(150, t); o.frequency.exponentialRampToValueAtTime(45, t + .15);
      g.gain.setValueAtTime(vol, t); g.gain.exponentialRampToValueAtTime(.001, t + .3); o.connect(g); o.start(t); o.stop(t + .32); live.push({ release() { try { o.stop(); } catch (e) {} } });
      return;
    }
    const len = kind === 'snare' ? .18 : .05, buf = c.createBuffer(1, c.sampleRate * len, c.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    const s = c.createBufferSource(), f = c.createBiquadFilter(); s.buffer = buf;
    f.type = kind === 'snare' ? 'bandpass' : 'highpass'; f.frequency.value = kind === 'snare' ? 1800 : 7000;
    g.gain.setValueAtTime(kind === 'snare' ? vol * .6 : vol * .25, t); g.gain.exponentialRampToValueAtTime(.001, t + len);
    s.connect(f); f.connect(g); s.start(t); live.push({ release() { try { s.stop(); } catch (e) {} } });
  }
  function click(accent, when = 0) { const c = ac(), t = c.currentTime + when, o = c.createOscillator(), g = c.createGain(); o.frequency.value = accent ? 1600 : 1000; g.gain.setValueAtTime(.18, t); g.gain.exponentialRampToValueAtTime(.001, t + .06); o.connect(g); g.connect(c.destination); o.start(t); o.stop(t + .07); }
  const silence = () => { live.splice(0).forEach(v => { try { v.release(); } catch (e) {} }); };

  /* ---------------- 主题 / 路由 ---------------- */
  const themeBtn = $('#themeToggle');
  function setTheme(t) { document.body.dataset.theme = t; localStorage.setItem('neonote-proto-theme', t); themeBtn.querySelector('use').setAttribute('href', t === 'dark' ? '#i-sun' : '#i-moon'); }
  setTheme(localStorage.getItem('neonote-proto-theme') || 'dark');
  themeBtn.onclick = () => setTheme(document.body.dataset.theme === 'dark' ? 'light' : 'dark');

  const MODE_NAMES = { home: '首页', creation: '创作', practice: '练习', transcription: '扒谱' };
  let view = 'home';
  function go(v) {
    if (!MODE_NAMES[v]) v = 'home';
    view = v; stopAll();
    $$('.view').forEach(s => s.classList.toggle('active', s.id === 'view-' + v));
    $$('.rail-btn[data-go]').forEach(b => b.classList.toggle('active', b.dataset.go === v));
    $('#crumbMode').textContent = 'NeoNote';
    $('#crumbTitle').textContent = v === 'creation' ? currentSong().name : MODE_NAMES[v] === '首页' ? '新的乐章' : MODE_NAMES[v] + '工作台';
    const showSong = v === 'creation';
    ['#saveState', '#saveBtn', '[data-modal=history]', '#modelTag'].forEach(s => { $(s).style.display = showSong ? '' : 'none'; });
    if (location.hash.slice(1) !== v) history.replaceState(null, '', '#' + v);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  document.addEventListener('click', e => { const t = e.target.closest('[data-go]'); if (t) go(t.dataset.go); });

  /* ---------------- 弹窗 ---------------- */
  const openModal = id => { $('#modal-' + id).classList.add('show'); const f = $('#modal-' + id + ' input'); if (f) setTimeout(() => f.focus(), 50); };
  const closeModals = () => $$('.modal-back').forEach(m => m.classList.remove('show'));
  document.addEventListener('click', e => {
    const m = e.target.closest('[data-modal]'); if (m) { if (m.dataset.modal === 'trash') renderTrash(); if (m.dataset.modal === 'history') renderHistory('#historyModal'); openModal(m.dataset.modal); }
    if (e.target.matches('[data-close]') || e.target.classList.contains('modal-back')) closeModals();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModals(); });

  /* ---------------- 首页 ---------------- */
  const eq = $('#heroEq');
  for (let i = 0; i < 28; i++) { const b = document.createElement('i'); b.style.animationDelay = `${-rand() * 1.6}s`; b.style.animationDuration = `${1 + rand() * 1.2}s`; eq.append(b); }
  const hk = $('#heroKeys'); for (let i = 0; i < 14; i++) hk.append(document.createElement('b'));
  setInterval(() => { const ks = $$('b', hk); ks.forEach(k => k.classList.remove('lit')); [0, 1].forEach(() => ks[Math.floor(rand() * ks.length)].classList.add('lit')); }, 420);

  /* ---------------- 歌曲列表 ---------------- */
  const GRADS = ['linear-gradient(135deg,#8b7cff,#4f8bff)', 'linear-gradient(135deg,#35d9c0,#1a9bd6)', 'linear-gradient(135deg,#ff7a8a,#ffb35c)', 'linear-gradient(135deg,#ffb35c,#ff6b9a)', 'linear-gradient(135deg,#6be3ff,#8b7cff)'];
  let songs = [
    { id: '7f3a9c21', name: '海边的告别', meta: 'v12 · 4 声部 · 01:52', g: 0 },
    { id: 'b81e0d44', name: '周末的小旅行', meta: 'v5 · 4 声部 · 00:60', g: 1 },
    { id: 'c02f7a19', name: '雨后的操场', meta: 'v3 · 仅录制片段', g: 2 },
    { id: 'e6d4b580', name: '给奶奶的歌', meta: 'v8 · 含演唱 · 02:14', g: 3 },
    { id: '91aa3c07', name: '深夜便利店', meta: 'v1 · 空白', g: 4 }
  ];
  let trash = [{ id: '3c9e11f2', name: '旧的练习曲', meta: '已删除 · 3 天前', g: 2 }];
  let currentId = songs[0].id;
  const currentSong = () => songs.find(s => s.id === currentId) || songs[0] || { name: '未选择歌曲' };
  function renderSongs() {
    const q = $('#songSearch').value.trim();
    const list = songs.filter(s => !q || s.name.includes(q));
    $('#songCount').textContent = songs.length + ' 首';
    $('#songList').innerHTML = list.map(s => `<div class="song ${s.id === currentId ? 'active' : ''}" data-id="${s.id}" tabindex="0" role="button" aria-label="打开歌曲 ${s.name}">
      <span class="art" style="background:${GRADS[s.g % GRADS.length]}">${s.name[0]}</span>
      <span style="min-width:0"><b>${s.name}</b><small>${s.meta}</small></span>
      <button class="del" data-del="${s.id}" title="删除歌曲" aria-label="删除歌曲 ${s.name}"><svg width="14" height="14"><use href="#i-trash"/></svg></button></div>`).join('') || '<p class="muted" style="padding:10px">没有匹配的歌曲</p>';
    $('#recentGrid').innerHTML = songs.slice(0, 4).map(s => `<div class="recent glass" data-open="${s.id}"><div class="cover" style="background:${GRADS[s.g % GRADS.length]}">${Array.from({ length: 16 }, () => `<i style="height:${20 + rand() * 70}%"></i>`).join('')}</div><b>${s.name}</b><span class="muted">${s.meta}</span></div>`).join('');
  }
  $('#songSearch').oninput = renderSongs;
  let pendingDelete = null;
  document.addEventListener('click', e => {
    const del = e.target.closest('[data-del]');
    if (del) { e.stopPropagation(); pendingDelete = del.dataset.del; const s = songs.find(x => x.id === pendingDelete); $('#deleteText').textContent = `「${s.name}」（${s.id}）将整首移入回收站，已保存的版本、音频与演唱都会保留，可随时恢复。`; openModal('delete'); return; }
    const row = e.target.closest('.song[data-id]'); if (row) { currentId = row.dataset.id; renderSongs(); $('#crumbTitle').textContent = currentSong().name; toast(`已打开「${currentSong().name}」`); }
    const open = e.target.closest('[data-open]'); if (open) { currentId = open.dataset.open; renderSongs(); go('creation'); }
    const res = e.target.closest('[data-restore]'); if (res) { const i = trash.findIndex(t => t.id === res.dataset.restore); const [s] = trash.splice(i, 1); s.meta = '已恢复 · 原 ID 保留'; songs.unshift(s); renderSongs(); renderTrash(); toast(`已恢复「${s.name}」`); }
  });
  $('#confirmDelete').onclick = () => { const i = songs.findIndex(s => s.id === pendingDelete); if (i < 0) return; const [s] = songs.splice(i, 1); s.meta = '已删除 · 刚刚'; trash.unshift(s); if (currentId === s.id) currentId = songs[0]?.id; closeModals(); renderSongs(); toast(`「${s.name}」已移入回收站`); };
  function renderTrash() { $('#trashList').innerHTML = trash.length ? trash.map(s => `<div class="hist" style="padding-left:12px"><div><b>${s.name}</b><small>歌曲ID · ${s.id} · ${s.meta}</small></div><button class="btn sm" data-restore="${s.id}">恢复歌曲</button></div>`).join('') : '<p class="muted">回收站是空的。</p>'; }
  $('#createSong').onclick = () => { const n = $('#newName').value.trim(); if (!n) { $('#newName').focus(); return; } const id = Math.random().toString(16).slice(2, 10); songs.unshift({ id, name: n, meta: 'v1 · 空白', g: Math.floor(rand() * 5) }); currentId = id; $('#newName').value = ''; closeModals(); renderSongs(); go('creation'); toast(`已创建「${n}」`); };
  $('#newName').addEventListener('keydown', e => { if (e.key === 'Enter') $('#createSong').click(); });

  let version = 12, dirty = false;
  function markDirty() { dirty = true; const s = $('#saveState'); s.classList.add('dirty'); s.innerHTML = '<span class="dot"></span>有未保存的修改'; }
  $('#saveBtn').onclick = () => { if (!dirty) { toast('当前没有需要保存的修改'); return; } version++; dirty = false; const s = $('#saveState'); s.classList.remove('dirty'); s.innerHTML = `<span class="dot" style="color:var(--ok)"></span>已保存 · v${version}`; toast(`已保存为 v${version}（原型不写入磁盘）`); };

  const HISTORY = [['v12', '采用 AI 候选 #1', '今天 14:20'], ['v11', '手动调整副歌贝斯', '今天 13:58'], ['v10', '整曲修改：鼓更有力', '昨天 22:41'], ['v9', '加入录制片段', '昨天 21:15'], ['v8', '保存歌词与主题', '11-16 19:02']];
  function renderHistory(sel) { $(sel).innerHTML = HISTORY.map((h, i) => `<div class="hist ${i === 0 ? 'current' : ''}"><div><b>${h[0]} · ${h[1]}</b><small>${h[2]}${i === 0 ? ' · 当前已保存' : ''}</small></div>${i === 0 ? '<span class="tag accent">当前</span>' : '<button class="btn sm ghost">恢复</button>'}</div>`).join(''); }
  renderHistory('#historyInline');

  /* ---------------- 步骤 ---------------- */
  const STEPS = ['inputs', 'edit', 'files'];
  function step(name) {
    stopAll();
    const idx = STEPS.indexOf(name);
    $$('.step').forEach((b, i) => { b.classList.toggle('active', i === idx); b.classList.toggle('done', i < idx); });
    $$('.pane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + name));
    if (name === 'edit') { renderRoll(); setTimeout(() => { $('#rollBody').scrollTop = (84 - 76) * 14; }, 30); }
  }
  $$('.step').forEach(b => b.onclick = () => step(b.dataset.step));

  /* ---------------- 虚拟键盘 ---------------- */
  const KEYMAP = { a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8, h: 9, u: 10, j: 11, k: 12 };
  const LABEL = Object.fromEntries(Object.entries(KEYMAP).map(([k, v]) => [v, k.toUpperCase()]));
  let base = 60, inst = 'keyboard';
  const held = new Map();
  function renderKeyboard() {
    const kb = $('#keyboard'); kb.innerHTML = '';
    const whites = []; for (let m = base; m <= base + 24; m++) if (!isBlack(m)) whites.push(m);
    const w = 100 / whites.length;
    whites.forEach(m => kb.insertAdjacentHTML('beforeend', `<button class="key white" data-p="${m}" aria-label="${noteName(m)}"><span class="lbl"><span>${m % 12 === 0 ? noteName(m) : ''}</span>${LABEL[m - base] !== undefined ? `<kbd>${LABEL[m - base]}</kbd>` : ''}</span></button>`));
    whites.forEach((m, i) => { if (isBlack(m + 1) && m + 1 <= base + 24) kb.insertAdjacentHTML('beforeend', `<button class="key black" data-p="${m + 1}" aria-label="${noteName(m + 1)}" style="left:calc(${(i + 1) * w}% - ${w * .31}%);width:${w * .62}%"><span class="lbl">${LABEL[m + 1 - base] !== undefined ? `<kbd>${LABEL[m + 1 - base]}</kbd>` : ''}</span></button>`); });
    $('#kbOct').textContent = `${noteName(base)}–${noteName(base + 24)}`;
  }
  function keyEl(p) { return $(`#keyboard [data-p="${p}"]`); }
  function noteOn(p, src) {
    if (held.has(src)) return;
    const sound = inst === 'drums' ? (drum(src === 'a' ? 36 : src === 's' ? 38 : 42), null) : voice(p, inst);
    held.set(src, { p, sound, t: performance.now() });
    keyEl(p)?.classList.add('down'); $('#kbNote').textContent = inst === 'drums' ? (src === 'a' ? 'Kick' : src === 's' ? 'Snare' : 'Hi-hat') : noteName(p);
    if (recording) recNotes.push({ p, t: (performance.now() - recStart) / 1000, d: .25, src, open: true });
  }
  function noteOff(src) {
    const h = held.get(src); if (!h) return; held.delete(src);
    h.sound?.release(); keyEl(h.p)?.classList.remove('down');
    const r = recNotes.findLast?.(n => n.src === src && n.open) || [...recNotes].reverse().find(n => n.src === src && n.open);
    if (r) { r.d = Math.max(.08, (performance.now() - h.t) / 1000); r.open = false; drawTimeline(); }
  }
  $('#keyboard').addEventListener('pointerdown', e => { const k = e.target.closest('.key'); if (!k) return; const p = +k.dataset.p; const src = 'ptr' + p; noteOn(p, src); const up = () => { noteOff(src); window.removeEventListener('pointerup', up); }; window.addEventListener('pointerup', up); });
  $$('#instSeg button').forEach(b => b.onclick = () => { $$('#instSeg button').forEach(x => x.classList.toggle('active', x === b)); inst = b.dataset.inst; $('#instHint').textContent = INST[inst].hint; $('#octDown').disabled = $('#octUp').disabled = inst === 'drums'; });
  $('#octDown').onclick = () => { if (base > 24) { base -= 12; renderKeyboard(); } };
  $('#octUp').onclick = () => { if (base < 84) { base += 12; renderKeyboard(); } };

  /* 录制 */
  let recording = false, recStart = 0, recNotes = [], recTimer = null;
  $('#recBtn').onclick = () => {
    recording = !recording; const b = $('#recBtn');
    if (recording) { recNotes = []; recStart = performance.now(); b.classList.add('on'); b.textContent = '■ 停止录制'; recTimer = setInterval(() => { $('#recInfo').textContent = `录制中 · ${fmt((performance.now() - recStart) / 1000, true)}`; }, 100); drawTimeline(); }
    else { b.classList.remove('on'); b.textContent = '● 开始录制'; clearInterval(recTimer); $('#recInfo').textContent = recNotes.length ? `${recNotes.length} 个音 · ${fmt(recLen(), true)}` : '尚未录制'; markDirty(); }
    ['#replayBtn', '#clearRec', '#toArrange'].forEach(s => $(s).disabled = recording || !recNotes.length);
  };
  const recLen = () => recNotes.reduce((m, n) => Math.max(m, n.t + n.d), 0);
  function drawTimeline() {
    const tl = $('#timeline');
    if (!recNotes.length) { tl.innerHTML = `<div class="empty">${recording ? '正在录制……弹几个音试试' : '录制后，你弹奏的每个音会出现在这里'}</div>`; return; }
    const total = Math.max(6, recLen() + .5); const ps = recNotes.map(n => n.p); const lo = Math.min(...ps), hi = Math.max(...ps) + 1;
    tl.innerHTML = recNotes.map(n => `<span class="blip" style="left:${n.t / total * 100}%;width:max(4px,${n.d / total * 100}%);top:${8 + (1 - (n.p - lo) / (hi - lo)) * 44}px"></span>`).join('');
  }
  $('#replayBtn').onclick = () => { stopAll(); const len = recLen(); recNotes.forEach(n => inst === 'drums' ? drum(n.src === 'a' ? 36 : n.src === 's' ? 38 : 42, n.t) : voice(n.p, inst, n.t, n.d)); showPlayer(len + .3, '回放片段'); };
  $('#clearRec').onclick = () => { recNotes = []; drawTimeline(); $('#recInfo').textContent = '尚未录制'; ['#replayBtn', '#clearRec', '#toArrange'].forEach(s => $(s).disabled = true); };
  $('#toArrange').onclick = () => { toast('片段已作为键盘声部输入，可在「演奏与编辑」中查看'); markDirty(); };

  /* ---------------- 灵感输入 / AI ---------------- */
  const MOODS = ['温柔', '怀旧', '明亮', '忧伤', '热血', '轻快', '梦幻', '治愈', '电子感', '民谣'];
  const moodOn = new Set(['温柔', '治愈']);
  $('#moodChips').innerHTML = MOODS.map(m => `<button class="chip ${moodOn.has(m) ? 'on' : ''}" data-mood="${m}" aria-pressed="${moodOn.has(m)}">${m}</button>`).join('');
  $('#moodChips').onclick = e => { const c = e.target.closest('.chip'); if (!c) return; c.classList.toggle('on'); c.setAttribute('aria-pressed', c.classList.contains('on')); markDirty(); };
  function countLyrics() {
    const text = $('#lyricInput').value; const lines = text.split(/\r?\n/).filter(l => l.trim());
    const words = (text.match(/[\u3400-\u9fff\uf900-\ufaff]|[A-Za-z]+(?:'[A-Za-z]+)?/g) || []).length;
    $('#lyricLines').textContent = lines.length; $('#lyricWords').textContent = words;
    const over = lines.length > 64 || words > 1024; $('#lyricWords').style.color = over ? 'var(--danger)' : '';
    return { lines, over };
  }
  $('#lyricInput').oninput = () => { countLyrics(); markDirty(); renderLyricPractice(); };
  ['#themeInput', '#feelInput'].forEach(s => $(s).addEventListener('input', markDirty));
  $('#lyricFile').onchange = async e => { const f = e.target.files[0]; if (!f) return; $('#lyricInput').value = await f.text(); countLyrics(); renderLyricPractice(); markDirty(); toast(`已读取 ${f.name}`); };
  $$('#melodySeg button').forEach(b => b.onclick = () => $$('#melodySeg button').forEach(x => x.classList.toggle('active', x === b)));
  $('#consent').onchange = e => { $('#genBtn').disabled = !e.target.checked; };
  $('#genBtn').onclick = () => {
    if (countLyrics().over) { toast('歌词超过 64 行或 1024 字词，请删减后再生成'); return; }
    const btn = $('#genBtn'), prog = $('#genProg'), bar = prog.firstElementChild; btn.disabled = true; prog.hidden = false; $('#candidate').classList.remove('show');
    const stages = ['整理输入…', '规划段落与和声…', '编排四声部…', '检查结构与歌词对应…']; let i = 0;
    const tick = () => { if (i < stages.length) { btn.innerHTML = `<span class="dot live"></span> ${stages[i]}`; bar.style.width = `${(i + 1) / stages.length * 100}%`; i++; setTimeout(tick, 700); }
      else { btn.disabled = false; btn.innerHTML = '<svg width="18" height="18"><use href="#i-sparkle"/></svg>重新生成'; prog.hidden = true; bar.style.width = '0'; renderCandidate(); } };
    tick();
  };
  function renderCandidate() {
    const cols = ['var(--c-keys)', 'var(--c-drums)', 'var(--c-guitar)', 'var(--c-bass)'];
    $('#candTracks').innerHTML = cols.map(c => `<span style="background:${c}">${Array.from({ length: 10 }, () => `<i style="height:${25 + rand() * 70}%"></i>`).join('')}</span>`).join('');
    $('#candidate').classList.add('show');
  }
  $('#adoptBtn').onclick = () => { markDirty(); toast('候选已采用为草稿，请完成试听与试唱审核'); step('edit'); };

  /* ---------------- 编辑器：声部与卷帘 ---------------- */
  const BEAT = 34, LANE = 14, TOP = 84, BOTTOM = 36, BPM = 92, SPB = 60 / BPM, BEATS = 32;
  const CHORDS = [[60, 64, 67], [57, 60, 64], [53, 57, 60], [55, 59, 62]];
  let uid = 0;
  const tracks = [
    { id: 'keyboard', name: '键盘', inst: 'Acoustic Grand Piano', color: 'var(--c-keys)', vol: 80, mute: false, solo: false, notes: [] },
    { id: 'drums', name: '鼓', inst: 'Standard Kit · 通道 10', color: 'var(--c-drums)', vol: 70, mute: false, solo: false, notes: [] },
    { id: 'guitar', name: '吉他', inst: 'Nylon String Guitar', color: 'var(--c-guitar)', vol: 65, mute: false, solo: false, notes: [] },
    { id: 'bass', name: '贝斯', inst: 'Fingered Bass', color: 'var(--c-bass)', vol: 75, mute: false, solo: false, notes: [] }
  ];
  (function seedScore() {
    const scale = [72, 74, 76, 79, 81, 84, 79, 76];
    const mel = [[0, 1], [1, .5], [1.5, .5], [2, 1.5], [4, 1], [5, 1], [6, 2]];
    for (let bar = 0; bar < 8; bar++) {
      const ch = CHORDS[bar % 4], b0 = bar * 4;
      mel.slice(0, bar % 2 ? 7 : 5).forEach(([s, d], k) => { if (s < 4) tracks[0].notes.push({ id: uid++, p: scale[(bar + k * 2 + Math.floor(rand() * 3)) % scale.length] - (k % 3 ? 0 : 3) , s: b0 + s, d }); });
      for (let e = 0; e < 8; e++) tracks[1].notes.push({ id: uid++, p: 42, s: b0 + e / 2, d: .25 });
      [0, 2].forEach(b => tracks[1].notes.push({ id: uid++, p: 36, s: b0 + b, d: .5 }));
      [1, 3].forEach(b => tracks[1].notes.push({ id: uid++, p: 38, s: b0 + b, d: .5 }));
      ch.forEach((p, k) => tracks[2].notes.push({ id: uid++, p: p, s: b0 + k * .5, d: 4 - k * .5 }));
      tracks[2].notes.push({ id: uid++, p: ch[1] + 12, s: b0 + 2.5, d: 1.5 });
      [0, 2].forEach((b, k) => tracks[3].notes.push({ id: uid++, p: ch[0] - 24 + (k ? 7 : 0), s: b0 + b, d: 1.8 }));
    }
  })();
  let activeTrack = 'keyboard', selected = null; const undo = [];
  const snapshot = () => { undo.push(JSON.stringify(tracks.map(t => t.notes))); if (undo.length > 50) undo.shift(); };
  const audible = t => tracks.some(x => x.solo) ? t.solo : !t.mute;
  function renderTracks() {
    $('#tracks').innerHTML = tracks.map(t => `<div class="track ${t.id === activeTrack ? 'active' : ''} ${audible(t) ? '' : 'muted'}" style="--tc:${t.color}" data-track="${t.id}">
      <div class="track-top"><b>${t.name}</b><span class="ms"><button class="m ${t.mute ? 'on' : ''}" data-mute="${t.id}" title="静音" aria-pressed="${t.mute}">M</button><button class="s ${t.solo ? 'on' : ''}" data-solo="${t.id}" title="独奏" aria-pressed="${t.solo}">S</button></span></div>
      <small>${t.inst} · ${t.notes.length} 音符</small>
      <label class="vol">音量<input type="range" min="0" max="100" value="${t.vol}" data-vol="${t.id}" aria-label="${t.name}音量" /></label></div>`).join('');
  }
  $('#tracks').addEventListener('click', e => {
    const m = e.target.closest('[data-mute]'), s = e.target.closest('[data-solo]');
    if (m) { const t = tracks.find(x => x.id === m.dataset.mute); t.mute = !t.mute; renderTracks(); renderRoll(); return; }
    if (s) { const t = tracks.find(x => x.id === s.dataset.solo); t.solo = !t.solo; renderTracks(); renderRoll(); return; }
    if (e.target.matches('input')) return;
    const tr = e.target.closest('[data-track]'); if (tr) { activeTrack = tr.dataset.track; selected = null; renderTracks(); renderRoll(); }
  });
  $('#tracks').addEventListener('input', e => { const v = e.target.dataset.vol; if (v) tracks.find(x => x.id === v).vol = +e.target.value; });
  function renderRoll() {
    const keys = $('#rollKeys'), grid = $('#rollGrid'); const lanes = TOP - BOTTOM + 1;
    if (!keys.childElementCount) {
      let k = '', l = ''; for (let p = TOP; p >= BOTTOM; p--) { k += `<div class="${isBlack(p) ? 'blk' : ''} ${p % 12 === 0 ? 'c' : ''}">${p % 12 === 0 || p === TOP ? noteName(p) : ''}</div>`; l += `<div class="lane ${isBlack(p) ? 'blk' : ''}" style="top:${(TOP - p) * LANE}px"></div>`; }
      keys.innerHTML = k; grid.insertAdjacentHTML('afterbegin', l);
      grid.style.height = lanes * LANE + 'px'; grid.style.width = BEATS * BEAT + 40 + 'px';
    }
    $$('.note', grid).forEach(n => n.remove());
    const frag = document.createDocumentFragment();
    [...tracks].sort((a, b) => (a.id === activeTrack) - (b.id === activeTrack)).forEach(t => t.notes.forEach(n => {
      const el = document.createElement('div'); el.className = `note ${t.id !== activeTrack || !audible(t) ? 'dim' : ''} ${selected === n.id ? 'sel' : ''}`;
      el.style.cssText = `--tc:${t.color};left:${n.s * BEAT + 1}px;width:${Math.max(6, n.d * BEAT - 2)}px;top:${(TOP - n.p) * LANE + 1}px`;
      el.dataset.note = n.id; el.dataset.t = t.id; el.title = `${t.name} · ${noteName(n.p)}`; frag.append(el);
    }));
    grid.append(frag);
    const t = tracks.find(x => x.id === activeTrack); $('#rollTitle').textContent = `${t.name} · ${t.inst}`;
    renderInspector();
  }
  $('#rollGrid').addEventListener('click', e => {
    const n = e.target.closest('.note'); if (!n) { selected = null; renderRoll(); return; }
    activeTrack = n.dataset.t; selected = +n.dataset.note; renderTracks(); renderRoll();
    const note = findNote(selected); if (note) activeTrack === 'drums' ? drum(note.p) : voice(note.p, activeTrack, 0, .35);
  });
  const findNote = id => { for (const t of tracks) { const n = t.notes.find(x => x.id === id); if (n) return n; } return null; };
  function renderInspector() {
    const n = selected != null && findNote(selected); $('#delNote').disabled = !n;
    $('#inspector').innerHTML = n ? `音高 <b>${noteName(n.p)}</b> · 开始 <b>${(n.s + 1).toFixed(2)} 拍</b> · 时长 <b>${n.d.toFixed(2)} 拍</b>
      <span class="spacer"></span><button class="btn sm" data-edit="p+1">音高 ↑</button><button class="btn sm" data-edit="p-1">音高 ↓</button><button class="btn sm" data-edit="s-0.5">← 前移</button><button class="btn sm" data-edit="s+0.5">后移 →</button><button class="btn sm" data-edit="d-0.25">缩短</button><button class="btn sm" data-edit="d+0.25">延长</button>`
      : '未选择音符 · 点击卷帘中的音符进行编辑';
  }
  $('#inspector').addEventListener('click', e => {
    const b = e.target.closest('[data-edit]'); if (!b) return; const n = findNote(selected); if (!n) return;
    snapshot(); const k = b.dataset.edit[0], v = parseFloat(b.dataset.edit.slice(1));
    if (k === 'p') n.p = Math.min(TOP, Math.max(BOTTOM, n.p + v)); if (k === 's') n.s = Math.max(0, Math.min(BEATS - n.d, n.s + v)); if (k === 'd') n.d = Math.max(.25, n.d + v);
    markDirty(); renderRoll(); if (k === 'p') activeTrack === 'drums' ? drum(n.p) : voice(n.p, activeTrack, 0, .3);
  });
  $('#addNote').onclick = () => { snapshot(); const t = tracks.find(x => x.id === activeTrack); const n = { id: uid++, p: t.id === 'drums' ? 38 : t.id === 'bass' ? 48 : 72, s: Math.floor(playBeat), d: 1 }; t.notes.push(n); selected = n.id; markDirty(); renderTracks(); renderRoll(); toast(`已在 ${t.name} 第 ${n.s + 1} 拍添加 ${noteName(n.p)}`); };
  $('#delNote').onclick = () => { snapshot(); tracks.forEach(t => t.notes = t.notes.filter(n => n.id !== selected)); selected = null; markDirty(); renderTracks(); renderRoll(); };
  $('#undoBtn').onclick = () => { const s = undo.pop(); if (!s) { toast('没有可以撤销的操作'); return; } JSON.parse(s).forEach((ns, i) => tracks[i].notes = ns); selected = null; renderTracks(); renderRoll(); };
  document.addEventListener('keydown', e => { if (view === 'creation' && $('#pane-edit').classList.contains('active') && !typing() && (e.key === 'Delete' || e.key === 'Backspace') && selected != null) { e.preventDefault(); $('#delNote').click(); } });

  /* 播放 */
  let playing = null, playBeat = 0;
  $('#playBtn').onclick = () => {
    stopAll(); const t0 = ac().currentTime + .05;
    tracks.filter(audible).forEach(t => t.notes.forEach(n => { const w = n.s * SPB + .05; t.id === 'drums' ? drum(n.p, w, .5 * t.vol / 100) : voice(n.p, t.id, w, n.d * SPB * .95, .1 * t.vol / 80); }));
    const total = BEATS * SPB, body = $('#rollBody');
    playing = { t0 };
    const frame = () => { if (!playing) return; const el = ac().currentTime - t0; playBeat = Math.max(0, el / SPB); if (el >= total) { stopAll(); return; }
      $('#playhead').style.left = playBeat * BEAT + 'px'; $('#clock').textContent = fmt(el, true);
      const x = playBeat * BEAT; if (x > body.scrollLeft + body.clientWidth - 120 || x < body.scrollLeft) body.scrollLeft = x - 60; playing.raf = requestAnimationFrame(frame); };
    frame(); showPlayer(total, '编曲试听', true);
  };
  $('#stopBtn').onclick = () => { stopAll(); playBeat = 0; $('#playhead').style.left = '0'; $('#clock').textContent = '00:00.0'; };

  /* 逐句练唱 */
  let lyricTimer = null;
  function renderLyricPractice() {
    const lines = $('#lyricInput').value.split(/\r?\n/).filter(l => l.trim()).slice(0, 8);
    $('#lyricPractice').innerHTML = lines.length ? lines.map((l, i) => `<div class="lyric-line" data-line="${i}"><span class="ln">L${i + 1}</span><span>${[...l.trim()].map(ch => `<span class="w">${ch}</span>`).join('')}</span><button class="btn sm ghost" data-sing="${i}"><svg width="11" height="11"><use href="#i-play"/></svg>练唱</button></div>`).join('') : '<p class="muted">还没有歌词。回到「收集灵感」填写后，这里会出现逐句练唱。</p>';
  }
  $('#lyricPractice').addEventListener('click', e => {
    const b = e.target.closest('[data-sing]'); if (!b) return; stopAll();
    const line = b.closest('.lyric-line'); line.classList.add('playing'); const ws = $$('.w', line).filter(w => w.textContent.trim());
    const rate = $('#speedSeg .active').textContent.startsWith('0.75') ? 1 / .75 : 1; const mel = tracks[0].notes.slice().sort((a, c) => a.s - c.s); let i = 0;
    const next = () => { ws.forEach(w => w.classList.remove('on')); if (i >= ws.length) { line.classList.remove('playing'); return; } ws[i].classList.add('on'); voice(mel[(i + +line.dataset.line * 5) % mel.length].p, 'keyboard', 0, .4 * rate); i++; lyricTimer = setTimeout(next, 420 * rate); };
    next();
  });
  $$('#speedSeg button').forEach(b => b.onclick = () => { stopAll(); $$('#speedSeg button').forEach(x => x.classList.toggle('active', x === b)); });
  $$('[data-out]').forEach(r => r.oninput = () => { r.nextElementSibling.textContent = r.value; });

  /* ---------------- 播放条 ---------------- */
  let playerTimer = null;
  function showPlayer(total, label, fromEditor = false) {
    const start = performance.now(), p = $('#player'); p.classList.add('show');
    clearInterval(playerTimer);
    playerTimer = setInterval(() => {
      const el = (performance.now() - start) / 1000; if (el >= total) { if (!fromEditor) hidePlayer(); return; }
      $('#pTime').textContent = `${label} · ${fmt(el)} / ${fmt(total)}`; $('#pBar').style.width = el / total * 100 + '%';
      $$('#meterBars i').forEach(i => i.style.height = 20 + Math.random() * 80 + '%');
    }, 90);
  }
  function hidePlayer() { clearInterval(playerTimer); $('#player').classList.remove('show'); $('#pBar').style.width = '0'; }
  function stopAll() {
    silence(); hidePlayer(); clearTimeout(lyricTimer); $$('.lyric-line').forEach(l => { l.classList.remove('playing'); $$('.w', l).forEach(w => w.classList.remove('on')); });
    if (playing) { cancelAnimationFrame(playing.raf); playing = null; }
    stopRhythm();
  }
  $('#pStop').onclick = () => { stopAll(); };

  /* ---------------- 练习 ---------------- */
  const UNITS = [
    { n: 'UNIT 01', name: '单音认谱', desc: 'C–G 一个八度白键，打乱顺序找音', prog: 80, notes: [[60, 1], [64, 1], [62, 1], [67, 1], [65, 1], [60, 1], [67, 1], [64, 1]] },
    { n: 'UNIT 02', name: '黑键与升降号', desc: '五个黑键与升号对应的琴键', prog: 45, notes: [[61, 1], [62, 1], [63, 1], [64, 1], [66, 1], [68, 1], [70, 1], [72, 1]] },
    { n: 'UNIT 03', name: '节奏入门', desc: '二分、四分与八分音符', prog: 20, notes: [[60, 2], [64, 1], [64, 1], [67, .5], [65, .5], [64, .5], [62, .5], [60, 2]] },
    { n: 'UNIT 04', name: '跨越八度', desc: '用 Shift / 空格切换基础八度', prog: 0, notes: [[60, 1], [72, 1], [67, 1], [79, 1], [76, 1], [64, 1], [72, 2]] },
    { n: 'UNIT 05', name: '小曲综合', desc: '原创 4 小节短曲', prog: 0, notes: [[64, 1], [64, 1], [65, 1], [67, 1], [67, 1], [65, 1], [64, 1], [62, 1], [60, 1], [60, 1], [62, 1], [64, 1], [64, 1.5], [62, .5], [62, 2]] }
  ];
  let unit = 0, pIdx = 0, pState = [], firstOk = 0, fixes = 0, pStart = 0, pTimer = null, octave = 4, phase = 'pitch';
  $('#units').innerHTML = UNITS.map((u, i) => `<button class="unit glass ${i === 0 ? 'active' : ''}" data-unit="${i}"><span class="u-n">${u.n}</span><b>${u.name}</b><small>${u.desc}</small><span class="bar"><i style="width:${u.prog}%"></i></span></button>`).join('');
  $('#units').onclick = e => { const b = e.target.closest('[data-unit]'); if (!b) return; unit = +b.dataset.unit; $$('.unit').forEach(x => x.classList.toggle('active', x === b)); resetPractice(); };
  const LETTER = [[0, ''], [0, '♯'], [1, ''], [1, '♯'], [2, ''], [3, ''], [3, '♯'], [4, ''], [4, '♯'], [5, ''], [5, '♯'], [6, '']];
  function drawScore() {
    const notes = UNITS[unit].notes; const W = Math.max(760, 120 + notes.reduce((s, [, d]) => s + 34 + d * 26, 0) + 40);
    const lines = [60, 70, 80, 90, 100].map(y => `<line x1="10" x2="${W - 10}" y1="${y}" y2="${y}" stroke="#2b2d42" stroke-width="1.2"/>`).join('');
    let x = 110, beat = 0, out = '';
    notes.forEach(([m, d], i) => {
      const [li, acc] = LETTER[m % 12], dia = (Math.floor(m / 12) - 1) * 7 + li, y = 100 - (dia - 30) * 5;
      let led = ''; for (let ly = 110; ly <= y; ly += 10) led += `<line x1="${x - 11}" x2="${x + 11}" y1="${ly}" y2="${ly}" stroke="#2b2d42" stroke-width="1.2"/>`;
      for (let ly = 50; ly >= y; ly -= 10) led += `<line x1="${x - 11}" x2="${x + 11}" y1="${ly}" y2="${ly}" stroke="#2b2d42" stroke-width="1.2"/>`;
      const up = dia < 34, hollow = d >= 2, sx = up ? x + 6 : x - 6, sy2 = up ? y - 34 : y + 34;
      const flag = d === .5 ? `<path d="M${sx} ${sy2} q 10 ${up ? 8 : -8} 8 ${up ? 20 : -20}" fill="none" stroke="#2b2d42" stroke-width="2"/>` : '';
      const st = pState[i] || (i === pIdx && phase === 'pitch' ? 'target' : '');
      out += `<g class="${st}">${led}${acc ? `<text x="${x - 22}" y="${y + 5}" font-size="16" fill="#2b2d42">${acc}</text>` : ''}
        <ellipse class="n-head" cx="${x}" cy="${y}" rx="6.6" ry="4.7" transform="rotate(-20 ${x} ${y})" fill="${hollow ? '#fbfaf5' : '#2b2d42'}" stroke="#2b2d42" stroke-width="${hollow ? 2 : 0}"/>
        <line x1="${sx}" x2="${sx}" y1="${y}" y2="${sy2}" stroke="#2b2d42" stroke-width="1.4"/>${flag}
        ${st === 'target' ? `<rect x="${x - 14}" y="30" width="28" height="100" rx="8" fill="#6c5cf0" opacity=".08"/>` : ''}</g>`;
      beat += d; x += 34 + d * 26;
      if (Math.abs(beat % 4) < 1e-6 && i < notes.length - 1) { out += `<line x1="${x - 18}" x2="${x - 18}" y1="60" y2="100" stroke="#2b2d42" stroke-width="1.2"/>`; }
    });
    $('#score').innerHTML = `<svg viewBox="0 0 ${W} 150" role="img" aria-label="练习五线谱"><rect width="${W}" height="150" fill="transparent"/>${lines}
      <text x="16" y="108" font-size="64" fill="#2b2d42" font-family="'Segoe UI Symbol','Noto Music',serif">𝄞</text>
      <text x="64" y="78" font-size="20" font-weight="700" fill="#2b2d42" font-family="serif">4</text><text x="64" y="98" font-size="20" font-weight="700" fill="#2b2d42" font-family="serif">4</text>
      ${out}<line x1="${W - 14}" x2="${W - 14}" y1="60" y2="100" stroke="#2b2d42" stroke-width="1.2"/><line x1="${W - 10}" x2="${W - 10}" y1="60" y2="100" stroke="#2b2d42" stroke-width="3.5"/></svg>`;
  }
  function updateStats() {
    const done = pState.filter(s => s === 'done').length, n = UNITS[unit].notes.length;
    $('#stProg').textContent = `${done}/${n}`; $('#stFix').textContent = fixes; $('#stFirst').textContent = done ? Math.round(firstOk / done * 100) + '%' : '—';
  }
  function resetPractice() {
    stopAll(); pIdx = 0; pState = []; firstOk = 0; fixes = 0; pStart = 0; clearInterval(pTimer); $('#stTime').textContent = '0:00'; phase = 'pitch';
    $$('#phaseSeg button').forEach(b => b.classList.toggle('active', b.dataset.phase === 'pitch')); $$('#phaseSeg button')[1].disabled = true;
    $('#rhythmBar').hidden = true; $('#unitTitle').textContent = `${UNITS[unit].name} · 第 1 条`;
    feedback('♪', '按下目标音对应的按键', '紫色音符是当前目标。按错会标红，可以继续纠正。'); drawScore(); updateStats();
  }
  function feedback(big, title, text, color = '') { $('#fbBig').textContent = big; $('#fbBig').style.color = color; $('#fbTitle').textContent = title; $('#fbText').textContent = text; }
  const WKEYS = { s: 0, d: 2, f: 4, g: 5, h: 7, j: 9, k: 11, l: 12 }, BKEYS = { e: 1, r: 3, y: 6, u: 8, i: 10 };
  $('#kgWhite').innerHTML = Object.entries(WKEYS).map(([k, v]) => `<span data-kg="${k}"><kbd>${k}</kbd>${NAMES[v % 12]}${v === 12 ? '′' : ''}</span>`).join('');
  $('#kgBlack').innerHTML = ['gap-h', 'e', 'r', 'gap', 'y', 'u', 'i', 'gap-h'].map(k => k.startsWith('gap') ? `<span class="gap" style="flex:${k === 'gap-h' ? .5 : 1}"></span>` : `<span data-kg="${k}"><kbd>${k}</kbd>${NAMES[BKEYS[k]]}</span>`).join('');
  function renderOct() { $$('#octMeter span').forEach(s => s.classList.toggle('on', +s.textContent === octave)); }
  let mod = null; // { key, used }
  function practiceNote(key) {
    const off = key in WKEYS ? WKEYS[key] : BKEYS[key]; const root = (octave + 1) * 12 + off;
    const played = [root]; if (mod) { mod.used = true; played.push(root + (mod.key === 'Shift' ? -12 : 12)); }
    played.forEach(p => voice(p, 'keyboard', 0, .5, .14));
    if (!pStart) { pStart = performance.now(); pTimer = setInterval(() => { const s = (performance.now() - pStart) / 1000; $('#stTime').textContent = `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`; }, 500); }
    if (phase === 'rhythm') { rhythmHit(); return; }
    const notes = UNITS[unit].notes; if (pIdx >= notes.length) return;
    const target = notes[pIdx][0];
    if (played.includes(target)) {
      if (pState[pIdx] !== 'wrong') firstOk++;
      pState[pIdx] = 'done'; pIdx++;
      if (pIdx >= notes.length) { feedback('✓', '认音全部正确！', '首次答案和纠正记录已分别保留。现在可以进入节奏轮。', 'var(--ok)'); $$('#phaseSeg button')[1].disabled = false; }
      else feedback(noteName(target), '正确', `下一个目标：第 ${pIdx + 1} 个音`, 'var(--ok)');
    } else { if (pState[pIdx] !== 'wrong') fixes++; pState[pIdx] = 'wrong'; feedback(noteName(played[0]), '再试一次', `你弹的是 ${played.map(noteName).join(' + ')}，提示：看看音符在第几线 / 间。`, 'var(--danger)'); setTimeout(() => { if (pState[pIdx] === 'wrong') drawScore(); }, 10); }
    drawScore(); updateStats();
  }
  $$('#phaseSeg button').forEach(b => b.onclick = () => { if (b.disabled) return; phase = b.dataset.phase; $$('#phaseSeg button').forEach(x => x.classList.toggle('active', x === b)); $('#rhythmBar').hidden = phase !== 'rhythm';
    if (phase === 'rhythm') { pState = []; drawScore(); feedback('♩', '节奏轮', '听 4 拍预备拍后，跟着节拍按任意音符键，系统记录你与原事件的时间偏差。'); } else resetPractice(); });
  $('#bpm').oninput = e => $('#bpmOut').textContent = e.target.value;
  let rhythm = null;
  $('#beatBtn').onclick = () => {
    stopAll(); const bpm = +$('#bpm').value, spb = 60 / bpm, c = ac(), t0 = c.currentTime + .1, dots = $$('#beatDots i');
    for (let i = 0; i < 4; i++) click(i === 0, .1 + i * spb);
    let acc = 0; const onsets = UNITS[unit].notes.map(([, d]) => { const t = t0 + (4 + acc) * spb; acc += d; return t; });
    rhythm = { t0, spb, onsets, judged: onsets.map(() => null) }; pState = []; drawScore();
    const total = 4 + acc;
    for (let b = 4; b < total; b++) click(b % 4 === 0, .1 + b * spb);
    const frame = () => { if (!rhythm) return; const el = (c.currentTime - t0) / spb; const bi = Math.floor(el);
      dots.forEach((d, i) => { d.classList.toggle('on', el >= 0 && bi % 4 === i); d.classList.toggle('first', i === 0); });
      rhythm.onsets.forEach((t, i) => { if (rhythm.judged[i] === null && c.currentTime - t > .3) { rhythm.judged[i] = 'miss'; pState[i] = 'wrong'; drawScore(); } });
      if (el > total + .5) { finishRhythm(); return; } rhythm.raf = requestAnimationFrame(frame); };
    frame(); feedback('4', '预备拍…', '第 5 拍开始弹奏'); $('#beatBtn').disabled = true;
  };
  function rhythmHit() {
    if (!rhythm) { feedback('♩', '先点击「开始节奏轮」', '节奏轮会先给出 4 拍预备拍。'); return; }
    const now = ac().currentTime; let best = -1, bd = 9;
    rhythm.onsets.forEach((t, i) => { if (rhythm.judged[i] === null && Math.abs(now - t) < bd) { bd = Math.abs(now - t); best = i; } });
    if (best < 0 || bd > .3) return;
    const ms = Math.round((now - rhythm.onsets[best]) * 1000); const good = Math.abs(ms) <= 120;
    rhythm.judged[best] = good ? 'ok' : 'off'; pState[best] = good ? 'done' : 'wrong'; drawScore();
    feedback(`${ms > 0 ? '+' : ''}${ms}`, good ? '很准！' : ms > 0 ? '稍慢了一点' : '稍快了一点', `第 ${best + 1} 个音 · 偏差 ${ms} ms（原型以 ±120 ms 作为演示阈值）`, good ? 'var(--ok)' : 'var(--warn)');
  }
  function finishRhythm() { if (!rhythm) return; const ok = rhythm.judged.filter(j => j === 'ok').length; feedback(`${ok}/${rhythm.onsets.length}`, '节奏轮完成', '本次结果作为独立记录保存（原型不写入）。可以调整速度后再来一次。', 'var(--accent)'); stopRhythm(); }
  function stopRhythm() { if (rhythm) { cancelAnimationFrame(rhythm.raf); rhythm = null; } $('#beatBtn').disabled = false; $$('#beatDots i').forEach(d => d.classList.remove('on')); }

  /* ---------------- 全局键盘 ---------------- */
  document.addEventListener('keydown', e => {
    if (typing() || e.ctrlKey || e.metaKey || e.altKey || $('.modal-back.show')) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (view === 'creation' && $('#pane-inputs').classList.contains('active')) {
      if (e.repeat) { if (k in KEYMAP) e.preventDefault(); return; }
      if (inst === 'drums' ? 'asdfghjk'.includes(k) && k.length === 1 : k in KEYMAP) { e.preventDefault(); noteOn(base + (KEYMAP[k] ?? 0), k); }
    } else if (view === 'practice') {
      if (k === 'Shift' || k === ' ') { e.preventDefault(); if (e.repeat) return; const name = k === ' ' ? 'Space' : 'Shift'; if (mod && mod.key !== name) { mod.blocked = true; return; } mod = { key: name, used: false }; return; }
      if (e.repeat) return;
      if (k in WKEYS || k in BKEYS) { e.preventDefault(); if (mod?.blocked) { feedback('✕', '两个修饰键同时按住', '此时不接受新音，先松开一个修饰键。', 'var(--warn)'); return; } $(`[data-kg="${k}"]`)?.classList.add('down'); practiceNote(k); }
    }
  });
  document.addEventListener('keyup', e => {
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (held.has(k)) noteOff(k);
    $(`[data-kg="${k}"]`)?.classList.remove('down');
    if (view === 'practice' && (k === 'Shift' || k === ' ') && mod) {
      const name = k === ' ' ? 'Space' : 'Shift';
      if (mod.key === name) { if (!mod.used && !mod.blocked) { octave = Math.max(3, Math.min(5, octave + (name === 'Shift' ? -1 : 1))); renderOct(); toast(`基础八度切换为 ${octave}`); } mod = null; }
      else if (mod.blocked) mod.blocked = false;
    }
  });
  window.addEventListener('blur', () => { [...held.keys()].forEach(noteOff); mod = null; });

  /* ---------------- 扒谱 ---------------- */
  const TASKS = [['Canon_in_D.mp3', '00:00–00:20 · 钢琴独奏', 'ok', '已完成'], ['雨的印记.wav', '01:10–02:05 · 混音分离', 'warn', '待确认分离'], ['练习录音_03.mp3', '00:00–00:45', 'danger', '已中断']];
  $('#taskList').innerHTML = TASKS.map((t, i) => `<div class="task ${i === 0 ? 'active' : ''}"><b>${t[0]}</b><span class="muted">${t[1]}</span><span class="row"><span class="tag ${t[2]}">${t[3]}</span></span></div>`).join('');
  const wave = $('#wave'); const DUR = 296; const BARS = 120;
  for (let i = 0; i < BARS; i++) { const b = document.createElement('i'); const env = .35 + .65 * Math.abs(Math.sin(i / 9)) * (0.6 + rand() * .4); b.style.height = env * 90 + '%'; wave.append(b); }
  function updateSel(changed) {
    let s = +$('#selStart').value, e = +$('#selEnd').value;
    if (e <= s) { changed === 'start' ? (e = Math.min(DUR, s + 1)) : (s = Math.max(0, e - 1)); }
    if (e - s > 60) { changed === 'start' ? (e = s + 60) : (s = e - 60); }
    $('#selStart').value = s; $('#selEnd').value = e; $('#startOut').textContent = fmt(s); $('#endOut').textContent = `${fmt(e)} · 片段 ${e - s} 秒（最多 60 秒）`;
    const sel = $('#waveSel'); sel.style.left = `calc(10px + (100% - 20px) * ${s / DUR})`; sel.style.width = `calc((100% - 20px) * ${(e - s) / DUR})`;
    $$('i', wave).forEach((b, i) => b.classList.toggle('in', i / BARS >= s / DUR && i / BARS <= e / DUR));
  }
  $('#selStart').oninput = () => updateSel('start'); $('#selEnd').oninput = () => updateSel('end'); updateSel('end');
  let tmode = 'solo';
  $('#modeCards').onclick = e => { const c = e.target.closest('.mode-card'); if (!c) return; tmode = c.dataset.mode; $$('.mode-card').forEach(x => x.classList.toggle('active', x === c)); $('[data-k=sep]').classList.toggle('skip', tmode === 'solo'); };
  $('#audioFile').onchange = e => { const f = e.target.files[0]; if (f) { $('#audioName').textContent = f.name; toast(`已选择 ${f.name}（原型不上传）`); } };
  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); if (ev === 'drop' && e.dataTransfer.files[0]) { $('#audioName').textContent = e.dataTransfer.files[0].name; toast('已选择文件（原型不上传）'); } }));
  let tJob = null;
  function pipe(k, st) { const p = $(`[data-k=${k}]`); p.classList.remove('run', 'ok'); if (st) p.classList.add(st); p.querySelector('.ps').textContent = st === 'ok' ? '✓' : st === 'run' ? '◌' : ['load', 'sep', 'tr', 'score'].indexOf(k) + 1; }
  function runSteps(keys, done) { const [k, ...rest] = keys; if (!k) { done(); return; } pipe(k, 'run'); tJob = setTimeout(() => { pipe(k, 'ok'); runSteps(rest, done); }, 1100); }
  $('#transBtn').onclick = () => {
    const btn = $('#transBtn');
    if (btn.dataset.wait) { delete btn.dataset.wait; btn.disabled = true; $('#transInfo').textContent = '继续转录分离后的钢琴声部…'; runSteps(['tr', 'score'], finish); return; }
    ['load', 'sep', 'tr', 'score'].forEach(k => pipe(k)); $('#transResult').hidden = true; btn.disabled = true; $('#transCancel').disabled = false; $('#transInfo').textContent = '本地推理中…';
    if (tmode === 'mix') runSteps(['load', 'sep'], () => { btn.disabled = false; btn.dataset.wait = '1'; btn.textContent = '试听后确认继续'; $('#transInfo').textContent = '分离完成：请先试听 piano 声部，确认没有明显串音再继续'; });
    else runSteps(['load', 'tr', 'score'], finish);
  };
  function finish() {
    const btn = $('#transBtn'); btn.disabled = false; btn.textContent = '重新转录'; $('#transCancel').disabled = true; $('#transInfo').textContent = '完成 · 原始 MIDI 已保留，校对将另存新修订';
    const roll = $('#resultRoll'); let h = ''; for (let i = 0; i < 186; i++) { const x = rand() * 96, y = 8 + (rand() < .55 ? rand() * 60 : 90 + rand() * 70); h += `<i style="left:${x}%;top:${y}px;width:${1 + rand() * 4}%;background:${y < 80 ? 'var(--c-keys)' : 'var(--c-bass)'}"></i>`; }
    roll.innerHTML = h; $('#transResult').hidden = false; toast('转录完成（模拟）');
  }
  $('#transCancel').onclick = () => { clearTimeout(tJob); const btn = $('#transBtn'); delete btn.dataset.wait; btn.disabled = false; btn.textContent = '开始转录'; $('#transCancel').disabled = true; $('#transInfo').textContent = '已取消 · 之前的成功结果不会被覆盖'; $$('.pipe').forEach(p => { p.classList.remove('run'); }); };

  /* ---------------- 初始化 ---------------- */
  renderSongs(); renderKeyboard(); countLyrics(); renderTracks(); renderLyricPractice(); resetPractice(); renderOct();
  go(location.hash.slice(1) || 'home');
  window.addEventListener('hashchange', () => { const h = location.hash.slice(1); if (h !== view) go(h); });
})();
