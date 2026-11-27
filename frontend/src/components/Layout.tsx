import { useEffect, useMemo, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import {
  BookOpen,
  CalendarClock,
  FileText,
  Handshake,
  LayoutDashboard,
  ListChecks,
  MessageSquareText,
  Moon,
  NotebookPen,
  Settings2,
  Sparkles,
  LogOut,
  Sun,
  Users,
} from 'lucide-react'
import { getHealth, getOverview, serverApi, type Health, type ServerStatus } from '../api/client'
import { useTheme } from '../hooks/useTheme'
import { useSpace } from '../context/SpaceContext'

const NAV_GROUPS = [
  {
    title: 'Plan',
    items: [{ to: '/plan', label: '计划助理', icon: Sparkles, count: null }],
  },
  {
    title: '学习',
    items: [
      { to: '/overview', label: '概览', icon: LayoutDashboard, count: null },
      { to: '/library', label: '知识库', icon: BookOpen, count: 'library' },
      { to: '/notes', label: '笔记', icon: FileText, count: 'notes' },
      { to: '/chat', label: '问答', icon: MessageSquareText, count: null },
      { to: '/practice', label: '练习', icon: ListChecks, count: null },
      { to: '/wrong', label: '错题本', icon: NotebookPen, count: 'wrong' },
    ],
  },
  {
    title: '记录',
    items: [{ to: '/review', label: '回顾', icon: CalendarClock, count: null }],
  },
  {
    title: '协作',
    items: [
      { to: '/spaces', label: '空间', icon: Users, count: null },
      { to: '/shares', label: '共享', icon: Handshake, count: null },
    ],
  },
]

const TITLES: Record<string, string> = {
  '/overview': '概览',
  '/library': '知识库',
  '/notes': '笔记',
  '/chat': '问答',
  '/practice': '练习',
  '/wrong': '错题本',
  '/plan': '计划助理',
  '/review': '回顾',
  '/spaces': '空间与成员',
  '/shares': '共享管理',
  '/settings': '设置',
}

const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

export default function Layout({ children }: { children: React.ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [offline, setOffline] = useState(false)
  const [server, setServer] = useState<ServerStatus | null>(null)
  const [counts, setCounts] = useState<{ library?: number; wrong?: number; notes?: number }>({})
  const { theme, toggle } = useTheme()
  const location = useLocation()
  const { user, spaces, current, switchTo, logout } = useSpace()

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setOffline(true))
  }, [])

  // 服务端在线状态：disabled / online / offline，10s 轮询一次
  useEffect(() => {
    let alive = true
    const tick = () => {
      serverApi
        .status()
        .then((s) => {
          if (alive) setServer(s)
        })
        .catch(() => {
          if (alive) setServer({ state: 'offline', online: false, url: '', latency_ms: null, detail: null })
        })
    }
    tick()
    const timer = window.setInterval(tick, 10_000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  // 导航上的计数：只在页面切换后刷新一次，成本可忽略
  useEffect(() => {
    getOverview()
      .then((d) =>
        setCounts({
          library: d.documents.total,
          wrong: d.wrong.total,
          notes: d.notes?.total ?? 0,
        })
      )
      .catch(() => setCounts({}))
  }, [location.pathname])

  const online = health?.status === 'ok'
  // 前缀匹配：/notes/:id 也要算作「笔记」，否则详情页面包屑会掉回概览
  const title =
    Object.keys(TITLES)
      .filter((path) => location.pathname === path || location.pathname.startsWith(`${path}/`))
      .sort((a, b) => b.length - a.length)
      .map((path) => TITLES[path])[0] ?? '概览'

  const today = useMemo(() => {
    const d = new Date()
    return `${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')} · ${
      WEEKDAY[d.getDay()]
    }`
  }, [])

  return (
    <div className="app">
      <div className="bg" aria-hidden="true">
        <div className="aurora a1" />
        <div className="aurora a2" />
        <div className="aurora a3" />
        <div className="bg-grid" />
        <div className="bg-noise" />
      </div>

      <aside className="rail">
        <div className="brand">
          <img className="brand-mark" src="/a4s-mark.png" alt="" aria-hidden="true" />
          <div className="brand-text">
            <span className="brand-name">A4S</span>
            <span className="brand-sub">Agent for students</span>
          </div>
        </div>

        <details className="space-switch">
          <summary title={`当前空间：${current?.name ?? '未选择'}`}>
            <span className="space-icon">
              {current?.kind === 'group' ? <Users size={14} /> : <FileText size={14} />}
            </span>
            <span className="space-switch-text">
              <span className="space-switch-name">{current?.name ?? '未选择空间'}</span>
              <span className="space-switch-kind">
                {current?.kind === 'group'
                  ? `小组 · ${current.member_count} 人`
                  : '个人空间'}
              </span>
            </span>
          </summary>
          <div className="space-menu">
            {spaces.map((item) => (
              <button
                key={item.id}
                type="button"
                className={item.id === current?.id ? 'space-menu-item active' : 'space-menu-item'}
                onClick={() => {
                  if (item.id !== current?.id) switchTo(item.id)
                }}
              >
                <span className="truncate">{item.name}</span>
                <span className="space-menu-kind">
                  {item.kind === 'group' ? `${item.member_count} 人` : '个人'}
                </span>
              </button>
            ))}
            <Link className="space-menu-manage" to="/spaces">
              管理空间与成员
            </Link>
          </div>
        </details>

        <nav className="nav-groups" aria-label="主导航">
          {NAV_GROUPS.map((group) => (
            <div key={group.title} className="nav-group">
              <div className="nav-label">{group.title}</div>
              {group.items.map((item) => {
                const Icon = item.icon
                const count = item.count
                  ? counts[item.count as 'library' | 'wrong' | 'notes']
                  : undefined
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
                  >
                    <Icon />
                    <span>{item.label}</span>
                    {count ? <span className="nav-count">{count}</span> : null}
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>

        <div className="rail-foot">
          <NavLink
            to="/settings"
            className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
          >
            <Settings2 />
            <span>设置</span>
          </NavLink>

          <button className="theme-toggle" onClick={toggle} aria-label="切换明暗主题">
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            <span>{theme === 'dark' ? '浅色模式' : '深色模式'}</span>
          </button>

          <div className="rail-user">
            <span className="avatar small">
              {(user?.display_name || user?.username || '?').slice(0, 1)}
            </span>
            <span className="rail-user-name truncate">
              {user?.display_name || user?.username}
            </span>
            <button
              type="button"
              className="icon-btn"
              title="退出登录"
              onClick={() => void logout()}
            >
              <LogOut size={15} />
            </button>
          </div>

          <div className="rail-status">
            <span className={online ? 'dot dot-ok' : 'dot'} />
            <span className="truncate">
              {offline ? '后端未连接' : online ? `v${health?.version} · 本地` : '连接中…'}
            </span>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <span className="crumb">{title}</span>
          <span className={online ? 'status-pill' : 'status-pill is-off'}>
            <i />
            {offline ? '离线' : online ? '本地工作区' : '连接中'}
          </span>
          {server && (
            <span
              className={server.state === 'online' ? 'status-pill' : 'status-pill is-off'}
              data-server-state={server.state}
              title={
                server.state === 'disabled'
                  ? '未配置服务端，纯本地运行'
                  : server.url
              }
            >
              <i />
              {server.state === 'disabled'
                ? '纯本地'
                : server.state === 'online'
                  ? '服务器在线'
                  : '服务器离线'}
            </span>
          )}
          <div className="topbar-right">
            <span className="topbar-date">{today}</span>
          </div>
        </header>

        <div className="content">{children}</div>
      </main>
    </div>
  )
}
