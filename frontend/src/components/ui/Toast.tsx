import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from 'lucide-react'

type ToastKind = 'success' | 'error' | 'info' | 'warn'

interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

interface ToastApi {
  success: (message: string) => void
  error: (message: string) => void
  info: (message: string) => void
  warn: (message: string) => void
}

const ToastContext = createContext<ToastApi>({
  success: () => {},
  error: () => {},
  info: () => {},
  warn: () => {},
})

export function useToast() {
  return useContext(ToastContext)
}

const ICONS = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
  warn: TriangleAlert,
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const seq = useRef(0)

  const push = useCallback((message: string, kind: ToastKind) => {
    const id = ++seq.current
    setItems((prev) => [...prev, { id, kind, message }])
    window.setTimeout(() => {
      setItems((prev) => prev.filter((item) => item.id !== id))
    }, 4200)
  }, [])

  const api = useMemo<ToastApi>(
    () => ({
      success: (message) => push(message, 'success'),
      error: (message) => push(message, 'error'),
      info: (message) => push(message, 'info'),
      warn: (message) => push(message, 'warn'),
    }),
    [push]
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-viewport" role="status" aria-live="polite">
        {items.map((item) => {
          const Icon = ICONS[item.kind]
          return (
            <div key={item.id} className={`toast toast-${item.kind}`}>
              <Icon size={15} />
              <span>{item.message}</span>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}
