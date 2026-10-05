import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorMessage, type Project } from '../api/client'

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const refreshRef = useRef<() => void>(() => {})
  useEffect(() => {
    let controller: AbortController
    let timer: ReturnType<typeof setTimeout>
    const load = () => {
      clearTimeout(timer)
      controller?.abort()
      controller = new AbortController()
      const { signal } = controller
      api.listProjects(signal).then((list) => {
        if (!signal.aborted) { setProjects(list); setError(null) }
      }).catch((error: unknown) => {
        if (!signal.aborted) setError(errorMessage(error))
      }).finally(() => {
        if (!signal.aborted) { setLoading(false); timer = setTimeout(load, 8000) }
      })
    }
    refreshRef.current = load
    load()
    return () => { controller.abort(); clearTimeout(timer); refreshRef.current = () => {} }
  }, [])
  const refresh = useCallback(() => refreshRef.current(), [])
  return { projects, loading, error, refresh }
}
