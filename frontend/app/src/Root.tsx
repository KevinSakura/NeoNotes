import { lazy, Suspense, useLayoutEffect, useState } from 'react';
import App from './App';
import { Icon, TopBar } from './StudioDesign';
import type { WorkspaceMode } from './StudioDesign';
import './practice/practice.css';
const PracticeWorkspace = lazy(() => import('./practice/Workspace'));
const TranscriptionWorkspace = lazy(() => import('./transcription/Workspace'));

const navigation = [
  ['home', '首页'],
  ['creation', '创作'],
  ['practice', '练习'],
  ['transcription', '扒谱'],
] as const;
const themeKey = 'neonote-theme';
type Theme = 'dark' | 'light';

function initialTheme(): Theme {
  try { if (localStorage.getItem(themeKey) === 'light') return 'light'; }
  catch { /* The theme still works when browser storage is disabled. */ }
  return 'dark';
}

export default function Root() {
  const [mode, setMode] = useState<WorkspaceMode>('creation');
  const [transcriptionOpened, setTranscriptionOpened] = useState(false);
  const [practiceOpened, setPracticeOpened] = useState(false);
  const [theme, setTheme] = useState(initialTheme);
  useLayoutEffect(() => {
    document.body.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#090b14' : '#f4f3fa');
    try { localStorage.setItem(themeKey, theme); }
    catch { /* The applied theme remains available for this visit. */ }
  }, [theme]);
  useLayoutEffect(() => { window.scrollTo(0, 0); }, [mode]);

  function choose(next: WorkspaceMode) {
    if (next === mode) return;
    window.dispatchEvent(new CustomEvent('neonote-stop-audio', {detail:'entry-change'}));
    document.querySelectorAll<HTMLAudioElement>('audio').forEach(a => a.pause());
    if (next === 'practice') setPracticeOpened(true);
    if (next === 'transcription') setTranscriptionOpened(true);
    setMode(next);
  }
  return <><nav className="rail glass" aria-label="选择工作台">
    <button className="studio-logo" aria-label="NeoNote 首页" onClick={() => choose('home')}><Icon name="logo" /></button>
    {navigation.map(([id, label]) => <button className="rail-btn" key={id} aria-pressed={mode === id} onClick={() => choose(id)}><Icon name={id} />{label}</button>)}
    <button className="rail-btn theme-toggle" aria-label="切换明暗主题" aria-pressed={theme === 'light'} title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'} onClick={() => setTheme(previous => previous === 'dark' ? 'light' : 'dark')}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} />主题
    </button>
  </nav>
  <div className="studio-shell">
    <div hidden={mode !== 'creation' && mode !== 'home'} inert={mode !== 'creation' && mode !== 'home'}>
      <App active={mode === 'creation'} home={mode === 'home'} chooseWorkspace={choose} />
    </div>
    {practiceOpened && <div hidden={mode !== 'practice'} inert={mode !== 'practice'}><TopBar title="练习工作台" /><Suspense fallback={<p>正在打开练习工作台…</p>}><PracticeWorkspace active={mode === 'practice'} /></Suspense></div>}
    {transcriptionOpened && <div hidden={mode !== 'transcription'} inert={mode !== 'transcription'}><TopBar title="扒谱工作台" /><Suspense fallback={<p>正在打开扒谱工作台…</p>}><TranscriptionWorkspace active={mode === 'transcription'} /></Suspense></div>}
  </div>
  </>;
}
