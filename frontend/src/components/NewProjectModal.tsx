import { useEffect, useRef, useState } from 'react'
import { FileUp, FolderOpen, Link, Plus } from 'lucide-react'
import { api, errorMessage, type TranscriptionMode, type YoutubeDownloadStatus, type YoutubeMetadata } from '../api/client'
import { formatTime } from '../api/format'
import { useToast } from '../hooks/useToast'
import Modal from './ui/Modal'
import WorkflowStep from './ui/WorkflowStep'

type Source = 'file' | 'path' | 'youtube'
interface DownloadJob { id: string; name: string; mode: TranscriptionMode }
export default function NewProjectModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [source, setSource] = useState<Source>('file')
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [path, setPath] = useState('')
  const [url, setUrl] = useState('')
  const [mode, setMode] = useState<TranscriptionMode>('cloud')
  const modeChanged = useRef(false)
  const [metadata, setMetadata] = useState<YoutubeMetadata | null>(null)
  const [job, setJob] = useState<DownloadJob | null>(null)
  const [download, setDownload] = useState<YoutubeDownloadStatus | null>(null)
  const [downloadedPath, setDownloadedPath] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pollPaused, setPollPaused] = useState(false)
  const [pollAttempt, setPollAttempt] = useState(0)
  const operation = useRef<AbortController | null>(null)
  const createdRef = useRef(onCreated)
  useEffect(() => { createdRef.current = onCreated }, [onCreated])
  const { showToast } = useToast()
  useEffect(() => {
    const controller = new AbortController()
    api.getSettings(controller.signal).then((settings) => { if (!modeChanged.current) setMode(settings.transcriptionMode === 'local' ? 'local' : 'cloud') }).catch((error: unknown) => { if (!controller.signal.aborted) setError(`Could not load the default transcription mode. Choose a mode below. ${errorMessage(error)}`) })
    return () => { controller.abort(); operation.current?.abort() }
  }, [])

  useEffect(() => {
    if (!job) return
    const controller = new AbortController()
    const { signal } = controller
    let timer: ReturnType<typeof setTimeout>
    let failures = 0
    const poll = async () => {
      try {
        const next = await api.getYoutubeDownloadStatus(job.id, signal)
        if (signal.aborted) return
        setDownload(next); setError(null); failures = 0
        if (next.status === 'done') {
          if (!next.path) { setError('Download finished without a media path. Please try again.'); setJob(null); setBusy(null); return }
          setDownloadedPath(next.path)
          setBusy('Creating project…')
          try {
            const project = await api.createProject({ name: job.name, sourcePath: next.path, transcriptionMode: job.mode }, signal)
            if (!signal.aborted) { showToast('success', 'YouTube video imported'); createdRef.current(project.id) }
          } catch (error) { if (!signal.aborted) { setError(errorMessage(error)); setJob(null); setBusy(null) } }
          return
        }
        if (['error', 'failed', 'cancelled', 'unknown'].includes(next.status)) {
          setError(next.error || `Download ${next.status}. Please try again.`); setJob(null); setBusy(null); return
        }
      } catch (error) {
        if (signal.aborted) return
        failures += 1
        setError(`Unable to check download progress. Retrying… ${errorMessage(error)}`)
        if (failures >= 5) {
          setPollPaused(true)
          setError(`Progress checks paused after repeated connection failures. Retry checking the existing download. ${errorMessage(error)}`)
          return
        }
      }
      if (!signal.aborted) timer = setTimeout(() => void poll(), Math.min(1500 * (failures + 1), 10000))
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [job, showToast, pollAttempt])

  const submit = async () => {
    if (busy || job) return
    operation.current?.abort()
    const controller = new AbortController()
    operation.current = controller
    const { signal } = controller
    setError(null)
    try {
      if (source === 'youtube' && !metadata) {
        const parsed = new URL(url.trim())
        if (!['https:', 'http:'].includes(parsed.protocol) || !(parsed.hostname === 'youtu.be' || parsed.hostname === 'youtube.com' || parsed.hostname.endsWith('.youtube.com'))) throw new Error('Enter a valid YouTube video URL.')
        setBusy('Checking video…')
        const info = await api.checkYoutube(url.trim(), signal)
        if (!info.ok) throw new Error(info.error || 'Could not inspect this video.')
        setMetadata(info)
        if (!name.trim()) setName(info.title || '')
      } else if (source === 'youtube' && !downloadedPath) {
        setBusy('Downloading video…'); setDownload(null)
        const result = await api.downloadYoutube(url.trim(), signal)
        setPollPaused(false); setJob({ id: result.jobId, name: name.trim() || metadata?.title || 'YouTube project', mode })
      } else {
        setBusy(source === 'file' ? 'Uploading & inspecting media…' : 'Creating project…')
        const projectName = name.trim() || file?.name.replace(/\.[^.]+$/, '') || 'Untitled project'
        if (source === 'file' && !file) throw new Error('Choose a video or audio file first.')
        const result = source === 'file' && file
          ? await api.uploadProject(file, projectName, mode, signal)
          : await api.createProject({ name: projectName, sourcePath: source === 'youtube' ? downloadedPath : path.trim(), transcriptionMode: mode }, signal)
        if (!signal.aborted) { showToast('success', 'Project created'); createdRef.current(result.id) }
      }
    } catch (error) { if (!signal.aborted) { setError(errorMessage(error)); showToast('error', errorMessage(error)) } }
    finally { if (!signal.aborted) setBusy(null) }
  }
  const working = !!busy || !!job
  const progress = Math.max(0, Math.min(100, download?.progress || 0))
  const logs = Array.isArray(download?.log) ? download.log.join('\n') : download?.log
  return <Modal title="New project" onClose={onClose} width={620}>
    <form className="stack" onSubmit={(event) => { event.preventDefault(); void submit() }}>
      <p className="muted">Start with a source. ClipForge will inspect the media and prepare your workspace.</p>
      <fieldset className="stack" disabled={working}>
        <legend className="sr-only">Project details</legend>
        <div className="source-options" role="group" aria-label="Media source">{([{ id: 'file', label: 'Local file', Icon: FileUp }, { id: 'path', label: 'Server path', Icon: FolderOpen }, { id: 'youtube', label: 'YouTube', Icon: Link }] as const).map(({ id, label, Icon }) => <button key={id} type="button" className={source === id ? 'selected' : ''} aria-pressed={source === id} onClick={() => { if (id !== source) setFile(null); setSource(id); setError(null) }}><Icon size={17} />{label}</button>)}</div>
        <label className="field">Project name <span className="field-hint">Optional</span><input className="input-field" placeholder="e.g. In conversation — Episode 12" value={name} onChange={(event) => setName(event.target.value)} maxLength={200} /></label>
        {source === 'file' && <div className="file-import"><FileUp size={28} /><label htmlFor="source-file">Choose a video or audio file</label><p className="muted">Your file is uploaded to the ClipForge server.</p><input id="source-file" className="input-field" type="file" accept="video/*,audio/*,.mkv,.mov,.mp4,.webm,.mp3,.wav,.m4a,.flac" required onChange={(event) => setFile(event.target.files?.[0] || null)} />{file && <p className="muted">{file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB</p>}</div>}
        {source === 'path' && <label className="field">Full server file path<input className="input-field mono" value={path} onChange={(event) => setPath(event.target.value)} placeholder="/path/to/media.mp4" required /><span className="field-hint">Use a file that already exists on the machine running ClipForge. This is optional; use Local file to upload from your device.</span></label>}
        {source === 'youtube' && <><label className="field">YouTube video URL<input className="input-field" type="url" required placeholder="https://www.youtube.com/watch?v=…" value={url} onChange={(event) => { setUrl(event.target.value); setMetadata(null); setDownload(null); setDownloadedPath('') }} /></label>{metadata && <div className="youtube-details"><p className="eyebrow">VIDEO DETAILS</p><h3>{metadata.title || 'YouTube video'}</h3><p className="muted">{metadata.uploader || 'Unknown uploader'} · {formatTime(metadata.duration)}</p><p className="muted">{metadata.license || 'License not provided'}</p>{metadata.reason && <p className="metadata-reason">{metadata.reason}</p>}</div>}</>}
        <label className="field">Transcription mode<select className="input-field" value={mode} onChange={(event) => { modeChanged.current = true; setMode(event.target.value as TranscriptionMode) }}><option value="cloud">Cloud · Deepgram</option><option value="local">Local · Whisper</option></select><span className="field-hint">{mode === 'cloud' ? 'Deepgram sends audio to the cloud and requires a saved API key.' : 'Whisper runs locally on the ClipForge server and requires the Whisper package.'} This is the project default; an active transcription profile in Settings takes priority.</span></label>
      </fieldset>
      {working && <div className="download-progress" role="status"><div className="section-heading"><span className="inline"><span className="spinner" />{busy || 'Downloading video…'}</span>{job && <span className="mono">{Math.round(progress)}%</span>}</div>{job && <progress aria-label="YouTube download progress" max={100} value={progress} />}<p className="muted">Keep this dialog open to create the project when the import finishes.</p></div>}
      {logs && <details className="log-details"><summary>Download log</summary><pre>{logs}</pre></details>}
      {error && <p className="notice notice-error" role="alert">{error}</p>}
      {pollPaused && <button type="button" className="btn-secondary" onClick={() => { setPollPaused(false); setPollAttempt((attempt) => attempt + 1) }}>Retry progress check</button>}
      <div className="form-actions"><button type="button" className="btn-secondary" onClick={onClose}>Close</button><button className="btn-accent" title="Step 1: Import media" disabled={working || (source === 'file' && !file) || (source === 'path' && !path.trim()) || (source === 'youtube' && !url.trim())}><WorkflowStep step={1} />{working ? <span className="spinner" /> : <Plus size={16} />}{source === 'youtube' && !metadata ? 'Check video' : source === 'youtube' && !downloadedPath ? 'Download & create' : 'Create project'}</button></div>
    </form>
  </Modal>
}
