import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowUpRight,
  BookPlus,
  ChevronDown,
  CornerDownLeft,
  FileText,
  Loader2,
  MessageSquareText,
  Quote,
  RotateCcw,
  SearchX,
  Sparkles,
  TriangleAlert,
  Upload,
} from 'lucide-react'
import Button from '../components/ui/Button'
import { useToast } from '../components/ui/Toast'
import MarkdownView from '../components/notes/MarkdownView'
import {
  chatStream,
  createNote,
  listChunks,
  listTopics,
  type ChatMessage,
  type Citation,
  type NoContextInfo,
  type Topic,
} from '../api/client'
import HelpTip from '../components/ui/HelpTip'

const SUGGESTIONS = ['这份资料的核心结论是什么？', '对比不同资料里的观点差异', '用一句话解释这个概念的直觉']

interface Turn {
  role: 'user' | 'assistant'
  content: string
  /** assistant：随回答先行的引用列表 */
  sources?: Citation[]
  /** assistant：范围内没找到相关内容 */
  noContext?: NoContextInfo
  /** assistant：模型侧错误 */
  error?: string
  /** assistant：这次回答对应的提问原文，用于「放宽范围重问」 */
  asked?: string
  /** assistant：流式是否已结束，用来决定何时挂 AI 生成声明 */
  done?: boolean
}

/**
 * 每段 AI 回答都要能看出「依据够不够」：
 * - 有来源且正文标了 [n]：正常提示核对原文
 * - 有来源但正文一个引用都没标：说明它是凭记忆写的，风险最高
 * - 压根没有来源：连检索都没命中
 */
function aiNote(turn: Turn): string {
  if (!turn.sources?.length) return '以下内容由 AI 生成，未使用你的资料，请注意甄别'
  if (!/\[\d+\]/.test(turn.content)) return '本回答由 AI 生成，且未标注引用，结论未与资料核对'
  return '本回答由 AI 生成，可能有误，请以下方引用原文为准'
}

/**
 * 问答页（FR-03）。
 * SSE 是 POST + fetch 流，事件顺序固定：sources → delta… → done；
 * no_context / error 时同样落到会话流里，保持对话完整可读。
 */
