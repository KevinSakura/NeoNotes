import { useState } from 'react';
import type { UnitCatalog } from './types';

export default function UnitLibrary({catalog,records,busy,onChoose}:{catalog:UnitCatalog;records:any[];busy:boolean;onChoose:(id:string)=>void}) {
  const [selected,setSelected]=useState(catalog.units[0]?.id??'');
  const unit=catalog.units.find(u=>u.id===selected)??catalog.units[0];
  if(!unit)return <p role="alert">本地基础单元为空，请检查教材资源。</p>;
  function progress(exercise:typeof unit.exercises[number]){
    const matching=records.filter(r=>r.builtin?.exercise_id===exercise.exercise_id&&r.builtin.artifact_hash===exercise.artifact_hash);
    return {recognition:matching.some(r=>r.result.recognition.complete),
      rhythm:matching.some(r=>r.result.rhythm.some((t:any)=>t.complete&&t.from_tick===r.builtin.start_tick)),
      goal:matching.some(r=>r.result.rhythm.some((t:any)=>t.complete&&t.from_tick===r.builtin.start_tick&&t.suggested_goal_met))};
  }
  return <section className="practice-card" data-testid="practice-units">
    <h2>本地基础单元</h2><p>从这里选一条短谱，核对后开始认音，再跟节拍练习。无需导入 MIDI 或先创作。</p>
    <nav className="practice-unit-nav" aria-label="选择基础单元">{catalog.units.map(u=><button key={u.id} disabled={busy} aria-pressed={u.id===unit.id} onClick={()=>{window.dispatchEvent(new CustomEvent('neonote-stop-audio'));setSelected(u.id);}}>{u.title}</button>)}</nav>
    <p>{unit.description}</p>
    <div className="practice-unit-grid">{unit.exercises.map(exercise=>{const status=progress(exercise);return <article key={exercise.exercise_id} data-testid={'unit-'+exercise.exercise_id}>
      <h3>{exercise.exercise_title}</h3><p>{exercise.goal}</p><small>{exercise.bars}小节 · 4/4 · {exercise.bpm}BPM</small>
      <p className="practice-unit-progress">{status.recognition?'认音已完成':'认音未完成'} · {status.rhythm?'完整节奏轮已练':'节奏待练'}{status.goal?' · 达到建议标准':''}</p>
      <button className="primary" disabled={busy} aria-label={'查看练习：'+exercise.exercise_title} onClick={()=>onChoose(exercise.exercise_id)}>查看练习谱 →</button>
    </article>;})}</div><small>可以自由选择单元。错误和纠正都会记录；单元状态只统计这一版固定谱的记录。</small>
  </section>;
}
