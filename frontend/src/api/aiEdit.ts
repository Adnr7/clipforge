import { api, type Candidate, type CaptionSettings, type JobStatus, type OutputSettings, type ProjectDetail, type RenderOptions, type VideoFilters } from './client'
import { isActiveJob, isFailedJob } from './jobs'

export interface AiEditBrief {
  audience: string
  goal: string
  notes: string
  platform: 'shorts' | 'reels' | 'tiktok'
  clipCount: number
  length: 'any' | '15-30' | '30-60'
  captions: boolean
  reuseCandidates: boolean
  analysisMode: 'visual' | 'transcript'
}

export interface AiEditMessage {
  role: 'user' | 'guide' | 'model'
  text: string
  model?: string
  basis?: 'metadata' | 'transcript'
}

export interface AiEditChatReply {
  message: string
  model: string
  basis: 'metadata' | 'transcript'
}

export interface VisualRecommendation {
  aspectRatio: { mode: 'preserve' | 'crop' | 'pad'; ratio: 'source' | '9:16' | '16:9' | '1:1' | '4:5' }
  videoFilters: Partial<VideoFilters>
  captionSettings: Partial<CaptionSettings>
  candidates: { start: number; end: number; score: number; hook: string; rationale: string }[]
  rationale: string
  analysisBasis: 'sampled-frames'
  source: { width: number; height: number; durationSeconds: number; hasVideo: boolean; hasAudio: boolean }
  sampledFrameTimes: number[]
  contextNotice: string
  model: string
}

const platforms = { shorts: 'YouTube Shorts', reels: 'Instagram Reels', tiktok: 'TikTok' }
const lengths = { any: 'Any natural length', '15-30': '15–30 seconds', '30-60': '30–60 seconds' }

export const aiBriefFieldLimits = { audience: 1000, goal: 1000, notes: 2000 } as const

/** One brief is shared by chat, recommendations and clip analysis. */
export function formatAiEditBrief(brief: AiEditBrief): string {
  return [`Audience: ${brief.audience}`, `Goal: ${brief.goal}`, `Topics, tone & things to avoid: ${brief.notes}`,
    `Platform: ${platforms[brief.platform]}`, `Clip count: up to ${brief.clipCount}`, `Preferred length: ${lengths[brief.length]}`,
    `Captions: ${brief.captions ? 'on' : 'off'}`, `Clip analysis: ${brief.analysisMode}`, `Reuse candidates: ${brief.reuseCandidates ? 'yes' : 'no'}`].join('\n')
}

/** Keep provider/gateway errors plain and bounded in every AI Edit surface. */
export function aiEditError(error: unknown): string {
  const text = error instanceof Error ? error.message : typeof error === 'string' ? error : 'Something went wrong. Please try again.'
  if (/<(?:!doctype|html|head|body|script|style)\b/i.test(text)) return 'The server returned an unreadable response. Check the provider connection and try again.'
  return text.replace(/<[^>]*>/g, '').replace(/\p{Cc}/gu, ' ').trim().slice(0, 600) || 'Request failed. Please try again.'
}

async function aiEditRequest<T>(projectId: string, route: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/${route}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  if (!response.ok) {
    const text = (await response.text()).slice(0, 8000)
    let message = text || `Request failed (HTTP ${response.status})`
    try {
      const value = JSON.parse(text)
      message = [value.error, value.message, value.detail].find((item) => typeof item === 'string') || `Request failed (HTTP ${response.status})`
    } catch {
      if (/<[^>]+>/.test(text)) message = `Server returned HTTP ${response.status}. Check the provider connection and try again.`
    }
    throw new Error(aiEditError(message))
  }
  if (response.status === 204) return undefined as T
  try { return await response.json() as T }
  catch { signal?.throwIfAborted(); throw new Error('The server returned an invalid AI response. Please try again.') }
}

