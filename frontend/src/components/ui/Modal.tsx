import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

export default function Modal({ title, onClose, children, width = 540, className = '', closeOnBackdrop = true, closeDisabled = false }: {
  title: string; onClose: () => void; children: ReactNode; width?: number; className?: string; closeOnBackdrop?: boolean; closeDisabled?: boolean
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current!
    dialog.showModal()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.close()
      document.body.style.overflow = overflow
      if (previous?.isConnected) previous.focus()
    }
  }, [])
  return createPortal(
    <dialog ref={dialogRef} className={`modal ${className}`.trim()} style={{ maxWidth: width }} aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return
        const elements = [...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, [tabindex="0"]')].filter((element) => !element.matches(':disabled') && element.getClientRects().length > 0)
        const first = elements[0], last = elements.at(-1)
        if (event.shiftKey && (document.activeElement === first || !event.currentTarget.contains(document.activeElement))) {
          event.preventDefault(); last?.focus()
        } else if (!event.shiftKey && (document.activeElement === last || !event.currentTarget.contains(document.activeElement))) {
          event.preventDefault(); first?.focus()
        }
      }}
      onCancel={(event) => { event.preventDefault(); if (!closeDisabled) onClose() }}
       onClick={(event) => {
         if (!closeOnBackdrop || closeDisabled) return
         if (event.target !== event.currentTarget) return
        const rect = event.currentTarget.getBoundingClientRect()
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose()
      }}>
      <header className="modal-header">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="btn-ghost" disabled={closeDisabled} aria-label={`Close ${title}`} onClick={onClose}><X size={20} /></button>
      </header>
      <div className="modal-body">{children}</div>
    </dialog>, document.body,
  )
}
