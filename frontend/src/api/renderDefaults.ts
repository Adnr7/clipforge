import type { RenderSettingsContract, VideoFilters } from './client'

const fallbackPresets: RenderSettingsContract['caption']['presets'] = [
  { id: 'classic', label: 'Classic', settings: { preset: 'classic', placement: 'bottom', fontSize: 48, fontColor: 'white', outlineWidth: 3, outlineColor: 'black', shadowEnabled: false, shadowColor: 'black', shadowX: 2, shadowY: 2, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
  { id: 'neon', label: 'Neon', settings: { preset: 'neon', placement: 'bottom', fontSize: 52, fontColor: '#00ff88', outlineWidth: 2, outlineColor: '#003322', shadowEnabled: false, shadowColor: 'black', shadowX: 0, shadowY: 0, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
  { id: 'minimal', label: 'Minimal', settings: { preset: 'minimal', placement: 'bottom', fontSize: 40, fontColor: '#cccccc', outlineWidth: 0, outlineColor: 'black', shadowEnabled: false, shadowColor: 'black', shadowX: 1, shadowY: 1, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
  { id: 'bold', label: 'Bold', settings: { preset: 'bold', placement: 'bottom', fontSize: 60, fontColor: 'yellow', outlineWidth: 4, outlineColor: 'black', shadowEnabled: false, shadowColor: 'black', shadowX: 3, shadowY: 3, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
  { id: 'typewriter', label: 'Typewriter', settings: { preset: 'typewriter', placement: 'bottom', fontSize: 44, fontColor: '#f5f5f5', outlineWidth: 2, outlineColor: '#202020', shadowEnabled: false, shadowColor: 'black', shadowX: 1, shadowY: 1, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
  { id: 'sunset', label: 'Sunset', settings: { preset: 'sunset', placement: 'bottom', fontSize: 54, fontColor: '#ffb347', outlineWidth: 3, outlineColor: '#4a1d35', shadowEnabled: false, shadowColor: 'black', shadowX: 2, shadowY: 2, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
  { id: 'mono', label: 'Mono', settings: { preset: 'mono', placement: 'bottom', fontSize: 42, fontColor: '#e6e6e6', outlineWidth: 1, outlineColor: '#333333', shadowEnabled: false, shadowColor: 'black', shadowX: 1, shadowY: 1, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
  { id: 'bubble', label: 'Bubble', settings: { preset: 'bubble', placement: 'bottom', fontSize: 50, fontColor: 'white', outlineWidth: 2, outlineColor: '#5b21b6', shadowEnabled: false, shadowColor: 'black', shadowX: 2, shadowY: 2, backgroundEnabled: false, backgroundColor: 'black', backgroundOpacity: .65, backgroundPadding: 12, chunkMode: 'chunk', wordsPerChunk: 2, maxGapSeconds: 1.2, splitOnSpeaker: true } },
]

const fallbackFilters: VideoFilters = { brightness: 0, contrast: 1, saturation: 1, blur: 0, sharpen: 0 }
const fallbackRanges: RenderSettingsContract['videoFilters']['ranges'] = {
  brightness: { min: -1, max: 1 }, contrast: { min: 0, max: 4 }, saturation: { min: 0, max: 4 }, blur: { min: 0, max: 10 }, sharpen: { min: 0, max: 2 },
}

export const fallbackRenderContract: RenderSettingsContract = {
  output: {
    defaults: { mode: 'source', aspectRatio: 'source', fit: 'contain', maxDimension: 1920 },
    legacyDefaults: { mode: 'manual', aspectRatio: '9:16', fit: 'crop', maxDimension: 1920 },
    modes: ['source', 'manual', 'ai'], aspectRatios: ['source', '16:9', '9:16', '1:1', '4:5', '4:3', '3:2'], fits: ['contain', 'crop'],
    maxDimension: { min: 64, max: 3840 },
    fitBehavior: 'Fit keeps the whole image with black bars if needed. Crop fills the frame by trimming the center. Neither stretches video.',
    sourceBehavior: 'Source preserves the displayed ratio, never upscales, and rounds dimensions to even pixels. Audio-only sources use a portrait black canvas.',
  },
  caption: { presets: fallbackPresets, placements: ['top', 'center', 'bottom'], chunkModes: ['chunk', 'word'], ranges: { fontSize: { min: 12, max: 160 }, outlineWidth: { min: 0, max: 16 }, shadowX: { min: 0, max: 32 }, shadowY: { min: 0, max: 32 }, backgroundPadding: { min: 0, max: 64 }, wordsPerChunk: { min: 1, max: 8 }, maxGapSeconds: { min: 0, max: 10 }, backgroundOpacity: { min: 0, max: 1 } } },
  videoFilters: { defaults: fallbackFilters, ranges: fallbackRanges, manualOnly: true },
}
