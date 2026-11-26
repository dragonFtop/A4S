import type { ReactNode } from 'react'
import { Construction } from 'lucide-react'
import Empty from './ui/Feedback'

/**
 * 里程碑占位页：用于尚未实现的模块，保证信息完整、视觉与正式页面一致。
 * 每实现一个模块，就把对应页面的 Placeholder 替换为真实页面。
 */
export default function Placeholder({
  title,
  milestone,
  description,
  points,
  icon,
}: {
  title: string
  milestone: string
  description: string
  points: string[]
  icon?: ReactNode
}) {
  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          <p className="page-sub">{description}</p>
        </div>
        <span className="badge">{milestone}</span>
      </header>

      <div className="card">
        <Empty
          icon={icon ?? <Construction size={20} />}
          title={`该模块将在 ${milestone} 实现`}
        >
          <div />
        </Empty>
        <div style={{ padding: '0 24px 28px' }}>
          <ul className="empty-list" style={{ margin: '0 auto' }}>
            {points.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          <p className="muted" style={{ textAlign: 'center', marginTop: 16 }}>
            需求细节见 <code>docs/PRD.md</code>
          </p>
        </div>
      </div>
    </section>
  )
}
