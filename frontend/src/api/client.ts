const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(body.detail ?? `请求失败（${res.status}）`, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function apiPost<T>(path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
    // 抓取外部网页这类慢请求需要显式放宽；其余请求维持浏览器默认
    signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined,
  })
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined })
}

export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined })
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' })
}

/** multipart 上传：不能带 Content-Type，浏览器要自己加 boundary */
export function apiUpload<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: 'POST', body: form, headers: {} })
}

/* ---------------- 系统 ---------------- */

export interface Health {
  status: string
  version: string
  env: string
  db: boolean
  llm_configured: boolean
  embedding_provider: string
}

export const getHealth = () => apiGet<Health>('/health')

/* ---------------- 概览 ---------------- */

export interface OverviewData {
  documents: {
    total: number
    ready: number
    failed: number
    processing: number
    chunks: number
    added_last_7d: number
  }
  practice: {
    sessions: number
    finished: number
    avg_score: number
    finished_last_7d: number
  }
  wrong: {
    total: number
    added_last_7d: number
    top_repeated: number
  }
  review: { reports: number }
  notes: { total: number; draft: number; confirmed: number }
  daily: Array<{ date: string; documents: number; practices: number }>
  recent_documents: Array<{
    id: string
    name: string
    ext: string
    status: string
    chunk_count: number
    created_at: string
    topic: TopicBrief
  }>
  recent_wrong: Array<{
    id: string
    question_id: string
    document_id: string | null
    stem: string
    qtype: string
    wrong_count: number
    last_wrong_at: string
  }>
  recent_reports: Array<{
    id: string
    window_days: number
    period_start: string
    period_end: string
    created_at: string
  }>
  activity: Array<{ kind: string; at: string; title: string; detail: string }>
  generated_at: string
}

export const getOverview = () => apiGet<OverviewData>('/overview')

/* ---------------- 主题与标签 ---------------- */

export interface TopicBrief {
  id: string
  name: string
  color: string
}

export interface Topic extends TopicBrief {
  kind: 'topic' | 'tag'
  sort_order: number
  count: number
  is_builtin: boolean
}

export interface TopicList {
  topics: Topic[]
  tags: Topic[]
}

export const listTopics = () => apiGet<TopicList>('/topics')

export const getSeeded = () => apiGet<{ seeded: boolean }>('/topics/seeded')

export const createTopic = (body: { name: string; kind?: 'topic' | 'tag'; color?: string }) =>
  apiPost<Topic>('/topics', body)

export const updateTopic = (
  id: string,
  body: { name?: string; color?: string; sort_order?: number }
) => apiPatch<Topic>(`/topics/${id}`, body)

export const deleteTopic = (id: string) =>
  apiDelete<{ moved_documents: number }>(`/topics/${id}`)

export interface TopicPack {
  id: string
  name: string
  summary: string
  topics: string[]
}

export const listPacks = () => apiGet<TopicPack[]>('/topics/packs')

export const applyPacks = (pack_ids: string[]) =>
  apiPost<{ created_topics: number; created_tags: number; skipped_packs: string[] }>(
    '/topics/packs',
    { pack_ids }
  )

/* ---------------- 知识库 ---------------- */

export interface DocumentItem {
  id: string
  name: string
  ext: string
  size: number
  status: string
  chunk_count: number
  error: string | null
  created_at: string
  ready_at: string | null
  topic: TopicBrief
  tags: TopicBrief[]
}

export interface DocumentStats {
  total: number
  ready: number
  processing: number
  failed: number
  chunks: number
}

export interface DocumentQuery {
  topic_ids?: string[]
  tag_ids?: string[]
  status?: string
  keyword?: string
}

function docQuery(q: DocumentQuery = {}): string {
  const params = new URLSearchParams()
  if (q.topic_ids?.length) params.set('topic_ids', q.topic_ids.join(','))
  if (q.tag_ids?.length) params.set('tag_ids', q.tag_ids.join(','))
  if (q.status) params.set('status', q.status)
  if (q.keyword) params.set('keyword', q.keyword)
  const s = params.toString()
  return s ? `?${s}` : ''
}

export const listDocuments = (q: DocumentQuery = {}) =>
  apiGet<DocumentItem[]>(`/documents${docQuery(q)}`)

export const getDocumentStats = () => apiGet<DocumentStats>('/documents/stats')

