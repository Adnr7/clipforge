const API_BASE = '/api'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  })
  if (!response.ok) {
    const text = await response.text()
    let message = text || response.statusText || `Request failed (${response.status})`
    try {
      const body = JSON.parse(text)
      if (typeof body.error === 'string') message = body.error
      else if (typeof body.message === 'string') message = body.message
    } catch {
      // Gateway HTML is not useful in an editing workspace.
      if (/<(?:!doctype|html|head|body)\b/i.test(text)) message = `Server returned HTTP ${response.status}. Try again, or check the provider connection in Settings.`
    }
    throw new Error(message.slice(0, 1000))
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}

export type TranscriptionMode = 'local' | 'cloud'
export interface Settings {
  llmProvider: string
  transcriptionMode: TranscriptionMode
  whisperModel: string
  captionStyle: string
  dataDir: string
  customBaseUrl: string
  customModel: string
  ollamaModel?: string
}

export interface ProviderProfile {
  id: string
  name: string
  kind: 'llm' | 'transcription'
  provider: string
  baseUrl: string
  model: string
  hasApiKey: boolean
  active: boolean
  createdAt: string
  updatedAt: string
  effectiveModel?: string
  effectiveBaseUrl?: string
}

export interface ProviderProfilesResponse {
  profiles: ProviderProfile[]
  active: { llm?: string | null; transcription?: string | null }
  fallback?: { llm?: string | null; transcription?: string | null }
}

export interface CaptionSettings {
  enabled?: boolean
  uppercase?: boolean
  fontFamily?: 'default' | 'sans' | 'serif' | 'mono' | 'display'
  verticalPosition?: number | null
  preset: string
  placement: 'top' | 'center' | 'bottom'
  fontSize: number
  fontColor: string
  outlineWidth: number
  outlineColor: string
  shadowEnabled: boolean
  shadowColor: string
  shadowX: number
  shadowY: number
  backgroundEnabled: boolean
  backgroundColor: string
  backgroundOpacity: number
  backgroundPadding: number
  chunkMode: 'chunk' | 'word'
  wordsPerChunk: number
  maxGapSeconds: number
  splitOnSpeaker: boolean
}

export interface VideoFilters {
  brightness: number
  contrast: number
  saturation: number
  blur: number
  sharpen: number
}

export type AspectRatio = 'source' | '16:9' | '9:16' | '1:1' | '4:5' | '4:3' | '3:2'
export interface OutputSettings {
  mode: 'source' | 'manual' | 'ai'
  aspectRatio: AspectRatio
  fit: 'contain' | 'crop'
  maxDimension: number
}

export interface ProjectRenderSettings {
  captionSettings: CaptionSettings
  videoFilters: VideoFilters
  outputSettings: OutputSettings
}

export interface RenderSettingsContract {
  output: {
    defaults: OutputSettings
    legacyDefaults: OutputSettings
    modes: OutputSettings['mode'][]
    aspectRatios: AspectRatio[]
    fits: OutputSettings['fit'][]
    maxDimension: { min: number; max: number }
    fitBehavior: string
    sourceBehavior: string
  }
  caption: {
    presets: { id: string; label: string; settings: CaptionSettings }[]
    placements: CaptionSettings['placement'][]
    chunkModes: CaptionSettings['chunkMode'][]
    ranges: Record<string, { min: number; max: number }>
  }
  videoFilters: {
    defaults: VideoFilters
    ranges: Record<keyof VideoFilters, { min: number; max: number }>
    manualOnly: boolean
  }
}

export interface FilterSuggestion {
  id: string
  projectId: string
  settings: VideoFilters
  captionSettings?: CaptionSettings | null
  outputSettings?: OutputSettings | null
  basis?: string
  contextNotice?: string
  source: 'ai'
  model: string | null
  rationale: string | null
  applied: boolean
  createdAt: string
}

export interface RenderOptions {
  captionStyle?: string
  captionSettings?: Partial<CaptionSettings>
  videoFilters?: Partial<VideoFilters>
  outputSettings?: Partial<OutputSettings>
}

export interface JobStatus {
  status: string
  error?: string | null
  log?: string | null
}

export interface YoutubeMetadata {
  ok: boolean
  error?: string
  videoId?: string
  title?: string
  duration?: number | null
  uploader?: string
  license?: string | null
  isCreativeCommons?: boolean
  isLikelyCopyrighted?: boolean
  reason?: string
}

export interface YoutubeDownloadStatus {
  status: string
  path?: string | null
  error?: string | null
  progress?: number
  log?: string[] | string
}

