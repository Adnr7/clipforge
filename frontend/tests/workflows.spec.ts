import { expect, test, type Page } from '@playwright/test'
import type { CaptionSettings, OutputSettings, ProjectRenderSettings, VideoFilters } from '../src/api/client'
import { fallbackRenderContract } from '../src/api/renderDefaults'

const projectFixture = {
  id: 'p1', name: 'A thoughtful conversation', source_path: '/media/source.wav', source_duration: 30,
  status: 'analyzed', transcription_mode: 'local', caption_style: 'classic', caption_settings_json: null as string | null,
  captionSettings: undefined as CaptionSettings | undefined, videoFilters: undefined as VideoFilters | undefined,
  outputSettings: undefined as OutputSettings | undefined,
  created_at: '2026-10-01T12:00:00', updated_at: '2026-10-04T12:00:00',
}
const candidatesFixture = [1, 2].map((rank) => ({
  id: `c${rank}`, project_id: 'p1', start_sec: rank * 5, end_sec: rank * 5 + 10, score: 92 - rank,
  hook: `A strong moment ${rank}`, rationale: 'A clear idea with a natural ending.', rank, selected: 1,
}))
const transcriptFixture = {
  id: 't1', language: 'en', engine: 'whisper',
  raw_json: JSON.stringify({ language: 'en', duration: 30, speakers: [], words: [
    { start: 5, end: 6, text: 'First' }, { start: 10, end: 11, text: 'Second' },
  ], segments: [{ start: 5, end: 10, text: 'First idea.' }, { start: 10, end: 20, text: 'Second idea.' }] }),
}

type ClipFixture = {
  id: string; candidate_id: string; status: string; output_path: string | null; render_log: string | null; created_at: string
}
type ProfileFixture = {
  id: string; name: string; kind: 'llm' | 'transcription'; provider: string; baseUrl: string; model: string
  hasApiKey: boolean; active: boolean; createdAt: string; updatedAt: string
}
type MockState = {
  saved: Record<string, string>[]; events: string[]; project: typeof projectFixture; candidates: typeof candidatesFixture
  transcript: typeof transcriptFixture | null; clips: ClipFixture[]; uploads: { type: string; body: string }[]
  created: Record<string, unknown>[]; settings: {
    llmProvider: string; transcriptionMode: 'local' | 'cloud'; whisperModel: string; captionStyle: string
    dataDir: string; customBaseUrl: string; customModel: string; ollamaModel: string
  }; environmentKeys: Record<string, string>; profiles: ProfileFixture[]; activeProfiles: { llm?: string | null; transcription?: string | null }
  renderSettingsSaves: ProjectRenderSettings[]; analysisRequests: { mode: 'transcript' | 'visual'; brief: string }[]
  previewFrames: Record<string, unknown>[]; suggestions: Record<string, unknown>[]; suggestionRequests: Record<string, unknown>[]
  renderRequests: { candidateId: string; body: Record<string, unknown> }[]; manualRequests: Record<string, unknown>[]; batchStyles: string[]; exportRequests: string[]
  clipStatusRequests: string[]; legacyJobStatusRequests: string[]; detailReads: number; batchProjectReads: number
  batchActive: boolean; batchIds: string[]; failNextProjectRead: boolean; delaySettingsMs: number; delaySelectionMs: number; youtubeTerminal: boolean
}

function audioFixture() {
  const buffer = Buffer.alloc(44 + 30 * 8000 * 2)
  buffer.write('RIFF', 0); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write('WAVEfmt ', 8)
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(8000, 24); buffer.writeUInt32LE(16000, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36); buffer.writeUInt32LE(buffer.length - 44, 40)
  return buffer
}

function jpegFixture() {
  return Buffer.from('/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/AX//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/AX//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAACD/2gAIAQEAAT8QH//Z', 'base64')
}

function zipFixture() {
  return Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00])
}

const completedClips: ClipFixture[] = [
  { id: 'old', candidate_id: 'c1', status: 'done', output_path: '/old.mp4', render_log: null, created_at: '2026-10-01T12:00:00' },
  { id: 'latest', candidate_id: 'c1', status: 'done', output_path: '/latest.mp4', render_log: null, created_at: '2026-10-04T12:00:00' },
  { id: 'failed', candidate_id: 'c2', status: 'error', output_path: null, render_log: 'FFmpeg could not read media', created_at: '2026-10-04T12:00:00' },
]

