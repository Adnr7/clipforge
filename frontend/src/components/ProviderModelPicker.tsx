import { useEffect, useRef, useState } from 'react'
import { Check, RefreshCw, X } from 'lucide-react'
import { api, errorMessage, type ProviderModel, type ProviderModelsRequest } from '../api/client'
import '../styles/provider-model-picker.css'

export default function ProviderModelPicker({ request, value, onSelect, disabled = false }: {
  request: ProviderModelsRequest
  value: string
  onSelect: (model: string) => void
  disabled?: boolean
}) {
  const [catalog, setCatalog] = useState<{ identity: string; models: ProviderModel[] } | null>(null)
  const [failure, setFailure] = useState<{ identity: string; message: string } | null>(null)
  const [loadingIdentity, setLoadingIdentity] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const requestIdentity = [request.kind, request.provider, request.profileId || '', request.baseUrl || '', request.apiKey ? 'key' : 'no-key'].join('|')
  const models = catalog?.identity === requestIdentity ? catalog.models : null
  const error = failure?.identity === requestIdentity ? failure.message : null
  const loading = loadingIdentity === requestIdentity

  useEffect(() => {
    controller.current?.abort()
    return () => controller.current?.abort()
  }, [requestIdentity])

  const fetchModels = async () => {
    if (disabled || loading) return
    controller.current?.abort()
    const next = new AbortController()
    controller.current = next
    setLoadingIdentity(requestIdentity)
    setFailure(null)
    try {
      const result = await api.listProviderModels(request, next.signal)
      if (!next.signal.aborted) setCatalog({ identity: requestIdentity, models: result.models })
    } catch (fetchError) {
      if (!next.signal.aborted) setFailure({ identity: requestIdentity, message: errorMessage(fetchError) })
    } finally {
      if (!next.signal.aborted) setLoadingIdentity(null)
    }
  }

  const openPicker = () => {
    if (disabled) return
    const nextOpen = !open
    setOpen(nextOpen)
    if (nextOpen && !models && !loading) void fetchModels()
  }

  return <div className="provider-model-picker">
    <button type="button" className="btn-secondary provider-model-picker-trigger" aria-expanded={open} aria-haspopup="dialog" disabled={disabled} onClick={openPicker}>
      {loading ? <span className="spinner" /> : <RefreshCw size={15} aria-hidden="true" />}
      {loading ? 'Fetching…' : 'Fetch models'}
    </button>
    {open && <div className="provider-model-picker-popover" role="dialog" aria-label="Available models">
      <div className="provider-model-picker-header">
        <div><strong>Available models</strong><p className="field-hint">Choose a model to fill the Model field. Nothing is saved until you save the profile.</p></div>
        <button type="button" className="provider-model-picker-close" aria-label="Close available models" onClick={() => setOpen(false)}><X size={16} /></button>
      </div>
      <div className="provider-model-picker-actions"><span className="provider-model-picker-result" role="status">{models ? `${models.length} model${models.length === 1 ? '' : 's'} available` : 'Loading provider catalog…'}</span><button type="button" className="btn-secondary" disabled={disabled || loading} onClick={() => void fetchModels()}>{loading ? 'Fetching…' : 'Refresh'}</button></div>
      {error && <div className="provider-model-picker-error" role="alert"><span>{error}</span><button type="button" className="text-button" disabled={disabled || loading} onClick={() => void fetchModels()}>Retry</button></div>}
      {models && models.length === 0 && <p className="provider-model-picker-empty" role="status">No models were returned. You can still enter a model ID manually.</p>}
      {models && models.length > 0 && <ul className="provider-model-picker-list">
        {models.map((model) => <li key={model.id} className={model.id === value ? 'selected' : undefined}>
          <button type="button" className="btn-secondary provider-model-picker-use" aria-label={`Use ${model.id}`} disabled={disabled} onClick={() => { onSelect(model.id); setOpen(false) }}>
            {model.id === value ? <Check size={15} aria-hidden="true" /> : null}{model.id === value ? 'Selected' : 'Use'}
          </button>
          <span className="provider-model-picker-copy"><strong title={model.id}>{model.label || model.id}</strong>{model.label !== model.id && <code title={model.id}>{model.id}</code>}{model.ownedBy && <small>{model.ownedBy}</small>}</span>
        </li>)}
      </ul>}
    </div>}
  </div>
}
