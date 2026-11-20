import { memo } from 'react';
import type { ReactNode } from 'react';
import type { ProjectItem, RecordedNote } from './types';

const icons = {
  logo: <path d="M5 19V6l7 9V5m3 0v10a3 3 0 1 0 3-3" />,
  home: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  creation: <><path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" /></>,
  practice: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M8 4v10M13 4v10M18 4v10M3 14h18" /></>,
  transcription: <path d="M2 12h2M6 8v8M10 4v16M14 7v10M18 10v4M22 12h.01" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon: <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />,
  trash: <path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  sparkle: <path fill="currentColor" stroke="none" d="M12 2l1.9 5.6L19.5 9.5l-5.6 1.9L12 17l-1.9-5.6L4.5 9.5l5.6-1.9zM19 15l.9 2.6 2.6.9-2.6.9L19 22l-.9-2.6-2.6-.9 2.6-.9z" />,
  upload: <path d="M12 16V4M7 9l5-5 5 5M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />,
  download: <path d="M12 4v12m-5-5 5 5 5-5M5 20h14" />,
};

export function Icon({ name }: { name: keyof typeof icons }) {
  return <svg className="studio-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{icons[name]}</svg>;
}

export function TopBar({ title, children }: { title: string; children?: ReactNode }) {
  return <header className="topbar glass">
    <div className="breadcrumb"><span>NeoNote</span><span aria-hidden="true">/</span><strong title={title}>{title}</strong></div>
    {children}
  </header>;
}

const bars = [21, 29, 18, 40, 58, 35, 54, 47, 72, 86, 69, 47, 87, 98, 80, 57, 72, 82, 53, 63, 86, 64, 44, 61, 33, 45, 29, 22];

export function StudioHero({ children }: { children: ReactNode }) {
  return <section className="studio-hero glass">
    <div>
      <span className="eyebrow">NeoNote · 新的乐章</span>
      <h1 aria-label="从一点灵感，开始一首歌。">从一点灵感，<br /><span className="gradient-text">开始一首歌。</span></h1>
      {children}
    </div>
    <div className="hero-visual" aria-hidden="true">
      <div className="hero-bars">{bars.map((height, index) => <span key={index} style={{ height: `${height}%` }} />)}</div>
      <div className="hero-keys">{Array.from({ length: 14 }, (_, index) => <span key={index} />)}</div>
    </div>
  </section>;
}

export const RecordingTimeline = memo(function RecordingTimeline({ notes, recording }: { notes: RecordedNote[]; recording: boolean }) {
  if (!notes.length) return <div className="record-timeline empty">{recording ? '正在录制，弹几个音试试。' : '录制后的音符会出现在这里。'}</div>;
  const duration = Math.max(...notes.map(note => note.start_seconds + note.duration_seconds));
  const pitches = notes.map(note => note.pitch);
  const low = Math.min(...pitches), high = Math.max(...pitches);
  return <div className="record-timeline">
    <svg viewBox="0 0 1000 52" preserveAspectRatio="none" role="img" aria-label={`录制时间线，共 ${notes.length} 个音符，${duration.toFixed(2)} 秒`}>
      {notes.map((note, index) => <rect key={index} data-family={note.family}
        x={1000 * note.start_seconds / duration} width={Math.max(2, 1000 * note.duration_seconds / duration)}
        y={high === low ? 23 : 4 + 36 * (high - note.pitch) / (high - low)}
        height="6" rx="3" fill="var(--track-color)" />)}
    </svg>
    <div className="timeline-times" aria-hidden="true"><span>0.00 秒</span><span>{duration.toFixed(2)} 秒</span></div>
  </div>;
});

const entries = [
  { id: 'creation', title: '创作', label: '进入创作', text: '弹出一段旋律，写下主题与歌词，用 AI 编排键盘、鼓、吉他与贝斯。', features: ['虚拟键盘录制与回放', '四声部编辑与自然语言修改', 'MIDI、音频与演唱导出'] },
  { id: 'practice', title: '练习', label: '进入练习', text: '对着五线谱认音、打节奏。错了可以纠正，首次答案与纠正分别保留。', features: ['本地新手单元', '认音轮与节奏轮', '导入 MIDI 自建练习'] },
  { id: 'transcription', title: '扒谱', label: '进入扒谱', text: '把钢琴录音转成 MIDI，试听校对后另存修订并整理成五线谱。', features: ['MP3 / WAV 选段转录', '混音钢琴分离（试验）', 'MIDI 与 MusicXML 导出'] },
] as const;

export type WorkspaceMode = 'home' | typeof entries[number]['id'];

export function HomeView({ items, busy, choose, openSong }: {
  items: ProjectItem[]; busy: boolean; choose: (mode: WorkspaceMode) => void; openSong: (item: ProjectItem) => void;
}) {
  return <>
    <TopBar title="新的乐章" />
    <main className="studio-home">
      <StudioHero>
        <p>弹几个音，写一段话，或带上你的歌词。NeoNote 帮你把旋律、情感和故事编成可编辑、可演奏的作品。</p>
        <div className="inline-actions">
          <button className="btn lg primary" onClick={() => choose('creation')}><Icon name="sparkle" />开始创作</button>
          <button className="btn lg" onClick={() => choose('practice')}>先练练手</button>
        </div>
      </StudioHero>
      <div className="studio-entries">
        {entries.map(entry => <button className={`entry-card glass entry-${entry.id}`} key={entry.id} onClick={() => choose(entry.id)}>
          <span className="entry-icon"><Icon name={entry.id} /></span>
          <strong>{entry.title}</strong>
          <span className="entry-copy">{entry.text}</span>
          <span className="entry-features">{entry.features.map(feature => <span key={feature}>{feature}</span>)}</span>
          <span className="entry-link">{entry.label} →</span>
        </button>)}
      </div>
      <section className="recent-songs">
        <h2>最近的作品</h2>
        {items.length > 0 ? <div className="recent-grid">
          {items.slice(0, 4).map(item => <button className="glass recent-song" key={item.id} disabled={busy} onClick={() => openSong(item)}>
            <span className="recent-art"><Icon name="creation" /></span>
            <strong>{item.name}</strong>
            <small>更新于 {new Date(item.updated_at).toLocaleDateString()}</small>
          </button>)}
        </div> : <p className="muted">还没有本地歌曲。在创作工作台新建后，作品会显示在这里。</p>}
      </section>
    </main>
  </>;
}
