import { useEffect, useRef, type ReactNode } from 'react'
import Button from './ui/Button'

export default function ImmichDialog({ title, busy, close, children }: {
  title: string; busy: boolean; close: () => void; children: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const node = dialog.current
    node?.showModal()
    return () => node?.close()
  }, [])
  return (
    <dialog ref={dialog} aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) close() }}
      className="w-[min(92vw,760px)] max-h-[85vh] overflow-auto rounded-xl bg-bg-secondary text-fg-primary p-5 backdrop:bg-black/60">
      <header className="mb-4 flex items-center justify-between gap-4">
        <h2 className="font-medium">{title}</h2>
        <Button size="sm" variant="ghost" disabled={busy} onClick={close}>关闭</Button>
      </header>
      {children}
    </dialog>
  )
}
