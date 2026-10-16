import { api, type AudienceBrief, type Candidate, type CaptionSettings, type ClipSelection, type JobStatus, type OutputSettings, type ProjectDetail, type RenderOptions, type VideoFilters } from './client'
import { audienceFromBrief } from './audience'
import { isActiveJob, isFailedJob } from './jobs'
import { fallbackRenderContract } from './renderDefaults'

export interface AiEditBrief {
  audience: string
  goal: string
  notes: string
  platform: 'shorts' | 'reels' | 'tiktok'
  clipCount: number
  length: 'any' | '15-30' | '30-60'
  captions: boolean
  reuseCandidates: boolean
  analysisMode: 'auto' | 'visual' | 'transcript'
}

type EditorialAssessment = { audienceFit: number; hook: number; payoff: number; clarity: number; shareability: number }

export interface VisualRecommendation {
  aspectRatio: { mode: 'preserve' | 'crop' | 'pad'; ratio: 'source' | '9:16' | '16:9' | '1:1' | '4:5' }
  videoFilters: Partial<VideoFilters>
  captionSettings: Partial<CaptionSettings>
  candidates: { start: number; end: number; score: number; hook: string; rationale: string; topic?: string; audienceReason?: string; assessment?: EditorialAssessment; evidenceFrame?: number; evidenceDescription?: string; selection?: ClipSelection }[]
  audience: string
  rationale: string
  analysisBasis: 'sampled-frames' | 'audio-and-sampled-frames' | 'audio-transcript'
  source: { width: number; height: number; durationSeconds: number; hasVideo: boolean; hasAudio: boolean }
  sampledFrameTimes: number[]
  contextNotice: string
  model: string
  provenance?: { version: 1; briefFingerprint: string }
}

const platforms = { shorts: 'YouTube Shorts', reels: 'Instagram Reels', tiktok: 'TikTok' }
const lengths = { any: 'Any natural length', '15-30': '15–30 seconds', '30-60': '30–60 seconds' }

export const aiBriefFieldLimits = { audience: 1000, goal: 1000, notes: 2000 } as const

/** One brief is shared by recommendations and clip analysis. */
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
  recommendations: async (projectId: string, brief: AiEditBrief, signal?: AbortSignal): Promise<VisualRecommendation> => {
    const controller = signal || new AbortController().signal
    let detail = await readProject(projectId, controller)
    // Audio context is required for a complete recommendation even when the
    // user has captions disabled. An existing silent transcript is not retried.
    if (!detail.transcript && sourceHasAudio(detail) !== false) {
      try {
        await api.transcribe(projectId, undefined, controller)
        await waitForProject(projectId, controller, (value) => {
          if (isFailedJob(value.jobs?.transcription.status)) {
            if (sourceHasVideo(value) === false) throw new Error(`Transcription failed: ${value.jobs?.transcription.error || 'Try again.'}`)
            return true
          }
          return value.jobs?.transcription.status === 'done'
        })
      } catch (error) {
        controller.throwIfAborted()
        detail = await readProject(projectId, controller)
        if (sourceHasVideo(detail) === false || isActiveJob(detail.jobs?.transcription.status)) throw error
      }
    }
    const value = await aiEditRequest<VisualRecommendation>(projectId, 'ai-edit/recommendations', {
      brief: formatAiEditBrief(brief), audienceBrief: audienceFromBrief(brief),
    }, signal)
    if (!isVisualRecommendation(value)) throw new Error('The recommendation endpoint returned an incomplete visual response. Please try again.')
    return value
  },
  analyze: async (projectId: string, options: { mode: 'auto' | 'visual' | 'transcript'; brief: string; provider?: string; audienceBrief?: AudienceBrief }, signal?: AbortSignal) => {
    const value = await aiEditRequest<{ status: string; model?: string }>(projectId, 'analyze', options, signal)
    if (!isPlainObject(value) || value.status !== 'started' || (value.model !== undefined && (typeof value.model !== 'string' || value.model.length > 200))) {
      throw new Error('The analysis endpoint returned an incomplete job response. Please try again.')
    }
    return value
  },
}

