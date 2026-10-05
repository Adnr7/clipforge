import { expect, test, type Page } from '@playwright/test'
import type { Candidate, Clip, JobStatus, OutputSettings, Project, ProjectDetail, ProjectRenderSettings } from '../src/api/client'
import type { VisualRecommendation } from '../src/api/aiEdit'
import { fallbackRenderContract } from '../src/api/renderDefaults'

const projects: Project[] = ['p1', 'p2'].map((id, index) => ({
  id, name: `Guided project ${index + 1}`, source_path: '/media/source.mp4', source_duration: 120,
  status: 'imported', transcription_mode: 'local', caption_style: 'classic', created_at: '2026-10-01', updated_at: '2026-10-05',
}))
const candidates: Candidate[] = [
  { id: 'c1', project_id: 'p1', start_sec: 0, end_sec: 10, score: 98, hook: 'Short idea', rationale: 'High score, short duration.', rank: 1, selected: 0 },
  { id: 'c2', project_id: 'p1', start_sec: 20, end_sec: 45, score: 90, hook: 'Complete explanation', rationale: 'A complete idea.', rank: 2, selected: 0 },
  { id: 'c3', project_id: 'p1', start_sec: 50, end_sec: 95, score: 85, hook: 'Longer story', rationale: 'Natural ending.', rank: 3, selected: 0 },
]
function transcript(id = 't1', text = 'A useful idea for your viewers.') {
  return { id, engine: 'whisper', language: 'en', raw_json: JSON.stringify({ language: 'en', duration: 120, speakers: [], words: [{ start: 1, end: 2, text }], segments: [{ start: 1, end: 2, text }] }) }
}
function visualRecommendation(latest = false): VisualRecommendation {
  return {
    aspectRatio: latest ? { mode: 'pad', ratio: '1:1' } : { mode: 'crop', ratio: '9:16' },
    videoFilters: { brightness: latest ? .07 : .02, contrast: 1.1 },
    captionSettings: { enabled: true, preset: 'minimal', placement: 'top' },
    candidates: candidates.map(({ start_sec, end_sec, score, hook, rationale }) => ({ start: start_sec, end: end_sec, score, hook, rationale })),
    rationale: latest ? 'Keep the full performance in a square padded frame.' : 'Use the visible action as a clear opening.',
    analysisBasis: 'sampled-frames', source: { width: 1920, height: 1080, durationSeconds: 120, hasVideo: true, hasAudio: true },
    sampledFrameTimes: [0, 20, 40, 60, 80, 100], contextNotice: 'Sampled video frames inspected; music does not require a speech transcript.',
    model: latest ? 'fixture-visual-analysis' : 'fixture-visual-preview',
  }
}
function audio() {
  const buffer = Buffer.alloc(44 + 8000 * 2)
  buffer.write('RIFF', 0); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write('WAVEfmt ', 8)
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(8000, 24); buffer.writeUInt32LE(16000, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36); buffer.writeUInt32LE(buffer.length - 44, 40)
  return buffer
}

