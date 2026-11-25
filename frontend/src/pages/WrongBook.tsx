import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BookOpenCheck,
  ChevronDown,
  Filter,
  NotebookPen,
  Play,
  RotateCcw,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import Button, { IconButton } from '../components/ui/Button'
import Empty from '../components/ui/Feedback'
import { useToast } from '../components/ui/Toast'
import QuestionBody, { formatAnswer } from '../components/practice/QuestionBody'
import { errText, formatClock, typeLabel } from '../components/practice/shared'
import {
  getWrongSummary,
  listDocuments,
  listTopics,
  listWrongAnswers,
  removeWrongAnswer,
  startPracticeRetry,
  type QuestionType,
  type WrongAnswerItem,
  type WrongSummary,
} from '../api/client'
import HelpTip from '../components/ui/HelpTip'

type Sort = 'recent' | 'count'

interface Filters {
  topicId: string | null
  documentId: string | null
  type: QuestionType | null
  days: number | null
  minWrong: number | null
  sort: Sort
}

const EMPTY_FILTERS: Filters = {
  topicId: null,
  documentId: null,
  type: null,
  days: null,
  minWrong: null,
  sort: 'recent',
}

const TYPES: Array<{ key: QuestionType; label: string }> = [
  { key: 'single_choice', label: '单选' },
  { key: 'multi_choice', label: '多选' },
  { key: 'true_false', label: '判断' },
  { key: 'short_answer', label: '简答' },
]

const DAYS: Array<{ value: number | null; label: string }> = [
  { value: null, label: '不限' },
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
]

const COUNTS: Array<{ value: number | null; label: string }> = [
  { value: null, label: '全部' },
  { value: 2, label: '错 2 次以上' },
  { value: 3, label: '错 3 次以上' },
]

