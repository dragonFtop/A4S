import type { ReactNode } from 'react'

interface EmptyProps {
  icon?: ReactNode
  title: string
  description?: string
  children?: ReactNode
}

export default function Empty({ icon, title, description, children }: EmptyProps) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <p className="empty-title">{title}</p>
      {description && <p className="empty-desc">{description}</p>}
      {children && <div className="empty-actions">{children}</div>}
    </div>
  )
}

export function Skeleton({ width = '100%', height = 12, radius = 6 }: { width?: number | string; height?: number | string; radius?: number }) {
  return (
    <div
      className="skeleton"
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  )
}

export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ padding: '12px 16px' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ marginBottom: 12 }}>
          <Skeleton width={`${70 - i * 8}%`} height={13} />
        </div>
      ))}
    </div>
  )
}

export function Spinner({ text }: { text?: string }) {
  return (
    <div className="spinner-box">
      <span className="spinner" />
      {text && <span>{text}</span>}
    </div>
  )
}
