import { useEffect, useRef, useState } from 'react';
import { request } from '../api';
import { AudioEngine } from '../audio';
import ScoreView from '../practice/ScoreView';
import type { Notation } from '../practice/types';
import './transcription.css';

type Note={id:string;pitch:number;onset:number;offset:number;velocity:number;hand:'auto'|'right'|'left'};
type Settings={bpm:number;numerator:number;denominator:number;first_downbeat:number;grid:number;key_fifths:number};
type Result={id:string;duration:number;clip_start:number;mode:string;notes:Note[];controls:unknown[];files?:Record<string,string>;boundary_report?:{clipped_notes:unknown[];outside_notes:unknown[];outside_controls:number}};
type Revision={id:string;result_id:string;notes:Note[];settings:Settings|null;notation_error:string|null;score:{notation:Notation;musicxml:string;report:{max_shift_ms:number;voices_per_hand?:{right:number;left:number}}}|null};
type Job={id:string;created_at:string;status:string;error:string|null;result_id:string|null;parameters:{start:number;end:number;mode:string}};
type Project={id:string;name:string;current_revision:string|null;source:{duration:number;codec:string;channels:number;sample_rate:number}};
type Detail={project:Project;jobs:Job[];results:Result[];revisions:Revision[]};
const defaults:Settings={bpm:120,numerator:4,denominator:4,first_downbeat:0,grid:120,key_fifths:0};
const activeStages=['queued','decoding','separating','transcribing','validating','cancelling'];
const stages:Record<string,string>={queued:'等待处理',decoding:'解码选段',separating:'分离钢琴',awaiting_review:'等待试听确认',transcribing:'转录音符',validating:'校验输出',succeeded:'处理完成',failed:'处理失败',cancelled:'已取消',cancelling:'正在取消',interrupted:'重启中断'};

