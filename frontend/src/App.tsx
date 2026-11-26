import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import Layout from './components/Layout'
import { ToastProvider } from './components/ui/Toast'
import { Spinner } from './components/ui/Feedback'
import { setUnauthorizedHandler } from './api/client'
import { SpaceProvider, useSpace } from './context/SpaceContext'
import Overview from './pages/Overview'
import KnowledgeBase from './pages/KnowledgeBase'
import Practice from './pages/Practice'
import WrongBook from './pages/WrongBook'
import Settings from './pages/Settings'
import Login from './pages/Login'
import Spaces from './pages/Spaces'

// 这些页面会带进 Markdown 渲染器与 KaTeX（含字体，约 650 kB），
// 而首屏（概览/知识库）完全用不到，所以按需加载，别让首屏等它。
// 回顾页也要读报告正文，同样按需加载——否则它会把主包撑大一倍。
const Notes = lazy(() => import('./pages/Notes'))
const NoteEditor = lazy(() => import('./pages/NoteEditor'))
const Chat = lazy(() => import('./pages/Chat'))
const Review = lazy(() => import('./pages/Review'))
const Plan = lazy(() => import('./pages/Plan'))

function Authed() {
  return (
    <Layout>
      <Suspense fallback={<Spinner text="正在加载…" />}>
        <Routes>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/library" element={<KnowledgeBase />} />
          <Route path="/notes" element={<Notes />} />
          <Route path="/notes/:id" element={<NoteEditor />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/practice" element={<Practice />} />
          <Route path="/wrong" element={<WrongBook />} />
          <Route path="/plan" element={<Plan />} />
          <Route path="/review" element={<Review />} />
          <Route path="/spaces" element={<Spaces />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}

function Shell() {
  const { user, ready } = useSpace()
  const navigate = useNavigate()

  // 401 统一跳登录：任何页面拿到过期 token 都不必各写一遍处理
  useEffect(() => {
    setUnauthorizedHandler(() => navigate('/login', { replace: true }))
  }, [navigate])

  if (!ready) {
    return (
      <div className="auth-page">
        <Spinner text="正在确认登录状态…" />
      </div>
    )
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/overview" replace />} />
      <Route path="*" element={<Authed />} />
    </Routes>
  )
}

export default function App() {
  return (
    <SpaceProvider>
      <ToastProvider>
        <Shell />
      </ToastProvider>
    </SpaceProvider>
  )
}
