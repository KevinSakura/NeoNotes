import test from 'node:test';
import assert from 'node:assert/strict';
import { lyricPractice } from '../src/lyric-practice.mjs';
import { playbackEvents } from '../src/music.mjs';

function fixture() {
  const track = { id: 'lead', family: 'keyboard', channel: 0, program: 4, bank_msb: 0, bank_lsb: 0, volume: 70, mute: true, solo: false, notes: [
    { id: 'a', pitch: 60, velocity: 90, start_tick: 0, duration_tick: 240 },
    { id: 'b', pitch: 62, velocity: 80, start_tick: 960, duration_tick: 240 },
    { id: 'c', pitch: 64, velocity: 75, start_tick: 1440, duration_tick: 480 },
  ] };
  return { inputs: { lyrics_text: '向\n前走', selected_lines: [0, 1] },
    score: { ppq: 480, end_tick: 2880, tempo_map: [{ tick: 0, microseconds_per_beat: 500000 }, { tick: 1200, microseconds_per_beat: 1000000 }], tracks: [track, { id: 'drum', family: 'drums', solo: true, notes: [] }] },
    composition: { melody_track_id: 'lead', lyrics_alignment: [
      { line_index: 0, unit_index: 0, text: '向', note_ids: ['a'] },
      { line_index: 1, unit_index: 0, text: '前', note_ids: ['b'] },
      { line_index: 1, unit_index: 1, text: '走', note_ids: ['c'] },
    ] },
  };
}

test('line practice seeks directly, retains tempo changes and does not alter saved mix', () => {
  const state = fixture(), before = structuredClone(state), clip = lyricPractice(state, 1);
  assert.equal(clip.offset, .7); assert.equal(clip.events.length, 2);
  assert.ok(Math.abs(clip.events[0].start-.3) < 1e-9);
  assert.equal(clip.events[0].duration, .25);
  assert.equal(clip.events[1].duration, 1);
  assert.equal(clip.spans[1].start, 1.75);
  assert.ok(Math.abs(clip.duration-2.65) < 1e-9);
  assert.ok(clip.events.every(event => event.track.id === 'lead' && event.track.program === 4 && event.track.volume === 70));
  assert.deepEqual(state, before);
});

test('one word can span multiple notes and unassigned lead notes are excluded', () => {
  const state = fixture();
  state.score.tracks[0].notes.push({ id: 'extra', pitch: 66, velocity: 50, start_tick: 2100, duration_tick: 100 });
  state.composition.lyrics_alignment[1].note_ids.push('c');
  state.composition.lyrics_alignment[2].note_ids = ['extra'];
  const clip = lyricPractice(state);
  assert.deepEqual(clip.spans.map(span => span.unit_index), [0, 0, 0, 1]);
  state.score.tracks[0].notes.push({ id: 'postlude', pitch: 67, velocity: 50, start_tick: 2500, duration_tick: 150 });
  assert.equal(lyricPractice(state).events.length, 4);
});

test('full playback retains instrumental passages while lyric practice follows only sung notes', () => {
  const state = fixture(), lead = state.score.tracks[0];
  lead.mute = false; state.score.tracks[1].solo = false;
  lead.notes.forEach(note => { note.start_tick += 480; });
  state.score.end_tick += 480;
  lead.notes.push(
    { id: 'intro', pitch: 67, velocity: 70, start_tick: 0, duration_tick: 120 },
    { id: 'interlude', pitch: 69, velocity: 70, start_tick: 960, duration_tick: 120 },
    { id: 'outro', pitch: 67, velocity: 70, start_tick: 2700, duration_tick: 150 },
  );
  const before = structuredClone(state), complete = playbackEvents(state.score), sung = lyricPractice(state);
  assert.equal(complete.length, 6); assert.equal(sung.events.length, 3);
  assert.deepEqual(sung.events.map(event => event.pitch), [60, 62, 64]);
  assert.deepEqual(complete.map(event => event.pitch), [67, 60, 69, 62, 64, 67]);
  assert.deepEqual(state, before);
});

test('missing, duplicate, reordered or changed lyric references cannot silently play', () => {
  for (const change of ['missing', 'duplicate', 'reverse', 'lyric', 'volume', 'polyphony']) {
    const state = fixture();
    if (change === 'missing') state.score.tracks[0].notes.pop();
    if (change === 'duplicate') state.composition.lyrics_alignment[2].note_ids = ['b'];
    if (change === 'reverse') state.composition.lyrics_alignment[1].note_ids = ['c'];
    if (change === 'lyric') state.inputs.lyrics_text = '换\n了词';
    if (change === 'volume') state.score.tracks[0].volume = 0;
    if (change === 'polyphony') state.score.tracks[0].notes[1].duration_tick = 1000;
    assert.throws(() => lyricPractice(state), Error, change);
  }
});

test('English word boundaries and selected-line changes require new alignment', () => {
  const state = fixture();
  state.inputs.lyrics_text = 'Go\nab c';
  ['Go', 'ab', 'c'].forEach((text, index) => { state.composition.lyrics_alignment[index].text = text; });
  assert.equal(lyricPractice(state).events.length, 3);
  state.inputs.lyrics_text = 'Go\na bc';
  assert.throws(() => lyricPractice(state), /字词已改变/);
  state.inputs.lyrics_text = 'Go\nab c'; state.inputs.selected_lines = [1];
  assert.throws(() => lyricPractice(state), /选段/);
});

test('slow practice scales wall time without transposing or changing tempo map, mix or words', () => {
  const state = fixture(), before = structuredClone(state);
  const normal = lyricPractice(state, 1), slow = lyricPractice(state, 1, { speed: .75 });
  assert.equal(slow.speed, .75);
  assert.equal(slow.offset, normal.offset);
  assert.deepEqual(slow.spans, normal.spans);
  for (let i=0; i<slow.events.length; i++) {
    assert.equal(slow.events[i].pitch, normal.events[i].pitch);
    assert.equal(slow.events[i].velocity, normal.events[i].velocity);
    assert.deepEqual(slow.events[i].track, normal.events[i].track);
    assert.equal(slow.events[i].duration, normal.events[i].duration/.75);
    assert.equal(slow.events[i].start, normal.events[i].start/.75);
    assert.equal(slow.offset + slow.events[i].start * slow.speed, normal.spans[i].start);
  }
  assert.equal(slow.duration, normal.duration/.75);
  assert.deepEqual(state, before);
});

test('unsupported practice speeds and empty line clips fail explicitly', () => {
  for (const speed of [0, -1, NaN, Infinity, .8, 2]) assert.throws(() => lyricPractice(fixture(), 0, {speed}), /速度/);
  assert.throws(() => lyricPractice(fixture(), 99), /没有/);
});
