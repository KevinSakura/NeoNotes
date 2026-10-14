import { useEffect, useRef, useState } from 'react';
import { noteName, secondsAt } from './music.mjs';
import type { GroupEditor, GroupImpact, GroupParameters, PracticePlayer, SongState } from './types';

const nonce = () => crypto.randomUUID().replaceAll('-', '');
export default function LyricGroupEditor({ state, busy, edit, practice, select, stop }: {
  state: SongState; busy: boolean; edit: GroupEditor; practice: PracticePlayer;
  select: (ids: string[], focus?: boolean) => void; stop: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [mode, setMode] = useState<'fixed' | 'ripple'>('fixed');
  const [duration, setDuration] = useState('1');
  const [pitches, setPitches] = useState('');
  const [weights, setWeights] = useState('');
  const [interval, setInterval] = useState(2);
  const [noteIndex, setNoteIndex] = useState(0);
  const [keep, setKeep] = useState<'first' | 'last'>('first');
  const [speed, setSpeed] = useState<0.75 | 1>(1);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<{ params: GroupParameters; id: string; impact: GroupImpact } | null>(null);
  const cancel = useRef<(() => void) | null>(null);
  const units = state.composition?.lyrics_alignment ?? [];
  const unit = units[index] ?? units[0];
  const track = state.score.tracks.find(t => t.id === state.composition?.melody_track_id);
  const notes = unit?.note_ids.map(id => track?.notes.find(n => n.id === id)).filter(n => n !== undefined) ?? [];
  const total = notes.reduce((sum, n) => sum + n.duration_tick, 0);
  const selectedNote = notes[noteIndex];
  const halfway = selectedNote ? selectedNote.start_tick + Math.floor(selectedNote.duration_tick / 2) : 0;
  const canSplit = selectedNote && secondsAt(state.score, halfway) - secondsAt(state.score, selectedNote.start_tick) >= .12 &&
    secondsAt(state.score, selectedNote.start_tick + selectedNote.duration_tick) - secondsAt(state.score, halfway) >= .12;
  const totalSeconds = notes.length ? secondsAt(state.score, notes[notes.length - 1].start_tick + notes[notes.length - 1].duration_tick) - secondsAt(state.score, notes[0].start_tick) : 0;
  const isV2 = state.composition?.arrangement === 'lyric-phrases-v2';
  const ids = unit?.note_ids.join(',') ?? '';
  const sourceUnit = state.composition?.v2?.lyrics_identity.units.find(u => u.line_index === unit?.line_index && u.unit_index === unit?.unit_index);
  const sourcePoints = Array.from(state.inputs.lyrics_text);
  const beforeLine = state.inputs.lyrics_text.split('\n').slice(0, unit?.line_index ?? 0).map(s => Array.from(s).length + 1).reduce((a, b) => a + b, 0);
  const lineLength = Array.from(state.inputs.lyrics_text.split('\n')[unit?.line_index ?? 0] ?? '').length;
  function invalidate() { setPreview(null); setError(''); cancel.current?.(); setPlaying(false); stop(); }
  useEffect(() => {
    setPreview(null); setError(''); setPlaying(false); setNoteIndex(0);
    setDuration(String(total / 480));
    setPitches(notes.map(n => n.pitch).join(', '));
    setWeights(notes.map(n => n.duration_tick).join(', '));
    select(unit?.note_ids ?? []);
    return () => { cancel.current?.(); };
  }, [state, index, ids]);
  useEffect(() => { select(unit?.note_ids ?? [], true); }, [index, unit?.line_index, unit?.unit_index]);
  if (!unit) return null;
  async function action(action: GroupParameters['action'], forcePreview = false) {
    invalidate();
    const params: GroupParameters = { line_index: unit.line_index, unit_index: unit.unit_index,
      action, time_mode: mode, note_index: noteIndex, interval, keep };
    if (mode === 'ripple') {
      const value = Number(duration) * 480;
      if (!Number.isInteger(value) || value <= 0) { setError('请填写可精确换算为tick的正时长。'); return; }
      params.duration_ticks = value;
    }
    if (action === 'set') {
      params.pitches = pitches.split(',').map(s => Number(s.trim()));
      params.weights = weights.split(',').map(s => Number(s.trim()));
      if (params.pitches.some(p => !Number.isInteger(p) || p < 48 || p > 84) ||
          params.weights.some(w => !Number.isInteger(w) || w <= 0) || params.weights.length !== params.pitches.length) {
        setError('音高请填48–84整数，比例请填相同数量的正整数。'); return;
      }
    }
    const id = nonce(), needPreview = forcePreview || mode === 'ripple';
    try {
      const impact = await edit(params, needPreview, id);
      if (needPreview) setPreview({ params, id, impact });
    } catch (failure) { setError(failure instanceof Error ? failure.message : '字组编辑失败。'); }
  }
  function listen(context: 'word' | 'word_group' | 'phrase') {
    invalidate(); setPlaying(true);
    cancel.current = practice(state, unit.line_index, time => { if (time === null) setPlaying(false); },
      { speed, loop: false, unitIndex: unit.unit_index, context });
  }
  return <article className="card lyric-group-editor" aria-label="歌词音符组编辑">
    <div className="card-heading"><h2>让这个字唱得更自然</h2><span className="tag">器乐即时试听</span></div>
    <label className="field">选择字词<select aria-label="选择歌词字词" value={Math.min(index, units.length - 1)} disabled={busy} onChange={e => { invalidate(); setIndex(Number(e.target.value)); }}>
      {units.map((u, i) => <option value={i} key={u.line_index + ':' + u.unit_index}>第{u.line_index + 1}行 · 第{u.unit_index + 1}字词 · {u.text}</option>)}
    </select></label>
    <p className="chosen-lyrics" data-testid="group-original-line">{sourceUnit
      ? <>{sourcePoints.slice(beforeLine, sourceUnit.source_span[0]).join('')}<mark>{sourcePoints.slice(sourceUnit.source_span[0], sourceUnit.source_span[1]).join('')}</mark>{sourcePoints.slice(sourceUnit.source_span[1], beforeLine + lineLength).join('')}</>
      : state.inputs.lyrics_text.split('\n')[unit.line_index]}</p>
    <p data-testid="group-summary">“{unit.text}”唱 {notes.length} 个音，共 {+(total / 480).toFixed(3)} 拍
      {notes.length ? '，' + (secondsAt(state.score, notes[notes.length - 1].start_tick + notes[notes.length - 1].duration_tick) - secondsAt(state.score, notes[0].start_tick)).toFixed(3) + ' 秒' : ''}。</p>
    <p>{notes.map(n => noteName(n.pitch) + ' · ' + +(n.duration_tick / 480).toFixed(3) + '拍').join(' → ')}</p>
    {notes.length !== unit.note_ids.length && <p role="alert">有音符已删除，请先修复歌词对应。</p>}
    <div className="inline-actions">
      <button disabled={busy || !notes.length} onClick={() => listen('word')}>听这个字</button>
      <button disabled={busy || !notes.length} onClick={() => listen('word_group')}>{isV2 ? '听前后词组' : '听这一行'}</button>
      {isV2 && <button disabled={busy} onClick={() => listen('phrase')}>听这个乐句</button>}
      <button disabled={!playing} onClick={invalidate}>停止字组试听</button>
      <label>试听速度<select aria-label="字组试听速度" value={speed} disabled={busy} onChange={e => { invalidate(); setSpeed(Number(e.target.value) as 0.75 | 1); }}><option value="1">1倍</option><option value="0.75">0.75倍</option></select></label>
    </div>
    <p className="hint">首音起字，后续音继续唱同一个字。试听使用乐器；修改后的草稿需要保存并重新生成演唱。</p>
    <details><summary>音符、比例和时长处理</summary>
      <label className="field">时长处理<select aria-label="字组时长模式" value={mode} disabled={busy} onChange={e => { invalidate(); setMode(e.target.value as 'fixed' | 'ripple'); }}>
        <option value="fixed">保持这个字总时长</option><option value="ripple" disabled={!isV2}>改变时长并顺延后文（v2受限）</option>
      </select></label>
      <p className="hint">{mode === 'fixed' ? '只调整这个字内部的音符和比例，其他字、和声和伴奏保持原位置。' : '先使用本句末留白；不足时移动之后整小节。手动伴奏、复杂速度或跨界音会阻止顺延。'}</p>
      <label className="field">这个字总时长（拍）<input aria-label="字组总时长拍数" type="number" step="0.25" min="0.25" value={duration} disabled={busy || mode === 'fixed'} onChange={e => { invalidate(); setDuration(e.target.value); }} /></label>
      <label className="field">在哪个音上添加转音<select aria-label="转音目标音符" disabled={busy} value={noteIndex} onChange={e => { invalidate(); setNoteIndex(Number(e.target.value)); }}>{notes.map((n, i) => <option key={n.id} value={i}>第{i + 1}音 · {noteName(n.pitch)}</option>)}</select></label>
      <label className="field">转音方向<select aria-label="转音方向" value={interval} disabled={busy} onChange={e => { invalidate(); setInterval(Number(e.target.value)); }}><option value="2">上行2半音</option><option value="-2">下行2半音</option><option value="0">相同音高</option></select></label>
      <label className="field">简化时保留<select aria-label="简化保留音" value={keep} disabled={busy} onChange={e => { invalidate(); setKeep(e.target.value as 'first' | 'last'); }}><option value="first">首音</option><option value="last">末音</option></select></label>
      <label className="field">音高（逗号分隔的MIDI编号）<input aria-label="字组音高" value={pitches} disabled={busy} onChange={e => { invalidate(); setPitches(e.target.value); }} /></label>
      <label className="field">音符时长比例（正整数）<input aria-label="字组时长比例" value={weights} disabled={busy} onChange={e => { invalidate(); setWeights(e.target.value); }} /></label>
      <button disabled={busy} onClick={() => void action('set', true)}>预览音符与时长调整</button>
    </details>
    <div className="inline-actions"><button disabled={busy || notes.length >= 12 || (mode === 'fixed' && !canSplit)} onClick={() => void action('add')}>添加转音</button><button disabled={busy || notes.length < 2 || (mode === 'fixed' && totalSeconds > 5.001)} onClick={() => void action('simplify')}>简化唱法</button></div>
    {mode === 'fixed' && !canSplit && <p className="hint">选中音符太短，不能在原总长内拆成两个至少0.12秒的音；可选择其他音，或在可用的顺延模式中加长。</p>}
    {mode === 'fixed' && totalSeconds > 5.001 && <p className="hint">合并后的单音会超过5秒，请先缩短再简化。</p>}
    {preview && <div className="render-notice" data-testid="group-impact">
      <p>本字变为 {preview.impact.note_count} 个音，共 {preview.impact.duration_ticks / 480} 拍；本句后面的字移动 {preview.impact.following_lyrics_delta_ticks / 480} 拍；之后歌曲移动 {preview.impact.later_music_bars} 小节。预计总长 {preview.impact.total_seconds.toFixed(3)} 秒。</p>
      <button className="primary" disabled={busy} onClick={() => void edit(preview.params, false, preview.id).then(() => setPreview(null)).catch(f => setError(f.message))}>应用本次字组预览</button>
    </div>}
    {error && <p role="alert">{error}</p>}
    <p className="hint">一次字组操作对应一次撤销；点击“保存歌曲”后写入新版本。</p>
  </article>;
}