export interface EnvironmentStatus {
  dataDir: string
  hasFfmpeg: boolean
  hasFfprobe: boolean
  hasDeepgramKey: boolean
  hasDeepseekKey: boolean
  hasAnthropicKey: boolean
  hasGeminiKey: boolean
  hasOpenaiKey: boolean
  hasOpenrouterKey: boolean
  hasGroqKey: boolean
  hasCustomProvider: boolean
  llmProvider: string
  hasYtdlp: boolean
}

export interface Project {
  id: string
  name: string
  source_path: string
  source_duration: number | null
  status: string
  transcription_mode: string
  caption_style: string
  caption_settings_json?: string | null
  captionSettings?: CaptionSettings
  videoFilters?: VideoFilters
  outputSettings?: OutputSettings
  render_settings_json?: string | null
  source_metadata_json?: string | null
  created_at: string
  updated_at: string
}

export interface Candidate {
  id: string
  project_id: string
  start_sec: number
  end_sec: number
  score: number
  hook: string
  rationale: string
  rank: number
  selected: number
}

export interface ManualCandidateInput {
  startSec: number
  endSec: number
  title?: string
}

export interface MediaProbe {
  duration_sec: number
  has_video: boolean
  has_audio: boolean
  width: number
  height: number
  display_width?: number
  display_height?: number
  sample_aspect_ratio?: number
  rotation?: number
  video_codec: string
  audio_codec: string
}

export interface Clip {
  id: string
  candidate_id: string
  status: string
  output_path: string | null
  render_log: string | null
  created_at?: string
  render_settings_json?: string | null
}

export interface ProjectDetail {
  project: Project
  transcript: { id: string; raw_json: string; language: string; engine: string } | null
  candidates: Candidate[]
  clips: Clip[]
  jobs?: { transcription: JobStatus; analysis: JobStatus }
}

export interface TranscriptData {
  language: string
  duration: number
  speakers: string[]
  words: { text: string; start: number; end: number; speaker?: string }[]
  segments: { start: number; end: number; text: string; speaker?: string }[]
}

export interface ConnectionTestResult {
  ok: boolean
  message: string
  latencyMs: number | null
}

