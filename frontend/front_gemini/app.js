/**
 * NeoNote · 新的乐章 — Gemini 视觉重设计静态交互原型脚本
 * Path: frontend/front_gemini/app.js
 * 说明：本脚本仅用于静态视觉与交互原型演示（内置 Web Audio 合成器与本地内存状态），
 * 不连接后端 API，不读写 data/ 目录，不读取 .env，不发起真实模型或人声推理请求。
 */

(() => {
  'use strict';

  /* ========================================================================
     1. 音名工具与常量定义
     ======================================================================== */
  const NOTE_NAMES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
  const DRUM_LABELS = [
    '底鼓 Kick', '侧击 Rim', '军鼓 Snare', '拍手 Clap', '闭镲 CHH',
    '低通鼓 LTom', '开镲 OHH', '中通鼓 MTom', '高通鼓 HTom', '吊镲 Crash',
    '叮叮镲 Ride', '牛铃 Cowbell', '沙锤 Tamb'
  ];

  // 创作模式键位：A W S E D F T G Y H U J K (半音递增 0..12)
  const CREATION_KEYS = [
    { offset: 0, code: 'KeyA', label: 'A', black: false },
    { offset: 1, code: 'KeyW', label: 'W', black: true, leftPct: 9.2 },
    { offset: 2, code: 'KeyS', label: 'S', black: false },
    { offset: 3, code: 'KeyE', label: 'E', black: true, leftPct: 21.8 },
    { offset: 4, code: 'KeyD', label: 'D', black: false },
    { offset: 5, code: 'KeyF', label: 'F', black: false },
    { offset: 6, code: 'KeyT', label: 'T', black: true, leftPct: 46.8 },
    { offset: 7, code: 'KeyG', label: 'G', black: false },
    { offset: 8, code: 'KeyY', label: 'Y', black: true, leftPct: 59.3 },
    { offset: 9, code: 'KeyH', label: 'H', black: false },
    { offset: 10, code: 'KeyU', label: 'U', black: true, leftPct: 71.8 },
    { offset: 11, code: 'KeyJ', label: 'J', black: false },
    { offset: 12, code: 'KeyK', label: 'K', black: false },
  ];

  // 练习模式键位：白键 s d f g h j k l (l 始终是右侧高一个八度的 C)，黑键 e r y u i
  const PRACTICE_KEYS = [
    { offset: 0, code: 'KeyS', label: 's', black: false },
    { offset: 1, code: 'KeyE', label: 'e', black: true, leftPct: 9.2 },
    { offset: 2, code: 'KeyD', label: 'd', black: false },
    { offset: 3, code: 'KeyR', label: 'r', black: true, leftPct: 21.8 },
    { offset: 4, code: 'KeyF', label: 'f', black: false },
    { offset: 5, code: 'KeyG', label: 'g', black: false },
    { offset: 6, code: 'KeyY', label: 'y', black: true, leftPct: 46.8 },
    { offset: 7, code: 'KeyH', label: 'h', black: false },
    { offset: 8, code: 'KeyU', label: 'u', black: true, leftPct: 59.3 },
    { offset: 9, code: 'KeyJ', label: 'j', black: false },
    { offset: 10, code: 'KeyI', label: 'i', black: true, leftPct: 71.8 },
    { offset: 11, code: 'KeyK', label: 'k', black: false },
    { offset: 12, code: 'KeyL', label: 'l', black: false },
  ];

  function midiToName(pitch, isDrum = false) {
    if (isDrum) {
      return DRUM_LABELS[pitch % DRUM_LABELS.length] || `Drum ${pitch}`;
    }
    const octave = Math.floor(pitch / 12) - 1;
    return `${NOTE_NAMES[pitch % 12]}${octave}`;
  }

  function midiToFreq(pitch) {
    return 440 * Math.pow(2, (pitch - 69) / 12);
  }

  /* ========================================================================
     2. Web Audio 多音色合成器 (支持键盘/吉他/贝斯/鼓组与电平表驱动)
     ======================================================================== */
  class StudioSynth {
    constructor() {
      this.ctx = null;
      this.activeTimers = new Set();
      this.vuLevel = 0;
    }

    ensureContext() {
      if (!this.ctx) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
          this.ctx = new AudioCtx();
        }
      }
      if (this.ctx && this.ctx.state === 'suspended') {
        this.ctx.resume();
      }
      const badge = document.getElementById('globalAudioBadge');
      if (badge) {
        badge.dataset.ready = 'true';
        badge.textContent = '🔊 Web Audio 合成器已就绪（原型模拟音色）';
      }
      return this.ctx;
    }

    triggerNote(pitch, family = 'keyboard', durationSec = 0.45, velocity = 90) {
      const ctx = this.ensureContext();
      if (!ctx) return;

      const now = ctx.currentTime;
      const gainScale = Math.max(0.15, Math.min(1, velocity / 110)) * 0.28;
      this.bumpVu(gainScale * 3.2);

      if (family === 'drums') {
        this.triggerDrum(ctx, now, pitch, gainScale);
        return;
      }

      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const filter = ctx.createBiquadFilter();
      const amp = ctx.createGain();

      const freq = midiToFreq(pitch);
      osc1.frequency.setValueAtTime(freq, now);
      osc2.frequency.setValueAtTime(freq, now);

      if (family === 'guitar') {
        osc1.type = 'sawtooth';
        osc2.type = 'triangle';
        osc2.detune.setValueAtTime(6, now);
        filter.type = 'lowpass';
        filter.frequency.setValueAtTime(2600, now);
        filter.frequency.exponentialRampToValueAtTime(650, now + durationSec);
      } else if (family === 'bass') {
        osc1.type = 'triangle';
        osc2.type = 'sine';
        osc2.frequency.setValueAtTime(freq * 0.5, now);
        filter.type = 'lowpass';
        filter.frequency.setValueAtTime(920, now);
      } else if (family === 'vocal') {
        osc1.type = 'sawtooth';
        osc2.type = 'triangle';
        osc2.detune.setValueAtTime(9, now);
        filter.type = 'bandpass';
        filter.frequency.setValueAtTime(1150, now);
        filter.Q.setValueAtTime(2.5, now);
      } else {
        // 默认原声钢琴包络
        osc1.type = 'triangle';
        osc2.type = 'sine';
        osc2.frequency.setValueAtTime(freq * 2, now);
        filter.type = 'lowpass';
        filter.frequency.setValueAtTime(2200, now);
      }

      amp.gain.setValueAtTime(0.001, now);
      amp.gain.linearRampToValueAtTime(gainScale, now + 0.014);
      amp.gain.exponentialRampToValueAtTime(0.0008, now + Math.max(0.12, durationSec));

      osc1.connect(filter);
      osc2.connect(filter);
      filter.connect(amp);
      amp.connect(ctx.destination);

      osc1.start(now);
      osc2.start(now);
      const stopAt = now + Math.max(0.14, durationSec) + 0.03;
      osc1.stop(stopAt);
      osc2.stop(stopAt);
    }

    triggerDrum(ctx, now, pitch, gainScale) {
      const idx = pitch % 4;
      const osc = ctx.createOscillator();
      const amp = ctx.createGain();
      if (idx === 0) {
        // Kick
        osc.type = 'sine';
        osc.frequency.setValueAtTime(135, now);
        osc.frequency.exponentialRampToValueAtTime(42, now + 0.16);
        amp.gain.setValueAtTime(gainScale * 1.4, now);
        amp.gain.exponentialRampToValueAtTime(0.001, now + 0.2);
      } else if (idx === 1) {
        // Snare-like
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(220, now);
        osc.frequency.exponentialRampToValueAtTime(95, now + 0.12);
        amp.gain.setValueAtTime(gainScale * 1.1, now);
        amp.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
      } else {
        // Hi-Hat / Rim
        osc.type = 'square';
        osc.frequency.setValueAtTime(680 + (pitch % 5) * 140, now);
        amp.gain.setValueAtTime(gainScale * 0.65, now);
        amp.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
      }
      osc.connect(amp);
      amp.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.22);
    }

    scheduleSequence(events, onComplete) {
      this.stopAll();
      events.forEach(ev => {
        const timer = window.setTimeout(() => {
          this.activeTimers.delete(timer);
          this.triggerNote(ev.pitch, ev.family || 'keyboard', ev.duration || 0.35, ev.velocity || 90);
          if (typeof ev.onTrigger === 'function') ev.onTrigger();
        }, Math.max(0, ev.time * 1000));
        this.activeTimers.add(timer);
      });
      if (onComplete) {
        const maxEnd = events.reduce((m, e) => Math.max(m, e.time + (e.duration || 0.35)), 0);
        const endTimer = window.setTimeout(() => {
          this.activeTimers.delete(endTimer);
          onComplete();
        }, maxEnd * 1000 + 80);
        this.activeTimers.add(endTimer);
      }
    }

    stopAll() {
      this.activeTimers.forEach(id => clearTimeout(id));
      this.activeTimers.clear();
      this.vuLevel = 0;
      this.renderVu();
    }

    bumpVu(amount) {
      this.vuLevel = Math.min(1, Math.max(this.vuLevel, amount));
      this.renderVu();
    }

    decayVu() {
      if (this.vuLevel > 0.02) {
        this.vuLevel *= 0.78;
      } else {
        this.vuLevel = 0;
      }
      this.renderVu();
    }

    renderVu() {
      const container = document.getElementById('headerVuBars');
      if (!container) return;
      const bars = container.querySelectorAll('span');
      const activeCount = Math.round(this.vuLevel * bars.length);
      bars.forEach((bar, i) => {
        const lit = i < activeCount;
        bar.classList.toggle('lit', lit);
        bar.classList.toggle('hot', lit && i >= 4);
        bar.style.height = lit ? `${6 + i * 1.6}px` : '4px';
      });
    }
  }

  const synth = new StudioSynth();
  setInterval(() => synth.decayVu(), 90);

  /* ========================================================================
     3. 全局通知与主题切换
     ======================================================================== */
  let toastTimeout = null;
  function showToast(message) {
    const banner = document.getElementById('globalToast');
    const text = document.getElementById('globalToastText');
    if (!banner || !text) return;
    text.textContent = message;
    banner.hidden = false;
    if (toastTimeout) clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => {
      banner.hidden = true;
    }, 5200);
  }

  document.getElementById('globalToastClose')?.addEventListener('click', () => {
    document.getElementById('globalToast').hidden = true;
  });

  document.getElementById('panicStopBtn')?.addEventListener('click', () => {
    synth.stopAll();
    showToast('已停止所有正在播放的合成器预览与节拍器声音。');
  });

  // 主题切换（日间暖调琴房 vs 夜色录音棚）
  const themeBtn = document.getElementById('themeToggleBtn');
  const themeLabel = document.getElementById('themeLabel');
  themeBtn?.addEventListener('click', () => {
    const root = document.documentElement;
    const next = root.getAttribute('data-theme') === 'midnight' ? 'atelier' : 'midnight';
    root.setAttribute('data-theme', next);
    if (themeLabel) {
      themeLabel.textContent = next === 'midnight' ? '切换：日间暖调琴房' : '切换：夜色录音棚';
    }
  });

  /* ========================================================================
     4. 顶层四大视图导航切换 (00 总览 / 01 创作 / 02 练习 / 03 扒谱)
     ======================================================================== */
  let currentMode = 'overview';

  function switchMode(targetMode) {
    if (!['overview', 'creation', 'practice', 'transcription'].includes(targetMode)) return;
    synth.stopAll();
    currentMode = targetMode;

    document.querySelectorAll('.mode-tab').forEach(btn => {
      const active = btn.dataset.mode === targetMode;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
    });

    document.querySelectorAll('.mode-view').forEach(section => {
      const isTarget = section.id === `view-${targetMode}`;
      section.hidden = !isTarget;
      section.classList.toggle('active', isTarget);
    });
  }

  document.querySelectorAll('.mode-tab').forEach(btn => {
    btn.addEventListener('click', () => switchMode(btn.dataset.mode));
  });

  document.querySelectorAll('[data-goto-mode]').forEach(btn => {
    btn.addEventListener('click', () => switchMode(btn.dataset.gotoMode));
  });

  document.getElementById('brandHomeLink')?.addEventListener('click', e => {
    e.preventDefault();
    switchMode('overview');
  });

  /* ========================================================================
     5. VIEW 00: 总览页迷你合成器琴台
     ======================================================================== */
  let heroFamily = 'keyboard';

  function initHeroDeck() {
    const selector = document.getElementById('heroTimbreSelector');
    const keybed = document.getElementById('heroMiniPiano');
    const readout = document.getElementById('heroLastNoteReadout');
    if (!keybed) return;

    selector?.querySelectorAll('.timbre-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        heroFamily = btn.dataset.family;
        selector.querySelectorAll('.timbre-pill').forEach(b => b.classList.toggle('active', b === btn));
        if (readout) {
          readout.textContent = `已切换音色：${btn.textContent.trim()}，点击琴键即可试听`;
        }
      });
    });

    renderPianoKeybed(keybed, CREATION_KEYS, 60, false, (pitch, keyMeta) => {
      const isDrum = heroFamily === 'drums';
      synth.triggerNote(pitch, heroFamily, 0.45, 95);
      if (readout) {
        readout.textContent = `触发音符：${midiToName(pitch, isDrum)} (MIDI ${pitch}) · 按键 ${keyMeta.label}`;
      }
    });

    document.getElementById('heroDemoArpBtn')?.addEventListener('click', () => {
      const notes = [60, 64, 67, 71, 72, 71, 67, 64];
      synth.scheduleSequence(
        notes.map((p, i) => ({
          pitch: p,
          family: heroFamily === 'drums' ? 'keyboard' : heroFamily,
          time: i * 0.2,
          duration: 0.38,
          velocity: 92,
        }))
      );
      showToast('正在试听 Cmaj7 温暖琶音示例（Web Audio 实时合成）');
    });
  }

  // 通用琴键渲染辅助函数
  function renderPianoKeybed(container, keyDefs, basePitch, isDrum, onPlay) {
    container.innerHTML = '';
    // 先渲染白键以撑开 flex 布局，再绝对定位黑键
    const whites = keyDefs.filter(k => !k.black);
    const blacks = keyDefs.filter(k => k.black);

    whites.forEach(k => {
      const pitch = basePitch + k.offset;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pkey white';
      btn.dataset.code = k.code;
      btn.dataset.pitch = String(pitch);
      btn.innerHTML = `<span class="pkey-note">${midiToName(pitch, isDrum)}</span><span class="pkey-shortcut">${k.label}</span>`;
      bindKeyPointer(btn, pitch, k, onPlay);
      container.appendChild(btn);
    });

    blacks.forEach(k => {
      const pitch = basePitch + k.offset;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pkey black';
      btn.style.left = `${k.leftPct}%`;
      btn.dataset.code = k.code;
      btn.dataset.pitch = String(pitch);
      btn.innerHTML = `<span class="pkey-note">${isDrum ? '' : midiToName(pitch, false)}</span><span class="pkey-shortcut">${k.label}</span>`;
      bindKeyPointer(btn, pitch, k, onPlay);
      container.appendChild(btn);
    });
  }

  function bindKeyPointer(btn, pitch, keyMeta, onPlay) {
    btn.addEventListener('pointerdown', e => {
      e.preventDefault();
      btn.classList.add('active');
      onPlay(pitch, keyMeta);
    });
    const clear = () => btn.classList.remove('active');
    btn.addEventListener('pointerup', clear);
    btn.addEventListener('pointerleave', clear);
    btn.addEventListener('pointercancel', clear);
  }

  function flashKeyElement(container, code) {
    if (!container) return;
    const el = container.querySelector(`[data-code="${code}"]`);
    if (el) {
      el.classList.add('active');
      setTimeout(() => el.classList.remove('active'), 180);
    }
  }

  /* ========================================================================
     6. VIEW 01: 创作工作台 (歌曲管理、录制、歌词、钢琴卷帘与导出)
     ======================================================================== */
  const creationState = {
    songs: [
      {
        id: 'song_8f3a91c2',
        name: '周末的落日列车',
        revision: 4,
        bpm: 108,
        dirty: false,
        theme: '毕业季傍晚的列车，告别旧时光奔赴新生活',
        emotion: '歌词有一点离别的不舍，但音乐希望是温暖明亮的轻快流行摇滚（约 108 BPM），电钢琴清澈铺底，木吉他分解和弦推进，副歌鼓点富有朝气。',
      },
      {
        id: 'song_4d19b0e7',
        name: '雨后天晴的操场',
        revision: 2,
        bpm: 120,
        dirty: false,
        theme: '午后雷阵雨停歇后的校园跑道',
        emotion: '清新轻快的独立流行，木吉他扫弦与轻快踩镲，充满氧气感。',
      },
      {
        id: 'song_c9201a55',
        name: '星空下的独白',
        revision: 1,
        bpm: 76,
        dirty: false,
        theme: '深夜独自散步时的安静思考',
        emotion: '温柔舒缓的叙事钢琴与大提琴铺底，留白充足。',
      },
    ],
    trashedSongs: [
      { id: 'song_71a0e831', name: '初夏练习草稿_旧版', revision: 3 },
    ],
    revisions: [
      { number: 4, label: '采用 AI 候选 #cand_04（全文歌词谱曲）', time: '2026-11-18 15:42' },
      { number: 3, label: '更新歌词全文（6 行 50 字词）', time: '2026-11-18 15:36' },
      { number: 2, label: '录制演奏片段（8 个钢琴音符）', time: '2026-11-18 15:30' },
      { number: 1, label: '创建歌曲工程', time: '2026-11-18 15:25' },
    ],
    activeSongId: 'song_8f3a91c2',
    step: 'inputs',
    family: 'keyboard',
    basePitch: 60,
    recording: false,
    recordedNotes: [
      { pitch: 60, start: 0.0, duration: 0.45, family: 'keyboard' },
      { pitch: 62, start: 0.5, duration: 0.45, family: 'keyboard' },
      { pitch: 64, start: 1.0, duration: 0.45, family: 'keyboard' },
      { pitch: 67, start: 1.5, duration: 0.65, family: 'keyboard' },
      { pitch: 69, start: 2.3, duration: 0.45, family: 'keyboard' },
      { pitch: 67, start: 2.8, duration: 0.45, family: 'keyboard' },
      { pitch: 64, start: 3.3, duration: 0.45, family: 'keyboard' },
      { pitch: 62, start: 3.8, duration: 0.55, family: 'keyboard' },
    ],
    recordStartStamp: 0,
    usingLyrics: true,
    activeTrackId: 'trk_keyboard',
    selectedNoteId: 'n_3',
    lyricSpeed: 1,
    activeLyricLine: 0,
    tracks: [
      {
        id: 'trk_keyboard',
        name: '键盘 · 主旋律与伴奏',
        family: 'keyboard',
        color: '#c85a32',
        program: 0,
        volume: 88,
        solo: false,
        mute: false,
        notes: [
          { id: 'n_1', pitch: 60, start: 0.0, duration: 1.0, velocity: 90, lyric: '前奏' },
          { id: 'n_2', pitch: 62, start: 1.0, duration: 1.0, velocity: 88, lyric: '前奏' },
          { id: 'n_3', pitch: 64, start: 2.0, duration: 1.0, velocity: 92, lyric: '晚' },
          { id: 'n_4', pitch: 67, start: 3.0, duration: 0.5, velocity: 90, lyric: '风' },
          { id: 'n_5', pitch: 69, start: 3.5, duration: 0.5, velocity: 86, lyric: '风(转)' },
          { id: 'n_6', pitch: 67, start: 4.0, duration: 1.0, velocity: 92, lyric: '吹' },
          { id: 'n_7', pitch: 64, start: 5.0, duration: 1.0, velocity: 88, lyric: '过' },
          { id: 'n_8', pitch: 62, start: 6.0, duration: 1.0, velocity: 86, lyric: '站' },
          { id: 'n_9', pitch: 60, start: 7.0, duration: 1.0, velocity: 84, lyric: '台' },
          { id: 'n_10', pitch: 62, start: 8.5, duration: 0.75, velocity: 88, lyric: '的' },
          { id: 'n_11', pitch: 64, start: 9.25, duration: 0.75, velocity: 90, lyric: '灯' },
          { id: 'n_12', pitch: 67, start: 10.0, duration: 1.5, velocity: 95, lyric: '光' },
        ],
      },
      {
        id: 'trk_guitar',
        name: '吉他 · 分解和弦',
        family: 'guitar',
        color: '#286653',
        program: 25,
        volume: 76,
        solo: false,
        mute: false,
        notes: [
          { id: 'g_1', pitch: 55, start: 0.0, duration: 1.5, velocity: 78 },
          { id: 'g_2', pitch: 60, start: 1.5, duration: 1.5, velocity: 75 },
          { id: 'g_3', pitch: 59, start: 3.0, duration: 1.5, velocity: 80 },
          { id: 'g_4', pitch: 64, start: 4.5, duration: 1.5, velocity: 76 },
          { id: 'g_5', pitch: 57, start: 6.0, duration: 2.0, velocity: 80 },
          { id: 'g_6', pitch: 60, start: 8.0, duration: 2.0, velocity: 82 },
        ],
      },
      {
        id: 'trk_bass',
        name: '贝斯 · 根音律动',
        family: 'bass',
        color: '#7a5c9e',
        program: 33,
        volume: 82,
        solo: false,
        mute: false,
        notes: [
          { id: 'b_1', pitch: 48, start: 0.0, duration: 2.0, velocity: 88 },
          { id: 'b_2', pitch: 43, start: 2.0, duration: 2.0, velocity: 86 },
          { id: 'b_3', pitch: 45, start: 4.0, duration: 2.0, velocity: 88 },
          { id: 'b_4', pitch: 41, start: 6.0, duration: 2.0, velocity: 85 },
          { id: 'b_5', pitch: 43, start: 8.0, duration: 3.0, velocity: 90 },
        ],
      },
      {
        id: 'trk_drums',
        name: '鼓组 · 流行节奏 (Ch 10)',
        family: 'drums',
        color: '#b87324',
        program: 0,
        volume: 80,
        solo: false,
        mute: false,
        notes: [
          { id: 'd_1', pitch: 36, start: 0.0, duration: 0.5, velocity: 96 },
          { id: 'd_2', pitch: 42, start: 1.0, duration: 0.5, velocity: 74 },
          { id: 'd_3', pitch: 38, start: 2.0, duration: 0.5, velocity: 92 },
          { id: 'd_4', pitch: 42, start: 3.0, duration: 0.5, velocity: 74 },
          { id: 'd_5', pitch: 36, start: 4.0, duration: 0.5, velocity: 96 },
          { id: 'd_6', pitch: 38, start: 6.0, duration: 0.5, velocity: 92 },
          { id: 'd_7', pitch: 36, start: 8.0, duration: 0.5, velocity: 96 },
          { id: 'd_8', pitch: 38, start: 10.0, duration: 0.5, velocity: 94 },
        ],
      },
    ],
    undoStack: [],
    redoStack: [],
  };

  function markCreationDirty(isDirty = true) {
    const song = creationState.songs.find(s => s.id === creationState.activeSongId);
    if (song) song.dirty = isDirty;
    const badge = document.getElementById('creationSaveBadge');
    if (badge && song) {
      badge.dataset.dirty = String(isDirty);
      badge.textContent = isDirty ? `有未保存的草稿修改 · 基于 v${song.revision}` : `已保存 · v${song.revision}`;
    }
  }

  function renderSongSidebar() {
    const container = document.getElementById('songListContainer');
    const countBadge = document.getElementById('songCountBadge');
    const trashBadge = document.getElementById('trashCountBadge');
    if (!container) return;

    if (countBadge) countBadge.textContent = String(creationState.songs.length);
    if (trashBadge) trashBadge.textContent = String(creationState.trashedSongs.length);

    container.innerHTML = '';
    creationState.songs.forEach(song => {
      const card = document.createElement('div');
      card.className = `song-row-card ${song.id === creationState.activeSongId ? 'active' : ''}`;
      card.innerHTML = `
        <button type="button" class="song-row-main">
          <strong>♫ ${song.name}</strong>
          <small>data/${song.id} · v${song.revision}</small>
        </button>
        <button type="button" class="song-delete-btn" title="移入回收站">删除</button>
      `;
      card.querySelector('.song-row-main').addEventListener('click', () => {
        creationState.activeSongId = song.id;
        document.getElementById('activeSongTitle').textContent = song.name;
        document.getElementById('activeSongIdPill').textContent = `data/${song.id}`;
        document.getElementById('themeInput').value = song.theme;
        document.getElementById('emotionInput').value = song.emotion;
        markCreationDirty(song.dirty);
        renderSongSidebar();
        showToast(`已切换至歌曲工程「${song.name}」（data/${song.id}）`);
      });

      card.querySelector('.song-delete-btn').addEventListener('click', () => {
        if (creationState.songs.length <= 1) {
          showToast('至少保留一首示例歌曲以供原型演示。');
          return;
        }
        creationState.songs = creationState.songs.filter(s => s.id !== song.id);
        creationState.trashedSongs.unshift(song);
        if (creationState.activeSongId === song.id) {
          creationState.activeSongId = creationState.songs[0].id;
          document.getElementById('activeSongTitle').textContent = creationState.songs[0].name;
          document.getElementById('activeSongIdPill').textContent = `data/${creationState.songs[0].id}`;
        }
        renderSongSidebar();
        showToast(`「${song.name}」已整目录移入回收站 data/.trash/${song.id}，可随时恢复。`);
      });

      container.appendChild(card);
    });
  }

  function renderCreationKeyboard() {
    const keybed = document.getElementById('creationPianoKeybed');
    if (!keybed) return;
    const isDrum = creationState.family === 'drums';
    renderPianoKeybed(keybed, CREATION_KEYS, creationState.basePitch, isDrum, (pitch, keyMeta) => {
      triggerCreationKey(pitch, keyMeta.label);
    });
  }

  function triggerCreationKey(pitch, label) {
    const isDrum = creationState.family === 'drums';
    synth.triggerNote(pitch, creationState.family, 0.42, 92);
    const pressedEl = document.getElementById('pressedNotesReadout');
    if (pressedEl) {
      pressedEl.textContent = `刚刚按下：${midiToName(pitch, isDrum)} (MIDI ${pitch} · 键 ${label})`;
    }
    if (creationState.recording) {
      const elapsed = Math.max(0, (performance.now() - creationState.recordStartStamp) / 1000);
      creationState.recordedNotes.push({
        pitch,
        start: Number(elapsed.toFixed(2)),
        duration: 0.4,
        family: creationState.family,
      });
      updateRecordedBadge();
      markCreationDirty(true);
    }
  }

  function updateRecordedBadge() {
    const badge = document.getElementById('recordedCountBadge');
    if (!badge) return;
    const count = creationState.recordedNotes.length;
    const totalSec = count
      ? Math.max(...creationState.recordedNotes.map(n => n.start + n.duration)).toFixed(1)
      : '0.0';
    badge.textContent = `${creationState.recording ? '● 录制中 · ' : '已录 '}${count} 个音符 · ${totalSec} 秒`;
  }

  // 歌词统计与预览渲染
  function updateLyricsStatus() {
    const textarea = document.getElementById('lyricsTextarea');
    const pill = document.getElementById('lyricStatsPill');
    const preview = document.getElementById('lyricPreviewLines');
    const toggleBtn = document.getElementById('toggleUseLyricsBtn');
    if (!textarea || !pill || !preview) return;

    const rawLines = textarea.value.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const charUnits = rawLines.join('').length;

    if (!creationState.usingLyrics) {
      pill.textContent = `当前仅使用主题与旋律（已暂存 ${rawLines.length} 行歌词原文件，本次不发送给模型）`;
      if (toggleBtn) toggleBtn.textContent = '切换为：使用歌词全文谱曲';
    } else {
      pill.textContent = `已启用全文谱曲：${rawLines.length} 行 · ${charUnits} / 1024 字词（上限 64 行）`;
      if (toggleBtn) toggleBtn.textContent = '切换为：本次不使用歌词（仅器乐）';
    }

    preview.innerHTML = rawLines
      .slice(0, 6)
      .map((line, idx) => `<div class="lyric-line-chip">${idx + 1}. ${line}</div>`)
      .join('');
  }

  /* ------------------------------------------------------------------------
     Step 02: 四声部混音器 + SVG 钢琴卷帘 (Piano Roll) + 歌词对应
     ------------------------------------------------------------------------ */
  function renderMixerTracks() {
    const container = document.getElementById('mixerTracksContainer');
    if (!container) return;
    container.innerHTML = '';

    creationState.tracks.forEach(trk => {
      const card = document.createElement('div');
      card.className = `mixer-track-card ${trk.id === creationState.activeTrackId ? 'active' : ''}`;
      card.style.setProperty('--track-accent', trk.color);
      card.innerHTML = `
        <div class="mixer-track-top">
          <button type="button" class="mixer-track-select">
            ${trk.name}
            <small>${trk.notes.length} 音符 · 音量 ${trk.volume}%</small>
          </button>
          <div class="sm-btns">
            <button type="button" class="sm-btn ${trk.solo ? 'on' : ''}" data-act="solo" title="独奏 Solo">S</button>
            <button type="button" class="sm-btn ${trk.mute ? 'on' : ''}" data-act="mute" title="静音 Mute">M</button>
          </div>
        </div>
        <div class="mixer-track-controls">
          <input type="range" min="0" max="100" value="${trk.volume}" aria-label="${trk.name}音量" />
        </div>
      `;

      card.querySelector('.mixer-track-select').addEventListener('click', () => {
        creationState.activeTrackId = trk.id;
        creationState.selectedNoteId = trk.notes[0]?.id || null;
        renderMixerTracks();
        renderPianoRoll();
      });

      card.querySelector('[data-act="solo"]').addEventListener('click', () => {
        trk.solo = !trk.solo;
        renderMixerTracks();
      });

      card.querySelector('[data-act="mute"]').addEventListener('click', () => {
        trk.mute = !trk.mute;
        renderMixerTracks();
      });

      card.querySelector('input[type="range"]').addEventListener('input', e => {
        trk.volume = Number(e.target.value);
        card.querySelector('small').textContent = `${trk.notes.length} 音符 · 音量 ${trk.volume}%`;
      });

      container.appendChild(card);
    });
  }

  function renderPianoRoll() {
    const svg = document.getElementById('pianoRollSvg');
    const titleEl = document.getElementById('rollTrackTitle');
    const dotEl = document.getElementById('rollTrackDot');
    if (!svg) return;

    const track = creationState.tracks.find(t => t.id === creationState.activeTrackId) || creationState.tracks[0];
    if (titleEl) titleEl.textContent = `${track.name} · 音符时间轴 (Piano Roll)`;
    if (dotEl) dotEl.style.background = track.color;

    const width = 920;
    const height = 320;
    const leftGutter = 56;
    const totalBeats = 12;
    const beatWidth = (width - leftGutter) / totalBeats;

    const pitches = track.notes.map(n => n.pitch);
    const minPitch = Math.min(...(pitches.length ? pitches : [55])) - 2;
    const maxPitch = Math.max(...(pitches.length ? pitches : [72])) + 2;
    const rowCount = Math.max(10, maxPitch - minPitch + 1);
    const rowHeight = height / rowCount;

    let html = '';

    // 1. 绘制横行与左侧音名标尺
    for (let i = 0; i < rowCount; i++) {
      const pitch = maxPitch - i;
      const y = i * rowHeight;
      const isBlack = [1, 3, 6, 8, 10].includes(pitch % 12);
      html += `<rect x="${leftGutter}" y="${y}" width="${width - leftGutter}" height="${rowHeight}" fill="${isBlack ? 'rgba(0,0,0,0.22)' : 'rgba(255,255,255,0.02)'}" stroke="rgba(255,255,255,0.05)" stroke-width="1" data-grid-pitch="${pitch}" />`;
      html += `<rect x="0" y="${y}" width="${leftGutter}" height="${rowHeight}" fill="${isBlack ? '#181614' : '#262320'}" stroke="rgba(255,255,255,0.08)" />`;
      html += `<text x="10" y="${y + rowHeight * 0.68}" fill="#b9b0a4" font-size="10" font-family="JetBrains Mono, monospace">${midiToName(pitch, track.family === 'drums')}</text>`;
    }

    // 2. 绘制拍线与小节线
    for (let b = 0; b <= totalBeats; b++) {
      const x = leftGutter + b * beatWidth;
      const isBar = b % 4 === 0;
      html += `<line x1="${x}" y1="0" x2="${x}" y2="${height}" stroke="${isBar ? 'rgba(255,255,255,0.24)' : 'rgba(255,255,255,0.08)'}" stroke-width="${isBar ? 1.5 : 1}" />`;
      if (b < totalBeats) {
        html += `<text x="${x + 5}" y="12" fill="rgba(255,255,255,0.4)" font-size="9" font-family="JetBrains Mono, monospace">${b + 1}拍</text>`;
      }
    }

    // 3. 绘制音符块
    track.notes.forEach(note => {
      const rowIdx = maxPitch - note.pitch;
      const x = leftGutter + note.start * beatWidth;
      const y = rowIdx * rowHeight + 2;
      const w = Math.max(14, note.duration * beatWidth - 3);
      const h = Math.max(12, rowHeight - 4);
      const isSelected = note.id === creationState.selectedNoteId;

      html += `
        <g class="roll-note-group" data-note-id="${note.id}" style="cursor:pointer">
          <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4"
            fill="${track.color}"
            stroke="${isSelected ? '#ffffff' : 'rgba(0,0,0,0.4)'}"
            stroke-width="${isSelected ? 2.5 : 1}" />
          <text x="${x + 6}" y="${y + h * 0.72}" fill="#ffffff" font-size="10" font-weight="600" pointer-events="none">
            ${note.lyric ? `${note.lyric} · ` : ''}${midiToName(note.pitch, track.family === 'drums')}
          </text>
        </g>
      `;
    });

    svg.innerHTML = html;

    // 绑定点击音符事件
    svg.querySelectorAll('.roll-note-group').forEach(g => {
      g.addEventListener('click', e => {
        e.stopPropagation();
        const nid = g.getAttribute('data-note-id');
        creationState.selectedNoteId = nid;
        const target = track.notes.find(n => n.id === nid);
        if (target) {
          synth.triggerNote(target.pitch, track.family, 0.35, target.velocity);
        }
        renderPianoRoll();
        syncNoteInspector();
      });
    });

    // 绑定点击空白网格新增音符事件
    svg.querySelectorAll('rect[data-grid-pitch]').forEach(cell => {
      cell.addEventListener('click', e => {
        const pitch = Number(cell.getAttribute('data-grid-pitch'));
        const pt = svg.createSVGPoint();
        pt.x = e.clientX;
        pt.y = e.clientY;
        const cursor = pt.matrixTransform(svg.getScreenCTM().inverse());
        const rawBeat = Math.max(0, (cursor.x - leftGutter) / beatWidth);
        const snappedStart = Math.round(rawBeat * 4) / 4;

        creationState.undoStack.push(structuredClone(creationState.tracks));
        const newId = `n_${Date.now().toString(36)}`;
        track.notes.push({
          id: newId,
          pitch,
          start: snappedStart,
          duration: 1.0,
          velocity: 90,
        });
        creationState.selectedNoteId = newId;
        synth.triggerNote(pitch, track.family, 0.35, 90);
        markCreationDirty(true);
        renderMixerTracks();
        renderPianoRoll();
        syncNoteInspector();
      });
    });

    syncNoteInspector();
  }

  function syncNoteInspector() {
    const track = creationState.tracks.find(t => t.id === creationState.activeTrackId);
    const note = track?.notes.find(n => n.id === creationState.selectedNoteId);
    const label = document.getElementById('selectedNoteLabel');
    if (!note) {
      if (label) label.textContent = '未选中音符（点击上方音符块可修改，点击空白网格可新增）';
      return;
    }
    if (label) {
      label.textContent = `已选中：${midiToName(note.pitch, track.family === 'drums')} (MIDI ${note.pitch})${note.lyric ? ` · 绑定字「${note.lyric}」` : ''}`;
    }
    document.getElementById('inspPitch').value = String(note.pitch);
    document.getElementById('inspStart').value = String(note.start);
    document.getElementById('inspDuration').value = String(note.duration);
    document.getElementById('inspVelocity').value = String(note.velocity);
  }

  function renderLyricAlignment() {
    const container = document.getElementById('lyricAlignmentList');
    if (!container) return;

    const linesData = [
      {
        lineIndex: 1,
        title: '第 1 句 · 02.0s – 11.5s · 句尾换气余量 1.50 拍',
        words: [
          { char: '晚', notes: 'E4 (#n_3)', noteId: 'n_3', pitch: 64 },
          { char: '风', notes: 'G4→A4 (#n_4, #n_5 转音)', noteId: 'n_4', pitch: 67 },
          { char: '吹', notes: 'G4 (#n_6)', noteId: 'n_6', pitch: 67 },
          { char: '过', notes: 'E4 (#n_7)', noteId: 'n_7', pitch: 64 },
          { char: '站', notes: 'D4 (#n_8)', noteId: 'n_8', pitch: 62 },
          { char: '台', notes: 'C4 (#n_9)', noteId: 'n_9', pitch: 60 },
          { char: '的', notes: 'D4 (#n_10)', noteId: 'n_10', pitch: 62 },
          { char: '灯', notes: 'E4 (#n_11)', noteId: 'n_11', pitch: 64 },
          { char: '光', notes: 'G4 (#n_12)', noteId: 'n_12', pitch: 67 },
        ],
      },
      {
        lineIndex: 2,
        title: '第 2 句 · 12.5s – 21.0s · 句尾换气余量 1.25 拍',
        words: [
          { char: '车', notes: 'A4', pitch: 69 },
          { char: '窗', notes: 'G4', pitch: 67 },
          { char: '倒', notes: 'E4', pitch: 64 },
          { char: '映', notes: 'G4', pitch: 67 },
          { char: '着', notes: 'E4', pitch: 64 },
          { char: '年', notes: 'D4', pitch: 62 },
          { char: '少', notes: 'C4→D4', pitch: 60 },
          { char: '的', notes: 'E4', pitch: 64 },
          { char: '模', notes: 'D4', pitch: 62 },
          { char: '样', notes: 'C4', pitch: 60 },
        ],
      },
    ];

    container.innerHTML = '';
    linesData.forEach((row, idx) => {
      const div = document.createElement('div');
      div.className = `lyric-align-row ${creationState.activeLyricLine === idx ? 'active' : ''}`;
      div.innerHTML = `
        <div class="lyric-align-head">
          <strong>${row.title}</strong>
          <span>✓ 单旋律无重叠</span>
        </div>
        <div class="word-pills-wrap"></div>
      `;
      const wrap = div.querySelector('.word-pills-wrap');
      row.words.forEach(w => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'word-pill';
        btn.innerHTML = `<b>${w.char}</b><small>${w.notes}</small>`;
        btn.addEventListener('click', () => {
          creationState.activeLyricLine = idx;
          synth.triggerNote(w.pitch, 'keyboard', 0.45 / creationState.lyricSpeed, 92);
          document.getElementById('activeWordGroupTitle').textContent =
            `第 ${row.lineIndex} 行 · 字「${w.char}」 (绑定音符: ${w.notes})`;
          if (w.noteId) {
            creationState.activeTrackId = 'trk_keyboard';
            creationState.selectedNoteId = w.noteId;
            renderMixerTracks();
            renderPianoRoll();
          }
          renderLyricAlignment();
        });
        wrap.appendChild(btn);
      });
      container.appendChild(div);
    });
  }

  function initCreationEvents() {
    renderSongSidebar();
    renderCreationKeyboard();
    updateLyricsStatus();
    renderMixerTracks();
    renderPianoRoll();
    renderLyricAlignment();

    // 创作三步 Tab 切换
    document.querySelectorAll('.step-pill').forEach(tab => {
      tab.addEventListener('click', () => {
        const step = tab.dataset.step;
        creationState.step = step;
        document.querySelectorAll('.step-pill').forEach(t => {
          const active = t === tab;
          t.classList.toggle('active', active);
          t.setAttribute('aria-selected', String(active));
        });
        document.getElementById('creation-step-inputs').hidden = step !== 'inputs';
        document.getElementById('creation-step-edit').hidden = step !== 'edit';
        document.getElementById('creation-step-export').hidden = step !== 'export';
      });
    });

    // 声部切换
    document.querySelectorAll('#creationFamilyTabs .family-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        creationState.family = btn.dataset.family;
        document.querySelectorAll('#creationFamilyTabs .family-tab').forEach(b => b.classList.toggle('active', b === btn));
        document.getElementById('activeVoiceReadout').textContent = `当前声部：${btn.textContent.trim()}`;
        renderCreationKeyboard();
      });
    });

    // 八度加减
    document.getElementById('octDownBtn')?.addEventListener('click', () => {
      creationState.basePitch = Math.max(36, creationState.basePitch - 12);
      document.getElementById('creationOctaveLabel').textContent = `基准：${midiToName(creationState.basePitch)} (MIDI ${creationState.basePitch})`;
      renderCreationKeyboard();
    });

    document.getElementById('octUpBtn')?.addEventListener('click', () => {
      creationState.basePitch = Math.min(84, creationState.basePitch + 12);
      document.getElementById('creationOctaveLabel').textContent = `基准：${midiToName(creationState.basePitch)} (MIDI ${creationState.basePitch})`;
      renderCreationKeyboard();
    });

    // 录制、回放、清空、加入编曲
    const recBtn = document.getElementById('recordToggleBtn');
    recBtn?.addEventListener('click', () => {
      creationState.recording = !creationState.recording;
      recBtn.classList.toggle('recording', creationState.recording);
      if (creationState.recording) {
        creationState.recordedNotes = [];
        creationState.recordStartStamp = performance.now();
        recBtn.textContent = '■ 停止录制';
        showToast('已开始录制：请点击屏幕琴键或按电脑键盘 A–K 演奏。');
      } else {
        recBtn.textContent = '● 开始录制片段';
        showToast(`录制完成，共捕获 ${creationState.recordedNotes.length} 个音符。`);
      }
      updateRecordedBadge();
    });

    document.getElementById('playRecordedBtn')?.addEventListener('click', () => {
      if (!creationState.recordedNotes.length) {
        showToast('当前没有已录制的音符，请先录制一小段旋律。');
        return;
      }
      synth.scheduleSequence(
        creationState.recordedNotes.map(n => ({
          pitch: n.pitch,
          family: n.family,
          time: n.start,
          duration: n.duration,
          velocity: 92,
        }))
      );
      showToast('正在回放已录制的灵感片段…');
    });

    document.getElementById('clearRecordedBtn')?.addEventListener('click', () => {
      creationState.recordedNotes = [];
      creationState.recording = false;
      recBtn?.classList.remove('recording');
      if (recBtn) recBtn.textContent = '● 开始录制片段';
      updateRecordedBadge();
    });

    document.getElementById('commitRecordingToScoreBtn')?.addEventListener('click', () => {
      if (!creationState.recordedNotes.length) {
        showToast('请先录制至少一个音符。');
        return;
      }
      const kbTrack = creationState.tracks[0];
      creationState.recordedNotes.forEach((n, i) => {
        kbTrack.notes.push({
          id: `rec_${Date.now()}_${i}`,
          pitch: n.pitch,
          start: Math.round(n.start * 2) / 2,
          duration: 0.5,
          velocity: 90,
        });
      });
      markCreationDirty(true);
      renderMixerTracks();
      renderPianoRoll();
      document.querySelector('.step-pill[data-step="edit"]')?.click();
      showToast('已将录制片段加入编曲时间轴（保留原始演奏，不做强制自动纠错）。');
    });

    // 歌词编辑与开关
    document.getElementById('lyricsTextarea')?.addEventListener('input', () => {
      markCreationDirty(true);
      updateLyricsStatus();
    });

    document.getElementById('toggleUseLyricsBtn')?.addEventListener('click', () => {
      creationState.usingLyrics = !creationState.usingLyrics;
      updateLyricsStatus();
      showToast(
        creationState.usingLyrics
          ? '已恢复使用歌词全文谱曲（lyric-motifs-v1）。'
          : '已切换为本次不使用歌词：原歌词文件仍保留在歌曲目录，但短曲生成只发送主题、情感与演奏。'
      );
    });

    document.getElementById('loadSampleLyricsBtn')?.addEventListener('click', () => {
      document.getElementById('lyricsTextarea').value =
        '晚风吹过站台的灯光\n车窗倒映着年少的模样\n挥手告别昨日的迷茫\n背上行囊奔向远方\n哪怕旅途偶有风雨阻挡\n心底依然向往那片晴朗';
      creationState.usingLyrics = true;
      updateLyricsStatus();
      showToast('已载入 6 行示例歌词。');
    });

    // AI 候选生成、试听与采用
    document.getElementById('generateAiCandidateBtn')?.addEventListener('click', () => {
      showToast('已生成四声部 AI 编曲候选 #cand_04（原型模拟演示，未消耗真实 DeepSeek Token）。');
    });

    document.getElementById('previewCandidateBtn')?.addEventListener('click', () => {
      playCurrentScore();
      showToast('正在试听四声部 AI 候选编曲（键盘 + 木吉他 + 指弹贝斯 + GM 鼓组）…');
    });

    document.getElementById('applyCandidateBtn')?.addEventListener('click', () => {
      const song = creationState.songs.find(s => s.id === creationState.activeSongId);
      if (song) song.revision += 1;
      markCreationDirty(false);
      renderSongSidebar();
      document.querySelector('.step-pill[data-step="edit"]')?.click();
      showToast('已采用候选并保存为新版本，自动进入「02 演奏编辑与歌词对应」。');
    });

    // 保存按钮
    const saveHandler = () => {
      const song = creationState.songs.find(s => s.id === creationState.activeSongId);
      if (song && song.dirty) {
        song.revision += 1;
        creationState.revisions.unshift({
          number: song.revision,
          label: '手动保存歌曲修改',
          time: '刚刚',
        });
      }
      markCreationDirty(false);
      renderSongSidebar();
      showToast(`已显式写入本地歌曲目录 data/${creationState.activeSongId}/（版本 v${song?.revision}）。`);
    };
    document.getElementById('saveSongBtn')?.addEventListener('click', saveHandler);
    document.getElementById('saveInputsBtn')?.addEventListener('click', saveHandler);

    // DAW 播放与音符修改表单
    document.getElementById('playScoreBtn')?.addEventListener('click', () => {
      playCurrentScore();
    });

    document.getElementById('stopScoreBtn')?.addEventListener('click', () => {
      synth.stopAll();
      document.getElementById('dawCurrentTime').textContent = '00:00';
    });

    document.getElementById('noteInspectorForm')?.addEventListener('submit', e => {
      e.preventDefault();
      const track = creationState.tracks.find(t => t.id === creationState.activeTrackId);
      const note = track?.notes.find(n => n.id === creationState.selectedNoteId);
      if (!note) return;
      creationState.undoStack.push(structuredClone(creationState.tracks));
      note.pitch = Math.max(0, Math.min(127, Number(document.getElementById('inspPitch').value)));
      note.start = Math.max(0, Number(document.getElementById('inspStart').value));
      note.duration = Math.max(0.25, Number(document.getElementById('inspDuration').value));
      note.velocity = Math.max(1, Math.min(127, Number(document.getElementById('inspVelocity').value)));
      markCreationDirty(true);
      renderPianoRoll();
      synth.triggerNote(note.pitch, track.family, 0.35, note.velocity);
      showToast('已更新选中音符的音高、起止拍与力度。');
    });

    document.getElementById('deleteNoteBtn')?.addEventListener('click', () => {
      const track = creationState.tracks.find(t => t.id === creationState.activeTrackId);
      if (!track || !creationState.selectedNoteId) return;
      creationState.undoStack.push(structuredClone(creationState.tracks));
      track.notes = track.notes.filter(n => n.id !== creationState.selectedNoteId);
      creationState.selectedNoteId = track.notes[0]?.id || null;
      markCreationDirty(true);
      renderMixerTracks();
      renderPianoRoll();
      showToast('已删除选中音符（可随时点击「撤销」恢复）。');
    });

    document.getElementById('scoreUndoBtn')?.addEventListener('click', () => {
      if (!creationState.undoStack.length) {
        showToast('当前没有可撤销的草稿步骤。');
        return;
      }
      creationState.redoStack.push(structuredClone(creationState.tracks));
      creationState.tracks = creationState.undoStack.pop();
      renderMixerTracks();
      renderPianoRoll();
      showToast('已撤销上一步乐谱编辑。');
    });

    document.getElementById('scoreRedoBtn')?.addEventListener('click', () => {
      if (!creationState.redoStack.length) {
        showToast('当前没有可重做的草稿步骤。');
        return;
      }
      creationState.undoStack.push(structuredClone(creationState.tracks));
      creationState.tracks = creationState.redoStack.pop();
      renderMixerTracks();
      renderPianoRoll();
      showToast('已重做乐谱编辑。');
    });

    // 慢速练唱 1.0x / 0.75x 与乐句转音 v2
    document.querySelectorAll('.speed-switch [data-speed]').forEach(btn => {
      btn.addEventListener('click', () => {
        creationState.lyricSpeed = Number(btn.dataset.speed);
        document.querySelectorAll('.speed-switch [data-speed]').forEach(b => b.classList.toggle('active', b === btn));
        showToast(`已切换练唱试听速度为 ${creationState.lyricSpeed}×（仅改变试听时间，不改变音高或导出文件）。`);
      });
    });

    document.getElementById('playCurrentLyricLineBtn')?.addEventListener('click', () => {
      const scale = 1 / creationState.lyricSpeed;
      const linePitches = [64, 67, 69, 67, 64, 62, 60, 62, 64, 67];
      synth.scheduleSequence(
        linePitches.map((p, idx) => ({
          pitch: p,
          family: 'keyboard',
          time: idx * 0.36 * scale,
          duration: 0.34 * scale,
          velocity: 92,
        }))
      );
      showToast(`正在按 ${creationState.lyricSpeed}× 速度试听当前歌词句旋律…`);
    });

    document.getElementById('addMelismaBtn')?.addEventListener('click', () => {
      document.getElementById('phraseImpactPreview').innerHTML =
        '<span>✓ 已为当前字添加平滑上行双音转音：主旋律单声部无重叠 · 行尾换气余量 1.25 拍（合格） · 一次撤销可恢复。</span>';
      synth.scheduleSequence([
        { pitch: 67, family: 'keyboard', time: 0, duration: 0.25, velocity: 92 },
        { pitch: 69, family: 'keyboard', time: 0.25, duration: 0.35, velocity: 88 },
      ]);
      markCreationDirty(true);
    });

    document.getElementById('simplifyOneNoteBtn')?.addEventListener('click', () => {
      document.getElementById('phraseImpactPreview').innerHTML =
        '<span>✓ 已简化为一字一音：合并转音尾音至主音时长 · 行尾换气余量 1.50 拍（合格）。</span>';
      synth.triggerNote(67, 'keyboard', 0.5, 92);
      markCreationDirty(true);
    });

    document.getElementById('lengthenTailBtn')?.addEventListener('click', () => {
      document.getElementById('phraseImpactPreview').innerHTML =
        '<span>✓ 句尾收束音已延长 +0.5 拍：剩余换气间隔 1.00 拍（仍满足换气阈值）。</span>';
      synth.triggerNote(67, 'keyboard', 0.75, 92);
      markCreationDirty(true);
    });

    document.getElementById('runNlModifyBtn')?.addEventListener('click', () => {
      showToast('已生成基于自然语言描述的整曲修改候选（原型演示）。');
    });

    // Step 03: 中文演唱、审核与导出
    document.querySelectorAll('.vocal-mode-pills .timbre-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.vocal-mode-pills .timbre-pill').forEach(b => b.classList.toggle('active', b === btn));
      });
    });

    document.getElementById('synthesizeVocalBtn')?.addEventListener('click', () => {
      const xing = document.getElementById('polyXingSelect')?.value || 'xing';
      document.getElementById('vocalStatusHint').textContent =
        `✓ 已按多音字修正（行 → ${xing}）完成绮萱 Qixuan v2.7.0 普通话独唱合成预览（绑定版本 v4）。`;
      showToast(`已更新多音字读音「行(${xing})」并完成中文演唱预览合成。`);
    });

    document.getElementById('playVocalDemoBtn')?.addEventListener('click', () => {
      const melody = [64, 67, 69, 67, 64, 62, 60, 62, 64, 67];
      synth.scheduleSequence(
        melody.flatMap((p, i) => [
          { pitch: p, family: 'vocal', time: i * 0.42, duration: 0.38, velocity: 95 },
          { pitch: p - 12, family: 'keyboard', time: i * 0.42, duration: 0.38, velocity: 70 },
        ])
      );
      showToast('正在试听中文演唱与钢琴伴奏合听示例（原型合成器模拟）…');
    });

    document.getElementById('approveRevisionBtn')?.addEventListener('click', () => {
      const badge = document.getElementById('reviewStatusBadge');
      if (badge) {
        badge.textContent = '✓ 已人工审核通过 (v4)';
        badge.style.background = 'var(--accent-emerald-soft)';
        badge.style.color = 'var(--accent-emerald)';
      }
      showToast('当前版本 v4 已标记为「人工试听与试唱审核通过」。');
    });

    document.getElementById('triggerFullSongBtn')?.addEventListener('click', () => {
      showToast('已发起基于已审核短曲的 3 分 15 秒整曲扩展任务（原型流程演示）。');
    });

    document.getElementById('exportMidiBtn')?.addEventListener('click', () => {
      showToast(`已生成多轨标准 MIDI：neonote-${creationState.activeSongId.slice(5, 13)}-v4.mid（演示提示）。`);
    });

    document.getElementById('renderAudioBtn')?.addEventListener('click', () => {
      playCurrentScore();
      showToast('已完成 FluidSynth + FFmpeg 音频渲染（含 +3.0s 尾音与 50ms 淡出），正在试听…');
    });

    document.getElementById('exportMp3Btn')?.addEventListener('click', () => {
      showToast(`已触发版本绑定 MP3 下载：neonote-${creationState.activeSongId.slice(5, 13)}-v4.mp3（演示提示）。`);
    });

    document.getElementById('exportVocalMixBtn')?.addEventListener('click', () => {
      showToast('已触发绮萱人声与伴奏立体声混音下载（演示提示）。');
    });
  }

  function playCurrentScore() {
    const bpm = Number(document.getElementById('scoreBpmInput')?.value || 108);
    const secPerBeat = 60 / bpm;
    const anySolo = creationState.tracks.some(t => t.solo);
    const events = [];

    creationState.tracks.forEach(trk => {
      if (trk.mute) return;
      if (anySolo && !trk.solo) return;
      trk.notes.forEach(n => {
        events.push({
          pitch: n.pitch,
          family: trk.family,
          time: n.start * secPerBeat,
          duration: n.duration * secPerBeat,
          velocity: Math.round((n.velocity * trk.volume) / 100),
          onTrigger: () => {
            const cur = document.getElementById('dawCurrentTime');
            if (cur) {
              const s = Math.floor(n.start * secPerBeat);
              cur.textContent = `00:${String(s).padStart(2, '0')}`;
            }
          },
        });
      });
    });

    synth.scheduleSequence(events, () => {
      const cur = document.getElementById('dawCurrentTime');
      if (cur) cur.textContent = '00:00';
    });
  }

  /* ========================================================================
     7. VIEW 02: 识谱与节奏练习 (5 单元 15 谱 + 五线谱旁键位说明 + 两轮闯关)
     ======================================================================== */
  const practiceCatalog = [
    {
      id: 'unit_1',
      title: '单元一：单音认谱',
      exercises: [
        { id: '1.1', name: '1.1 C4–G4 五指顺阶', goal: '熟悉 s d f g h 五个基础白键与高音谱表下加一线到第二线', notes: [60, 62, 64, 65, 67, 65, 64, 60] },
        { id: '1.2', name: '1.2 C4–C5 八度上行与跳进', goal: '掌握右侧高音 C5（按键 l）与三度跳进认谱', notes: [60, 64, 67, 72, 71, 69, 67, 60] },
        { id: '1.3', name: '1.3 全音阶打乱找音', goal: '不靠顺序记忆，直接看五线谱线间位置反应按键', notes: [64, 60, 69, 65, 72, 67, 62, 60] },
      ],
    },
    {
      id: 'unit_2',
      title: '单元二：黑键与升降号',
      exercises: [
        { id: '2.1', name: '2.1 F♯4 与 G 大调色彩', goal: '使用黑键 y 弹奏升 F，体会大调导音倾向', notes: [67, 69, 71, 66, 67, 62, 66, 67] },
        { id: '2.2', name: '2.2 C♯4 / D♯4 半音阶漫步', goal: '练习黑键 e 与 r 的准确触发', notes: [60, 61, 62, 63, 64, 62, 61, 60] },
        { id: '2.3', name: '2.3 升降号混合认谱', goal: '在 e r y u i 五个黑键与白键之间自如切换', notes: [60, 63, 67, 68, 70, 67, 63, 60] },
      ],
    },
    {
      id: 'unit_3',
      title: '单元三：基础节奏',
      exercises: [
        { id: '3.1', name: '3.1 四分与二分音符稳拍', goal: '体会一拍与两拍的时值保持', notes: [60, 60, 64, 67, 65, 64, 62, 60] },
        { id: '3.2', name: '3.2 八分音符律动组合', goal: '均匀弹奏八分音符，不抢拍不拖拍', notes: [60, 62, 64, 65, 67, 69, 67, 60] },
        { id: '3.3', name: '3.3 带休止符的呼吸感', goal: '注意休止拍不误按琴键', notes: [64, 67, 69, 67, 64, 62, 64, 60] },
      ],
    },
    {
      id: 'unit_4',
      title: '单元四：简单旋律',
      exercises: [
        { id: '4.1', name: '4.1 晨光小步舞曲', goal: '结合级进与四度跳进的 4 小节优美旋律', notes: [67, 64, 65, 67, 69, 67, 64, 60] },
        { id: '4.2', name: '4.2 同音重复起音练习', goal: '清晰释放再重新按下同一琴键', notes: [64, 64, 65, 67, 67, 65, 64, 62] },
        { id: '4.3', name: '4.3 跨八度呼应乐句', goal: '运用 Shift/空格切换八度或使用 l 键衔接高音', notes: [60, 67, 64, 72, 69, 67, 64, 60] },
      ],
    },
    {
      id: 'unit_5',
      title: '单元五：和弦与延音',
      exercises: [
        { id: '5.1', name: '5.1 三度与五度双音', goal: '同时按下两个键演奏和声音程', notes: [60, 64, 62, 65, 64, 67, 69, 72] },
        { id: '5.2', name: '5.2 C 大三和弦分解与柱式', goal: '体会 s + f + h 同时按下的饱满和声', notes: [60, 64, 67, 72, 67, 64, 62, 60] },
        { id: '5.3', name: '5.3 按住修饰键叠加八度副音', goal: '体验 Shift/空格 + 音符键同时触发高低八度双音', notes: [60, 62, 64, 67, 69, 71, 72, 60] },
      ],
    },
  ];

  const practiceState = {
    unitIdx: 0,
    exIdx: 1,
    baseOctave: 4, // 3..5, 默认 4 (C4=60)
    round: 1, // 1: 认音轮, 2: 节奏轮
    targetIndex: 0,
    firstTryMistaken: new Set(),
    firstCorrectCount: 0,
    correctionCount: 0,
    lastWrongIndex: -1,
    shiftHeld: false,
    spaceHeld: false,
    modifierUsedForNote: false,
    sessions: [
      { unit: '单元一 · 1.1 C4–G4 五指顺阶', status: '节奏达标', firstAcc: '100.0%', offset: '平均 +11ms' },
      { unit: '单元二 · 2.1 F♯4 与 G 大调色彩', status: '认音完成', firstAcc: '87.5%', offset: '待完成节奏轮' },
    ],
  };

  function getActiveExercise() {
    return practiceCatalog[practiceState.unitIdx].exercises[practiceState.exIdx];
  }

  function resetPracticeExercise() {
    practiceState.targetIndex = 0;
    practiceState.firstTryMistaken.clear();
    practiceState.firstCorrectCount = 0;
    practiceState.correctionCount = 0;
    practiceState.lastWrongIndex = -1;
    renderPracticeCurriculum();
    renderPracticeScoreSvg();
    updatePracticeFeedbackUI();
  }

  function renderPracticeCurriculum() {
    const unitTabs = document.getElementById('practiceUnitTabs');
    const exRow = document.getElementById('practiceExerciseCards');
    if (!unitTabs || !exRow) return;

    unitTabs.innerHTML = '';
    practiceCatalog.forEach((u, idx) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `unit-tab-btn ${idx === practiceState.unitIdx ? 'active' : ''}`;
      btn.textContent = u.title;
      btn.addEventListener('click', () => {
        practiceState.unitIdx = idx;
        practiceState.exIdx = 0;
        resetPracticeExercise();
      });
      unitTabs.appendChild(btn);
    });

    const unit = practiceCatalog[practiceState.unitIdx];
    exRow.innerHTML = '';
    unit.exercises.forEach((ex, idx) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = `exercise-item-card ${idx === practiceState.exIdx ? 'active' : ''}`;
      card.innerHTML = `
        <strong>${ex.name}</strong>
        <small>${ex.goal}</small>
      `;
      card.addEventListener('click', () => {
        practiceState.exIdx = idx;
        resetPracticeExercise();
      });
      exRow.appendChild(card);
    });

    const ex = getActiveExercise();
    document.getElementById('activePracticeUnitTag').textContent = `${unit.title} · 练习 ${ex.id}`;
    document.getElementById('activePracticeTitle').textContent = `${ex.name}（4/4 拍 · 高音谱表）`;
  }

  // 绘制高音谱表五线谱 SVG 并高亮当前目标与已完成音符
  function renderPracticeScoreSvg() {
    const svg = document.getElementById('practiceScoreSvg');
    if (!svg) return;
    const ex = getActiveExercise();
    const notes = ex.notes;

    // 高音谱表底线 E4 = y:150, 每级音高阶梯 9px
    const diatonicStepMap = {
      60: 0, // C4 (下加一线)
      61: 0,
      62: 1, // D4
      63: 1,
      64: 2, // E4 (第一线)
      65: 3, // F4
      66: 3,
      67: 4, // G4 (第二线)
      68: 4,
      69: 5, // A4
      70: 5,
      71: 6, // B4 (第三线)
      72: 7, // C5 (第三间)
    };

    const staffTop = 62;
    const lineSpacing = 18;
    const e4Y = staffTop + 4 * lineSpacing; // 134

    let html = '';
    // 五条谱线
    for (let i = 0; i < 5; i++) {
      const y = staffTop + i * lineSpacing;
      html += `<line x1="40" y1="${y}" x2="820" y2="${y}" stroke="#3a332c" stroke-width="1.4" />`;
    }

    // 小节线
    [40, 440, 820].forEach(x => {
      html += `<line x1="${x}" y1="${staffTop}" x2="${x}" y2="${e4Y}" stroke="#231f1c" stroke-width="2" />`;
    });

    // 高音谱号与 4/4 拍号
    html += `<text x="52" y="${e4Y - 12}" font-size="64" font-family="serif" fill="#231f1c">𝄞</text>`;
    html += `<text x="102" y="${staffTop + 34}" font-size="24" font-weight="700" font-family="serif" fill="#231f1c">4</text>`;
    html += `<text x="102" y="${staffTop + 68}" font-size="24" font-weight="700" font-family="serif" fill="#231f1c">4</text>`;

    // 渲染 8 个目标音符
    notes.forEach((pitch, idx) => {
      const x = 165 + idx * 80 + (idx >= 4 ? 22 : 0);
      const step = diatonicStepMap[pitch] ?? 2;
      const y = e4Y - (step - 2) * (lineSpacing / 2);
      const isSharp = [1, 3, 6, 8, 10].includes(pitch % 12);

      let color = '#231f1c';
      let ring = '';
      if (idx < practiceState.targetIndex) {
        color = '#286653'; // 已正确完成
      } else if (idx === practiceState.targetIndex) {
        color = practiceState.lastWrongIndex === idx ? '#b53d2e' : '#c85a32';
        ring = `<circle cx="${x}" cy="${y}" r="18" fill="${color}" fill-opacity="0.14" stroke="${color}" stroke-dasharray="3 2" />`;
      }

      // 下加一线 (C4)
      if (pitch <= 61) {
        html += `<line x1="${x - 16}" y1="${e4Y + lineSpacing}" x2="${x + 16}" y2="${e4Y + lineSpacing}" stroke="#231f1c" stroke-width="1.6" />`;
      }

      // 升号
      if (isSharp) {
        html += `<text x="${x - 24}" y="${y + 6}" font-size="18" font-weight="700" fill="${color}">♯</text>`;
      }

      // 符头与符干
      html += `
        <g style="cursor:pointer" data-pnote-idx="${idx}">
          ${ring}
          <ellipse cx="${x}" cy="${y}" rx="9.5" ry="7" transform="rotate(-18 ${x} ${y})" fill="${color}" />
          <line x1="${x + 8.5}" y1="${y}" x2="${x + 8.5}" y2="${y - 44}" stroke="${color}" stroke-width="2" />
          <text x="${x - 10}" y="${e4Y + 46}" font-size="11" font-family="JetBrains Mono, monospace" font-weight="600" fill="${color}">
            ${midiToName(pitch)}
          </text>
        </g>
      `;
    });

    svg.innerHTML = html;

    svg.querySelectorAll('[data-pnote-idx]').forEach(g => {
      g.addEventListener('click', () => {
        const i = Number(g.getAttribute('data-pnote-idx'));
        synth.triggerNote(notes[i], 'keyboard', 0.45, 92);
      });
    });
  }

  function updatePracticeFeedbackUI() {
    const ex = getActiveExercise();
    const total = ex.notes.length;
    const idx = practiceState.targetIndex;
    const targetBadge = document.getElementById('practiceTargetNoteBadge');
    const firstAccBadge = document.getElementById('practiceFirstAccBadge');
    const corrBadge = document.getElementById('practiceCorrectionCountBadge');
    const statText = document.getElementById('recognitionStatsText');

    const answered = Math.max(0, idx);
    const accPct = answered > 0 ? ((practiceState.firstCorrectCount / answered) * 100).toFixed(1) + '%' : '100.0%';

    if (firstAccBadge) firstAccBadge.textContent = `首次准确率：${accPct}`;
    if (corrBadge) corrBadge.textContent = `纠正次数：${practiceState.correctionCount}`;

    if (idx >= total) {
      if (targetBadge) {
        targetBadge.textContent = '🎉 本条谱面全部目标音已正确完成！已解锁第二轮：节奏跟弹';
      }
      if (statText) {
        statText.textContent = `已完成 ${total}/${total} 个起音 · 首次准确率：${accPct}（保留首次作答与纠正记录）`;
      }
      document.getElementById('roundIndicator1')?.classList.remove('active');
      document.getElementById('roundIndicator2')?.classList.add('active');
      return;
    }

    const targetPitch = ex.notes[idx];
    const keyDef = PRACTICE_KEYS.find(k => (practiceState.baseOctave + 1) * 12 + k.offset === targetPitch);
    const keyHint = keyDef ? `按键 ${keyDef.label.toUpperCase()}` : '切换八度后弹奏';
    if (targetBadge) {
      targetBadge.textContent = `第 ${idx + 1}/${total} 音：${midiToName(targetPitch)}（请按电脑 ${keyHint} 或点击下方琴键）`;
    }
    if (statText) {
      statText.textContent = `当前目标：第 ${idx + 1} / ${total} 个起音 · 首次准确率：${accPct}`;
    }
  }

  function handlePracticeNoteInput(pitch, keyLabel) {
    // 若同时按住 Shift 和 空格，根据规则拒绝新音
    if (practiceState.shiftHeld && practiceState.spaceHeld) {
      showToast('防冲突保护：同时按住 Shift 与空格时拒绝触发新音。');
      return;
    }

    const pitchesToPlay = [pitch];
    if (practiceState.shiftHeld) {
      pitchesToPlay.push(pitch - 12);
      practiceState.modifierUsedForNote = true;
    } else if (practiceState.spaceHeld) {
      pitchesToPlay.push(pitch + 12);
      practiceState.modifierUsedForNote = true;
    }

    pitchesToPlay.forEach(p => synth.triggerNote(p, 'keyboard', 0.42, 92));

    const ex = getActiveExercise();
    if (practiceState.targetIndex >= ex.notes.length) return;

    const expected = ex.notes[practiceState.targetIndex];
    if (pitchesToPlay.includes(expected)) {
      if (!practiceState.firstTryMistaken.has(practiceState.targetIndex)) {
        practiceState.firstCorrectCount += 1;
      } else {
        practiceState.correctionCount += 1;
      }
      practiceState.lastWrongIndex = -1;
      practiceState.targetIndex += 1;
      renderPracticeScoreSvg();
      updatePracticeFeedbackUI();

      if (practiceState.targetIndex === ex.notes.length) {
        const finalAcc = ((practiceState.firstCorrectCount / ex.notes.length) * 100).toFixed(1) + '%';
        practiceState.sessions.unshift({
          unit: `${practiceCatalog[practiceState.unitIdx].title.split('：')[0]} · ${ex.name}`,
          status: '认音完成',
          firstAcc: finalAcc,
          offset: `纠正 ${practiceState.correctionCount} 次`,
        });
        renderPracticeRecords();
        showToast(`恭喜完成认音轮！首次准确率 ${finalAcc}，现可直接启动「第二轮：节奏跟弹」。`);
      }
    } else {
      // 答错记录首次失误，允许原地纠正
      practiceState.firstTryMistaken.add(practiceState.targetIndex);
      practiceState.lastWrongIndex = practiceState.targetIndex;
      renderPracticeScoreSvg();
      const targetBadge = document.getElementById('practiceTargetNoteBadge');
      if (targetBadge) {
        targetBadge.textContent = `刚刚按了 ${midiToName(pitch)} (${keyLabel})，目标是 ${midiToName(expected)}，请直接按正确琴键纠正！`;
      }
    }
  }

  function renderPracticeKeyboard() {
    const keybed = document.getElementById('practicePianoKeybed');
    if (!keybed) return;
    const baseMidi = (practiceState.baseOctave + 1) * 12; // 4 -> 60 (C4)
    renderPianoKeybed(keybed, PRACTICE_KEYS, baseMidi, false, (pitch, keyMeta) => {
      handlePracticeNoteInput(pitch, keyMeta.label);
    });
    document.getElementById('practiceBaseOctaveText').textContent =
      `第 ${practiceState.baseOctave} 八度 (独立范围 ${midiToName(baseMidi)}–${midiToName(baseMidi + 12)})`;
    ['pracOct3Btn', 'pracOct4Btn', 'pracOct5Btn'].forEach((id, idx) => {
      document.getElementById(id)?.classList.toggle('active', practiceState.baseOctave === idx + 3);
    });
  }

  function renderPracticeRecords() {
    const list = document.getElementById('practiceRecordsList');
    if (!list) return;
    list.innerHTML = practiceState.sessions
      .slice(0, 4)
      .map(
        s => `
        <div class="record-mini-item">
          <div>
            <strong>${s.unit}</strong>
            <div class="muted">首次准确率 ${s.firstAcc} · ${s.offset}</div>
          </div>
          <span class="status-pill emerald">${s.status}</span>
        </div>
      `
      )
      .join('');
  }

  function initPracticeEvents() {
    renderPracticeCurriculum();
    renderPracticeScoreSvg();
    renderPracticeKeyboard();
    updatePracticeFeedbackUI();
    renderPracticeRecords();

    document.getElementById('listenPracticeScoreBtn')?.addEventListener('click', () => {
      const ex = getActiveExercise();
      synth.scheduleSequence(
        ex.notes.map((p, i) => ({
          pitch: p,
          family: 'keyboard',
          time: i * 0.42,
          duration: 0.38,
          velocity: 92,
        }))
      );
      showToast(`正在试听「${ex.name}」标准示范旋律…`);
    });

    document.getElementById('resetPracticeRoundBtn')?.addEventListener('click', () => {
      practiceState.round = 1;
      document.getElementById('roundIndicator1')?.classList.add('active');
      document.getElementById('roundIndicator2')?.classList.remove('active');
      resetPracticeExercise();
      showToast('已重置本条练习，从第 1 个目标音重新开始认音。');
    });

    document.getElementById('switchRoundModeBtn')?.addEventListener('click', () => {
      practiceState.round = practiceState.round === 1 ? 2 : 1;
      document.getElementById('roundIndicator1')?.classList.toggle('active', practiceState.round === 1);
      document.getElementById('roundIndicator2')?.classList.toggle('active', practiceState.round === 2);
      showToast(practiceState.round === 2 ? '已切换到第二轮：节奏跟弹模式。' : '已返回第一轮：认音模式。');
    });

    // 手动八度按钮
    [3, 4, 5].forEach(oct => {
      document.getElementById(`pracOct${oct}Btn`)?.addEventListener('click', () => {
        practiceState.baseOctave = oct;
        renderPracticeKeyboard();
        updatePracticeFeedbackUI();
      });
    });

    // 节奏轮速度与预备拍启动
    document.querySelectorAll('[data-rspeed]').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('[data-rspeed]').forEach(b => b.classList.toggle('active', b === btn));
      });
    });

    document.getElementById('startRhythmRoundBtn')?.addEventListener('click', () => {
      const metro = document.getElementById('metronomeVisual');
      const timingBadge = document.getElementById('practiceTimingOffsetBadge');
      practiceState.targetIndex = 0;
      renderPracticeScoreSvg();
      const beats = ['● ○ ○ ○', '● ● ○ ○', '● ● ● ○', '● ● ● ● (开始跟弹!)'];
      beats.forEach((text, i) => {
        setTimeout(() => {
          if (metro) metro.textContent = `预备拍：${text}`;
          synth.triggerNote(i === 3 ? 76 : 72, 'drums', 0.1, 85);
          if (i === 3 && timingBadge) {
            timingBadge.textContent = '节奏轮进行中：请跟随节拍按 s–l 弹奏（实时判定准拍 ±25ms）';
          }
        }, i * 500);
      });
    });

    document.getElementById('importPracticeMidiBtn')?.addEventListener('click', () => {
      showToast('支持上传自选 MIDI 并选择非鼓轨道与 1–16 小节范围（当前原型使用内置 15 条标准单元演示）。');
    });

    document.getElementById('copyFromSongToPracticeBtn')?.addEventListener('click', () => {
      showToast('已从创作歌曲「周末的落日列车 (v4)」复制独立练习材料副本到 data/.practice/materials/。');
    });
  }

  /* ========================================================================
     8. VIEW 03: 乐曲键盘扒谱 (选段波形、混音分离确认门禁、音符表、32声部大谱表)
     ======================================================================== */
  const transState = {
    projects: [
      { id: 'tr_canon_01', name: 'Canon_in_D_Pachelbel.mp3', spec: '44100 Hz · 立体声 · 总长 184.20s', duration: 60 },
      { id: 'tr_nocturne_02', name: '夜曲即兴钢琴独奏录音.wav', spec: '48000 Hz · 双声道 PCM · 总长 92.50s', duration: 60 },
    ],
    activeProjectId: 'tr_canon_01',
    selectedNoteId: 'tn_1',
    notes: [
      { id: 'tn_1', pitch: 66, onset: 0.25, offset: 1.15, velocity: 88, hand: 'right' },
      { id: 'tn_2', pitch: 64, onset: 1.20, offset: 2.10, velocity: 85, hand: 'right' },
      { id: 'tn_3', pitch: 62, onset: 2.15, offset: 3.05, velocity: 86, hand: 'right' },
      { id: 'tn_4', pitch: 61, onset: 3.10, offset: 4.00, velocity: 82, hand: 'right' },
      { id: 'tn_5', pitch: 59, onset: 4.05, offset: 4.95, velocity: 80, hand: 'right' },
      { id: 'tn_6', pitch: 57, onset: 5.00, offset: 5.90, velocity: 82, hand: 'right' },
      { id: 'tn_7', pitch: 59, onset: 5.95, offset: 6.85, velocity: 85, hand: 'right' },
      { id: 'tn_8', pitch: 61, onset: 6.90, offset: 7.80, velocity: 88, hand: 'right' },
      { id: 'tn_9', pitch: 50, onset: 0.25, offset: 1.15, velocity: 78, hand: 'left' },
      { id: 'tn_10', pitch: 45, onset: 1.20, offset: 2.10, velocity: 76, hand: 'left' },
      { id: 'tn_11', pitch: 47, onset: 2.15, offset: 3.05, velocity: 77, hand: 'left' },
      { id: 'tn_12', pitch: 42, onset: 3.10, offset: 4.00, velocity: 75, hand: 'left' },
    ],
    history: [],
  };

  function renderTransSidebar() {
    const container = document.getElementById('transProjectList');
    if (!container) return;
    container.innerHTML = '';
    transState.projects.forEach(p => {
      const card = document.createElement('div');
      card.className = `song-row-card ${p.id === transState.activeProjectId ? 'active' : ''}`;
      card.innerHTML = `
        <button type="button" class="song-row-main">
          <strong>💿 ${p.name}</strong>
          <small>data/.transcriptions/${p.id}</small>
        </button>
      `;
      card.querySelector('button').addEventListener('click', () => {
        transState.activeProjectId = p.id;
        document.getElementById('activeTransProjectName').textContent = p.name;
        document.getElementById('activeTransSpecBadge').textContent = p.spec;
        renderTransSidebar();
        showToast(`已打开扒谱工程「${p.name}」`);
      });
      container.appendChild(card);
    });
  }

  function renderTransWaveform() {
    const barsBox = document.getElementById('transWaveformBars');
    if (!barsBox) return;
    const heights = [
      24, 38, 52, 68, 45, 76, 84, 60, 42, 72, 90, 64, 50, 78, 86, 55,
      40, 62, 74, 88, 66, 48, 70, 82, 58, 44, 65, 79, 52, 36, 60, 75,
      48, 66, 80, 54, 42, 68, 72, 46, 35, 58, 64, 40, 30, 50, 62, 38,
    ];
    barsBox.innerHTML = heights.map(h => `<span style="height:${h}%"></span>`).join('');
  }

  function updateTransClipOverlay() {
    const s = Math.max(0, Number(document.getElementById('transStartSec')?.value || 0));
    const e = Math.max(s + 0.5, Math.min(60, Number(document.getElementById('transEndSec')?.value || 20)));
    const sel = document.getElementById('transWaveformSelection');
    if (sel) {
      const leftPct = (s / 60) * 100;
      const widthPct = ((e - s) / 60) * 100;
      sel.style.left = `${leftPct}%`;
      sel.style.width = `${widthPct}%`;
      sel.querySelector('span').textContent = `选段 ${s.toFixed(2)}s – ${e.toFixed(2)}s (共 ${(e - s).toFixed(2)}s)`;
    }
  }

  function renderTransNoteTable() {
    const tbody = document.getElementById('transNoteTableBody');
    if (!tbody) return;
    tbody.innerHTML = '';

    transState.notes.forEach(n => {
      const tr = document.createElement('tr');
      if (n.id === transState.selectedNoteId) tr.classList.add('selected');
      tr.innerHTML = `
        <td><strong>${midiToName(n.pitch)}</strong> <input type="number" min="21" max="108" value="${n.pitch}" data-field="pitch" /></td>
        <td><input type="number" step="0.05" value="${n.onset.toFixed(2)}" data-field="onset" /></td>
        <td><input type="number" step="0.05" value="${n.offset.toFixed(2)}" data-field="offset" /></td>
        <td><input type="number" min="1" max="127" value="${n.velocity}" data-field="velocity" /></td>
        <td>
          <select data-field="hand">
            <option value="right" ${n.hand === 'right' ? 'selected' : ''}>右手 (高音谱表)</option>
            <option value="left" ${n.hand === 'left' ? 'selected' : ''}>左手 (低音谱表)</option>
            <option value="auto" ${n.hand === 'auto' ? 'selected' : ''}>自动建议</option>
          </select>
        </td>
        <td>
          <button type="button" class="btn-xs" data-act="play">🔊 定位试听</button>
          <button type="button" class="btn-xs" data-act="del">删除</button>
        </td>
      `;

      tr.querySelectorAll('input, select').forEach(input => {
        input.addEventListener('change', () => {
          transState.history.push(structuredClone(transState.notes));
          const field = input.dataset.field;
          n[field] = field === 'hand' ? input.value : Number(input.value);
          renderTransNoteTable();
          renderTransGrandStaff();
        });
      });

      tr.querySelector('[data-act="play"]').addEventListener('click', () => {
        transState.selectedNoteId = n.id;
        synth.triggerNote(n.pitch, 'keyboard', Math.max(0.25, n.offset - n.onset), n.velocity);
        renderTransNoteTable();
        renderTransGrandStaff();
      });

      tr.querySelector('[data-act="del"]').addEventListener('click', () => {
        transState.history.push(structuredClone(transState.notes));
        transState.notes = transState.notes.filter(item => item.id !== n.id);
        renderTransNoteTable();
        renderTransGrandStaff();
      });

      tbody.appendChild(tr);
    });
  }

  // 绘制扒谱整理后的高低音大谱表 (Grand Staff)
  function renderTransGrandStaff() {
    const svg = document.getElementById('transGrandStaffSvg');
    if (!svg) return;

    let html = '';
    const trebleTop = 36;
    const bassTop = 142;
    const sp = 14;

    // 括线与起止连接线
    html += `<line x1="36" y1="${trebleTop}" x2="36" y2="${bassTop + 4 * sp}" stroke="#231f1c" stroke-width="2.5" />`;
    [400, 790].forEach(x => {
      html += `<line x1="${x}" y1="${trebleTop}" x2="${x}" y2="${bassTop + 4 * sp}" stroke="#231f1c" stroke-width="1.6" />`;
    });

    // 高音与低音五条谱线
    for (let i = 0; i < 5; i++) {
      html += `<line x1="36" y1="${trebleTop + i * sp}" x2="790" y2="${trebleTop + i * sp}" stroke="#3a332c" stroke-width="1.2" />`;
      html += `<line x1="36" y1="${bassTop + i * sp}" x2="790" y2="${bassTop + i * sp}" stroke="#3a332c" stroke-width="1.2" />`;
    }

    // 谱号、D大调两个升号、4/4拍号
    html += `<text x="44" y="${trebleTop + 48}" font-size="50" font-family="serif" fill="#231f1c">𝄞</text>`;
    html += `<text x="44" y="${bassTop + 42}" font-size="40" font-family="serif" fill="#231f1c">𝄢</text>`;
    html += `<text x="84" y="${trebleTop + 14}" font-size="16" font-weight="700" fill="#231f1c">♯</text>`;
    html += `<text x="94" y="${trebleTop + 34}" font-size="16" font-weight="700" fill="#231f1c">♯</text>`;
    html += `<text x="114" y="${trebleTop + 26}" font-size="20" font-weight="700" font-family="serif" fill="#231f1c">4</text>`;
    html += `<text x="114" y="${trebleTop + 52}" font-size="20" font-weight="700" font-family="serif" fill="#231f1c">4</text>`;

    const rightNotes = transState.notes.filter(n => n.hand !== 'left');
    const leftNotes = transState.notes.filter(n => n.hand === 'left');

    rightNotes.forEach((n, idx) => {
      const x = 158 + idx * 76 + (idx >= 4 ? 18 : 0);
      const y = trebleTop + 4 * sp - (n.pitch - 64) * 4.2;
      const sel = n.id === transState.selectedNoteId;
      const color = sel ? '#c85a32' : '#231f1c';
      html += `
        <g style="cursor:pointer" data-tnote-id="${n.id}">
          <ellipse cx="${x}" cy="${y}" rx="8" ry="6" transform="rotate(-18 ${x} ${y})" fill="${color}" />
          <line x1="${x + 7}" y1="${y}" x2="${x + 7}" y2="${y - 34}" stroke="${color}" stroke-width="1.8" />
          <text x="${x - 10}" y="${trebleTop - 8}" font-size="10" font-family="JetBrains Mono, monospace" fill="${color}">${midiToName(n.pitch)}</text>
        </g>
      `;
    });

    leftNotes.forEach((n, idx) => {
      const x = 158 + idx * 145;
      const y = bassTop + 2 * sp - (n.pitch - 50) * 4.2;
      const sel = n.id === transState.selectedNoteId;
      const color = sel ? '#c85a32' : '#286653';
      html += `
        <g style="cursor:pointer" data-tnote-id="${n.id}">
          <ellipse cx="${x}" cy="${y}" rx="8" ry="6" transform="rotate(-18 ${x} ${y})" fill="${color}" />
          <line x1="${x - 7}" y1="${y}" x2="${x - 7}" y2="${y + 34}" stroke="${color}" stroke-width="1.8" />
          <text x="${x - 10}" y="${bassTop + 4 * sp + 18}" font-size="10" font-family="JetBrains Mono, monospace" fill="${color}">${midiToName(n.pitch)}</text>
        </g>
      `;
    });

    svg.innerHTML = html;

    svg.querySelectorAll('[data-tnote-id]').forEach(g => {
      g.addEventListener('click', () => {
        const id = g.getAttribute('data-tnote-id');
        transState.selectedNoteId = id;
        const target = transState.notes.find(n => n.id === id);
        if (target) synth.triggerNote(target.pitch, 'keyboard', 0.45, target.velocity);
        renderTransNoteTable();
        renderTransGrandStaff();
      });
    });
  }

  function initTranscriptionEvents() {
    renderTransSidebar();
    renderTransWaveform();
    updateTransClipOverlay();
    renderTransNoteTable();
    renderTransGrandStaff();

    document.getElementById('transStartSec')?.addEventListener('input', updateTransClipOverlay);
    document.getElementById('transEndSec')?.addEventListener('input', updateTransClipOverlay);

    const playCanonClip = () => {
      const events = transState.notes.map(n => ({
        pitch: n.pitch,
        family: 'keyboard',
        time: n.onset * 0.45,
        duration: Math.max(0.25, (n.offset - n.onset) * 0.45),
        velocity: n.velocity,
      }));
      synth.scheduleSequence(events);
    };

    document.getElementById('playTransClipBtn')?.addEventListener('click', () => {
      playCanonClip();
      showToast('正在试听所选片段（0.00s – 20.00s Canon in D 钢琴声部）…');
    });

    document.getElementById('playTransCompareBtn')?.addEventListener('click', () => {
      playCanonClip();
      showToast('正在从当前定位点同步试听对照音源…');
    });

    document.querySelectorAll('#transComparePills .timbre-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('#transComparePills .timbre-pill').forEach(b => b.classList.toggle('active', b === btn));
      });
    });

    document.getElementById('loadSeparatedAudioBtn')?.addEventListener('click', () => {
      document.querySelectorAll('#transComparePills .timbre-pill').forEach(b => {
        b.classList.toggle('active', b.dataset.akind === 'piano');
      });
      playCanonClip();
      showToast('已切换至「分离钢琴 (Demucs htdemucs_6s)」试听，请检查是否有明显贝斯串音。');
    });

    document.getElementById('confirmContinueTransBtn')?.addEventListener('click', () => {
      document.getElementById('transJobStageBadge').textContent = '状态：已确认分离 · 转录完成';
      showToast('已确认分离钢琴质量，继续执行 Transkun 2.0.1 音符转录并生成选段求交报告。');
    });

    document.getElementById('runTranscriptionJobBtn')?.addEventListener('click', () => {
      const mode = document.getElementById('transInputModeSelect')?.value;
      if (mode === 'mix') {
        document.getElementById('transJobStageBadge').textContent = '状态：等待分离试听确认';
        showToast('混音已分离完成！请先在右侧点击「1. 载入这次分离钢琴试听」确认后再继续转录。');
      } else {
        document.getElementById('transJobStageBadge').textContent = '状态：处理完成';
        showToast('已完成独奏钢琴转录并执行选段边界求交处理。');
      }
    });

    document.getElementById('addTransNoteBtn')?.addEventListener('click', () => {
      transState.history.push(structuredClone(transState.notes));
      const newId = `tn_${Date.now().toString(36)}`;
      transState.notes.push({
        id: newId,
        pitch: 64,
        onset: 8.0,
        offset: 8.8,
        velocity: 85,
        hand: 'right',
      });
      transState.selectedNoteId = newId;
      renderTransNoteTable();
      renderTransGrandStaff();
      showToast('已在校对表中新增音符 E4。');
    });

    document.getElementById('undoTransNoteBtn')?.addEventListener('click', () => {
      if (!transState.history.length) {
        showToast('当前没有可撤销的校对修改。');
        return;
      }
      transState.notes = transState.history.pop();
      renderTransNoteTable();
      renderTransGrandStaff();
      showToast('已撤销上一步扒谱音符校对。');
    });

    const saveTransHandler = () => {
      const bpm = document.getElementById('transBpmInput')?.value || 72;
      const meter = document.getElementById('transMeterSelect')?.value || '4/4';
      document.getElementById('transSaveBadge').textContent = `当前修订：rev_03 (${bpm} BPM · ${meter})`;
      renderTransGrandStaff();
      showToast('已保存不可变校对修订并更新高低音大谱表（支持 v2 动态每手最多 32 声部）。');
    };

    document.getElementById('saveTransRevisionBtn')?.addEventListener('click', saveTransHandler);
    document.getElementById('rebuildTransScoreBtn')?.addEventListener('click', saveTransHandler);

    document.getElementById('dlRawPerformanceMidiBtn')?.addEventListener('click', () => {
      showToast('已触发原始转录 MIDI 下载：performance.mid（含完整模型补帧与踏板控制事件）。');
    });
    document.getElementById('dlClipMidiBtn')?.addEventListener('click', () => {
      showToast('已触发选段 MIDI 下载：clip.mid（0.00s–20.00s 边界截断求交后的片段 MIDI）。');
    });
    document.getElementById('dlTransScoreMidiBtn')?.addEventListener('click', () => {
      showToast('已触发整理后标准五线谱 MIDI 下载：score.mid。');
    });
    document.getElementById('dlTransMusicXmlBtn')?.addEventListener('click', () => {
      showToast('已触发多声部 MusicXML 下载：score.musicxml（可用 MuseScore / OSMD 打开）。');
    });
  }

  /* ========================================================================
     9. 弹窗管理 (新建歌曲 / 历史版本 / 回收站)
     ======================================================================== */
  function initModals() {
    document.getElementById('newSongBtn')?.addEventListener('click', () => {
      document.getElementById('newSongNameInput').value = '';
      document.getElementById('newSongModal')?.showModal();
    });

    document.getElementById('newSongForm')?.addEventListener('submit', e => {
      e.preventDefault();
      const name = document.getElementById('newSongNameInput').value.trim();
      if (!name) return;
      const newId = `song_${Math.random().toString(16).slice(2, 10)}`;
      creationState.songs.unshift({
        id: newId,
        name,
        revision: 1,
        bpm: 108,
        dirty: false,
        theme: '',
        emotion: '',
      });
      creationState.activeSongId = newId;
      document.getElementById('activeSongTitle').textContent = name;
      document.getElementById('activeSongIdPill').textContent = `data/${newId}`;
      document.getElementById('newSongModal')?.close();
      renderSongSidebar();
      showToast(`已创建新歌曲「${name}」（独立目录 data/${newId}/）。`);
    });

    document.getElementById('openHistoryModalBtn')?.addEventListener('click', () => {
      const list = document.getElementById('historyModalList');
      if (list) {
        list.innerHTML = creationState.revisions
          .map(
            r => `
            <div class="modal-row">
              <div>
                <strong>v${r.number} · ${r.label}</strong>
                <div class="muted">${r.time}</div>
              </div>
              <button type="button" class="btn-xs" data-restore-ver="${r.number}">恢复为此版本</button>
            </div>
          `
          )
          .join('');
        list.querySelectorAll('[data-restore-ver]').forEach(btn => {
          btn.addEventListener('click', () => {
            const ver = btn.getAttribute('data-restore-ver');
            document.getElementById('historyModal')?.close();
            showToast(`已从历史版本 v${ver} 恢复并创建新栈顶版本。`);
          });
        });
      }
      document.getElementById('historyModal')?.showModal();
    });

    document.getElementById('openTrashModalBtn')?.addEventListener('click', () => {
      const list = document.getElementById('trashModalList');
      if (list) {
        if (!creationState.trashedSongs.length) {
          list.innerHTML = '<p class="muted">回收站当前为空。</p>';
        } else {
          list.innerHTML = creationState.trashedSongs
            .map(
              s => `
              <div class="modal-row">
                <div>
                  <strong>${s.name}</strong>
                  <div class="muted">data/.trash/${s.id} · v${s.revision}</div>
                </div>
                <button type="button" class="btn-xs btn-primary" data-restore-song="${s.id}">完整恢复工程</button>
              </div>
            `
            )
            .join('');
          list.querySelectorAll('[data-restore-song]').forEach(btn => {
            btn.addEventListener('click', () => {
              const sid = btn.getAttribute('data-restore-song');
              const target = creationState.trashedSongs.find(item => item.id === sid);
              if (target) {
                creationState.trashedSongs = creationState.trashedSongs.filter(item => item.id !== sid);
                creationState.songs.push(target);
                renderSongSidebar();
                document.getElementById('trashModal')?.close();
                showToast(`已从 data/.trash/${sid} 完整恢复歌曲「${target.name}」。`);
              }
            });
          });
        }
      }
      document.getElementById('trashModal')?.showModal();
    });

    document.querySelectorAll('[data-close-modal]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-close-modal');
        document.getElementById(id)?.close();
      });
    });
  }

  /* ========================================================================
     10. 全局电脑键盘监听 (严格区分创作模式键位 vs 练习模式键位)
     ======================================================================== */
  function initGlobalKeyboardShortcuts() {
    window.addEventListener('keydown', e => {
      const target = e.target;
      if (
        e.ctrlKey ||
        e.altKey ||
        e.metaKey ||
        target.isContentEditable ||
        /INPUT|TEXTAREA|SELECT/.test(target.tagName) ||
        document.querySelector('dialog[open]')
      ) {
        return;
      }

      // 练习模式键盘捕获 (s-l 白键, e r y u i 黑键, Shift / Space 八度规则)
      if (currentMode === 'practice') {
        if (e.key === 'Shift') {
          if (!practiceState.shiftHeld) {
            practiceState.shiftHeld = true;
            practiceState.modifierUsedForNote = false;
            updatePracticeModifierUI();
          }
          return;
        }
        if (e.code === 'Space') {
          e.preventDefault();
          if (!practiceState.spaceHeld) {
            practiceState.spaceHeld = true;
            practiceState.modifierUsedForNote = false;
            updatePracticeModifierUI();
          }
          return;
        }
        if (e.repeat) return;

        const keyMeta = PRACTICE_KEYS.find(k => k.code === e.code);
        if (keyMeta) {
          e.preventDefault();
          const baseMidi = (practiceState.baseOctave + 1) * 12;
          const pitch = baseMidi + keyMeta.offset;
          flashKeyElement(document.getElementById('practicePianoKeybed'), e.code);
          handlePracticeNoteInput(pitch, keyMeta.label);
        }
        return;
      }

      // 创作模式与总览页键盘捕获 (A W S E D F T G Y H U J K)
      if (e.repeat) return;
      const creationKey = CREATION_KEYS.find(k => k.code === e.code);
      if (!creationKey) return;

      if (currentMode === 'creation' && creationState.step === 'inputs') {
        e.preventDefault();
        const pitch = creationState.basePitch + creationKey.offset;
        flashKeyElement(document.getElementById('creationPianoKeybed'), e.code);
        triggerCreationKey(pitch, creationKey.label);
      } else if (currentMode === 'overview') {
        e.preventDefault();
        const pitch = 60 + creationKey.offset;
        flashKeyElement(document.getElementById('heroMiniPiano'), e.code);
        synth.triggerNote(pitch, heroFamily, 0.42, 92);
      }
    });

    window.addEventListener('keyup', e => {
      if (currentMode !== 'practice') return;
      // 单按 Shift / 空格在最后松键且本次未参与音符组合演奏时切换整档八度 (3-5)
      if (e.key === 'Shift' && practiceState.shiftHeld) {
        practiceState.shiftHeld = false;
        if (!practiceState.spaceHeld && !practiceState.modifierUsedForNote) {
          practiceState.baseOctave = Math.max(3, practiceState.baseOctave - 1);
          renderPracticeKeyboard();
          updatePracticeFeedbackUI();
        }
        updatePracticeModifierUI();
      } else if (e.code === 'Space' && practiceState.spaceHeld) {
        practiceState.spaceHeld = false;
        if (!practiceState.shiftHeld && !practiceState.modifierUsedForNote) {
          practiceState.baseOctave = Math.min(5, practiceState.baseOctave + 1);
          renderPracticeKeyboard();
          updatePracticeFeedbackUI();
        }
        updatePracticeModifierUI();
      }
    });
  }

  function updatePracticeModifierUI() {
    const el = document.getElementById('practiceModifierStateText');
    if (!el) return;
    if (practiceState.shiftHeld && practiceState.spaceHeld) {
      el.textContent = '⚠ Shift + 空格同时按住（拒绝新音，原音保持）';
    } else if (practiceState.shiftHeld) {
      el.textContent = '按住 Shift：弹音符将叠加低八度副音；直接松开则降八度档';
    } else if (practiceState.spaceHeld) {
      el.textContent = '按住 空格：弹音符将叠加高八度副音；直接松开则升八度档';
    } else {
      el.textContent = '无（单按 Shift 降八度 / 单按空格升八度）';
    }
  }

  /* ========================================================================
     11. 启动初始化
     ======================================================================== */
  initHeroDeck();
  initCreationEvents();
  initPracticeEvents();
  initTranscriptionEvents();
  initModals();
  initGlobalKeyboardShortcuts();
})();
