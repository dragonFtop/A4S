import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

// 换过一套配色，旧键里存的是上一版自动写入的偏好，换新键让老用户也落到新的浅色默认
const STORAGE_KEY = 'a4s-theme-v2'

function resolveInitial(): Theme {
  // 支持 ?theme=dark 直接指定（便于截图、演示与分享固定主题的链接）
  const forced = new URLSearchParams(window.location.search).get('theme')
  if (forced === 'light' || forced === 'dark') return forced
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  // 默认浅色：产品以蓝白为主视觉，不跟随系统的深色偏好
  return 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(resolveInitial)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }, [])

  return { theme, setTheme, toggle }
}
