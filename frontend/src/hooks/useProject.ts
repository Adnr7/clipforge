import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorMessage, type JobStatus, type ProjectDetail } from '../api/client'
import { isActiveJob } from '../api/jobs'

export function useProject(projectId: string) {
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<{ transcription: JobStatus; analysis: JobStatus }>({ transcription: { status: 'idle' }, analysis: { status: 'idle' } })
  const trackedClips = useRef(new Set<string>())
  const loadRef = useRef<() => Promise<boolean>>(async () => false)
  useEffect(() => {
    let controller: AbortController
    let timer: ReturnType<typeof setTimeout>
    let failures = 0
    trackedClips.current.clear()
    const load = async (): Promise<boolean> => {
      clearTimeout(timer)
      controller?.abort()
      controller = new AbortController()
      const { signal } = controller
      let active = true
      try {
        // One consistent snapshot includes job and clip statuses. Avoid 3+N
        // parallel requests on every poll while FFmpeg is busy.
        const data = await api.getProject(projectId, signal)
        const [transcription, analysis] = data.jobs ? [data.jobs.transcription, data.jobs.analysis] : await Promise.all([
          api.getTranscriptionStatus(projectId, signal), api.getAnalysisStatus(projectId, signal),
        ])
        if (signal.aborted) return false
        data.clips.forEach((clip) => { if (!isActiveJob(clip.status)) trackedClips.current.delete(clip.id) })
        setDetail(data); setJobs({ transcription, analysis }); setError(null); failures = 0
        active = isActiveJob(transcription.status) || isActiveJob(analysis.status) || data.clips.some((clip) => isActiveJob(clip.status)) || trackedClips.current.size > 0
        return true
      } catch (error) {
        if (!signal.aborted) { setError(errorMessage(error)); failures += 1 }
        return false
      } finally {
        if (!signal.aborted) {
          setLoading(false)
          // Retry transient errors without abandoning a batch; terminal jobs return to a quiet refresh cadence.
          timer = setTimeout(() => void load(), document.hidden ? 30000 : failures ? Math.min(2000 * failures, 15000) : active ? 2000 : 12000)
        }
      }
    }
    loadRef.current = load
    const onVisible = () => { if (!document.hidden) void load() }
    document.addEventListener('visibilitychange', onVisible)
    void load()
    return () => { controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', onVisible); loadRef.current = async () => false }
  }, [projectId])
  const refresh = useCallback(() => loadRef.current(), [])
  const trackClips = useCallback((ids: string[]) => { ids.forEach((id) => trackedClips.current.add(id)) }, [])
  return { detail, jobs, loading, error, refresh, trackClips }
}
