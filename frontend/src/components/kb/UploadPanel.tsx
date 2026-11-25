import { useEffect, useRef, useState } from 'react'
import { FileUp, Globe, Paperclip, Type } from 'lucide-react'
import {
  fetchWebPage,
  pasteDocument,
  uploadDocuments,
  type DocumentItem,
  type Topic,
  type UploadResult,
} from '../../api/client'
import Button from '../ui/Button'
import Field from '../ui/Field'
import Modal from '../ui/Modal'
import { useToast } from '../ui/Toast'

const ACCEPT = '.pdf,.docx,.md,.markdown,.txt'
const MAX_MB = 20

interface Props {
  open: boolean
  onClose: () => void
  /** 深链定位：?upload=web 时初始停在网页导入 */
  initialTab?: 'file' | 'paste' | 'web'
  topics: Topic[]
  tags: Topic[]
  defaultTopicId: string
  onUploaded: (result: UploadResult) => void
  onPasted: (doc: DocumentItem) => void
}

export default function UploadPanel({
  open,
  onClose,
  initialTab,
  topics,
  tags,
  defaultTopicId,
  onUploaded,
  onPasted,
}: Props) {
  const toast = useToast()
  const [tab, setTab] = useState<'file' | 'paste' | 'web'>('file')
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [url, setUrl] = useState('')
  const [fetching, setFetching] = useState(false)
  const [topicId, setTopicId] = useState(defaultTopicId)
  const [tagIds, setTagIds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setTopicId(defaultTopicId)
      setTagIds([])
      setFiles([])
      setTitle('')
      setContent('')
      setUrl('')
      setTab(initialTab ?? 'file')
    }
  }, [open, defaultTopicId, initialTab])

  const grabWeb = async () => {
    if (!url.trim()) {
      toast.error('请先填写网页地址')
      return
    }
    setFetching(true)
    try {
      const preview = await fetchWebPage(url.trim())
      // 抓到后直接填进「粘贴文本」表单：用户先看清抓到了什么、
      // 可以删掉不想要的部分，再点「保存并索引」——确认这步不能省
      setTitle(preview.title)
      setContent(preview.markdown)
      setTab('paste')
      toast.success(`已抓取 ${preview.chars} 字，检查无误后保存`)
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setFetching(false)
    }
  }

  const addFiles = (list: FileList | null) => {
    if (!list) return
    const next = Array.from(list)
    const oversize = next.filter((f) => f.size > MAX_MB * 1024 * 1024)
    if (oversize.length) {
      toast.error(`${oversize[0].name} 超过 ${MAX_MB} MB，已跳过`)
    }
    const accepted = next.filter((f) => f.size <= MAX_MB * 1024 * 1024)
    setFiles((prev) => [...prev, ...accepted])
  }

  const toggleTag = (id: string) =>
    setTagIds((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]))

  const submit = async () => {
    if (tab === 'file' && files.length === 0) {
      toast.error('请先选择文件')
      return
    }
    if (tab === 'paste' && !content.trim()) {
      toast.error('正文不能为空')
      return
    }
    setBusy(true)
    try {
      if (tab === 'file') {
        const result = await uploadDocuments(files, topicId, tagIds)
        if (result.errors.length) {
          toast.error(`${result.errors.length} 份失败：${result.errors[0].reason}`)
        }
        if (result.documents.length) {
          toast.success(`已提交 ${result.documents.length} 份，正在后台解析`)
        }
        onUploaded(result)
      } else {
        const doc = await pasteDocument({
          content,
          title: title.trim(),
          topic_id: topicId,
          tag_ids: tagIds,
        })
        toast.success('已提交，正在后台解析')
        onPasted(doc)
      }
      onClose()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const topicOptions = topics.filter((t) => t.kind === 'topic')

  return (
    <Modal
      open={open}
      title="导入资料"
      description="解析与向量化在后台进行，可以继续做别的事。"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button variant="primary" loading={busy} onClick={submit}>
            {tab === 'file'
              ? files.length
                ? `导入 ${files.length} 份`
                : '选择文件'
              : '保存并索引'}
          </Button>
        </>
      }
    >
      <div className="seg" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'file'}
          className={`seg-item${tab === 'file' ? ' is-active' : ''}`}
          onClick={() => setTab('file')}
        >
          <Paperclip size={13} /> 文件
        </button>
        <button
          role="tab"
          aria-selected={tab === 'paste'}
          className={`seg-item${tab === 'paste' ? ' is-active' : ''}`}
          onClick={() => setTab('paste')}
        >
          <Type size={13} /> 粘贴文本
        </button>
        <button
          role="tab"
          aria-selected={tab === 'web'}
          className={`seg-item${tab === 'web' ? ' is-active' : ''}`}
          onClick={() => setTab('web')}
        >
          <Globe size={13} /> 网页
        </button>
      </div>

      {tab === 'web' ? (
        <Field
          label="网页地址"
          required
          hint="抓取正文转 Markdown，成功后可编辑再保存；不支持需登录或动态渲染的页面"
        >
          <div className="webfetch-row">
            <input
              className="input"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !fetching && grabWeb()}
              placeholder="https://… 教程 / 文档 / 博客文章"
              inputMode="url"
            />
            <Button variant="outline" loading={fetching} onClick={grabWeb}>
              抓取
            </Button>
          </div>
        </Field>
      ) : tab === 'file' ? (
        <>
          <div
            className={`dropzone${dragging ? ' is-over' : ''}`}
            role="button"
            tabIndex={0}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              addFiles(e.dataTransfer.files)
            }}
          >
            <FileUp />
            <span className="dropzone-title">拖入文件，或点击选择</span>
            <span className="dropzone-hint">PDF / DOCX / MD / TXT · 单文件 20 MB 以内</span>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              addFiles(e.target.files)
              e.target.value = ''
            }}
          />
          {files.length > 0 && (
            <ul className="file-queue">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`}>
                  <span className="file-queue-name">{f.name}</span>
                  <span className="file-queue-size">{Math.round(f.size / 1024)} KB</span>
                  <button
                    className="file-queue-remove"
                    onClick={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
                    aria-label="移除"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <>
          <Field label="标题" hint="留空则取正文首行">
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：第三章 虚拟内存"
            />
          </Field>
          <Field label="正文" required hint={`${content.length} / 100000 字符`}>
            <textarea
              className="input textarea"
              rows={10}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="把课件文字、网页正文或自己的整理粘贴进来…"
            />
          </Field>
        </>
      )}

      <Field label="归属主题" required hint="每份资料只能属于一个主题">
        <select className="input" value={topicId} onChange={(e) => setTopicId(e.target.value)}>
          {topicOptions.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </Field>

      {tags.length > 0 && (
        <Field label="标签" hint="可多选，用于跨主题筛选">
          <div className="chip-row">
            {tags.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`chip${tagIds.includes(t.id) ? ' is-on' : ''}`}
                onClick={() => toggleTag(t.id)}
              >
                <i className="dot" style={{ background: t.color }} />
                {t.name}
              </button>
            ))}
          </div>
        </Field>
      )}
    </Modal>
  )
}