export interface UploadResult {
  documents: DocumentItem[]
  errors: Array<{ name: string; reason: string }>
}

export function uploadDocuments(files: File[], topicId: string, tagIds: string[]) {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  form.append('topic_id', topicId)
  form.append('tag_ids', tagIds.join(','))
  return apiUpload<UploadResult>('/documents/upload', form)
}

export const pasteDocument = (body: {
  content: string
  title: string
  topic_id: string
  tag_ids: string[]
}) => apiPost<DocumentItem>('/documents/paste', body)

export interface WebFetchPreview {
  url: string
  title: string
  markdown: string
  chars: number
}

export const fetchWebPage = (url: string) =>
  apiPost<WebFetchPreview>('/documents/webfetch', { url }, 90_000)

/* ---------------- 问答（SSE） ---------------- */

export interface Citation {
  index: number
  chunk_id: string
  document_id: string | null
  document_name: string | null
  snippet: string
  page: number | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatRange {
  topic_id?: string
  tag_ids?: string[]
  document_ids?: string[]
}

/**
 * 检索不到内容时后端发来的说明。
 * empty  知识库里还没有任何片段 → 引导去导入
 * no_match 有片段但都不相关（或超出当前范围）→ 引导换说法 / 放宽范围
 */
export interface NoContextInfo {
  reason: 'empty' | 'no_match'
  detail: string
  /** 是否带了主题/标签/资料范围限制 */
  scoped: boolean
  /** 服务端是否会在声明之后继续用通用知识作答 */
  fallback: boolean
}

export interface ChatHandlers {
  onSources?: (items: Citation[]) => void
  onDelta?: (text: string) => void
  onNoContext?: (info: NoContextInfo) => void
  onError?: (detail: string) => void
  onDone?: () => void
}

/** POST 方式的 SSE 只能用 fetch 手动读流；EventSource 不支持 POST body。 */
export async function chatStream(
  body: { question: string; history: ChatMessage[] } & ChatRange,
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => ({}))
    throw new ApiError(detail.detail ?? `请求失败（${res.status}）`, res.status)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let splitAt = buffer.indexOf('\n\n')
    while (splitAt >= 0) {
      const block = buffer.slice(0, splitAt)
      buffer = buffer.slice(splitAt + 2)
      splitAt = buffer.indexOf('\n\n')
      let event = ''
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data = line.slice(6)
      }
      if (!event) continue
      const parsed = data ? JSON.parse(data) : {}
      if (event === 'sources') handlers.onSources?.(parsed.items ?? [])
      else if (event === 'delta') handlers.onDelta?.(parsed.text ?? '')
      else if (event === 'no_context')
        handlers.onNoContext?.({
          reason: parsed.reason === 'empty' ? 'empty' : 'no_match',
          detail: parsed.detail ?? '',
          scoped: Boolean(parsed.scoped),
          fallback: Boolean(parsed.fallback),
        })
      else if (event === 'error') handlers.onError?.(parsed.detail ?? '生成失败')
      else if (event === 'done') handlers.onDone?.()
    }
  }
}

export const deleteDocument = (id: string) =>
  apiDelete<{ deleted: string }>(`/documents/${id}`)

export const batchUpdateDocuments = (body: {
  document_ids: string[]
  topic_id?: string
  add_tag_ids?: string[]
  remove_tag_ids?: string[]
}) => apiPost<{ updated: number }>('/documents/batch', body)

export interface ChunkItem {
  id: string
  index: number
  text: string
  page: number | null
}

export const listChunks = (documentId: string) =>
  apiGet<ChunkItem[]>(`/documents/${documentId}/chunks`)

export const reingestDocument = (id: string) =>
  apiPost<DocumentItem>(`/documents/${id}/reingest`)

/* ---------------- 设置 ---------------- */

export interface AppSettings {
  llm: {
    provider: string
    base_url: string
    model: string
    temperature: number
    timeout: number
    api_key: string
    configured: boolean
  }
  embedding: {
    provider: string
    model: string
    base_url: string
    dimension: number
    api_key: string
    configured: boolean
  }
  smtp: {
    host: string
    port: number
    use_ssl: boolean
    user: string
    password: string
    from_addr: string
    to: string
    configured: boolean
  }
  scheduler: {
    enabled: boolean
    day_of_week: string
    hour: number
    minute: number
    next_run?: string | null
    job_registered?: boolean
  }
  runtime: {
    app_env: string
    version: string
    database_url: string
    data_dir: string
    env_file: string
    env_file_exists: boolean
  }
  // 下拉框可选项：llm 用预设表的键，embedding 用两个主名（别名后端仍认）
  llm_providers: string[]
  embedding_providers: string[]
  llm_presets: Array<{ provider: string; label: string; base_url: string; model: string }>
}

