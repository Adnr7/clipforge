import type { AudienceBrief } from './client'

export const audienceFieldLimits = { audience: 1000, goal: 1000, notes: 2000 } as const
export const emptyAudienceBrief: AudienceBrief = { audience: '', goal: '', notes: '' }

export function audienceFromBrief(brief: AudienceBrief): AudienceBrief {
  return { audience: brief.audience, goal: brief.goal, notes: brief.notes }
}

export function loadAudienceBrief(projectId: string): AudienceBrief {
  try {
    const saved = JSON.parse(localStorage.getItem(`clipforge.audience.v1.${projectId}`) || 'null')
      || JSON.parse(sessionStorage.getItem(`clipforge.ai-edit.v2.${projectId}`) || 'null')?.brief
    if (saved && typeof saved === 'object') return Object.fromEntries(Object.entries(audienceFieldLimits)
      .map(([key, maximum]) => [key, typeof saved[key] === 'string' ? saved[key].slice(0, maximum) : ''])) as unknown as AudienceBrief
  } catch { /* The brief remains available in memory when storage is unavailable. */ }
  return { ...emptyAudienceBrief }
}
