import { useEffect, useRef } from 'react';
import type { Note, Track, Score } from './types';
import { noteName } from './music.mjs';

export default function PianoRoll({ score, track, selected, onSelect, onAdd, highlighted = [] }: {
  score: Score; track: Track; selected: string | null;
  onSelect: (id: string) => void; onAdd: (pitch: number, start: number) => void;
  highlighted?: string[];
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const pitches = track.notes.map(note => note.pitch);
  const low = Math.max(0, Math.min(track.is_drum ? 35 : 48, ...pitches) - 1);
  const high = Math.min(127, Math.max(track.is_drum ? 59 : 84, ...pitches) + 1);
  const row = 18, beat = 36, label = 64;
  const beats = Math.ceil(score.end_tick / score.ppq), width = label + beats * beat, height = (high - low + 1) * row + 25;
  useEffect(() => {
    const container = scroll.current;
    if (!container) return;
    const center = pitches.length ? pitches.reduce((sum, pitch) => sum + pitch, 0) / pitches.length : track.is_drum ? 42 : 60;
    container.scrollTop = Math.max(0, 25 + (high - center) * row - container.clientHeight / 2);
  }, [track.id, high, track.notes.length === 0]);
  const chosen = track.notes.find(note => note.id === selected);
  useEffect(() => {
    const container = scroll.current;
    if (!container || !chosen) return;
    const x = label + chosen.start_tick / score.ppq * beat, y = 25 + (high - chosen.pitch) * row;
    if (x < container.scrollLeft + label || x > container.scrollLeft + container.clientWidth - 50) container.scrollLeft = Math.max(0, x - label - 70);
    if (y < container.scrollTop + 30 || y > container.scrollTop + container.clientHeight - 30) container.scrollTop = Math.max(0, y - container.clientHeight / 2);
  }, [selected, chosen?.start_tick, chosen?.pitch, high]);
  const add = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left - label, y = event.clientY - rect.top - 25;
    if (x < 0 || y < 0) return;
    const pitch = high - Math.floor(y / row);
    const start = Math.floor(x / beat * 4) / 4 * score.ppq;
    if (pitch < low || pitch > high || start >= score.end_tick) return;
    onAdd(pitch, start);
  };
  const noteLabel = (note: Note) => `${noteName(note.pitch, track.is_drum)}，第 ${+(note.start_tick / score.ppq).toFixed(3)} 拍，长度 ${+(note.duration_tick / score.ppq).toFixed(3)} 拍`;
  return <div className="roll-scroll" data-testid="roll-scroll" ref={scroll}>
    <svg className="roll" width={width} height={height} onClick={add} role="group" aria-label={`${track.name}音符时间轴`}>
      {Array.from({ length: high - low + 1 }, (_, i) => {
        const pitch = high - i;
        return <g key={pitch}>
          <rect x={0} y={25 + i * row} width={width} height={row} fill={[1, 3, 6, 8, 10].includes(pitch % 12) && !track.is_drum ? '#eff3ec' : '#f9fbf6'} />
          <text x={5} y={38 + i * row} className="pitch-label">{noteName(pitch, track.is_drum)}</text>
          <line x1={label} y1={25 + i * row} x2={width} y2={25 + i * row} stroke="#e3e9df" />
        </g>;
      })}
      {Array.from({ length: beats + 1 }, (_, i) => <g key={i}>
        <line x1={label + i * beat} y1={25} x2={label + i * beat} y2={height} stroke={i % 4 ? '#e3e9df' : '#bdcdb5'} />
        {i % 4 === 0 && <text x={label + i * beat + 3} y={16}>{i} 拍</text>}
      </g>)}
      {track.notes.map(note => <rect key={note.id} data-testid="roll-note"
        x={label + note.start_tick / score.ppq * beat + 1} y={25 + (high - note.pitch) * row + 2}
        width={Math.max(4, note.duration_tick / score.ppq * beat - 2)} height={row - 4} rx={3}
        fill="var(--track-color)" stroke={selected === note.id || highlighted.includes(note.id) ? '#163b2d' : 'none'} strokeWidth={highlighted.includes(note.id) ? 3 : 2}
        data-group-selected={highlighted.includes(note.id)}
        tabIndex={0} role="button" aria-label={noteLabel(note)}
        onClick={event => { event.stopPropagation(); onSelect(note.id); }}
        onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(note.id); } }}
      />)}
    </svg>
  </div>;
}
