import { expect, test, type Page } from '@playwright/test'
import type { Candidate, Clip, JobStatus, OutputSettings, Project, ProjectDetail, ProjectRenderSettings } from '../src/api/client'
import { defaultAiBrief, editingBriefFingerprint, type AiEditBrief, type VisualRecommendation } from '../src/api/aiEdit'
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
const visualCandidates = [
  { start: 0, end: 20, score: 78, hook: 'Open with the useful idea', rationale: 'The opening establishes the topic clearly.', topic: 'opening', audienceReason: 'Viewers get immediate context.', assessment: { audienceFit: 4, hook: 4, payoff: 4, clarity: 4, shareability: 3 }, evidenceFrame: 0, evidenceDescription: 'The speaker is addressing the camera.' },
  { start: 25, end: 55, score: 78, hook: 'Show the complete example', rationale: 'A concrete demonstration.', topic: 'example', audienceReason: 'A practical reference.', assessment: { audienceFit: 4, hook: 4, payoff: 4, clarity: 4, shareability: 3 }, evidenceFrame: 2, evidenceDescription: 'A clear demonstration is visible.' },
  { start: 65, end: 110, score: 78, hook: 'Close with the takeaway', rationale: 'A complete conclusion.', topic: 'takeaway', audienceReason: 'Reinforces the lesson.', assessment: { audienceFit: 4, hook: 4, payoff: 4, clarity: 4, shareability: 3 }, evidenceFrame: 5, evidenceDescription: 'The speaker delivers the conclusion.' },
]
function transcript(id = 't1', text = 'A useful idea for your viewers.') {
  return { id, engine: 'whisper', language: 'en', raw_json: JSON.stringify({ language: 'en', duration: 120, speakers: [], words: [{ start: 1, end: 2, text }], segments: [{ start: 1, end: 2, text }] }) }
}
function visualRecommendation(latest = false, provenance?: AiEditBrief): VisualRecommendation {
  return {
    aspectRatio: latest ? { mode: 'pad', ratio: '1:1' } : { mode: 'crop', ratio: '9:16' },
    videoFilters: { brightness: latest ? .07 : .02, contrast: 1.1 },
    captionSettings: { enabled: true, preset: 'minimal', placement: 'top' }, candidates: visualCandidates,
    audience: 'First-time founders', rationale: latest ? 'Keep the full story in a square padded frame.' : 'Use the complete idea as a clear opening.',
    analysisBasis: 'audio-and-sampled-frames', source: { width: 1920, height: 1080, durationSeconds: 120, hasVideo: true, hasAudio: true },
    sampledFrameTimes: [0, 20, 40, 60, 80, 100, 110, 119], contextNotice: 'Audio transcript and sampled video frames inspected together.',
    model: latest ? 'fixture-visual-analysis' : 'fixture-visual-preview',
    ...(provenance ? { provenance: { version: 1 as const, briefFingerprint: editingBriefFingerprint(provenance) } } : {}),
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
    failSelection: false, failRender: false, recommendationStatus: 200, recommendationHtml: false,
    reuseDoneRenders: false, stageReads: 0, renderReads: 0,
    visual: options.hasVisual ? visualRecommendation(true) : null as VisualRecommendation | null,
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
        if (options.hasVideo !== false) state.visual = visualRecommendation(true)
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
    if (path === '/render-settings') return json(fallbackRenderContract)
    if (/^\/projects\/p[12]$/.test(path)) {
      advance()
      return json({ project: { ...projects.find((project) => path.endsWith(project.id)), ...state.renderSettings[path.split('/').at(-1)!],
        source_metadata_json: JSON.stringify({ width: options.hasVideo === false ? 0 : 1920, height: options.hasVideo === false ? 0 : 1080, has_video: options.hasVideo ?? true, has_audio: options.hasAudio ?? true }), visualAnalysis: state.visual },
        transcript: state.transcript, candidates: state.candidates, clips: state.clips, ...(options.legacy ? {} : { jobs: state.jobs }) })
    }
    if (path.endsWith('/transcribe/status')) return json(state.jobs.transcription)
    if (path.endsWith('/analyze/status')) return json(state.jobs.analysis)
    if (path.endsWith('/probe')) return json({ duration_sec: 120, width: 1920, height: 1080, has_video: true, has_audio: options.hasAudio ?? true, video_codec: 'h264', audio_codec: 'aac' })
    if (path.endsWith('/render-settings') && path.startsWith('/projects/')) {
      const projectId = path.split('/')[2]
      if (method === 'PUT') state.renderSettings[projectId] = { ...state.renderSettings[projectId], ...body() }
      return json(state.renderSettings[projectId])
    }
    if (path.endsWith('/filter-suggestions/generate')) return json({ id: 'audience-look', projectId: 'p1', settings: fallbackRenderContract.videoFilters.defaults, captionSettings: null, source: 'ai', model: 'fixture-editor', rationale: 'Readable, restrained settings for the specified audience.', applied: false, createdAt: '2026-10-05' }, 201)
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
  const reuse = page.getByLabel('Reuse existing candidates', { exact: true })
  if (await reuse.count()) await reuse.check()
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

test('audience brief is shared across Manual and AI buttons, and changing it alone makes no model request', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true })
  await openAi(page); await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await page.locator('.audience-brief summary').click()
  await page.getByLabel('Target audience', { exact: true }).fill('First-time founders')
  await page.getByLabel('Viewer takeaway', { exact: true }).fill('Make a clear customer pitch')
  await page.getByLabel('Audience notes & exclusions', { exact: true }).fill('Avoid sponsor reads')
  expect(state.requests).toEqual([])
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByLabel('Who is the audience?', { exact: true })).toHaveValue('First-time founders')
  await expect(page.getByLabel('Reuse existing candidates', { exact: true })).not.toBeChecked()
  await expect(page.getByText('Project chat', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Clip analysis', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Get AI recommendation' }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('Audio transcript and sampled video frames inspected together.')
  expect(state.requests[0].body.audienceBrief).toEqual({ audience: 'First-time founders', goal: 'Make a clear customer pitch', notes: 'Avoid sponsor reads' })
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  state.holdAnalysis = true
  await page.getByRole('button', { name: 'Analyze again', exact: true }).click()
  await page.getByRole('button', { name: 'Replace and analyze', exact: true }).click()
  await expect.poll(() => state.events).toEqual(['analyze'])
  expect(state.requests.find(({ path }) => path.endsWith('/analyze'))?.body).toEqual({ mode: 'auto', brief: '', audienceBrief: { audience: 'First-time founders', goal: 'Make a clear customer pitch', notes: 'Avoid sponsor reads' } })
})

