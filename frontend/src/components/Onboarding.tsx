import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, Clapperboard, Cloud, HardDrive } from 'lucide-react'
import { api, errorMessage, type TranscriptionMode } from '../api/client'
import { enteredKeys } from '../api/providers'
import { useEnvironment } from '../hooks/useEnvironment'
import { useToast } from '../hooks/useToast'
import ThemeSelect from './ui/ThemeSelect'

const steps = ['Welcome', 'Processing', 'Configure', 'Ready']
export default function Onboarding({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState(0)
  const [mode, setMode] = useState<TranscriptionMode>('cloud')
  const [transcription, setTranscription] = useState<TranscriptionMode>('cloud')
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)
  const { status } = useEnvironment()
  const { showToast } = useToast()
  const headingRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => { headingRef.current?.focus() }, [step])

  const save = async (test = false) => {
    if (busy) return
    setBusy(true); setError(null); setTestResult(null)
    try {
      await api.updateSettings({ LLM_PROVIDER: mode === 'local' ? 'ollama' : 'deepseek', TRANSCRIPTION_MODE: mode === 'local' ? 'local' : transcription, ...enteredKeys(keys) })
      setKeys({})
      showToast('success', 'Setup preferences and entered keys saved')
      if (test) {
        const providers = mode === 'local' ? ['ollama'] : ['deepseek']
        const results = await Promise.all(providers.map(async (provider) => {
          const result = await api.testConnection(provider)
          return { provider, ...result }
        }))
        const message = results.map((result) => `${result.provider}: ${result.message}`).join(' · ')
        setTestResult(message)
        showToast(results.every((result) => result.ok) ? 'success' : 'error', message)
      } else setStep(3)
    } catch (error) { setError(errorMessage(error)); showToast('error', errorMessage(error)) }
    finally { setBusy(false) }
  }

  return <main className="onboarding">
    <header className="onboarding-header"><div className="brand"><span className="brand-mark"><Clapperboard size={21} /></span><strong>ClipForge</strong></div><ThemeSelect /></header>
    <div className="onboarding-content">
      <ol className="setup-steps" aria-label="Setup progress">{steps.map((label, index) => <li key={label} className={index === step ? 'active' : index < step ? 'complete' : ''} aria-current={index === step ? 'step' : undefined}><span>{index < step ? <Check size={14} /> : index + 1}</span>{label}</li>)}</ol>
      <section className="onboarding-card">
        <p className="eyebrow">YOUR EDITING WORKSPACE</p>
        <h1 ref={headingRef} tabIndex={-1}>{['Good stories deserve a shorter cut.', 'Choose how you work.', 'Connect your tools.', 'Your workspace is ready.'][step]}</h1>
        {step === 0 && <div className="stack"><p className="intro-copy">Find the moments worth sharing. Import your media, review the transcript, and turn strong ideas into captioned clips.</p><div className="setup-summary"><span>01 / Import media</span><span>02 / Find moments</span><span>03 / Export clips</span></div><button className="btn-accent align-start" onClick={() => setStep(1)}>Set up ClipForge <ArrowRight size={16} /></button><button className="text-button align-start" onClick={onComplete}>Go to projects — configure later</button></div>}
        {step === 1 && <div className="stack"><p className="muted">Choose a starting point. You can change individual providers in Settings.</p><fieldset className="stack compact"><legend className="sr-only">Processing mode</legend>{(['cloud', 'local'] as const).map((value) => <label key={value} className={`mode-card ${mode === value ? 'selected' : ''}`}><input type="radio" name="mode" value={value} checked={mode === value} onChange={() => { setMode(value); setTranscription(value) }} />{value === 'cloud' ? <Cloud size={24} /> : <HardDrive size={24} />}<span><strong>{value === 'cloud' ? 'Cloud assisted' : 'Local processing'}</strong><span>{value === 'cloud' ? 'DeepSeek analyzes transcripts. Deepgram transcribes audio, or use local Whisper.' : 'Ollama analyzes transcripts and Whisper transcribes audio on your server.'}</span></span></label>)}</fieldset><div className="form-actions"><button className="btn-secondary" onClick={() => setStep(0)}><ArrowLeft size={15} />Back</button><button className="btn-accent" onClick={() => setStep(2)}>Continue<ArrowRight size={15} /></button></div></div>}
        {step === 2 && <form className="stack" onSubmit={(event) => { event.preventDefault(); void save() }}>
          <fieldset disabled={busy} className="stack">
            {mode === 'cloud' ? <><p className="muted">Entered keys are saved on your server. Leave a field blank to keep an existing key; you can add keys later in Settings.</p><label className="field">DeepSeek API key<input className="input-field" type="password" autoComplete="new-password" spellCheck={false} placeholder={status?.hasDeepseekKey ? 'Key saved · enter to replace' : 'Analysis API key'} value={keys.DEEPSEEK_API_KEY || ''} onChange={(event) => setKeys((previous) => ({ ...previous, DEEPSEEK_API_KEY: event.target.value }))} /></label><label className="field">Transcription<select className="input-field" value={transcription} onChange={(event) => setTranscription(event.target.value as TranscriptionMode)}><option value="cloud">Cloud · Deepgram</option><option value="local">Local · Whisper</option></select></label><label className="field">Deepgram API key<input className="input-field" type="password" autoComplete="new-password" spellCheck={false} placeholder={status?.hasDeepgramKey ? 'Key saved · enter to replace' : 'Transcription API key (optional for local mode)'} value={keys.DEEPGRAM_API_KEY || ''} onChange={(event) => setKeys((previous) => ({ ...previous, DEEPGRAM_API_KEY: event.target.value }))} /></label><p className="muted">Cloud processing sends audio or transcripts to the selected provider.</p></> : <div className="setup-instructions"><h3>Local tools</h3><p>Start Ollama and download its default model:</p><code>ollama serve</code><code>ollama pull llama3.2</code><p>Install Whisper in the backend Python environment:</p><code>pip install openai-whisper</code><p className="muted">The first Whisper run may download its model. Both tools run on the ClipForge server.</p></div>}
            {status && (!status.hasFfmpeg || !status.hasFfprobe) && <div className="notice notice-warning">Install FFmpeg and FFprobe on the server before importing or rendering media.</div>}
            <button type="button" className="btn-secondary align-start" onClick={() => void save(true)}>{busy && <span className="spinner" />}Save & test analysis connection</button>
            {testResult && <p className="muted" role="status">{testResult}</p>}
            {error && <p className="notice notice-error" role="alert">{error}</p>}
            <div className="form-actions"><button type="button" className="btn-secondary" onClick={() => setStep(1)}><ArrowLeft size={15} />Back</button><button className="btn-accent">{busy ? <span className="spinner" /> : <Check size={16} />}Save & continue</button></div>
          </fieldset>
        </form>}
        {step === 3 && <div className="stack"><div className="success-mark"><Check size={28} /></div><p className="intro-copy">Your {mode === 'local' ? 'local' : 'cloud-assisted'} preferences are saved. Start with a video or audio file and build your first set of clips.</p><button className="btn-accent align-start" onClick={onComplete}>Open projects <ArrowRight size={16} /></button></div>}
      </section>
      <p className="onboarding-footer">ClipForge / A focused space for the final cut.</p>
    </div>
  </main>
}
