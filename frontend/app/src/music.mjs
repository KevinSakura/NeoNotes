export const PPQ = 480;
export const KEY_CODES = ['KeyA', 'KeyW', 'KeyS', 'KeyE', 'KeyD', 'KeyF', 'KeyT', 'KeyG', 'KeyY', 'KeyH', 'KeyU', 'KeyJ', 'KeyK'];
export const DRUM_PITCHES = [36, 37, 38, 39, 42, 46, 44, 41, 45, 43, 47, 49, 51];
export const DRUM_NAMES = { 35: '底鼓', 36: '底鼓', 37: '边击', 38: '军鼓', 39: '拍手', 41: '低嗵鼓', 42: '闭踩镲', 43: '中嗵鼓', 44: '踏板踩镲', 45: '高嗵鼓', 46: '开踩镲', 47: '嗵鼓', 49: '吊镲', 51: '叮叮镲' };

/** Convert absolute ticks using every tempo change, not a single opening BPM. */
export function secondsAt(score, tick) {
  let seconds = 0, cursor = 0, tempo = score.tempo_map[0].microseconds_per_beat;
  for (const item of score.tempo_map.slice(1)) {
    if (item.tick > tick) break;
    seconds += (item.tick - cursor) * tempo / score.ppq / 1e6;
    cursor = item.tick; tempo = item.microseconds_per_beat;
  }
  return seconds + (tick - cursor) * tempo / score.ppq / 1e6;
}

export function tickAtSeconds(score, seconds) {
  let cursor = 0, elapsed = 0, tempo = score.tempo_map[0].microseconds_per_beat;
  for (const item of score.tempo_map.slice(1)) {
    const segment = (item.tick - cursor) * tempo / score.ppq / 1e6;
    if (elapsed + segment > seconds) break;
    elapsed += segment; cursor = item.tick; tempo = item.microseconds_per_beat;
  }
  return Math.round(cursor + (seconds - elapsed) * score.ppq * 1e6 / tempo);
}

export function selectedLines(lines, current, index, checked) {
  if (!checked) return current.filter(value => value < index);
  const selected = [...current, index], first = Math.min(...selected), last = Math.max(...selected);
  return lines.map((line, n) => line.trim() && n >= first && n <= last ? n : -1).filter(n => n >= 0);
}

/** @param {string} text @returns {number[]} */
export function fullLyricLines(text) {
  return text.split('\n').flatMap((line, index) => line.trim() ? [index] : []);
}

/** Chinese characters / Unicode words, matching the backend's lyric units. */
export function lyricUnits(text) {
  return text.match(/[\u3400-\u9fff]|[\p{L}\p{Nl}\p{No}]+(?:['’][\p{L}\p{Nl}\p{No}]+)*|\p{Nd}+/gu) ?? [];
}

/** Counts only explicitly selected lines; the stored text is not implicitly selected. */
export function lyricSelection(text, selected, full = false) {
  const lines = text.split('\n'), maxUnits = full ? 1024 : 256, maxLines = full ? 64 : 16;
  const invalid = selected.some(index => !Number.isInteger(index) || index < 0 || !lines[index]?.trim());
  const units = selected.reduce((count, index) => count + lyricUnits(lines[index] ?? '').length, 0);
  const emptyLine = selected.some(index => !lyricUnits(lines[index] ?? '').length);
  const error = invalid ? '歌词范围已失效，请重新选择。' : selected.length && emptyLine ? '有歌词行只有标点，没有可谱曲的字词，请去除该行。' : selected.length > maxLines || units > maxUnits ? `当前已选${selected.length}行、${units}个字词，${full ? '全文歌词' : '60秒短曲'}上限为${maxLines}行、${maxUnits}个字词。请缩短歌词。` : null;
  return { lines: selected.length, units, maxUnits, maxLines, error };
}

export function noteName(pitch, drums = false) {
  if (drums && DRUM_NAMES[pitch]) return DRUM_NAMES[pitch] + ` · ${pitch}`;
  return ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'][pitch % 12] + (Math.floor(pitch / 12) - 1);
}

export function audibleTracks(tracks) {
  const solo = tracks.some(track => track.solo);
  return tracks.filter(track => !track.mute && (!solo || track.solo));
}

export function playbackEvents(score) {
  return audibleTracks(score.tracks).flatMap(track => track.notes.map(note => ({
    pitch: note.pitch, velocity: note.velocity, track,
    start: secondsAt(score, note.start_tick),
    duration: secondsAt(score, note.start_tick + note.duration_tick) - secondsAt(score, note.start_tick),
  }))).sort((a, b) => a.start - b.start);
}
