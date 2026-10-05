import { useMemo, useState, type FormEvent } from 'react'
import { Download, Play, Scissors, SlidersHorizontal } from 'lucide-react'
import { api, type Candidate, type Clip } from '../api/client'
import { formatTime } from '../api/format'
import { isActiveJob, isFailedJob, latestClips } from '../api/jobs'
import ClipPreview from './ClipPreview'
import WorkflowStep from './ui/WorkflowStep'

export default function CandidateList({ candidates, clips, sourceDuration, preferredClipIds, onCreateManualCut, onRender, onSeek, onSelectToggle, selectionBusy, renderBusy, captionStyle, onOpenControls, disabled }: {
  candidates: Candidate[]; clips: Clip[]; onRender: (candidate: Candidate) => void; onSeek: (time: number) => void
  sourceDuration: number; preferredClipIds: Record<string, string>; onCreateManualCut: (startSec: number, endSec: number, title: string) => void
  onSelectToggle: (candidate: Candidate, selected: boolean) => void; selectionBusy: boolean; renderBusy: boolean
  captionStyle: string; onOpenControls: () => void; disabled: boolean
}) {
  const byCandidate = useMemo(() => latestClips(clips), [clips])
  const completed = useMemo(() => latestClips(clips.filter((clip) => clip.status === 'done')), [clips])
  const [preview, setPreview] = useState<{ candidate: Candidate; clip: Clip } | null>(null)
  const [manualOpen, setManualOpen] = useState(false)
  const [manualStart, setManualStart] = useState('0')
  const [manualEnd, setManualEnd] = useState(String(Math.min(sourceDuration || 10, 30)))
  const [manualTitle, setManualTitle] = useState('')
  const submitManual = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const start = Number(manualStart); const end = Number(manualEnd)
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) onCreateManualCut(start, end, manualTitle)
  }
  return <>
    <div className="panel-heading"><div className="inline"><Scissors size={16} /><h2>Clip candidates</h2><span className="count-label">{candidates.length}</span></div></div>
    <div className="candidate-tools"><button className="btn-secondary" aria-expanded={manualOpen} onClick={() => setManualOpen((value) => !value)}><Scissors size={16} />{manualOpen ? 'Hide manual cut' : 'Create manual cut'}</button><button className="btn-secondary" onClick={onOpenControls}><SlidersHorizontal size={16} />Caption Controls & filters</button></div>
    <div className="candidate-toolbar"><span>Next render: {captionStyle}</span><p className="field-hint">Downloads keep their rendered look. Re-render for changes.</p></div>
    {manualOpen && <form className="manual-cut-form" onSubmit={submitManual}><strong>Create a manual cut</strong><p className="field-hint">Choose an exact time range from any video.</p><div className="form-grid"><label className="field">Start (seconds)<input className="input-field" type="number" min="0" max={sourceDuration || undefined} step=".01" value={manualStart} onChange={(event) => setManualStart(event.target.value)} required /></label><label className="field">End (seconds)<input className="input-field" type="number" min="0" max={sourceDuration || undefined} step=".01" value={manualEnd} onChange={(event) => setManualEnd(event.target.value)} required /></label><label className="field full-width">Title <span className="field-hint">Optional</span><input className="input-field" maxLength={200} value={manualTitle} onChange={(event) => setManualTitle(event.target.value)} placeholder="Manual cut" /></label></div><button className="btn-accent" disabled={disabled || selectionBusy || renderBusy || !sourceDuration}><WorkflowStep step={4} />Add manual cut</button>{!sourceDuration && <p className="field-hint">Source duration is unavailable; re-import or probe the media first.</p>}</form>}
    <div className="panel-body candidate-list">
      {candidates.length === 0 && <div className="empty-state"><Scissors size={30} strokeWidth={1.3} /><h3>Find your strongest moments.</h3><p>Analyze speech or video visuals for AI highlights. Create a manual cut when you want an exact time range.</p></div>}
      {candidates.map((candidate) => {
        const clip = byCandidate.get(candidate.id)
        const active = isActiveJob(clip?.status)
        const failed = isFailedJob(clip?.status)
         const preferred = preferredClipIds[candidate.id] ? clips.find((item) => item.id === preferredClipIds[candidate.id] && item.status === 'done') : undefined
         const doneClip = preferred || completed.get(candidate.id)
        const done = !!doneClip
        return <article key={candidate.id} className={`candidate-card ${candidate.selected ? 'selected' : ''}`}>
          <div className="section-heading"><div className="inline compact"><span className="badge badge-neutral">{String(candidate.rank).padStart(2, '0')}</span><button className="timestamp" aria-label={`Seek to clip ${candidate.rank} at ${formatTime(candidate.start_sec)}`} onClick={() => onSeek(candidate.start_sec)}>{formatTime(candidate.start_sec)} – {formatTime(candidate.end_sec)}</button></div><div className="candidate-score" title="AI suggestion score">{Math.round(candidate.score)}<span>/100</span></div></div>
          <h3>{candidate.hook}</h3><p className="rationale">{candidate.rationale}</p>
          {failed && <details className="clip-error"><summary>Render failed · view details</summary><p>{clip?.render_log || 'The render did not complete. Try rendering this clip again.'}</p></details>}
          {doneClip && <><span className="badge badge-success align-start">{active || failed ? 'Previous render available' : 'Ready to download'}</span><div className="clip-actions"><button className="btn-secondary" onClick={() => setPreview({ candidate, clip: doneClip })}><Play size={14} />Preview</button><a className="btn-secondary" title="Step 6: Download completed MP4" href={api.clipFileUrl(doneClip.id)} download><WorkflowStep step={6} /><Download size={14} />Download MP4</a></div></>}
          <div className="candidate-footer"><label title="Step 4: Select clips to keep"><input type="checkbox" checked={!!candidate.selected} disabled={selectionBusy || disabled || renderBusy} onChange={(event) => onSelectToggle(candidate, event.target.checked)} aria-label={`Select clip ${candidate.rank}: ${candidate.hook}`} /><WorkflowStep step={4} />Select</label><button className={done ? 'btn-secondary' : 'btn-accent'} title="Step 5: Render this clip" disabled={active || renderBusy || disabled || selectionBusy} onClick={() => onRender(candidate)}><WorkflowStep step={5} />{active ? <span className="spinner" /> : <Scissors size={13} />}{active ? (clip?.status === 'pending' || clip?.status === 'queued' ? 'Queued…' : 'Rendering…') : failed ? 'Retry render' : done ? 'Re-render' : 'Render clip'}</button></div>
        </article>
      })}
    </div>
    {preview && <ClipPreview clip={preview.clip} candidate={preview.candidate} onClose={() => setPreview(null)} />}
  </>
}
