import { lazy, Suspense, useState } from 'react';
import App from './App';
import './practice/practice.css';
const PracticeWorkspace = lazy(() => import('./practice/Workspace'));

export default function Root() {
  const [mode, setMode] = useState<'creation' | 'practice'>('creation');
  const [practiceOpened, setPracticeOpened] = useState(false);
  function choose(next: typeof mode) {
    if (next === mode) return;
    window.dispatchEvent(new CustomEvent('neonote-stop-audio', {detail:'entry-change'}));
    document.querySelectorAll<HTMLAudioElement>('audio').forEach(a => a.pause());
    if (next === 'practice') setPracticeOpened(true);
    setMode(next);
  }
  return <><nav className="mode-switch" aria-label="选择工作台"><button aria-pressed={mode === 'creation'} onClick={() => choose('creation')}>创作</button><button aria-pressed={mode === 'practice'} onClick={() => choose('practice')}>练习</button></nav>
    <div hidden={mode !== 'creation'} inert={mode !== 'creation'}><App active={mode === 'creation'} /></div>
    {practiceOpened && <div hidden={mode !== 'practice'} inert={mode !== 'practice'}><Suspense fallback={<p>正在打开练习工作台…</p>}><PracticeWorkspace active={mode === 'practice'} /></Suspense></div>}
  </>;
}
