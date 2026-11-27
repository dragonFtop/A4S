import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useCanWrite } from '../context/SpaceContext'
import {
  Check,
  FileText,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  TriangleAlert,
  Upload,
} from 'lucide-react'
import {
  applyPacks,
  batchUpdateDocuments,
  createTopic,
  deleteDocument,
  deleteTopic,
  getDocumentStats,
  getSeeded,
  listDocuments,
  listPacks,
  listTopics,
  updateTopic,
  type DocumentItem,
  type DocumentStats,
  type Topic,
  type TopicPack,
} from '../api/client'
import HealthPanel from '../components/kb/HealthPanel'
import PackPicker from '../components/kb/PackPicker'
import UploadPanel from '../components/kb/UploadPanel'
import Button from '../components/ui/Button'
import Empty, { SkeletonList } from '../components/ui/Feedback'
import Field from '../components/ui/Field'
import Modal from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import { formatBytes, formatDateTime } from '../utils/format'

// 记住「先跳过」这个选择，跳过一次后不再每次进知识库都弹
const PACK_SKIP_KEY = 'a4s-kb-pack-skipped'

const STATUS_TEXT: Record<string, string> = {
  pending: '排队中',
  processing: '处理中',
  ready: '已就绪',
  failed: '解析失败',
}

export default function KnowledgeBase() {
  const canWrite = useCanWrite()
  const toast = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const [topics, setTopics] = useState<Topic[]>([])
  const [tags, setTags] = useState<Topic[]>([])
  const [docs, setDocs] = useState<DocumentItem[]>([])
  const [stats, setStats] = useState<DocumentStats | null>(null)
  const [packs, setPacks] = useState<TopicPack[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 深链：?topic=<id> 预选主题，?upload=1 直接打开导入面板（概览页的快捷入口用），
  // ?upload=web 打开并定位到网页导入
  const [activeTopic, setActiveTopic] = useState<string | null>(searchParams.get('topic'))
  const [activeTags, setActiveTags] = useState<string[]>([])
  const [keyword, setKeyword] = useState('')
  const [selected, setSelected] = useState<string[]>([])

  const uploadParam = searchParams.get('upload')
  const [uploadOpen, setUploadOpen] = useState(uploadParam === '1' || uploadParam === 'web')
  const [packOpen, setPackOpen] = useState(false)
  const [packChecked, setPackChecked] = useState(false)
  const [topicModal, setTopicModal] = useState<{ open: boolean; editing?: Topic }>({ open: false })
  const [topicName, setTopicName] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<DocumentItem | null>(null)
  const [batchTopic, setBatchTopic] = useState('')

  const loadTopics = useCallback(async () => {
    const data = await listTopics()
    setTopics(data.topics)
    setTags(data.tags)
  }, [])

  const loadDocs = useCallback(async () => {
    const [list, s] = await Promise.all([
      listDocuments({
        topic_ids: activeTopic ? [activeTopic] : undefined,
        tag_ids: activeTags.length ? activeTags : undefined,
        keyword: keyword.trim() || undefined,
      }),
      getDocumentStats(),
    ])
    setDocs(list)
    setStats(s)
  }, [activeTopic, activeTags, keyword])

  const loadAll = useCallback(async () => {
    setError('')
    try {
      await Promise.all([loadTopics(), loadDocs()])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [loadTopics, loadDocs])

  useEffect(() => {
    setLoading(true)
    loadAll()
  }, [loadAll])

  // 有任务在跑时短轮询，跑完自动停
  useEffect(() => {
    if (!stats || stats.processing === 0) return
    const timer = setTimeout(() => {
      loadDocs().catch(() => undefined)
    }, 2000)
    return () => clearTimeout(timer)
  }, [stats, loadDocs])

  // 零主题时问一次学习方向
  useEffect(() => {
    if (packChecked || topics.length === 0) return
    setPackChecked(true)
    if (localStorage.getItem(PACK_SKIP_KEY) === '1') return
    getSeeded()
      .then(async (r) => {
        if (r.seeded) return
        const list = await listPacks()
        setPacks(list)
        setPackOpen(true)
      })
      .catch(() => undefined)
  }, [topics.length, packChecked])

  const closeUpload = () => {
    setUploadOpen(false)
    if (searchParams.has('upload')) {
      const next = new URLSearchParams(searchParams)
      next.delete('upload')
      setSearchParams(next, { replace: true })
    }
  }

  const topicById = useMemo(() => {
    const map = new Map<string, Topic>()
    topics.forEach((t) => map.set(t.id, t))
    tags.forEach((t) => map.set(t.id, t))
    return map
  }, [topics, tags])

  const toggleTag = (id: string) =>
    setActiveTags((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]))

  const toggleSelect = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id]))

  const fileName = (d: DocumentItem) => d.ext.replace('.', '') || 'txt'

  const onApplyPacks = async (ids: string[]) => {
    const r = await applyPacks(ids)
    toast.success(`已建 ${r.created_topics} 个主题、${r.created_tags} 个标签`)
    localStorage.removeItem(PACK_SKIP_KEY)
    setPackOpen(false)
    await loadAll()
  }

  const dismissPacks = () => {
    localStorage.setItem(PACK_SKIP_KEY, '1')
    setPackOpen(false)
  }

  const onSaveTopic = async () => {
    const name = topicName.trim()
    if (!name) return
    try {
      if (topicModal.editing) {
        await updateTopic(topicModal.editing.id, { name })
        toast.success('已重命名')
      } else {
        await createTopic({ name })
        toast.success(`已创建主题「${name}」`)
      }
      setTopicModal({ open: false })
      setTopicName('')
      await loadAll()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const onDeleteTopic = async (topic: Topic) => {
    const hint =
      topic.count > 0
        ? `「${topic.name}」下有 ${topic.count} 份资料，删除后它们会移到「未分类」，资料本身不会丢。`
        : `删除主题「${topic.name}」？`
    if (!window.confirm(hint)) return
    try {
      const r = await deleteTopic(topic.id)
      toast.success(r.moved_documents ? `已删除，${r.moved_documents} 份资料移到未分类` : '已删除')
      if (activeTopic === topic.id) setActiveTopic(null)
      await loadAll()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const onDeleteDoc = async () => {
    if (!confirmDelete) return
    try {
      await deleteDocument(confirmDelete.id)
      toast.success('已删除资料与其向量片段')
      setSelected((prev) => prev.filter((id) => id !== confirmDelete.id))
      setConfirmDelete(null)
      await loadDocs()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const onBatchTopic = async () => {
    if (!batchTopic || selected.length === 0) return
    try {
      const r = await batchUpdateDocuments({ document_ids: selected, topic_id: batchTopic })
      toast.success(`已移动 ${r.updated} 份资料`)
      setSelected([])
      setBatchTopic('')
      await loadAll()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const onBatchTag = async (tagId: string) => {
    if (selected.length === 0) return
    try {
      const r = await batchUpdateDocuments({ document_ids: selected, add_tag_ids: [tagId] })
      toast.success(`已给 ${r.updated} 份资料打上「${topicById.get(tagId)?.name ?? '标签'}」`)
      await loadDocs()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  return (
    <section className="page kb-page">
      <header className="page-head">
        <div>
          <h1>知识库</h1>
        </div>
        <div className="page-actions">
          <Button variant="ghost" size="sm" icon={<RefreshCw size={13} />} onClick={loadAll}>
            刷新
          </Button>
          {canWrite && (
            <Button
              variant="primary"
              icon={<Upload size={14} />}
              onClick={() => setUploadOpen(true)}
            >
              导入资料
            </Button>
          )}
        </div>
      </header>

      <div className="figures">
        <div className="figure">
          <span className="figure-value">
            {stats?.total ?? docs.length}
            <i>份</i>
          </span>
          <span className="figure-label">资料 · 就绪 {stats?.ready ?? 0} 份</span>
        </div>
        <div className="figure">
          <span className="figure-value">
            {stats?.chunks ?? 0}
            <i>个</i>
          </span>
          <span className="figure-label">知识片段</span>
        </div>
        <div className={`figure${(stats?.processing ?? 0) > 0 ? ' is-busy' : ''}`}>
          <span className="figure-value">{stats?.processing ?? 0}</span>
          <span className="figure-label">处理中</span>
        </div>
        <div className={`figure${(stats?.failed ?? 0) > 0 ? ' is-danger' : ''}`}>
          <span className="figure-value">{stats?.failed ?? 0}</span>
          <span className="figure-label">解析失败</span>
        </div>
      </div>

      {error && (
        <div className="notice notice-warn">
          <TriangleAlert />
          <span>无法加载数据：{error}</span>
        </div>
      )}

      <HealthPanel />

      <div className="kb-shell">
        <aside className="kb-side" aria-label="主题导航">
          <div className="kb-side-head">
            <span>主题</span>
            {canWrite && (
              <button
                className="kb-side-add"
                onClick={() => {
                  setTopicName('')
                  setTopicModal({ open: true })
                }}
                aria-label="新建主题"
              >
                <Plus size={13} />
              </button>
            )}
          </div>

          <button
            className={`kb-topic${activeTopic === null ? ' is-active' : ''}`}
            onClick={() => setActiveTopic(null)}
          >
            <i className="dot" style={{ background: 'var(--amber)' }} />
            <span className="kb-topic-name">全部资料</span>
            <span className="kb-topic-count">{stats?.total ?? 0}</span>
          </button>

          {topics.map((t) => (
            <div key={t.id} className="kb-topic-row">
              <button
                className={`kb-topic${activeTopic === t.id ? ' is-active' : ''}`}
                onClick={() => setActiveTopic(t.id)}
              >
                <i className="dot" style={{ background: t.color }} />
                <span className="kb-topic-name">{t.name}</span>
                <span className="kb-topic-count">{t.count}</span>
              </button>
              {canWrite && !t.is_builtin && (
                <span className="kb-topic-ops">
                  <button
                    onClick={() => {
                      setTopicName(t.name)
                      setTopicModal({ open: true, editing: t })
                    }}
                    aria-label="重命名"
                  >
                    <Pencil size={11} />
                  </button>
                  <button onClick={() => onDeleteTopic(t)} aria-label="删除主题">
                    <Trash2 size={11} />
                  </button>
                </span>
              )}
            </div>
          ))}
        </aside>

        <div className="kb-main">
          <div className="kb-bar">
            <label className="kb-search">
              <Search size={13} />
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="搜索资料名"
              />
            </label>
            {tags.length > 0 && (
              <div className="chip-row">
                {tags.map((t) => (
                  <button
                    key={t.id}
                    className={`chip chip-sm${activeTags.includes(t.id) ? ' is-on' : ''}`}
                    onClick={() => toggleTag(t.id)}
                  >
                    <i className="dot" style={{ background: t.color }} />
                    {t.name}
                  </button>
                ))}
              </div>
            )}
            <span className="kb-count">{docs.length} 份</span>
          </div>

          {selected.length > 0 && (
            <div className="batch-bar">
              <span className="batch-count">
                <Check size={13} /> 已选 {selected.length} 份
              </span>
              <select
                className="input input-sm"
                value={batchTopic}
                onChange={(e) => setBatchTopic(e.target.value)}
              >
                <option value="">移到主题…</option>
                {topics
                  .filter((t) => !t.is_builtin)
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
              </select>
              <Button size="sm" variant="outline" onClick={onBatchTopic} disabled={!batchTopic}>
                应用
              </Button>
              <span className="batch-sep" />
              <span className="batch-hint">打标：</span>
              {tags.slice(0, 4).map((t) => (
                <button key={t.id} className="chip chip-sm" onClick={() => onBatchTag(t.id)}>
                  {t.name}
                </button>
              ))}
              <button className="batch-clear" onClick={() => setSelected([])}>
                取消选择
              </button>
            </div>
          )}

          <div className="kb-list">
            <div className="kb-list-head">
              <h2>{activeTopic ? topicById.get(activeTopic)?.name : '全部资料'}</h2>
              <span className="kb-list-note">
                {activeTags.length > 0 ? `${activeTags.length} 个标签筛选 · ` : ''}按导入时间倒序
              </span>
            </div>

            {loading ? (
              <SkeletonList rows={5} />
            ) : docs.length === 0 ? (
              <Empty
                icon={<FileText size={22} />}
                title={activeTopic || activeTags.length ? '该范围内还没有资料' : '知识库还是空的'}
                description={
                  activeTopic || activeTags.length ? '当前筛选下没有资料。' : '支持 PDF / Word / Markdown / 纯文本。'
                }
              >
                <Button variant="primary" size="sm" onClick={() => setUploadOpen(true)}>
                  导入第一份资料
                </Button>
              </Empty>
            ) : (
              <div>
                {docs.map((d) => {
                  const failed = d.status === 'failed'
                  const checked = selected.includes(d.id)
                  return (
                    <div key={d.id} className={`kb-row${checked ? ' is-selected' : ''}`}>
                      <button
                        className={`checkbox${checked ? ' is-on' : ''}`}
                        onClick={() => toggleSelect(d.id)}
                        aria-label="选择"
                        aria-pressed={checked}
                      >
                        {checked && <Check size={11} />}
                      </button>
                      <span className={`kb-row-ext${failed ? ' is-fail' : ''}`}>
                        {fileName(d)}
                      </span>
                      <span className="kb-row-main">
                        <span className="kb-row-title">{d.name}</span>
                        <span className="kb-row-meta">
                          <span>{d.topic.name}</span>
                          <span>{formatBytes(d.size)}</span>
                          <span>{d.chunk_count} 片段</span>
                          <span>{formatDateTime(d.created_at)}</span>
                          {d.tags.map((t) => (
                            <span key={t.id} className="tag-pill">
                              {t.name}
                            </span>
                          ))}
                        </span>
                        {failed && d.error && <span className="kb-row-error">{d.error}</span>}
                      </span>
                      <span
                        className={`kb-row-state${
                          failed ? ' is-fail' : d.status === 'ready' ? '' : ' is-busy'
                        }`}
                      >
                        {STATUS_TEXT[d.status] ?? d.status}
                      </span>
                      {canWrite && (
                        <button
                          className="row-action"
                          onClick={() => setConfirmDelete(d)}
                          aria-label="删除"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      <UploadPanel
        open={uploadOpen}
        onClose={closeUpload}
        initialTab={uploadParam === 'web' ? 'web' : undefined}
        topics={topics}
        tags={tags}
        defaultTopicId={activeTopic ?? topics.find((t) => t.is_builtin)?.id ?? ''}
        onUploaded={() => loadDocs().catch(() => undefined)}
        onPasted={() => loadDocs().catch(() => undefined)}
      />

      <PackPicker
        open={packOpen}
        packs={packs}
        onApply={onApplyPacks}
        onSkip={dismissPacks}
      />

      <Modal
        open={topicModal.open}
        title={topicModal.editing ? '重命名主题' : '新建主题'}
        onClose={() => setTopicModal({ open: false })}
        footer={
          <>
            <Button variant="ghost" onClick={() => setTopicModal({ open: false })}>
              取消
            </Button>
            <Button variant="primary" onClick={onSaveTopic} disabled={!topicName.trim()}>
              保存
            </Button>
          </>
        }
      >
        <Field label="主题名称" required hint="建议用具体课程名，例如「计算机组成原理」">
          <input
            className="input"
            value={topicName}
            autoFocus
            onChange={(e) => setTopicName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onSaveTopic()}
            placeholder="例如：密码学"
          />
        </Field>
      </Modal>

      <Modal
        open={Boolean(confirmDelete)}
        title="删除资料"
        onClose={() => setConfirmDelete(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(null)}>
              取消
            </Button>
            <Button variant="danger" onClick={onDeleteDoc}>
              删除
            </Button>
          </>
        }
      >
        <p className="modal-text">
          「{confirmDelete?.name}」及其 {confirmDelete?.chunk_count ?? 0} 个向量片段会被一并删除，
          问答与出题将不再引用它。此操作不可撤销。
        </p>
      </Modal>
    </section>
  )
}
