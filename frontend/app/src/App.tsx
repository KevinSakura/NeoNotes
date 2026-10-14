import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { download, rawRequest, request } from './api';
import { AudioEngine } from './audio';
import PianoRoll from './PianoRoll';
import AudioExports from './AudioExports';
import VocalPanel from './VocalPanel';
import AICreation from './AICreation';
import ReviewPanel from './ReviewPanel';
import LyricRepair from './LyricRepair';
import LyricGroupEditor from './LyricGroupEditor';
import { draftHash } from './draft-hash.mjs';
import type { GroupImpact, GroupParameters } from './types';
import { lyricPractice, lyricGroupPractice } from './lyric-practice.mjs';
import './ai.css';
import { DRUM_PITCHES, KEY_CODES, fullLyricLines, lyricSelection, noteName, playbackEvents, secondsAt, tickAtSeconds } from './music.mjs';
import type { Assessment, Family, GenerationJob, HistoryItem, Inputs, Note, PracticePlayer, Project, ProjectItem, RecordedNote, Score, SongState, Track } from './types';

const familyNames: Record<Family, string> = { keyboard: '键盘', drums: '鼓', guitar: '吉他', bass: '贝斯', other: '其他乐器' };
const familyColors: Record<Family, string> = { keyboard: '#7ba575', drums: '#c68d60', guitar: '#779cb7', bass: '#a28ab5', other: '#759f9b' };
const programs: Record<Family, [number, string][]> = {
  keyboard: [[0, '原声钢琴'], [4, '电钢琴'], [16, '风琴']], drums: [[0, 'GM 鼓组']],
  guitar: [[24, '尼龙弦吉他'], [25, '钢弦吉他'], [27, '电吉他']],
  bass: [[32, '原声贝斯'], [33, '指弹贝斯'], [34, '拨片贝斯']],
  other: [[0, '原声钢琴'], [40, '小提琴'], [56, '小号'], [73, '长笛']],
};
const copy = <T,>(value: T): T => structuredClone(value);
const clock = (seconds: number) => {
  // MIDI tempo uses integer microseconds; tolerate the sub-millisecond rounding at 60s.
  const whole = Math.floor(Math.max(0, seconds) + .001);
  return `${Math.floor(whole / 60).toString().padStart(2, '0')}:${(whole % 60).toString().padStart(2, '0')}`;
};

function Modal({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} aria-label={title} onCancel={event => { event.preventDefault(); close(); }}>
    <div className="modal-header"><h2>{title}</h2><button aria-label="关闭窗口" onClick={close}>×</button></div>{children}
  </dialog>;
}