test('briefs remain project scoped and review preserves Manual state without starting jobs', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true })
  await openAi(page); await fillBrief(page)
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await page.getByRole('button', { name: 'Create manual cut', exact: true }).click()
  await page.getByLabel('Start (seconds)').fill('7'); await page.getByLabel('End (seconds)').fill('25')
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await page.getByRole('button', { name: 'Review plan', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Reuse existing candidates without reranking for this audience.')
  await page.getByRole('button', { name: 'Back to brief' }).click()
  expect(state.requests).toEqual([])
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await expect(page.getByLabel('Start (seconds)')).toHaveValue('7')
  await page.getByRole('button', { name: 'Back to projects' }).click()
  await page.getByRole('button', { name: `Open project ${projects[1].name}` }).click(); await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByLabel('Who is the audience?', { exact: true })).toHaveValue('')
  await page.getByRole('button', { name: 'Back to projects' }).click()
  await page.getByRole('button', { name: `Open project ${projects[0].name}` }).click(); await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByLabel('Who is the audience?', { exact: true })).toHaveValue('First-time founders')
})

test('confirmed automation transcribes, combines evidence, selects by length, and preserves source output', async ({ page }) => {
  const state = await setup(page); state.holdTranscription = true
  await openAi(page); await fillBrief(page)
  await page.getByLabel('Number of clips').selectOption('1'); await page.getByLabel('Preferred clip length').selectOption('15-30')
  await confirmPlan(page); await expect.poll(() => state.events).toEqual(['transcribe'])
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Transcribing…', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click(); state.holdTranscription = false
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c2'])
  expect(state.requests.find(({ path }) => path.endsWith('/analyze'))?.body).toMatchObject({ mode: 'auto', brief: expect.stringContaining('Goal: One useful, practical idea') })
  expect(state.requests.find(({ path }) => path.endsWith('/candidates'))?.body).toEqual({ selectedIds: ['c2'] })
  expect(state.requests.find(({ path }) => path.endsWith('/render/c2'))?.body).toMatchObject({ captionSettings: { enabled: true }, outputSettings: fallbackRenderContract.output.defaults })
  await expect(page.getByRole('link', { name: 'Download completed edits (1)' })).toHaveAttribute('href', '/api/projects/p1/export?clipIds=render-c2')
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Preview clip 2' }).locator('video')).toHaveAttribute('src', '/api/clips/render-c2/file')
})

