import { useState } from 'react'
import { CircleHelp, X } from 'lucide-react'

const steps = [
  ['Import', 'Create a project from video or audio.'],
  ['Transcribe', 'Turn speech into a reviewable transcript.'],
  ['Analyze', 'Find and rank clip candidates.'],
  ['Select', 'Check the moments you want to keep.'],
  ['Render', 'Create captioned MP4 files with your controls.'],
  ['Download', 'Save completed files to your device.'],
]

export default function WorkflowGuide({ context, step, action, onAction, disabled = false, note }: {
  context: 'dashboard' | 'workspace'; step: number; action: string; onAction: () => void; disabled?: boolean; note: string
}) {
  const key = `clipforge.guide.${context}`
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(key) !== 'dismissed' } catch { return true } })
  const dismiss = () => {
    setOpen(false)
    try { localStorage.setItem(key, 'dismissed') } catch { /* Session-only preference. */ }
  }
  return <section className="workflow-guide" aria-label="Workflow guide">
    <div className="section-heading"><strong><CircleHelp size={17} /> {open ? 'From source to a finished clip' : 'Next step'}</strong><button className="text-button" onClick={open ? dismiss : () => setOpen(true)}>{open ? <><X size={15} />Hide guide</> : 'Show workflow guide'}</button></div>
    {open && <ol>{steps.map(([label, description], index) => <li key={label} aria-current={index === step ? 'step' : undefined}><strong>{index + 1}. {label}</strong>{description}</li>)}</ol>}
    <div className="guide-action"><p className="muted">{note}</p><button className="btn-secondary" disabled={disabled} onClick={onAction}>{action}</button></div>
  </section>
}