function isVisualRecommendation(value: unknown): value is VisualRecommendation {
  if (!isPlainObject(value)) return false
  const item = value as unknown as VisualRecommendation
  const source = item.source
  const aspect = item.aspectRatio
  if (!['sampled-frames', 'audio-and-sampled-frames', 'audio-transcript'].includes(item.analysisBasis) || !boundedText(item.model, 200) || !boundedText(item.rationale, 1000)
      || !boundedText(item.contextNotice, 1000) || !boundedText(item.audience, 1000) || !isPlainObject(source)
      || typeof source.hasVideo !== 'boolean' || typeof source.hasAudio !== 'boolean'
      || !finiteNumber(source.durationSeconds, 0) || (source.hasVideo && (!finiteNumber(source.width, 1) || !finiteNumber(source.height, 1)))
      || (!source.hasVideo && (!finiteNumber(source.width, 0) || !finiteNumber(source.height, 0)))) return false
  if (!isPlainObject(aspect) || !['preserve', 'crop', 'pad'].includes(aspect.mode)
      || !['source', '9:16', '16:9', '1:1', '4:5'].includes(aspect.ratio)
      || ((aspect.mode === 'preserve') !== (aspect.ratio === 'source'))) return false
  if (!isVisualFilters(item.videoFilters) || !isCaptionSuggestion(item.captionSettings)) return false
  if (!Array.isArray(item.sampledFrameTimes)
      || (item.analysisBasis === 'audio-transcript' ? item.sampledFrameTimes.length !== 0 : item.sampledFrameTimes.length < 8 || item.sampledFrameTimes.length > 12)
      || item.sampledFrameTimes.some((time) => !finiteNumber(time, 0) || time >= source.durationSeconds)) return false
  if (!Array.isArray(item.candidates) || item.candidates.length > 8 || item.candidates.some((candidate) => {
    if (!isPlainObject(candidate) || !finiteNumber(candidate.start, 0) || !finiteNumber(candidate.end, 0)
        || candidate.end <= candidate.start || candidate.end > source.durationSeconds || candidate.end - candidate.start > 60
        || (source.durationSeconds >= 15 && candidate.end - candidate.start < 15) || !finiteNumber(candidate.score, 0, 100)
        || !boundedText(candidate.hook, 200) || !boundedText(candidate.rationale, 1000)) return true
    if (item.analysisBasis === 'audio-transcript') return !isVisualSelection(candidate.selection)
      || candidate.selection.evidence.basis !== 'transcript'
    const frame = candidate.evidenceFrame
    if (!boundedText(candidate.topic, 100) || !boundedText(candidate.audienceReason, 300)
        || !isAssessment(candidate.assessment) || typeof frame !== 'number' || !Number.isInteger(frame)
        || frame < 0 || frame >= item.sampledFrameTimes.length
        || (item.analysisBasis === 'sampled-frames' && (item.sampledFrameTimes[frame] < candidate.start
          || item.sampledFrameTimes[frame] >= candidate.end))
        || !boundedText(candidate.evidenceDescription, 300)) return true
    return candidate.selection !== undefined && !isVisualSelection(candidate.selection)
  })) return false
  return item.provenance === undefined || (isPlainObject(item.provenance)
    && item.provenance.version === 1 && /^fnv1a64-v1:[0-9a-f]{16}$/.test(item.provenance.briefFingerprint))
}

function isPlainObject(value: unknown): value is Record<string, any> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= maximum
}

function finiteNumber(value: unknown, minimum: number, maximum = Number.POSITIVE_INFINITY): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum
}

function isAssessment(value: unknown): value is EditorialAssessment {
  return isPlainObject(value) && Object.keys(value).length === 5
    && ['audienceFit', 'hook', 'payoff', 'clarity', 'shareability'].every((key) => finiteNumber(value[key], 0, 5))
}

function isVisualSelection(value: unknown): value is ClipSelection {
  if (!isPlainObject(value) || value.method !== 'audience-first-v1' || typeof value.audienceInferred !== 'boolean'
      || !boundedText(value.audience, 1000) || !boundedText(value.audienceReason, 300) || !boundedText(value.topic, 100)
      || !isAssessment(value.assessment) || !isPlainObject(value.evidence)) return false
  if (value.evidence.basis === 'sampled-frames') return finiteNumber(value.evidence.timeSeconds, 0)
    && boundedText(value.evidence.description, 300)
  return value.evidence.basis === 'transcript' && boundedText(value.evidence.openingQuote, 240)
    && boundedText(value.evidence.closingQuote, 240)
}

function isVisualFilters(value: unknown): value is Partial<VideoFilters> {
  if (!isPlainObject(value)) return false
  const ranges = fallbackRenderContract.videoFilters.ranges
  return Object.entries(value).every(([key, number]) => key in ranges && finiteNumber(number, ranges[key as keyof VideoFilters].min, ranges[key as keyof VideoFilters].max))
}

