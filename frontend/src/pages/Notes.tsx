import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCanWrite } from '../context/SpaceContext'
import {
  CheckCircle2,
  FileText,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import {
  ApiError,
  createNote,
  deleteNote,
  listNotes,
  listTopics,
  type NoteBrief,
  type NoteStatus,
  type Topic,
} from '../api/client'
import DraftWizard from '../components/notes/DraftWizard'
import Button from '../components/ui/Button'
import Empty, { SkeletonList } from '../components/ui/Feedback'
import Modal from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import { formatRelative } from '../utils/format'

const TEMPLATE_TEXT: Record<string, string> = {
  outline: '章节摘要',
  concept: '概念卡',
  code: '代码笔记',
  formula: '公式清单',
}

const STATUS_OPTIONS: Array<{ key: '' | NoteStatus; label: string }> = [
  { key: '', label: '全部' },
  { key: 'draft', label: '草稿' },
  { key: 'confirmed', label: '定稿' },
]

/** 命中高亮：切文本而不是插 HTML，标题里就算是 <script> 也只会被当字面量。 */
function Highlight({ text, term }: { text: string; term: string }) {
  const needle = term.trim()
  if (!needle) return <>{text}</>
  const haystack = text.toLowerCase()
  const target = needle.toLowerCase()
  const parts: ReactNode[] = []
  let from = 0
  let at = haystack.indexOf(target)
  let key = 0
  while (at >= 0) {
    if (at > from) parts.push(text.slice(from, at))
    parts.push(<mark key={key++}>{text.slice(at, at + needle.length)}</mark>)
    from = at + needle.length
    at = haystack.indexOf(target, from)
  }
  if (from < text.length) parts.push(text.slice(from))
  return <>{parts}</>
}

export default function Notes() {
  const canWrite = useCanWrite()
  const toast = useToast()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [notes, setNotes] = useState<NoteBrief[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [tags, setTags] = useState<Topic[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [activeTopic, setActiveTopic] = useState<string | null>(searchParams.get('topic'))
  const [activeTags, setActiveTags] = useState<string[]>([])
  const [status, setStatus] = useState<'' | NoteStatus>('')
  const [keyword, setKeyword] = useState(searchParams.get('keyword') ?? '')

  const [wizardOpen, setWizardOpen] = useState(searchParams.get('draft') === '1')
  const [newOpen, setNewOpen] = useState(false)
  const [newTopic, setNewTopic] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [creating, setCreating] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<NoteBrief | null>(null)

  const loadTopics = useCallback(async () => {
    const data = await listTopics()
    setTopics(data.topics)
    setTags(data.tags)
  }, [])

  const loadNotes = useCallback(async () => {
    setError('')
    try {
      const data = await listNotes({
        topic_ids: activeTopic ? [activeTopic] : undefined,
        tag_ids: activeTags.length ? activeTags : undefined,
        status,
        keyword: keyword.trim() || undefined,
      })
      setNotes(data.items)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '无法读取笔记')
    } finally {
      setLoading(false)
    }
  }, [activeTopic, activeTags, status, keyword])

  useEffect(() => {
    loadTopics().catch(() => undefined)
  }, [loadTopics])

  // 搜索输入防抖，避免每敲一个字打一次接口
  useEffect(() => {
    const timer = window.setTimeout(loadNotes, 220)
    return () => window.clearTimeout(timer)
  }, [loadNotes])

  const topicById = useMemo(() => {
    const map = new Map<string, Topic>()
    topics.forEach((t) => map.set(t.id, t))
    tags.forEach((t) => map.set(t.id, t))
    return map
  }, [topics, tags])

  const counts = useMemo(() => {
    const draft = notes.filter((item) => item.status === 'draft').length
    return { draft, confirmed: notes.length - draft }
  }, [notes])

  const toggleTag = (id: string) =>
    setActiveTags((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]))

  const openNew = () => {
    setNewTitle('')
    setNewTopic(activeTopic ?? topics[0]?.id ?? '')
    setNewOpen(true)
  }

  const onCreate = async () => {
    if (!newTopic) {
      toast.error('请先选择一个主题')
      return
    }
    setCreating(true)
    try {
      const note = await createNote({
        title: newTitle.trim() || '未命名笔记',
        topic_id: newTopic,
        markdown: '',
      })
      setNewOpen(false)
      navigate(`/notes/${note.id}`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const onDelete = async () => {
    if (!pendingDelete) return
    try {
      await deleteNote(pendingDelete.id)
      toast.success('笔记已删除，来源资料不受影响')
      setPendingDelete(null)
      await loadNotes()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  const syncTopicParam = (id: string | null) => {
    setActiveTopic(id)
    const next = new URLSearchParams(searchParams)
    if (id) next.set('topic', id)
    else next.delete('topic')
    next.delete('draft')
    setSearchParams(next, { replace: true })
  }

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>笔记</h1>
        </div>
        <div className="page-actions">
          <Button variant="ghost" size="sm" icon={<RefreshCw size={13} />} onClick={loadNotes}>
            刷新
          </Button>
          {canWrite && (
            <>
              <Button icon={<Plus size={14} />} onClick={openNew}>
                空白新建
              </Button>
              <Button
                variant="primary"
                icon={<Sparkles size={14} />}
                onClick={() => setWizardOpen(true)}
              >
                AI 起草
              </Button>
            </>
          )}
        </div>
      </header>

      <div className="kb-toolbar note-toolbar">
        <div className="note-filter-row">
          <span className="note-filter-label">主题</span>
          <div className="chip-row chip-row-scroll">
            <button
              className={`chip chip-sm${activeTopic === null ? ' is-on' : ''}`}
              onClick={() => syncTopicParam(null)}
            >
              全部
            </button>
            {topics.map((t) => (
              <button
                key={t.id}
                className={`chip chip-sm${activeTopic === t.id ? ' is-on' : ''}`}
                onClick={() => syncTopicParam(t.id)}
              >
                <i className="dot" style={{ background: t.color }} />
                {t.name}
              </button>
            ))}
          </div>
        </div>
        {tags.length > 0 && (
          <div className="note-filter-row">
            <span className="note-filter-label">标签</span>
            <div className="chip-row chip-row-scroll">
              {tags.map((t) => (
                <button
                  key={t.id}
                  className={`chip chip-sm${activeTags.includes(t.id) ? ' is-on' : ''}`}
                  onClick={() => toggleTag(t.id)}
                >
                  {t.name}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="note-toolbar-right">
          <div className="seg seg-sm">
            {STATUS_OPTIONS.map((option) => (
              <button
                key={option.key || 'all'}
                data-active={status === option.key}
                onClick={() => setStatus(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="kb-search">
            <Search size={13} />
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索标题或正文"
            />
          </label>
        </div>
      </div>

      {error && (
        <div className="notice notice-warn">
          <TriangleAlert />
          <span>{error}</span>
        </div>
      )}

      <div className="card note-list-card">
        <div className="card-head">
          <h2>
            <FileText size={16} /> 共 {notes.length} 篇
          </h2>
          <span className="head-note">
            草稿 {counts.draft} · 定稿 {counts.confirmed}
          </span>
        </div>

        {loading ? (
          <SkeletonList rows={4} />
        ) : notes.length === 0 ? (
          <Empty
            icon={<Sparkles size={20} />}
            title={keyword ? '没有匹配的笔记' : '还没有笔记'}
            description={keyword ? '换个关键词试试。' : '让模型起草，或者从空白页开始。'}
          >
            {canWrite && (
              <>
                <Button
                  variant="primary"
                  icon={<Sparkles size={13} />}
                  onClick={() => setWizardOpen(true)}
                >
                  AI 起草
                </Button>
                <Button icon={<Plus size={13} />} onClick={openNew}>
                  空白新建
                </Button>
              </>
            )}
          </Empty>
        ) : (
          <ul className="note-list">
            {notes.map((note) => (
              <li key={note.id} className="note-row" onClick={() => navigate(`/notes/${note.id}`)}>
                <div className="note-row-main">
                  <div className="note-row-title">
                    <span className="note-title-text">
                      <Highlight text={note.title} term={keyword} />
                    </span>
                    {note.status === 'confirmed' ? (
                      <span className="tag tag-ok">
                        <CheckCircle2 size={10} /> 定稿
                      </span>
                    ) : (
                      <span className="tag tag-warn">草稿</span>
                    )}
                    {note.template && (
                      <span className="tag tag-plain">{TEMPLATE_TEXT[note.template]}</span>
                    )}
                  </div>
                  <p className="note-row-excerpt">
                    {note.excerpt ? (
                      <Highlight text={note.excerpt} term={keyword} />
                    ) : (
                      '（还没有正文）'
                    )}
                  </p>
                  <div className="note-row-meta">
                    <span className="note-topic">
                      <i className="dot" style={{ background: note.topic.color }} />
                      {note.topic.name}
                    </span>
                    {note.tags.map((t) => (
                      <span key={t.id} className="pill">
                        {t.name}
                      </span>
                    ))}
                    {note.ai_written_count > 0 && (
                      <span className="note-ai" title="含无来源的推断内容">
                        AI 补写 {note.ai_written_count} 处
                      </span>
                    )}
                    {note.source_count > 0 && (
                      <span className="note-src">来源 {note.source_count} 条</span>
                    )}
                  </div>
                </div>
                <div className="note-row-side">
                  <span className="muted mono">{formatRelative(note.updated_at)}</span>
                  <div className="note-row-ops">
                    <button
                      aria-label="打开"
                      onClick={(e) => {
                        e.stopPropagation()
                        navigate(`/notes/${note.id}`)
                      }}
                    >
                      <Pencil size={12} />
                    </button>
                    {canWrite && (
                      <button
                        aria-label="删除"
                        onClick={(e) => {
                          e.stopPropagation()
                          setPendingDelete(note)
                        }}
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <DraftWizard
        open={wizardOpen}
        defaultTopicId={activeTopic ?? undefined}
        onClose={() => {
          setWizardOpen(false)
          const next = new URLSearchParams(searchParams)
          next.delete('draft')
          setSearchParams(next, { replace: true })
        }}
        onDone={(noteId) => {
          setWizardOpen(false)
          navigate(`/notes/${noteId}`)
        }}
      />

      <Modal
        open={newOpen}
        title="新建空白笔记"
        description="先定主题再写，之后随时可以改归属。"
        onClose={() => setNewOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setNewOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={creating} onClick={onCreate}>
              创建并编辑
            </Button>
          </>
        }
      >
        <div className="form-grid">
          <label className="field form-grid-full">
            <span className="field-label">标题</span>
            <input
              className="input"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="例如：红黑树的插入与删除"
              autoFocus
            />
          </label>
          <label className="field form-grid-full">
            <span className="field-label">主题</span>
            <select
              className="select"
              value={newTopic}
              onChange={(e) => setNewTopic(e.target.value)}
            >
              {topics.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {topicById.size === 0 && (
          <div className="notice notice-warn" style={{ marginTop: 12 }}>
            <TriangleAlert />
            <span>还没有主题，先去知识库建一个（或套用学习方向包）。</span>
          </div>
        )}
      </Modal>

      <Modal
        open={Boolean(pendingDelete)}
        title="删除这篇笔记？"
        description="笔记会被删除，但它引用的资料不受影响。"
        onClose={() => setPendingDelete(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setPendingDelete(null)}>
              取消
            </Button>
            <Button variant="danger" onClick={onDelete}>
              删除
            </Button>
          </>
        }
      />
    </section>
  )
}
