import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/**
 * 页面里的问号。
 *
 * 原先大段写在页面上的解释文字都收进这里：
 * 界面上只留一个 16px 的圆点，想知道细节再点。
 *
 * - 点击 / 回车切换
 * - 鼠标悬停 260ms 后自动展开（触屏不触发，避免误触）
 * - Esc 或点击外部关闭
 * - 气泡走 Portal，不会被卡片的 overflow: hidden 裁掉
 * - 靠近视口边缘时自动换展开方向、并夹住水平位置
 */
export default function HelpTip({
  children,
  label = '查看说明',
  className = '',
}: {
  children: React.ReactNode
  label?: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [box, setBox] = useState<{ top: number; left: number; arrow: number; drop: 'up' | 'down' } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLSpanElement>(null)
  const wrapRef = useRef<HTMLSpanElement>(null)
  const timer = useRef<number | null>(null)
  const id = useId()

  const show = useCallback(() => setOpen(true), [])
  const hide = useCallback(() => setOpen(false), [])

  // 气泡尺寸要等渲染后才能量，量完再定位，先放在视口外避免闪一下
  useLayoutEffect(() => {
    if (!open) return
    const btn = btnRef.current
    const pop = popRef.current
    if (!btn || !pop) return
    const r = btn.getBoundingClientRect()
    const w = pop.offsetWidth
    const h = pop.offsetHeight
    const gap = 10
    // 靠近页头时向下展开，避免盖住页面标题
    const up = r.top > Math.max(h + gap + 10, 200)
    const top = up ? r.top - h - gap : r.bottom + gap
    const cx = r.left + r.width / 2
    const left = Math.max(10, Math.min(cx - w / 2, window.innerWidth - w - 10))
    setBox({ top, left, arrow: Math.min(Math.max(cx - left, 14), w - 14), drop: up ? 'up' : 'down' })
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hide()
    }
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!wrapRef.current?.contains(t) && !popRef.current?.contains(t)) hide()
    }
    const onScroll = () => hide()
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onScroll)
    }
  }, [open, hide])

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current)
    },
    []
  )

  return (
    <span className={`tip ${className}`.trim()} ref={wrapRef}>
      <button
        ref={btnRef}
        type="button"
        className="tip-btn"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={() => (open ? hide() : show())}
        onMouseEnter={() => {
          timer.current = window.setTimeout(show, 260)
        }}
        onMouseLeave={() => {
          if (timer.current) window.clearTimeout(timer.current)
        }}
        onFocus={show}
      >
        ?
      </button>
      {open
        ? createPortal(
            <span
              id={id}
              role="tooltip"
              ref={popRef}
              className="tip-pop"
              data-drop={box?.drop ?? 'up'}
              style={
                box
                  ? ({ top: box.top, left: box.left, '--tip-arrow': `${box.arrow}px` } as React.CSSProperties)
                  : { top: -9999, left: -9999 }
              }
            >
              {children}
            </span>,
            document.body
          )
        : null}
    </span>
  )
}
