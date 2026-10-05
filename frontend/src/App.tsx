import { useCallback, useState } from 'react'
import Dashboard from './components/Dashboard'
import Workspace from './components/Workspace'
import Onboarding from './components/Onboarding'
import SettingsPanel from './components/SettingsPanel'
import { ToastProvider } from './components/ui/Toast'
import { useEnvironment } from './hooks/useEnvironment'

type View =
  | { page: 'onboarding' }
  | { page: 'dashboard' }
  | { page: 'workspace'; projectId: string }

export default function App() {
  const { status } = useEnvironment()
  const [view, setView] = useState<View>({ page: 'dashboard' })
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    () => { try { return localStorage.getItem('clipforge.onboarded') === '1' } catch { return false } }
  )
  const showOnboarding = view.page === 'onboarding' || (!!status && !onboardingDismissed)

  const completeOnboarding = useCallback(() => {
    try { localStorage.setItem('clipforge.onboarded', '1') } catch { /* Session-only completion. */ }
    setOnboardingDismissed(true)
    setView({ page: 'dashboard' })
  }, [])

  return (
    <ToastProvider>
      {showOnboarding && (
        <Onboarding onComplete={completeOnboarding} />
      )}
      {!showOnboarding && view.page === 'dashboard' && (
        <Dashboard
          onOpenProject={(projectId) => setView({ page: 'workspace', projectId })}
          onOpenSettings={() => setSettingsOpen(true)}
          needsOnboarding={!!status && (!status.hasFfmpeg || !status.hasFfprobe)}
          onStartOnboarding={() => setView({ page: 'onboarding' })}
        />
      )}
      {!showOnboarding && view.page === 'workspace' && (
        <Workspace
          key={view.projectId}
          projectId={view.projectId}
          onBack={() => setView({ page: 'dashboard' })}
          onOpenSettings={() => setSettingsOpen(true)}
        />
      )}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </ToastProvider>
  )
}