export const aiEditApi = {
  chat: async (projectId: string, message: string, messages: AiEditMessage[], brief: AiEditBrief, signal?: AbortSignal): Promise<AiEditChatReply> => {
    const value = await aiEditRequest<AiEditChatReply>(projectId, 'ai-edit/chat', {
      message, messages: messages.map(({ role, text }) => ({ role, text })), brief: formatAiEditBrief(brief),
    }, signal)
    if (!value || typeof value.message !== 'string' || !value.message.trim() || typeof value.model !== 'string'
      || !['metadata', 'transcript'].includes(value.basis)) throw new Error('The chat endpoint returned an incomplete model reply. Please try again.')
    return value
  },
  recommendations: async (projectId: string, brief: AiEditBrief, signal?: AbortSignal): Promise<VisualRecommendation> => {
    const value = await aiEditRequest<VisualRecommendation>(projectId, 'ai-edit/recommendations', { brief: formatAiEditBrief(brief) }, signal)
    if (!isVisualRecommendation(value)) throw new Error('The recommendation endpoint returned an incomplete visual response. Please try again.')
    return value
  },
  analyze: (projectId: string, options: { mode: 'visual' | 'transcript'; brief: string; provider?: string }, signal?: AbortSignal) =>
    aiEditRequest<{ status: string; model?: string }>(projectId, 'analyze', options, signal),
}

function isVisualRecommendation(value: unknown): value is VisualRecommendation {
  if (!value || typeof value !== 'object') return false
  const item = value as VisualRecommendation
  return item.analysisBasis === 'sampled-frames' && typeof item.model === 'string' && typeof item.rationale === 'string'
    && !!item.source && typeof item.source.hasVideo === 'boolean' && typeof item.source.hasAudio === 'boolean'
    && !!item.aspectRatio && ['preserve', 'crop', 'pad'].includes(item.aspectRatio.mode)
    && ['source', '9:16', '16:9', '1:1', '4:5'].includes(item.aspectRatio.ratio)
    && !!item.videoFilters && !!item.captionSettings && Array.isArray(item.candidates) && Array.isArray(item.sampledFrameTimes)
}

export function projectVisualRecommendation(detail: ProjectDetail): VisualRecommendation | null {
  // Kept here so the shared client can be evolved independently by the aspect owner.
  const value = (detail.project as ProjectDetail['project'] & { visualAnalysis?: unknown }).visualAnalysis
  return isVisualRecommendation(value) ? value : null
}

export function applyAiRecommendedSettings(base: RenderOptions, recommendation: VisualRecommendation, captions: boolean): RenderOptions {
  const aspect = recommendation.aspectRatio
  return {
    ...structuredClone(base),
    captionStyle: recommendation.captionSettings.preset || base.captionStyle,
    captionSettings: { ...base.captionSettings, ...recommendation.captionSettings,
      enabled: captions && recommendation.captionSettings.enabled !== false },
    videoFilters: { ...base.videoFilters, ...recommendation.videoFilters },
    outputSettings: { ...base.outputSettings, mode: aspect.mode === 'preserve' ? 'source' : 'ai',
      aspectRatio: aspect.mode === 'preserve' ? 'source' : aspect.ratio, fit: aspect.mode === 'crop' ? 'crop' : 'contain' },
  }
}

export function describeAiOutput(settings: Partial<OutputSettings> | undefined): string {
  if (!settings || settings.mode === 'source' || settings.aspectRatio === 'source') return `Source ratio preserved · max ${settings?.maxDimension || 1920} px`
  return `${settings.aspectRatio} · ${settings.fit === 'crop' ? 'center crop' : 'fit with padding'} · max ${settings.maxDimension || 1920} px`
}

export type AiEditStep = 'transcribe' | 'analyze' | 'select' | 'render'
export interface AiEditActivity {
  step: AiEditStep
  status: 'waiting' | 'running' | 'done' | 'error' | 'stopped'
  detail: string
}
export interface AiEditPlan {
  projectId: string
  basis: string
  brief: AiEditBrief
  transcribe: boolean
  analyze: boolean
  replacesCandidates: boolean
  candidateIds: string[]
  renderOptions: RenderOptions
  baseRenderOptions: RenderOptions
  recommendation: VisualRecommendation | null
  useRecommendedSettings: boolean
}

export const defaultAiBrief: AiEditBrief = {
  audience: '', goal: '', notes: '', platform: 'shorts', clipCount: 3,
  length: 'any', captions: true, reuseCandidates: true, analysisMode: 'visual',
}

