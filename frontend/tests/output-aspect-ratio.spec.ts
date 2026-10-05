import { expect, test, type Page } from '@playwright/test'
import type { CaptionSettings, Clip, OutputSettings, VideoFilters } from '../src/api/client'
import { fallbackRenderContract } from '../src/api/renderDefaults'

async function setup(page: Page, legacy = false, completed = false) {
  const caption = fallbackRenderContract.caption.presets[0].settings
  const filters = fallbackRenderContract.videoFilters.defaults
  const state = {
    output: { ...(legacy ? fallbackRenderContract.output.legacyDefaults : fallbackRenderContract.output.defaults) },
    caption: { ...caption } as CaptionSettings, filters: { ...filters } as VideoFilters,
    saves: [] as Record<string, unknown>[], previews: [] as Record<string, unknown>[],
    renders: [] as Record<string, unknown>[], suggestions: [] as Record<string, unknown>[],
    generated: [] as Record<string, unknown>[],
    clips: [] as Clip[],
  }
  if (completed) state.clips.push({ id: 'previous', candidate_id: 'cut', status: 'done', output_path: '/previous.mp4', render_log: null,
    render_settings_json: JSON.stringify({ caption: state.caption, videoFilters: state.filters, outputSettings: state.output, outputDimensions: { width: 1920, height: 1080 } }) })
  const project = () => ({
    id: 'ratio', name: 'Ratio project', source_path: '/video/source.mp4', source_duration: 30,
    status: 'analyzed', transcription_mode: 'local', caption_style: state.caption.preset,
    captionSettings: state.caption, videoFilters: state.filters, outputSettings: state.output,
    source_metadata_json: JSON.stringify({ has_video: true, width: 1920, height: 1080, display_width: 1920, display_height: 1080 }),
    created_at: 'now', updated_at: 'now',
  })
  await page.addInitScript(() => localStorage.setItem('clipforge.onboarded', '1'))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
    const method = route.request().method()
    const body = () => route.request().postDataJSON() as Record<string, unknown>
    if (path === '/status') return route.fulfill({ json: { hasFfmpeg: true, hasFfprobe: true, llmProvider: 'ollama', dataDir: '/data' } })
    if (path === '/settings') return route.fulfill({ json: { llmProvider: 'ollama', transcriptionMode: 'local', whisperModel: 'base', captionStyle: 'classic', dataDir: '/data' } })
    if (path === '/render-settings') return route.fulfill({ json: fallbackRenderContract })
    if (path === '/projects') return route.fulfill({ json: [project()] })
    if (path === '/projects/ratio') return route.fulfill({ json: {
      project: project(), transcript: { id: 'transcript', raw_json: JSON.stringify({ words: [{ text: 'Hello', start: 1, end: 2 }], segments: [{ text: 'Hello', start: 1, end: 2 }] }) },
      candidates: [{ id: 'cut', project_id: 'ratio', start_sec: 1, end_sec: 10, score: 80, hook: 'Test cut', rationale: 'Test', rank: 1, selected: 1 }],
      clips: state.clips, jobs: { transcription: { status: 'done' }, analysis: { status: 'done' } },
    } })
    if (path === '/projects/ratio/render-settings' && method === 'PUT') {
      const value = body(); state.saves.push(value)
      if (value.outputSettings) state.output = value.outputSettings as OutputSettings
      if (value.captionSettings) state.caption = value.captionSettings as CaptionSettings
      if (value.videoFilters) state.filters = value.videoFilters as VideoFilters
      return route.fulfill({ json: { outputSettings: state.output, captionSettings: state.caption, videoFilters: state.filters } })
    }
    if (path === '/projects/ratio/preview-frame') {
      state.previews.push(body())
      return route.fulfill({ contentType: 'image/jpeg', body: Buffer.from('/9j/2Q==', 'base64') })
    }
    if (path === '/projects/ratio/filter-suggestions/generate') {
      state.generated.push(body())
      const suggestion = { id: 'ratio-ai', settings: state.filters, outputSettings: { mode: 'ai', aspectRatio: '4:5', fit: 'contain', maxDimension: 1920 }, rationale: 'A feed format matches your brief.', model: 'test-model', applied: false }
      state.suggestions.push(suggestion)
      return route.fulfill({ status: 201, json: suggestion })
    }
    if (path === '/projects/ratio/filter-suggestions') return route.fulfill({ json: state.suggestions })
    if (path === '/projects/ratio/render/cut' || path === '/projects/ratio/render-batch') {
      state.renders.push({ ...body(), effectiveOutput: state.output })
      return route.fulfill({ status: 202, json: path.endsWith('render-batch') ? { clipIds: ['render'], status: 'started' } : { clipId: 'render', status: 'started' } })
    }
    return route.fulfill({ status: 404, json: { error: 'Fixture media unavailable' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Open project Ratio project' }).click()
  await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click()
  return state
}

test('source default, manual fit/crop, preview, saved reopening and render share the selected output', async ({ page }) => {
  const state = await setup(page)
  const editor = page.getByRole('dialog', { name: 'Caption Controls & video filters' })
  const ratio = editor.getByRole('combobox', { name: 'Aspect ratio', exact: true })
  await expect(ratio).toHaveValue('source')
  await expect(editor.getByText('Source · 1920×1080', { exact: true })).toBeVisible()
  await expect(editor.locator('.editor-preview-canvas')).toHaveCSS('aspect-ratio', '1920 / 1080')
  await ratio.selectOption('1:1')
  await editor.getByRole('combobox', { name: 'Maximum output edge', exact: true }).selectOption('1280')
  await editor.getByRole('combobox', { name: 'Framing', exact: true }).selectOption('crop')
  await expect(editor.locator('.editor-preview-canvas video')).toHaveCSS('object-fit', 'cover')
  await editor.getByRole('button', { name: 'Render exact preview frame', exact: true }).click()
  await expect.poll(() => state.previews.length).toBe(1)
  expect(state.previews[0].outputSettings).toEqual({ mode: 'manual', aspectRatio: '1:1', fit: 'crop', maxDimension: 1280 })
  await expect(editor.getByText('1:1 · 1280×1280', { exact: true })).toBeVisible()
  expect(state.saves).toHaveLength(0)
  await editor.getByRole('button', { name: 'Save project settings', exact: true }).click()
  await expect(editor.getByText('Saved to this project. Existing downloads keep their rendered settings.')).toBeVisible()
  expect(state.output.aspectRatio).toBe('1:1')
  await editor.getByRole('button', { name: 'Done editing controls', exact: true }).click()
  await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click()
  await expect(editor.getByRole('combobox', { name: 'Aspect ratio', exact: true })).toHaveValue('1:1')
  await editor.getByRole('button', { name: 'Done editing controls', exact: true }).click()
  await page.getByRole('button', { name: 'Render clip', exact: true }).click()
  await expect.poll(() => state.renders.length).toBe(1)
  expect(state.renders[0].effectiveOutput).toEqual(state.previews[0].outputSettings)
  expect(state.renders[0].outputSettings).toEqual(state.previews[0].outputSettings)
  expect(state.renders[0].captionSettings).toEqual(state.previews[0].captionSettings)
  expect(state.renders[0].videoFilters).toEqual(state.previews[0].videoFilters)
})

test('changing output ratio keeps the prior download and enables rendering the new settings', async ({ page }) => {
  const state = await setup(page, false, true)
  const editor = page.getByRole('dialog', { name: 'Caption Controls & video filters' })
  await editor.getByRole('button', { name: 'Done editing controls', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Render selected (0)', exact: true })).toBeDisabled()
  await expect(page.getByRole('link', { name: 'Download selected (1)', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Caption Controls & filters', exact: true }).click()
  await editor.getByRole('combobox', { name: 'Aspect ratio', exact: true }).selectOption('4:5')
  await editor.getByRole('button', { name: 'Done editing controls', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Render selected (1)', exact: true })).toBeEnabled()
  await expect(page.getByRole('link', { name: 'Download selected (1)', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Render selected (1)', exact: true }).click()
  await expect.poll(() => state.renders.length).toBe(1)
  expect(state.renders[0].outputSettings).toMatchObject({ aspectRatio: '4:5', fit: 'contain' })
  expect(state.clips[0].id).toBe('previous')
})

test('legacy 9:16 cropping remains selected and AI ratio is reviewed before saving', async ({ page }) => {
  const state = await setup(page, true)
  const editor = page.getByRole('dialog', { name: 'Caption Controls & video filters' })
  const ratio = editor.getByRole('combobox', { name: 'Aspect ratio', exact: true })
  await expect(ratio).toHaveValue('9:16')
  await expect(editor.getByRole('combobox', { name: 'Framing', exact: true })).toHaveValue('crop')
  await ratio.selectOption('ai')
  await expect(editor.getByText('Suggested output: 4:5 · Fit', { exact: true })).toBeVisible()
  expect(state.output.aspectRatio).toBe('9:16')
  expect(state.generated[0].brief).toContain('Suggest outputSettings')
  await editor.getByRole('button', { name: 'Apply suggestion to controls', exact: true }).click()
  await expect(ratio).toHaveValue('ai')
  await expect(editor.getByText('4:5 · 1536×1920 · AI choice', { exact: true })).toBeVisible()
  expect(state.output.aspectRatio).toBe('9:16')
  await editor.getByRole('button', { name: 'Save project settings', exact: true }).click()
  await expect.poll(() => state.output.aspectRatio).toBe('4:5')
  expect(state.output.mode).toBe('ai')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => editor.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
})
