/** Capture the actual UI with generated media and synthetic data; no backend/API keys. */
import { chromium } from '@playwright/test'
import { spawn, spawnSync } from 'node:child_process'
import { mkdir, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { fallbackRenderContract } from '../src/api/renderDefaults.ts'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const root = resolve(frontend, '..')
const images = resolve(root, 'docs/images')
const temporary = resolve(frontend, 'test-results/docs-capture')
const sourcePath = resolve(temporary, 'demo-source.mp4')
const framePath = resolve(temporary, 'demo-preview.jpg')
const port = 5181
await mkdir(images, { recursive: true })
await mkdir(temporary, { recursive: true })

const chart = 'color=c=0x10212b:s=1280x720:r=12,'
  + 'drawbox=x=80:y=70:w=1120:h=580:color=0x1c3545:t=fill,'
  + 'drawbox=x=155:y=390:w=155:h=190:color=0x74a6be:t=fill,'
  + 'drawbox=x=355:y=310:w=155:h=270:color=0xa6c5d6:t=fill,'
  + 'drawbox=x=555:y=230:w=155:h=350:color=0x88c0d0:t=fill,'
  + 'drawbox=x=755:y=150:w=155:h=430:color=0xb8d7e8:t=fill'
const generated = spawnSync(process.env.FFMPEG_BIN || 'ffmpeg', ['-nostdin', '-v', 'error',
  '-f', 'lavfi', '-i', chart, '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono',
  '-t', '90', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
  '-c:a', 'aac', '-movflags', '+faststart', '-y', sourcePath], { encoding: 'utf8' })
if (generated.status !== 0) throw new Error('Could not generate demo media. Install FFmpeg with libx264 on PATH.')
const media = await readFile(sourcePath)
const frame = spawnSync(process.env.FFMPEG_BIN || 'ffmpeg', ['-nostdin', '-v', 'error',
  '-ss', '44', '-i', sourcePath, '-frames:v', '1', '-vf',
  "drawtext=text='the customer problem':fontcolor=white:fontsize=48:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-text_h-70",
  '-y', framePath], { encoding: 'utf8' })
if (frame.status !== 0) throw new Error('Could not generate the caption preview. Check FFmpeg drawtext and fonts.')
const previewImage = await readFile(framePath)
const caption = { ...fallbackRenderContract.caption.presets[0].settings, enabled: true }
const settings = { captionSettings: caption, videoFilters: fallbackRenderContract.videoFilters.defaults,
  outputSettings: fallbackRenderContract.output.defaults }
const metadata = { duration_sec: 90, width: 1280, height: 720, display_width: 1280, display_height: 720,
  has_video: true, has_audio: true, sample_aspect_ratio: 1, rotation: 0 }
const project = { id: 'demo-project', name: 'A customer-first product story', source_path: '/demo/customer-story.mp4',
  source_duration: 90, status: 'analyzed', transcription_mode: 'whisper', caption_style: 'classic',
  source_metadata_json: JSON.stringify(metadata), ...settings, created_at: '2026-10-01', updated_at: '2026-10-05' }
const audience = { audience: 'First-time founders', goal: 'Explain a product through the customer problem',
  notes: 'Keep the practical example. Skip greetings, sponsor reads, and exaggerated claims.' }
const selection = { method: 'audience-first-v1', audience: audience.audience, audienceInferred: false,
  audienceReason: 'A complete, practical example helps founders explain why a customer would care.',
  topic: 'Customer-first pitch', assessment: { audienceFit: 5, hook: 4, payoff: 5, clarity: 4, shareability: 3 },
  evidence: { basis: 'transcript', openingQuote: 'Lead with the customer problem.',
    closingQuote: 'Show one example of the result they can achieve.' } }
const candidates = [
  { id: 'demo-c1', project_id: project.id, start_sec: 40, end_sec: 65, score: 89,
    hook: 'Lead with the customer problem', rationale: 'A clear opening, a concrete example, and a useful payoff for a first-time founder.',
    rank: 1, selected: 1, selection },
  { id: 'demo-c2', project_id: project.id, start_sec: 10, end_sec: 35, score: 78,
    hook: 'One promise your audience can remember', rationale: 'Focus the message on a single outcome instead of a list of product features.',
    rank: 2, selected: 0, selection: { ...selection, topic: 'A memorable promise',
      evidence: { basis: 'transcript', openingQuote: 'Make one promise your audience can remember.',
        closingQuote: 'A useful outcome beats a long feature list.' },
      assessment: { audienceFit: 4, hook: 4, payoff: 4, clarity: 4, shareability: 3 } } },
]
const transcript = { id: 'demo-transcript', engine: 'whisper', language: 'en', raw_json: JSON.stringify({
  language: 'en', duration: 90, speakers: ['Speaker 1'], words: [
    { start: 40, end: 42, text: 'Lead', speaker: 'Speaker 1' }, { start: 42, end: 44, text: 'with', speaker: 'Speaker 1' },
    { start: 44, end: 46, text: 'the customer problem.', speaker: 'Speaker 1' },
  ], segments: [
    { start: 0, end: 10, text: 'Start with what your customer is trying to achieve.', speaker: 'Speaker 1' },
    { start: 10, end: 35, text: 'Make one promise your audience can remember. A useful outcome beats a long feature list.', speaker: 'Speaker 1' },
    { start: 40, end: 65, text: 'Lead with the customer problem. Show one example of the result they can achieve.', speaker: 'Speaker 1' },
    { start: 65, end: 90, text: 'Keep the opening clear, make the example concrete, and deliver the promised takeaway.', speaker: 'Speaker 1' },
  ],
}) }
const clips = [{ id: 'demo-render', candidate_id: 'demo-c1', status: 'done', output_path: '/demo/clip.mp4',
  render_log: 'Demo completed artifact', render_settings_json: JSON.stringify({ caption, videoFilters: settings.videoFilters,
    outputSettings: settings.outputSettings, output: { width: 1280, height: 720 } }) }]
const recommendation = { audience: audience.audience, aspectRatio: { mode: 'preserve', ratio: 'source' },
  videoFilters: settings.videoFilters, captionSettings: { enabled: true, preset: 'classic', placement: 'bottom' },
  candidates: candidates.map((candidate) => ({ start: candidate.start_sec, end: candidate.end_sec,
    score: candidate.score, hook: candidate.hook, rationale: candidate.rationale, selection: { ...candidate.selection,
      evidence: { basis: 'sampled-frames', timeSeconds: candidate.start_sec === 40 ? 40 : 20,
        description: 'A four-column chart presents a visible comparison.' } } })),
  rationale: 'Keep the full demonstration visible and use readable captions. Prioritize a complete example that gives these viewers a useful takeaway.',
  analysisBasis: 'sampled-frames', model: 'Demo editorial model',
  source: { width: 1280, height: 720, durationSeconds: 90, hasVideo: true, hasAudio: true },
  sampledFrameTimes: [0, 10, 20, 30, 40, 50, 60, 70, 80, 89.5],
  contextNotice: 'Demo recommendation. Visual analysis uses sampled frames; speech selection uses timed transcript evidence.' }

const preview = spawn(process.execPath,
  [resolve(frontend, 'node_modules/vite/bin/vite.js'), 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd: frontend, stdio: 'ignore', detached: process.platform !== 'win32' })
let browser: Awaited<ReturnType<typeof chromium.launch>> | undefined
try {
  let ready = false
  for (let attempt = 0; attempt < 100; attempt++) {
    try { ready = (await fetch(`http://127.0.0.1:${port}`)).ok } catch { /* Server startup. */ }
    if (ready) break
    if (preview.exitCode !== null) throw new Error('Preview failed to start. Build the frontend and make sure port 5181 is free.')
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  if (!ready) throw new Error('Preview did not start. Run npm run build first.')
  browser = await chromium.launch({ channel: 'chromium' })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, locale: 'en-US', timezoneId: 'UTC' })
  await context.addInitScript(({ projectId, audience }) => {
    localStorage.setItem('clipforge.onboarded', '1')
    localStorage.setItem(`clipforge.audience.v1.${projectId}`, JSON.stringify(audience))
  }, { projectId: project.id, audience })
  const page = await context.newPage()
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api', '')
    const json = (body: unknown) => route.fulfill({ json: body })
    if (path.endsWith('/file')) {
      const match = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range || '')
      if (!match) return route.fulfill({ contentType: 'video/mp4', body: media, headers: { 'accept-ranges': 'bytes' } })
      const start = Number(match[1]); const end = Math.min(Number(match[2] || media.length - 1), media.length - 1)
      return route.fulfill({ status: 206, contentType: 'video/mp4', body: media.subarray(start, end + 1),
        headers: { 'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${media.length}` } })
    }
    if (path === '/status') return json({ ...metadata, dataDir: '/demo', hasFfmpeg: true,
      hasFfprobe: true, hasCustomProvider: true, hasYtdlp: true, llmProvider: 'custom' })
    if (path === '/projects') return json([project, { ...project, id: 'demo-launch', name: 'Launch-day lessons', status: 'imported' },
      { ...project, id: 'demo-guide', name: 'A practical product walkthrough', status: 'transcribed' }])
    if (path === '/render-settings') return json(fallbackRenderContract)
    if (path === `/projects/${project.id}`) return json({ project, transcript, candidates, clips,
      jobs: { transcription: { status: 'done' }, analysis: { status: 'done' } } })
    if (path.endsWith('/render-settings')) return json(settings)
    if (path.endsWith('/probe')) return json(metadata)
    if (path.endsWith('/preview-frame')) return route.fulfill({ contentType: 'image/jpeg', body: previewImage })
    if (path.endsWith('/ai-edit/recommendations')) return json(recommendation)
    if (path.endsWith('/filter-suggestions')) return json([])
    if (path.endsWith('/transcribe/status') || path.endsWith('/analyze/status')) return json({ status: 'done' })
    if (path === '/provider-profiles') return json({ profiles: [], active: {}, fallback: {} })
    if (path === '/settings') return json({ llmProvider: 'custom', transcriptionMode: 'local',
      whisperModel: 'base', captionStyle: 'classic', dataDir: '/demo', customBaseUrl: '', customModel: 'Demo editorial model' })
    return route.fulfill({ status: 404, json: { error: 'No demo endpoint' } })
  })
  const save = async (name: string, height?: number) => {
    await page.screenshot({ path: resolve(images, name), fullPage: !height, animations: 'disabled',
      ...(height ? { clip: { x: 0, y: 0, width: 1600, height } } : {}) })
    console.log(`Captured docs/images/${name}`)
  }
  await page.goto(`http://127.0.0.1:${port}`)
  await page.getByLabel('Color theme').selectOption('nord')
  await page.getByRole('button', { name: `Open project ${project.name}` }).waitFor()
  await save('library-nord.png', 820)
  await page.getByRole('button', { name: `Open project ${project.name}` }).click()
  await page.getByLabel('Color theme').selectOption('dark')
  await page.getByRole('heading', { name: 'Clip candidates', exact: true }).waitFor()
  const settleVideo = async () => {
    await page.waitForFunction(() => Array.from(document.querySelectorAll('video'))
      .filter((video) => video.offsetParent !== null).every((video) => video.readyState >= 4 && !video.seeking))
    await page.locator('video').evaluateAll(async (videos) => {
      await Promise.all(videos.filter((video) => video.offsetParent !== null).map(async (video) => {
        video.muted = true
        await video.play()
        video.pause()
      }))
    })
    await page.waitForTimeout(1000)
  }
  await settleVideo()
  await page.getByText('Audience fit & source evidence', { exact: true }).first().click()
  await save('workspace-dark.png')
  await page.getByRole('button', { name: 'AI Edit', exact: true }).click()
  await page.getByRole('button', { name: 'Get visual recommendation', exact: true }).click()
  await page.getByRole('region', { name: 'AI recommendation' }).getByText('Demo editorial model', { exact: false }).waitFor()
  await page.setViewportSize({ width: 1600, height: 1440 })
  await settleVideo()
  await save('ai-edit-dark.png')
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await page.getByLabel('Color theme').selectOption('light')
  await page.setViewportSize({ width: 1600, height: 1100 })
  await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click()
  await page.getByRole('dialog', { name: 'Caption Controls & video filters' }).waitFor()
  await settleVideo()
  await save('caption-studio-light.png')
  await context.close()
} finally {
  await browser?.close()
  if (preview.pid) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(preview.pid), '/t', '/f'], { stdio: 'ignore' })
    else { try { process.kill(-preview.pid, 'SIGTERM') } catch { /* Preview already exited. */ } }
  }
}
