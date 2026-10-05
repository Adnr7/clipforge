import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react'
import { createPortal } from 'react-dom'
import { ToastContext, type ToastKind } from '../../hooks/useToast'

interface ToastItem { id: number; kind: ToastKind; message: string }
let nextId = 1
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())
  const [target, setTarget] = useState<Element>(document.body)
  useEffect(() => {
    // Native modal dialogs occupy the top layer. Keep save/test feedback visible and reachable there.
    const updateTarget = () => setTarget([...document.querySelectorAll('dialog[open]')].at(-1) || document.body)
    const observer = new MutationObserver(updateTarget)
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['open'] })
    updateTarget()
    return () => observer.disconnect()
  }, [])
  const dismiss = useCallback((id: number) => {
    clearTimeout(timers.current.get(id))
    timers.current.delete(id)
    setToasts((previous) => previous.filter((toast) => toast.id !== id))
  }, [])
  const showToast = useCallback((kind: ToastKind, message: string) => {
    const id = nextId++
    setToasts((previous) => [...previous.slice(-3), { id, kind, message }])
    timers.current.set(id, setTimeout(() => dismiss(id), kind === 'error' ? 12000 : 6000))
  }, [dismiss])
  useEffect(() => {
    const activeTimers = timers.current
    return () => { activeTimers.forEach(clearTimeout); activeTimers.clear() }
  }, [])
  const icons = { success: CheckCircle2, error: AlertCircle, info: Info }
  return <ToastContext.Provider value={{ showToast }}>
    {children}
    {createPortal(<div className="toast-region" aria-label="Notifications">
      {toasts.map((toast) => {
        const Icon = icons[toast.kind]
        return <div key={toast.id} className={`toast toast-${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}>
          <Icon size={18} aria-hidden="true" /><span>{toast.message}</span>
          <button className="btn-ghost" aria-label="Dismiss notification" onClick={() => dismiss(toast.id)}><X size={16} /></button>
        </div>
      })}
    </div>, target)}
  </ToastContext.Provider>
}
