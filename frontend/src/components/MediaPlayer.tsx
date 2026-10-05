import { useEffect, useRef, useState } from 'react'
import { Film, Mic } from 'lucide-react'
import { api } from '../api/client'
import { formatTime } from '../api/format'
import WorkflowStep from './ui/WorkflowStep'

export default function MediaPlayer({ projectId, hasTranscript, onTimeUpdate, onTranscribe, seekRequest, transcribing, disabled, mode }: {
  projectId: string; hasTranscript: boolean; onTimeUpdate: (time: number) => void; onTranscribe: () => void
  seekRequest: { time: number; sequence: number } | null; transcribing: boolean; disabled: boolean; mode: string
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const pendingSeek = useRef<number | null>(null)
  const [duration, setDuration] = useState(0)
  const [time, setTime] = useState(0)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!seekRequest) return
    const video = videoRef.current
    if (!video || video.readyState < 1) { pendingSeek.current = seekRequest.time; return }
    video.currentTime = Math.max(0, Math.min(seekRequest.time, Number.isFinite(video.duration) ? video.duration : seekRequest.time))
    onTimeUpdate(video.currentTime)
    setTime(video.currentTime)
  }, [seekRequest, onTimeUpdate])
  return <>
    <div className="panel-heading"><div className="inline"><Film size={16} /><h2>Source media</h2></div></div>
    <div className="media-stage">
      <video key={attempt} ref={videoRef} className="source-video" src={api.mediaFileUrl(projectId)} controls preload="metadata" playsInline aria-label="Source media player"
        onTimeUpdate={(event) => { const next = event.currentTarget.currentTime; setTime(next); onTimeUpdate(next) }}
        onLoadedMetadata={(event) => {
          setError(false); setDuration(event.currentTarget.duration)
          if (pendingSeek.current !== null) { event.currentTarget.currentTime = Math.min(pendingSeek.current, event.currentTarget.duration); pendingSeek.current = null }
        }} onError={() => setError(true)} />
    </div>
    <div className="media-details">
      {error && <div className="notice notice-error" role="alert"><span>This browser could not play the source. The file may be unavailable or use an unsupported codec.</span><button className="btn-secondary" onClick={() => { setError(false); setAttempt((value) => value + 1) }}>Retry</button></div>}
      <div className="media-meta"><span className="mono">{formatTime(time)} / {formatTime(duration)}</span><span>Project default: {mode === 'local' || mode === 'whisper' ? 'Local Whisper' : 'Cloud Deepgram'}</span></div>
      <p className="media-instruction">Select a timestamp in the transcript or a clip candidate to seek to that moment.</p>
      <button className={hasTranscript ? 'btn-secondary transcription-action' : 'btn-accent transcription-action'} title="Step 2: Transcribe source audio" disabled={disabled} onClick={onTranscribe}><WorkflowStep step={2} />{transcribing ? <span className="spinner" /> : <Mic size={16} />}{transcribing ? 'Transcribing…' : hasTranscript ? 'Transcribe again' : 'Transcribe audio'}</button>
      <p className="field-hint">An active transcription profile in Settings overrides this project default.</p>
      <p className="muted">{hasTranscript ? 'Your transcript is ready to review. Analyze it to find clip candidates.' : 'Start with a transcript. Then use analysis to find moments worth clipping.'}</p>
    </div>
  </>
}