export const getSettings = () => apiGet<AppSettings>('/settings')

export const updateSettings = (changes: Record<string, unknown>) =>
  apiPut<{ saved: string[]; settings: AppSettings; index?: IndexStatus | null }>(
    '/settings',
    changes
  )

/* ---------------- 向量索引 ---------------- */

export interface RebuildJob {
  state: 'idle' | 'running' | 'done' | 'failed'
  total: number
  done: number
  message: string
  started_at: string | null
  finished_at: string | null
}

export interface IndexStatus {
  enabled: boolean
  provider: string
  model: string
  index_dimension: number | null
  configured_dimension: number
  compatible: boolean
  chunk_count: number
  vector_count: number
  needs_rebuild: boolean
  reason: string
  job: RebuildJob
  can_rebuild: boolean
}

export const getIndexStatus = () => apiGet<IndexStatus>('/settings/index')

export const rebuildIndex = () =>
  apiPost<{ ok: boolean; job: RebuildJob }>('/settings/index/rebuild')

export interface TestResult {
  ok: boolean
  message: string
  detail?: string
}

export const testConnection = (
  target: 'llm' | 'embedding' | 'smtp',
  overrides: Record<string, unknown> = {}
) => apiPost<TestResult>('/settings/test', { target, overrides })

/* ---------------- 笔记 ---------------- */

export type NoteStatus = 'draft' | 'confirmed'
export type NoteTemplate = 'outline' | 'concept' | 'code' | 'formula'

export interface NoteSourceItem {
  chunk_id: string
  document_id: string | null
  document_name: string | null
  snippet: string
  index: number
  missing: boolean
}

export interface NoteBrief {
  id: string
  title: string
  excerpt: string
  status: NoteStatus
  source_type: string
  template: NoteTemplate | null
  ai_written_count: number
  source_count: number
  topic: TopicBrief
  tags: TopicBrief[]
  created_at: string
  updated_at: string
  confirmed_at: string | null
}

export interface NoteDetail {
  id: string
  title: string
  markdown: string
  status: NoteStatus
  source_type: string
  template: NoteTemplate | null
  ai_model: string | null
  ai_written_count: number
  topic: TopicBrief
  tags: TopicBrief[]
  sources: NoteSourceItem[]
  created_at: string
  updated_at: string
  confirmed_at: string | null
}

export interface NoteQuery {
  topic_ids?: string[]
  tag_ids?: string[]
  status?: NoteStatus | ''
  keyword?: string
  limit?: number
  offset?: number
}

function noteQuery(q: NoteQuery = {}): string {
  const params = new URLSearchParams()
  if (q.topic_ids?.length) params.set('topic_ids', q.topic_ids.join(','))
  if (q.tag_ids?.length) params.set('tag_ids', q.tag_ids.join(','))
  if (q.status) params.set('status', q.status)
  if (q.keyword) params.set('keyword', q.keyword)
  if (q.limit) params.set('limit', String(q.limit))
  if (q.offset) params.set('offset', String(q.offset))
  const s = params.toString()
  return s ? `?${s}` : ''
}

export const listNotes = (q: NoteQuery = {}) =>
  apiGet<{ items: NoteBrief[]; total: number }>(`/notes${noteQuery(q)}`)

export const getNote = (id: string) => apiGet<NoteDetail>(`/notes/${id}`)

export const createNote = (body: {
  title?: string
  topic_id: string
  tag_ids?: string[]
  markdown?: string
  template?: NoteTemplate | null
  source_type?: 'manual' | 'chat'
}) => apiPost<NoteDetail>('/notes', body)

export const updateNote = (
  id: string,
  body: {
    title?: string
    markdown?: string
    topic_id?: string
    tag_ids?: string[]
    template?: NoteTemplate | null
  }
) => apiPatch<NoteDetail>(`/notes/${id}`, body)

export const confirmNote = (id: string) => apiPost<NoteDetail>(`/notes/${id}/confirm`)

export const deleteNote = (id: string) => apiDelete<{ deleted: string }>(`/notes/${id}`)

