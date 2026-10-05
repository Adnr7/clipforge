import { useEffect, useState } from 'react'
import { api, errorMessage, type EnvironmentStatus } from '../api/client'

export function useEnvironment() {
  const [status, setStatus] = useState<EnvironmentStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let controller: AbortController
    const refresh = () => {
      controller?.abort()
      controller = new AbortController()
      const { signal } = controller
      api.getStatus(signal).then((result) => {
        if (!signal.aborted) { setStatus(result); setError(null) }
      }).catch((error: unknown) => { if (!signal.aborted) setError(errorMessage(error)) })
    }
    refresh()
    window.addEventListener('clipforge:settings', refresh)
    return () => { controller.abort(); window.removeEventListener('clipforge:settings', refresh) }
  }, [])
  return { status, error }
}
