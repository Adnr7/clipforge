import { useSyncExternalStore } from 'react'

export type Theme = 'dark' | 'light' | 'nord'
const storageKey = 'clipforge.theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')
const isTheme = (value: string | null): value is Theme => value === 'dark' || value === 'light' || value === 'nord'
const systemTheme = (): Theme => media.matches ? 'dark' : 'light'
let hasStoredTheme = false
let theme: Theme = systemTheme()
try {
  const saved = localStorage.getItem(storageKey)
  if (isTheme(saved)) { theme = saved; hasStoredTheme = true }
} catch { /* Storage can be unavailable in embedded browsers. */ }
function applyTheme() {
  document.documentElement.dataset.theme = theme
}
applyTheme()
const listeners = new Set<() => void>()
function notify() {
  applyTheme()
  listeners.forEach((listener) => listener())
}
media.addEventListener('change', () => {
  if (!hasStoredTheme) { theme = systemTheme(); notify() }
})
window.addEventListener('storage', (event) => {
  if (event.key === storageKey) {
    if (isTheme(event.newValue)) { theme = event.newValue; hasStoredTheme = true }
    else { theme = systemTheme(); hasStoredTheme = false }
    notify()
  }
})
export function useTheme() {
  const value = useSyncExternalStore((listener) => {
    listeners.add(listener)
    return () => { listeners.delete(listener) }
  }, () => theme)
  return {
    theme: value,
    setTheme: (next: Theme) => {
      theme = next
      hasStoredTheme = true
      try { localStorage.setItem(storageKey, next) } catch { /* Keep the session preference. */ }
      notify()
    },
  }
}
