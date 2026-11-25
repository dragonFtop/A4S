import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  Bold,
  Check,
  CheckCircle2,
  Code2,
  Columns2,
  Copy,
  Download,
  Eye,
  Heading2,
  Italic,
  Link2,
  List,
  ListOrdered,
  Quote,
  RefreshCw,
  Sparkles,
  SquareSigma,
  Table2,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'
import {
  ApiError,
  confirmNote,
  deleteNote,
  getDraftJob,
  getNote,
  getNoteSource,
  listTopics,
  regenerateNote,
  updateNote,
  type NoteDetail,
  type NoteSourceItem,
  type NoteTemplate,
  type Topic,
} from '../api/client'
import MarkdownView from '../components/notes/MarkdownView'
import Button from '../components/ui/Button'
import Empty, { Spinner } from '../components/ui/Feedback'
import Modal from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import { formatRelative } from '../utils/format'

const TEMPLATE_TEXT: Record<NoteTemplate, string> = {
  outline: '章节摘要',
  concept: '概念卡',
  code: '代码笔记',
  formula: '公式清单',
}

type Insert = { wrap?: [string, string]; block?: string; label: string; icon: typeof Bold }

const TOOLS: Insert[] = [
  { label: '二级标题', block: '\n## 标题\n', icon: Heading2 },
  { label: '加粗', wrap: ['**', '**'], icon: Bold },
  { label: '斜体', wrap: ['*', '*'], icon: Italic },
  { label: '无序列表', block: '\n- 要点\n', icon: List },
  { label: '有序列表', block: '\n1. 步骤\n', icon: ListOrdered },
  { label: '引用', block: '\n> 原文摘录\n', icon: Quote },
  { label: '表格', block: '\n| 列 1 | 列 2 |\n| --- | --- |\n| 内容 | 内容 |\n', icon: Table2 },
  { label: '代码块', block: '\n```python\n\n```\n', icon: Code2 },
  { label: '行内公式', wrap: ['$', '$'], icon: SquareSigma },
  { label: '块级公式', block: '\n$$\n\n$$\n', icon: SquareSigma },
  { label: '链接', wrap: ['[', '](https://)'], icon: Link2 },
]