async function setup(page: Page, options: { hasTranscript?: boolean; hasCandidates?: boolean; legacy?: boolean; hasVisual?: boolean; outputSettings?: OutputSettings; hasAudio?: boolean; hasVideo?: boolean } = {}) {
  await page.addInitScript(() => localStorage.setItem('clipforge.onboarded', '1'))
  const state = {
    transcript: options.hasTranscript ? transcript() : null as ProjectDetail['transcript'],
    candidates: options.hasCandidates ? structuredClone(candidates) : [] as Candidate[], clips: [] as Clip[],
    jobs: { transcription: { status: 'idle' }, analysis: { status: 'idle' } } as { transcription: JobStatus; analysis: JobStatus },
    events: [] as string[], requests: [] as { path: string; body: Record<string, unknown> }[],
    holdTranscription: false, holdAnalysis: false, failTranscription: false, silentTranscript: false, failAnalysis: false,
    failSelection: false, failRender: false, recommendationStatus: 200, recommendationHtml: false, chatStatus: 200,
    chatError: 'Chat provider unavailable', chatHtml: false, reuseDoneRenders: false, stageReads: 0, renderReads: 0,
    analysisMode: 'visual' as 'transcript' | 'visual', visual: options.hasVisual ? visualRecommendation(true) : null as VisualRecommendation | null,
    renderSettings: Object.fromEntries(projects.map((project) => [project.id, {
      captionSettings: structuredClone(fallbackRenderContract.caption.presets[0].settings),
      videoFilters: structuredClone(fallbackRenderContract.videoFilters.defaults),
      outputSettings: structuredClone(options.outputSettings || fallbackRenderContract.output.defaults),
    }])) as Record<string, ProjectRenderSettings>,
  }
  const advance = () => {
    if (state.jobs.transcription.status === 'transcribing' && !state.holdTranscription && ++state.stageReads >= 2) {
      state.jobs.transcription = state.failTranscription ? { status: 'error', error: 'Transcription provider unavailable' } : { status: 'done' }
      if (!state.failTranscription) state.transcript = transcript('new-transcript', state.silentTranscript ? '' : 'New transcript speech.')
    }
    if (state.jobs.analysis.status === 'analyzing' && !state.holdAnalysis && ++state.stageReads >= 2) {
      state.jobs.analysis = state.failAnalysis ? { status: 'error', error: 'Analysis provider unavailable' } : { status: 'done' }
      if (!state.failAnalysis) {
        state.candidates = structuredClone(candidates); state.clips = []
        if (state.analysisMode === 'visual') state.visual = visualRecommendation(true)
      }
    }
    if (state.clips.some((clip) => clip.status === 'queued') && ++state.renderReads >= 2) {
      state.clips.forEach((clip) => { const failed = state.failRender && clip.candidate_id === 'c2'; clip.status = failed ? 'error' : 'done'; clip.output_path = failed ? null : `/renders/${clip.id}.mp4`; clip.render_log = failed ? 'FFmpeg failed' : 'Render complete' })
    }
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname.replace('/api', '')
    const method = request.method(); const json = (body: unknown, status = 200) => route.fulfill({ json: body, status })
    const body = () => (request.postDataJSON() || {}) as Record<string, unknown>
    if (method !== 'GET') state.requests.push({ path, body: body() })
    if (path === '/status') return json({ dataDir: '/data', hasFfmpeg: true, hasFfprobe: true, llmProvider: 'fixture' })
    if (path === '/projects') return json(projects)
    if (path === '/render-settings') {
      return json(fallbackRenderContract)
    }
    if (/^\/projects\/p[12]$/.test(path)) {
      advance()
      return json({ project: { ...projects.find((project) => path.endsWith(project.id)),
        ...state.renderSettings[path.split('/').at(-1)!],
        source_metadata_json: JSON.stringify({ width: options.hasVideo === false ? 0 : 1920, height: options.hasVideo === false ? 0 : 1080, has_video: options.hasVideo ?? true, has_audio: options.hasAudio ?? true }),
        visualAnalysis: state.visual }, transcript: state.transcript, candidates: state.candidates, clips: state.clips, ...(options.legacy ? {} : { jobs: state.jobs }) })
    }
    if (path.endsWith('/transcribe/status')) return json(state.jobs.transcription)
    if (path.endsWith('/analyze/status')) return json(state.jobs.analysis)
    if (path.endsWith('/probe')) return json({ duration_sec: 120, width: 1920, height: 1080, has_video: true, has_audio: options.hasAudio ?? true, video_codec: 'h264', audio_codec: 'aac' })
    if (path.endsWith('/render-settings') && path.startsWith('/projects/')) {
      const projectId = path.split('/')[2]
      if (method === 'PUT') state.renderSettings[projectId] = { ...state.renderSettings[projectId], ...body() }
      return json(state.renderSettings[projectId])
    }
    if (path.endsWith('/ai-edit/chat')) {
      if (state.chatHtml) return route.fulfill({ status: state.chatStatus, contentType: 'text/html', body: '<html><body>Gateway secret debug dump</body></html>' })
      return json(state.chatStatus === 200 ? { message: `For this project, emphasize ${body().message}.`, model: 'fixture-project-chat', basis: state.transcript ? 'transcript' : 'metadata' } : { error: state.chatError }, state.chatStatus)
    }
    if (path.endsWith('/ai-edit/recommendations')) {
      if (state.recommendationHtml) return route.fulfill({ status: state.recommendationStatus, contentType: 'text/html', body: '<html><body>Gateway debug dump</body></html>' })
      return json(state.recommendationStatus === 200 ? visualRecommendation() : { error: 'Visual provider unavailable' }, state.recommendationStatus)
    }
    if (path.endsWith('/transcribe') && method === 'POST') {
      state.events.push('transcribe'); state.jobs.transcription = { status: 'transcribing' }; state.stageReads = 0
      return json({ status: 'started' }, 202)
    }
    if (path.endsWith('/analyze') && method === 'POST') {
      state.events.push('analyze'); state.jobs.analysis = { status: 'analyzing' }; state.stageReads = 0
      state.analysisMode = body().mode as 'transcript' | 'visual'
      return json({ status: 'started' }, 202)
    }
    if (path.endsWith('/candidates') && method === 'PATCH') {
      state.events.push('select')
      if (state.failSelection) return json({ error: 'Selection could not be saved' }, 500)
      const selectedIds = body().selectedIds as string[]
      state.candidates.forEach((candidate) => { candidate.selected = selectedIds.includes(candidate.id) ? 1 : 0 })
      return json({ ok: true })
    }
    if (path.includes('/render/') && method === 'POST') {
      const candidateId = path.split('/').at(-1)!; const id = `render-${candidateId}`
      state.events.push(`render:${candidateId}`)
      state.clips.push({ id, candidate_id: candidateId, status: state.reuseDoneRenders ? 'done' : 'queued', output_path: state.reuseDoneRenders ? `/renders/${id}.mp4` : null, render_log: null })
      state.renderReads = 0
      return json({ clipId: id, status: state.reuseDoneRenders ? 'done' : 'started' }, state.reuseDoneRenders ? 200 : 202)
    }
    if (path.endsWith('/file') && path.startsWith('/projects/')) return route.fulfill({ status: 200, contentType: 'audio/wav', body: audio() })
    if (path.endsWith('/file') && path.startsWith('/clips/')) return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('fixture-mp4') })
    if (path.includes('/export')) return route.fulfill({ status: 200, contentType: 'application/zip', body: Buffer.from('PK'), headers: { 'content-disposition': 'attachment; filename="guided-edits.zip"' } })
    return json({ error: `No endpoint ${method} ${path}` }, 404)
  })
  return state
}

