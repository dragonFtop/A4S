import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, ArrowUp, RefreshCw, TriangleAlert } from 'lucide-react'
import { getOverview, type OverviewData } from '../api/client'
import Button from '../components/ui/Button'
import Empty, { SkeletonList, Spinner } from '../components/ui/Feedback'
import { formatRelative, formatScore, greeting } from '../utils/format'

const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

const KIND_LABEL: Record<string, string> = {
  document: '资料',
  practice: '练习',
  review: '回顾',
}

const QUICK_ASKS = [
  '这份资料的核心结论是什么？',
  '帮我列出这一章的重点',
  '用一句话解释最难的概念',
]

interface Advice {
  text: React.ReactNode
  to: string
  action: string
}

export default function Overview() {
  const [data, setData] = useState<OverviewData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [question, setQuestion] = useState('')
  const navigate = useNavigate()

  const load = () => {
    setLoading(true)
    setError('')
    getOverview()
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const today = useMemo(() => {
    const d = new Date()
    return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(
      d.getDate()
    ).padStart(2, '0')} ${WEEKDAY[d.getDay()]}`
  }, [])

  /* 标题里只高亮一个词——它指向今天最该做的那件事 */
  const headline = useMemo(() => {
    const hello = greeting()
    if (!data || data.documents.total === 0) {
      return (
        <>
          {hello}，先把资料<em>导进来</em>
        </>
      )
    }
    if (data.wrong.total > 0) {
      return (
        <>
          {hello}，从<em>错题</em>开始更快见效
        </>
      )
    }
    return (
      <>
        {hello}，今天<em>练一套</em>怎么样
      </>
    )
  }, [data])

  const advice = useMemo<Advice[]>(() => {
    if (!data) return []
    const list: Advice[] = []
    if (data.documents.total === 0) {
      list.push({
        text: <>先导入一份资料，问答和出题才有依据。</>,
        to: '/library',
        action: '去导入',
      })
    }
    if (data.documents.failed > 0) {
      list.push({
        text: (
          <>
            有 <b>{data.documents.failed} 份</b>资料解析失败。
          </>
        ),
        to: '/library',
        action: '去查看',
      })
    }
    if (data.wrong.total > 0) {
      list.push({
        text: (
          <>
            错题本里 <b>{data.wrong.total} 道</b>待复习
            {data.wrong.top_repeated > 0 ? `，其中 ${data.wrong.top_repeated} 道是重复做错` : ''}。
          </>
        ),
        to: '/wrong',
        action: '去复习',
      })
    }
    if (data.documents.ready > 0 && data.practice.finished === 0) {
      list.push({
        text: <>资料已就绪，生成第一套检测题。</>,
        to: '/practice',
        action: '生成题目',
      })
    }
    if (data.review.reports === 0 && data.documents.ready > 0) {
      list.push({
        text: <>还没生成过回顾报告。</>,
        to: '/review',
        action: '生成回顾',
      })
    }
    if (list.length === 0) {
      list.push({
        text: <>先清错题，再练一套新题。</>,
        to: '/practice',
        action: '继续练习',
      })
    }
    return list.slice(0, 3)
  }, [data])

  const ask = (text?: string) => {
    const q = (text ?? question).trim()
    if (!q) return
    navigate(`/chat?q=${encodeURIComponent(q)}`)
  }

  if (error) {
    return (
      <section className="page">
        <div className="ov-error">
          <Empty icon={<TriangleAlert size={20} />} title="无法加载概览数据" description={error}>
            <Button icon={<RefreshCw size={14} />} onClick={load}>
              重试
            </Button>
          </Empty>
        </div>
      </section>
    )
  }

  return (
    <section className="page ov">
      <div className="ov-intro">
        <div className="ov-date">
          <span className={data ? 'dot dot-ok' : 'dot'} />
          {today}
        </div>
        <h1 className="ov-title">{headline}</h1>
        <p className="ov-sub">
          {data && data.documents.total > 0
            ? `已归档 ${data.documents.total} 份资料 · ${data.documents.chunks} 个知识片段`
            : '先导入资料，问答、出题与回顾都基于它。'}
        </p>

        <form
          className="ov-ask"
          onSubmit={(e) => {
            e.preventDefault()
            ask()
          }}
        >
          <textarea
            value={question}
            rows={1}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                ask()
              }
            }}
            placeholder="问点什么，比如：第三章的核心结论是什么？"
            aria-label="向知识库提问"
          />
          <div className="ov-ask-bar">
            <span className="ov-ask-hint">Enter 发送</span>
            <button
              type="submit"
              className="ov-ask-send"
              disabled={!question.trim()}
              aria-label="发送"
            >
              <ArrowUp />
            </button>
          </div>
        </form>

        <div className="ov-asks">
          {QUICK_ASKS.map((q) => (
            <button key={q} className="ov-ask-chip" onClick={() => ask(q)}>
              {q}
            </button>
          ))}
        </div>
      </div>

      {loading && !data ? (
        <div className="figures">
          <div className="figure">
            <div className="skeleton" style={{ width: 52, height: 22 }} />
            <div className="skeleton" style={{ width: 64, height: 10 }} />
          </div>
          <div className="figure">
            <div className="skeleton" style={{ width: 52, height: 22 }} />
            <div className="skeleton" style={{ width: 64, height: 10 }} />
          </div>
          <div className="figure">
            <div className="skeleton" style={{ width: 52, height: 22 }} />
            <div className="skeleton" style={{ width: 64, height: 10 }} />
          </div>
        </div>
      ) : data ? (
        <div className="figures">
          <div className="figure">
            <span className="figure-value">
              {data.documents.total}
              <i>份</i>
            </span>
            <span className="figure-label">资料 · 就绪 {data.documents.ready}</span>
          </div>
          <div className="figure">
            <span className="figure-value">
              {data.documents.chunks}
              <i>个</i>
            </span>
            <span className="figure-label">知识片段</span>
          </div>
          <div className="figure">
            <span className="figure-value">
              {data.practice.finished > 0 ? formatScore(data.practice.avg_score) : '—'}
            </span>
            <span className="figure-label">练习正确率 · {data.practice.finished} 套</span>
          </div>
          <div className={`figure${data.wrong.total > 0 ? ' is-danger' : ''}`}>
            <span className="figure-value">
              {data.wrong.total}
              <i>道</i>
            </span>
            <span className="figure-label">待复习错题</span>
          </div>
          <span className="figure-note">
            近 7 天新增 {data.documents.added_last_7d} 份资料 · {data.practice.finished_last_7d} 套练习
          </span>
        </div>
      ) : null}

      <div className="ov-body">
        <div className="ov-col">
          <section className="ov-block">
            <header className="ov-block-head">
              <h2>最近资料</h2>
              <Link to="/library">全部 {data?.documents.total ?? 0} 份</Link>
            </header>
            {loading && !data ? (
              <SkeletonList rows={5} />
            ) : data && data.recent_documents.length > 0 ? (
              <div>
                {data.recent_documents.map((doc) => {
                  const failed = doc.status === 'failed'
                  return (
                    <Link
                      key={doc.id}
                      to={`/library?topic=${doc.topic?.id ?? ''}`}
                      className="ov-row"
                    >
                      <span className="ov-row-ext">{doc.ext.replace('.', '') || 'txt'}</span>
                      <span className="ov-row-main">
                        <span className="ov-row-title">{doc.name}</span>
                        <span className="ov-row-meta">
                          <span>{doc.topic?.name ?? '未分类'}</span>
                          <span>{doc.chunk_count} 片段</span>
                          <span>{formatRelative(doc.created_at)}</span>
                        </span>
                      </span>
                      <span
                        className={`ov-row-state${
                          failed ? ' is-fail' : doc.status === 'ready' ? '' : ' is-busy'
                        }`}
                      >
                        {doc.status === 'ready' ? '已就绪' : failed ? '解析失败' : '处理中'}
                      </span>
                    </Link>
                  )
                })}
              </div>
            ) : (
              <div className="ov-empty">
                <span>知识库还是空的，支持 PDF、Word、Markdown 与纯文本。</span>
                <button onClick={() => navigate('/library')}>导入第一份资料 →</button>
              </div>
            )}
          </section>

          {data && data.activity.length > 0 && (
            <section className="ov-block">
              <header className="ov-block-head">
                <h2>学习动态</h2>
                <span className="ov-block-note">最近 {data.activity.length} 条</span>
              </header>
              <div>
                {data.activity.map((item, i) => (
                  <div key={i} className="ov-row">
                    <span className="ov-row-ext">{KIND_LABEL[item.kind] ?? item.kind}</span>
                    <span className="ov-row-main">
                      <span className="ov-row-title">{item.title}</span>
                      <span className="ov-row-meta">
                        <span>{item.detail}</span>
                        <span>{formatRelative(item.at)}</span>
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="ov-col">
          <section className="ov-block">
            <header className="ov-block-head">
              <h2>今日建议</h2>
            </header>
            <div>
              {advice.map((item, i) => (
                <button key={i} className="ov-advice" onClick={() => navigate(item.to)}>
                  <span className="ov-advice-idx">{String(i + 1).padStart(2, '0')}</span>
                  <span className="ov-advice-text">{item.text}</span>
                  <ArrowRight />
                </button>
              ))}
            </div>
          </section>

          {data && data.recent_wrong.length > 0 && (
            <section className="ov-block">
              <header className="ov-block-head">
                <h2>薄弱知识点</h2>
                <Link to="/wrong">全部</Link>
              </header>
              <div>
                {data.recent_wrong.map((item) => (
                  <Link key={item.id} to="/wrong" className="ov-row">
                    <span className="ov-row-main">
                      <span className="ov-row-title">{item.stem || '（题干缺失）'}</span>
                      <span className="ov-row-meta">
                        <span>{formatRelative(item.last_wrong_at)}</span>
                      </span>
                    </span>
                    <span className="ov-row-state is-fail">错 {item.wrong_count} 次</span>
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>

      {loading && data && <Spinner text="刷新中…" />}
    </section>
  )
}
