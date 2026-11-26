import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Check,
  ChevronDown,
  FileText,
  ListChecks,
  NotebookPen,
  Play,
  RotateCcw,
  Sparkles,
  Target,
  TriangleAlert,
  X,
} from 'lucide-react'
import Button, { IconButton } from '../components/ui/Button'
import Empty from '../components/ui/Feedback'
import Modal from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import AnswerSheet from '../components/practice/AnswerSheet'
import QuestionBody from '../components/practice/QuestionBody'
import ResultView from '../components/practice/ResultView'
import { errText, formatClock, typeLabel } from '../components/practice/shared'
import {
  deletePracticeQuestion,
  deletePracticeSession,
  generateQuestions,
  getPracticeJob,
  getPracticeMeta,
  getPracticeSession,
  listDocuments,
  listPracticeSessions,
  listTopics,
  startPracticeSession,
  type DocumentItem,
  type PracticeJob,
  type PracticeMeta,
  type PracticeQuestion,
  type PracticeSession,
  type PracticeSessionBrief,
  type QuestionType,
  type Topic,
} from '../api/client'

type ScopeMode = 'topic' | 'docs' | 'all'

const SCOPES: Array<{ key: ScopeMode; label: string; hint: string }> = [
  { key: 'topic', label: '按主题', hint: '整个主题的资料一起出题' },
  { key: 'docs', label: '按资料', hint: '手动挑一份或几份' },
  { key: 'all', label: '全部资料', hint: '可叠加「近 N 天」限定' },
]

const DAY_OPTIONS: Array<{ value: number | null; label: string }> = [
  { value: null, label: '不限时间' },
  { value: 7, label: '近 7 天' },
  { value: 14, label: '近 14 天' },
  { value: 30, label: '近 30 天' },
]

const DEFAULT_COUNTS: Record<QuestionType, number> = {
  single_choice: 5,
  multi_choice: 1,
  true_false: 2,
  short_answer: 2,
}

const STATUS_LABEL: Record<PracticeSession['status'], string> = {
  draft: '草稿',
  in_progress: '作答中',
  submitted: '已提交',
}

