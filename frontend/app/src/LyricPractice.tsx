import { useEffect, useMemo, useRef, useState } from 'react';
import { lyricPractice } from './lyric-practice.mjs';
import { noteName, secondsAt } from './music.mjs';
import type { PracticePlayer, SongState } from './types';

export default function LyricPractice({ state, busy, practice }: { state: SongState; busy: boolean; practice: PracticePlayer }) {
  const [cursor, setCursor] = useState<number | null>(null);
  const [activeLine, setActiveLine] = useState<number | null>(null);
  const [speed, setSpeed] = useState<0.75 | 1>(1);
  const [loop, setLoop] = useState(false);
  const cancel = useRef<(() => void) | null>(null);
  const serial = useRef(0);
  const prepared = useMemo(() => {
    try { return { clip: lyricPractice(state), error: '' }; }
    catch (error) { return { clip: null, error: error instanceof Error ? error.message : '歌词对应需重新核对。' }; }
  }, [state]);
  useEffect(() => {
    setCursor(null); setActiveLine(null);
    return () => { serial.current++; cancel.current?.(); cancel.current = null; };
  }, [state]);
  const composition = state.composition;
  if (!composition?.lyrics_alignment.length) return null;
  const track = state.score.tracks.find(track => track.id === composition.melody_track_id);
  const grouped = [...new Set(composition.lyrics_alignment.map(unit => unit.line_index))];
  function play(line: number | null) {
    cancel.current?.(); const id = ++serial.current;
    setActiveLine(line); setCursor(-1);
    cancel.current = practice(state, line, seconds => { if (id === serial.current) { setCursor(seconds); if (seconds === null) setActiveLine(null); } }, { speed, loop: line !== null && loop });
  }
  function stop() { serial.current++; cancel.current?.(); cancel.current = null; setCursor(null); setActiveLine(null); }
  return <div className="lyric-alignment" data-testid="lyrics-alignment">
    <p className="muted">练唱跳过无词的前奏、间奏和尾奏；需要听完整器乐段落时，请使用歌曲或候选的完整试听。</p>
    <p className="muted">只播放所选歌词的主旋律，暂时跳过其他声部及静音/独奏设置，保持音高、音色和音量。慢速只影响练唱，不改歌曲或导出文件。原曲句前最多留0.3秒准备、末尾0.6秒尾音，慢速时同步延长。</p>
    <div className="practice-options"><label>练唱速度<select aria-label="练唱速度" disabled={busy} value={speed} onChange={event => { stop(); setSpeed(Number(event.target.value) as 0.75 | 1); }}><option value="1">1倍 · 原速</option><option value="0.75">0.75倍 · 慢速</option></select></label><label><input type="checkbox" aria-label="循环这一句" disabled={busy} checked={loop} onChange={event => { stop(); setLoop(event.target.checked); }} />循环这一句（点击“听这一句”生效）</label></div>
    <div className="inline-actions"><button disabled={busy || !prepared.clip} onClick={() => play(null)}>只听歌词主旋律</button><button disabled={cursor === null} onClick={stop}>停止试唱试听</button></div>
    {prepared.error && <p role="alert" className="render-notice">{prepared.error}</p>}
    {grouped.map(line => <div className={`aligned-line ${cursor !== null && (activeLine === null || activeLine === line) ? 'practicing' : ''}`} key={line}>
      <div className="lyric-line-heading"><b>{state.inputs.lyrics_text.split('\n')[line]}</b><button aria-label={`听第 ${line+1} 行歌词`} disabled={busy || !prepared.clip} onClick={() => play(line)}>听这一句</button></div>
      <div>{composition.lyrics_alignment.filter(unit => unit.line_index === line).map(unit => {
        const notes = unit.note_ids.map(id => track?.notes.find(note => note.id === id));
        const active = cursor !== null && !!prepared.clip?.spans.some(span => span.line_index === line && span.unit_index === unit.unit_index && span.start <= cursor && cursor < span.end);
        return <span className={`aligned-unit ${active ? 'active-word' : ''}`} data-testid="lyric-unit" data-active={active} aria-current={active ? 'true' : undefined} key={unit.unit_index}><strong>{unit.text}</strong><small>{notes.every(Boolean) ? notes.map(note => noteName(note!.pitch)).join(' · ') : '音符已改变，需重核'}</small><small>{notes[0] ? `${secondsAt(state.score, notes[0].start_tick).toFixed(1)} 秒` : '对应缺失'}</small></span>;
      })}</div>
    </div>)}
    <p className="muted" aria-live="polite">{cursor !== null && cursor >= 0 ? `跟随位置：原曲 ${cursor.toFixed(1)} 秒。` : '请照着器乐旋律亲自试唱；试听不会勾选人工审核。'}</p>
  </div>;
}
