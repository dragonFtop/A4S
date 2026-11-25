import { useEffect, useMemo, useState } from 'react'
import { FileText, Layers, Loader2, Sparkles, TriangleAlert } from 'lucide-react'
import {
  ApiError,
  draftNote,
  getDraftJob,
  getNoteTemplates,
  listDocuments,
  listTopics,
  type DocumentItem,
  type NoteTemplate,
  type NoteTemplateInfo,
  type Topic,
} from '../../api/client'
import Button from '../ui/Button'
import Modal from '../ui/Modal'
import { Spinner } from '../ui/Feedback'
import { useToast } from '../ui/Toast'

interface Props {
  open: boolean
  onClose: () => void
  onDone: (noteId: string) => void
  defaultTopicId?: string
}

/**
 * 起草向导：先定范围，再定模板。
 *
 * 模板不是风格选项而是骨架——固定结构才能让两份笔记可比、重生成不漂移，
 * 所以这里把每个模板的骨架用一句话写在卡片上，而不是只给名字。
 */
export default function DraftWizard({ open, onClose, onDone, defaultTopicId }: Props) {
  const toast = useToast()
  const [mode, setMode] = useState<'topic' | 'documents'>('documents')
  const [topicId, setTopicId] = useState(defaultTopicId ?? '')
  const [docIds, setDocIds] = useState<string[]>([])
  const [template, setTemplate] = useState<NoteTemplate>('outline')
  const [templates, setTemplates] = useState<NoteTemplateInfo[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [docs, setDocs] = useState<DocumentItem[] | null>(null)
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!open) return
    getNoteTemplates()
      .then((res) => setTemplates(res.items))
      .catch(() => setTemplates([]))
    listTopics()
      .then((res) => setTopics(res.topics))
      .catch(() => setTopics([]))
    listDocuments({ status: 'ready' })
      .then(setDocs)
      .catch(() => setDocs([]))
  }, [open])

  useEffect(() => {
    if (open && defaultTopicId) setTopicId(defaultTopicId)
  }, [open, defaultTopicId])

  const readyDocs = useMemo(() => docs ?? [], [docs])
  const selectedTopic = topics.find((t) => t.id === topicId)

  const canSubmit =
    !running &&
    (mode === 'topic' ? Boolean(topicId) : docIds.length > 0) &&
    readyDocs.length > 0

  const submit = async () => {
    setRunning(true)
    setMessage('正在收集资料片段…')
    try {
      const job = await draftNote({
        template,
        document_ids: mode === 'documents' ? docIds : [],
        topic_id: mode === 'topic' ? topicId : undefined,
        note_topic_id: mode === 'topic' ? topicId : selectedTopicOf(docIds, readyDocs) || undefined,
      })
      setMessage('正在整理…模型读完资料需要几十秒')
      // 轮询：起草是后台任务，切页也不影响生成
      for (;;) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500))
        const info = await getDraftJob(job.id)
        if (info.state === 'running') {
          setMessage('正在整理…模型读完资料需要几十秒')
          continue
        }
        if (info.state === 'failed') {
          toast.error(info.message || '起草失败')
          setMessage('')
          break
        }
        if (info.note_id) {
          toast.success('草稿已生成')
          reset()
          onDone(info.note_id)
        }
        break
      }
    } catch (e) {
      const text = e instanceof ApiError ? e.message : '起草请求失败'
      toast.error(text)
      setMessage('')
    } finally {
      setRunning(false)
    }
  }

  const reset = () => {
    setDocIds([])
    setMessage('')
    onClose()
  }

  return (
    <Modal
      open={open}
      title="AI 起草笔记"
      description="选范围 + 选模板，生成的是草稿；核对、改完再确认定稿。"
      onClose={running ? () => {} : reset}
      footer={
        <>
          <Button variant="ghost" onClick={reset} disabled={running}>
            取消
          </Button>
          <Button variant="primary" onClick={submit} disabled={!canSubmit} loading={running}>
            {running ? '生成中…' : '开始整理'}
          </Button>
        </>
      }
    >
      {docs === null ? (
        <Spinner text="正在读取资料…" />
      ) : readyDocs.length === 0 ? (
        <div className="notice notice-warn">
          <TriangleAlert />
          <span>
            还没有已完成索引的资料。先到知识库导入并等待状态变为「已就绪」，再来整理笔记。
          </span>
        </div>
      ) : (
        <div className="wizard">
          <div className="wizard-step">
            <span className="wizard-index">1</span>
            <div className="wizard-body">
              <div className="wizard-title">整理范围</div>
              <div className="seg">
                <button data-active={mode === 'documents'} onClick={() => setMode('documents')}>
                  <FileText size={13} /> 指定资料
                </button>
                <button data-active={mode === 'topic'} onClick={() => setMode('topic')}>
                  <Layers size={13} /> 整个主题
                </button>
              </div>

              {mode === 'documents' ? (
                <div className="wizard-docs">
                  {readyDocs.map((doc) => {
                    const active = docIds.includes(doc.id)
                    return (
                      <label key={doc.id} className="wizard-doc" data-active={active}>
                        <input
                          type="checkbox"
                          checked={active}
                          onChange={() =>
                            setDocIds((prev) =>
                              active ? prev.filter((id) => id !== doc.id) : [...prev, doc.id]
                            )
                          }
                        />
                        <span className="wizard-doc-name">{doc.name}</span>
                        <span className="wizard-doc-meta mono">{doc.chunk_count} 片段</span>
                      </label>
                    )
                  })}
                </div>
              ) : (
                <div className="wizard-topics">
                  {topics.map((topic) => (
                    <button
                      key={topic.id}
                      className="chip-sm"
                      data-active={topicId === topic.id}
                      onClick={() => setTopicId(topic.id)}
                    >
                      <i style={{ background: topic.color }} />
                      {topic.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="wizard-step">
            <span className="wizard-index">2</span>
            <div className="wizard-body">
              <div className="wizard-title">笔记骨架</div>
              <div className="wizard-templates">
                {templates.map((item) => (
                  <button
                    key={item.key}
                    className="wizard-template"
                    data-active={template === item.key}
                    onClick={() => setTemplate(item.key)}
                  >
                    <strong>{item.label}</strong>
                    <span>{item.hint}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {message && (
            <div className="wizard-status">
              {running ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
              <span>{message}</span>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

function selectedTopicOf(docIds: string[], docs: DocumentItem[]): string {
  const first = docs.find((doc) => docIds.includes(doc.id))
  return first?.topic.id ?? ''
}