async function openAi(page: Page, project = projects[0]) {
  await page.goto('/'); await page.getByRole('button', { name: `Open project ${project.name}` }).click()
  await expect(page.getByRole('button', { name: 'Manual', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
}
async function fillBrief(page: Page) {
  await page.getByLabel('Who is the audience?', { exact: true }).fill('First-time founders')
  await page.getByLabel('What should viewers take away?', { exact: true }).fill('One useful, practical idea')
}
async function confirmPlan(page: Page, replace = false, useAiSettings = false) {
  await page.getByRole('button', { name: 'Review plan', exact: true }).click()
  const review = page.getByRole('dialog', { name: 'Review automated edit plan' })
  await expect(review.getByRole('button', { name: 'Start automated edit' })).toBeDisabled()
  if (useAiSettings) await review.getByLabel('Use AI recommended settings', { exact: true }).check()
  await review.getByLabel('I approve this plan and the project selection update').check()
  if (replace) {
    await expect(review.getByRole('button', { name: 'Start automated edit' })).toBeDisabled()
    await review.getByLabel('I confirm replacing existing candidates and outputs').check()
  }
  await review.getByRole('button', { name: 'Start automated edit' }).click()
}

test('real project chat sends answers and history, preserves Manual state, and review never starts jobs', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true })
  await openAi(page)
  await expect(page.getByRole('region', { name: 'AI Edit mode' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Transcript', exact: true })).toBeHidden()
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Create manual cut', exact: true })).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByLabel('Start (seconds)')).toHaveCount(0)
  await page.getByRole('button', { name: 'Create manual cut', exact: true }).click()
  await page.getByLabel('Start (seconds)').fill('7'); await page.getByLabel('End (seconds)').fill('25')
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await page.getByLabel('Message to project model').fill('New founders'); await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(page.getByLabel('Who is the audience?', { exact: true })).toHaveValue('New founders')
  await expect(page.getByRole('log')).toContainText('Model reply · fixture-project-chat')
  await expect(page.getByRole('log')).toContainText('For this project, emphasize New founders.')
  await expect(page.getByRole('log')).toContainText('Project context: transcript')
  await page.getByLabel('Message to project model').fill('A practical takeaway'); await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(page.getByLabel('What should viewers take away?', { exact: true })).toHaveValue('A practical takeaway')
  await expect(page.getByRole('log')).toContainText('For this project, emphasize A practical takeaway.')
  await page.getByLabel('Message to project model').fill('Keep the music and avoid exaggerated cuts'); await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(page.getByRole('log')).toContainText('For this project, emphasize Keep the music')
  const chatRequests = state.requests.filter((request) => request.path.endsWith('/ai-edit/chat'))
  expect(chatRequests).toHaveLength(3)
  expect(chatRequests[2].body.brief).toContain('Audience: New founders')
  expect(chatRequests[2].body.brief).toContain('Goal: A practical takeaway')
  expect(chatRequests[2].body.brief).toContain('Keep the music and avoid exaggerated cuts')
  expect(chatRequests[2].body.messages).toEqual(expect.arrayContaining([{ role: 'user', text: 'New founders' }, { role: 'model', text: 'For this project, emphasize New founders.' }]))
  await page.getByRole('button', { name: 'Review plan', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Reuse existing candidates without replacing them.')
  await page.getByRole('button', { name: 'Back to brief' }).click()
  expect(state.events).toEqual([])
  expect(state.requests.every((request) => request.path.endsWith('/ai-edit/chat'))).toBe(true)
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Transcript', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Analyze again', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Hide manual cut', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByLabel('Start (seconds)')).toHaveValue('7')
  await expect(page.getByLabel('End (seconds)')).toHaveValue('25')
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByLabel('Who is the audience?', { exact: true })).toHaveValue('New founders')
  await page.getByRole('button', { name: 'Back to projects' }).click()
  await page.getByRole('button', { name: `Open project ${projects[1].name}` }).click(); await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByLabel('Who is the audience?', { exact: true })).toHaveValue('')
  await page.getByRole('button', { name: 'Back to projects' }).click()
  await page.getByRole('button', { name: `Open project ${projects[0].name}` }).click(); await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByLabel('Who is the audience?', { exact: true })).toHaveValue('New founders')
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  for (const width of [390, 640, 800, 1100, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(() => page.locator('.workspace').evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    for (const selector of ['.media-panel', '.transcript-panel .panel-body', '.candidate-list']) {
      await expect.poll(() => page.locator(selector).evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    }
  }
})

test('confirmed automation transcribes for captions, visually analyzes, selects by length, and preserves source output', async ({ page }) => {
  const state = await setup(page); state.holdTranscription = true
  await openAi(page); await fillBrief(page)
  await page.getByLabel('Number of clips').selectOption('1'); await page.getByLabel('Preferred clip length').selectOption('15-30')
  await confirmPlan(page)
  await expect.poll(() => state.events).toEqual(['transcribe'])
  await expect(page.getByRole('region', { name: 'Activity timeline' })).toContainText('Transcribing source audio')
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Transcribing…', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  state.holdTranscription = false
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c2'])
  expect(state.requests.find((request) => request.path.endsWith('/candidates'))?.body).toEqual({ selectedIds: ['c2'] })
  expect(state.requests.find((request) => request.path.endsWith('/analyze'))?.body).toMatchObject({ mode: 'visual', brief: expect.stringContaining('Goal: One useful, practical idea') })
  expect(state.requests.find((request) => request.path.endsWith('/render/c2'))?.body).toMatchObject({ captionStyle: 'classic', captionSettings: { enabled: true }, outputSettings: { mode: 'source', aspectRatio: 'source', fit: 'contain', maxDimension: 1920 } })
  expect(state.requests.some((request) => request.path.endsWith('/render-settings'))).toBe(false)
  await expect(page.getByRole('link', { name: 'Download completed edits (1)' })).toHaveAttribute('href', '/api/projects/p1/export?clipIds=render-c2')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Preview clip 2' }).locator('video')).toHaveAttribute('src', '/api/clips/render-c2/file')
})

test('sampled-frame recommendations work without speech, show actual model context, and merge partial caption choices', async ({ page }) => {
  const state = await setup(page, { hasCandidates: true }); state.recommendationStatus = 502; state.recommendationHtml = true
  await openAi(page); await fillBrief(page)
  await page.getByLabel('Burn in captions', { exact: true }).uncheck()
  await page.getByRole('button', { name: 'Inspect source metadata' }).click()
  await expect(page.getByRole('region', { name: 'Source ratio' })).toContainText('16:9')
  await expect(page.getByRole('region', { name: 'Source ratio' })).toContainText('1920 × 1080')
  await page.getByRole('button', { name: 'Get visual recommendation' }).click()
  await expect(page.getByRole('alert')).toContainText('Recommendation unavailable')
  await expect(page.getByRole('alert')).toContainText('HTTP 502')
  await expect(page.getByRole('alert')).not.toContainText('debug dump')
  expect(state.events).toEqual([])
  state.recommendationStatus = 200; state.recommendationHtml = false
  await page.getByRole('button', { name: 'Get visual recommendation' }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-preview')
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('6 sampled frames')
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('Sampled video frames inspected')
  await confirmPlan(page, false, true)
  await expect(page.getByText('Automated edit complete. 3 MP4s are ready to review.', { exact: true })).toBeVisible({ timeout: 15000 })
  expect(state.events).toEqual(['select', 'render:c1', 'render:c2', 'render:c3'])
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({
    captionStyle: 'minimal', captionSettings: { enabled: false, preset: 'minimal', placement: 'top', fontSize: 48, outlineWidth: 3, wordsPerChunk: 2, backgroundOpacity: .65 },
    videoFilters: { brightness: .02, contrast: 1.1, saturation: 1, blur: 0, sharpen: 0 },
    outputSettings: { mode: 'ai', aspectRatio: '9:16', fit: 'crop', maxDimension: 1920 },
  })
  expect(state.requests.filter((request) => request.path.endsWith('/ai-edit/recommendations'))[0].body.brief).toContain('Audience: First-time founders')
})

test('replacement requires its own confirmation and an analysis failure stops before selection/render', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true }); state.failAnalysis = true
  await openAi(page); await fillBrief(page); await page.getByLabel('Reuse existing candidates').uncheck()
  await confirmPlan(page, true)
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('Analysis provider unavailable', { timeout: 15000 })
  expect(state.events).toEqual(['analyze']); expect(state.candidates).toHaveLength(3)
  await expect(page.getByRole('button', { name: 'Review plan', exact: true })).toBeEnabled()
})

test('no speech continues with visual analysis and captions off even when transcript analysis was requested', async ({ page }) => {
  const state = await setup(page); state.silentTranscript = true
  await openAi(page); await fillBrief(page); await page.getByRole('combobox', { name: 'Clip analysis', exact: true }).selectOption('transcript'); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 3 MP4s are ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  await expect(page.getByRole('region', { name: 'Activity timeline' })).toContainText('No speech found. Captions off')
  expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c1', 'render:c2', 'render:c3'])
  expect(state.requests.find((request) => request.path.endsWith('/analyze'))?.body.mode).toBe('visual')
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false } })
})

