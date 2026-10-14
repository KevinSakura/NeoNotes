import { useEffect, useState } from 'react';
import { request } from './api';
import LyricPractice from './LyricPractice';
import { noteName } from './music.mjs';
import type { PracticePlayer, Project, ReviewStatus, SongState } from './types';

interface Props { project: Project; state: SongState; dirty: boolean; busy: boolean; run: (action: () => Promise<void>) => Promise<void>; practice: PracticePlayer; reviewUpdated: () => void }

export default function ReviewPanel({ project, state, dirty, busy, run, practice, reviewUpdated }: Props) {
  const [status, setStatus] = useState<ReviewStatus | null>(null);
  const [checks, setChecks] = useState({ melody: false, arrangement: false, style: false, lyrics_sung: false });
  const [comment, setComment] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true; setStatus(null); setError(''); setChecks({ melody: false, arrangement: false, style: false, lyrics_sung: false });
    void request<ReviewStatus>(`/projects/${project.id}/review`).then(result => { if (alive) setStatus(result); }).catch(error => { if (alive) setError(error.message); });
    return () => { alive = false; };
  }, [project.id, project.current_revision, dirty]);
  const ready = checks.melody && checks.arrangement && checks.style && (!state.inputs.selected_lines.length || checks.lyrics_sung);
  async function refresh() {
    setError('');
    setChecks({ melody: false, arrangement: false, style: false, lyrics_sung: false });
    setStatus(await request<ReviewStatus>(`/projects/${project.id}/review`));
    reviewUpdated();
  }
  async function submit(approved: boolean) {
    setStatus(await request<ReviewStatus>(`/projects/${project.id}/review`, { method: 'POST', body: JSON.stringify({ base_revision: project.current_revision, approved, ...checks, comment }) }));
    reviewUpdated();
  }
  return <article className="card review-card" data-testid="review-panel">
    <div className="card-heading"><h2>试听与人工审核</h2><span className="tag">保存版本 v{project.revision.number}</span></div>
    {state.composition ? <><p>{state.composition.title}{state.composition.mock ? ' · 模拟曲' : ''}</p><p className="muted">{state.composition.style_summary}</p>
      <LyricPractice state={state} busy={busy} practice={practice} />
      {dirty && <p className="render-notice">当前还有未保存修改。先保存，再重新试听和审核这个版本。</p>}
      {status && !dirty && <div className="assessment-report" data-testid="assessment-report"><div className="card-heading"><h3>当前保存版本的结构核对</h3><button disabled={busy} onClick={() => void run(refresh)}>重新核对当前版本</button></div>
        <p className="muted">{status.assessment.duration_seconds.toFixed(3)} 秒 · {status.assessment.note_count} 个音符。以下核对不调用模型，也不评判音乐是否好听。</p>
        <ul className="assessment-checks">{status.assessment.checks.map(check => <li key={check.id} className={check.passed ? 'passed' : 'failed'}><b>{check.passed ? '✓' : '需处理'} {check.label}</b><span>{check.message}</span></li>)}</ul>
        {status.assessment.lyric_lines.map(line => <p className="line-metrics" key={line.line_index}>第 {line.line_index+1} 行 · {line.unit_count} 字/词、{line.note_count} 音符 · {line.start_seconds.toFixed(1)}–{line.end_seconds.toFixed(1)} 秒 · 音域 {noteName(line.pitch_low)}–{noteName(line.pitch_high)} · 最大跳进 {line.max_jump_semitones} 半音{line.breath_before_seconds !== null ? ` · 句前间隔 ${line.breath_before_seconds.toFixed(2)} 秒` : ''}{line.warnings.map(warning => <span className="practice-warning" key={warning}>{warning}</span>)}</p>)}
        {status.assessment.reference_lock && <p className="muted">程序固定原演奏 {status.assessment.reference_lock.note_count} 个音符；生成时过滤参考窗口内 {status.assessment.reference_lock.filtered_model_notes} 个模型音符。此记录说明生成过程，当前是否仍保留以本次核对为准。</p>}
        {!!status.assessment.musical_phrases?.length && <details><summary>音乐乐句与重音依据（需亲自试听）</summary>
          {status.assessment.musical_phrases.map(p => <div key={p.phrase_id}><b>{p.phrase_id} · 原歌词第{p.line_index + 1}行</b>
            <p className="hint">{p.start_seconds.toFixed(2)}–{p.sung_end_seconds.toFixed(2)} 秒；句末留白 {p.tail_rest_seconds.toFixed(2)} 秒。</p>
            {p.groups.filter(g => g.stress !== 'none' || g.note_count > 1).map(g => <p className="hint" key={g.uid}>“{g.text}” · {g.note_count}音 · 第{g.bar_beat + 1}拍 · {g.duration_seconds.toFixed(2)}秒 · 重音意图{g.stress === 'primary' ? '主要' : g.stress === 'secondary' ? '次要' : '无'}。标签与这些谱面数值需要合听确认。</p>)}
            {p.warnings.map(w => <p className="practice-warning" key={w}>{w}</p>)}
          </div>)}
        </details>}
        <p className="hint">请依次试唱每一句，留意音域、字词停留、跳进和换气，再试听四声部并与原旋律、主题及曲风对照。仍需你亲自确认。</p>
      </div>}
      {!status?.valid && status?.reason && <p className="render-notice">{status.reason}</p>}
      {error && <p role="alert" className="render-notice">{error}</p>}
      <div className="review-checks">{([['melody', '已试听旋律与段落发展'], ['arrangement', '已试听四声部配合与音色'], ['style', '已核对主题、曲风及原旋律约束'], ...(state.inputs.selected_lines.length ? [['lyrics_sung', '已亲自照所选歌词试唱，音域与停顿合适']] : [])] as [keyof typeof checks, string][]).map(([key, label]) => <label key={key}><input type="checkbox" disabled={busy || dirty} checked={checks[key]} onChange={event => setChecks(previous => ({ ...previous, [key]: event.target.checked }))} />{label}</label>)}</div>
      <label className="field">审核备注<textarea aria-label="审核备注" rows={2} maxLength={2000} value={comment} disabled={busy || dirty} onChange={event => setComment(event.target.value)} placeholder="例如：第二段需要更自然的停顿。" /></label>
      <div className="inline-actions"><button className="primary" disabled={busy || dirty || !ready || !status?.valid} onClick={() => void run(() => submit(true))}>记录审核通过</button><button disabled={busy || dirty} onClick={() => void run(() => submit(false))}>记录需要修改</button></div>
      {status?.review && <p className="hint" data-testid="review-result">v{status.review.revision_number} · {status.review.mock ? '模拟' : '人工'}审核{status.review.approved ? '通过' : '需要修改'}{status.review.comment ? `：${status.review.comment}` : ''}。{!status.valid && status.review.approved ? '这是此前的记录，当前核对失败，需处理后重新审核。' : '修改另存后须重新审核。'}</p>}
    </> : <p className="muted">先生成并采用一个候选，再试听和审核；当前仍可手动编辑与导出。</p>}
    <div className="full-song-boundary"><p>审核不会自动发起模型请求。{state.composition?.scope === 'lyrics' ? '当前已覆盖歌词全文，可试唱、整体修改及导出；修改另存后须重新审核。' : '60秒短曲审核通过后，可在下方明确生成整曲；整曲采用后还须重新试听/试唱和审核。'}</p></div>
  </article>;
}
