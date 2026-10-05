import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Eye, Lightbulb, RotateCcw } from 'lucide-react'
import { api, errorMessage, type AspectRatio, type CaptionSettings, type FilterSuggestion, type MediaProbe, type OutputSettings, type RenderSettingsContract, type TranscriptData, type VideoFilters } from '../api/client'
import { fallbackRenderContract } from '../api/renderDefaults'
import { formatTime } from '../api/format'
import '../styles/editor-controls.css'

const fonts = { default: 'Arial, sans-serif', sans: 'Arial, sans-serif', serif: 'Georgia, serif', mono: 'monospace', display: 'Impact, sans-serif' }
function colorHex(color: string) {
  const named: Record<string, string> = { white: '#ffffff', black: '#000000', yellow: '#ffff00', blue: '#0000ff', red: '#ff0000', green: '#008000', transparent: '#000000' }
  return color.startsWith('#') ? color.slice(0, 7) : named[color] || '#808080'
}
function rgba(color: string, opacity: number) {
  const hex = colorHex(color)
  return `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${opacity})`
}

export default function RenderControls({ projectId, initialTime, transcript, disabled = false, contract, captionSettings, videoFilters, outputSettings: output, sourceMetadata, onCaptionChange, onFiltersChange, onOutputChange, suggestions, suggestionsLoading, suggestionsStatus, onReviewSuggestions, onGenerateSuggestions }: {
  projectId: string; initialTime: number; transcript: TranscriptData | null
  disabled?: boolean
  contract: RenderSettingsContract | null; captionSettings: CaptionSettings; videoFilters: VideoFilters; outputSettings: OutputSettings
  sourceMetadata?: MediaProbe | null
  onCaptionChange: (settings: CaptionSettings) => void; onFiltersChange: (filters: VideoFilters) => void; onOutputChange: (settings: OutputSettings) => void
  suggestions: FilterSuggestion[]; suggestionsLoading: boolean; suggestionsStatus: string | null
  onReviewSuggestions: () => void; onGenerateSuggestions: (brief: string) => void
}) {
  const schema = contract || fallbackRenderContract
  const outputSchema = schema.output || fallbackRenderContract.output
  const [probedSource, setProbedSource] = useState<MediaProbe | null>(null)
  const [browserSize, setBrowserSize] = useState<{ width: number; height: number } | null>(null)
  const [tab, setTab] = useState<'captions' | 'filters'>('captions')
  const [time, setTime] = useState(initialTime)
  const [brief, setBrief] = useState('')
  const [safeArea, setSafeArea] = useState(true)
  const [before, setBefore] = useState(false)
  const [frame, setFrame] = useState<{ url: string; settings: string } | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [mediaError, setMediaError] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const frameUrl = useRef<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const signature = JSON.stringify({ captionSettings, videoFilters, outputSettings: output, time })
  const setCaption = (values: Partial<CaptionSettings>) => onCaptionChange({ ...captionSettings, ...values })
  const hasSourceMetadata = !!sourceMetadata
  useEffect(() => () => { controller.current?.abort(); if (frameUrl.current) URL.revokeObjectURL(frameUrl.current) }, [])
  useEffect(() => {
    if (hasSourceMetadata) return
    const abort = new AbortController()
    api.getProject(projectId, abort.signal).then(({ project }) => {
      if (abort.signal.aborted) return
      if (project.source_metadata_json) setProbedSource(JSON.parse(project.source_metadata_json))
      else return api.probeMedia(projectId, abort.signal).then((metadata) => {
        if (!abort.signal.aborted) setProbedSource(metadata)
      })
    }).catch(() => { /* Browser metadata or the exact FFmpeg preview can still provide the geometry. */ })
    return () => abort.abort()
  }, [projectId, hasSourceMetadata])
  const metadata = sourceMetadata || probedSource
  const sourceSize = (() => {
    if (metadata?.has_video === false) return null
    const width = metadata?.display_width ?? metadata?.width
    const height = metadata?.display_height ?? metadata?.height
    return width && height && Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
      ? { width, height } : browserSize
  })()
  const changeOutput = (value: OutputSettings) => onOutputChange(value)
  const dimensions = (() => {
    if (output.aspectRatio === 'source' && sourceSize) {
      const scale = Math.min(1, output.maxDimension / Math.max(sourceSize.width, sourceSize.height))
      return { width: Math.max(2, Math.floor(sourceSize.width * scale / 2) * 2), height: Math.max(2, Math.floor(sourceSize.height * scale / 2) * 2) }
    }
    if (output.aspectRatio === 'source' && metadata?.has_video !== false) return null
    const [x, y] = (output.aspectRatio === 'source' ? '9:16' : output.aspectRatio).split(':').map(Number)
    const unit = Math.floor(output.maxDimension / (2 * Math.max(x, y))) * 2
    return { width: x * unit, height: y * unit }
  })()
  const captionScale = (dimensions?.width || 1080) / 100

  const exactPreview = async () => {
    if (previewBusy || disabled) return
    videoRef.current?.pause()
    const abort = new AbortController()
    controller.current = abort
    setPreviewBusy(true); setPreviewError(null)
    try {
      const blob = await api.previewFrame(projectId, captionSettings, videoFilters, time, abort.signal, output)
      if (abort.signal.aborted) return
      if (frameUrl.current) URL.revokeObjectURL(frameUrl.current)
      const url = URL.createObjectURL(blob)
      frameUrl.current = url; setFrame({ url, settings: signature })
    } catch (error) { if (!abort.signal.aborted) setPreviewError(errorMessage(error)) }
    finally { if (!abort.signal.aborted) setPreviewBusy(false) }
  }
  const activeWord = transcript?.words.find((word) => word.start <= time && word.end > time)
  const sample = activeWord ? activeWord.text : captionSettings.chunkMode === 'word' ? 'Create' : 'Create something worth sharing'.split(' ').slice(0, captionSettings.wordsPerChunk).join(' ')
  const position = captionSettings.verticalPosition ?? ({ top: 12.5, center: 50, bottom: 75 }[captionSettings.placement])
  const textStyle: CSSProperties = {
    position: 'absolute', top: `${position}%`, left: '6%', width: '88%', textAlign: 'center',
    transform: captionSettings.placement === 'center' && captionSettings.verticalPosition == null ? 'translateY(-50%)' : undefined,
    fontFamily: fonts[captionSettings.fontFamily || 'default'], fontSize: `${captionSettings.fontSize / captionScale}cqw`,
    color: captionSettings.fontColor, lineHeight: 1.25, textTransform: captionSettings.uppercase ? 'uppercase' : undefined,
    WebkitTextStroke: `${captionSettings.outlineWidth / captionScale}cqw ${captionSettings.outlineColor}`, paintOrder: 'stroke fill',
    textShadow: captionSettings.shadowEnabled ? `${captionSettings.shadowX / captionScale}cqw ${captionSettings.shadowY / captionScale}cqw 0 ${captionSettings.shadowColor}` : undefined,
  }
  const captionRange = (key: keyof CaptionSettings, label: string, step = 1) => {
    const range = schema.caption.ranges[key]
    if (!range) return null
    return <label className="field">{label} <output>{String(captionSettings[key] ?? 0)}</output><input aria-label={label} type="range" min={range.min} max={range.max} step={step} value={Number(captionSettings[key] ?? 0)} onChange={(event) => setCaption({ [key]: Number(event.target.value) })} /></label>
  }
  const colorInput = (key: 'fontColor' | 'outlineColor' | 'shadowColor' | 'backgroundColor', label: string) => <label className="field">{label}<input className="color-input" type="color" value={colorHex(captionSettings[key])} onChange={(event) => setCaption({ [key]: event.target.value })} /></label>
  return <div className="editor-controls">
    <aside className="editor-preview stack compact">
      <div className="section-heading"><h3>On your video</h3><span className="badge badge-info">{output.aspectRatio === 'source' ? 'Source' : output.aspectRatio} · {dimensions ? `${dimensions.width}×${dimensions.height}` : 'detecting source size'}{output.aspectRatio === 'source' && metadata?.has_video === false ? ' · audio canvas' : ''}{output.mode === 'ai' ? ' · AI choice' : ''}</span></div>
      <div className="editor-preview-canvas" style={{ aspectRatio: dimensions ? `${dimensions.width} / ${dimensions.height}` : '1 / 1', maxWidth: dimensions ? `${Math.min(360, 500 * dimensions.width / dimensions.height)}px` : '360px' }}>
        <video ref={videoRef} aria-label="Caption source preview" src={api.mediaFileUrl(projectId)} playsInline preload="metadata" style={{ objectFit: output.fit === 'crop' ? 'cover' : 'contain', filter: before ? undefined : `brightness(${Math.max(0, 1 + videoFilters.brightness)}) contrast(${videoFilters.contrast}) saturate(${videoFilters.saturation}) blur(${videoFilters.blur / 3}px)` }} onLoadedMetadata={(event) => { const video = event.currentTarget; if (video.videoWidth > 0 && video.videoHeight > 0) setBrowserSize({ width: video.videoWidth, height: video.videoHeight }); if (Number.isFinite(video.duration)) video.currentTime = Math.min(initialTime, Math.max(0, video.duration - .1)) }} onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)} onError={() => setMediaError(true)} />
        {captionSettings.enabled !== false && !before && <div style={textStyle}><span style={{ background: captionSettings.backgroundEnabled ? rgba(captionSettings.backgroundColor, captionSettings.backgroundOpacity) : undefined, padding: captionSettings.backgroundEnabled ? `${captionSettings.backgroundPadding / captionScale}cqw` : undefined }}>{sample}</span></div>}
         {safeArea && <div className="editor-safe-guide" aria-label="Generic safe area guide" />}
        {frame && <img className="editor-exact-frame" src={frame.url} alt="FFmpeg rendered frame with current caption and filter settings" />}
      </div>
      {frame ? <><p className="muted">Rendered frame {frame.settings === signature ? '· current settings' : '· settings or time changed; refresh preview'}</p><button className="btn-secondary" onClick={() => setFrame(null)}>Back to live video</button></> : <><div className="inline wrap"><button className="btn-secondary" onClick={() => { const video = videoRef.current; if (!video) return; if (video.paused) void video.play().catch(() => setMediaError(true)); else video.pause() }}>Play / pause</button><span className="mono">{formatTime(time)}</span><button className="btn-secondary" aria-pressed={before} onClick={() => setBefore(!before)}>Original</button></div><p className="muted">Live approximation{!activeWord ? ' · sample caption at silence' : ' · current word'}. Render a frame to check exact font, wrapping, placement and filters.</p></>}
      {mediaError && <p className="notice notice-warning">Source playback unavailable. Try rendering a frame; FFmpeg can preview formats your browser cannot play.</p>}
      <label className="checkbox-field"><input type="checkbox" checked={safeArea} onChange={(event) => setSafeArea(event.target.checked)} />Show safe-area guide</label>
      <p className="field-hint">Generic guide: leave space for social-player controls. Platform overlays vary.</p>
      <button className="btn-accent" disabled={previewBusy || disabled} onClick={() => void exactPreview()}>{previewBusy ? <span className="spinner" /> : <Eye size={16} />}{previewBusy ? 'Rendering preview…' : 'Render exact preview frame'}</button>
      {previewError && <p className="notice notice-error" role="alert">{previewError}</p>}
    </aside>
     <fieldset className="editor-options" disabled={disabled}>
      <section className="editor-output stack compact" aria-label="Output format">
        <h3>Output format</h3>
        <div className="form-grid">
          <label className="field">Aspect ratio<select className="input-field" value={output.mode === 'ai' ? 'ai' : output.aspectRatio} onChange={(event) => {
            const ratio = event.target.value
            if (ratio === 'ai') { setTab('filters'); onGenerateSuggestions(`${brief.slice(0, 1800)}\nSuggest outputSettings with an explicit aspectRatio and fit for this clip. Prefer preserving the source unless a platform format is justified.`); return }
            changeOutput({ ...output, mode: ratio === 'source' ? 'source' : 'manual', aspectRatio: ratio as AspectRatio })
          }}>{outputSchema.aspectRatios.map((ratio) => <option key={ratio} value={ratio}>{ratio === 'source' ? 'Original / source (default for new imports)' : ratio}</option>)}<option value="ai" disabled={!transcript || suggestionsLoading}>AI-suggested{output.mode === 'ai' ? ` (${output.aspectRatio})` : ' — ask your provider'}</option></select></label>
          <label className="field">Framing<select className="input-field" value={output.fit} onChange={(event) => changeOutput({ ...output, fit: event.target.value as OutputSettings['fit'] })}><option value="contain">Fit (whole image)</option><option value="crop">Center crop (fill frame)</option></select></label>
          <label className="field">Maximum output edge<select aria-label="Maximum output edge" aria-describedby="output-edge-hint" className="input-field" value={output.maxDimension} onChange={(event) => changeOutput({ ...output, maxDimension: Number(event.target.value) })}>{[720, 1280, 1920, 2560, 3840].map((size) => <option key={size} value={size}>{size} px</option>)}{![720, 1280, 1920, 2560, 3840].includes(output.maxDimension) && <option value={output.maxDimension}>{output.maxDimension} px</option>}</select><span id="output-edge-hint" className="field-hint">Source mode keeps the original resolution up to this limit.</span></label>
        </div>
        <p className="field-hint">{outputSchema.fitBehavior} {outputSchema.sourceBehavior}</p>
        <p className="field-hint">The preview and your next render use these output settings. Save project settings to keep them for this project.</p>
      </section>
      <nav className="source-options" aria-label="Editing controls"><button aria-pressed={tab === 'captions'} className={tab === 'captions' ? 'selected' : ''} onClick={() => setTab('captions')}>Captions</button><button aria-pressed={tab === 'filters'} className={tab === 'filters' ? 'selected' : ''} onClick={() => setTab('filters')}>Video & AI suggestions</button></nav>
       {tab === 'captions' ? <div className="stack">
        <label className="checkbox-field"><input type="checkbox" checked={captionSettings.enabled !== false} onChange={(event) => setCaption({ enabled: event.target.checked })} />Burn captions into the video</label>
       <div className="preset-grid" aria-label="Caption presets">{schema.caption.presets.map((preset) => <button key={preset.id} className={`preset-card ${captionSettings.preset === preset.id ? 'selected' : ''}`} aria-label={`${preset.label} caption preset`} aria-pressed={captionSettings.preset === preset.id} onClick={() => onCaptionChange({ ...preset.settings, enabled: captionSettings.enabled, placement: captionSettings.placement, verticalPosition: captionSettings.verticalPosition })}><span className="preset-mini" style={{ color: preset.settings.fontColor, fontFamily: fonts[preset.settings.fontFamily || 'default'], textShadow: `1px 1px ${preset.settings.outlineColor}` }}>Your words</span><strong>{preset.label}</strong></button>)}</div>
        <div className="form-grid">
          <label className="field">Placement<select className="input-field" value={captionSettings.placement} onChange={(event) => setCaption({ placement: event.target.value as CaptionSettings['placement'], verticalPosition: null })}><option value="top">Top</option><option value="center">Center</option><option value="bottom">Lower third</option></select></label>
          <label className="field">Font family<select className="input-field" value={captionSettings.fontFamily || 'default'} onChange={(event) => setCaption({ fontFamily: event.target.value as CaptionSettings['fontFamily'] })}>{Object.keys(fonts).map((font) => <option value={font} key={font}>{font === 'default' ? 'System default' : font}</option>)}</select></label>
          <label className="field">Vertical position <output>{position}%</output><input aria-label="Vertical position" type="range" min="10" max="85" value={position} step=".5" onChange={(event) => setCaption({ verticalPosition: Number(event.target.value) })} /></label>
          {captionRange('fontSize', 'Font size')}
          {colorInput('fontColor', 'Text color')}{colorInput('outlineColor', 'Outline color')}
          {captionRange('outlineWidth', 'Outline width')}
          <label className="checkbox-field"><input type="checkbox" checked={!!captionSettings.uppercase} onChange={(event) => setCaption({ uppercase: event.target.checked })} />UPPERCASE</label>
        </div>
        <details className="editor-disclosure" open><summary>Background & shadow</summary><div className="stack compact">
          <label className="checkbox-field"><input type="checkbox" checked={captionSettings.backgroundEnabled} onChange={(event) => setCaption({ backgroundEnabled: event.target.checked })} />Caption background</label>
          {captionSettings.backgroundEnabled && <div className="form-grid">{colorInput('backgroundColor', 'Background color')}{captionRange('backgroundOpacity', 'Background opacity', .05)}{captionRange('backgroundPadding', 'Background padding')}</div>}
          <label className="checkbox-field"><input type="checkbox" checked={captionSettings.shadowEnabled} onChange={(event) => setCaption({ shadowEnabled: event.target.checked })} />Text shadow</label>
          {captionSettings.shadowEnabled && <div className="form-grid">{colorInput('shadowColor', 'Shadow color')}{captionRange('shadowX', 'Shadow horizontal offset')}{captionRange('shadowY', 'Shadow vertical offset')}</div>}
        </div></details>
        <details className="editor-disclosure"><summary>Timing & phrase length</summary><div className="form-grid"><label className="field">Caption pacing<select className="input-field" value={captionSettings.chunkMode} onChange={(event) => setCaption({ chunkMode: event.target.value as CaptionSettings['chunkMode'] })}><option value="chunk">Phrase chunks</option><option value="word">One word at a time</option></select></label>{captionSettings.chunkMode === 'chunk' && captionRange('wordsPerChunk', 'Words per chunk')}{captionRange('maxGapSeconds', 'Split after silence (seconds)', .1)}<label className="checkbox-field"><input type="checkbox" checked={captionSettings.splitOnSpeaker} onChange={(event) => setCaption({ splitOnSpeaker: event.target.checked })} />Split on speaker change</label></div></details>
      </div> : <div className="stack">
        <div><h3>Video adjustments</h3><p className="muted">Filters change the picture; captions keep their original colors.</p></div>
        <div className="filter-presets"><button className="btn-secondary" onClick={() => onFiltersChange({ ...schema.videoFilters.defaults })}><RotateCcw size={15} />Neutral</button><button className="btn-secondary" onClick={() => onFiltersChange({ brightness: .02, contrast: 1.08, saturation: 1.05, blur: 0, sharpen: .25 })}>Clean</button><button className="btn-secondary" onClick={() => onFiltersChange({ brightness: .03, contrast: 1.12, saturation: 1.25, blur: 0, sharpen: .2 })}>Vivid</button><button className="btn-secondary" onClick={() => onFiltersChange({ brightness: 0, contrast: 1.1, saturation: 0, blur: 0, sharpen: 0 })}>Monochrome</button></div>
        <div className="form-grid">{(Object.keys(videoFilters) as (keyof VideoFilters)[]).map((key) => <label key={key} className="field">{key[0].toUpperCase() + key.slice(1)} <output>{videoFilters[key]}</output><input aria-label={key} type="range" min={schema.videoFilters.ranges[key].min} max={schema.videoFilters.ranges[key].max} step=".05" value={videoFilters[key]} onChange={(event) => onFiltersChange({ ...videoFilters, [key]: Number(event.target.value) })} /></label>)}</div>
        <p className="field-hint">Exact preview includes sharpening and FFmpeg color adjustments. The live video is an approximation.</p>
        <section className="ai-suggestion-box"><h3><Lightbulb size={18} /> Ask your AI for an editing look</h3><p className="muted">Uses your active analysis provider and transcript context. It does not inspect video frames or diagnose exposure. Review the suggestion before applying.</p><label className="field">Editing brief<textarea className="input-field" rows={3} maxLength={2000} placeholder="For example: readable captions and a restrained documentary look" value={brief} onChange={(event) => setBrief(event.target.value)} /></label><div className="inline wrap"><button className="btn-accent" disabled={suggestionsLoading || !transcript} onClick={() => onGenerateSuggestions(brief)}>{suggestionsLoading ? <span className="spinner" /> : <Lightbulb size={16} />}Suggest editing settings</button><button className="btn-secondary" disabled={suggestionsLoading} onClick={onReviewSuggestions}>Saved suggestions</button></div>{!transcript && <p className="field-hint">Transcribe the source first.</p>}{suggestionsStatus && <p role="status" className="muted">{suggestionsStatus}</p>}
          {suggestions.map((suggestion) => <article className="suggestion-item stack compact" key={suggestion.id}><strong>{suggestion.model || 'Editing suggestion'}</strong><p>{suggestion.rationale}</p><small>{suggestion.contextNotice || 'Transcript-based suggestion; no frames analyzed.'}</small><p className="muted">{Object.entries(suggestion.settings).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>{suggestion.outputSettings && <p className="muted">Suggested output: {suggestion.outputSettings.aspectRatio} · {suggestion.outputSettings.fit === 'contain' ? 'Fit' : 'Center crop'}</p>}<button className="btn-secondary" onClick={() => { onFiltersChange(suggestion.settings); if (suggestion.captionSettings) onCaptionChange(suggestion.captionSettings); if (suggestion.outputSettings) changeOutput({ ...suggestion.outputSettings, mode: 'ai' }) }}>Apply suggestion to controls</button></article>)}
        </section>
       </div>}
     </fieldset>
  </div>
}
