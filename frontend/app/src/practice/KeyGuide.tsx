const whiteKeys = [['s', 'C'], ['d', 'D'], ['f', 'E'], ['g', 'F'], ['h', 'G'], ['j', 'A'], ['k', 'B'], ['l', '高C']];
const blackKeys = [['e', 'C♯'], ['r', 'D♯'], ['y', 'F♯'], ['u', 'G♯'], ['i', 'A♯']];

export default function KeyGuide() {
  return <aside className="practice-key-guide" aria-label="练习按键提醒">
    <div className="card-heading"><h3>按键提醒</h3><span className="tag">练习键位</span></div>
    <p className="practice-key-guide-label">白键</p>
    <div className="practice-key-guide-white">{whiteKeys.map(([key, note]) => <span key={key}><kbd>{key}</kbd><span>{note}</span></span>)}</div>
    <p className="practice-key-guide-label">黑键</p>
    <div className="practice-key-guide-black">{blackKeys.map(([key, note]) => <span key={key}><kbd>{key}</kbd><span>{note}</span></span>)}</div>
    <p><kbd>Shift</kbd> 单按降一档<br /><kbd>空格</kbd> 单按升一档</p>
    <p>按住 Shift / 空格再弹音符：同时弹基础音和低 / 高八度，组合不切档。</p>
    <p>l 始终是右侧高一个八度的 C。两个修饰键同时按住时不接受新音。</p>
    <small>基础八度可选3–5档，当前档位见下方练习区域。</small>
  </aside>;
}
