import { useEffect, useState } from 'react';
import { noteName, secondsAt } from './music.mjs';
import type { Assessment, SongState } from './types';

export default function LyricRepair({ state, busy, bind }: {
  state: SongState; busy: boolean;
  bind: (line: number, unit: number, ids: string[]) => Promise<Assessment | null>;
}) {
  const units = state.composition?.lyrics_alignment ?? [];
  const [target, setTarget] = useState('');
  const [chosen, setChosen] = useState<string[]>([]);
  const unit = units.find(unit => `${unit.line_index}:${unit.unit_index}` === target);
  const track = state.score.tracks.find(track => track.id === state.composition?.melody_track_id);
  const ordered = [...(track?.notes ?? [])].sort((a, b) => a.start_tick-b.start_tick);
  useEffect(() => {
    setChosen(unit?.note_ids.filter(id => ordered.some(note => note.id === id)) ?? []);
  }, [state, target]);
  if (!units.length) return null;
  const byId = new Map(ordered.map(note => [note.id, note]));
  const owners = new Map(units.flatMap(item => item.note_ids.map(id => [id, item] as const)));
  return <article className="card lyric-repair" data-testid="lyric-repair">
    <div className="card-heading"><h2>手动修复歌词对应</h2><span className="tag">不调用模型</span></div>
    <p className="muted">先选字词，再勾选已有键盘主旋律音符，最后确认绑定；一字可选多个音。这里不改歌词或音符，修改仍须保存，可撤销。错误对应不会通过审核。</p>
    <div className="repair-words">{units.map(item => <button key={`${item.line_index}:${item.unit_index}`} disabled={busy} aria-pressed={item === unit} aria-label={`选择第${item.line_index+1}行第${item.unit_index+1}个字词 ${item.text}`} data-testid="repair-word" onClick={() => setTarget(`${item.line_index}:${item.unit_index}`)}>{item.text}<small>第{item.line_index+1}行 · {item.note_ids.some(id => !byId.has(id)) ? '对应缺失' : `${item.note_ids.length}音`}</small></button>)}</div>
    {unit ? <>
      <p data-testid="repair-original">正在修复“{unit.text}”。原对应：{unit.note_ids.map(id => byId.has(id) ? `${noteName(byId.get(id)!.pitch)} · ${secondsAt(state.score, byId.get(id)!.start_tick).toFixed(2)}秒` : '已删除的音符').join('、')}。</p>
      <p className="muted">按时间显示音符；已用于其他字词的音符也会标示，不能重复绑定。若需替代音符，先在上方时间轴添加并调整。</p>
      <div className="repair-notes" role="group" aria-label="选择对应的主旋律音符">{ordered.map(note => {
        const owner = owners.get(note.id);
        return <label key={note.id}><input type="checkbox" disabled={busy} checked={chosen.includes(note.id)} data-note-id={note.id} aria-label={`绑定音符 ${noteName(note.pitch)} ${secondsAt(state.score,note.start_tick).toFixed(2)}秒`} onChange={event => {
          const ids = event.target.checked ? [...chosen, note.id] : chosen.filter(id => id !== note.id);
          setChosen(ordered.filter(note => ids.includes(note.id)).map(note => note.id));
        }} /><span><b>{noteName(note.pitch)}</b> · {secondsAt(state.score,note.start_tick).toFixed(2)}秒 · 长{(secondsAt(state.score,note.start_tick+note.duration_tick)-secondsAt(state.score,note.start_tick)).toFixed(2)}秒{owner && owner !== unit && <small>已用于“{owner.text}”（第{owner.line_index+1}行）</small>}</span></label>;
      })}</div>
      <p data-testid="repair-selection">将按演唱顺序绑定：{chosen.length ? chosen.map(id => byId.has(id) ? noteName(byId.get(id)!.pitch) : '音符已删除').join(' → ') : '尚未选择音符'}（{chosen.length}/12）。</p>
      <button className="primary" disabled={busy || !chosen.length || chosen.length > 12} onClick={() => void bind(unit.line_index, unit.unit_index, chosen)}>确认绑定到这个字词</button>
    </> : <p className="muted">选择上方一个字词查看原对应并开始修复。</p>}
  </article>;
}