function isCaptionSuggestion(value: unknown): value is Partial<CaptionSettings> {
  if (!isPlainObject(value)) return false
  const allowed = new Set(['enabled', 'preset', 'placement'])
  if (Object.keys(value).some((key) => !allowed.has(key))) return false
  return (value.enabled === undefined || typeof value.enabled === 'boolean')
    && (value.preset === undefined || fallbackRenderContract.caption.presets.some((preset) => preset.id === value.preset))
    && (value.placement === undefined || fallbackRenderContract.caption.placements.includes(value.placement))
}

export function editingBriefFingerprint(brief: AiEditBrief): string {
  const canonical = JSON.stringify({ brief: formatAiEditBrief(brief), audienceBrief: audienceFromBrief(brief) })
  let value = 0xCBF29CE484222325n
  for (const byte of new TextEncoder().encode(canonical)) {
    value ^= BigInt(byte)
    value = BigInt.asUintN(64, value * 0x100000001B3n)
  }
  return `fnv1a64-v1:${value.toString(16).padStart(16, '0')}`
}

export function visualRecommendationIsCurrent(recommendation: VisualRecommendation, brief: AiEditBrief): boolean {
  return recommendation.provenance?.version === 1
    && recommendation.provenance.briefFingerprint === editingBriefFingerprint(brief)
}

export function projectVisualRecommendation(detail: ProjectDetail): VisualRecommendation | null {
  // Kept here so the shared client can be evolved independently by the aspect owner.
  const value = detail.project.visualAnalysis
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
  length: 'any', captions: true, reuseCandidates: false, analysisMode: 'auto',
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

export function sourceHasVideo(detail: ProjectDetail): boolean | undefined {
  const visual = projectVisualRecommendation(detail)
  if (visual) return visual.source.hasVideo
  try { return JSON.parse(detail.project.source_metadata_json || 'null')?.has_video }
  catch { return undefined }
}

export function createAiEditPlan(detail: ProjectDetail, brief: AiEditBrief, renderOptions: RenderOptions, recommendation: VisualRecommendation | null = null): AiEditPlan {
  const analyze = !detail.candidates.length || !brief.reuseCandidates
  const effectiveBrief = { ...brief, analysisMode: 'auto' as const }
  return {
    projectId: detail.project.id, basis: projectPlanBasis(detail), brief: effectiveBrief,
    transcribe: (brief.captions || analyze) && !detail.transcript && sourceHasAudio(detail) !== false, analyze,
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
           report('done', `No speech found. Captions off; ${plan.analyze ? 'the AI will use visual evidence.' : 'reusing the reviewed cuts.'}`)
        }
      } catch (error) {
        signal.throwIfAborted()
        // A monitoring/transport failure is not proof that an accepted job stopped.
        detail = await readProject(plan.projectId, signal)
        if (isActiveJob(detail.jobs?.transcription.status)) throw error
        if (sourceHasVideo(detail) === false && plan.analyze) throw error
        report('error', `${aiEditError(error)} Captions off; ${plan.analyze ? 'the AI will use the available audio/video evidence.' : 'reusing the reviewed cuts.'}`)
      }
    } else report('done', !renderOptions.captionSettings?.enabled ? 'Captions off; transcription skipped.'
      : transcriptHasSpeech(detail) ? 'Reusing the available speech transcript.' : 'No usable speech or audio; captions off. Transcription skipped.')

    let captions = !!renderOptions.captionSettings?.enabled && transcriptHasSpeech(detail)
    renderOptions.captionSettings = { ...renderOptions.captionSettings, enabled: captions }

    step = 'analyze'
    if (plan.analyze) {
      report('running', 'Your AI is evaluating the full speech transcript and sampled video frames together…')
      const analysis = await aiEditApi.analyze(plan.projectId, { mode: 'auto',
        brief: formatAiEditBrief(plan.brief), audienceBrief: audienceFromBrief(plan.brief) }, signal)
      await callbacks.refresh()
      detail = await waitForProject(plan.projectId, signal, (value) => jobDone(value.jobs?.analysis, 'Analysis'))
      const visual = projectVisualRecommendation(detail)
      if (visual) {
        callbacks.recommendation?.(visual)
        if (plan.useRecommendedSettings) {
          renderOptions = applyAiRecommendedSettings(plan.baseRenderOptions, visual, plan.brief.captions)
          captions = !!renderOptions.captionSettings?.enabled && transcriptHasSpeech(detail)
          renderOptions.captionSettings = { ...renderOptions.captionSettings, enabled: captions }
        }
      }
      report('done', `${detail.candidates.length} audio + visual candidates returned${visual
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
