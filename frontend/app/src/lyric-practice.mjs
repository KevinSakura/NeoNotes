import { lyricUnits, secondsAt } from './music.mjs';

/** Only referenced lead notes; offsets and highlighting use the original tempo map.
 * @param {any} state
 * @param {number | null} lineIndex
 * @param {{ speed?: number }} options
 */
export function lyricPractice(state, lineIndex = null, { speed = 1 } = {}) {
  if (speed !== 1 && speed !== .75) throw Error('请选择1倍或0.75倍练唱速度。');
  const composition = state.composition;
  if (!composition?.lyrics_alignment.length) throw Error('当前没有可试唱的歌词对应。');
  const track = state.score.tracks.find(item => item.id === composition.melody_track_id);
  if (!track || track.family !== 'keyboard') throw Error('歌词主旋律声部缺失，请恢复或重新生成。');
  if (track.volume <= 0) throw Error('主旋律音量为零，请调整音量后试唱。');
  const byId = new Map(track.notes.map(note => [note.id, note]));
  const used = new Set(), spans = [], events = [];
  let previousEnd = -1, previousLine = null;
  const alignment = composition.lyrics_alignment;
  const lines = state.inputs.lyrics_text.split('\n');
  const selected = state.inputs.selected_lines;
  const grouped = [...new Set(alignment.map(unit => unit.line_index))];
  // Match the backend's Chinese-character/Unicode-word units, including apostrophes.
  if (JSON.stringify(grouped) !== JSON.stringify(selected) || grouped.some(line => {
    const units = alignment.filter(unit => unit.line_index === line);
    return units.some((unit, index) => unit.unit_index !== index) || JSON.stringify(units.map(unit => unit.text)) !== JSON.stringify(lyricUnits(lines[line] ?? ''));
  })) throw Error('歌词选段或字词已改变，请重新生成后试唱。');
  const ordered = [...track.notes].sort((a, b) => a.start_tick - b.start_tick);
  if (ordered.some((note, index) => index && ordered[index-1].start_tick + ordered[index-1].duration_tick > note.start_tick + 1)) throw Error('歌词主旋律有同时发声的音符，请恢复单旋律。');
  for (const unit of alignment) {
    for (const id of unit.note_ids) {
      const note = byId.get(id);
      if (!note) throw Error('歌词对应的音符已删除，请到编辑页手动修复对应或恢复版本。');
      if (used.has(id)) throw Error('歌词重复使用同一个音符，请重新生成对应。');
      used.add(id);
      const start = secondsAt(state.score, note.start_tick), end = secondsAt(state.score, note.start_tick + note.duration_tick);
      if (start < previousEnd - 1e-7 || note.pitch < 48 || note.pitch > 84 || end-start < .12 || end-start > 5.001) throw Error('歌词音符的顺序、音域或长度需重新核对。');
      if (previousLine !== null && previousLine !== unit.line_index && start-previousEnd < .149) throw Error('歌词行间缺少换气位置，请恢复或重新生成。');
      previousEnd = end; previousLine = unit.line_index;
      if (lineIndex === null || lineIndex === unit.line_index) {
        spans.push({ line_index: unit.line_index, unit_index: unit.unit_index, start, end });
        events.push({ note_id: note.id, pitch: note.pitch, velocity: note.velocity, track, start, duration: end-start });
      }
    }
  }
  if (!events.length) throw Error('这一行没有可找到的歌词音符。');
  const offset = Math.max(0, Math.min(...events.map(event => event.start)) - .3);
  const duration = Math.max(...spans.map(span => span.end)) - offset + .6;
  return { events: events.map(event => ({ ...event, start: (event.start-offset)/speed, duration: event.duration/speed })), offset, duration: duration/speed, spans, speed };
}

/** Instrumental group preview; never changes the score or requests singing.
 * @param {any} state
 * @param {number} lineIndex
 * @param {number} unitIndex
 * @param {'word' | 'word_group' | 'phrase'} context
 * @param {{ speed?: number }} options
 */
export function lyricGroupPractice(state, lineIndex, unitIndex, context = 'word', { speed = 1 } = {}) {
  const checked = lyricPractice(state, null, { speed: 1 });
  const units = state.composition.lyrics_alignment;
  const uid = units.findIndex(u => u.line_index === lineIndex && u.unit_index === unitIndex);
  if (uid < 0) throw Error('这个字词已不在当前草稿中。');
  let selected = [uid];
  const layout = state.composition.v2?.phrase_layout.find(p => p.uids.includes(uid));
  if (context !== 'word') {
    if (!layout) selected = units.map((u, i) => u.line_index === lineIndex ? i : -1).filter(i => i >= 0);
    else if (context === 'phrase') selected = layout.uids;
    else {
      const group = layout.word_groups.findIndex(w => w.includes(uid));
      if (group < 0) throw Error('乐句词组资料与当前字不一致。');
      selected = layout.word_groups.slice(Math.max(0, group - 1), group + 2).flat();
    }
  }
  if (speed !== 1 && speed !== .75) throw Error('请选择1倍或0.75倍速度。');
  const ids = new Set(selected.flatMap(i => units[i].note_ids));
  const track = state.score.tracks.find(t => t.id === state.composition.melody_track_id);
  const chosen = track.notes.filter(n => ids.has(n.id));
  const start = Math.min(...chosen.map(n => secondsAt(state.score, n.start_tick)));
  const end = Math.max(...chosen.map(n => secondsAt(state.score, n.start_tick + n.duration_tick)));
  const offset = Math.max(0, start - .15);
  const events = checked.events.filter(e => ids.has(e.note_id)).map(e => ({
    ...e, start: (e.start + checked.offset - offset) / speed, duration: e.duration / speed,
  }));
  return { events, offset, duration: (end - offset + .4) / speed, speed,
    spans: checked.spans.filter(s => selected.some(i => units[i].line_index === s.line_index && units[i].unit_index === s.unit_index)) };
}
