import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, Cpu, KeyRound, Palette, Plus, Save, Server, Subtitles, Trash2, Wifi } from 'lucide-react'
import {
  api,
  errorMessage,
  type ConnectionTestResult,
  type ProviderProfile,
  type ProviderProfilesResponse,
  type Settings,
} from '../api/client'
import { ANALYSIS_PROVIDERS, KEY_PROVIDERS } from '../api/providers'
import { fallbackRenderContract } from '../api/renderDefaults'
import { useEnvironment } from '../hooks/useEnvironment'
import Modal from './ui/Modal'
import ThemeSelect from './ui/ThemeSelect'
import '../styles/settings.css'

type ProfileKind = ProviderProfile['kind']
// Additive metadata remains optional when talking to an older server.
type DisplayProfile = ProviderProfile & { effectiveModel?: string; effectiveBaseUrl?: string }
type ProfileListing = ProviderProfilesResponse & { fallback?: ProviderProfilesResponse['active'] }
type ProfileDraft = {
  id?: string
  name: string
  kind: ProfileKind
  provider: string
  apiKey: string
  baseUrl: string
  model: string
}
type Feedback = { tone: 'success' | 'error' | 'info'; message: string }
type ProviderOption = { id: string; label: string; mark: string; detail: string }

const sections = [
  { id: 'appearance', label: 'Appearance', icon: Palette, description: 'Make your workspace comfortable.' },
  { id: 'providers', label: 'Providers', icon: KeyRound, description: 'Manage connections for analysis and transcription.' },
  { id: 'processing', label: 'Processing', icon: Cpu, description: 'Choose provider fallbacks and defaults for new projects.' },
  { id: 'captions', label: 'Caption defaults', icon: Subtitles, description: 'Choose the starting style for new projects.' },
  { id: 'environment', label: 'Environment', icon: Server, description: 'Check the tools and storage on your ClipForge server.' },
] as const
type Section = typeof sections[number]['id']

const profileProviders: Record<ProfileKind, ProviderOption[]> = {
  llm: [
    { id: 'deepseek', label: 'DeepSeek', mark: 'DS', detail: 'DeepSeek models' },
    { id: 'openai', label: 'OpenAI', mark: 'OA', detail: 'GPT models' },
    { id: 'claude', label: 'Anthropic', mark: 'AN', detail: 'Claude models' },
    { id: 'gemini', label: 'Gemini', mark: 'GE', detail: 'Google models' },
    { id: 'openrouter', label: 'OpenRouter', mark: 'OR', detail: 'Multi-model gateway' },
    { id: 'groq', label: 'Groq', mark: 'GQ', detail: 'Hosted inference' },
    { id: 'ollama', label: 'Ollama', mark: 'OL', detail: 'Local inference' },
    { id: 'custom', label: 'Custom OpenAI-compatible', mark: '+', detail: 'Your own endpoint' },
  ],
  transcription: [
    { id: 'deepgram', label: 'Deepgram', mark: 'DG', detail: 'Cloud transcription' },
    { id: 'whisper', label: 'Whisper', mark: 'WH', detail: 'Local transcription' },
  ],
}
const whisperModels = ['tiny', 'base', 'small', 'medium', 'large', 'turbo']
const captionDescriptions: Record<string, string> = {
  classic: 'White · dark outline', minimal: 'Soft gray · light type', bold: 'Yellow · heavy outline',
  neon: 'Green · dark green outline', typewriter: 'White · Courier type', sunset: 'Amber · plum outline',
  mono: 'Gray · monospace type', bubble: 'White · purple outline',
}
const providerLabel = (id: string) => [...profileProviders.llm, ...profileProviders.transcription].find((item) => item.id === id)?.label || id

function profileDraftFor(kind: ProfileKind, provider: string, profile?: ProviderProfile): ProfileDraft {
  return {
    id: profile?.id, name: profile?.name || (provider === 'custom' ? '' : providerLabel(provider)), kind, provider,
    apiKey: '',
    baseUrl: provider === 'deepgram' ? 'https://api.deepgram.com/v1/listen' : profile?.baseUrl || '',
    model: provider === 'deepgram' ? 'nova-2' : profile?.model || '',
  }
}