export default function Chat() {
  const toast = useToast()
  const navigate = useNavigate()
  const [topics, setTopics] = useState<Topic[]>([])
  const [tags, setTags] = useState<Topic[]>([])
  const [rangeTopic, setRangeTopic] = useState('')
  const [rangeTags, setRangeTags] = useState<string[]>([])
  const [messages, setMessages] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [openSource, setOpenSource] = useState<string | null>(null)
  const [savingNote, setSavingNote] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    listTopics()
      .then((res) => {
        setTopics(res.topics)
        setTags(res.tags)
      })
      .catch(() => undefined)
  }, [])

  // 新消息或流式增量时贴底；用户往上翻就不打扰
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  const send = async (text?: string, opts?: { noRange?: boolean }) => {
    const ask = (text ?? question).trim()
    if (!ask || streaming) return
    setQuestion('')

    // 检索没命中时允许「不限范围再问一次」：这次不带主题与标签
    const scopeTopic = opts?.noRange ? '' : rangeTopic
    const scopeTags = opts?.noRange ? [] : rangeTags

    // 历史只带正常完成的对话（提示/错误不进上下文）
    // 提示/错误不进上下文；但"没查到资料却给了通用回答"算完整一轮，要带上
    const history: ChatMessage[] = messages
      .filter((m) => !m.error && m.content.trim())
      .map((m) => ({ role: m.role, content: m.content }))
      .slice(-10)

    const base: Turn[] = [
      ...messages,
      { role: 'user', content: ask },
      { role: 'assistant', content: '', asked: ask, done: false },
    ]
    setMessages(base)
    const assistantIndex = base.length - 1

    const patch = (changes: Partial<Turn>) =>
      setMessages((prev) => prev.map((m, i) => (i === assistantIndex ? { ...m, ...changes } : m)))

    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await chatStream(
        {
          question: ask,
          history,
          ...(scopeTopic ? { topic_id: scopeTopic } : {}),
          ...(scopeTags.length ? { tag_ids: scopeTags } : {}),
        },
        {
          onSources: (items) => patch({ sources: items }),
          onDelta: (t) =>
            setMessages((prev) =>
              prev.map((m, i) => (i === assistantIndex ? { ...m, content: m.content + t } : m)),
            ),
          onNoContext: (info) => patch({ noContext: info, done: true }),
          onError: (detail) => patch({ error: detail, done: true }),
          onDone: () => patch({ done: true }),
        },
        controller.signal,
      )
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        patch({ error: (err as Error).message, done: true })
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  const stop = () => abortRef.current?.abort()

  const saveAsNote = async (turn: Turn) => {
    if (!turn.sources?.length) return
    setSavingNote(true)
    try {
      // 回答里的 [n] 还原成笔记角标 [[chunk:id]]：进编辑器就能点开原文，
      // 来源表也走同一条双向校验
      const byIndex = new Map(turn.sources.map((s) => [s.index, s.chunk_id]))
      const markdown = `${turn.content.replace(/\[(\d+)\]/g, (whole, n: string) => {
        const id = byIndex.get(Number(n))
        return id ? ` [[chunk:${id}]]` : ''
      })}\n`
      const note = await createNote({
        title: (turn.content.split('\n')[0] || '问答存档').slice(0, 60),
        markdown,
        source_type: 'chat',
        topic_id: rangeTopic || 'uncategorized',
        tag_ids: rangeTags,
      })
      toast.success('已存为笔记草稿')
      navigate(`/notes/${note.id}`)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setSavingNote(false)
    }
  }

  const toggleTag = (id: string) =>
    setRangeTags((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]))

  const hasMessages = messages.length > 0

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>问答</h1>
          <p className="page-sub">
            优先依据你的资料作答，查不到会明说。
            <HelpTip label="了解问答的依据">
              回答前先检索相关片段（最相关的 5 个，相似度 0.42 以上），正文角标可点开看原文。
              检索不到时不会拿无关片段硬答：先声明"没有找到相关资料"，再改用模型自己的通用知识回答，
              这段回答下方会标明"未使用你的资料"。有资料依据的回答也会标注引用情况。
            </HelpTip>
          </p>
        </div>
      </header>

      <div className="workbench">
        <div className="card chat-panel">
          <div className="thread">
            {!hasMessages && (
              <div className="thread-empty">
                <span className="orb">
                  <MessageSquareText size={19} />
                </span>
                <h3>还没有提问</h3>
                <p>答案会附带来源片段，点开可以核对原文。</p>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={`chat-msg is-${m.role}`}>
                {m.role === 'user' ? (
                  <p className="chat-q">{m.content}</p>
                ) : (
                  <div className="chat-a">
                    {m.noContext && (
                      <div className={`chat-empty is-${m.noContext.reason}`}>
                        <p className="chat-empty-title">
                          {m.noContext.reason === 'empty' ? (
                            <FileText size={14} />
                          ) : (
                            <SearchX size={14} />
                          )}
                          {m.noContext.reason === 'empty'
                            ? '知识库中还没有资料'
                            : '知识库中没有找到相关资料'}
                        </p>
                        <p className="chat-empty-detail">{m.noContext.detail}</p>
                        {m.noContext.reason === 'empty' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<Upload size={13} />}
                            onClick={() => navigate('/library')}
                          >
                            去导入资料
                          </Button>
                        ) : (
                          m.noContext.scoped &&
                          m.asked && (
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<RotateCcw size={13} />}
                              onClick={() => send(m.asked, { noRange: true })}
                            >
                              不限范围再问一次
                            </Button>
                          )
                        )}
                      </div>
                    )}

                    {m.content ? (
                      <MarkdownView markdown={m.content} sources={toNoteSources(m.sources)} />
                    ) : (
                      !m.noContext && !m.error && (
                        <p className="chat-waiting">
                          <Loader2 size={13} className="spin" /> 正在检索资料…
                        </p>
                      )
                    )}

                    {m.error && (
                      <p className="chat-hint is-error">
                        <TriangleAlert size={13} /> {m.error}
                      </p>
                    )}

                    {m.content && m.done && !m.error && (
                      <p className="chat-ai-note">
                        <Sparkles size={11} />
                        {aiNote(m)}
                      </p>
                    )}

                    {m.sources && m.sources.length > 0 && !m.error && (
                      <div className="chat-sources">
                        <span className="chat-sources-label">
                          <Quote size={10} /> 来源 {m.sources.length}
                        </span>
                        {m.sources.map((s) => (
                          <div key={s.chunk_id} className="chat-source">
                            <button
                              className="chat-source-head"
                              onClick={() =>
                                setOpenSource(openSource === s.chunk_id ? null : s.chunk_id)
                              }
                            >
                              <span className="mono chat-source-idx">[{s.index}]</span>
                              <FileText size={11} />
                              <span className="chat-source-name">
                                {s.document_name ?? '（来源已删除）'}
                                {s.page ? ` · 第 ${s.page} 页` : ''}
                              </span>
                              <ChevronDown
                                size={12}
                                className={openSource === s.chunk_id ? 'is-open' : ''}
                              />
                            </button>
                            {openSource === s.chunk_id && (
                              <p className="chat-source-snippet">{s.snippet}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {m.sources && m.sources.length > 0 && m.content && !streaming && (
                      <div className="chat-actions">
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<BookPlus size={13} />}
                          loading={savingNote}
                          onClick={() => saveAsNote(m)}
                        >
                          存为笔记
                        </Button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>

          <div className="followups">
            <span className="followups-label">可以这样问</span>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="chip" disabled={streaming} onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>

          <div className="composer">
            <input
              className="input"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={streaming ? '回答生成中…' : '提一个具体问题…'}
              aria-label="提问"
              disabled={streaming}
              onKeyDown={(e) => {
                if (e.key === 'Enter') send()
              }}
            />
            {streaming ? (
              <Button variant="ghost" onClick={stop}>
                停止
              </Button>
            ) : (
              <Button variant="primary" onClick={() => send()} icon={<CornerDownLeft size={13} />}>
                发送
              </Button>
            )}
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h2>
                <ArrowUpRight size={16} /> 提问范围
                <HelpTip label="了解检索规则">
                  每次提问检索最相关的 5 个片段（相似度 ≥ 0.3），并带上最近 5 轮对话。
                  资料里没有的内容，助手会直接说「资料中未提及」，不会编造。
                  「存为笔记」会把引用还原成笔记角标，进编辑器还能点开原文。
                </HelpTip>
              </h2>
            </div>
            <div className="range-body">
              <label className="range-label" htmlFor="chat-topic">
                主题
              </label>
              <select
                id="chat-topic"
                className="input select-sm"
                value={rangeTopic}
                onChange={(e) => setRangeTopic(e.target.value)}
              >
                <option value="">全部资料</option>
                {topics.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              {tags.length > 0 && (
                <>
                  <span className="range-label">标签（可多选）</span>
                  <div className="chip-row">
                    {tags.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        className={`chip chip-sm${rangeTags.includes(t.id) ? ' is-on' : ''}`}
                        onClick={() => toggleTag(t.id)}
                      >
                        {t.name}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>

        </div>
      </div>
    </section>
  )
}

/** 问答引用与笔记来源结构对齐：MarkdownView 直接复用 */
function toNoteSources(citations?: Citation[]) {
  return (citations ?? []).map((c) => ({ ...c, missing: false }))
}