export function transcriptHasSpeech(detail: ProjectDetail): boolean {
  try {
    const data = JSON.parse(detail.transcript?.raw_json || 'null')
    return ['segments', 'words'].some((key) => Array.isArray(data?.[key]) && data[key].some((part: { text?: unknown; start?: number; end?: number }) =>
      part && typeof part.text === 'string' && part.text.trim() && Number.isFinite(part.start) && Number.isFinite(part.end)))
  } catch { return false }
}

// Review is invalidated if the transcript or candidate set changes before Start.
export function projectPlanBasis(detail: ProjectDetail): string {
  return JSON.stringify([detail.project.source_path, detail.project.source_duration, detail.transcript?.id, detail.transcript?.raw_json,
    detail.candidates.map(({ id, start_sec, end_sec, score, rank, selected }) => [id, start_sec, end_sec, score, rank, selected])])
}

export function chooseAiCandidates(detail: ProjectDetail, brief: AiEditBrief): Candidate[] {
  const fits = (candidate: Candidate) => {
    const length = candidate.end_sec - candidate.start_sec
    return brief.length === 'any' || (brief.length === '15-30' ? length >= 15 && length <= 30 : length >= 30 && length <= 60)
  }
  return detail.candidates.filter((candidate) => Number.isFinite(candidate.start_sec) && Number.isFinite(candidate.end_sec)
    && candidate.start_sec >= 0 && candidate.end_sec > candidate.start_sec
    && (!detail.project.source_duration || candidate.end_sec <= detail.project.source_duration))
    .sort((a, b) => Number(fits(b)) - Number(fits(a)) || b.score - a.score || a.rank - b.rank)
    .slice(0, Math.max(1, Math.min(5, brief.clipCount)))
}

function sourceHasAudio(detail: ProjectDetail): boolean | undefined {
  const visual = projectVisualRecommendation(detail)
  if (visual) return visual.source.hasAudio
  try { return JSON.parse(detail.project.source_metadata_json || 'null')?.has_audio }
  catch { return undefined }
}

function sourceHasVideo(detail: ProjectDetail): boolean | undefined {
  const visual = projectVisualRecommendation(detail)
  if (visual) return visual.source.hasVideo
  try { return JSON.parse(detail.project.source_metadata_json || 'null')?.has_video }
  catch { return undefined }
}

export function createAiEditPlan(detail: ProjectDetail, brief: AiEditBrief, renderOptions: RenderOptions, recommendation: VisualRecommendation | null = null): AiEditPlan {
  const analyze = !detail.candidates.length || !brief.reuseCandidates
  const effectiveBrief = { ...brief, analysisMode: analyze && sourceHasVideo(detail) === false ? 'transcript' as const : brief.analysisMode }
  return {
    projectId: detail.project.id, basis: projectPlanBasis(detail), brief: effectiveBrief,
    transcribe: (brief.captions || (analyze && effectiveBrief.analysisMode === 'transcript')) && !detail.transcript && sourceHasAudio(detail) !== false, analyze,
    replacesCandidates: analyze && detail.candidates.length > 0,
    candidateIds: analyze ? [] : chooseAiCandidates(detail, brief).map((candidate) => candidate.id),
    renderOptions: structuredClone(renderOptions),
    baseRenderOptions: structuredClone(renderOptions), recommendation: recommendation ? structuredClone(recommendation) : null,
    useRecommendedSettings: false,
  }
}

async function readProject(projectId: string, signal: AbortSignal): Promise<ProjectDetail> {
  const detail = await api.getProject(projectId, signal)
  // Older backends expose stage status separately; current backends have one snapshot.
  if (!detail.jobs) {
    const [transcription, analysis] = await Promise.all([
      api.getTranscriptionStatus(projectId, signal), api.getAnalysisStatus(projectId, signal),
    ])
    detail.jobs = { transcription, analysis }
  }
  return detail
}

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted()
    const abort = () => { clearTimeout(timer); reject(signal.reason) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 1500)
    signal.addEventListener('abort', abort, { once: true })
  })
}