test('selection save failure stops before requesting any renders', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true }); state.failSelection = true
  await openAi(page); await fillBrief(page)
  await confirmPlan(page)
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('Selection could not be saved')
  expect(state.events).toEqual(['select'])
})

test('stopping automation or leaving the project prevents all future pipeline steps', async ({ page }) => {
  const state = await setup(page); state.holdTranscription = true
  await openAi(page); await fillBrief(page); await confirmPlan(page)
  await expect.poll(() => state.events).toEqual(['transcribe'])
  await page.getByRole('button', { name: 'Stop automation' }).click()
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('Already queued jobs')
  state.holdTranscription = false
  await page.getByRole('button', { name: 'Back to projects' }).click()
  await page.getByRole('button', { name: `Open project ${projects[0].name}` }).click(); await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Review plan', exact: true })).toBeEnabled({ timeout: 10000 })
  expect(state.events).toEqual(['transcribe'])
  state.transcript = null; state.jobs.transcription = { status: 'idle' }; state.holdTranscription = true
  await page.getByRole('button', { name: 'Back to projects' }).click()
  await page.getByRole('button', { name: `Open project ${projects[0].name}` }).click(); await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await confirmPlan(page); await expect.poll(() => state.events).toEqual(['transcribe', 'transcribe'])
  await page.getByRole('button', { name: 'Back to projects' }).click()
  state.holdTranscription = false
  await page.waitForTimeout(2000)
  expect(state.events).toEqual(['transcribe', 'transcribe'])
})

