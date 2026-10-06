import { useEffect, useRef, useState } from 'react'
import { FileText, ScanText, ScanEye } from 'lucide-react'
import type { AudienceBrief, TranscriptData } from '../api/client'
import { audienceFieldLimits } from '../api/audience'
import { formatTime } from '../api/format'
import WorkflowStep from './ui/WorkflowStep'

export default function TranscriptView({ transcript, hasSpeech, currentTime, onAnalyze, analysisMode, onAnalysisModeChange, audienceBrief, onAudienceChange, hasCandidates, analyzing, disabled, onSeek, invalid }: {
  transcript: TranscriptData | null; hasSpeech: boolean; currentTime: number; onAnalyze: () => void; analyzing: boolean
  hasCandidates: boolean; disabled: boolean; onSeek: (seconds: number) => void; invalid: boolean
  analysisMode: 'transcript' | 'visual'; onAnalysisModeChange: (mode: 'transcript' | 'visual') => void
  audienceBrief: AudienceBrief; onAudienceChange: (brief: AudienceBrief) => void
}) {
  const activeRef = useRef<HTMLDivElement>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const [followPlayback, setFollowPlayback] = useState(true)
  const activeIndex = transcript?.segments.findIndex((segment) => currentTime >= segment.start && currentTime < segment.end) ?? -1
  useEffect(() => {
    const active = activeRef.current
    const scroller = scrollerRef.current
    if (!active || !scroller || !followPlayback || scroller.contains(document.activeElement)) return
    const item = active.getBoundingClientRect()
    const container = scroller.getBoundingClientRect()
    // Move only the transcript scroller, never the page or keyboard focus.
    if (item.top < container.top || item.bottom > container.bottom) scroller.scrollTop += item.top - container.top - 20
  }, [activeIndex, followPlayback])
  return <>
    <div className="panel-heading"><div className="inline"><FileText size={16} /><h2>Transcript</h2>{transcript?.language && <span className="badge badge-neutral">{transcript.language.toUpperCase()}</span>}</div></div>
    <div className="analysis-toolbar">
      <details className="audience-brief"><summary>Audience & takeaway{audienceBrief.audience.trim() ? ` · ${audienceBrief.audience.slice(0, 60)}` : ''}</summary><fieldset disabled={disabled} className="stack compact">
        <label className="field">Target audience<input className="input-field" maxLength={audienceFieldLimits.audience} value={audienceBrief.audience} onChange={(event) => onAudienceChange({ ...audienceBrief, audience: event.target.value })} placeholder="For example, students learning public speaking" /></label>
        <label className="field">Viewer takeaway<input className="input-field" maxLength={audienceFieldLimits.goal} value={audienceBrief.goal} onChange={(event) => onAudienceChange({ ...audienceBrief, goal: event.target.value })} placeholder="A practical way to make a confident opening" /></label>
        <label className="field">Audience notes & exclusions<textarea className="input-field" rows={2} maxLength={audienceFieldLimits.notes} value={audienceBrief.notes} onChange={(event) => onAudienceChange({ ...audienceBrief, notes: event.target.value })} placeholder="Knowledge level, topics to emphasize, and parts to avoid" /></label>
        <p className="field-hint">Shared with AI Edit and editing suggestions. If the audience is blank, the model labels its source-based audience assumption.</p>
      </fieldset></details>
      <div className="analysis-modes" role="group" aria-label="Clip analysis source">
        <label><input type="radio" name="analysis-mode" checked={analysisMode === 'transcript'} disabled={disabled || !hasSpeech} onChange={() => onAnalysisModeChange('transcript')} />Speech</label>
        <label><input type="radio" name="analysis-mode" checked={analysisMode === 'visual'} disabled={disabled} onChange={() => onAnalysisModeChange('visual')} />Video visuals</label>
      </div>
      <button className="btn-accent" title={`Step 3: Analyze ${analysisMode === 'visual' ? 'video visuals' : 'transcript'}`} onClick={onAnalyze} disabled={disabled}><WorkflowStep step={3} />{analyzing ? <span className="spinner" /> : analysisMode === 'visual' ? <ScanEye size={15} /> : <ScanText size={15} />}{analyzing ? 'Finding highlights…' : hasCandidates ? 'Analyze again' : 'Find clip candidates'}</button>
      <p className="analysis-hint">{analysisMode === 'visual' ? 'Ranks visible moments for your audience using sampled-frame evidence. No transcription required.' : 'Finds audience-relevant ideas from timed speech, then reviews their opening and payoff.'}</p>
      {!followPlayback && hasSpeech && <button className="text-button" onClick={() => setFollowPlayback(true)}>Follow playback</button>}
    </div>
    <div className="panel-body" ref={scrollerRef} onScroll={() => setFollowPlayback(false)}>
       {!transcript && <div className="empty-state"><FileText size={30} strokeWidth={1.3} /><h3>{invalid ? 'Transcript could not be read' : 'Speech is optional.'}</h3><p>{invalid ? 'Transcribe again to recover speech. Visual analysis can still find highlights from video frames.' : 'Use Video visuals to find highlights in music, scenery, gameplay or any video without speech. Transcribe only when you need speech context or captions.'}</p></div>}
       {transcript && !hasSpeech && <div className="empty-state"><ScanEye size={30} /><h3>No speech, still plenty to see.</h3><p>Use Video visuals above to let an image-capable model suggest highlights. Manual cuts are also available.</p></div>}
       {transcript && hasSpeech && <div className="transcript-list">{transcript.segments.map((segment, index) => <div className={`transcript-segment ${index === activeIndex ? 'active' : ''}`} key={`${segment.start}-${index}`} ref={index === activeIndex ? activeRef : undefined}><button className="timestamp" aria-label={`Seek to ${formatTime(segment.start)}`} onClick={() => { setFollowPlayback(true); onSeek(segment.start) }}>{formatTime(segment.start)}</button><div>{segment.speaker && <span className="transcript-speaker">{segment.speaker}</span>}<p>{segment.text}</p></div></div>)}</div>}
    </div>
  </>
}