export const api = {
  getStatus: (signal?: AbortSignal) => request<EnvironmentStatus>('/status', { signal }),
  listProjects: (signal?: AbortSignal) => request<Project[]>('/projects', { signal }),
  createProject: (data: { name: string; sourcePath: string; transcriptionMode?: TranscriptionMode }, signal?: AbortSignal) =>
    request<{ id: string }>('/projects', { method: 'POST', body: JSON.stringify(data), signal }),
  uploadProject: (file: File, name: string, transcriptionMode: TranscriptionMode, signal?: AbortSignal) => {
    const body = new FormData()
    body.append('file', file)
    body.append('name', name)
    body.append('transcriptionMode', transcriptionMode)
    return request<{ id: string }>('/projects/upload', { method: 'POST', body, signal })
  },
  getProject: (id: string, signal?: AbortSignal) => request<ProjectDetail>(`/projects/${id}`, { signal }),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' }),
  renameProject: (id: string, name: string) =>
    request<void>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  probeMedia: (id: string, signal?: AbortSignal) => request<MediaProbe>(`/projects/${id}/probe`, { method: 'POST', signal }),
  extractAudio: (id: string) => request(`/projects/${id}/extract-audio`, { method: 'POST' }),
  transcribe: (id: string, mode?: string, signal?: AbortSignal) =>
    request(`/projects/${id}/transcribe`, { method: 'POST', body: JSON.stringify({ mode }), signal }),
  getTranscriptionStatus: (id: string, signal?: AbortSignal) =>
    request<JobStatus>(`/projects/${id}/transcribe/status`, { signal }),
  analyze: (id: string, provider?: string, signal?: AbortSignal) =>
    request(`/projects/${id}/analyze`, { method: 'POST', body: JSON.stringify({ provider }), signal }),
  getAnalysisStatus: (id: string, signal?: AbortSignal) =>
    request<JobStatus>(`/projects/${id}/analyze/status`, { signal }),
  renderClip: (projectId: string, candidateId: string, options: RenderOptions = {}, signal?: AbortSignal) =>
    request<{ clipId: string; status?: string }>(`/projects/${projectId}/render/${candidateId}`, {
      method: 'POST',
      body: JSON.stringify(options),
      signal,
    }),
  getClipStatus: (clipId: string, signal?: AbortSignal) =>
    request<JobStatus>(`/clips/${clipId}/status`, { signal }),
  renderBatch: (projectId: string, options: RenderOptions = {}) =>
    request<{ clipIds: string[]; status?: string }>(`/projects/${projectId}/render-batch`, {
      method: 'POST', body: JSON.stringify(options),
    }),
  updateCandidates: (projectId: string, selectedIds: string[], signal?: AbortSignal) =>
    request(`/projects/${projectId}/candidates`, {
      method: 'PATCH',
      body: JSON.stringify({ selectedIds }),
      signal,
    }),
  createManualCandidate: (projectId: string, data: ManualCandidateInput) =>
    request<Candidate>(`/projects/${projectId}/candidates/manual`, {
      method: 'POST', body: JSON.stringify(data),
    }),
  testConnection: (provider?: string, profileId?: string) =>
    request<ConnectionTestResult>('/test-connection', {
      method: 'POST',
      body: JSON.stringify({ ...(provider ? { provider } : {}), ...(profileId ? { profileId } : {}) }),
    }),
  checkYoutube: (url: string, signal?: AbortSignal) =>
    request<YoutubeMetadata>('/youtube/check', { method: 'POST', body: JSON.stringify({ url }), signal }),
  downloadYoutube: (url: string, signal?: AbortSignal) =>
    request<{ jobId: string }>('/youtube/download', { method: 'POST', body: JSON.stringify({ url }), signal }),
  getYoutubeDownloadStatus: (jobId: string, signal?: AbortSignal) =>
    request<YoutubeDownloadStatus>(`/youtube/download/${jobId}`, { signal }),
  pullOllama: (model: string) =>
    request(`/ollama/pull`, { method: 'POST', body: JSON.stringify({ model }) }),
  getSettings: (signal?: AbortSignal) => request<Settings>('/settings', { signal }),
  getRenderSettings: (signal?: AbortSignal) => request<RenderSettingsContract>('/render-settings', { signal }),
  listProviderProfiles: (signal?: AbortSignal) => request<ProviderProfilesResponse>('/provider-profiles', { signal }),
  createProviderProfile: (data: { name: string; kind: ProviderProfile['kind']; provider: string; apiKey?: string; baseUrl?: string; model?: string }) =>
    request<ProviderProfile>('/provider-profiles', { method: 'POST', body: JSON.stringify(data) }),
  updateProviderProfile: (id: string, data: Partial<{ name: string; kind: ProviderProfile['kind']; provider: string; apiKey: string; baseUrl: string; model: string }>) =>
    request<ProviderProfile>(`/provider-profiles/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteProviderProfile: (id: string) => request<void>(`/provider-profiles/${id}`, { method: 'DELETE' }),
  selectActiveProviderProfile: (kind: ProviderProfile['kind'], profileId: string | null) =>
    request<{ active: { llm?: string; transcription?: string } }>('/provider-profiles/active', {
      method: 'PUT', body: JSON.stringify({ kind, profileId }),
    }),
  listFilterSuggestions: (projectId: string, signal?: AbortSignal) =>
    request<FilterSuggestion[]>(`/projects/${projectId}/filter-suggestions`, { signal }),
  generateFilterSuggestion: (projectId: string, brief: string, signal?: AbortSignal) =>
    request<FilterSuggestion>(`/projects/${projectId}/filter-suggestions/generate`, { method: 'POST', body: JSON.stringify({ brief }), signal }),
  getProjectRenderSettings: (projectId: string, signal?: AbortSignal) =>
    request<ProjectRenderSettings>(`/projects/${projectId}/render-settings`, { signal }),
  saveRenderSettings: (projectId: string, captionSettings: CaptionSettings, videoFilters: VideoFilters, outputSettings?: OutputSettings) =>
    request<ProjectRenderSettings>(`/projects/${projectId}/render-settings`, { method: 'PUT', body: JSON.stringify({ captionSettings, videoFilters, outputSettings }) }),
  saveOutputSettings: (projectId: string, outputSettings: OutputSettings) =>
    request<ProjectRenderSettings>(`/projects/${projectId}/render-settings`, { method: 'PUT', body: JSON.stringify({ outputSettings }) }),
  // Keep time fourth and AbortSignal fifth for existing callers; output is sixth.
  previewFrame: async (projectId: string, captionSettings: CaptionSettings, videoFilters: VideoFilters, time: number, signal?: AbortSignal, outputSettings?: OutputSettings): Promise<Blob> => {
    const response = await fetch(`${API_BASE}/projects/${projectId}/preview-frame`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ captionSettings, videoFilters, outputSettings, time }), signal })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(data.error || `Preview failed (HTTP ${response.status})`)
    }
    return response.blob()
  },
  exportUrl: (projectId: string, clipIds: string[]) => `${API_BASE}/projects/${projectId}/export?${new URLSearchParams({ clipIds: clipIds.join(',') })}`,
  updateSettings: async (data: Record<string, string>) => {
    const result = await request('/settings', { method: 'PUT', body: JSON.stringify(data) })
    window.dispatchEvent(new Event('clipforge:settings'))
    return result
  },
  mediaFileUrl: (projectId: string) => `${API_BASE}/projects/${projectId}/file`,
  clipFileUrl: (clipId: string) => `${API_BASE}/clips/${clipId}/file`,
}
