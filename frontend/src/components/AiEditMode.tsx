import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, Circle, Download, Film, Lightbulb, MessageSquare, Play, Send, Sparkles, Square, XCircle } from 'lucide-react'
import { api, type AudienceBrief, type CaptionSettings, type MediaProbe, type OutputSettings, type ProjectDetail, type VideoFilters } from '../api/client'
import { audienceFromBrief } from '../api/audience'
import { aiBriefFieldLimits, aiEditApi, aiEditError, applyAiRecommendedSettings, createAiEditPlan, defaultAiBrief, describeAiOutput, formatAiEditBrief, projectPlanBasis, projectVisualRecommendation, runAiEditPlan, type AiEditActivity, type AiEditBrief, type AiEditMessage, type AiEditPlan, type AiEditStep, type VisualRecommendation } from '../api/aiEdit'
import { formatTime } from '../api/format'
import Modal from './ui/Modal'
import ClipPreview from './ClipPreview'
import '../styles/ai-edit-mode.css'

const stepLabels: Record<AiEditStep, string> = { transcribe: 'Transcribe', analyze: 'Analyze', select: 'Select', render: 'Render' }
const steps = Object.keys(stepLabels) as AiEditStep[]
const platforms = { shorts: 'YouTube Shorts', reels: 'Instagram Reels', tiktok: 'TikTok' }
const lengths = { any: 'Any natural length', '15-30': '15–30 seconds', '30-60': '30–60 seconds' }
const greeting: AiEditMessage = { role: 'guide', text: 'Who are these clips for, and what should viewers take away? Your answers become the editing brief. Send a message to discuss this project with your configured model, then review the plan before starting.' }

function recommendationBasis(brief: AiEditBrief): string {
  return formatAiEditBrief(brief)
}

function loadDraft(projectId: string): { brief: AiEditBrief; messages: AiEditMessage[] } {
  try {
    const saved = JSON.parse(sessionStorage.getItem(`clipforge.ai-edit.v2.${projectId}`) || 'null')
    if (saved?.brief && Array.isArray(saved.messages)) {
      const brief = { ...defaultAiBrief }
      for (const key of ['audience', 'goal', 'notes'] as const) if (typeof saved.brief[key] === 'string') brief[key] = saved.brief[key]
      if (Object.hasOwn(platforms, saved.brief.platform)) brief.platform = saved.brief.platform
      if (Object.hasOwn(lengths, saved.brief.length)) brief.length = saved.brief.length
      if ([1, 3, 5].includes(saved.brief.clipCount)) brief.clipCount = saved.brief.clipCount
      if (typeof saved.brief.captions === 'boolean') brief.captions = saved.brief.captions
      if (saved.selectionMethod === 'audience-first-v1' && typeof saved.brief.reuseCandidates === 'boolean') brief.reuseCandidates = saved.brief.reuseCandidates
      if (['auto', 'visual', 'transcript'].includes(saved.brief.analysisMode)) brief.analysisMode = saved.brief.analysisMode
      const messages = saved.messages.filter((message: AiEditMessage) => message && ['user', 'guide', 'model'].includes(message.role) && typeof message.text === 'string')
        .slice(-20).map((message: AiEditMessage) => ({ role: message.role, text: message.text.slice(0, 8000),
          ...(typeof message.model === 'string' ? { model: message.model.slice(0, 200) } : {}),
          ...(['metadata', 'transcript'].includes(message.basis || '') ? { basis: message.basis } : {}) }))
      return { brief, messages: messages.length ? messages : [greeting] }
    }
  } catch { /* Keep a session-only draft if storage is unavailable. */ }
  return { brief: { ...defaultAiBrief }, messages: [greeting] }
}

function sourceRatio(width: number, height: number): string {
  if (!width || !height) return 'Audio only'
  const ratio = width / height
  for (const [w, h] of [[16, 9], [9, 16], [4, 3], [1, 1], [4, 5]]) if (Math.abs(ratio - w / h) < .025) return `${w}:${h}`
  return `${ratio.toFixed(2)}:1`
}