async function mockApi(page: Page, options: { onboarded?: boolean; initialClips?: ClipFixture[]; delaySettingsMs?: number } = {}) {
  if (options.onboarded !== false) await page.addInitScript(() => localStorage.setItem('clipforge.onboarded', '1'))
  const state: MockState = {
    saved: [], events: [], project: { ...projectFixture }, candidates: candidatesFixture.map((candidate) => ({ ...candidate })),
    transcript: transcriptFixture, clips: (options.initialClips || completedClips).map((clip) => ({ ...clip })), uploads: [], created: [],
    settings: { llmProvider: 'deepseek', transcriptionMode: 'local', whisperModel: 'base', captionStyle: 'classic', dataDir: '/data', customBaseUrl: '', customModel: '', ollamaModel: 'llama3.2' },
     environmentKeys: {}, profiles: [], activeProfiles: {}, renderSettingsSaves: [], analysisRequests: [], previewFrames: [], suggestions: [], suggestionRequests: [],
    renderRequests: [], manualRequests: [], batchStyles: [], exportRequests: [], clipStatusRequests: [], legacyJobStatusRequests: [], detailReads: 0,
    batchProjectReads: 0, batchActive: false, batchIds: [], failNextProjectRead: false, delaySettingsMs: options.delaySettingsMs || 0, delaySelectionMs: 0, youtubeTerminal: true,
  }
  const jobs = { transcription: { status: 'idle' }, analysis: { status: 'idle' } }
  const stringValue = (body: Record<string, unknown>, key: string) => typeof body[key] === 'string' ? body[key] as string : ''
  const providerProfile = (body: Record<string, unknown>, id: string, previous?: ProfileFixture): ProfileFixture => ({
    id, name: stringValue(body, 'name'), kind: (stringValue(body, 'kind') || previous?.kind || 'llm') as ProfileFixture['kind'],
    provider: stringValue(body, 'provider') || previous?.provider || 'deepseek', baseUrl: stringValue(body, 'baseUrl') || previous?.baseUrl || '',
    model: stringValue(body, 'model') || previous?.model || '', hasApiKey: Object.prototype.hasOwnProperty.call(body, 'apiKey') ? !!stringValue(body, 'apiKey') : previous?.hasApiKey || false,
    active: false, createdAt: previous?.createdAt || '2026-10-04T13:00:00', updatedAt: '2026-10-04T13:00:00',
  })
  const profileResponse = () => ({ profiles: state.profiles, active: state.activeProfiles })
  const updateActiveFlags = () => state.profiles.forEach((profile) => { profile.active = state.activeProfiles[profile.kind] === profile.id })
  const projectResponse = () => ({ project: state.project, transcript: state.transcript, candidates: state.candidates, clips: state.clips, jobs })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()
    const json = (body: unknown, status = 200) => route.fulfill({ json: body, status })
    const body = () => (request.postDataJSON() || {}) as Record<string, unknown>

    if (path === '/status') {
      return json({ dataDir: '/data', hasFfmpeg: true, hasFfprobe: true, hasYtdlp: true, llmProvider: state.settings.llmProvider,
        hasDeepseekKey: !!state.environmentKeys.DEEPSEEK_API_KEY, hasDeepgramKey: !!state.environmentKeys.DEEPGRAM_API_KEY,
        hasAnthropicKey: !!state.environmentKeys.ANTHROPIC_API_KEY, hasGeminiKey: !!state.environmentKeys.GEMINI_API_KEY,
        hasOpenaiKey: !!state.environmentKeys.OPENAI_API_KEY, hasOpenrouterKey: !!state.environmentKeys.OPENROUTER_API_KEY,
        hasGroqKey: !!state.environmentKeys.GROQ_API_KEY, hasCustomProvider: !!state.environmentKeys.CUSTOM_API_KEY && !!state.settings.customBaseUrl })
    }
    if (path === '/settings') {
      if (method === 'PUT') {
        if (state.delaySettingsMs) await new Promise((resolve) => setTimeout(resolve, state.delaySettingsMs))
        const values = body()
        state.saved.push(values as Record<string, string>); state.events.push('settings:save')
        for (const [key, value] of Object.entries(values)) if (key.endsWith('_API_KEY') && typeof value === 'string' && value) state.environmentKeys[key] = value
        if (typeof values.LLM_PROVIDER === 'string') state.settings.llmProvider = values.LLM_PROVIDER
        if (values.TRANSCRIPTION_MODE === 'local' || values.TRANSCRIPTION_MODE === 'cloud') state.settings.transcriptionMode = values.TRANSCRIPTION_MODE
        if (typeof values.WHISPER_MODEL === 'string') state.settings.whisperModel = values.WHISPER_MODEL
        if (typeof values.CAPTION_STYLE === 'string') state.settings.captionStyle = values.CAPTION_STYLE
        if (typeof values.CUSTOM_BASE_URL === 'string') state.settings.customBaseUrl = values.CUSTOM_BASE_URL
        if (typeof values.CUSTOM_MODEL === 'string') state.settings.customModel = values.CUSTOM_MODEL
        if (typeof values.OLLAMA_MODEL === 'string') state.settings.ollamaModel = values.OLLAMA_MODEL
        return json({ ok: true })
      }
      return json(state.settings)
    }
    if (path === '/test-connection') {
      const values = body(); state.events.push(values.profileId ? `profile:test:${values.profileId}` : `connection:test:${stringValue(values, 'provider')}`)
      return json({ ok: true, message: 'Connected', latencyMs: 23 })
    }
    if (path === '/render-settings' && method === 'GET') return json(fallbackRenderContract)
    if (path === '/provider-profiles') {
      if (method === 'POST') {
        const profile = providerProfile(body(), `profile-${state.profiles.length + 1}`)
        state.profiles.push(profile); state.events.push(`profile:create:${profile.id}`)
        return json(profile, 201)
      }
      return json(profileResponse())
    }
    if (path.startsWith('/provider-profiles/') && path !== '/provider-profiles/active') {
      const id = path.split('/').at(-1)!
      const profile = state.profiles.find((item) => item.id === id)
      if (!profile) return json({ error: 'Profile not found' }, 404)
      if (method === 'PUT') {
        const updated = providerProfile({ ...body(), kind: profile.kind, provider: profile.provider }, id, profile)
        Object.assign(profile, updated); state.events.push(`profile:update:${id}`); return json(profile)
      }
      if (method === 'DELETE') {
        state.profiles = state.profiles.filter((item) => item.id !== id)
        if (state.activeProfiles[profile.kind] === id) state.activeProfiles[profile.kind] = null
        updateActiveFlags(); state.events.push(`profile:delete:${id}`); return route.fulfill({ status: 204 })
      }
    }
    if (path === '/provider-profiles/active' && method === 'PUT') {
      const values = body(); const kind = stringValue(values, 'kind') as 'llm' | 'transcription'; const id = values.profileId == null ? null : stringValue(values, 'profileId')
      state.activeProfiles[kind] = id; updateActiveFlags(); state.events.push(`profile:active:${kind}:${id || 'fallback'}`)
      return json({ active: state.activeProfiles })
    }
    if (path === '/projects/upload') {
      state.uploads.push({ type: request.headers()['content-type'] || '', body: request.postDataBuffer()!.toString() }); return json({ id: 'p1' }, 201)
    }
    if (path === '/projects') {
      if (method === 'POST') { state.created.push(body()); return json({ id: 'p1' }, 201) }
      return json([state.project])
    }
    if (path === '/projects/p1/render-settings') {
      const current: ProjectRenderSettings = {
        captionSettings: state.project.captionSettings || fallbackRenderContract.caption.presets.find((preset) => preset.id === state.project.caption_style)!.settings,
        videoFilters: state.project.videoFilters || fallbackRenderContract.videoFilters.defaults,
        outputSettings: state.project.outputSettings || fallbackRenderContract.output.legacyDefaults,
      }
      if (method === 'GET') return json(current)
      if (method === 'PUT') {
        const saved: ProjectRenderSettings = { ...current, ...body() }
        state.project.captionSettings = saved.captionSettings; state.project.videoFilters = saved.videoFilters; state.project.outputSettings = saved.outputSettings
        state.project.caption_settings_json = JSON.stringify(saved.captionSettings); state.project.caption_style = saved.captionSettings.preset
        state.renderSettingsSaves.push(saved); state.events.push('project:render-settings:save')
        return json(saved)
      }
    }
    if (path === '/projects/p1/preview-frame' && method === 'POST') {
      state.previewFrames.push(body()); return route.fulfill({ status: 200, contentType: 'image/jpeg', body: jpegFixture() })
    }
    if (path === '/projects/p1/filter-suggestions/generate' && method === 'POST') {
      const values = body(); state.suggestionRequests.push(values)
      const suggestion = { id: `suggestion-${state.suggestions.length + 1}`, projectId: 'p1', settings: { brightness: .3, contrast: 1.2, saturation: 1.1, blur: 0, sharpen: .4 },
        captionSettings: null, basis: 'Transcript context', contextNotice: 'Transcript-based suggestion; no frames analyzed.', source: 'ai', model: 'fixture-model', rationale: `A restrained look for ${stringValue(values, 'brief') || 'this story'}.`, applied: false, createdAt: '2026-10-04T13:00:00' }
      state.suggestions.unshift(suggestion); return json(suggestion, 201)
    }
    if (path === '/projects/p1/filter-suggestions') return json(state.suggestions)
    if (path === '/projects/p1/export') {
      state.exportRequests.push(url.search); return route.fulfill({ status: 200, contentType: 'application/zip', body: zipFixture(), headers: { 'content-disposition': 'attachment; filename="clipforge-export.zip"' } })
    }
    if (path === '/projects/p1/candidates' && method === 'PATCH') {
      state.events.push('selection:save')
      if (state.delaySelectionMs) await new Promise((resolve) => setTimeout(resolve, state.delaySelectionMs))
      const selected = new Set((body().selectedIds as string[]) || [])
      state.candidates.forEach((candidate) => { candidate.selected = selected.has(candidate.id) ? 1 : 0 }); return json({ ok: true })
    }
    if (path === '/projects/p1/transcribe' && method === 'POST') {
      state.events.push('transcribe:start'); state.transcript = transcriptFixture; jobs.transcription.status = 'done'
      return json({ status: 'started' }, 202)
    }
    if (path === '/projects/p1/analyze' && method === 'POST') {
      const values = body(); state.analysisRequests.push({ mode: values.mode as 'transcript' | 'visual', brief: stringValue(values, 'brief') })
      state.events.push('analyze:start'); state.candidates = candidatesFixture.map((candidate) => ({ ...candidate, selected: 0 })); state.clips = []; jobs.analysis.status = 'done'
      return json({ status: 'started' }, 202)
    }
    if (path === '/projects/p1/render-batch' && method === 'POST') {
      const values = body(); state.batchStyles.push(stringValue(values, 'captionStyle')); state.batchActive = true; state.batchProjectReads = 0; state.failNextProjectRead = true
      state.batchIds = state.candidates.map((candidate) => `batch-${candidate.id}`); state.clips = []
      return json({ clipIds: state.batchIds, status: 'queued' }, 202)
    }
    if (path === '/projects/p1/candidates/manual' && method === 'POST') {
      const values = body(); state.manualRequests.push(values)
      const candidate = { id: `manual-${state.candidates.length + 1}`, project_id: 'p1', start_sec: Number(values.startSec), end_sec: Number(values.endSec), score: 0, hook: stringValue(values, 'title') || 'Manual cut', rationale: 'Manually selected time range; no AI analysis.', rank: state.candidates.length + 1, selected: 1 }
      state.candidates.push(candidate); state.events.push('manual:create'); return json(candidate, 201)
    }
    if (path.startsWith('/projects/p1/render/') && method === 'POST') {
      const candidateId = path.split('/').at(-1)!; const values = body(); const clipId = `missing-${candidateId}`
      state.renderRequests.push({ candidateId, body: values }); state.clips = [...state.clips.filter((clip) => clip.candidate_id !== candidateId), { id: clipId, candidate_id: candidateId, status: 'queued', output_path: null, render_log: null, created_at: '2026-10-04T14:00:00' }]
      return json({ clipId, status: 'queued' }, 202)
    }
    if (path === '/projects/p1') {
      state.detailReads += 1
      if (state.failNextProjectRead) { state.failNextProjectRead = false; return json({ error: 'Temporary connection failure' }, 503) }
      if (state.batchActive) {
        state.batchProjectReads += 1
        if (state.batchProjectReads === 2) state.clips = state.batchIds.map((id, index) => ({ id, candidate_id: `c${index + 1}`, status: 'rendering', output_path: null, render_log: null, created_at: '2026-10-04T13:00:00' }))
        if (state.batchProjectReads >= 4) state.clips = state.batchIds.map((id, index) => ({ id, candidate_id: `c${index + 1}`, status: index === 0 ? 'done' : 'error', output_path: index === 0 ? `/renders/${id}.mp4` : null, render_log: index === 0 ? null : 'Render failed for fixture', created_at: '2026-10-04T13:00:00' }))
      }
      return json(projectResponse())
    }
    if (path.endsWith('/transcribe/status') || path.endsWith('/analyze/status')) {
      state.legacyJobStatusRequests.push(path); return json({ status: 'idle' })
    }
    if (path.startsWith('/clips/') && path.endsWith('/status')) {
      state.clipStatusRequests.push(path); return json({ status: 'rendering' })
    }
    if (path === '/projects/p1/file') {
      const audio = audioFixture(); const range = request.headers().range?.match(/bytes=(\d+)-(\d*)/); const start = range ? Number(range[1]) : 0; const end = range?.[2] ? Number(range[2]) : audio.length - 1
      return route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav', body: audio.subarray(start, end + 1), headers: { 'accept-ranges': 'bytes', 'content-length': String(end - start + 1), ...(range ? { 'content-range': `bytes ${start}-${end}/${audio.length}` } : {}) } })
    }
    if (path.startsWith('/clips/') && path.endsWith('/file')) return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('fixture-mp4') })
    if (path === '/youtube/check') return json({ ok: true, title: 'An imported interview', duration: 30, uploader: 'Studio', license: 'Creative Commons', isCreativeCommons: true, isLikelyCopyrighted: false, reason: 'Creative Commons licensed' })
    if (path === '/youtube/download') return json({ jobId: 'yt1' }, 202)
    if (path === '/youtube/download/yt1') {
      state.events.push('youtube:poll'); const polls = state.events.filter((event) => event === 'youtube:poll').length
      return json(polls >= 3 && state.youtubeTerminal ? { status: 'done', progress: 100, path: '/downloads/video.mp4', log: ['Finished'] } : { status: 'downloading', progress: 45, log: ['Downloading 45%'] })
    }
    return json({ error: `Unhandled mock ${method} ${path}` }, 404)
  })
  return state
}