test('recommendation errors are readable and recover; reviewed suggestions merge full caption settings', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true }); state.recommendationStatus = 502; state.recommendationHtml = true
  await openAi(page); await fillBrief(page); await page.getByLabel('Burn in captions').uncheck()
  await page.getByRole('button', { name: 'Inspect source metadata' }).click()
  await expect(page.getByRole('region', { name: 'Source ratio' })).toContainText('1920 × 1080')
  await page.getByRole('button', { name: 'Get AI recommendation' }).click()
  await expect(page.getByRole('alert')).toContainText('HTTP 502'); await expect(page.getByRole('alert')).not.toContainText('debug dump')
  state.recommendationStatus = 200; state.recommendationHtml = false
  await page.getByRole('button', { name: 'Get AI recommendation' }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('8 sampled frames')
  await confirmPlan(page, false, true)
  await expect(page.getByText('Automated edit complete. 3 MP4s are ready to review.', { exact: true })).toBeVisible({ timeout: 15000 })
  expect(state.events).toEqual(['select', 'render:c1', 'render:c2', 'render:c3'])
  expect(state.requests.find(({ path }) => path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false, preset: 'minimal', placement: 'top', fontSize: 48, outlineWidth: 3, wordsPerChunk: 2 }, videoFilters: { brightness: .02, saturation: 1 }, outputSettings: { mode: 'ai', aspectRatio: '9:16', fit: 'crop', maxDimension: 1920 } })
})

