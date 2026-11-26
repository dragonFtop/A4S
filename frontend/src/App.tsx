import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { ToastProvider } from './components/ui/Toast'
import { Spinner } from './components/ui/Feedback'
import Overview from './pages/Overview'
import KnowledgeBase from './pages/KnowledgeBase'
import Practice from './pages/Practice'
import WrongBook from './pages/WrongBook'
import Settings from './pages/Settings'

// 这些页面会带进 Markdown 渲染器与 KaTeX（含字体，约 650 kB），
// 而首屏（概览/知识库）完全用不到，所以按需加载，别让首屏等它。
// 回顾页也要读报告正文，同样按需加载——否则它会把主包撑大一倍。
const Notes = lazy(() => import('./pages/Notes'))
const NoteEditor = lazy(() => import('./pages/NoteEditor'))
const Chat = lazy(() => import('./pages/Chat'))
const Review = lazy(() => import('./pages/Review'))
const Plan = lazy(() => import('./pages/Plan'))

export default function App() {
  return (
    <ToastProvider>
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
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </Suspense>
      </Layout>
    </ToastProvider>
  )
}