/** 错题本（FR-06）：筛选 → 看答案对比与原文 → 勾选重练。 */
export default function WrongBook() {
  const toast = useToast()
  const navigate = useNavigate()

  const [items, setItems] = useState<WrongAnswerItem[]>([])
  const [summary, setSummary] = useState<WrongSummary | null>(null)
  const [topics, setTopics] = useState<Array<{ id: string; name: string; color: string }>>([])
  const [documents, setDocuments] = useState<Array<{ id: string; name: string }>>([])
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)
  const [tab, setTab] = useState<'list' | 'topics'>('list')
  const [selected, setSelected] = useState<string[]>([])
  const [expanded, setExpanded] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const loadList = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listWrongAnswers({
        topic_id: filters.topicId ?? undefined,
        document_id: filters.documentId ?? undefined,
        type: filters.type ?? undefined,
        days: filters.days ?? undefined,
        min_wrong: filters.minWrong ?? undefined,
        sort: filters.sort,
      })
      setItems(data)
      // 列表变了，勾选里可能残留已经不存在的条目
      setSelected((prev) => prev.filter((id) => data.some((item) => item.id === id)))
    } catch (error) {
      toast.error(errText(error))
    } finally {
      setLoading(false)
    }
  }, [filters, toast])

  useEffect(() => {
    void loadList()
  }, [loadList])

  useEffect(() => {
    void (async () => {
      try {
        const [topicData, docData, summaryData] = await Promise.all([
          listTopics(),
          listDocuments(),
          getWrongSummary(),
        ])
        setTopics(
          topicData.topics.map((topic) => ({
            id: topic.id,
            name: topic.name,
            color: topic.color,
          }))
        )
        setDocuments(docData.map((doc) => ({ id: doc.id, name: doc.name })))
        setSummary(summaryData)
      } catch {
        // 筛选选项加载失败不阻塞列表
      }
    })()
  }, [])

  const refreshAll = async () => {
    await loadList()
    try {
      setSummary(await getWrongSummary())
    } catch {
      // 汇总失败不影响明细
    }
  }

  const patch = (next: Partial<Filters>) => setFilters((prev) => ({ ...prev, ...next }))

  const toggleSelect = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))

  const toggleExpand = (id: string) =>
    setExpanded((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))

  const allSelected = items.length > 0 && selected.length === items.length

  const retry = async (wrongIds?: string[]) => {
    setBusy(true)
    try {
      const session = await startPracticeRetry(
        wrongIds?.length ? { wrong_ids: wrongIds } : { limit: 10 }
      )
      navigate(`/practice?session=${session.id}`)
    } catch (error) {
      toast.error(errText(error))
    } finally {
      setBusy(false)
    }
  }

  const drop = async (id: string) => {
    try {
      await removeWrongAnswer(id)
      toast.success('已移出错题本')
      await refreshAll()
    } catch (error) {
      toast.error(errText(error))
    }
  }

  const maxWrong = useMemo(
    () => Math.max(1, ...(summary?.by_topic ?? []).map((topic) => topic.wrong_count)),
    [summary]
  )
  const weakest = summary?.by_topic[0]

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>错题本</h1>
          <p className="page-sub">
            答错自动收录，同一题只累加次数。
            <HelpTip label="了解错题规则">
              同一道题重复答错只增加「错了几次」，不会堆出重复条目。
              每道题都能点回原文片段，对不上说明当初就没读懂。
              重练只抽错题、不掺新题；答对不会自动移出——「会了」由你判断，不由系统猜。
            </HelpTip>
          </p>
        </div>
        <div className="page-actions">
          <Button
            size="sm"
            variant="primary"
            icon={<Play size={13} />}
            loading={busy}
            disabled={!summary || summary.total === 0}
            onClick={() => void retry(selected.length ? selected : undefined)}
          >
            {selected.length ? `重练选中 ${selected.length} 题` : '重练最常错的 10 题'}
          </Button>
        </div>
      </header>

      <div className="wb-stats">
        <div className="wb-stat">
          <span className="wb-stat-n">{summary?.total ?? 0}</span>
          <span className="wb-stat-k">错题条目</span>
        </div>
        <div className="wb-stat">
          <span className="wb-stat-n">{summary?.total_wrong_count ?? 0}</span>
          <span className="wb-stat-k">累计错次</span>
        </div>
        <div className="wb-stat">
          <span className="wb-stat-n">{summary?.repeat_count ?? 0}</span>
          <span className="wb-stat-k">重复答错</span>
        </div>
        <div className="wb-stat">
          <span className="wb-stat-n" style={{ fontSize: weakest ? 14 : 20 }}>
            {weakest ? weakest.name : '—'}
          </span>
          <span className="wb-stat-k">最薄弱主题</span>
        </div>
      </div>

      <div className="wb-filters">
        <div className="wb-row">
          <span className="wb-label">
            <Filter size={11} /> 主题
          </span>
          <button
            className="chip"
            data-on={!filters.topicId}
            onClick={() => patch({ topicId: null })}
          >
            全部
          </button>
          {topics.map((topic) => (
            <button
              key={topic.id}
              className="chip"
              data-on={filters.topicId === topic.id}
              onClick={() => patch({ topicId: filters.topicId === topic.id ? null : topic.id })}
            >
              <span className="topic-dot" style={{ background: topic.color }} />
              {topic.name}
            </button>
          ))}
        </div>

        <div className="wb-row">
          <span className="wb-label">文档</span>
          <select
            className="wb-select"
            value={filters.documentId ?? ''}
            onChange={(event) => patch({ documentId: event.target.value || null })}
          >
            <option value="">全部文档</option>
            {documents.map((doc) => (
              <option key={doc.id} value={doc.id}>
                {doc.name}
              </option>
            ))}
          </select>

          <span className="wb-label" style={{ width: 'auto', marginLeft: 8 }}>
            题型
          </span>
          <button className="chip" data-on={!filters.type} onClick={() => patch({ type: null })}>
            全部
          </button>
          {TYPES.map((type) => (
            <button
              key={type.key}
              className="chip"
              data-on={filters.type === type.key}
              onClick={() => patch({ type: filters.type === type.key ? null : type.key })}
            >
              {type.label}
            </button>
          ))}
        </div>

        <div className="wb-row">
          <span className="wb-label">时间</span>
          {DAYS.map((option) => (
            <button
              key={option.label}
              className="chip"
              data-on={filters.days === option.value}
              onClick={() => patch({ days: option.value })}
            >
              {option.label}
            </button>
          ))}

          <span className="wb-label" style={{ width: 'auto', marginLeft: 8 }}>
            次数
          </span>
          {COUNTS.map((option) => (
            <button
              key={option.label}
              className="chip"
              data-on={filters.minWrong === option.value}
              onClick={() => patch({ minWrong: option.value })}
            >
              {option.label}
            </button>
          ))}

          <span className="wb-label" style={{ width: 'auto', marginLeft: 'auto' }}>
            排序
          </span>
          <button
            className="chip"
            data-on={filters.sort === 'recent'}
            onClick={() => patch({ sort: 'recent' })}
          >
            最近错的
          </button>
          <button
            className="chip"
            data-on={filters.sort === 'count'}
            onClick={() => patch({ sort: 'count' })}
          >
            错得最多
          </button>
        </div>
      </div>

      <div className="wb-tabs">
        <button data-on={tab === 'list'} onClick={() => setTab('list')}>
          <NotebookPen size={13} /> 错题明细
          <span className="wb-tab-n">{items.length}</span>
        </button>
        <button data-on={tab === 'topics'} onClick={() => setTab('topics')}>
          <BookOpenCheck size={13} /> 主题分布
        </button>
      </div>

      {tab === 'topics' ? (
        <div className="card">
          <div className="card-head">
            <h2>
              <BookOpenCheck size={16} /> 你在哪错得最多
              <HelpTip>按真实作答计算错误率，而不是错题条数——错得多但做得也多，排名就不会虚高。</HelpTip>
            </h2>
          </div>
          {(summary?.by_topic ?? []).length === 0 ? (
            <div className="card-body">
              <p className="muted" style={{ fontSize: 12.5 }}>
                还没有错题。
              </p>
            </div>
          ) : (
            <div>
              {(summary?.by_topic ?? []).map((topic) => (
                <button
                  key={topic.topic_id}
                  className="wb-topic"
                  onClick={() => {
                    patch({ topicId: topic.topic_id })
                    setTab('list')
                  }}
                >
                  <span className="topic-dot" style={{ background: topic.color }} />
                  <span className="wb-topic-name">{topic.name}</span>
                  <span className="wb-topic-bar">
                    <span
                      className="wb-topic-fill"
                      style={{ width: `${(topic.wrong_count / maxWrong) * 100}%` }}
                    />
                  </span>
                  <span className="wb-topic-num">
                    {topic.wrong_count} 次 · {topic.question_count} 题
                  </span>
                  <span className="wb-topic-rate">
                    错误率 {topic.error_rate}%
                    <em>（作答 {topic.answered} 次）</em>
                  </span>
                </button>
              ))}
            </div>
          )}
          <div className="pq-tip">
            错误率 =（该主题作答次数 − 答对次数）/ 作答次数。样本少时这个数会跳得很厉害，
            作答次数不到 5 次就先只看条数。
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="card-head">
            <h2>
              <NotebookPen size={15} /> 错题明细
            </h2>
            <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
              {loading ? '加载中…' : `${items.length} 条`}
            </span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelected(allSelected ? [] : items.map((item) => item.id))}
                disabled={items.length === 0}
              >
                {allSelected ? '取消全选' : '全选本页'}
              </Button>
              <Button
                size="sm"
                variant="outline"
                icon={<RotateCcw size={13} />}
                loading={busy}
                disabled={!selected.length}
                onClick={() => void retry(selected)}
              >
                重练选中
              </Button>
            </div>
          </div>

          {!loading && items.length === 0 ? (
            <Empty
              icon={<TriangleAlert size={18} />}
              title={summary?.total ? '当前筛选下没有错题' : '错题本还是空的'}
              description={
                summary?.total ? '当前筛选下没有结果。' : '答错或标记「未掌握」的题会自动收进来。'
              }
            >
              {summary?.total ? (
                <Button size="sm" variant="ghost" onClick={() => setFilters(EMPTY_FILTERS)}>
                  清空筛选
                </Button>
              ) : (
                <Button size="sm" variant="primary" onClick={() => navigate('/practice')}>
                  去练习一套
                </Button>
              )}
            </Empty>
          ) : (
            <div>
              {items.map((item) => {
                const open = expanded.includes(item.id)
                const picked = selected.includes(item.id)
                return (
                  <article className="wb-item" key={item.id} data-picked={picked}>
                    <div className="wb-item-head">
                      <button
                        type="button"
                        className="wb-check"
                        data-on={picked}
                        aria-label={picked ? '取消选择' : '选择这道题'}
                        onClick={() => toggleSelect(item.id)}
                      >
                        {picked && <span />}
                      </button>
                      <span className="tag">{typeLabel(item.question.type)}</span>
                      <span className="wb-times" data-repeat={item.wrong_count > 1}>
                        错 {item.wrong_count} 次
                      </span>
                      <span className="wb-topic-chip">{item.topic_name}</span>
                      {item.document_name && <span className="wb-doc">《{item.document_name}》</span>}
                      <span className="wb-when">{formatClock(item.last_wrong_at)}</span>
                      <IconButton
                        label="已掌握，移出错题本"
                        className="wb-remove"
                        onClick={() => void drop(item.id)}
                      >
                        <Trash2 size={13} />
                      </IconButton>
                    </div>

                    <p className="pq-stem">{item.question.stem}</p>

                    <QuestionBody
                      question={item.question}
                      mode="result"
                      value={item.last_response}
                    />

                    <div className="res-compare">
                      <div className="res-pair" data-kind="yours">
                        <span className="res-pair-k">你的答案</span>
                        <span className="res-pair-v">
                          {formatAnswer(item.question, item.last_response)}
                        </span>
                      </div>
                      <span className="res-pair-arrow">→</span>
                      <div className="res-pair" data-kind="right">
                        <span className="res-pair-k">正确答案</span>
                        <span className="res-pair-v">
                          {formatAnswer(item.question, item.question.answer)}
                        </span>
                      </div>
                      <div className="wb-item-actions">
                        <button className="pq-link" onClick={() => toggleExpand(item.id)}>
                          {open ? '收起解析' : '查看解析与原文'}
                          <ChevronDown
                            size={11}
                            style={{ transform: open ? 'rotate(180deg)' : undefined }}
                          />
                        </button>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<RotateCcw size={12} />}
                          onClick={() => void retry([item.id])}
                        >
                          只练这题
                        </Button>
                      </div>
                    </div>

                    {open && (
                      <>
                        <div className="pq-answer">
                          <div className="pq-answer-line">
                            <span className="pq-answer-k">解析</span>
                            <span className="pq-answer-v">{item.question.explanation}</span>
                          </div>
                        </div>
                        {item.question.sources.length > 0 && (
                          <div className="pq-sources">
                            {item.question.sources.map((source) => (
                              <div key={`${item.id}-${source.chunk_id}`} className="pq-snippet">
                                <span className="pq-snippet-src">
                                  《{source.document_name}》
                                  {source.page ? ` 第 ${source.page} 页` : ''}
                                </span>
                                {source.snippet}
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </div>
      )}

    </section>
  )
}