async function waitForProject(projectId: string, signal: AbortSignal, check: (detail: ProjectDetail) => boolean): Promise<ProjectDetail> {
  const deadline = Date.now() + 30 * 60 * 1000
  let failures = 0
  while (Date.now() < deadline) {
    signal.throwIfAborted()
    let detail: ProjectDetail
    try { detail = await readProject(projectId, signal); failures = 0 }
    catch (error) {
      signal.throwIfAborted()
      if (++failures >= 3) throw new Error(`Could not monitor the job: ${aiEditError(error)}. Check Manual mode before starting again.`)
      await pause(signal); continue
    }
    // Server failures from check must stop the pipeline, rather than be retried as network failures.
    if (check(detail)) return detail
    await pause(signal)
  }
  throw new Error('Monitoring timed out. The server job may still be running; check Manual mode before starting again.')
}

function jobDone(job: JobStatus | undefined, name: string): boolean {
  if (isFailedJob(job?.status)) throw new Error(`${name} failed: ${job?.error || 'Check the project job details.'}`)
  return job?.status === 'done'
}

/** Browser-owned sequencing. Aborting stops future requests, not already queued server jobs. */
export async function runAiEditPlan(plan: AiEditPlan, signal: AbortSignal, callbacks: {
  activity: (activity: AiEditActivity) => void
  selectionSaved: () => void
  renderQueued: (candidateId: string, clipId: string) => void
  refresh: () => Promise<boolean>
  recommendation?: (recommendation: VisualRecommendation) => void
}): Promise<{ candidateIds: string[]; clipIds: string[] }> {
  let step: AiEditStep = 'transcribe'
  const report = (status: AiEditActivity['status'], detail: string) => callbacks.activity({ step, status, detail })
  try {
    let detail = await readProject(plan.projectId, signal)
    if (projectPlanBasis(detail) !== plan.basis) throw new Error('The project changed since review. Review a fresh plan before starting.')
    if (isActiveJob(detail.jobs?.transcription.status) || isActiveJob(detail.jobs?.analysis.status) || detail.clips.some((clip) => isActiveJob(clip.status))) {
      throw new Error('The project has an active job. Wait for it to finish, then review a fresh plan.')
    }
    let analysisMode = plan.brief.analysisMode
    let renderOptions = structuredClone(plan.renderOptions)
    if (plan.transcribe) {
      report('running', 'Transcribing source audio with your configured transcription provider…')
      try {
        await api.transcribe(plan.projectId, undefined, signal)
        await callbacks.refresh()
        detail = await waitForProject(plan.projectId, signal, (value) => jobDone(value.jobs?.transcription, 'Transcription'))
        if (transcriptHasSpeech(detail)) report('done', 'Speech transcript ready for captions and project context.')
        else {
          if (plan.analyze && sourceHasVideo(detail) === false) throw new Error('No usable speech was found in this audio-only source.')
          analysisMode = 'visual'
          report('done', `No speech found. Captions off; ${plan.analyze ? 'continuing with sampled-frame visual analysis.' : 'reusing the reviewed cuts.'}`)
        }
      } catch (error) {
        signal.throwIfAborted()
        // A monitoring/transport failure is not proof that an accepted job stopped.
        detail = await readProject(plan.projectId, signal)
        if (isActiveJob(detail.jobs?.transcription.status)) throw error
        if (sourceHasVideo(detail) === false && plan.analyze) throw error
        analysisMode = 'visual'
        report('error', `${aiEditError(error)} Captions off; ${plan.analyze ? 'continuing with sampled-frame visual analysis.' : 'reusing the reviewed cuts.'}`)
      }
    } else report('done', !renderOptions.captionSettings?.enabled ? 'Captions off; transcription skipped.'
      : transcriptHasSpeech(detail) ? 'Reusing the available speech transcript.' : 'No usable speech or audio; captions off. Transcription skipped.')

    let captions = !!renderOptions.captionSettings?.enabled && transcriptHasSpeech(detail)
    renderOptions.captionSettings = { ...renderOptions.captionSettings, enabled: captions }

    step = 'analyze'
    if (plan.analyze) {
      if (analysisMode === 'transcript' && !transcriptHasSpeech(detail)) {
        if (sourceHasVideo(detail) === false) throw new Error('Transcript analysis needs usable speech. No video frames are available in this audio-only source.')
        analysisMode = 'visual'
      }
      report('running', analysisMode === 'visual' ? 'Your visual model is inspecting sampled video frames with your editing brief…'
        : 'Your analysis model is ranking transcript moments with your editing brief…')
      const analysis = await aiEditApi.analyze(plan.projectId, { mode: analysisMode, brief: formatAiEditBrief(plan.brief) }, signal)
      await callbacks.refresh()
      detail = await waitForProject(plan.projectId, signal, (value) => jobDone(value.jobs?.analysis, 'Analysis'))
      const visual = analysisMode === 'visual' ? projectVisualRecommendation(detail) : null
      if (visual) {
        callbacks.recommendation?.(visual)
        if (plan.useRecommendedSettings) {
          renderOptions = applyAiRecommendedSettings(plan.baseRenderOptions, visual, plan.brief.captions)
          captions = !!renderOptions.captionSettings?.enabled && transcriptHasSpeech(detail)
          renderOptions.captionSettings = { ...renderOptions.captionSettings, enabled: captions }
        }
      }
      report('done', `${detail.candidates.length} ${analysisMode === 'visual' ? 'visual' : 'transcript-based'} candidates returned${visual
        ? ` · ${visual.model} · ${visual.sampledFrameTimes.length} sampled frames. ${visual.contextNotice || ''}` : analysis.model ? ` · ${analysis.model}.` : '.'}`)
    } else report('done', `Reusing ${detail.candidates.length} existing candidates; no replacement requested.`)

    step = 'select'
    signal.throwIfAborted()
    const chosen = chooseAiCandidates(detail, plan.brief)
    if (!chosen.length) throw new Error('No valid clip candidates were returned. Adjust the brief and analyze again, or create a manual cut.')
    if (!plan.analyze && JSON.stringify(chosen.map((candidate) => candidate.id)) !== JSON.stringify(plan.candidateIds)) {
      throw new Error('Candidate ranking changed. Review a fresh plan before selecting.')
    }
    report('running', `Saving the top ${chosen.length} candidates, with your length preference applied locally…`)
    await api.updateCandidates(plan.projectId, chosen.map((candidate) => candidate.id), signal)
    signal.throwIfAborted()
    callbacks.selectionSaved()
    report('done', `${chosen.length} clips selected. Existing project selection has been updated.`)
    await callbacks.refresh()

    step = 'render'
    report('running', `Requesting ${chosen.length} renders · ${describeAiOutput(renderOptions.outputSettings)} · captions ${captions ? 'on' : 'off'}…`)
    const clipIds: string[] = []
    for (const candidate of chosen) {
      signal.throwIfAborted()
      // Per-clip requests render only the reviewed candidates, independent of a later selection change.
      const result = await api.renderClip(plan.projectId, candidate.id, renderOptions, signal)
      signal.throwIfAborted()
      if (!result.clipId) throw new Error('The render API returned no clip ID. Check Manual mode for any queued jobs.')
      clipIds.push(result.clipId)
      callbacks.renderQueued(candidate.id, result.clipId)
    }
    await callbacks.refresh()
    await waitForProject(plan.projectId, signal, (value) => {
      const clips = clipIds.map((id) => value.clips.find((clip) => clip.id === id))
      const failed = clips.find((clip) => isFailedJob(clip?.status))
      if (failed) throw new Error(`Render failed: ${failed.render_log || 'Review the clip in Manual mode.'} Other completed clips remain downloadable.`)
      const done = clips.filter((clip) => clip?.status === 'done').length
      report('running', `${done} of ${clipIds.length} renders completed. Remaining jobs are queued or rendering on the server.`)
      return done === clipIds.length
    })
    report('done', `${clipIds.length} MP4s ready. Preview the framing and captions before sharing.`)
    await callbacks.refresh()
    return { candidateIds: chosen.map((candidate) => candidate.id), clipIds }
  } catch (error) {
    report(signal.aborted ? 'stopped' : 'error', signal.aborted ? 'Automation stopped. Already queued server jobs may finish; no further steps will start.' : aiEditError(error))
    throw error
  }
}
