import { useEffect, useRef, useState } from 'react'
import { FileText, ScanText, ScanEye } from 'lucide-react'
import type { TranscriptData } from '../api/client'
import { formatTime } from '../api/format'
import WorkflowStep from './ui/WorkflowStep'

export default function TranscriptView({ transcript, hasSpeech, currentTime, onAnalyze, analysisMode, onAnalysisModeChange, hasCandidates, analyzing, disabled, onSeek, invalid }: {
  transcript: TranscriptData | null; hasSpeech: boolean; currentTime: number; onAnalyze: () => void; analyzing: boolean
  hasCandidates: boolean; disabled: boolean; onSeek: (seconds: number) => void; invalid: boolean
  analysisMode: 'transcript' | 'visual'; onAnalysisModeChange: (mode: 'transcript' | 'visual') => void
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
      <div className="analysis-modes" role="group" aria-label="Clip analysis source">
        <label><input type="radio" name="analysis-mode" checked={analysisMode === 'transcript'} disabled={disabled || !hasSpeech} onChange={() => onAnalysisModeChange('transcript')} />Speech</label>
        <label><input type="radio" name="analysis-mode" checked={analysisMode === 'visual'} disabled={disabled} onChange={() => onAnalysisModeChange('visual')} />Video visuals</label>
      </div>
      <button className="btn-accent" title={`Step 3: Analyze ${analysisMode === 'visual' ? 'video visuals' : 'transcript'}`} onClick={onAnalyze} disabled={disabled}><WorkflowStep step={3} />{analyzing ? <span className="spinner" /> : analysisMode === 'visual' ? <ScanEye size={15} /> : <ScanText size={15} />}{analyzing ? 'Finding highlights…' : hasCandidates ? 'Analyze again' : 'Find clip candidates'}</button>
      <p className="analysis-hint">{analysisMode === 'visual' ? 'Inspects sampled video frames with your image-capable analysis model. No transcription required.' : 'Ranks moments from the speech transcript using your analysis model.'}</p>
      {!followPlayback && hasSpeech && <button className="text-button" onClick={() => setFollowPlayback(true)}>Follow playback</button>}
    </div>
    <div className="panel-body" ref={scrollerRef} onScroll={() => setFollowPlayback(false)}>
       {!transcript && <div className="empty-state"><FileText size={30} strokeWidth={1.3} /><h3>{invalid ? 'Transcript could not be read' : 'Speech is optional.'}</h3><p>{invalid ? 'Transcribe again to recover speech. Visual analysis can still find highlights from video frames.' : 'Use Video visuals to find highlights in music, scenery, gameplay or any video without speech. Transcribe only when you need speech context or captions.'}</p></div>}
       {transcript && !hasSpeech && <div className="empty-state"><ScanEye size={30} /><h3>No speech, still plenty to see.</h3><p>Use Video visuals above to let an image-capable model suggest highlights. Manual cuts are also available.</p></div>}
       {transcript && hasSpeech && <div className="transcript-list">{transcript.segments.map((segment, index) => <div className={`transcript-segment ${index === activeIndex ? 'active' : ''}`} key={`${segment.start}-${index}`} ref={index === activeIndex ? activeRef : undefined}><button className="timestamp" aria-label={`Seek to ${formatTime(segment.start)}`} onClick={() => { setFollowPlayback(true); onSeek(segment.start) }}>{formatTime(segment.start)}</button><div>{segment.speaker && <span className="transcript-speaker">{segment.speaker}</span>}<p>{segment.text}</p></div></div>)}</div>}
    </div>
  </>
}