/** 练习页：组卷 → 草稿核对 → 作答 → 成绩。三种状态共用一条 session。 */
export default function Practice() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()

  const [meta, setMeta] = useState<PracticeMeta | null>(null)
  const [topics, setTopics] = useState<Topic[]>([])
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [history, setHistory] = useState<PracticeSessionBrief[]>([])

  const [scope, setScope] = useState<ScopeMode>('topic')
  const [topicIds, setTopicIds] = useState<string[]>([])
  const [docIds, setDocIds] = useState<string[]>([])
  const [days, setDays] = useState<number | null>(null)
  const [counts, setCounts] = useState<Record<QuestionType, number>>(DEFAULT_COUNTS)
  const [difficulty, setDifficulty] = useState('medium')

  const [job, setJob] = useState<PracticeJob | null>(null)
  const [generating, setGenerating] = useState(false)
  const [session, setSession] = useState<PracticeSession | null>(null)
  const [starting, setStarting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const [openAnswers, setOpenAnswers] = useState<string[]>([])
  const [openSources, setOpenSources] = useState<string[]>([])
  const draftRef = useRef<HTMLDivElement | null>(null)

  const readyDocs = useMemo(
    () => documents.filter((doc) => doc.status === 'ready'),
    [documents]
  )
  const total = useMemo(
    () => Object.values(counts).reduce((sum, value) => sum + value, 0),
    [counts]
  )
  const maxQuestions = meta?.max_questions ?? 30

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await listPracticeSessions(12))
    } catch {
      // 列表失败不影响组卷，静默即可
    }
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        const [metaData, topicData, docData] = await Promise.all([
          getPracticeMeta(),
          listTopics(),
          listDocuments({ status: 'ready' }),
        ])
        setMeta(metaData)
        setTopics(topicData.topics)
        setDocuments(docData)
      } catch (error) {
        toast.error(errText(error))
      }
      await loadHistory()
    })()
    // 首次加载即可；toast 是 provider 提供的稳定引用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 深链：错题本「重练」跳过来时直接打开那一场。
  // URL 里保留 session 参数——用户中途刷新页面应当回到原来的答题卡，
  // 而不是被扔回组卷页再自己找一遍。
  const focusId = params.get('session')
  const loadedRef = useRef<string | null>(null)
  useEffect(() => {
    if (!focusId || loadedRef.current === focusId) return
    loadedRef.current = focusId
    void (async () => {
      try {
        setSession(await getPracticeSession(focusId))
      } catch (error) {
        toast.error(errText(error))
        setParams({}, { replace: true })
        loadedRef.current = null
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId])

  const focusSession = (sessionId: string) => {
    loadedRef.current = sessionId
    setParams({ session: sessionId }, { replace: true })
  }

  const clearSession = () => {
    loadedRef.current = null
    setSession(null)
    setParams({}, { replace: true })
  }

  // 轮询任务：setTimeout 挂在 job 状态上，页面卸载时自动停止
  useEffect(() => {
    if (!job || job.state !== 'running') return
    const timer = window.setTimeout(async () => {
      try {
        const snapshot = await getPracticeJob(job.id)
        setJob(snapshot)
        if (snapshot.state === 'done' && snapshot.session_id) {
          const detail = await getPracticeSession(snapshot.session_id)
          setSession(detail)
          focusSession(detail.id)
          setOpenAnswers([])
          setOpenSources([])
          setGenerating(false)
          if (snapshot.warnings.length) {
            toast.warn(snapshot.warnings[0])
          } else {
            toast.success(snapshot.message)
          }
          await loadHistory()
          window.setTimeout(() => {
            draftRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }, 60)
        } else if (snapshot.state === 'failed') {
          setGenerating(false)
          toast.error(snapshot.message)
        }
      } catch (error) {
        // 轮询本身失败：停掉提示，别让「正在出题」一直挂着
        setGenerating(false)
        setJob(null)
        toast.error(errText(error))
      }
    }, 1200)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job])

  const toggleIn = (list: string[], value: string) =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value]

  const setTypeCount = (key: QuestionType, value: number) => {
    const next = Math.max(0, Math.min(20, value))
    setCounts((prev) => ({ ...prev, [key]: next }))
  }

  const canGenerate =
    total >= 1 &&
    total <= maxQuestions &&
    (scope !== 'topic' || topicIds.length > 0) &&
    (scope !== 'docs' || docIds.length > 0)

  const onGenerate = async () => {
    if (total < 1) {
      toast.warn('至少设置一道题')
      return
    }
    if (total > maxQuestions) {
      toast.warn(`单次最多 ${maxQuestions} 道题`)
      return
    }
    if (scope === 'topic' && topicIds.length === 0) {
      toast.warn('先选择至少一个主题')
      return
    }
    if (scope === 'docs' && docIds.length === 0) {
      toast.warn('先选择至少一份资料')
      return
    }

    const payload: Parameters<typeof generateQuestions>[0] = {
      counts: Object.fromEntries(
        Object.entries(counts).filter(([, value]) => value > 0)
      ) as Partial<Record<QuestionType, number>>,
      difficulty: difficulty as 'easy' | 'medium' | 'hard',
    }
    if (scope === 'topic') payload.topic_ids = topicIds
    if (scope === 'docs') payload.document_ids = docIds
    if (days) payload.days = days

    setGenerating(true)
    setJob(null)
    try {
      const created = await generateQuestions(payload)
      setJob(created)
    } catch (error) {
      setGenerating(false)
      toast.error(errText(error))
    }
  }

  const removeQuestion = async (questionId: string) => {
    if (!session) return
    try {
      const updated = await deletePracticeQuestion(session.id, questionId)
      setSession(updated)
      toast.success('已从草稿中移除')
    } catch (error) {
      toast.error(errText(error))
    }
  }

  const removeDraft = async () => {
    if (!session) return
    setDeleting(true)
    try {
      await deletePracticeSession(session.id)
      clearSession()
      setConfirmDelete(false)
      toast.success('草稿已删除')
      await loadHistory()
    } catch (error) {
      toast.error(errText(error))
    } finally {
      setDeleting(false)
    }
  }

  const beginAnswer = async (sessionId: string) => {
    setStarting(true)
    try {
      const next = await startPracticeSession(sessionId)
      setSession(next)
      focusSession(next.id)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (error) {
      toast.error(errText(error))
    } finally {
      setStarting(false)
    }
  }

  const openSession = async (sessionId: string) => {
    try {
      const detail = await getPracticeSession(sessionId)
      setSession(detail)
      focusSession(detail.id)
      setOpenAnswers([])
      setOpenSources([])
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (error) {
      toast.error(errText(error))
    }
  }

  const handleSubmitted = async (done: PracticeSession) => {
    setSession(done)
    focusSession(done.id)
    window.scrollTo({ top: 0, behavior: 'smooth' })
    await loadHistory()
    if (done.stats) {
      toast.success(`得分 ${Math.round(done.stats.accuracy)}，错题已收进错题本`)
    }
  }

  const toggleAnswer = (id: string) => setOpenAnswers((prev) => toggleIn(prev, id))
  const toggleSource = (id: string) => setOpenSources((prev) => toggleIn(prev, id))

  const showRunner = session?.status === 'in_progress'
  const showResult = session?.status === 'submitted'
  const showSetup = !session || session.status === 'draft'

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>练习</h1>
          <p className="page-sub">从资料出题，每道题都可核对原文依据。</p>
        </div>
        <div className="page-actions">
          {session && <span className="badge">{STATUS_LABEL[session.status]}</span>}
        </div>
      </header>

      {showRunner && session && (
        <AnswerSheet
          session={session}
          onSubmitted={(done) => void handleSubmitted(done)}
          onExit={() => {
            clearSession()
            void loadHistory()
          }}
        />
      )}

      {showResult && session && (
        <ResultView
          session={session}
          onAgain={() => {
            clearSession()
            void loadHistory()
          }}
        />
      )}

      {showSetup && (
        <>
          {session && (
            <div className="card" ref={draftRef} id="practice-draft">
              <div className="card-head">
                <h2>
                  <ListChecks size={16} /> 题集草稿
                </h2>
                <span className="head-note">
                  {session.questions.length} 题 · {formatClock(session.started_at)} 生成
                </span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<RotateCcw size={13} />}
                    onClick={onGenerate}
                    loading={generating}
                  >
                    按当前配置重出
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    icon={<Play size={13} />}
                    onClick={() => void beginAnswer(session.id)}
                    loading={starting}
                    disabled={session.questions.length === 0}
                  >
                    开始作答
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="pq-danger"
                    icon={<X size={13} />}
                    onClick={() => setConfirmDelete(true)}
                  >
                    删除草稿
                  </Button>
                </div>
              </div>

              {session.questions.length === 0 ? (
                <Empty
                  icon={<TriangleAlert size={18} />}
                  title="草稿里没有题目了"
                  description="题目已全部删除。"
                >
                  <Button size="sm" variant="primary" onClick={onGenerate} loading={generating}>
                    重新生成
                  </Button>
                </Empty>
              ) : (
                <div className="pq-list">
                  {session.questions.map((question, index) => (
                    <QuestionRow
                      key={question.id}
                      index={index + 1}
                      question={question}
                      answerOpen={openAnswers.includes(question.id)}
                      sourceOpen={openSources.includes(question.id)}
                      onToggleAnswer={() => toggleAnswer(question.id)}
                      onToggleSource={() => toggleSource(question.id)}
                      onRemove={() => void removeQuestion(question.id)}
                    />
                  ))}
                </div>
              )}

            </div>
          )}

          <div className="workbench">
            <div className="card">
              <div className="card-head">
                <h2>
                  <Target size={16} /> 组卷
                </h2>
                <span className="head-note">{readyDocs.length} 份可用资料</span>
              </div>
              <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
                <div>
                  <div className="field-label" style={{ marginBottom: 8 }}>
                    出题范围
                  </div>
                  <div className="opt-list">
                    {SCOPES.map((item, index) => (
                      <button
                        key={item.key}
                        className="opt"
                        data-selected={scope === item.key}
                        onClick={() => setScope(item.key)}
                      >
                        <span className="opt-key">{String.fromCharCode(65 + index)}</span>
                        <span style={{ flex: 1, minWidth: 0 }}>
                          <span style={{ display: 'block', fontWeight: 550 }}>{item.label}</span>
                          <span style={{ display: 'block', fontSize: 12, color: 'var(--ink-3)' }}>
                            {item.hint}
                          </span>
                        </span>
                        {scope === item.key && <Check size={14} style={{ color: 'var(--amber)' }} />}
                      </button>
                    ))}
                  </div>
                </div>

                {scope === 'topic' && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 8 }}>
                      选主题
                      {topicIds.length > 0 && (
                        <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                          已选 {topicIds.length} 个
                        </span>
                      )}
                    </div>
                    {topics.length === 0 ? (
                      <p className="muted" style={{ fontSize: 12.5 }}>
                        还没有主题，先去知识库归类。
                      </p>
                    ) : (
                      <div className="chip-row">
                        {topics.map((topic) => (
                          <button
                            key={topic.id}
                            className="chip"
                            onClick={() => setTopicIds((prev) => toggleIn(prev, topic.id))}
                            style={
                              topicIds.includes(topic.id)
                                ? {
                                    color: 'var(--ink)',
                                    borderColor: 'var(--amber-line)',
                                    background: 'var(--amber-soft)',
                                  }
                                : undefined
                            }
                          >
                            <span className="topic-dot" style={{ background: topic.color }} />
                            {topic.name}
                            <span style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{topic.count}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {scope === 'docs' && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 8 }}>
                      选资料
                      {docIds.length > 0 && (
                        <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                          已选 {docIds.length} 份
                        </span>
                      )}
                    </div>
                    {readyDocs.length === 0 ? (
                      <p className="muted" style={{ fontSize: 12.5 }}>
                        没有已完成的资料。导入后等索引跑完再来出题。
                      </p>
                    ) : (
                      <div className="doc-pick">
                        {readyDocs.map((doc) => (
                          <button
                            key={doc.id}
                            className="doc-pick-item"
                            data-selected={docIds.includes(doc.id)}
                            onClick={() => setDocIds((prev) => toggleIn(prev, doc.id))}
                          >
                            <FileText size={13} />
                            <span className="doc-pick-name">{doc.name}</span>
                            <span className="doc-pick-meta">{doc.chunk_count} 段</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                <div>
                  <div className="field-label" style={{ marginBottom: 8 }}>
                    时间过滤
                    <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                      与上面范围取交集
                    </span>
                  </div>
                  <div className="chip-row">
                    {DAY_OPTIONS.map((option) => (
                      <button
                        key={option.label}
                        className="chip"
                        onClick={() => setDays(option.value)}
                        style={
                          days === option.value
                            ? {
                                color: 'var(--ink)',
                                borderColor: 'var(--amber-line)',
                                background: 'var(--amber-soft)',
                              }
                            : undefined
                        }
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <div className="field-label" style={{ marginBottom: 8 }}>
                    题型与数量
                    <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                      共 {total} / {maxQuestions} 题
                    </span>
                  </div>
                  <div className="opt-list">
                    {(meta?.types ?? []).map((type) => {
                      const value = counts[type.key] ?? 0
                      return (
                        <div className="opt is-static qtype-row" key={type.key}>
                          <button
                            type="button"
                            className="qtype-check"
                            data-on={value > 0}
                            aria-label={`${value > 0 ? '取消' : '启用'}${type.label}`}
                            onClick={() => setTypeCount(type.key, value > 0 ? 0 : 2)}
                          >
                            {value > 0 && <Check size={11} />}
                          </button>
                          <span style={{ flex: 1, minWidth: 0 }}>
                            <span style={{ display: 'block', fontWeight: 550 }}>{type.label}</span>
                            <span style={{ display: 'block', fontSize: 12, color: 'var(--ink-3)' }}>
                              {type.hint}
                            </span>
                          </span>
                          <div className="qty" data-off={value === 0}>
                            <button
                              type="button"
                              onClick={() => setTypeCount(type.key, value - 1)}
                              disabled={value === 0}
                              aria-label={`减少${type.label}`}
                            >
                              −
                            </button>
                            <span>{value}</span>
                            <button
                              type="button"
                              onClick={() => setTypeCount(type.key, value + 1)}
                              disabled={total >= maxQuestions}
                              aria-label={`增加${type.label}`}
                            >
                              +
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                <div>
                  <div className="field-label" style={{ marginBottom: 8 }}>
                    难度
                  </div>
                  <div className="chip-row">
                    {(meta?.difficulties ?? []).map((item) => (
                      <button
                        key={item.key}
                        className="chip"
                        title={item.hint}
                        onClick={() => setDifficulty(item.key)}
                        style={
                          difficulty === item.key
                            ? {
                                color: 'var(--ink)',
                                borderColor: 'var(--amber-line)',
                                background: 'var(--amber-soft)',
                              }
                            : undefined
                        }
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>

                {generating && (
                  <div className="notice">
                    <Sparkles size={14} />
                    <span>{job?.message ?? '正在依据资料出题…'}约需几十秒。</span>
                  </div>
                )}

                <div className="row">
                  <Button
                    variant="primary"
                    icon={<ListChecks size={14} />}
                    onClick={onGenerate}
                    loading={generating}
                    disabled={!canGenerate}
                  >
                    生成题集
                  </Button>
                  <span className="mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                    {scope === 'topic'
                      ? `${topicIds.length} 个主题`
                      : scope === 'docs'
                        ? `${docIds.length} 份资料`
                        : '全部资料'}
                    {days ? ` · 近 ${days} 天` : ''} · {total} 题
                  </span>
                </div>
              </div>
            </div>

            <div className="stack">
              <div className="card">
                <div className="card-head">
                  <h2>
                    <NotebookPen size={15} /> 判分方式
                  </h2>
                </div>
                <div className="mini-list">
                  <div className="mini-item">
                    <span className="k">单选 / 多选 / 判断</span>
                    <span className="n">自动</span>
                  </div>
                  <div className="mini-item">
                    <span className="k">多选计分</span>
                    <span className="n">完全一致</span>
                  </div>
                  <div className="mini-item">
                    <span className="k">简答</span>
                    <span className="n">对照自评</span>
                  </div>
                  <div className="mini-item">
                    <span className="k">未作答</span>
                    <span className="n">0 分，不进错题</span>
                  </div>
                  <div className="mini-item">
                    <span className="k">答错后</span>
                    <span className="n">进错题本</span>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-head">
                  <h2>
                    <RotateCcw size={16} /> 最近练习
                  </h2>
                  <span className="head-note">{history.length} 条</span>
                </div>
                {history.length === 0 ? (
                  <div className="card-body">
                    <p className="muted" style={{ fontSize: 12.5 }}>
                      还没有练习记录。
                    </p>
                  </div>
                ) : (
                  <div className="session-list">
                    {history.map((item) => (
                      <button
                        key={item.id}
                        className="session-item"
                        data-active={session?.id === item.id}
                        onClick={() => void openSession(item.id)}
                      >
                        <span className="session-main">
                          <span className="session-title">
                            {item.question_count} 题 · {STATUS_LABEL[item.status]}
                            {item.status === 'submitted' ? ` · ${Math.round(item.score)} 分` : ''}
                          </span>
                          <span className="session-sub">
                            {formatClock(item.started_at)}
                            {item.status === 'in_progress'
                              ? ` · 已答 ${item.answered_count}/${item.question_count}`
                              : item.meta.document_total
                                ? ` · ${item.meta.document_total} 份资料`
                                : ''}
                          </span>
                        </span>
                        <ChevronDown
                          size={13}
                          style={{ transform: 'rotate(-90deg)', opacity: 0.5 }}
                        />
                      </button>
                    ))}
                  </div>
                )}
              </div>

            </div>
          </div>
        </>
      )}

      <Modal
        open={confirmDelete}
        title="删除这套草稿？"
        description="草稿会一起删除，已提交的练习不受影响。"
        onClose={() => setConfirmDelete(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              取消
            </Button>
            <Button variant="danger" onClick={() => void removeDraft()} loading={deleting}>
              删除
            </Button>
          </>
        }
      />
    </section>
  )
}

function QuestionRow({
  index,
  question,
  answerOpen,
  sourceOpen,
  onToggleAnswer,
  onToggleSource,
  onRemove,
}: {
  index: number
  question: PracticeQuestion
  answerOpen: boolean
  sourceOpen: boolean
  onToggleAnswer: () => void
  onToggleSource: () => void
  onRemove: () => void
}) {
  const primary = question.sources[0]

  return (
    <article className="pq">
      <div className="pq-head">
        <span className="pq-idx">{String(index).padStart(2, '0')}</span>
        <span className="tag">{typeLabel(question.type)}</span>
        {question.knowledge_point && <span className="pq-kp">{question.knowledge_point}</span>}
        <IconButton label="从草稿中移除" className="pq-remove" onClick={onRemove}>
          <X size={13} />
        </IconButton>
      </div>

      <p className="pq-stem">{question.stem}</p>

      {/* 草稿态：不点「查看答案」就不亮正确项，核对时也要先自己读一遍 */}
      <QuestionBody question={question} mode="review" reveal={answerOpen} />

      <div className="pq-foot">
        <button className="pq-link" onClick={onToggleAnswer}>
          {answerOpen ? '收起答案' : '查看答案与解析'}
        </button>
        <span className="pq-dot">·</span>
        <button className="pq-link" onClick={onToggleSource}>
          {sourceOpen ? '收起原文' : '来源'}
        </button>
        {primary && (
          <span className="pq-source">
            《{primary.document_name}》{primary.page ? ` 第 ${primary.page} 页` : ''}
          </span>
        )}
      </div>

      {answerOpen && (
        <div className="pq-answer">
          <div className="pq-answer-line">
            <span className="pq-answer-k">答案</span>
            <span className="pq-answer-v">{question.answer}</span>
          </div>
          <div className="pq-answer-line">
            <span className="pq-answer-k">解析</span>
            <span className="pq-answer-v">{question.explanation}</span>
          </div>
        </div>
      )}

      {sourceOpen && (
        <div className="pq-sources">
          {question.sources.map((source) => (
            <div key={`${question.id}-${source.chunk_id}`} className="pq-snippet">
              <span className="pq-snippet-src">《{source.document_name}》</span>
              {source.snippet}
            </div>
          ))}
        </div>
      )}
    </article>
  )
}
