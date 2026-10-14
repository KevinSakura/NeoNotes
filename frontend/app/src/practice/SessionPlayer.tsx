import { useEffect, useRef, useState } from 'react';
import { AudioEngine } from '../audio';
import { request, download } from '../api';
import { noteName } from '../music.mjs';
import { PracticeKeyboard, KEYS, inputTime } from './keyboard.mjs';
import { PracticeTransport, audioAnchor, performanceAt, tapCalibration } from './transport.mjs';
import { replay, secondsAt } from './judge.mjs';
import { PracticeBuffer } from './save';
import type { Event, Revision, Session } from './types';

export default function SessionPlayer({revision,active,incoming,onHighlights,onSaved}: {revision:Revision;active:boolean;incoming?:Session;onHighlights:(ids:string[],correct:string[])=>void;onSaved?:()=>void}) {
  const [session,setSession]=useState<Session|null>(null),[result,setResult]=useState<any>(null),[phase,setPhase]=useState('idle');
  const [saveState,setSaveState]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false),[speed,setSpeed]=useState<.5|.75|1>(.75);
  const [keyState,setKeyState]=useState({octave:4,codes:[] as string[],pitches:[] as number[]}),[feedback,setFeedback]=useState(''),[position,setPosition]=useState('');
  const [selfTest,setSelfTest]=useState<any>({status:'not_tested',received:[]}),[verified,setVerified]=useState(false);
  const [calibration,setCalibration]=useState<any>({status:'uncalibrated',offset_ms:0}),[screenChord,setScreenChord]=useState<number[]>([]);
  const engine=useRef(new AudioEngine()),capture=useRef<HTMLDivElement>(null),buffer=useRef<PracticeBuffer|null>(null),machine=useRef<PracticeKeyboard|null>(null),transport=useRef<PracticeTransport|null>(null);
  const phaseRef=useRef('idle'),activeRef=useRef(active),epoch=useRef(0),base=useRef({page:performance.now(),offset:0}),sessionRef=useRef<Session|null>(null),resultRef=useRef<any>(null);
  const commit=useRef<ReturnType<typeof setTimeout>|null>(null),auxTimer=useRef<ReturnType<typeof setTimeout>|null>(null),frame=useRef(0),tickRef=useRef(revision.targets.start_tick),resumeTick=useRef(revision.targets.start_tick),lastHighlights=useRef('');
  const selfTrace=useRef<any[]>([]),calibrationRun=useRef<{expected:number[];actual:number[]}|null>(null),virtual=useRef(new Map<string,{pitch:number;stop:()=>void}>()),virtualSerial=useRef(0);
  activeRef.current=active;
  const savedCallback=useRef(onSaved),savedProgress=useRef('');savedCallback.current=onSaved;
  const now=()=>base.current.offset+performance.now()-base.current.page;
  const relative=(performanceTime:number)=>base.current.offset+performanceTime-base.current.page;
  function changePhase(next:string){phaseRef.current=next;setPhase(next);}
  function refreshResult(){if(!buffer.current)return;const value=replay(buffer.current.session.snapshot,buffer.current.events);resultRef.current=value;setResult(value);return value;}
  function emit(event:Event){
    if(!buffer.current)return;
    const recorded=now();buffer.current.push({...event,recorded_at:recorded,phase:phaseRef.current,...(event.kind==='note_down'?{resolved_at:recorded,sound_requested_at:recorded}:{} )});
    const value=refreshResult();
    if(event.kind==='note_down'&&phaseRef.current==='rhythm'){
      const timing=value?.rhythm.at(-1),actual=event.at-(buffer.current.session.snapshot.rules.calibration?.offset_ms??0);
      const match=timing&&[...timing.matched].reverse().find((m:any)=>m.pitch===event.pitch&&Math.abs(m.actual-actual)<1e-6);
      const extra=timing&&[...timing.extras].reverse().find((e:any)=>e.pitch===event.pitch&&Math.abs(e.at-actual)<1e-6);
      setFeedback(match?`${noteName(event.pitch)} 音对了，${match.timing==='on_time'?'节拍正确':match.timing==='early'?'稍早':'稍晚'}。`:extra?.kind==='rest_error'?'休止期间先不按键。':`${noteName(event.pitch)} 当前未匹配目标；继续跟随节拍。`);
    }
    if(event.kind==='note_down'&&phaseRef.current==='recognition'&&value?.recognition.pending&&!commit.current){
      commit.current=setTimeout(()=>{commit.current=null;if(phaseRef.current==='recognition'){emit({kind:'recognition_commit',at:now()});const state=resultRef.current;if(state.recognition.complete){machine.current?.clear(now(),'recognition_complete');changePhase('recognition_complete');setFeedback('全部目标已纠正完成，可以从片段开头开始节奏轮。');}else{const attempt=state.recognition.attempts.at(-1);setFeedback(attempt?.correct?'这个起音组已正确。':`本次还差${attempt?.missing.map((p:number)=>noteName(p)).join('、')||'无'}；额外音${attempt?.extra.map((p:number)=>noteName(p)).join('、')||'无'}。松开本次按键后再试。`);}void buffer.current?.flush();}},105);
    }
  }
  function stopVirtual(at:number){for(const [id,voice] of virtual.current){voice.stop();emit({kind:'note_up',at,pitch:voice.pitch,instance:id,reason:'cleanup'});}virtual.current.clear();}
  function safeResume(tick:number){
    let start=revision.targets.measures.find(m=>m.start_tick<=tick&&tick<m.end_tick)?.start_tick??revision.targets.start_tick;
    while(true){const crossed=revision.notation.notes.filter(n=>n.start_tick<start&&start<n.end_tick);if(!crossed.length)return start;start=Math.min(...crossed.map(n=>n.start_tick));}
  }
  function pause(reason='manual',lastValid?:number){
    epoch.current++;setLoading(false);if(commit.current){clearTimeout(commit.current);commit.current=null;}cancelAnimationFrame(frame.current);
    if(auxTimer.current){clearTimeout(auxTimer.current);auxTimer.current=null;}
    const previous=phaseRef.current,at=now();
    machine.current?.clear(at,reason);stopVirtual(at);
    if(['recognition','rhythm'].includes(previous)){
      let start=tickRef.current;
      if(previous==='rhythm'){
        start=safeResume(tickRef.current);
      }
      resumeTick.current=start;
      emit({kind:'pause',at,reason,resume_tick:start,...(lastValid!==undefined?{last_valid_at:lastValid}:{} )});
      changePhase('paused');void buffer.current?.flush();
    }else if(['self_test','calibration'].includes(previous)){changePhase(sessionRef.current?'paused':'idle');}
    transport.current?.stop();engine.current.stop();calibrationRun.current=null;
  }
  async function audioReady(){
    const serial=++epoch.current;setLoading(true);setError('');
    try{const context=await engine.current.practiceContext();if(serial!==epoch.current||!activeRef.current)return false;transport.current??=new PracticeTransport(context);return true;}
    catch(e){if(serial===epoch.current)setError(e instanceof Error?e.message:'音色加载失败');return false;}
    finally{if(serial===epoch.current)setLoading(false);}
  }
  async function attach(next:Session){
    const b=new PracticeBuffer(next,text=>{if(buffer.current!==b)return;setSaveState(text);if(text==='已保存'){const result=resultRef.current;const marker=`${next.snapshot.id}:${result?.recognition.next_index}:${result?.recognition.attempts.length}:${result?.rhythm.at(-1)?.index}:${result?.rhythm.at(-1)?.complete}`;if(marker!==savedProgress.current){savedProgress.current=marker;savedCallback.current?.();}}});await b.restore();
    buffer.current?.close();
    buffer.current=b;sessionRef.current=next;setSession(next);base.current={page:performance.now(),offset:(b.events.at(-1)?.recorded_at??b.events.at(-1)?.at??0)+1};
    refreshResult();return b;
  }
  async function startReading(){
    pause('new_session');if(!await audioReady())return;
    const serial=epoch.current;
    try{
      const next=await request<Session>('/practice/sessions',{method:'POST',body:JSON.stringify({material_id:revision.revision.material_id,revision_id:revision.revision.id,speed,calibration,self_test:selfTest})});
      if(serial!==epoch.current||!activeRef.current)return;
      const b=await attach(next);if(serial!==epoch.current||!activeRef.current)return;
      machine.current?.register(4,now());changePhase('recognition');emit({kind:'recognition_start',at:now()});capture.current?.focus();void b.flush();
    }catch(e){setError(String(e));}
  }
  async function continueReading(){if(!await audioReady())return;changePhase('recognition');emit({kind:'recognition_resume',at:now()});capture.current?.focus();}
  function tickAt(seconds:number){const target=secondsAt(revision.targets.tempos,transport.current!.running!.from)+seconds;let lo=revision.targets.start_tick,hi=revision.targets.end_tick;for(let i=0;i<20;i++){const mid=(lo+hi)/2;if(secondsAt(revision.targets.tempos,mid)<target)lo=mid;else hi=mid;}return Math.round((lo+hi)/2);}
  function animate(){
    if(phaseRef.current!=='rhythm')return;
    const position=transport.current?.position();if(!position)return;
    if(position.anomalous){pause('clock_interruption',relative(position.last_valid));setFeedback('计时中断：本段已保留，重新预备后再练。');return;}
    const tick=tickAt(position.seconds);tickRef.current=tick;
    setPosition(position.counting?'预备拍…':`原小节${revision.targets.measures.find(m=>m.start_tick<=tick&&tick<m.end_tick)?.source_number??revision.targets.measures.at(-1)?.source_number}`);
    const ids=revision.notation.segments.filter(s=>s.tick<=tick&&tick<s.tick+s.duration).map(s=>s.id);
    if(ids.join(',')!==lastHighlights.current){lastHighlights.current=ids.join(',');onHighlights(ids,[]);}
    if(position.ended){
      const at=now();machine.current?.clear(at,'timeline_end');stopVirtual(at);emit({kind:'segment_end',at,completed:true});transport.current?.stop();changePhase('completed');void buffer.current?.flush();return;
    }
    frame.current=requestAnimationFrame(animate);
  }
  async function startRhythm(resume=false){
    if(!resultRef.current?.recognition.complete)return;
    if(revision.targets.attacks.some(a=>a.reachability.level==='blocked')){setError('有键盘不能完成的目标，请调整独立练习版本。');return;}
    if(revision.targets.attacks.some(a=>a.reachability.level==='difficult')&&!verified){setError('混合八度需要先自检并确认设备能完成，或选择较简单的声部。');return;}
    if(!await audioReady())return;
    machine.current?.clear(now(),'rhythm_restart');stopVirtual(now());
    const from=resume?resumeTick.current:revision.targets.start_tick;tickRef.current=from;
    try{const clock=transport.current!.start(revision.targets,from,speed);changePhase('rhythm');emit({kind:'segment_start',at:now(),expected_start:relative(clock.expected_start),from_tick:from,speed,clock:{...clock.clock,start_audio:transport.current!.running!.startAt,end_audio:transport.current!.running!.endAt},calibration_status:calibration.status});capture.current?.focus();frame.current=requestAnimationFrame(animate);}catch(e){setError(String(e));}
  }
  async function preview(){pause('demonstration');if(!await audioReady())return;const start=secondsAt(revision.targets.tempos,revision.targets.start_tick);await engine.current.play(revision.notation.notes.map(n=>({pitch:n.pitch,velocity:n.velocity,start:secondsAt(revision.targets.tempos,n.start_tick)-start,duration:secondsAt(revision.targets.tempos,n.end_tick)-secondsAt(revision.targets.tempos,n.start_tick),track:{family:'keyboard',channel:14,program:0,volume:80}})),secondsAt(revision.targets.tempos,revision.targets.end_tick)-start,()=>{},()=>{});}
  async function beginSelfTest(){pause('self_test');if(!await audioReady())return;selfTrace.current=[];setVerified(false);setSelfTest({status:'observing',received:[]});changePhase('self_test');setFeedback('30秒多键自检：请逐个按s、e、l，再试s/f/h、按住Shift+d和空格+d；屏幕只显示浏览器收到的键。');capture.current?.focus();auxTimer.current=setTimeout(()=>{pause('self_test_timeout');setFeedback('自检已结束，请核对收到的组合，再确认设备。');},30000);}
  async function calibrate(){
    pause('calibration');if(!await audioReady())return;const ctx=await engine.current.practiceContext(),anchor=audioAnchor(ctx,performance.now()),first=ctx.currentTime+.6;
    const expected=Array.from({length:8},(_,i)=>performanceAt(anchor,first+i*.6));
    for(let i=0;i<8;i++)transport.current!.click(first+i*.6,i===0);
    calibrationRun.current={expected,actual:[]};changePhase('calibration');setFeedback('听8拍，每拍按一次s；估计包含个人反应，不能当作纯硬件延迟。');capture.current?.focus();
    auxTimer.current=setTimeout(()=>{pause('calibration_timeout');setFeedback('校准未收齐8次敲击，保留原校准状态；可重新开始。');},8000);
  }
  useEffect(()=>{
    machine.current=new PracticeKeyboard((e:Event)=>{if(e.kind==='help')setFeedback(e.message??e.reason);emit(e);},(pitch:number)=>engine.current.practiceVoice(pitch),setKeyState);
    const editable=(target:EventTarget|null)=>{const node=target as HTMLElement;return !!node&&(node.isContentEditable||!!node.closest('input,textarea,select,button,a,dialog,[inert]'));};
    const down=(event:KeyboardEvent)=>{
      if(!activeRef.current||!['recognition','rhythm','self_test','calibration'].includes(phaseRef.current)||editable(event.target)||event.ctrlKey||event.altKey||event.metaKey||event.isComposing)return;
      if(!Object.hasOwn(KEYS,event.code)&&!['ShiftLeft','ShiftRight','Space'].includes(event.code))return;
      event.preventDefault();const p=performance.now(),stamp=inputTime(event.timeStamp,p,performance.timeOrigin),at=relative(stamp.at);
      if(stamp.precision!=='event'||p-stamp.at>200){emit({kind:'raw_down',at:Math.max(0,at),code:event.code,precision:stamp.precision,raw_timestamp:event.timeStamp});pause('input_clock_interruption');return;}
      if(phaseRef.current==='calibration'&&event.code==='KeyS'&&!event.repeat){const run=calibrationRun.current;if(run){run.actual.push(stamp.at);if(run.actual.length===8){const result=tapCalibration(run.expected,run.actual);setCalibration(result);setFeedback(`敲击估计偏移${result.offset_ms.toFixed(1)}ms，离散${result.dispersion_ms.toFixed(1)}ms；包括个人反应。`);pause('calibration_end');}}return;}
      if(phaseRef.current==='self_test'&&!event.repeat){selfTrace.current.push({code:event.code,type:'down',at});setSelfTest({status:'observed',received:selfTrace.current.slice(-600)});}
      machine.current?.down(event.code,Math.max(0,at),event);
    };
    const up=(event:KeyboardEvent)=>{const p=performance.now(),stamp=inputTime(event.timeStamp,p,performance.timeOrigin),consumed=machine.current?.up(event.code,Math.max(0,relative(stamp.at)));if(phaseRef.current==='self_test'&&consumed){selfTrace.current.push({code:event.code,type:'up',at:relative(stamp.at)});setSelfTest({status:'observed',received:selfTrace.current.slice(-600)});}};
    const leave=()=>pause('blur'),hide=()=>{if(document.hidden)pause('background');},external=()=>pause('page_or_audio_change');
    const focus=(event:FocusEvent)=>{const node=event.target as HTMLElement;if(node?.closest('input,textarea,select,dialog,[contenteditable=true]'))pause('form_focus');};
    const unload=(event:BeforeUnloadEvent)=>{pause('page_close');if(buffer.current?.pending()){event.preventDefault();event.returnValue='';}};
    window.addEventListener('keydown',down);window.addEventListener('keyup',up);window.addEventListener('blur',leave);window.addEventListener('neonote-stop-audio',external);window.addEventListener('beforeunload',unload);document.addEventListener('visibilitychange',hide);document.addEventListener('focusin',focus);
    const timer=setInterval(()=>{if(['recognition','rhythm'].includes(phaseRef.current))emit({kind:'checkpoint',at:now(),tick:tickRef.current});void buffer.current?.flush();},1000);
    return()=>{pause('component_close');clearInterval(timer);window.removeEventListener('keydown',down);window.removeEventListener('keyup',up);window.removeEventListener('blur',leave);window.removeEventListener('neonote-stop-audio',external);window.removeEventListener('beforeunload',unload);document.removeEventListener('visibilitychange',hide);document.removeEventListener('focusin',focus);buffer.current?.close();engine.current.dispose();};
  },[]);
  useEffect(()=>{if(!active)pause('entry_change');},[active]);
  useEffect(()=>{if(incoming){void attach(incoming).then(()=>{const marker=buffer.current&&[...buffer.current.events].reverse().find(e=>e.kind==='pause'&&Number.isInteger(e.resume_tick)||e.kind==='checkpoint'&&e.phase==='rhythm'&&Number.isInteger(e.tick));resumeTick.current=safeResume(marker?.resume_tick??marker?.tick??revision.targets.start_tick);tickRef.current=resumeTick.current;changePhase('paused');emit({kind:'pause',at:now(),reason:'refresh_or_reopen',resume_tick:resumeTick.current});void buffer.current?.flush();}).catch(e=>setError(String(e)));}},[incoming?.snapshot.id]);
  useEffect(()=>{
    if(phase!=='recognition'||!result)return;const index=result.recognition.next_index,target=revision.targets.attacks[index];if(!target)return;
    onHighlights(target.notes.flatMap(n=>n.notation_ids),result.recognition.attempts.filter((a:any)=>a.correct).flatMap((a:any)=>revision.targets.attacks[a.target_index].notes.flatMap(n=>n.notation_ids)));
    const raf=requestAnimationFrame(()=>emit({kind:'target_ready',at:now(),target_index:index}));return()=>cancelAnimationFrame(raf);
  },[phase,result?.recognition.next_index]);
  function pointerDown(pitch:number,token:string){if(!['recognition','rhythm'].includes(phaseRef.current))return;const instance=`screen${++virtualSerial.current}:${pitch}`;const stop=engine.current.practiceVoice(pitch);virtual.current.set(instance,{pitch,stop});emit({kind:'note_down',at:now(),pitch,instance,source:'screen',pointer_token:token});return instance;}
  function pointerUp(pitch:number,token:string,instance?:string){if(instance&&virtual.current.has(instance)){virtual.current.get(instance)!.stop();virtual.current.delete(instance);emit({kind:'note_up',at:now(),pitch,instance,source:'screen',pointer_token:token});}}
  const pointers=useRef(new Map<number,{pitch:number;instance?:string}>());
  const current=result?.recognition.next_index??0,lastTiming=result?.rhythm.at(-1),percent=(v:number|null)=>v===null||v===undefined?'未作答':`${(v*100).toFixed(1)}%`;
  return <section className="practice-card" data-testid="practice-player"><h2>先认音，再练节奏</h2>{error&&<p role="alert">{error}</p>}
    <p data-testid="practice-save-state">{saveState||'尚未开始练习'}{saveState.startsWith('保存失败')&&<button onClick={()=>void buffer.current?.flush()}>重试保存</button>}</p>
    <div className="practice-actions"><button disabled={loading||['recognition','rhythm'].includes(phase)} onClick={()=>void startReading()}>开始新的认音轮</button><button disabled={loading||phase!=='paused'||result?.recognition.complete} onClick={()=>void continueReading()}>继续认音</button><button disabled={loading||!result?.recognition.complete||phase==='rhythm'} onClick={()=>void startRhythm()}>从头开始节奏轮</button><button disabled={loading||phase!=='paused'||!result?.rhythm.length} onClick={()=>void startRhythm(true)}>重新预备后续练</button><button onClick={()=>pause('manual')}>暂停 / 停止</button><button disabled={loading} onClick={()=>void preview()}>听一遍整理谱</button></div>
    <label>练习速度<select aria-label="节奏练习速度" value={speed} disabled={phase==='rhythm'} onChange={e=>setSpeed(Number(e.target.value) as .5|.75|1)}><option value="0.5">0.5倍</option><option value="0.75">0.75倍</option><option value="1">原速</option></select></label>
    <div ref={capture} tabIndex={0} className="practice-capture" aria-label="练习键盘捕获区" onClick={()=>capture.current?.focus()} data-testid="practice-capture" data-phase={phase}>
      <b>{phase==='recognition'?`认音 ${Math.min(current+1,revision.targets.attacks.length)}/${revision.targets.attacks.length}`:phase==='rhythm'?position:phase==='completed'?'节奏轮已结束':phase==='paused'?'已暂停':phase==='recognition_complete'?'认音全部完成':phase==='self_test'?'多键自检中':phase==='calibration'?'敲击校准中':'点击开始，再在此区域弹奏'}</b>
      <p>{feedback}</p><p data-testid="practice-key-state">当前基础八度{keyState.octave} · 收到的键：{keyState.codes.join('、')||'无'} · 发声：{keyState.pitches.map(p=>noteName(p)).join('、')||'无'}</p>
      <p>s d f g h j k l 对应 C D E F G A B 高C；e/r/y/u/i为黑键。单按Shift降档、空格升档；按住后弹音符为跨八度双音，组合不切档。</p>
    </div>
    <details><summary>屏幕琴键与替代操作</summary><p>触屏可多点。鼠标逐音适合探索；和弦认音可勾选后同时试听作答，记录会标明屏幕输入。节奏轮仍要求真实同时起音。</p>
      <div className="practice-screen-keys">{Array.from({length:13},(_,i)=>48+(keyState.octave-3)*12+i).map(p=><button key={p} onPointerDown={e=>{e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);pointers.current.set(e.pointerId,{pitch:p,instance:pointerDown(p,String(e.pointerId))});}} onPointerUp={e=>{const n=pointers.current.get(e.pointerId);if(n){pointerUp(n.pitch,String(e.pointerId),n.instance);pointers.current.delete(e.pointerId);}}} onPointerCancel={e=>{const n=pointers.current.get(e.pointerId);if(n)pointerUp(n.pitch,String(e.pointerId),n.instance);pointers.current.delete(e.pointerId);}}>{noteName(p)}</button>)}</div>
      <label>手动八度<select value={keyState.octave} onChange={e=>machine.current?.register(Number(e.target.value),now())}>{[3,4,5].map(o=><option key={o} value={o}>{o}</option>)}</select></label>
      <fieldset><legend>认音和弦选择</legend>{(revision.targets.attacks[current]?.notes??[]).map(n=><label key={n.id}><input type="checkbox" checked={screenChord.includes(n.pitch)} onChange={e=>setScreenChord(s=>e.target.checked?[...s,n.pitch]:s.filter(p=>p!==n.pitch))} />{noteName(n.pitch)}</label>)}</fieldset>
      <button disabled={phase!=='recognition'||!screenChord.length} onClick={()=>{const ids=screenChord.map(p=>({p,id:pointerDown(p,'chord')})),serial=epoch.current;setTimeout(()=>{if(serial===epoch.current)ids.forEach(({p,id})=>pointerUp(p,'chord',id));},180);}}>同时演奏所选和弦（认音辅助）</button>
    </details>
    <details><summary>多键自检与延迟校准</summary><button disabled={loading} onClick={()=>void beginSelfTest()}>开始多键自检</button><p>浏览器收到{selfTest.received.length}个自检事件。未上报的物理键无法由网页直接检测；可重试、更换键盘、选择简单声部、移调或使用屏幕操作。</p>
      <label><input type="checkbox" checked={verified} onChange={e=>{setVerified(e.target.checked);setSelfTest((s:any)=>({...s,user_confirmed:e.target.checked}));}} />我已自检并确认设备能完成这个片段的组合</label>
      <button disabled={loading} onClick={()=>void calibrate()}>听8拍并按s估计延迟</button><p>{calibration.status==='uncalibrated'?'未校准':`敲击估计偏移${calibration.offset_ms.toFixed(1)}ms，包含个人反应偏差`}；规则快照随新会话保存，当前练习不会自动调分。</p>
    </details>
    {result&&<div data-testid="practice-result"><h3>本次记录</h3><p>首次已答准确率：{percent(result.recognition.first_accuracy)}；最终正确 {result.recognition.next_index}/{result.recognition.target_count}；尝试{result.recognition.attempts.length}次。</p><p>上项按首次完整答对的起音组计；首次音高准确率{percent(result.recognition.first_pitch_accuracy)}，累计音高准确率{percent(result.recognition.cumulative_pitch_accuracy)}。首次反应中位数{result.recognition.first_response_median_ms?.toFixed(1)??'未作答'}ms。</p>
      {lastTiming&&<p>节奏音高准确率{percent(lastTiming.pitch_accuracy)}；准拍{percent(lastTiming.on_time_rate)}；漏音{lastTiming.missed.length}；额外/错音{lastTiming.extras.length}；休止误按{lastTiming.rest_errors}。{lastTiming.interrupted?'本段中断，保留已有作答。':lastTiming.complete?'本段完成。':'练习进行中。'}</p>}
      <p>松键、保持时值与圆滑表达分别提示，首版不加通关门槛。建议达标线：音高≥90%、准拍≥80%、休止误按0。</p>
      {lastTiming&&<p>匹配音起音偏差中位数{lastTiming.onset_median_abs_ms?.toFixed(1)??'未测量'}ms、P90 {lastTiming.onset_p90_abs_ms?.toFixed(1)??'未测量'}ms；{lastTiming.matched.filter((m:any)=>m.release_warning).length}音松键偏早或偏晚，{lastTiming.matched.filter((m:any)=>m.release_feedback==='held_or_interrupted').length}音没有有效松键测量。和弦齐奏{lastTiming.chord_feedback.filter((c:any)=>c.complete&&c.together).length}/{lastTiming.chord_feedback.length}组；{lastTiming.slur_feedback.filter((s:any)=>s.warning).length}处圆滑线有明显间隙。</p>}
      <details><summary>查看每次答案与节奏偏差</summary><table><thead><tr><th>目标</th><th>实际</th><th>缺少/额外</th><th>首次反应ms</th><th>结果</th></tr></thead><tbody>{result.recognition.attempts.map((a:any,i:number)=><tr key={i}><td>{a.target_index+1}</td><td>{a.actual.map((p:number)=>noteName(p)).join('/')}</td><td>{a.missing.map((p:number)=>noteName(p)).join('/')||'无'} / {a.extra.map((p:number)=>noteName(p)).join('/')||'无'}</td><td>{a.first_response_ms.toFixed(1)}</td><td>{a.correct?'正确':a.interrupted?'中断':'待纠正'}</td></tr>)}</tbody></table>{lastTiming&&<pre>{JSON.stringify({matched:lastTiming.matched,missed:lastTiming.missed,extras:lastTiming.extras,slur_feedback:lastTiming.slur_feedback},null,2)}</pre>}</details>
      <button onClick={()=>download(new Blob([JSON.stringify({snapshot:session?.snapshot,events:buffer.current?.events,result},null,2)],{type:'application/json'}),'neonote-practice-record.json')}>下载练习记录JSON</button>
    </div>}
  </section>;
}