export const getNoteSource = (noteId: string, chunkId: string) =>
  apiGet<NoteSourceItem>(`/notes/${noteId}/sources/${chunkId}`)

export interface DraftJob {
  id: string
  state: 'running' | 'done' | 'failed'
  note_id: string | null
  message: string
  started_at: string | null
  finished_at: string | null
}

export interface DraftRequest {
  template: NoteTemplate
  document_ids?: string[]
  topic_id?: string
  note_topic_id?: string
  tag_ids?: string[]
  title?: string
}

export const draftNote = (body: DraftRequest) => apiPost<DraftJob>('/notes/draft', body)

export const regenerateNote = (id: string, body: DraftRequest) =>
  apiPost<DraftJob>(`/notes/${id}/regenerate?confirm=true`, body)

export const getDraftJob = (jobId: string) => apiGet<DraftJob>(`/notes/drafts/${jobId}`)

export interface NoteTemplateInfo {
  key: NoteTemplate
  label: string
  hint: string
}

export const getNoteTemplates = () =>
  apiGet<{ items: NoteTemplateInfo[] }>('/notes/templates')

/* ---------------- 练习（出题 / 草稿） ---------------- */

export type QuestionType = 'single_choice' | 'multi_choice' | 'true_false' | 'short_answer'

export interface PracticeSource {
  document_id: string
  document_name: string
  chunk_id: string | null
  snippet: string
  page: number | null
}

export interface PracticeQuestion {
  id: string
  type: QuestionType
  stem: string
  options: string[]
  answer: string
  explanation: string
  knowledge_point: string | null
  document_id: string | null
  topic_id: string | null
  sources: PracticeSource[]
}

export interface PracticeAnswer {
  question_id: string
  response: string
  is_correct: boolean
  duration_seconds: number
}

export interface PracticeStats {
  total: number
  answered: number
  correct: number
  accuracy: number
  duration_seconds: number
}

export interface PracticeSession {
  id: string
  status: 'draft' | 'in_progress' | 'submitted'
  score: number
  questions: PracticeQuestion[]
  answers: PracticeAnswer[]
  stats: PracticeStats | null
  started_at: string
  finished_at: string | null
  meta: {
    kind?: 'retry'
    counts?: Record<string, number>
    difficulty?: string
    topic_ids?: string[]
    document_ids?: string[]
    days?: number | null
    requested?: number
    produced?: number
    warnings?: string[]
    documents?: Array<{ id: string; name: string }>
    document_total?: number
    result?: PracticeStats
  }
}

export interface PracticeSessionBrief {
  id: string
  status: 'draft' | 'in_progress' | 'submitted'
  score: number
  question_count: number
  answered_count: number
  started_at: string
  finished_at: string | null
  meta: PracticeSession['meta']
}

export interface PracticeJob {
  id: string
  state: 'running' | 'done' | 'failed'
  session_id: string | null
  message: string
  warnings: string[]
  requested: number
  produced: number
  started_at: string | null
  finished_at: string | null
}

export interface PracticeMeta {
  types: Array<{ key: QuestionType; label: string; auto_graded: boolean; hint: string }>
  difficulties: Array<{ key: string; label: string; hint: string }>
  max_questions: number
  min_chars_per_question: number
}

export const getPracticeMeta = () => apiGet<PracticeMeta>('/practice/meta')

export const generateQuestions = (body: {
  topic_ids?: string[]
  document_ids?: string[]
  days?: number | null
  counts: Partial<Record<QuestionType, number>>
  difficulty?: 'easy' | 'medium' | 'hard'
}) => apiPost<PracticeJob>('/practice/generate', body)

export const getPracticeJob = (jobId: string) => apiGet<PracticeJob>(`/practice/jobs/${jobId}`)

export const listPracticeSessions = (limit = 20) =>
  apiGet<PracticeSessionBrief[]>(`/practice/sessions?limit=${limit}`)

export const getPracticeSession = (id: string) => apiGet<PracticeSession>(`/practice/sessions/${id}`)

export const deletePracticeQuestion = (sessionId: string, questionId: string) =>
  apiDelete<PracticeSession>(`/practice/sessions/${sessionId}/questions/${questionId}`)

export const deletePracticeSession = (id: string) =>
  apiDelete<{ deleted: string }>(`/practice/sessions/${id}`)

/* ---------------- 练习：作答、判分、错题本 ---------------- */

