import { expect, test } from '@playwright/test'
import { api, type OutputSettings } from '../src/api/client'
import { fallbackRenderContract } from '../src/api/renderDefaults'

test('preview API preserves time/signal argument positions and sends the explicit output snapshot', async () => {
  const originalFetch = globalThis.fetch
  const caption = fallbackRenderContract.caption.presets[0].settings
  const filters = fallbackRenderContract.videoFilters.defaults
  const output: OutputSettings = { mode: 'manual', aspectRatio: '4:5', fit: 'crop', maxDimension: 1280 }
  const abort = new AbortController()
  const requests: { url: unknown; options?: RequestInit }[] = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options })
    return new Response(new Blob(['jpeg'], { type: 'image/jpeg' }), { status: 200 })
  }
  try {
    const blob = await api.previewFrame('project', caption, filters, 2.5, abort.signal, output)
    expect(await blob.text()).toBe('jpeg')
    expect(requests[0].url).toBe('/api/projects/project/preview-frame')
    expect(requests[0].options?.signal).toBe(abort.signal)
    expect(requests[0].options?.method).toBe('POST')
    expect(JSON.parse(requests[0].options?.body as string)).toEqual({ captionSettings: caption, videoFilters: filters, outputSettings: output, time: 2.5 })

    await api.previewFrame('project', caption, filters, 3, abort.signal)
    expect(requests[1].options?.signal).toBe(abort.signal)
    expect(JSON.parse(requests[1].options?.body as string)).toEqual({ captionSettings: caption, videoFilters: filters, time: 3 })
  } finally {
    globalThis.fetch = originalFetch
  }
})