test('replacement requires confirmation and analysis failure preserves existing selection and stops renders', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true }); state.failAnalysis = true
  await openAi(page); await fillBrief(page); await page.getByLabel('Reuse existing candidates').uncheck(); await confirmPlan(page, true)
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('Analysis provider unavailable', { timeout: 15000 })
  expect(state.events).toEqual(['analyze']); expect(state.candidates).toHaveLength(3)
})
for (const failure of ['silent', 'provider'] as const) {
  test(`${failure} transcription uses available video evidence and disables captions`, async ({ page }) => {
    const state = await setup(page); state.silentTranscript = failure === 'silent'; state.failTranscription = failure === 'provider'
    await openAi(page); await fillBrief(page); await confirmPlan(page)
    await expect(page.getByText('Automated edit complete. 3 MP4s are ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
    expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c1', 'render:c2', 'render:c3'])
    expect(state.requests.find(({ path }) => path.endsWith('/analyze'))?.body.mode).toBe('auto')
    expect(state.requests.find(({ path }) => path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false } })
    await expect(page.getByRole('region', { name: 'Activity timeline' })).toContainText(failure === 'silent' ? 'No speech found' : 'Transcription provider unavailable')
  })
}
test('selection failure stops before renders; successful partial renders remain downloadable', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true }); state.failSelection = true
  await openAi(page); await fillBrief(page); await confirmPlan(page)
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('Selection could not be saved')
  expect(state.events).toEqual(['select']); state.failSelection = false; state.failRender = true
  await confirmPlan(page)
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('FFmpeg failed')
  await expect(page.getByRole('link', { name: 'Download completed edits (2)' })).toBeVisible()
})
test('stop and unmount prevent future steps while accepted jobs can finish', async ({ page }) => {
  const state = await setup(page); state.holdTranscription = true
  await openAi(page); await fillBrief(page); await confirmPlan(page); await expect.poll(() => state.events).toEqual(['transcribe'])
  await page.getByRole('button', { name: 'Stop automation' }).click()
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('Already queued jobs')
  state.holdTranscription = false; await page.getByRole('button', { name: 'Back to projects' }).click()
  await page.getByRole('button', { name: `Open project ${projects[0].name}` }).click(); await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Review plan', exact: true })).toBeEnabled({ timeout: 10000 })
  expect(state.events).toEqual(['transcribe'])
})
test('stale project review is rejected before mutations', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true, hasCandidates: true })
  await openAi(page); await fillBrief(page); await page.getByRole('button', { name: 'Review plan' }).click()
  const review = page.getByRole('dialog'); await review.getByLabel('I approve this plan and the project selection update').check()
  state.candidates[0].score = 20; await review.getByRole('button', { name: 'Start automated edit' }).click()
  await expect(page.getByRole('region', { name: 'Activity timeline' }).getByRole('alert')).toContainText('project changed since review')
  expect(state.requests).toEqual([])
})
test('legacy status and reused renders work; AI layout fills desktop/mobile in all themes', async ({ page }, testInfo) => {
  const state = await setup(page, { hasCandidates: true, legacy: true }); state.reuseDoneRenders = true
  await openAi(page); await fillBrief(page); await page.getByLabel('Burn in captions').uncheck(); await page.getByLabel('Number of clips').selectOption('1'); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible()
  expect(state.events).toEqual(['select', 'render:c1'])
  for (const theme of ['light', 'dark', 'nord']) {
    await page.getByLabel('Color theme').selectOption(theme)
    for (const width of [390, 640, 800, 1100, 1280, 1366, 1440, 1920]) {
      await page.setViewportSize({ width, height: 960 })
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      const layout = (await page.locator('.ai-edit-layout').boundingBox())!
      const main = (await page.locator('.ai-edit-main').boundingBox())!
      expect(Math.abs(layout.width - main.width)).toBeLessThan(1)
      if ([390, 1440].includes(width)) await page.screenshot({ path: testInfo.outputPath(`ai-edit-${theme}-${width}.png`), fullPage: true })
    }
  }
})
test('fresh video analysis transcribes audio even with captions off and preserves a reviewed manual ratio', async ({ page }) => {
  const outputSettings: OutputSettings = { mode: 'manual', aspectRatio: '4:5', fit: 'contain', maxDimension: 1280 }
  const state = await setup(page, { outputSettings })
  await openAi(page); await fillBrief(page); await page.getByLabel('Burn in captions').uncheck(); await page.getByLabel('Number of clips').selectOption('1'); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c1'])
  expect(state.requests.find(({ path }) => path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false }, outputSettings })
})
for (const useAiSettings of [false, true]) {
  test(`latest combined settings ${useAiSettings ? 'apply with reviewed opt-in' : 'preserve project settings'}`, async ({ page }) => {
    const outputSettings: OutputSettings = { mode: 'manual', aspectRatio: '16:9', fit: 'crop', maxDimension: 1280 }
    const state = await setup(page, { hasTranscript: true, outputSettings })
    await openAi(page); await fillBrief(page); await page.getByLabel('Number of clips').selectOption('1')
    await page.getByRole('button', { name: 'Get AI recommendation' }).click()
    await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-preview')
    await confirmPlan(page, false, useAiSettings)
    await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 15000 })
    const rendered = state.requests.find(({ path }) => path.endsWith('/render/c1'))!.body
    expect(rendered.outputSettings).toEqual(useAiSettings ? { mode: 'ai', aspectRatio: '1:1', fit: 'contain', maxDimension: 1280 } : outputSettings)
    expect(rendered.captionSettings).toMatchObject({ enabled: true, preset: useAiSettings ? 'minimal' : 'classic', placement: useAiSettings ? 'top' : 'bottom', fontSize: 48, outlineWidth: 3 })
  })
}
test('full form brief reaches recommendations and automation without losing trailing preferences', async ({ page }) => {
  const state = await setup(page, { hasTranscript: true }); await openAi(page)
  await page.getByLabel('Who is the audience?', { exact: true }).fill('a'.repeat(1000))
  await page.getByLabel('What should viewers take away?', { exact: true }).fill('g'.repeat(1000))
  await page.getByLabel('Topics, tone & things to avoid').fill('n'.repeat(1980) + ' Preserve the ending')
  await page.getByLabel('Number of clips').selectOption('1'); await page.getByLabel('Destination').selectOption('reels')
  await page.getByRole('button', { name: 'Get AI recommendation' }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-preview'); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 15000 })
  const briefs = state.requests.filter(({ path }) => /\/(ai-edit\/recommendations|analyze)$/.test(path)).map(({ body }) => body.brief as string)
  expect(briefs).toHaveLength(2)
  for (const brief of briefs) {
    expect(brief.length).toBeGreaterThan(4000); expect(brief.length).toBeLessThanOrEqual(5000)
    expect(brief).toContain('Preserve the ending'); expect(brief).toContain('Platform: Instagram Reels')
  }
})
test('audio-only sources transcribe for analysis even without captions', async ({ page }) => {
  const state = await setup(page, { hasVideo: false }); await openAi(page); await fillBrief(page)
  await page.getByLabel('Burn in captions').uncheck(); await page.getByLabel('Number of clips').selectOption('1'); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible({ timeout: 20000 })
  expect(state.events).toEqual(['transcribe', 'analyze', 'select', 'render:c1']); expect(state.visual).toBeNull()
})
test('silent video without audio reuses saved candidates without transcription', async ({ page }) => {
  const state = await setup(page, { hasVisual: true, hasCandidates: true, hasAudio: false }); state.visual!.source.hasAudio = false
  await openAi(page); await fillBrief(page); await page.getByLabel('Number of clips').selectOption('1'); await confirmPlan(page)
  await expect(page.getByText('Automated edit complete. 1 MP4 is ready to review.', { exact: true })).toBeVisible()
  expect(state.events).toEqual(['select', 'render:c1'])
  expect(state.requests.find(({ path }) => path.endsWith('/render/c1'))?.body).toMatchObject({ captionSettings: { enabled: false } })
})
test('persisted recommendations cannot supply settings after the shared brief changes', async ({ page }) => {
  const state = await setup(page, { hasVisual: true, hasCandidates: true })
  const brief = { ...defaultAiBrief, audience: 'First-time founders', goal: 'One useful, practical idea', reuseCandidates: true }
  state.visual = visualRecommendation(true, brief); await openAi(page); await fillBrief(page)
  await page.getByRole('button', { name: 'Review plan' }).click(); await expect(page.getByLabel('Use AI recommended settings')).toBeVisible()
  await page.getByRole('button', { name: 'Back to brief' }).click(); await page.getByLabel('Who is the audience?', { exact: true }).fill('Another audience')
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('The brief changed')
  await page.getByRole('button', { name: 'Review plan' }).click(); await expect(page.getByLabel('Use AI recommended settings')).toHaveCount(0)
})
test('pending recommendations can be stopped without accepting late responses', async ({ page }) => {
  await setup(page, { hasTranscript: true })
  let release!: () => void; const held = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/ai-edit/recommendations', async (route) => { await held; await route.fulfill({ json: visualRecommendation() }).catch(() => {}) })
  await openAi(page); await page.getByRole('button', { name: 'Get AI recommendation' }).click()
  await page.getByRole('button', { name: 'Stop recommendation' }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' }).getByRole('alert')).toContainText('Recommendation request stopped.')
  release(); await expect(page.getByRole('button', { name: 'Get AI recommendation' })).toBeEnabled()
  await expect(page.getByRole('region', { name: 'AI recommendation' })).not.toContainText('fixture-visual-preview')
})

test('asking for a recommendation creates audio context even with captions off', async ({ page }) => {
  const state = await setup(page); await openAi(page)
  await page.getByLabel('Burn in captions').uncheck()
  await page.getByRole('button', { name: 'Get AI recommendation' }).click()
  await expect(page.getByRole('region', { name: 'AI recommendation' })).toContainText('fixture-visual-preview', { timeout: 15000 })
  expect(state.events).toEqual(['transcribe'])
  expect(state.requests.map(({ path }) => path)).toEqual(['/projects/p1/transcribe', '/projects/p1/ai-edit/recommendations'])
})