test('a project changed after review is rejected before any mutation', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true })
  await openAi(page); await fillBrief(page); await page.getByRole('button', { name: 'Review plan', exact: true }).click()
  const review = page.getByRole('dialog', { name: 'Review automated edit plan' })
  await review.getByLabel('I approve this plan and the project selection update').check()
  state.candidates[0].score = 20
  await review.getByRole('button', { name: 'Start automated edit' }).click()
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('project changed since review')
  expect(state.requests).toEqual([])
})

test('failed transcription still runs visual analysis for video and renders with captions off', async ({ page }) => {
  const state = await setup(page); state.failTranscription = true
  await openAi(page); await fillBrief(page); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 3 MP4s are ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  await expect(page.getByRole('region', { name: 'Activity timeline' })).toContainText('Transcription provider unavailable')
  await expect(page.getByRole('region', { name: 'Activity timeline' })).toContainText('continuing with sampled-frame visual analysis')
  expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c1', 'render:c2', 'render:c3'])
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false } })
})

test('render failures report the failed step and leave successful partial exports downloadable', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true }); state.failRender = true
  await openAi(page); await fillBrief(page)
  await confirmPlan(page)
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('FFmpeg failed')
  await expect(page.getByRole('link', { name: 'Download completed edits (2)' })).toBeVisible()
  await expect(page.getByText(/Automated edit complete\./)).toHaveCount(0)
})

test('legacy status fallback and completed-render reuse work; AI layout fits desktop/mobile in all three themes', async ({ page }, testInfo) => {
  const state = await setup(page, { hasCandidates: true, legacy: true }); state.reuseDoneRenders = true
  await openAi(page); await fillBrief(page)
  // Existing cuts can be reused on audio-only/silent projects without asking for transcription.
  await page.getByLabel('Burn in captions', { exact: true }).uncheck()
  await page.getByLabel('Number of clips').selectOption('1'); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible()
  expect(state.events).toEqual(['select', 'render:c1'])
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false } })
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({ outputSettings: fallbackRenderContract.output.defaults })
  for (const theme of ['light', 'dark', 'nord']) {
    await page.getByLabel('Color theme').selectOption(theme)
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    for (const width of [390, 640, 800, 1100, 1280, 1366, 1440, 1920]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await expect.poll(() => page.locator('.ai-edit').evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
      if ([390, 1280, 1366, 1440, 1920].includes(width)) {
        await page.locator('.ai-edit').evaluate((element) => { element.scrollTop = 0 }); await page.evaluate(() => window.scrollTo(0, 0))
        const content = await page.locator('.ai-edit').evaluate((element) => {
          const box = element.getBoundingClientRect(); const style = getComputedStyle(element)
          return { x: box.x + element.clientLeft + parseFloat(style.paddingLeft), width: element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) }
        })
        const layout = (await page.locator('.ai-edit-layout').boundingBox())!
        const main = (await page.locator('.ai-edit-main').boundingBox())!
        const chat = (await page.getByRole('complementary', { name: 'Project chat' }).boundingBox())!
        expect(Math.abs(layout.width - Math.min(1600, content.width)), 'AI layout fills its usable content up to its maximum').toBeLessThan(1)
        expect(Math.abs(layout.x + layout.width / 2 - content.x - content.width / 2), 'AI layout is centered inside the padded, gutter-free content').toBeLessThan(1)
        expect(Math.abs(main.x - layout.x)).toBeLessThan(1)
        if (width === 390) {
          expect(Math.abs(main.width - layout.width)).toBeLessThan(1)
          expect(Math.abs(chat.x - layout.x)).toBeLessThan(1)
          expect(Math.abs(chat.width - layout.width)).toBeLessThan(1)
          expect(main.y, 'mobile chat is available before the editing brief and timeline').toBeGreaterThanOrEqual(chat.y + chat.height)
        } else {
          expect(Math.abs(chat.x - main.x - main.width - 24), 'desktop chat and editing controls have the intended gap').toBeLessThan(1)
          expect(Math.abs(chat.x + chat.width - layout.x - layout.width)).toBeLessThan(1)
          expect(Math.abs(chat.y - main.y), 'desktop chat and main cards share a top edge').toBeLessThan(1)
        }
        if ((theme === 'nord' && width !== 1440) || (theme !== 'nord' && [390, 1440].includes(width))) {
          await page.screenshot({ path: testInfo.outputPath(`ai-edit-${theme}-${width}.png`), fullPage: true })
        }
      }
    }
  }
})

