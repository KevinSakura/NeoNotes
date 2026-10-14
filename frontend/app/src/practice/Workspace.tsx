import { useEffect, useRef, useState } from 'react';
import { request } from '../api';
import { AudioEngine } from '../audio';
import { noteName } from '../music.mjs';
import { secondsAt } from './judge.mjs';
import ScoreView from './ScoreView';
import SessionPlayer from './SessionPlayer';
import UnitLibrary from './UnitLibrary';
import type { Material, Prepared, Revision, Session, UnitCatalog } from './types';
import './practice.css';

export default function Workspace({active}: {active: boolean}) {
  const [materials, setMaterials] = useState<any[]>([]), [material, setMaterial] = useState<Material | null>(null);
  const [preview, setPreview] = useState<Prepared | null>(null), [revision, setRevision] = useState<Revision | null>(null);
  const [settings, setSettings] = useState({track_id: '', start_measure: 1, end_measure: 4, grid: 120, clef: 'treble', transpose: 0,
    pickup_ticks: 0, key_fifths: null as number | null, voices: [] as number[], acknowledge_controls: false,
    acknowledge_large_quantization: false, expand_boundaries: false, slurs: [] as string[][]});
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [mapped, setMapped] = useState(false), [selected, setSelected] = useState<string[]>([]);
  const [correct,setCorrect]=useState<string[]>([]),[records,setRecords]=useState<any[]>([]),[incoming,setIncoming]=useState<Session|undefined>(undefined);
  const [songs,setSongs]=useState<any[]>([]),[songId,setSongId]=useState('');
  const [voiceOptions,setVoiceOptions]=useState<any[]>([]),[slurStart,setSlurStart]=useState(''),[slurEnd,setSlurEnd]=useState('');
  const [units,setUnits]=useState<UnitCatalog|null>(null),[unitError,setUnitError]=useState('');
  const recordsRequest=useRef(0);
  const previewAudio=useRef(new AudioEngine());
  async function action(task: () => Promise<void>) { setBusy(true); setError(''); try { await task(); } catch (e) { setError(e instanceof Error ? e.message : '操作失败'); } finally { setBusy(false); } }
  async function refreshRecords(){const serial=++recordsRequest.current;const next=(await request<{items:any[]}>('/practice/sessions')).items;if(serial===recordsRequest.current)setRecords(next);}
  async function refresh() { setMaterials((await request<{items: any[]}>('/practice/materials')).items);await refreshRecords();setSongs((await request<{items:any[]}>('/projects')).items); }
  useEffect(() => { if (active) void action(refresh); }, [active]);
  useEffect(()=>{void request<UnitCatalog>('/practice/units').then(setUnits).catch(e=>setUnitError(e instanceof Error?e.message:'本地单元读取失败'));},[]);
  useEffect(()=>{if(!active)previewAudio.current.stop();},[active]);
  useEffect(()=>{const stop=()=>previewAudio.current.stop();window.addEventListener('neonote-stop-audio',stop);return()=>{window.removeEventListener('neonote-stop-audio',stop);previewAudio.current.dispose();};},[]);
  function load(next: Material) {
    previewAudio.current.stop();setMaterial(next); setPreview(null); setRevision(null); setMapped(false); setSelected([]);setCorrect([]);setIncoming(undefined);setVoiceOptions([]);
    setSettings(s => ({...s, track_id: '', start_measure: 1, end_measure: 4, voices: [], slurs: []}));
  }
  function change(value: Partial<typeof settings>) { previewAudio.current.stop();setSettings(s => ({...s, ...value})); setPreview(null); setRevision(null); setMapped(false);setIncoming(undefined);if(value.track_id!==undefined||value.grid!==undefined)setVoiceOptions([]); }
  async function importFile(file: File) { const form = new FormData(); form.set('file', file); load(await request<Material>('/practice/materials', {method: 'POST', body: form})); await refresh(); }
  const score = revision ?? (preview?.ready ? preview : null);
  const builtin=revision?revision.revision.builtin:preview?.builtin;
  async function chooseUnit(id:string){
    window.dispatchEvent(new CustomEvent('neonote-stop-audio'));
    await action(async()=>{const next=await request<{material:Material;settings:typeof settings;preview:Prepared;revision:Revision|null}>(`/practice/units/${id}/prepare`,{method:'POST',body:'{}'});load(next.material);setSettings(next.settings);setPreview(next.preview);setRevision(next.revision);await refresh();});
  }
  async function listen(original:boolean){
    if(!score||!material)return;
    window.dispatchEvent(new CustomEvent('neonote-stop-audio',{detail:'practice-preview'}));
    const chosen=new Set(score.notation.notes.map(n=>n.id)),track=material.parsed.tracks.find((t:any)=>t.id===settings.track_id);
    const notes=original?track.notes.filter((n:any)=>chosen.has(n.id)):score.notation.notes;
    const start=secondsAt(material.parsed.tempos,score.notation.start_tick);
    await previewAudio.current.play(notes.map((n:any)=>({pitch:n.pitch,velocity:n.velocity,start:secondsAt(material.parsed.tempos,n.start_tick)-start,duration:secondsAt(material.parsed.tempos,n.end_tick)-secondsAt(material.parsed.tempos,n.start_tick),track:{family:'keyboard',channel:14,program:0,volume:80}})),secondsAt(material.parsed.tempos,score.notation.end_tick)-start,()=>{},()=>{});
  }
  return <main className="practice-workspace" data-testid="practice-workspace">
    <header><small>NeoNote · 识谱与节奏</small><h1>从一小段旋律练起。</h1><p>先认音，再跟着节拍演奏。错误和纠正都会留在记录里。</p></header>
    {error && <p role="alert" className="notice">{error}</p>}
    {units?<UnitLibrary catalog={units} records={records} busy={busy} onChoose={id=>void chooseUnit(id)} />:<section className="practice-card"><h2>本地基础单元</h2><p role={unitError?'alert':undefined}>{unitError||'正在读取本地练习单元…'}</p>{unitError&&<button onClick={()=>void request<UnitCatalog>('/practice/units').then(next=>{setUnits(next);setUnitError('');}).catch(e=>setUnitError(String(e)))}>重新读取单元</button>}</section>}
    <section className="practice-card"><h2>选择练习材料</h2>
      <label className="file-button">导入练习 MIDI<input aria-label="导入练习MIDI" type="file" accept=".mid,.midi" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void action(() => importFile(file)); e.target.value = ''; }} /></label>
      <label>已有材料<select aria-label="已有练习材料" value={material?.source.id ?? ''} disabled={busy} onChange={e => void action(async () => load(await request<Material>('/practice/materials/' + e.target.value)))}><option value="">请选择</option>{materials.map(m => <option key={m.id} value={m.id}>{m.name} · {m.id.slice(0, 6)}</option>)}</select></label>
      {material&&material.revisions.length>0&&<label>已保存练习版本<select aria-label="已保存练习版本" value={revision?.revision.id??''} onChange={e=>void action(async()=>{const r=await request<Revision>(`/practice/materials/${material.source.id}/revisions/${e.target.value}`);setSettings(r.revision.settings);setPreview(null);setRevision(r);setIncoming(undefined);})}><option value="">请选择不可变版本</option>{material.revisions.map(r=><option key={r.id} value={r.id}>{r.created_at.slice(0,19)} · {r.id.slice(0,6)}</option>)}</select></label>}
      <details><summary>从已保存创作复制材料</summary><select aria-label="复制创作歌曲" value={songId} onChange={e=>setSongId(e.target.value)}><option value="">请选择歌曲</option>{songs.map(s=><option key={s.id} value={s.id}>{s.name} · 当前保存版本</option>)}</select><button disabled={busy||!songId} onClick={()=>void action(async()=>{const song=songs.find(s=>s.id===songId);load(await request<Material>('/practice/from-song',{method:'POST',body:JSON.stringify({song_id:song.id,revision_id:song.current_revision})}));await refresh();})}>复制这个保存版本到练习</button><p>仅复制保存版本；原歌曲修改、删除或恢复不会改写已有练习材料。</p></details>
    </section>
    {material && !builtin && <section className="practice-card"><h2>选择键盘轨道与片段</h2>
      <p>{material.source.name} · {material.parsed.original_ppq} PPQ · {material.parsed.seconds.toFixed(2)}秒；{material.parsed.declared.tempo ? '文件声明速度' : '未声明初速，按120BPM整理'}；{material.parsed.declared.meter ? '文件声明拍号' : '未声明初始拍号，按4/4整理'}。</p>
      <label>练习轨道<select aria-label="练习轨道" value={settings.track_id} disabled={busy} onChange={e => change({track_id: e.target.value, voices: []})}><option value="">请明确选轨</option>{material.parsed.tracks.map((t: any) => <option key={t.id} value={t.id} disabled={t.channel === 9}>{t.name} · 原轨{t.source_track + 1}/通道{t.channel + 1} · {t.instrument.program === null ? '未声明乐器' : `Program ${t.instrument.program}`} · {t.note_count}音 · {t.pitch_range.join('–')}{t.channel === 9 ? '（鼓轨）' : ''}</option>)}</select></label>
      <div className="practice-form-grid"><label>开始小节<input aria-label="开始小节" type="number" min="1" value={settings.start_measure} onChange={e => change({start_measure: Number(e.target.value)})} /></label><label>结束小节<input aria-label="结束小节" type="number" min="1" value={settings.end_measure} onChange={e => change({end_measure: Number(e.target.value)})} /></label>
        <label>量化网格<select aria-label="量化网格" value={settings.grid} onChange={e => change({grid: Number(e.target.value)})}><option value="0">不量化（时值须可记谱）</option><option value="120">十六分音符</option><option value="240">八分音符</option><option value="160">八分三连音</option><option value="80">十六分三连音</option></select></label>
        <label>谱表<select aria-label="练习谱表" value={settings.clef} onChange={e => change({clef: e.target.value})}><option value="treble">高音谱表</option><option value="bass">低音谱表</option><option value="grand">高低音大谱表</option></select></label>
      </div>
      <details><summary>整理选项与缺失信息</summary><p>声部、谱号、休止与音名拼写是整理推断。试听统一钢琴，保留原MIDI；不复现踏板、弯音和原控制事件。同音高重叠的松键按先入先出配对，须核对或选择分离声部；不会合并独立起音。</p>
        {settings.track_id&&(()=>{const track=material.parsed.tracks.find((t:any)=>t.id===settings.track_id);return <p>原始Program：{track.instrument.program??'未声明'}；Bank MSB：{track.instrument.bank_msb??'未声明'}；Bank LSB：{track.instrument.bank_lsb??'未声明'}。控制事件：{[...new Set(track.controls.map((e:any)=>e.type))].join('、')||'无'}。速度图{material.parsed.tempos.length}处，拍号图{material.parsed.meters.length}处；PPQ转换最大舍入{material.parsed.max_rounding_ticks.toFixed(3)}tick。</p>;})()}
        <label>整段移调<select aria-label="练习移调" value={settings.transpose} onChange={e => change({transpose: Number(e.target.value)})}>{[-24, -12, 0, 12, 24].map(v => <option key={v} value={v}>{v === 0 ? '不移调' : `${v > 0 ? '+' : ''}${v}半音`}</option>)}</select></label>
        <label>确认首小节弱起（拍，0为不猜测）<input aria-label="弱起拍数" type="number" min="0" step="0.25" value={settings.pickup_ticks / 480} onChange={e => change({pickup_ticks: Number(e.target.value) * 480})} /></label>
        <label>调号拼写<select aria-label="调号拼写" value={settings.key_fifths ?? 'source'} onChange={e => change({key_fifths: e.target.value === 'source' ? null : Number(e.target.value)})}><option value="source">文件调号（缺省C）</option>{[-7,-6,-5,-4,-3,-2,-1,0,1,2,3,4,5,6,7].map(v => <option key={v} value={v}>{v === 0 ? 'C / 无升降号' : `${Math.abs(v)}个${v < 0 ? '降' : '升'}号`}</option>)}</select></label>
        <label><input type="checkbox" checked={settings.expand_boundaries} onChange={e => change({expand_boundaries: e.target.checked})} />允许扩展片段到完整持续音边界</label>
        <label><input type="checkbox" checked={settings.acknowledge_controls} onChange={e => change({acknowledge_controls: e.target.checked})} />确认只练琴键起止，忽略踏板/表情/原音色控制</label>
        <label><input type="checkbox" checked={settings.acknowledge_large_quantization} onChange={e => change({acknowledge_large_quantization: e.target.checked})} />已核对明显量化偏移</label>
      </details>
      <button disabled={busy || !settings.track_id} onClick={() => void action(async () => { previewAudio.current.stop();setRevision(null);const next=await request<Prepared>(`/practice/materials/${material.source.id}/preview`, {method:'POST', body: JSON.stringify(settings)});setPreview(next);setVoiceOptions(next.voices??[]); })}>整理并预览五线谱</button>
      {preview && !preview.ready && <p role="alert">{preview.reason}</p>}
      {voiceOptions.length>0 && <fieldset><legend>推断声部（未选时预览全部）</legend>{voiceOptions.map(v => <label key={v.voice}><input type="checkbox" checked={settings.voices.includes(v.voice)} onChange={e => change({voices: e.target.checked ? [...settings.voices, v.voice] : settings.voices.filter(x => x !== v.voice)})} />声部{v.voice} · {v.note_count}音</label>)}</fieldset>}
    </section>}
    {score && <section className="practice-card"><h2>确认整理后的五线谱</h2><ScoreView xml={score.musicxml} notation={score.notation} active={selected} correct={correct} onSelect={id => setSelected([id])} onReady={setMapped} />
      {builtin&&<div className="practice-unit-instructions" data-testid="active-unit"><h3>{builtin.unit_title} · {builtin.exercise_title}</h3><p>{builtin.goal}</p><ul>{builtin.instructions.map((tip,i)=><li key={i}>{tip}</li>)}</ul><p>这条固定谱已安排好音高与节奏，无需选择轨道或量化；核对谱面后确认即可练习。</p></div>}
      <p>实际片段：原小节{score.notation.measures[0].source_number}–{score.notation.measures.at(-1).source_number}；{score.targets.attacks.length}个起音组。延音线只起音一次，圆滑线中的不同音仍分别起音。</p>
      {preview?.report && <p>{preview.report.quantized_notes}音量化，最大偏移{preview.report.max_shift_ms.toFixed(1)}ms；排除{preview.report.excluded_notes}个未选声部音符。</p>}
      <button disabled={busy} onClick={()=>void action(()=>listen(true))}>听原始时间与音高</button><button disabled={busy} onClick={()=>void action(()=>listen(false))}>听整理后的片段</button><button onClick={()=>previewAudio.current.stop()}>停止整理试听</button>
      {preview&&<details><summary>逐音量化与可演奏性报告</summary><p>以下仅描述选择的练习片段；原文件全部事件仍保留。</p><table><thead><tr><th>原起止tick</th><th>整理起止tick</th><th>最大偏移ms</th></tr></thead><tbody>{preview.report.shifts.map((s:any,i:number)=><tr key={i}><td>{s.before.join('–')}</td><td>{s.after.join('–')}</td><td>{(s.max_seconds*1000).toFixed(2)}</td></tr>)}</tbody></table>{score.targets.attacks.filter(a=>a.reachability.level!=='simple').map(a=><p key={a.id}>起音组{score.targets.attacks.indexOf(a)+1}：{a.reachability.reason}</p>)}</details>}
      {!revision&&!builtin&&<details><summary>手动圆滑线（不合并起音）</summary><label>圆滑线起点<select aria-label="圆滑线起点" value={slurStart} onChange={e=>setSlurStart(e.target.value)}><option value="">请选择</option>{score.notation.notes.map((n:any,i:number)=><option key={n.id} value={n.id}>起音{i+1} · {noteName(n.pitch)}</option>)}</select></label><label>圆滑线终点<select aria-label="圆滑线终点" value={slurEnd} onChange={e=>setSlurEnd(e.target.value)}><option value="">请选择</option>{score.notation.notes.map((n:any,i:number)=><option key={n.id} value={n.id}>起音{i+1} · {noteName(n.pitch)}</option>)}</select></label><button disabled={!slurStart||!slurEnd} onClick={()=>change({slurs:[...settings.slurs,[slurStart,slurEnd]]})}>添加圆滑线并重新预览</button></details>}
      {preview?.report?.blocked_targets.length > 0 && <p role="alert">有当前键盘不能完成的目标，请重新选段/声部或明确移调。原音符仍保留。</p>}
      {preview && !revision && <button className="primary" disabled={busy || !mapped} onClick={() => void action(async () => {const r=await request<Revision>(`/practice/materials/${material!.source.id}/revisions`, {method:'POST', body: JSON.stringify({settings, preview_hash: preview.preview_hash})});setRevision(r);setMaterial(await request<Material>('/practice/materials/'+material!.source.id));})}>确认五线谱并保存练习版本</button>}
      {revision && <p>整理版本已独立保存。</p>}
    </section>}
    {settings.slurs.length>0&&<section className="practice-card"><h2>手动圆滑线</h2>{settings.slurs.map((pair,i)=><button key={i} onClick={()=>change({slurs:settings.slurs.filter((_,j)=>j!==i)})}>删除圆滑线{i+1}</button>)}</section>}
    {revision&&mapped&&<SessionPlayer key={revision.revision.id+':'+(incoming?.snapshot.id??'new')} revision={revision} active={active} incoming={incoming} onSaved={()=>void refreshRecords().catch(e=>setError(String(e)))} onHighlights={(ids,done)=>{setSelected(ids);setCorrect(done);}} />}
    <section className="practice-card"><h2>练习记录</h2><button onClick={()=>void action(refresh)}>刷新练习记录</button>{records.length===0?<p>开始后保留首次答案、纠正与未完成记录。</p>:records.map(r=><article key={r.id}>{r.builtin&&<p>{r.builtin.unit_title} · {r.builtin.exercise_title}</p>}<b>{r.status==='completed'?'节奏完成':r.status==='recognition_complete'?'认音完成':'未完成 / 可恢复'} · {r.id.slice(0,8)}</b><p>首次准确率{r.result.recognition.first_accuracy===null?'未作答':(r.result.recognition.first_accuracy*100).toFixed(1)+'%'}；最终正确{r.result.recognition.next_index}/{r.result.recognition.target_count}。</p><button onClick={()=>void action(async()=>{const session=await request<Session>('/practice/sessions/'+r.id);const m=await request<Material>('/practice/materials/'+session.snapshot.material_id);load(m);const saved=await request<Revision>(`/practice/materials/${m.source.id}/revisions/${session.snapshot.revision_id}`);setSettings(saved.revision.settings);setRevision(saved);setIncoming(session);})}>查看记录 / 恢复练习</button></article>)}</section>
  </main>;
}
