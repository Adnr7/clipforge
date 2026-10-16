import { useEffect, useRef, useState } from 'react'
import { FileText, ScanEye, Sparkles } from 'lucide-react'
import type { AudienceBrief, TranscriptData } from '../api/client'
import { audienceFieldLimits } from '../api/audience'
import { formatTime } from '../api/format'
import WorkflowStep from './ui/WorkflowStep'

export default function TranscriptView({ transcript, hasSpeech, currentTime, onAnalyze, audienceBrief, onAudienceChange, hasCandidates, analyzing, disabled, onSeek, invalid }: {
  transcript: TranscriptData | null; hasSpeech: boolean; currentTime: number; onAnalyze: () => void; analyzing: boolean
  hasCandidates: boolean; disabled: boolean; onSeek: (seconds: number) => void; invalid: boolean
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
      <button className="btn-accent" title="Step 3: Let AI find the strongest clips from audio and video" onClick={onAnalyze} disabled={disabled}><WorkflowStep step={3} />{analyzing ? <span className="spinner" /> : <Sparkles size={15} />}{analyzing ? 'Finding highlights…' : hasCandidates ? 'Analyze again' : 'Find clip candidates'}</button>
      <p className="analysis-hint">AI reviews the full timed speech transcript and sampled video frames together, then chooses complete clips for your audience. Speech is optional for video sources.</p>
      {!followPlayback && hasSpeech && <button className="text-button" onClick={() => setFollowPlayback(true)}>Follow playback</button>}
    </div>
    <div className="panel-body" ref={scrollerRef} onScroll={() => setFollowPlayback(false)}>
        {!transcript && <div className="empty-state"><FileText size={30} strokeWidth={1.3} /><h3>{invalid ? 'Transcript could not be read' : 'Speech is optional.'}</h3><p>{invalid ? 'Transcribe again to restore audio context. AI can still inspect video frames.' : 'AI can find highlights from the video even without speech. Transcribe when you want spoken context or captions.'}</p></div>}
        {transcript && !hasSpeech && <div className="empty-state"><ScanEye size={30} /><h3>No speech, still plenty to see.</h3><p>AI will use the visible video evidence to suggest highlights. Manual cuts are also available.</p></div>}
       {transcript && hasSpeech && <div className="transcript-list">{transcript.segments.map((segment, index) => <div className={`transcript-segment ${index === activeIndex ? 'active' : ''}`} key={`${segment.start}-${index}`} ref={index === activeIndex ? activeRef : undefined}><button className="timestamp" aria-label={`Seek to ${formatTime(segment.start)}`} onClick={() => { setFollowPlayback(true); onSeek(segment.start) }}>{formatTime(segment.start)}</button><div>{segment.speaker && <span className="transcript-speaker">{segment.speaker}</span>}<p>{segment.text}</p></div></div>)}</div>}
    </div>
  </>
}
