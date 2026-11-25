import { useEffect, useMemo, useState } from 'react'
import {
  Boxes,
  Check,
  Clock,
  Copy,
  Cpu,
  Database,
  Mail,
  Plug,
  RefreshCw,
  Save,
  TriangleAlert,
  X,
} from 'lucide-react'
import {
  ApiError,
  getIndexStatus,
  getSettings,
  rebuildIndex,
  runScheduleNow,
  testConnection,
  testMail,
  updateSettings,
  type AppSettings,
  type IndexStatus,
  type TestResult,
} from '../api/client'
import Button from '../components/ui/Button'
import Field, { Switch } from '../components/ui/Field'
import Empty, { Spinner } from '../components/ui/Feedback'
import Modal from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import HelpTip from '../components/ui/HelpTip'

type SectionKey = 'llm' | 'embedding' | 'smtp' | 'scheduler' | 'runtime'

const SECTIONS: Array<{ key: SectionKey; label: string; icon: typeof Cpu }> = [
  { key: 'llm', label: '大模型', icon: Cpu },
  { key: 'embedding', label: '向量化', icon: Boxes },
  { key: 'smtp', label: '邮件推送', icon: Mail },
  { key: 'scheduler', label: '定时回顾', icon: Clock },
  { key: 'runtime', label: '数据与关于', icon: Database },
]

const WEEKDAYS = [
  { value: 'mon', label: '周一' },
  { value: 'tue', label: '周二' },
  { value: 'wed', label: '周三' },
  { value: 'thu', label: '周四' },
  { value: 'fri', label: '周五' },
  { value: 'sat', label: '周六' },
  { value: 'sun', label: '周日' },
]

interface Draft {
  llm_provider: string
  llm_base_url: string
  llm_model: string
  llm_temperature: number
  llm_api_key: string
  embedding_provider: string
  embedding_model: string
  embedding_base_url: string
  embedding_dimension: number
  embedding_api_key: string
  smtp_host: string
  smtp_port: number
  smtp_use_ssl: boolean
  smtp_user: string
  smtp_password: string
  smtp_from: string
  smtp_to: string
  scheduler_enabled: boolean
  scheduler_cron_day_of_week: string
  scheduler_cron_hour: number
  scheduler_cron_minute: number
}

function toDraft(s: AppSettings): Draft {
  return {
    llm_provider: s.llm.provider,
    llm_base_url: s.llm.base_url,
    llm_model: s.llm.model,
    llm_temperature: s.llm.temperature,
    llm_api_key: '',
    embedding_provider: s.embedding.provider,
    embedding_model: s.embedding.model,
    embedding_base_url: s.embedding.base_url,
    embedding_dimension: s.embedding.dimension,
    embedding_api_key: '',
    smtp_host: s.smtp.host,
    smtp_port: s.smtp.port,
    smtp_use_ssl: s.smtp.use_ssl,
    smtp_user: s.smtp.user,
    smtp_password: '',
    smtp_from: s.smtp.from_addr,
    smtp_to: s.smtp.to,
    scheduler_enabled: s.scheduler.enabled,
    scheduler_cron_day_of_week: s.scheduler.day_of_week,
    scheduler_cron_hour: s.scheduler.hour,
    scheduler_cron_minute: s.scheduler.minute,
  }
}

