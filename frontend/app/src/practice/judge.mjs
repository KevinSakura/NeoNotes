export function secondsAt(tempos, tick) {
  let total=0, previous=0, tempo=500000;
  for (const item of tempos) { if (item.tick>tick) break; total+=(item.tick-previous)*tempo/480000000; previous=item.tick; tempo=item.tempo; }
  return total+(tick-previous)*tempo/480000000;
}
export function percentile(values, fraction) {
  if (!values.length) return null;
  const ordered=[...values].sort((a,b)=>a-b), index=(ordered.length-1)*fraction, low=Math.floor(index);
  return ordered[low]+(ordered[Math.ceil(index)]-ordered[low])*(index-low);
}
export function recognition(snapshot,events) {
  const targets=snapshot.targets.attacks,rules=snapshot.rules;
  let index=0,readyAt=0,pausedMs=0,mode='ready',current=null,waitRelease=false,pauseAt=null,readyMarked=false;
  const held=new Map(),attempts=[];
  function finish(at,interrupted=false) {
    if (!current) return;
    const expected=[...new Set(targets[index].notes.map(n=>n.pitch))].sort((a,b)=>a-b),actual=[...new Set(current.pitches)].sort((a,b)=>a-b);
    const correct=JSON.stringify(actual)===JSON.stringify(expected)&&current.simultaneous&&!interrupted;
    const record={...current,actual,expected,missing:expected.filter(p=>!actual.includes(p)),extra:actual.filter(p=>!expected.includes(p)),correct,interrupted,finished_at:at,final_correction_ms:Math.max(0,(correct?current.simultaneous_at:at)-readyAt-pausedMs)};
    delete record.pitches; attempts.push(record); current=null; waitRelease=!correct&&held.size>0;
    if (correct) {index++;readyAt=at;pausedMs=0;readyMarked=false;}
  }
  for (const e of events) {
    const {kind,at}=e;
    if (kind==='recognition_start') {if(attempts.length||index)throw Error('认音不能清空首次答案');mode='recognition';readyAt=at;}
    else if(kind==='target_ready'&&e.target_index===index&&!readyMarked){readyAt=at;readyMarked=true;}
    else if(kind==='recognition_resume'){if(pauseAt!==null)pausedMs+=at-pauseAt;pauseAt=null;mode='recognition';held.clear();waitRelease=false;}
    else if(kind==='pause'){if(mode==='recognition'){finish(at,true);pauseAt=at;}mode='paused';held.clear();waitRelease=false;}
    else if(kind==='segment_start'){if(index<targets.length)throw Error('全部认音正确后才能练节奏');mode='rhythm';}
    else if(kind==='note_up'){held.delete(e.instance);if(!held.size)waitRelease=false;}
    else if(kind==='note_down'){
      held.set(e.instance,e.pitch);
      if(mode!=='recognition'||index>=targets.length||waitRelease)continue;
      if(current&&at>current.started_at+rules.chord_ms){finish(current.started_at+rules.chord_ms);if(waitRelease||index>=targets.length)continue;}
      if(!current)current={target_index:index,target_id:targets[index].id,started_at:at,first_response_ms:Math.max(0,at-readyAt-pausedMs),pitches:[],simultaneous:false,simultaneous_at:null};
      current.pitches.push(e.pitch);
      const live=new Set(held.values());if(targets[index].notes.every(n=>live.has(n.pitch))){current.simultaneous=true;if(current.simultaneous_at===null)current.simultaneous_at=at;}
    }else if(kind==='recognition_commit'&&mode==='recognition'&&current&&at+1e-7>=current.started_at+rules.chord_ms)finish(at);
  }
  const grouped=targets.map((t,i)=>attempts.filter(a=>a.target_index===i)),firsts=grouped.filter(a=>a.length).map(a=>a[0]);
  const pitchAccuracy=rows=>{const tp=rows.reduce((total,a)=>total+a.actual.filter(p=>a.expected.includes(p)).length,0),denominator=tp+rows.reduce((total,a)=>total+a.missing.length+a.extra.length,0);return denominator?tp/denominator:null;};
  return {complete:index===targets.length,next_index:index,pending:current!==null,attempts,target_count:targets.length,answered:firsts.length,
    first_correct:firsts.filter(a=>a.correct).length,first_accuracy:firsts.length?firsts.filter(a=>a.correct).length/firsts.length:null,
    first_pitch_accuracy:pitchAccuracy(firsts),cumulative_pitch_accuracy:pitchAccuracy(attempts),
    first_response_median_ms:percentile(firsts.map(a=>a.first_response_ms),.5),first_response_p90_ms:percentile(firsts.map(a=>a.first_response_ms),.9),
    final_correction_ms:grouped.map(row=>row.find(a=>a.correct)?.final_correction_ms??null)};
}
export function rhythm(snapshot,events) {
  const {targets,rules}=snapshot,segments=[];let current=null;
  for(const e of events){if(e.kind==='segment_start'){if(current)throw Error('旧段尚未结束');current={start:e,events:[],end:null};segments.push(current);}else if(current){current.events.push(e);if(['pause','segment_end'].includes(e.kind)){current.end=e;current=null;}}}
  return segments.map((s,index)=>{
    const start=s.start,end=s.end,speed=start.speed,from=start.from_tick,origin=start.expected_start,offset=rules.calibration?.offset_ms??0;
    const complete=!!(end&&end.kind==='segment_end'&&end.completed===true),lastAt=end&&!complete&&end.last_valid_at!==undefined?Math.min(end.at,end.last_valid_at):(end??s.events.at(-1)??start).at,base=secondsAt(targets.tempos,from),expected=[];
    for(const a of targets.attacks){if(a.tick<from)continue;const when=origin+(secondsAt(targets.tempos,a.tick)-base)*1000/speed,local=[...targets.tempos].reverse().find(t=>t.tick<=a.tick).tempo/1000/speed,tolerance=Math.max(rules.onset_min_ms,Math.min(rules.onset_max_ms,local*rules.onset_quarter_ratio));for(const n of a.notes)expected.push({target_id:n.id,attack_id:a.id,pitch:n.pitch,when,tolerance,release:origin+(secondsAt(targets.tempos,n.end_tick)-base)*1000/speed,tie_chain_id:n.tie_chain_id});}
    const byPitch=new Map();for(const t of expected)byPitch.set(t.pitch,[...(byPitch.get(t.pitch)??[]),t]);
    const expectedEnd=origin+(secondsAt(targets.tempos,targets.end_tick)-base)*1000/speed;if(complete&&end.at+.001<expectedEnd)throw Error('时间轴未结束不能标记完成');
    const inputs=s.events.filter(e=>e.kind==='note_down'&&e.at<=lastAt),releases=new Map(s.events.filter(e=>e.kind==='note_up'&&(e.reason??'keyup')==='keyup'&&e.at<=lastAt).map(e=>[e.instance,e.at-offset])),used=new Set(),matched=[],missing=[];
    for(const [pitch,row] of byPitch)for(const [i,t] of row.entries()){
      const low=Math.max(t.when-rules.match_ms,i?(row[i-1].when+t.when)/2:-Infinity),high=Math.min(t.when+rules.match_ms,i+1<row.length?(row[i+1].when+t.when)/2:Infinity);
      const candidates=inputs.map((e,j)=>({e,j,d:Math.abs(e.at-offset-t.when)})).filter(x=>!used.has(x.j)&&x.e.pitch===pitch&&low<=x.e.at-offset&&x.e.at-offset<high).sort((a,b)=>a.d-b.d||a.j-b.j);
      if(candidates.length){const {e,j}=candidates[0];used.add(j);const delta=e.at-offset-t.when,limit=Math.min(t.tolerance,t.when-low,high-t.when),release=releases.get(e.instance),rd=release===undefined?null:release-t.release;
        const releaseLimit=Math.max(rules.release_min_ms,(t.release-t.when)*rules.release_ratio);
        matched.push({...t,input_index:j,actual:e.at-offset,delta_ms:delta,timing:Math.abs(delta)<=limit?'on_time':delta<0?'early':'late',release_delta_ms:rd,release_feedback:rd===null?'held_or_interrupted':rd< -releaseLimit?'early':rd>releaseLimit?'late':'within_tolerance',release_warning:rd!==null&&Math.abs(rd)>releaseLimit});
      }else if(complete||lastAt>high+20)missing.push(t);
    }
    const extras=inputs.flatMap((e,j)=>{if(used.has(j))return[];const actual=e.at-offset,silent=targets.silences.some(t=>origin+(secondsAt(targets.tempos,t.start_tick)-base)*1000/speed<=actual&&actual<origin+(secondsAt(targets.tempos,t.end_tick)-base)*1000/speed),nearest=[...expected].sort((a,b)=>Math.abs(a.when-actual)-Math.abs(b.when-actual))[0];return[{pitch:e.pitch,at:actual,kind:silent?'rest_error':'wrong_or_extra',nearest_target:nearest&&Math.abs(nearest.when-actual)<=rules.match_ms?nearest.target_id:null}];});
    const count=complete?expected.length:matched.length+missing.length,denominator=matched.length+extras.length+missing.length,onsetHits=matched.filter(m=>m.timing==='on_time').length,byId=new Map(matched.map(m=>[m.target_id,m]));
    const slurs=targets.slurs.flatMap(([a,b])=>{const first=byId.get(a),second=byId.get(b);if(!first||!second||first.release_delta_ms===null)return[];const gap=second.actual-first.release-first.release_delta_ms;return[{start:a,end:b,gap_ms:gap,warning:gap>rules.slur_gap_ms}];});
    const chords=targets.attacks.filter(a=>a.tick>=from&&a.notes.length>1).map(a=>{const members=a.notes.filter(n=>byId.has(n.id)).map(n=>byId.get(n.id)),spread=members.length>1?Math.max(...members.map(m=>m.actual))-Math.min(...members.map(m=>m.actual)):null;return{attack_id:a.id,matched:members.length,expected:a.notes.length,spread_ms:spread,complete:members.length===a.notes.length,together:spread!==null&&spread<=rules.chord_ms};});
    const accuracy=denominator?matched.length/denominator:null,onTime=count?onsetHits/count:null;
    return{index,from_tick:from,speed,complete,interrupted:!!(end&&!complete),interruption:end?.reason??null,matched,missed:missing,extras,slur_feedback:slurs,chord_feedback:chords,pitch_accuracy:accuracy,on_time_rate:onTime,onset_median_abs_ms:percentile(matched.map(m=>Math.abs(m.delta_ms)),.5),onset_p90_abs_ms:percentile(matched.map(m=>Math.abs(m.delta_ms)),.9),rest_errors:extras.filter(e=>e.kind==='rest_error').length,suggested_goal_met:complete&&accuracy!==null&&accuracy>=rules.suggested_pitch_goal&&onTime>=rules.suggested_timing_goal&&!extras.some(e=>e.kind==='rest_error')};
  });
}
export function replay(snapshot,events){
  const reading=recognition(snapshot,events),timing=rhythm(snapshot,events),last=events.filter(e=>['recognition_start','recognition_resume','pause','segment_start','segment_end'].includes(e.kind)).at(-1)?.kind;
  const status=timing.at(-1)?.complete?'completed':last==='pause'?'paused':last==='segment_start'?'rhythm':reading.complete?'recognition_complete':['recognition_start','recognition_resume'].includes(last)?'recognition':'ready';
  return{status,recognition:reading,rhythm:timing,pauses:events.filter(e=>e.kind==='pause'),event_count:events.length};
}