export default function App({active = true}: {active?: boolean}) {
  const [items, setItems] = useState<ProjectItem[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [draft, setDraft] = useState<SongState | null>(null);
  const draftRef = useRef<SongState | null>(null);
  const projectRef = useRef<Project | null>(null);
  const [view, setView] = useState<'inputs' | 'edit' | 'files'>('inputs');
  const [trackId, setTrackId] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [message, setMessage] = useState('');
  const [reviewSignal, setReviewSignal] = useState(0);
  const [audioStatus, setAudioStatus] = useState('点击琴键或开始录制，加载本地音色');
  const [audioLevel, setAudioLevel] = useState(0);
  const [modal, setModal] = useState<'new' | 'history' | 'unsaved' | 'delete' | 'trash' | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectItem | null>(null);
  const [trashed, setTrashed] = useState<ProjectItem[]>([]);
  const [trashWarnings, setTrashWarnings] = useState<string[]>([]);
  const [newName, setNewName] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [basePitch, setBasePitch] = useState(60);
  const [pressed, setPressed] = useState<number[]>([]);
  const [recording, setRecording] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [playTime, setPlayTime] = useState(0);
  const [playDuration, setPlayDuration] = useState(0);
  const [noteForm, setNoteForm] = useState({ pitch: '60', start: '0', duration: '1', velocity: '90' });
  const [groupNoteIds, setGroupNoteIds] = useState<string[]>([]);
  const [, refreshUndo] = useState(0);
  type EditSnapshot = Pick<SongState, 'score' | 'composition'>;
  const snapshot = (state: SongState): EditSnapshot => copy({ score: state.score, composition: state.composition });
  const undo = useRef<EditSnapshot[]>([]), redo = useRef<EditSnapshot[]>([]);
  const engine = useRef(new AudioEngine());
  const practiceSession = useRef<symbol | null>(null);
  const voices = useRef(new Map<string, { pitch: number; stop: () => void; time: number; track: Track }>());
  const pendingKeys = useRef(new Set<string>());
  const take = useRef<{ start: number; notes: RecordedNote[] } | null>(null);
  const lastAction = useRef('保存修改');
  const continuation = useRef<(() => Promise<void>) | null>(null);
  const activeTrack = draft?.score.tracks.find(track => track.id === trackId) ?? draft?.score.tracks[0];
  const activeRef = useRef(activeTrack); activeRef.current = activeTrack;
  const dirty = !!draft && !!project && JSON.stringify(draft) !== JSON.stringify(project.revision.state);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const note = activeTrack?.notes.find(note => note.id === selected);
  const activeLines = draft?.inputs.selected_lines.length ? fullLyricLines(draft.inputs.lyrics_text) : [];
  const selectedLyrics = draft && lyricSelection(draft.inputs.lyrics_text, activeLines, true);

  function working(next: SongState | null) { draftRef.current = next; setDraft(next); }

  function accept(next: Project) {
    projectRef.current = next; setProject(next); working(copy(next.revision.state));
    setTrackId(previous => next.revision.state.score.tracks.some(track => track.id === previous) ? previous : next.revision.state.score.tracks[0].id);
    setSelected(null); undo.current = []; redo.current = []; refreshUndo(value => value + 1);
    lastAction.current = '保存修改';
  }

  async function list() {
    const result = await request<{ items: ProjectItem[]; warnings: string[] }>('/projects');
    setItems(result.items);
    if (result.warnings.length) setMessage(result.warnings.join('；'));
  }

  async function listTrash() {
    const result = await request<{ items: ProjectItem[]; warnings: string[] }>('/trash');
    setTrashed(result.items); setTrashWarnings(result.warnings);
  }

  function askDelete(item: ProjectItem) {
    stop(); setMessage('');
    document.querySelectorAll<HTMLAudioElement>('audio').forEach(audio => audio.pause());
    const current = projectRef.current;
    setDeleteTarget(current?.id === item.id ? { ...item, current_revision: current.current_revision } : item);
    setModal('delete');
  }

  async function deleteSong() {
    if (!deleteTarget) return;
    const target = deleteTarget;
    await request(`/projects/${target.id}`, { method: 'DELETE', body: JSON.stringify({ base_revision: target.current_revision }) });
    if (projectRef.current?.id === target.id) {
      stop(); practiceSession.current = null;
      projectRef.current = null; setProject(null); working(null);
      setTrackId(''); setSelected(null); setHistory([]); setView('inputs');
      undo.current = []; redo.current = []; continuation.current = null;
      refreshUndo(value => value + 1);
    }
    setItems(previous => previous.filter(item => item.id !== target.id));
    setDeleteTarget(null); setModal(null);
    await list(); setMessage(`「${target.name}」已移到回收站，可从左侧“回收站”恢复。`);
  }

  async function run(action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setMessage('');
    try { await action(); }
    catch (error) { engine.current.stop(); setPlaying(false); setMessage(error instanceof Error ? error.message : '操作失败，请重试。'); }
    finally { busyRef.current = false; setBusy(false); }
  }

  useEffect(() => { void run(list); }, []);
  useEffect(() => {
    engine.current.onStatus = setAudioStatus;
    const timer = setInterval(() => setAudioLevel(engine.current.level()), 100);
    return () => { clearInterval(timer); engine.current.onStatus = undefined; engine.current.dispose(); };
  }, []);
  useEffect(() => {
    if (note) setNoteForm({ pitch: String(note.pitch), start: String(note.start_tick / 480), duration: String(note.duration_tick / 480), velocity: String(note.velocity) });
  }, [note?.id, note?.pitch, note?.start_tick, note?.duration_tick, note?.velocity]);

  function inputs(change: Partial<Inputs>) {
    if (!draftRef.current) return;
    if (!take.current) { engine.current.stop(); setPlaying(false); }
    working({ ...draftRef.current, inputs: { ...draftRef.current.inputs, ...change } });
    lastAction.current = '更新创作输入';
  }

  function release(token: string) {
    pendingKeys.current.delete(token);
    const voice = voices.current.get(token);
    if (!voice) return;
    voice.stop(); voices.current.delete(token);
    if (take.current) {
      const start = Math.max(0, (voice.time - take.current.start) / 1000);
      const duration = Math.min(600 - start, Math.max(0.001, (performance.now() - voice.time) / 1000));
      if (duration > 0 && take.current.notes.length < 5000) {
        take.current.notes.push({ pitch: voice.pitch, velocity: 90, start_seconds: start, duration_seconds: duration,
          track_id: voice.track.id, family: voice.track.family, program: voice.track.program,
          channel: voice.track.channel, bank_msb: voice.track.bank_msb, bank_lsb: voice.track.bank_lsb });
        inputs({ recording: copy(take.current.notes) });
        if (take.current.notes.length >= 5000) {
          take.current = null; setRecording(false); releaseAll();
          setMessage('录制达到 5000 个音符上限，已停止。片段已保留，请保存。');
        }
      }
    }
    setPressed([...voices.current.values()].map(voice => voice.pitch));
  }

  function releaseAll() {
    pendingKeys.current.clear();
    [...voices.current.keys()].forEach(release);
  }

  function finishRecording() {
    if (!take.current) return;
    releaseAll(); take.current = null; setRecording(false);
    lastAction.current = '录制演奏片段';
  }

  function stop(source?: unknown) {
    window.dispatchEvent(new CustomEvent('neonote-stop-audio', { detail: typeof source === 'string' ? source : null }));
    finishRecording(); releaseAll(); engine.current.stop(); setPlaying(false); setPlayTime(0);
  }

  async function press(token: string, pitch: number) {
    const track = activeRef.current;
    if (!track || pendingKeys.current.has(token) || voices.current.has(token) || busyRef.current) return;
    pendingKeys.current.add(token);
    try {
      await engine.current.enable();
      if (!pendingKeys.current.has(token)) return;
      pendingKeys.current.delete(token);
      const voice = { pitch, track: copy(track), time: performance.now(), stop: engine.current.voice(pitch, track) };
      voices.current.set(token, voice); setPressed([...voices.current.values()].map(voice => voice.pitch));
    } catch (error) { pendingKeys.current.delete(token); setMessage(error instanceof Error ? error.message : '浏览器未能启用音频，请再次点击琴键。'); }
  }

  const keyboardActions = useRef({ press, release, stop, basePitch, view, active });
  keyboardActions.current = { press, release, stop, basePitch, view, active };
  useEffect(() => { if (!active) stop(); }, [active]);
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (!keyboardActions.current.active || event.repeat || event.ctrlKey || event.altKey || event.metaKey || target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(target.tagName) || document.querySelector('dialog[open]') || keyboardActions.current.view !== 'inputs') return;
      const index = KEY_CODES.indexOf(event.code);
      if (index < 0) return;
      event.preventDefault();
      const pitch = activeRef.current?.is_drum ? DRUM_PITCHES[index] : keyboardActions.current.basePitch + index;
      void keyboardActions.current.press(event.code, pitch);
    };
    const up = (event: KeyboardEvent) => keyboardActions.current.release(event.code);
    const leave = () => keyboardActions.current.stop();
    const hide = () => { if (document.hidden) leave(); };
    const unload = (event: BeforeUnloadEvent) => { if (dirtyRef.current || take.current) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('keydown', down); window.addEventListener('keyup', up); window.addEventListener('blur', leave);
    document.addEventListener('visibilitychange', hide); window.addEventListener('beforeunload', unload);
    return () => {
      window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('blur', leave);
      document.removeEventListener('visibilitychange', hide); window.removeEventListener('beforeunload', unload); engine.current.stop();
    };
  }, []);
  useEffect(() => {
    if (!recording) return;
    const timer = setInterval(() => {
      if (take.current && performance.now() - take.current.start >= 599500) {
        finishRecording(); setMessage('录制达到 10 分钟上限，已停止。请保存片段。');
      }
    }, 500);
    return () => clearInterval(timer);
  }, [recording]);

  async function saveCurrent() {
    stop();
    const current = projectRef.current!, state = draftRef.current!;
    if (JSON.stringify(state) === JSON.stringify(current.revision.state)) return current;
    const result = await request<Project>(`/projects/${current.id}`, {
      method: 'PUT', body: JSON.stringify({ base_revision: current.current_revision, label: lastAction.current, state }),
    });
    accept(result); await list(); return result;
  }

  function guard(action: () => Promise<void>) {
    stop();
    if (dirtyRef.current || (draftRef.current && projectRef.current && JSON.stringify(draftRef.current) !== JSON.stringify(projectRef.current.revision.state))) {
      continuation.current = action; setModal('unsaved');
    } else void run(action);
  }

  function editScore(label: string, action: (score: Score) => void) {
    if (!draftRef.current || busyRef.current) return;
    finishRecording(); releaseAll(); engine.current.stop(); setPlaying(false);
    const score = copy(draftRef.current.score); action(score);
    score.end_tick = Math.max(score.end_tick, ...score.tracks.flatMap(track => track.notes.map(note => note.start_tick + note.duration_tick)));
    if (score.tracks.reduce((count, track) => count + track.notes.length, 0) > 5000 || secondsAt(score, score.end_tick) > 600.001) {
      setMessage('每首歌暂限 5000 个音符、10 分钟；该修改没有应用。原始输入保留。'); return;
    }
    undo.current.push(snapshot(draftRef.current)); undo.current = undo.current.slice(-100); redo.current = [];
    working({ ...draftRef.current, score }); lastAction.current = label; refreshUndo(value => value + 1);
  }

  function chooseTrack(track: Track) {
    releaseAll(); setTrackId(track.id); setSelected(null);
    setBasePitch(track.family === 'bass' ? 36 : track.family === 'guitar' ? 48 : 60);
  }

  function addNote(pitch: number, start: number) {
    if (!activeTrack) return;
    const id = crypto.randomUUID();
    editScore('添加音符', score => score.tracks.find(track => track.id === activeTrack.id)!.notes.push({ id, pitch, velocity: 90, start_tick: Math.round(start), duration_tick: 480 }));
    setSelected(id);
  }

  function changeView(next: typeof view) { stop(); setView(next); }

  async function playScore() {
    if (!draftRef.current) return;
    finishRecording(); releaseAll();
    const events = playbackEvents(draftRef.current.score);
    if (!events.length) { setMessage('当前没有可试听的音符，请录制并加入编曲，或导入 MIDI。'); return; }
    const duration = secondsAt(draftRef.current.score, draftRef.current.score.end_tick);
    setPlaying(true); setPlayDuration(duration);
    await engine.current.play(events, duration, setPlayTime, () => setPlaying(false));
  }

  async function moveHistory(action: 'undo' | 'redo') {
    stop();
    const from = action === 'undo' ? undo : redo, to = action === 'undo' ? redo : undo;
    if (from.current.length && draftRef.current) {
      to.current.push(snapshot(draftRef.current));
      working({ ...draftRef.current, ...from.current.pop()! }); setSelected(null);
      lastAction.current = action === 'undo' ? '撤销音乐与歌词对应编辑' : '重做音乐与歌词对应编辑'; refreshUndo(value => value + 1); return;
    }
    if (dirtyRef.current) { setMessage('请先保存输入修改，再撤销或重做已保存版本。'); return; }
    const current = projectRef.current!;
    accept(await request<Project>(`/projects/${current.id}/${action}`, { method: 'POST', body: JSON.stringify({ base_revision: current.current_revision }) }));
    await list();
  }

  function applyCandidate(job: GenerationJob) {
    guard(async () => {
      const current = projectRef.current!;
      accept(await request<Project>(`/projects/${current.id}/generations/${job.id}/apply`, { method: 'POST', body: JSON.stringify({ base_revision: current.current_revision }) }));
      await list(); setView('edit'); setMessage(`${job.mock ? '模拟' : 'AI'}候选已作为新版本保存。请试听、检查歌词对应，再人工审核。`);
    });
  }

  function previewCandidate(state: SongState) {
    void run(async () => {
      stop(); const duration = secondsAt(state.score, state.score.end_tick);
      setPlaying(true); setPlayDuration(duration);
      await engine.current.play(playbackEvents(state.score), duration, setPlayTime, () => setPlaying(false));
    });
  }

  async function editLyricGroup(params: GroupParameters, preview: boolean, transaction_id: string) {
    let impact: GroupImpact | null = null;
    let failure: unknown;
    await run(async () => {
      try {
        stop(); const before = copy(draftRef.current!), current = projectRef.current!;
        const hash = await draftHash(before);
        const result = await request<{ state: SongState; impact: GroupImpact; before_hash: string }>(
          '/projects/' + current.id + '/lyric-groups', { method: 'POST',
          body: JSON.stringify({ ...params, preview, transaction_id, state: before, draft_hash: hash, base_revision: current.current_revision }) });
        if (projectRef.current?.current_revision !== current.current_revision || await draftHash(draftRef.current!) !== hash) throw Error('草稿或基础版本已变化，字组结果未应用，请重新预览。');
        impact = result.impact;
        if (!preview) {
          undo.current.push(snapshot(before)); undo.current = undo.current.slice(-100); redo.current = [];
          working(result.state); lastAction.current = '歌词音符组原子编辑'; refreshUndo(value => value + 1);
          setMessage('字组已更新到草稿，一次撤销可恢复；请保存后重新试听与审核。');
        }
      } catch (error) { failure = error; throw error; }
    });
    if (failure) throw failure;
    if (!impact) throw Error('另一项操作正在进行，请稍后再试。');
    return impact;
  }

  function selectLyricGroup(ids: string[], focus = false) {
    stop(); setGroupNoteIds(ids);
    const lead = draftRef.current?.composition?.melody_track_id;
    if (focus) { if (lead) setTrackId(lead); setSelected(ids[0] ?? null); }
  }

  async function repairLyrics(line_index: number, unit_index: number, note_ids: string[]) {
    let assessment: Assessment | null = null;
    await run(async () => {
      stop(); const before = draftRef.current!, current = projectRef.current!;
      const result = await request<{ state: SongState; assessment: Assessment }>(`/projects/${current.id}/lyrics-repair`, { method: 'POST', body: JSON.stringify({ base_revision: current.current_revision, state: before, line_index, unit_index, note_ids }) });
      undo.current.push(snapshot(before)); undo.current = undo.current.slice(-100); redo.current = [];
      working(result.state); lastAction.current = '人工修复歌词与音符对应'; refreshUndo(value => value+1);
      assessment = result.assessment; setMessage(`歌词对应已更新到草稿，请保存后重新试听和审核。${assessment.valid ? '结构核对通过，仍需亲自试唱。' : `仍需处理：${assessment.checks.filter(check => !check.passed).map(check => check.message).join('；')}`}`);
    });
    return assessment;
  }

  const practice: PracticePlayer = (state, lineIndex, progress, options) => {
    const session = Symbol('lyric-practice');
    void run(async () => {
      stop(); practiceSession.current = session;
      try {
        const clip = options.unitIndex !== undefined && lineIndex !== null
          ? lyricGroupPractice(state, lineIndex, options.unitIndex, options.context ?? 'word', options)
          : lyricPractice(state, lineIndex, options);
        setPlaying(true); setPlayTime(0); setPlayDuration(clip.duration);
        await engine.current.play(clip.events, clip.duration, seconds => {
          if (practiceSession.current === session) { setPlayTime(seconds); progress(clip.offset + seconds*clip.speed); }
        }, () => {
          if (practiceSession.current === session) { practiceSession.current = null; setPlaying(false); progress(null); }
        }, { loop: lineIndex !== null && options.loop });
      } catch (error) { if (practiceSession.current === session) { practiceSession.current = null; progress(null); } throw error; }
    });
    return () => { if (practiceSession.current === session) { stop(); practiceSession.current = null; } };
  };

  async function upload(file: File, kind: 'lyrics' | 'midi') {
    const current = await saveCurrent();
    const form = new FormData(); form.set('file', file); form.set('base_revision', current.current_revision);
    accept(await request<Project>(`/projects/${current.id}/${kind}`, { method: 'POST', body: form }));
    await list(); setMessage(kind === 'lyrics' ? '歌词原文件已保存，本次使用全文谱曲，时长按歌词安排。' : 'MIDI 已解析并保存，原文件也已保留。');
    if (kind === 'midi') setView('edit');
  }

  function draftDownload() {
    if (!draftRef.current || !projectRef.current) return;
    download(new Blob([JSON.stringify({ song_id: projectRef.current.id, base_revision: projectRef.current.current_revision, notice: '浏览器草稿备份；可能尚未保存到歌曲目录。当前不提供 JSON 导入。', state: draftRef.current }, null, 2)], { type: 'application/json' }), 'neonote-draft.json');
  }

  const lines = draft?.inputs.lyrics_text.split('\n') ?? [];
  const chosenText = draft?.inputs.selected_lines.map(index => lines[index]).join('\n') ?? '';
  const totalNotes = draft?.score.tracks.reduce((sum, track) => sum + track.notes.length, 0) ?? 0;
  const duration = draft ? secondsAt(draft.score, draft.score.end_tick) : 0;

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" onClick={event => event.preventDefault()}><span className="brand-symbol">♮</span><span>NeoNote<small>新的乐章 · LOCAL STUDIO</small></span></a>
      <button className="primary new-song" disabled={busy} onClick={() => { setNewName(''); setModal('new'); }}>＋ 新建歌曲</button>
      <div className="sidebar-label">我的歌曲 <span>{items.length}</span></div>
      <nav className="project-list" aria-label="歌曲列表">{items.map(item => <div className="project-row" key={item.id}><button disabled={busy}
        className={`project-item ${project?.id === item.id ? 'active' : ''}`} onClick={() => guard(async () => { accept(await request<Project>(`/projects/${item.id}`)); })}>
        <span>♫　{item.name}</span><small>本地歌曲 · {item.id.slice(0, 8)}</small>
      </button><button className="delete-song" disabled={busy} aria-label={`删除歌曲「${item.name}」（${item.id.slice(0, 8)}）`} title="删除歌曲" onClick={() => askDelete(item)}>删除</button></div>)}</nav>
      {!items.length && <p className="muted">新建一首歌，让灵感有个落脚的地方。</p>}
      <button className="sidebar-trash" disabled={busy} onClick={() => void run(async () => { stop(); await listTrash(); setModal('trash'); })}>回收站</button>
      <div className="sidebar-bottom"><span className="online-dot" /> 本地编曲工作台<p>歌曲与音频保存到 data/<br />模型模式与每次请求在生成页确认</p></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><div className="breadcrumb">我的歌曲 <span>/</span> <strong>{project?.name ?? '开始你的第一首歌'}</strong></div>
        {project && <div className="header-actions"><span className={`save-state ${dirty ? 'unsaved' : ''}`} data-testid="save-state">{recording ? '正在录制，请完成后保存' : dirty ? '有未保存的修改' : `已写入歌曲目录 · v${project.revision.number}`}</span>
          <button disabled={busy || recording} onClick={() => void run(async () => { stop(); setHistory(await request<HistoryItem[]>(`/projects/${project.id}/history`)); setModal('history'); })}>◷ 历史版本</button>
          <button className="primary" disabled={busy || (!dirty && !recording)} onClick={() => void run(async () => { await saveCurrent(); setMessage('歌曲已保存到本地目录。'); })}>保存歌曲</button></div>}
      </header>
      <div className="stage-banner"><span>歌词谱曲更新</span>有歌词时一次覆盖全文，时长按歌词安排；无歌词先生成60秒短曲。候选须明确采用、试听与审核。</div>
      {message && <div className="notice" role="alert"><span>{message}</span><button aria-label="关闭提示" onClick={() => setMessage('')}>×</button></div>}
      <main>
        {!draft || !project ? <section className="welcome"><span className="eyebrow">MAKE ROOM FOR YOUR MELODY</span><h1>从一点灵感，开始一首歌。</h1><p>弹几个音，写一段话，或带上你的歌词。<br />这一阶段，你可以把它们存好，编辑并导出真正的 MIDI。</p><button className="primary" disabled={busy} onClick={() => setModal('new')}>＋ 新建第一首歌</button><div className="welcome-notes"><span>⌨　电脑键盘试弹</span><span>♫　音符编辑与回放</span><span>▣　每首歌独立保存</span></div></section> : <>
          <nav className="steps" aria-label="工作步骤">{([['inputs', '01', '收集灵感'], ['edit', '02', '演奏与编辑'], ['files', '03', '保存与导出']] as const).map(([name, number, label]) => <button key={name} className={view === name ? 'active' : ''} disabled={busy} onClick={() => changeView(name)}><b>{number}</b>{label}</button>)}</nav>
          {view === 'inputs' && <>
            <div className="page-heading"><span className="eyebrow">A LITTLE SOUND, A LITTLE STORY</span><h1>先听听，再决定怎么表达。</h1><p>演奏和文字都可以先保存。无需先弹琴，也可以只整理文字灵感。</p></div>
            <div className="creation-grid"><div className="column">
              <article className="card"><div className="card-heading"><h2><em>1</em> 试着弹一小段</h2><span className="tag">真实音色试听</span></div><p className="muted">点击琴键，或按 A–K。填写文字时，电脑按键不会触发琴键。</p>
                <div className="audio-state"><span data-testid="audio-status">{audioStatus}</span><meter aria-label="音频电平" data-testid="audio-level" min="0" max="1" value={audioLevel} /></div>
                <div className="instrument-buttons">{(['keyboard', 'drums', 'guitar', 'bass'] as Family[]).map(family => {
                  const target = draft.score.tracks.find(track => track.family === family);
                  return <button key={family} className={activeTrack?.family === family ? 'active' : ''} disabled={!target || busy} aria-pressed={activeTrack?.family === family} onClick={() => target && chooseTrack(target)}>{familyNames[family]}</button>;
                })}</div>
                <div className="keyboard" aria-label="电子琴键盘">{[0, 2, 4, 5, 7, 9, 11, 12, 1, 3, 6, 8, 10].map(index => {
                  const black = [1, 3, 6, 8, 10].includes(index);
                  const position: Record<number, number> = { 1: 1, 3: 2, 6: 4, 8: 5, 10: 6 };
                  const pitch = activeTrack?.is_drum ? DRUM_PITCHES[index] : basePitch + index;
                  return <button key={index} className={`key ${black ? 'black' : ''} ${pressed.includes(pitch) ? 'pressed' : ''}`} disabled={busy}
                    style={black ? { left: `calc(${position[index] * 12.5}% - 3.5%)` } : {}} aria-label={`演奏 ${noteName(pitch, activeTrack?.is_drum)}，按键 ${KEY_CODES[index].slice(3)}`}
                    onPointerDown={event => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); void press('pointer' + event.pointerId, pitch); }}
                    onPointerUp={event => release('pointer' + event.pointerId)} onPointerCancel={event => release('pointer' + event.pointerId)}>
                    <small>{black ? '' : noteName(pitch, activeTrack?.is_drum)}</small><span>{KEY_CODES[index].slice(3)}</span>
                  </button>;
                })}</div>
                <div className="keyboard-options"><span>{activeTrack?.is_drum ? 'A 底鼓 · S 军鼓 · D 闭踩镲' : `${activeTrack?.name} · 电脑按键固定力度`}</span>{!activeTrack?.is_drum && <div><button disabled={basePitch <= 0 || busy} onClick={() => { releaseAll(); setBasePitch(pitch => pitch - 12); }}>低八度</button><button disabled={basePitch >= 108 || busy} onClick={() => { releaseAll(); setBasePitch(pitch => pitch + 12); }}>高八度</button></div>}</div>
                <div className="record-bar"><button className={recording ? 'record recording' : 'record'} disabled={busy} onClick={() => void run(async () => {
                  if (take.current) { finishRecording(); return; }
                  stop(); await engine.current.enable(); inputs({ recording: [] }); take.current = { start: performance.now(), notes: [] }; setRecording(true);
                })}>{recording ? '■ 停止录制' : '● 开始录制'}</button>
                  <button disabled={busy || recording || !draft.inputs.recording.length} onClick={() => void run(async () => {
                    stop(); const notes = draftRef.current!.inputs.recording;
                    const events = notes.map(note => ({ pitch: note.pitch, velocity: note.velocity, start: note.start_seconds, duration: note.duration_seconds, track: { family: note.family, program: note.program, volume: 75, channel: note.channel ?? undefined, bank_msb: note.bank_msb ?? 0, bank_lsb: note.bank_lsb ?? 0 } }));
                    const length = Math.max(...notes.map(note => note.start_seconds + note.duration_seconds));
                    setPlaying(true); setPlayDuration(length); await engine.current.play(events.sort((a, b) => a.start - b.start), length, setPlayTime, () => setPlaying(false));
                  })}>▷ 回放片段</button>
                  <button className="text-button" disabled={busy || recording || !draft.inputs.recording.length} onClick={() => { stop(); inputs({ recording: [] }); }}>清空</button>
                  <span data-testid="record-info">{recording ? '录制中 · ' : ''}{draft.inputs.recording.length} 个音符</span>
                </div>
                <div className="inline-actions"><button disabled={busy || recording || !draft.inputs.recording.length} onClick={() => {
                  const recording = copy(draftRef.current!.inputs.recording);
                  if (recording.some(note => !draftRef.current!.score.tracks.some(track => track.id === note.track_id))) { setMessage('录制所用声部已被 MIDI 导入替换，请重新录制。原始片段仍保留。'); return; }
                  editScore('把演奏片段加入编曲', score => {
                    for (const note of recording) {
                      const start = tickAtSeconds(score, note.start_seconds), end = tickAtSeconds(score, note.start_seconds + note.duration_seconds);
                      score.tracks.find(track => track.id === note.track_id)!.notes.push({ id: crypto.randomUUID(), pitch: note.pitch, velocity: note.velocity, start_tick: start, duration_tick: Math.max(1, end - start) });
                    }
                  }); changeView('edit');
                }}>把片段加入编曲 →</button><span className="muted">保留原始录制；不自动纠错或量化。</span></div>
                <label className="field">这段旋律如何参与创作？<select aria-label="旋律参与方式" disabled={busy} value={draft.inputs.melody_mode} onChange={event => inputs({ melody_mode: event.target.value as Inputs['melody_mode'] })}><option value="extend">依据原始走向，自由延展</option><option value="preserve">完整保留原演奏，随后延展</option></select></label>
                <p className="hint">此选择作为 AI 编曲约束。鼓键使用 GM 打击乐音符，不当作钢琴音高。</p>
              </article>
              <article className="card"><div className="card-heading"><h2><em>2</em> 带上你的歌词</h2><label className={`upload-button ${busy ? 'disabled' : ''}`}>↑ 上传 TXT<input type="file" accept=".txt,text/plain" aria-label="上传歌词 TXT" disabled={busy || recording} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void run(() => upload(file, 'lyrics')); }} /></label></div>
                <p className="muted">UTF-8 TXT，最多 500 KB。原文件会保留在歌曲目录。也可以直接粘贴。</p>
                <label className="sr-only" htmlFor="lyrics">歌词全文</label><textarea id="lyrics" disabled={busy} rows={5} value={draft.inputs.lyrics_text} placeholder="把想唱的话写在这里……" onChange={event => { const text = event.target.value.replace(/\r\n?/g, '\n'); inputs({ lyrics_text: text, selected_lines: fullLyricLines(text) }); }} />
                <div className="lyric-label">一次谱曲覆盖全文 <span data-testid="lyric-selection-count">使用 {selectedLyrics?.lines} 行 · {selectedLyrics?.units}/1024 字词（最多64行）</span></div>
                {selectedLyrics?.error && <p className="lyric-input-warning" data-testid="lyric-selection-warning">{selectedLyrics.error}</p>}
                <div className="inline-actions"><button className="text-button" disabled={busy || !draft.inputs.selected_lines.length} onClick={() => inputs({ selected_lines: [] })}>本次不使用歌词</button><button className="text-button" disabled={busy || !draft.inputs.lyrics_text.trim() || !!draft.inputs.selected_lines.length} onClick={() => inputs({ selected_lines: fullLyricLines(draft.inputs.lyrics_text) })}>使用歌词全文</button></div>
                {!draft.inputs.selected_lines.length && <p className="hint">本次只使用主题、音乐感觉或演奏；歌词全文和原文件保留，不发送给模型。点击“使用歌词全文”可恢复歌词谱曲。</p>}
                {!!draft.inputs.selected_lines.length && <p className="hint">不再先选60秒歌词。模型设计逐句旋律，平台安排全文的字词、换气、器乐段与伴奏；可唱性仍须亲自试唱。</p>}
                <div className="lyric-lines">{lines.map((line, index) => line.trim() && <p key={index}><span>{index + 1}. {line}</span></p>)}</div>
                <p className="hint">歌词决定表达内容，情感描述决定音乐呈现。悲伤歌词也可以搭配明亮曲风。生成后展示字词与主旋律对应；采用并保存后，可在“保存与导出”中生成绮萱演唱并试听。</p>
              </article>
            </div><div className="column"><article className="card"><h2><em>3</em> 想表达什么？</h2>
              <label className="field">主题<input disabled={busy} value={draft.inputs.theme} maxLength={2000} placeholder="例如：毕业后，奔向各自的生活" onChange={event => inputs({ theme: event.target.value })} /></label>
              <label className="field">情感与希望的音乐感觉<textarea disabled={busy} rows={5} maxLength={4000} value={draft.inputs.emotion} placeholder="有一点不舍，但希望明亮、轻快，带着期待……" onChange={event => inputs({ emotion: event.target.value })} /></label>
              <div className="input-summary"><span>已记录的灵感</span><p>{draft.inputs.recording.length} 个演奏音符 · {draft.inputs.selected_lines.length} 行歌词</p></div>
              <button className="primary full-width" disabled={busy} onClick={() => void run(async () => { await saveCurrent(); setMessage('创作输入已保存到歌曲目录。'); })}>保存这份灵感</button>
            </article><AICreation key={project.id} project={project} draft={draft} busy={busy} operation="generate" dirty={dirty} prepare={saveCurrent} run={run} apply={applyCandidate} preview={previewCandidate} practice={practice} /><button className="text-button" disabled={busy} onClick={() => changeView('edit')}>去手动编辑 →</button></div></div>
          </>}
          {view === 'edit' && activeTrack && <>
            <div className="page-heading"><span className="eyebrow">GIVE YOUR IDEA A SHAPE</span><h1>让每一个音，落在想要的位置。</h1><p>先选声部，点网格添加音符；点音符后，可用下方表单移动和调整。</p></div>
            <div className="editor-toolbar"><button className="primary" disabled={busy} onClick={() => { if (playing) stop(); else void run(playScore); }}>{playing ? '■ 停止试听' : '▶ 试听编曲'}</button><button disabled={!playing} onClick={stop}>停止</button><span className="play-clock">{clock(playTime)} / {clock(playing ? playDuration : duration)}</span>
              <button disabled={busy || (!undo.current.length && (dirty || !project.can_undo))} onClick={() => void run(() => moveHistory('undo'))}>↶ 撤销</button><button disabled={busy || (!redo.current.length && (dirty || !project.can_redo))} onClick={() => void run(() => moveHistory('redo'))}>↷ 重做</button>
              <label className="upload-button">↑ 导入 MIDI<input type="file" accept=".mid,.midi,audio/midi" aria-label="导入 MIDI" disabled={busy} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void run(() => upload(file, 'midi')); }} /></label>
            </div>
            <div className="editor-layout"><article className="card tracks-card"><h2>乐器声部</h2><p className="muted">静音和独奏作用于编曲试听。</p>{draft.score.tracks.map(track => <div key={track.id} className={`track ${activeTrack.id === track.id ? 'active' : ''}`} style={{ '--track-color': familyColors[track.family] } as React.CSSProperties}>
              <button className="track-select" disabled={busy} onClick={() => chooseTrack(track)}>{track.name}<small>{track.notes.length} 个音符 · 通道 {track.channel + 1}</small></button>
              <div className="track-buttons">{(['solo', 'mute'] as const).map(key => <button key={key} disabled={busy} aria-pressed={track[key]} className={track[key] ? 'on' : ''} onClick={() => editScore(`调整${track.name}${key === 'solo' ? '独奏' : '静音'}`, score => { const target = score.tracks.find(item => item.id === track.id)!; target[key] = !target[key]; })}>{key === 'solo' ? '独奏' : '静音'}</button>)}</div>
              <label className="compact-field">音色<select aria-label={`${track.name}音色`} disabled={busy || track.is_drum} value={track.program} onChange={event => editScore(`调整${track.name}音色`, score => { score.tracks.find(item => item.id === track.id)!.program = Number(event.target.value); })}>
                {!programs[track.family].some(([value]) => value === track.program) && <option value={track.program}>导入音色 · Program {track.program}</option>}
                {programs[track.family].map(([value, name]) => <option key={value} value={value}>{name}</option>)}
              </select></label><label className="compact-field">音量 {track.volume}%<input type="range" aria-label={`${track.name}音量`} min={0} max={100} disabled={busy} value={track.volume} onChange={event => editScore(`调整${track.name}音量`, score => { score.tracks.find(item => item.id === track.id)!.volume = Number(event.target.value); })} /></label>
            </div>)}</article>
            <article className="card piano-roll" style={{ '--track-color': familyColors[activeTrack.family] } as React.CSSProperties}><div className="card-heading"><h2>{activeTrack.name} · 音符时间轴</h2><span className="tag">真实音色试听</span></div>
              <div className="score-controls"><label>开头速度（BPM）<input type="number" min={40} max={240} aria-label="开头速度" disabled={busy || draft.score.tempo_map.length > 1} value={Math.round(60e6 / draft.score.tempo_map[0].microseconds_per_beat)} onChange={event => {
                const bpm = Number(event.target.value); if (bpm >= 40 && bpm <= 240) editScore('调整速度', score => { score.tempo_map[0].microseconds_per_beat = Math.round(60e6 / bpm); });
              }} /></label><span>{draft.score.time_signatures[0].numerator}/{draft.score.time_signatures[0].denominator} · {draft.score.tempo_map.length > 1 ? '导入变速已保留，首阶段不编辑变速图' : `${clock(duration)} 时间轴`}</span></div>
              <PianoRoll score={draft.score} track={activeTrack} selected={selected} onSelect={setSelected} onAdd={addNote} highlighted={groupNoteIds} />
              <p className="muted">左右滚动查看完整时间轴。坐标按四分音符拍计；网格新增以 1/4 拍定位，录制只做 tick 精度换算。</p>
              <form className="note-form" onSubmit={event => {
                event.preventDefault(); if (!note) return;
                const pitch = Number(noteForm.pitch), start = Number(noteForm.start), length = Number(noteForm.duration), velocity = Number(noteForm.velocity);
                if (!Number.isInteger(pitch) || pitch < 0 || pitch > 127 || !Number.isInteger(velocity) || velocity < 1 || velocity > 127 || !Number.isFinite(start) || !Number.isFinite(length) || start < 0 || length <= 0) { setMessage('请填写有效音符：音高 0–127，力度 1–127，开始不小于 0，时长大于 0。'); return; }
                editScore('移动或调整音符', score => { Object.assign(score.tracks.find(track => track.id === activeTrack.id)!.notes.find(item => item.id === note.id)!, { pitch, velocity, start_tick: Math.round(start * 480), duration_tick: Math.max(1, Math.round(length * 480)) }); });
              }}>
                {([['pitch', '音高（MIDI）'], ['start', '开始（拍）'], ['duration', '时长（拍）'], ['velocity', '力度']] as const).map(([key, label]) => <label key={key}>{label}<input type="number" step={key === 'pitch' || key === 'velocity' ? '1' : 'any'} disabled={!note || busy} value={noteForm[key]} onChange={event => setNoteForm(form => ({ ...form, [key]: event.target.value }))} /></label>)}
                <button type="submit" disabled={!note || busy}>应用修改</button><button type="button" disabled={!note || busy} onClick={() => { editScore('删除音符', score => { const track = score.tracks.find(track => track.id === activeTrack.id)!; track.notes = track.notes.filter(item => item.id !== selected); }); setSelected(null); }}>删除音符</button>
              </form><p className="muted">{note ? `${noteName(note.pitch, activeTrack.is_drum)} 已选中` : '点一个音符即可修改；点空白网格添加。'}</p>
            </article></div>
            <LyricRepair state={draft} busy={busy} bind={repairLyrics} />
            <LyricGroupEditor state={draft} busy={busy} edit={editLyricGroup} practice={practice} select={selectLyricGroup} stop={stop} />
            <AICreation key={project.id} project={project} draft={draft} busy={busy} operation="modify" dirty={dirty} prepare={saveCurrent} run={run} apply={applyCandidate} preview={previewCandidate} practice={practice} />
            <div className="bottom-actions"><button disabled={busy} onClick={() => changeView('inputs')}>← 返回创作输入</button><button className="primary" disabled={busy} onClick={() => changeView('files')}>去保存与导出 →</button></div>
          </>}
          {view === 'files' && <>
            <div className="page-heading"><span className="eyebrow">KEEP WHAT YOU HAVE MADE</span><h1>把灵感，留在这首歌里。</h1><p>歌词、演奏输入、乐谱和历史版本，都在这首歌自己的目录中。</p></div>
            <div className="files-grid"><article className="card"><span className="tag">当前已保存版本 v{project.revision.number}</span><h2 className="spaced">歌曲工程</h2><dl className="project-details"><dt>保存位置</dt><dd><code>{project.directory}/</code></dd><dt>保存方式</dt><dd>手动保存；收到后端确认后显示已保存。</dd><dt>当前内容</dt><dd>{draft.score.tracks.length} 个声部 · {totalNotes} 个音符 · {draft.inputs.selected_lines.length} 行歌词选段</dd><dt>时间轴</dt><dd>{clock(duration)}（空白时间也保留在 MIDI 中）</dd></dl>
              {dirty && <p className="hint">你还有未保存的修改。导出 MIDI 会先保存当前草稿。</p>}
              <div className="inline-actions"><button className="primary" disabled={busy || !dirty} onClick={() => void run(async () => { await saveCurrent(); setMessage('当前歌曲已保存。'); })}>保存当前版本</button><button disabled={busy} onClick={draftDownload}>下载草稿 JSON</button></div>
              {chosenText && <div className="chosen-lyrics"><b>当前版本使用的歌词</b><p>{chosenText}</p></div>}
              <p className="muted">草稿 JSON 用于备份检查，当前不提供 JSON 导入。请定期备份整个 data 文件夹。</p>
            </article><article className="card"><h2>带走你的音乐</h2><button className="export-card" disabled={busy || !totalNotes} onClick={() => void run(async () => {
              const current = await saveCurrent(); const response = await rawRequest(`/projects/${current.id}/midi-export`, { method: 'POST', body: JSON.stringify({ base_revision: current.current_revision }) });
              download(await response.blob(), `neonote-${current.id.slice(0, 8)}-v${current.revision.number}.mid`); setMessage(`真实 MIDI 已下载，并保存到 ${current.directory}/exports/。`);
            })}><b>♫ MIDI</b><span>真实多轨文件 · 保留音符、时间与实际乐器信息</span><em>导出 ↓</em></button>
              <p className="muted">导出完整乐谱，保留所有声部音符。静音与独奏设置保存在工程中、用于试听；MIDI 保留声部音量。没有音符时不能导出。</p>
              <p className="hint">MIDI 是可编辑的演奏信息，WAV 和 MP3 是下面实际渲染的声音。</p>
            </article></div>
            <VocalPanel key={`vocals-${project.id}`} project={project} dirty={dirty} busy={busy} prepare={saveCurrent} run={run} stopOther={() => stop()} />
            <AudioExports key={project.id} project={project} dirty={dirty} busy={busy} prepare={saveCurrent} run={run} stopOther={() => stop('render')} />
            <ReviewPanel key={project.id} project={project} state={draft} dirty={dirty} busy={busy} run={run} practice={practice} reviewUpdated={() => setReviewSignal(value => value + 1)} />{draft.composition?.scope !== 'lyrics' && <AICreation key={`full-${project.id}`} project={project} draft={draft} dirty={dirty} busy={busy} operation="full" prepare={saveCurrent} run={run} apply={applyCandidate} preview={previewCandidate} practice={practice} reviewSignal={reviewSignal} />}
          </>}
        </>}
      </main><footer>NeoNote <span>把复杂留给工具，把表达留给你。</span><span>本地工作台</span></footer>
    </div>
    {playing && <div className="floating-player"><span>真实音色试听 · {clock(playTime)} / {clock(playDuration)}</span><meter aria-label="回放音频电平" data-testid="playback-audio-level" min="0" max="1" value={audioLevel} /><button onClick={stop}>■ 停止</button></div>}
    {modal === 'delete' && deleteTarget && <Modal title="删除这首歌曲？" close={() => { if (!busyRef.current) { setModal(null); setDeleteTarget(null); } }}>
      <p>将「{deleteTarget.name}」（{deleteTarget.id.slice(0, 8)}）从歌曲列表移除。已保存的歌词、演奏、所有版本、AI候选及MIDI/WAV/MP3会一起移到本地回收站，可恢复。</p>
      {project?.id === deleteTarget.id && dirty && <><p className="hint">当前未保存的修改会被放弃，回收站只能恢复已保存内容。需要保留草稿时，请先下载或取消删除后保存。</p><button disabled={busy} onClick={draftDownload}>下载当前草稿</button></>}
      <p className="muted">正在生成或渲染的歌曲须等待任务结束。回收站保留文件，仍占用磁盘空间。</p>
      {message && <p className="hint">{message}</p>}
      <div className="modal-actions"><button autoFocus disabled={busy} onClick={() => { setModal(null); setDeleteTarget(null); }}>取消</button><button className="danger" disabled={busy} onClick={() => void run(deleteSong)}>{busy ? '正在移入回收站…' : '确认删除'}</button></div>
    </Modal>}
    {modal === 'trash' && <Modal title="歌曲回收站" close={() => { if (!busyRef.current) setModal(null); }}>
      <p className="muted">这里保留删除时的整首歌曲，包括输入、已保存版本、AI候选和导出文件。恢复后从左侧歌曲列表打开；当前草稿不会被替换。文件保存在 data/.trash/，未提供永久清空。</p>
      {!trashed.length && <p>回收站为空。</p>}
      {trashWarnings.map(warning => <p className="hint" key={warning}>{warning}</p>)}
      {trashed.map(item => <div className="history-row" key={item.id}><div><b>{item.name}</b><small>歌曲ID · {item.id.slice(0, 8)}</small></div><button disabled={busy} aria-label={`恢复歌曲「${item.name}」（${item.id.slice(0, 8)}）`} onClick={() => void run(async () => {
        await request<Project>(`/trash/${item.id}/restore`, { method: 'POST' });
        await list(); await listTrash(); setMessage(`「${item.name}」已恢复，请从歌曲列表打开。`);
      })}>恢复歌曲</button></div>)}
      {message && <p className="hint">{message}</p>}
    </Modal>}
    {modal === 'new' && <Modal title="新建一首歌" close={() => setModal(null)}><form onSubmit={event => {
      event.preventDefault(); const name = newName.trim(); if (!name) return;
      setModal(null); guard(async () => { accept(await request<Project>('/projects', { method: 'POST', body: JSON.stringify({ name }) })); await list(); setView('inputs'); });
    }}><label className="field">歌曲名称<input autoFocus required maxLength={80} value={newName} onChange={event => setNewName(event.target.value)} placeholder="例如：周末的小旅行" /></label><p className="muted">创建空白歌曲，使用独立 ID 保存到 data/。同名歌曲也不会互相覆盖。</p><button className="primary" disabled={busy}>创建歌曲</button></form></Modal>}
    {modal === 'history' && <Modal title="已保存的历史版本" close={() => setModal(null)}><p className="muted">版本保存在磁盘，刷新或重启后仍可撤销、重做和恢复。恢复历史会创建新版本。</p>{history.map(item => <div className="history-row" key={item.id}><div><b>v{item.number} · {item.label}</b><small>{new Date(item.created_at).toLocaleString()} {item.id === project?.current_revision ? ' · 当前已保存' : ''}</small></div><button disabled={busy || item.id === project?.current_revision} onClick={() => {
      setModal(null); guard(async () => { const current = projectRef.current!; accept(await request<Project>(`/projects/${current.id}/restore`, { method: 'POST', body: JSON.stringify({ base_revision: current.current_revision, revision_id: item.id }) })); await list(); });
    }}>恢复</button></div>)}</Modal>}
    {modal === 'unsaved' && <Modal title="这首歌还有未保存的修改" close={() => setModal(null)}><p>可以先保存再继续，或明确放弃当前草稿。你已保存的歌曲版本会保留。</p><button onClick={draftDownload}>下载当前草稿</button><div className="modal-actions"><button className="primary" disabled={busy} onClick={() => void run(async () => { await saveCurrent(); const action = continuation.current; continuation.current = null; setModal(null); await action?.(); })}>保存并继续</button><button disabled={busy} onClick={() => { const action = continuation.current; continuation.current = null; setModal(null); if (action) void run(action); }}>放弃草稿并继续</button><button disabled={busy} onClick={() => setModal(null)}>取消</button></div></Modal>}
  </div>;
}