export default function AiEditMode({ detail, active, busy, audienceBrief, onAudienceChange, captionSettings, videoFilters, outputSettings, onBusyChange, onRefresh, onRenderQueued, onSelectionSaved, onManual, onOpenControls }: {
  detail: ProjectDetail; active: boolean; busy: boolean; captionSettings: CaptionSettings; videoFilters: VideoFilters
  outputSettings: OutputSettings
  audienceBrief: AudienceBrief; onAudienceChange: (brief: AudienceBrief) => void
  onBusyChange: (busy: boolean) => void; onRefresh: () => Promise<boolean>
  onRenderQueued: (candidateId: string, clipId: string) => void; onSelectionSaved: () => void
  onManual: () => void; onOpenControls: () => void
}) {
  const projectId = detail.project.id
  const [draft, setDraft] = useState(() => loadDraft(projectId))
  const { messages } = draft
  const brief = { ...draft.brief, ...audienceBrief }
  const [reply, setReply] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [metadata, setMetadata] = useState<{ width: number; height: number } | null>(() => {
    try {
      const source = JSON.parse(detail.project.source_metadata_json || 'null')
      const width = source?.display_width ?? source?.width; const height = source?.display_height ?? source?.height
      return Number.isFinite(width) && Number.isFinite(height) ? { width, height } : null
    } catch { return null }
  })
  const [probe, setProbe] = useState<MediaProbe | null>(null)
  const [probeBusy, setProbeBusy] = useState(false)
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const [requestedRecommendation, setRequestedRecommendation] = useState<VisualRecommendation | null>(null)
  const [recommendationBrief, setRecommendationBrief] = useState<string | null>(null)
  const [recommendationBusy, setRecommendationBusy] = useState(false)
  const [recommendationError, setRecommendationError] = useState<string | null>(null)
  const [plan, setPlan] = useState<AiEditPlan | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [replaceConfirmed, setReplaceConfirmed] = useState(false)
  const [running, setRunning] = useState(false)
  const [activities, setActivities] = useState<Partial<Record<AiEditStep, AiEditActivity>>>({})
  const [runError, setRunError] = useState<string | null>(null)
  const [result, setResult] = useState<{ candidateIds: string[]; clipIds: string[] } | null>(null)
  const [runClipIds, setRunClipIds] = useState<string[]>([])
  const [previewId, setPreviewId] = useState<string | null>(null)
  const runner = useRef<AbortController | null>(null)
  const recommendationController = useRef<AbortController | null>(null)
  const probeController = useRef<AbortController | null>(null)
  const chatController = useRef<AbortController | null>(null)
  const chatLock = useRef(false)
  const runLock = useRef(false)
  const mounted = useRef(true)
  const chatEnd = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const blocked = busy || running || recommendationBusy || probeBusy || chatBusy
  const nextQuestion = !brief.audience.trim() ? 'Who is the audience?' : !brief.goal.trim() ? 'What should viewers take away?' : 'What should we emphasize or avoid?'
  const completedClips = detail.clips.filter((clip) => runClipIds.includes(clip.id) && clip.status === 'done')
  const previewClip = detail.clips.find((clip) => clip.id === previewId)
  const previewCandidate = detail.candidates.find((candidate) => candidate.id === previewClip?.candidate_id)
  const stalePlan = !!plan && projectPlanBasis(detail) !== plan.basis
  const persistedRecommendation = projectVisualRecommendation(detail)
  const recommendation = requestedRecommendation || persistedRecommendation
  const recommendationStale = !!requestedRecommendation && recommendationBrief !== recommendationBasis(brief)
  const currentOutput = describeAiOutput(outputSettings)
  const sourceDimensions = probe ? { width: probe.display_width || probe.width, height: probe.display_height || probe.height }
    : recommendation ? { width: recommendation.source.width, height: recommendation.source.height } : metadata

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; runner.current?.abort(); recommendationController.current?.abort(); probeController.current?.abort(); chatController.current?.abort() }
  }, [])
  useEffect(() => {
    try { sessionStorage.setItem(`clipforge.ai-edit.v2.${projectId}`, JSON.stringify({ ...draft, selectionMethod: 'audience-first-v1' })) } catch { /* Draft stays in memory. */ }
  }, [draft, projectId])
  useEffect(() => {
    if (!active) videoRef.current?.pause()
  }, [active])
  useEffect(() => {
    // Scroll only the conversation, never the whole workspace.
    const scroller = chatEnd.current?.parentElement
    if (active && scroller) scroller.scrollTop = scroller.scrollHeight
  }, [messages, active])

  const updateBrief = (patch: Partial<AiEditBrief>) => {
    if (['audience', 'goal', 'notes'].some((key) => Object.hasOwn(patch, key))) onAudienceChange(audienceFromBrief({ ...brief, ...patch }))
    setDraft((previous) => ({ ...previous, brief: { ...previous.brief, ...patch } }))
    setPlan(null); setConfirmed(false)
  }
  const submitReply = async (event: FormEvent) => {
    event.preventDefault()
    const text = reply.trim()
    if (!text || blocked || chatLock.current) return
    let patch: Partial<AiEditBrief>
    if (!brief.audience.trim()) {
      patch = { audience: text }
    } else if (!brief.goal.trim()) {
      patch = { goal: text }
    } else {
      const notes = [brief.notes, text].filter(Boolean).join('\n')
      if (notes.length > aiBriefFieldLimits.notes) {
        setChatError(`This message would exceed the ${aiBriefFieldLimits.notes}-character notes limit. Shorten “Topics, tone & things to avoid” before sending.`)
        return
      }
      patch = { notes }
    }
    const updatedBrief = { ...brief, ...patch }
    onAudienceChange(audienceFromBrief(updatedBrief))
    const controller = new AbortController(); chatController.current = controller; chatLock.current = true
    setDraft((previous) => ({ brief: updatedBrief, messages: [...previous.messages, { role: 'user', text } as AiEditMessage].slice(-20) }))
    setReply(''); setPlan(null); setConfirmed(false); setChatError(null); setChatBusy(true)
    try {
      const value = await aiEditApi.chat(projectId, text, messages, updatedBrief, controller.signal)
      if (controller.signal.aborted || !mounted.current) return
      setDraft((previous) => ({ ...previous, messages: [...previous.messages,
        { role: 'model', text: value.message, model: value.model, basis: value.basis } as AiEditMessage].slice(-20) }))
    } catch (error) {
      if (mounted.current) setChatError(controller.signal.aborted ? 'Chat request stopped.' : aiEditError(error))
    } finally { chatLock.current = false; if (mounted.current) setChatBusy(false) }
  }

  const inspectMetadata = async () => {
    if (blocked) return
    const controller = new AbortController(); probeController.current = controller
    setProbeBusy(true); setMetadataError(null)
    try {
      const value = await api.probeMedia(projectId, controller.signal)
      if (controller.signal.aborted) return
      setProbe(value); setMetadata({ width: value.display_width || value.width, height: value.display_height || value.height })
      await onRefresh()
    } catch (error) { if (!controller.signal.aborted && mounted.current) setMetadataError(aiEditError(error)) }
    finally { if (mounted.current) setProbeBusy(false) }
  }

  const askRecommendation = async () => {
    if (blocked) return
    const controller = new AbortController(); recommendationController.current = controller
    setRecommendationBusy(true); setRecommendationError(null)
    try {
      const value = await aiEditApi.recommendations(projectId, brief, controller.signal)
      if (controller.signal.aborted || !mounted.current) return
      setRequestedRecommendation(value); setRecommendationBrief(recommendationBasis(brief)); setPlan(null)
    } catch (error) { if (!controller.signal.aborted && mounted.current) setRecommendationError(aiEditError(error)) }
    finally { if (mounted.current) setRecommendationBusy(false) }
  }

  const reviewPlan = () => {
    if (blocked || !brief.audience.trim() || !brief.goal.trim()) return
    setConfirmed(false); setReplaceConfirmed(false)
    setPlan(createAiEditPlan(detail, brief, { captionStyle: captionSettings.preset,
      captionSettings: { ...captionSettings, enabled: brief.captions }, videoFilters, outputSettings,
    }, recommendationStale ? null : recommendation))
  }
  const toggleRecommendedSettings = (enabled: boolean) => {
    setConfirmed(false)
    setPlan((previous) => previous && ({ ...previous, useRecommendedSettings: enabled,
      renderOptions: enabled && previous.recommendation
        ? applyAiRecommendedSettings(previous.baseRenderOptions, previous.recommendation, previous.brief.captions)
        : structuredClone(previous.baseRenderOptions),
    }))
  }
  const startEdit = async () => {
    if (!plan || !confirmed || (plan.replacesCandidates && !replaceConfirmed) || blocked || stalePlan || runLock.current) return
    const reviewedPlan = plan
    const controller = new AbortController(); runner.current = controller; runLock.current = true
    setPlan(null); setRunning(true); onBusyChange(true)
    setRunError(null); setResult(null); setRunClipIds([])
    setActivities(Object.fromEntries(steps.map((step) => [step, { step, status: 'waiting', detail: 'Waiting for the previous step.' }])))
    try {
      const value = await runAiEditPlan(reviewedPlan, controller.signal, {
        activity: (activity) => { if (mounted.current) setActivities((previous) => ({ ...previous, [activity.step]: activity })) },
        selectionSaved: onSelectionSaved,
        renderQueued: (candidateId, clipId) => {
          onRenderQueued(candidateId, clipId)
          if (mounted.current) setRunClipIds((previous) => [...previous, clipId])
        },
        refresh: onRefresh,
        recommendation: (value) => {
          if (mounted.current) { setRequestedRecommendation(value); setRecommendationBrief(recommendationBasis(reviewedPlan.brief)) }
        },
      })
      if (mounted.current) setResult(value)
    } catch (error) {
      if (mounted.current) {
        setRunError(controller.signal.aborted ? 'Stopped. Already queued jobs can still finish on the server.' : aiEditError(error))
        setActivities((previous) => Object.fromEntries(Object.entries(previous).map(([step, activity]) => [step, activity.status === 'waiting' ? { ...activity, status: 'stopped', detail: 'Not started because automation stopped.' } : activity])))
      }
    } finally {
      runLock.current = false
      if (mounted.current) { setRunning(false); onBusyChange(false); void onRefresh() }
    }
  }

  return <section className="ai-edit" hidden={!active} aria-label="AI Edit mode">
    <div className="ai-edit-intro"><div><p className="eyebrow">AI EDITING</p><h2>Shape the story. Approve the edit.</h2><p className="muted">Chat about this project, inspect visual recommendations, then approve your automated edit.</p></div><span className="badge badge-info"><Sparkles size={14} />Project chat · visual analysis</span></div>
    <div className="ai-edit-layout">
      <div className="ai-edit-main">
        <div className="ai-edit-summary-grid">
          <section className="ai-edit-card" aria-labelledby="ai-source-title">
            <div className="section-heading"><h3 id="ai-source-title"><Film size={18} />Source ratio</h3><span className="badge badge-neutral">{sourceDimensions ? sourceRatio(sourceDimensions.width, sourceDimensions.height) : 'Not inspected'}</span></div>
            <video ref={videoRef} className="ai-source-preview" src={api.mediaFileUrl(projectId)} aria-label="AI mode source preview" controls preload="metadata" playsInline
              onLoadedMetadata={(event) => { if (!probe && event.currentTarget.videoWidth > 0) setMetadata({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight }) }} onError={() => setMetadataError('Browser preview unavailable. Inspect metadata with FFprobe to check the source.')} />
            <p className="muted">{sourceDimensions?.width ? `${sourceDimensions.width} × ${sourceDimensions.height} · ` : ''}{formatTime(probe?.duration_sec || recommendation?.source.durationSeconds || detail.project.source_duration || 0)} source → <strong>{currentOutput}</strong></p>
            <p className="muted">{sourceDimensions && !sourceDimensions.width ? 'Audio-only exports use a black background.' : 'Output follows your project settings. Preview the framing after rendering.'}</p>
            <button className="btn-secondary" disabled={blocked} onClick={() => void inspectMetadata()}>{probeBusy && <span className="spinner" />}Inspect source metadata</button>
            {metadataError && <p className="muted" role="status">{metadataError}</p>}
          </section>
          <section className="ai-edit-card" aria-labelledby="ai-recommendation-title">
            <div className="section-heading"><h3 id="ai-recommendation-title"><Lightbulb size={18} />AI recommendation</h3><span className="badge badge-info">{recommendation ? 'Sampled-frame model response' : 'Visual model'}</span></div>
            <p>{recommendation?.rationale || 'Inspect sampled frames for moments and a look that fit your intended viewer. Visible evidence, audience fit and a complete payoff take priority over filling a clip quota.'}</p>
            {recommendation && <>
              {recommendation.audience && <p className="muted"><strong>Intended audience:</strong> {recommendation.audience}</p>}
              <p className="muted">Model: {recommendation.model} · {recommendation.sampledFrameTimes.length} sampled frames</p>
              <p className="muted">{recommendation.contextNotice || 'Based on sampled video frames.'}</p>
              <p className="muted">Framing: {recommendation.aspectRatio.mode} · {recommendation.aspectRatio.ratio}. Captions: {recommendation.captionSettings.enabled === false ? 'off' : recommendation.captionSettings.preset || 'current preset'}{recommendation.captionSettings.placement ? ` · ${recommendation.captionSettings.placement}` : ''}</p>
              <p className="muted">{Object.entries(recommendation.videoFilters).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>
              <details className="ai-visual-details"><summary>Sample times & suggested moments ({recommendation.candidates.length})</summary><p className="muted">Frames: {recommendation.sampledFrameTimes.map(formatTime).join(' · ')}</p><ul>{recommendation.candidates.map((candidate, index) => <li key={index}><strong>{candidate.hook}</strong><span className="mono">{formatTime(candidate.start)} – {formatTime(candidate.end)} · editorial score {candidate.score}</span><p>{candidate.rationale}</p>{candidate.selection && <p><strong>Audience fit {candidate.selection.assessment.audienceFit}/5:</strong> {candidate.selection.audienceReason}</p>}</li>)}</ul></details>
              {recommendationStale && <p className="field-hint">The brief changed. Request a fresh recommendation to review these settings.</p>}
            </>}
            <button className="btn-secondary" disabled={blocked} onClick={() => void askRecommendation()}>{recommendationBusy ? <span className="spinner" /> : <Sparkles size={16} />}{recommendationBusy ? 'Inspecting sampled frames…' : 'Get visual recommendation'}</button>
            {recommendationBusy && <button className="text-button" onClick={() => { recommendationController.current?.abort(); setRecommendationError('Recommendation request stopped.') }}>Stop recommendation</button>}
            {recommendationError && <p className="notice notice-error" role="alert">Recommendation unavailable: {recommendationError}</p>}
          </section>
        </div>

        <section className="ai-edit-card" aria-labelledby="ai-brief-title">
          <div className="section-heading"><h3 id="ai-brief-title">Questions & editing brief</h3><span className="badge badge-neutral">Project-scoped · saved in this tab</span></div>
          <fieldset disabled={blocked} className="ai-brief-fields">
            <div className="form-grid"><label className="field">Who is the audience?<input className="input-field" value={brief.audience} maxLength={aiBriefFieldLimits.audience} onChange={(event) => updateBrief({ audience: event.target.value })} placeholder="For example, first-time founders" /></label><label className="field">What should viewers take away?<input className="input-field" value={brief.goal} maxLength={aiBriefFieldLimits.goal} onChange={(event) => updateBrief({ goal: event.target.value })} placeholder="One useful idea they can act on" /></label></div>
            <div className="ai-brief-options"><label className="field">Destination<select className="input-field" value={brief.platform} onChange={(event) => updateBrief({ platform: event.target.value as AiEditBrief['platform'] })}>{Object.entries(platforms).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field">Number of clips<select className="input-field" value={brief.clipCount} onChange={(event) => updateBrief({ clipCount: Number(event.target.value) })}><option value={1}>Up to 1</option><option value={3}>Up to 3</option><option value={5}>Up to 5</option></select></label><label className="field">Preferred clip length<select className="input-field" value={brief.length} onChange={(event) => updateBrief({ length: event.target.value as AiEditBrief['length'] })}>{Object.entries(lengths).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
            <label className="field ai-analysis-field">Clip analysis<select className="input-field" value={brief.analysisMode} onChange={(event) => updateBrief({ analysisMode: event.target.value as AiEditBrief['analysisMode'] })}><option value="auto">Auto · speech when available, otherwise visuals</option><option value="transcript">Transcript · timed spoken moments</option><option value="visual">Visual · sampled video frames</option></select></label>
            <label className="field">Topics, tone & things to avoid<textarea className="input-field" rows={3} value={brief.notes} maxLength={aiBriefFieldLimits.notes} onChange={(event) => updateBrief({ notes: event.target.value })} placeholder="Keep the useful explanations; avoid an exaggerated look." /></label>
            <div className="inline wrap"><label className="checkbox-field"><input type="checkbox" checked={brief.captions} onChange={(event) => updateBrief({ captions: event.target.checked })} />Burn in captions</label>{detail.candidates.length > 0 && <label className="checkbox-field"><input type="checkbox" checked={brief.reuseCandidates} onChange={(event) => updateBrief({ reuseCandidates: event.target.checked })} />Reuse existing candidates</label>}</div>
          </fieldset>
          <p className="field-hint">Audience fit 35% · hook 20% · payoff 20% · clarity 15% · shareability 10%. Auto uses timed speech when available, otherwise frames. Fresh analysis is the default; reusing candidates keeps their existing ranking.</p>
          <div className="ai-plan-actions"><button className="btn-secondary" disabled={blocked} onClick={onOpenControls}>Caption Controls & filters</button><button className="btn-accent" disabled={blocked || !brief.audience.trim() || !brief.goal.trim()} onClick={reviewPlan}><Sparkles size={16} />Review plan</button></div>
          {(!brief.audience.trim() || !brief.goal.trim()) && <p className="field-hint">Answer the audience and takeaway questions to review your plan.</p>}
        </section>

        <section className="ai-edit-card" aria-labelledby="ai-activity-title">
          <div className="section-heading"><h3 id="ai-activity-title">Activity timeline</h3>{running && <button className="btn-secondary" onClick={() => runner.current?.abort()}><Square size={14} />Stop automation</button>}</div>
          <p className="muted">Keep this project open while automation runs. Switching Manual/AI modes keeps it running. Leaving the project or reloading stops future steps; queued server jobs can finish.</p>
          <ol className="ai-activity-list" aria-label="Automated edit progress">{steps.map((step) => {
            const activity = activities[step]
            return <li key={step} className={`ai-activity-${activity?.status || 'waiting'}`}><span aria-hidden="true">{activity?.status === 'running' ? <span className="spinner" /> : activity?.status === 'done' ? <CheckCircle2 size={18} /> : activity?.status === 'error' ? <XCircle size={18} /> : <Circle size={18} />}</span><div><strong>{stepLabels[step]}</strong><p>{activity?.detail || 'Starts only after you review and confirm the plan.'}</p></div></li>
          })}</ol>
          {running && <p role="status" className="muted">{Object.values(activities).find((activity) => activity.status === 'running')?.detail || 'Checking the reviewed project…'}</p>}
          {runError && <p className="notice notice-error" role="alert">{runError}</p>}
          {result && <p className="notice" role="status">Automated edit complete. {result.clipIds.length} {result.clipIds.length === 1 ? 'MP4 is' : 'MP4s are'} ready to review.</p>}
          {completedClips.length > 0 && <div className="ai-results"><h4>Completed exports</h4>{completedClips.map((clip) => <div key={clip.id} className="ai-result"><span>{detail.candidates.find((candidate) => candidate.id === clip.candidate_id)?.hook || 'Completed clip'}</span><button className="btn-secondary" onClick={() => setPreviewId(clip.id)}><Play size={14} />Preview</button><a className="btn-secondary" href={api.clipFileUrl(clip.id)} download><Download size={14} />MP4</a></div>)}<a className="btn-accent" href={api.exportUrl(projectId, completedClips.map((clip) => clip.id))} download><Download size={16} />Download completed edits ({completedClips.length})</a></div>}
          <button className="text-button" onClick={onManual}>Review candidates in Manual mode</button>
        </section>
      </div>

      <aside className="ai-edit-card ai-chat" aria-labelledby="ai-chat-title">
        <div className="section-heading"><h3 id="ai-chat-title"><MessageSquare size={18} />Project chat</h3><span className="badge badge-info">Connected model</span></div>
        <p className="muted">Send uses your configured model with this project’s metadata or transcript and your full brief. Visual recommendations inspect frames separately.</p>
        <div className="ai-chat-messages" role="log" aria-label="Project conversation" aria-live="polite">{messages.map((message, index) => <article className={`ai-chat-message ai-chat-${message.role}`} key={index}><strong>{message.role === 'user' ? 'You' : message.role === 'model' ? `Model reply · ${message.model || 'Configured model'}` : 'ClipForge starter guide'}</strong><p>{message.text}</p>{message.role === 'model' && message.basis && <small>Project context: {message.basis}</small>}</article>)}<div ref={chatEnd} /></div>
        {chatBusy && <div className="ai-model-status" role="status"><span className="spinner" />Waiting for the project model…<button className="text-button" onClick={() => chatController.current?.abort()}>Stop chat</button></div>}
        {chatError && <p className="notice notice-error" role="alert">{chatError}</p>}
        <form onSubmit={(event) => void submitReply(event)} className="ai-chat-form"><label className="field">{nextQuestion}<textarea className="input-field" aria-label="Message to project model" value={reply} maxLength={1000} rows={3} disabled={blocked} onChange={(event) => setReply(event.target.value)} placeholder="Answer here or ask about this project…" /></label><button className="btn-accent" disabled={blocked || !reply.trim()}><Send size={15} />Send to model</button></form>
      </aside>
    </div>

    {plan && <Modal title="Review automated edit plan" width={720} onClose={() => setPlan(null)} closeOnBackdrop={false}>
      <div className="stack compact ai-plan-review"><p><strong>{platforms[plan.brief.platform]}</strong> · Up to {plan.brief.clipCount} clips · {lengths[plan.brief.length]}</p><p className="muted">Audience: {plan.brief.audience}<br />Takeaway: {plan.brief.goal}</p>
        <ol><li><strong>Transcribe:</strong> {plan.transcribe ? 'Create speech context for analysis or captions. Video can continue with visual analysis if speech is unavailable.' : 'Skip transcription; reuse speech if available.'}</li><li><strong>Analyze:</strong> {plan.analyze ? plan.brief.analysisMode === 'visual' ? 'Inspect sampled frames using audience fit and visible evidence.' : 'Scout timed spoken ideas and independently review audience fit, opening and payoff; use visual evidence when speech is unavailable.' : 'Reuse existing candidates without reranking for this audience.'}</li><li><strong>Select:</strong> Save up to {plan.brief.clipCount} candidates, preferring {lengths[plan.brief.length].toLowerCase()}, then higher scores.</li><li><strong>Render:</strong> {describeAiOutput(plan.renderOptions.outputSettings)} · captions {plan.renderOptions.captionSettings?.enabled ? `on (${plan.renderOptions.captionStyle}, when speech is available)` : 'off'} · {plan.useRecommendedSettings ? 'AI recommended settings' : 'reviewed project settings'}.</li></ol>
        {(plan.recommendation || plan.analyze) && <div className="ai-plan-settings"><label className="checkbox-field"><input type="checkbox" checked={plan.useRecommendedSettings} onChange={(event) => toggleRecommendedSettings(event.target.checked)} />Use AI recommended settings</label><p className="field-hint">{plan.recommendation ? `${plan.recommendation.model}: ${plan.recommendation.rationale}` : 'Use visual model framing, filters, and caption choices when available.'}{plan.analyze ? ' The completed visual analysis can update these render settings.' : ''}</p></div>}
        {plan.candidateIds.length > 0 && <div className="ai-plan-candidates">{plan.candidateIds.map((id) => { const candidate = detail.candidates.find((item) => item.id === id); return candidate && <p key={id}><strong>{candidate.hook}</strong><span className="mono">{formatTime(candidate.start_sec)} – {formatTime(candidate.end_sec)}</span></p> })}</div>}
        {plan.replacesCandidates && <><p className="notice notice-warning">Successful reanalysis replaces current candidates and selections and removes their existing rendered clips from this project. Download clips you want to keep first.</p><label className="checkbox-field"><input type="checkbox" checked={replaceConfirmed} onChange={(event) => setReplaceConfirmed(event.target.checked)} />I confirm replacing existing candidates and outputs</label></>}
        {stalePlan && <p className="field-hint" role="status">The project changed since review. Close this plan and review it again.</p>}
        <label className="checkbox-field"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I approve this plan and the project selection update</label>
        <div className="form-actions"><button className="btn-secondary" onClick={() => setPlan(null)}>Back to brief</button><button className="btn-accent" disabled={blocked || stalePlan || !confirmed || (plan.replacesCandidates && !replaceConfirmed)} onClick={() => void startEdit()}><Play size={16} />Start automated edit</button></div>
      </div>
    </Modal>}
    {previewClip && previewCandidate && <ClipPreview clip={previewClip} candidate={previewCandidate} onClose={() => setPreviewId(null)} />}
  </section>
}