test('music/no-transcript visual automation skips transcription and passes through a reviewed manual output ratio', async ({ page }) => {
  const outputSettings: OutputSettings = { mode: 'manual', aspectRatio: '4:5', fit: 'contain', maxDimension: 1280 }
  const state = await setup(page, { outputSettings }); state.holdTranscription = true; state.holdAnalysis = true
  await openAi(page); await fillBrief(page)
  await expect(page.getByRole('combobox', { name: 'Clip analysis', exact: true })).toHaveValue('visual')
  await page.getByLabel('Burn in captions', { exact: true }).uncheck()
  await page.getByLabel('Number of clips').selectOption('1')
  await page.getByLabel('Topics, tone & things to avoid').fill('Keep the full performance, including the music.')
  await confirmPlan(page)
  await expect.poll(() => state.events).toEqual(['analyze'])
  await expect(page.getByRole('region', { name: 'Activity timeline' })).toContainText('Captions off; transcription skipped.')
  await expect(page.getByRole('region', { name: 'Activity timeline' })).toContainText('visual model is inspecting sampled video frames')
  state.holdAnalysis = false
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  expect(state.transcript).toBeNull()
  expect(state.events).toEqual(['analyze', 'select', 'render:c1'])
  expect(state.requests.find((request) => request.path.endsWith('/analyze'))?.body).toEqual({ mode: 'visual', brief: expect.stringContaining('Keep the full performance, including the music.') })
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({
    captionSettings: { enabled: false }, outputSettings, videoFilters: fallbackRenderContract.videoFilters.defaults,
  })
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-analysis')
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('Framing: pad · 1:1')
  await expect(page.getByRole('region', { name: 'Source ratio' })).toContainText('4:5')
})

for (const useAiSettings of [false, true]) {
  test(`latest persisted visual settings ${useAiSettings ? 'override only with reviewed opt-in' : 'leave the reviewed project snapshot intact'}`, async ({ page }) => {
    const outputSettings: OutputSettings = { mode: 'manual', aspectRatio: '16:9', fit: 'crop', maxDimension: 1280 }
    const state = await setup(page, { hasTranscript: true, outputSettings })
    await openAi(page); await fillBrief(page)
    await page.getByLabel('Number of clips').selectOption('1')
    await page.getByRole('button', { name: 'Get visual recommendation' }).click()
    await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-preview')
    await page.getByRole('button', { name: 'Review plan', exact: true }).click()
    const review = page.getByRole('dialog', { name: 'Review automated edit plan' })
    await expect(review.getByLabel('Use AI recommended settings', { exact: true })).not.toBeChecked()
    if (useAiSettings) {
      await review.getByLabel('Use AI recommended settings', { exact: true }).check()
      await expect(review).toContainText('9:16 · center crop · max 1280 px')
    } else await expect(review).toContainText('16:9 · center crop · max 1280 px')
    await review.getByLabel('I approve this plan and the project selection update').check()
    await review.getByRole('button', { name: 'Start automated edit' }).click()
    await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 15000 })
    const rendered = state.requests.find((request) => request.path.endsWith('/render/c1'))!.body
    expect(rendered.outputSettings).toEqual(useAiSettings ? { mode: 'ai', aspectRatio: '1:1', fit: 'contain', maxDimension: 1280 } : outputSettings)
    expect(rendered.captionSettings).toMatchObject({ enabled: true, preset: useAiSettings ? 'minimal' : 'classic', placement: useAiSettings ? 'top' : 'bottom', fontSize: 48, outlineWidth: 3, wordsPerChunk: 2 })
    expect(rendered.videoFilters).toMatchObject({ brightness: useAiSettings ? .07 : 0, saturation: 1 })
    await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-analysis')
    expect(state.requests.some((request) => request.path.endsWith('/render-settings'))).toBe(false)
  })
}