export default function SettingsPanel({ onClose }: { onClose: () => void }) {
  const { status, error: statusError } = useEnvironment()
  const [section, setSection] = useState<Section>('appearance')
  const [settings, setSettings] = useState<Settings | null>(null)
  // Keep the server's saved defaults separate from editable workspace drafts.
  const [savedSettings, setSavedSettings] = useState<Settings | null>(null)
  const [dirty, setDirty] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [profiles, setProfiles] = useState<DisplayProfile[]>([])
  const [activeProfiles, setActiveProfiles] = useState<ProviderProfilesResponse['active']>({})
  const [fallbackProfiles, setFallbackProfiles] = useState<ProviderProfilesResponse['active']>({})
  const [profilesLoading, setProfilesLoading] = useState(true)
  const [profilesLoadError, setProfilesLoadError] = useState<string | null>(null)
  const [profilesAttempt, setProfilesAttempt] = useState(0)
  const [profileKind, setProfileKind] = useState<ProfileKind>('llm')
  const [providerView, setProviderView] = useState<'connections' | 'add' | 'edit'>('connections')
  // Each editor has its own draft, including new profiles for different providers.
  const [drafts, setDrafts] = useState<Record<string, ProfileDraft>>({})
  const [draftKey, setDraftKey] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, ConnectionTestResult>>({})
  const contentRef = useRef<HTMLDivElement>(null)
  const providerContentRef = useRef<HTMLDivElement>(null)
  const tabScrollRef = useRef<number | null>(null)
  const [providerMinHeight, setProviderMinHeight] = useState(0)
  const [providerNavigation, setProviderNavigation] = useState(0)
  const profileFormRef = useRef<HTMLFormElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    api.getSettings(controller.signal).then((result) => {
      if (!controller.signal.aborted) { setSettings(result); setSavedSettings(result); setLoadError(null) }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(error))
    })
    return () => controller.abort()
  }, [loadAttempt])

  useEffect(() => {
    const controller = new AbortController()
    api.listProviderProfiles(controller.signal).then((result) => {
      if (controller.signal.aborted) return
      setProfiles(result.profiles)
      setActiveProfiles(result.active)
      setFallbackProfiles((result as ProfileListing).fallback || {})
      setProfilesLoadError(null)
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setProfilesLoadError(errorMessage(error))
    }).finally(() => { if (!controller.signal.aborted) setProfilesLoading(false) })
    return () => controller.abort()
  }, [profilesAttempt])

  useLayoutEffect(() => {
    const pane = contentRef.current
    if (!pane) return
    if (tabScrollRef.current !== null) {
      const missingRange = tabScrollRef.current - (pane.scrollHeight - pane.clientHeight)
      if (missingRange > 0) {
        // Footer feedback can also change the viewport height. Extend the floor
        // before restoring a position that the browser would otherwise clamp.
        const height = providerContentRef.current?.getBoundingClientRect().height || 0
        setProviderMinHeight((previous) => Math.max(previous, height + missingRange + 1))
        return
      }
      pane.scrollTop = tabScrollRef.current
      tabScrollRef.current = null
    } else {
      pane.scrollTo({ top: 0 })
      setProviderMinHeight(0)
    }
  }, [section, providerView, profileKind, providerNavigation, providerMinHeight])

  const currentSection = sections.find((item) => item.id === section)!
  const draft = draftKey ? drafts[draftKey] : undefined
  const savedProfile = profiles.find((profile) => profile.id === draft?.id)
  const visibleProfiles = profiles.filter((profile) => profile.kind === profileKind)
  const activeProfile = (kind: ProfileKind) => profiles.find((profile) => profile.kind === kind && profile.id === activeProfiles[kind])
  const fallbackProfile = (kind: ProfileKind) => profiles.find((profile) => profile.kind === kind && profile.id === fallbackProfiles[kind])
  const unsavedProfiles = Object.values(drafts).some((value) => {
    const original = profileDraftFor(value.kind, value.provider, profiles.find((profile) => profile.id === value.id))
    return value.name !== original.name || value.model !== original.model || value.baseUrl !== original.baseUrl || !!value.apiKey
  })
  const environmentKeyStatus = (provider: string) => {
    if (provider === 'ollama' || provider === 'whisper') return 'Local · no API key required'
    if (statusError) return 'Key status unavailable'
    if (!status) return 'Checking key status…'
    const keyProvider = KEY_PROVIDERS.find((item) => item.id === provider)
    if (!keyProvider) return 'Key status unknown'
    // The API's custom flag combines key + base URL; false does not prove a missing key.
    if (provider === 'custom') return status.hasCustomProvider ? 'Environment key + base URL configured' : 'Environment key + base URL not fully configured'
    return status[keyProvider.statusKey] ? 'Environment key configured' : 'Environment key not set'
  }
  const profileKeyStatus = (profile: ProviderProfile) => profile.provider === 'whisper'
    ? 'Local · no API key required'
    : profile.hasApiKey ? 'Profile key saved'
      : profile.provider === 'ollama' ? 'No profile key saved · optional for local Ollama' : 'No profile key saved · environment key not used'
  const profileModel = (profile: DisplayProfile) => (profile.effectiveModel ?? (profile.provider === 'deepgram' ? 'nova-2' : profile.model || 'Provider default (not reported)')) || 'Model not set'
  const profileEndpoint = (profile: DisplayProfile) => profile.provider === 'whisper' ? 'Local · ClipForge server'
    : (profile.effectiveBaseUrl ?? profile.baseUrl) || (profile.provider === 'custom' ? 'Base URL not set' : 'Provider default (not reported)')
  const profileSelection = (profile: ProviderProfile) => activeProfiles[profile.kind] === profile.id ? 'Active'
    : !activeProfiles[profile.kind] && fallbackProfiles[profile.kind] === profile.id
      ? profile.kind === 'llm' ? 'In use · legacy default' : 'New-project default' : 'Saved · inactive'
  const close = () => {
    if (busy) return
    if (dirty || unsavedProfiles) {
      setFeedback({ tone: 'info', message: unsavedProfiles
        ? 'You have unsaved profile changes. Save each profile or choose Discard profile drafts before closing.'
        : 'You have unsaved workspace changes. Save settings or choose Discard changes.' })
      return
    }
    onClose()
  }
  const clearResult = (key: string) => setResults((previous) => {
    const next = { ...previous }; delete next[key]; return next
  })
  const change = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setSettings((previous) => previous ? { ...previous, [key]: value } : previous)
    setDirty(true)
    setFeedback(null)
    setResults((previous) => Object.fromEntries(Object.entries(previous).filter(([id]) => id.startsWith('profile:'))))
  }
  const refreshProfiles = async () => {
    const result: ProfileListing = await api.listProviderProfiles()
    setProfiles(result.profiles)
    setActiveProfiles(result.active)
    setFallbackProfiles(result.fallback || {})
    setProfilesLoadError(null)
  }
  const navigateProviderView = (view: typeof providerView) => {
    tabScrollRef.current = null
    setProviderMinHeight(0)
    setProviderView(view)
    setProviderNavigation((value) => value + 1)
  }
  const switchCapability = (kind: ProfileKind) => {
    if (kind === profileKind && providerView === 'connections') return
    tabScrollRef.current = contentRef.current?.scrollTop || 0
    // Keep enough content height to avoid native scroll clamping when the next
    // capability has fewer profiles. Navigation releases this temporary floor.
    setProviderMinHeight((height) => Math.max(height, providerContentRef.current?.getBoundingClientRect().height || 0))
    setProfileKind(kind)
    setProviderView('connections')
    setFeedback(null)
  }

  const saveSettings = async (testProvider?: string) => {
    if (busy || !settings) return
    setBusy(testProvider ? 'connection-test' : 'settings-save')
    setFeedback(null)
    let saved = false
    try {
      const nextSettings = { ...settings, customBaseUrl: (settings.customBaseUrl || '').trim(),
        customModel: (settings.customModel || '').trim(), ollamaModel: (settings.ollamaModel || 'llama3.2').trim() }
      await api.updateSettings({
        LLM_PROVIDER: nextSettings.llmProvider, TRANSCRIPTION_MODE: nextSettings.transcriptionMode,
        WHISPER_MODEL: nextSettings.whisperModel, CAPTION_STYLE: nextSettings.captionStyle,
        CUSTOM_BASE_URL: nextSettings.customBaseUrl, CUSTOM_MODEL: nextSettings.customModel,
        OLLAMA_MODEL: nextSettings.ollamaModel,
      })
      saved = true
      setSavedSettings(nextSettings)
      setSettings(nextSettings)
      setDirty(false)
      setFeedback({ tone: 'success', message: 'Settings saved' })
      await refreshProfiles()
      if (testProvider) {
        const result = await api.testConnection(testProvider)
        setResults((previous) => ({ ...previous, [testProvider]: result }))
        setFeedback({ tone: result.ok ? 'success' : 'error', message: `Settings saved. ${result.message}` })
      }
    } catch (error) {
      setFeedback({ tone: 'error', message: `${saved ? 'Settings saved; provider refresh or connection test failed. ' : ''}${errorMessage(error)}` })
    } finally { setBusy(null) }
  }

  const openProfile = (provider: string, profile?: ProviderProfile) => {
    const key = profile?.id || `new:${profileKind}:${provider}`
    setDrafts((previous) => previous[key] ? previous : { ...previous, [key]: profileDraftFor(profileKind, provider, profile) })
    setDraftKey(key)
    navigateProviderView('edit')
    setFeedback(null)
  }
  const changeDraft = (patch: Partial<Pick<ProfileDraft, 'name' | 'model' | 'baseUrl' | 'apiKey'>>) => {
    if (!draft || !draftKey) return
    setDrafts((previous) => ({ ...previous, [draftKey]: { ...draft, ...patch } }))
    if (draft.id) clearResult(`profile:${draft.id}`)
    setFeedback(null)
  }

  const saveProfile = async (action: 'save' | 'activate' | 'test') => {
    if (!draft || !draftKey || busy || (action === 'test' && draft.kind !== 'llm')) return
    if (!profileFormRef.current?.reportValidity()) return
    if (!draft.name.trim() || (draft.provider === 'custom' && (!draft.baseUrl.trim() || !draft.model.trim()))) {
      setFeedback({ tone: 'error', message: 'Enter a profile name and the required connection fields.' })
      return
    }
    setBusy(`profile-${action}`)
    setFeedback(null)
    if (draft.id) clearResult(`profile:${draft.id}`)
    let saved: ProviderProfile | undefined
    let phase = action === 'test' ? 'connection test' : 'activation'
    try {
      const fields = { name: draft.name.trim(), baseUrl: draft.baseUrl.trim(), model: draft.model.trim(),
        ...(draft.apiKey.trim() ? { apiKey: draft.apiKey.trim() } : {}) }
      // Provider and capability are immutable for existing profiles. Never migrate secrets across providers.
      saved = draft.id
        ? await api.updateProviderProfile(draft.id, fields)
        : await api.createProviderProfile({ ...fields, kind: draft.kind, provider: draft.provider })
      const profile = saved
      setProfiles((previous) => [...previous.filter((item) => item.id !== profile.id), profile])
      setDrafts((previous) => {
        const next = { ...previous }; delete next[draftKey]
        return { ...next, [profile.id]: profileDraftFor(profile.kind, profile.provider, profile) }
      })
      setDraftKey(profile.id)
      setFeedback({ tone: 'success', message: `${profile.name}: profile ${draft.id ? 'saved' : 'created'}.` })
      // Both actions intentionally persist the current draft before using its profile ID.
      if (action === 'activate') {
        const result = await api.selectActiveProviderProfile(profile.kind, profile.id)
        setActiveProfiles(result.active)
        setFeedback({ tone: 'success', message: `${profile.name} saved and active for ${profile.kind === 'llm' ? 'analysis' : 'transcription'}.` })
      } else if (action === 'test') {
        const result = await api.testConnection(undefined, profile.id)
        setResults((previous) => ({ ...previous, [`profile:${profile.id}`]: result }))
        setFeedback({ tone: result.ok ? 'success' : 'error', message: `${profile.name} saved. ${result.message}` })
      }
      phase = 'provider status refresh'
      await refreshProfiles()
    } catch (error) {
      setFeedback({ tone: 'error', message: `${saved ? `Profile saved; ${phase} failed. ` : ''}${errorMessage(error)}` })
    } finally { setBusy(null) }
  }

  const deleteProfile = async () => {
    if (!savedProfile || busy) return
    setBusy('profile-delete')
    setFeedback(null)
    try {
      await api.deleteProviderProfile(savedProfile.id)
      setProfiles((previous) => previous.filter((item) => item.id !== savedProfile.id))
      setActiveProfiles((previous) => previous[savedProfile.kind] === savedProfile.id ? { ...previous, [savedProfile.kind]: undefined } : previous)
      setFallbackProfiles((previous) => previous[savedProfile.kind] === savedProfile.id ? { ...previous, [savedProfile.kind]: undefined } : previous)
      setDrafts((previous) => { const next = { ...previous }; delete next[savedProfile.id]; return next })
      clearResult(`profile:${savedProfile.id}`)
      setDraftKey(null)
      navigateProviderView('connections')
      setFeedback({ tone: 'success', message: `${savedProfile.name} deleted.${activeProfiles[savedProfile.kind] === savedProfile.id ? savedProfile.kind === 'llm' ? ' Using saved analysis fallback settings.' : ' Using each project’s saved transcription mode.' : ''}` })
    } catch (error) { setFeedback({ tone: 'error', message: errorMessage(error) }) }
    finally { setBusy(null) }
  }

  const clearActiveProfile = async () => {
    if (busy) return
    setBusy('profile-clear')
    setFeedback(null)
    try {
      const result = await api.selectActiveProviderProfile(profileKind, null)
      setActiveProfiles(result.active)
      setFeedback({ tone: 'success', message: profileKind === 'llm' ? 'Using saved analysis fallback settings.' : 'Using each project’s saved transcription mode; new projects use the Processing default.' })
    } catch (error) { setFeedback({ tone: 'error', message: errorMessage(error) }) }
    finally { setBusy(null) }
  }

  const renderResult = (key: string) => results[key] && <p className={`settings-test-result ${results[key].ok ? 'text-success' : 'text-error'}`} role="status">
    {results[key].ok && <CheckCircle2 size={16} aria-hidden="true" />}{results[key].message}
    {results[key].latencyMs != null && <span> · {results[key].latencyMs} ms</span>}
  </p>

  const renderCurrentProviders = () => <section className="settings-card settings-current-providers" aria-label="Current provider defaults">
    <h4>Current provider defaults</h4>
    <div className="settings-current-grid">
      {(['llm', 'transcription'] as const).map((kind) => {
        const selected = activeProfile(kind)
        const profile = activeProfiles[kind] ? selected : fallbackProfile(kind)
        const provider = profile?.provider || (savedSettings ? kind === 'llm' ? savedSettings.llmProvider.trim().toLowerCase() : savedSettings.transcriptionMode === 'local' ? 'whisper' : 'deepgram' : undefined)
        const selectionNotice = profilesLoading ? 'Loading provider selection…' : profilesLoadError ? 'Provider selection unavailable' : activeProfiles[kind] && !profile ? 'Active profile unavailable' : !provider ? loadError ? 'Saved settings unavailable' : 'Loading saved settings…' : null
        return <div key={kind} className="settings-current-provider">
          <h5>{kind === 'llm' ? 'Analysis / LLM' : 'Transcription'}</h5>
          {selectionNotice || !provider ? <p className="settings-help" role="status">{selectionNotice}</p> : <>
            <strong>{profile?.name || providerLabel(provider)}</strong>
            <p className="settings-help">{profile ? `${providerLabel(provider)} · ${profileModel(profile)}` : kind === 'llm' ? 'Fallback · saved Processing settings' : 'Fallback · new-project default'}</p>
            {profile && <p className="settings-help">{profileEndpoint(profile)}</p>}
            {profile && <p className="settings-help">{selected ? 'Active profile' : kind === 'llm' ? 'In use · saved legacy settings' : 'New-project default · existing projects keep their saved mode'}</p>}
            <p className="settings-help">{profile ? profileKeyStatus(profile) : environmentKeyStatus(provider)}</p>
            {!profile && provider === 'custom' && !savedSettings?.customModel.trim() && <p className="settings-help">Custom model not set</p>}
          </>}
        </div>
      })}
    </div>
    <p className="settings-help">Active profiles take priority. Without one, transcription uses each project’s saved mode; the fallback above is for new projects.</p>
    {dirty && <p className="settings-help">Unsaved workspace changes are not in use.</p>}
  </section>

  return <Modal title="Settings" onClose={close} width={1080} className="settings-modal settings-dialog" closeOnBackdrop={false} closeDisabled={!!busy}>
    <div className="settings-shell">
      <div className="settings-layout">
        <nav className="settings-sidebar" aria-label="Settings sections">
          <p className="settings-sidebar-label">Workspace</p>
          {sections.map(({ id, label, icon: Icon }) => <button key={id} id={`settings-nav-${id}`} type="button" disabled={!!busy} aria-current={section === id ? 'page' : undefined} aria-controls="settings-section-content" onClick={() => setSection(id)}>
            <Icon size={19} aria-hidden="true" /><span>{label}</span>
          </button>)}
          <p className="settings-sidebar-note">Your drafts stay here as you switch sections.</p>
        </nav>
        <div ref={contentRef} id="settings-section-content" className="settings-pane" role="region" aria-labelledby={`settings-nav-${section}`} tabIndex={0}>
          <header className="settings-section-header"><h3>{currentSection.label}</h3><p>{currentSection.description}</p></header>
          {!settings && <div className={`notice ${loadError ? 'notice-error' : ''}`} role={loadError ? 'alert' : 'status'}>
            <span>{loadError || 'Loading settings…'}</span>
            {loadError && <button type="button" className="btn-secondary" onClick={() => { setLoadError(null); setLoadAttempt((value) => value + 1) }}>Retry settings</button>}
          </div>}

          {section === 'appearance' && <div className="settings-section-body">
            <div className="settings-card settings-appearance"><div><h4>Color theme</h4><p className="muted">Dark charcoal, matching Light, or blue-grey Nord.</p></div><ThemeSelect /></div>
            <p className="settings-help">Theme changes apply immediately and are remembered on this device.</p>
          </div>}

          {section === 'providers' && <div ref={providerContentRef} className="settings-section-body settings-provider-content" style={{ minHeight: providerMinHeight || undefined }}>
            <div className="settings-capabilities" role="group" aria-label="Provider capability">
              {(['llm', 'transcription'] as const).map((kind) => <button type="button" key={kind} aria-pressed={profileKind === kind} disabled={!!busy}
                onPointerDown={(event) => {
                  if (event.button !== 0 || !event.isPrimary) return
                  event.preventDefault()
                  event.currentTarget.focus({ preventScroll: true })
                }}
                onClick={() => switchCapability(kind)}>
                {kind === 'llm' ? 'Analysis / LLM' : 'Transcription'}
              </button>)}
            </div>
            {providerView === 'connections' && <>
              <p className="settings-key-notice"><KeyRound size={18} aria-hidden="true" /><span><strong>Saved API keys are hidden (write-only).</strong> Leave blank to keep a profile’s saved key. Previously configured connections are imported as persistent profiles.</span></p>
              {renderCurrentProviders()}
            </>}
            {profilesLoading && <p role="status" className="settings-help">Loading provider profiles…</p>}
            {profilesLoadError && <div className="notice notice-error" role="alert"><span>Provider profiles unavailable. {profilesLoadError}</span><button type="button" className="btn-secondary" disabled={profilesLoading} onClick={() => { setProfilesLoading(true); setProfilesAttempt((value) => value + 1) }}>Retry profiles</button></div>}

            {providerView === 'connections' && <>
              <div className="settings-row"><div><h4>Saved profiles</h4><p className="settings-help">All saved models and connections, including imported legacy settings.</p></div><button type="button" className="btn-accent" disabled={!!busy || profilesLoading || !!profilesLoadError} onClick={() => navigateProviderView('add')}><Plus size={17} aria-hidden="true" />Add profile</button></div>
              <div className="settings-connection-list">
                {!profilesLoading && !profilesLoadError && visibleProfiles.length === 0 && <div className="settings-empty"><KeyRound size={24} aria-hidden="true" /><p>No saved {profileKind === 'llm' ? 'analysis' : 'transcription'} profiles yet.</p><p className="settings-help">Add a profile to save a model and its connection details.</p></div>}
                {visibleProfiles.map((profile) => <button type="button" key={profile.id} className="settings-connection-card" disabled={!!busy} aria-label={`Edit ${profile.name}`} onClick={() => openProfile(profile.provider, profile)}>
                  <span className="settings-provider-mark" aria-hidden="true">{profileProviders[profileKind].find((item) => item.id === profile.provider)?.mark || 'API'}</span>
                  <span className="settings-connection-copy"><strong>{profile.name}</strong><span>{providerLabel(profile.provider)} · Model: {profileModel(profile)}</span><span>{profile.provider === 'whisper' ? 'Runtime' : 'Base URL'}: {profileEndpoint(profile)}</span><small>{profileKeyStatus(profile)}</small></span>
                  <span className={`badge ${activeProfiles[profile.kind] === profile.id ? 'badge-success' : 'badge-neutral'}`}>{profileSelection(profile)}</span><ArrowRight size={17} aria-hidden="true" />
                </button>)}
              </div>
              {activeProfiles[profileKind] && <button type="button" className="text-button" disabled={!!busy} onClick={() => void clearActiveProfile()}>Use fallback settings instead</button>}
              <p className="settings-help">Visual analysis needs an image-capable model: for example GPT-4o, Gemini, Claude, or a vision model through OpenRouter, Groq, Ollama or a custom endpoint. Text-only models such as deepseek-chat and llama3.2 can analyze speech transcripts. A connection test checks text connectivity, not image capability.</p>
            </>}

            {providerView === 'add' && <>
              <button type="button" className="text-button" onClick={() => navigateProviderView('connections')}><ArrowLeft size={16} aria-hidden="true" />Back to connections</button>
              <div><h4>Add a profile</h4><p className="settings-help">Choose a provider, then name your connection. Custom endpoints can use labels like Dahl or OrcaRouter.</p></div>
              <div className="settings-provider-tiles">{profileProviders[profileKind].map((provider) => <button type="button" className="settings-provider-tile" key={provider.id} onClick={() => openProfile(provider.id)}><span className="settings-provider-mark" aria-hidden="true">{provider.mark}</span><span><strong>{provider.label}</strong><small>{provider.detail}</small></span><Plus size={16} aria-hidden="true" /></button>)}</div>
            </>}

            {providerView === 'edit' && draft && <>
              <button type="button" className="text-button" disabled={!!busy} onClick={() => navigateProviderView('connections')}><ArrowLeft size={16} aria-hidden="true" />Back to connections · draft kept</button>
              <form ref={profileFormRef} className="settings-profile-form" onSubmit={(event) => { event.preventDefault(); void saveProfile('save') }}>
                <div className="settings-row"><div><h4>{draft.id ? 'Edit profile' : 'New profile'}</h4><p className="settings-help">{providerLabel(draft.provider)} · {draft.kind === 'llm' ? 'Analysis / LLM' : 'Transcription'}</p></div>{draft.id && activeProfiles[draft.kind] === draft.id && <span className="badge badge-success">Active</span>}</div>
                <div className="settings-profile-actions">
                  <button type="submit" className="btn-accent" disabled={!!busy}><Save size={16} aria-hidden="true" />{draft.id ? 'Save profile' : 'Create profile'}</button>
                  <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => void saveProfile('activate')}><Wifi size={16} aria-hidden="true" />Save & activate</button>
                  {draft.kind === 'llm' && <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => void saveProfile('test')}>Save & test</button>}
                  {draft.id && <button type="button" className="btn-danger" aria-label={`Delete ${savedProfile?.name || 'profile'}`} disabled={!!busy} onClick={() => void deleteProfile()}><Trash2 size={16} aria-hidden="true" /><span>Delete</span></button>}
                </div>
                {savedProfile && <section className="settings-card" aria-label="Saved profile details">
                  <h4>Saved connection</h4>
                  <p className="settings-help">{savedProfile.name} · {providerLabel(savedProfile.provider)} · {profileSelection(savedProfile)}</p>
                  <p className="settings-help">Model: {profileModel(savedProfile)}</p>
                  <p className="settings-help">{savedProfile.provider === 'whisper' ? 'Runtime' : 'Base URL'}: {profileEndpoint(savedProfile)}</p>
                  <p className="settings-help">{profileKeyStatus(savedProfile)}. Unsaved editor changes are not in use.</p>
                </section>}
                <fieldset className="settings-section-body" disabled={!!busy}>
                  <div className="settings-fields">
                    <label className="field">Profile name<input className="input-field" required maxLength={80} value={draft.name} onChange={(event) => changeDraft({ name: event.target.value })} placeholder={draft.provider === 'custom' ? 'Dahl or OrcaRouter' : 'Studio analysis'} /></label>
                    <label className="field">Provider type<input className="input-field" value={providerLabel(draft.provider)} readOnly aria-describedby="settings-provider-locked" /></label>
                  </div>
                  <p id="settings-provider-locked" className="settings-help">Provider type is fixed. Add a separate profile to use a different provider.</p>
                  {draft.provider !== 'whisper' && <label className="field">API key<input className="input-field" type="password" autoComplete="new-password" spellCheck={false} required={draft.provider === 'deepgram' && !savedProfile?.hasApiKey} maxLength={2000} value={draft.apiKey} onChange={(event) => changeDraft({ apiKey: event.target.value })} placeholder={savedProfile?.hasApiKey ? 'Saved · leave blank to keep' : draft.provider === 'ollama' ? 'Optional for local Ollama' : 'Enter this profile’s key'} /><span className="field-hint">{savedProfile?.hasApiKey ? 'A profile key is saved.' : 'This profile uses its own credentials.'} Saved API keys are hidden (write-only). Leave blank to keep the saved key; enter a new key to replace it.</span></label>}
                  {draft.kind === 'llm' && <div className="settings-fields">
                    <label className="field">Model<input className="input-field" required={draft.provider === 'custom'} maxLength={200} value={draft.model} onChange={(event) => changeDraft({ model: event.target.value })} placeholder={draft.provider === 'custom' ? 'Required model identifier' : !savedProfile?.model && savedProfile?.effectiveModel ? savedProfile.effectiveModel : 'Leave blank for provider default'} /><span className="field-hint">{draft.provider === 'custom' ? 'Enter the exact model identifier for this endpoint.' : 'Blank uses the current server/provider default.'}</span></label>
                    <label className="field">Base URL<input className="input-field" type="url" pattern="https?://.+" required={draft.provider === 'custom'} maxLength={500} value={draft.baseUrl} onChange={(event) => changeDraft({ baseUrl: event.target.value })} placeholder={draft.provider === 'custom' ? 'https://provider.example/v1' : !savedProfile?.baseUrl && savedProfile?.effectiveBaseUrl ? savedProfile.effectiveBaseUrl : draft.provider === 'ollama' ? 'http://localhost:11434/v1' : 'Leave blank for provider default'} /><span className="field-hint">{draft.provider === 'claude' ? 'An override must be the full Anthropic messages endpoint.' : 'Use an HTTP or HTTPS API base URL.'}</span></label>
                  </div>}
                  {draft.provider === 'whisper' && <label className="field">Whisper model<select className="input-field" value={draft.model} onChange={(event) => changeDraft({ model: event.target.value })}><option value="">Use Processing default</option>{draft.model && !whisperModels.includes(draft.model) && <option value={draft.model}>{draft.model}</option>}{whisperModels.map((model) => <option key={model} value={model}>{model}</option>)}</select><span className="field-hint">Runs on the ClipForge server. No API key required.</span></label>}
                  {draft.provider === 'deepgram' && <div className="settings-card"><h4>Server transcription connection</h4><p className="settings-help">Model: nova-2</p><p className="settings-help">Base URL: https://api.deepgram.com/v1/listen</p><p className="settings-help">The server fixes the Deepgram model and endpoint; only the saved profile key is selected.</p></div>}
                  <p className="settings-help">{draft.kind === 'llm' ? 'Save & test and Save & activate both save these fields first.' : draft.provider === 'deepgram' ? 'Save & activate selects this cloud connection.' : 'Save & activate selects this local transcription profile.'} {draft.id ? 'Deleting an active profile restores fallback settings.' : 'Creating a profile keeps the current active connection until you activate it.'} Use the profile actions above to save this draft; Save settings saves workspace defaults.</p>
                  {draft.kind === 'transcription' && <p className="settings-help">Connection tests are available for analysis profiles only. Transcription is checked when a project is transcribed.</p>}
                  {draft.id && renderResult(`profile:${draft.id}`)}
                </fieldset>
              </form>
            </>}
          </div>}

          {section === 'processing' && <fieldset className="settings-section-body" disabled={!settings || !!busy}>
            {renderCurrentProviders()}
            <button type="button" className="text-button" onClick={() => { setSection('providers'); setProviderView('connections') }}>Manage provider profiles <ArrowRight size={16} aria-hidden="true" /></button>
            <p className="settings-help">Save settings to apply these defaults. Analysis uses its fallback when no profile is active. The transcription default applies to new projects; existing projects keep their saved mode unless a profile is active.</p>
            <div className="settings-fields">
              <label className="field">Analysis provider<select className="input-field" value={settings?.llmProvider || 'deepseek'} onChange={(event) => change('llmProvider', event.target.value)}>{ANALYSIS_PROVIDERS.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}</select></label>
              <label className="field">Transcription<select className="input-field" value={settings?.transcriptionMode || 'cloud'} onChange={(event) => change('transcriptionMode', event.target.value as Settings['transcriptionMode'])}><option value="cloud">Cloud · Deepgram</option><option value="local">Local · Whisper</option></select></label>
              <label className="field">Whisper model<select className="input-field" value={settings?.whisperModel || 'base'} onChange={(event) => change('whisperModel', event.target.value)}>{whisperModels.map((model) => <option key={model}>{model}</option>)}</select><span className="field-hint">Used for local transcription; larger models need more memory.</span></label>
              <label className="field">Ollama model<input className="input-field" value={settings?.ollamaModel ?? 'llama3.2'} onChange={(event) => change('ollamaModel', event.target.value)} placeholder="llama3.2" /><span className="field-hint">Requires Ollama running on the server.</span></label>
            </div>
            {settings?.llmProvider === 'custom' && <div className="settings-fields"><label className="field">Custom fallback base URL<input className="input-field" type="url" value={settings.customBaseUrl} onChange={(event) => change('customBaseUrl', event.target.value)} placeholder="https://provider.example/v1" /></label><label className="field">Custom fallback model<input className="input-field" value={settings.customModel} onChange={(event) => change('customModel', event.target.value)} /></label></div>}
            {settings?.llmProvider === 'ollama' && <div><button type="button" className="btn-secondary" onClick={() => void saveSettings('ollama')}>Save & test Ollama</button>{renderResult('ollama')}</div>}
            <button type="button" className="text-button" onClick={() => { setSection('providers'); navigateProviderView('connections') }}>Manage saved model connections <ArrowRight size={16} aria-hidden="true" /></button>
          </fieldset>}

          {section === 'captions' && <fieldset className="settings-section-body" disabled={!settings || !!busy}>
            <legend className="sr-only">Default caption preset</legend>
            <p className="settings-help">Existing projects keep their own caption settings. Static previews approximate color, outline, and relative size; final fonts and wrapping depend on the renderer.</p>
            <div className="settings-caption-grid">{fallbackRenderContract.caption.presets.map(({ id, label, settings: preset }) => <label key={id} className={`settings-caption-card ${settings?.captionStyle === id ? 'is-selected' : ''}`}>
              <span className={`settings-caption-preview settings-caption-preview-${id}`} aria-hidden="true"><span style={{ color: preset.fontColor, fontSize: preset.fontSize / 2.4, WebkitTextStroke: `${preset.outlineWidth / 2.4}px ${preset.outlineColor}`, paintOrder: 'stroke fill' }}>Your story</span></span>
              <span className="settings-caption-label"><input type="radio" name="default-caption-style" value={id} checked={settings?.captionStyle === id} onChange={() => change('captionStyle', id)} /><strong>{label}</strong></span><small>{captionDescriptions[id]}</small>
            </label>)}</div>
            <div className="settings-card"><h4>Fine-tune a project</h4><p className="settings-help">Open a project → Caption Controls & filters → adjust placement, typography, colors, and chunking → render your clips.</p><button type="button" className="text-button" onClick={close}>Close settings to continue in your project <ArrowRight size={16} aria-hidden="true" /></button></div>
          </fieldset>}

          {section === 'environment' && <div className="settings-section-body">
            {statusError && <div className="notice notice-error" role="alert"><span>{statusError}</span></div>}
            <div className="settings-environment-list">{([['FFmpeg', 'Video rendering', status?.hasFfmpeg], ['FFprobe', 'Media inspection', status?.hasFfprobe], ['yt-dlp', 'YouTube import', status?.hasYtdlp]] as const).map(([label, description, installed]) => <div className="settings-row" key={label}><div><strong>{label}</strong><p className="settings-help">{description}</p></div><span className={`badge ${installed ? 'badge-success' : 'badge-neutral'}`}>{status ? installed ? 'Installed' : 'Missing' : statusError ? 'Unavailable' : 'Checking…'}</span></div>)}</div>
            <div className="settings-card"><h4>Data directory</h4><p className="settings-path">{settings?.dataDir || status?.dataDir || 'Unavailable'}</p><p className="settings-help">Media and rendered files are stored on this server.</p></div>
            <p className="settings-help">Rendering runs on the ClipForge server. Cloud providers receive the audio or transcript needed for the selected operation. API keys are write-only.</p>
            <button type="button" className="text-button" onClick={() => setSection('providers')}>Manage connections in Providers <ArrowRight size={16} aria-hidden="true" /></button>
          </div>}
        </div>
      </div>
      <footer className="settings-footer">
        <div className={`settings-feedback ${feedback ? `is-${feedback.tone}` : ''}`} role="status" aria-live="polite" aria-atomic="true">
          {busy ? <><span className="spinner" aria-hidden="true" /><span>{busy.includes('test') ? 'Saving and testing connection…' : 'Saving changes…'}</span></> : feedback ? <>{feedback.tone === 'success' && <CheckCircle2 size={18} aria-hidden="true" />}<span>{feedback.message}</span></> : <span>{unsavedProfiles ? 'Unsaved profile drafts. Use each profile’s save actions.' : dirty ? 'Unsaved settings changes.' : 'Theme applies instantly. Profiles use their own save actions.'}</span>}
        </div>
        <div className="settings-footer-actions">{unsavedProfiles && <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => { setDrafts({}); setDraftKey(null); navigateProviderView('connections'); setFeedback({ tone: 'info', message: 'Unsaved profile drafts discarded.' }) }}>Discard profile drafts</button>}{dirty && <button type="button" className="btn-secondary" disabled={!!busy} onClick={() => { setSettings(savedSettings); setDirty(false); setFeedback({ tone: 'info', message: 'Unsaved workspace changes discarded.' }) }}>Discard changes</button>}<button type="button" className="btn-secondary" disabled={!!busy} onClick={close}>Close</button><button type="button" className="btn-accent" disabled={!settings || !!busy} onClick={() => void saveSettings()}><Save size={17} aria-hidden="true" />Save settings</button></div>
      </footer>
    </div>
  </Modal>
}
