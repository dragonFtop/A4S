import type { QuestionType } from '../../api/client'

export const LETTERS = 'ABCDEF'

/** 题型中文名。服务端 /practice/meta 也给了一份，这里只作渲染兜底，避免每个组件都要传 meta。 */
export const TYPE_LABEL: Record<QuestionType, string> = {
  single_choice: '单选',
  multi_choice: '多选',
  true_false: '判断',
  short_answer: '简答',
}

export function typeLabel(key: QuestionType): string {
  return TYPE_LABEL[key] ?? key
}

export function errText(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

export function formatClock(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 用时：60 秒以内标秒，超过标分，避免「3721 秒」这种没法读的数字。 */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
}

export function pad2(value: number): string {
  return String(value).padStart(2, '0')
}