test('optional transcript mode sends the complete brief and skips transcription when speech is already present', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true })
  await openAi(page); await fillBrief(page)
  await page.getByRole('combobox', { name: 'Clip analysis', exact: true }).selectOption('transcript')
  await page.getByLabel('Burn in captions', { exact: true }).uncheck()
  await page.getByLabel('Destination').selectOption('reels')
  await page.getByLabel('Preferred clip length').selectOption('30-60')
  await page.getByLabel('Number of clips').selectOption('1')
  await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 15000 })
  expect(state.events).toEqual(['analyze', 'select', 'render:c3'])
  const analysis = state.requests.find((request) => request.path.endsWith('/analyze'))!.body
  expect(analysis.mode).toBe('transcript')
  for (const answer of ['Audience: First-time founders', 'Goal: One useful, practical idea', 'Platform: Instagram Reels', 'Clip count: up to 1', 'Preferred length: 30–60 seconds', 'Captions: off', 'Clip analysis: transcript']) expect(analysis.brief).toContain(answer)
  expect(state.visual).toBeNull()
})

test('audio-only sources create speech context for analysis even with captions off', async ({ page }) => {
  const state = await setup(page, { hasVideo: false })
  await openAi(page); await fillBrief(page)
  await page.getByLabel('Burn in captions', { exact: true }).uncheck()
  await page.getByLabel('Number of clips').selectOption('1')
  await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c1'])
  expect(state.requests.find((request) => request.path.endsWith('/analyze'))?.body.mode).toBe('transcript')
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false } })
  expect(state.visual).toBeNull()
})

test('chat errors are plain and bounded, never become fake model messages, and a subsequent request can recover', async ({ page }) => {
  const state = await setup(page); state.chatStatus = 502; state.chatHtml = true
  await openAi(page)
  await page.getByLabel('Message to project model').fill('Music fans'); await page.getByRole('button', { name: 'Send to model' }).click()
  const chat = page.getByRole('complementary', { name: 'Project chat' })
  await expect(chat.getByRole('alert')).toContainText('HTTP 502')
  await expect(chat.getByRole('alert')).not.toContainText('secret debug dump')
  await expect(chat.locator('.ai-chat-model')).toHaveCount(0)
  state.chatHtml = false; state.chatStatus = 500; state.chatError = `Provider unavailable: ${'x'.repeat(4000)}`
  await page.getByLabel('Message to project model').fill('Enjoy the performance'); await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(chat.getByRole('alert')).toContainText('Provider unavailable')
  expect((await chat.getByRole('alert').innerText()).length).toBeLessThanOrEqual(600)
  state.chatStatus = 200
  await page.getByLabel('Message to project model').fill('Keep the original framing'); await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(chat.locator('.ai-chat-model')).toHaveCount(1)
  await expect(chat.getByRole('log')).toContainText('Project context: metadata')
  await expect(chat.getByRole('alert')).toHaveCount(0)
  const sent = state.requests.filter((request) => request.path.endsWith('/ai-edit/chat')).at(-1)!.body
  for (const answer of ['Music fans', 'Enjoy the performance', 'Keep the original framing']) expect(sent.brief).toContain(answer)
  expect(state.events).toEqual([])
})


test('full form brief reaches chat, recommendations and automation without losing trailing options', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true })
  await openAi(page)
  await page.getByLabel('Who is the audience?', { exact: true }).fill('a'.repeat(1000))
  await page.getByLabel('What should viewers take away?', { exact: true }).fill('g'.repeat(1000))
  const message = 'Preserve the ending'
  const notes = 'n'.repeat(2000 - message.length - 1)
  await page.getByLabel('Topics, tone & things to avoid').fill(notes)
  await page.getByLabel('Number of clips').selectOption('1')
  await page.getByLabel('Destination').selectOption('reels')
  // The appended message and its newline exactly fill the notes field's budget.
  await page.getByLabel('Message to project model').fill(message)
  await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(page.locator('.ai-chat-model')).toHaveCount(1)
  await expect(page.getByLabel('Topics, tone & things to avoid')).toHaveValue(`${notes}\n${message}`)
  await page.getByRole('button', { name: 'Get visual recommendation' }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-preview')
  await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 15000 })
  const briefs = state.requests.filter(({ path }) => /\/(ai-edit\/(chat|recommendations)|analyze)$/.test(path)).map(({ body }) => body.brief as string)
  expect(briefs).toHaveLength(3)
  for (const brief of briefs) {
    expect(brief.length).toBeGreaterThan(4000)
    expect(brief.length).toBeLessThanOrEqual(5000)
    expect(brief).toContain('a'.repeat(1000))
    expect(brief).toContain('g'.repeat(1000))
    expect(brief).toContain('Preserve the ending')
    expect(brief).toContain('Platform: Instagram Reels')
    expect(brief).toContain('Reuse candidates: yes')
  }
})

