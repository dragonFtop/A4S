import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  BookOpen,
  CalendarClock,
  FileText,
  Layers,
  ListChecks,
  MessageSquareText,
  NotebookPen,
  Play,
  RefreshCw,
  Sparkles,
  Target,
  TriangleAlert,
  Upload,
} from 'lucide-react'
import { getOverview, type OverviewData } from '../api/client'
import Button from '../components/ui/Button'
import Empty, { SkeletonList, Spinner } from '../components/ui/Feedback'
import HelpTip from '../components/ui/HelpTip'
import { formatRelative, formatScore, greeting } from '../utils/format'

const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

interface Advice {
  text: React.ReactNode
  to: string
  action: string
}

export default function Overview() {
  const [data, setData] = useState<OverviewData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
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

  const chart = useMemo(() => {
    if (!data) return { items: [], max: 1, peak: -1 }
    const items = data.daily.map((d) => ({ ...d, total: d.documents + d.practices }))
    const max = Math.max(1, ...items.map((d) => d.total))
    let peak = -1
    items.forEach((d, i) => {
      if (d.total > 0 && d.total === max) peak = i
    })
    return { items, max, peak }
  }, [data])

  if (error) {
    return (
      <section className="page">
        <div className="card">
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
    <section className="page">
      <div className="hero">
        <div className="hero-content">
          <div>
            <div className="hero-eyebrow">
              <span className={data ? 'dot dot-ok' : 'dot'} />
              {today} · 本地工作区
            </div>
            <h1 className="hero-title">{headline}</h1>
            <p className="hero-sub">
              {data && data.documents.total > 0
                ? `已归档 ${data.documents.total} 份资料、${data.documents.chunks} 个知识片段。`
                : '先把资料导进来，后面的问答、出题、回顾都以它为依据。'}
              <HelpTip label="了解本地工作区">
                全部在本机运行：资料不上传，模型由你配置，检索、出题与报告都在本地完成。
                换台电脑时把 data 目录一起带走即可。
              </HelpTip>
            </p>
          </div>
          <div className="hero-actions">
            <Button
              variant="primary"
              icon={<Upload size={14} />}
              onClick={() => navigate('/library?upload=1')}
            >
              导入资料
            </Button>
            <Button icon={<Play size={14} />} onClick={() => navigate('/practice')}>
              开始练习
            </Button>
          </div>
        </div>
      </div>

      {loading && !data ? (
        <div className="grid-stats">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="stat-card">
              <div className="skeleton" style={{ width: 56, height: 12 }} />
              <div className="skeleton" style={{ width: 88, height: 28 }} />
            </div>
          ))}
        </div>
      ) : data ? (
        <div className="grid-stats">
          <StatCard
            icon={<FileText size={13} />}
            label="资料总数"
            value={data.documents.total}
            unit="份"
            note={`就绪 ${data.documents.ready} · 共 ${data.documents.chunks} 片段`}
          />
          <StatCard
            icon={<Layers size={13} />}
            label="知识片段"
            value={data.documents.chunks}
            unit="个"
            note={`近 7 天新增 ${data.documents.added_last_7d} 份资料`}
          />
          <StatCard
            icon={<Target size={13} />}
            label="练习正确率"
            value={data.practice.finished > 0 ? formatScore(data.practice.avg_score) : '—'}
            note={`已完成 ${data.practice.finished} 套练习`}
          />
          <StatCard
            icon={<NotebookPen size={13} />}
            label="待复习错题"
            value={data.wrong.total}
            unit="道"
            alert={data.wrong.total > 0}
            note={
              data.wrong.top_repeated > 0
                ? `其中 ${data.wrong.top_repeated} 道重复做错`
                : '暂无重复错题'
            }
          />
        </div>
      ) : null}

      <div className="split">
        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h2>
                <Sparkles size={16} /> 学习轨迹
                <HelpTip>按天统计导入的资料份数与完成的练习套数，最高的一天标为琥珀色。只统计最近 7 天。</HelpTip>
              </h2>
              <span className="head-note">近 7 天</span>
            </div>
            {chart.items.some((d) => d.total > 0) ? (
              <div className="bars">
                {chart.items.map((d, i) => (
                  <div
                    key={d.date}
                    className={`bar-col${i === chart.peak ? ' is-peak' : ''}`}
                    title={`${d.date}：${d.documents} 份资料 / ${d.practices} 次练习`}
                  >
                    <div
                      className={`bar${d.total === 0 ? ' is-empty' : ''}`}
                      data-active={d.total > 0}
                      style={{ height: `${Math.max(3, (d.total / chart.max) * 100)}%` }}
                    />
                    <span className="bar-label">{Number(d.date.slice(-2))}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ padding: '0 18px 18px' }}>
                <div className="notice">
                  <Sparkles />
                  <span>近 7 天还没有记录</span>
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <h2>
                <BookOpen size={16} /> 最近资料
              </h2>
              <Link to="/library" className="card-link">
                全部 {data?.documents.total ?? 0} 份
              </Link>
            </div>
            {loading && !data ? (
              <SkeletonList />
            ) : data && data.recent_documents.length > 0 ? (
              <div className="list">
                {data.recent_documents.map((doc) => {
                  const failed = doc.status === 'failed'
                  return (
                    <Link
                      key={doc.id}
                      to={`/library?topic=${doc.topic?.id ?? ''}`}
                      className="list-item"
                    >
                      <span className={`list-item-icon${failed ? ' is-fail' : ''}`}>
                        {doc.ext.replace('.', '') || 'txt'}
                      </span>
                      <span className="list-item-main">
                        <span className="list-item-title ov-doc-title">
                          <i
                            className="dot"
                            style={{ background: doc.topic?.color ?? '#888780' }}
                          />
                          {doc.name}
                        </span>
                        <span className="list-item-meta">
                          <span className={`status status-${doc.status}`}>
                            {doc.status === 'ready'
                              ? '已就绪'
                              : failed
                                ? '解析失败'
                                : '处理中'}
                          </span>
                          <span>{doc.topic?.name ?? '未分类'}</span>
                          <span>{doc.chunk_count} 片段</span>
                          <span>{formatRelative(doc.created_at)}</span>
                        </span>
                      </span>
                      <ArrowRight size={14} className="text-tertiary" />
                    </Link>
                  )
                })}
              </div>
            ) : (
              <Empty
                icon={<Upload size={18} />}
                title="知识库还是空的"
                description="支持 PDF、Word、Markdown 与纯文本。"
              >
                <Button variant="primary" size="sm" onClick={() => navigate('/library')}>
                  导入第一份资料
                </Button>
              </Empty>
            )}
          </div>

          {data && data.activity.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h2>
                  <ListChecks size={16} /> 学习动态
                  <HelpTip>最近发生的导入、练习与回顾记录，新的在前。</HelpTip>
                </h2>
              </div>
              <div className="list">
                {data.activity.map((item, i) => (
                  <div key={i} className="list-item">
                    <span className="list-item-icon">
                      {item.kind === 'document' ? 'doc' : item.kind === 'practice' ? 'quiz' : 'rpt'}
                    </span>
                    <span className="list-item-main">
                      <span className="list-item-title">{item.title}</span>
                      <span className="list-item-meta">
                        <span>{item.detail}</span>
                        <span>{formatRelative(item.at)}</span>
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h2>
                <Sparkles size={16} /> 今日建议
                <HelpTip>根据资料状态、错题数量与练习记录排出优先级，最多给三条。点任意一条直接跳到对应页面。</HelpTip>
              </h2>
            </div>
            <div className="advice">
              {advice.map((item, i) => (
                <button key={i} className="advice-item" onClick={() => navigate(item.to)}>
                  <span className="advice-idx">{i + 1}</span>
                  <span className="advice-text">{item.text}</span>
                  <span className="advice-act">{item.action}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h2>
                <MessageSquareText size={16} /> 快捷入口
              </h2>
            </div>
            <div className="quick-grid">
              <Link to="/chat" className="quick-item">
                <MessageSquareText />
                <span className="quick-item-text">
                  <span className="quick-item-title">提问</span>
                  <span className="quick-item-desc">基于资料作答</span>
                </span>
              </Link>
              <Link to="/practice" className="quick-item">
                <ListChecks />
                <span className="quick-item-text">
                  <span className="quick-item-title">出题</span>
                  <span className="quick-item-desc">自测掌握程度</span>
                </span>
              </Link>
              <Link to="/wrong" className="quick-item">
                <NotebookPen />
                <span className="quick-item-text">
                  <span className="quick-item-title">错题本</span>
                  <span className="quick-item-desc">薄弱点重练</span>
                </span>
              </Link>
              <Link to="/review" className="quick-item">
                <CalendarClock />
                <span className="quick-item-text">
                  <span className="quick-item-title">回顾</span>
                  <span className="quick-item-desc">生成学习周报</span>
                </span>
              </Link>
            </div>
          </div>

          {data && data.recent_wrong.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h2>
                  <TriangleAlert size={16} /> 薄弱知识点
                  <HelpTip>按「错了几次」排序的知识点，次数越多越该先看。</HelpTip>
                </h2>
                <Link to="/wrong" className="card-link">
                  全部
                </Link>
              </div>
              <div className="list">
                {data.recent_wrong.map((item) => (
                  <Link key={item.id} to="/wrong" className="list-item">
                    <span className="list-item-main">
                      <span className="list-item-title">{item.stem || '（题干缺失）'}</span>
                      <span className="list-item-meta">
                        <span className="text-danger">错 {item.wrong_count} 次</span>
                        <span>{formatRelative(item.last_wrong_at)}</span>
                      </span>
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {loading && data && <Spinner text="刷新中…" />}
    </section>
  )
}

function StatCard({
  icon,
  label,
  value,
  unit,
  note,
  alert,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
  unit?: string
  note?: string
  alert?: boolean
}) {
  return (
    <div className={alert ? 'stat-card is-alert' : 'stat-card'}>
      <div className="stat-card-head">
        <span className="stat-label">{label}</span>
        <span className="stat-icon">{icon}</span>
      </div>
      <div className="stat">
        <span className="stat-value">
          {value}
          {unit && <span className="stat-unit">{unit}</span>}
        </span>
        {note && <span className="stat-delta">{note}</span>}
      </div>
    </div>
  )
}