export default function NoteEditor() {
  const { id = '' } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToast()

  const [note, setNote] = useState<NoteDetail | null>(null)
  const [error, setError] = useState('')
  const [topics, setTopics] = useState<Topic[]>([])
  const [tags, setTags] = useState<Topic[]>([])

  const [title, setTitle] = useState('')
  const [markdown, setMarkdown] = useState('')
  const [baseline, setBaseline] = useState({ title: '', markdown: '' })
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const [view, setView] = useState<'split' | 'source' | 'preview'>('split')
  const [activeSource, setActiveSource] = useState<NoteSourceItem | null>(null)
  const [confirmRegen, setConfirmRegen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [regenTemplate, setRegenTemplate] = useState<NoteTemplate>('outline')
  const [regenerating, setRegenerating] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null)

  const load = useCallback(async () => {
    try {
      const data = await getNote(id)
      setNote(data)
      setTitle(data.title)
      setMarkdown(data.markdown)
      setBaseline({ title: data.title, markdown: data.markdown })
      setSavedAt(data.updated_at)
      if (data.template) setRegenTemplate(data.template)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '无法打开笔记')
    }
  }, [id])

  useEffect(() => {
    load()
    listTopics()
      .then((res) => {
        setTopics(res.topics)
        setTags(res.tags)
      })
      .catch(() => undefined)
  }, [load])

  const dirty = title !== baseline.title || markdown !== baseline.markdown

  const save = useCallback(
    async (patch?: { topic_id?: string; tag_ids?: string[] }) => {
      if (!note) return
      setSaving(true)
      try {
        const res = await updateNote(note.id, {
          ...(dirty ? { title, markdown } : {}),
          ...patch,
        })
        setNote(res)
        setTitle(res.title)
        setMarkdown(res.markdown)
        setBaseline({ title: res.title, markdown: res.markdown })
        setSavedAt(res.updated_at)
        // 后端会在定稿后改动时自动退回草稿，这里同步提示
        if (res.status === 'draft' && note.status === 'confirmed') {
          toast.info('已修改，需重新确认')
        }
      } catch (e) {
        toast.error(e instanceof ApiError ? e.message : '保存失败')
      } finally {
        setSaving(false)
      }
    },
    [note, title, markdown, dirty, toast]
  )

  // 自动保存：停手 1 秒后写库，避免边打字边请求
  useEffect(() => {
    if (!dirty || !note) return
    const timer = window.setTimeout(() => save(), 1000)
    return () => window.clearTimeout(timer)
  }, [dirty, note, save])

  const onConfirm = async () => {
    if (!note) return
    try {
      if (dirty) await save()
      const res = await confirmNote(note.id)
      setNote(res)
      toast.success('已确认定稿')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '确认失败')
    }
  }

  // 导出前先落库，保证拿到的就是当前编辑内容
  const onExport = async () => {
    if (!note) return
    try {
      if (dirty) await save()
      const res = await fetch(`/api/notes/${note.id}/export`)
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? '导出失败')
      }
      const blob = await res.blob()
      // 后端把中文名放在 filename*=UTF-8'' 里，浏览器标准解析
      const match = (res.headers.get('Content-Disposition') ?? '').match(
        /filename\*=UTF-8''([^;]+)/,
      )
      const filename = match ? decodeURIComponent(match[1]) : `${note.title || '笔记'}.md`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      toast.success('已导出 Markdown')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导出失败')
    }
  }

  const onRegenerate = async () => {
    if (!note) return
    setRegenerating(true)
    try {
      const job = await regenerateNote(note.id, {
        template: regenTemplate,
        topic_id: note.topic.id,
      })
      for (;;) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500))
        const info = await getDraftJob(job.id)
        if (info.state === 'running') continue
        if (info.state === 'failed') {
          toast.error(info.message || '重新整理失败')
          break
        }
        await load()
        toast.success('已重新整理，记得再核对一遍')
        break
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '重新整理失败')
    } finally {
      setRegenerating(false)
      setConfirmRegen(false)
    }
  }

  const onDelete = async () => {
    if (!note) return
    try {
      await deleteNote(note.id)
      toast.success('笔记已删除')
      navigate('/notes')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  const openSource = async (chunkId: string) => {
    if (!note) return
    const known = note.sources.find((s) => s.chunk_id === chunkId)
    setActiveSource(known ?? null)
    try {
      const full = await getNoteSource(note.id, chunkId)
      setActiveSource(full)
    } catch {
      // 片段已随资料删除：保留列表里的旧快照，标 missing 由前端提示
      if (known) setActiveSource({ ...known, missing: true })
    }
  }

  // ?source=<chunk_id> 进来就直接展开那条来源，方便从问答或消息里跳回原段落。
  // 只在首次加载时触发：之后用户点别的角标不该被参数拽回去。
  const deepLinked = useRef('')
  useEffect(() => {
    const chunkId = searchParams.get('source')
    if (!note || !chunkId || deepLinked.current === chunkId) return
    if (!note.sources.some((s) => s.chunk_id === chunkId)) return
    deepLinked.current = chunkId
    void openSource(chunkId)
  }, [note, searchParams])

  const insert = (tool: Insert) => {
    const el = textarea.current
    if (!el) {
      setMarkdown((prev) => prev + (tool.block ?? ''))
      return
    }
    const start = el.selectionStart
    const end = el.selectionEnd
    const selected = markdown.slice(start, end)
    let next: string
    let caret: number
    if (tool.wrap) {
      const [before, after] = tool.wrap
      next = markdown.slice(0, start) + before + (selected || '文本') + after + markdown.slice(end)
      caret = start + before.length + (selected || '文本').length
    } else {
      const block = tool.block ?? ''
      next = markdown.slice(0, start) + block + markdown.slice(end)
      caret = start + block.length
    }
    setMarkdown(next)
    window.requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(caret, caret)
    })
  }

  const toggleTag = (tagId: string) => {
    if (!note) return
    const current = note.tags.map((t) => t.id)
    const tagIds = current.includes(tagId)
      ? current.filter((t) => t !== tagId)
      : [...current, tagId]
    save({ tag_ids: tagIds })
  }

  const template = note?.template ? TEMPLATE_TEXT[note.template] : ''
  const aiCount = useMemo(() => (markdown.match(/<!--ai-->/g) ?? []).length, [markdown])

  if (error) {
    return (
      <section className="page">
        <div className="card">
          <Empty icon={<TriangleAlert size={20} />} title="打不开这篇笔记" description={error}>
            <Button icon={<ArrowLeft size={13} />} onClick={() => navigate('/notes')}>
              返回列表
            </Button>
          </Empty>
        </div>
      </section>
    )
  }

  if (!note) {
    return (
      <section className="page">
        <Spinner text="正在打开笔记…" />
      </section>
    )
  }

  return (
    <section className="page note-editor">
      <header className="note-head">
        <button className="note-back" onClick={() => navigate('/notes')} aria-label="返回列表">
          <ArrowLeft size={15} />
        </button>
        <div className="note-head-main">
          <input
            className="note-title-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="笔记标题"
          />
          <div className="note-head-meta">
            <select
              className="select select-sm"
              value={note.topic.id}
              onChange={(e) => save({ topic_id: e.target.value })}
            >
              {topics.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <div className="chip-row">
              {tags.map((t) => (
                <button
                  key={t.id}
                  className={`chip chip-xs${note.tags.some((x) => x.id === t.id) ? ' is-on' : ''}`}
                  onClick={() => toggleTag(t.id)}
                >
                  {t.name}
                </button>
              ))}
            </div>
            {template && <span className="tag tag-plain">{template}</span>}
            {note.ai_model && <span className="muted mono note-model">{note.ai_model}</span>}
          </div>
        </div>
        <div className="note-head-actions">
          <span className="note-save-state">
            {saving ? '保存中…' : dirty ? '未保存' : savedAt ? `已保存 · ${formatRelative(savedAt)}` : ''}
          </span>
          <Button
            size="sm"
            variant="ghost"
            icon={<Download size={13} />}
            onClick={onExport}
          >
            导出
          </Button>
          {note.status === 'confirmed' ? (
            <span className="tag tag-ok">
              <CheckCircle2 size={10} /> 已定稿
            </span>
          ) : (
            <Button variant="primary" size="sm" icon={<Check size={13} />} onClick={onConfirm}>
              确认定稿
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            icon={<Sparkles size={13} />}
            onClick={() => setConfirmRegen(true)}
          >
            重新整理
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<Trash2 size={13} />}
            onClick={() => setConfirmDelete(true)}
            aria-label="删除笔记"
          />
        </div>
      </header>

      {note.status === 'confirmed' && dirty && (
        <div className="notice notice-warn">
          <TriangleAlert />
          <span>已修改，需重新确认——保存后状态会退回草稿。</span>
        </div>
      )}
      {aiCount > 0 && (
        <div className="notice notice-ai">
          <Sparkles />
          <span>
            正文里有 {aiCount} 处没有资料出处的 AI 补写内容，已用灰色徽标记，核对后可以删掉或改写。
          </span>
        </div>
      )}

      <div className="note-panes" data-view={view}>
        <div className="note-pane note-pane-source">
          <div className="note-pane-head">
            <div className="note-tools">
              {TOOLS.map((tool) => {
                const Icon = tool.icon
                return (
                  <button key={tool.label} title={tool.label} onClick={() => insert(tool)}>
                    <Icon size={13} />
                  </button>
                )
              })}
            </div>
            <div className="seg seg-sm note-view-seg">
              <button data-active={view === 'source'} onClick={() => setView('source')}>
                <Code2 size={12} /> 源码
              </button>
              <button data-active={view === 'split'} onClick={() => setView('split')}>
                <Columns2 size={12} /> 双栏
              </button>
              <button data-active={view === 'preview'} onClick={() => setView('preview')}>
                <Eye size={12} /> 预览
              </button>
            </div>
          </div>
          <textarea
            ref={textarea}
            className="note-source"
            value={markdown}
            spellCheck={false}
            onChange={(e) => setMarkdown(e.target.value)}
            placeholder={'# 标题\n\n在这里写 Markdown。左边是源码，右边实时预览；\nAI 生成的内容会带来源角标 [1]，点开可以看到原文。'}
          />
        </div>

        <div className="note-pane note-pane-preview">
          <div className="note-pane-head">
            <span className="muted" style={{ fontSize: 12.5 }}>
              预览
            </span>
            <button
              className="note-refresh"
              onClick={load}
              title="从服务端重新读取"
              aria-label="重新读取"
            >
              <RefreshCw size={12} />
            </button>
          </div>
          <div className="note-preview-scroll">
            {markdown.trim() ? (
              <MarkdownView markdown={markdown} sources={note.sources} onCite={openSource} />
            ) : (
              <p className="note-empty-hint">还没有内容。用工具栏插入结构，或让模型起草。</p>
            )}
          </div>
        </div>
      </div>

      {activeSource && (
        <aside className="source-drawer">
          <div className="source-drawer-head">
            <span className="source-badge">[{activeSource.index}]</span>
            <div className="source-drawer-title">
              {activeSource.document_name ?? '来源已删除'}
              {activeSource.missing && <span className="tag tag-danger">已删除</span>}
            </div>
            <button onClick={() => setActiveSource(null)} aria-label="关闭">
              <X size={14} />
            </button>
          </div>
          <p className="source-text">{activeSource.snippet}</p>
          <div className="source-drawer-foot">
            {/* 手改正文时想给新段落挂出处，只能靠这里取出角标原文 */}
            <button
              className="source-copy"
              onClick={() => {
                const mark = `[[chunk:${activeSource.chunk_id}]]`
                navigator.clipboard
                  ?.writeText(mark)
                  .then(() => toast.success('角标已复制，粘到正文即可引用'))
                  .catch(() => toast.error('复制失败，请手动选中'))
              }}
            >
              <Copy size={11} />
              复制角标
            </button>
            <code className="source-copy-hint">[[chunk:{activeSource.chunk_id.slice(0, 8)}…]]</code>
          </div>
          {activeSource.missing && (
            <p className="source-missing">
              这份资料已经被删除了，片段内容来自笔记保存时的快照，仍然可以阅读。
            </p>
          )}
        </aside>
      )}

      <Modal
        open={confirmRegen}
        title="重新整理这篇笔记？"
        description="会沿用所选骨架重新读一遍资料，当前正文（包括你手改的内容）将被覆盖。"
        onClose={() => setConfirmRegen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmRegen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={regenerating} onClick={onRegenerate}>
              覆盖并重新整理
            </Button>
          </>
        }
      >
        <div className="wizard-templates">
          {(Object.keys(TEMPLATE_TEXT) as NoteTemplate[]).map((key) => (
            <button
              key={key}
              className="wizard-template"
              data-active={regenTemplate === key}
              onClick={() => setRegenTemplate(key)}
            >
              <strong>{TEMPLATE_TEXT[key]}</strong>
            </button>
          ))}
        </div>
      </Modal>

      <Modal
        open={confirmDelete}
        title="删除这篇笔记？"
        description="来源资料不会被删除。"
        onClose={() => setConfirmDelete(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
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
