import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowUp,
  ArrowUpRight,
  BookPlus,
  ChevronDown,
  FileText,
  Loader2,
  MessageSquareText,
  Plus,
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
  getDocumentStats,
  listChunks,
  listTopics,
  type ChatMessage,
  type Citation,
  type NoContextInfo,
  type Topic,
} from '../api/client'

const SUGGESTIONS = ['这份资料的核心结论是什么？', '对比不同资料里的观点差异', '用一句话解释这个概念的直觉']
/** 库是空的时候，上面那三个例子一定检索不到，换成不依赖资料的问法 */
const SUGGESTIONS_EMPTY = ['帮我讲讲这个概念的直觉', '列一个三步的复习思路', '这个概念常见的误区有哪些？']

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
 * - 压根没有来源：连检索都没命中（知识库为空时同样适用，
 *   但"库是空的"这件事由顶部状态条统一说明，不再逐条重复）
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
  const [searchParams, setSearchParams] = useSearchParams()
  const [topics, setTopics] = useState<Topic[]>([])
  const [tags, setTags] = useState<Topic[]>([])
  const [rangeTopic, setRangeTopic] = useState('')
  const [rangeTags, setRangeTags] = useState<string[]>([])
  const [messages, setMessages] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [openSource, setOpenSource] = useState<string | null>(null)
  const [savingNote, setSavingNote] = useState(false)
  /** 已入库片段数。null = 还没查出来，不渲染状态条，避免闪一下 */
  const [kbChunks, setKbChunks] = useState<number | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  /** 概览页带来的问题只自动发一次 */
  const askedFromUrl = useRef(false)

  useEffect(() => {
    listTopics()
      .then((res) => {
        setTopics(res.topics)
        setTags(res.tags)
      })
      .catch(() => undefined)
  }, [])

  // 知识库状态只在进页面时提示一次，不跟着每条回答重复。
  // 判据与后端一致：片段数为 0 就是检索必然空，无论是有 0 份资料还是资料还在处理。
  useEffect(() => {
    const load = () =>
      getDocumentStats()
        .then((s) => setKbChunks(s.chunks))
        .catch(() => undefined)
    load()
    // 去知识库导入完再切回来，状态条要自己消失
    window.addEventListener('focus', load)
    return () => window.removeEventListener('focus', load)
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
          onNoContext: (info) =>
            // 会继续作答时不置 done：AI 声明要等正文写完再出现，否则会跟着打字闪
            patch({ noContext: info, done: !info.fallback }),
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

  // 概览页的大提问框带 ?q= 跳过来：直接发一次，再把参数清掉，
  // 刷新页面不会重复发送。
  useEffect(() => {
    if (askedFromUrl.current) return
    const q = searchParams.get('q')?.trim()
    if (!q) return
    askedFromUrl.current = true
    const next = new URLSearchParams(searchParams)
    next.delete('q')
    setSearchParams(next, { replace: true })
    send(q)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, setSearchParams])

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
  const kbEmpty = kbChunks === 0

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>问答</h1>
          <p className="page-sub">先查你的资料，查不到会说明。</p>
        </div>
      </header>

      <div className="chat-shell">
        <div className="chat-scope">
          <span className="chat-scope-label">
            <ArrowUpRight size={13} /> 提问范围
          </span>
          <select
            id="chat-topic"
            aria-label="提问范围"
            className="input select-sm chat-scope-select"
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
            <div className="chip-row chat-scope-tags">
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
          )}
          <Button
            size="sm"
            variant="ghost"
            className="chat-scope-new"
            icon={<Plus size={14} />}
            disabled={!hasMessages || streaming}
            onClick={() => setMessages([])}
          >
            新对话
          </Button>
        </div>

        <div className="card chat-panel">
          {kbEmpty && (
            <div className="chat-kb">
              <FileText size={14} />
              <div className="chat-kb-body">
                <p className="chat-kb-title">知识库中还没有资料</p>
                <p className="chat-kb-detail">回答将由 AI 通用知识生成，不引用任何原文。</p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                icon={<Upload size={13} />}
                onClick={() => navigate('/library')}
              >
                去导入资料
              </Button>
            </div>
          )}

          <div className="thread">
            {!hasMessages && (
              <div className="thread-empty">
                <span className="orb">
                  <MessageSquareText size={19} />
                </span>
                <h3>还没有提问</h3>
                <p>有资料时答案附来源片段；没有资料也能问，回答会标明未使用资料。</p>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={`chat-msg is-${m.role}`}>
                {m.role === 'user' ? (
                  <p className="chat-q">{m.content}</p>
                ) : (
                  <div className="chat-a">
                    {/* 空库的说明已上移到顶部状态条，这里只处理「这次提问没命中」 */}
                    {m.noContext?.reason === 'no_match' && (
                      <div className="chat-empty is-no_match">
                        <p className="chat-empty-title">
                          <SearchX size={14} />
                          知识库中没有找到相关资料
                        </p>
                        <p className="chat-empty-detail">{m.noContext.detail}</p>
                        {m.noContext.scoped && m.asked && (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<RotateCcw size={13} />}
                            onClick={() => send(m.asked, { noRange: true })}
                          >
                            不限范围再问一次
                          </Button>
                        )}
                      </div>
                    )}

                    {m.content ? (
                      <MarkdownView markdown={m.content} sources={toNoteSources(m.sources)} />
                    ) : (
                      !m.error &&
                      !(m.noContext && !m.noContext.fallback) && (
                        <p className="chat-waiting">
                          <Loader2 size={13} className="spin" />
                          {m.noContext ? '正在用通用知识回答…' : '正在检索资料…'}
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
            {(kbEmpty ? SUGGESTIONS_EMPTY : SUGGESTIONS).map((s) => (
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
              <Button variant="ghost" size="sm" onClick={stop}>
                停止
              </Button>
            ) : (
              <Button
                variant="primary"
                className="composer-send"
                aria-label="发送"
                title="发送"
                onClick={() => send()}
                icon={<ArrowUp size={17} />}
              />
            )}
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
