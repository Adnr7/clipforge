import { useState } from 'react'
import { Download } from 'lucide-react'
import { api, type Candidate, type Clip } from '../api/client'
import { formatTime } from '../api/format'
import Modal from './ui/Modal'
import WorkflowStep from './ui/WorkflowStep'

export default function ClipPreview({ clip, candidate, onClose }: { clip: Clip; candidate: Candidate; onClose: () => void }) {
  const [error, setError] = useState(false)
  const clipFileUrl = api.clipFileUrl(clip.id)
  let renderedStyle = 'Original render'
  let outputLabel = 'MP4'
  let dimensions: { width: number; height: number } | null = null
  try {
    const settings = JSON.parse(clip.render_settings_json || 'null')
    if (settings?.caption) renderedStyle = settings.caption.enabled === false ? 'Captions off' : `${settings.caption.preset} · ${settings.caption.fontSize}px · ${settings.caption.placement}`
    const size = settings?.outputDimensions
    if (Number.isFinite(size?.width) && Number.isFinite(size?.height) && size.width > 0 && size.height > 0) dimensions = size
    const output = settings?.outputSettings
    if (output) outputLabel += ` · ${output.aspectRatio === 'source' ? 'Source ratio' : output.aspectRatio} · ${output.fit === 'contain' ? 'Fit' : 'Center crop'}`
    if (dimensions) outputLabel += ` · ${dimensions.width}×${dimensions.height}`
  } catch { /* Old renders use the video's intrinsic geometry. */ }
  return <Modal title={`Preview clip ${candidate.rank}`} onClose={onClose} width={700} closeOnBackdrop={false}>
    <div className="stack"><div><h3>{candidate.hook}</h3><p className="muted">{formatTime(candidate.start_sec)} – {formatTime(candidate.end_sec)} · {formatTime(candidate.end_sec - candidate.start_sec)}</p></div>
      <video className="clip-preview" controls src={clipFileUrl} preload="metadata" playsInline style={{ aspectRatio: dimensions ? `${dimensions.width} / ${dimensions.height}` : undefined, objectFit: 'contain' }} aria-label={`Rendered clip: ${candidate.hook}`} onError={() => setError(true)} />
      {error && <p className="notice notice-error" role="alert">The preview could not load. Try downloading the MP4, or re-render if the file is unavailable.</p>}
      <div className="clip-preview-info"><p className="muted">{outputLabel} · {renderedStyle}<br />Captions and filters are embedded in this file.</p><a className="btn-accent" title="Step 6: Download completed MP4" href={clipFileUrl} download><WorkflowStep step={6} /><Download size={16} />Download MP4</a></div>
    </div>
  </Modal>
}
