import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import type { Project } from './types';

type RenderJob = {
  id: string; revision_id: string; revision_number: number; mode: 'mix' | 'full';
  status: 'queued' | 'rendering' | 'completed' | 'failed'; phase: string; error: string | null;
  duration_seconds: number; created_at: string;
};

export default function AudioExports({ project, dirty, busy, prepare, run, stopOther }: {
  project: Project; dirty: boolean; busy: boolean; prepare: () => Promise<Project>;
  run: (action: () => Promise<void>) => Promise<void>;
  stopOther: () => void;
}) {
  const [items, setItems] = useState<RenderJob[]>([]);
  const [mode, setMode] = useState<'mix' | 'full'>('mix');
  const [error, setError] = useState('');
  const [previewId, setPreviewId] = useState<string | null>(null);
  const player = useRef<HTMLAudioElement>(null);
  const sequence = useRef(0);
  const pending = items.some(item => item.status === 'queued' || item.status === 'rendering');

  useEffect(() => {
    let active = true;
    const generation = ++sequence.current;
    const load = async () => {
      try {
        const result = await request<{ items: RenderJob[] }>(`/projects/${project.id}/renders`);
        if (active && sequence.current === generation) { setItems(result.items); setError(''); }
      } catch (failure) { if (active) setError(failure instanceof Error ? failure.message : '无法获取渲染记录。'); }
    };
    void load();
    const timer = setInterval(() => { void load(); }, 1000);
    const pause = () => player.current?.pause();
    const exclusive = (event: Event) => { if ((event as CustomEvent).detail !== 'render') pause(); };
    const hide = () => { if (document.hidden) pause(); };
    window.addEventListener('blur', pause); document.addEventListener('visibilitychange', hide);
    window.addEventListener('neonote-stop-audio', exclusive);
    return () => { active = false; clearInterval(timer); pause(); window.removeEventListener('neonote-stop-audio', exclusive); window.removeEventListener('blur', pause); document.removeEventListener('visibilitychange', hide); };
  }, [project.id, project.current_revision]);

  const current = items.find(item => item.revision_id === project.current_revision && item.mode === mode && item.status === 'completed');
  const selected = items.find(item => item.id === previewId && item.status === 'completed') ?? current ?? items.find(item => item.status === 'completed');
  const url = (job: RenderJob, extension: string) => `/api/projects/${project.id}/renders/${job.id}/audio/${extension}`;

  return <article className="card audio-exports">
    <div className="card-heading"><h2>把编曲变成声音</h2><span className="tag">本地真实音色</span></div>
    <p className="muted">先保存，再生成可试听的 WAV 和可分享的 MP3。使用本地音色库，不发送到云端。</p>
    <label className="field">音频包含哪些声部？<select disabled={busy} value={mode} onChange={event => setMode(event.target.value as 'mix' | 'full')}>
      <option value="mix">按当前独奏、静音与音量混音</option><option value="full">完整声部（忽略独奏和静音，保留音量）</option>
    </select></label>
    <button className="primary" disabled={busy || pending} onClick={() => void run(async () => {
      player.current?.pause(); setPreviewId(null); const saved = await prepare();
      const job = await request<RenderJob>(`/projects/${saved.id}/renders`, { method: 'POST', body: JSON.stringify({ base_revision: saved.current_revision, mode }) });
      setItems(previous => [job, ...previous.filter(item => item.id !== job.id)]);
    })}>{pending ? '正在生成声音…' : '生成 WAV 与 MP3'}</button>
    <p className="hint">保留完整时间轴，结束后加 3 秒尾音；钢琴与鼓等音色来自 GeneralUser GS。电脑试听与导出引擎存在细微差异。</p>
    {error && <p role="alert">{error}</p>}
    {dirty && <p className="render-notice">当前有未保存修改。下方已生成音频属于旧稿；生成时会先保存当前修改。</p>}
    {selected && <div className="render-player">
      <b>渲染试听 · v{selected.revision_number} · {selected.mode === 'mix' ? '当前混音' : '完整声部'}</b>
      <p className="hint">{!dirty && selected.revision_id === project.current_revision && selected.mode === mode ? '对应当前保存版本与所选模式。' : '历史音频，尚未反映当前编辑或所选模式。'}这是实际下载的 WAV。</p>
      <audio key={selected.id} ref={player} controls preload="metadata" src={url(selected, 'wav')} onPlay={stopOther} aria-label="渲染后的 WAV 试听" />
    </div>}
    <div className="render-list" aria-label="音频渲染历史">
      {items.slice(0, 30).map(item => <div key={item.id} className="render-row" data-testid="render-status">
        <div><b>v{item.revision_number} · {item.mode === 'mix' ? '当前混音' : '完整声部'}</b>
          <span>{item.status === 'completed' ? `已完成 · ${item.duration_seconds.toFixed(2)} 秒` : item.status === 'failed' ? '生成失败' : item.phase}</span>
          {item.error && <p role="alert">{item.error}</p>}
        </div>
        {item.status === 'completed' && <div className="render-downloads"><button aria-label={`试听 v${item.revision_number} ${item.mode === 'mix' ? '当前混音' : '完整声部'}`} onClick={() => { player.current?.pause(); setPreviewId(item.id); }}>试听</button><a href={url(item, 'wav') + '?download=true'} download>下载 WAV</a><a href={url(item, 'mp3') + '?download=true'} download>下载 MP3</a></div>}
      </div>)}
      {!items.length && <p className="muted">尚未生成音频。新建空白歌曲需要先添加音符。</p>}
    </div>
  </article>;
}
