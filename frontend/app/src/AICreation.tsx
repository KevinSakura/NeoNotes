import { useEffect, useState } from 'react';
import { request } from './api';
import type { PhraseBudget, GenerationJob, PracticePlayer, Project, ReviewStatus, SongState } from './types';
import { fullLyricLines, lyricSelection, noteName, secondsAt } from './music.mjs';
import LyricPractice from './LyricPractice';

interface ModelStatus { mode: 'mock' | 'real' | 'unavailable'; ready: boolean; error: string | null; model: string; max_tokens: number; full_song_max_tokens: number }
interface Props {
  project: Project; draft: SongState; busy: boolean; operation: 'generate' | 'modify' | 'full'; dirty?: boolean; reviewSignal?: number;
  prepare: () => Promise<Project>; run: (action: () => Promise<void>) => Promise<void>;
  apply: (job: GenerationJob) => void; preview: (state: SongState) => void;
  practice: PracticePlayer;
}

export default function AICreation({ project, draft, busy, operation, dirty = false, reviewSignal = 0, prepare, run, apply, preview, practice }: Props) {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [jobs, setJobs] = useState<GenerationJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<SongState | null>(null);
  const [instruction, setInstruction] = useState('');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const [contract, setContract] = useState('lyric-motifs-v1');
  const [offer, setOffer] = useState<PhraseBudget | null>(null);
  const [retryOffer, setRetryOffer] = useState<{ job: GenerationJob; offer: PhraseBudget } | null>(null);
  const [retryConsent, setRetryConsent] = useState(false);
  const [review, setReview] = useState<ReviewStatus | null>(null);
  const full = operation === 'full';
  const usesLyrics = full ? !!draft.inputs.lyrics_text.trim() : !!draft.inputs.selected_lines.length;
  const useV2 = usesLyrics && !full && contract === 'lyric-phrases-v2';
  const fullScope = full || usesLyrics || (operation === 'modify' && draft.composition?.scope === 'full');
  const tokenLimit = fullScope ? status?.full_song_max_tokens ?? 32768 : status?.max_tokens;
  const selectedLines = usesLyrics ? fullLyricLines(draft.inputs.lyrics_text) : [];
  const lyrics = lyricSelection(draft.inputs.lyrics_text, selectedLines, fullScope);
  const lyricIssue = lyrics.error || (!full && draft.inputs.lyrics_text.trim() && !selectedLines.length && !draft.inputs.theme.trim() && !draft.inputs.emotion.trim() && !draft.inputs.recording.length && !(operation === 'modify' && instruction.trim()) ? '请点击“使用歌词全文”，或填写主题、音乐感觉、录制片段。' : null);
  const pending = jobs.some(job => ['queued', 'running', 'validating', 'cancelling'].includes(job.status));
  const available = jobs.filter(job => job.operation === operation);
  const chosen = available.find(job => job.id === selectedId) ?? available.find(job => job.status === 'completed');

  useEffect(() => {
    let alive = true; setReview(null); setConsent(false);
    if (full) void request<ReviewStatus>(`/projects/${project.id}/review`).then(result => { if (alive) setReview(result); }).catch(error => { if (alive) setError(error.message); });
    return () => { alive = false; };
  }, [project.id, project.current_revision, full, dirty, reviewSignal]);

  useEffect(() => {
    let alive = true;
    void request<ModelStatus>('/model/status').then(result => { if (alive) setStatus(result); }).catch(error => { if (alive) setError(error.message); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const result = await request<{ items: GenerationJob[] }>(`/projects/${project.id}/generations`);
        if (alive) setJobs(result.items);
      } catch (error) { if (alive) setError(error instanceof Error ? error.message : '编曲记录读取失败。'); }
    };
    void refresh();
    const timer = setInterval(() => { if (alive) void refresh(); }, 1500);
    return () => { alive = false; clearInterval(timer); };
  }, [project.id]);

  useEffect(() => {
    let alive = true; setCandidate(null);
    if (chosen?.status === 'completed') void request<SongState>(`/projects/${project.id}/generations/${chosen.id}/candidate`).then(result => { if (alive) setCandidate(result); }).catch(error => { if (alive) setError(error.message); });
    return () => { alive = false; };
  }, [project.id, chosen?.id, chosen?.status]);

  const draftKey = JSON.stringify(draft);
  useEffect(() => {
    let alive = true; setOffer(null);
    if (!useV2) return;
    setConsent(false);
    const timer = setTimeout(() => {
      void request<PhraseBudget>('/projects/' + project.id + '/generations/phrase-budget', {
        method: 'POST', body: JSON.stringify({ base_revision: project.current_revision, operation,
          instruction: operation === 'modify' ? instruction : '', state: draft })
      }).then(value => { if (alive) setOffer(value); }).catch(f => { if (alive) setError(f.message); });
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [project.id, project.current_revision, draftKey, useV2, operation, instruction]);

  async function retry(job: GenerationJob) {
    if (dirty) throw Error('请保持原任务的保存版本，不能用新草稿复用旧块。');
    setError('');
    const budget = await request<PhraseBudget>('/projects/' + project.id + '/generations/' + job.id + '/retry-budget',
      { method: 'POST', body: JSON.stringify({ base_revision: project.current_revision }) });
    setRetryOffer({ job, offer: budget }); setRetryConsent(false);
  }

  async function confirmRetry() {
    if (!retryOffer) return;
    const job = await request<GenerationJob>('/projects/' + project.id + '/generations/' + retryOffer.job.id + '/retry',
      { method: 'POST', body: JSON.stringify({ base_revision: project.current_revision,
        confirm_real_call: retryConsent, budget_hash: retryOffer.offer.hash }) });
    setJobs(previous => [job, ...previous]); setSelectedId(job.id); setRetryOffer(null); setRetryConsent(false);
  }

  async function generate() {
    setError('');
    if (lyricIssue) { setError(lyricIssue); return; }
    try {
      const current = await prepare();
      const job = await request<GenerationJob>(`/projects/${current.id}/generations`, { method: 'POST', body: JSON.stringify({ base_revision: current.current_revision, operation, instruction: operation === 'modify' ? instruction : '', confirm_real_call: consent, contract: useV2 ? 'lyric-phrases-v2' : undefined, budget_hash: useV2 ? offer?.hash : undefined }) });
      setJobs(previous => [job, ...previous.filter(item => item.id !== job.id)]); setSelectedId(job.id); setConsent(false);
    } catch (error) { setError(error instanceof Error ? error.message : '生成请求失败，请查看输入提示。'); throw error; }
  }

  const source = draft.inputs.recording.filter(note => note.family !== 'drums').slice(0, 8);
  return <article className="card ai-card" data-testid={`ai-${operation}`}>
    <div className="card-heading"><h2>{full ? '从已审核短曲生成整曲' : operation === 'generate' ? usesLyrics ? '为歌词全文谱曲' : '把灵感编成 60 秒短曲' : '用一句话调整整首歌'}</h2><span className="tag">{status?.mode === 'real' ? 'DeepSeek · 真实模式' : status?.mode === 'mock' ? '模拟流程' : '检查模型配置'}</span></div>
    {status?.mode === 'mock' && <p className="hint">当前生成的是明确标注的模拟曲，用于体验流程，不调用 API，也不代表 DeepSeek 编曲质量。</p>}
    {status?.mode === 'real' && !useV2 && <p className="muted">每次生成最多请求一次 DeepSeek，输出上限 {tokenLimit} token，不自动重试。候选通过结构检查后仍需试听和人工试唱。</p>}
    {full && <>
      <p className="hint">沿用已审核短曲的旋律与风格，重新安排完整段落，原60秒不会固定为开头。短曲和审核记录保留，生成完成也不会自动采用。</p>
      <p className="muted" data-testid="full-song-spec">{review?.full_song_spec?.lyric_lines ? `使用TXT全部${review.full_song_spec.lyric_lines}行、${review.full_song_spec.lyric_units}个字/词，时长按歌词和乐句安排（最多10分钟）。` : draft.inputs.lyrics_text.trim() ? '使用TXT全部歌词，时长按歌词和乐句安排（最多10分钟）。' : '无歌词整曲固定3分15秒（195秒）。'} 单次输出上限32768 token；仍可能截断或未满足音乐约束，失败会保留短曲。</p>
      {(dirty || !review?.full_song_enabled) && <p className="hint" data-testid="full-song-gate">{dirty ? '当前有未保存修改，保存后须重新审核短曲。' : review?.full_song_reason ?? '正在核对当前短曲与审核记录…'}</p>}
    </>}
    {status?.error && <p className="render-notice">{status.error}</p>}
    {lyrics.units > 0 && <p className="muted">歌曲可以有无词的前奏、间奏和尾奏；歌词对应只描述演唱部分，一字可跨多个音符。完整试听包含器乐段，逐句练唱只播放已绑定的主旋律。</p>}
    {usesLyrics && !useV2 && <p className="hint" data-testid="lyric-composition-policy">本次使用全文 {lyrics.lines} 行、{lyrics.units} 字词，不固定60秒。模型给出逐句旋律型与和声，平台展开字词、安排换气及四声部伴奏；候选完成后仍须试唱和审核。</p>}
    {usesLyrics && !full && <label className="field">歌词谱曲方式<select aria-label="歌词谱曲方式" value={contract} disabled={busy || pending} onChange={e => { setContract(e.target.value); setConsent(false); setError(''); }}>
      <option value="lyric-motifs-v1">现有全文谱曲</option><option value="lyric-phrases-v2">乐句与转音（v2试验）</option>
    </select></label>}
    {useV2 && <div className="render-notice" data-testid="phrase-budget">
      <b>乐句谱曲 · 试验入口</b>
      <p>按全文编号安排词组、乐句、长音和转音，全文完成后才能采用。默认入口继续使用现有方案。</p>
      {offer ? <><p>本次 {offer.unit_count} 字词，{offer.mode === 'single' ? '单请求' : '共享规划后分块'}，最多 {offer.request_limit} 次请求；每次最多 {offer.per_request_max_tokens} 输出 token，总输出预算上限 {offer.total_output_token_limit} token。</p><p>{offer.cost_notice}</p></> : <p>正在核对输入与请求预算…</p>}
      <p>{status?.mode === 'mock' ? '模拟服务验证流程，不请求真实API。' : '勾选下方确认即同意上述新合同预算；不会自动重试或切换服务商。'}</p>
    </div>}
    {retryOffer && <div className="render-notice" data-testid="phrase-retry-budget">
      <p>本次重试将复用 {retryOffer.offer.reused_blocks} 个已成功且哈希有效的块，最多 {retryOffer.offer.request_limit} 次新请求；单次 {retryOffer.offer.per_request_max_tokens} 输出 token，总输出上限 {retryOffer.offer.total_output_token_limit} token。原失败任务保留。</p>
      {status?.mode === 'real' && <label><input type="checkbox" checked={retryConsent} onChange={e => setRetryConsent(e.target.checked)} />确认按上述预算重试，会消耗API额度。</label>}
      <button disabled={busy || pending || dirty || (status?.mode === 'real' && !retryConsent)} onClick={() => void run(confirmRetry)}>按此预算显式重试</button>
    </div>}
    {error && <p className="render-notice" role="alert">{error}</p>}
    {operation === 'modify' && <><label className="field">整曲修改要求<textarea aria-label="整曲修改要求" disabled={busy || pending} value={instruction} maxLength={2000} rows={3} onChange={event => setInstruction(event.target.value)} placeholder="例如：整体更轻快，保留歌词，把吉他伴奏处理得更柔和。" /></label><p className="muted">支持标准四声部作品的整体修改。{usesLyrics ? '全文歌词保留，时长按修改后的乐句重新安排。' : '保持当前时长。'}局部修改与单声部重生成留待后续。原版本会保留。</p></>}
    {source.length > 0 && <p className="muted">原旋律开头：{source.map(note => noteName(note.pitch)).join(' → ')}。{draft.inputs.melody_mode === 'preserve' ? '保留模式由程序固定原演奏，模型从各声部参考窗口之后延展。' : '自由改编需试听对照原旋律走向和乐句延展。'}</p>}
    {status?.mode === 'real' && <label className="ai-consent"><input type="checkbox" checked={consent} disabled={busy || pending} onChange={event => setConsent(event.target.checked)} />{full ? '同意本次向DeepSeek发送TXT全部歌词、主题、音乐感觉、原演奏及已审核短曲乐谱，生成整曲并消耗API额度。' : `同意本次向 DeepSeek 发送${usesLyrics ? '歌词框全文、' : ''}主题、音乐感觉和旋律；整曲修改还会发送当前乐谱，并消耗 API 额度。`}</label>}
    {lyricIssue && <p className="lyric-input-warning" data-testid="ai-input-warning">{lyricIssue}</p>}
    <button className="primary" disabled={busy || pending || (useV2 && !offer) || !!lyricIssue || !status?.ready || (status.mode === 'real' && !consent) || (full && (dirty || !review?.full_song_enabled)) || (operation === 'modify' && !instruction.trim())} onClick={() => void run(generate)}>{pending ? '编曲任务进行中…' : `${status?.mode === 'mock' ? '模拟' : 'AI'}${full ? '生成整曲候选' : operation === 'generate' ? usesLyrics ? '生成全文歌词候选' : '生成 60 秒候选' : '生成整曲修改候选'}`}</button>
    <p className="muted">{full ? '以当前审核通过的保存版本为输入；' : '生成前保存输入；'}候选需要你明确采用。{draft.inputs.selected_lines.length ? '歌词决定内容，音乐感觉决定曲风；采用并保存后可另行生成绮萱演唱。' : '可仅凭文字创作。'}未采用前不会替换当前乐谱。</p>
    {jobs.filter(job => job.contract === 'lyric-phrases-v2' && ['queued', 'running', 'validating', 'cancelling'].includes(job.status)).map(job => <div key={'cancel-' + job.id}><button disabled={busy || job.status === 'cancelling'} onClick={() => void run(async () => {
      const changed = await request<GenerationJob>('/projects/' + project.id + '/generations/' + job.id + '/cancel', { method: 'POST' });
      setJobs(previous => previous.map(j => j.id === changed.id ? changed : j));
    })}>取消乐句谱曲</button></div>)}
    {jobs.filter(job => job.contract === 'lyric-phrases-v2' && ['failed', 'cancelled', 'interrupted'].includes(job.status)).slice(0, 3).map(job => <div key={'retry-' + job.id}>
      <button disabled={busy || pending || dirty || job.base_revision !== project.current_revision} onClick={() => void run(() => retry(job))}>查看重试预算 · {job.id.slice(0, 8)}</button>
    </div>)}
    {jobs.slice(0, 8).map(job => <div className="generation-row" key={job.id}>
      <div><b>{job.mock ? '模拟' : 'AI'} · {job.operation === 'full' ? '完整歌曲' : job.operation === 'modify' ? '整曲修改' : job.scope === 'lyrics' ? '全文歌词谱曲' : '60秒短曲'} · 输入 v{job.base_number}</b><small data-testid="generation-status">{job.phase}</small>{job.error && <p className="render-notice">{job.error}</p>}{!job.mock && job.request_count > 0 && <small>{job.metadata?.usage ? `实际输入 ${job.metadata.usage.prompt_tokens ?? '未知'} / 输出 ${job.metadata.usage.completion_tokens ?? '未知'} token` : '用量尚未确认'}；失败请求也可能计费，以服务商账单为准。</small>}</div>
      {job.contract === 'lyric-phrases-v2' && <p className="hint" data-testid="phrase-task-ledger">试验v2 · 预算最多{job.reserved_attempts}次；{job.mock ? '模拟分发' : 'HTTP分发'} {job.mock ? job.simulated_requests : job.request_count}次；成功块 {job.blocks?.filter(b => b.status === 'completed').length ?? 0}/{job.blocks?.length ?? 0}。全文统一校验后才发布候选。</p>}
      {job.status === 'completed' && job.operation === operation && <button disabled={busy} aria-label={`查看候选 ${job.id.slice(0, 8)}`} onClick={() => setSelectedId(job.id)}>查看候选</button>}
    </div>)}
    {chosen?.status === 'completed' && candidate?.composition && <div className="candidate-preview" data-testid="candidate-preview">
      <h3>{candidate.composition.title}{chosen.mock ? ' · 模拟结果' : ''}</h3>
      <p>{candidate.composition.style_summary}</p><p className="muted">{secondsAt(candidate.score, candidate.score.end_tick).toFixed(3)} 秒 · {candidate.score.tracks.length} 个声部 · {candidate.score.tracks.reduce((count, track) => count + track.notes.length, 0)} 个音符</p>
      <p className="muted">{candidate.composition.sections.map(section => section.name).join(' → ')} · 调性 {candidate.composition.key}</p>
      <p className="hint">已检查音乐结构；旋律、风格与歌词可唱性仍需人工验收。</p>
      {candidate.composition.arrangement === 'lyric-phrases-v2' && <p className="muted" data-testid="phrase-contract">试验v2 · {candidate.composition.v2?.phrase_layout.length} 个音乐乐句；稳定字词编号与实际音符绑定。词组、重音和乐句表达需要人工试听；这份候选未代表真实模型效果或歌声验收。</p>}
      {candidate.composition.arrangement === 'lyric-motifs-v1' && <p className="muted">此候选采用逐句旋律型循环与句尾收束；字词对应、换气和四声部伴奏由平台生成。重复是否自然、重音和伴奏表现仍需人工确认。</p>}
      {candidate.composition.full_source && <p className="hint" data-testid="full-song-source">整曲引用审核短曲 {candidate.composition.full_source.revision_id.slice(0, 8)}；参考音高片段 {candidate.composition.full_source.motif_pitches.map(pitch => noteName(pitch)).join(' → ')}。音高片段匹配通过，旋律延展、歌词重音与音乐风格仍需人工对照。</p>}
      {candidate.composition.reference_lock && <p className="hint" data-testid="reference-lock">程序保留 {candidate.composition.reference_lock.note_count} 个原演奏音符及音色；过滤参考窗口内 {candidate.composition.reference_lock.filtered_model_notes} 个模型音符。延展部分来自{chosen.mock ? '模拟器' : '模型'}，仍需人工试听。</p>}
      {chosen.base_revision !== project.current_revision && draft.composition?.job_id !== chosen.id && <p className="render-notice">此候选基于旧版本，不能覆盖新保存的歌曲；可以试听和下载 MIDI。</p>}
      <div className="inline-actions"><button disabled={busy} onClick={() => preview(candidate)}>▶ 试听候选</button><button className="primary" disabled={busy || chosen.base_revision !== project.current_revision || draft.composition?.job_id === chosen.id} onClick={() => apply(chosen)}>{draft.composition?.job_id === chosen.id ? '已采用此候选' : '采用到编辑器'}</button><a className="audio-download" href={`/api/projects/${project.id}/generations/${chosen.id}/midi`}>下载候选 MIDI</a></div>
      {candidate.composition.lyrics_alignment.length > 0 && <details><summary>查看候选的歌词与音符对应</summary><LyricPractice state={candidate} busy={busy} practice={practice} /></details>}
      {chosen.metadata && <details><summary>调用记录</summary><p className="muted">返回模型：{chosen.metadata.returned_model} · 实际请求 {chosen.request_count} 次{chosen.metadata.elapsed_seconds !== null ? ` · ${chosen.metadata.elapsed_seconds.toFixed(1)} 秒` : ''}</p><p className="muted">{chosen.metadata.usage ? `输入 ${chosen.metadata.usage.prompt_tokens ?? '未知'} / 输出 ${chosen.metadata.usage.completion_tokens ?? '未知'} token；实际费用以服务商账单为准。` : chosen.mock ? '模拟任务没有真实 token 用量。' : '服务未返回实际用量，以服务商账单为准。'}</p></details>}
    </div>}
  </article>;
}