export default function TranscriptionWorkspace({active}:{active:boolean}) {
  const [projects,setProjects]=useState<Project[]>([]),[detail,setDetail]=useState<Detail|null>(null);
  const [ready,setReady]=useState<{ready:boolean;error:string|null}|null>(null);
  const [start,setStart]=useState(0),[end,setEnd]=useState(15),[mode,setMode]=useState('solo');
  const [resultId,setResultId]=useState(''),[revisionId,setRevisionId]=useState('');
  const [notes,setNotes]=useState<Note[]>([]),[history,setHistory]=useState<Note[][]>([]),[future,setFuture]=useState<Note[][]>([]);
  const [settings,setSettings]=useState<Settings>(defaults),[dirty,setDirty]=useState(false);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
  const [position,setPosition]=useState(0),[audioKind,setAudioKind]=useState('original'),[selected,setSelected]=useState('');
  const [playing,setPlaying]=useState(false),[page,setPage]=useState(0);
  const audio=useRef<HTMLAudioElement>(null),sourceAudio=useRef<HTMLAudioElement>(null),engine=useRef(new AudioEngine()),serial=useRef(0),positionRef=useRef(0);
  const result=detail?.results.find(r=>r.id===resultId),revision=detail?.revisions.find(r=>r.id===revisionId);
  const latestJob=detail?.jobs.slice().sort((a,b)=>b.created_at.localeCompare(a.created_at));
  const pending=detail?.jobs.find(j=>activeStages.includes(j.status));
  const awaiting=detail?.jobs.find(j=>j.status==='awaiting_review');
  const duration=result?.duration??(awaiting?awaiting.parameters.end-awaiting.parameters.start:Math.max(.05,end-start));
  const prefix=detail?`/transcriptions/${detail.project.id}`:'';
  const file=(kind:string,id:string,name:string)=>`/api${prefix}/files/${kind}/${id}/${name}`;
  const audioUrl=result?file('results',result.id,audioKind==='piano'?'piano.wav':'clip.wav'):awaiting?file('jobs',awaiting.id,audioKind==='piano'?'piano.wav':'clip.wav'):'';
  const previewUrl=detail?`/api${prefix}/source`:'';
  const clipOffset=result?.clip_start??awaiting?.parameters.start??start;

  function locate(value:number){positionRef.current=value;setPosition(value);}
  function stop(){serial.current++;audio.current?.pause();sourceAudio.current?.pause();engine.current.stop();setPlaying(false);}
  async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}}
  async function list(){setProjects(await request<Project[]>('/transcriptions'));}
  function loadRevision(d:Detail,rid:string,vid=''){
    const r=d.results.find(r=>r.id===rid),v=d.revisions.find(v=>v.id===vid);
    stop();setResultId(rid);setRevisionId(vid);setNotes(structuredClone(v?.notes??r?.notes??[]));setSettings(v?.settings??defaults);setHistory([]);setFuture([]);setDirty(false);setPage(0);setSelected('');locate(0);
  }
  async function open(id:string){if(dirty&&!window.confirm('当前校对尚未保存，切换工程会放弃这些修改。继续吗？'))return;const d=await request<Detail>(`/transcriptions/${id}`);setDetail(d);setStart(0);setEnd(Math.min(15,d.project.source.duration));const v=d.revisions.find(v=>v.id===d.project.current_revision);loadRevision(d,v?.result_id??d.results.at(-1)?.id??'',v?.id);}
  useEffect(()=>{if(active)void action(async()=>{await list();setReady(await request('/transcriptions/resources'));});else stop();},[active]);
  useEffect(()=>{const cleanup=()=>stop();window.addEventListener('neonote-stop-audio',cleanup);return()=>window.removeEventListener('neonote-stop-audio',cleanup);},[]);
  useEffect(()=>()=>engine.current.dispose(),[]);
  useEffect(()=>{if(!active||!pending||!detail)return;let alive=true;const id=detail.project.id;const timer=setInterval(()=>{void request<Detail>(`/transcriptions/${id}`).then(d=>{if(!alive)return;setDetail(d);if(!resultId){const done=d.jobs.find(j=>j.id===pending.id);if(done?.result_id)loadRevision(d,done.result_id);}}).catch(e=>alive&&setError(String(e)));},1200);return()=>{alive=false;clearInterval(timer);};},[active,pending?.id,detail?.project.id,resultId]);
  function edit(next:Note[]){stop();setHistory(h=>[...h,structuredClone(notes)]);setFuture([]);setNotes(next);setDirty(true);}
  function changeSetting(key:keyof Settings,value:number){stop();setSettings(s=>({...s,[key]:value}));setDirty(true);}
  async function play(){
    stop();const ticket=serial.current;const from=positionRef.current;
    if(audioKind==='midi'){
      if(!notes.length)throw Error('尚无可试听的音符。');
      setPlaying(true);
      await engine.current.play(notes.filter(n=>n.offset>from).map(n=>({pitch:n.pitch,velocity:n.velocity,start:Math.max(0,n.onset-from),duration:n.offset-Math.max(n.onset,from),track:{family:'keyboard',program:0,volume:80}})),duration-from,t=>{if(ticket===serial.current)locate(from+t);},()=>{if(ticket===serial.current)setPlaying(false);});
    }else{
      const element=audio.current;if(!element)return;
      element.currentTime=(audioUrl?0:start)+from;
      await element.play();if(ticket!==serial.current)element.pause();else setPlaying(true);
    }
  }
  async function save(withScore:boolean){
    if(!result)throw Error('请选择完整转录结果。');if(revisionId && revisionId!==detail!.project.current_revision && !window.confirm('正在查看旧修订；保存将创建新修订并设为当前版本，继续吗？'))return;stop();
    const v=await request<Revision>(`${prefix}/revisions`,{method:'POST',body:JSON.stringify({base:detail!.project.current_revision,result_id:resultId,notes,settings:withScore?settings:null})});
    const d=await request<Detail>(prefix);setDetail(d);setProjects(p=>p.map(p=>p.id===d.project.id?d.project:p));loadRevision(d,resultId,v.id);setMessage(v.notation_error?`校对已保存，暂不能整理成谱：${v.notation_error}`:'修订已保存；下载与此版本绑定。');
  }
  const visible=notes.slice(page*50,page*50+50);
  return <main className="transcription-workspace"><header><h1>乐曲键盘扒谱</h1><p>从钢琴音频提取音符，试听校对后保存 MIDI 与五线谱。原曲和原始转录始终保留。</p></header>
    {error&&<p role="alert" className="transcription-error">{error}</p>}{message&&<p role="status">{message}</p>}
    <div className="transcription-layout"><aside className="practice-card"><h2>扒谱工程</h2><label>上传 MP3 / WAV<input type="file" accept=".mp3,.wav" disabled={busy} onChange={e=>{const f=e.target.files?.[0];if(!f)return;if(dirty&&!window.confirm('上传新工程会离开当前未保存校对，继续吗？'))return;void action(async()=>{if(f.size>100*1024*1024)throw Error('文件超过100 MiB。');const form=new FormData();form.append('file',f);const d=await request<Detail>('/transcriptions',{method:'POST',body:form});await list();setDetail(d);setStart(0);setEnd(Math.min(15,d.project.source.duration));loadRevision(d,'');});e.target.value='';}}/></label><p>最大100 MiB / 10分钟；每次处理最多60秒。</p>
      {projects.map(p=><button key={p.id} disabled={busy} aria-pressed={detail?.project.id===p.id} onClick={()=>void action(()=>open(p.id))}>{p.name}</button>)}
      {!ready?.ready&&<p>{ready?.error??'检查本地资源…'}</p>}
      <details><summary>模型与资源</summary><p>Transkun 2.0.1；Demucs 4.0.1 / htdemucs_6s 的 piano 输出；CPU 顺序处理。普通访问不会下载资源。当前分离样例有明显贝斯串音，混音能力仍为试验；需逐首试听确认。</p><p>复制到练习、复制为创作歌曲：后续开放。</p></details></aside>
    <div>{detail&&<><section className="practice-card"><h2>1. 选择片段</h2><p>{detail.project.name} · {detail.project.source.duration.toFixed(2)}秒 · {detail.project.source.codec} · {detail.project.source.channels}声道 · {detail.project.source.sample_rate}Hz</p>
      <div className="transcription-controls"><label>开始（秒）<input type="number" min="0" step="0.01" value={start} disabled={!!pending} onChange={e=>{stop();setStart(+e.target.value);locate(0);}}/></label><label>结束（秒）<input type="number" step="0.01" value={end} disabled={!!pending} onChange={e=>{stop();setEnd(+e.target.value);}}/></label><label>输入类型<select value={mode} onChange={e=>setMode(e.target.value)}><option value="solo">独奏钢琴：直接转录</option><option value="mix">混音（试验）：先分离钢琴并试听</option></select></label></div>
      <audio ref={sourceAudio} controls src={previewUrl} onPlay={()=>{engine.current.stop();audio.current?.pause();}} onTimeUpdate={()=>{if(sourceAudio.current && sourceAudio.current.currentTime>=end)sourceAudio.current.pause();}} />
      <button disabled={busy||!!pending} onClick={()=>{const el=document.querySelector<HTMLAudioElement>('.transcription-workspace section audio');if(el){stop();el.currentTime=start;void el.play().catch(e=>setError(String(e)));}}}>从选段起点试听原曲</button>
      <button disabled={busy||!!pending||!ready?.ready} onClick={()=>void action(async()=>{if(dirty&&!window.confirm('校对尚未保存，仍要开始新处理吗？'))return;stop();await request(`${prefix}/jobs`,{method:'POST',body:JSON.stringify({base:detail.project.current_revision,start,end,mode})});setDetail(await request(prefix));setMessage('新任务独立保存，已有结果不受影响。');})}>开始{mode==='mix'?'分离':'转录'}</button>
      <p>技术 MIDI 的120 BPM仅用于保存秒级演奏时间，不代表已识别曲速。</p>
    </section>
    <section className="practice-card"><h2>2. 处理与试听</h2>{latestJob?.map(j=><div key={j.id} className="transcription-job"><strong>{stages[j.status]??j.status}</strong> · {j.parameters.start.toFixed(2)}–{j.parameters.end.toFixed(2)}秒 {j.error&&<span role="alert">{j.error}</span>}{(activeStages.includes(j.status)||j.status==='awaiting_review')&&<button onClick={()=>void action(async()=>{await request(`${prefix}/jobs/${j.id}/cancel`,{method:'POST'});setDetail(await request(prefix));})}>取消</button>}{j.status==='awaiting_review'&&<><p>请在下方选择“分离钢琴”试听。若钢琴缺失或串音明显，请取消；不要把不合格分离当作键盘。</p><button onClick={()=>{if(dirty&&!window.confirm('校对尚未保存，切换到分离试听会放弃草稿，继续吗？'))return;stop();setResultId('');setRevisionId('');setNotes([]);setDirty(false);setAudioKind('piano');locate(0);}}>载入这次分离试听</button><button disabled={busy} onClick={()=>void action(async()=>{stop();await request(`${prefix}/jobs/${j.id}/continue`,{method:'POST',body:JSON.stringify({base:detail.project.current_revision})});setDetail(await request(prefix));})}>我已试听，继续转录</button></>}</div>)}
      <div className="transcription-controls"><label>完整结果<select value={resultId} onChange={e=>{if(dirty&&!window.confirm('放弃未保存校对？'))return;loadRevision(detail,e.target.value);}}><option value="">选择转录结果</option>{detail.results.map(r=><option key={r.id} value={r.id}>{r.id.slice(0,8)} · 原曲{r.clip_start.toFixed(2)}秒起</option>)}</select></label><label>已保存修订<select value={revisionId} onChange={e=>{if(dirty&&!window.confirm('放弃未保存校对？'))return;const v=detail.revisions.find(v=>v.id===e.target.value);loadRevision(detail,v?.result_id??resultId,v?.id);}}><option value="">原始转录 / 草稿</option>{detail.revisions.map((v,i)=><option value={v.id} key={v.id}>修订{i+1} · {v.id.slice(0,8)}</option>)}</select></label></div>
      {(result||awaiting)&&<><div className="transcription-controls"><label>对照声音<select value={audioKind} onChange={e=>{stop();setAudioKind(e.target.value);}}><option value="original">原始选段</option>{(result?.mode==='mix'||awaiting)&&<option value="piano">分离钢琴</option>}{result&&<option value="midi">MIDI / 当前校对（SoundFont）</option>}</select></label><button disabled={busy} onClick={()=>void action(play)}>{playing?'重新从定位点播放':'试听'}</button><button onClick={stop}>停止</button></div><input aria-label="片段播放位置" type="range" min="0" max={duration} step="0.01" value={Math.min(position,duration)} onChange={e=>{stop();locate(+e.target.value);}}/><p>片段 {position.toFixed(2)}秒 / {duration.toFixed(2)}秒 · 原曲 {(clipOffset+position).toFixed(2)}秒</p><audio ref={audio} src={audioUrl} hidden onTimeUpdate={()=>{if(audio.current&&!audio.current.paused){const t=audio.current.currentTime-(audioUrl?0:start);locate(t);if(t>=duration)stop();}}} onEnded={()=>{setPlaying(false);}}/></>}
      {result?.boundary_report && (result.boundary_report.clipped_notes.length>0||result.boundary_report.outside_notes.length>0||result.boundary_report.outside_controls>0) && <p role="status" data-testid="transcription-boundary-report">选段边界已处理：{result.boundary_report.clipped_notes.length}个持续音在片段末尾结束，{result.boundary_report.outside_notes.length}个完全位于片段外的补帧音符未纳入选段。{result.boundary_report.outside_controls>0&&<>另有{result.boundary_report.outside_controls}个片段外控制事件仅保留在原始输出中。</>}原始 MIDI 保留模型完整输出；试听、校对和选段 MIDI 使用片段内时间，不移动起音。</p>}{result&&<p><a href={file('results',result.id,'performance.mid')}>下载原始转录 MIDI</a>{result.files?.['clip.mid']&&<> · <a href={file('results',result.id,'clip.mid')}>下载选段 MIDI</a></>}{result.controls.length>0&&' · 原始MIDI保留踏板等控制事件；校对MIDI仅包含音符，暂不编辑踏板。'}</p>}
    </section>
    {result&&<><section className="practice-card"><h2>3. 校对音符</h2><p>{notes.length}个音符 · {dirty?'有未保存修改':'当前版本已载入'}。时间以片段起点为0，时长＝结束秒－开始秒。音域保持原值，不自动补音、移调或吸附音阶。</p><div className="transcription-controls"><button onClick={()=>edit([...notes,{id:crypto.randomUUID().replaceAll('-',''),pitch:60,onset:Math.min(position,duration-.1),offset:Math.min(duration,position+.5),velocity:80,hand:'auto'}])}>添加音符</button><button disabled={!history.length} onClick={()=>{stop();setFuture(f=>[notes,...f]);setNotes(history.at(-1)!);setHistory(h=>h.slice(0,-1));setDirty(true);}}>撤销</button><button disabled={!future.length} onClick={()=>{stop();setHistory(h=>[...h,notes]);setNotes(future[0]);setFuture(f=>f.slice(1));setDirty(true);}}>重做</button><button disabled={busy} onClick={()=>void action(()=>save(false))}>保存校对修订</button></div>
      <div className="transcription-table"><table><thead><tr><th>音高(MIDI)</th><th>开始秒</th><th>结束秒</th><th>力度</th><th>左右手</th><th>操作</th></tr></thead><tbody>{visible.map(n=><tr key={n.id} className={selected===n.id?'selected':''}>{(['pitch','onset','offset','velocity'] as const).map(key=><td key={key}><input aria-label={`${n.id}-${key}`} type="number" step={(key==='onset'||key==='offset')?0.01:1} value={n[key]} onFocus={()=>setSelected(n.id)} onChange={e=>edit(notes.map(row=>row.id===n.id?{...row,[key]:+e.target.value}:row))}/></td>)}<td><select aria-label="音符左右手" value={n.hand} onChange={e=>edit(notes.map(row=>row.id===n.id?{...row,hand:e.target.value as Note['hand']}:row))}><option value="auto">自动建议</option><option value="right">右手</option><option value="left">左手</option></select></td><td><button onClick={()=>{stop();locate(n.onset);setSelected(n.id);}}>定位</button><button onClick={()=>edit(notes.filter(row=>row.id!==n.id))}>删除</button></td></tr>)}</tbody></table></div><button disabled={!page} onClick={()=>setPage(p=>p-1)}>上一页</button><span> 第{page+1}页 </span><button disabled={(page+1)*50>=notes.length} onClick={()=>setPage(p=>p+1)}>下一页</button></section>
    <section className="practice-card"><h2>4. 确认节拍并整理五线谱</h2><p>以下参数由你确认，不是从音频可靠恢复的原谱信息。首版为恒速，复杂自由速度请保留演奏MIDI后在专业软件整理。</p><div className="transcription-controls"><label>速度 BPM<input type="number" value={settings.bpm} onChange={e=>changeSetting('bpm',+e.target.value)}/></label><label>拍号<select value={`${settings.numerator}/${settings.denominator}`} onChange={e=>{const [n,d]=e.target.value.split('/').map(Number);setSettings(s=>({...s,numerator:n,denominator:d}));setDirty(true);}}>{['2/4','3/4','4/4','6/8'].map(v=><option key={v}>{v}</option>)}</select></label><label>第一强拍（片段秒）<input type="number" step="0.01" value={settings.first_downbeat} onChange={e=>changeSetting('first_downbeat',+e.target.value)}/></label><label>量化<select value={settings.grid} onChange={e=>changeSetting('grid',+e.target.value)}>{[[0,'不量化（可能无法成谱）'],[60,'三十二分'],[120,'十六分'],[160,'八分三连音'],[240,'八分'],[480,'四分']].map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label><label>调号（降号负 / 升号正）<input type="number" min="-7" max="7" value={settings.key_fifths} onChange={e=>changeSetting('key_fifths',+e.target.value)}/></label></div><p>正数表示从片段起点到第一强拍的弱起长度；负数表示第一小节强拍早于片段。左右手按声部连续性和音区建议，可逐音调整；支持多声部复调，左右手和各音时长均保留。</p><button disabled={busy} onClick={()=>void action(()=>save(true))}>确认参数，保存并生成五线谱</button>
      {revision?.notation_error&&<p role="alert">{revision.notation_error}{notes.some(n=>revision.notation_error!.includes(n.id))&&<button onClick={()=>{const index=notes.findIndex(n=>revision.notation_error!.includes(n.id));setSelected(notes[index].id);setPage(Math.floor(index/50));locate(notes[index].onset);}}>定位问题音符</button>}</p>}{revision?.score&&<><p>{dirty?'下方是已保存修订的谱面，尚未包含当前草稿。':'下方谱面与已保存修订一致。'} 最大量化偏移 {revision.score.report.max_shift_ms.toFixed(1)}毫秒。{revision.score.report.voices_per_hand&&<> 右手{revision.score.report.voices_per_hand.right}个声部，左手{revision.score.report.voices_per_hand.left}个声部。</>}</p><ScoreView xml={revision.score.musicxml} notation={revision.score.notation} active={revision.score.notation.segments.filter(s=>s.source_id===selected).map(s=>s.id)} onSelect={id=>{const n=revision.score!.notation.segments.find(s=>s.id===id);if(n){setSelected(n.source_id);setPage(Math.max(0,Math.floor(notes.findIndex(row=>row.id===n.source_id)/50)));}}}/><a href={file('revisions',revision.id,'score.mid')}>下载整理 MIDI</a> · <a href={file('revisions',revision.id,'score.musicxml')}>下载 MusicXML</a></>}{revision&&<p><a href={file('revisions',revision.id,'performance.mid')}>下载此修订的演奏 MIDI</a> · 修订{revision.id.slice(0,8)}</p>}
    </section></>}</>}</div></div></main>;
}
