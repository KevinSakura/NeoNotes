import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import type { Project } from './types';
import './vocals.css';

type Settings = { singer: string; runner: 'CPU' | 'DirectML'; gpu: number; pronunciations: { line_index: number; unit_index: number; pinyin: string }[]; vocal_gain: number; accompaniment_gain: number };
type ScoreCheck = { ready: boolean; detail: string | null; unsupported_count: number; unsupported_units: { line_index: number; unit_index: number; text: string }[] };
type Config = { hash: string | null; settings: Settings; score_check: ScoreCheck | null; lyrics_identity_hash?: string };
type Resource = { ready: boolean; error: string | null; singers: { singer_id: string; singer_name: string; attribution: string; devices: { id: number; name: string }[]; sources: string[] }[] };
type Job = { id: string; revision_id: string; revision_number: number; status: string; phase: string; error: string | null; current?: boolean; stale_reason?: string; settings_hash: string; settings: Settings; duration_seconds: number; reused_from: string | null; warnings?: string[] };
const defaults: Settings = { singer: 'qixuan-2.7.0', runner: 'DirectML', gpu: 0, pronunciations: [], vocal_gain: 0.8, accompaniment_gain: 1 };
const active = (job: Job) => ['queued', 'running', 'cancelling'].includes(job.status);

