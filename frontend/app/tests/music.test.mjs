import test from 'node:test';
import assert from 'node:assert/strict';
import { fullLyricLines, lyricSelection, lyricUnits, playbackEvents, secondsAt, selectedLines, tickAtSeconds } from '../src/music.mjs';

const score = { ppq: 480, tempo_map: [{ tick: 0, microseconds_per_beat: 500000 }, { tick: 960, microseconds_per_beat: 1000000 }] };

test('tempo changes preserve timing and duration of crossing notes', () => {
  assert.equal(secondsAt(score, 1440), 2);
  assert.equal(tickAtSeconds(score, 2), 1440);
  assert.equal(tickAtSeconds(score, 1), 960);
  const events = playbackEvents({ ...score, tracks: [{ mute: false, solo: false, notes: [{ pitch: 60, velocity: 90, start_tick: 480, duration_tick: 960 }] }] });
  assert.equal(events[0].start, .5); assert.equal(events[0].duration, 1.5);
});

test('raw timing converts at one-tick precision without beat-grid quantization', () => {
  const seconds = .1234567, tick = tickAtSeconds(score, seconds);
  assert.ok(Math.abs(secondsAt(score, tick) - seconds) <= .5 / 480);
  assert.notEqual(tick % 120, 0);
});

test('solo and mute select the audible tracks without changing notes', () => {
  const tracks = [
    { id: 'a', mute: false, solo: false, notes: [{ pitch: 60, velocity: 90, start_tick: 0, duration_tick: 480 }] },
    { id: 'b', mute: false, solo: true, notes: [{ pitch: 64, velocity: 80, start_tick: 0, duration_tick: 480 }] },
    { id: 'c', mute: true, solo: true, notes: [{ pitch: 67, velocity: 70, start_tick: 0, duration_tick: 480 }] },
  ];
  const original = structuredClone(tracks);
  assert.deepEqual(playbackEvents({ ...score, tracks }).map(event => event.pitch), [64]);
  assert.deepEqual(tracks, original);
});

test('continuous lyric selection crosses empty lines without dropping words', () => {
  const lines = ['第一行', '', '第二行', '第三行'];
  assert.deepEqual(selectedLines(lines, [0], 3, true), [0, 2, 3]);
  assert.deepEqual(selectedLines(lines, [0, 2, 3], 2, false), [0]);
});

test('lyric counters preserve Chinese characters, Unicode words and apostrophes', () => {
  assert.deepEqual(lyricUnits("你好，don't stop! 夜里42"), ['你', '好', "don't", 'stop', '夜', '里', '42']);
  const text = '词'.repeat(173);
  assert.equal(lyricSelection(text, [0]).units, 173);
  assert.equal(lyricSelection(text, [0]).error, null);
  assert.equal(lyricSelection(text, []).units, 0);
});

test('new lyric limits accept the boundary and show precise one-word-over diagnostics', () => {
  for (const [full, cap] of [[false, 256], [true, 1024]]) {
    assert.equal(lyricSelection('词'.repeat(cap), [0], full).error, null);
    assert.match(lyricSelection('词'.repeat(cap + 1), [0], full).error, new RegExp(String(cap + 1)));
  }
  assert.match(lyricSelection(Array(17).fill('词').join('\n'), Array.from({ length: 17 }, (_, i) => i)).error, /16行/);
  assert.match(lyricSelection('词', [1]).error, /失效/);
});

test('full lyric input preserves original row numbers and rejects punctuation-only rows', () => {
  const text = '追梦\n\nWe can fly\n明天';
  assert.deepEqual(fullLyricLines(text), [0, 2, 3]);
  assert.equal(lyricSelection(text, fullLyricLines(text), true).units, 7);
  const punctuation = '唱吧\n?!';
  assert.match(lyricSelection(punctuation, fullLyricLines(punctuation), true).error, /只有标点/);
});