async function expectLibraryLayout(page: Page) {
  // #root follows the body's usable content box, excluding reserved scrollbar gutters.
  const usable = (await page.locator('#root').boundingBox())!
  const library = (await page.locator('.dashboard-main').boundingBox())!
  const grid = (await page.locator('.project-grid').boundingBox())!
  const card = (await page.locator('.project-card').boundingBox())!
  expect(usable.x).toBeGreaterThanOrEqual(0)
  expect(usable.x + usable.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  expect(Math.abs(library.width - Math.min(usable.width, 1420)), 'library fills usable width up to its desktop maximum').toBeLessThan(2)
  for (const [name, box] of [['library', library], ['single project card', card]] as const) {
    expect(Math.abs(box.x + box.width / 2 - (usable.x + usable.width / 2)), `${name} is centered inside the usable layout`).toBeLessThan(1)
  }
  expect(grid.x - library.x, 'library content has left padding').toBeGreaterThanOrEqual(18)
  expect(Math.abs(grid.x - library.x - (library.x + library.width - grid.x - grid.width)), 'library content has balanced padding').toBeLessThan(1)
  expect(Math.abs(card.width - Math.min(320, grid.width)), 'project card retains its intended width').toBeLessThan(1)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
}

async function expectManualLayout(page: Page) {
  const usable = (await page.locator('#root').boundingBox())!
  const grid = (await page.locator('.workspace-grid').boundingBox())!
  const panels = await Promise.all(['.media-panel', '.transcript-panel', '.candidates-panel'].map((selector) => page.locator(selector).boundingBox()))
  expect(Math.abs(grid.x - usable.x), 'workspace starts at the usable left edge').toBeLessThan(1)
  expect(Math.abs(grid.width - usable.width), 'workspace fills the usable layout width').toBeLessThan(1)
  for (const panel of panels) expect(panel).not.toBeNull()
  if (page.viewportSize()!.width >= 1280) {
    for (const panel of panels) {
      expect(Math.abs(panel!.y - grid.y), 'desktop panels share the same top edge').toBeLessThan(1)
      expect(Math.abs(panel!.height - grid.height), 'desktop panels fill the workspace height').toBeLessThan(1)
    }
    expect(Math.abs(panels[0]!.x - grid.x)).toBeLessThan(1)
    for (let index = 1; index < panels.length; index++) expect(Math.abs(panels[index]!.x - panels[index - 1]!.x - panels[index - 1]!.width), 'desktop panels are adjacent without overlap').toBeLessThan(1)
    expect(Math.abs(panels[2]!.x + panels[2]!.width - grid.x - grid.width)).toBeLessThan(1)
  } else {
    for (const panel of panels) {
      expect(Math.abs(panel!.x - grid.x), 'mobile panels share the usable left edge').toBeLessThan(1)
      expect(Math.abs(panel!.width - grid.width), 'mobile panels fill one stacked column').toBeLessThan(1)
    }
    for (let index = 1; index < panels.length; index++) expect(Math.abs(panels[index]!.y - panels[index - 1]!.y - panels[index - 1]!.height), 'mobile panels stack without overlap or gaps').toBeLessThan(1)
  }
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  for (const selector of ['.workspace', '.media-panel', '.transcript-panel .panel-body', '.candidate-list']) {
    await expect.poll(() => page.locator(selector).evaluate((element) => element.scrollWidth <= element.clientWidth + 1), { message: `${selector} has no horizontal clipping` }).toBe(true)
  }
}

test('initial system theme, exactly three explicit themes, persistence, responsive library and no external requests', async ({ page }) => {
  await mockApi(page)
  const external: string[] = []
  page.on('request', (request) => { if (!request.url().startsWith('http://127.0.0.1:5180')) external.push(request.url()) })
  await page.emulateMedia({ colorScheme: 'dark' }); await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  const themeSelect = page.getByLabel('Color theme')
  await expect(themeSelect.locator('option')).toHaveCount(3)
  expect(await themeSelect.locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))).toEqual(['dark', 'light', 'nord'])
  expect(await page.evaluate(() => localStorage.getItem('clipforge.theme'))).toBeNull()
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  for (const theme of ['dark', 'light', 'nord']) {
    await themeSelect.selectOption(theme)
    await page.emulateMedia({ colorScheme: theme === 'dark' ? 'light' : 'dark' }); await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expect(themeSelect).toHaveValue(theme)
    expect(await page.evaluate(() => localStorage.getItem('clipforge.theme'))).toBe(theme)
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByLabel('Search projects').fill('no match')
  await expect(page.getByText('No matching projects')).toBeVisible()
  expect(external).toEqual([])
})

