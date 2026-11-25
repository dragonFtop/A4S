import { useState } from 'react'
import { Check, Compass } from 'lucide-react'
import type { TopicPack } from '../../api/client'
import Button from '../ui/Button'
import Modal from '../ui/Modal'

interface Props {
  open: boolean
  packs: TopicPack[]
  onApply: (packIds: string[]) => Promise<void>
  onSkip: () => void
}

const MAX_PACKS = 3

/** 零主题时的引导：选学习方向，一键建好主题与默认标签（PRD FR-12 规则 4）。 */
export default function PackPicker({ open, packs, onApply, onSkip }: Props) {
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const toggle = (id: string) => {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((p) => p !== id)
      if (prev.length >= MAX_PACKS) return prev
      return [...prev, id]
    })
  }

  const submit = async () => {
    if (!selected.length) return
    setBusy(true)
    try {
      await onApply(selected)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title="你在学什么？"
      description="选 1~3 个方向，我会把这些主题建好。之后随时可以增删改。"
      onClose={onSkip}
      footer={
        <>
          <Button variant="ghost" onClick={onSkip} disabled={busy}>
            先跳过
          </Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={!selected.length}
            onClick={submit}
          >
            {selected.length ? `建 ${selected.length} 个方向` : '选择方向'}
          </Button>
        </>
      }
    >
      <div className="pack-grid">
        {packs.map((pack) => {
          const on = selected.includes(pack.id)
          return (
            <button
              key={pack.id}
              type="button"
              className={`pack-card${on ? ' is-on' : ''}`}
              onClick={() => toggle(pack.id)}
              aria-pressed={on}
            >
              <span className="pack-head">
                <span className="pack-name">{pack.name}</span>
                {on ? <Check size={14} /> : <Compass size={14} className="pack-icon" />}
              </span>
              <span className="pack-summary">{pack.summary}</span>
              <span className="pack-topics">{pack.topics.slice(0, 4).join(' · ')}…</span>
            </button>
          )
        })}
      </div>
      <p className="pack-hint">
        每个方向会附带「重点 / 公式 / 代码 / 易错 / 待整理」五个标签。最多选 {MAX_PACKS} 个。
      </p>
    </Modal>
  )
}
