import type { Clip } from './client'

export function isActiveJob(status?: string): boolean {
  return !!status && ['pending', 'queued', 'started', 'processing', 'running', 'extracting', 'transcribing', 'analyzing', 'rendering', 'downloading'].includes(status)
}
export function isFailedJob(status?: string): boolean {
  return !!status && ['error', 'failed', 'cancelled', 'canceled', 'interrupted'].includes(status)
}
export function latestClips(clips: Clip[]): Map<string, Clip> {
  const latest = new Map<string, Clip>()
  for (const clip of clips) {
    const previous = latest.get(clip.candidate_id)
    // Legacy records have no timestamp; the API's insertion order is oldest first.
    if (!previous || !previous.created_at || !clip.created_at || clip.created_at >= previous.created_at) latest.set(clip.candidate_id, clip)
  }
  return latest
}
