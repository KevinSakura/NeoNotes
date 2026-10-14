import test from 'node:test';
import assert from 'node:assert/strict';
import { draftHash } from '../src/draft-hash.mjs';
import { lyricGroupPractice } from '../src/lyric-practice.mjs';

function fixture() {
  return {
    inputs: { lyrics_text: '向远方', selected_lines: [0] },
    score: { ppq: 480, end_tick: 2880, tempo_map: [{ tick: 0, microseconds_per_beat: 500000 }, { tick: 960, microseconds_per_beat: 750000 }],
      tracks: [{ id: 'lead', family: 'keyboard', volume: 80, program: 0, notes: [
        { id: 'a', pitch: 60, velocity: 80, start_tick: 0, duration_tick: 240 },
        { id: 'b', pitch: 62, velocity: 80, start_tick: 240, duration_tick: 240 },
        { id: 'c', pitch: 64, velocity: 80, start_tick: 720, duration_tick: 240 },
        { id: 'd', pitch: 65, velocity: 80, start_tick: 1200, duration_tick: 480 },
        { id: 'instrumental', pitch: 60, velocity: 80, start_tick: 2000, duration_tick: 480 },
      ] }] },
    composition: { melody_track_id: 'lead', lyrics_alignment: [
      { line_index: 0, unit_index: 0, text: '向', note_ids: ['a', 'b'] },
      { line_index: 0, unit_index: 1, text: '远', note_ids: ['c'] },
      { line_index: 0, unit_index: 2, text: '方', note_ids: ['d'] },
    ], v2: { phrase_layout: [{ uids: [0, 1, 2], word_groups: [[0], [1, 2]] }] } },
  };
}

test('canonical draft hash matches Python including fractional numbers, zero and supplementary characters', async () => {
  assert.equal(await draftHash({ schema_version: 1, n: 60, a: [.000001, true, -0], text: '重逢\n𠀀' }),
    '2788fc6298b18a36bbd1fd49b94334c7f3597f28985e4f14e6298dec431ad232');
});

test('word preview includes its entire melisma, respects tempo and leaves the score intact', () => {
  const state = fixture(), original = structuredClone(state);
  const clip = lyricGroupPractice(state, 0, 0, 'word');
  assert.deepEqual(clip.events.map(e => e.note_id), ['a', 'b']);
  assert.deepEqual(clip.events.map(e => e.pitch), [60, 62]);
  assert.equal(clip.events[1].start, .25);
  assert.deepEqual(state, original);
});

test('neighbour word groups and phrase exclude unbound instrumental notes', () => {
  const state = fixture();
  for (const context of ['word_group', 'phrase']) {
    const clip = lyricGroupPractice(state, 0, 1, context);
    assert.deepEqual(clip.events.map(e => e.note_id), ['a', 'b', 'c', 'd']);
    assert.ok(!clip.events.some(e => e.note_id === 'instrumental'));
  }
  const normal = lyricGroupPractice(state, 0, 2), slow = lyricGroupPractice(state, 0, 2, 'word', { speed: .75 });
  assert.equal(normal.events[0].pitch, slow.events[0].pitch);
  assert.equal(slow.duration, normal.duration / .75);
  assert.equal(slow.events[0].duration, normal.events[0].duration / .75);
});
