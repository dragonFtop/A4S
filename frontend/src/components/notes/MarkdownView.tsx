import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import 'katex/dist/katex.min.css'
import type { NoteSourceItem } from '../../api/client'

/**
 * 笔记正文渲染。
 *
 * 两个协议标记不能交给 `dangerouslySetInnerHTML` 处理（LLM 输出不可全信）：
 * 先把它们改写成 Markdown 链接，再由自定义 `a` 渲染器换成角标／徽记。
 * - `[[chunk:xxx]]` → `[n](#chunk-xxx)` → 可点击的来源角标
 * - `<!--ai-->`    → `[AI 补写](#ai-written)` → 灰色小徽记
 */
function transform(markdown: string, sources: NoteSourceItem[]): string {
  const index = new Map(sources.map((s) => [s.chunk_id, s.index]))
  const lines = markdown.split('\n')
  let inFence = false

  return lines
    .map((line) => {
      if (line.trimStart().startsWith('```')) {
        inFence = !inFence
        return line
      }
      let out = line
      if (!inFence) {
        // 代码块里出现角标不改写，避免破坏示例代码
      }
      out = out.replace(/\[\[chunk:([A-Za-z0-9-]+)\]\]/g, (whole, id: string) => {
        const n = index.get(id)
        return n ? `[${n}](#chunk-${id})` : ''
      })
      out = out.replace(/<!--ai-->/g, '[AI 补写](#ai-written)')
      return out
    })
    .join('\n')
}

interface Props {
  markdown: string
  sources?: NoteSourceItem[]
  onCite?: (chunkId: string) => void
  className?: string
}

export default function MarkdownView({ markdown, sources = [], onCite, className = '' }: Props) {
  const content = useMemo(() => transform(markdown, sources), [markdown, sources])

  return (
    <div className={`md ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          a({ href, children }) {
            if (href?.startsWith('#chunk-')) {
              const chunkId = href.slice('#chunk-'.length)
              const source = sources.find((s) => s.chunk_id === chunkId)
              return (
                <button
                  type="button"
                  className="cite"
                  data-missing={source?.missing ?? false}
                  title={source?.missing ? '来源资料已删除' : '查看原文片段'}
                  onClick={() => onCite?.(chunkId)}
                >
                  {children}
                </button>
              )
            }
            if (href === '#ai-written') {
              return <span className="ai-mark">AI 补写</span>
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            )
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