test('chat note overflow preserves the unsent message and draft and recovers after shortening notes', async ({ page, browser }, testInfo) => {
  test.setTimeout(90000)
  const state = await setup(page)
  await openAi(page); await fillBrief(page)
  const notes = 'n'.repeat(2000)
  await page.getByLabel('Topics, tone & things to avoid').fill(notes)
  await page.getByLabel('Message to project model').fill('Keep this unsent preference')
  await page.getByRole('button', { name: 'Send to model' }).click()
  const chat = page.getByRole('complementary', { name: 'Project chat' })
  await expect(chat.getByRole('alert')).toContainText('2000-character notes limit')
  await expect(page.getByLabel('Message to project model')).toHaveValue('Keep this unsent preference')
  await expect(page.getByLabel('Topics, tone & things to avoid')).toHaveValue(notes)
  expect(state.requests).toEqual([])
  await expect(chat.locator('.ai-chat-user')).toHaveCount(0)
  for (const theme of ['dark', 'light', 'nord']) {
    await page.getByLabel('Color theme').selectOption(theme)
    for (const width of [1440, 1920, 1280, 1366, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
      await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width)
      await chat.getByRole('alert').scrollIntoViewIfNeeded()
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await expect.poll(() => chat.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
      await page.screenshot({ path: testInfo.outputPath(`ai-edit-notes-overflow-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
    }
  }
  // Half the CSS viewport at 2x device scale reproduces 200% zoom's reflow.
  // Use a separate context so screenshot/device metrics cannot affect the matrix.
  const zoomContext = await browser.newContext({ viewport: { width: 720, height: 480 }, deviceScaleFactor: 2 })
  try {
    const zoomPage = await zoomContext.newPage()
    await setup(zoomPage); await openAi(zoomPage); await fillBrief(zoomPage)
    await zoomPage.getByLabel('Topics, tone & things to avoid').fill(notes)
    await zoomPage.getByLabel('Message to project model').fill('Keep this unsent preference')
    await zoomPage.getByRole('button', { name: 'Send to model' }).click()
    const alert = zoomPage.getByRole('complementary', { name: 'Project chat' }).getByRole('alert')
    await expect(alert).toContainText('2000-character notes limit')
    for (const theme of ['dark', 'light', 'nord']) {
      await zoomPage.getByLabel('Color theme').selectOption(theme)
      await expect.poll(() => zoomPage.evaluate(() => innerWidth)).toBe(720)
      await alert.scrollIntoViewIfNeeded()
      await expect.poll(() => zoomPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await zoomPage.screenshot({ path: testInfo.outputPath(`ai-edit-notes-overflow-${theme}-1440-zoom-200.png`), fullPage: true, animations: 'disabled' })
    }
  } finally { await zoomContext.close() }
  await page.getByLabel('Topics, tone & things to avoid').fill('Shorter notes')
  await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(chat.getByRole('alert')).toHaveCount(0)
  await expect(chat.locator('.ai-chat-model')).toHaveCount(1)
  await expect(page.getByLabel('Topics, tone & things to avoid')).toHaveValue('Shorter notes\nKeep this unsent preference')
  expect(state.requests.filter(({ path }) => path.endsWith('/ai-edit/chat'))).toHaveLength(1)
})

test('saved project visual analysis is visible after reopening and silent video without audio does not transcribe', async ({ page }) => {
  const state = await setup(page, { hasVisual: true, hasCandidates: true, hasAudio: false })
  state.visual!.source.hasAudio = false
  await openAi(page); await fillBrief(page)
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-analysis')
  await page.getByLabel('Number of clips').selectOption('1')
  await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible()
  expect(state.events).toEqual(['select', 'render:c1'])
  expect(state.requests.find((request) => request.path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false }, outputSettings: fallbackRenderContract.output.defaults })
})

test('pending model chat and visual recommendation requests can be stopped without adding late responses', async ({ page }) => {
  await setup(page)
  let releaseChat!: () => void
  const heldChat = new Promise<void>((resolve) => { releaseChat = resolve })
  await page.route('**/ai-edit/chat', async (route) => {
    await heldChat
    await route.fulfill({ json: { message: 'Late chat reply', model: 'fixture-late-chat', basis: 'metadata' } }).catch(() => {})
  })
  let releaseRecommendation!: () => void
  const heldRecommendation = new Promise<void>((resolve) => { releaseRecommendation = resolve })
  await page.route('**/ai-edit/recommendations', async (route) => {
    await heldRecommendation
    await route.fulfill({ json: visualRecommendation() }).catch(() => {})
  })
  await openAi(page)
  await page.getByLabel('Message to project model').fill('Music fans'); await page.getByRole('button', { name: 'Send to model' }).click()
  await expect(page.getByRole('complementary', { name: 'Project chat' })).toContainText('Waiting for the project model')
  await page.getByRole('button', { name: 'Stop chat', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Chat request stopped.')
  releaseChat()
  await expect(page.getByRole('button', { name: 'Get visual recommendation' })).toBeEnabled()
  await page.getByRole('button', { name: 'Get visual recommendation' }).click()
  await expect(page.getByRole('button', { name: 'Inspecting sampled frames…' })).toBeDisabled()
  await page.getByRole('button', { name: 'Stop recommendation', exact: true }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' }).getByRole('alert')).toContainText('Recommendation request stopped.')
  releaseRecommendation()
  await expect(page.getByRole('button', { name: 'Get visual recommendation' })).toBeEnabled()
  await expect(page.locator('.ai-chat-model')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'AI recommendation' })).not.toContainText('fixture-visual-preview')
})
