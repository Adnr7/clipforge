import { createContext, useContext } from 'react'
export type ToastKind = 'success' | 'error' | 'info'
export const ToastContext = createContext<{ showToast: (kind: ToastKind, message: string) => void }>({ showToast: () => {} })
export function useToast() { return useContext(ToastContext) }
