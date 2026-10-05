import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Download, Scissors, Settings, Sparkles, XCircle } from 'lucide-react'
import { api, errorMessage, type Candidate, type CaptionSettings, type Clip, type FilterSuggestion, type OutputSettings, type RenderSettingsContract, type TranscriptData, type VideoFilters } from '../api/client'
import { aiEditApi } from '../api/aiEdit'
import { fallbackRenderContract } from '../api/renderDefaults'
import { isActiveJob, isFailedJob, latestClips } from '../api/jobs'
import { useProject } from '../hooks/useProject'
import { useToast } from '../hooks/useToast'
import MediaPlayer from './MediaPlayer'
import TranscriptView from './TranscriptView'
import CandidateList from './CandidateList'
import PipelineStatus, { type PipelineStage } from './PipelineStatus'
import ThemeSelect from './ui/ThemeSelect'
import Modal from './ui/Modal'
import RenderControls from './RenderControls'
import WorkflowStep from './ui/WorkflowStep'
import AiEditMode from './AiEditMode'

function parseTranscript(raw: string | undefined): TranscriptData | null {
  if (!raw) return null
  try {
    const data = JSON.parse(raw) as Partial<TranscriptData>
    const validSegment = (segment: TranscriptData['segments'][number]) => segment && typeof segment.text === 'string' && Number.isFinite(segment.start) && Number.isFinite(segment.end)
    const words = Array.isArray(data.words) ? data.words.filter(validSegment) : []
    const segments = Array.isArray(data.segments) && data.segments.length ? data.segments.filter(validSegment) : words
    if (!Array.isArray(data.segments) && !Array.isArray(data.words)) return null
    return { language: typeof data.language === 'string' ? data.language : '', duration: data.duration || 0, speakers: data.speakers || [], words, segments }
  } catch { return null }
}

function sameSettings(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false
  const a = left as Record<string, unknown>; const b = right as Record<string, unknown>
  return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((key) => sameSettings(a[key], b[key]))
}

