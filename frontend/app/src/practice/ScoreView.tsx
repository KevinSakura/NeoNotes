import { useEffect, useRef, useState } from 'react';
import { OpenSheetMusicDisplay, Pitch, type GraphicalNote, type VexFlowGraphicalNote } from 'opensheetmusicdisplay';
import type { Notation } from './types';

type Props = {xml: string; notation: Notation; active?: string[]; correct?: string[]; onSelect?: (id: string) => void; onReady?: (ok: boolean) => void};
/** Match semantic source objects, then attach our IDs to individual noteheads. SVG IDs are irrelevant. */
export default function ScoreView({xml, notation, active = [], correct = [], onSelect, onReady}: Props) {
  const root = useRef<HTMLDivElement>(null), instance = useRef<OpenSheetMusicDisplay | null>(null);
  const mapping = useRef(new Map<string, {g: GraphicalNote; heads: HTMLElement[]}>());
  const callbacks = useRef({onSelect, onReady}); callbacks.current = {onSelect, onReady};
  const colors = useRef({active, correct}); colors.current = {active, correct};
  const [error, setError] = useState(''), [ready, setReady] = useState(false);
  function highlight(follow = false) {
    const activeIds = new Set(colors.current.active), correctIds = new Set(colors.current.correct);
    for (const [id, {g, heads}] of mapping.current) {
      const color = activeIds.has(id) ? '#c77712' : correctIds.has(id) ? '#24745f' : '#263e38';
      g.setColor(color, {applyToNoteheads: false, applyToStem: false, applyToFlag: false, applyToModifiers: false, applyToTies: true});
      for (const head of heads) {
        head.setAttribute('data-active', String(activeIds.has(id)));
        head.style.fill = color;
        for (const glyph of head.querySelectorAll('path')) glyph.setAttribute('fill', color);
      }
    }
    if (follow) mapping.current.get(colors.current.active[0])?.heads[0]?.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  useEffect(() => {
    const element = root.current!; let alive = true, serial = 0;
    setReady(false); setError(''); callbacks.current.onReady?.(false);
    const osmd = new OpenSheetMusicDisplay(element, {backend: 'svg', autoResize: false, drawTitle: false, drawPartNames: false});
    instance.current = osmd;
    osmd.EngravingRules.RenderXMeasuresPerLineAkaSystem = 4;
    function render() {
      if (!alive || !osmd.IsReadyToRender()) return;
      try {
        osmd.render(); mapping.current.clear();
        const table = new Map<string, string[]>();
        for (const s of notation.segments) {
          const key = `${s.measure}:${s.staff}:${s.voice + (s.staff - 1) * 2}:${s.offset}:${s.pitch}`;
          table.set(key, [...(table.get(key) ?? []), s.id]);
        }
        for (const [mi, staves] of osmd.GraphicSheet.MeasureList.entries()) {
          for (const [si, measure] of staves.entries()) for (const entry of measure.staffEntries) for (const voice of entry.graphicalVoiceEntries) for (const graph of voice.notes) {
            const source = graph.sourceNote;
            if (!source.Pitch || source.isRest()) continue;
            const pitch = source.Pitch.FundamentalNote + source.Pitch.AccidentalHalfTones + (source.Pitch.Octave + Pitch.OctaveXmlDifference + 1) * 12;
            const offset = Math.round(source.ParentVoiceEntry.Timestamp.RealValue * 1920);
            const key = `${mi + 1}:${si + 1}:${source.ParentVoiceEntry.ParentVoice.VoiceId}:${offset}:${pitch}`;
            const ids = table.get(key);
            if (!ids || ids.length !== 1) throw Error(`谱面音符无法唯一映射：${key}。练习判定未启用。`);
            const id = ids.pop()!;
            const vex = graph as VexFlowGraphicalNote;
            const head = vex.getNoteheadSVGs()[vex.vfnoteIndex];
            const heads = head ? [head] : [];
            if (!heads.length || mapping.current.has(id)) throw Error('音头缺失或重复，练习判定未启用。');
            for (const head of heads) {
              if (head.hasAttribute('data-notation-id')) throw Error('和弦音头映射不独立。');
              head.setAttribute('data-notation-id', id); head.setAttribute('data-pitch', String(pitch));
              head.setAttribute('role', 'button'); head.setAttribute('tabindex', '0'); head.setAttribute('aria-label', `音高${pitch}，原小节${notation.measures[mi].source_number}`);
              head.onclick = e => { e.stopPropagation(); callbacks.current.onSelect?.(id); };
              head.onkeydown = e => { if (e.key === 'Enter') { e.stopPropagation(); callbacks.current.onSelect?.(id); } };
            }
            mapping.current.set(id, {g: graph, heads});
          }
        }
        if (mapping.current.size !== notation.segments.length) throw Error(`只映射${mapping.current.size}/${notation.segments.length}个音头，练习判定未启用。`);
        element.dataset.mappingCount = String(mapping.current.size);
        setReady(true); setError(''); callbacks.current.onReady?.(true); highlight();
      } catch (failure) { setError(failure instanceof Error ? failure.message : '谱面渲染失败'); setReady(false); callbacks.current.onReady?.(false); }
    }
    void osmd.load(xml).then(() => { if (alive) render(); }).catch(f => { if (alive) setError(String(f)); });
    let width = 0;
    const observer = new ResizeObserver(entries => {
      const next = entries[0]?.contentRect.width ?? 0;
      if (next && Math.abs(next - width) > 5) { width = next; cancelAnimationFrame(serial); serial = requestAnimationFrame(render); }
    });
    observer.observe(element);
    return () => { alive = false; observer.disconnect(); cancelAnimationFrame(serial); mapping.current.clear(); osmd.clear(); instance.current = null; };
  }, [xml, notation]);
  useEffect(() => { highlight(true); }, [active.join(','), correct.join(','), ready]);
  return <div className="practice-score-shell"><div ref={root} className="practice-score" data-testid="practice-score" />{error && <p role="alert">{error}</p>}<small>{ready ? '五线谱音符映射已核对' : '正在排版五线谱…'}</small></div>;
}
