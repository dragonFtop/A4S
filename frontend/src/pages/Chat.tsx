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
  TriangleAlert,
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
  noContext?: string
  /** assistant：模型侧错误 */
  error?: string
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

  const send = async (text?: string) => {
    const ask = (text ?? question).trim()
    if (!ask || streaming) return
    setQuestion('')

    // 历史只带正常完成的对话（提示/错误不进上下文）
    const history: ChatMessage[] = messages
      .filter((m) => !m.noContext && !m.error && m.content.trim())
      .map((m) => ({ role: m.role, content: m.content }))
      .slice(-10)

    const base: Turn[] = [...messages, { role: 'user', content: ask }, { role: 'assistant', content: '' }]
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
          ...(rangeTopic ? { topic_id: rangeTopic } : {}),
          ...(rangeTags.length ? { tag_ids: rangeTags } : {}),
        },
        {
          onSources: (items) => patch({ sources: items }),
          onDelta: (t) =>
            setMessages((prev) =>
              prev.map((m, i) => (i === assistantIndex ? { ...m, content: m.content + t } : m)),
            ),
          onNoContext: (detail) => patch({ noContext: detail }),
          onError: (detail) => patch({ error: detail }),
        },
        controller.signal,
      )
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        patch({ error: (err as Error).message })
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
            只依据你的资料作答，找不到依据就说不知道。
            <HelpTip label="了解问答的依据">
              回答前会先检索相关片段，正文里的角标可以点开看原文。
              检索不到足够依据时，助手会直接说"没有找到"，不会凭空编一个答案。
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
                    {m.content ? (
                      <MarkdownView markdown={m.content} sources={toNoteSources(m.sources)} />
                    ) : (
                      !m.noContext && !m.error && (
                        <p className="chat-waiting">
                          <Loader2 size={13} className="spin" /> 正在检索资料…
                        </p>
                      )
                    )}

                    {m.noContext && (
                      <p className="chat-hint">
                        <TriangleAlert size={13} /> {m.noContext}
                      </p>
                    )}
                    {m.error && (
                      <p className="chat-hint is-error">
                        <TriangleAlert size={13} /> {m.error}
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