export default function Settings() {
  const toast = useToast()
  const [section, setSection] = useState<SectionKey>(() => {
    const wanted = new URLSearchParams(window.location.search).get('section')
    return SECTIONS.some((item) => item.key === wanted) ? (wanted as SectionKey) : 'llm'
  })
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [result, setResult] = useState<Record<string, TestResult | undefined>>({})
  const [confirmRebuild, setConfirmRebuild] = useState(false)
  const [index, setIndex] = useState<IndexStatus | null>(null)
  const [indexBusy, setIndexBusy] = useState(false)

  const loadIndex = () =>
    getIndexStatus()
      .then(setIndex)
      .catch(() => setIndex(null))

  const load = () => {
    setError('')
    getSettings()
      .then((s) => {
        setSettings(s)
        setDraft(toDraft(s))
      })
      .catch((e: Error) => setError(e.message))
    loadIndex()
  }

  useEffect(load, [])

  // 重建索引是后台任务，跑起来就轮询进度；结束即停
  const jobState = index?.job.state
  useEffect(() => {
    if (jobState !== 'running') return
    const timer = window.setInterval(loadIndex, 1200)
    return () => window.clearInterval(timer)
  }, [jobState])

  const patch = (changes: Partial<Draft>) =>
    setDraft((prev) => (prev ? { ...prev, ...changes } : prev))

  const changes = useMemo(() => {
    if (!settings || !draft) return {}
    const out: Record<string, unknown> = {}
    const compare: Array<[string, unknown, unknown]> = [
      ['llm_provider', draft.llm_provider, settings.llm.provider],
      ['llm_base_url', draft.llm_base_url, settings.llm.base_url],
      ['llm_model', draft.llm_model, settings.llm.model],
      ['llm_temperature', draft.llm_temperature, settings.llm.temperature],
      ['embedding_provider', draft.embedding_provider, settings.embedding.provider],
      ['embedding_model', draft.embedding_model, settings.embedding.model],
      ['embedding_base_url', draft.embedding_base_url, settings.embedding.base_url],
      ['embedding_dimension', draft.embedding_dimension, settings.embedding.dimension],
      ['smtp_host', draft.smtp_host, settings.smtp.host],
      ['smtp_port', draft.smtp_port, settings.smtp.port],
      ['smtp_use_ssl', draft.smtp_use_ssl, settings.smtp.use_ssl],
      ['smtp_user', draft.smtp_user, settings.smtp.user],
      ['smtp_from', draft.smtp_from, settings.smtp.from_addr],
      ['smtp_to', draft.smtp_to, settings.smtp.to],
      ['scheduler_enabled', draft.scheduler_enabled, settings.scheduler.enabled],
      ['scheduler_cron_day_of_week', draft.scheduler_cron_day_of_week, settings.scheduler.day_of_week],
      ['scheduler_cron_hour', draft.scheduler_cron_hour, settings.scheduler.hour],
      ['scheduler_cron_minute', draft.scheduler_cron_minute, settings.scheduler.minute],
    ]
    for (const [key, next, prev] of compare) {
      if (next !== prev) out[key] = next
    }
    if (draft.llm_api_key.trim()) out.llm_api_key = draft.llm_api_key.trim()
    if (draft.embedding_api_key.trim()) out.embedding_api_key = draft.embedding_api_key.trim()
    if (draft.smtp_password.trim()) out.smtp_password = draft.smtp_password.trim()
    return out
  }, [draft, settings])

  const dirtyKeys = Object.keys(changes)
  const embeddingTouched = dirtyKeys.some((k) => k.startsWith('embedding_'))

  const performSave = async (payload: Record<string, unknown>, alsoRebuild = false) => {
    setSaving(true)
    try {
      const res = await updateSettings(payload)
      setSettings(res.settings)
      setDraft(toDraft(res.settings))
      setResult({})
      if (res.index) setIndex(res.index)
      toast.success(`已保存 ${res.saved.length} 项配置`)

      if (res.index?.needs_rebuild) {
        setSection('embedding')
        toast.warn('向量索引与当前配置不一致，请重建索引后再导入新资料')
        if (alsoRebuild) await runRebuild()
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败，请查看后端日志')
    } finally {
      setSaving(false)
      setConfirmRebuild(false)
    }
  }

  const runRebuild = async () => {
    setIndexBusy(true)
    try {
      const res = await rebuildIndex()
      setIndex((prev) => (prev ? { ...prev, job: res.job, can_rebuild: false } : prev))
      toast.success('已开始重建索引，完成后会自动刷新')
      loadIndex()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '无法启动重建任务')
    } finally {
      setIndexBusy(false)
    }
  }

  const handleSave = () => {
    if (!dirtyKeys.length) {
      toast.info('没有需要保存的修改')
      return
    }
    if (embeddingTouched) {
      setConfirmRebuild(true)
      return
    }
    performSave(changes)
  }

  const runTest = async (target: 'llm' | 'embedding' | 'smtp') => {
    if (!draft) return
    setTesting(target)
    setResult((prev) => ({ ...prev, [target]: undefined }))
    try {
      const res = await testConnection(target, changes)
      setResult((prev) => ({ ...prev, [target]: res }))
      if (res.ok) toast.success(res.message)
      else toast.error(res.message)
    } catch (e) {
      const message = e instanceof ApiError ? e.message : '测试请求失败'
      setResult((prev) => ({ ...prev, [target]: { ok: false, message } }))
      toast.error(message)
    } finally {
      setTesting(null)
    }
  }

  /** 真发一封测试邮件：验证的是「登录 + 组信 + 投递」全链路 */
  const sendTestMail = async () => {
    if (!draft) return
    setTesting('mail')
    setResult((prev) => ({ ...prev, mail: undefined }))
    try {
      const res = await testMail(changes)
      setResult((prev) => ({ ...prev, mail: { ok: res.ok, message: res.message } }))
      if (res.ok) toast.success(res.message)
      else toast.error(res.message)
    } catch (e) {
      const message = e instanceof ApiError ? e.message : '发送测试邮件失败'
      setResult((prev) => ({ ...prev, mail: { ok: false, message } }))
      toast.error(message)
    } finally {
      setTesting(null)
    }
  }

  /** 立即跑一次定时任务：走与定时触发完全相同的代码路径 */
  const runScheduleOnce = async () => {
    setTesting('run')
    try {
      const res = await runScheduleNow()
      toast.success(`${res.message}。结果可在「回顾」页查看。`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '执行失败')
    } finally {
      setTesting(null)
    }
  }

  if (error) {
    return (
      <section className="page">
        <div className="card">
          <Empty
            icon={<TriangleAlert size={20} />}
            title="无法读取配置"
            description={error}
          >
            <Button icon={<RefreshCw size={14} />} onClick={load}>
              重试
            </Button>
          </Empty>
        </div>
      </section>
    )
  }

  if (!settings || !draft) {
    return (
      <section className="page">
        <Spinner text="正在读取配置…" />
      </section>
    )
  }

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>设置</h1>
          <p className="page-sub">
            模型、向量化与邮件配置。
            <HelpTip label="了解配置存放位置">
              保存后写入项目根目录的 <code>.env</code>，后端会即时重载，不需要重启。
              密钥只存在本地文件里，不会上传到任何地方。
            </HelpTip>
          </p>
        </div>
        <div className="page-actions">
          {dirtyKeys.length > 0 && (
            <span className="tag tag-warn">{dirtyKeys.length} 项待保存</span>
          )}
          <Button variant="ghost" size="sm" icon={<RefreshCw size={13} />} onClick={load}>
            重新读取
          </Button>
        </div>
      </header>

      <div className="settings-shell">
        <nav className="settings-nav">
          {SECTIONS.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.key}
                data-active={section === item.key}
                onClick={() => setSection(item.key)}
              >
                <Icon />
                {item.label}
              </button>
            )
          })}
        </nav>

        <div className="settings-panel">
          {section === 'llm' && (
            <div className="card">
              <div className="card-head">
                <h2>
                  <Cpu size={14} /> 大模型
                </h2>
                <StatusTag configured={settings.llm.configured} />
              </div>
              <div className="card-body">
                <div className="form-grid">
                  <Field
                    label="供应商"
                    tip="选中供应商后会自动填入它的默认接口地址与常用模型名，之后仍可手动改。"
                  >
                    <select
                      className="select"
                      value={draft.llm_provider}
                      onChange={(e) => {
                        const preset = settings.llm_presets.find(
                          (p) => p.provider === e.target.value
                        )
                        patch({
                          llm_provider: e.target.value,
                          llm_base_url: preset?.base_url ?? draft.llm_base_url,
                          llm_model: preset?.model ?? draft.llm_model,
                        })
                      }}
                    >
                      {settings.llm_providers.map((p) => (
                        <option key={p} value={p}>
                          {settings.llm_presets.find((x) => x.provider === p)?.label ?? p}
                        </option>
                      ))}
                    </select>
                  </Field>

                  <Field label="模型名称" required>
                    <input
                      className="input mono"
                      value={draft.llm_model}
                      onChange={(e) => patch({ llm_model: e.target.value })}
                      placeholder="deepseek-chat"
                    />
                  </Field>

                  <Field
                    label="接口地址"
                    required
                    hint="需以 /v1 结尾"
                    tip="走 OpenAI 兼容协议：绝大多数国内外模型服务都提供 /v1 路径，把完整地址填在这里即可。"
                    className="form-grid-full"
                  >
                    <input
                      className="input mono"
                      value={draft.llm_base_url}
                      onChange={(e) => patch({ llm_base_url: e.target.value })}
                      placeholder="https://api.deepseek.com/v1"
                    />
                  </Field>

                  <Field
                    label="API Key"
                    className="form-grid-full"
                    hint={
                      settings.llm.api_key
                        ? `当前已配置：${settings.llm.api_key}，留空表示不修改`
                        : '仅保存在本地 .env，不会上传'
                    }
                  >
                    <div className="input-group">
                      <input
                        className="input mono"
                        type="password"
                        autoComplete="off"
                        value={draft.llm_api_key}
                        onChange={(e) => patch({ llm_api_key: e.target.value })}
                        placeholder={settings.llm.api_key ? '留空则保持不变' : 'sk-...'}
                      />
                      <Button
                        icon={testing === 'llm' ? undefined : <Plug size={13} />}
                        loading={testing === 'llm'}
                        onClick={() => runTest('llm')}
                      >
                        测试
                      </Button>
                    </div>
                  </Field>

                  <Field
                    label="温度"
                    hint="建议 0.2 ~ 0.5"
                    tip="越低越稳定、越贴近原文；越高越发散。出题和问答都建议偏低，避免模型自由发挥。"
                  >
                    <input
                      className="input"
                      type="number"
                      step="0.1"
                      min="0"
                      max="2"
                      value={draft.llm_temperature}
                      onChange={(e) => patch({ llm_temperature: Number(e.target.value) })}
                    />
                  </Field>
                </div>
                <TestBanner result={result.llm} />
              </div>
            </div>
          )}

          {section === 'embedding' && (
            <div className="card">
              <div className="card-head">
                <h2>
                  <Boxes size={14} /> 向量化
                </h2>
                <StatusTag configured={settings.embedding.configured} />
              </div>
              <div className="card-body">
                <div className="notice notice-warn" style={{ marginBottom: 16 }}>
                  <TriangleAlert />
                  <span>
                    切换 Embedding 模型会让已有向量与新向量不在同一空间，需要重建索引；否则检索结果会失真。
                  </span>
                </div>
                <div className="form-grid">
                  <Field label="供应商">
                    <select
                      className="select"
                      value={draft.embedding_provider}
                      onChange={(e) => patch({ embedding_provider: e.target.value })}
                    >
                      {settings.embedding_providers.map((p) => (
                        <option key={p} value={p}>
                          {p === 'local' ? '本地 BGE（离线、零成本）' : '云端 Embedding（需 API）'}
                        </option>
                      ))}
                      {/* 旧配置里可能存的是别名（bge / openai），补一项避免下拉显示空白 */}
                      {!settings.embedding_providers.includes(draft.embedding_provider) && (
                        <option value={draft.embedding_provider}>
                          {draft.embedding_provider}（别名）
                        </option>
                      )}
                    </select>
                  </Field>

                  <Field label="模型" required>
                    <input
                      className="input mono"
                      value={draft.embedding_model}
                      onChange={(e) => patch({ embedding_model: e.target.value })}
                      placeholder="BAAI/bge-small-zh-v1.5"
                    />
                  </Field>

                  <Field
                    label="向量维度"
                    hint="需与模型输出一致"
                    tip="必须和模型真实输出的维度对齐，填错会让检索结果全乱。bge-small-zh 是 512，text-embedding-3-small 是 1536。"
                  >
                    <input
                      className="input"
                      type="number"
                      value={draft.embedding_dimension}
                      onChange={(e) => patch({ embedding_dimension: Number(e.target.value) })}
                    />
                  </Field>

                  {draft.embedding_provider !== 'local' && draft.embedding_provider !== 'bge' && (
                    <>
                      <Field label="接口地址" className="form-grid-full">
                        <input
                          className="input mono"
                          value={draft.embedding_base_url}
                          onChange={(e) => patch({ embedding_base_url: e.target.value })}
                          placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
                        />
                      </Field>
                      <Field
                        label="API Key"
                        className="form-grid-full"
                        hint={
                          settings.embedding.api_key
                            ? `当前已配置：${settings.embedding.api_key}`
                            : '仅保存在本地 .env'
                        }
                      >
                        <div className="input-group">
                          <input
                            className="input mono"
                            type="password"
                            autoComplete="off"
                            value={draft.embedding_api_key}
                            onChange={(e) => patch({ embedding_api_key: e.target.value })}
                            placeholder={settings.embedding.api_key ? '留空则保持不变' : 'sk-...'}
                          />
                          <Button
                            icon={<Plug size={13} />}
                            loading={testing === 'embedding'}
                            onClick={() => runTest('embedding')}
                          >
                            测试
                          </Button>
                        </div>
                      </Field>
                    </>
                  )}

                  {(draft.embedding_provider === 'local' || draft.embedding_provider === 'bge') && (
                    <div className="form-grid-full">
                      <Button
                        icon={<Plug size={13} />}
                        loading={testing === 'embedding'}
                        onClick={() => runTest('embedding')}
                      >
                        测试本地模型（首次会下载权重）
                      </Button>
                    </div>
                  )}
                </div>
                <TestBanner result={result.embedding} />

                {index && (
                  <div className="index-status" data-state={indexState(index)}>
                    <div className="index-status-head">
                      <div>
                        <strong>向量索引</strong>
                        <span className="index-status-reason">{index.reason}</span>
                      </div>
                      <span className={`tag ${indexTagClass(index)}`}>
                        {indexTagText(index)}
                      </span>
                    </div>

                    <dl className="kv index-status-kv">
                      <dt>索引维度</dt>
                      <dd className="mono">
                        {index.index_dimension ?? '未建表'}
                        {index.index_dimension !== null &&
                          index.index_dimension !== index.configured_dimension && (
                            <span className="muted"> · 配置 {index.configured_dimension}</span>
                          )}
                      </dd>
                      <dt>片段 / 向量</dt>
                      <dd className="mono">
                        {index.chunk_count} / {index.vector_count}
                        {index.vector_count !== index.chunk_count && (
                          <span className="muted"> · 不一致</span>
                        )}
                      </dd>
                      <dt>模型</dt>
                      <dd className="mono">{index.model}</dd>
                    </dl>

                    {index.job.state === 'running' && (
                      <div className="index-progress">
                        <div className="index-progress-bar">
                          <span
                            style={{
                              width: `${
                                index.job.total > 0
                                  ? Math.round((index.job.done / index.job.total) * 100)
                                  : 6
                              }%`,
                            }}
                          />
                        </div>
                        <span className="muted mono">
                          {index.job.done} / {index.job.total || '…'}
                        </span>
                      </div>
                    )}

                    {index.job.state !== 'running' && index.job.message && (
                      <p className="index-job-msg" data-failed={index.job.state === 'failed'}>
                        {index.job.state === 'failed' ? '上次重建失败 · ' : '上次重建 · '}
                        {index.job.message}
                      </p>
                    )}

                    <div className="index-actions">
                      <Button
                        size="sm"
                        icon={<RefreshCw size={12} />}
                        loading={indexBusy || index.job.state === 'running'}
                        disabled={!index.can_rebuild}
                        onClick={runRebuild}
                      >
                        {index.job.state === 'running' ? '重建中…' : '重建索引'}
                      </Button>
                      <span className="muted" style={{ fontSize: 12.5 }}>
                        用片段原文重新算向量，不重新解析文件、不改主题归属
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {section === 'smtp' && (
            <div className="card">
              <div className="card-head">
                <h2>
                  <Mail size={14} /> 邮件推送
                </h2>
                <StatusTag configured={settings.smtp.configured} />
              </div>
              <div className="card-body">
                <div className="form-grid">
                  <Field label="SMTP 服务器" required>
                    <input
                      className="input mono"
                      value={draft.smtp_host}
                      onChange={(e) => patch({ smtp_host: e.target.value })}
                      placeholder="smtp.qq.com"
                    />
                  </Field>
                  <Field label="端口" required>
                    <input
                      className="input"
                      type="number"
                      value={draft.smtp_port}
                      onChange={(e) => patch({ smtp_port: Number(e.target.value) })}
                    />
                  </Field>
                  <Field label="账号" required hint="通常是完整邮箱地址">
                    <input
                      className="input"
                      value={draft.smtp_user}
                      onChange={(e) => patch({ smtp_user: e.target.value })}
                      placeholder="you@qq.com"
                    />
                  </Field>
                  <Field
                    label="授权码"
                    required
                    tip="多数邮箱（QQ、163、Gmail）要求填 SMTP 授权码，而不是网页登录密码。授权码在邮箱的账户设置里单独生成。"
                  >
                    <input
                      className="input mono"
                      type="password"
                      autoComplete="off"
                      value={draft.smtp_password}
                      onChange={(e) => patch({ smtp_password: e.target.value })}
                      placeholder={settings.smtp.password ? '留空则保持不变' : '授权码'}
                    />
                  </Field>
                  <Field label="发件人" hint="留空则使用账号">
                    <input
                      className="input"
                      value={draft.smtp_from}
                      onChange={(e) => patch({ smtp_from: e.target.value })}
                      placeholder="A4S <you@qq.com>"
                    />
                  </Field>
                  <Field label="收件人" hint="周报接收邮箱，可填自己">
                    <input
                      className="input"
                      value={draft.smtp_to}
                      onChange={(e) => patch({ smtp_to: e.target.value })}
                      placeholder="you@qq.com"
                    />
                  </Field>
                </div>

                <div className="setting-row" style={{ marginTop: 8 }}>
                  <div className="setting-row-text">
                    <div className="setting-row-title">使用 SSL</div>
                    <div className="setting-row-desc">465 端口通常开启，587 端口建议关闭并使用 STARTTLS</div>
                  </div>
                  <Switch
                    label="使用 SSL"
                    checked={draft.smtp_use_ssl}
                    onChange={(v) => patch({ smtp_use_ssl: v })}
                  />
                </div>

                <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <Button
                    icon={<Plug size={13} />}
                    loading={testing === 'smtp'}
                    onClick={() => runTest('smtp')}
                  >
                    测试登录
                  </Button>
                  <Button
                    icon={<Mail size={13} />}
                    loading={testing === 'mail'}
                    onClick={() => void sendTestMail()}
                  >
                    发送测试邮件
                  </Button>
                </div>
                <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                  「测试登录」只验证账号密码；「发送测试邮件」会真的发出一封，
                  能收到才说明整条链路可用。
                </p>
                <TestBanner result={result.smtp} />
                <TestBanner result={result.mail} />
              </div>
            </div>
          )}

          {section === 'scheduler' && (
            <div className="card">
              <div className="card-head">
                <h2>
                  <Clock size={14} /> 定时回顾
                </h2>
                <span className={settings.scheduler.enabled ? 'tag tag-ok' : 'tag'}>
                  {settings.scheduler.enabled ? '已开启' : '已关闭'}
                </span>
              </div>
              <div className="card-body">
                <div className="setting-row">
                  <div className="setting-row-text">
                    <div className="setting-row-title">自动生成周报并发送邮件</div>
                    <div className="setting-row-desc">
                      应用需保持运行才会触发；关机期间不会补发（补发机制在 P1）。
                    </div>
                  </div>
                  <Switch
                    label="定时回顾"
                    checked={draft.scheduler_enabled}
                    onChange={(v) => patch({ scheduler_enabled: v })}
                  />
                </div>

                <div className="form-grid" style={{ marginTop: 16 }}>
                  <Field label="触发日">
                    <select
                      className="select"
                      value={draft.scheduler_cron_day_of_week}
                      onChange={(e) => patch({ scheduler_cron_day_of_week: e.target.value })}
                    >
                      {WEEKDAYS.map((d) => (
                        <option key={d.value} value={d.value}>
                          {d.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="小时">
                    <input
                      className="input"
                      type="number"
                      min="0"
                      max="23"
                      value={draft.scheduler_cron_hour}
                      onChange={(e) => patch({ scheduler_cron_hour: Number(e.target.value) })}
                    />
                  </Field>
                  <Field label="分钟">
                    <input
                      className="input"
                      type="number"
                      min="0"
                      max="59"
                      value={draft.scheduler_cron_minute}
                      onChange={(e) => patch({ scheduler_cron_minute: Number(e.target.value) })}
                    />
                  </Field>
                </div>

                <div className="notice" style={{ marginTop: 16 }}>
                  <Clock />
                  <span>
                    当前设定：每周
                    {WEEKDAYS.find((d) => d.value === draft.scheduler_cron_day_of_week)?.label}
                    {String(draft.scheduler_cron_hour).padStart(2, '0')}:
                    {String(draft.scheduler_cron_minute).padStart(2, '0')} 自动生成回顾报告
                  </span>
                </div>

                <div className="setting-row" style={{ marginTop: 16 }}>
                  <div className="setting-row-text">
                    <div className="setting-row-title">
                      {settings.scheduler.enabled
                        ? settings.scheduler.next_run
                          ? `下次触发：${formatMoment(settings.scheduler.next_run)}`
                          : '下次触发：等待装填'
                        : '定时未开启，不会自动生成'}
                    </div>
                    <div className="setting-row-desc">
                      改完触发时间记得点右上角保存。想立刻验证效果，可以直接跑一次。
                    </div>
                  </div>
                  <Button
                    icon={<RefreshCw size={13} />}
                    loading={testing === 'run'}
                    onClick={() => void runScheduleOnce()}
                  >
                    立即执行一次
                  </Button>
                </div>
              </div>
            </div>
          )}

          {section === 'runtime' && (
            <>
              <div className="card">
                <div className="card-head">
                  <h2>
                    <Database size={14} /> 运行环境
                  </h2>
                  <span className="tag">只读</span>
                </div>
                <div className="card-body">
                  <dl className="kv">
                    <dt>版本</dt>
                    <dd>v{settings.runtime.version}</dd>
                    <dt>运行模式</dt>
                    <dd>{settings.runtime.app_env}</dd>
                    <dt>数据库</dt>
                    <dd className="mono">{settings.runtime.database_url}</dd>
                    <dt>数据目录</dt>
                    <dd className="mono">{settings.runtime.data_dir}</dd>
                    <dt>配置文件</dt>
                    <dd className="mono">
                      {settings.runtime.env_file}
                      {!settings.runtime.env_file_exists && (
                        <span className="tag tag-warn" style={{ marginLeft: 8 }}>
                          尚未创建，保存配置后生成
                        </span>
                      )}
                    </dd>
                  </dl>
                  <div style={{ marginTop: 12 }}>
                    <Button
                      size="sm"
                      icon={<Copy size={12} />}
                      onClick={() => {
                        navigator.clipboard
                          .writeText(settings.runtime.env_file)
                          .then(() => toast.success('路径已复制'))
                          .catch(() => toast.error('复制失败'))
                      }}
                    >
                      复制配置路径
                    </Button>
                  </div>
                </div>
              </div>

            </>
          )}
        </div>
      </div>

      <div className="settings-foot">
        <span className="muted">
          {dirtyKeys.length > 0 ? `共 ${dirtyKeys.length} 项修改未保存` : '配置已是最新'}
        </span>
        <div className="row">
          <Button variant="ghost" onClick={() => setDraft(toDraft(settings))} disabled={!dirtyKeys.length || saving}>
            还原
          </Button>
          <Button
            variant="primary"
            icon={<Save size={13} />}
            loading={saving}
            disabled={!dirtyKeys.length}
            onClick={handleSave}
          >
            保存配置
          </Button>
        </div>
      </div>

      <Modal
        open={confirmRebuild}
        title="切换向量化配置？"
        description="新的 Embedding 模型与旧向量不在同一空间。重建索引会用片段原文重新算一遍向量（不清空资料），在此之前问答与出题会检索到无关内容。"
        onClose={() => setConfirmRebuild(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmRebuild(false)}>
              取消
            </Button>
            <Button loading={saving} onClick={() => performSave(changes)}>
              仅保存
            </Button>
            <Button variant="primary" loading={saving} onClick={() => performSave(changes, true)}>
              保存并重建索引
            </Button>
          </>
        }
      />
    </section>
  )
}

function StatusTag({ configured }: { configured: boolean }) {
  return (
    <span className={configured ? 'tag tag-ok' : 'tag tag-warn'}>
      {configured ? '已配置' : '待配置'}
    </span>
  )
}

function indexState(index: IndexStatus): string {
  if (!index.enabled) return 'off'
  if (index.job.state === 'running') return 'running'
  return index.needs_rebuild ? 'warn' : 'ok'
}

function indexTagClass(index: IndexStatus): string {
  const state = indexState(index)
  if (state === 'ok') return 'tag-ok'
  if (state === 'warn') return 'tag-warn'
  return 'tag-danger'
}

function indexTagText(index: IndexStatus): string {
  const state = indexState(index)
  if (state === 'off') return '未启用'
  if (state === 'running') return '重建中'
  if (state === 'warn') return '需重建'
  return '可用'
}

function formatMoment(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function TestBanner({ result }: { result?: TestResult }) {
  if (!result) return null
  return (
    <div
      className={result.ok ? 'notice' : 'notice notice-warn'}
      style={{ marginTop: 16 }}
    >
      {result.ok ? <Check size={14} /> : <X size={14} />}
      <span>
        {result.message}
        {result.detail && <span className="mono"> · {result.detail}</span>}
      </span>
    </div>
  )
}