export interface AnswerInput {
  question_id: string
  response: string
  duration_seconds?: number
  self_grade?: 'mastered' | 'unsure' | null
}

export interface WrongAnswerItem {
  id: string
  question: PracticeQuestion
  wrong_count: number
  last_response: string
  first_wrong_at: string
  last_wrong_at: string
  topic_id: string | null
  topic_name: string
  document_name: string
}

export interface WrongTopic {
  topic_id: string
  name: string
  color: string
  wrong_count: number
  question_count: number
  answered: number
  correct: number
  error_rate: number
}

export interface WrongSummary {
  total: number
  total_wrong_count: number
  repeat_count: number
  by_topic: WrongTopic[]
  by_type: Array<{ type: QuestionType; label: string; count: number }>
}

export const startPracticeSession = (id: string) =>
  apiPost<PracticeSession>(`/practice/sessions/${id}/start`)

export const savePracticeAnswer = (id: string, body: AnswerInput) =>
  apiPatch<PracticeSession>(`/practice/sessions/${id}/answers`, body)

export const submitPractice = (id: string, answers: AnswerInput[]) =>
  apiPost<PracticeSession>(`/practice/sessions/${id}/submit`, { answers })

export const listWrongAnswers = (params: {
  topic_id?: string
  document_id?: string
  type?: QuestionType
  days?: number
  min_wrong?: number
  sort?: 'recent' | 'count'
  limit?: number
} = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  const suffix = query.toString()
  return apiGet<WrongAnswerItem[]>(`/practice/wrong-answers${suffix ? `?${suffix}` : ''}`)
}

export const getWrongSummary = (days?: number) =>
  apiGet<WrongSummary>(`/practice/wrong-answers/summary${days ? `?days=${days}` : ''}`)

export const removeWrongAnswer = (id: string) =>
  apiDelete<{ deleted: string }>(`/practice/wrong-answers/${id}`)

export const startPracticeRetry = (body: { wrong_ids?: string[]; limit?: number }) =>
  apiPost<PracticeSession>('/practice/retry', body)

/* ---------------- 回顾报告 ---------------- */

export type ReportWindow = 7 | 14 | 30
export type PushStatus = 'pending' | 'sent' | 'failed' | 'skipped' | 'none'

export interface ReportSummary {
  documents: number
  chunks: number
  sessions: number
  answered: number
  correct: number
  accuracy: number
  wrong: number
  has_activity: boolean
  analysis_source: 'llm' | 'rule' | 'empty'
}

export interface ReportBrief {
  id: string
  window_days: ReportWindow
  period_start: string
  period_end: string
  created_at: string
  trigger: 'manual' | 'schedule'
  push_status: PushStatus
  push_detail: string
  summary: ReportSummary
}

export interface ReportDetail extends ReportBrief {
  markdown: string
  metrics: Record<string, unknown>
}

/** 报告生成要等模型，给 120 秒；超时提示用户重试而不是无限转圈 */
export const createReviewReport = (body: { window_days: ReportWindow; notify?: boolean }) =>
  apiPost<ReportDetail>('/reviews', body, 120_000)

export const listReviewReports = (limit = 50) =>
  apiGet<ReportBrief[]>(`/reviews?limit=${limit}`)

export const getReviewReport = (id: string) => apiGet<ReportDetail>(`/reviews/${id}`)

export const deleteReviewReport = (id: string) => apiDelete<{ deleted: string }>(`/reviews/${id}`)

export const pushReviewReport = (id: string) =>
  apiPost<{ ok: boolean; detail: string; report: ReportDetail }>(`/reviews/${id}/push`)

export const getPushState = () =>
  apiGet<{ status: PushStatus; detail: string; at: string | null; report_id: string | null }>(
    '/reviews/push-state'
  )

/* ---------------- 定时任务 ---------------- */

export interface ScheduleState {
  enabled: boolean
  job_registered: boolean
  next_run: string | null
  cron: { day_of_week: string; hour: number; minute: number }
  note: string
}

export const getSchedule = () => apiGet<ScheduleState>('/schedule')

export const runScheduleNow = () =>
  apiPost<{ ok: boolean; message: string; enabled?: boolean }>('/schedule/run')

/** 真发一封测试邮件（不是只测登录） */
export const testMail = (overrides: Record<string, unknown> = {}) =>
  apiPost<{ ok: boolean; message: string }>('/settings/test-mail', { overrides })
