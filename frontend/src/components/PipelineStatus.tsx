import { CheckCircle2, Circle } from 'lucide-react'

export type PipelineStage = 'transcribing' | 'analyzing' | 'rendering' | null
export default function PipelineStatus({ stage, hasTranscript, hasCandidates, hasClips, hasSelection }: {
  stage: PipelineStage; hasTranscript: boolean; hasCandidates: boolean; hasClips: boolean; hasSelection: boolean
}) {
  const steps = [
    { label: 'Import', complete: true, active: false },
    { label: 'Transcribe', complete: hasTranscript, active: stage === 'transcribing' },
    { label: 'Analyze', complete: hasCandidates, active: stage === 'analyzing' },
    { label: 'Select', complete: hasSelection, active: false },
    { label: 'Render', complete: hasClips, active: stage === 'rendering' },
    { label: 'Download', complete: false, active: hasClips && !stage },
  ]
  return <nav className="pipeline" aria-label="Project progress"><ol>{steps.map((step, index) => <li key={step.label} className={step.active ? 'active' : step.complete ? 'complete' : ''} aria-current={step.active ? 'step' : undefined}>{step.active && stage ? <span className="spinner" /> : step.complete ? <CheckCircle2 size={13} /> : <Circle size={12} />}<span>{index + 1} {step.label}</span><span className="sr-only">{step.label === 'Download' && hasClips ? ' available' : step.active ? ' in progress' : step.complete ? ' complete' : ' not started'}</span></li>)}</ol><span className="pipeline-note" role="status">{stage ? 'Processing on the server · safe to leave this project' : hasClips ? 'MP4 files ready to download' : 'Review at your own pace'}</span></nav>
}