export default function Workspace({ projectId, onBack, onOpenSettings }: { projectId: string; onBack: () => void; onOpenSettings: () => void }) {
  const { detail, jobs, loading, error, refresh, trackClips } = useProject(projectId)
  const { showToast } = useToast()
  const [editMode, setEditMode] = useState<'manual' | 'ai'>('manual')
  const [aiVisited, setAiVisited] = useState(false)
  const [aiBusy, setAiBusy] = useState(false)
  const manualGrid = useRef<HTMLDivElement>(null)
  useEffect(() => { if (editMode === 'ai') manualGrid.current?.querySelector<HTMLVideoElement>('video')?.pause() }, [editMode])
  const [currentTime, setCurrentTime] = useState(0)
  const [seekRequest, setSeekRequest] = useState<{ time: number; sequence: number } | null>(null)
  const [pending, setPending] = useState<'transcribe' | 'analyze' | 'render' | 'manual' | null>(null)
  const [selectionBusy, setSelectionBusy] = useState(false)
  const [selectionOverride, setSelectionOverride] = useState<Set<string> | null>(null)
  const [renderArtifactIds, setRenderArtifactIds] = useState<Record<string, string>>({})
  const [confirmAnalysis, setConfirmAnalysis] = useState(false)
  const actionLock = useRef(false)
  const selectionLock = useRef(false)
  const [contract, setContract] = useState<RenderSettingsContract | null>(null)
  const [controlsError, setControlsError] = useState<string | null>(null)
  const [controlsAttempt, setControlsAttempt] = useState(0)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [savingControls, setSavingControls] = useState(false)
  const [saveNotice, setSaveNotice] = useState<string | null>(null)
  const [captionOverride, setCaptionOverride] = useState<CaptionSettings | null>(null)
  const [filterOverride, setFilterOverride] = useState<VideoFilters | null>(null)
  const [outputOverride, setOutputOverride] = useState<OutputSettings | null>(null)
  const [analysisMode, setAnalysisMode] = useState<'transcript' | 'visual'>('transcript')
  const [suggestions, setSuggestions] = useState<FilterSuggestion[]>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  const [suggestionsStatus, setSuggestionsStatus] = useState<string | null>(null)
  const suggestionController = useRef<AbortController | null>(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; suggestionController.current?.abort() } }, [])
  useEffect(() => {
    const controller = new AbortController()
    api.getRenderSettings(controller.signal).then((value) => { if (!controller.signal.aborted) setContract(value) }).catch((loadError) => { if (!controller.signal.aborted) setControlsError(errorMessage(loadError)) })
    return () => controller.abort()
  }, [controlsAttempt])
  const transcript = useMemo(() => parseTranscript(detail?.transcript?.raw_json), [detail?.transcript?.raw_json])
  const hasSpeech = !!transcript && transcript.segments.some((segment) => segment.text.trim())
  const visibleCandidates = useMemo(() => detail?.candidates.map((candidate) => selectionOverride ? { ...candidate, selected: selectionOverride.has(candidate.id) ? 1 : 0 } : candidate) || [], [detail?.candidates, selectionOverride])
  const transcribing = pending === 'transcribe' || isActiveJob(jobs.transcription.status)
  const analyzing = pending === 'analyze' || isActiveJob(jobs.analysis.status)
  const rendering = pending === 'render' || !!detail?.clips.some((clip) => isActiveJob(clip.status))
  const processing = transcribing || analyzing
  const captionSettings = (() => {
    if (captionOverride) return captionOverride
    const schema = contract || fallbackRenderContract
    let stored: Partial<CaptionSettings> = {}
    try { stored = detail?.project.captionSettings || JSON.parse(detail?.project.caption_settings_json || '{}') || {} } catch { /* Legacy project uses its preset. */ }
    const preset = schema.caption.presets.find((item) => item.id === (stored.preset || detail?.project.caption_style)) || schema.caption.presets[0]
    return { ...preset.settings, ...stored }
  })()
  const videoFilters = filterOverride || detail?.project.videoFilters || contract?.videoFilters.defaults || fallbackRenderContract.videoFilters.defaults
  const outputSettings = outputOverride || detail?.project.outputSettings || contract?.output?.legacyDefaults || { mode: 'manual' as const, aspectRatio: '9:16' as const, fit: 'crop' as const, maxDimension: 1920 }
  const effectiveAnalysisMode = hasSpeech ? analysisMode : 'visual'
  const captionStyle = captionSettings.preset
  const stage: PipelineStage = transcribing ? 'transcribing' : analyzing ? 'analyzing' : rendering ? 'rendering' : null
  const selected = visibleCandidates.filter((candidate) => candidate.selected)
  const completed = useMemo(() => {
    const latest = latestClips(detail?.clips.filter((clip) => clip.status === 'done') || [])
    Object.entries(renderArtifactIds).forEach(([candidateId, clipId]) => {
      const clip = detail?.clips.find((item) => item.id === clipId && item.status === 'done')
      if (clip) latest.set(candidateId, clip)
    })
    return latest
  }, [detail?.clips, renderArtifactIds])
  const downloadable = selected.filter((candidate) => completed.has(candidate.id))
  const matchingLook = (clip: Clip | undefined) => {
    if (!clip) return false
    if (!clip.render_settings_json) return true // Legacy artifacts have no inspectable settings snapshot.
    try {
      const snapshot = JSON.parse(clip.render_settings_json)
      return sameSettings(snapshot.caption, captionSettings) && sameSettings(snapshot.videoFilters, videoFilters)
        && sameSettings(snapshot.outputSettings || contract?.output?.legacyDefaults, outputSettings)
    } catch { return false }
  }
  const toRender = selected.filter((candidate) => !matchingLook(completed.get(candidate.id)))
  const exportUrl = api.exportUrl(projectId, downloadable.map((candidate) => completed.get(candidate.id)!.id))
  const saveControls = async () => {
    if (savingControls || aiBusy) return
    setSavingControls(true); setSaveNotice(null)
    try {
      const saved = await api.saveRenderSettings(projectId, captionSettings, videoFilters, outputSettings)
      setCaptionOverride(saved.captionSettings); setFilterOverride(saved.videoFilters); setOutputOverride(saved.outputSettings)
      setSaveNotice('Saved to this project. Existing downloads keep their rendered settings.')
      await refresh()
    } catch (error) { setSaveNotice(errorMessage(error)) }
    finally { if (mounted.current) setSavingControls(false) }
  }
  const previousStates = useRef(new Map<string, string>())
  useEffect(() => {
    if (!detail) return
    const states = new Map([['transcription', jobs.transcription.status], ['analysis', jobs.analysis.status], ...detail.clips.map((clip): [string, string] => [clip.id, clip.status])])
    let complete = 0
    let failed = 0
    states.forEach((status, id) => {
      if (!isActiveJob(previousStates.current.get(id))) return
      if (status === 'done') complete += 1
      if (isFailedJob(status)) failed += 1
    })
    if (complete) showToast('success', `${complete} ${complete === 1 ? 'job completed' : 'jobs completed'}`)
    if (failed) showToast('error', `${failed} ${failed === 1 ? 'job failed' : 'jobs failed'}. Review the error details in the workspace.`)
    previousStates.current = states
  }, [detail, jobs, showToast])

  const run = async (kind: 'transcribe' | 'analyze' | 'render' | 'manual', action: () => Promise<void>) => {
    if (actionLock.current || selectionLock.current || aiBusy) return
    actionLock.current = true; setPending(kind)
    try { await action(); if (mounted.current) await refresh() }
    catch (error) { if (mounted.current) { showToast('error', errorMessage(error)); await refresh() } }
    finally { actionLock.current = false; if (mounted.current) setPending(null) }
  }
  const render = (candidate?: Candidate) => void run('render', async () => {
    const options = { captionStyle, captionSettings, videoFilters, outputSettings }
    await api.saveRenderSettings(projectId, captionSettings, videoFilters, outputSettings)
    if (candidate) {
      const result = await api.renderClip(projectId, candidate.id, options)
      if (result.clipId) setRenderArtifactIds((previous) => ({ ...previous, [candidate.id]: result.clipId }))
      if (result.status !== 'done') trackClips([result.clipId])
      if (mounted.current) showToast(result.status === 'done' ? 'success' : 'info', result.status === 'done' ? 'An identical completed render is ready to download.' : 'Clip queued for rendering')
    } else if (toRender.length) {
      // Batch renders all selected candidates. Only use it when none already has a completed file.
      if (toRender.length === selected.length) {
        const result = await api.renderBatch(projectId, options)
        setRenderArtifactIds((previous) => Object.fromEntries(toRender.map((item, index) => [item.id, result.clipIds[index] || previous[item.id]]).filter(([, value]) => value)))
        if (result.status !== 'done') trackClips(result.clipIds)
        if (mounted.current) showToast('info', result.status === 'done' ? 'Selected clips are already rendered and ready to download.' : `${result.clipIds.length} clips queued for rendering`)
      } else {
        for (const item of toRender) {
          const result = await api.renderClip(projectId, item.id, options)
          if (result.clipId) setRenderArtifactIds((previous) => ({ ...previous, [item.id]: result.clipId }))
          if (result.status !== 'done') trackClips([result.clipId])
        }
        if (mounted.current) showToast('info', 'Rendering requested for selected clips without a completed file.')
      }
    }
  })
  const transcribe = () => void run('transcribe', async () => { await api.transcribe(projectId); if (mounted.current) showToast('info', 'Transcription started') })
  const analyze = () => void run('analyze', async () => { await aiEditApi.analyze(projectId, { mode: effectiveAnalysisMode, brief: '' }); if (mounted.current) showToast('info', effectiveAnalysisMode === 'visual' ? 'Visual analysis started — no transcription required' : 'Transcript analysis started') })
  const requestAnalysis = () => detail?.candidates.length ? setConfirmAnalysis(true) : analyze()
  const reviewSuggestions = async () => {
    if (suggestionsLoading) return
    suggestionController.current?.abort()
    const controller = new AbortController()
    suggestionController.current = controller
    setSuggestionsLoading(true); setSuggestionsStatus(null)
    try {
      const result = await api.listFilterSuggestions(projectId, controller.signal)
      if (controller.signal.aborted) return
      setSuggestions(result)
      setSuggestionsStatus(result.length ? `${result.length} saved suggestions. Choose one to apply to your controls.` : 'No saved suggestions yet. Ask your AI for an editing look above.')
    } catch (suggestionError) { if (!controller.signal.aborted) setSuggestionsStatus(`Could not load suggestions: ${errorMessage(suggestionError)}`) }
    finally { if (!controller.signal.aborted) setSuggestionsLoading(false) }
  }
  const generateSuggestions = async (brief: string) => {
    if (suggestionsLoading) return
    const controller = new AbortController()
    suggestionController.current = controller
    setSuggestionsLoading(true); setSuggestionsStatus('Asking your active analysis provider…')
    try {
      const suggestion = await api.generateFilterSuggestion(projectId, brief, controller.signal)
      if (!controller.signal.aborted) { setSuggestions((items) => [suggestion, ...items]); setSuggestionsStatus('Suggestion ready. Review and apply it, then check the rendered preview.') }
    } catch (error) { if (!controller.signal.aborted) setSuggestionsStatus(errorMessage(error)) }
    finally { if (!controller.signal.aborted) setSuggestionsLoading(false) }
  }
  const toggleSelection = async (candidate: Candidate, checked: boolean) => {
    if (selectionLock.current || actionLock.current || aiBusy || !detail) return
    selectionLock.current = true; setSelectionBusy(true)
    try {
      const ids = new Set(selected.map((item) => item.id))
      if (checked) ids.add(candidate.id); else ids.delete(candidate.id)
      setSelectionOverride(ids)
      await api.updateCandidates(projectId, [...ids])
      const reconciled = mounted.current ? await refresh() : false
      if (reconciled && mounted.current) setSelectionOverride(null)
      else if (mounted.current) showToast('info', 'Selection saved; waiting to refresh the project state.')
    } catch (error) { if (mounted.current) { setSelectionOverride(null); showToast('error', errorMessage(error)) } }
    finally { selectionLock.current = false; if (mounted.current) setSelectionBusy(false) }
  }
  const seek = (time: number) => setSeekRequest((previous) => ({ time, sequence: (previous?.sequence || 0) + 1 }))
  const createManualCut = (startSec: number, endSec: number, title: string) => void run('manual', async () => {
    const candidate = await api.createManualCandidate(projectId, { startSec, endSec, title: title.trim() || undefined })
    if (mounted.current) showToast('success', `Manual cut ${candidate.rank} created`)
  })

  if (loading && !detail) return <main className="empty-state full-state" role="status"><span className="spinner" /><p>Loading project…</p></main>
  if (!detail) return <main className="empty-state full-state"><XCircle size={32} /><h1>Unable to open project</h1><p role="alert">{error || 'Project not found'}</p><div className="inline"><button className="btn-secondary" onClick={onBack}>Back to projects</button><button className="btn-accent" onClick={() => void refresh()}>Retry</button></div></main>
  return <main className="workspace">
    <header className="workspace-header"><button className="btn-ghost" onClick={onBack} aria-label="Back to projects"><ArrowLeft size={19} /></button><div className="workspace-title"><h1>{detail.project.name}</h1><p className="mono" title={detail.project.source_path}>{detail.project.source_path.split(/[\\/]/).at(-1)}</p></div><nav className="workspace-edit-modes" aria-label="Editing mode"><button className="btn-ghost" aria-pressed={editMode === 'manual'} onClick={() => setEditMode('manual')}><Scissors size={15} />Manual</button><button className="btn-ghost" aria-pressed={editMode === 'ai'} onClick={() => { setAiVisited(true); setEditMode('ai') }}><Sparkles size={15} />AI Edit</button></nav><div className="header-actions"><ThemeSelect />{editMode === 'manual' && <><button className="btn-secondary" title="Step 5: Render selected clips without a completed file" onClick={() => render()} disabled={aiBusy || toRender.length === 0 || rendering || processing || !!pending || selectionBusy}><WorkflowStep step={5} /><Scissors size={15} />Render selected ({toRender.length})</button>{downloadable.length ? <a className="btn-accent" title="Step 6: Download existing MP4s without rendering again" href={exportUrl} download><WorkflowStep step={6} /><Download size={15} />Download selected ({downloadable.length})</a> : <button className="btn-accent" disabled><WorkflowStep step={6} /><Download size={15} />Download selected (0)</button>}</>}<button className="btn-ghost" onClick={onOpenSettings} aria-label="Open settings"><Settings size={18} /></button></div></header>
    <PipelineStatus stage={stage} hasTranscript={!!transcript} hasCandidates={detail.candidates.length > 0} hasClips={completed.size > 0} hasSelection={selected.length > 0} />
    {aiBusy && <div className="notice" role="status">AI automation is running. Keep this project open for the next steps; leaving stops future requests, while queued server jobs can finish.</div>}
    {error && <div className="notice notice-error" role="alert"><span>Live updates interrupted; retrying automatically. {error}</span><button className="text-button" onClick={() => void refresh()}>Retry now</button></div>}
    {isFailedJob(jobs.transcription.status) && <div className="notice notice-error" role="alert">Transcription failed: {jobs.transcription.error || 'Try transcribing again.'}</div>}
    {isFailedJob(jobs.analysis.status) && <div className="notice notice-error" role="alert">Analysis failed: {jobs.analysis.error || 'Try finding clip candidates again.'}</div>}
    <div className="workspace-grid" ref={manualGrid} hidden={editMode !== 'manual'}>
      <section className="workspace-panel media-panel" aria-label="Source media"><MediaPlayer projectId={projectId} hasTranscript={!!transcript} onTimeUpdate={setCurrentTime} onTranscribe={transcribe} seekRequest={seekRequest} transcribing={transcribing} disabled={aiBusy || processing || rendering || !!pending || selectionBusy} mode={detail.project.transcription_mode} /></section>
      <section className="workspace-panel transcript-panel" aria-label="Transcript"><TranscriptView transcript={transcript} hasSpeech={hasSpeech} currentTime={currentTime} onSeek={seek} onAnalyze={requestAnalysis} analysisMode={effectiveAnalysisMode} onAnalysisModeChange={setAnalysisMode} hasCandidates={visibleCandidates.length > 0} analyzing={analyzing} disabled={aiBusy || processing || rendering || !!pending || selectionBusy} invalid={!!detail.transcript && !transcript} /></section>
      <section id="candidate-panel" className="workspace-panel candidates-panel" aria-label="Clip candidates"><CandidateList candidates={visibleCandidates} clips={detail.clips} sourceDuration={detail.project.source_duration || 0} preferredClipIds={renderArtifactIds} onCreateManualCut={createManualCut} onRender={render} onSeek={seek} onSelectToggle={(candidate, checked) => void toggleSelection(candidate, checked)} selectionBusy={selectionBusy} renderBusy={rendering} disabled={aiBusy || processing || !!pending} captionStyle={captionStyle} onOpenControls={() => setControlsOpen(true)} /></section>
    </div>
    {aiVisited && <AiEditMode detail={detail} active={editMode === 'ai'} busy={processing || rendering || !!pending || selectionBusy || savingControls || controlsOpen || suggestionsLoading} captionSettings={captionSettings} videoFilters={videoFilters} outputSettings={outputSettings} onBusyChange={setAiBusy} onRefresh={refresh} onSelectionSaved={() => setSelectionOverride(null)} onRenderQueued={(candidateId, clipId) => { setRenderArtifactIds((previous) => ({ ...previous, [candidateId]: clipId })); trackClips([clipId]) }} onManual={() => setEditMode('manual')} onOpenControls={() => setControlsOpen(true)} />}
    {confirmAnalysis && <Modal title="Replace clip candidates?" onClose={() => setConfirmAnalysis(false)} closeOnBackdrop={false}>
      <div className="stack"><p>Analyzing again replaces this project’s candidates and selections after a successful result. Existing rendered clips will no longer be available in this project. Download any clips you want to keep first.</p><div className="form-actions"><button className="btn-secondary" onClick={() => setConfirmAnalysis(false)}>Keep current candidates</button><button className="btn-danger" disabled={processing || rendering || !!pending || selectionBusy} onClick={() => { setConfirmAnalysis(false); analyze() }}>Replace and analyze</button></div></div>
    </Modal>}
    {controlsOpen && <Modal title="Caption Controls & video filters" className="editor-modal" width={1120} onClose={() => setControlsOpen(false)} closeOnBackdrop={false} closeDisabled={savingControls}>
       {contract ? <RenderControls projectId={projectId} initialTime={currentTime || (selected[0]?.start_sec ?? detail.candidates[0]?.start_sec ?? 0)} transcript={transcript} disabled={savingControls || aiBusy} contract={contract} captionSettings={captionSettings} videoFilters={videoFilters} outputSettings={outputSettings} onOutputChange={(value) => { setOutputOverride(value); setSaveNotice(null) }} onCaptionChange={(value) => { setCaptionOverride(value); setSaveNotice(null) }} onFiltersChange={(value) => { setFilterOverride(value); setSaveNotice(null) }} suggestions={suggestions} suggestionsLoading={suggestionsLoading} suggestionsStatus={suggestionsStatus} onReviewSuggestions={() => void reviewSuggestions()} onGenerateSuggestions={(brief) => void generateSuggestions(brief)} /> : <div className="stack"><p role="status">{controlsError ? `Render controls unavailable: ${controlsError}. Rendering still uses the project's saved defaults.` : 'Loading supported render controls…'}</p>{controlsError && <button className="btn-secondary" onClick={() => setControlsAttempt((value) => value + 1)}>Retry render controls</button>}</div>}
      <div className="form-actions editor-save-bar"><p className="muted" role="status">{saveNotice || 'Save your look for this project. Re-render a clip to update its MP4.'}</p><button className="btn-secondary" disabled={savingControls} onClick={() => setControlsOpen(false)}>Done editing controls</button><button className="btn-accent" disabled={savingControls || aiBusy || !contract} onClick={() => void saveControls()}>{savingControls && <span className="spinner" />}Save project settings</button></div>
    </Modal>}
  </main>
}