export default function VocalPanel({ project, dirty, busy, prepare, run, stopOther }: {
  project: Project; dirty: boolean; busy: boolean; prepare: () => Promise<Project>;
  run: (action: () => Promise<void>) => Promise<void>; stopOther: () => void;
}) {
  const [resource, setResource] = useState<Resource | null>(null);
  const [config, setConfig] = useState<Config>({ hash: null, settings: defaults, score_check: null });
  const [form, setForm] = useState<Settings>(defaults);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [word, setWord] = useState('');
  const [pinyin, setPinyin] = useState('');
  const [playing, setPlaying] = useState(false);
  const [loadingAudio, setLoadingAudio] = useState(false);
  const [position, setPosition] = useState(0);
  const context = useRef<AudioContext | null>(null);
  const sources = useRef<AudioBufferSourceNode[]>([]);
  const gains = useRef<GainNode[]>([]);
  const output = useRef<DynamicsCompressorNode | null>(null);
  const loadingRequest = useRef<AbortController | null>(null);
  const session = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const mounted = useRef(true);
  const configSequence = useRef(0);
  const units = project.revision.state.composition?.lyrics_alignment ?? [];
  const readingUnits = units.filter(unit => /^[\u3400-\u9fff\u{20000}-\u{2fa1f}]$/u.test(unit.text));
  const pending = jobs.find(active);
  const scoreBlocked = !dirty && config.score_check?.ready === false;
  const settingsDirty = JSON.stringify(form) !== JSON.stringify(config.settings);
  const selected = jobs.find(j => j.id === selectedId && j.status === 'completed') ?? jobs.find(j => j.status === 'completed' && j.current) ?? jobs.find(j => j.status === 'completed');
  const url = (job: Job, name: string) => `/api/projects/${project.id}/vocals/${job.id}/assets/${name}`;

  function stop() {
    session.current++;
    loadingRequest.current?.abort(); loadingRequest.current = null;
    sources.current.forEach(source => { source.onended = null; try { source.stop(); } catch { /* Already ended. */ } source.disconnect(); });
    sources.current = []; gains.current.forEach(g => g.disconnect()); gains.current = [];
    output.current?.disconnect(); output.current = null;
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    if (mounted.current) { setPlaying(false); setLoadingAudio(false); setPosition(0); }
  }

  useEffect(() => {
    mounted.current = true;
    const hide = () => { if (document.hidden) stop(); };
    window.addEventListener('neonote-stop-audio', stop); window.addEventListener('blur', stop);
    document.addEventListener('visibilitychange', hide);
    return () => { mounted.current = false; stop(); void context.current?.close(); window.removeEventListener('neonote-stop-audio', stop); window.removeEventListener('blur', stop); document.removeEventListener('visibilitychange', hide); };
  }, []);

  useEffect(() => {
    let live = true;
    const sequence = ++configSequence.current;
    stop(); setWord(''); setPinyin(''); setError(''); setNotice('');
    const load = async () => {
      try {
        const [available, settings] = await Promise.all([request<Resource>('/vocals/resources'), request<Config>(`/projects/${project.id}/vocal-settings`)]);
        if (live) { setResource(available); if (sequence === configSequence.current) { setConfig(settings); setForm(settings.settings); } }
      } catch (failure) { if (live) setError(failure instanceof Error ? failure.message : '无法读取演唱配置。'); }
    };
    void load();
    const poll = async () => {
      try {
        const result = await request<{ items: Job[] }>(`/projects/${project.id}/vocals`);
        if (live) setJobs(result.items);
      } catch (failure) { if (live) setError(failure instanceof Error ? failure.message : '无法读取演唱记录。'); }
    };
    void poll(); const interval = setInterval(() => void poll(), 1000);
    return () => { live = false; clearInterval(interval); stop(); };
  }, [project.id, project.current_revision]);

  useEffect(() => { if (dirty || busy) stop(); }, [dirty, busy]);
  useEffect(() => { if (gains.current[0]) gains.current[0].gain.value = form.vocal_gain; if (gains.current[1]) gains.current[1].gain.value = form.accompaniment_gain; }, [form.vocal_gain, form.accompaniment_gain]);

  async function saveSettings(saved: Project, value: Settings) {
    configSequence.current++;
    const existing = await request<Config>(`/projects/${saved.id}/vocal-settings`);
    if (value.pronunciations.length && config.lyrics_identity_hash !== existing.lyrics_identity_hash) throw Error('歌词原文或字词范围已改变，请为当前歌词重新选择读音，旧修正未保存。');
    // Use the loaded hash for the same revision, so a second page cannot overwrite it.
    const expected = saved.current_revision === project.current_revision ? config.hash : existing.hash;
    const next = await request<Config>(`/projects/${saved.id}/vocal-settings`, { method: 'POST', body: JSON.stringify({ base_revision: saved.current_revision, expected_settings_hash: expected, settings: value, lyrics_identity_hash: existing.lyrics_identity_hash }) });
    if (mounted.current) { setConfig(next); setForm(next.settings); }
    return next;
  }

  function perform(action: () => Promise<void>) {
    void run(async () => {
      setError(''); setNotice('');
      try { await action(); }
      catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : '演唱操作失败，请查看具体设置。'); }
    });
  }

  async function preview(mode: 'vocal' | 'accompaniment' | 'mix') {
    if (!selected) return;
    stopOther(); stop(); setError(''); setLoadingAudio(true);
    const id = ++session.current;
    const controller = new AbortController(); loadingRequest.current = controller;
    try {
      const ctx = context.current ?? new AudioContext(); context.current = ctx;
      await ctx.resume();
      const names = mode === 'mix' ? ['vocal.wav', 'accompaniment.wav'] : [mode === 'vocal' ? 'vocal.wav' : 'accompaniment.wav'];
      const buffers = await Promise.all(names.map(async name => {
        const response = await fetch(url(selected, name), { signal: controller.signal });
        if (!response.ok) throw Error('试听文件缺失或校验失败，请查看演唱记录。');
        return ctx.decodeAudioData(await response.arrayBuffer());
      }));
      if (!mounted.current || id !== session.current) return;
      loadingRequest.current = null;
      const nodes = buffers.map(buffer => { const node = ctx.createBufferSource(); node.buffer = buffer; return node; });
      const compressor = ctx.createDynamicsCompressor(); compressor.threshold.value = -1; compressor.knee.value = 0; compressor.ratio.value = 20;
      output.current = compressor;
      compressor.connect(ctx.destination);
      sources.current = nodes;
      const voiceGain = ctx.createGain(), backingGain = ctx.createGain(); gains.current = [voiceGain, backingGain];
      voiceGain.gain.value = form.vocal_gain; backingGain.gain.value = form.accompaniment_gain;
      voiceGain.connect(compressor); backingGain.connect(compressor);
      nodes.forEach((node, index) => node.connect(mode === 'accompaniment' || mode === 'mix' && index === 1 ? backingGain : voiceGain));
      const start = ctx.currentTime + 0.08;
      nodes.forEach(node => node.start(start));
      nodes[0].onended = () => { compressor.disconnect(); stop(); };
      setPlaying(true); setLoadingAudio(false);
      timer.current = setInterval(() => setPosition(Math.max(0, Math.min(selected.duration_seconds, ctx.currentTime - start))), 100);
    } catch (failure) { if (mounted.current && id === session.current) { stop(); setError(failure instanceof Error ? failure.message : '无法试听演唱。'); } }
  }

  return <article className="card vocal-panel" aria-label="人声演唱">
    <div className="card-heading"><h2>让歌手唱出这首歌</h2><span className="tag">本地演唱 · 非商业演示</span></div>
    <p className="muted">使用已保存的全文歌词、旋律与节奏，不重新作曲。发音和自然度请亲自试听确认。</p>
    <label className="field">歌手<select aria-label="演唱歌手" value={form.singer} disabled={busy || !!pending} onChange={event => setForm({ ...form, singer: event.target.value })}><option value="qixuan-2.7.0">绮萱 Qixuan v2.7.0 · 普通话独唱</option></select></label>
    <details className="vocal-details"><summary>运行设备与资源</summary>
      <label className="field">运行方式<select aria-label="演唱运行方式" disabled={busy || !!pending} value={form.runner} onChange={event => { stop(); setForm({ ...form, runner: event.target.value as Settings['runner'] }); }}><option value="DirectML">显卡（DirectML）</option><option value="CPU">CPU（兼容方式）</option></select></label>
      {form.runner === 'DirectML' && <label className="field">显卡<select aria-label="演唱显卡" value={form.gpu} disabled={busy || !!pending} onChange={event => setForm({ ...form, gpu: Number(event.target.value) })}>{resource?.singers[0]?.devices.map(device => <option key={device.id} value={device.id}>{device.name}</option>)}</select></label>}
      <p className="hint">{resource?.ready ? '声库、声码器与渲染工具已通过资源检查。' : resource?.error ?? '正在检查演唱资源…'}</p>
      {resource?.singers[0]?.sources.map((source, index) => <a key={source} href={source} target="_blank" rel="noreferrer">{['歌手官方来源', '声码器官方来源', 'OpenUtau 引擎'][index]}</a>)}
    </details>
    {!resource?.ready && resource?.error && <p role="alert">{resource.error}</p>}
    <div className="vocal-gains">{([['vocal_gain', '人声音量'], ['accompaniment_gain', '伴奏音量']] as const).map(([key, label]) => <label key={key}>{label} · {Math.round(form[key] * 100)}%<input aria-label={label} type="range" min="0" max="2" step="0.05" value={form[key]} disabled={busy} onChange={event => setForm({ ...form, [key]: Number(event.target.value) })} /></label>)}</div>
    <details className="vocal-details"><summary>修正读错的字</summary>
      <p className="hint">选择一个汉字，填写无声调拼音，如 chong、zhe；ü 写作 v。一字多音会延续元音，不重复起字。英文歌词需另行接入英文演唱。</p>
      <label className="field">选择字<select aria-label="修正哪个字" value={word} disabled={busy} onChange={event => { setWord(event.target.value); const [line, unit] = event.target.value.split(':').map(Number); setPinyin(form.pronunciations.find(p => p.line_index === line && p.unit_index === unit)?.pinyin ?? ''); }}><option value="">请选择</option>{readingUnits.map(unit => <option key={`${unit.line_index}:${unit.unit_index}`} value={`${unit.line_index}:${unit.unit_index}`}>第{unit.line_index + 1}行 · 第{unit.unit_index + 1}字 · {unit.text}</option>)}</select></label>
      <label className="field">正确读音<input aria-label="正确读音拼音" value={pinyin} maxLength={12} disabled={busy || !word} onChange={event => setPinyin(event.target.value.toLowerCase().trim())} placeholder="例如 chong" /></label>
      <div className="inline-actions"><button disabled={busy || !word || !/^[a-z]+$/.test(pinyin)} onClick={() => { stop(); const [line, unit] = word.split(':').map(Number); setForm({ ...form, pronunciations: [...form.pronunciations.filter(p => p.line_index !== line || p.unit_index !== unit), { line_index: line, unit_index: unit, pinyin }].sort((a,b) => a.line_index - b.line_index || a.unit_index - b.unit_index) }); }}>应用读音修正</button><button disabled={busy || !word} onClick={() => { stop(); const [line, unit] = word.split(':').map(Number); setForm({ ...form, pronunciations: form.pronunciations.filter(p => p.line_index !== line || p.unit_index !== unit) }); setPinyin(''); }}>恢复自动读音</button></div>
      <p className="hint">已修正 {form.pronunciations.length} 个字。保存设置并重新生成演唱后生效，原歌词和乐谱保留。</p>
    </details>
    {dirty && selected && <p className="render-notice">当前草稿有修改，下方演唱属于已保存旧稿；生成前会先保存。</p>}
    {settingsDirty && <p className="render-notice">演唱设置已调整。试听使用当前音量；点击“生成演唱 / 更新合听”保存新的导出结果。</p>}
    {!units.length && <p className="hint">当前保存版本还没有歌词对应，请先采用歌词谱曲并保存。</p>}
    {config.score_check?.ready === false && <div className="vocal-score-check" data-testid="vocal-score-check" role="status">
      <b>当前保存版本暂不能生成演唱</b><p>{config.score_check.detail}</p>
      {config.score_check.unsupported_count > 0 && <><p>共有 {config.score_check.unsupported_count} 处字词超出当前普通话演唱范围：</p><ul>{config.score_check.unsupported_units.map(unit => <li key={`${unit.line_index}:${unit.unit_index}`}>第 {unit.line_index + 1} 行 · 第 {unit.unit_index + 1} 字词 · {unit.text}</li>)}</ul>{config.score_check.unsupported_count > config.score_check.unsupported_units.length && <p>另有 {config.score_check.unsupported_count - config.score_check.unsupported_units.length} 处未展开。</p>}</>}
      <p>演唱设置可以单独保存。{dirty ? '当前草稿有修改，生成前会先保存并重新检查。' : '请使用已完成歌词对应的中文谱曲版本后生成演唱。'}</p>
    </div>}
    <div className="inline-actions"><button className="primary" disabled={busy || !!pending || !resource?.ready || !units.length || scoreBlocked} onClick={() => perform(async () => {
      stop(); setError(''); const value = structuredClone(form); const saved = await prepare(); const settings = await saveSettings(saved, value);
      if (!settings.score_check?.ready) throw Error(settings.score_check?.detail ?? '当前保存版本还未通过演唱检查。');
      const job = await request<Job>(`/projects/${saved.id}/vocals`, { method: 'POST', body: JSON.stringify({ base_revision: saved.current_revision, settings_hash: settings.hash }) });
      if (mounted.current) setJobs(previous => [job, ...previous.filter(p => p.id !== job.id)]);
    })}>{pending ? pending.phase : '生成演唱 / 更新合听'}</button>
      <button disabled={busy} onClick={() => perform(async () => { stop(); const value = structuredClone(form); await saveSettings(await prepare(), value); if (mounted.current) setNotice('演唱设置已保存。'); })}>保存演唱设置</button>
      {pending && <button disabled={busy || pending.status === 'cancelling'} onClick={() => perform(async () => { const item = await request<Job>(`/projects/${project.id}/vocals/${pending.id}/cancel`, { method: 'POST' }); setJobs(previous => previous.map(j => j.id === item.id ? item : j)); })}>取消演唱</button>}
    </div>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {selected?.warnings?.map(warning => <p key={warning} className="hint">{warning}</p>)}
    {selected && <div className="render-player">
      <b>演唱试听 · v{selected.revision_number}</b><p className="hint">{dirty || settingsDirty || !selected.current ? `历史演唱，${selected.stale_reason ?? '尚未反映当前修改'}。` : '对应当前保存版本与演唱设置。'}人声与伴奏使用同一播放时钟；音量保护与导出混音略有差异。</p>
      <div className="inline-actions">{(['vocal', 'accompaniment', 'mix'] as const).map((mode, index) => <button key={mode} disabled={loadingAudio || busy} onClick={() => void preview(mode)}>{['听人声', '听伴奏', '人声与伴奏合听'][index]}</button>)}<button disabled={!playing && !loadingAudio} onClick={stop}>停止演唱试听</button></div>
      <p className="hint" aria-live="polite">{loadingAudio ? '准备试听…' : playing ? `演唱试听 ${position.toFixed(1)} / ${selected.duration_seconds.toFixed(1)} 秒` : '伴奏只移除绑定歌词的导唱音符，保留无词键盘段和其他声部；保留已保存音量，按完整声部渲染。'}</p>
    </div>}
    <div className="render-list" aria-label="演唱历史">{jobs.slice(0, 30).map(job => <div className="render-row" key={job.id} data-testid="vocal-status"><div><b>绮萱 · v{job.revision_number}</b><span>{job.status === 'completed' ? `已保存 · ${job.duration_seconds.toFixed(2)} 秒 · ${job.current ? '当前结果' : job.stale_reason}` : job.phase}</span>{job.error && <p role="alert">{job.error}</p>}{job.reused_from && <p className="hint">复用原干声与伴奏，本次只更新合听。</p>}</div>
      {job.status === 'completed' && <div className="render-downloads"><button onClick={() => { stop(); setSelectedId(job.id); }}>选择试听</button>{[['vocal.wav', '人声 WAV'], ['accompaniment.wav', '伴奏 WAV'], ['mix.wav', '合听 WAV'], ['mix.mp3', '合听 MP3'], ['singing.ustx', 'OpenUtau 工程']].map(([name, label]) => <a key={name} href={url(job, name) + '?download=true'} download>{label}</a>)}</div>}
    </div>)}</div>
    <p className="hint">AI合成演唱：绮萱 / DiffSinger；社区声码器 OpenVPI。初版仅非商业演示，公开分享请保留合成与歌手署名。</p>
  </article>;
}