test('settings sidebar renders one section, keeps drafts, persists provider CRUD and blocks close while busy', async ({ page }) => {
  const state = await mockApi(page, { delaySettingsMs: 150 }); await page.goto('/')
  const settingsButton = page.getByRole('button', { name: 'Settings', exact: true }); await settingsButton.click()
  const dialog = page.getByRole('dialog', { name: 'Settings' }); const nav = dialog.getByRole('navigation', { name: 'Settings sections' })
  await expect(dialog.getByRole('heading', { name: 'Appearance', exact: true })).toBeVisible()
  await expect(nav.getByRole('button', { name: /Appearance/ })).toHaveAttribute('aria-current', 'page')
  for (const label of ['Providers', 'Processing', 'Caption defaults', 'Environment', 'Appearance']) {
    await nav.getByRole('button', { name: label, exact: true }).click()
    await expect(dialog.getByRole('heading', { name: label, exact: true })).toBeVisible()
    expect(await nav.locator('button[aria-current="page"]').count()).toBe(1)
  }

  await nav.getByRole('button', { name: 'Providers', exact: true }).click()
  await expect(dialog.getByText('Saved API keys are hidden (write-only).', { exact: true })).toBeVisible()
  const pane = dialog.locator('.settings-pane'); await pane.evaluate((element) => { element.scrollTop = element.scrollHeight })
  const beforeTabScroll = await pane.evaluate((element) => element.scrollTop)
  await dialog.getByRole('button', { name: 'Transcription', exact: true }).click()
  await expect.poll(() => pane.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  expect(await pane.evaluate((element) => element.scrollTop)).toBeLessThanOrEqual(beforeTabScroll)

  await dialog.getByRole('button', { name: 'Analysis / LLM', exact: true }).click()
  await dialog.getByRole('button', { name: 'Add profile', exact: true }).click()
  await dialog.getByRole('button', { name: /DeepSeek/ }).click()
  await dialog.getByLabel('Profile name', { exact: true }).fill('Studio analysis')
  const profileKey = dialog.locator('.settings-profile-form input[type="password"]'); await profileKey.scrollIntoViewIfNeeded(); await profileKey.fill('profile-secret')
  await dialog.getByRole('button', { name: 'Save & test', exact: true }).click()
  await expect(dialog.getByText(/Studio analysis saved\. Connected/)).toBeVisible()
  expect(state.events.slice(0, 2)).toEqual(['profile:create:profile-1', 'profile:test:profile-1'])

  await dialog.getByLabel('Profile name', { exact: true }).fill('Studio analysis edited')
  await dialog.locator('label.field').filter({ hasText: /^Model/ }).locator('input').fill('deepseek-chat')
  await dialog.getByRole('button', { name: 'Save & activate', exact: true }).click()
  await expect(dialog.getByText(/saved and active for analysis/)).toBeVisible()
  expect(state.events.slice(2, 4)).toEqual(['profile:update:profile-1', 'profile:active:llm:profile-1'])
  expect(state.profiles[0]).toMatchObject({ name: 'Studio analysis edited', model: 'deepseek-chat', hasApiKey: true, active: true })

  await dialog.getByRole('button', { name: /Back to connections/ }).click()
  await expect(dialog.getByText('Saved profiles', { exact: true })).toBeVisible()
  await pane.evaluate((element) => { element.scrollTop = element.scrollHeight })
  expect(await pane.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  await page.mouse.click(4, 4); await expect(dialog).toBeVisible()
  await nav.getByRole('button', { name: 'Processing', exact: true }).click(); await dialog.locator('label.field').filter({ hasText: 'Analysis provider' }).locator('select').selectOption('ollama')
  await dialog.getByRole('button', { name: 'Save settings', exact: true }).click()
  await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeDisabled(); await page.keyboard.press('Escape'); await expect(dialog).toBeVisible()
  await expect(dialog.getByText('Settings saved', { exact: true })).toBeVisible({ timeout: 5000 }); await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(settingsButton).toBeFocused()

  await settingsButton.click(); const reopened = page.getByRole('dialog', { name: 'Settings' }); await reopened.getByRole('navigation').getByRole('button', { name: 'Providers', exact: true }).click()
  await expect(reopened.getByRole('button', { name: 'Edit Studio analysis edited' })).toBeVisible()
  await reopened.getByRole('button', { name: 'Edit Studio analysis edited' }).click(); await reopened.getByRole('button', { name: 'Delete Studio analysis edited' }).click()
  await expect.poll(() => state.events.at(-1)).toBe('profile:delete:profile-1')
  expect(state.profiles).toHaveLength(0)
})

test('onboarding saves typed cloud keys before testing and persists completion', async ({ page }) => {
  const state = await mockApi(page, { onboarded: false }); await page.goto('/')
  await page.getByRole('button', { name: 'Set up ClipForge' }).click(); await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await page.getByLabel('DeepSeek API key').fill('fixture-analysis'); await page.getByLabel('Deepgram API key').fill('fixture-audio')
  await page.getByRole('button', { name: 'Save & test analysis connection' }).click()
  await expect.poll(() => state.events).toEqual(['settings:save', 'connection:test:deepseek'])
  expect(state.saved[0]).toMatchObject({ DEEPSEEK_API_KEY: 'fixture-analysis', DEEPGRAM_API_KEY: 'fixture-audio', LLM_PROVIDER: 'deepseek', TRANSCRIPTION_MODE: 'cloud' })
  await page.getByRole('button', { name: 'Save & continue' }).click(); await page.getByRole('button', { name: 'Open projects', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Projects', exact: true })).toBeVisible(); expect(await page.evaluate(() => localStorage.getItem('clipforge.onboarded'))).toBe('1')
})

test('onboarding local mode saves canonical local and Ollama settings', async ({ page }) => {
  const state = await mockApi(page, { onboarded: false }); await page.goto('/')
  await page.getByRole('button', { name: 'Set up ClipForge' }).click(); await page.getByRole('radio', { name: /Local processing/ }).check(); await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await page.getByRole('button', { name: 'Save & continue' }).click(); await expect(page.getByRole('heading', { name: 'Your workspace is ready.' })).toBeVisible()
  expect(state.saved[0]).toEqual({ LLM_PROVIDER: 'ollama', TRANSCRIPTION_MODE: 'local' })
})

test('local import uploads a real multipart file with name and mode', async ({ page }) => {
  const state = await mockApi(page); await page.goto('/'); await page.getByRole('button', { name: 'New project', exact: true }).click()
  await page.getByLabel('Project name').fill('Uploaded interview'); await page.getByLabel('Choose a video or audio file').setInputFiles({ name: 'source.wav', mimeType: 'audio/wav', buffer: audioFixture() })
  await page.getByRole('button', { name: 'Server path', exact: true }).click(); await page.getByRole('button', { name: 'Local file', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Create project', exact: true })).toBeDisabled(); await expect(page.getByText('source.wav ·', { exact: false })).toHaveCount(0)
  await page.getByLabel('Choose a video or audio file').setInputFiles({ name: 'source.wav', mimeType: 'audio/wav', buffer: audioFixture() })
  await page.getByLabel('Transcription mode').selectOption('local'); await page.getByRole('button', { name: 'Create project', exact: true }).click(); await expect(page.getByRole('heading', { name: projectFixture.name })).toBeVisible()
  expect(state.uploads).toHaveLength(1); expect(state.uploads[0].type).toContain('multipart/form-data; boundary='); expect(state.uploads[0].body).toContain('name="file"; filename="source.wav"')
  expect(state.uploads[0].body).toContain('name="name"\r\n\r\nUploaded interview'); expect(state.uploads[0].body).toContain('name="transcriptionMode"\r\n\r\nlocal')
})

test('YouTube metadata, progress, automatic project creation and polling cleanup', async ({ page }) => {
  const state = await mockApi(page); await page.goto('/'); await page.getByRole('button', { name: 'New project', exact: true }).click(); await page.getByRole('button', { name: 'YouTube', exact: true }).click()
  await page.getByLabel('YouTube video URL').fill('https://www.youtube.com/watch?v=fixture'); await page.getByRole('button', { name: 'Check video' }).click(); await expect(page.getByRole('heading', { name: 'An imported interview' })).toBeVisible()
  await page.getByRole('button', { name: 'Download & create' }).click(); await expect(page.getByRole('progressbar')).toHaveAttribute('value', '45'); await expect.poll(() => state.created.length).toBe(1)
  await expect(page.getByRole('heading', { name: projectFixture.name })).toBeVisible({ timeout: 10000 }); expect(state.created[0]).toMatchObject({ name: 'An imported interview', sourcePath: '/downloads/video.mp4', transcriptionMode: 'local' })
  await page.getByRole('button', { name: 'Back to projects' }).click(); state.youtubeTerminal = false; await page.getByRole('button', { name: 'New project', exact: true }).click(); await page.getByRole('button', { name: 'YouTube', exact: true }).click()
  await page.getByLabel('YouTube video URL').fill('https://youtu.be/fixture'); await page.getByRole('button', { name: 'Check video' }).click(); await page.getByRole('button', { name: 'Download & create' }).click(); await expect(page.getByRole('progressbar')).toBeVisible()
  await page.keyboard.press('Escape'); const polls = state.events.filter((event) => event === 'youtube:poll').length; await page.waitForTimeout(1800); expect(state.events.filter((event) => event === 'youtube:poll').length).toBe(polls)
})

test('timestamps seek real media, download uses the completed ZIP anchor, and no render starts', async ({ page }) => {
  const state = await mockApi(page); await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  const video = page.getByLabel('Source media player'); await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThan(0)
  await page.getByRole('button', { name: 'Seek to 0:10', exact: true }).click(); await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBe(10)
  await page.getByRole('button', { name: 'Seek to clip 1 at 0:05' }).click(); await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBe(5)
  await expect(page.getByText('Render failed · view details')).toBeVisible(); await expect(page.getByRole('button', { name: 'Retry render' })).toBeEnabled()
  const preview = page.getByRole('button', { name: 'Preview', exact: true }); await preview.click(); const previewDialog = page.getByRole('dialog', { name: 'Preview clip 1' })
  await expect(previewDialog.locator('video')).toHaveAttribute('src', '/api/clips/latest/file'); await expect(previewDialog.locator('video')).toHaveAttribute('controls', ''); await expect(previewDialog.getByRole('link', { name: 'Download MP4' })).toHaveAttribute('href', '/api/clips/latest/file')
  await page.keyboard.press('Escape'); await expect(preview).toBeFocused()
  const zip = page.getByRole('link', { name: 'Download selected (1)' }); await expect(zip).toHaveAttribute('href', /\/api\/projects\/p1\/export\?clipIds=latest$/)
  const [download] = await Promise.all([page.waitForEvent('download'), zip.click()])
  expect(download.suggestedFilename()).toBeTruthy()
  expect(state.renderRequests).toEqual([]); expect(state.batchStyles).toEqual([])
})

test('mixed selection renders only missing clips through per-clip paths', async ({ page }) => {
  const state = await mockApi(page); await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  await expect(page.getByRole('button', { name: 'Render selected (1)', exact: true })).toBeVisible(); await page.getByRole('button', { name: 'Render selected (1)', exact: true }).click()
  await expect.poll(() => state.renderRequests.length).toBe(1); expect(state.renderRequests[0].candidateId).toBe('c2'); expect(state.batchStyles).toEqual([])
  expect(state.renderRequests[0].body).toMatchObject({ captionStyle: 'classic' }); expect(state.renderSettingsSaves).toHaveLength(1)
})

test('batch tracks returned clip ids and reads aggregate progress from project GET', async ({ page }) => {
  const state = await mockApi(page, { initialClips: [] }); await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  await expect(page.getByRole('button', { name: 'Render selected (2)', exact: true })).toBeVisible(); await page.getByRole('button', { name: 'Render selected (2)', exact: true }).click()
  await expect(page.getByText(/Live updates interrupted/)).toBeVisible(); await expect(page.getByRole('button', { name: 'Rendering…', exact: true })).toHaveCount(2, { timeout: 12000 })
  expect(state.clips).toEqual(expect.arrayContaining([{ id: 'batch-c1', candidate_id: 'c1', status: 'rendering', output_path: null, render_log: null, created_at: '2026-10-04T13:00:00' }, expect.objectContaining({ id: 'batch-c2' })]))
  await expect(page.getByRole('button', { name: 'Preview', exact: true })).toBeVisible({ timeout: 12000 }); await expect(page.getByRole('button', { name: 'Retry render' })).toBeEnabled()
  expect(state.batchStyles).toEqual(['classic']); expect(state.batchIds).toEqual(['batch-c1', 'batch-c2']); expect(state.clipStatusRequests).toEqual([]); expect(state.legacyJobStatusRequests).toEqual([]); expect(state.batchProjectReads).toBeGreaterThanOrEqual(4)
  await page.getByRole('button', { name: 'Back to projects' }).click(); const reads = state.detailReads; await page.waitForTimeout(1800); expect(state.detailReads).toBe(reads)
})

test('caption editor uses the render contract, exact JPEG preview, caption/output persistence, and saved reopening', async ({ page }) => {
  const state = await mockApi(page); await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click(); await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Caption Controls & video filters' }); await expect(editor.getByRole('button', { name: 'Neon caption preset' })).toHaveAttribute('aria-pressed', 'false')
  const placement = editor.locator('label.field').filter({ hasText: 'Placement' }).locator('select'); const fontFamily = editor.locator('label.field').filter({ hasText: 'Font family' }).locator('select')
  const verticalPosition = editor.locator('label.field').filter({ hasText: 'Vertical position' }).locator('input[type="range"]'); const fontSize = editor.locator('label.field').filter({ hasText: 'Font size' }).locator('input[type="range"]')
  const outputSettings: OutputSettings = { mode: 'manual', aspectRatio: '4:5', fit: 'contain', maxDimension: 1920 }
  await editor.getByRole('combobox', { name: 'Aspect ratio', exact: true }).selectOption(outputSettings.aspectRatio)
  await editor.getByRole('combobox', { name: 'Framing', exact: true }).selectOption(outputSettings.fit)
  await editor.getByRole('button', { name: 'Neon caption preset' }).click(); await placement.selectOption('center'); await fontFamily.selectOption('mono'); await verticalPosition.fill('44')
  await fontSize.fill('58'); await editor.getByLabel('Caption background', { exact: true }).check(); await editor.locator('label.field').filter({ hasText: 'Background opacity' }).locator('input[type="range"]').fill('0.8')
  const source = editor.getByLabel('Caption source preview'); await expect(source).toHaveAttribute('src', '/api/projects/p1/file')
  await editor.getByRole('button', { name: 'Render exact preview frame', exact: true }).click(); await expect(editor.getByAltText('FFmpeg rendered frame with current caption and filter settings')).toBeVisible(); expect(state.previewFrames[0]).toMatchObject({ time: 5, captionSettings: expect.objectContaining({ preset: 'neon', placement: 'center', fontFamily: 'mono', backgroundEnabled: true, backgroundOpacity: .8 }), outputSettings })
  await page.mouse.click(4, 4); await expect(editor).toBeVisible(); await editor.getByRole('button', { name: 'Save project settings', exact: true }).click(); await expect(editor.getByText('Saved to this project. Existing downloads keep their rendered settings.', { exact: true })).toBeVisible()
  expect(state.renderSettingsSaves[0].captionSettings).toMatchObject({ preset: 'neon', placement: 'center', fontFamily: 'mono', verticalPosition: 44, fontSize: 58, backgroundEnabled: true, backgroundOpacity: .8 })
  expect(state.renderSettingsSaves[0].outputSettings).toEqual(outputSettings); expect(state.project.outputSettings).toEqual(outputSettings)
  await expect(editor.getByRole('combobox', { name: 'Aspect ratio', exact: true })).toHaveValue('4:5')
  await expect(editor.getByRole('combobox', { name: 'Framing', exact: true })).toHaveValue('contain')
  await editor.getByRole('button', { name: 'Done editing controls', exact: true }).click(); await page.getByRole('button', { name: 'Back to projects' }).click(); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click(); await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click()
  const reopened = page.getByRole('dialog', { name: 'Caption Controls & video filters' }); const reopenedPlacement = reopened.locator('label.field').filter({ hasText: 'Placement' }).locator('select'); const reopenedFontFamily = reopened.locator('label.field').filter({ hasText: 'Font family' }).locator('select')
  await expect(reopened.getByRole('button', { name: 'Neon caption preset' })).toHaveAttribute('aria-pressed', 'true'); await expect(reopenedPlacement).toHaveValue('center'); await expect(reopenedFontFamily).toHaveValue('mono'); await expect(reopened.getByLabel('Caption background', { exact: true })).toBeChecked()
  await expect(reopened.getByRole('combobox', { name: 'Aspect ratio', exact: true })).toHaveValue('4:5')
  await expect(reopened.getByRole('combobox', { name: 'Framing', exact: true })).toHaveValue('contain')
  await reopened.getByRole('button', { name: 'Done editing controls', exact: true }).click()
  await page.getByRole('button', { name: 'Render selected (1)', exact: true }).click()
  await expect.poll(() => state.renderRequests.length).toBe(1)
  expect(state.renderRequests[0].body).toMatchObject({ outputSettings, captionSettings: state.renderSettingsSaves[0].captionSettings })
  expect(fallbackRenderContract.caption.presets.map((preset) => preset.id)).toContain('neon')
})

test('Video & AI suggestions generates a POST and applies the returned look', async ({ page }) => {
  const state = await mockApi(page); await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click(); await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Caption Controls & video filters' }); await editor.getByRole('button', { name: 'Video & AI suggestions', exact: true }).click(); await editor.getByLabel('Editing brief', { exact: true }).fill('Readable captions with a restrained documentary look'); await editor.getByRole('button', { name: 'Suggest editing settings', exact: true }).click()
  await expect.poll(() => state.suggestionRequests).toHaveLength(1); expect(state.suggestionRequests[0]).toEqual({ brief: 'Readable captions with a restrained documentary look', audienceBrief: { audience: '', goal: '', notes: '' } }); await expect(editor.getByText('A restrained look for Readable captions with a restrained documentary look.')).toBeVisible()
  await editor.getByRole('button', { name: 'Apply suggestion to controls', exact: true }).click(); await expect(editor.getByLabel('brightness', { exact: true })).toHaveValue('0.3'); await expect(editor.getByLabel('contrast', { exact: true })).toHaveValue('1.2')
})

test('centered library and desktop/mobile workspace geometry with light, dark, and Nord screenshots', async ({ page }, testInfo) => {
  await mockApi(page); await page.goto('/'); await page.getByLabel('Color theme').selectOption('light')
  await expect(page.getByRole('region', { name: 'Workflow guide' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'New project', exact: true }).locator('.workflow-step')).toHaveText('1')
  await expect(page.locator('.project-card')).toBeVisible(); await expectLibraryLayout(page)
  const projectGrid = await page.locator('.project-grid').boundingBox(); expect(projectGrid!.y).toBeLessThan(350)
  await page.screenshot({ path: testInfo.outputPath('library-light-1440.png'), fullPage: true })
  await page.getByLabel('Color theme').selectOption('nord'); await expect(page.locator('html')).toHaveAttribute('data-theme', 'nord')
  for (const width of [1280, 1366, 1920, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
    await expectLibraryLayout(page)
    await page.screenshot({ path: testInfo.outputPath(`library-nord-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 960 }); await page.getByLabel('Color theme').selectOption('light')
  await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  const grid = page.locator('.workspace-grid'); await expect(grid).toBeVisible(); expect((await grid.boundingBox())!.y).toBeLessThan(180)
  await expect(page.getByRole('region', { name: 'Workflow guide' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Transcribe again', exact: true }).locator('.workflow-step')).toHaveText('2')
  await expect(page.getByRole('button', { name: 'Analyze again', exact: true }).locator('.workflow-step')).toHaveText('3')
  await expect(page.locator('.candidate-footer label .workflow-step').first()).toHaveText('4')
  await expect(page.getByRole('button', { name: 'Render selected (1)', exact: true }).locator('.workflow-step')).toHaveText('5')
  await expect(page.getByRole('link', { name: 'Download selected (1)', exact: true }).locator('.workflow-step')).toHaveText('6')
  await expectManualLayout(page); await page.screenshot({ path: testInfo.outputPath('workspace-light-1440.png'), fullPage: true })
  await page.getByLabel('Color theme').selectOption('dark'); await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark'); await page.screenshot({ path: testInfo.outputPath('workspace-dark-1440.png'), fullPage: true })
  await page.getByLabel('Color theme').selectOption('nord'); await expect(page.locator('html')).toHaveAttribute('data-theme', 'nord')
  for (const width of [1280, 1366, 1920, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
    await expectManualLayout(page); await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`workspace-nord-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click(); const editor = page.getByRole('dialog', { name: 'Caption Controls & video filters' }); await expect(editor).toBeVisible(); await page.screenshot({ path: testInfo.outputPath('captions-nord-1440.png'), fullPage: true }); await editor.getByRole('button', { name: 'Done editing controls', exact: true }).click()
  await page.getByRole('button', { name: 'Open settings' }).click(); const settings = page.getByRole('dialog', { name: 'Settings' }); await settings.getByRole('navigation').getByRole('button', { name: 'Providers', exact: true }).click(); await page.screenshot({ path: testInfo.outputPath('settings-providers-nord-1440.png'), fullPage: true }); await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.setViewportSize({ width: 390, height: 844 }); await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: testInfo.outputPath('settings-providers-nord-390.png'), fullPage: true }); await settings.getByRole('button', { name: 'Close', exact: true }).click(); await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  for (const width of [390, 640, 800, 1100, 1440, 2000]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.locator('.workspace').evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    for (const selector of ['.media-panel', '.transcript-panel .panel-body', '.candidate-list']) await expect.poll(() => page.locator(selector).evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  }
  await page.setViewportSize({ width: 390, height: 844 }); await page.getByLabel('Color theme').selectOption('light'); await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: testInfo.outputPath('workspace-light-390.png'), fullPage: true })
  // Overflow can pass while flex items squash labels into one-character columns.
  // Keep the primary mobile actions readable within two touch-target rows.
  const actions = page.locator('.workspace-header .header-actions')
  for (const [name, action] of [
    ['Render selected', actions.getByRole('button', { name: 'Render selected (1)', exact: true })],
    ['Download selected', actions.getByRole('link', { name: 'Download selected (1)', exact: true })],
  ] as const) {
    const box = (await action.boundingBox())!
    expect.soft(box.width, `${name} at 390px needs readable label space`).toBeGreaterThanOrEqual(120)
    expect.soft(box.height, `${name} at 390px must not become a tall character column`).toBeLessThanOrEqual(88)
  }
})

test('saved environment keys are explained without profiles and discarded defaults stay discarded', async ({ page }, testInfo) => {
  const state = await mockApi(page)
  state.settings.llmProvider = 'custom'; state.settings.customBaseUrl = 'https://provider.example/v1'; state.settings.customModel = 'studio-model'; state.settings.transcriptionMode = 'cloud'
  state.environmentKeys = { CUSTOM_API_KEY: 'fixture-secret', DEEPGRAM_API_KEY: 'fixture-deepgram' }
  await page.goto('/'); await page.getByRole('button', { name: 'Settings', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Settings' }); const nav = dialog.getByRole('navigation', { name: 'Settings sections' })
  await nav.getByRole('button', { name: 'Providers', exact: true }).click()
  const current = dialog.getByRole('region', { name: 'Current provider defaults' })
  await expect(dialog.getByText('Saved API keys are hidden (write-only).', { exact: true })).toBeInViewport()
  await expect(current.getByText('Custom OpenAI-compatible', { exact: true })).toBeInViewport(); await expect(current.getByText('Environment key + base URL configured', { exact: true })).toBeInViewport()
  await expect(current.getByText('Deepgram', { exact: true })).toBeInViewport(); await expect(current.getByText('Environment key configured', { exact: true })).toBeVisible()
  await nav.getByRole('button', { name: 'Processing', exact: true }).click(); const provider = dialog.getByRole('combobox', { name: 'Analysis provider', exact: true }); await provider.selectOption('ollama')
  await expect(current.getByText('Custom OpenAI-compatible', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Discard changes', exact: true }).click(); await expect(provider).toHaveValue('custom')
  await dialog.getByRole('button', { name: 'Save settings', exact: true }).click(); await expect(dialog.getByText('Settings saved', { exact: true })).toBeVisible()
  expect(state.saved[0].LLM_PROVIDER).toBe('custom'); expect(state.saved[0]).not.toHaveProperty('CUSTOM_API_KEY'); expect(state.environmentKeys.CUSTOM_API_KEY).toBe('fixture-secret')
  await nav.getByRole('button', { name: 'Providers', exact: true }).click(); await page.screenshot({ path: testInfo.outputPath('provider-status.png'), fullPage: true })
})

test('numbered workflow transcribes, analyzes, and blocks render until selection finishes saving', async ({ page }) => {
  const state = await mockApi(page, { initialClips: [] }); state.transcript = null; state.candidates = []; state.delaySelectionMs = 700
  await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  await expect(page.getByRole('button', { name: /Render selected/ })).toBeDisabled()
  await page.getByRole('button', { name: 'Transcribe audio', exact: true }).click(); await expect(page.getByRole('button', { name: 'Find clip candidates', exact: true })).toBeEnabled()
  await expect(page.getByRole('radio', { name: 'Speech', exact: true })).toBeChecked()
  await expect(page.getByRole('radio', { name: 'Video visuals', exact: true })).not.toBeChecked()
  await page.getByRole('button', { name: 'Find clip candidates', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: /Select clip/ })).toHaveCount(2)
  const candidate = page.locator('.candidate-card').first(); await candidate.getByRole('checkbox').click()
  await expect(candidate.getByRole('button', { name: 'Render clip', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: /Render selected/ })).toBeDisabled()
  await expect(candidate.getByRole('button', { name: 'Render clip', exact: true })).toBeEnabled()
  await expect(candidate.getByRole('checkbox')).toBeChecked()
  expect(state.events).toEqual(['transcribe:start', 'analyze:start', 'selection:save'])
  expect(state.analysisRequests).toEqual([{ mode: 'transcript', brief: '' }])
  await candidate.getByRole('button', { name: 'Render clip', exact: true }).click(); await expect.poll(() => state.renderRequests.length).toBe(1)
  expect(state.renderRequests[0].candidateId).toBe('c1')
})

test('reanalyzing confirms replacement before removing current candidates and outputs', async ({ page }) => {
  const state = await mockApi(page); await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  await page.getByRole('button', { name: 'Analyze again', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Replace clip candidates?' }); await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Keep current candidates' }).click(); expect(state.events).not.toContain('analyze:start'); expect(state.clips).toHaveLength(3)
  await page.getByRole('button', { name: 'Analyze again', exact: true }).click(); await dialog.getByRole('button', { name: 'Replace and analyze' }).click()
  await expect.poll(() => state.events.filter((event) => event === 'analyze:start')).toHaveLength(1); await expect(page.getByRole('button', { name: 'Render selected (0)' })).toBeDisabled()
})

test('silent projects can create and render a manual cut without transcription', async ({ page }) => {
  const state = await mockApi(page, { initialClips: [] }); state.transcript = null; state.candidates = []
  await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  await expect(page.getByRole('button', { name: 'Find clip candidates', exact: true })).toBeEnabled()
  await expect(page.getByRole('heading', { name: 'Speech is optional.', exact: true })).toBeVisible()
  await expect(page.getByRole('radio', { name: 'Video visuals', exact: true })).toBeChecked()
  await expect(page.getByRole('radio', { name: 'Speech', exact: true })).toBeDisabled()
  await expect(page.getByLabel('Start (seconds)')).toHaveCount(0)
  const manualCut = page.getByRole('button', { name: 'Create manual cut', exact: true })
  await expect(manualCut).toHaveAttribute('aria-expanded', 'false'); await manualCut.click()
  await expect(page.getByRole('button', { name: 'Hide manual cut', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await page.getByLabel('Start (seconds)').fill('3.5'); await page.getByLabel('End (seconds)').fill('12.25'); await page.locator('label.field').filter({ hasText: /^Title/ }).locator('input').fill('Visual moment')
  await page.getByRole('button', { name: 'Add manual cut', exact: true }).click()
  await expect.poll(() => state.manualRequests).toHaveLength(1)
  expect(state.manualRequests[0]).toEqual({ startSec: 3.5, endSec: 12.25, title: 'Visual moment' })
  await expect(page.getByRole('heading', { name: 'Visual moment', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Render clip', exact: true }).click()
  await expect.poll(() => state.renderRequests.length).toBe(1)
  expect(state.renderRequests[0].candidateId).toBe('manual-1')
  expect(state.events).toEqual(['manual:create', 'project:render-settings:save'])
  expect(state.analysisRequests).toEqual([]); expect(state.transcript).toBeNull()
})

test('Manual mode finds visual clip candidates without requesting transcription', async ({ page }) => {
  const state = await mockApi(page, { initialClips: [] }); state.transcript = null; state.candidates = []
  await page.goto('/'); await page.getByRole('button', { name: `Open project ${projectFixture.name}` }).click()
  await expect(page.getByRole('button', { name: 'Manual', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('radio', { name: 'Speech', exact: true })).toBeDisabled()
  await expect(page.getByRole('radio', { name: 'Video visuals', exact: true })).toBeChecked()
  await page.getByRole('button', { name: 'Find clip candidates', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: /Select clip/ })).toHaveCount(2)
  await expect(page.getByRole('heading', { name: 'A strong moment 1', exact: true })).toBeVisible()
  expect(state.analysisRequests).toEqual([{ mode: 'visual', brief: '' }])
  expect(state.events).toEqual(['analyze:start']); expect(state.transcript).toBeNull()
  expect(state.renderRequests).toEqual([])
})
